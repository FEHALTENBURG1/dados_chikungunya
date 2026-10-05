#!/usr/bin/env python3
"""
Atualização incremental dos dados de Chikungunya da RIDE-DF.

Substitui baixar_chikungunya.py + processar_ride.py.

Ideia central
-------------
Anos fechados não precisam ser baixados de novo todos os dias. O script guarda
um "cache" por ano (dados/historico/) e, em cada execução:

  1. consulta (HEAD) o ETag do arquivo do ano corrente no S3 do Ministério da
     Saúde; se não mudou, termina sem baixar nada e sem gerar commit;
  2. se mudou, baixa só esse arquivo (~4 MB), filtra a RIDE-DF e atualiza o
     cache do ano;
  3. anos que ainda não têm cache (primeira execução) são construídos
     automaticamente; anos antigos só são reprocessados sob demanda (--anos).

Saídas (mesmo formato de antes, nada muda para quem consome os dados):
  dados/chikungunya_ride.csv   notificações da RIDE-DF, todos os anos
  dados/atraso_nacional.csv    curva de atraso de digitação F(d), nacional
  dados/atraso_ride.csv        curva de atraso local (notificações de residentes da RIDE)
  dados/nowcast_ride.csv       notificações por semana: observado, estimado e IC 95%
  dados/nowcast_validacao.csv  backtest: viés, erro e cobertura por método (ver nowcast.py)

Cache (versionado no Git):
  dados/historico/ride_AAAA.parquet     recorte RIDE-DF já tratado, por ano do arquivo
  dados/historico/atraso_AAAA.parquet   contagens nacionais (data do sintoma x atraso)
  dados/estado.json                     ETag, data e nº de linhas de cada ano

Uso:
  python atualizar.py                  # rotina diária
  python atualizar.py --anos 2025      # reprocessa um ano (ex.: correções tardias)
  python atualizar.py --tudo           # reconstrói todos os anos
  python atualizar.py --forcar         # ignora o ETag e reprocessa o ano corrente
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import tempfile
import time
import zipfile
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pacsv

import nowcast
from municipios_ride import CODIGOS_6, NOME_POR_COD6, UF_POR_COD6

# ---------------------------------------------------------------------------
# CONFIGURAÇÃO
# ---------------------------------------------------------------------------

ANO_INICIAL = 2021
URL = (
    "https://s3.sa-east-1.amazonaws.com/ckan.saude.gov.br/"
    "SINAN/Chikungunya/csv/CHIKBR{aa:02d}.csv.zip"
)

SAIDA = Path("dados")
HIST = SAIDA / "historico"
ESTADO = SAIDA / "estado.json"
ARQ_RIDE = SAIDA / "chikungunya_ride.csv"
ARQ_ATRASO = SAIDA / "atraso_nacional.csv"
ARQ_NOWCAST = SAIDA / "nowcast_ride.csv"
ARQ_ATRASO_LOCAL = SAIDA / "atraso_ride.csv"
ARQ_VALIDACAO = SAIDA / "nowcast_validacao.csv"

TIMEOUT = 120
TENTATIVAS = 4
ESPERA_BASE = 5
MATURIDADE = 120  # dias desde o início dos sintomas para a coorte ser "madura"
D_MAX = 200  # atraso máximo de digitação considerado na curva
QUEDA_MAXIMA = 0.10  # recusa publicar se um ano encolher mais que 10%

COLUNAS = [
    # Tempo
    "NU_ANO", "DT_NOTIFIC", "SEM_NOT", "DT_SIN_PRI", "SEM_PRI", "DT_DIGITA",
    # Lugar
    "SG_UF_NOT", "ID_MUNICIP", "SG_UF", "ID_MN_RESI",
    # Pessoa
    "NU_IDADE_N", "CS_SEXO", "CS_GESTANT", "CS_RACA", "CS_ESCOL_N",
    # Desfecho
    "CLASSI_FIN", "CRITERIO", "EVOLUCAO", "DT_OBITO", "DT_ENCERRA",
    "HOSPITALIZ", "DT_INVEST",
    # Laboratório — sorologia de chikungunya
    "DT_CHIK_S1", "DT_CHIK_S2", "DT_PRNT", "RES_CHIKS1", "RES_CHIKS2", "RESUL_PRNT",
    # Laboratório — campos de dengue mantidos para auditoria da ficha conjunta
    "DT_SORO", "RESUL_SORO",
    # Laboratório — RT-PCR
    "DT_PCR", "RESUL_PCR_",
    # Sinais e sintomas
    "FEBRE", "MIALGIA", "CEFALEIA", "ARTRALGIA", "ARTRITE", "EXANTEMA",
    "DOR_RETRO", "NAUSEA", "VOMITO", "CONJUNTVIT",
    # Comorbidades
    "HIPERTENSA", "DIABETES", "RENAL", "AUTO_IMUNE", "HEPATOPAT", "ACIDO_PEPT",
    "HEMATOLOG",
]
OBRIGATORIAS = ["ID_MUNICIP", "ID_MN_RESI", "SEM_PRI", "DT_SIN_PRI", "DT_DIGITA"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("chik")


class AnoIndisponivel(Exception):
    """O arquivo do ano ainda não foi publicado (HTTP 404)."""


# ---------------------------------------------------------------------------
# REDE
# ---------------------------------------------------------------------------

def _req(url: str, metodo: str = "GET") -> Request:
    return Request(
        url,
        method=metodo,
        headers={"User-Agent": "dados-chikungunya-github-actions/2.0"},
    )


def com_retry(funcao, descricao: str):
    for tentativa in range(1, TENTATIVAS + 1):
        try:
            return funcao()
        except HTTPError as e:
            if e.code == 404:
                raise AnoIndisponivel(descricao) from e
            erro = e
        except (URLError, TimeoutError, OSError, zipfile.BadZipFile) as e:
            erro = e
        if tentativa == TENTATIVAS:
            raise RuntimeError(f"Falha definitiva: {descricao} ({erro})") from erro
        espera = ESPERA_BASE * 2 ** (tentativa - 1)
        log.warning("%s: %s — nova tentativa em %ds", descricao, erro, espera)
        time.sleep(espera)


def metadados_remotos(ano: int) -> dict:
    """ETag e Last-Modified do arquivo, sem baixá-lo."""
    url = URL.format(aa=ano % 100)

    def head():
        with urlopen(_req(url, "HEAD"), timeout=TIMEOUT) as r:
            return {
                "etag": (r.headers.get("ETag") or "").strip('"'),
                "last_modified": r.headers.get("Last-Modified") or "",
            }

    return com_retry(head, f"HEAD {ano}")


def baixar_zip(ano: int, destino: Path) -> None:
    url = URL.format(aa=ano % 100)

    def get():
        with urlopen(_req(url), timeout=TIMEOUT) as r, destino.open("wb") as f:
            shutil.copyfileobj(r, f, length=1 << 20)
        if destino.stat().st_size == 0:
            raise OSError("arquivo vazio")
        with zipfile.ZipFile(destino) as zf:
            if zf.testzip() is not None:
                raise zipfile.BadZipFile("ZIP corrompido")

    com_retry(get, f"download {ano}")


# ---------------------------------------------------------------------------
# PROCESSAMENTO DE UM ANO
# ---------------------------------------------------------------------------

def parse_data(serie: pd.Series) -> pd.Series:
    return pd.to_datetime(serie, errors="coerce", format="%Y-%m-%d")


def adicionar_oportunidade_pcr(ride: pd.DataFrame) -> pd.DataFrame:
    """Variáveis de oportunidade da coleta de RT-PCR (1º dia clínico = dia do sintoma)."""
    ride = ride.copy()
    dt_sintoma = parse_data(ride["DT_SIN_PRI"])
    dt_pcr = parse_data(ride["DT_PCR"])

    dias = (dt_pcr - dt_sintoma).dt.days.astype("Int64")
    dia_clinico = (dias + 1).where(dias >= 0).astype("Int64")
    ride["DIAS_SINT_PCR"] = dias
    ride["DIA_CLINICO_PCR"] = dia_clinico

    janela = pd.Series("Sem data de coleta", index=ride.index, dtype="string")
    janela.loc[dt_pcr.notna() & dt_sintoma.isna()] = "Sem data de início dos sintomas"
    janela.loc[dias < 0] = "Data inconsistente"
    janela.loc[dia_clinico.between(1, 5, inclusive="both")] = "Ideal: 1º–5º dia"
    janela.loc[dia_clinico.between(6, 10, inclusive="both")] = "6º–10º dia"
    janela.loc[dia_clinico > 10] = "Após o 10º dia"
    ride["JANELA_PCR"] = janela
    return ride


def enriquecer(ride: pd.DataFrame) -> pd.DataFrame:
    ride = ride.copy()
    # Semana epidemiológica derivada de SEM_PRI (não de NU_ANO): evita o viés da SE 53.
    ride["ANO_EPI"] = ride["SEM_PRI"].str[:4]
    ride["SE"] = ride["SEM_PRI"].str[4:6]
    ride["MUN_NOTIF_NOME"] = ride["ID_MUNICIP"].map(NOME_POR_COD6)
    ride["MUN_RESI_NOME"] = ride["ID_MN_RESI"].map(NOME_POR_COD6)
    ride["MUN_RESI_UF"] = ride["ID_MN_RESI"].map(UF_POR_COD6)
    ride["FORA_DO_MUN"] = (ride["ID_MUNICIP"] != ride["ID_MN_RESI"]).map(
        {True: "1", False: "0"}
    )
    return adicionar_oportunidade_pcr(ride)


def processar_zip(caminho_zip: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.Timestamp, int]:
    """
    Lê o CSV nacional em fluxo (lotes), sem carregá-lo inteiro na memória.

    Devolve: recorte RIDE-DF, contagens nacionais (sintoma x atraso),
    maior DT_DIGITA do arquivo e total de linhas nacionais.
    """
    ride_partes: list[pd.DataFrame] = []
    contagens: list[pd.DataFrame] = []
    max_digita = pd.NaT
    total = 0
    codigos = pa.array(sorted(CODIGOS_6))

    with zipfile.ZipFile(caminho_zip) as zf:
        membros = [m for m in zf.infolist() if m.filename.lower().endswith(".csv")]
        if len(membros) != 1:
            raise RuntimeError(f"Esperado 1 CSV no ZIP, achei {len(membros)}")

        with zf.open(membros[0]) as fluxo:
            leitor = pacsv.open_csv(
                fluxo,
                read_options=pacsv.ReadOptions(block_size=1 << 24),
                convert_options=pacsv.ConvertOptions(
                    include_columns=COLUNAS,
                    include_missing_columns=True,
                    column_types={c: pa.string() for c in COLUNAS},
                    null_values=[],  # vazio continua vazio (como antes)
                    strings_can_be_null=False,
                ),
            )

            for lote in leitor:
                total += lote.num_rows
                if total == lote.num_rows:  # primeiro lote: confere o layout
                    ausentes = [
                        c for c in OBRIGATORIAS
                        if pc.all(pc.fill_null(pc.equal(lote.column(c), ""), True)).as_py()
                    ]
                    if ausentes:
                        raise KeyError(f"Colunas obrigatórias vazias/ausentes: {ausentes}")

                # Curva de atraso: usa o país inteiro
                d = pd.DataFrame(
                    {
                        "sintoma": parse_data(lote.column("DT_SIN_PRI").to_pandas()),
                        "digita": parse_data(lote.column("DT_DIGITA").to_pandas()),
                    }
                ).dropna()
                if not d.empty:
                    max_digita = max(max_digita, d["digita"].max()) if pd.notna(max_digita) else d["digita"].max()
                    d["atraso"] = (d["digita"] - d["sintoma"]).dt.days
                    d = d[(d["atraso"] >= 0) & (d["atraso"] <= D_MAX)]
                    contagens.append(
                        d.groupby(["sintoma", "atraso"]).size().rename("n").reset_index()
                    )

                # Recorte RIDE-DF: filtra em Arrow, antes de ir para o pandas
                mask = pc.or_(
                    pc.is_in(lote.column("ID_MUNICIP"), value_set=codigos),
                    pc.is_in(lote.column("ID_MN_RESI"), value_set=codigos),
                )
                filtrado = lote.filter(mask)
                if filtrado.num_rows:
                    ride_partes.append(filtrado.to_pandas())

    if not ride_partes:
        raise RuntimeError("Recorte da RIDE-DF vazio. O formato de ID_MUNICIP mudou?")

    ride = enriquecer(pd.concat(ride_partes, ignore_index=True))

    if contagens:
        cont = (
            pd.concat(contagens)
            .groupby(["sintoma", "atraso"], as_index=False)["n"].sum()
        )
    else:
        cont = pd.DataFrame({"sintoma": pd.to_datetime([]), "atraso": [], "n": []})
    cont["atraso"] = cont["atraso"].astype("int16")
    cont["n"] = cont["n"].astype("int32")
    return ride, cont, max_digita, total


# ---------------------------------------------------------------------------
# CURVA DE ATRASO (a partir das contagens guardadas)
# ---------------------------------------------------------------------------

def curva_atraso(contagens: pd.DataFrame, snapshot: pd.Timestamp) -> pd.DataFrame:
    """F(d) = proporção de casos digitados até d dias, em coortes nacionais maduras."""
    limite = snapshot - pd.Timedelta(days=MATURIDADE)
    maduras = contagens[contagens["sintoma"] <= limite]
    n = int(maduras["n"].sum())
    log.info("curva de atraso: %d casos maduros de %d nacionais", n, int(contagens["n"].sum()))
    if n == 0:
        raise RuntimeError("Nenhuma coorte madura para a curva de atraso.")
    if n < 1000:
        log.warning("poucas coortes maduras (%d); F(d) pode ser instável", n)

    por_dia = maduras.groupby("atraso")["n"].sum().reindex(range(D_MAX + 1), fill_value=0)
    return pd.DataFrame(
        {"dias": range(D_MAX + 1), "f": (por_dia.cumsum() / n).values, "n_base": n}
    )


# ---------------------------------------------------------------------------
# ESTADO / ARQUIVOS
# ---------------------------------------------------------------------------

def ler_estado() -> dict:
    if ESTADO.exists():
        return json.loads(ESTADO.read_text(encoding="utf-8"))
    return {"anos": {}}


def gravar_atomico(caminho: Path, escrever) -> None:
    tmp = caminho.with_suffix(caminho.suffix + ".part")
    escrever(tmp)
    tmp.replace(caminho)


def instante_remoto(meta: dict) -> pd.Timestamp | None:
    if not meta.get("last_modified"):
        return None
    return pd.Timestamp(parsedate_to_datetime(meta["last_modified"])).tz_convert(None).normalize()


def atualizar_ano(ano: int, meta: dict, estado: dict) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        zip_local = Path(tmpdir) / f"CHIKBR{ano % 100:02d}.csv.zip"
        log.info("Baixando %d ...", ano)
        baixar_zip(ano, zip_local)
        log.info("ZIP %d: %.1f MB", ano, zip_local.stat().st_size / 1_048_576)
        t0 = time.time()
        ride, cont, max_digita, total = processar_zip(zip_local)
        log.info(
            "%d: %d nacionais -> %d RIDE (%.1fs)", ano, total, len(ride), time.time() - t0
        )

    anterior = estado["anos"].get(str(ano), {}).get("linhas_ride")
    if anterior and len(ride) < anterior * (1 - QUEDA_MAXIMA):
        raise RuntimeError(
            f"{ano}: recorte caiu de {anterior} para {len(ride)} linhas "
            f"(> {QUEDA_MAXIMA:.0%}). Arquivo truncado? Use --forcar para aceitar."
        )

    # DT_DIGITA tem erros de digitação (já apareceu data em dez/2026 em out/2026):
    # o "snapshot" nunca pode ser posterior à publicação do arquivo.
    publicado = instante_remoto(meta)
    snapshot = max_digita
    if publicado is not None and pd.notna(snapshot):
        snapshot = min(snapshot, publicado)

    HIST.mkdir(parents=True, exist_ok=True)
    gravar_atomico(
        HIST / f"ride_{ano}.parquet",
        lambda p: ride.to_parquet(p, index=False, compression="zstd"),
    )
    gravar_atomico(
        HIST / f"atraso_{ano}.parquet",
        lambda p: cont.to_parquet(p, index=False, compression="zstd"),
    )
    estado["anos"][str(ano)] = {
        "etag": meta.get("etag", ""),
        "last_modified": meta.get("last_modified", ""),
        "linhas_ride": int(len(ride)),
        "linhas_nacional": int(total),
        "snapshot": snapshot.date().isoformat() if pd.notna(snapshot) else None,
        "atualizado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def gerar_nowcast(ride: pd.DataFrame, snapshot: pd.Timestamp, atraso_nacional: pd.DataFrame) -> None:
    """Nowcast de notificações + validação retrospectiva. Falha aqui não derruba o resto."""
    try:
        base = nowcast.preparar(ride, CODIGOS_6)
        ano_epi = max(base.semanas)[:4]
        serie = nowcast.serie_atual(base, snapshot, ano_epi)
        rng = nowcast.np.random.default_rng(nowcast.SEMENTE)
        pont, _ = nowcast.curva_boot(base, snapshot, rng)
        curva = pd.DataFrame({
            "dias": range(nowcast.D + 1), "f": pont,
            "n_coortes": len(nowcast.coortes_maduras(base, snapshot)),
            "snapshot": snapshot.date().isoformat(),
        })
        bt = nowcast.backtest(base, snapshot, atraso_nacional)
        validacao = nowcast.resumir(bt)
        validacao["snapshot"] = snapshot.date().isoformat()
    except Exception as erro:  # noqa: BLE001
        log.error("Nowcast não gerado (%s: %s); demais saídas seguem.", type(erro).__name__, erro)
        return

    serie = serie[["ano_epi", "se", "sem", "fim_semana", "observado", "f", "estimado", "lo", "hi"]].copy()
    serie["snapshot"] = snapshot.date().isoformat()
    for arq, tab in ((ARQ_NOWCAST, serie), (ARQ_ATRASO_LOCAL, curva), (ARQ_VALIDACAO, validacao)):
        gravar_atomico(arq, lambda p, t=tab: t.round(4).to_csv(p, index=False, encoding="utf-8"))
    geral = validacao[validacao["faixa_dias_desde_fim_semana"] == "todas"].set_index("metodo")
    for metodo, linha in geral.iterrows():
        log.info(
            "Backtest %-24s razão est/final %.2f | erro médio %.2f | cobertura IC95 %.0f%%",
            metodo, linha["razao_estimado_final"], linha["erro_medio_abs"], 100 * linha["cobertura_ic95"],
        )


def montar_saidas(anos: list[int], estado: dict) -> None:
    """Junta os caches por ano e grava os CSVs consumidos pelo painel."""
    ride = pd.concat(
        [pd.read_parquet(HIST / f"ride_{a}.parquet") for a in anos], ignore_index=True
    )
    cont = pd.concat([pd.read_parquet(HIST / f"atraso_{a}.parquet") for a in anos])
    cont = cont.groupby(["sintoma", "atraso"], as_index=False)["n"].sum()

    snapshots = [
        pd.Timestamp(estado["anos"][str(a)]["snapshot"])
        for a in anos
        if estado["anos"].get(str(a), {}).get("snapshot")
    ]
    if not snapshots:
        raise RuntimeError("Sem snapshot válido em nenhum ano.")
    snapshot = max(snapshots)
    log.info("Snapshot do DATASUS: %s", snapshot.date())

    atraso = curva_atraso(cont, snapshot)
    atraso_nacional = atraso.copy()
    atraso["snapshot"] = snapshot.date().isoformat()

    ride = ride.sort_values(["ANO_EPI", "SE"], kind="stable", na_position="last")

    SAIDA.mkdir(parents=True, exist_ok=True)
    gravar_atomico(ARQ_RIDE, lambda p: ride.to_csv(p, index=False, encoding="utf-8"))
    gravar_atomico(ARQ_ATRASO, lambda p: atraso.to_csv(p, index=False, encoding="utf-8"))
    gerar_nowcast(ride, snapshot, atraso_nacional)

    gravar_atomico(
        ESTADO,
        lambda p: p.write_text(
            json.dumps(estado, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        ),
    )

    log.info(
        "Recorte: %d notificações | %.1f MB | %s",
        len(ride), ARQ_RIDE.stat().st_size / 1_048_576, ARQ_RIDE,
    )
    for ano, n in ride["ANO_EPI"].value_counts().sort_index().items():
        log.info("  ANO_EPI %s: %5d", ano, n)

    presentes = set(ride["ID_MN_RESI"].dropna()) | set(ride["ID_MUNICIP"].dropna())
    sem_registro = sorted(NOME_POR_COD6[c] for c in CODIGOS_6 if c not in presentes)
    log.info("Municípios da RIDE sem nenhum registro: %d", len(sem_registro))
    if sem_registro:
        log.info("  %s", ", ".join(sem_registro))


# ---------------------------------------------------------------------------
# EXECUÇÃO
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--anos", type=int, nargs="*", default=[], help="reprocessa estes anos")
    ap.add_argument("--tudo", action="store_true", help="reconstrói todos os anos")
    ap.add_argument("--forcar", action="store_true", help="ignora ETag e trava de queda")
    args = ap.parse_args()

    ano_corrente = datetime.now(timezone.utc).year
    todos = list(range(ANO_INICIAL, ano_corrente + 1))
    estado = ler_estado()

    alvo = set(args.anos)
    if args.tudo:
        alvo |= set(todos)
    alvo.add(ano_corrente)
    # Anos sem cache (primeira execução, ou ano novo) entram automaticamente.
    alvo |= {a for a in todos if not (HIST / f"ride_{a}.parquet").exists()}

    mudou = False
    for ano in sorted(alvo):
        try:
            meta = metadados_remotos(ano)
        except AnoIndisponivel:
            log.warning("Arquivo de %d ainda não publicado; ignorando.", ano)
            continue

        em_cache = estado["anos"].get(str(ano), {})
        explicito = args.tudo or ano in args.anos or args.forcar
        if (
            not explicito
            and (HIST / f"ride_{ano}.parquet").exists()
            and meta["etag"]
            and meta["etag"] == em_cache.get("etag")
        ):
            log.info("%d: sem alterações na fonte (ETag %s).", ano, meta["etag"][:8])
            continue

        try:
            atualizar_ano(ano, meta, estado)
            mudou = True
        except AnoIndisponivel:
            log.warning("Arquivo de %d ainda não publicado; ignorando.", ano)

    anos_disponiveis = [a for a in todos if (HIST / f"ride_{a}.parquet").exists()]
    if not anos_disponiveis:
        raise RuntimeError("Nenhum ano disponível.")
    if ANO_INICIAL not in anos_disponiveis:
        raise RuntimeError(f"Falta o cache de {ANO_INICIAL}; rode com --tudo.")

    if not mudou and ARQ_RIDE.exists() and ARQ_ATRASO.exists():
        log.info("Nada a fazer: dados já estão atualizados.")
        return

    montar_saidas(anos_disponiveis, estado)


if __name__ == "__main__":
    try:
        main()
    except Exception as erro:
        log.exception("ERRO FATAL: %s: %s", type(erro).__name__, erro)
        sys.exit(1)

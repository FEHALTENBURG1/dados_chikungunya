#!/usr/bin/env python3
"""
Filtra os CSVs nacionais de Chikungunya para os municípios da RIDE-DF.

Gera:
  dados/chikungunya_ride.csv
  dados/atraso_nacional.csv

Revisão laboratorial:
  - inclui datas e resultados de sorologia de chikungunya e RT-PCR;
  - calcula o intervalo entre início dos sintomas e coleta do RT-PCR;
  - considera o mesmo dia do início dos sintomas como o 1º dia clínico;
  - classifica a coleta em 1º–5º dia, 6º–10º dia e após o 10º dia.

Observação:
  DT_SORO/RESUL_SORO pertencem à sorologia de dengue na ficha conjunta.
  Para chikungunya, utilizam-se DT_CHIK_S1, DT_CHIK_S2, DT_PRNT,
  RES_CHIKS1, RES_CHIKS2 e RESUL_PRNT.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pandas as pd

from municipios_ride import CODIGOS_6, NOME_POR_COD6, UF_POR_COD6

ENTRADA = Path("./bruto")
SAIDA = Path("./dados")
ARQ_RIDE = SAIDA / "chikungunya_ride.csv"
ARQ_ATRASO = SAIDA / "atraso_nacional.csv"

MATURIDADE = 120
D_MAX = 200

COLUNAS = [
    # Tempo
    "NU_ANO",
    "DT_NOTIFIC",
    "SEM_NOT",
    "DT_SIN_PRI",
    "SEM_PRI",
    "DT_DIGITA",
    # Lugar
    "SG_UF_NOT",
    "ID_MUNICIP",
    "SG_UF",
    "ID_MN_RESI",
    # Pessoa
    "NU_IDADE_N",
    "CS_SEXO",
    "CS_GESTANT",
    "CS_RACA",
    "CS_ESCOL_N",
    # Desfecho
    "CLASSI_FIN",
    "CRITERIO",
    "EVOLUCAO",
    "DT_OBITO",
    "DT_ENCERRA",
    "HOSPITALIZ",
    "DT_INVEST",
    # Laboratório — sorologia de chikungunya
    "DT_CHIK_S1",
    "DT_CHIK_S2",
    "DT_PRNT",
    "RES_CHIKS1",
    "RES_CHIKS2",
    "RESUL_PRNT",
    # Laboratório — campos de dengue mantidos para auditoria da ficha conjunta
    "DT_SORO",
    "RESUL_SORO",
    # Laboratório — RT-PCR
    "DT_PCR",
    "RESUL_PCR_",
    # Sinais e sintomas
    "FEBRE",
    "MIALGIA",
    "CEFALEIA",
    "ARTRALGIA",
    "ARTRITE",
    "EXANTEMA",
    "DOR_RETRO",
    "NAUSEA",
    "VOMITO",
    "CONJUNTVIT",
    # Comorbidades
    "HIPERTENSA",
    "DIABETES",
    "RENAL",
    "AUTO_IMUNE",
    "HEPATOPAT",
    "ACIDO_PEPT",
    "HEMATOLOG",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("ride")


def parse_data(serie: pd.Series) -> pd.Series:
    """Converte datas sem alterar a coluna original usada na exportação."""
    return pd.to_datetime(serie, errors="coerce", dayfirst=True)


def curva_atraso(nacional: pd.DataFrame, snapshot: pd.Timestamp) -> pd.DataFrame:
    """Calcula F(d) em coortes nacionais maduras."""
    d = nacional.dropna(subset=["_sintoma", "_digita"]).copy()
    d["atraso"] = (d["_digita"] - d["_sintoma"]).dt.days
    d = d[(d["atraso"] >= 0) & (d["atraso"] <= D_MAX)]

    maduras = d[(snapshot - d["_sintoma"]).dt.days >= MATURIDADE]
    log.info(
        "curva de atraso: %d casos maduros de %d nacionais",
        len(maduras),
        len(d),
    )

    if len(maduras) < 1000:
        log.warning("poucas coortes maduras (%d); F(d) pode ser instável", len(maduras))
    if maduras.empty:
        raise RuntimeError("Nenhuma coorte madura. Baixe anos anteriores.")

    n = len(maduras)
    return pd.DataFrame(
        {
            "dias": range(D_MAX + 1),
            "f": [(maduras["atraso"] <= dia).mean() for dia in range(D_MAX + 1)],
            "n_base": n,
        }
    )


def adicionar_oportunidade_pcr(ride: pd.DataFrame) -> pd.DataFrame:
    """Acrescenta variáveis auditáveis de oportunidade da coleta do RT-PCR."""
    ride = ride.copy()

    if "DT_PCR" not in ride.columns:
        ride["DT_PCR"] = pd.NA

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


def processar(caminho: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    df = pd.read_csv(caminho, dtype=str, low_memory=False)

    for coluna in ("ID_MUNICIP", "ID_MN_RESI", "SEM_PRI"):
        if coluna not in df.columns:
            raise KeyError(f"{caminho.name}: coluna {coluna} ausente. Layout mudou?")

    df["_sintoma"] = parse_data(df.get("DT_SIN_PRI"))
    df["_digita"] = parse_data(df.get("DT_DIGITA"))

    mask = df["ID_MUNICIP"].isin(CODIGOS_6) | df["ID_MN_RESI"].isin(CODIGOS_6)
    ride = df.loc[mask].copy()

    cols = [coluna for coluna in COLUNAS if coluna in ride.columns]
    ausentes = sorted(set(COLUNAS) - set(cols))
    if ausentes:
        log.warning("%s: colunas ausentes: %s", caminho.name, ausentes)

    ride = ride[cols].copy()

    # Semana epidemiológica derivada diretamente de SEM_PRI.
    ride["ANO_EPI"] = ride["SEM_PRI"].str[:4]
    ride["SE"] = ride["SEM_PRI"].str[4:6]

    ano_arq = ride["NU_ANO"].mode()
    if len(ano_arq):
        fora = (ride["ANO_EPI"] != ano_arq.iloc[0]).sum()
        if fora:
            log.info(
                "%s: %d casos com ANO_EPI diferente de NU_ANO; atribuídos pelo SEM_PRI",
                caminho.name,
                fora,
            )

    ride["MUN_NOTIF_NOME"] = ride["ID_MUNICIP"].map(NOME_POR_COD6)
    ride["MUN_RESI_NOME"] = ride["ID_MN_RESI"].map(NOME_POR_COD6)
    ride["MUN_RESI_UF"] = ride["ID_MN_RESI"].map(UF_POR_COD6)
    ride["FORA_DO_MUN"] = (ride["ID_MUNICIP"] != ride["ID_MN_RESI"]).map(
        {True: "1", False: "0"}
    )

    ride = adicionar_oportunidade_pcr(ride)

    log.info(
        "%s: %6d nacionais -> %5d RIDE",
        caminho.name,
        len(df),
        len(ride),
    )

    return ride, df[["_sintoma", "_digita"]]


def main() -> None:
    if not ENTRADA.exists():
        raise FileNotFoundError(f"{ENTRADA}/ não existe. Rode baixar_chikungunya.py.")

    arquivos = sorted(ENTRADA.glob("CHIKBR*.csv"))
    if not arquivos:
        raise FileNotFoundError(f"Nenhum CHIKBR*.csv em {ENTRADA}/.")

    partes: list[pd.DataFrame] = []
    nacionais: list[pd.DataFrame] = []

    for arquivo in arquivos:
        recorte, nacional = processar(arquivo)
        partes.append(recorte)
        nacionais.append(nacional)

    ride = pd.concat(partes, ignore_index=True)
    nacional = pd.concat(nacionais, ignore_index=True)

    if ride.empty:
        raise RuntimeError(
            "Recorte da RIDE ficou vazio. O formato de ID_MUNICIP pode ter mudado."
        )

    snapshot = nacional["_digita"].max()
    if pd.isna(snapshot):
        raise RuntimeError("Não foi possível determinar a última data de digitação.")

    log.info("Snapshot do DATASUS: %s", snapshot.date())

    atraso = curva_atraso(nacional, snapshot)
    atraso["snapshot"] = snapshot.date().isoformat()

    ride = ride.sort_values(["ANO_EPI", "SE"], na_position="last")

    SAIDA.mkdir(parents=True, exist_ok=True)
    ride.to_csv(ARQ_RIDE, index=False, encoding="utf-8")
    atraso.to_csv(ARQ_ATRASO, index=False, encoding="utf-8")

    tamanho_mb = ARQ_RIDE.stat().st_size / 1_048_576
    log.info("Recorte: %d notificações | %.1f MB | %s", len(ride), tamanho_mb, ARQ_RIDE)

    if tamanho_mb > 50:
        log.warning("Arquivo acima de 50 MB; monitore o limite do GitHub.")

    log.info("Por ano epidemiológico:")
    for ano, n in ride["ANO_EPI"].value_counts().sort_index().items():
        log.info("%s: %5d", ano, n)

    if "JANELA_PCR" in ride.columns:
        log.info("Oportunidade das coletas de RT-PCR:")
        for categoria, n in ride["JANELA_PCR"].value_counts(dropna=False).items():
            log.info("%s: %d", categoria, n)

    presentes = set(ride["ID_MN_RESI"].dropna()) | set(ride["ID_MUNICIP"].dropna())
    sem_registro = sorted(
        NOME_POR_COD6[codigo] for codigo in CODIGOS_6 if codigo not in presentes
    )
    log.info("Municípios da RIDE sem nenhum registro: %d", len(sem_registro))
    if sem_registro:
        log.info("%s", ", ".join(sem_registro))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log.error("ERRO FATAL: %s: %s", type(exc).__name__, exc)
        sys.exit(1)

#!/usr/bin/env python3
"""
Filtra os CSVs nacionais de Chikungunya para os 34 municipios da RIDE-DF.

Gera:
  dados/chikungunya_ride.csv   -> recorte da RIDE (o painel le daqui)
  dados/atraso_nacional.csv    -> curva de atraso de digitacao, base NACIONAL

Sobre a curva de atraso: o nowcasting precisa da distribuicao F(d) = proporcao
de casos ja digitados d dias apos o inicio dos sintomas. Estimar isso so com os
~500 casos/ano da RIDE da um resultado instavel. O atraso e propriedade do
SISTEMA de digitacao, nao da doenca, entao estimamos na base nacional (~100 mil
casos/ano) e aplicamos a RIDE.

SEMANA EPIDEMIOLOGICA — cuidado:
  - A SE brasileira vai de DOMINGO a SABADO. NAO e a semana ISO (que comeca na
    segunda). 85% dos registros divergem entre as duas convencoes.
  - O campo SEM_PRI do DATASUS ja vem calculado na convencao brasileira.
    USE-O DIRETO. Nunca recalcule com isocalendar()/lubridate::isoweek().
  - SEM_PRI carrega o proprio ano: "202553" = SE 53 de 2025.
  - NU_ANO e o ano da NOTIFICACAO, nao o ano epidemiologico. Um caso com
    sintomas na SE 53/2025 notificado em janeiro/2026 tem NU_ANO=2026 e
    SEM_PRI=202553. Agrupar por NU_ANO joga esse caso no ano errado.
    => ANO_EPI vem de SEM_PRI[:4], nunca de NU_ANO.
"""

import sys
import logging
from pathlib import Path

import pandas as pd

from municipios_ride import CODIGOS_6, NOME_POR_COD6, UF_POR_COD6

ENTRADA = Path("./bruto")
SAIDA = Path("./dados")
ARQ_RIDE = SAIDA / "chikungunya_ride.csv"
ARQ_ATRASO = SAIDA / "atraso_nacional.csv"

# Coortes com sintomas ha >= MATURIDADE dias tem o atraso ja completo.
# Estimar F(d) com todos os casos enviesaria para atrasos curtos: os casos
# lentos das semanas recentes ainda nao apareceram no arquivo (truncamento
# a direita).
MATURIDADE = 120
D_MAX = 200

COLUNAS = [
    # tempo
    "NU_ANO", "DT_NOTIFIC", "SEM_NOT", "DT_SIN_PRI", "SEM_PRI",
    "DT_DIGITA",                      # necessaria para o nowcasting
    # lugar — as duas dimensoes, separadas
    "SG_UF_NOT", "ID_MUNICIP",
    "SG_UF", "ID_MN_RESI",
    # pessoa
    "NU_IDADE_N", "CS_SEXO", "CS_GESTANT", "CS_RACA",
    # desfecho
    "CLASSI_FIN", "CRITERIO", "EVOLUCAO", "DT_OBITO", "DT_ENCERRA",
    "HOSPITALIZ", "DT_INVEST",
    # laboratorio
    "RES_CHIKS1", "RES_CHIKS2", "RESUL_PRNT", "RESUL_SORO", "RESUL_PCR_",
]

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("ride")


def curva_atraso(nacional: pd.DataFrame, snapshot: pd.Timestamp) -> pd.DataFrame:
    """F(d) na base nacional, estimada apenas em coortes maduras."""
    d = nacional.dropna(subset=["_sintoma", "_digita"]).copy()
    d["atraso"] = (d["_digita"] - d["_sintoma"]).dt.days
    d = d[(d.atraso >= 0) & (d.atraso <= D_MAX)]

    maduras = d[(snapshot - d["_sintoma"]).dt.days >= MATURIDADE]
    log.info("  curva de atraso: %d casos maduros de %d nacionais",
             len(maduras), len(d))

    if len(maduras) < 1000:
        log.warning("  poucas coortes maduras (%d). F(d) pode ser instavel.",
                    len(maduras))
    if maduras.empty:
        raise RuntimeError("Nenhuma coorte madura. Baixe anos anteriores.")

    n = len(maduras)
    return pd.DataFrame({
        "dias": range(D_MAX + 1),
        "f": [(maduras.atraso <= dd).mean() for dd in range(D_MAX + 1)],
        "n_base": n,
    })


def processar(caminho: Path):
    df = pd.read_csv(caminho, dtype=str, low_memory=False)

    for c in ("ID_MUNICIP", "ID_MN_RESI", "SEM_PRI"):
        if c not in df.columns:
            raise KeyError(f"{caminho.name}: coluna {c} ausente. Layout mudou?")

    df["_sintoma"] = pd.to_datetime(df.get("DT_SIN_PRI"), errors="coerce")
    df["_digita"] = pd.to_datetime(df.get("DT_DIGITA"), errors="coerce")

    mask = df["ID_MUNICIP"].isin(CODIGOS_6) | df["ID_MN_RESI"].isin(CODIGOS_6)
    ride = df[mask].copy()

    cols = [c for c in COLUNAS if c in ride.columns]
    ausentes = set(COLUNAS) - set(cols)
    if ausentes:
        log.warning("  %s: colunas ausentes: %s", caminho.name, sorted(ausentes))
    ride = ride[cols]

    # ---- Semana epidemiologica: SEMPRE derivada de SEM_PRI -----------------
    ride["ANO_EPI"] = ride["SEM_PRI"].str[:4]
    ride["SE"] = ride["SEM_PRI"].str[4:6]

    # Quantos casos caem em ano epidemiologico != ano do arquivo?
    ano_arq = ride["NU_ANO"].mode()
    if len(ano_arq):
        fora = (ride["ANO_EPI"] != ano_arq[0]).sum()
        if fora:
            log.info("  %s: %d casos com ANO_EPI != NU_ANO "
                     "(sintomas no ano anterior) — atribuidos ao ano correto",
                     caminho.name, fora)

    ride["MUN_NOTIF_NOME"] = ride["ID_MUNICIP"].map(NOME_POR_COD6)
    ride["MUN_RESI_NOME"] = ride["ID_MN_RESI"].map(NOME_POR_COD6)
    ride["MUN_RESI_UF"] = ride["ID_MN_RESI"].map(UF_POR_COD6)
    ride["FORA_DO_MUN"] = (ride["ID_MUNICIP"] != ride["ID_MN_RESI"]).map(
        {True: "1", False: "0"})

    log.info("  %s: %6d nacionais -> %5d RIDE", caminho.name, len(df), len(ride))
    return ride, df[["_sintoma", "_digita"]]


def main() -> None:
    if not ENTRADA.exists():
        raise FileNotFoundError(f"{ENTRADA}/ nao existe. Rode baixar_chikungunya.py.")

    arquivos = sorted(ENTRADA.glob("CHIKBR*.csv"))
    if not arquivos:
        raise FileNotFoundError(f"Nenhum CHIKBR*.csv em {ENTRADA}/.")

    partes, nacionais = [], []
    for a in arquivos:
        r, n = processar(a)
        partes.append(r)
        nacionais.append(n)

    ride = pd.concat(partes, ignore_index=True)
    nacional = pd.concat(nacionais, ignore_index=True)

    if ride.empty:
        raise RuntimeError(
            "Recorte da RIDE ficou VAZIO. O formato de ID_MUNICIP mudou? "
            "Esperado codigo IBGE de 6 digitos.")

    snapshot = nacional["_digita"].max()
    log.info("")
    log.info("Snapshot do DATASUS (ultima digitacao): %s", snapshot.date())

    atraso = curva_atraso(nacional, snapshot)
    atraso["snapshot"] = snapshot.date().isoformat()

    ride = ride.sort_values(["ANO_EPI", "SE"], na_position="last")

    SAIDA.mkdir(parents=True, exist_ok=True)
    ride.to_csv(ARQ_RIDE, index=False, encoding="utf-8")
    atraso.to_csv(ARQ_ATRASO, index=False, encoding="utf-8")

    tam = ARQ_RIDE.stat().st_size / 1_048_576
    log.info("")
    log.info("Recorte: %d notificacoes | %.1f MB | %s", len(ride), tam, ARQ_RIDE)
    if tam > 50:
        log.warning("Acima de 50 MB — cuidado com o limite de 100 MB do GitHub.")

    log.info("")
    log.info("Por ano EPIDEMIOLOGICO (de SEM_PRI, nao de NU_ANO):")
    for ano, n in ride["ANO_EPI"].value_counts().sort_index().items():
        log.info("  %s: %5d", ano, n)

    log.info("")
    log.info("Atraso de digitacao (nacional, coortes maduras):")
    for dias in (7, 14, 21, 28, 42):
        f = atraso.loc[atraso.dias == dias, "f"].iloc[0]
        log.info("  ate %2d dias: %5.1f%% digitado", dias, 100 * f)

    presentes = set(ride["ID_MN_RESI"].dropna()) | set(ride["ID_MUNICIP"].dropna())
    sem_reg = sorted(NOME_POR_COD6[c] for c in CODIGOS_6 if c not in presentes)
    log.info("")
    log.info("Municipios da RIDE sem nenhum registro: %d de 34", len(sem_reg))
    if sem_reg:
        log.info("  %s", ", ".join(sem_reg))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.error("ERRO FATAL: %s: %s", type(e).__name__, e)
        sys.exit(1)

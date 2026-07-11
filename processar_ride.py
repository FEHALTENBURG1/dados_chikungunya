#!/usr/bin/env python3
"""
Filtra os CSVs nacionais de Chikungunya (baixados por baixar_chikungunya.py)
para os 34 municipios da RIDE-DF e grava um recorte enxuto em dados/.

Criterio de recorte: UNIAO — mantem a notificacao se o municipio de NOTIFICACAO
(ID_MUNICIP) OU o de RESIDENCIA (ID_MN_RESI) pertencer a RIDE. As duas colunas
sao preservadas separadamente para o painel escolher o denominador.

Mantem TODAS as notificacoes (nao filtra por CLASSI_FIN) — a classificacao fica
disponivel para o painel filtrar.

Formato do Dados Abertos (verificado em CHIKBR26.csv):
  - separador VIRGULA (nao ';' como no microdado .dbc)
  - encoding UTF-8/ASCII (nao latin1)
  - ID_MUNICIP / ID_MN_RESI com 6 digitos (IBGE sem digito verificador)
  - datas em ISO (AAAA-MM-DD)
"""

import sys
import logging
from pathlib import Path

import pandas as pd

from municipios_ride import CODIGOS_6, NOME_POR_COD6, UF_POR_COD6

ENTRADA = Path("./bruto")
SAIDA = Path("./dados")
ARQUIVO_SAIDA = SAIDA / "chikungunya_ride.csv"

# Colunas mantidas no recorte. Reduzir de 122 para o essencial derruba o tamanho
# do arquivo em ~10x. Acrescente aqui o que o painel precisar.
COLUNAS = [
    # identificacao temporal
    "NU_ANO", "DT_NOTIFIC", "SEM_NOT", "DT_SIN_PRI", "SEM_PRI",
    # lugar — as duas dimensoes, separadas
    "SG_UF_NOT", "ID_MUNICIP",      # notificacao
    "SG_UF", "ID_MN_RESI",          # residencia
    # pessoa
    "NU_IDADE_N", "CS_SEXO", "CS_GESTANT", "CS_RACA",
    # desfecho e classificacao
    "CLASSI_FIN", "CRITERIO", "EVOLUCAO", "DT_OBITO", "DT_ENCERRA",
    "HOSPITALIZ", "DT_INVEST",
    # laboratorio
    "RES_CHIKS1", "RES_CHIKS2", "RESUL_PRNT", "RESUL_SORO", "RESUL_PCR_",
]

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"
)
log = logging.getLogger("ride")


def processar(caminho: Path) -> pd.DataFrame:
    df = pd.read_csv(caminho, dtype=str, low_memory=False)

    faltando = [c for c in ("ID_MUNICIP", "ID_MN_RESI") if c not in df.columns]
    if faltando:
        raise KeyError(f"{caminho.name}: colunas ausentes {faltando}. Layout mudou?")

    mask = df["ID_MUNICIP"].isin(CODIGOS_6) | df["ID_MN_RESI"].isin(CODIGOS_6)
    ride = df[mask].copy()

    cols = [c for c in COLUNAS if c in ride.columns]
    ausentes = set(COLUNAS) - set(cols)
    if ausentes:
        log.warning("  %s: colunas pedidas mas ausentes: %s", caminho.name, sorted(ausentes))
    ride = ride[cols]

    # Nomes legiveis, para o painel nao precisar de outra tabela de-para
    ride["MUN_NOTIF_NOME"] = ride["ID_MUNICIP"].map(NOME_POR_COD6)
    ride["MUN_RESI_NOME"] = ride["ID_MN_RESI"].map(NOME_POR_COD6)
    ride["MUN_RESI_UF"] = ride["ID_MN_RESI"].map(UF_POR_COD6)

    # Flag util: o caso foi notificado fora do municipio onde mora?
    ride["FORA_DO_MUN"] = (ride["ID_MUNICIP"] != ride["ID_MN_RESI"]).map({True: "1", False: "0"})

    log.info("  %s: %6d nacionais -> %5d RIDE", caminho.name, len(df), len(ride))
    return ride


def main() -> None:
    if not ENTRADA.exists():
        raise FileNotFoundError(f"{ENTRADA}/ nao existe. Rode baixar_chikungunya.py antes.")

    arquivos = sorted(ENTRADA.glob("CHIKBR*.csv"))
    if not arquivos:
        raise FileNotFoundError(f"Nenhum CHIKBR*.csv em {ENTRADA}/.")

    partes = [processar(a) for a in arquivos]
    ride = pd.concat(partes, ignore_index=True)

    if ride.empty:
        # Recorte vazio significa que o formato do codigo de municipio mudou.
        # Falhar aqui evita commitar um CSV vazio e congelar o painel.
        raise RuntimeError(
            "Recorte da RIDE ficou VAZIO. O formato de ID_MUNICIP mudou? "
            "Esperado codigo IBGE de 6 digitos."
        )

    ride = ride.sort_values(["NU_ANO", "DT_NOTIFIC"], na_position="last")

    SAIDA.mkdir(parents=True, exist_ok=True)
    ride.to_csv(ARQUIVO_SAIDA, index=False, encoding="utf-8")

    tam = ARQUIVO_SAIDA.stat().st_size / 1_048_576
    log.info("")
    log.info("Recorte: %d notificacoes | %.1f MB | %s", len(ride), tam, ARQUIVO_SAIDA)

    if tam > 50:
        log.warning("Arquivo acima de 50 MB — cuidado com o limite de 100 MB do GitHub.")

    # Resumo por ano, util para conferir no log do Actions se um ano sumiu
    log.info("")
    log.info("Por ano de notificacao:")
    for ano, n in ride["NU_ANO"].value_counts().sort_index().items():
        log.info("  %s: %5d", ano, n)

    # Municipios sem registro: esperado para os pequenos, mas se TODOS sumirem e bug
    presentes = set(ride["ID_MN_RESI"].dropna()) | set(ride["ID_MUNICIP"].dropna())
    sem_registro = sorted(NOME_POR_COD6[c] for c in CODIGOS_6 if c not in presentes)
    log.info("")
    log.info("Municipios da RIDE sem nenhum registro: %d de 34", len(sem_registro))
    if sem_registro:
        log.info("  %s", ", ".join(sem_registro))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.error("ERRO FATAL: %s: %s", type(e).__name__, e)
        sys.exit(1)

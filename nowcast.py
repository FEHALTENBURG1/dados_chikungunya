#!/usr/bin/env python3
"""
Nowcasting do atraso de digitação — notificações de chikungunya na RIDE-DF.

Por que existe
--------------
As semanas mais recentes aparecem incompletas porque a digitação no SINAN leva
dias ou semanas. Este módulo estima quantas notificações cada semana terá quando
a digitação terminar, com intervalo de incerteza, e mede por backtest se a
estimativa é melhor do que simplesmente usar o valor observado.

Método (em vez de "observado / curva nacional", como antes)
-----------------------------------------------------------
1. Curva de atraso LOCAL: distribuição de (DT_DIGITA − DT_SIN_PRI) das notificações
   de residentes da RIDE-DF, em coortes maduras (fim da semana há ≥ D dias) dos
   últimos 2 anos.
2. Completude por semana: média da curva sobre os 7 dias da semana (cada caso tem
   o seu próprio tempo decorrido; antes todos eram tratados como se tivessem
   adoecido no sábado).
3. Modelo Poisson–Gama / binomial negativo: dado x casos digitados e completude
   F, os ainda não digitados seguem uma binomial negativa. A média a priori (opcional)
   vem das semanas recentes quase completas e ancora semanas com muito pouca informação.
4. Incerteza da própria curva: bootstrap por semana-coorte (a curva varia de
   semana para semana). Cada sorteio usa uma curva diferente.
5. Validação retrospectiva: para cada data de corte passada, usa só o que estava
   digitado naquela data e compara com o valor final. Publica viés, erro médio
   e cobertura do intervalo, por método e por faixa de recência.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

log = logging.getLogger("chik.nowcast")

D = 120  # atraso máximo modelado (dias); acima disso, a completude é 1
JANELA_COORTES = 104  # semanas de coortes maduras usadas na curva (2 anos)
BOOT = 300  # sorteios do bootstrap
F_MIN = 0.20  # abaixo disso não se publica estimativa corrigida
PHI = 0.5  # dispersão da priori (CV² da média semanal); maior = priori mais fraca
SEMENTE = 2026


# ---------------------------------------------------------------------------
# Preparação
# ---------------------------------------------------------------------------

def fim_da_semana_epi(sem: str) -> pd.Timestamp:
    """Sábado da semana epidemiológica 'AAAASS' (SE 1 = semana com ≥ 4 dias no ano)."""
    ano, semana = int(sem[:4]), int(sem[4:6])
    jan1 = pd.Timestamp(ano, 1, 1)
    dom = (jan1.dayofweek + 1) % 7  # dias desde o domingo
    inicio = jan1 - pd.Timedelta(days=dom)
    if dom > 3:
        inicio += pd.Timedelta(days=7)
    return inicio + pd.Timedelta(days=7 * (semana - 1) + 6)


@dataclass
class Base:
    casos: pd.DataFrame  # sem, fim, digita, atraso
    semanas: list[str]  # ordenadas
    fim: dict[str, pd.Timestamp]
    digita_ord: dict[str, np.ndarray]  # datas de digitação (int64 dias), ordenadas
    hist: pd.DataFrame  # linhas = semanas, colunas = atraso 0..D (só atraso ≤ D)
    final: pd.Series  # total de notificações por semana


def preparar(ride: pd.DataFrame, codigos_residencia: set[str]) -> Base:
    d = ride.loc[ride["ID_MN_RESI"].isin(codigos_residencia),
                 ["SEM_PRI", "DT_SIN_PRI", "DT_DIGITA"]].copy()
    d["onset"] = pd.to_datetime(d["DT_SIN_PRI"], errors="coerce", format="%Y-%m-%d")
    d["digita"] = pd.to_datetime(d["DT_DIGITA"], errors="coerce", format="%Y-%m-%d")
    d = d[d["SEM_PRI"].str.fullmatch(r"\d{6}") & d["onset"].notna() & d["digita"].notna()]
    d = d.rename(columns={"SEM_PRI": "sem"})
    d["atraso"] = (d["digita"] - d["onset"]).dt.days.clip(lower=0)
    semanas = sorted(d["sem"].unique())
    fim = {s: fim_da_semana_epi(s) for s in semanas}
    d["fim"] = d["sem"].map(fim)

    digita_ord = {
        s: np.sort(g["digita"].values.astype("datetime64[D]").astype("int64"))
        for s, g in d.groupby("sem")
    }
    dentro = d[d["atraso"] <= D]
    hist = (
        dentro.groupby(["sem", "atraso"]).size().unstack(fill_value=0)
        .reindex(index=semanas, columns=range(D + 1), fill_value=0)
    )
    final = d.groupby("sem").size().reindex(semanas)
    return Base(d, semanas, fim, digita_ord, hist, final)


# ---------------------------------------------------------------------------
# Curva de atraso (com bootstrap por coorte)
# ---------------------------------------------------------------------------

def coortes_maduras(base: Base, corte: pd.Timestamp) -> list[str]:
    limite = corte - pd.Timedelta(days=D)
    ok = [s for s in base.semanas if base.fim[s] <= limite]
    return ok[-JANELA_COORTES:]


def curva_boot(base: Base, corte: pd.Timestamp, rng: np.random.Generator,
               n_boot: int = BOOT) -> tuple[np.ndarray, np.ndarray] | None:
    """Devolve (curva pontual [D+1], curvas bootstrap [n_boot, D+1]) ou None."""
    sem = coortes_maduras(base, corte)
    if len(sem) < 20:
        return None
    M = base.hist.loc[sem].values.astype(float)
    if M.sum() < 150:
        return None
    pont = np.cumsum(M.sum(axis=0)) / M.sum()
    idx = rng.integers(0, len(sem), size=(n_boot, len(sem)))
    somas = M[idx].sum(axis=1)  # [n_boot, D+1]
    cb = np.cumsum(somas, axis=1) / somas.sum(axis=1, keepdims=True)
    return pont, cb


def dias_decorridos(fim: pd.Timestamp, corte: pd.Timestamp) -> np.ndarray:
    """Tempo decorrido (dias) desde cada um dos 7 dias da semana até o corte."""
    return np.clip(np.array([(corte - (fim - pd.Timedelta(days=6 - j))).days for j in range(7)]), 0, D)


def completude(curva: np.ndarray, idx: np.ndarray) -> np.ndarray:
    """Média da curva sobre os 7 dias; curva pode ser [D+1] ou [B, D+1]."""
    return curva[..., idx].mean(axis=-1)


# ---------------------------------------------------------------------------
# Estimativa de uma semana
# ---------------------------------------------------------------------------

def casos_conhecidos(base: Base, sem: str, corte: pd.Timestamp) -> int:
    arr = base.digita_ord[sem]
    return int(np.searchsorted(arr, np.datetime64(corte, "D").astype("int64"), side="right"))


def priori_media(base: Base, sem: str, corte: pd.Timestamp, pont: np.ndarray) -> float | None:
    """Média das 4 semanas anteriores quase completas (F ≥ 0,9), corrigida por F."""
    i = base.semanas.index(sem)
    vals = []
    for s in reversed(base.semanas[:i]):
        F = float(completude(pont, dias_decorridos(base.fim[s], corte)))
        if F >= 0.9:
            vals.append(casos_conhecidos(base, s, corte) / F)
        if len(vals) == 4:
            break
    return float(np.mean(vals)) if len(vals) >= 2 else None


def estimar_semana(base: Base, sem: str, corte: pd.Timestamp, curvas, rng,
                   usar_priori: bool = True) -> dict:
    pont, cb = curvas
    x = casos_conhecidos(base, sem, corte)
    idx = dias_decorridos(base.fim[sem], corte)
    F = float(completude(pont, idx))
    saida = {"sem": sem, "observado": x, "f": F, "estimado": np.nan, "lo": np.nan, "hi": np.nan}
    if F >= 0.995:
        saida.update(estimado=x, lo=x, hi=x)
        return saida
    if F < F_MIN:
        return saida

    alfa, beta = 0.5, 0.0  # priori praticamente não informativa
    if usar_priori:
        mu = priori_media(base, sem, corte, pont)
        if mu is not None:
            mu = max(mu, 0.5)
            alfa, beta = 1.0 / PHI, 1.0 / (PHI * mu)

    Fb = np.clip(completude(cb, idx), 1e-3, 1.0)  # [B]
    p = (beta + Fb) / (beta + 1.0)
    nao_digitados = rng.negative_binomial(alfa + x, p)
    total = x + nao_digitados
    saida.update(
        estimado=float(np.median(total)),
        lo=float(np.percentile(total, 2.5)),
        hi=float(np.percentile(total, 97.5)),
    )
    return saida


# ---------------------------------------------------------------------------
# Série atual (para o painel)
# ---------------------------------------------------------------------------

def serie_atual(base: Base, corte: pd.Timestamp, ano_epi: str, usar_priori: bool = True) -> pd.DataFrame:
    rng = np.random.default_rng(SEMENTE)
    curvas = curva_boot(base, corte, rng)
    if curvas is None:
        raise RuntimeError("Poucas coortes maduras para estimar a curva de atraso local.")
    # Semanas ainda sem nenhuma notificação digitada também precisam existir na série.
    for w in range(1, 54):
        s = f"{ano_epi}{w:02d}"
        if s not in base.fim and fim_da_semana_epi(s) - pd.Timedelta(days=6) < corte:
            base.fim[s] = fim_da_semana_epi(s)
            base.digita_ord[s] = np.array([], dtype="int64")
            base.semanas = sorted([*base.semanas, s])
    linhas = []
    for s in base.semanas:
        if s[:4] != ano_epi or base.fim[s] - pd.Timedelta(days=6) >= corte:
            continue
        r = estimar_semana(base, s, corte, curvas, rng, usar_priori)
        r["fim_semana"] = base.fim[s].date().isoformat()
        r["n_coortes_curva"] = len(coortes_maduras(base, corte))
        linhas.append(r)
    df = pd.DataFrame(linhas)
    df["ano_epi"] = df["sem"].str[:4]
    df["se"] = df["sem"].str[4:].astype(int)
    return df


# ---------------------------------------------------------------------------
# Backtest
# ---------------------------------------------------------------------------

def backtest(base: Base, corte_final: pd.Timestamp, f_nacional: pd.DataFrame | None,
             semanas_corte: int = 60, n_boot: int = 200) -> pd.DataFrame:
    """
    Para cada sábado de corte no passado, usa só o que estava digitado até o corte
    e compara a estimativa das 7 últimas semanas com o total final (≥ 75 dias depois).
    """
    rng = np.random.default_rng(SEMENTE)
    f_nac = None if f_nacional is None else dict(zip(f_nacional["dias"], f_nacional["f"]))
    ultimo = corte_final - pd.Timedelta(days=75)
    cortes = pd.date_range(end=ultimo, periods=semanas_corte, freq="7D")
    linhas = []
    for t in cortes:
        curvas = curva_boot(base, t, rng, n_boot)
        if curvas is None:
            continue
        for s in base.semanas:
            fim = base.fim[s]
            if not (t - pd.Timedelta(days=49) <= fim <= t):
                continue
            if (corte_final - fim).days < 75:
                continue
            final = int(base.final[s])
            dias = (t - fim).days
            x = casos_conhecidos(base, s, t)
            base_row = {"corte": t, "sem": s, "dias": dias, "final": final, "observado": x}
            # Observado puro
            linhas.append({**base_row, "metodo": "Observado", "est": x, "lo": x, "hi": x, "f": np.nan})
            # Método antigo: curva nacional, fim da semana, IC binomial normal
            if f_nac is not None:
                fv = f_nac.get(min(max(dias, 0), 200), np.nan)
                if fv >= 0.15:
                    est = round(x / fv)
                    se = np.sqrt(x * (1 - fv) / fv ** 2)
                    linhas.append({**base_row, "metodo": "Antigo (curva nacional)", "est": est,
                                   "lo": max(x, round(est - 1.96 * se)), "hi": max(x, round(est + 1.96 * se)), "f": fv})
            for nome, priori in (("Local sem priori", False), ("Local + priori", True)):
                r = estimar_semana(base, s, t, curvas, rng, usar_priori=priori)
                if np.isnan(r["estimado"]):
                    continue
                linhas.append({**base_row, "metodo": nome, "est": r["estimado"], "lo": r["lo"],
                               "hi": r["hi"], "f": r["f"]})
    return pd.DataFrame(linhas)


def resumir(bt: pd.DataFrame) -> pd.DataFrame:
    # compara todos os métodos no mesmo conjunto de (corte, semana)
    comuns = bt.groupby(["corte", "sem"])["metodo"].nunique()
    comuns = comuns[comuns == bt["metodo"].nunique()].index
    bt = bt.set_index(["corte", "sem"]).loc[comuns].reset_index()
    bt["faixa"] = pd.cut(bt["dias"], [-1, 7, 14, 28, 49], labels=["0–7 d", "8–14 d", "15–28 d", "29–49 d"])
    linhas = []
    for faixa_nome, dados in [("todas", bt)] + [(str(f), g) for f, g in bt.groupby("faixa", observed=True)]:
        for metodo, g in dados.groupby("metodo"):
            linhas.append({
                "metodo": metodo,
                "faixa_dias_desde_fim_semana": faixa_nome,
                "n": len(g),
                "razao_estimado_final": g["est"].sum() / g["final"].sum(),
                "erro_medio_abs": (g["est"] - g["final"]).abs().mean(),
                "cobertura_ic95": ((g["lo"] <= g["final"]) & (g["final"] <= g["hi"])).mean(),
            })
    return pd.DataFrame(linhas)

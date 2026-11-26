"""Marche 5 : l'optimisation à horizon glissant (MPC) du modèle Simulink.

Dans `microgrid_WithESSOpt.slx`, l'optimiseur n'est pas appelé une seule fois :
un bloc MATLAB Function l'appelle **toutes les 5 min**, sur un horizon de
**20 h** (M = 241 pas), à partir du SOC **mesuré** sur la batterie. Il n'applique
que la consigne `Pbatt(2)`, puis tout recommence 5 min plus tard. C'est le
principe de la commande prédictive (MPC, Model Predictive Control) :

    à chaque pas k :
        1. mesurer l'état réel (SOC)
        2. résoudre l'optimisation sur [k, k + 20 h] avec les prévisions
        3. appliquer seulement la première décision
        4. avancer d'un pas et recommencer

On reproduit fidèlement `initialConditions.m` et le bloc Simulink :
  - prévisions = profils réels futurs (prévision parfaite) ;
  - au-delà de minuit, le lendemain est une copie du jour (numDays = 2) ;
  - l'optimiseur voit le prix `costData` sans le +5 ; le site est facturé
    `costData + 5` (c$/kWh) ;
  - la consigne appliquée est `Pbatt(2)`.

Usage, depuis la racine du dépôt :
    python src/marche5_mpc.py
"""

from dataclasses import replace

import matplotlib.pyplot as plt
import numpy as np

from marche1_donnees import RACINE
from marche2_parametrage import J_PAR_KWH, CasOptimisation, cas_matlab_only
from marche3_optimisation import batt_solar_optimize
from marche4_regle import RegleStateflow, cout_dollars_simulink, simuler

FIGURE = RACINE / "results" / "marche5_compare_costs.png"
HORIZON_H = 20  # timePred dans initialConditions.m


def horizon_en_pas(cas: CasOptimisation, horizon_h: float = HORIZON_H) -> int:
    """M = find(tvec > timePred*3600, 1, 'first'), converti en nombre de pas."""
    return int(np.argmax(cas.tvec > horizon_h * 3600)) + 1  # 241 pour 20 h


def mpc(cas: CasOptimisation, horizon_h: float = HORIZON_H, indice_applique: int = 1):
    """Rejoue la journée avec une optimisation relancée à chaque pas.

    indice_applique = 1 reproduit `Pbatt(2)` du modèle (Python commence à 0) ;
    indice_applique = 0 applique la toute première décision, `Pbatt(1)`.
    Renvoie (Pgrid, Pbatt, Ebatt) réellement subis par le site.
    """
    N, dt, b = cas.N, cas.dt, cas.batterie
    M = horizon_en_pas(cas, horizon_h)

    # Prévisions sur deux jours : le lendemain est une copie du jour (repmat).
    cost2 = np.tile(cas.Cost, 2)
    ppv2 = np.tile(cas.Ppv, 2)
    pload2 = np.tile(cas.Pload, 2)

    E = np.empty(N)
    Pb = np.empty(N)
    e = cas.Einit
    for k in range(N):
        E[k] = e
        # Sous-problème de taille M qui commence au pas k, avec l'état mesuré.
        sous_cas = replace(
            cas, N=M, Einit=e,
            Cost=cost2[k:k + M], Ppv=ppv2[k:k + M], Pload=pload2[k:k + M],
            tvec=cas.tvec[:M],
        )
        r = batt_solar_optimize(sous_cas, "highs-ds")
        p = r.Pbatt[indice_applique]

        # La batterie réelle obéit dans ses limites, comme à la marche 4.
        p = min(max(p, b.Pmin), b.Pmax)
        p = min(p, (e - b.Emin) / dt)
        p = max(p, -(b.Emax - e) / dt)
        Pb[k] = p
        e = e - p * dt

    Pgrid = cas.Pload - cas.Ppv - Pb
    return Pgrid, Pb, E


def main() -> None:
    cas = cas_matlab_only()
    dt = cas.dt
    print(f"Horizon : M = {horizon_en_pas(cas)} pas de 5 min ; {cas.N} optimisations à résoudre...")

    # Les quatre stratégies, toutes rejouées sur le même modèle de site.
    pgrid = {}
    pgrid["Sans batterie"] = cas.Pload - cas.Ppv
    pgrid["Heuristique"] = simuler(cas, RegleStateflow(cas.batterie.Pmin, cas.batterie.Pmax)).Pgrid
    pgrid_mpc, pb_mpc, e_mpc = mpc(cas, indice_applique=1)
    pgrid["MPC 20 h, Pbatt(2)"] = pgrid_mpc
    pgrid["MPC 20 h, Pbatt(1)"] = mpc(cas, indice_applique=0)[0]
    pgrid["Optimisation journée entière"] = batt_solar_optimize(cas, "highs-ds").Pgrid

    simulink = {"Sans batterie": "≈ 840", "Heuristique": "≈ 735", "MPC 20 h, Pbatt(2)": "≈ 640"}
    print(f"\n{'Stratégie':<32}{'Python [$]':>11}{'Simulink [$]':>14}{'Réseau [kWh]':>14}")
    for nom, pg in pgrid.items():
        kwh = pg.sum() * dt / J_PAR_KWH
        print(f"{nom:<32}{cout_dollars_simulink(cas, pg):>11.0f}{simulink.get(nom, '—'):>14}{kwh:>14.0f}")
    print(f"\nÉnergie finale de la batterie, MPC : "
          f"{(e_mpc[-1] - pb_mpc[-1] * dt) / J_PAR_KWH:.0f} kWh")

    # Figure au format de compareCosts.m : coût cumulé et énergie cumulée.
    t_h = cas.tvec / 3600
    styles = {
        "Heuristique": dict(color="tab:blue"),
        "MPC 20 h, Pbatt(2)": dict(color="tab:red"),
        "Sans batterie": dict(color="goldenrod"),
        "Optimisation journée entière": dict(color="grey", ls="--", lw=1.2),
    }
    fig, ax = plt.subplots(2, 1, figsize=(10, 8), sharex=True)
    for nom, style in styles.items():
        pg = pgrid[nom]
        cout_cumule = np.cumsum(dt * (cas.Cost + 5) * pg) / J_PAR_KWH / 100
        ax[0].plot(t_h, cout_cumule, lw=style.pop("lw", 2), label=nom, **style)
        ax[1].plot(t_h, np.cumsum(pg) * dt / J_PAR_KWH, lw=2 if "ls" not in style else 1.2,
                   label=nom, **style)
    ax[0].set_title("Coût cumulé du réseau ($)")
    ax[0].set_ylabel("Coût cumulé [$]")
    ax[1].set_title("Énergie cumulée soutirée au réseau (kWh)")
    ax[1].set_ylabel("Énergie [kWh]")
    ax[1].set_xlabel("Heure de la journée")
    ax[1].set_xticks(range(0, 25, 3))
    for a in ax:
        a.grid(alpha=0.3)
        a.legend(loc="upper left", fontsize=9)
    fig.tight_layout()
    fig.savefig(FIGURE, dpi=120)
    print(f"\nFigure enregistrée : {FIGURE.relative_to(RACINE)}")


if __name__ == "__main__":
    main()

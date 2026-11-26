"""Marche 2 : reproduire le paramétrage du cas « MATLAB only ».

Traduction ligne à ligne de `EMS_Optimization_MATLAB_Only/energyOptimizationScript.m`,
jusqu'à l'appel de l'optimiseur (exclu : c'est la marche 3).

On garde volontairement les unités du modèle MathWorks (W, J, s) pour pouvoir
comparer nos résultats aux siens au chiffre près. Le passage en kW/kWh/€ se fera
dans notre propre version.

Correspondance des indices : MATLAB commence à 1, Python à 0.
    MATLAB  clearDay(2:stepAdjust:end)   <->   Python  clearDay[1::step]

Usage, depuis la racine du dépôt :
    python src/marche2_parametrage.py
"""

from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from marche1_donnees import FICHIER_MAT, RACINE, charger_donnees

FIGURE = RACINE / "results" / "marche2_parametrage.png"
J_PAR_KWH = 3.6e6  # 1 kWh = 3,6 MJ


@dataclass
class BornesBatterie:
    """Équivalent de la structure MATLAB `batteryMinMax`."""
    Emin: float  # énergie stockée minimale [J]
    Emax: float  # énergie stockée maximale [J]
    Pmin: float  # puissance minimale [W] ; négative = charge
    Pmax: float  # puissance maximale [W] ; positive = décharge


@dataclass
class CasOptimisation:
    """Tout ce que reçoit battSolarOptimize(N, dt, Ppv, Pload, Einit, Cost, FinalWeight, batteryMinMax)."""
    N: int                   # nombre de pas de l'horizon
    dt: float                # pas de temps [s]
    Ppv: np.ndarray          # production PV [W], taille N
    Pload: np.ndarray        # consommation [W], taille N
    Einit: float             # énergie initiale de la batterie [J]
    Cost: np.ndarray         # prix du réseau [unité du .mat, « $/kWh »], taille N
    FinalWeight: float       # bonus sur l'énergie finale [-]
    batterie: BornesBatterie
    tvec: np.ndarray         # instants de fin de pas [s], taille N


def cas_matlab_only(ciel: str = "clair", load_select: int = 3) -> CasOptimisation:
    """Reconstruit les entrées de l'optimiseur exactement comme energyOptimizationScript.m."""
    d = charger_donnees(FICHIER_MAT)

    # --- Paramètres d'optimisation ---------------------------------------
    num_days = 1           # numDays
    final_weight = 1.0     # FinalWeight
    time_optimize = 5      # timeOptimize, pas d'optimisation [min]

    # --- Paramètres PV et batterie ---------------------------------------
    panel_area = 2500.0    # [m²]
    panel_eff = 0.3        # [-]
    batt_energy = 2500 * J_PAR_KWH           # capacité : 2500 kWh, en J
    einit = 0.5 * batt_energy                # SOC initial 50 %
    batterie = BornesBatterie(
        Emin=0.2 * batt_energy,              # SOC min 20 %
        Emax=0.8 * batt_energy,              # SOC max 80 %
        Pmin=-400e3,                         # charge max 400 kW
        Pmax=400e3,                          # décharge max 400 kW
    )

    # --- Rééchantillonnage : du pas de 60 s au pas de 5 min ---------------
    # stepAdjust = (timeOptimize*60)/(time(2)-time(1)) = 300/60 = 5
    step = int(time_optimize * 60 / (d["time"][1] - d["time"][0]))
    # MATLAB prend un point sur `step` en partant du 2e : sous-échantillonnage
    # simple, sans moyenne. Les fluctuations rapides du ciel nuageux entre deux
    # points sont donc ignorées.
    eclairement = d["clearDay"] if ciel == "clair" else d["cloudyDay"]
    ppv = panel_area * panel_eff * np.tile(eclairement[1::step], num_days)

    load_base = 350e3
    pload = np.tile(d["loadData"][1::step, load_select - 1], num_days) + load_base

    cost = np.tile(d["costData"][1::step], num_days)

    # --- Vecteurs de temps -----------------------------------------------
    dt = time_optimize * 60.0
    n = num_days * (len(d["time"][::step]) - 1)   # N = numDays*(numel(time(1:stepAdjust:end))-1)
    tvec = np.arange(1, n + 1) * dt

    return CasOptimisation(
        N=n, dt=dt, Ppv=ppv, Pload=pload, Einit=einit, Cost=cost,
        FinalWeight=final_weight, batterie=batterie, tvec=tvec,
    )


def main() -> None:
    cas = cas_matlab_only()
    b = cas.batterie
    assert len(cas.Ppv) == len(cas.Pload) == len(cas.Cost) == cas.N, "tailles incohérentes"

    print(f"N  = {cas.N} pas de {cas.dt:.0f} s, soit {cas.N * cas.dt / 3600:.0f} h")
    print(f"Batterie : E dans [{b.Emin / J_PAR_KWH:.0f}, {b.Emax / J_PAR_KWH:.0f}] kWh, "
          f"Einit = {cas.Einit / J_PAR_KWH:.0f} kWh")
    print(f"           P dans [{b.Pmin / 1e3:.0f}, {b.Pmax / 1e3:.0f}] kW (négatif = charge)")

    # d = Pload - Ppv : ce que batterie + réseau doivent fournir (battSolarOptimize_fullMatrices.m).
    d = cas.Pload - cas.Ppv
    dt_h = cas.dt / 3600
    surplus = -d[d < 0].sum() * dt_h / 1e3
    deficit = d[d > 0].sum() * dt_h / 1e3
    print(f"\nBesoin net d = Pload - Ppv : déficit {deficit:.0f} kWh, surplus PV {surplus:.0f} kWh")
    print(f"Énergie utilisable de la batterie : {(b.Emax - b.Emin) / J_PAR_KWH:.0f} kWh")

    # Coût sans batterie : toute la différence passe par le réseau.
    # Même formule que l'objectif MATLAB : dt * Cost' * Pgrid, en « $/kWh × W × s ».
    cout_brut = cas.dt * cas.Cost @ d
    cout_kwh = cout_brut / J_PAR_KWH  # converti en « $ » si Cost est en $/kWh
    print(f"\nCoût sans batterie : dt*Cost'*Pgrid = {cout_brut:.4e} (unités MATLAB)")
    print(f"                     soit {cout_kwh:,.0f} en unité de prix × kWh")

    # Figure : besoin net et puissance que la batterie peut absorber ou fournir.
    t_h = cas.tvec / 3600
    fig, ax = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
    ax[0].plot(t_h, cas.Ppv / 1e3, label="PV (ciel clair)")
    ax[0].plot(t_h, cas.Pload / 1e3, label="charge (profil 3)")
    ax[0].plot(t_h, d / 1e3, label="besoin net d = charge − PV", lw=2)
    ax[0].axhspan(b.Pmin / 1e3, b.Pmax / 1e3, color="tab:green", alpha=0.12,
                  label="plage de puissance batterie ±400 kW")
    ax[0].axhline(0, color="k", lw=0.8)
    ax[0].set_ylabel("Puissance [kW]")
    ax[0].set_title(f"Entrées de l'optimiseur : N = {cas.N} pas de 5 min")
    ax[0].legend(fontsize=8, loc="upper left")

    ax[1].plot(t_h, cas.Cost, color="tab:red")
    ax[1].set_ylabel("Prix [unité du .mat]")
    ax[1].set_xlabel("Heure de la journée")
    ax[1].set_xticks(range(0, 25, 3))
    for a in ax:
        a.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(FIGURE, dpi=120)
    print(f"\nFigure enregistrée : {FIGURE.relative_to(RACINE)}")


if __name__ == "__main__":
    main()

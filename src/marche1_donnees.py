"""Marche 1 : explorer les données du modèle de référence MathWorks.

On ouvre `pvLoadPriceData.mat` (fourni avec l'exemple Microgrid EMS de
Jonathan LeSage), on regarde ce qu'il contient, puis on trace les trois
grandeurs qui pilotent le problème : production PV, consommation et prix.

Les échelles reprennent celles de `initialConditions.m` :
    Ppv   = panelArea * panelEff * éclairement    (2500 m² × 30 %)
    Pload = loadData[:, loadSelect] + loadBase    (profil n°3 + 350 kW)

Usage, depuis la racine du dépôt :
    python src/marche1_donnees.py
"""

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import scipy.io

RACINE = Path(__file__).resolve().parents[1]
FICHIER_MAT = RACINE / "data" / "reference" / "pvLoadPriceData.mat"
FIGURE = RACINE / "results" / "marche1_donnees.png"

# Paramètres de initialConditions.m, recopiés à l'identique.
PANEL_AREA = 2500.0  # surface du champ PV [m²]
PANEL_EFF = 0.3      # rendement des panneaux [-]
LOAD_BASE = 350e3    # charge de base ajoutée au profil [W]
LOAD_SELECT = 3      # profil de charge retenu (indice MATLAB, commence à 1)


def charger_donnees(chemin: Path) -> dict[str, np.ndarray]:
    """Lit le .mat et renvoie ses variables sous forme de vecteurs 1D ou 2D."""
    brut = scipy.io.loadmat(chemin)
    # loadmat ajoute des clés techniques (__header__, __version__, __globals__).
    return {k: np.squeeze(v) for k, v in brut.items() if not k.startswith("__")}


def decrire(donnees: dict[str, np.ndarray]) -> None:
    """Affiche la taille et la plage de chaque variable."""
    print(f"{'variable':<10} {'taille':<12} {'min':>12} {'max':>12} {'moyenne':>12}")
    for nom, v in donnees.items():
        print(f"{nom:<10} {str(v.shape):<12} {v.min():>12.2f} {v.max():>12.2f} {v.mean():>12.2f}")


def main() -> None:
    d = charger_donnees(FICHIER_MAT)
    decrire(d)

    t_h = d["time"] / 3600  # secondes -> heures
    pas_s = d["time"][1] - d["time"][0]
    print(f"\nPas de temps : {pas_s:.0f} s, {len(t_h)} points, de {t_h[0]:.3f} h à {t_h[-1]:.2f} h")

    # Puissances du site, en kW, comme dans initialConditions.m.
    pv_clair = PANEL_AREA * PANEL_EFF * d["clearDay"] / 1e3
    pv_nuageux = PANEL_AREA * PANEL_EFF * d["cloudyDay"] / 1e3
    charges = (d["loadData"] + LOAD_BASE) / 1e3  # les 4 profils, base incluse
    charge = charges[:, LOAD_SELECT - 1]
    prix = d["costData"]

    # Énergies sur la journée : somme des puissances × pas de temps.
    dt_h = pas_s / 3600
    print("\nBilan de la journée")
    print(f"  PV ciel clair    : {pv_clair.sum() * dt_h:8.0f} kWh, pic {pv_clair.max():.0f} kW")
    print(f"  PV ciel nuageux  : {pv_nuageux.sum() * dt_h:8.0f} kWh, pic {pv_nuageux.max():.0f} kW")
    for i in range(charges.shape[1]):
        c = charges[:, i]
        print(f"  Charge profil {i + 1}  : {c.sum() * dt_h:8.0f} kWh, de {c.min():.0f} à {c.max():.0f} kW")
    print(f"  Prix             : de {prix.min():.2f} à {prix.max():.2f}, "
          f"pic à {t_h[prix.argmax()]:.1f} h")

    # Figure : trois panneaux partageant l'axe du temps.
    fig, ax = plt.subplots(3, 1, figsize=(10, 8), sharex=True)

    ax[0].plot(t_h, pv_clair, label="ciel clair")
    ax[0].plot(t_h, pv_nuageux, label="ciel nuageux", alpha=0.8)
    ax[0].set_ylabel("PV [kW]")
    ax[0].set_title("Production photovoltaïque (2500 m² × 30 %)")
    ax[0].legend()

    for i in range(charges.shape[1]):
        style = dict(lw=2) if i == LOAD_SELECT - 1 else dict(lw=0.8, alpha=0.5)
        ax[1].plot(t_h, charges[:, i], label=f"profil {i + 1}", **style)
    ax[1].set_ylabel("Charge [kW]")
    ax[1].set_title(f"Consommation (350 kW + fluctuation ; profil {LOAD_SELECT} retenu)")
    ax[1].legend(ncol=4)

    ax[2].plot(t_h, prix, color="tab:red")
    ax[2].set_ylabel("Prix [unité du .mat]")
    ax[2].set_title("Prix du réseau (commentaires : \\$/kWh ; plus vraisemblablement c\\$/kWh)")
    ax[2].set_xlabel("Heure de la journée")
    ax[2].set_xticks(range(0, 25, 3))

    for a in ax:
        a.grid(alpha=0.3)
    fig.tight_layout()
    FIGURE.parent.mkdir(exist_ok=True)
    fig.savefig(FIGURE, dpi=120)
    print(f"\nFigure enregistrée : {FIGURE.relative_to(RACINE)}")


if __name__ == "__main__":
    main()

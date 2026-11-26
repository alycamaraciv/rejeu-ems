"""Marche 3 : porter battSolarOptimize en scipy.optimize.linprog.

Traduction de `EMS_Optimization_MATLAB_Only/battSolarOptimize_fullMatrices.m`,
la version qui écrit les matrices à la main (slides 8 et 9).

Forme standard d'un programme linéaire (identique en MATLAB et en SciPy) :

    min  f' x     sous   A x <= b
     x                   Aeq x = beq
                         lb <= x <= ub

Vecteur des inconnues, de taille 3N :

    x = [ Pgrid(1..N) | Pbatt(1..N) | Ebatt(1..N) ]
          réseau [W]    batterie [W]  énergie [J]
                        (> 0 = décharge)

Usage, depuis la racine du dépôt :
    python src/marche3_optimisation.py
"""

from dataclasses import dataclass

import matplotlib.pyplot as plt
import numpy as np
from scipy import sparse
from scipy.optimize import linprog

from marche1_donnees import RACINE
from marche2_parametrage import J_PAR_KWH, CasOptimisation, cas_matlab_only

FIGURE = RACINE / "results" / "marche3_optimisation.png"


@dataclass
class Resultat:
    Pgrid: np.ndarray  # [W]
    Pbatt: np.ndarray  # [W], > 0 = décharge
    Ebatt: np.ndarray  # [J]
    objectif: float    # valeur de f'x, terme FinalWeight compris
    cout: float        # dt * Cost' * Pgrid : coût de l'électricité seul


def batt_solar_optimize(cas: CasOptimisation, methode: str = "highs") -> Resultat:
    """Équivalent Python de battSolarOptimize_fullMatrices.m."""
    N, dt = cas.N, cas.dt
    b = cas.batterie

    # Besoin net : ce que la batterie et le réseau doivent fournir ensemble.
    d = cas.Pload - cas.Ppv

    # Blocs de base. On les construit en creux (sparse) : la plupart des
    # coefficients sont nuls, ce qui économise mémoire et temps de calcul.
    I = sparse.identity(N, format="csr")
    Z = sparse.csr_matrix((N, N))
    sous_diag = sparse.eye(N, k=-1, format="csr")  # 1 juste sous la diagonale

    # --- Contraintes d'égalité  Aeq x = beq -------------------------------
    # Bloc 1, bilan de puissance à chaque pas :
    #     Pgrid(k) + Pbatt(k) = Pload(k) - Ppv(k) = d(k)
    # Bloc 2, dynamique de l'énergie (slide 9, matrices Υ et Φ) :
    #     ligne 1 :  E(1) = Einit
    #     ligne k :  dt*Pbatt(k-1) + E(k) - E(k-1) = 0
    #           soit E(k) = E(k-1) - Pbatt(k-1)*dt
    #     (décharger, Pbatt > 0, fait baisser l'énergie stockée).
    upsilon = dt * sous_diag   # Υ : dt sous la diagonale
    phi = I - sous_diag        # Φ : 1 sur la diagonale, -1 dessous
    Aeq = sparse.bmat([[I, I, Z],
                       [Z, upsilon, phi]], format="csr")
    beq = np.concatenate([d, [cas.Einit], np.zeros(N - 1)])

    # --- Fonction objectif  f' x ----------------------------------------
    # Coût de l'énergie achetée : somme de Cost(k)*Pgrid(k)*dt,
    # moins un bonus FinalWeight sur l'énergie restante à la fin, E(N).
    f = np.concatenate([cas.Cost * dt, np.zeros(N), np.zeros(N - 1), [-cas.FinalWeight]])

    # --- Contraintes d'inégalité  A x <= b --------------------------------
    #      Pbatt <= Pmax        -Pbatt <= -Pmin
    #      Ebatt <= Emax        -Ebatt <= -Emin
    A = sparse.bmat([[Z, I, Z],
                     [Z, -I, Z],
                     [Z, Z, I],
                     [Z, Z, -I]], format="csr")
    b_ineq = np.concatenate([np.full(N, b.Pmax), np.full(N, -b.Pmin),
                             np.full(N, b.Emax), np.full(N, -b.Emin)])

    # --- Résolution --------------------------------------------------------
    # PIÈGE : par défaut, scipy impose x >= 0 à toutes les variables, alors que
    # MATLAB les laisse libres quand lb et ub sont vides. Il faut donc écrire
    # explicitement bounds=(None, None). Sans cela, Pgrid ne pourrait plus être
    # négatif (pas d'injection) et Pbatt non plus (pas de charge) : ce serait un
    # autre problème.
    res = linprog(f, A_ub=A, b_ub=b_ineq, A_eq=Aeq, b_eq=beq,
                  bounds=(None, None), method=methode)

    # Contrairement au code MATLAB, qui renvoie des zéros en silence, on
    # s'arrête net si le solveur échoue.
    if not res.success:
        raise RuntimeError(f"linprog a échoué : {res.message}")

    x = res.x
    pgrid, pbatt, ebatt = x[:N], x[N:2 * N], x[2 * N:]
    return Resultat(pgrid, pbatt, ebatt, objectif=res.fun, cout=dt * cas.Cost @ pgrid)


def verifier(cas: CasOptimisation, r: Resultat, tol: float = 1e-3) -> None:
    """Contrôle a posteriori : la solution respecte-t-elle toutes les contraintes ?"""
    b = cas.batterie
    bilan = r.Pgrid + r.Pbatt + cas.Ppv - cas.Pload
    dyn = r.Ebatt[1:] - (r.Ebatt[:-1] - r.Pbatt[:-1] * cas.dt)
    assert np.abs(bilan).max() < tol * 1e3, "bilan de puissance non respecté"
    assert np.abs(dyn).max() < tol * J_PAR_KWH, "dynamique de l'énergie non respectée"
    assert abs(r.Ebatt[0] - cas.Einit) < tol * J_PAR_KWH, "état initial non respecté"
    assert r.Pbatt.max() <= b.Pmax + tol and r.Pbatt.min() >= b.Pmin - tol
    assert r.Ebatt.max() <= b.Emax + tol * J_PAR_KWH and r.Ebatt.min() >= b.Emin - tol * J_PAR_KWH


def main() -> None:
    cas = cas_matlab_only()
    d = cas.Pload - cas.Ppv
    cout_sans_batterie = cas.dt * cas.Cost @ d

    # Deux algorithmes de HiGHS : simplexe dual et points intérieurs.
    r_ds = batt_solar_optimize(cas, "highs-ds")
    r_ipm = batt_solar_optimize(cas, "highs-ipm")
    for r in (r_ds, r_ipm):
        verifier(cas, r)

    print("Toutes les contraintes sont respectées.\n")
    print(f"{'':<24}{'unités MATLAB':>16}{'prix × kWh':>14}")
    print(f"{'Sans batterie':<24}{cout_sans_batterie:>16.6e}{cout_sans_batterie / J_PAR_KWH:>14,.1f}")
    for nom, r in (("Optimisé, simplexe", r_ds), ("Optimisé, pts intérieurs", r_ipm)):
        print(f"{nom:<24}{r.cout:>16.6e}{r.cout / J_PAR_KWH:>14,.1f}")
    gain = 1 - r_ds.cout / cout_sans_batterie
    print(f"\nGain de l'optimisation : {gain:.1%}")
    print(f"Objectif f'x (bonus FinalWeight compris) : {r_ds.objectif:.6e}")
    print(f"Énergie finale : {r_ds.Ebatt[-1] / J_PAR_KWH:.0f} kWh")

    # Même coût, mais des trajectoires différentes ? -> solutions multiples.
    ecart_cout = abs(r_ds.cout - r_ipm.cout) / r_ds.cout
    ecart_pbatt = np.abs(r_ds.Pbatt - r_ipm.Pbatt).max() / 1e3
    print(f"\nÉcart de coût entre les deux algorithmes : {ecart_cout:.1e} (relatif)")
    print(f"Écart maximal sur Pbatt                  : {ecart_pbatt:.0f} kW")

    # Figure dans la mise en page de energyOptimizationScript.m.
    t_h = cas.tvec / 3600
    fig, ax = plt.subplots(3, 1, figsize=(10, 9), sharex=True)
    ax[0].plot(t_h, r_ds.Ebatt / J_PAR_KWH, label="simplexe dual")
    ax[0].plot(t_h, r_ipm.Ebatt / J_PAR_KWH, "--", label="points intérieurs")
    ax[0].set_ylabel("Énergie batterie [kWh]")
    ax[0].set_title("Même coût optimal, deux trajectoires : le problème a plusieurs solutions")
    ax[0].legend()

    ax[1].plot(t_h, cas.Cost, color="tab:red")
    ax[1].set_ylabel("Prix [unité du .mat]")

    ax[2].plot(t_h, cas.Ppv / 1e3, label="PV")
    ax[2].plot(t_h, r_ds.Pbatt / 1e3, label="batterie (> 0 décharge)")
    ax[2].plot(t_h, r_ds.Pgrid / 1e3, label="réseau (< 0 injection)")
    ax[2].plot(t_h, cas.Pload / 1e3, label="charge")
    ax[2].set_ylabel("Puissance [kW]")
    ax[2].set_xlabel("Heure de la journée")
    ax[2].set_xticks(range(0, 25, 3))
    ax[2].legend(fontsize=8, ncol=4, loc="lower left")
    for a in ax:
        a.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(FIGURE, dpi=120)
    print(f"\nFigure enregistrée : {FIGURE.relative_to(RACINE)}")


if __name__ == "__main__":
    main()

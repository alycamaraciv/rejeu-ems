"""Marche 4 : reproduire la règle Stateflow `HeuristicEMS` en Python.

La logique est relue dans `microgrid_WithESSOpt.slx` (graphe chart_117) :

    HeuristicEMS
    ├── IdleESS            Pcmd = 0
    │     ├─[5 h < t < 15 h et 20 % < SOC < 80 %]──> AbsorbSupplyPV   (priorité 1)
    │     └─[t > 16 h]─────────────────────────────> PeakSupply       (priorité 2)
    ├── AbsorbSupplyPV     Pnet = Load − PV
    │     ├── Charging      Pcmd = Pnet   ─[Load > PV]──> Discharging
    │     │                               ─[Pnet < Pmin]─> MPPTOff
    │     ├── Discharging   Pcmd = Pnet   ─[PV > Load]──> Charging
    │     │                               ─[Pnet > Pmax]─> LoadShedding
    │     ├── MPPTOff       Pcmd = Pmin   ─[Pnet > Pmin]─> Charging
    │     ├── LoadShedding  Pcmd = Pmax   ─[Pnet < Pmax]─> Discharging
    │     └─[SOC >= 80 % ou SOC <= 20 %]──> IdleESS
    ├── PeakSupply         Pcmd = Pmax  ─[t > 23 h]──> NightRecharge
    └── NightRecharge      Pcmd = Pmin  ─[SOC >= 80 %]──> IdleESS

Convention de signe identique à l'optimisation : Pcmd > 0 = décharge.

Point de sémantique Stateflow : une action écrite sous le nom d'un état, sans
mot-clé (`en:`, `du:`, `ex:`), est une action d'**entrée** : elle n'est exécutée
qu'une fois, au moment où l'on entre dans l'état. Dans le modèle, `Pnet` et
`Pcmd = Pnet` sont donc figés à leur valeur d'entrée. On simule les deux
lectures :
    "stateflow" : action d'entrée seulement (lecture stricte du graphe) ;
    "continu"   : action recalculée à chaque pas (suivi du besoin net).

Verdict, obtenu par comparaison avec la simulation Simulink (compareCosts) : c'est
la lecture « continu » qui reproduit le modèle. Le coût cumulé de l'heuristique
y est plat de 5 h à 14 h (réseau nul, batterie = besoin net), et le coût final
concorde (environ 735 $ en Simulink, 741 $ ici), alors que la lecture
« stateflow » donne 794 $ et une courbe qui n'est pas plate.

Unité du coût dans Simulink : le modèle facture (costData + 5) en c$/kWh, d'où
    coût [$] = Σ (Cost + 5) · Pgrid · dt / 3,6e6 / 100.

Usage, depuis la racine du dépôt :
    python src/marche4_regle.py
"""

from dataclasses import dataclass, field

import matplotlib.pyplot as plt
import numpy as np

from marche1_donnees import RACINE
from marche2_parametrage import J_PAR_KWH, CasOptimisation, cas_matlab_only

FIGURE = RACINE / "results" / "marche4_regle.png"
FIGURE_SIMULINK = RACINE / "results" / "marche4_style_simulink.png"

MIN_SOC = 20.0   # [%], constante minSOC du graphe
MAX_SOC = 80.0   # [%], constante maxSOC du graphe


class RegleStateflow:
    """Machine à états équivalente au graphe HeuristicEMS (ESS activé)."""

    def __init__(self, pmin: float, pmax: float, semantique: str = "continu"):
        if semantique not in ("stateflow", "continu"):
            raise ValueError("semantique doit valoir 'stateflow' ou 'continu'")
        self.pmin, self.pmax = pmin, pmax
        self.semantique = semantique
        self.etat = "IdleESS"   # transition par défaut de HeuristicEMS
        self.pcmd = 0.0         # sortie Pcmd, conservée d'un pas à l'autre
        self.pnet = 0.0         # variable locale Pnet

    # --- entrées dans les états : on exécute l'action d'entrée --------------
    def _entrer(self, etat: str, load_kw: float, pv_kw: float) -> None:
        self.etat = etat
        if etat == "IdleESS":
            self.pcmd = 0.0
        elif etat == "PeakSupply":
            self.pcmd = self.pmax
        elif etat == "NightRecharge":
            self.pcmd = self.pmin
        elif etat == "Absorb.Charging" or etat == "Absorb.Discharging":
            self.pcmd = self.pnet
        elif etat == "Absorb.MPPTOff":
            self.pcmd = self.pmin
        elif etat == "Absorb.LoadShedding":
            self.pcmd = self.pmax

    def _entrer_absorb(self, load_kw: float, pv_kw: float) -> None:
        # Entrée dans le super-état : Pnet = 1e3*(Load - PV), en W.
        self.pnet = 1e3 * (load_kw - pv_kw)
        # Puis transition par défaut vers le sous-état Charging.
        self._entrer("Absorb.Charging", load_kw, pv_kw)

    # --- un pas de temps ------------------------------------------------------
    def pas(self, heure: float, load_kw: float, pv_kw: float, soc: float) -> float:
        """Évalue le graphe une fois et renvoie Pcmd [W].

        Règle Stateflow : on teste d'abord les transitions qui sortent de
        l'état parent (les plus extérieures), dans l'ordre de priorité ; la
        première vraie est prise. Sinon, on descend dans le sous-état actif.
        """
        e = self.etat

        if e == "IdleESS":
            if 5 < heure < 15 and MIN_SOC < soc < MAX_SOC:
                self._entrer_absorb(load_kw, pv_kw)
            elif heure > 16:
                self._entrer("PeakSupply", load_kw, pv_kw)

        elif e == "PeakSupply":
            # Aucune condition sur le SOC : on décharge jusqu'à 23 h, quoi qu'il arrive.
            if heure > 23:
                self._entrer("NightRecharge", load_kw, pv_kw)

        elif e == "NightRecharge":
            if soc >= MAX_SOC:
                self._entrer("IdleESS", load_kw, pv_kw)

        elif e.startswith("Absorb."):
            # Transition de sortie du super-état : uniquement sur le SOC.
            # Il n'y a pas de condition horaire : on peut rester dans
            # AbsorbSupplyPV bien après 15 h.
            if soc >= MAX_SOC or soc <= MIN_SOC:
                self._entrer("IdleESS", load_kw, pv_kw)
            else:
                if self.semantique == "continu":
                    self.pnet = 1e3 * (load_kw - pv_kw)
                sous = e.split(".")[1]
                if sous == "Charging":
                    if load_kw > pv_kw:
                        self._entrer("Absorb.Discharging", load_kw, pv_kw)
                    elif self.pnet < self.pmin:
                        self._entrer("Absorb.MPPTOff", load_kw, pv_kw)
                elif sous == "Discharging":
                    if pv_kw > load_kw:
                        self._entrer("Absorb.Charging", load_kw, pv_kw)
                    elif self.pnet > self.pmax:
                        self._entrer("Absorb.LoadShedding", load_kw, pv_kw)
                elif sous == "MPPTOff":
                    if self.pnet > self.pmin:
                        self._entrer("Absorb.Charging", load_kw, pv_kw)
                elif sous == "LoadShedding":
                    if self.pnet < self.pmax:
                        self._entrer("Absorb.Discharging", load_kw, pv_kw)
                # Lecture « continu » : l'action est une action « du: »,
                # recalculée à chaque pas dans Charging et Discharging.
                if self.semantique == "continu" and self.etat in ("Absorb.Charging", "Absorb.Discharging"):
                    self.pcmd = self.pnet

        return self.pcmd


@dataclass
class Simulation:
    Pgrid: np.ndarray            # [W], < 0 = injection
    Pbatt: np.ndarray            # [W] réellement appliqué, > 0 = décharge
    Ebatt: np.ndarray            # [J] au début de chaque pas
    Pcmd: np.ndarray             # [W] demandé par la règle, avant limitation
    etats: list = field(default_factory=list)
    cout: float = 0.0            # dt * Cost' * Pgrid


def simuler(cas: CasOptimisation, regle: RegleStateflow) -> Simulation:
    """Rejoue la journée pas à pas : la règle décide, la batterie obéit dans ses limites.

    Le « modèle physique » est volontairement le même que celui de l'optimiseur :
        E(k+1) = E(k) − Pbatt(k)·dt,   Pgrid(k) = Pload(k) − Ppv(k) − Pbatt(k),
    avec les mêmes bornes de puissance et d'énergie. Le système de gestion de la
    batterie refuse toute consigne qui la ferait sortir de [Emin, Emax] : c'est
    nécessaire, car PeakSupply décharge sans regarder le SOC.
    """
    N, dt, b = cas.N, cas.dt, cas.batterie
    capacite = b.Emax / 0.8  # 2500 kWh, puisque Emax = 80 % de la capacité
    E = np.empty(N)
    Pb = np.empty(N)
    Pc = np.empty(N)
    etats = []
    e = cas.Einit
    for k in range(N):
        E[k] = e
        soc = 100 * e / capacite
        heure = (cas.tvec[k] / 3600) % 24
        p = regle.pas(heure, cas.Pload[k] / 1e3, cas.Ppv[k] / 1e3, soc)
        Pc[k] = p
        etats.append(regle.etat)
        # Limitations physiques : puissance, puis énergie disponible ou libre.
        p = min(max(p, b.Pmin), b.Pmax)
        p = min(p, (e - b.Emin) / dt)    # ne pas descendre sous Emin
        p = max(p, -(b.Emax - e) / dt)   # ne pas monter au-dessus de Emax
        Pb[k] = p
        e = e - p * dt
    Pgrid = cas.Pload - cas.Ppv - Pb
    return Simulation(Pgrid, Pb, E, Pc, etats, cout=dt * cas.Cost @ Pgrid)


def cout_dollars_simulink(cas: CasOptimisation, pgrid: np.ndarray) -> float:
    """Coût tel que l'affiche compareCosts : prix décalé de +5, en c$/kWh, converti en $."""
    return cas.dt * (cas.Cost + 5) @ pgrid / J_PAR_KWH / 100


def resume_etats(t_h: np.ndarray, etats: list) -> list[tuple[float, float, str]]:
    """Regroupe la suite d'états en plages horaires [début, fin, état]."""
    plages, debut = [], 0
    for k in range(1, len(etats) + 1):
        if k == len(etats) or etats[k] != etats[debut]:
            plages.append((t_h[debut] - (t_h[1] - t_h[0]), t_h[k - 1], etats[debut]))
            debut = k
    return plages


def main() -> None:
    cas = cas_matlab_only()
    cout_sans = cas.dt * cas.Cost @ (cas.Pload - cas.Ppv)
    t_h = cas.tvec / 3600

    sims = {}
    for sem in ("stateflow", "continu"):
        sim = simuler(cas, RegleStateflow(cas.batterie.Pmin, cas.batterie.Pmax, sem))
        sims[sem] = sim
        print(f"\n=== Lecture « {sem} » ===")
        for debut, fin, etat in resume_etats(t_h, sim.etats):
            print(f"  {debut:5.2f} h -> {fin:5.2f} h  {etat}")
        ecretees = np.abs(sim.Pcmd - sim.Pbatt).sum() * cas.dt / J_PAR_KWH
        print(f"  Coût : {sim.cout / J_PAR_KWH:,.1f} (prix × kWh), "
              f"gain {1 - sim.cout / cout_sans:.1%} vs sans batterie")
        print(f"  Équivalent Simulink : {cout_dollars_simulink(cas, sim.Pgrid):.0f} $")
        print(f"  Énergie finale : {sim.Ebatt[-1] / J_PAR_KWH - sim.Pbatt[-1] * cas.dt / J_PAR_KWH:.0f} kWh ; "
              f"consignes refusées par les limites : {ecretees:.0f} kWh")
    print(f"\nRéférence sans batterie : {cout_sans / J_PAR_KWH:,.1f}, "
          f"soit {cout_dollars_simulink(cas, cas.Pload - cas.Ppv):.0f} $ en équivalent Simulink")

    fig, ax = plt.subplots(2, 1, figsize=(10, 7), sharex=True)
    for sem, style in (("stateflow", "-"), ("continu", "--")):
        ax[0].plot(t_h, sims[sem].Ebatt / J_PAR_KWH, style, label=f"lecture « {sem} »")
        ax[1].plot(t_h, sims[sem].Pbatt / 1e3, style, label=f"batterie, « {sem} »")
    ax[1].plot(t_h, (cas.Pload - cas.Ppv) / 1e3, color="grey", lw=1, label="besoin net charge − PV")
    ax[0].axhline(cas.batterie.Emin / J_PAR_KWH, color="k", lw=0.6, ls=":")
    ax[0].axhline(cas.batterie.Emax / J_PAR_KWH, color="k", lw=0.6, ls=":")
    ax[0].set_ylabel("Énergie batterie [kWh]")
    ax[0].set_title("Règle Stateflow HeuristicEMS rejouée sur la journée MathWorks")
    ax[0].legend()
    ax[1].set_ylabel("Puissance [kW] (> 0 décharge)")
    ax[1].set_xlabel("Heure de la journée")
    ax[1].set_xticks(range(0, 25, 3))
    ax[1].legend(fontsize=8)
    for a in ax:
        a.grid(alpha=0.3)
    # Bandes horaires de la règle : 5-15 h absorption PV, 16-23 h pointe.
    for a in ax:
        a.axvspan(5, 15, color="gold", alpha=0.08)
        a.axvspan(16, 23, color="tab:red", alpha=0.06)
    fig.tight_layout()
    fig.savefig(FIGURE, dpi=120)
    print(f"\nFigure enregistrée : {FIGURE.relative_to(RACINE)}")

    figure_style_simulink(cas, sims["continu"])


def figure_style_simulink(cas: CasOptimisation, sim: Simulation) -> None:
    """Reprend la mise en page de l'oscilloscope « Microgrid Measures » pour comparer à l'œil.

    Axe des temps en secondes, comme dans Simulink. SOC en %, prix en c$/kWh
    décalé de +5, comme le prix réellement facturé par le modèle.
    """
    t_s = cas.tvec
    capacite = cas.batterie.Emax / 0.8
    fig, ax = plt.subplots(3, 1, figsize=(7, 9), sharex=True)
    ax[0].plot(t_s, cas.Ppv / 1e3, color="gold", label="PV")
    ax[0].plot(t_s, sim.Pbatt / 1e3, color="tab:blue", label="ESS")
    ax[0].plot(t_s, sim.Pgrid / 1e3, color="tab:orange", label="Grid")
    ax[0].plot(t_s, cas.Pload / 1e3, color="tab:green", label="Loads")
    ax[0].set_title("Power (kW)")
    ax[0].legend(fontsize=8)
    ax[1].plot(t_s, 100 * sim.Ebatt / capacite, color="gold")
    ax[1].set_title("ESS SOC (%)")
    ax[2].plot(t_s, cas.Cost + 5, color="gold")
    ax[2].set_title("Price (Cents/kW-h)")
    ax[2].set_xlabel("Temps [s]")
    for a in ax:
        a.grid(alpha=0.3)
    fig.suptitle("Python, heuristique : même mise en page que l'oscilloscope Simulink")
    fig.tight_layout()
    fig.savefig(FIGURE_SIMULINK, dpi=110)
    print(f"Figure enregistrée : {FIGURE_SIMULINK.relative_to(RACINE)}")


if __name__ == "__main__":
    main()

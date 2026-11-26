# Analyse du modèle de référence MathWorks

Modèle étudié : *Microgrid Energy Management System (EMS) using Optimization*,
Jonathan LeSage, <https://github.com/jonlesage/Microgrid-EMS-Optimization>.

Fichiers analysés : `battSolarOptimize.m`, `initialConditions.m`, `compareCosts.m`,
`compareCosts_Multi.mlx`, `pvLoadPriceData.mat`, ainsi que la logique Stateflow et
le bloc MATLAB Function contenus dans `microgrid_WithESSOpt.slx`.

La version locale de `initialConditions.m` et `compareCosts.m` diffère de celle du
dépôt GitHub sur un seul point : le facteur `timeScale = 1000` (qui compresse 24 h
de simulation électromagnétique en 86 s) a été retiré, et le coût est lu dans
`logsout{4}` au lieu de `logsout{1}`. Le reste est identique.

---

## 1. `pvLoadPriceData.mat` : les données

| Variable | Taille | Contenu | Plage |
|---|---|---|---|
| `time` | 1441 × 1 | temps en secondes, pas de 60 s, une journée | 60 à 86 460 s |
| `clearDay` | 1441 × 1 | éclairement par ciel clair, W/m² | 0 à 1 014 |
| `cloudyDay` | 1441 × 1 | éclairement par ciel nuageux, W/m² | 0 à 1 217 |
| `loadData` | 1441 × 4 | quatre profils de **fluctuation** de charge, W | -159 à 696 kW |
| `costData` | 1441 × 1 | prix du réseau | 9,0 à 25,3 |

Remarques :

- **Une seule journée, synthétique, au pas de la minute.** Ni date, ni lieu, ni
  source. Ces données servent à faire tourner la démonstration, pas à chiffrer un
  gain réel.
- **La charge n'est pas une charge.** `loadData` est une fluctuation (elle prend
  des valeurs négatives) à laquelle `initialConditions.m` ajoute une base de
  350 kW.
- **L'unité du prix est ambiguë.** Les commentaires disent `$/kWh`, mais des
  valeurs de 9 à 25 ressemblent plutôt à des cents/kWh.
- **L'échelle est dix fois celle de notre site** : 2 500 m² × 30 % ≈ 760 kWc de PV,
  une batterie de 2 500 kWh / 400 kW et une charge de base de 350 kW, contre
  126 kWc et 56 kWh pour notre site.

**Ce que l'on garde :** rien pour les chiffres. Le fichier peut servir de test de
non-régression : une fois porté en Python, notre optimiseur doit retrouver la
solution de `battSolarOptimize` sur ces données.

---

## 2. `battSolarOptimize.m` : l'optimisation

**Rôle.** Construire et résoudre le programme linéaire avec l'interface
`optimproblem` (le solveur appelé en interne est `linprog`).

**Formulation**, avec la convention de signe du code (`Pbatt > 0` = décharge) :

- variables : `Pgrid(1:N)` (libre), `Pbatt(1:N)` dans `[Pmin, Pmax]`,
  `Ebatt(1:N)` dans `[Emin, Emax]` ;
- objectif : `min  dt·Σ Cost(k)·Pgrid(k)  −  FinalWeight·Ebatt(N)` ;
- bilan : `Ppv + Pgrid + Pbatt = Pload` à chaque pas ;
- dynamique : `Ebatt(1) = Einit`, `Ebatt(k) = Ebatt(k−1) − Pbatt(k−1)·dt`.

**Limites relevées :**

1. **Rendement unitaire** : aucune perte entre charge et décharge (limite n°1 de
   la note de cadrage).
2. **Pas de coût d'usure** (limite n°2).
3. **`Pgrid` n'a pas de borne inférieure** : l'injection est permise sans limite
   et rémunérée **au prix d'achat**. En France, le surplus est valorisé bien
   en dessous du prix payé, quand il l'est. Cette hypothèse avantage
   artificiellement l'optimisation, qui peut faire de l'arbitrage
   achat-revente avec la batterie.
4. **Pas de puissance souscrite** : aucune borne supérieure sur `Pgrid`, donc
   aucun écrêtage de pointe.
5. **Unités mélangées dans l'objectif** : le premier terme est en
   prix × W × s, le second en joules. `FinalWeight = 1` vaut donc environ un
   dixième du prix moyen de l'énergie. Le réglage de ce poids n'a pas de sens
   physique : c'est un paramètre empirique.
6. **Le dernier pas de `Pbatt` est libre** : `Pbatt(N)` n'intervient dans aucune
   équation de dynamique, seulement dans le bilan.
7. **En cas d'échec, le code renvoie des zéros** sans prévenir : la batterie
   s'arrête en silence.

---

## 3. Le bloc MATLAB Function du modèle `.slx` : le mode d'appel

**Rôle.** Appeler `battSolarOptimize` pendant la simulation.

- Le bloc est échantillonné à `optTime` = 5 min et appelle l'optimiseur **à chaque
  pas**, sur un horizon `M = 241` pas (20 h), à partir du SOC **mesuré** sur le
  modèle physique.
- Seule la consigne `Pbatt(2)` est appliquée : c'est une **commande à horizon
  glissant (MPC)**.
- Les « prévisions » sont les profils réels décalés (voir section 4). C'est donc
  une MPC **à prévision parfaite**, qui reste une borne optimiste.

**Limites relevées :**

- `M = 241` est **codé en dur** dans le bloc, alors qu'il est aussi calculé dans
  `initialConditions.m`. Si l'on change l'horizon ou le pas, les deux divergent.
- `Pbatt(2)` au lieu de `Pbatt(1)` : la consigne appliquée est celle du pas
  suivant. C'est peut-être voulu, pour compenser un retard d'un échantillon dans
  le modèle, mais rien ne le documente. **À vérifier.**
- Les limites de puissance (±400 kW) et de SOC (20 à 80 %) sont **recopiées** dans
  le bloc au lieu d'être lues dans `initialConditions.m`.

---

## 4. `initialConditions.m` : le paramétrage

**Rôle.** Charger les données, fixer les paramètres du site et de la batterie, et
construire les matrices de prévision `N × M` envoyées à l'optimiseur.

**Points notables :**

- **Prévision parfaite** : `CostMat(i,:) = C(i:i+M-1)`, et de même pour le PV et
  la charge. La prévision à l'instant `i` est exactement le futur réel.
- **Le lendemain est une copie du jour** (`numDays = 2`, `repmat`). En fin de
  journée, l'horizon de 20 h « voit » un lendemain identique.
- **Le PV prévu est toujours celui du ciel clair** (`clearPpvVec`), même quand la
  simulation tourne sur la journée nuageuse (`pvSelect = 1`). Dans ce cas, la
  prévision est fausse, ce qui est intéressant (c'est un premier test de
  robustesse), mais ce n'est pas présenté comme tel.
- **`costDataOffset = costData + 5`** : le prix vu par le modèle physique est
  décalé de +5, alors que l'optimiseur reçoit `C` sans décalage. Un décalage
  constant ne change pas les décisions quand l'injection est payée au même prix,
  mais il fausse la comparaison avec `FinalWeight`. **À vérifier dans le modèle :
  quel prix sert réellement au calcul du coût ?**
- Le script efface ses variables intermédiaires (`clear ...`). Cela rend le
  débogage difficile.

---

## 5. La logique Stateflow `HeuristicEMS` : la règle de référence

Structure lue dans le graphe (on donne la logique d'ensemble, pas l'ordre exact
des transitions) :

| État | Condition d'entrée | Consigne |
|---|---|---|
| `AbsorbSupplyPV` | 5 h < t < 15 h | suivre `Pnet = Load − PV` |
| `Charging` / `Discharging` | PV > charge / charge > PV | `Pcmd = Pnet`, borné |
| `MPPTOff` | surplus PV supérieur à la puissance de charge max | `Pcmd = Pmin`, PV écrêté |
| `LoadShedding` | déficit supérieur à la puissance de décharge max | `Pcmd = Pmax`, délestage |
| `PeakSupply` | t > 16 h | décharge à pleine puissance jusqu'au SOC min |
| `NightRecharge` | t > 23 h | recharge à pleine puissance **depuis le réseau** jusqu'au SOC max |
| `IdleESS` | SOC en butée | `Pcmd = 0` |

**Limites relevées :**

- Les **horaires sont figés** (5 h, 15 h, 16 h, 23 h) et ne regardent ni le prix ni
  la météo.
- La **recharge nocturne depuis le réseau** n'est rentable que si la nuit est
  moins chère que la pointe du soir. Avec des prix de gros réels, ce n'est pas
  toujours le cas.
- La règle **écrête le PV** quand la batterie sature, alors que l'optimisation peut
  injecter sans limite. **Les deux stratégies ne jouent pas avec les mêmes
  règles du jeu.**

---

## 6. `compareCosts.m` et `compareCosts_Multi.mlx` : la comparaison

**`compareCosts.m`** lance trois simulations (heuristique, optimisation, sans
batterie), trace le coût cumulé et l'énergie soutirée, puis affiche l'écart en %.

- C'est une bonne structure, et nous la reprenons : **sans batterie** devient la
  référence basse, comme dans EMSx.
- Il ne calcule que le coût et l'énergie soutirée, et aucun des autres indicateurs
  de la note (dépassements, énergie cyclée, autoconsommation).

**`compareCosts_Multi.mlx`** fait une étude de sensibilité : 2 journées PV × 5
décalages du profil de prix, avec histogramme des coûts.

- **Probable erreur d'unité** : `offset = linspace(1,25,5)` décale le prix de 1 à
  25 **échantillons**, et un échantillon vaut **une minute**. Le décalage maximal
  est donc de 25 minutes, ce qui est presque sans effet. L'intention était
  vraisemblablement de décaler de 1 à 25 **heures** (`× 60`).
- **Les prévisions de prix ne suivent pas le décalage** : le prix décalé est
  envoyé au modèle physique, mais `CostForecast` est construit dans
  `initialConditions.m` à partir du prix non décalé. L'optimiseur optimise donc
  sur un prix qui n'est pas celui facturé. C'est sans conséquence ici puisque le
  décalage ne fait que quelques minutes, mais l'erreur deviendrait visible avec
  un décalage en heures.
- La boucle `for` sur `out(i)` repose sur l'ordre des `SimulationInput`. Une
  table de résultats nommée serait plus sûre.

---

## 7. Le README et le dossier `EMS_Optimization_MATLAB_Only`

Le README annonce « optimization combined with forecast data » et renvoie aux
slides `EMS_Optimization_Formulation.pdf` pour la formulation. Les deux appellent
une précision :

- le mot **forecast** désigne en réalité le futur exact (section 4) : aucune
  erreur de prévision n'est simulée ;
- les slides présentent la formulation matricielle (`Aeq`, `A`, `f`) pour
  `linprog`, mais elles s'écartent du code sur plusieurs points (détail
  ci-dessous) ;
- le dossier **MATLAB only** (`energyOptimizationScript.m`) résout le problème
  **une seule fois** sur la journée, sans modèle physique : c'est l'équivalent
  exact de notre scénario « information parfaite », et le point de comparaison
  le plus simple pour valider notre portage Python ;
- la version `battSolarOptimize_fullMatrices.m` du même dossier écrit les
  matrices à la main et appelle directement `linprog` : c'est celle qui se
  transpose ligne à ligne en `scipy.optimize.linprog`.

**Écarts entre les slides et le code :**

| Point | Slides (p. 7 à 9) | Code `battSolarOptimize` |
|---|---|---|
| Dynamique | `E(k) = E(k−1) + Pbatt(k)·ΔT` | `E(k) = E(k−1) − Pbatt(k−1)·dt` |
| Signe de `Pbatt` | incohérent : `+` dans la dynamique, mais la batterie est une **source** dans le bilan | cohérent : `Pbatt > 0` = décharge |
| Indice de temps | `Pbatt(k)` modifie `E(k)` | `Pbatt(k−1)` modifie `E(k)`, avec un décalage d'un pas |
| Objectif | `Σ Cgrid·Egrid`, sans autre terme | ajoute `− FinalWeight·E(N)` |
| Unité du prix | l'axe de la p. 11 indique `$/kWh` entre 10 et 25 | idem, ce qui est irréaliste : il s'agit vraisemblablement de cents |

Dans notre version, on fixe une convention unique et on l'écrit dans le code :
`P_ch ≥ 0` et `P_dis ≥ 0` séparées, avec
`E(k+1) = E(k) + (ηc·P_ch(k) − P_dis(k)/ηd)·Δt`.

Les slides 10 et 11 montrent le modèle Simulink : simulation électrique en
phasor à 60 Hz, charge fixe de 350 kW plus une charge variable, et un sélecteur
Chart / Optimization. La p. 11 montre que l'optimisation vide la batterie avant
le pic de prix pour la recharger juste avant, puis décharger pendant le pic,
alors que l'heuristique suit ses horaires fixes. Elles ne donnent pas de coût
chiffré.

Prérequis listés : Optimization Toolbox, Simulink, Simscape, Simscape Electrical,
Stateflow. Notre version n'en demande aucun (Python, Pandas, Pyomo, HiGHS).

---

## 8. Ce que l'on améliore dans notre version

| Limite du modèle de référence | Amélioration dans `rejeu-ems` |
|---|---|
| Une journée synthétique au pas minute | une semaine réelle au pas horaire : PVGIS, Enedis, Ember |
| Rendement unitaire | charge et décharge séparées, avec rendements ηc et ηd ; MILP avec une binaire qui interdit de charger et décharger en même temps |
| Usure non tarifée | coût de cyclage en €/kWh déchargé, plus l'indicateur d'énergie cyclée |
| Injection payée au prix d'achat | prix de revente distinct et paramétrable (0 par défaut, ou tarif de surplus) |
| Pas de puissance souscrite | variable de dépassement pénalisée, plus l'indicateur de nombre et d'ampleur des dépassements |
| `FinalWeight` sans unité | contrainte SOC final = SOC initial, pour comparer à énergie stockée égale |
| Prévision parfaite présentée comme un contrôleur | trois scénarios distincts : règle, information parfaite (borne), MPC avec prévision **imparfaite** |
| Paramètres recopiés et `M` codé en dur | un seul fichier de configuration lu par tous les modules |
| Échec du solveur silencieux | erreur explicite si HiGHS ne trouve pas de solution |
| Règle et optimisation avec des règles du jeu différentes | même modèle physique (bilan, bornes, rendements) pour toutes les stratégies : seule la décision change |
| Pas de recharge de véhicules | profil IRVE, d'abord subi, puis pilotable dans un second temps |

La simulation Simulink garde sa place à l'étape suivante, celle du contrôleur
dans la boucle : les consignes produites en Python peuvent être rejouées dans
`microgrid_WithESSOpt.slx` pour vérifier leur tenue sur un modèle électrique.

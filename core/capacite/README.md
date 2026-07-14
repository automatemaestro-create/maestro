# core/capacite — Contrôle de capacité des agents

Dépôt des **réglages de capacité** des agents (EF-21, ticket #86) : depuis la
Control Tower, on active/désactive un agent et on ajuste son nombre
d'instances — avec un **effet réel** sur la file et les workers, sans
redémarrage.

## Fonctionnement

- Un fichier par agent réglé : `<nom>.json` (`actif`, `instances`, horodaté).
  Un agent **sans fichier** a la capacité par défaut : actif, une instance.
- Effet à l'exécution (`maestro/engine/executor.py`, relu **à chaud** à chaque
  tâche, comme les playbooks #78) :
  - un agent **désactivé** est écarté des candidats du routage
    (`Router.route(exclus=...)`) : il ne reçoit plus de tâches — ni par
    auto-assignation, ni par réassignation manuelle (422 côté API) ;
  - le plafond d'**instances** borne ses exécutions simultanées
    (`JaugeInstances`) : une tâche routée vers un agent au complet attend
    qu'un créneau se libère.
- Lecture/écriture par le code : `maestro.agents.capacity.CapacityStore` ; par
  HTTP : `POST /api/agents/{nom}/capacite` (API Control Tower) ; depuis l'UI :
  les boutons activer/désactiver et **+ / −** instances des fiches agents.
- Racine remplaçable par `MAESTRO_CAPACITE_DIR` (cf. `.env.example`).

Les réglages écrits ici sont des **données d'exécution** : ils ne sont pas
commités (voir `.gitignore`). Moteur, workers et API Control Tower doivent voir
le même stockage au POC (fichiers partagés). Limite POC assumée : la jauge
d'instances borne les exécutions simultanées **par process** — exacte pour le
moteur en process, elle ne coordonne pas encore plusieurs workers entre eux
(EF-16, scalabilité horizontale). En V1, ce stockage passera en base (champs
`actif`/`instances_max` de l'entité `AGENT`, docs/03) sans changer le contrat.

Tests (#86) : `tests/test_capacity.py` (dépôt, routage, jauge, API).

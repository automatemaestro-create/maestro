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

## Rangement par projet (#1038)

Depuis [docs/37 §2.1](../../docs/37-decision-equipe-sur-mesure.md), **un agent
appartient à un projet**. Ce dossier porte donc deux niveaux :

- **la racine** — les **gabarits de rôle**, ce qui vaut hors de tout projet et ce
  que l'analyse d'équipe consultera (#1039) ;
- **`_projets/<projet_id>/`** — la même arborescence, pour un projet. Le tiret bas
  le met hors d'atteinte d'un nom d'agent, qui commence par `[a-z0-9]`.

L'API sert ces deux niveaux par `?projet=<id>` (omis : les gabarits), et
l'exécution lit ceux du projet de la tâche. Code :
`maestro/agents/rangement.py` (la règle), `maestro/agents/configuration.py`
(les six dépôts d'un bloc).

**Ce que le projet ne règle pas, il l'hérite du gabarit.** Un agent que le projet n'a pas réglé
garde le réglage du gabarit, et un agent **désactivé** au gabarit le reste. Retomber sur
« actif, une instance » serait ici un garde-fou qui saute, pas un défaut.

⚠ Le **plafond d'instances** reste celui du poste à l'exécution : c'est ce que la
machine fait tourner en même temps pour cet agent, pas un quota par projet. Deux
projets qui lui accordent trois instances n'en ouvrent pas six.

**Reprise.** Ce qu'un poste portait ici avant #1038 est rattaché au projet qui
l'utilise au démarrage de l'API — idempotente, sans rien supprimer, et elle dit
ce qu'elle a fait. À la main : `python -m maestro.agents.reprise [--check]
[--projet <id>]` (`MAESTRO_REPRISE_AGENTS=0` pour s'en passer).

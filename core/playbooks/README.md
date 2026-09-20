# core/playbooks — Playbooks versionnés

Stockage versionné des **playbooks** des agents (leurs instructions, docs/04 §1) —
tickets #76 à #78, exigences EF-24 à EF-26.

## Fonctionnement

- Un dossier par agent (`developpeur/`, `bdd/`, `devops/`, `designer/`, `qa/`),
  une version par fichier : `v0001.md`, `v0002.md`… (append-only).
- La **version courante** est la plus haute ; le **retour arrière** republie une
  version passée comme nouvelle version — l'historique reste linéaire et complet.
- Un agent **sans version stockée** retombe sur son playbook « du code » : ce dossier
  vide reproduit exactement le comportement d'origine. Depuis #295 ce repli n'est plus
  une chaîne Python mais le **document Markdown structuré** du rôle, livré avec le
  paquet (`maestro/agents/playbooks_defaut/<agent>.md`, lu par
  `maestro.agents.playbook_du_code`) — un tronc commun partagé, `_socle.md`, y porte le
  régime sénior de tous les rôles.
- Les **propositions** d'auto-amélioration (#111) vivent à part, dans un
  sous-dossier `<agent>/propositions/` : `p0001.md` (contenu candidat) +
  `p0001.json` (justification), numérotation propre. Elles ne sont **jamais**
  renvoyées par `lire()`/`versions()` — le moteur ne peut donc pas en charger
  une ; appliquer une proposition publie son contenu comme version ordinaire
  (`vNNNN.md`), rejeter la supprime. Détails :
  [docs/22](../../docs/22-auto-amelioration-playbooks.md).
- Lecture/écriture par le code : `maestro.agents.playbooks.PlaybookStore` ; par
  HTTP : les endpoints `/api/playbooks` de l'API Control Tower
  (`maestro/controltower/app.py`).
- Racine remplaçable par `MAESTRO_PLAYBOOKS_DIR` (cf. `.env.example`).

Les versions écrites ici sont des **données d'exécution** (éditées depuis l'éditeur
de l'UI Control Tower, page `/playbooks` — #77 — ou l'API) : elles ne sont pas
commitées (voir `.gitignore`). L'application est **à chaud** (#78, EF-26) :
l'exécuteur relit la version courante **à chaque tâche**, donc une version publiée
vaut pour l'exécution suivante sans reconstruire le moteur ni redémarrer les
workers — qui doivent voir le même stockage que l'API au POC (fichiers partagés).
La version utilisée est **tracée** sur chaque exécution : `playbook_version` sur le
résultat de tâche, au journal (#8) et dans les métadonnées Langfuse ; None si
l'agent a exécuté avec son prompt du code. En V1, ce stockage passera en base
(entité `PLAYBOOK_VERSION`, docs/03) sans changer le contrat.

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

**Ce que le projet ne règle pas, il l'hérite du gabarit.** Tant que le projet n'a publié aucune
version pour un agent, tout se lit au gabarit — playbook courant et historique.
Sa première publication **poursuit la numérotation** du gabarit, si bien que la frise
ne recule jamais ; ensuite le projet a son histoire. Les **propositions**, elles, ne
s'héritent pas : un brouillon s'applique là où il a été déposé.

**Reprise.** Ce qu'un poste portait ici avant #1038 est rattaché au projet qui
l'utilise au démarrage de l'API — idempotente, sans rien supprimer, et elle dit
ce qu'elle a fait. À la main : `python -m maestro.agents.reprise [--check]
[--projet <id>]` (`MAESTRO_REPRISE_AGENTS=0` pour s'en passer).

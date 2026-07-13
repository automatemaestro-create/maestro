# core/playbooks — Playbooks versionnés

Stockage versionné des **playbooks** des agents (leurs instructions, docs/04 §1) —
tickets #76 à #78, exigences EF-24 à EF-26.

## Fonctionnement

- Un dossier par agent (`developpeur/`, `bdd/`, `devops/`, `designer/`, `qa/`),
  une version par fichier : `v0001.md`, `v0002.md`… (append-only).
- La **version courante** est la plus haute ; le **retour arrière** republie une
  version passée comme nouvelle version — l'historique reste linéaire et complet.
- Un agent **sans version stockée** retombe sur son playbook « du code » (les
  prompts système de `maestro/agents/`) : ce dossier vide reproduit exactement le
  comportement d'origine.
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

# core/playbooks — Playbooks versionnés

Stockage versionné des **playbooks** des agents (leurs instructions, docs/04 §1) —
ticket #76, exigences EF-24/EF-25.

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

Les versions écrites ici sont des **données d'exécution** (éditées à chaud depuis
l'API/l'UI) : elles ne sont pas commitées (voir `.gitignore`). Le moteur charge le
playbook courant à sa construction ; l'application à chaud d'un process déjà en vie
arrive avec le ticket #78, l'éditeur UI avec le #77. En V1, ce stockage passera en
base (entité `PLAYBOOK_VERSION`, docs/03) sans changer le contrat.

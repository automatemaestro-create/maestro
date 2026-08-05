# core/projets — Projets de l'utilisateur

Dépôt des **projets déclarés** (EF-35, ticket #221 — socle de la
[Phase 7](../../docs/24-projets-locaux-et-poste-de-travail.md#2-projets-et-espace-de-travail-réel)) :
un projet désigne une **racine sur le disque** de l'utilisateur, avec son
origine, son gestionnaire de versions détecté et son périmètre.

C'est ce qui manquait pour que Maestro travaille *dans* un projet plutôt que de
produire des livrables à recopier à la main : l'espace de travail d'une tâche
était un répertoire temporaire créé vide et détruit en fin d'exécution
(`maestro/sandbox/workspace.py`).

## Fonctionnement

- Un fichier par projet : `<id>.json` (`prj-<8 hex>`), horodaté `cree_le` /
  `modifie_le`. Forme du document (docs/24 §2.3) :

  ```jsonc
  {
    "id": "prj-7f3a1c04",
    "nom": "Dépensio",
    "racine": "D:/projets/depensio",     // frontière unique et déclarée
    "origine": "existant",               // nouveau | existant
    "vcs": { "type": "git", "branche_base": "main", "distant": "git@…" },  // null si non versionné
    "perimetre": {
      "inclus": ["."],                   // relatif à la racine
      "exclus": [".git", "node_modules", ".env", "**/secrets/**"]
    },
    "cree_le": "2026-08-05T09:00:00+00:00",
    "modifie_le": "2026-08-05T09:00:00+00:00"
  }
  ```

- **La racine est validée, jamais prise telle quelle** (EF-38) : le chemin est
  **canonicalisé** (`..` écrasés, liens symboliques suivis), puis refusé s'il
  tombe sur une **racine interdite** — racine de disque, dossier utilisateur nu
  ou l'un de ses ancêtres, `.ssh`/`.gnupg`/`.aws`/`.config`/`AppData`/`Library`,
  dossiers système, et le dépôt de Maestro lui-même (ou un dossier qui le
  contient). **Un refus porte son motif** (`RacineRefusee.motif` :
  `racine-de-disque`, `chemin-sensible`, `depot-maestro`…) — l'écran Projets
  doit pouvoir dire *pourquoi*, jamais ignorer en silence
  ([docs/05 §2.7](../../docs/05-interface-control-tower.md)).
- **L'écriture au-dessus de la racine est barrée** : tout chemin dérivé passe par
  `chemin_dans_racine()`, qui le résout **avant** de vérifier qu'il est sous la
  racine — un `../..` comme un lien symbolique pointant dehors sont refusés.
- **Le VCS est détecté, pas imposé** : un `.git` (dossier, ou fichier `gitdir:`
  d'un worktree) donne `vcs.type = "git"` avec sa branche courante et son
  remote `origin` ; sans lui, `vcs` vaut `null` et **le projet reste
  parfaitement déclarable** — il travaillera par copie plutôt que par worktree
  (#224, décision D2).
- **Les exclusions par défaut sont les deux gisements de secrets** d'un dépôt
  d'utilisateur (`.env`, `**/secrets/**`) plus la plomberie qu'on ne lit jamais
  (`.git`, `node_modules`) — docs/24 §2.5.
- Lecture/écriture par le code : `maestro.projets.ProjetStore`
  (`creer` / `lire` / `lister` / `par_racine` / `ecrire` / `supprimer`). L'API
  HTTP et l'explorateur de dossiers viennent avec #223, l'écran avec #225.
- Racine du dépôt remplaçable par `MAESTRO_PROJETS_DIR` (cf. `.env.example`).

Les projets écrits ici sont des **données d'exécution** — et des chemins du
poste de leur propriétaire : ils ne sont pas commités (voir `.gitignore`).
Moteur, workers et API Control Tower doivent voir le même stockage au POC
(fichiers partagés). En V1, ce stockage passera en base (table `PROJECT`,
[docs/03](../../docs/03-modele-de-donnees.md)) sans changer le contrat.

Ce dépôt ne fait que **déclarer où le projet se trouve** : les agents ne
travaillent jamais directement dans la racine (EF-36) et aucune modification ne
l'atteint sans validation humaine (EF-37) — c'est l'objet des lots #224 et #227.

Tests (#221) : `tests/test_projets.py` (modèle, racines interdites, détection
Git, dépôt). Le reste de la couverture et la doc de la phase reviennent au lot
final #220.

# Contribuer à Maestro

Ce fichier est le **mode d'emploi humain** : du clone à la première Merge Request, en une page.
Il ne remplace pas [`docs/10-workflow-git.md`](./docs/10-workflow-git.md) (la règle complète, avec
les cas particuliers) ni [`CLAUDE.md`](./CLAUDE.md) (les mêmes règles, écrites pour l'agent) — il
dit **par où commencer** et renvoie vers eux pour le détail.

---

## 1. Mettre en route le clone — une commande

```bash
bash scripts/setup.sh
```

C'est la **source unique** du parcours : prérequis (Python 3.11+, Node 20+, git, `glab`), `.venv`,
`.env`, hook git `commit-msg`, dépendances de `apps/web`, réglages Claude Code, Docker + runner CI.
Idempotent et non destructif — le relancer ne casse rien, un `.env` existant n'est jamais écrasé.
`--check` diagnostique sans rien écrire ; `--only <étape>` en rejoue une seule.

Dans une session Claude Code, [`/setup`](./.claude/commands/setup.md) lance ce même script **et**
prend en charge ce qu'il ne peut pas faire seul (authentifications interactives, diagnostic d'un
échec). N'exécutez pas les étapes à la main : le script en est la référence.

### Ce qui reste à votre charge

| Geste | Comment |
|---|---|
| **Secrets partagés** du `.env` (marqués `# [partagé]` dans [`.env.example`](./.env.example)) | `bash scripts/env-pull.sh` — les récupère depuis les variables CI/CD du projet, sans écraser ce qui est déjà posé ([docs/10 §8](./docs/10-workflow-git.md)) |
| **Secrets personnels** du `.env` (marqués `# [perso]` : jetons nominatifs, chemins de machine) | à renseigner à la main, une fois |
| **Figma** | s'authentifier via `/mcp` dans une session Claude Code (OAuth, un clic, par personne) |
| **Réglages machine** de Claude Code | `scripts/setup.sh` écrit `.claude/settings.local.json` (non versionné) ; [`.claude/settings.local.example.json`](./.claude/settings.local.example.json) documente les clés attendues — c'est une **référence à lire**, pas un fichier à recopier |

Vérifier que tout est prêt : `bash scripts/setup.sh --check`, puis `maestro-check-env` — depuis le
venv du dépôt (`.venv/Scripts/maestro-check-env` sous Windows, `.venv/bin/maestro-check-env` sous
Unix), les dépendances n'étant installées que là.

---

## 2. Prendre un ticket

Le backlog vit dans **GitLab**. Un ticket = une branche = une Merge Request.

```
/backlog
```

La vue est groupée par **statut** (À faire / En cours / En revue / Terminé) et signale les tickets
**libres** — ceux sans assigné. Prenez-en un dans « Libres » : un ticket « En cours » assigné à
quelqu'un d'autre est **déjà pris**, le démarrer le lui retirerait.

```
/ticket-start <iid>
```

crée la branche (`<type>/<iid>-<slug>`), vous assigne, passe le statut à « En cours » et pose les
dates. **Ne créez pas la branche à la main** : le nommage et le statut en dépendent.

> Besoin d'un ticket qui n'existe pas encore ? `/ticket-create <type> <titre>`. Au-delà d'environ
> une session de travail, il propose un **découpage** en sous-tickets ([docs/10 §5.1](./docs/10-workflow-git.md)).

---

## 3. Travailler

- **Jamais de commit direct sur `main`.** Toujours sur la branche du ticket.
- **Convention de commit** : [Conventional Commits](https://www.conventionalcommits.org/fr/) +
  `Refs #<iid>` sur les commits intermédiaires, `Closes #<iid>` sur le dernier. Le hook git
  `commit-msg` refuse tout message hors convention — c'est voulu.
- **Python** : toujours via le venv du dépôt (`.venv/Scripts/python.exe` sous Windows,
  `.venv/bin/python` sous Unix). Le `python` système n'a pas les dépendances.
- **Avant de pousser**, rejouer la CI en local évite un aller-retour :

  ```bash
  bash scripts/ci/local.sh      # les mêmes jobs que le pipeline : shellcheck, ruff,
                                # pytest (avec couverture), mypy, build de apps/web
  ```

---

## 4. Clore : une commande

```
/ticket-ship
```

commite ce qui est en attente, pousse, ouvre la Merge Request (`Closes #<iid>`, checklist
renseignée), pose un relecteur, passe le ticket « En revue » et loggue le temps. Si le commit est
déjà fait, `/ticket-finish` fait la même chose sans l'étape de commit.

Ces commandes sont la **source unique** de la clôture : ne rejouez pas `git push` +
`glab mr create` à la main.

Au passage, `/ticket-finish` vérifie si votre branche a pris du **retard sur `origin/main`** et
signale les fichiers modifiés des deux côtés (`CLAUDE.md`, `docs/10`, `lib.sh` sont des aimants à
conflits). Le rebase proposé —

```bash
git fetch origin main && git rebase origin/main
```

— reste **votre décision** : aucune commande ne rebase ni ne force-push à votre place.

---

## 5. Qui relit, qui merge

- **La revue est best-effort** : un relecteur est posé automatiquement (un humain ≠ l'auteur) et la
  file de revue s'affiche dans `/backlog`, mais l'approbation **n'est pas bloquante**. Personne
  n'attend l'autre pour avancer.
- **Le merge est toujours une décision humaine** — jamais un agent, jamais automatique. La
  condition technique est un **pipeline vert**.
- **Pipeline rouge ?** `/pipeline-fix` diagnostique et corrige ce qui l'est en local.
- **Après le merge** : `/branch-cleanup` supprime la branche **locale**, revient sur `main` à jour
  et passe le ticket « Terminé ». La branche **distante**, elle, est supprimée par GitLab au merge
  (la MR est créée avec « supprimer la branche source »).

Pour éclairer une décision de merge : `/mr-review <mr>` (synthèse état + pipeline + threads + diff).

---

## 6. Travailler à plusieurs

- **Deux tickets en parallèle** : un **worktree** par session —
  `bash scripts/git/worktree.sh <iid>` monte un second répertoire de travail sur le même dépôt,
  avec ses propres ports Control Tower et son profil de navigateur. Deux sessions Claude Code sur
  le même dossier se marchent dessus ([docs/10 §9](./docs/10-workflow-git.md)).
- **La CI est partagée** : un runner monté sur une machine toujours allumée sert toute l'équipe ;
  celui de votre poste est un secours. Sans aucun runner en ligne, les pipelines restent `pending`
  et personne ne peut merger ([docs/10 §8.1](./docs/10-workflow-git.md)).
- **Bilan de santé** (lecture seule) : `bash scripts/gitlab/doctor.sh` détecte les dérives
  (ticket « En revue » sans MR, branche mergée à nettoyer, réglages de merge retombés).

---

## 7. Les garde-fous, en bref

Ce qu'aucune commande — et personne — ne fait automatiquement :

- **merger ou fermer une MR** ;
- **force-push** une branche déjà poussée (`--force`, `--force-with-lease`) ;
- **supprimer une branche** dont GitLab ne confirme pas la MR comme `merged` ;
- **committer sur `main`**.

Ces règles sont doublées par la couche permissions de [`.claude/settings.json`](./.claude/settings.json)
(`deny` sur les force-push et `glab mr merge`/`close`) — un filet, pas un remplacement du jugement.

---

## Où aller ensuite

| Question | Fichier |
|---|---|
| Le workflow Git complet (statuts, découpage, CI, worktrees) | [docs/10-workflow-git.md](./docs/10-workflow-git.md) |
| Ce que le projet est et où il en est | [README.md](./README.md), [docs/06-roadmap.md](./docs/06-roadmap.md) |
| Les règles telles que l'agent les applique | [CLAUDE.md](./CLAUDE.md) |
| Démarrer la Control Tower en local | skill `control-tower` |

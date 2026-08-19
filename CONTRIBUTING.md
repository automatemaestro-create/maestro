# Contribuer à Maestro

Ce fichier est le **mode d'emploi humain** : du clone à la première Pull Request, en une page.
Il ne remplace pas [`docs/10-workflow-git.md`](./docs/10-workflow-git.md) (la règle complète, avec
les cas particuliers) ni [`CLAUDE.md`](./CLAUDE.md) (les mêmes règles, écrites pour l'agent) — il
dit **par où commencer** et renvoie vers eux pour le détail.

> **Le projet vit sur GitHub** — [`automatemaestro-create/maestro`](https://github.com/automatemaestro-create/maestro).
> Tickets, Pull Requests et CI y sont depuis la bascule du **2026-08-17** (#343). Le projet GitLab
> est **archivé en lecture seule** : il reste l'archive des 271 Merge Requests d'avant la bascule,
> plus rien ne s'y écrit. Voir [§8](#8-larchive-gitlab).

---

## 1. Mettre en route le clone — une commande

```bash
git clone https://github.com/automatemaestro-create/maestro.git
cd maestro
bash scripts/setup.sh
```

C'est la **source unique** du parcours : prérequis (Python 3.11+, Node 20+, git, `gh`, `glab`),
`.venv`, `.env`, hook git `commit-msg`, dépendances de `apps/web`, réglages Claude Code, Docker.
Idempotent et non destructif — le relancer ne casse rien, un `.env` existant n'est jamais écrasé.
`--check` diagnostique sans rien écrire ; `--only <étape>` en rejoue une seule.

Dans une session Claude Code, [`/setup`](./.claude/commands/setup.md) lance ce même script **et**
prend en charge ce qu'il ne peut pas faire seul (authentifications interactives, diagnostic d'un
échec). N'exécutez pas les étapes à la main : le script en est la référence.

### Ce qui reste à votre charge

| Geste | Comment |
|---|---|
| **Secrets partagés** du `.env` (marqués `# [partagé]` dans [`.env.example`](./.env.example)) | `bash scripts/env-pull.sh` — les récupère depuis les variables CI/CD du projet, sans écraser ce qui est déjà posé ([docs/10 §7.3](./docs/10-workflow-git.md)) |
| **Secrets personnels** du `.env` (marqués `# [perso]` : jetons nominatifs, chemins de machine) | à renseigner à la main, une fois |
| **Figma** | s'authentifier via `/mcp` dans une session Claude Code (OAuth, un clic, par personne) |
| **Réglages machine** de Claude Code | `scripts/setup.sh` écrit `.claude/settings.local.json` (non versionné) ; [`.claude/settings.local.example.json`](./.claude/settings.local.example.json) documente les clés attendues — c'est une **référence à lire**, pas un fichier à recopier |

Vérifier que tout est prêt : `bash scripts/setup.sh --check`, puis `maestro-check-env` — depuis le
venv du dépôt (`.venv/Scripts/maestro-check-env` sous Windows, `.venv/bin/maestro-check-env` sous
Unix), les dépendances n'étant installées que là.

---

## 2. Prendre un ticket

Le backlog vit dans **GitHub**. Un ticket = une branche = une Pull Request.

```
/backlog
```

La vue est groupée par **statut** (À faire / En cours / En revue / Terminé) et signale les tickets
**libres** — ceux sans assigné. Prenez-en un dans « Libres » : un ticket « En cours » assigné à
quelqu'un d'autre est **déjà pris**, le démarrer le lui retirerait.

> **Où vit ce statut, et pourquoi ça se sait.** Dans le champ **Status** du projet GitHub Projects v2
> — six valeurs, seul support depuis #365 ([docs/10 §3.1](./docs/10-workflow-git.md)). Il ne vit donc
> **pas sur l'issue mais sur son item de projet** : un ticket ouvert depuis l'interface web de GitHub
> n'est dans aucun projet, n'a **aucun état**, et sort de tous les comptes — `/backlog` le rend « - »
> et `/ticket-start` refusera de lui en poser un. La réparation est un geste :
> `bash scripts/gitlab/lib.sh project-add <iid> "À faire"`. Créez plutôt vos tickets avec
> `/ticket-create`, qui s'en charge. (Le cycle de vie a été porté un temps par des labels
> `workflow::*` — c'était le seul mécanisme que GitLab Free laissait, pas un choix ; le champ
> **remplace** ce repli, il ne le défait pas.)

```
/ticket-start <iid>
```

monte le **worktree du ticket** et y bascule la session, crée la branche (`<type>/<iid>-<slug>`),
vous assigne, passe le statut à « En cours » et pose les dates. **Ne créez ni la branche ni le
worktree à la main** : le nommage, le statut et les ports dédiés en dépendent.

Votre clone principal, lui, **ne change pas de branche** : il reste sur `main`, disponible pour
lire du code ou relire une MR pendant que le ticket avance à côté ([docs/10 §9.1](./docs/10-workflow-git.md)).

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
- **La CI distante ne tourne que sur les Merge Requests** : un push sur votre branche ne déclenche
  rien tant que la MR n'est pas ouverte, et `main` n'est plus rejoué après le merge
  ([docs/10 §8](./docs/10-workflow-git.md)). Le filet, c'est donc le local :

  ```bash
  bash scripts/ci/local.sh      # les mêmes jobs que le pipeline : shellcheck, ruff,
                                # pytest, mypy, build de apps/web
  bash scripts/ci/local.sh --complet   # + la suite pytest ENTIÈRE et sa couverture
  ```

  Par défaut, **pytest n'y joue que les suites concernées par votre diff** ([docs/10
  §8.4](./docs/10-workflow-git.md)) : la suite complète prend 10 minutes, et c'est le pipeline de
  la MR qui la joue — inutile de l'attendre à chaque itération. Le lint, lui, tourne toujours en
  entier.

---

## 4. Clore : une commande

```
/ticket-ship
```

commite ce qui est en attente, pousse, ouvre la Pull Request (`Closes #<iid>`, checklist
renseignée), passe le ticket « En revue » et loggue le temps. Aucun relecteur n'est désigné au
passage — voir §5. Si le commit est déjà fait, `/ticket-finish` fait la même chose sans l'étape de
commit.

Ces commandes sont la **source unique** de la clôture : ne rejouez pas `git push` +
`gh pr create` à la main.

Au passage, `/ticket-finish` vérifie si votre branche a pris du **retard sur `origin/main`** et
signale les fichiers modifiés des deux côtés (`CLAUDE.md`, `docs/10`, `lib.sh` sont des aimants à
conflits). Le rebase proposé —

```bash
git fetch origin main && git rebase origin/main
```

— reste **votre décision** : aucune commande ne rebase ni ne force-push à votre place.

---

## 5. Qui relit, qui merge

- **La revue est best-effort** : personne n'est désigné d'office relecteur — c'est la **file de
  revue** de `/backlog` (les PR ouvertes, la plus ancienne d'abord) qui appelle une relecture, et
  l'approbation **n'est pas bloquante**. Personne n'attend l'autre pour avancer. Pour vous attribuer
  une PR — ou en confier une à quelqu'un — la pose se fait à la main :
  `bash scripts/gitlab/lib.sh set-reviewer <pr|branche> [username]`.
- **Le merge est toujours une décision humaine** — jamais un agent, jamais automatique. La
  condition technique est une **CI verte** (GitHub Actions, en autorité depuis #338).
- **PR bloquée ?** `/mr-fix` la rend mergeable : il résout le conflit avec `origin/main` s'il y en
  a un, puis remet la CI au vert pour ce qui est corrigeable en local.
- **Après le merge** : `/branch-cleanup` supprime la branche **locale**, revient sur `main` à jour
  et passe le ticket « Terminé ». La branche **distante**, elle, est supprimée au merge. Le merge
  **ferme** le ticket mais ne le passe pas « Terminé » tout seul — le cycle de vie est porté par le
  champ **Status** du projet ([docs/10 §3.8](./docs/10-workflow-git.md)), que rien côté forge ne
  met à jour : un ticket fraîchement mergé reste affiché « En revue » jusqu'à cette commande. C'est normal quelques
  minutes, pas quelques jours.

Pour éclairer une décision de merge : `/mr-review <pr>` (synthèse état + CI + threads + diff).

---

## 6. Travailler à plusieurs

- **Deux tickets en parallèle** : rien à faire de particulier — `/ticket-start` monte un
  **worktree** par ticket, avec ses propres ports Control Tower et son profil de navigateur. Deux
  sessions Claude Code sur le même dossier se marcheraient dessus ; elles n'y sont plus
  ([docs/10 §9](./docs/10-workflow-git.md)). Le geste manuel reste `bash scripts/git/worktree.sh <iid>`.
  Et rien à ranger derrière : les worktrees dont la MR est mergée sont **ramassés d'office**
  (`worktree.sh gc`, [§9.2](./docs/10-workflow-git.md)) — sauf s'ils portent du travail non
  sauvegardé, qu'ils signalent alors au lieu de disparaître avec.
- **La CI ne demande rien** : elle tourne sur les exécutants hébergés de GitHub, il n'y a aucun
  runner à monter ni à laisser allumé ([docs/10 §8.1](./docs/10-workflow-git.md)). Docker n'est
  utile que pour les bases locales, optionnelles.
- **Bilan de santé** (lecture seule) : `bash scripts/gitlab/doctor.sh` détecte les dérives
  (ticket « En revue » sans MR, branche mergée à nettoyer, réglages de merge retombés).
- **Laisser la machine dérouler le backlog** : `bash scripts/orchestrate/run.sh` traite les tickets
  libres du milestone courant un par un — un worktree et une session Claude Code chacun, de
  `/ticket-start` à `/ticket-ship`, avec reprise automatique après la limite d'usage de 5 h. À
  lancer dans un terminal à part (`--dry-run` d'abord pour voir le plan) ; il produit des **MR en
  Draft à relire** et ne merge jamais ([docs/10 §11](./docs/10-workflow-git.md)). Un run coupé
  (console fermée, machine éteinte) se reprend par `--resume`, qui rejoue son plan sans rien
  recalculer — `/orchestrate` le propose de lui-même au lancement ([§11.8](./docs/10-workflow-git.md)).

---

## 7. Les garde-fous, en bref

Ce qu'aucune commande — et personne — ne fait automatiquement :

- **merger ou fermer une PR** ;
- **force-push** une branche déjà poussée (`--force`, `--force-with-lease`) ;
- **supprimer une branche** dont la forge ne confirme pas la PR comme `merged` ;
- **committer sur `main`** ;
- **clôturer un ticket qui n'est pas celui de la session** : `/ticket-finish` et `/ticket-ship`
  s'arrêtent avant toute écriture si l'iid visé ne correspond pas à la branche courante, ou si le
  ticket est assigné à quelqu'un d'autre — de sorte qu'on ne pose jamais une PR, un statut ni un
  temps sur le travail d'un collègue.

Ces règles sont doublées par la couche permissions de [`.claude/settings.json`](./.claude/settings.json)
(`deny` sur les force-push et `gh pr merge`/`close`) — un filet, pas un remplacement du jugement.

---

## 8. L'archive GitLab

Le projet GitLab est **archivé en lecture seule** depuis le **2026-08-17** (#343). On peut tout y
consulter, rien y écrire.

**Ce qu'on y trouve encore, et nulle part ailleurs** : les **281 Merge Requests** d'avant la bascule
(diffs, fils de discussion, verdicts de pipeline) — elles n'ont pas été rejouées en PR, faute de
leurs branches d'origine, supprimées au merge ; et le **time tracking natif**, 629 h sur 273
tickets, dont GitHub n'a aucun équivalent.

**Ce qui n'y est plus** : le backlog vivant, passé sur GitHub avec la plage `#1`→`#356` préservée au
numéro près — un `Refs #123` d'un vieux commit y pointe toujours vers le bon ticket. Le temps passé,
lui, **continue** d'être suivi côté GitHub sous forme maison (commentaire `maestro:suivi:v1`), et
l'historique GitLab y a été importé sous cette forme.

En pratique : tout ce qui date d'**avant** le 2026-08-17 et concerne une **revue** se cherche sur
GitLab ; tout le reste, sur GitHub. Le détail — tableau de ce qui a suivi et de ce qui est resté —
est en [docs/27 §11](./docs/27-decision-gitlab-vers-github.md).

Pour relire l'archive : l'**UI web de GitLab**, ou en ligne de commande
`glab <verbe> --repo maestro-group4345327/maestro`. Le `--repo` n'est pas optionnel — `glab` déduit
sinon le projet des remotes, qui pointent maintenant sur GitHub.

---

## Où aller ensuite

| Question | Fichier |
|---|---|
| Le workflow Git complet (statuts, découpage, CI, worktrees) | [docs/10-workflow-git.md](./docs/10-workflow-git.md) |
| Ce qui change quand on travaille **à plusieurs** (synthèse) | [docs/10 §10](./docs/10-workflow-git.md) |
| Laisser la machine **dérouler le backlog** sans supervision | [docs/10 §11](./docs/10-workflow-git.md), commande `/orchestrate` |
| Ce que le projet est et où il en est | [README.md](./README.md), [docs/06-roadmap.md](./docs/06-roadmap.md) |
| Ce qui reste sur l'**archive GitLab**, et pourquoi | [docs/27 §11](./docs/27-decision-gitlab-vers-github.md) |
| Les règles telles que l'agent les applique | [CLAUDE.md](./CLAUDE.md) |
| Démarrer la Control Tower en local | skill `control-tower` |

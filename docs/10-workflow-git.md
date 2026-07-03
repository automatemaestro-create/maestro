# Workflow Git & tickets — Maestro

**Version :** 0.2
Objectif : que chaque ticket GitLab soit traité de façon prévisible — même branche, même convention de commit, même cycle de vie — que ce soit un humain ou un agent Claude Code qui l'exécute.

> Ce document décrit le schéma de labels **réellement utilisé** sur le projet GitLab (créé par
> l'automatisation `MaestroAgents` dès la Phase 0). Ne pas réinventer de nouveaux labels
> `type::`/`status::`/`priority::` en anglais — utiliser ceux ci-dessous.

---

## 1. Modèle de branches

**Trunk-based léger.** Pas de branche `develop`, pas de branches `release/*`. `main` est toujours stable et déployable.

```
main ──●──●──────●──────────●──●──▶
        \        \          \
         feat/12-…  fix/15-…  chore/8-…
              (courte durée de vie, supprimée après merge)
```

Règles :

1. **Jamais de commit direct sur `main`.** Tout changement passe par une branche + une Merge Request (MR).
2. **Une branche = un ticket = une MR.** Pas de branche fourre-tout multi-tickets.
3. Une branche part toujours de `main` à jour (`git pull origin main` avant `git checkout -b`).
4. Une branche est **courte** : quelques heures à quelques jours. Si un ticket prend plus longtemps, il est probablement trop gros — le redécouper.
5. La branche est supprimée (locale + distante) dès que la MR est mergée.

### Nommage des branches

```
<type>/<iid>-<slug>
```

- `iid` : l'ID du ticket GitLab (le numéro affiché dans l'issue, ex. `#12` → `12`).
- `slug` : le titre du ticket en minuscules, sans accents, mots séparés par `-`, tronqué à ~40 caractères.
- `type` : dérivé du label `type::*` du ticket (voir §3).

| Label du ticket | Préfixe de branche | Exemple |
|---|---|---|
| `type::feature` | `feat` | `feat/6-boucle-orchestration` |
| `type::bug` | `fix` | `fix/15-session-expiree-trop-tot` |
| `type::infra` | `chore` | `chore/8-journalisation-couts` |
| `type::doc` | `docs` | `docs/10-guide-contribution` |

La commande [`/ticket-start`](../.claude/commands/ticket-start.md) applique cette règle automatiquement.

---

## 2. Convention de commit

[Conventional Commits](https://www.conventionalcommits.org/), avec référence au ticket en pied de message :

```
<type>(<scope>): <description courte à l'impératif>

<corps optionnel — le pourquoi, pas le quoi>

Refs #<iid>
```

- `type` : `feat`, `fix`, `chore`, `docs`, `refactor`, `test`, `ci`, `build`, `perf`.
- `scope` : optionnel, le dossier/module concerné (`api`, `web`, `router`, `queue`…).
- Le **dernier commit de la branche** (ou la description de la MR) contient `Closes #<iid>` plutôt que `Refs #<iid>` — GitLab ferme alors le ticket automatiquement au merge.

Exemple :

```
feat(api): ajouter l'endpoint POST /login

Valide les identifiants et retourne un JWT.

Closes #12
```

---

## 3. Labels GitLab (scoped labels)

Quatre familles de labels **exclusifs entre eux** (`::` = un seul actif par famille sur GitLab),
déjà en place sur le projet :

| Famille | Valeurs | Usage |
|---|---|---|
| `type::` | `feature`, `bug`, `doc`, `infra` | Nature du ticket → détermine le préfixe de branche (§1) |
| `agent::` | `dev`, `bdd`, `devops`, `design`, `qa`, `orchestrateur` | Quel rôle/agent Maestro traite ce ticket — reflète les agents décrits dans le [README](../README.md) |
| `workflow::` | `à faire`, `en cours`, `en revue`, `terminé` | Où en est le ticket (cycle de vie, §5) |
| `prio::` | `haute`, `moyenne`, `basse` | Urgence, pour le tri du backlog |

`workflow::à faire` est le défaut à la création (posé par les templates d'issue, §4).
Contrairement à un schéma classique todo/in-progress/review, celui-ci a un état terminal
explicite `workflow::terminé`, posé **en plus** de la fermeture de l'issue (les deux vont
ensemble — voir issue #1 comme référence).

Les labels sont créés une fois pour toutes (idempotent) via
[`scripts/gitlab/bootstrap.sh`](../scripts/gitlab/bootstrap.sh).

`agent::*` et `prio::*` ne sont pas touchés par les commandes `/ticket-*` : ils relèvent du
triage (fait à la création du ticket), pas du cycle Git.

---

## 4. Templates GitLab

- **Issues** (`.gitlab/issue_templates/`) : `Feature.md`, `Bug.md`, `Doc.md`, `Infra.md` — un
  par valeur de `type::*`. Posent la structure attendue (contexte, critères d'acceptation) et
  appliquent `workflow::à faire` + le bon `type::*` via une quick action `/label` intégrée au
  template. Le label `agent::*` est à ajouter manuellement au triage (aucun template ne peut
  deviner quel agent est concerné).
- **Merge Request** (`.gitlab/merge_request_templates/Default.md`) : checklist de definition
  of done + rappel `Closes #`.

---

## 5. Cycle de vie d'un ticket

```
à faire ──/ticket-start──▶ en cours ──/ticket-finish──▶ en revue ──(merge humain)──▶ terminé
                                                                          │
                                                                    /branch-cleanup
```

1. **`workflow::à faire`** — le ticket existe, personne ne travaille dessus.
2. **`/ticket-start <iid>`** — crée/checkout la branche, assigne le ticket à l'exécutant, passe
   le label en `workflow::en cours`.
3. Développement sur la branche (commits `Refs #<iid>`).
4. **`/ticket-finish`** — pousse la branche, ouvre (ou passe en "Ready") la MR avec
   `Closes #<iid>`, passe le label en `workflow::en revue`.
5. **Revue + merge** — **toujours une action humaine** (voir garde-fous, §6). Le merge ferme
   le ticket automatiquement.
6. **`/branch-cleanup`** — une fois la MR mergée : supprime la branche locale et distante,
   revient sur `main` à jour, et pose `workflow::terminé` sur le ticket.

Détail des commandes : [`.claude/commands/`](../.claude/commands/).

---

## 6. Garde-fous

Cohérent avec le principe « autonomie sous supervision » du projet (voir [README](../README.md)) :

- **Aucune commande n'effectue de merge ou de fermeture de MR automatiquement.** La revue et le merge restent une décision humaine.
- **Aucun force-push** sur une branche déjà poussée.
- La suppression de branche locale utilise toujours `git branch -d` (jamais `-D`) : Git refuse de supprimer une branche non mergée, ce qui protège d'une perte de travail.
- Une branche distante n'est supprimée que si GitLab confirme que sa MR est à l'état `merged`.

---

## 7. Prérequis

- [`glab`](https://gitlab.com/gitlab-org/cli) installé et authentifié : `glab auth login`.
- Vérifier l'accès : `glab issue list` doit lister les tickets du projet.

---

## 8. Portée actuelle

Ce workflow couvre la gestion des branches et des tickets. Il ne couvre **pas encore** de
pipeline CI (`.gitlab-ci.yml`) — le monorepo est encore un squelette sans code exécutable (voir
[roadmap](./06-roadmap.md), Phase 0). Quand du code apparaît dans `apps/`, `core/` ou
`packages/`, ajouter un pipeline de lint/test et faire du "pipeline vert" une condition de
passage `workflow::en revue` → merge.

#!/usr/bin/env bash
# Pose « Terminé » quand un ticket se ferme — la décision du workflow GitHub Actions
# `.github/workflows/cycle-de-vie.yml` (ticket #377, docs/10 §9.2).
#
# Le merge d'une PR FERME le ticket (`Closes #<iid>`) mais ne touche à aucun état : « Terminé »
# n'était posé qu'ensuite, par `worktree.sh gc` (#275) — donc au prochain `/ticket-start`, au
# prochain `/branch-cleanup` ou au démarrage d'un run, et sur la SEULE MACHINE qui les lance. D'où
# la limite que §9.2 assumait : le board était faux AU REPOS. Ce script déplace la pose à
# l'ÉVÉNEMENT : elle a lieu quel que soit l'auteur du merge et la machine d'où il vient — y compris
# un merge fait depuis l'interface web, que rien ne voyait.
#
# L'objection qui l'interdisait était propre à GitLab (« pas un job post-merge, qui obligerait à
# rouvrir un pipeline sur `main` ») : `on: issues: [closed]` est un workflow ÉVÉNEMENTIEL, pas un
# pipeline de push. La raison du refus n'existe plus.
#
# Usage :
#   bash scripts/github/ticket-ferme.sh <iid> [<state_reason>]
#
# Codes de retour : 0 = posé, ou ABSTENTION voulue (raison non « completed », secret absent, état
# déjà final) ; 1 = la pose a échoué ; 2 = usage. Le 1 laisse le run ROUGE dans l'onglet Actions —
# c'est là toute la visibilité qu'on peut lui donner, et il ne casse rien : ce workflow ne
# conditionne aucun merge, `worktree.sh gc` gardant son rôle de filet de rattrapage.
#
# ═══════════════════════════════════════════════════════════════════════════════════════════════
# DEUX BARRIÈRES DEVANT « ABANDONNÉ »/« DOUBLON », ET LA SECONDE EST CELLE QUI PORTE
# ═══════════════════════════════════════════════════════════════════════════════════════════════
# Écraser l'état d'un ticket abandonné est la dérive que §9.2 qualifie de « sans retour possible,
# puisque rien dans le ticket ne dirait qu'il a été abandonné ». Deux filtres l'en empêchent, et
# savoir LEQUEL protège réellement importe plus que l'ordre dans lequel on les lit :
#
#   1. LA RAISON DE FERMETURE, ici — liste BLANCHE sur « completed ». En liste blanche et non en
#      exclusion de « not_planned » : GitHub a ajouté « duplicate » à l'énumération sans rien nous
#      demander, et une liste noire aurait laissé passer chaque valeur suivante.
#
#   2. L'ÉTAT COURANT du ticket, dans `lib.sh reconcile-workflow` — qui saute « Abandonné »,
#      « Doublon » et « Terminé » (filtre de #275). C'EST CELUI-CI QUI PROTÈGE `/ticket-abandon` :
#      la commande ferme par un `gh issue close <iid>` NU, donc GitHub y met `state_reason:
#      completed` comme sur n'importe quel merge — la barrière n°1 le laisse passer, et seule la
#      lecture de l'état l'arrête. Ce qui la sauve est l'ORDRE de la commande : elle pose
#      « Abandonné » (étape 6) PUIS ferme (étape 7), donc l'état est déjà là quand ce script lit.
#
# La barrière n°1 n'est donc pas la protection principale mais le filet du geste MANUEL : un ticket
# fermé « as not planned » depuis l'interface web, sans qu'aucun état ait été posé, ne doit pas
# ressortir « Terminé ».
#
# ⚠ Cet écart est CONNU et TICKETÉ (#388) : passer `--reason "not planned"` à l'étape 7 de
# `/ticket-abandon` ferait tomber l'abandon dans la barrière n°1, et corrigerait au passage
# l'affichage GitHub (un ticket abandonné y porte aujourd'hui l'icône « Completed »). Ce n'est pas
# fait ici — hors périmètre de #377, et une session Claude Code ne peut pas écrire sous `.claude/`
# (#229). Tant que ce n'est pas fait, ne pas relire ce bloc comme une défense en profondeur : il
# n'y a qu'une couche active devant un abandon, et c'est la n°2.
# ═══════════════════════════════════════════════════════════════════════════════════════════════
set -euo pipefail

iid="${1:-}"
raison="${2:-}"

if [ -z "$iid" ] || [ -n "${iid//[0-9]/}" ]; then
  echo "usage: $0 <iid> [<state_reason>]" >&2
  exit 2
fi

# La raison, d'abord : sur un ticket abandonné il n'y a rien à dire du secret, et ce script tourne
# à CHAQUE fermeture de ticket — le journal reste lisible s'il ne parle que de ce qui le concerne.
if [ "$raison" != "completed" ]; then
  printf '#%s fermé en « %s » : rien à poser (seul « completed » vaut livraison).\n' \
    "$iid" "${raison:-sans raison}"
  exit 0
fi

# `GITHUB_TOKEN` n'est VOLONTAIREMENT pas un repli. Il ne peut pas écrire dans un Projects v2
# appartenant à un compte utilisateur — c'est ce qu'a établi #359 : le blocage est le TYPE de jeton,
# pas une permission qu'on aurait oublié de cocher. L'accepter ferait tenter une écriture vouée à un
# 403, et ce 403 masquerait le vrai diagnostic, qui est celui-ci.
if [ -z "${GH_TOKEN:-}" ]; then
  echo "Aucun jeton : le secret de dépôt MAESTRO_PROJECT_TOKEN n'est pas posé — rien n'est écrit."
  echo "  Il lui faut la portée « project » (jeton classique ou OAuth ; un fine-grained ne peut pas"
  echo "  écrire dans le projet d'un compte utilisateur — docs/10 §3.5)."
  echo "  Le filet de rattrapage reste en place : bash scripts/git/worktree.sh gc (docs/10 §9.2)."
  exit 0
fi

lib="$(cd "$(dirname "${BASH_SOURCE[0]}")/../gitlab" && pwd)/lib.sh"
if [ ! -f "$lib" ]; then
  echo "scripts/gitlab/lib.sh introuvable depuis $0" >&2
  exit 1
fi

# LA POSE N'EST PAS RÉÉCRITE ICI, elle est déléguée. `reconcile-workflow <iid>` est le verbe de
# #275 : il lit l'état, saute les trois états finaux, pose « Terminé » sinon, et il est idempotent
# — reposer une valeur déjà présente ne change rien. Le recopier ferait deux formulations du même
# filtre à tenir d'accord, et une seule des deux qu'on penserait à corriger.
printf '#%s fermé comme réalisé — réconciliation du cycle de vie.\n' "$iid"
if bash "$lib" reconcile-workflow "$iid"; then
  exit 0
fi

echo "Pose de « Terminé » en échec sur #$iid — le run reste rouge, rien d'autre n'en dépend." >&2
echo "  Rattrapage : bash scripts/gitlab/lib.sh reconcile-workflow $iid" >&2
exit 1

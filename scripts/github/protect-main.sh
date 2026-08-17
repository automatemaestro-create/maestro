#!/usr/bin/env bash
# Protège `main` sur le dépôt GitHub du projet : les six jobs de `.github/workflows/ci.yml`
# deviennent des checks REQUIS, donc aucun merge sans verdict vert (ticket #338, chantier #335).
#
# C'est le pendant GitHub de ce que `scripts/gitlab/bootstrap.sh` pose côté GitLab avec
# `only_allow_merge_if_pipeline_succeeds` — et, comme lui, c'est la SOURCE UNIQUE du réglage :
# à rejouer sur un dépôt neuf plutôt qu'à recliquer dans une interface dont personne ne se
# souviendra six mois plus tard. Idempotent (PUT complet), sans aucune écriture en `--check`.
#
# ⚠ CE RÉGLAGE N'EST PAS EN PLACE AUJOURD'HUI, et ce n'est pas un oubli. La protection de branche
# n'existe pas sur un dépôt PRIVÉ d'un compte GitHub Free ; ni GitHub Pro ni le passage en public
# n'ont été retenus (décision utilisateur, 2026-08-14, docs/10 §8.8). Aucun garde-fou technique
# n'empêche donc de merger une PR au rouge — les six verdicts se lisent sur la PR, et le merge
# reste une décision humaine, comme la revue (docs/10 §6).
# Ce script est écrit sans être joué à dessein : il rend cette décision réversible en une commande
# le jour où le plan change, au lieu d'une enquête à refaire. `--check` répond 3 et dit laquelle
# des deux causes (plan, jeton) bloque.
#
# Usage :
#   bash scripts/github/protect-main.sh            # pose (ou repose) la protection
#   bash scripts/github/protect-main.sh --check    # diagnostic seul, aucune écriture
#
# Codes de retour : 0 = conforme (ou posé), 3 = non conforme / impossible à poser, 1 = pré-requis
# manquant. Le 3 est distinct pour qu'un appelant sache que le dépôt a répondu et que c'est le
# RÉGLAGE qui manque, pas l'outil.
#
# Détail, limites connues et procédure de reprise : docs/10-workflow-git.md §8.8.
set -euo pipefail

DEPOT="${MAESTRO_GITHUB_REPO:-automatemaestro-create/maestro}"
BRANCHE="${MAESTRO_GITHUB_BRANCHE:-main}"

# Les six jobs de `.github/workflows/ci.yml`, par leur NOM DE JOB — c'est sous ce nom que GitHub
# rapporte un check, et c'est ce nom qui est requis ici. Renommer un job dans le workflow sans le
# renommer ici rendrait toute PR non mergeable : le check requis ne serait plus jamais rapporté,
# et une PR qui attend un verdict qui n'arrivera pas ne se débloque par aucun clic.
CHECKS=(perimetre shellcheck python-lint pytest mypy web-build)

check_only=0
case "${1:-}" in
  --check) check_only=1 ;;
  "") ;;
  *) echo "Usage: $0 [--check]" >&2; exit 1 ;;
esac

if ! command -v gh >/dev/null 2>&1; then
  echo "gh n'est pas installé. Voir https://cli.github.com" >&2
  exit 1
fi

if ! gh auth status >/dev/null 2>&1; then
  echo "Non authentifié. Lancer d'abord : gh auth login" >&2
  echo "  (compte propre au projet — poser GH_CONFIG_DIR, voir docs/10 §7.4)" >&2
  exit 1
fi

# Explique un refus au lieu de le recracher. Les deux causes n'ont pas le même remède et ne se
# distinguent QUE par le message : GitHub rend 403 dans les deux cas.
expliquer_refus() {
  local corps="$1"
  if printf '%s' "$corps" | grep -q "Upgrade to GitHub Pro"; then
    cat >&2 <<'EOF'
  Cause : le PLAN du compte. La protection de branche — comme les rulesets — n'existe pas sur un
  dépôt PRIVÉ d'un compte GitHub Free : seulement sur un dépôt public, ou avec GitHub Pro / Team /
  Enterprise. Aucun jeton ne lève ça.
  Remèdes : souscrire GitHub Pro sur le compte propriétaire, ou passer le dépôt en public — la
  visibilité a été arbitrée « privé » par #335, c'est donc une décision et pas un réglage.
EOF
  elif printf '%s' "$corps" | grep -q "not accessible by personal access token"; then
    cat >&2 <<'EOF'
  Cause : le JETON. Le PAT fine-grained actif n'a pas la permission « Administration » sur ce
  dépôt (GitHub la nomme dans son en-tête de réponse : administration=read pour lire, write pour
  poser).
  Remède : github.com › Settings › Developer settings › Fine-grained tokens › le jeton du projet ›
  Repository permissions › Administration: Read and write. Le jeton vit dans le GH_CONFIG_DIR du
  projet (docs/10 §7.4) — jamais dans le dépôt, jamais en variable globale.
EOF
  else
    printf '  Réponse de GitHub : %s\n' "$corps" >&2
  fi
}

echo "Dépôt : $DEPOT — branche : $BRANCHE"
echo "Checks attendus : ${CHECKS[*]}"
echo

# État actuel. « Pas de protection », « protection illisible » et « protection en place » sont
# trois situations distinctes : seule la dernière s'inspecte, et la deuxième ne se devine pas.
lu=""
code=0
lu="$(gh api "repos/$DEPOT/branches/$BRANCHE/protection" \
        --jq '(.required_status_checks.contexts // [])[]' 2>&1)" || code=$?

if [ "$code" -ne 0 ]; then
  if printf '%s' "$lu" | grep -q "Branch not protected"; then
    echo "État : branche NON protégée."
  else
    echo "État : protection illisible." >&2
    expliquer_refus "$lu"
  fi
  if [ "$check_only" -eq 1 ]; then
    echo
    echo "Non conforme : la protection n'est pas en place."
    echo "Rejouer sans --check pour la poser."
    exit 3
  fi
else
  echo "État : branche protégée. Checks actuellement requis :"
  if [ -z "$lu" ]; then
    echo "  (aucun)"
  else
    printf '  - %s\n' $lu
  fi

  manquant=0
  for c in "${CHECKS[@]}"; do
    if ! printf '%s\n' $lu | grep -qx -- "$c"; then
      echo "  ⚠ check requis manquant : $c"
      manquant=1
    fi
  done

  if [ "$check_only" -eq 1 ]; then
    echo
    if [ "$manquant" -eq 1 ]; then
      echo "Non conforme. Rejouer sans --check pour reposer la protection."
      exit 3
    fi
    echo "Conforme."
    exit 0
  fi
fi

# Corps du PUT. L'API exige les quatre clés, y compris celles laissées nulles.
#
# `strict: false` — « require branches to be up to date before merging » n'est PAS activé, par
# fidélité à GitLab (`only_allow_merge_if_pipeline_succeeds` seul) et par cohérence avec le
# workflow : `strict: true` obligerait à ramener `main` dans chaque PR avant de merger, alors que
# le rattrapage d'une branche en retard est ici un geste jugé au cas par cas (/mr-fix, docs/10 §8.3).
#
# `enforce_admins: false` — ce n'est pas une porte de sortie de confort, c'est ce qui LAISSE VIVRE
# LE MIROIR : jusqu'à la bascule d'`origin` (#343), la branche `main` de ce dépôt est alimentée par
# le miroir push depuis GitLab, qui pousse directement dessus. Une branche protégée refuse les
# pushes directs — sauf aux administrateurs quand `enforce_admins` est faux, et le compte du miroir
# est le propriétaire. Le passer à `true` est un geste délibéré, à faire le jour où plus rien ne
# pousse sur `main` en dehors des merges de PR.
corps_json() {
  local contextes="" c
  for c in "${CHECKS[@]}"; do
    contextes="${contextes:+$contextes,}\"$c\""
  done
  cat <<EOF
{
  "required_status_checks": { "strict": false, "contexts": [$contextes] },
  "enforce_admins": false,
  "required_pull_request_reviews": null,
  "restrictions": null
}
EOF
}

echo
echo "Pose de la protection…"
reponse=""
if reponse="$(corps_json | gh api --method PUT \
                "repos/$DEPOT/branches/$BRANCHE/protection" --input - 2>&1)"; then
  echo "✓ Protection posée : ${#CHECKS[@]} checks requis sur $BRANCHE."
  echo "  Vérifier : bash scripts/github/protect-main.sh --check"
  exit 0
fi

echo "✗ Protection NON posée." >&2
expliquer_refus "$reponse"
exit 3

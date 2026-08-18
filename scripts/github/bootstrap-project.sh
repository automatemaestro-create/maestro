#!/usr/bin/env bash
# Monte le projet GitHub Projects v2 qui portera le CYCLE DE VIE des tickets, et son champ Status
# aux six valeurs du workflow Maestro (ticket #359, chantier #358).
#
# C'est le pendant de ce que `scripts/gitlab/bootstrap.sh` fait pour les labels, et la SOURCE
# UNIQUE du réglage : à rejouer sur un dépôt neuf plutôt qu'à recliquer dans une interface dont
# personne ne se souviendra six mois plus tard. Idempotent et non destructif ; aucune écriture en
# `--check`.
#
# ⚠ POURQUOI UN CHAMP PLUTÔT QUE DES LABELS. Le cycle de vie est porté depuis #207 par six labels
# scopés `workflow::*`, et ce n'était pas un choix : GitLab Free ayant perdu le champ Status natif
# à la fin de l'essai Ultimate, les labels étaient le seul mécanisme disponible. L'exclusion
# mutuelle des six est donc restée À NOTRE CHARGE — `set-workflow` ajoute la cible et retire les
# cinq autres dans le même appel, faute de quoi un ticket porte deux états. Un champ à valeur
# unique rend cette classe de bug impossible par construction. Ce chantier ne DÉFAIT pas #207 : il
# le remplace par ce qui manquait alors.
#
# ⚠ CE QUE CE SCRIPT DÉPLACE, ET QU'IL FAUT SAVOIR AVANT DE S'EN SERVIR. Le Status vit sur l'ITEM
# DE PROJET, pas sur l'issue. Un ticket absent du projet n'a donc AUCUN état, et aucune requête de
# cycle de vie ne le voit — l'équivalent exact du « 0 label workflow:: » d'aujourd'hui, en plus
# silencieux. Ce script ne touche à aucun ticket : le peuplement est #361, sa détection #363.
#
# Usage :
#   bash scripts/github/bootstrap-project.sh            # crée / met en conformité
#   bash scripts/github/bootstrap-project.sh --check    # diagnostic seul, aucune écriture
#   bash scripts/github/bootstrap-project.sh --force    # autorise la réécriture des options d'un
#                                                       # projet DÉJÀ PEUPLÉ (voir le garde-fou)
#
# Codes de retour : 0 = conforme (ou posé), 3 = non conforme / impossible à poser, 1 = pré-requis
# manquant. Le 3 est distinct pour qu'un appelant sache que GitHub a répondu et que c'est le
# RÉGLAGE qui manque, pas l'outil — même convention que `protect-main.sh`.
#
# Détail et procédure de reprise : docs/10-workflow-git.md §3.
#
# shellcheck disable=SC2016
# Les requêtes GraphQL de ce fichier portent LEURS PROPRES variables (`$proprietaire`, `$fieldId`,
# `$options`…) et sont donc en guillemets SIMPLES à dessein : c'est GitHub qui les substitue, pas le
# shell. SC2016 (« expressions don't expand in single quotes ») signale ici exactement le
# comportement recherché — sur chacune des six requêtes du fichier, d'où une déclaration unique en
# tête plutôt que six répétées.
set -euo pipefail

DEPOT="${MAESTRO_GITHUB_REPO:-automatemaestro-create/maestro}"

# Le titre du projet est une CLÉ : c'est par lui que ce script le retrouve pour être idempotent, et
# c'est par lui que `lib.sh` le résoudra (#360) — aucun ID de projet n'est jamais figé dans le
# dépôt, exactement comme aucun GID de label ne l'est aujourd'hui. Il est volontairement en ASCII
# pur : une clé qui traverse shell, GraphQL et awk sous Windows comme sous Linux n'a rien à gagner
# à porter un tiret cadratin (voir le mojibake de #141).
PROJET="${MAESTRO_PROJECT_TITRE:-Maestro}"

# Les six valeurs du cycle de vie, DANS L'ORDRE DU FLUX — c'est cet ordre qui fait les colonnes du
# tableau, donc il se lit de gauche à droite comme le travail avance. Format : libellé|couleur|description.
# Les libellés sont EXACTEMENT ceux que rend `lib.sh workflow-label` et les descriptions celles des
# six labels d'aujourd'hui : le vocabulaire du cycle de vie ne change pas de support en changeant de
# support. Les couleurs reprennent celles des labels (gris/bleu/orange/vert/rouge), Doublon passant
# en rose pour ne pas être indiscernable d'Abandonné une fois en colonnes.
ETATS=(
  "À faire|GRAY|Pas encore commencé"
  "En cours|BLUE|Quelqu'un travaille dessus"
  "En revue|ORANGE|PR ouverte, en attente de relecture et de merge"
  "Terminé|GREEN|Livré et mergé"
  "Abandonné|RED|Ne sera pas réalisé (won't do)"
  "Doublon|PINK|Déjà couvert par un autre ticket"
)

check_only=0
force=0
for arg in "$@"; do
  case "$arg" in
    --check) check_only=1 ;;
    --force) force=1 ;;
    *) echo "Usage: $0 [--check] [--force]" >&2; exit 1 ;;
  esac
done

if ! command -v gh >/dev/null 2>&1; then
  echo "gh n'est pas installé. Voir https://cli.github.com" >&2
  exit 1
fi

if ! gh auth status >/dev/null 2>&1; then
  echo "Non authentifié. Lancer d'abord : gh auth login" >&2
  echo "  (compte propre au projet — poser GH_CONFIG_DIR, voir docs/10 §7.4)" >&2
  exit 1
fi

# ── Sortie ───────────────────────────────────────────────────────────────────────────────────────
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
info() { printf '  \033[90m·\033[0m %s\n' "$1"; }
warn() { printf '  \033[33m⚠\033[0m %s\n' "$1"; }
err()  { printf '  \033[31m✗\033[0m %s\n' "$1" >&2; }

# ── Appel GraphQL ────────────────────────────────────────────────────────────────────────────────
# Le corps de la requête voyage par l'ENTRÉE STANDARD et jamais sur la ligne de commande : la couche
# permissions découpe un appel sur ses sauts de ligne, si bien qu'une requête multi-ligne passée en
# `-f query=…` est refusée alors même que `gh api graphql` est autorisé (docs/10 §11.7). C'est le
# même motif que `--body-file` pour les descriptions.
#
# ⚠ `-F` et non `-f` : seul le premier interprète le `@` de `@-` comme « lis l'entrée standard ».
# Avec `-f`, gh envoie la chaîne littérale « @- » comme requête, et GitHub répond une erreur de
# syntaxe GraphQL qui ne dit rien de la cause (« actual: DIR_SIGN (@) »).
#
# ⚠ L'erreur voyage par un FICHIER et non par une variable : les appelants invoquent `gql` dans une
# substitution de commande, donc dans un SOUS-SHELL, d'où aucune affectation ne remonte. Une
# variable d'erreur y serait toujours vide au moment de l'expliquer — c'est-à-dire précisément quand
# on en a besoin.
GQL_ERR_FICHIER="$(mktemp)"
BROUILLON=""
trap 'rm -f "$GQL_ERR_FICHIER" "$BROUILLON"' EXIT

gql() {
  local requete="$1"; shift
  local sortie rc
  : > "$GQL_ERR_FICHIER"
  set +e
  sortie="$(printf '%s' "$requete" | gh api graphql -F query=@- "$@" 2>"$GQL_ERR_FICHIER")"
  rc=$?
  set -e
  if [ "$rc" -ne 0 ]; then
    # gh met son message sur stderr et le corps JSON des erreurs sur stdout : les deux comptent.
    printf '%s\n' "$sortie" >> "$GQL_ERR_FICHIER"
    return 1
  fi
  printf '%s' "$sortie"
}

gql_erreur() { cat "$GQL_ERR_FICHIER" 2>/dev/null; }

# Explique un refus au lieu de le recracher. C'est le seul message de ce script qui compte
# vraiment : sans la permission, rien de ce qui suit n'est possible, et la cause est invisible
# depuis `gh auth status`, qui n'imprime AUCUN scope pour un jeton fine-grained.
expliquer_refus() {
  case "$(gql_erreur)" in
    *"not accessible by personal access token"*|*FORBIDDEN*|*"Resource not accessible"*)
      err "GitHub refuse l'écriture Projects v2 avec ce jeton."
      echo "" >&2
      echo "  Le compte du projet s'authentifie par un jeton FINE-GRAINED, dont les permissions" >&2
      echo "  ne s'accordent qu'une par une — et « Projects » n'est pas donnée par « repo »." >&2
      echo "" >&2
      echo "  Geste à faire, une fois, par une personne :" >&2
      echo "    1. https://github.com/settings/personal-access-tokens — ouvrir le jeton du compte" >&2
      echo "       « $(gh api user -q .login 2>/dev/null || echo '<compte du projet>') »" >&2
      echo "    2. section « Account permissions » (PAS « Repository permissions ») :" >&2
      echo "       → « Projects » : Read-only  ⟶  Read and write" >&2
      echo "    3. enregistrer, puis rejouer ce script." >&2
      echo "" >&2
      echo "  ⚠ Le piège : « Repository permissions » porte AUSSI une entrée « Projects », qui ne" >&2
      echo "    gouverne que les projets rattachés à un dépôt. Un projet Projects v2 appartient au" >&2
      echo "    COMPTE, donc c'est la permission de compte qui décide. Accorder la mauvaise des" >&2
      echo "    deux laisse l'erreur strictement identique." >&2
      echo "" >&2
      echo "  ⚠ La LECTURE, elle, passe déjà : un diagnostic qui ne lit que le projet ne verra" >&2
      echo "    jamais ce blocage. C'est une écriture réelle qui le révèle, et c'est pourquoi ce" >&2
      echo "    script en tente une." >&2
      ;;
    *)
      err "Appel GraphQL en échec :"
      gql_erreur | head -5 >&2
      ;;
  esac
}

echo ""
echo "Projet GitHub Projects v2 — cycle de vie des tickets"
echo ""

# ── 1. Propriétaire et dépôt ─────────────────────────────────────────────────────────────────────
proprietaire="${DEPOT%%/*}"
nom_depot="${DEPOT##*/}"

reponse="$(gql 'query($proprietaire: String!, $nom: String!) {
  repositoryOwner(login: $proprietaire) { id }
  repository(owner: $proprietaire, name: $nom) { id }
}' -f proprietaire="$proprietaire" -f nom="$nom_depot" --jq '[.data.repositoryOwner.id, .data.repository.id] | @tsv')" || {
  err "Dépôt « $DEPOT » illisible."
  gql_erreur | head -3 >&2
  exit 1
}

owner_id="${reponse%%$'\t'*}"
repo_id="${reponse##*$'\t'}"
[ -n "$owner_id" ] && [ -n "$repo_id" ] || { err "Identifiants du dépôt introuvables."; exit 1; }
info "dépôt $DEPOT résolu"

# ── 2. Le projet existe-t-il déjà ? ──────────────────────────────────────────────────────────────
# Recherche PAR TITRE parmi les projets du compte : c'est ce qui rend le script rejouable sans rien
# créer en double, et ce qui évite d'avoir à mémoriser un numéro de projet quelque part.
projet_id=""
projet_num=""
liste="$(gql 'query($proprietaire: String!) {
  repositoryOwner(login: $proprietaire) {
    ... on ProjectV2Owner { projectsV2(first: 100) { nodes { id number title } } }
  }
}' -f proprietaire="$proprietaire" --jq '.data.repositoryOwner.projectsV2.nodes[]? | [.id, (.number|tostring), .title] | @tsv')" || {
  expliquer_refus
  exit 3
}

while IFS=$'\t' read -r pid pnum ptitre; do
  [ -n "$pid" ] || continue
  if [ "$ptitre" = "$PROJET" ]; then projet_id="$pid"; projet_num="$pnum"; fi
done <<EOF
$liste
EOF

# ── 3. Créer si absent ───────────────────────────────────────────────────────────────────────────
if [ -z "$projet_id" ]; then
  if [ "$check_only" -eq 1 ]; then
    warn "projet « $PROJET » absent — serait créé"
    echo ""
    echo "Résumé"
    echo "  non conforme : le projet n'existe pas. Rejouer sans --check pour le poser."
    exit 3
  fi

  reponse="$(gql 'mutation($ownerId: ID!, $titre: String!) {
    createProjectV2(input: {ownerId: $ownerId, title: $titre}) { projectV2 { id number } }
  }' -f ownerId="$owner_id" -f titre="$PROJET" --jq '[.data.createProjectV2.projectV2.id, (.data.createProjectV2.projectV2.number|tostring)] | @tsv')" || {
    expliquer_refus
    exit 3
  }
  projet_id="${reponse%%$'\t'*}"
  projet_num="${reponse##*$'\t'}"
  ok "projet « $PROJET » créé (#$projet_num)"

  # Rattachement au dépôt : c'est ce qui le fait apparaître sous l'onglet Projects du dépôt, et ce
  # qui permet d'y ajouter une issue sans la chercher dans tout le compte. Non bloquant — le projet
  # fonctionne sans, seul son accès est moins direct.
  if gql 'mutation($projectId: ID!, $repositoryId: ID!) {
    linkProjectV2ToRepository(input: {projectId: $projectId, repositoryId: $repositoryId}) { repository { id } }
  }' -f projectId="$projet_id" -f repositoryId="$repo_id" --jq '.data' >/dev/null; then
    ok "projet rattaché au dépôt"
  else
    warn "rattachement au dépôt impossible (le projet reste utilisable) : $(gql_erreur | head -1)"
  fi

  gql 'mutation($projectId: ID!, $desc: String!) {
    updateProjectV2(input: {projectId: $projectId, shortDescription: $desc}) { projectV2 { id } }
  }' -f projectId="$projet_id" -f desc="Cycle de vie des tickets Maestro — le champ Status fait autorité (chantier #358)." --jq '.data' >/dev/null || true
else
  ok "projet « $PROJET » présent (#$projet_num)"
fi

# ── 4. Le champ Status et ses options ────────────────────────────────────────────────────────────
# Un projet neuf porte un champ « Status » à trois options (Todo / In Progress / Done). On ne le
# recrée pas : on le REMPLIT. Une option par défaut laissée en place serait un septième état que
# rien ne gouverne — et que `set-workflow` ne saurait jamais poser ni retirer.
champ="$(gql 'query($projectId: ID!) {
  node(id: $projectId) {
    ... on ProjectV2 {
      items(first: 1) { totalCount }
      field(name: "Status") {
        ... on ProjectV2SingleSelectField { id options { name color description } }
      }
    }
  }
}' -f projectId="$projet_id" --jq '[.data.node.field.id // "", (.data.node.items.totalCount|tostring), ([.data.node.field.options[]? | .name + "|" + .color + "|" + .description] | join("¤"))] | @tsv')" || {
  expliquer_refus
  exit 3
}

champ_id="$(printf '%s' "$champ" | cut -f1)"
nb_items="$(printf '%s' "$champ" | cut -f2)"
options_actuelles="$(printf '%s' "$champ" | cut -f3)"

if [ -z "$champ_id" ]; then
  err "le projet #$projet_num n'a pas de champ « Status »."
  echo "  Un champ Status ne se crée pas par l'API sur un projet existant : le recréer demande de" >&2
  echo "  repartir d'un projet neuf. Renommer « $PROJET » et rejouer ce script." >&2
  exit 3
fi

# Cible attendue, dans l'ordre du flux.
attendu=""
for etat in "${ETATS[@]}"; do
  [ -n "$attendu" ] && attendu="$attendu¤"
  attendu="$attendu$etat"
done

if [ "$options_actuelles" = "$attendu" ]; then
  ok "champ Status conforme — six valeurs, dans l'ordre du flux"
  echo ""
  echo "Résumé"
  echo "  conforme — projet « $PROJET » (#$projet_num), champ Status aux six valeurs du cycle de vie."
  echo "  https://github.com/users/$proprietaire/projects/$projet_num"
  exit 0
fi

# Ce qui partirait. `updateProjectV2Field` REMPLACE la liste des options : celles qui n'y figurent
# pas sont supprimées, et avec elles l'état des items qui les portaient. C'est la seule opération
# destructrice de ce script, d'où le garde-fou ci-dessous.
info "champ Status à mettre en conformité"
while IFS='|' read -r nom _ _; do
  [ -n "$nom" ] || continue
  case "$attendu" in
    *"$nom|"*) ;;
    *) warn "option « $nom » sera retirée" ;;
  esac
done <<EOF
$(printf '%s' "$options_actuelles" | tr '¤' '\n')
EOF

if [ "$check_only" -eq 1 ]; then
  echo ""
  echo "Résumé"
  echo "  non conforme : le champ Status ne porte pas les six valeurs attendues."
  echo "  Rejouer sans --check pour le mettre en conformité."
  exit 3
fi

# GARDE-FOU. Réécrire les options d'un projet DÉJÀ PEUPLÉ efface l'état des items qui portaient une
# option retirée — c'est-à-dire, à ce stade du chantier, le cycle de vie de tickets réels. Sur un
# projet neuf (aucun item) la question ne se pose pas. La borne est volontairement grossière : elle
# ne coûte qu'un `totalCount`, là où « cette option est-elle utilisée ? » demanderait de paginer
# tous les items — le travail de #362, pas celui de ce lot.
if [ "$nb_items" != "0" ] && [ "$force" -eq 0 ]; then
  err "le projet porte $nb_items item(s) : réécrire les options effacerait leur état."
  echo "  Relancer avec --force si c'est bien ce qui est voulu, après avoir vérifié qu'aucun" >&2
  echo "  ticket ne porte une option sur le point d'être retirée." >&2
  exit 3
fi

# Construction du tableau d'options pour la mutation. Il voyage par un FICHIER de variables JSON :
# `gh api graphql -f` ne sait passer que des scalaires, et une liste d'objets ne s'écrit pas en -f.
options_json=""
for etat in "${ETATS[@]}"; do
  nom="${etat%%|*}"; reste="${etat#*|}"
  couleur="${reste%%|*}"; description="${reste#*|}"
  [ -n "$options_json" ] && options_json="$options_json,"
  # Échappement JSON minimal : ces six valeurs sont écrites juste au-dessus, sans guillemet ni
  # antislash. Un `printf %s` suffit donc, et c'est vérifiable à l'œil — pas un pari sur une entrée
  # arbitraire, qui appellerait un encodeur.
  options_json="$options_json{\"name\":\"$nom\",\"color\":\"$couleur\",\"description\":\"$description\"}"
done

BROUILLON="$(mktemp)"
# Le brouillon d'un appel n'est lu par personne : il reste dans le répertoire temporaire, comme le
# veut la règle du dépôt sur ce qui va sous `.maestro/` et ce qui n'y va pas.
cat > "$BROUILLON" <<JSON
{"query":"mutation(\$fieldId: ID!, \$options: [ProjectV2SingleSelectFieldOptionInput!]!) { updateProjectV2Field(input: {fieldId: \$fieldId, singleSelectOptions: \$options}) { projectV2Field { ... on ProjectV2SingleSelectField { id options { name } } } } }","variables":{"fieldId":"$champ_id","options":[$options_json]}}
JSON

: > "$GQL_ERR_FICHIER"
set +e
sortie="$(gh api graphql --input "$BROUILLON" 2>"$GQL_ERR_FICHIER")"
rc=$?
set -e
if [ "$rc" -ne 0 ]; then
  printf '%s\n' "$sortie" >> "$GQL_ERR_FICHIER"
  expliquer_refus
  exit 3
fi

ok "champ Status : six valeurs posées, dans l'ordre du flux"
for etat in "${ETATS[@]}"; do
  info "  ${etat%%|*}"
done

echo ""
echo "Résumé"
echo "  posé — projet « $PROJET » (#$projet_num), champ Status aux six valeurs du cycle de vie."
echo "  https://github.com/users/$proprietaire/projects/$projet_num"
echo ""
echo "  Le projet est VIDE : aucun ticket n'y est encore, donc aucun ticket n'a d'état."
echo "  Le peuplement (ajout à la création + backfill des existants) est #361."
exit 0

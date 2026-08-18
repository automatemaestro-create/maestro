#!/usr/bin/env bash
# Lecture de l'ARCHIVE GitLab — les seules primitives `glab` qui restent dans le dépôt (#344).
#
# Pourquoi ce fichier existe. Le lot 9 de #335 a retiré de `scripts/gitlab/lib.sh` toute la branche
# GitLab : les verbes du workflow répondent contre GitHub, et plus rien de l'outillage quotidien ne
# parle à `glab`. Restaient deux scripts qui, par définition, ne peuvent parler qu'à GitLab —
# `export-gitlab.sh` (le backlog exporté, lot 2) et `inventaire.sh` (les prérequis d'import, lot 1).
# Les supprimer aurait rendu la migration NON REJOUABLE, et le lot 10 (#345) prévoit précisément de
# les couvrir. Ils gardent donc leurs appels, et ceux-ci sont regroupés ICI plutôt que laissés dans
# `lib.sh` : c'est ce qui rend l'exception lisible d'un `grep -rn glab scripts/` — deux fichiers de
# migration et leur bibliothèque, tous nommés « gitlab », et rien d'autre.
#
# CE QU'IL NE FAIT PAS, jamais : écrire. Le projet GitLab est archivé en lecture seule depuis la
# bascule (#343) ; une écriture y échouerait, et personne n'a de raison d'essayer.
#
# ⚠ `glab` N'EST PLUS UN PRÉREQUIS de la mise en route (`setup.sh` ne l'installe plus). Qui rejoue
# un de ces deux scripts l'installe et l'authentifie à la main — c'est un geste de mainteneur, une
# fois, et `mig_require_glab` le dit avec la commande.
#
# ⚠ `origin` POINTE SUR GITHUB : `glab` déduit le projet des remotes et répondrait « None of the git
# remotes configured point to a known GitLab host ». D'où le `--repo` explicite posé par
# `mig_glab_api` sur chaque appel — c'est lui qui rend ces scripts rejouables depuis un clone
# basculé, et l'oublier est le seul moyen de les casser en silence.
#
# Sourçable uniquement (aucune action à l'exécution), à côté de lib.sh dont les deux scripts
# gardent les helpers de TEXTE (gl_json_string_field…), qui ne parlent à aucune forge.

# Le projet GitLab d'origine. Surchargeable pour rejouer l'export sur un autre projet.
MIG_GL_PROJECT="${MIG_GL_PROJECT:-${GL_PROJECT:-maestro-group4345327/maestro}}"

# Retry des lectures GraphQL : l'endpoint de GitLab rend parfois une réponse vide (hoquet réseau,
# rate-limit). Mêmes réglages que ceux que lib.sh appliquait — le comportement ne change pas, seul
# le domicile du code.
MIG_GQL_RETRIES="${MIG_GQL_RETRIES:-3}"
MIG_GQL_RETRY_DELAY="${MIG_GQL_RETRY_DELAY:-1}"

# mig_require_glab -> `glab` installé ET authentifié, sinon message et code 1.
mig_require_glab() {
  if ! command -v glab >/dev/null 2>&1; then
    echo "glab n'est pas installé — requis pour relire l'archive GitLab (docs/27 §11)." >&2
    echo "  Installation : https://gitlab.com/gitlab-org/cli — ce n'est plus un prérequis du dépôt (#344)." >&2
    return 1
  fi
  if ! glab auth status >/dev/null 2>&1; then
    echo "glab non authentifié. Lancer d'abord : glab auth login --hostname gitlab.com" >&2
    return 1
  fi
}

# mig_project_enc -> chemin du projet URL-encodé pour l'API REST (« groupe%2Fprojet »).
mig_project_enc() {
  printf '%s\n' "$MIG_GL_PROJECT" | sed 's,/,%2F,g'
}

# mig_glab_api <chemin-rest> -> la réponse brute de l'API REST du projet archivé.
mig_glab_api() {
  glab api --repo "$MIG_GL_PROJECT" "$1" 2>/dev/null
}

# mig_graphql_read <query> -> exécute une LECTURE GraphQL et imprime la réponse JSON brute.
# Réessaie tant que la réponse revient VIDE, jamais autrement : une réponse non vide — même
# porteuse d'erreurs applicatives GraphQL — est rendue telle quelle, l'appelant reste responsable
# de son parsing.
# ⚠ Réservé aux LECTURES. Ne jamais envelopper une mutation : un retry pourrait la ré-appliquer.
mig_graphql_read() {
  local query="$1"
  if [ -z "$query" ]; then echo "mig_graphql_read : requête manquante" >&2; return 2; fi
  local attempt=1 out
  while :; do
    out="$(glab api --repo "$MIG_GL_PROJECT" graphql -f query="$query" 2>/dev/null)"
    if [ -n "$out" ]; then printf '%s\n' "$out"; return 0; fi
    if [ "$attempt" -ge "$MIG_GQL_RETRIES" ]; then
      echo "mig_graphql_read : réponse vide de l'API GraphQL après $attempt tentative(s)" >&2
      return 1
    fi
    sleep "$MIG_GQL_RETRY_DELAY"
    attempt=$((attempt + 1))
  done
}

# mig_description <iid> -> la description d'un ticket ARCHIVÉ, par le chemin REST, octets intacts.
# Sert la vérification d'encodage de l'export : comparer ce que GraphQL a rendu à ce que REST rend
# pour le même ticket. C'était `gl_get_description` avant #344 — qui lit désormais GITHUB, donc un
# autre ticket, donc une comparaison qui aurait dit n'importe quoi sans jamais échouer.
mig_description() {
  local iid="$1"
  if [ -z "$iid" ]; then echo "mig_description : iid manquant" >&2; return 2; fi
  mig_glab_api "projects/$(mig_project_enc)/issues/$iid" | gl_json_string_field description
}

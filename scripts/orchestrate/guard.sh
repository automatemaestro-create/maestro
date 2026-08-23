#!/usr/bin/env bash
# Garde-fou en dur des sessions Claude Code autonomes (#169, parent #167).
#
# Branché en hook `PreToolUse` par scripts/orchestrate/settings.run.json, il refuse les gestes
# irréversibles ou réservés à un humain — QUEL QUE SOIT LE MODE DE PERMISSION de la session. C'est
# ce qui distingue ce filet de la couche `permissions` : celle-ci tombe avec
# `--dangerously-skip-permissions`, le hook non. Une boucle qui tourne la nuit n'a personne pour
# rattraper un `gh pr merge` parti tout seul.
#
# LES MOTIFS `glab …` SONT PARTIS AVEC LA BRANCHE GITLAB (#344). Ils avaient été gardés à côté des
# motifs `gh …` tant que les deux forges étaient joignables depuis le même poste ; ce n'est plus le
# cas, et un garde-fou qui protège une cible qui n'existe plus n'est qu'un motif de plus à relire.
# Ancien contexte (#341, chantier #335) : entre le lot 4 (qui posait MAESTRO_FORGE) et la bascule
# (lot 8),
# les deux outils sont installés sur les mêmes postes et une session peut appeler l'un ou l'autre.
# Un garde-fou qui suivrait la forge active se laisserait contourner par la variable qu'il est censé
# ne pas croire — et ce fichier n'est justement PAS censé faire confiance à l'environnement de la
# session qu'il surveille. Le prix est nul : refuser `gh pr merge` sur un poste encore sur GitLab
# n'empêche aucun geste légitime, personne n'ayant de raison de merger une PR à la main ici.
#
#   bash scripts/orchestrate/guard.sh                  # mode hook : JSON PreToolUse sur stdin
#   bash scripts/orchestrate/guard.sh --test "<cmd>"   # verdict sur une commande, sans hook
#   bash scripts/orchestrate/guard.sh --check          # settings.run.json ne dérive pas du dépôt
#
# Contrat du hook (Claude Code) : le JSON de l'appel arrive sur stdin (`tool_name`, `tool_input`) ;
# SORTIE 0 = laisser passer, SORTIE 2 = bloquer l'appel, stderr étant rendu au modèle comme motif.
# On s'en tient à ce code de retour plutôt qu'à une décision structurée en JSON : il est documenté,
# stable d'une version à l'autre, et rend le garde-fou testable depuis un simple shell (--test).
#
# Ce qui est refusé, et pourquoi (docs/10-workflow-git.md §6, CLAUDE.md « Garde-fous ») :
#   - force-push : réécrit un historique déjà poussé, que d'autres ont pu reprendre ;
#   - `gh pr merge` : AUCUN MERGE NON VÉRIFIÉ (#417, chantier #413). Depuis que le merge n'attend
#     plus un humain, l'interdit n'est plus « ne jamais merger » — c'est le geste NU qui reste
#     impossible, le geste vérifié passant par `bash scripts/gitlab/lib.sh merge-mr <iid>`, qui
#     éprouve ses quatre prérequis (PR ouverte non brouillon fermant le ticket, rien de non poussé,
#     aucun conflit avec origin/main, pipeline vert sur la tête de la PR). Ce filet-ci juge le TEXTE
#     de la commande lancée par la session, jamais ce qu'un script appelle en interne : laisser le
#     motif en place ne barre donc aucun appelant légitime, et lever le `deny` mettrait un merge au
#     rouge à un `gh` près pour zéro gain ;
#   - `lib.sh merge-mr` ET `lib.sh pipeline-wait` : le merge appartient AU PILOTE (#419, parent
#     #413), et c'est le seul refus de ce fichier qui ne vaille QUE pour un run autonome. Depuis
#     #418, `/ticket-ship` merge le ticket qu'il clôt — juste en session interactive, faux ici pour
#     deux raisons qu'aucune bonne volonté de prompt ne corrige : une session qui attend un pipeline
#     brûle du quota à ne rien faire, en tenant un worktree et un créneau de concurrence ; et à N
#     tickets en vol, N sessions qui mergent en parallèle périment mutuellement leur verdict de
#     conflit, alors que la règle est « un merge à la fois, verdict recalculé après chacun ». Le
#     pilote, lui, sérialise et ne consomme aucun quota. La clôture d'une session s'arrête donc à la
#     PR ouverte + « En revue » : le pilote reprend de là ;
#   - `gh pr close` : fermer une PR reste une décision humaine — rien ne l'a levée ;
#   - `gh run delete` : détruit des runs dont d'autres lisent le verdict ;
#   - `git reset --hard` : jette du travail non commité, sans rattrapage ;
#   - `git commit --no-verify` : contourne le hook `commit-msg`, donc la convention de commit ;
#   - tout commit sur `main` : la règle Git n°1 du dépôt (un ticket = une branche).
#
# Limites assumées. Le contrôle porte sur le TEXTE de la commande : une commande construite
# dynamiquement (variable, `eval`, script tiers) lui échappe. Ce n'est pas un bac à sable, c'est un
# filet contre le geste accidentel — le vrai confinement, c'est le worktree et le passage obligé
# par `merge-mr`, qui vérifie. De même, le contrôle « commit sur main » lit la branche du répertoire
# du hook : un `git -C <ailleurs> commit` n'est pas attrapé.

set -uo pipefail

RACINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SETTINGS_RUN="$RACINE/scripts/orchestrate/settings.run.json"
SETTINGS_DEPOT="$RACINE/.claude/settings.json"

usage() {
  cat <<'USAGE'
Garde-fou en dur des sessions Claude Code autonomes (hook PreToolUse).

  bash scripts/orchestrate/guard.sh                  Mode hook : JSON PreToolUse sur stdin.
                                                     Sortie 0 = laisser passer, 2 = bloquer.
  bash scripts/orchestrate/guard.sh --test "<cmd>"   Verdict sur une commande, sans hook.
  bash scripts/orchestrate/guard.sh --check          Vérifie que settings.run.json conserve tous
                                                     les `deny` de .claude/settings.json.
  bash scripts/orchestrate/guard.sh -h | --help      Cette aide.
USAGE
}

# --- Le jugement, en un seul endroit ---------------------------------------------------------------
# refus <commande> -> imprime le motif et renvoie 0 si la commande doit être BLOQUÉE, 1 sinon.
# Les motifs sont des ERE volontairement larges (« quelque part dans la ligne ») : une commande
# chaînée (`… && git push --force`) doit être attrapée comme une commande nue.
refus() {
  local cmd="$1"

  # `-f` et `-n` sont des options d'une lettre : les borner, sans quoi « -force » ou un nom de
  # fichier « -n.txt » déclencheraient un refus incompréhensible.
  local opt_f='(^|[[:space:]])-f([[:space:]]|$)'
  local opt_n='(^|[[:space:]])-n([[:space:]]|$)'

  if printf '%s' "$cmd" | grep -qE 'git[[:space:]]+push'; then
    if printf '%s' "$cmd" | grep -qE -- '--force-with-lease|--force' || printf '%s' "$cmd" | grep -qE "$opt_f"; then
      printf "force-push interdit : il réécrit un historique déjà poussé (docs/10 §6). Pousse sans --force, ou repars d'une branche à jour."
      return 0
    fi
  fi

  # Deux gestes, deux motifs — et depuis #413 ils n'ont plus la même raison, d'où la séparation du
  # motif unique qui les couvrait. Un motif de refus est lu par un modèle : lui dire que le merge
  # n'a jamais lieu, alors que le dépôt merge désormais tout seul, l'envoie chercher un
  # contournement au lieu du chemin prévu.
  if printf '%s' "$cmd" | grep -qE 'gh[[:space:]]+pr[[:space:]]+merge'; then
    printf 'aucun merge NON VÉRIFIÉ (CLAUDE.md, Garde-fous) : ce qui est interdit est le merge nu, pas le merge. Passe par « bash scripts/gitlab/lib.sh merge-mr <iid> », qui éprouve les prérequis avant de merger — PR ouverte, non brouillon, fermant le ticket ; rien de non poussé ; aucun conflit avec origin/main ; pipeline vert sur la tête de la PR.'
    return 0
  fi

  # Le merge d'un run appartient au pilote (#419). Le motif vise le VERBE de lib.sh, pas `gh` : ce
  # qui doit être barré ici est exactement le chemin que #417 a rendu légitime ailleurs, et le
  # message doit le dire — un refus qui laisserait croire que le merge est interdit enverrait la
  # session chercher un contournement, alors qu'elle n'a simplement plus rien à faire.
  if printf '%s' "$cmd" | grep -qE 'lib\.sh[[:space:]]+merge-mr'; then
    printf 'dans un run autonome, le merge appartient au PILOTE (#419) : il sérialise les merges et n'\''attend aucun pipeline sur ton quota. Ta clôture s'\''arrête à la PR ouverte + cycle de vie « En revue » — c'\''est le verdict que le run attend de toi. Ne merge pas, ne réessaie pas.'
    return 0
  fi
  if printf '%s' "$cmd" | grep -qE 'lib\.sh[[:space:]]+pipeline-wait'; then
    printf 'n'\''attends aucun pipeline dans un run autonome (#419) : c'\''est du quota brûlé à ne rien faire, et c'\''est le pilote qui attend, hors session. Laisse la PR ouverte et le ticket « En revue » : il la mergera dès qu'\''elle sera verte.'
    return 0
  fi

  if printf '%s' "$cmd" | grep -qE 'gh[[:space:]]+pr[[:space:]]+close'; then
    printf 'fermer une PR reste une décision humaine — aucune commande ne la ferme à ta place (CLAUDE.md, Garde-fous). Pour un travail abandonné, « /ticket-abandon <iid> » clôt le ticket et laisse la PR à un humain.'
    return 0
  fi

  # `gh run cancel`, lui, ne détruit rien et reste autorisé : il n'efface aucun verdict, il en
  # abrège un qui n'a plus d'objet.
  if printf '%s' "$cmd" | grep -qE 'gh[[:space:]]+run[[:space:]]+delete'; then
    printf 'suppression de run Actions interdite : d'\''autres lisent son verdict. Au plus « gh run rerun ».'
    return 0
  fi

  if printf '%s' "$cmd" | grep -qE 'git[[:space:]]+reset[[:space:]]+.*--hard'; then
    printf '« git reset --hard » jette le travail non commité sans rattrapage. Utilise « git restore » sur les fichiers visés, ou committe d'\''abord.'
    return 0
  fi

  if printf '%s' "$cmd" | grep -qE 'git[[:space:]]+commit'; then
    if printf '%s' "$cmd" | grep -qE -- '--no-verify' || printf '%s' "$cmd" | grep -qE "$opt_n"; then
      printf '« --no-verify » contourne le hook commit-msg, donc la convention de commit. Corrige le message plutôt que de le contourner.'
      return 0
    fi
    local branche
    branche="$(git -C "${CLAUDE_PROJECT_DIR:-$RACINE}" rev-parse --abbrev-ref HEAD 2>/dev/null)"
    case "$branche" in
      main | master)
        printf 'jamais de commit sur « %s » : un ticket = une branche (CLAUDE.md, Règles Git). Démarre le ticket avec /ticket-start <iid>.' "$branche"
        return 0
        ;;
    esac
  fi

  return 1
}

# --- Mode --check : le fichier de run ne dérive pas du dépôt ----------------------------------------
# Le bloc `deny` de settings.run.json est une COPIE de celui du dépôt (voir son $comment). Une copie
# se périme : ce contrôle est ce qui rend la dérive visible au lieu de silencieuse — un interdit
# ajouté à .claude/settings.json et oublié ici ne protégerait plus les sessions autonomes.
deny_de() { # <fichier json> -> une règle par ligne, triées
  awk '/"deny"[[:space:]]*:/ { dans = 1 } dans { print; if (/\]/) exit }' "$1" |
    grep -o '"[^"]*"' | tr -d '"' | grep -v '^deny$' | sort -u
}

mode_check() {
  local manquants regle_manquante code=0
  for f in "$SETTINGS_DEPOT" "$SETTINGS_RUN"; do
    [ -f "$f" ] || { printf 'guard.sh --check : fichier introuvable — %s\n' "$f" >&2; return 1; }
  done
  manquants="$(comm -23 <(deny_de "$SETTINGS_DEPOT") <(deny_de "$SETTINGS_RUN"))"
  if [ -n "$manquants" ]; then
    printf 'guard.sh --check : ✗ règles « deny » du dépôt absentes de settings.run.json :\n' >&2
    # Une règle par ligne, jamais `printf … $manquants` : une règle porte des espaces
    # (« Bash(gh pr merge:*) ») et le découpage de mots la rendrait en trois morceaux.
    while IFS= read -r regle_manquante; do
      printf '  %s\n' "$regle_manquante" >&2
    done <<EOF
$manquants
EOF
    printf 'Les recopier dans scripts/orchestrate/settings.run.json.\n' >&2
    code=1
  else
    printf 'guard.sh --check : ✓ settings.run.json conserve les %s règle(s) « deny » du dépôt.\n' \
      "$(deny_de "$SETTINGS_DEPOT" | wc -l | tr -d ' ')"
  fi

  # Les interdits du hook et ceux de la couche permissions doivent raconter la même histoire : une
  # règle `deny` que le hook laisserait passer donnerait une fausse sécurité dès qu'un run tourne
  # en mode permissif. On rejoue donc chaque règle du dépôt contre `refus`.
  local regle cmd
  while IFS= read -r regle; do
    case "$regle" in
      "Bash("*) cmd="${regle#Bash(}"; cmd="${cmd%)}"; cmd="${cmd%:\*}" ;;
      *) continue ;;
    esac
    if ! refus "$cmd" >/dev/null; then
      printf 'guard.sh --check : ✗ « %s » est refusé par les permissions mais PAS par le hook.\n' "$cmd" >&2
      code=1
    fi
  done < <(deny_de "$SETTINGS_DEPOT")
  [ "$code" -eq 0 ] && printf 'guard.sh --check : ✓ le hook refuse aussi chacune de ces règles.\n'
  return "$code"
}

# --- Modes ------------------------------------------------------------------------------------------
case "${1:-}" in
  -h | --help)
    usage
    exit 0
    ;;
  --check)
    mode_check
    exit $?
    ;;
  --test)
    motif="$(refus "${2:-}")" && { printf 'REFUSÉ — %s\n' "$motif"; exit 2; }
    printf 'AUTORISÉ\n'
    exit 0
    ;;
  "") ;;
  *)
    printf 'Option inconnue : %s\n\n' "$1" >&2
    usage >&2
    exit 2
    ;;
esac

# --- Mode hook ---------------------------------------------------------------------------------------
# Le JSON arrive sur stdin. On l'aplatit en une ligne : rien n'impose au client de l'envoyer compact,
# et un motif à cheval sur deux lignes échapperait à grep.
charge="$(tr '\n' ' ')"

# Seuls les appels Bash sont jugés. Les autres outils passent sans examen — et surtout, la recherche
# de motifs ne DOIT PAS courir sur un Edit/Write : ce dépôt documente « gh pr merge » et
# « git push --force » dans docs/10 et CLAUDE.md, et écrire ces pages se ferait refuser.
outil="$(printf '%s' "$charge" | grep -o '"tool_name"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1 | sed 's/.*"\([^"]*\)"$/\1/')"
[ "$outil" = "Bash" ] || exit 0

# Pour un appel Bash, la charge ne porte que la commande, sa description et le contexte de session :
# y chercher les motifs directement évite d'extraire un champ JSON à la main — un découpage qui
# raterait une commande contenant des guillemets, donc qui laisserait passer au lieu de bloquer.
if motif="$(refus "$charge")"; then
  printf 'Garde-fou d'\''orchestration : appel refusé. %s\n' "$motif" >&2
  exit 2
fi
exit 0

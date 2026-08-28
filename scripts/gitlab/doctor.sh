#!/usr/bin/env bash
# Bilan de santé (LECTURE SEULE) du setup de forge Maestro + détection de dérive.
# N'écrit jamais rien (ni état, ni label, ni PR) — voir docs/10-workflow-git.md.
# Réutilise scripts/gitlab/lib.sh (cycle de vie par nom de label, pas de GID en dur).
#
# Usage :  bash scripts/gitlab/doctor.sh [--strict]
#   --strict : code de sortie non nul aussi en présence d'avertissements (utile en CI).
# Code de sortie : 1 si un contrôle DUR échoue (auth, labels de catégorisation, labels de cycle
#   de vie) ; sinon 0
#   (ou 1 avec --strict s'il reste des avertissements de dérive).
#
# --- Aucune lecture en direct (#341) --------------------------------------------------------------
# Ce fichier n'appelle jamais le CLI de la forge : TOUTES ses lectures passent par les verbes de
# lib.sh. La raison n'est pas l'élégance, c'est le SILENCE. Les contrôles 4a/4b/4c cherchaient
# « "iid":" » dans le JSON brut du backlog — une clé que GitHub n'écrit pas (il rend « "number": »)
# —, si bien qu'ils n'échouaient pas après la bascule : ils rendaient « aucune dérive ». Un ✓ sur une
# question jamais posée, dans le seul fichier du dépôt dont le métier est de détecter les dérives.
# D'où la projection TSV (`backlog-table`, `workflow-derives`) plutôt qu'un grep sur le JSON : le
# contrat de lib.sh porte sur des COLONNES, pas sur la forme d'une réponse d'API.
#
# Deux sections ont disparu avec l'outillage GitLab (#344) — le board Kanban (§3) et les runners de
# projet (§7) : la première n'a pas d'équivalent (le projet n'utilise pas Projects v2, le suivi
# maison du lot 4 ayant remplacé le seul usage qu'on en aurait eu), la seconde n'a plus d'objet, les
# runners étant hébergés par la forge. La numérotation des sections restantes n'a pas été resserrée :
# elle est citée dans docs/10 et dans les tests, et la faire glisser pour combler deux trous coûterait
# plus qu'elle ne rapporte.
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/gitlab/lib.sh
. "$here/lib.sh"

strict=0
[ "${1:-}" = "--strict" ] && strict=1

FORGE_NOM="$(gl_forge_nom)"
FORGE_CLI="$(gl_forge_cli)"
DEPOT="$(gl_depot_courant)"

if [ -t 1 ]; then
  C_G=$'\033[32m'; C_Y=$'\033[33m'; C_R=$'\033[31m'; C_B=$'\033[1m'; C_0=$'\033[0m'
else
  C_G=''; C_Y=''; C_R=''; C_B=''; C_0=''
fi

errors=0
warns=0
ok()      { printf '  %s✓%s %s\n' "$C_G" "$C_0" "$1"; }
warn()    { printf '  %s⚠%s %s\n' "$C_Y" "$C_0" "$1"; warns=$((warns + 1)); }
err()     { printf '  %s✗%s %s\n' "$C_R" "$C_0" "$1"; errors=$((errors + 1)); }
# Ni ✓ ni ⚠ : « ce contrôle ne s'applique pas ici ». N'incrémente aucun compteur, donc n'entre pas
# dans le verdict de --strict — un contrôle sans objet n'est pas une dérive à corriger, et le
# compter comme telle rendrait le bilan durablement jaune sur GitHub, ce qui apprend à ne plus le lire.
info()    { printf '  %s·%s %s\n' "$C_B" "$C_0" "$1"; }
section() { printf '\n%s%s%s\n' "$C_B" "$1" "$C_0"; }

# --- 1. Prérequis -------------------------------------------------------------------------------
section "1. Prérequis"
if gl_require 2>/dev/null; then
  user="$(gl_current_user 2>/dev/null)"
  ok "$FORGE_CLI installé et authentifié (${user:-?}) — $FORGE_NOM, dépôt $DEPOT"
else
  err "$FORGE_CLI absent ou non authentifié — lancer : $FORGE_CLI auth login"
  section "Résumé"
  printf '  Bilan interrompu : authentification requise.\n'
  exit 1
fi

# --- 2. Labels de catégorisation ----------------------------------------------------------------
section "2. Labels de catégorisation (§3.2)"
PROVISIONNER="bash scripts/gitlab/bootstrap.sh"
existing_labels="$(gl_labels 2>/dev/null)"
expected_labels="type::feature type::bug type::doc type::infra \
agent::orchestrateur agent::dev agent::bdd agent::devops agent::design agent::qa \
prio::haute prio::moyenne prio::basse"
missing=""
for l in $expected_labels; do
  printf '%s\n' "$existing_labels" | grep -qx "$l" || missing="$missing $l"
done
if [ -z "$missing" ]; then
  ok "familles type::/agent::/prio:: complètes (13 labels)"
else
  err "labels manquants :$missing → relancer : $PROVISIONNER"
fi

# --- 3. Cycle de vie : les six valeurs sont-elles là ? --------------------------------------------
# Pendant exact du contrôle des six labels `workflow::*` que #365 vient de retirer, sur l'objet qui
# les remplace : les six OPTIONS du champ Status. Les valeurs attendues sont dérivées des slugs par
# `gl_workflow_label` et jamais recopiées — le vocabulaire ne change pas en changeant de support
# (c'est aussi ce que pose bootstrap-project.sh), donc une liste écrite ici serait une copie de plus
# à tenir d'accord avec les autres.
#
# Les TROIS dérives du champ, parce qu'elles appellent trois gestes différents : une valeur qu'on ne
# pourra jamais poser, un septième état que rien ne gouverne, des colonnes hors de l'ordre du flux.
# La version minimale de #365 ne couvrait que la première ; celle-ci la remplace, comme son en-tête
# l'avait prévu.
#
# LA LECTURE EST CELLE DE `pj_resoudre` (#361), PAS UNE SECONDE. Ce lot avait écrit son propre verbe
# (`st_options`) — même requête, même comparaison du titre en égalité — les deux ayant été menés en
# parallèle ; le doublon est tombé à la fusion. `PJ_OPTIONS` rend « <id><TAB><libellé> » DANS L'ORDRE
# DU CHAMP, et c'est cet ordre qui fait les colonnes du projet : il se lit de gauche à droite comme
# le travail avance, donc il est une donnée et non un détail d'affichage.
section "3. Cycle de vie (champ Status du projet « $GL_PROJET_TITRE »)"
PROVISIONNER_PROJET="bash scripts/github/bootstrap-project.sh"
# Lu ici pour §4c, qui ne peut rien contrôler d'un projet illisible — et le dirait à tort « sans
# dérive ». Un contrôle sans objet n'est pas un contrôle vert.
PROJET_LISIBLE=0
# Les six valeurs attendues, DANS L'ORDRE DU FLUX, dérivées des slugs par `gl_workflow_label` et
# jamais recopiées : le vocabulaire du cycle de vie ne change pas en changeant de support (c'est
# aussi ce que pose bootstrap-project.sh). Une liste écrite ici serait une copie de plus à tenir
# d'accord avec les autres.
SLUGS="a-faire en-cours en-revue termine abandonne doublon"
if ! pj_resoudre 2>/dev/null; then
  err "champ « Status » du projet « $GL_PROJET_TITRE » illisible → relancer : $PROVISIONNER_PROJET"
else
  PROJET_LISIBLE=1
  attendu="$(for s in $SLUGS; do gl_workflow_label "$s"; done)"
  options="$(printf '%s\n' "$PJ_OPTIONS" | cut -f2)"

  manquantes=""
  for s in $SLUGS; do
    libelle="$(gl_workflow_label "$s")"
    printf '%s\n' "$options" | grep -qxF "$libelle" \
      || manquantes="${manquantes:+$manquantes, }« $libelle »"
  done
  en_trop=""
  while IFS= read -r o; do
    [ -z "$o" ] && continue
    printf '%s\n' "$attendu" | grep -qxF "$o" || en_trop="${en_trop:+$en_trop, }« $o »"
  done <<EOF2
$options
EOF2

  if [ -n "$manquantes" ]; then
    err "option(s) manquante(s) du champ Status : $manquantes — set-workflow ne pourra jamais les poser → relancer : $PROVISIONNER_PROJET"
  fi
  if [ -n "$en_trop" ]; then
    # Le cas nominal est l'option par défaut d'un projet neuf (Todo / In Progress / Done) qu'une mise
    # en conformité n'a pas remplacée : un septième état que `set-workflow` ne sait ni poser ni
    # retirer, et qu'aucune lecture de cycle de vie ne reconnaîtra.
    warn "option(s) en trop dans le champ Status : $en_trop — état(s) que rien ne gouverne → $PROVISIONNER_PROJET --check"
    printf '    → la réécriture des options d'\''un projet DÉJÀ PEUPLÉ efface l'\''état des items qui les portent : elle demande --force\n'
  fi
  if [ -z "$manquantes" ] && [ -z "$en_trop" ] && [ "$options" != "$attendu" ]; then
    warn "les six options du champ Status ne sont pas dans l'ordre du flux (c'est l'ordre des colonnes du projet) → relancer : $PROVISIONNER_PROJET"
  fi
  if [ -z "$manquantes" ] && [ -z "$en_trop" ] && [ "$options" = "$attendu" ]; then
    ok "6 options du champ Status résolues par nom (1 appel), dans l'ordre du flux — set-workflow opérationnel (aucun ID en dur)"
  fi
fi

# --- 4. Dérive cycle de vie ↔ réalité -----------------------------------------------------------
section "4. Dérive cycle de vie ↔ réalité"

# Les deux backlogs, lus UNE fois chacun : les contrôles 4a et 4b ci-dessous s'en servent et
# gl_backlog_table n'a pas de cache — une lecture par contrôle multiplierait les allers-retours d'un
# bilan qui en fait déjà beaucoup. 4d, lui, délègue tout à `reconcile-en-cours`, qui refait sa propre
# lecture : le verbe existe pour être appelé seul, et le rendre dépendant d'un backlog déjà en main
# l'aurait rendu inutilisable partout ailleurs.
#
# ⚠ LA TABLE TSV, PAS LE JSON BRUT (#341). Ces deux lectures étaient des `gl_backlog` grepés sur
# « "iid":" » — la forme de la réponse GitLab, pas le contrat de lib.sh. Après la bascule, le grep
# ne matchait plus rien (GitHub rend « "number": ») et les trois contrôles répondaient
# « aucune dérive » : le bilan restait vert, sur des questions qu'il ne posait plus. La colonne
# `statut` de `backlog-table` porte le LIBELLÉ du cycle de vie des deux côtés — c'est ce que le
# contrat garantit, et c'est donc sur lui qu'on branche.
backlog_opened="$(gl_backlog_table opened)" || backlog_opened=""
backlog_closed="$(gl_backlog_table closed)" || backlog_closed=""

# helper local : iid des tickets d'une table déjà lue portant un cycle de vie donné (colonne 2).
iids_with_workflow() { # $1=table TSV  $2=libellé de cycle de vie
  printf '%s\n' "$1" | awk -F'\t' -v cible="$2" '$1 !~ /^#/ && $2 == cible { print $1 }'
}

# 4a. Tickets « En revue » ouverts : une PR ouverte est-elle rattachée ?
revue_iids="$(iids_with_workflow "$backlog_opened" "En revue")"
open_mr_branches="$(gl_open_mr_branches 2>/dev/null)"
if [ -z "$revue_iids" ]; then
  ok "aucun ticket « En revue » en attente"
else
  for iid in $revue_iids; do
    if printf '%s\n' "$open_mr_branches" | grep -q "/$iid-"; then
      ok "#$iid « En revue » ↔ PR ouverte"
    else
      warn "#$iid « En revue » sans PR ouverte rattachée (état resté après merge/close ?)"
    fi
  done
fi

# 4b. Tickets fermés dont le cycle de vie est resté « actif »
stuck_iids="$(printf '%s\n' "$backlog_closed" \
  | awk -F'\t' '$1 !~ /^#/ && ($2 == "À faire" || $2 == "En cours" || $2 == "En revue") { print $1 }')"
if [ -z "$stuck_iids" ]; then
  ok "aucun ticket fermé à l'état encore actif"
else
  for iid in $stuck_iids; do
    warn "#$iid est fermé mais son état est encore « actif » (attendu : Terminé/Abandonné/Doublon)"
  done
  # Cette dérive-là a désormais sa réparation (#275) : on la nomme au lieu de laisser chercher — une
  # seule fois, pas par ticket. Le diagnostic reste en LECTURE SEULE : doctor.sh ne répare rien de
  # lui-même, c'est sa promesse, et le verbe est à appeler explicitement.
  printf '    → tous réparables d'\''un coup : bash scripts/gitlab/lib.sh reconcile-workflow  (--check pour la liste)\n'
fi

# 4c. L'invariant « exactement un état par ticket ouvert ».
# LA DÉRIVE A PERDU UNE MOITIÉ AVEC LES LABELS (#365), ET C'EST LE GAIN DU CHANTIER #358. Tant que
# le cycle de vie était porté par six labels scopés, l'exclusion mutuelle était à notre charge — le
# « :: » n'est que cosmétique — et un ticket pouvait en porter DEUX, les lectures en rendant alors
# un au hasard : le plus pernicieux des cas, puisque rien ne dépassait à l'affichage. Un champ à
# valeur unique rend ce « ≥ 2 » impossible par construction.
#
# Reste le « 0 », qui n'a pas disparu mais CHANGÉ DE FORME : l'état vit sur l'ITEM DE PROJET et non
# sur l'issue, donc un ticket sans état est soit hors du projet, soit un item à colonne vide. Dans
# les deux cas il sort de tous les comptes — `queue.sh` ne le verra pas, `/backlog` le rendra « - ».
# DISTINGUER les deux causes, qui appellent deux gestes différents, est le lot #363.
#
# Le COMPTAGE est le seul contrôle de cette section que la table plate ne peut pas porter : elle rend
# un statut, pas une cause — projeter la dérive l'effacerait. Il est donc délégué à `st_derives`, qui
# refait une lecture et répond à la source.
#
# ET LA QUESTION A CHANGÉ AVEC LE SUPPORT, pas seulement la source. Le « ≥ 2 » est devenu impossible
# par construction — c'est le gain du chantier #358 — mais l'état vit sur l'ITEM DE PROJET : il reste
# le « 0 », qui se scinde en DEUX causes appelant deux gestes différents (ajouter le ticket au
# projet, ou lui poser un état). D'où un verbe à part, dont la seconde colonne est une CAUSE là où
# celle de `gl_workflow_derives` était un nombre : les fondre sous un « 0 » commun rendrait le
# diagnostic vrai et inutilisable.
if [ "$PROJET_LISIBLE" = 0 ]; then
  # Sans projet lisible, TOUS les tickets ressortiraient « hors projet » : ce serait une seule cause
  # rendue N fois, et elle est déjà dite en §3. Un contrôle sans objet n'est pas une dérive.
  info "projet « $GL_PROJET_TITRE » illisible (§3) — contrôle des tickets sans état sans objet"
elif ! st_brut="$(st_derives 2>&1)"; then
  warn "dérives du champ Status illisibles : $st_brut"
else
  # La borne d'abord : `st_derives` rend en tête « #examines <examinés> <ouverts> ». Une borne
  # atteinte laisse des tickets NON CONTRÔLÉS, donc un ✓ y serait un ✓ sur une question posée à
  # moitié — exactement le défaut qu'a corrigé #341, et dans le fichier qui l'a payé.
  st_examines="$(printf '%s\n' "$st_brut" | awk -F'\t' '$1 == "#examines" { print $2; exit }')"
  st_total="$(printf '%s\n' "$st_brut" | awk -F'\t' '$1 == "#examines" { print $3; exit }')"
  if [ -n "$st_total" ] && [ "$st_examines" != "$st_total" ]; then
    warn "seuls $st_examines des $st_total tickets ouverts ont été examinés (borne first:100) — le reste n'est pas contrôlé"
  fi

  st_sans_etat="$(printf '%s\n' "$st_brut" | awk -F'\t' '$1 !~ /^#/ && $1 != "" { print }')"
  if [ -z "$st_sans_etat" ]; then
    ok "tous les tickets ouverts sont dans le projet « $GL_PROJET_TITRE » et portent un Status"
  else
    # ⚠ LA RÉPARATION EST NOMMÉE PAR TICKET, ET C'EST VOULU. Le backfill en masse est parti avec les
    # labels (#365) : il dérivait le Status du label courant et de rien d'autre, si bien qu'un verbe
    # d'ensemble poserait aujourd'hui un état PAR DÉFAUT sur des tickets anciens — il inventerait la
    # donnée qu'on cherche justement. `project-add` prend l'état en argument : c'est un geste, pas un
    # balayage, et ce fichier ne fait de toute façon que le nommer.
    while IFS=$'\t' read -r iid cause; do
      [ -z "$iid" ] && continue
      case "$cause" in
        hors-projet)
          warn "#$iid ouvert hors du projet « $GL_PROJET_TITRE » — aucun état, et rien ne l'en distingue d'un ticket filtré → l'y ajouter : bash scripts/gitlab/lib.sh project-add $iid \"<état>\"" ;;
        sans-etat)
          warn "#$iid est dans le projet mais son Status est vide — un état que personne n'a voulu → poser : bash scripts/gitlab/lib.sh set-workflow $iid \"<état>\"" ;;
        *)
          warn "#$iid : cause de dérive inconnue « $cause »" ;;
      esac
    done <<EOF2
$st_sans_etat
EOF2
  fi
fi

# 4d. Tickets « En cours » dont plus personne ne s'occupe (#328).
# La quatrième dérive du cycle de vie, et la seule qui ne se voie nulle part : un ticket entre en
# « En cours » à /ticket-start et n'en sort que par une clôture ou un abandon — une session morte
# (délai, pilote tué, console fermée, session interactive laissée en plan) l'y laisse pour toujours.
# « En cours » ET assigné étant exactement ce que `queue.sh` écarte, plus rien ne le ramène jamais
# dans le champ de vision : la règle d'anti-collision qui protège le travail vivant cache le travail
# mort. Le diagnostic est DÉLÉGUÉ au verbe (`lib.sh reconcile-en-cours`), déjà en lecture seule de
# bout en bout et seul à savoir départager un vivant d'un orphelin ; ce fichier ne fait que le nommer
# comme dérive, sans le réparer — c'est sa promesse.
# ⚠ Portée : les worktrees de CETTE machine, comme le ramassage et la purge — même borne qu'en 4b.
# Le verbe est appelé en SOUS-PROCESSUS (et non par sa fonction, pourtant sourcée avec lib.sh) pour
# la même raison que la boucle ci-dessous n'est pas un pipeline : ce qu'il imprime doit revenir dans
# une variable, et `warn` doit incrémenter le compteur du shell principal, sans quoi `--strict`
# rendrait 0 sur une dérive qu'il vient d'afficher.
en_cours_orphelins="$(bash "$here/lib.sh" reconcile-en-cours --auto 2>/dev/null | grep -F ' orphelin — ')"
if [ -z "$en_cours_orphelins" ]; then
  ok "aucun ticket « En cours » abandonné par sa session (worktrees de cette machine)"
else
  while IFS= read -r ligne; do
    [ -z "$ligne" ] && continue
    warn "${ligne#*⚠ } — plus personne dessus"
  done <<EOF
$en_cours_orphelins
EOF
  printf '    → le détail, verdict par verdict : bash scripts/gitlab/lib.sh reconcile-en-cours\n'
  # La réparation existe (#329) mais reste un GESTE : ce bilan la NOMME, il ne la déclenche pas —
  # même partage que la dérive 4b, où `reconcile-workflow` est proposé et jamais joué d'office. Ici
  # la raison est plus forte encore : « orphelin » est une déduction, et reprendre le ticket d'une
  # session vivante coûterait bien plus cher que de laisser un orphelin un jour de plus.
  printf '    → le reprendre (« À faire » + libéré, worktree intact) : bash scripts/gitlab/lib.sh reprendre-en-cours <iid>\n'
fi

# --- 5. Ménage des branches locales -------------------------------------------------------------
section "5. Ménage des branches locales"
if git rev-parse --git-dir >/dev/null 2>&1; then
  cleanup_found=0
  while IFS= read -r b; do
    [ -z "$b" ] && continue
    # `gl_mr_state`, et non une lecture directe : c'est le MÊME verbe que celui sur lequel s'appuie
    # la suppression (`cleanup-merged`, docs/10 §6). Diagnostiquer avec une autre source que celle
    # qui décide, c'est signaler des branches que la purge refusera — ou taire celles qu'elle prendra.
    st="$(gl_mr_state "$b" 2>/dev/null)"
    if [ "$st" = merged ]; then
      warn "branche locale « $b » : PR mergée → à nettoyer avec /branch-cleanup"
      cleanup_found=1
    fi
  done <<EOF
$(git branch --format='%(refname:short)' | grep -v '^main$')
EOF
  [ "$cleanup_found" = 0 ] && ok "aucune branche locale mergée en attente de nettoyage"
else
  warn "hors dépôt git — contrôle des branches locales ignoré"
fi

# --- 6. Garde-fous de merge du dépôt ---------------------------------------------------------------
# Dérive si le dépôt n'exige plus un pipeline vert pour merger, ou ne supprime plus la branche source
# au merge (docs/10-workflow-git.md §6). Les trois promesses sont lues par `gl_merge_settings`, qui
# les rend NORMALISÉES (true|false|-) : elles vivent dans la protection de branche de `main` et
# dans `delete_branch_on_merge`.
#
# ⚠ L'ABSENCE DE PROTECTION A CHANGÉ DE SENS LE 2026-08-28 (#734). Tant que le dépôt était privé sur
# un compte Free, elle était une DÉCISION documentée (docs/10 §8.8, 2026-08-14) : la protection
# n'existait pas sur ce plan, `protect-main.sh` attendait écrit-mais-non-joué, et la rendre en ⚠
# aurait fait de ce bilan un fichier durablement jaune sur un point que personne ne comptait
# corriger — `--strict` aurait échoué en CI pour dire une chose qu'on savait déjà.
#
# Le dépôt est public et la protection est POSÉE. Son absence n'est donc plus une décision mais une
# DÉRIVE : quelqu'un l'a retirée, ou un dépôt neuf ne l'a pas encore reçue. Elle se répare en une
# commande, ce qui est exactement le critère qui sépare ici un `info` d'un `warn` — et un `--strict`
# qui échoue dessus dit quelque chose qu'on ne sait PAS déjà.
section "6. Garde-fous de merge du dépôt"
declare -A REGLAGE=()
while IFS=$'\t' read -r cle valeur; do
  [ -n "$cle" ] && REGLAGE["$cle"]="$valeur"
done <<EOF
$(gl_merge_settings 2>/dev/null)
EOF

if [ "${#REGLAGE[@]}" = 0 ]; then
  warn "réglages du dépôt illisibles (API muette) — contrôle ignoré"
else
  case "${REGLAGE[pipeline_requis]:--}" in
    true)  ok "protection de branche sur main : les checks CI sont requis — aucun merge au rouge" ;;
    false) warn "aucune protection de branche sur main : un merge au rouge redevient possible hors de nos chemins"
           printf '    → posée le 2026-08-28 (#734, docs/10 §8.8) — son absence est une dérive, plus une décision\n'
           printf '    → « lib.sh merge-mr » tient toujours la règle POUR LES SESSIONS ; ce qui manque est\n'
           printf '      ce qui la tenait pour un clic dans l'"'"'interface web (docs/10 §6, #417)\n'
           printf '    → réparer : bash scripts/github/protect-main.sh\n' ;;
    *)     warn "protection de branche de main illisible — contrôle ignoré" ;;
  esac
  case "${REGLAGE[suppression_branche]:--}" in
    true)  ok "delete_branch_on_merge=true — la branche source est supprimée au merge" ;;
    false) warn "delete_branch_on_merge ≠ true : les branches distantes s'accumuleraient après merge"
           printf '    → gh api -X PATCH repos/%s -F delete_branch_on_merge=true\n' "$DEPOT"
           printf '    → aucun script ne le pose : ni bootstrap.sh (labels seuls) ni /ticket-finish (docs/10 §6)\n' ;;
    # « - » n'est PAS « false » : le champ n'est présent que si le jeton a le droit d'administration
    # du dépôt (mesuré le 2026-08-17 sur le PAT du projet). L'absence parle du jeton, pas du dépôt.
    *)     info "delete_branch_on_merge illisible (jeton sans droit d'administration du dépôt) — contrôle ignoré" ;;
  esac
fi

# --- 8. Milestones de phase -----------------------------------------------------------------------
# Dérives autour du milestone de phase (docs/10-workflow-git.md §3.4) : un jalon actif ENTIÈREMENT
# SOLDÉ qui attend son BOUCLAGE ; un ticket OUVERT sans milestone (l'outillage pose la phase courante
# à la création — lib.sh current-milestone).
section "8. Milestones de phase"

# 8a. LE BOUCLAGE (#758, chantier #756).
# ⚠ Ce contrôle nommait jusqu'ici la MAUVAISE action. Il disait « à fermer » — la décision finale —
# en sautant le geste qui doit la précéder : démontrer le livrable sur pièces et rendre un verdict.
# C'était le seul endroit du dépôt qui parlait d'un jalon soldé, et c'est la deuxième raison pour
# laquelle le bouclage a disparu (14 jalons fermés sans bilan, constat de #756) : personne n'était
# convoqué. Il dit désormais « à boucler, puis à fermer », et se tait sur un jalon déjà bouclé.
#
# LA RÈGLE N'EST PLUS RECOPIÉE ICI. « Actif ET entièrement soldé » vivait dans l'awk ci-dessous, en
# TROISIÈME exemplaire ; elle vit maintenant dans `milestones-a-boucler`, qui pose en outre la
# question que ce contrôle ne savait pas poser — le verdict est-il consigné ? Le verbe est MUET quand
# il n'y a rien : le ✓ ci-dessous est donc le nôtre, pas le sien.
#
# RIEN N'EST LANCÉ NI ÉCRIT : la commande de bouclage est IMPRIMÉE, jamais jouée — doctor.sh est en
# lecture seule (en-tête de ce fichier), et le verdict reste un jugement humain (#562, #612, #714).
a_boucler="$(gl_milestones_a_boucler 2>/dev/null)"
case $? in
  0)
    while IFS=$'\t' read -r ms_titre ms_rail ms_criteres ms_fermes ms_total; do
      case "$ms_titre" in ''|'#'*) continue ;; esac
      warn "milestone « $ms_titre » actif et entièrement soldé ($ms_fermes/$ms_total, rail $ms_rail) → à boucler, puis à fermer"
      printf '    → /milestone-bilan "%s"  (démontre le livrable sur pièces et rend un verdict)\n' "$ms_titre"
      # Un jalon sans critères de sortie se boucle quand même — l'absence est un manque à combler AU
      # bouclage (#757), jamais une raison de ne pas convoquer. Mais elle se dit : sans critères,
      # « le livrable est-il à la hauteur ? » n'a rien contre quoi se mesurer.
      [ "$ms_criteres" = non ] && printf '    → aucun critère de sortie consigné : les poser d'\''abord (lib.sh milestone-criteres "%s" <fichier>)\n' "$ms_titre"
    done <<EOF
$a_boucler
EOF
    ;;
  3) ok "aucun jalon actif à boucler (soldé et sans verdict consigné)" ;;
  *) warn "jalons illisibles (API muette) — contrôle du bouclage ignoré" ;;
esac

# 8b. LA PHASE COURANTE — celle que /ticket-create pose sur les nouveaux tickets.
courant="$(gl_current_milestone 2>/dev/null)"
if [ -n "$courant" ]; then
  ok "phase courante : « $courant » (milestone posé par /ticket-create sur les nouveaux tickets)"
else
  warn "aucun milestone utilisable sur le rail produit (tous soldés ou vides, #619) — /ticket-create créera les prochains tickets sans milestone"
fi

if ! nomiles="$(gl_issues_sans_milestone 2>/dev/null)"; then
  warn "tickets ouverts illisibles (API muette) — contrôle des milestones manquants ignoré"
elif [ -z "$nomiles" ]; then
  ok "tous les tickets ouverts portent un milestone"
else
  poser_milestone='gh issue edit %s --repo '"$DEPOT"' --milestone "<titre>"'
  for iid in $nomiles; do
    # shellcheck disable=SC2059  # le gabarit est choisi juste au-dessus, jamais une donnée lue
    warn "#$iid ouvert sans milestone → poser celui de sa phase : $(printf "$poser_milestone" "$iid")"
  done
fi

# --- Résumé -------------------------------------------------------------------------------------
section "Résumé"
printf '  %d erreur(s), %d avertissement(s)\n' "$errors" "$warns"
if [ "$errors" -gt 0 ]; then exit 1; fi
if [ "$strict" = 1 ] && [ "$warns" -gt 0 ]; then exit 1; fi
exit 0

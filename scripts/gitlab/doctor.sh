#!/usr/bin/env bash
# Bilan de santé (LECTURE SEULE) du setup de forge Maestro + détection de dérive.
# N'écrit jamais rien (ni état, ni label, ni MR) — voir docs/10-workflow-git.md.
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
# projet (§7) : la première n'avait alors pas d'équivalent, la seconde n'a plus d'objet, les runners
# étant hébergés par la forge. La numérotation des sections restantes n'a pas été resserrée : elle
# est citée dans docs/10 et dans les tests, et la faire glisser pour combler deux trous coûterait
# plus qu'elle ne rapporte.
#
# --- Deux backends de cycle de vie, deux jeux de dérives (#363, chantier #358) ---------------------
# Le cycle de vie se lit dans les six labels `workflow::*` ou dans le champ Status d'un projet
# Projects v2, selon `MAESTRO_CYCLE` (défaut `labels`, #360). Les DÉRIVES à traquer ne sont pas les
# mêmes des deux côtés, et ce n'est pas une variante d'affichage :
#   • en `labels`, l'exclusion mutuelle est à notre charge, d'où « 0 ou ≥ 2 labels » (§4c) ;
#   • en `status`, un champ à valeur unique rend le « ≥ 2 » IMPOSSIBLE par construction — c'est le
#     gain du chantier —, mais l'état vit sur l'ITEM DE PROJET et non sur l'issue : un ticket hors
#     projet n'a aucun état, et rien à l'écran ne le distingue d'un ticket filtré. Deux dérives
#     nouvelles (§4c), plus une sur le champ lui-même (§3), pendant exact du contrôle des six labels.
# Les sections 3 et 4c branchent donc sur le backend, lu UNE fois (`gl_cycle`) ; le reste du bilan
# est commun. En mode `labels`, la sortie de ce fichier est inchangée — sections, ordre et verdicts.
#
# ⚠ Ce qui NE branche pas encore : §4a et §4b passent par `gl_backlog_table`, dont le backend Status
# est le lot #362. En `status`, ces deux contrôles lisent donc des labels que plus personne ne met à
# jour, et RETARDENT — c'est la conséquence annoncée du découpage (docs/10 §3.5), pas un oubli d'ici.
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
# Le backend, lu UNE FOIS pour les deux sections qui en dépendent (3 et 4c) : les relire séparément
# laisserait la porte à un bilan qui contrôle les labels dans l'une et le champ dans l'autre.
# Sur une valeur inconnue on ne DEVINE pas — c'est la règle de `gl_vers_status` (#360, leçon de
# #339) : la section le dit et s'arrête, plutôt que de rendre un verdict sur un backend au hasard.
CYCLE="$(gl_cycle 2>/dev/null)" || CYCLE=""
PROJET_LISIBLE=0

# Les six valeurs attendues, DANS L'ORDRE DU FLUX, dérivées des slugs par `gl_workflow_label` et
# jamais recopiées : le vocabulaire du cycle de vie ne change pas en changeant de support (c'est
# aussi ce que pose bootstrap-project.sh), donc les deux backends se contrôlent contre la MÊME
# source. Une liste écrite ici serait une septième copie à tenir d'accord avec les six autres.
SLUGS="a-faire en-cours en-revue termine abandonne doublon"
# Pendant de PROVISIONNER pour l'autre support : le champ Status et ses options se posent par
# bootstrap-project.sh, jamais par bootstrap.sh (qui ne connaît que les labels).
PROVISIONNER_PROJET="bash scripts/github/bootstrap-project.sh"

if [ -z "$CYCLE" ]; then
  section "3. Cycle de vie (backend indéterminé)"
  err "MAESTRO_CYCLE=« ${MAESTRO_CYCLE-} » inconnu — attendu : labels | status (défaut labels)"

elif [ "$CYCLE" = status ]; then
  # Pendant exact du contrôle des six labels, sur un autre objet : les six options du champ
  # Status. Trois dérives distinctes, parce qu'elles appellent trois lectures différentes du
  # même écran — une valeur qu'on ne pourra jamais poser, un septième état que rien ne gouverne,
  # des colonnes dans le désordre. La lecture est déléguée à `lib.sh` (st_options), qui nomme
  # lui-même les trois causes d'échec : compte illisible, projet absent, champ absent.
  section "3. Cycle de vie (champ Status du projet « $GL_PROJET_TITRE »)"
  if ! options="$(st_options 2>&1)"; then
    err "$options"
  else
    PROJET_LISIBLE=1
    attendu=""
    for s in $SLUGS; do attendu="${attendu}$(gl_workflow_label "$s")"$'\n'; done
    attendu="${attendu%$'\n'}"

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
    done <<EOF
$options
EOF

    if [ -n "$manquantes" ]; then
      err "option(s) manquante(s) du champ Status : $manquantes — set-workflow ne pourra jamais les poser → relancer : $PROVISIONNER_PROJET"
    fi
    if [ -n "$en_trop" ]; then
      # Le cas nominal est l'option par défaut d'un projet neuf (Todo / In Progress / Done) qu'une
      # mise en conformité n'a pas remplacée : un septième état que `set-workflow` ne sait ni poser
      # ni retirer, et qu'aucune lecture de cycle de vie ne reconnaîtra.
      warn "option(s) en trop dans le champ Status : $en_trop — état(s) que rien ne gouverne → $PROVISIONNER_PROJET --check"
      printf '    → la réécriture des options d'\''un projet DÉJÀ PEUPLÉ efface l'\''état des items qui les portent : elle demande --force\n'
    fi
    if [ -z "$manquantes" ] && [ -z "$en_trop" ] && [ "$options" != "$attendu" ]; then
      # Les six y sont et rien d'autre, mais pas dans cet ordre : l'ordre du champ EST celui des
      # colonnes du projet, donc il se lit de gauche à droite comme le travail avance.
      warn "les six options du champ Status ne sont pas dans l'ordre du flux (c'est l'ordre des colonnes du projet) → relancer : $PROVISIONNER_PROJET"
    fi
    if [ -z "$manquantes" ] && [ -z "$en_trop" ] && [ "$options" = "$attendu" ]; then
      ok "6 options du champ Status résolues par nom (1 appel), dans l'ordre du flux — set-workflow opérationnel (aucun ID en dur)"
    fi
  fi

else
  # Depuis #209 le cycle de vie n'est plus le champ Status natif (lifecycle custom « Maestro »,
  # Premium, disparu avec l'essai Ultimate) mais des labels scopés `workflow::*` — voir le contrat de
  # surface en tête de lib.sh. Une SEULE lecture (gl_workflow_gids, avec retry) pour les six, comme
  # la section le faisait pour les six statuts : on évite le faux « incohérent » que six appels
  # indépendants déclenchaient dès qu'un seul retombait vide.
  section "3. Cycle de vie (labels $GL_WORKFLOW_SCOPE::*)"
  workflow_gids="$(gl_workflow_gids 2>/dev/null)"
  if [ -z "$workflow_gids" ]; then
    err "aucun label « $GL_WORKFLOW_SCOPE::* » lisible dans $DEPOT → relancer : $PROVISIONNER"
  else
    missing_workflow=""
    for s in $SLUGS; do
      printf '%s\n' "$workflow_gids" | cut -f1 | grep -qx "$s" \
        || missing_workflow="${missing_workflow:+$missing_workflow, }$GL_WORKFLOW_SCOPE::$s"
    done
    if [ -z "$missing_workflow" ]; then
      ok "6 labels de cycle de vie résolus par nom (1 appel) — set-workflow opérationnel (aucun GID en dur)"
    else
      err "label(s) de cycle de vie manquant(s) : $missing_workflow → relancer : $PROVISIONNER"
    fi
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

# 4a. Tickets « En revue » ouverts : une MR ouverte est-elle rattachée ?
revue_iids="$(iids_with_workflow "$backlog_opened" "En revue")"
open_mr_branches="$(gl_open_mr_branches 2>/dev/null)"
if [ -z "$revue_iids" ]; then
  ok "aucun ticket « En revue » en attente"
else
  for iid in $revue_iids; do
    if printf '%s\n' "$open_mr_branches" | grep -q "/$iid-"; then
      ok "#$iid « En revue » ↔ MR ouverte"
    else
      warn "#$iid « En revue » sans MR ouverte rattachée (état resté après merge/close ?)"
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

# 4c. L'invariant « exactement un workflow:: par ticket ouvert ».
# C'est LA dérive propre au dispositif par labels, et rien d'autre ne l'attrape : l'exclusion
# mutuelle des labels scopés est une fonctionnalité Premium, donc sur Free le « :: » n'est que
# cosmétique et rien n'empêche un ticket de porter deux valeurs à la fois (docs/10 §3, #207). Deux
# cas, de causes opposées :
#   • 0 label  → ticket échappé à la migration, ou créé depuis l'UI de la forge (qui ne connaît pas
#                notre convention) : il n'est sur AUCUNE colonne du Kanban et sort de tous les
#                comptes (`queue.sh` ne le verra pas, `/backlog` le rendra « - ») ;
#   • ≥ 2      → pose partielle : un ajout sans le retrait des autres. Les lectures rendent alors
#                le PREMIER label rencontré (cf. gl_awk_workflow), donc un état plausible mais
#                arbitraire — le plus pernicieux des deux, puisque rien ne dépasse à l'affichage.
#
# Le COMPTAGE est le seul contrôle de cette section que la table plate ne peut pas porter : elle
# rend un statut, pas un nombre de labels — projeter la dérive l'effacerait. Il est donc délégué à
# `gl_workflow_derives`, qui refait une lecture du backlog et compte à la source, des deux côtés.
# C'est un aller-retour de plus, assumé : le contrôle qui coûte le moins cher est celui qui répond
# encore quand la forge change.
#
# EN MODE `status`, CE CONTRÔLE CHANGE DE QUESTION et pas seulement de source. Le « ≥ 2 » devient
# impossible par construction — c'est le gain du chantier #358 — mais l'état vit sur l'ITEM DE
# PROJET : il reste le « 0 », qui se scinde en deux causes appelant deux gestes différents (ajouter
# le ticket au projet, ou lui poser un état). D'où un verbe à part, `st_derives`, dont la seconde
# colonne est une CAUSE là où celle de `gl_workflow_derives` est un nombre : les fondre sous un
# « 0 » commun rendrait le diagnostic vrai et inutilisable.
if [ -z "$CYCLE" ]; then
  # Le backend n'a pas été deviné en §3 : il ne l'est pas davantage ici. Rendre le verdict des
  # labels sous un `MAESTRO_CYCLE` fautif serait pire que se taire — c'est un ✓ sur un dispositif
  # dont on vient de dire qu'on ne sait pas lequel il est.
  info "backend de cycle de vie indéterminé (§3) — contrôle des dérives sans objet"
elif [ "$CYCLE" = status ]; then
  if [ "$PROJET_LISIBLE" = 0 ]; then
    # Sans projet lisible, TOUS les tickets ressortiraient « hors projet » : ce serait une seule
    # cause rendue N fois, et elle est déjà dite en §3. Un contrôle sans objet n'est pas une dérive.
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
      while IFS=$'\t' read -r iid cause; do
        [ -z "$iid" ] && continue
        case "$cause" in
          hors-projet)
            warn "#$iid ouvert hors du projet « $GL_PROJET_TITRE » — aucun état, et rien ne l'en distingue d'un ticket filtré" ;;
          sans-etat)
            warn "#$iid est dans le projet mais son Status est vide — un état que personne n'a voulu → poser : bash scripts/gitlab/lib.sh set-workflow $iid \"<état>\"" ;;
          *)
            warn "#$iid : cause de dérive inconnue « $cause »" ;;
        esac
      done <<EOF
$st_sans_etat
EOF
      # Nommée une seule fois, pas par ticket : le peuplement est un geste d'ensemble. ⚠ Le verbe
      # est celui du lot #361 — s'il répond « commande inconnue », c'est qu'il n'est pas encore
      # mergé, et non que la dérive est mal diagnostiquée.
      if printf '%s\n' "$st_sans_etat" | grep -q 'hors-projet$'; then
        printf '    → les ajouter au projet d'\''un coup : bash scripts/gitlab/lib.sh project-backfill  (--check pour la liste, verbe du lot #361)\n'
      fi
    fi
  fi
else
  wf_derives="$(gl_workflow_derives opened 2>/dev/null)"
  if [ -z "$wf_derives" ]; then
    ok "tous les tickets ouverts portent exactement un label $GL_WORKFLOW_SCOPE::*"
  else
    while IFS=$'\t' read -r iid n; do
      [ -z "$iid" ] && continue
      if [ "$n" = 0 ]; then
        warn "#$iid ouvert sans label $GL_WORKFLOW_SCOPE::* — hors du Kanban et de tous les comptes → poser : bash scripts/gitlab/lib.sh set-workflow $iid \"<état>\""
      else
        warn "#$iid ouvert porte $n labels $GL_WORKFLOW_SCOPE::* (un seul attendu) — les lectures en rendent un au hasard → reposer le bon : bash scripts/gitlab/lib.sh set-workflow $iid \"<état>\""
      fi
    done <<EOF
$wf_derives
EOF
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
      warn "branche locale « $b » : MR mergée → à nettoyer avec /branch-cleanup"
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
# L'absence de protection de branche est une DÉCISION documentée (docs/10 §8.8, 2026-08-14) — la
# protection de branche n'existe pas sur un dépôt privé d'un compte Free, ni GitHub Pro ni le passage
# en public n'ont été retenus, et `scripts/github/protect-main.sh` attend écrit-mais-non-joué le jour
# où le plan change. Le rendre en ⚠ ferait de ce bilan un fichier durablement jaune, sur un point que
# personne ne compte corriger — et `--strict` échouerait en CI pour dire une chose qu'on sait déjà.
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
    false) info "aucune protection de branche sur main — décision assumée (dépôt privé, compte Free : docs/10 §8.8)"
           printf '    → les six verdicts se lisent sur la PR ; le merge reste une décision humaine (docs/10 §6)\n'
           printf '    → le jour où le plan change : bash scripts/github/protect-main.sh\n' ;;
    *)     warn "protection de branche de main illisible — contrôle ignoré" ;;
  esac
  case "${REGLAGE[suppression_branche]:--}" in
    true)  ok "delete_branch_on_merge=true — la branche source est supprimée au merge" ;;
    false) warn "delete_branch_on_merge ≠ true : les branches distantes s'accumuleraient après merge → relancer : $PROVISIONNER" ;;
    # « - » n'est PAS « false » : le champ n'est présent que si le jeton a le droit d'administration
    # du dépôt (mesuré le 2026-08-17 sur le PAT du projet). L'absence parle du jeton, pas du dépôt.
    *)     info "delete_branch_on_merge illisible (jeton sans droit d'administration du dépôt) — contrôle ignoré" ;;
  esac
fi

# --- 8. Milestones de phase -----------------------------------------------------------------------
# Dérives autour du milestone de phase (docs/10-workflow-git.md §3.4) : un ticket OUVERT sans
# milestone (l'outillage pose la phase courante à la création — lib.sh current-milestone) ; un
# milestone actif ENTIÈREMENT SOLDÉ (la phase est finie : sa fermeture — décision humaine, jamais
# faite ici — est à faire pour que la phase suivante devienne la courante).
section "8. Milestones de phase"
# `gl_milestones` plutôt qu'une requête GraphQL écrite ici : ses colonnes (titre, etat, debut,
# echeance, fermes, total) sont le contrat commun aux deux forges, et « soldé » s'y lit en une
# comparaison — total > 0 et fermés == total. La requête inline, elle, ne pouvait pas passer la
# bascule : elle nommait `project(fullPath:…)`, un champ qui n'existe pas dans le schéma GitHub.
ms_raw="$(gl_milestones 2>/dev/null)"
if [ -z "$ms_raw" ]; then
  warn "milestones illisibles (API muette) — contrôle ignoré"
else
  soldes="$(printf '%s\n' "$ms_raw" | awk -F'\t' '$1 !~ /^#/ && $2 == "active" && $6 > 0 && $5 == $6 { print $1 }')"
  if [ -z "$soldes" ]; then
    ok "aucun milestone actif entièrement soldé"
  else
    while IFS= read -r t; do
      [ -z "$t" ] && continue
      warn "milestone « $t » actif mais entièrement soldé → à fermer (décision humaine) pour que la phase suivante devienne la courante"
    done <<EOF
$soldes
EOF
  fi
  courant="$(gl_current_milestone 2>/dev/null)"
  if [ -n "$courant" ]; then
    ok "phase courante : « $courant » (milestone posé par /ticket-create sur les nouveaux tickets)"
  else
    warn "aucun milestone actif non soldé — /ticket-create créera les prochains tickets sans milestone"
  fi
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

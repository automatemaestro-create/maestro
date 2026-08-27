#!/usr/bin/env bash
# Helpers de forge partagés pour le workflow de tickets Maestro (voir docs/10-workflow-git.md).
#
# Deux usages :
#   1. Sourcé depuis un autre script :   . scripts/gitlab/lib.sh ; gl_set_workflow 16 "En cours"
#   2. Exécuté en sous-commandes :        bash scripts/gitlab/lib.sh set-workflow 16 "En cours"
#
# ================================================================================================
# LA FORGE EST GITHUB, ET ELLE EST SEULE (#344, lot 9 de #335)
# ================================================================================================
# Il n'y a plus de commutateur : `MAESTRO_FORGE` et sa branche `glab` ont été retirés avec le reste
# de l'outillage GitLab. Les verbes de ce fichier répondent contre le dépôt GitHub
# (MAESTRO_GITHUB_REPO), qui porte les tickets, les PR et la CI.
#
# Le projet GitLab reste l'ARCHIVE en lecture seule (271 MR, historique du time tracking natif),
# mais RIEN ici n'y accède : la relire est un geste ponctuel de mainteneur — l'UI web, ou
# `glab <verbe> --repo maestro-group4345327/maestro` avec un `glab` installé à la main (docs/27 §11).
# Le seul code du dépôt qui parle encore à GitLab est `scripts/migration/` (export et inventaire),
# qui ne dépend pas de ce fichier.
#
# CE QUI RESTE DE LA MIGRATION, ET POURQUOI CE N'EST PAS UN OUBLI — deux couches de fonctions :
#
#   • `gl_<verbe>` — le VERBE du workflow Maestro : validation des arguments, idempotence, mise en
#     forme, et tout ce qui ne touche pas le réseau. C'est l'API publique, celle qu'appellent
#     queue.sh, run.sh, status.sh, doctor.sh, worktree.sh et les commandes /ticket-*, et celle que
#     le dispatcher en fin de fichier expose en sous-commandes.
#   • `gh_<verbe>` — la couche qui PARLE À GITHUB, et elle seule.
#
# La frontière n'est plus « forge A contre forge B » mais « verbe contre réseau », ce qui reste une
# séparation utile : elle donne un inventaire exact de la surface réseau (`grep -n '^gh_'`), et
# c'est elle qui a permis d'écrire le backend GitHub sans toucher aux appelants (#339). Fusionner
# les deux couches serait un renommage massif pour un gain de forme — pas ce que ce ticket fait.
#
# TROIS PRIMITIVES portent l'essentiel des lectures ; la plupart des verbes ne parlent pas au
# réseau, ils parlent à un FORMAT, et se branchent donc sur l'une d'elles plutôt que sur l'API :
#
#   • gl_issue_raw <iid>     — la vue TEXTE canonique d'un ticket (en-tête « clé:<TAB>valeur », puis
#                              « -- », puis le corps). Tout ce qui lit un ticket passe par elle —
#                              issue-brief, parent-of, subtickets, start-brief, branch-for,
#                              worktree-done, lots-ouverts, ferme-parent, demarre-parent.
#   • gh_issues_state <iid…> — « <iid><TAB>open|closed » pour N tickets en UNE lecture. La seule
#                              source de l'OUVERT/FERMÉ d'un lot, que ni la table du backlog (bornée
#                              à 100) ni le cycle de vie (posé après coup) ne peuvent porter.
#   • gl_backlog_table       — la table plate TSV du backlog, pour les questions d'ENSEMBLE :
#                              /backlog, queue.sh, doctor.sh, reconcile-workflow, reconcile-en-cours.
#   • st_statuts <iid…>      — « <iid><TAB><libellé> » : le cycle de vie de N tickets NOMMÉS en UNE
#                              lecture, pendant unitaire de la table. Même sortie colonne pour
#                              colonne, mais SANS sa fenêtre de 100 ni la pagination des items du
#                              projet — subtickets/startables s'y branchent (#577, docs/10 §3.6).
#   • gl_mr_brief <branche>  — « etat<TAB>numéro<TAB>sha » de la PR d'une branche, d'où mr-state,
#                              cleanup-merged et worktree-done sortent inchangés.
#
# LE VOCABULAIRE EST CELUI DE GITLAB, et il le reste : `iid` (= le `number` GitHub, la plage
# #2→#333 étant préservée par l'import ordonné du lot 5), état de PR « opened|closed|merged »
# (GitHub dit OPEN/MERGED), assigné « username » (GitHub dit `login`). Ce n'est pas de la nostalgie
# : les appelants comparent ces valeurs en dur, et les renommer propagerait une rupture de contrat
# dans quatre scripts pour ne rien gagner.
#
# CE QUE GITHUB N'A PAS, et comment on le remplace :
#   • Pas de lien « relates to » — mais un `#<n>` mentionné dans un corps y CRÉE une référence
#     croisée native, sur quoi `gh_issue_link` s'appuyait. ⚠ Le seul couple que le dépôt liait
#     jamais était parent/lot, et GitHub porte CELUI-LÀ nativement : depuis #391, `issue-link`
#     rattache une sub-issue (cf. gl_issue_link) au lieu de poster une mention.
#   • Pas de date de début ni d'échéance, PAS DE TEMPS PASSÉ (263/330 tickets, 603 h — docs/27 §5) :
#     c'est la seule vraie perte, et elle est comblée par le SUIVI MAISON ci-dessous.
#   • Pas de date de début sur un jalon : la colonne `debut` de `milestones` vaut « - ».
#   • Ni board Kanban, ni runner de projet, ni id numérique de projet : les verbes qui les
#     servaient (`board-lists`, `project-runners`, `project-id`, `project-enc`) sont partis avec la
#     branche GitLab, et leurs appelants avec eux.
#
# ================================================================================================
# SUIVI MAISON — dates et temps passé, dans un commentaire (implémentation : gh_suivi_*)
# ================================================================================================
# UN SEUL commentaire par ticket, réécrit EN PLACE. Un bloc HTML invisible porte la donnée, le
# texte qui suit en est le rendu humain — DÉRIVÉ du bloc à chaque écriture, donc incapable d'en
# diverger :
#
#     <!-- maestro:suivi:v1
#     debut=2026-08-04
#     echeance=2026-08-06
#     log=2026-08-17|4500|Historique importé de GitLab
#     log=2026-08-21|1800|Cycle de dev (start->finish)
#     temps=6300
#     -->
#     **⏱ Suivi Maestro** — début … · échéance … · temps passé **1h 45m**
#
# Quatre règles : (1) une paire `clé=valeur` par ligne, entre le marqueur et le `-->` ; (2) `log=`
# est le DÉTAIL, « date|secondes|résumé », le « | » ne pouvant apparaître dans un résumé (aplati à
# l'écriture) ; (3) `temps` est TOUJOURS recalculé comme la somme des `log=` — l'agrégat ne peut
# donc pas dériver de son détail, même après une reprise ou une écriture partielle ; (4) les clés
# inconnues traversent intactes, ce qui rend le format extensible sans migration.
#
# LA JOINTURE AVEC L'HISTORIQUE IMPORTÉ (#400). L'import du backlog (#340) a écrit le temps passé de
# chaque ticket dans un AUTRE commentaire, « <!-- maestro:meta v1 … temps_s=… --> », une seule ligne
# de clés. Les deux formats ne se lisaient pas l'un l'autre : sur un ticket importé, le premier log
# de temps repartait de zéro et le ticket se retrouvait avec deux commentaires disant chacun une
# partie du total (mesuré sur #212 : 9000 s d'un côté, « 0m » de l'autre). `gh_suivi_lire` cherche
# donc les DEUX marqueurs et FUSIONNE — la dissymétrie entre eux étant tout le sujet :
#   → le commentaire d'import n'est JAMAIS réécrit. C'est une archive (lien GitLab, tableau, relevés
#     détaillés) : la réparation est du côté de la LECTURE, jamais d'une réécriture de masse sur 352
#     tickets, irréversible là où une lecture ne l'est pas ;
#   → ce qu'il porte est RECOPIÉ dans le bloc du suivi. Les dates comblent ce qui manque (un
#     /ticket-start postérieur l'emporte), et le temps devient une entrée `log=` ordinaire — la
#     seule forme que la règle (3) additionne, un total rangé à côté du détail étant effacé au
#     premier log. Le premier `gh_suivi_ecrire` qui suit pose le tout au format courant : la
#     migration du ticket se fait au fil de l'eau, sans campagne.
# L'entrée se reconnaît à son résumé ($GL_SUIVI_IMPORT), ce qui rend la fusion idempotente et permet
# de la RETRANCHER : `get-time-spent --hors-import` est la forme que /ticket-finish interroge pour
# son idempotence, un total importé n'étant pas un cycle de dev déjà loggé.
#
# ================================================================================================
# CYCLE DE VIE — le champ Status de GitHub Projects v2, seule autorité (#365, chantier #358)
# ================================================================================================
# L'état d'un ticket (À faire / En cours / En revue / Terminé / Abandonné / Doublon) se lit et
# s'écrit dans le champ **Status** du projet GitHub Projects v2 monté par
# `scripts/github/bootstrap-project.sh`. C'est le SEUL support, depuis que #365 a retiré les six
# labels `workflow::*` qui l'ont porté de #207 à #364, et avec eux le commutateur `MAESTRO_CYCLE`
# qui choisissait entre les deux.
#
# ⚠ IL N'Y A PLUS DE RETOUR ARRIÈRE, ET C'EST LA DÉCISION DE #365. Tant que les labels étaient là,
# rebasculer coûtait une variable d'environnement ; ils sont partis, donc rebasculer coûterait une
# migration. Ne pas réintroduire un second backend « au cas où » : c'est exactement ce que ce
# chantier a supprimé, et le premier symptôme de deux supports est un ticket qui porte deux états.
#
# POURQUOI UN CHAMP PLUTÔT QUE DES LABELS. Le retour aux labels de #207 n'était pas un choix : GitLab
# Free ayant perdu le champ Status natif à la fin de l'essai Ultimate, ils étaient le seul mécanisme
# disponible, et l'EXCLUSION MUTUELLE des six est donc restée à notre charge — toute pose devait
# AJOUTER la cible et RETIRER les cinq autres dans le même appel, faute de quoi un ticket portait
# deux états. Un champ à valeur unique rend cette classe de bug impossible par construction : c'est
# tout le gain du chantier, et la raison pour laquelle ce fichier ne porte plus une seule ligne
# d'exclusion mutuelle. Ce chantier ne défait pas #207 : il le remplace par ce qui manquait alors.
#
# CE QUE LE CHAMP DÉPLACE, ET QU'IL FAUT SAVOIR AVANT D'Y TOUCHER. Le Status vit sur l'ITEM DE
# PROJET, pas sur l'issue. Un ticket absent du projet n'a donc AUCUN état — l'équivalent exact du
# « 0 label workflow:: » de l'ère des labels, en plus silencieux. Les deux verbes de l'unité en
# tirent des conclusions OPPOSÉES, et c'est délibéré :
#   • en ÉCRITURE, `st_set_workflow` REFUSE en nommant la cause (rien à mettre à jour : la mutation
#     `updateProjectV2ItemFieldValue` a besoin d'un item). Ajouter le ticket au passage serait faire
#     le travail de #361 en silence, sur une écriture que personne n'a demandée ;
#   • en LECTURE, `st_issue_owner` rend un statut VIDE, qui est déjà le contrat de « non posé » et
#     ce que `gl_close_guard`/`gl_start_brief` savent lire. Une lecture qui échouerait ferait
#     s'arrêter des appelants dont ce n'est pas le sujet.
# Le peuplement est #361 (`gl_project_add`, appelé par /ticket-create dans la foulée de la
# création), sa détection #363 (`doctor.sh`), et sa réparation à l'unité le même `gl_project_add`.
#
# SEPT VERBES PORTENT LE CYCLE DE VIE, et ils se lisent en deux groupes. L'UNITÉ d'abord — lire et
# écrire l'état d'UN ticket (#360) : `set-workflow`, `issue-owner`, `begin` et `liberer-ticket`, les
# deux derniers écrivant l'état pour prendre le ticket et pour le rendre. Les lectures d'ENSEMBLE
# ensuite (#362) : `backlog-table`, `milestone-issues` et `workflow-derives`. Ces trois-là suffisent
# à servir TOUS les consommateurs d'ensemble — `/backlog`, `queue.sh` (donc `/orchestrate`),
# `reconcile-workflow`, `reconcile-en-cours`, `subtickets`, `startables` et `doctor.sh` —, parce
# qu'aucun ne parle au réseau : tous lisent la colonne `statut` des deux tables plates (cf. les
# TROIS PRIMITIVES en tête de fichier).
#
# LES SEPT SONT DES ALIAS `gl_` → `st_`, ET LA COUTURE RESTE. Chacun portait, jusqu'à #365, le
# commutateur qui choisissait son backend ; il n'en reste qu'une délégation d'une ligne. La garder
# plutôt que de renommer `st_*` en `gl_*` tient à ce que `gl_` est la SURFACE PUBLIQUE — le
# dispatcher en fin de fichier, les scripts et les prompts n'appellent que lui — et à ce que les
# trois backends successifs de ce fichier ont tous été greffés à cette couture-là.
#
# AUCUN ID EN DUR, JAMAIS. L'ID du projet, celui du champ Status et ceux de ses six options se
# dérivent PAR NOM, en une lecture (`st_contexte`). Le projet se désigne par son TITRE
# (`MAESTRO_PROJECT_TITRE`, défaut « Maestro » — la même clé que bootstrap-project.sh), les options
# par leur LIBELLÉ. Un ID de projet figé dans un script est un clone qui ne démarre pas ; un `grep`
# du dépôt ne doit en trouver aucun.
#
# ================================================================================================
# CONTRAT DE SURFACE DU CYCLE DE VIE — à lire avant d'y toucher (ticket #209, chantier #207)
# ================================================================================================
# CE CONTRAT A SURVÉCU À TROIS SUPPORTS — champ natif GitLab, labels scopés, champ Projects v2 — et
# c'est ce qui a permis d'en changer deux fois sans qu'aucune des 8 commandes `.claude/` bouge d'une
# ligne. Il décrit le VOCABULAIRE, jamais le stockage. Deux formes coexistent :
#
#   • SLUG      — « a-faire », « en-cours », « en-revue », « termine », « abandonne », « doublon ».
#                 Forme ASCII, sans accent. Ce fut le SUFFIXE DU LABEL, c'est-à-dire un stockage ;
#                 ce n'est plus qu'une forme D'ENTRÉE acceptée et la clé de la normalisation.
#   • LIBELLÉ   — « À faire », « En cours », « En revue », « Terminé », « Abandonné », « Doublon ».
#                 C'est la SURFACE : le vocabulaire du domaine, celui de la doc et des commandes, et
#                 EXACTEMENT le libellé des six options du champ Status.
#
# Décision, tranchée une fois pour toutes et valable pour TOUS les helpers de ce fichier :
#
#   → EN SORTIE, toujours le LIBELLÉ. Colonne `statut` des TSV (backlog-table, milestone-issues,
#     subtickets), gl_issue_owner, gl_start_brief, gl_close_guard : tous rendent « À faire », pas
#     « a-faire ». Le slug ne sort JAMAIS de ce fichier.
#   → EN ENTRÉE, les DEUX sont acceptés (gl_set_workflow 16 "En cours" ≡ gl_set_workflow 16
#     en-cours), la normalisation étant faite par gl_workflow_slug. Écrire en libellé reste la
#     forme canonique dans les appelants.
#
# Pourquoi le libellé et pas le slug : les consommateurs comparent sur des chaînes en dur —
# queue.sh (« $2 == "À faire" »), run.sh (« En cours » / « En revue »), doctor.sh, et
# gl_subtickets_startables ici même. Garder le libellé fait de tout changement de support un
# changement INTERNE à ce fichier : les lots 3 et 4 de #207 n'ont pas eu à réécrire leurs
# comparaisons, seulement à renommer set-status → set-workflow. Passer aux slugs aurait propagé une
# rupture de contrat dans quatre scripts pour ne gagner qu'un `sed` de moins ici. Vérifié deux fois
# plutôt qu'une : à la bascule de #364, puis au retrait des labels de #365, sans qu'une ligne y soit
# touchée de part et d'autre.
# ================================================================================================
#
# NB : pas de `set -e` global — ce fichier est conçu pour être sourcé sans imposer son mode
# d'erreur au script appelant. Chaque fonction renvoie un code non nul en cas d'échec.

# --- Configuration (surchageable par variables d'environnement) --------------------------------
# Le répertoire de CE fichier, pour atteindre ses voisins (scripts/orchestrate/pilote.sh) sans
# dépendre du répertoire courant. Préfixé `GL_` parce que lib.sh est SOURCÉ par une dizaine de
# scripts qui ont déjà leur `ICI`/`RACINE` : écraser le leur les enverrait chercher leurs propres
# fichiers dans scripts/gitlab/.
#
# Résolu SANS AUCUN FORK (#372) — expansions de paramètre et `case`, rien d'autre. La forme
# évidente, `$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)`, coûte TROIS processus sous MSYS et
# elle les coûte à CHAQUE chargement du fichier : lib.sh est sourcée par une dizaine de scripts
# (worktree.sh, run.sh, queue.sh, doctor.sh…) que les suites d'outillage appellent des milliers de
# fois. Mesuré ici le 2026-08-19 : 47,0 ms sur les 57,9 ms de marginal d'un chargement, soit 81 %
# — pour une variable qui ne sert qu'à TROIS lignes du fichier (gl_reconcile_en_cours,
# gl_reprendre_en_cours). C'est la cause n°3 des 15 min du filet CI local.
#
# Pourquoi ancré au chargement et non résolu paresseusement à l'usage : `${BASH_SOURCE[0]}` peut
# être relatif (`. scripts/gitlab/lib.sh`), et un appelant qui change de répertoire entre le
# `source` et l'appel ferait alors résoudre le chemin depuis le mauvais endroit. On paie donc
# l'ancrage tout de suite — mais en expansions, pas en processus.
#
# ⚠ LES DEUX SÉPARATEURS, et ce n'est pas de la prudence décorative : le harnais de tests lance
# `bash <racine>/scripts/gitlab/lib.sh` avec une `WindowsPath`, donc un chemin TOUT EN ANTISLASHS,
# sans un seul `/`. `${BASH_SOURCE[0]%/*}` n'y coupe alors rien, et un `GL_ICI` retombé sur le
# répertoire courant rend `pilote.sh` et `journal.sh` introuvables — soit, en pratique, un ticket
# vivant déclaré « orphelin » et une reprise qui répond « aucun run ne l'a jugé ». Le fork qu'on
# retire ici savait le faire (`dirname` est conscient des antislashs sous MSYS, et `cd`+`pwd`
# reconvertissait) : le remplacer, c'est reprendre CE travail-là, pas seulement couper au dernier
# `/`.
#
# Le résultat est absolu mais pas forcément normalisé (il peut porter un `./`, et il garde la
# lettre de lecteur quand on l'a reçue) : sans importance, il ne sert qu'à préfixer un chemin que
# le système résout lui-même, et MSYS accepte les deux formes.
GL_ICI="${BASH_SOURCE[0]//\\//}"                    # antislashs → slashs, AVANT tout decoupage
case "$GL_ICI" in
  */*) GL_ICI="${GL_ICI%/*}" ;;
  *) GL_ICI="." ;;                                  # chargée sans séparateur : le répertoire courant
esac
case "$GL_ICI" in
  /* | ?:/*) ;;                                     # déjà absolu (POSIX, ou lettre de lecteur)
  *) GL_ICI="$PWD/$GL_ICI" ;;
esac

# Le dépôt GitHub, seule cible des verbes de ce fichier. Même variable d'environnement que
# scripts/migration/inventaire.sh (#336) : un seul nom pour un seul dépôt cible.
GL_GH_REPO="${MAESTRO_GITHUB_REPO:-automatemaestro-create/maestro}"

# Marqueur du commentaire de suivi maison (dates + temps passé) — voir l'en-tête du fichier.
GL_SUIVI_MARQUEUR="maestro:suivi:v1"

# Marqueur du commentaire de MÉTADONNÉES posé par l'import du backlog (`scripts/migration/
# import-github.sh`, #340) : une ligne de clés, `temps_s=` en secondes. Ce n'est PAS un second
# support du suivi — c'est une ARCHIVE, en lecture seule, que la fusion de gh_suivi_lire rapatrie
# dans le format courant (#400). Voir SUIVI MAISON en tête de fichier.
GL_META_MARQUEUR="maestro:meta v1"

# Résumé de l'entrée `log=` qui porte l'historique importé, et par lequel on la RECONNAÎT : c'est ce
# qui rend la fusion idempotente et le retranchement de l'historique possible (`get-time-spent
# --hors-import`). Il ne porte aucun métacaractère d'expression rationnelle ni de « | » — les deux
# le sont, et les motifs qui le cherchent en dépendent.
GL_SUIVI_IMPORT="Historique importé de GitLab"

# Nombre d'heures dans un « jour » de temps passé, et de jours dans une « semaine » : les unités de
# durée de GitLab (« 1d », « 1w »), reprises TELLES QUELLES pour que les 603 h d'historique importées
# et les entrées écrites après la bascule se comptent avec la même règle.
GL_HEURES_PAR_JOUR="${GL_HEURES_PAR_JOUR:-8}"
GL_JOURS_PAR_SEMAINE="${GL_JOURS_PAR_SEMAINE:-5}"

# Délai (en jours) entre la date de début et l'échéance, par priorité. L'échéance est posée au
# /ticket-start = début + délai. Surchargeable par variables d'environnement.
GL_DUE_DELAY_HAUTE="${GL_DUE_DELAY_HAUTE:-2}"
GL_DUE_DELAY_MOYENNE="${GL_DUE_DELAY_MOYENNE:-5}"
GL_DUE_DELAY_BASSE="${GL_DUE_DELAY_BASSE:-10}"

# Revue best-effort (voir la section « Revue » plus bas) : comptes d'AUTOMATISATION à ne jamais
# désigner comme relecteur, séparés par des virgules. Le compte de l'agent Maestro n'est pas un
# « bot » au sens de GitLab (`User.bot` y vaut false : c'est un compte utilisateur ordinaire), il ne
# peut donc pas être écarté par l'API seule. Cette liste est une CONFIGURATION d'instance — c'est le
# compte à exclure qui est nommé, jamais le relecteur, qui reste résolu dynamiquement.
GL_BOT_USERS="${GL_BOT_USERS:-MaestroAgents}"

# Niveau d'accès minimal d'un relecteur (30 = Developer) : en dessous, le membre ne peut ni pousser
# ni merger, donc le désigner n'aurait pas de sens.
GL_REVIEWER_MIN_ACCESS="${GL_REVIEWER_MIN_ACCESS:-30}"

# Seuil de SILENCE au-delà duquel un ticket « En cours » est tenu pour ORPHELIN (#328, docs/10 §9.6),
# en secondes. Volontairement GÉNÉREUX, et calé sur ce qui fait taire une session vivante : une
# session Claude Code qui épuise la limite d'usage de 5 h dort jusqu'à son reset sans rien écrire, et
# `run.sh` l'attend jusqu'à 5 h 30 (son PLAFOND_ATTENTE_S). Sous ce seuil, on désignerait comme
# abandonné un ticket dont la session attend légitimement — et c'est le sens dans lequel se tromper
# coûte cher, puisque #329 rendra l'orphelin prenable.
GL_ORPHELIN_SEUIL="${MAESTRO_ORPHELIN_SEUIL:-21600}"   # 6 h — juste au-dessus du plafond d'attente
# Une valeur d'environnement fantaisiste ne doit pas faire planter un verbe de diagnostic : on
# retombe sur le défaut plutôt que d'échouer au premier test arithmétique (même parti pris que
# scripts/orchestrate/status.sh).
[ "$GL_ORPHELIN_SEUIL" -ge 0 ] 2>/dev/null || GL_ORPHELIN_SEUIL=21600

# Nombre de fois qu'un même ticket peut être RENDU PRENABLE avant que la reprise ne demande
# `--force` (#329, docs/10 §9.6). Ce n'est pas un réglage de confort : sans plafond, un ticket que
# chaque session fait tomber au même endroit repart à chaque run, brûle une session entière et
# redevient orphelin — la reprise deviendrait une boucle, et une boucle sur un quota partagé. Deux
# essais, parce que le premier échec est souvent conjoncturel (limite d'usage, pilote tué) là où le
# second désigne le ticket lui-même. Au-delà, on ne refuse pas la reprise : on exige qu'elle soit
# demandée, ce qui est exactement la différence entre un geste et une boucle.
GL_REPRISES_MAX="${MAESTRO_REPRISES_MAX:-2}"
[ "$GL_REPRISES_MAX" -ge 0 ] 2>/dev/null || GL_REPRISES_MAX=2

# Retry des LECTURES GraphQL (voir gh_graphql_read) : l'endpoint GraphQL renvoie parfois une
# réponse vide (hoquet réseau / rate-limit). On ré-essaie jusqu'à GL_GQL_RETRIES tentatives, avec
# GL_GQL_RETRY_DELAY seconde(s) de pause entre deux. Surchargeable.
GL_GQL_RETRIES="${GL_GQL_RETRIES:-3}"
GL_GQL_RETRY_DELAY="${GL_GQL_RETRY_DELAY:-1}"

# Titre du projet GitHub Projects v2 qui porte le champ Status. C'est une CLÉ, pas un libellé
# d'affichage : c'est par elle que le projet se résout, ici comme dans
# scripts/github/bootstrap-project.sh, et c'est ce qui évite d'avoir un numéro de projet mémorisé
# quelque part. Même variable d'environnement des deux côtés — un seul nom pour un seul projet.
GL_PROJET_TITRE="${MAESTRO_PROJECT_TITRE:-Maestro}"

# --- Identité de la forge ---------------------------------------------------------------------
# gl_depot_courant -> le dépôt visé par la forge active, pour les MESSAGES du code partagé
# (« ticket #12 introuvable dans … »). Ne sert jamais à construire un appel : chaque backend
# connaît le sien.
gl_depot_courant() {
  printf '%s\n' "$GL_GH_REPO"
}

# gl_forge_nom / gl_forge_cli -> « GitHub » et « gh », pour les MESSAGES des appelants (#341).
# Deux fonctions et non une : un message parle tantôt de la forge (« GitHub injoignable »), tantôt
# de l'outil qui y accède (« gh absent ou non authentifié »), et les confondre produit des phrases
# fausses dans les deux sens. Elles rendent désormais une constante — mais restent le seul endroit
# à changer si le dépôt déménage encore, et évitent d'avoir à toucher chacun de leurs appelants.
gl_forge_nom() {
  printf 'GitHub\n'
}

gl_forge_cli() {
  printf 'gh\n'
}

# --- Pré-requis ---------------------------------------------------------------------------------
# Vérifie que le CLI de la forge est installé ET authentifié. À appeler en tête des commandes.
gl_require() {
  gh_require
}

# gl_current_user -> imprime le username de l'utilisateur authentifié (pour l'auto-assignation du
# ticket par /ticket-start). Parsing en shell pur (grep/sed) — pas de dépendance à jq/python, et
# entièrement couvert par l'allowlist `bash scripts/gitlab/lib.sh:*` (docs/10 §7.1), pour que
# /ticket-start ne déclenche aucun prompt de permission sur cette étape.
gl_current_user() {
  gh_current_user
}

# --- Résolution d'identifiants ------------------------------------------------------------------
# gl_workitem_gid <iid> -> imprime le GID global du work item (gid://gitlab/WorkItem/<n>).
gl_workitem_gid() {
  gh_workitem_gid "$@"
}

# --- Cycle de vie : slugs, libellés, GID de labels ----------------------------------------------
# Voir le CONTRAT DE SURFACE en tête de fichier : slug = stockage (suffixe du label), libellé =
# surface (ce que rendent tous les helpers). Ces trois fonctions sont le seul endroit du dépôt qui
# connaît la correspondance entre les deux.

# gl_workflow_slug <valeur> -> normalise une valeur de cycle de vie en SLUG (« En cours » →
# « en-cours », « en-cours » → « en-cours »). Accepte donc indifféremment le libellé ou le slug,
# c'est la porte d'entrée de toute écriture. Code 1 sur une valeur inconnue, avec la liste.
gl_workflow_slug() {
  local v="$1"
  if [ -z "$v" ]; then echo "gl_workflow_slug : valeur manquante" >&2; return 2; fi
  case "$v" in
    "À faire"|"a-faire"|"À FAIRE")     printf 'a-faire\n' ;;
    "En cours"|"en-cours")             printf 'en-cours\n' ;;
    "En revue"|"en-revue")             printf 'en-revue\n' ;;
    "Terminé"|"termine"|"Termine")     printf 'termine\n' ;;
    "Abandonné"|"abandonne"|"Abandonne") printf 'abandonne\n' ;;
    "Doublon"|"doublon")               printf 'doublon\n' ;;
    *)
      echo "Valeur de cycle de vie inconnue : « $v »." >&2
      echo "Attendu : À faire | En cours | En revue | Terminé | Abandonné | Doublon" >&2
      echo "  (les slugs a-faire|en-cours|en-revue|termine|abandonne|doublon sont acceptés aussi)" >&2
      return 1 ;;
  esac
}

# gl_workflow_label <slug> -> chemin inverse : rend le LIBELLÉ lisible d'un slug. C'est ce que
# toutes les lectures appliquent avant de rendre la main, pour que le slug ne sorte jamais d'ici.
# Une valeur inconnue est rendue TELLE QUELLE (pas d'erreur) : une lecture ne doit pas échouer sur
# un label exotique posé à la main — l'appelant verra passer la valeur brute et pourra la signaler.
gl_workflow_label() {
  case "$1" in
    a-faire)   printf 'À faire\n' ;;
    en-cours)  printf 'En cours\n' ;;
    en-revue)  printf 'En revue\n' ;;
    termine)   printf 'Terminé\n' ;;
    abandonne) printf 'Abandonné\n' ;;
    doublon)   printf 'Doublon\n' ;;
    *)         printf '%s\n' "$1" ;;
  esac
}

# --- Actions ------------------------------------------------------------------------------------
# gl_set_workflow <iid> <valeur> -> pose le cycle de vie du ticket dans le champ Status du projet.
# <valeur> accepte le libellé (« En cours ») comme le slug (« en-cours »).
# Idempotent : reposer la valeur déjà présente ne change rien. REFUSE en nommant la cause si le
# ticket n'est pas un item du projet — il n'y a alors rien à mettre à jour (cf. en-tête).
gl_set_workflow() {
  st_set_workflow "$@"
}

# gl_reconcile_workflow [--check] [<iid>…] -> pose « Terminé » sur les tickets dont le travail est
# SOLDÉ mais dont le cycle de vie est resté ACTIF (#275). C'est la réparation de la dérive que
# doctor.sh se contentait de diagnostiquer (« ticket fermé mais son état est encore actif ») : le
# merge FERME le ticket mais ne touche à aucun label, et depuis #207 seul /branch-cleanup — un geste
# manuel — posait « Terminé ». Entre les deux, un ticket mergé s'affiche « En revue » indéfiniment.
#
# Deux modes, même règle :
#   • avec des <iid>  : ne traite que ceux-là, une lecture par ticket. C'est ainsi que le ramassage
#                       des worktrees s'y branche (worktree.sh gc), sur un verdict DÉJÀ rendu. Ce
#                       mode FAIT CONFIANCE à l'appelant sur le fait que le travail est soldé — il
#                       ne revérifie pas que le ticket est fermé, `gl_worktree_done` rendant « fini »
#                       aussi sur une PR mergée dont le ticket est resté ouvert (PR sans `Closes`) ;
#   • sans argument   : balaie le backlog FERMÉ en UNE lecture (les labels y sont déjà) et répare
#                       tout ce qui traîne — le verbe explicite, utilisable seul. Périmètre : les
#                       100 derniers fermés (le `first: 100` de gl_backlog), donc exactement celui
#                       du diagnostic §4b de doctor.sh — ce qu'il signale, ce verbe le répare, ni
#                       plus ni moins. Un ticket fermé de longue date et resté actif lui échappe
#                       comme il échappe déjà au diagnostic.
#
# LA RÈGLE ET SON SEUL PIÈGE : on ne pose que sur un cycle de vie ACTIF (« À faire »/« En cours »/
# « En revue ») ou ABSENT. Un ticket déjà « Abandonné » ou « Doublon » n'est JAMAIS écrasé — un
# ticket fermé sans avoir été réalisé est fermé quand même, et `gl_worktree_done` rend « fini » pour
# lui exactement comme pour un ticket livré (cf. son en-tête). Sans ce filtre, ramasser le worktree
# d'un ticket abandonné le déclarerait « Terminé », et la dérive réparée en créerait une autre.
# « Terminé » déjà posé est également sauté : c'est le cas nominal en régime établi, et le sauter
# évite une écriture par passage de `gc`.
#
# Best-effort par construction : un ticket illisible est signalé et n'arrête pas les suivants. Le
# code de retour vaut 1 s'il en reste un en échec, mais aucun appelant ne doit en faire un motif de
# blocage (même statut que gl_sync_main, docs/10 §9.3).
gl_reconcile_workflow() {
  local check=0
  while [ "${1:-}" = "--check" ]; do check=1; shift; done
  local iids="$*" statut echecs=0 poses=0 sautes=0

  if [ -z "$iids" ]; then
    # Balayage : le cycle de vie est DANS le backlog fermé, donc aucune lecture par ticket. Même
    # filtre que doctor.sh §4b — les trois valeurs actives, et elles seules.
    # Le filtre porte sur la TABLE et non sur le JSON brut (#339) : c'est la table qui est le
    # contrat commun aux deux forges, là où la forme du JSON est propre à chacune. Rien ne se perd
    # au passage — la colonne `statut` porte déjà le libellé dérivé du label.
    local ferme
    ferme="$(gl_backlog_table closed)" || return 1
    iids="$(printf '%s\n' "$ferme" | awk -F '\t' '
      $1 ~ /^[0-9]+$/ && ($2 == "À faire" || $2 == "En cours" || $2 == "En revue") { print $1 }')"
    if [ -z "$iids" ]; then
      printf 'Aucun ticket fermé au cycle de vie resté actif — rien à réconcilier.\n'
      return 0
    fi
    # Le filtre a déjà tranché : ces tickets portent un label actif, la relecture serait redondante.
    local iid
    for iid in $iids; do
      if [ "$check" = 1 ]; then
        printf '  → #%s passerait à « Terminé »\n' "$iid"; poses=$((poses + 1)); continue
      fi
      if gl_set_workflow "$iid" "Terminé"; then poses=$((poses + 1)); else echecs=$((echecs + 1)); fi
    done
  else
    local iid brut
    for iid in $iids; do
      # Une lecture par ticket : c'est le prix du filtre ci-dessus, et il est payé sur des tickets
      # déjà identifiés par l'appelant (0 ou 1 par passage de `gc`), jamais sur une découverte.
      # Capture PUIS découpe : `gl_issue_owner | cut` rendrait le code de `cut`, toujours 0 —
      # un ticket illisible passerait alors pour un ticket sans cycle de vie, donc à poser.
      if ! brut="$(gl_issue_owner "$iid")"; then
        echecs=$((echecs + 1)); continue
      fi
      statut="${brut%%$'\t'*}"
      case "$statut" in
        'Abandonné'|'Doublon'|'Terminé') sautes=$((sautes + 1)); continue ;;
      esac
      if [ "$check" = 1 ]; then
        printf '  → #%s passerait de « %s » à « Terminé »\n' "$iid" "${statut:-aucun}"
        poses=$((poses + 1)); continue
      fi
      if gl_set_workflow "$iid" "Terminé"; then poses=$((poses + 1)); else echecs=$((echecs + 1)); fi
    done
  fi

  [ "$sautes" -gt 0 ] && printf '%s ticket(s) déjà à un état final — inchangé(s).\n' "$sautes"
  [ "$echecs" -gt 0 ] && { printf 'Réconciliation : %s ticket(s) en échec.\n' "$echecs" >&2; return 1; }
  return 0
}

# --- Lecture / reporting ------------------------------------------------------------------------
# gl_backlog [state] -> JSON des work items du projet avec leurs labels et leurs assignés.
# state ∈ opened (défaut) | closed | all. Requête canonique du backlog, source unique de /backlog
# comme des futurs outils (Control Tower, agents). La mise en forme est laissée à l'appelant — jq
# n'est pas requis.
# ⚠ CE VERBE NE PORTE PAS LE CYCLE DE VIE, et c'est délibéré : son contrat est de rendre LA RÉPONSE
# DE LA FORGE telle quelle, or l'état vit sur l'item de projet et non sur l'issue. Qui veut l'état
# lit la TABLE (gl_backlog_table), pas ce JSON.
gl_backlog() {
  gh_backlog "$@"
}

# gl_backlog_table [state] -> projette le JSON de gl_backlog en une TABLE PLATE COMPACTE (une ligne
# par ticket) pour réinjecter beaucoup moins de contexte que le JSON imbriqué. Le JSON brut reste
# disponible via gl_backlog / la sous-commande `backlog` pour tout appelant qui en a besoin.
#
# Format de sortie (source unique, exploitable par /backlog comme par les futurs outils — Control
# Tower, agents) : TSV (séparateur TABULATION), une ligne d'en-tête préfixée « # » que les
# consommateurs machine peuvent ignorer, puis une ligne par ticket :
#     iid <TAB> statut <TAB> prio <TAB> agent <TAB> assigne <TAB> titre
# Les valeurs `prio`/`agent` sont le suffixe nu du label (« moyenne », « devops ») ; un champ vide
# (prio/agent/assigné absent) est rendu « - ». Le `statut`, lui, est le LIBELLÉ du cycle de vie
# (« À faire », « En revue » — jamais un slug : contrat de surface en tête de fichier), et vaut
# « - » si le ticket n'a pas d'état : hors du projet, ou Status non posé.
#
# Projection en awk pur (pas de jq requis) : le parsing suit la même approche grep/sed/awk que le
# reste de ce fichier, donc la commande fonctionne à l'identique que jq soit installé ou non.
gl_backlog_table() {
  st_backlog_table "$@"
}

# gl_labels -> tous les labels du projet/dépôt, un NOM par ligne, non triés (l'ordre est celui de la
# forge). Sert au contrôle de complétude des familles type::/agent::/prio:: (doctor.sh §2) et au
# provisionnement idempotent (bootstrap.sh) : deux appelants qui lisaient les labels en direct par
# le CLI de la forge, donc deux appelants qui rendaient une liste VIDE dès qu'elle changeait — et
# une liste vide se lit ici « tous les labels manquent », pas « je n'ai pas su lire ».
gl_labels() {
  gh_labels
}

# gl_workflow_derives [state] -> « <iid><TAB><n> » pour les tickets dont le nombre d'états est
# DIFFÉRENT de 1, où n est ce nombre. Sur un champ à valeur unique, n ne peut valoir que 0 : le
# « ≥ 2 » que traquait le dispositif par labels est impossible par construction, et c'est tout le
# gain du chantier (cf. en-tête). Reste « 0 », qui recouvre deux causes — ticket hors du projet, ou
# Status non posé — que #363 distingue et formule dans doctor.sh.
#
# Le verbe existe parce que doctor.sh le calculait sur le JSON BRUT de gl_backlog, en cherchant
# « "iid":" » — une clé que GitHub n'écrit pas (il rend « "number": »). Le contrôle ne tombait donc
# pas en erreur après la bascule de forge : il rendait « aucune dérive », c'est-à-dire un ✓ sur une
# question jamais posée. Un contrôle de dérive qui se tait est pire qu'absent.
gl_workflow_derives() {
  st_workflow_derives "$@"
}

# gl_issues_sans_milestone -> iid des tickets OUVERTS ne portant aucun jalon, un par ligne.
# L'outillage pose la phase courante à la création (/ticket-create, docs/10 §3.4) : un ticket ouvert
# sans jalon vient de l'UI de la forge, et il échappe alors à tout plan de run comme à toute
# présentation de milestone.
gl_issues_sans_milestone() {
  gh_issues_sans_milestone
}

# gl_issue_owner <iid> -> imprime « <statut><TAB><assignés> » : le LIBELLÉ du cycle de vie (lu dans
# le champ Status, cf. contrat de surface en tête de fichier) et les usernames des assignés
# séparés par des virgules. Un champ vide signifie « non posé » pour le cycle de vie, « personne »
# (ticket LIBRE) pour les assignés. Une seule lecture GraphQL, parsing shell pur (pas de jq) —
# même approche que gl_backlog_table, en ciblant un seul ticket.
# Sert l'ANTI-COLLISION du travail à plusieurs (#159) : la vue texte expose bien les labels,
# mais pas de quoi décider d'un coup d'œil, donc gl_start_brief s'appuie là-dessus pour dire si un
# ticket est déjà pris — et /ticket-start pour refuser de le démarrer (gl_begin REMPLACE la liste
# des assignés : démarrer un ticket pris le retirerait en silence à son propriétaire).
gl_issue_owner() {
  st_issue_owner "$@"
}

# gl_issue_taken <iid> [moi] -> code 0 (et message sur stdout) si le ticket est DÉJÀ PRIS PAR
# QUELQU'UN D'AUTRE : statut « En cours » et assigné à un username différent de l'utilisateur
# courant (résolu par gl_current_user si l'argument est absent). Code 1 sinon (libre, à moi, ou
# statut différent). Prédicat volontairement étroit — c'est la seule situation où deux personnes se
# marchent dessus ; un ticket « En revue »/« Terminé » assigné à un tiers relève d'un autre sujet.
gl_issue_taken() {
  local iid="$1" moi="${2:-}"
  if [ -z "$iid" ]; then echo "usage: gl_issue_taken <iid> [username]" >&2; return 2; fi
  local owner statut assignes
  owner="$(gl_issue_owner "$iid")" || return 1
  IFS=$'\t' read -r statut assignes <<< "$owner"
  [ "$statut" = "En cours" ] || return 1
  [ -n "$assignes" ] || return 1
  [ -n "$moi" ] || moi="$(gl_current_user 2>/dev/null)"
  # Appartenance exacte à la liste (les virgules encadrantes évitent qu'« alice » matche
  # « alice-bot ») : si je suis dans les assignés, le ticket est à moi, pas « pris ».
  if [ -n "$moi" ] && printf '%s' ",$assignes," | grep -q ",$moi,"; then return 1; fi
  printf '%s\n' "$assignes"
}

# gl_issue_raw <iid> -> LA VUE TEXTE CANONIQUE d'un ticket, et l'une des trois primitives dont
# descend tout ce qui lit un ticket (voir l'en-tête du fichier). Le format est celui qu'imprimait
# `glab issue view`, gardé tel quel parce que six verbes le parsent :
#
#     title:<TAB><titre>
#     state:<TAB>open|closed
#     author:<TAB><username>
#     labels:<TAB><csv>
#     assignees:<TAB><csv>
#     milestone:<TAB><titre>
#     --
#     <corps>
#
# Six verbes lisent un ticket, et AUCUN n'a de variante par forge parce qu'ils lisent tous ce
# format-ci : gl_issue_brief, gl_parent_of, gl_subtickets, gl_start_brief, gl_branch_for,
# gl_worktree_done. C'est ce qui fait la différence entre doubler trois fonctions et en doubler
# neuf — et, surtout, entre une règle métier écrite une fois et la même règle écrite deux fois.
gl_issue_raw() {
  local iid="$1"
  if [ -z "$iid" ]; then echo "usage: gl_issue_raw <iid>" >&2; return 2; fi
  gh_issue_raw "$iid"
}

# gl_issue_brief <iid> -> projection compacte de la vue ticket : uniquement le titre, les labels et
# la section « Critères d'acceptation ». Le reste du corps (Description, « Pourquoi maintenant ? »…)
# est écarté. Utilisé par /ticket-start à la place de la vue intégrale pour réinjecter moins de
# contexte (celle-ci reste disponible en direct si besoin). Parsing en awk pur (pas de jq requis).
gl_issue_brief() {
  local iid="$1"
  if [ -z "$iid" ]; then echo "usage: gl_issue_brief <iid>" >&2; return 2; fi
  local raw
  raw="$(gl_issue_raw "$iid")" || return 1
  printf '%s\n' "$raw" | gl_issue_brief_render "$iid"
}

# gl_issue_brief_render <iid> — cœur de gl_issue_brief, séparé pour être rejoué sur un ticket DÉJÀ
# LU (stdin = sortie brute de gl_issue_raw) : gl_start_brief s'en sert pour ne lire le ticket
# qu'une seule fois et enchaîner toutes les projections sur le même texte.
# Deux formats de « critères d'acceptation » coexistent dans le backlog : les tickets récents
# (issue templates) posent un titre de section « ## Critères d'acceptation » suivi d'une liste
# « - [ ] … » ; les tickets plus anciens l'écrivent en paragraphe inline (« Critères d'acceptation :
# … »). Le mot « acceptation » n'a pas d'accent → on l'utilise comme ancre robuste aux deux formes
# (avec ou sans accent sur « Critères »). En forme titre on capture les lignes suivantes jusqu'au
# prochain titre ; en forme inline on n'imprime que la ligne elle-même.
gl_issue_brief_render() {
  local iid="$1"
  awk -v iid="$iid" '
    ph == 1 {
      if (crit == 0 && $0 ~ /[Aa]cceptation/) {
        print ""; print $0
        if ($0 ~ /^#+[ \t]/) crit = 1
        next
      }
      if (crit && $0 ~ /^#+[ \t]/) crit = 0
      if (crit) print $0
      next
    }
    /^--$/ {
      printf "#%s — %s\n", iid, title
      if (labels != "") printf "labels: %s\n", labels
      ph = 1; next
    }
    /^title:/  { t = $0; sub(/^title:[ \t]*/,  "", t); title  = t; next }
    /^labels:/ { l = $0; sub(/^labels:[ \t]*/, "", l); labels = l; next }
  '
}

# --- Milestone de phase ---------------------------------------------------------------------------
# gl_current_milestone [rail] -> imprime le TITRE du milestone de la « phase courante » : le
# milestone ACTIF le plus ancien (tri par échéance croissante) qui porte encore du travail —
# c'est-à-dire ayant AU MOINS UN TICKET OUVERT. Deux milestones sont sautés, et pour deux raisons
# NOMMÉES SÉPARÉMENT sur stderr (#619) : le SOLDÉ (N fermés / N total) — la phase est finie, seule
# sa fermeture reste, décision humaine que doctor.sh suggère — et le VIDE (0 / 0) — la phase n'est
# pas découpée, parfois à dessein (docs/06-roadmap.md, « la Phase 9 reste un contenant vide à
# dessein »). La règle est volontairement indépendante des dates prévisionnelles des milestones :
# le réel peut être en avance sur elles. Sortie vide + code 1 si aucun candidat ; /ticket-create
# omet alors simplement --milestone à la création. Détail et prix assumé : gh_current_milestone.
#
# `rail` vaut « produit » (défaut) ou « outillage » et restreint la recherche aux milestones de ce
# rail — voir GL_RAIL_MOTIF. Le défaut est « produit » pour que tout appelant qui ne connaît pas
# encore les rails garde EXACTEMENT le comportement d'avant #617 sur un dépôt dont aucun milestone
# n'est marqué. Code 2 sur un rail inconnu, avant toute lecture de forge.
gl_current_milestone() {
  gh_current_milestone "$@"
}

# gl_milestones -> table plate des milestones du projet, du plus ancien au plus récent (tri par
# échéance croissante, comme gl_current_milestone). Sert à /milestone-presentation : choisir le
# milestone à présenter, et lever une ambiguïté quand l'utilisateur donne un fragment de titre.
#
# Sortie TSV (en-tête préfixée « # » ignorable par les consommateurs machine) :
#     titre <TAB> etat <TAB> debut <TAB> echeance <TAB> fermes <TAB> total <TAB> rail
# `etat` vaut `active`/`closed` ; une date absente vaut « - ». Le titre vient EN PREMIER parce
# qu'il est la clé (c'est lui qu'on repasse à gl_milestone_issues), et en dernier viennent les
# compteurs, de largeur fixe.
#
# ⚠ `rail` (#617) est ajouté EN DERNIÈRE POSITION, et pas auprès du titre où il se lirait mieux :
# les six premières colonnes sont un contrat, que `queue.sh` lit par leur RANG ($1, $4, $5, $6).
# Insérer une colonne au milieu ferait rendre à `milestones_traitables` l'échéance à la place du
# compte de tickets, sans rien casser de visible.
gl_milestones() {
  gh_milestones
}

# gl_milestone_issues <titre-exact> -> table plate des tickets d'un milestone, même modèle compact
# que gl_backlog_table (une ligne par ticket, projection awk sans dépendance à jq).
#
# Le titre doit être EXACT (c'est le filtre `milestoneTitle` de l'API) : la résolution d'un
# fragment (« Phase 3 ») est le travail de l'appelant, via gl_milestones. Un titre inconnu ne
# lève pas d'erreur côté API — il rend simplement zéro ticket, d'où le garde-fou ci-dessous.
#
# Sortie TSV (en-tête préfixée « # » ignorable) :
#     iid <TAB> statut <TAB> type <TAB> agent <TAB> prio <TAB> titre
# `statut` est le LIBELLÉ du cycle de vie (À faire / En cours / En revue / Terminé / Abandonné /
# Doublon — lu dans le champ Status, jamais un slug : contrat de surface en tête de fichier ; « - »
# si le ticket n'a pas d'état) ; `type`/`agent`/`prio` sont le suffixe nu du label (« feature »,
# « dev », « moyenne ») ; un champ absent vaut « - ». Les tickets sortent du plus récent au plus
# ancien (ordre de l'API) ; l'appelant regroupe et trie selon sa présentation.
gl_milestone_issues() {
  local title="$1"
  if [ -z "$title" ]; then echo "usage: gl_milestone_issues <titre-exact-du-milestone>" >&2; return 2; fi
  st_milestone_issues "$@"
}

# gl_milestone_rail <titre-exact> [produit|outillage] -> LIT le rail d'un milestone (sans second
# argument : imprime « produit » ou « outillage ») ou le POSE, en ajoutant ou retirant la ligne
# « rail: outillage » de sa description. Poser est idempotent : reposer le même rail ne réécrit rien
# et rend 0.
#
# Un seul verbe pour les deux gestes, à la différence de l'arbitrage des lots qui en a deux
# (`arbitrage` lit, `arbitre` écrit, #562) : là-bas la lecture rend quatre champs et trois codes de
# retour, ici elle rend un mot, et un verbe de lecture séparé pour un mot serait un nom de plus à
# retenir pour rien. L'écriture reste reconnaissable à son second argument.
#
# C'est un VERBE et non un `gh api ... --field description=…` recopié dans les prompts, pour la
# raison exacte qui a fait de l'arbitrage un verbe (#562) : `tests/test_cycle_de_vie.py` interdit
# les écritures de forge dans `.claude/commands/**`, et le support du marqueur peut bouger — un
# prompt qui appellerait `gh` directement serait à retrouver, un verbe non.
#
# La description existante est PRÉSERVÉE : elle porte le cadrage du milestone (parent de suivi,
# dépendances, décisions), et l'écraser pour poser un marqueur d'une ligne serait payer un rail au
# prix d'un bilan.
gl_milestone_rail() {
  local title="$1" rail="${2:-}" numero desc courant nouvelle
  if [ -z "$title" ]; then
    echo "usage: gl_milestone_rail <titre-exact-du-milestone> [produit|outillage]" >&2; return 2
  fi
  if [ -n "$rail" ] && ! gl_rail_valide "$rail"; then
    echo "gl_milestone_rail : rail inconnu « $rail » (attendu : produit | outillage)" >&2; return 2
  fi
  gh_require || return 1

  # LECTURE : par gl_milestones, qui porte déjà la colonne `rail`. Pas par une seconde requête à
  # soi — la règle du marqueur vivrait alors à deux endroits, et c'est le jalon d'un ticket qui
  # dépend de leur accord. Un aller, celui que la table fait déjà.
  courant="$(gl_milestones | awk -F'\t' -v cible="$title" '$1 == cible { print $7; exit }')"
  if [ -z "$courant" ]; then
    echo "gl_milestone_rail : aucun milestone intitulé « $title »" >&2; return 1
  fi
  if [ -z "$rail" ]; then
    printf '%s\n' "$courant"
    return 0
  fi

  # ÉCRITURE : en REST, la seule voie qui sache modifier la description d'un jalon.
  numero="$(gh api "repos/$GL_GH_REPO/milestones?state=all&per_page=100" \
    --jq ".[] | select(.title == \"$title\") | .number" 2>/dev/null | head -1)"
  if [ -z "$numero" ]; then
    echo "gl_milestone_rail : aucun milestone intitulé « $title »" >&2; return 1
  fi
  desc="$(gh api "repos/$GL_GH_REPO/milestones/$numero" --jq '.description // ""' 2>/dev/null)"
  if [ "$courant" = "$rail" ]; then
    printf 'milestone « %s » : déjà sur le rail « %s » — rien à écrire.\n' "$title" "$rail"
    return 0
  fi

  if [ "$rail" = outillage ]; then
    # Le marqueur va EN TÊTE : une description longue se lit tronquée dans l'interface, et un
    # marqueur qu'on ne voit qu'en déroulant est un marqueur qu'on croit absent.
    nouvelle="rail: outillage"
    [ -n "$desc" ] && nouvelle="$nouvelle
$desc"
  else
    nouvelle="$(printf '%s' "$desc" | grep -Eiv "^[[:space:]]*${GL_RAIL_MOTIF}[[:space:]]*\$")"
  fi

  gh api --method PATCH "repos/$GL_GH_REPO/milestones/$numero" \
    --field description="$nouvelle" >/dev/null || {
      echo "gl_milestone_rail : échec de l'écriture sur « $title »" >&2; return 1; }
  printf 'milestone « %s » : rail « %s » → « %s ».\n' "$title" "$courant" "$rail"
}

# --- Sous-tickets (découpage parent / lots) -------------------------------------------------------
# Convention (docs/10-workflow-git.md §5.1) : un besoin qui dépasse ~1 session de travail est porté
# par un ticket PARENT de suivi dont la description contient une section « ## Sous-tickets » :
# checklist ORDONNÉE « - [ ] #<iid> — <titre> » (ordre de réalisation, lot final tests+doc).
# Chaque sous-ticket commence sa description par « Sous-ticket de #<parent> » (marqueur parsé par
# gl_parent_of) et est lié au parent via un issue link « relates to » (gl_issue_link).
#
# MARQUEUR « (parallèle) » (ticket #160) — un lot dont le titre de checklist se termine par
# « (parallèle) » déclare qu'il **ne dépend pas** des autres lots parallèles qui le précèdent :
# deux personnes peuvent les prendre en même temps sans que /ticket-start n'en bloque un. Le
# marqueur est FACULTATIF, et son absence conserve le comportement séquentiel d'origine. D'où la
# règle de blocage, appliquée par gl_start_brief et gl_subtickets_startables :
#   un lot précédent non livré (ni « Terminé » ni « En revue ») bloque, SAUF si le lot visé ET ce
#   lot précédent portent tous deux le marqueur.
# Un lot NON marqué reste donc barré par tout ce qui le précède — c'est ce qui garde le lot final
# « tests + doc » derrière l'ensemble des lots, marqueurs compris.
#
# ================================================================================================
# DEUX SUPPORTS, UN COMMUTATEUR — `MAESTRO_LOTS=checklist|natif` (#390, chantier #389)
# ================================================================================================
# Tout ce qui précède décrit le support HISTORIQUE : de la PROSE, et rien d'autre. Le sens
# enfant → parent est un `grep` du marqueur « Sous-ticket de #N » dans le corps, le sens
# parent → enfants un parsing awk de la section « ## Sous-tickets ». GitHub porte nativement cette
# relation depuis les SUB-ISSUES (`Issue.parent`, `Issue.subIssues`), et #389 en fait le seul
# support ; ce lot-ci pose le second régime et le laisse ÉTEINT.
#
# LE COMMUTATEUR EST LA CLÉ DU DÉCOUPAGE, patron validé deux fois (#335 `MAESTRO_FORGE`, #358
# `MAESTRO_CYCLE`) : réécrire les lectures d'un bloc casserait `/ticket-start` et `/orchestrate`
# entre deux merges, puisque les 41 parents du dépôt ne sont pas encore rattachés nativement (c'est
# le lot 3). Le défaut reste donc `checklist`, la bascule est le lot 4, et le RETRAIT vient en
# dernier et seul (lot 6) — comme le retrait des labels de #365 : tant que la checklist est là, un
# retour arrière coûte une variable d'environnement.
#
# LE RÉGIME DÉCIDE, JAMAIS LA PRÉSENCE, et c'est le seul point de conception qui vaille d'être
# défendu. Il aurait été tentant de lire « nativement si des sub-issues existent, dans la prose
# sinon » : c'est exactement ce qui fabrique DEUX SUPPORTS ACTIFS EN MÊME TEMPS — un parent
# rattaché à moitié rendrait la moitié de ses lots, sans que rien ne le dise, et l'ordre des lots
# viendrait tantôt de la checklist tantôt de la priorisation native. Un régime inconnu est refusé
# AVANT toute lecture, comme l'effort de `run.sh` : le repli silencieux sur un régime est ce qu'on
# ne veut pas ici.
#
# CE QUE LE NATIF NE COÛTE PAS : UN SEUL ALLER. `parent` et `subIssues` sont demandés DANS la
# requête de `gh_issue_raw` (cf. son en-tête), donc la relation voyage dans la vue TEXTE canonique,
# en lignes d'en-tête `parent:` et `lot:`. C'est ce qui fait que les cinq verbes qui rejouent
# `gl_parent_marqueur`/`gl_subticket_rows` sur un ticket DÉJÀ LU — `gl_arbitrage_de`,
# `gl_ferme_parent`, `gl_demarre_parent`, `gl_start_brief`, et `queue.sh` sur sa vue en cache —
# basculent sans qu'une ligne y soit touchée, et sans payer une lecture de forge de plus. Le coût
# de lecture étant le risque technique du chantier (#389, note technique), le mesurer au lot 4
# revient à mesurer une requête plus grosse, jamais des allers en plus.
GL_LOTS_DEFAUT="${GL_LOTS_DEFAUT:-checklist}"

# LE MARQUEUR « (parallèle) » EST LE SEUL VRAI TROU DU CHANGEMENT DE SUPPORT (#389) : les
# sub-issues natives portent la relation, l'ordre et l'état, mais rien qui dise « ce lot ne dépend
# pas de celui d'avant ». Il ne peut PAS aller dans le titre du sous-ticket — `gl_branch_from_raw`
# en dérive le slug de branche, et un « (parallèle) » salirait tous les noms de branches. D'où un
# LABEL SCOPÉ, cohérent avec `type::`/`agent::`/`prio::` et avec le `lot::arbitre` de #562, qui dit
# déjà quelque chose du DÉCOUPAGE et non du ticket. Provisionné par `scripts/gitlab/bootstrap.sh`,
# lu par `gh_lots_natifs`, et posé sur le LOT (là où `lot::arbitre` se pose sur le parent).
GL_LABEL_LOT_PARALLELE="${GL_LABEL_LOT_PARALLELE:-lot::parallele}"

# gl_lots_regime -> « checklist » ou « natif ». Code 2 sur une valeur inconnue, avec son message :
# `MAESTRO_LOTS` est un ensemble FERMÉ de deux valeurs (même raison que l'effort de `run.sh`, et pas
# celle du nom de modèle, qui est une chaîne ouverte). L'échec remonte jusqu'à `gh_issue_raw`, donc
# une faute de frappe fait échouer la première lecture de ticket au lieu de traiter en silence le
# backlog dans le mauvais régime.
gl_lots_regime() {
  local regime="${MAESTRO_LOTS:-$GL_LOTS_DEFAUT}"
  case "$regime" in
    checklist|natif) printf '%s\n' "$regime" ;;
    *) echo "MAESTRO_LOTS : régime inconnu « $regime » (checklist|natif)" >&2; return 2 ;;
  esac
}

# gl_issue_link <iid-parent> <iid-lot> -> RATTACHE un lot à son parent (sub-issue native).
# Idempotent : un lot déjà rattaché à ce parent est un succès, comme un lien déjà posé l'était.
#
# LE VERBE A CHANGÉ DE FOND, PAS DE SIGNATURE (#391, chantier #389). Ce qu'il posait jusqu'ici était
# le pendant du « relates to » de GitLab, qui n'existe pas côté GitHub : un « #<n> » écrit dans un
# commentaire, dont la seule vertu est d'apparaître dans la chronologie du ticket cité. La relation
# elle-même n'était portée par personne. Les sub-issues la portent, dans les deux sens et sans prose.
# `gh_issue_link` reste en place : c'est l'implémentation de l'ère checklist, que le lot 6 (#395)
# retirera avec le reste de ce support, et non un repli qu'on garderait « au cas où ».
#
# SON UNIQUE APPELANT PASSE DÉJÀ LE COUPLE DANS CET ORDRE — `/ticket-create` écrit
# `issue-link <iid-parent> <iid-sous-ticket>` (docs/10 §5.1), et c'est la seule invocation du dépôt.
# La bascule ne réinterprète donc AUCUN appel existant : elle donne au même couple, dans le même
# sens, un support qui tient. Le seul changement observable est qu'un lot déjà rattaché AILLEURS
# échoue là où un commentaire de plus réussissait — c'est-à-dire qu'une anomalie cesse d'être muette.
#
# ⚠ ET CE N'EST PAS COMMUTÉ PAR `MAESTRO_LOTS`, à la différence des LECTURES de #390. La raison est
# dans l'ordre des lots, pas dans une préférence : le backfill des 41 parents (#392) tourne AVANT la
# bascule (#393), c'est-à-dire pendant que le régime par défaut est encore `checklist`. Un
# rattachement gouverné par le régime serait donc impossible à faire au moment exact où il doit
# avoir lieu. Rien ne s'y oppose, et ce n'est pas une entorse au principe « le régime décide, jamais
# la présence » : celui-ci tranche COMMENT ON LIT, et tant que le régime est `checklist` la relation
# native est écrite sans être lue. Écrire les deux supports est le propre d'une migration ; en lire
# deux est la panne que le principe interdit.
gl_issue_link() {
  local iid="$1" target="$2"
  if [ -z "$iid" ] || [ -z "$target" ]; then echo "usage: gl_issue_link <iid-parent> <iid-lot>" >&2; return 2; fi
  gl_subticket_add "$@"
}

# gl_parent_of <iid> -> imprime l'iid du ticket PARENT si <iid> est un sous-ticket (marqueur
# « Sous-ticket de #<parent> » dans sa description), rien (code 1) sinon.
gl_parent_of() {
  local iid="$1"
  if [ -z "$iid" ]; then echo "usage: gl_parent_of <iid>" >&2; return 2; fi
  local raw
  raw="$(gl_issue_raw "$iid")" || return 1
  printf '%s\n' "$raw" | gl_parent_marqueur
}

# gl_parent_marqueur — cœur du parsing de gl_parent_of, séparé pour être rejoué sur un ticket DÉJÀ
# LU (stdin = sortie de gl_issue_raw), exactement comme gl_subticket_rows l'est pour gl_subtickets.
# Imprime l'iid du parent, rien s'il n'y a pas de marqueur.
#
# CE QUE LA SÉPARATION ACHÈTE : `gl_parent_of` rend « rien » aussi bien pour un ticket sans parent
# que pour un ticket illisible, et gl_ferme_parent doit les distinguer — le premier est le cas
# nominal (la plupart des tickets ne sont pas des lots), le second une panne à ne pas annoncer comme
# une réponse. L'appelant lit donc le ticket lui-même et rejoue le marqueur sur ce qu'il a lu.
#
# EN RÉGIME NATIF, LE MARQUEUR EST UNE LIGNE D'EN-TÊTE (`parent:<TAB><iid>`), posée par
# `gh_issue_raw` depuis `Issue.parent` — pas une phrase du corps. La lecture est BORNÉE À L'EN-TÊTE
# (`/^--$/ { exit }`) et non lâchée sur tout le texte : un corps qui contiendrait une ligne
# « parent:	42 » — une note technique, un extrait de vue collé dans une description — rendrait
# sinon un parent qui n'existe pas, et c'est précisément le genre de faux positif que le support
# natif est censé supprimer.
gl_parent_marqueur() {
  local regime
  regime="$(gl_lots_regime)" || return 2
  if [ "$regime" = natif ]; then
    awk -F '\t' '/^--$/ { exit } $1 == "parent:" { print $2; exit }'
    return
  fi
  grep -o 'Sous-ticket de #[0-9]\+' | head -1 | grep -o '[0-9]\+$'
}

# gl_subtickets <iid-parent> -> liste ORDONNÉE des sous-tickets déclarés dans la checklist
# « ## Sous-tickets » du parent, enrichie du cycle de vie (une seule requête backlog, pas N).
# Sortie TSV : iid <TAB> coche(x|-) <TAB> statut <TAB> par(∥|-) <TAB> titre  (ligne d'en-tête « # »
# à ignorer). La colonne « par » porte le marqueur « (parallèle) », retiré du titre pour que le
# marqueur ne soit lu qu'à un seul endroit.
# Code 1 si le ticket n'a pas de section « ## Sous-tickets » (ce n'est pas un ticket parent) —
# c'est le test utilisé par /ticket-start pour détecter un parent de suivi.
gl_subtickets() {
  local iid="$1"
  if [ -z "$iid" ]; then echo "usage: gl_subtickets <iid-parent>" >&2; return 2; fi
  local raw rows
  raw="$(gl_issue_raw "$iid")" || return 1
  rows="$(printf '%s\n' "$raw" | gl_subticket_rows)"
  if [ -z "$rows" ]; then
    gl_pas_un_parent "$iid" >&2
    return 1
  fi
  printf '%s\n' "$rows" | gl_subtickets_enrich
}

# gl_pas_un_parent <iid> -> le message de « ce ticket n'est pas un parent de suivi », dans les mots
# du RÉGIME COURANT. Le verdict est le même des deux côtés (aucun lot), sa cause ne l'est pas :
# envoyer quelqu'un chercher une section « ## Sous-tickets » dans un dépôt qui lit les sub-issues
# natives, c'est le faire corriger le mauvais support — et l'inverse au lot 6, quand la section
# n'existera plus nulle part. Deux appelants, `gl_subtickets` et `gl_arbitrage`.
gl_pas_un_parent() {
  local iid="$1" regime
  regime="$(gl_lots_regime)" || return 2
  if [ "$regime" = natif ]; then
    printf 'Aucune sub-issue rattachée à #%s — pas un ticket parent.\n' "$iid"
  else
    printf 'Pas de section « ## Sous-tickets » dans #%s — pas un ticket parent.\n' "$iid"
  fi
}

# gl_subticket_rows — cœur du parsing de gl_subtickets, séparé pour être rejoué sur un ticket DÉJÀ
# LU (stdin = sortie brute de gl_issue_raw) : imprime les lignes de la checklist
# « ## Sous-tickets » en TSV brut (iid <TAB> coche(x|-) <TAB> par(∥|-) <TAB> titre), rien si la
# section est absente. gl_start_brief s'en sert pour détecter un parent de suivi sans relire le
# ticket. Le marqueur « (parallèle) » de fin de titre est extrait dans sa propre colonne : détection
# sur le titre minusculé et motif « parall[^)]* » plutôt qu'une classe [eè], parce qu'un awk orienté
# octets (mawk) ne sait pas faire tenir le « è » (2 octets en UTF-8) dans une classe de caractères.
#
# EN RÉGIME NATIF, LES LIGNES SONT DÉJÀ FAITES : `gh_issue_raw` a écrit un `lot:<TAB>…` par
# sub-issue dans l'en-tête de la vue, aux MÊMES QUATRE COLONNES. Ce verbe n'a donc plus qu'à les
# recueillir — et c'est tout le gain de faire porter la relation par la vue texte plutôt que par un
# appel réseau de plus : le contrat de sortie est le même au caractère près, si bien que
# `gl_subtickets_enrich`, `gl_subtickets_startables`, `gl_arbitrage_de` et `queue.sh` ne
# distinguent pas les deux régimes.
#
# La lecture est BORNÉE À L'EN-TÊTE, pour la raison de `gl_parent_marqueur` : un corps qui
# contiendrait une ligne « lot:	… » fabriquerait sinon des lots à partir d'une description.
gl_subticket_rows() {
  local regime
  regime="$(gl_lots_regime)" || return 2
  if [ "$regime" = natif ]; then
    # `substr($0, 6)` — après « lot: » et sa tabulation — plutôt que `$2, $3, $4, $5` : un titre
    # qui porterait une tabulation (aucun sur GitHub, mais le contrat ne le promet pas) serait
    # tronqué par la reconstruction, jamais par la coupe brute.
    awk -F '\t' '/^--$/ { exit } $1 == "lot:" { print substr($0, 6) }'
    return
  fi
  awk '
    insec {
      if ($0 ~ /^#+[ \t]/) { insec = 0; next }
      if ($0 ~ /^- \[[ xX]\] #[0-9]+/) {
        coche = ($0 ~ /^- \[[xX]\]/) ? "x" : "-"
        match($0, /#[0-9]+/)
        id = substr($0, RSTART + 1, RLENGTH - 1)
        titre = substr($0, RSTART + RLENGTH)
        sub(/^[-—–: \t]+/, "", titre)
        par = "-"
        if (tolower(titre) ~ /\([ \t]*parall[^)]*\)[ \t]*$/) {
          par = "∥"
          sub(/[ \t]*\([ \t]*[Pp]arall[^)]*\)[ \t]*$/, "", titre)
        }
        printf "%s\t%s\t%s\t%s\n", id, coche, par, titre
      }
      next
    }
    /^#+[ \t]+Sous-tickets/ { insec = 1 }
  '
}

# gl_subtickets_enrich — enrichit du CYCLE DE VIE les lignes TSV de gl_subticket_rows (stdin) et
# imprime la table finale « iid/coche/statut/par/titre » (une seule lecture, pas N).
# La colonne `statut` reprend telle quelle celle de gl_backlog_table : le LIBELLÉ (« À faire »),
# jamais le slug — c'est sur ces libellés que gl_subtickets_startables compare.
#
# ELLE DEMANDE LES LOTS PAR LEUR NUMÉRO (#577, docs/10 §3.6), et non plus le backlog entier. La
# question posée ici est unitaire — « quel est l'état de ces 8 lots ? » —, et la payer au prix de
# l'ensemble coûtait 6 allers (résolution du projet + 5 pages de 100 items) pour 1 aller utile :
# `subtickets`/`startables` sont les deux verbes les plus appelés d'un run (16 invocations sur 31
# au run 20260826-134119), à ~30 s pièce. Deux conséquences, et la seconde ne se voit pas au
# chronomètre : `gl_backlog_table` est bornée à `first: 100`, si bien que tout lot plus ancien que
# cette fenêtre sortait « ? » — donc jamais « À faire », donc jamais démarrable (mesuré : les cinq
# lots de #167, tous « Terminé », rendus « ? »). Voir l'en-tête de `st_statuts`.
#
# LA JOINTURE SE FAIT EN UN SEUL AWK et non par une recherche par ligne : la boucle d'avant payait
# un fork par lot pour relire la même table, ce qui sous MSYS (~120 ms le fork) coûtait plus cher
# que la lecture réseau qu'elle évitait.
#
# UNE LECTURE EN ÉCHEC N'EST PAS FATALE — comportement d'origine conservé : les lots sortent « ? »
# et l'appelant voit qu'aucun état n'est lisible, au lieu de perdre la checklist elle-même.
gl_subtickets_enrich() {
  local rows iids table
  rows="$(cat)"
  iids="$(printf '%s\n' "$rows" | awk -F '\t' 'NF && $1 ~ /^[0-9]+$/ { print $1 }' | tr '\n' ' ')"
  table=''
  # shellcheck disable=SC2086  # découpage en mots VOULU : st_statuts prend N iid en arguments.
  [ -n "${iids// /}" ] && { table="$(st_statuts $iids)" || table=''; }
  printf '# iid\tcoche\tstatut\tpar\ttitre\n'
  # La table voyage par ENVIRON et jamais par `awk -v`, qui interprète les échappements (#340).
  printf '%s\n' "$rows" | ST_TABLE="$table" awk -F '\t' -v OFS='\t' '
    BEGIN {
      n = split(ENVIRON["ST_TABLE"], lignes, "\n")
      for (i = 1; i <= n; i++) {
        p = index(lignes[i], "\t")
        if (p == 0) continue
        etat[substr(lignes[i], 1, p - 1)] = substr(lignes[i], p + 1)
      }
    }
    NF == 0 { next }
    { print $1, $2, (($1 in etat && etat[$1] != "") ? etat[$1] : "?"), $3, $4 }
  '
}

# gl_subtickets_startables — stdin = table enrichie de gl_subtickets SANS son en-tête. Imprime les
# lots « À faire » que la règle de blocage laisse démarrer **maintenant** (« #<iid> — <titre> »,
# suffixé « (parallèle) » pour les lots marqués), rien s'il n'en reste aucun. C'est ce qui permet à
# /ticket-start de proposer, sur un parent, TOUS les lots démarrables et plus seulement le premier.
gl_subtickets_startables() {
  awk -F '\t' '
    { iid[NR] = $1; statut[NR] = $3; par[NR] = $4; titre[NR] = $5; n = NR }
    END {
      for (i = 1; i <= n; i++) {
        if (statut[i] != "À faire") continue
        bloque = 0
        for (j = 1; j < i; j++) {
          if (statut[j] == "Terminé" || statut[j] == "En revue") continue
          if (par[i] == "∥" && par[j] == "∥") continue
          bloque = 1
          break
        }
        if (!bloque) printf "  #%s — %s%s\n", iid[i], titre[i], (par[i] == "∥" ? " (parallèle)" : "")
      }
    }
  '
}

# --- Arbitrage des lots parallélisables (#562, docs/10 §5.1) ------------------------------------
# Le marqueur « (parallèle) » est FACULTATIF (#160), et c'est ce qui rend son absence AMBIGUË : un
# lot non marqué veut dire « il dépend réellement de ce qui le précède » (l'intention du marqueur, et
# le cas du lot final « tests + doc ») ou « personne n'y a pensé », et rien ne les distingue. Un run
# part alors en séquentiel sans que ce séquentiel ait jamais été décidé.
#
# CE VERBE NE TRANCHE QUE LA MOITIÉ QU'UNE MACHINE PEUT TRANCHER — « ce parent a-t-il été
# arbitré ? » —, jamais « ces deux lots sont-ils indépendants ? », qui reste un jugement rendu une
# fois par parent. Marquer sans juger enverrait un lot partir d'une base incomplète : le sens sûr est
# le séquentiel (docs/10 §5.1), et une détection qui marquerait d'office ferait l'inverse.
#
# DEUX SOURCES POUR UNE SEULE QUESTION, ET AUCUNE N'EST DE TROP :
#   1. le label `lot::arbitre` sur le PARENT — posé par l'arbitrage QUEL QUE SOIT SON VERDICT ;
#   2. à défaut, « au moins un lot marqué » — la règle de FAIT, qui évite de signaler comme
#      non arbitrés les 25 parents arbitrés à la main avant que ce label n'existe.
# Sans la première, un parent dont la réponse juste est « aucun lot n'est parallélisable » ne
# pourrait JAMAIS être déclaré arbitré : il serait proposé à chaque run, pour toujours — le défaut
# symétrique de celui qu'on corrige, et la règle de `gc --auto` prise à l'envers (signaler
# l'abstention nominale apprend à ne plus lire les signalements).
GL_LABEL_ARBITRE="${GL_LABEL_ARBITRE:-lot::arbitre}"

# gl_arbitrage <iid-parent> -> « <verdict><TAB><marqués><TAB><lots><TAB><source> », source valant
# `label`, `marqueur` ou `-`.
# Codes : 0 = arbitré · 3 = jamais arbitré · 1 = pas un parent, ou lecture impossible. Le 3 plutôt
# qu'un 1, pour la raison de gl_lots_ouverts : « jamais arbitré » est une RÉPONSE, pas une panne.
#
# UNE SEULE LECTURE : `gl_issue_raw` rend les labels ET le corps, donc les deux sources se lisent
# dans la même réponse.
#
# gl_arbitrage_de — cœur du verdict, rejoué sur un ticket DÉJÀ LU (stdin = sortie de gl_issue_raw),
# exactement comme `gl_parent_marqueur` l'est pour `gl_parent_of` et `gl_subticket_rows` pour
# `gl_subtickets`. Ce n'est pas une commodité : `queue.sh` a déjà la vue de chaque parent de son plan
# en cache (`vue`), et sans cette moitié il paierait une lecture de forge par parent pour une
# question dont la réponse est dans un fichier qu'il vient d'écrire.
gl_arbitrage_de() {
  local raw rows labels nb_lots nb_marques verdict source
  raw="$(cat)"
  rows="$(printf '%s\n' "$raw" | gl_subticket_rows)"
  [ -n "$rows" ] || return 1
  # Comptage en awk et non en `wc -l` : `printf '%s\n' ""` compte une ligne pour zéro résultat, et
  # « 1 lot marqué » au lieu de « 0 » rendrait arbitré exactement le parent qu'on cherche.
  nb_lots="$(printf '%s\n' "$rows" | awk 'END { print NR }')"
  nb_marques="$(printf '%s\n' "$rows" | awk -F '\t' '$3 == "∥" { n++ } END { print n + 0 }')"
  labels="$(printf '%s\n' "$raw" | sed -n 's/^labels:[[:space:]]*//p' | head -1)"

  source="-"
  case ",$(printf '%s' "$labels" | tr -d '[:space:]')," in
    *",$GL_LABEL_ARBITRE,"*) source="label" ;;
  esac
  if [ "$source" = "label" ]; then verdict="arbitré"
  elif [ "$nb_marques" -gt 0 ]; then verdict="arbitré"; source="marqueur"
  else verdict="jamais"
  fi

  printf '%s\t%s\t%s\t%s\n' "$verdict" "$nb_marques" "$nb_lots" "$source"
  [ "$verdict" = "arbitré" ] || return 3
  return 0
}

gl_arbitrage() {
  local iid="$1"
  if [ -z "$iid" ]; then echo "usage: gl_arbitrage <iid-parent>" >&2; return 2; fi
  local raw code
  raw="$(gl_issue_raw "$iid")" || return 1
  printf '%s\n' "$raw" | gl_arbitrage_de
  code=$?
  if [ "$code" = 1 ]; then
    gl_pas_un_parent "$iid" >&2
  fi
  return "$code"
}

# gl_arbitre <iid-parent> -> ENREGISTRE que l'arbitrage a eu lieu, en posant `lot::arbitre`.
# Codes : 0 = posé (ou déjà là) · 1 = pas un parent, ou échec d'écriture.
#
# C'EST UN VERBE, ET PAS UN `gh issue edit --add-label` DANS UN PROMPT, pour deux raisons dont une
# seule est visible tout de suite. La visible : `tests/test_cycle_de_vie.py` interdit `--add-label`
# dans `.claude/commands/**`, parce que c'est par là qu'un prompt remettrait le CYCLE DE VIE sur
# l'issue, à côté de `set-workflow` — la garde est plus large que son motif, et elle a raison de
# l'être. L'autre : #389 fera passer le marqueur `(parallèle)` au label `lot::parallele`, donc
# l'écriture de l'arbitrage bougera ; un prompt qui appelle `gh` directement serait à retrouver, un
# verbe non.
#
# IL NE POSE RIEN SUR UN NON-PARENT : le label dit quelque chose du DÉCOUPAGE, et le poser sur un
# ticket ordinaire en ferait une décoration que la lecture prendrait pour un fait.
gl_arbitre() {
  local iid="$1"
  if [ -z "$iid" ]; then echo "usage: gl_arbitre <iid-parent>" >&2; return 2; fi
  local avant
  avant="$(gl_arbitrage "$iid")" || [ $? = 3 ] || return 1
  gh_add_label "$iid" "$GL_LABEL_ARBITRE" || return 1
  printf '#%s : arbitrage enregistré (« %s »)%s\n' "$iid" "$GL_LABEL_ARBITRE" \
    "$(printf '%s' "$avant" | awk -F '\t' '$2 == 0 { printf " — aucun lot parallélisable" }')"
}

# --- ÉCRITURE DU DÉCOUPAGE : rattacher et ordonner les lots (#391, chantier #389) -----------------
# Le pendant en ÉCRITURE de ce que #390 a appris à lire. Deux mutations, vérifiées disponibles sur
# ce dépôt : `addSubIssue` rattache, `reprioritizeSubIssue` ordonne. Ce que la convention faisait
# avec une phrase en tête de description et une ligne ajoutée à la main dans une checklist, la forge
# le porte désormais elle-même.
#
# L'ORDRE N'EST PAS DE L'AFFICHAGE, C'EST CE QUI FAIT LE PLAN. `queue.sh` garde les lots d'un parent
# CONTIGUS et dans l'ordre de la checklist, et `gl_subtickets_startables` juge « ce lot est-il
# démarrable ? » sur ce qui le PRÉCÈDE. Rattacher sans ordonner ne casserait donc pas l'affichage
# d'une page, mais l'orchestration : des lots rendus dans un ordre arbitraire feraient partir le
# dernier en premier, et le lot final « tests + doc » avant ce qu'il teste. D'où deux verbes, et
# non un.
#
# AUCUN DES DEUX N'EST COMMUTÉ PAR `MAESTRO_LOTS` — voir `gl_issue_link` ci-dessus, même raison : le
# backfill (#392) écrit pendant que le régime par défaut est encore `checklist`.

# gl_subticket_add <iid-parent> <iid-lot> -> rattache <iid-lot> comme sub-issue de <iid-parent>.
# Codes : 0 = rattaché, ou DÉJÀ rattaché à ce parent · 1 = ticket inexistant, lot déjà rattaché à un
# AUTRE parent, ou échec d'écriture · 2 = usage.
#
# IDEMPOTENT COMME `issue-link` L'ÉTAIT, ET PAR LE MÊME GESTE : on LIT avant d'écrire.
# `addSubIssue` rendrait bien une erreur sur un lot déjà parenté — il porte pour cela un
# `replaceParent` dont on ne se sert PAS, remplacer un parent en silence étant l'inverse de ce qu'on
# veut ici —, mais son message ne nomme pas le parent en place, et c'est précisément ce qu'il faut
# dire à qui rattache le mauvais lot. La lecture préalable rend les deux identifiants de nœud ET le
# parent courant en UN SEUL ALLER : le cas nominal coûte une lecture et une écriture, le cas
# déjà-rattaché une lecture seule.
gl_subticket_add() {
  local parent="$1" lot="$2"
  if [ -z "$parent" ] || [ -z "$lot" ]; then echo "usage: gl_subticket_add <iid-parent> <iid-lot>" >&2; return 2; fi
  case "$parent$lot" in
    *[!0-9]*) echo "gl_subticket_add : « $parent » et « $lot » doivent être des iid." >&2; return 2 ;;
  esac
  # Un ticket qui serait son propre lot rendrait un cycle, que rien en aval ne sait lire : la
  # question « quel lot précède celui-ci ? » n'aurait plus de fin. La forge le refuse aussi, mais
  # après un aller et dans ses mots à elle.
  if [ "$parent" = "$lot" ]; then
    echo "#$parent ne peut pas être son propre lot." >&2
    return 1
  fi
  gh_subticket_add "$parent" "$lot"
}

# gl_subticket_order <iid-parent> <iid…> -> impose aux lots nommés l'ordre où ils sont donnés.
# Codes : 0 = ordre posé, ou rien à ordonner · 1 = pas un parent, iid qui n'est pas un lot de ce
# parent, lot nommé deux fois, ou échec d'écriture · 2 = usage.
#
# CE QU'IL PROMET, EXACTEMENT : les lots nommés se retrouvent CONTIGUS et dans l'ordre donné, à la
# place qu'occupe le premier d'entre eux. Il ne prétend pas les pousser en tête, et c'est délibéré —
# un appelant qui ne nomme qu'une partie des lots déplacerait sinon ceux dont il n'a rien dit. Les
# deux appelants réels (le backfill #392, la création d'un parent) nomment TOUS les lots, et la
# distinction n'y paraît pas ; elle protège le troisième.
#
# « EN UNE PASSE » EST À PRENDRE AU PIED DE LA LETTRE. `reprioritizeSubIssue` ne déplace qu'UN lot à
# la fois, mais les champs de premier niveau d'une mutation GraphQL sont exécutés EN SÉRIE et dans
# l'ordre écrit : les N-1 déplacements voyagent donc dans un seul document, sous alias, en un seul
# aller. C'est la règle de #577/#602 — ne pas demander N fois ce qui se demande une fois — appliquée
# à une écriture, et la sérialisation est ici une garantie du langage, pas un pari sur la forge.
#
# RIEN N'EST ÉCRIT AVANT QUE TOUT SOIT VALIDÉ, et c'est le point à ne pas défaire : un iid qui n'est
# pas un lot de ce parent, ou nommé deux fois, arrête le verbe AVANT la mutation. Un ordre posé à
# moitié serait pire que pas d'ordre du tout — il laisserait le plan dans un état que personne n'a
# voulu, sur un parent que plus rien ne repasse.
gl_subticket_order() {
  local parent="$1" iid
  if [ -z "$parent" ] || [ -z "$2" ]; then echo "usage: gl_subticket_order <iid-parent> <iid>…" >&2; return 2; fi
  shift
  case "$parent" in
    *[!0-9]*) echo "gl_subticket_order : « $parent » doit être un iid." >&2; return 2 ;;
  esac
  for iid in "$@"; do
    case "$iid" in
      ''|*[!0-9]*) echo "gl_subticket_order : « $iid » n'est pas un iid." >&2; return 2 ;;
    esac
  done
  # UN SEUL LOT N'A PAS D'ORDRE : l'ordre est une relation entre au moins deux éléments, et vérifier
  # qu'il appartient bien à ce parent est la question de `subtickets`, pas celle-ci. On s'abstient
  # donc SANS parler à la forge — ce qui rend triviale la boucle du backfill (#392), qui rencontrera
  # des parents à un lot sans avoir à les distinguer.
  if [ "$#" = 1 ]; then
    printf '#%s : un seul lot (#%s) — rien à ordonner.\n' "$parent" "$1"
    return 0
  fi
  gh_subticket_order "$parent" "$@"
}

# --- Ce qui touche « .claude/ » : la moitié AMONT du reste à appliquer (#612, docs/10 §11.7) -----
# Une session autonome ne peut pas écrire sous `.claude/` — blocage dur du CLI, EN AMONT de
# l'`allow`, déduit par #229 puis mesuré par #238 —, donc elle rend son correctif dans la
# description de sa PR (#188). Depuis #418/#419 cette PR est mergée sans que personne ne l'ouvre :
# le résidu ne disparaît pas, il devient INVISIBLE, ce qui est pire — rien n'échoue, rien n'est
# rouge, le run se solde vert. docs/10 §11.7 tire depuis #238 la conclusion qui va avec (« Mieux
# vaut ne pas l'y envoyer […] **`queue.sh` ne le détecte pas** — c'est au rédacteur du ticket de le
# dire ») sans l'avoir jamais outillée. Ce verbe l'outille.
#
# CE VERBE NE TRANCHE QUE CE QUI EST CONNU À L'HEURE DU PLAN, limite assumée plutôt que masquée : il
# lit ce que le ticket dit de lui-même, jamais ce qu'un diff révélera — le cinquième emplacement de
# #599 a été trouvé par un balayage PENDANT le ticket, et aucune lecture de sa description ne
# pouvait l'annoncer. Il réduit la fréquence du résidu ; il ne le supprime pas, et c'est pourquoi
# #610/#611 traitent le cas où il ne voit rien.
#
# IL NE DÉCIDE RIEN NON PLUS : le verdict n'écarte aucun ticket d'aucun plan. Écarter est une
# décision, et le geste existe déjà — assigner le ticket, que le filtre « À faire ET libre » de
# `queue.sh` suffit à tenir dehors. Ce verbe DIT, comme tous les signalements du dépôt.
#
# --- LE MOTIF EST LARGE, ET C'EST UNE MESURE QUI L'A TRANCHÉ, PAS UN PRINCIPE (#612) -------------
# Il cherche `.claude/` dans TOUT ce que le ticket dit de lui-même — titre comme corps — et ne
# distingue pas un USAGE d'une MENTION. Distinguer serait de toute façon impossible ici : à l'heure
# du plan aucun fichier n'est écrit, il n'y a aucun diff à lire (même impossibilité que « déduire
# l'indépendance du contenu des lots », #562). Restait à savoir OÙ chercher, et l'intuition
# — « restreindre aux critères d'acceptation, comme le dit §11.7 » — est fausse.
#
# Mesure du 2026-08-27 sur les 120 tickets ayant des commits sur `main`, vérité établie par les
# FICHIERS de leurs commits (technique de #544 : tout commit porte `Refs`/`Closes #<iid>`) —
# 25 d'entre eux ont touché `.claude/` :
#
#     variante                          VP   FP   FN   précision   rappel
#     titre seul                         2    1   23        67 %      8 %
#     titre ou critères d'acceptation    9    1   16        90 %     36 %
#     partout, ≥ 3 lignes                8    2   17        80 %     32 %
#     partout, ≥ 2 lignes               13    4   12        76 %     52 %
#     partout  (ce qui est retenu)      17   10    8        63 %     68 %
#
# LES 8 QUE « TITRE OU CRITÈRES » RATE SONT EXACTEMENT LES GROS CHANTIERS D'OUTILLAGE — #344, #388,
# #417, #418, #460, #498, #562, #617 —, dont les critères parlent du COMPORTEMENT et jamais du
# fichier : un signalement qui laisse passer #418 et #562 ne sert à rien, et c'est ce qui écarte la
# variante la plus précise. Le seuil sur le nombre de lignes n'achète rien de mieux : il perd du
# rappel plus vite qu'il ne gagne en précision.
#
# LES 63 % SONT UN PLANCHER, et la mesure ne peut pas faire mieux : un ticket dont le résidu
# `.claude/` n'a JAMAIS été appliqué ne laisse aucun commit sous `.claude/`, donc compte ici comme
# un faux positif alors que le signalement était juste — c'est précisément le cas que #608
# documente (#599 et #595, résidus encore en place au moment de l'écrire).
#
# ET LE TROU EST MESURÉ, LUI AUSSI : 8 des 25 (32 %) ont touché `.claude/` sans que leur ticket le
# nomme NULLE PART (#361, #364, #365, #400, #414, #438, #455, #593). Aucun motif ne les attrapera,
# c'est la limite que #612 assume en prose et que ce chiffre chiffre — la raison d'être des lots
# #610 (le ticket de reprise à l'acte) et #611 (le filet en fin de run).
#
# La BARRE FINALE, elle, fait tout le tri utile : elle sépare le CHEMIN du mot « Claude »,
# omniprésent dans ce dépôt.
GL_CLAUDE_MOTIF="${GL_CLAUDE_MOTIF:-\.claude/}"

# gl_touche_claude_de — cœur du verdict, rejoué sur un ticket DÉJÀ LU (stdin = sortie de
# gl_issue_raw), exactement comme `gl_arbitrage_de` l'est pour `gl_arbitrage`. Ce n'est pas une
# commodité : `queue.sh` a déjà la vue de chaque candidat de son plan en cache, et sans cette moitié
# il paierait une lecture de forge PAR TICKET pour une question dont la réponse est dans un fichier
# qu'il vient d'écrire.
# Rend « <verdict><TAB><lignes> », verdict valant `touche` ou `-`.
# Codes : 0 = le ticket nomme `.claude/` · 3 = il ne le nomme pas. Le 3 plutôt qu'un 1, pour la
# raison de gl_arbitrage_de : « il ne le nomme pas » est une RÉPONSE, pas une panne.
gl_touche_claude_de() {
  local n
  n="$(grep -c -e "$GL_CLAUDE_MOTIF")" || n=0
  if [ "${n:-0}" -gt 0 ]; then
    printf 'touche\t%s\n' "$n"
    return 0
  fi
  printf -- '-\t0\n'
  return 3
}

# gl_touche_claude <iid> -> le même verdict sur un ticket qu'on lit pour l'occasion. UNE lecture.
gl_touche_claude() {
  local iid="$1"
  if [ -z "$iid" ]; then echo "usage: gl_touche_claude <iid>" >&2; return 2; fi
  local raw
  raw="$(gl_issue_raw "$iid")" || return 1
  printf '%s\n' "$raw" | gl_touche_claude_de
}

# --- Fermeture du parent (#515, docs/10 §5.1) ---------------------------------------------------
# Un parent de suivi ne porte ni branche ni code : aucune PR ne le ferme par un `Closes #`, et sa
# fermeture était le SEUL geste du cycle d'un chantier resté manuel — §5.1 la décrivait comme « une
# décision humaine/orchestrateur ». Depuis #418/#419 un run se solde TOUT MERGÉ : les lots d'un
# parent se ferment tous dans la foulée du run, et il ne reste plus personne pour fermer le parent.
#
# Le déclencheur n'est pas à inventer, il est câblé depuis #377 : `issues: closed` passe à chaque
# fermeture de ticket, quels que soient l'auteur du merge et la machine d'où il vient.
# `scripts/github/ticket-ferme.sh` y pose « Terminé » ; il y pose désormais la seconde question,
# celle de ces deux verbes — « ce lot était-il le dernier de son parent ? ».

# gl_lots_ouverts <iid-parent> -> les lots de la checklist « ## Sous-tickets » encore OUVERTS,
# « <iid><TAB><titre> », dans l'ordre de la checklist.
# Codes : 0 = aucun, le parent est soldé · 3 = il en reste (listés sur stdout) · 1 = pas un parent,
# ou lecture impossible. Le 3 plutôt qu'un 1 : « il reste des lots » est une RÉPONSE, pas une panne,
# et l'appelant n'a pas à trancher entre les deux sur la même valeur.
#
# LA MESURE EST L'OUVERT/FERMÉ DU LOT, JAMAIS SON CYCLE DE VIE. C'est ce qui rend vrai, sans une
# ligne pour lui, « un lot abandonné compte comme soldé » : « Abandonné » et « Doublon » ferment le
# ticket au même titre que « Terminé ». Prendre le cycle de vie ferait l'inverse — un chantier dont
# un lot a été abandonné garderait son parent ouvert pour toujours — et le ferait dépendre d'une
# pose qui, sur le lot qu'on vient de fermer, n'a pas encore eu lieu.
#
# LA COCHE NE DÉCIDE DE RIEN NON PLUS : elle est tenue au fil de l'eau par /ticket-ship, donc
# best-effort. Un parent dont la checklist a pris du retard doit se fermer quand même.
gl_lots_ouverts() {
  local iid="$1"
  if [ -z "$iid" ]; then echo "usage: gl_lots_ouverts <iid-parent>" >&2; return 2; fi
  local raw rows ids etats restants
  raw="$(gl_issue_raw "$iid")" || return 1
  rows="$(printf '%s\n' "$raw" | gl_subticket_rows)"
  if [ -z "$rows" ]; then
    echo "Pas de section « ## Sous-tickets » dans #$iid — pas un ticket parent." >&2
    return 1
  fi
  ids="$(printf '%s\n' "$rows" | cut -f1 | tr '\n' ' ')"
  # shellcheck disable=SC2086  # la liste d'iid est délibérément éclatée en autant d'arguments.
  etats="$(gh_issues_state $ids)" || return 1
  # Les états voyagent par l'ENVIRONNEMENT et non par `awk -v` : `-v` interprète les échappements de
  # sa valeur, et une donnée qui traverse ce décodage n'est plus la donnée (piège de #340).
  restants="$(printf '%s\n' "$rows" | MAESTRO_ETATS="$etats" awk -F '\t' '
    BEGIN {
      n = split(ENVIRON["MAESTRO_ETATS"], lignes, "\n")
      for (i = 1; i <= n; i++) { split(lignes[i], champs, "\t"); etat[champs[1]] = champs[2] }
    }
    # Un lot dont la réponse ne porte aucun état (ticket supprimé, réponse partielle) est compté
    # OUVERT : fermer un parent sur une donnée manquante ne se rattrape pas tout seul.
    etat[$1] != "closed" { printf "%s\t%s\n", $1, $4 }
  ')"
  if [ -z "$restants" ]; then return 0; fi
  printf '%s\n' "$restants"
  return 3
}

# gl_ferme_parent [--check] <iid> -> <iid> est un LOT qui vient de se fermer : si c'était le dernier
# encore ouvert de son parent, ferme le parent (« completed ») et y poste un commentaire disant sur
# quoi la fermeture s'appuie.
# Codes : 0 = parent fermé (ou, en --check, le serait) · 3 = abstention NOMMÉE (pas un lot, parent
# déjà fermé, lots encore ouverts) · 1 = échec.
#
# POURQUOI LE LOT EN ARGUMENT ET PAS LE PARENT : c'est ce que l'appelant a. L'événement
# `issues: closed` porte le ticket qui vient de se fermer et rien d'autre.
#
# L'ABSTENTION EST LE CAS NOMINAL, et c'est ce qui gouverne le coût : ce verbe passe à CHAQUE
# fermeture de ticket du dépôt, dont l'immense majorité ne sont pas des lots. Sur ce cas-là il ne
# fait qu'UNE lecture — celle qui répond « ce ticket a-t-il un parent ? ».
#
# IL NE POSE AUCUN CYCLE DE VIE, et n'a pas à le faire : la fermeture qu'il déclenche repasse par
# `issues: closed`, donc par la pose de « Terminé » de #377. C'est aussi ce qui borne la récursion à
# deux passages — au second, le parent n'a plus de parent (ou en a un dont il reste des lots).
gl_ferme_parent() {
  local check=0
  while [ "${1:-}" = "--check" ]; do check=1; shift; done
  local iid="${1:-}"
  if [ -z "$iid" ]; then echo "usage: gl_ferme_parent [--check] <iid>" >&2; return 2; fi

  # UN TICKET ILLISIBLE EST UNE ABSTENTION (3) ET NON UN ÉCHEC (1), et l'asymétrie avec le reste de
  # ce verbe est le contenu de la décision : ici on ne sait même pas s'il y a quelque chose à faire
  # — la question est « ce ticket est-il un lot ? », et sans réponse le repli (le parent reste
  # ouvert, quelqu'un le ferme à la main) est exactement l'état d'avant #515. Rougir un run
  # d'Actions pour ça mettrait un voyant rouge sur la moitié des fermetures de tickets le jour d'un
  # hoquet d'API. Une fois qu'on SAIT qu'il y a un parent, en revanche, l'échec se propage : voir
  # plus bas, où des lots illisibles rendent 1.
  local raw parent
  if ! raw="$(gl_issue_raw "$iid")"; then
    echo "Ticket #$iid illisible — parent non recherché." >&2
    return 3
  fi
  parent="$(printf '%s\n' "$raw" | gl_parent_marqueur)"
  if [ -z "$parent" ]; then
    printf "#%s n'est pas un lot (aucun parent déclaré) — rien à fermer.\n" "$iid"
    return 3
  fi

  # L'état du parent AVANT sa description : un parent déjà fermé n'a pas à faire lire sa checklist,
  # et surtout pas à recevoir un second commentaire. Le cas se produit dès qu'un chantier finit par
  # deux lots fermés coup sur coup — le second repasse ici quand le premier a déjà tout soldé.
  # Capture PUIS découpe : `gh_issues_state | cut` rendrait le code de `cut`, toujours 0 — un parent
  # illisible passerait alors pour un parent ouvert, et on lui lirait sa checklist pour rien.
  local petat brut
  brut="$(gh_issues_state "$parent")" || return 1
  petat="${brut##*$'\t'}"
  if [ "$petat" = "closed" ]; then
    printf 'Parent #%s déjà fermé — rien à faire.\n' "$parent"
    return 3
  fi

  local restants rc
  restants="$(gl_lots_ouverts "$parent")"; rc=$?
  case "$rc" in
    1) echo "Lots de #$parent illisibles — le parent est laissé ouvert." >&2; return 1 ;;
    3)
      printf 'Parent #%s : %s lot(s) encore ouvert(s) — rien à fermer.\n' \
        "$parent" "$(printf '%s\n' "$restants" | wc -l | tr -d ' ')"
      printf '%s\n' "$restants" | awk -F '\t' '{ printf "  #%s — %s\n", $1, $2 }'
      return 3
      ;;
  esac

  if [ "$check" = 1 ]; then
    printf '  → #%s serait fermé : tous ses lots sont soldés (dernier : #%s).\n' "$parent" "$iid"
    return 0
  fi

  gh_issue_close "$parent" || return 1

  # Brouillon que personne ne relit : répertoire temporaire du système, pas .maestro/ (règle #234).
  local note
  if ! note="$(mktemp "${TMPDIR:-/tmp}/maestro-parent.XXXXXX")"; then
    echo "Parent #$parent fermé, mais son commentaire n'a pas pu être écrit." >&2
    return 0
  fi
  {
    printf 'Tous les lots de ce chantier sont soldés (dernier : #%s) — parent fermé automatiquement.\n\n' "$iid"
    printf 'Fermeture posée par `lib.sh ferme-parent` sur `issues: closed` (docs/10 §5.1, #515).\n'
    printf 'Un lot fermé sans être réalisé — « Abandonné », « Doublon » — y compte comme soldé : '
    printf 'une fermeture reste une fermeture.\n'
  } > "$note"
  # Le commentaire est le CONTEXTE de la fermeture, pas la fermeture : son échec se dit et ne
  # rougit rien. Rendre 1 ici ferait clignoter un run d'Actions pour un ticket correctement fermé.
  if ! gh_issue_note "$parent" "$note" >/dev/null; then
    echo "Parent #$parent fermé, mais son commentaire n'a pas pu être posté." >&2
  fi
  rm -f "$note"
  printf 'Parent #%s fermé : tous ses lots sont soldés (dernier : #%s).\n' "$parent" "$iid"
}

# --- Entrée en travail du parent (#517, docs/10 §5.1) -------------------------------------------
# L'AUTRE BOUT DU MÊME CYCLE. `gl_ferme_parent` ci-dessus solde le parent quand son dernier lot
# tombe ; celui-ci le fait entrer en travail quand son PREMIER lot démarre. Rien, jusqu'ici,
# n'écrivait jamais l'état d'un parent de suivi : `/ticket-create` le pose « À faire »,
# `/ticket-start` REFUSE de le démarrer (il ne porte ni branche ni code) et `/ticket-ship` ne touche
# que le lot — si bien qu'un parent affichait « À faire » pendant que ses lots partaient un par un,
# et que depuis #419, où un run en solde plusieurs d'affilée, le board mentait sur tout un chantier
# à la fois.
#
# LE DÉCLENCHEUR EST `gl_begin`, ET C'EST LE SEUL À L'ÊTRE : « En cours » ne s'obtient QUE par
# /ticket-start, dont `gl_begin` est la mutation groupée — donc le point de passage OBLIGÉ de ses
# deux appelants, session interactive et session de run. Câbler les prompts en ferait deux à tenir
# d'accord, et une session autonome lit le prompt en dernier : c'est lui qui l'emporterait. Même
# raisonnement que la greffe de `reconcile-workflow` dans `worktree.sh gc` plutôt que dans ses trois
# points de passage (#275).
#
# ET IL N'Y A PAS DE FILET DERRIÈRE, à la différence de « Terminé » — choix, pas oubli. Celui-là
# naît d'un merge côté forge, donc pouvait arriver sans qu'aucune machine ne regarde, d'où le
# ramassage de #275 puis l'événement de #377. Ici la pose est SUR le chemin qui crée l'événement :
# un `reconcile` n'aurait rien à rattraper, et une dérive `doctor.sh` rien à diagnostiquer. À
# rouvrir seulement si un `set-workflow` manuel devenait courant.
#
# LE GESTE INVERSE N'EXISTE PAS NON PLUS : `gl_liberer_ticket` ne fait pas repasser le parent
# « À faire ». D'autres lots peuvent être en vol, et un parent dont un lot a démarré reste
# légitimement en travail — le seul retour du parent est sa fermeture (#515).

# gl_demarre_parent [--check] <iid> -> <iid> est un LOT qui vient de démarrer : si son parent est
# encore « À faire » (ou sans état), le passe « En cours ».
# Codes : 0 = posé (ou, en --check, le serait) · 3 = abstention NOMMÉE (pas un lot, parent déjà en
# travail ou soldé) · 1 = échec.
#
# POURQUOI LE LOT EN ARGUMENT ET PAS LE PARENT : même raison que `gl_ferme_parent`, c'est ce que
# l'appelant a. `/ticket-start` démarre un lot et ne sait rien du parent avant de l'avoir lu.
#
# LE FILTRE EST UNE LISTE BLANCHE SUR L'ÉTAT COURANT — « À faire », ou pas d'état du tout — et non
# une liste noire des cinq autres. Nous n'avons pas la main sur ce que porte le champ : une option
# renommée dans l'UI, un état ajouté demain, une lecture qui rend une valeur exotique passeraient
# tous une liste noire et écraseraient un parent « En revue » ou « Abandonné ». C'est la leçon de la
# liste blanche `completed` de scripts/github/ticket-ferme.sh. « Sans état » est DEDANS à dessein :
# un parent hors projet n'a aucun état à protéger, et l'écriture qui suit refusera d'elle-même en
# nommant sa cause (`st_set_workflow` sur un ticket hors projet).
#
# L'ABSTENTION EST LE CAS NOMINAL, et c'est ce qui gouverne le coût. Deux abstentions, deux prix :
# un ticket SANS PARENT ne coûte qu'UNE lecture — celle qui répond « pas de parent » —, et un parent
# DÉJÀ « En cours » en coûte une seconde mais AUCUNE écriture. Ce second cas est celui de tous les
# lots à partir du deuxième, c'est-à-dire de la majorité des démarrages d'un chantier.
gl_demarre_parent() {
  local check=0
  while [ "${1:-}" = "--check" ]; do check=1; shift; done
  local iid="${1:-}"
  if [ -z "$iid" ]; then echo "usage: gl_demarre_parent [--check] <iid>" >&2; return 2; fi

  # UN TICKET ILLISIBLE EST UNE ABSTENTION (3) ET NON UN ÉCHEC (1), pour la raison exacte donnée
  # dans gl_ferme_parent : la question posée ici est « ce ticket est-il un lot ? », et sans réponse
  # le repli — le parent reste « À faire », quelqu'un le pousse à la main — est l'état d'avant #517.
  # Une fois qu'on SAIT qu'il y a un parent, en revanche, l'échec se propage : voir plus bas.
  local raw parent
  if ! raw="$(gl_issue_raw "$iid")"; then
    echo "Ticket #$iid illisible — parent non recherché." >&2
    return 3
  fi
  parent="$(printf '%s\n' "$raw" | gl_parent_marqueur)"
  if [ -z "$parent" ]; then
    printf "#%s n'est pas un lot (aucun parent déclaré) — rien à poser.\n" "$iid"
    return 3
  fi

  # Capture PUIS découpe, comme dans gl_ferme_parent : `gl_issue_owner | cut` rendrait le code de
  # `cut`, toujours 0, et un parent illisible passerait alors pour un parent SANS ÉTAT — c'est-à-dire
  # pour un parent à écrire, ce qui est exactement le cas où il ne faut pas.
  local owner petat
  if ! owner="$(gl_issue_owner "$parent")"; then
    echo "État de #$parent illisible — « En cours » non posé." >&2
    return 1
  fi
  petat="${owner%%$'\t'*}"

  case "$petat" in
    ''|'À faire') ;;
    *)
      printf 'Parent #%s déjà « %s » — rien à poser.\n' "$parent" "$petat"
      return 3 ;;
  esac

  if [ "$check" = 1 ]; then
    printf '  → #%s passerait « En cours » (lot démarré : #%s).\n' "$parent" "$iid"
    return 0
  fi

  gl_set_workflow "$parent" "en-cours" >/dev/null || return 1
  printf 'Parent #%s → « En cours » (lot démarré : #%s).\n' "$parent" "$iid"
}

# --- Démarrage de ticket (/ticket-start : préflight + mutation groupée) --------------------------
# Deux helpers pour que /ticket-start remplace une dizaine d'allers-retours par deux (ticket #61) :
# gl_start_brief fait tout le préflight en UNE lecture du ticket, gl_begin pose assignation,
# statut et dates en UNE mutation. Les sous-commandes unitaires restent disponibles à côté.

# gl_start_brief <iid> -> préflight complet de /ticket-start en un appel et UNE SEULE lecture du
# ticket (un unique gl_issue_raw, rejoué pour toutes les projections ; autres lectures : le
# statut/assigné du ticket, et la checklist du parent si <iid> est un sous-ticket). Vérifie les
# pré-requis (gl_require), signale un arbre sale, puis imprime un bloc compact : titre/labels/
# critères (gl_issue_brief_render), la ligne « statut : … — libre / pris par … » (gl_issue_owner,
# avec ⚠ si le ticket est « En cours » chez quelqu'un d'autre), selon le cas marqueur sous-ticket
# (parent, rang « lot n/total », tests différés, contrôle du statut des lots précédents) ou
# checklist « ## Sous-tickets » (parent de suivi — qui ne porte ni branche ni code : pas de branche
# proposée dans ce cas), et enfin la branche proposée (gl_branch_prefix depuis le label type:: +
# gl_slug du titre).
# Informatif : les avertissements (ticket déjà pris, lot précédent non livré, label type:: absent)
# sont dans la sortie ; la décision — démarrer, rediriger, s'arrêter — reste à l'appelant. Code
# retour non nul seulement sur vrai échec (pré-requis, ticket introuvable) — l'arbre sale est
# depuis #181 un avertissement, pas un refus : le travail se fait dans le worktree du ticket.
gl_start_brief() {
  local iid="$1"
  if [ -z "$iid" ]; then echo "usage: gl_start_brief <iid>" >&2; return 2; fi
  gl_require || return 1
  # Arbre sale : AVERTISSEMENT, plus un refus (#181). Depuis que /ticket-start monte un worktree
  # par ticket, le travail ne se fait plus forcément ici : des changements non commités dans le
  # répertoire courant restent alors derrière nous, intacts et hors du chemin — les refuser
  # bloquerait le démarrage pour une saleté sans rapport avec le ticket. La décision revient à
  # l'appelant, qui seul connaît le verdict de `worktree.sh ensure` : bloquant si « ICI » (on
  # travaillerait DANS cet arbre), anodin si « WORKTREE ».
  local sales
  sales="$(git status --porcelain 2>/dev/null | grep -c .)" || sales=0
  if [ "${sales:-0}" -gt 0 ]; then
    printf '⚠ arbre de travail non propre : %s fichier(s) non commité(s) dans %s\n' \
      "$sales" "$(git rev-parse --show-toplevel 2>/dev/null || pwd)" >&2
    printf '  Sans objet si un worktree est monté pour ce ticket ; à trancher sinon.\n' >&2
  fi
  local raw
  raw="$(gl_issue_raw "$iid")" || return 1

  printf '%s\n' "$raw" | gl_issue_brief_render "$iid"

  # Cycle de vie + assigné (gl_issue_owner) : de quoi voir d'un coup d'œil si le ticket est LIBRE
  # ou DÉJÀ PRIS, sans avoir à lire les labels à la main. Avertissement explicite quand il est
  # « En cours » chez quelqu'un d'autre : /ticket-start doit s'arrêter là plutôt que de lui
  # retirer l'assignation en silence (gl_begin remplace la liste des assignés).
  local owner statut assignes moi
  owner="$(gl_issue_owner "$iid" 2>/dev/null)"
  IFS=$'\t' read -r statut assignes <<< "$owner"
  printf '\n'
  if [ -z "$owner" ]; then
    printf 'statut : ? — appartenance illisible (lecture de la forge en échec) : à vérifier à la main\n'
  elif [ -z "$assignes" ]; then
    printf 'statut : %s — libre (aucun assigné)\n' "${statut:-?}"
  else
    printf 'statut : %s — pris par : %s\n' "${statut:-?}" "$assignes"
    moi="$(gl_current_user 2>/dev/null)"
    if [ "$statut" = "En cours" ] && ! printf '%s' ",$assignes," | grep -q ",${moi:-__aucun__},"; then
      printf '⚠ déjà pris par %s — ne pas démarrer : le démarrer retirerait son assignation.\n' "$assignes"
      printf '  Reprise seulement sur demande explicite de la personne qui pilote.\n'
    fi
  fi

  # Parent de suivi ? (section « ## Sous-tickets » dans la description déjà lue)
  local rows
  rows="$(printf '%s\n' "$raw" | gl_subticket_rows)"
  if [ -n "$rows" ]; then
    local ptable startables
    ptable="$(printf '%s\n' "$rows" | gl_subtickets_enrich)"
    printf '\nparent de suivi — ne porte ni branche ni code ; rediriger vers un lot démarrable :\n'
    printf '%s\n' "$ptable"
    startables="$(printf '%s\n' "$ptable" | tail -n +2 | gl_subtickets_startables)"
    printf '\n'
    if [ -n "$startables" ]; then
      printf 'lots démarrables maintenant (les lots « parallèle » ne se bloquent pas entre eux) :\n'
      printf '%s\n' "$startables"
    else
      printf 'lots démarrables maintenant : aucun (tout est livré, en cours, ou bloqué par un lot précédent)\n'
    fi
    return 0
  fi

  # Sous-ticket ? (marqueur « Sous-ticket de #<parent> ») → rang de lot + contrôle des lots
  # précédents (ordre de la checklist du parent — ils doivent être livrés : « Terminé » ou
  # « En revue », les lots étant additifs et mergeables seuls depuis main ; ticket #63).
  local parent
  parent="$(printf '%s\n' "$raw" | grep -o 'Sous-ticket de #[0-9]\+' | head -1 | grep -o '[0-9]\+$')"
  if [ -n "$parent" ]; then
    local ptable total rank self_par blocked deferred
    ptable="$(gl_subtickets "$parent" 2>/dev/null | tail -n +2)"
    printf '\n'
    if [ -n "$ptable" ]; then
      total="$(printf '%s\n' "$ptable" | awk 'END { print NR }')"
      rank="$(printf '%s\n' "$ptable" | awk -F '\t' -v id="$iid" '$1 == id { print NR; exit }')"
      printf 'sous-ticket de #%s — lot %s/%s\n' "$parent" "${rank:-?}" "$total"
      # Marqueur « (parallèle) » du lot visé : il neutralise le blocage par les AUTRES lots
      # marqués qui le précèdent (voir la règle en tête de section). Un lot non marqué, lui,
      # reste barré par tout lot précédent non livré — marqueur compris.
      self_par="$(printf '%s\n' "$ptable" | awk -F '\t' -v id="$iid" '$1 == id { print $4; exit }')"
      [ "$self_par" = "∥" ] && printf 'lot marqué « parallèle » — indépendant des autres lots marqués du parent\n'
      blocked="$(printf '%s\n' "$ptable" | awk -F '\t' -v id="$iid" -v self_par="$self_par" '
        $1 == id { exit }
        $3 == "Terminé" || $3 == "En revue" { next }
        self_par == "∥" && $4 == "∥" { next }
        { printf "#%s (%s) ", $1, $3 }')"
      if [ -n "$blocked" ]; then
        printf 'lots précédents : ⚠ non livrés : %s— les terminer (au moins « En revue ») avant de démarrer ce lot\n' "$blocked"
      else
        printf 'lots précédents : OK (aucun lot bloquant — livrés ou marqués « parallèle »)\n'
      fi
    else
      printf 'sous-ticket de #%s (checklist du parent illisible — contrôler les lots précédents à la main)\n' "$parent"
    fi
    deferred="$(printf '%s\n' "$raw" | grep -o '[Tt]ests différés[^#]*#[0-9]\+' | head -1 | grep -o '[0-9]\+$')"
    if [ -n "$deferred" ]; then printf 'tests différés → #%s\n' "$deferred"; fi
  fi

  # Branche proposée : préfixe depuis le label type:: + slug du titre.
  local branche code
  branche="$(printf '%s\n' "$raw" | gl_branch_from_raw "$iid")"; code=$?
  if [ "$code" = 0 ]; then
    printf '\nbranche proposée : %s\n' "$branche"
  else
    printf '\nbranche proposée : %s (label type:: absent — préfixe à déduire : feat|fix|chore|docs)\n' "$branche"
  fi
}

# gl_begin <iid> [username] -> démarrage groupé du ticket : assignation (username auto-résolu si
# absent) + cycle de vie « En cours » + dates début/échéance (mêmes règles que gl_start_dates :
# début = aujourd'hui conservé si déjà posé — idempotent —, échéance = début + délai selon prio::).
# La liste des assignés est REMPLACÉE, sémantique voulue au démarrage : le ticket passe à celui qui
# le démarre — c'est aussi ce qui oblige /ticket-start à refuser un ticket déjà pris (gl_issue_taken).
#
# LES TROIS ÉCRITURES NE SE GROUPENT PAS, et c'est la contrepartie du champ : l'état vit sur l'item
# de projet, l'assignation sur l'issue, si bien que la paire « En cours + assigné » que regarde le
# filtre de queue.sh n'est plus indivisible comme elle l'était sous `PATCH /issues/:n`. D'où l'ordre
# d'écriture de st_begin — l'état D'ABORD, voir son en-tête. Les dates, elles, ont toujours été un
# appel à part : elles n'ont pas de domicile natif sur GitHub et vivent dans le commentaire de
# suivi maison.
#
# LA QUATRIÈME ÉCRITURE N'EST PAS SUR CE TICKET-CI (#517) : démarrer un LOT fait entrer son parent
# de suivi en travail. Elle est greffée ici et pas dans le prompt de /ticket-start parce que ce
# verbe est le point de passage obligé des deux appelants — voir l'en-tête de `gl_demarre_parent`.
gl_begin() {
  local iid="$1" user="${2:-}"
  if [ -z "$iid" ]; then echo "usage: gl_begin <iid> [username]" >&2; return 2; fi
  st_begin "$@" || return $?

  # BEST-EFFORT, au même titre que les dates ci-dessus et que `sync-main` (docs/10 §9.3) : le ticket
  # est démarré, sa branche est là, et un board en retard d'un état ne vaut pas de refuser un
  # démarrage. L'échec se DIT — avec le geste qui le rattrape — et ne remonte pas.
  #
  # L'ABSTENTION NOMINALE EST MUETTE ICI, alors que le verbe la NOMME, et l'asymétrie est voulue :
  # appelé seul, il doit dire pourquoi il n'a rien fait ; appelé sur CHAQUE démarrage, un « #517
  # n'est pas un lot » ajouterait une ligne de bruit à l'immense majorité des tickets, qui n'en sont
  # pas — et une ligne qu'on apprend à ne plus lire est une ligne qui ne dira rien le jour où elle
  # portera autre chose.
  if [ "${MAESTRO_PARENT_EN_COURS:-1}" = 0 ]; then return 0; fi
  local sortie rc
  sortie="$(gl_demarre_parent "$iid" 2>&1)"; rc=$?
  case "$rc" in
    0) printf '%s\n' "$sortie" ;;
    3) ;;
    *)
      printf '%s\n' "$sortie" >&2
      printf '  ~ parent de #%s non passé « En cours » — rattrapage : bash scripts/gitlab/lib.sh demarre-parent %s\n' \
        "$iid" "$iid" >&2 ;;
  esac
}

# --- Dates & time tracking ----------------------------------------------------------------------
# Renseignés automatiquement le long du cycle de vie (voir docs/10-workflow-git.md §3.3) :
#   • date de début + échéance  → posées par /ticket-start (gl_start_dates)
#   • temps passé               → proposé puis loggé par /ticket-finish (gl_log_time)
# Tout passe par la mutation workItemUpdate, comme gl_set_workflow (widgets startAndDueDate / timeTracking).

# gl_prio <iid> -> imprime le label prio du ticket (« prio::haute » | « prio::moyenne » | « prio::basse »),
# vide si absent.
gl_prio() {
  local iid="$1"
  if [ -z "$iid" ]; then echo "gl_prio : iid manquant" >&2; return 2; fi
  gh_prio "$@"
}

# gl_prio_delay <prio> -> imprime le délai (jours) pour l'échéance. Accepte « haute » ou
# « prio::haute ». Défaut (moyenne ou priorité absente) = GL_DUE_DELAY_MOYENNE.
gl_prio_delay() {
  case "${1#prio::}" in
    haute) echo "$GL_DUE_DELAY_HAUTE" ;;
    basse) echo "$GL_DUE_DELAY_BASSE" ;;
    *)     echo "$GL_DUE_DELAY_MOYENNE" ;;
  esac
}

# gl_get_start_date <iid> -> imprime la date de début (YYYY-MM-DD) déjà posée sur le ticket,
# vide si aucune. Sert à /ticket-finish (calcul du temps écoulé) et à l'idempotence de gl_start_dates.
gl_get_start_date() {
  local iid="$1"
  if [ -z "$iid" ]; then echo "gl_get_start_date : iid manquant" >&2; return 2; fi
  gh_get_start_date "$@"
}

# gl_get_time_spent <iid> [--hors-import] -> imprime le temps total déjà loggé, en secondes (0 si
# aucun) ; avec `--hors-import`, seulement ce qui a été loggé depuis la bascule sur GitHub.
# C'est cette seconde forme qui sert l'idempotence de /ticket-finish (ne pas re-logger un cycle sur
# une ré-exécution) : sur un ticket importé, le total ne vaut zéro ni avant ni après (#400).
gl_get_time_spent() {
  local iid="$1"
  if [ -z "$iid" ]; then echo "gl_get_time_spent : iid manquant" >&2; return 2; fi
  gh_get_time_spent "$@"
}

# gl_elapsed_days <date-début YYYY-MM-DD> -> imprime le nombre de jours calendaires écoulés
# entre cette date et aujourd'hui (entier, plancher 0).
gl_elapsed_days() {
  local start="$1"
  if [ -z "$start" ]; then echo "gl_elapsed_days : date de début manquante" >&2; return 2; fi
  local s n d
  s="$(date -d "$start" +%s 2>/dev/null)" || { echo "gl_elapsed_days : date invalide « $start »" >&2; return 1; }
  n="$(date +%s)"
  d=$(( (n - s) / 86400 ))
  [ "$d" -lt 0 ] && d=0
  printf '%s\n' "$d"
}

# gl_set_dates <iid> [début] [échéance] -> pose le widget startAndDueDate (dates YYYY-MM-DD).
# Un argument vide laisse le champ correspondant inchangé ; au moins une date est requise.
gl_set_dates() {
  local iid="$1" start="$2" due="$3"
  if [ -z "$iid" ]; then echo "usage: gl_set_dates <iid> [début YYYY-MM-DD] [échéance YYYY-MM-DD]" >&2; return 2; fi
  if [ -z "$start" ] && [ -z "$due" ]; then echo "gl_set_dates : au moins une date (début ou échéance) requise" >&2; return 2; fi
  gh_set_dates "$@"
}

# gl_start_dates <iid> -> pose les dates au démarrage : début = aujourd'hui (conservé si déjà
# renseigné), échéance = début + délai dérivé de la priorité du ticket (gl_prio_delay). Idempotent :
# une ré-exécution garde la date de début d'origine et recalcule l'échéance.
gl_start_dates() {
  local iid="$1"
  if [ -z "$iid" ]; then echo "usage: gl_start_dates <iid>" >&2; return 2; fi
  local today start prio delay due
  today="$(date +%F)"
  start="$(gl_get_start_date "$iid")"
  [ -z "$start" ] && start="$today"
  prio="$(gl_prio "$iid")"
  delay="$(gl_prio_delay "$prio")"
  due="$(date -d "$start +$delay days" +%F 2>/dev/null)"
  if [ -z "$due" ]; then echo "gl_start_dates : calcul de l'échéance impossible (commande date indisponible ?)" >&2; return 1; fi
  gl_set_dates "$iid" "$start" "$due" || return 1
  printf '  (priorité %s → échéance à +%s j)\n' "${prio:-prio::moyenne (défaut)}" "$delay"
}

# gl_log_time <iid> <durée> [résumé] -> ajoute une entrée de temps passé au ticket.
# <durée> au format des unités GitLab (« 2h », « 1h 30m », « 1d »…), gardé tel quel pour que les
# 603 h d'historique importées et les entrées écrites après la bascule se comptent avec la même
# règle. ADDITIF : n'écrase jamais l'existant (cf. le suivi maison, en tête de fichier).
gl_log_time() {
  local iid="$1" dur="$2"
  if [ -z "$iid" ] || [ -z "$dur" ]; then echo "usage: gl_log_time <iid> <durée> [résumé]" >&2; return 2; fi
  gh_log_time "$@"
}

# --- Descriptions : lecture/écriture fidèles aux octets (ticket #141) ------------------------------
# Relire puis réécrire une description (cocher la checklist d'un parent, mettre à jour celle
# d'une PR) est un aller-retour à risque : il a corrompu #111 le 2026-07-22 en y repoussant du
# mojibake (« â€” » au lieu de « — », « Ã© » au lieu de « é »).
#
# La cause n'est PAS glab, qui émet du bon UTF-8 : c'est un consommateur qui re-décode les octets —
# typiquement `sys.stdin` de Python, en cp1252 sous Windows. Le piège précis :
#   PYTHONIOENCODING=utf-8 glab ... | python     <-- la variable s'applique à GLAB, pas à python :
#                                                    bash ne la propage pas au reste du pipeline,
#                                                    elle n'a donc AUCUN effet.
#   glab ... | PYTHONIOENCODING=utf-8 python     <-- correct (variable sur le lecteur)
#
# Ces helpers suppriment l'improvisation : tout reste en shell, qui est byte-transparent, donc les
# octets traversent inchangés quelles que soient la locale et la plateforme. Les commandes
# /ticket-start, /ticket-ship et /ticket-finish doivent passer par eux plutôt que d'inventer une
# lecture. Vérifier une correction d'encodage se fait par OCTETS, jamais à l'affichage : un terminal
# cp1252 réaffiche le mojibake de façon plausible (em-dash correct = e2 80 94).

# gl_json_string_field <champ> -> lit un JSON sur stdin, imprime la valeur DÉSÉCHAPPÉE du champ
# chaîne <champ>. Balayage awk sous LC_ALL=C : sûr en UTF-8, car les octets d'une séquence
# multi-octets valent tous >= 0x80 et ne peuvent donc jamais collisionner avec les délimiteurs
# ASCII (" et \) que l'on cherche. Le JSON de GitLab rend les non-ASCII en UTF-8 brut ; seuls
# \n, \", \\ et quelques &/</> (&/</>) sont échappés.
gl_json_string_field() {
  local champ="$1"
  if [ -z "$champ" ]; then echo "usage: gl_json_string_field <champ>" >&2; return 2; fi
  LC_ALL=C awk -v champ="$champ" '
    { buf = buf $0 }
    END {
      cle = "\"" champ "\":\""
      i = index(buf, cle)
      if (i == 0) exit 1
      p = i + length(cle); n = length(buf); out = ""
      while (p <= n) {
        c = substr(buf, p, 1)
        if (c == "\\") {
          e = substr(buf, p + 1, 1)
          if      (e == "n") out = out "\n"
          else if (e == "t") out = out "\t"
          else if (e == "r") out = out "\r"
          else if (e == "u") {
            hex = substr(buf, p + 2, 4)
            if      (hex == "0026") out = out "&"
            else if (hex == "003c") out = out "<"
            else if (hex == "003e") out = out ">"
            else                    out = out "\\u" hex   # échappement inconnu : laissé tel quel
            p += 6; continue
          }
          else out = out e            # \" \\ \/ … : le caractère littéral
          p += 2; continue
        }
        if (c == "\"") break
        out = out c
        p++
      }
      printf "%s", out
    }
  '
}

# gl_get_description <iid> -> la description du ticket <iid>, en UTF-8 intact, sur stdout.
gl_get_description() {
  local iid="$1"
  if [ -z "$iid" ]; then echo "usage: gl_get_description <iid>" >&2; return 2; fi
  gh_get_description "$@"
}

# gl_set_description <iid> <fichier> -> remplace la description du ticket <iid> par le contenu du
# fichier (UTF-8). L'écriture par argument est fidèle : bash est byte-transparent.
gl_set_description() {
  local iid="$1" fichier="$2"
  if [ -z "$iid" ] || [ -z "$fichier" ]; then echo "usage: gl_set_description <iid> <fichier>" >&2; return 2; fi
  if [ ! -f "$fichier" ]; then echo "fichier introuvable : $fichier" >&2; return 1; fi
  gh_set_description "$@"
}

# gl_get_mr_description <mr> -> la description de la PR <mr>, en UTF-8 intact, sur stdout.
gl_get_mr_description() {
  local mr="$1"
  if [ -z "$mr" ]; then echo "usage: gl_get_mr_description <mr>" >&2; return 2; fi
  gh_get_mr_description "$@"
}

# gl_set_mr_description <mr> <fichier> -> remplace la description de la PR <mr> par le fichier.
gl_set_mr_description() {
  local mr="$1" fichier="$2"
  if [ -z "$mr" ] || [ -z "$fichier" ]; then echo "usage: gl_set_mr_description <mr> <fichier>" >&2; return 2; fi
  if [ ! -f "$fichier" ]; then echo "fichier introuvable : $fichier" >&2; return 1; fi
  gh_set_mr_description "$@"
}

# gl_roundtrip_description <iid> -> validation REPRODUCTIBLE de la fidélité (ticket #141) : lit la
# description, la réécrit telle quelle, la relit, puis compare OCTET POUR OCTET. C'est la preuve
# qu'un aller-retour ne perd rien sur un texte à accents et em-dash. Sans effet de bord quand tout
# va bien : on réécrit un contenu identique. Code 0 si fidèle, 1 sinon.
gl_roundtrip_description() {
  local iid="$1"
  if [ -z "$iid" ]; then echo "usage: gl_roundtrip_description <iid>" >&2; return 2; fi
  local avant apres taille
  avant="$(mktemp)" || return 1
  apres="$(mktemp)" || { rm -f "$avant"; return 1; }
  if ! gl_get_description "$iid" > "$avant" || [ ! -s "$avant" ]; then
    echo "Description vide ou illisible pour #$iid" >&2
    rm -f "$avant" "$apres"; return 1
  fi
  if ! gl_set_description "$iid" "$avant" >/dev/null; then
    rm -f "$avant" "$apres"; return 1
  fi
  if ! gl_get_description "$iid" > "$apres"; then
    rm -f "$avant" "$apres"; return 1
  fi
  if cmp -s "$avant" "$apres"; then
    taille="$(wc -c < "$avant" | tr -d ' ')"
    printf 'Aller-retour fidèle sur #%s : %s octets identiques.\n' "$iid" "$taille"
    rm -f "$avant" "$apres"; return 0
  fi
  echo "ALLER-RETOUR INFIDÈLE sur #$iid — les octets ont changé :" >&2
  cmp "$avant" "$apres" >&2
  rm -f "$avant" "$apres"; return 1
}

# --- Création : PR et notes, depuis un FICHIER (#233) ---------------------------------------------
# Pourquoi ces helpers existent alors que la création de PR et de commentaire est DÉJÀ autorisée
# (docs/10-workflow-git.md §7.1) : la couche permissions de Claude Code découpe une commande sur ses
# SAUTS DE LIGNE et ne sait matcher aucune SUBSTITUTION `$(…)`. Or une description de PR fait par
# nature plusieurs lignes. La commande prescrite jusqu'ici par /ticket-finish était donc refusée
# telle quelle, et ses deux replis naturels l'étaient tout autant — `--description "$(cat f)"`, puis
# `D="$(cat f)"; gh pr create … "$D"`. 10 refus sur 8 sessions autonomes (#232, cause n°1), et
# toujours sur la DERNIÈRE action du ticket : tout est commité, rien ne le déclare.
#
# Le remède ne demande AUCUN droit nouveau — il rend matchable une commande déjà autorisée. Le texte
# voyage par FICHIER (écrit par l'outil Write, qui n'est pas une ligne de commande), l'appel reste
# plat et court, et c'est `Bash(bash scripts/gitlab/lib.sh:*)` qui le couvre. Le `$(cat …)` survit,
# mais à l'INTÉRIEUR du script, où aucune permission ne s'applique : c'est exactement le parti pris
# de gl_set_description / gl_set_mr_description, dont ceci est le pendant à la CRÉATION.

# gl_issue_title <iid> -> titre du ticket <iid>, UTF-8 intact, sur stdout. Même lecture REST +
# décodage octet-transparent que gl_get_description (le `title` du ticket précède celui du
# milestone dans la charge REST, donc la première occurrence est bien la bonne).
gl_issue_title() {
  local iid="$1" titre
  if [ -z "$iid" ]; then echo "usage: gl_issue_title <iid>" >&2; return 2; fi
  gh_issue_title "$@"
}

# gl_create_mr <iid> <fichier> [branche] -> ouvre la PR de <branche> (défaut : la branche courante)
# en DRAFT vers main, le TITRE lu depuis le ticket et la DESCRIPTION lue depuis le fichier. Imprime
# l'URL de la PR en dernière ligne.
# NE POSE AUCUN drapeau de suppression de branche : le `--remove-source-branch` de GitLab n'a pas
# d'équivalent par PR côté GitHub, où c'est le réglage de dépôt `delete_branch_on_merge` qui s'en
# charge pour toutes les PR à la fois (docs/10 §6, voir gh_create_pr).
# IDEMPOTENT : si une PR ouverte existe déjà pour la branche, sa description est mise à jour au lieu
# d'échouer — /ticket-finish peut donc être rejoué (reprise de session, second passage après un
# commit de plus) sans que la deuxième passe casse.
# Ne merge ni ne dé-draft jamais : passer une PR en « prête » reste un geste explicite.
gl_create_mr() {
  local iid="$1" fichier="$2" branche="${3:-}" mr titre sortie
  if [ -z "$iid" ] || [ -z "$fichier" ]; then
    echo "usage: gl_create_mr <iid> <fichier> [branche]" >&2; return 2
  fi
  if [ ! -f "$fichier" ]; then echo "fichier introuvable : $fichier" >&2; return 1; fi
  if [ ! -s "$fichier" ]; then echo "gl_create_mr : $fichier est vide — description requise" >&2; return 1; fi
  [ -n "$branche" ] || branche="$(git branch --show-current 2>/dev/null)"
  if [ -z "$branche" ]; then echo "gl_create_mr : branche courante indéterminable" >&2; return 1; fi
  case "$branche" in
    main|master) echo "gl_create_mr : refus d'ouvrir une PR depuis « $branche »" >&2; return 1 ;;
  esac

  # Une PR ouverte porte déjà cette branche : on met sa description à jour, on ne recrée pas.
  # Tout ce bloc — validations, idempotence, refus de partir de main — est COMMUN aux deux forges :
  # seule la création elle-même diffère, et c'est la seule chose qui soit déléguée.
  if mr="$(gl_mr_iid "$branche" 2>/dev/null)" && [ -n "$mr" ]; then
    gl_set_mr_description "$mr" "$fichier" >/dev/null || return 1
    printf 'PR #%s déjà ouverte pour « %s » — description mise à jour (aucune PR recréée).\n' "$mr" "$branche"
    gl_mr_url "$mr"
    return 0
  fi

  titre="$(gl_issue_title "$iid")" || return 1

  gh_create_pr "$iid" "$branche" "$titre" "$fichier"
}

# gl_mr_url <mr> -> l'URL web de la PR. Les deux forges ne l'écrivent pas pareil
# (« /-/merge_requests/<n> » contre « /pull/<n> »), et c'est la seule chose qui les sépare ici.
gl_mr_url() {
  local mr="$1"
  if [ -z "$mr" ]; then echo "usage: gl_mr_url <mr>" >&2; return 2; fi
  printf 'https://%s/%s/pull/%s\n' "$(gl_host)" "$GL_GH_REPO" "$mr"
}

# gl_issue_url <iid> -> l'URL web du TICKET. Dérivée et jamais relue de la réponse de création : le
# `html_url` d'une écriture est ce que la forge veut bien en dire, l'URL d'un ticket est une règle.
gl_issue_url() {
  local iid="$1"
  if [ -z "$iid" ]; then echo "usage: gl_issue_url <iid>" >&2; return 2; fi
  printf 'https://%s/%s/issues/%s\n' "$(gl_host)" "$GL_GH_REPO" "$iid"
}

# gl_issue_note <iid> <fichier> -> poste le contenu du fichier en COMMENTAIRE sur le ticket <iid>.
# Même raison d'être que gl_create_mr : un `-m "$(cat …)"` n'est pas matchable (#186).
gl_issue_note() {
  local iid="$1" fichier="$2"
  if [ -z "$iid" ] || [ -z "$fichier" ]; then echo "usage: gl_issue_note <iid> <fichier>" >&2; return 2; fi
  if [ ! -f "$fichier" ]; then echo "fichier introuvable : $fichier" >&2; return 1; fi
  if [ ! -s "$fichier" ]; then echo "gl_issue_note : $fichier est vide — rien à poster" >&2; return 1; fi
  gh_issue_note "$@"
}

# --- Le reste à appliquer sous `.claude/` devient un TICKET (#610, chantier #608) ------------------
# Une session autonome ne peut pas écrire sous `.claude/` : garde-fou du CLI, EN AMONT de l'`allow`
# comme des hooks — déduit par #229, mesuré par #238, re-mesuré par #614 trois semaines et une
# version de CLI plus tard. On ne le contourne pas : c'est ce qui empêche une boucle sans
# surveillance de réécrire les instructions que la boucle suivante exécutera. La conduite prescrite
# (#188) est donc de RENDRE le correctif intégral au lieu de l'appliquer.
#
# CE QUI A CASSÉ, ET QUAND. Cette conduite avait un lecteur tant qu'un humain mergeait. #418/#419 le
# lui ont retiré : le pilote merge, la PR se ferme, et la section « reste à appliquer » finit dans le
# corps d'une PR mergée que plus personne n'ouvre. Le résidu ne disparaît pas — il devient
# INVISIBLE, ce qui est pire : rien n'échoue, rien n'est rouge, le ticket passe « Terminé » et le run
# se solde vert. Mesure du run `20260827-094044` : trois tickets, DEUX résidus (#599 → PR #603,
# #595 → PR #605), tous deux mergés dans les vingt minutes, tous deux encore en place le lendemain.
#
# LE SUPPORT CHANGE, LA CONDUITE NON. La PR reste le lieu de la revue — #188 ne bouge pas, et le
# prompt de `run.sh` prescrit l'appel EN PLUS du rendu dans la PR, jamais à sa place. Ce que le
# ticket ajoute est la SURVIE : il vit après le merge, remonte dans `/backlog`, se démarre par
# `/ticket-start`, et aucun `Closes` qui ne le vise pas ne peut le fermer.
#
# QUATRE PROPRIÉTÉS, dont trois se déduisent de règles déjà écrites du dépôt :
#   - le corps voyage PAR UN FICHIER, jamais sur la ligne de commande — même raison que create-mr et
#     issue-note juste au-dessus (#233, docs/10 §11.7) ;
#   - le ticket naît ASSIGNÉ, donc hors de tout plan : `queue.sh` filtre sur « À faire ET libre », et
#     un run qui le prendrait se ferait refuser exactement la même écriture. C'est le mécanisme
#     d'exclusion que le dépôt emploie déjà, pas un label de plus ;
#   - il porte son ÉTAT dans la foulée (`project-add`) — depuis #365 le champ Status est le seul
#     support du cycle de vie, et rien ne rattrape un ticket qui naît sans état ;
#   - il est IDEMPOTENT : une session qui bute deux fois sur `.claude/` dans le même ticket n'ouvre
#     pas deux tickets de reprise.
#
# L'IDEMPOTENCE S'ANCRE SUR LE TICKET SOURCE, ET PAS SUR UNE RECHERCHE. L'API de recherche de GitHub
# est indexée de façon ASYNCHRONE — deux appels rapprochés y trouveraient « rien », donc deux
# tickets, c'est-à-dire le doublon que la propriété interdit. L'ancre est donc un COMMENTAIRE posé
# sur le ticket SOURCE, relu à l'appel suivant : même mécanisme que `gh_issue_link`, immédiatement
# lisible, et il rend au passage le lien visible dans les deux sens. Il est lu dans les COMMENTAIRES
# et jamais dans la description : celle-ci est écrite par un humain, et un ticket qui PARLE de
# tickets de reprise — le parent #608, par exemple — n'en a pas pour autant un.
#
# REJOUÉ, IL AJOUTE ET N'ÉCRASE PAS. Deux refus dans un même ticket, ce sont DEUX correctifs :
# remplacer le corps perdrait le premier, c'est-à-dire exactement ce que ce chantier veut sauver.
# Chaque correctif entre donc dans sa propre section, reconnue à l'EMPREINTE de son fichier
# (`cksum`) — rejoué à l'identique, le verbe ne réécrit rien du tout et le dit.
#
# CE QU'IL NE FAIT PAS EN MISE À JOUR : ni réassigner, ni reposer l'état. Un ticket de reprise déjà
# « En cours » chez quelqu'un ne doit pas retomber « À faire » parce que la session source a buté une
# seconde fois — même raison que `reconcile-workflow`, qui n'écrase jamais un état qu'il n'a pas posé.
GL_RESTE_LABELS="${MAESTRO_RESTE_CLAUDE_LABELS:-type::infra,agent::orchestrateur,prio::haute}"
# Le rail est SU par construction : `.claude/**` est de l'outillage (#617, docs/10 §3.4). Il n'est
# donc pas deviné, et le jalon suit la règle commune — l'actif le plus ancien non soldé de ce rail.
GL_RESTE_RAIL="${MAESTRO_RESTE_CLAUDE_RAIL:-outillage}"

# gl_reste_claude <iid-source> <fichier> -> crée (ou complète) le ticket de reprise du ticket source.
# Codes : 0 créé / mis à jour / déjà à jour · 2 usage · 3 iid source inconnu · 4 fichier absent ou
# vide · 1 échec côté forge. Les deux REFUS (3 et 4) tombent AVANT toute écriture : c'est ce qui rend
# « refus » et « écriture partielle » mutuellement exclusifs.
gl_reste_claude() {
  local source="$1" fichier="$2"
  if [ -z "$source" ] || [ -z "$fichier" ]; then
    echo "usage: gl_reste_claude <iid-source> <fichier>" >&2; return 2
  fi
  # Le fichier d'abord : c'est le contrôle GRATUIT, et refuser sans avoir rien demandé à la forge
  # est ce qui garantit qu'un refus ne laisse rien derrière lui.
  if [ ! -f "$fichier" ]; then
    echo "gl_reste_claude : fichier introuvable : $fichier" >&2
    echo "  Le correctif EST le corps du ticket : l'écrire d'abord (outil Write), puis passer son CHEMIN." >&2
    return 4
  fi
  if [ ! -s "$fichier" ]; then
    echo "gl_reste_claude : $fichier est vide — un ticket de reprise sans correctif n'apprend rien." >&2
    return 4
  fi
  case "$source" in
    ''|*[!0-9]*)
      echo "gl_reste_claude : « $source » n'est pas un iid de ticket — rien n'a été écrit." >&2
      return 3 ;;
  esac

  local vue rc titre reprise empreinte
  vue="$(gh_reste_source "$source")"; rc=$?
  if [ "$rc" -ne 0 ]; then return "$rc"; fi
  # La tabulation s'écrit `$'\t'` et jamais en littéral : un caractère invisible au milieu d'une
  # expansion est ce qu'un éditeur ou un patch retransforme en espaces sans que rien ne le dise.
  titre="${vue%%$'\t'*}"
  reprise="${vue##*$'\t'}"

  # L'empreinte identifie le CORRECTIF, pas la section qui l'enveloppe : elle doit rester la même
  # d'un appel à l'autre, donc aucune date n'y entre. `cksum` lit sur stdin pour que son nom de
  # fichier — qui change d'un appel à l'autre — ne compte pas dans la sortie.
  empreinte="$(cksum < "$fichier" | awk '{ printf "%s-%s", $1, $2 }')"
  if [ -z "$empreinte" ]; then
    echo "gl_reste_claude : empreinte de « $fichier » illisible (cksum absent ?)" >&2; return 1
  fi

  if [ -n "$reprise" ]; then
    gl_reste_complete "$source" "$reprise" "$fichier" "$empreinte"
    return $?
  fi
  gl_reste_cree "$source" "$titre" "$fichier" "$empreinte"
}

# gl_reste_complete <source> <reprise> <fichier> <empreinte> — la voie du REJEU : le ticket de
# reprise existe, on lui ajoute ce correctif-ci s'il ne l'a pas déjà.
gl_reste_complete() {
  local source="$1" reprise="$2" fichier="$3" empreinte="$4" corps
  # Brouillon relu par personne — il repart tel quel vers la forge : temporaire du système, pas
  # `.maestro/` (règle #234, docs/10 §8.5).
  corps="$(mktemp "${TMPDIR:-/tmp}/maestro-reste.XXXXXX")" || return 1
  # Un corps VIDE ne peut pas être un ticket de reprise que ce verbe a écrit : c'est un ticket
  # supprimé ou une forge muette. Le dire franchement plutôt que d'écraser un corps qu'on n'a pas lu.
  if ! gl_get_description "$reprise" > "$corps" || [ ! -s "$corps" ]; then
    rm -f "$corps"
    echo "gl_reste_claude : #$reprise (ticket de reprise de #$source) illisible ou vide — rien réécrit." >&2
    return 1
  fi
  if grep -q "empreinte $empreinte" "$corps"; then
    rm -f "$corps"
    printf 'Ticket de reprise #%s : ce correctif y est déjà (empreinte %s) — rien à écrire.\n' \
      "$reprise" "$empreinte"
    gl_issue_url "$reprise"
    return 0
  fi
  gl_reste_section "$fichier" "$empreinte" >> "$corps"
  if ! gl_set_description "$reprise" "$corps" >/dev/null; then
    rm -f "$corps"
    echo "gl_reste_claude : échec de la mise à jour de #$reprise — le correctif n'y est PAS." >&2
    return 1
  fi
  rm -f "$corps"
  printf 'Ticket de reprise #%s complété (correctif ajouté, empreinte %s) — aucun doublon ouvert.\n' \
    "$reprise" "$empreinte"
  gl_issue_url "$reprise"
}

# gl_reste_cree <source> <titre-source> <fichier> <empreinte> — la voie de la CRÉATION.
#
# L'ORDRE DES TROIS ÉCRITURES EST LE CONTENU DE LA DÉCISION : création, puis ANCRE, puis état. Un
# rejeu qui suit un échec ne doit jamais ouvrir un second ticket, donc l'ancre passe avant tout ce
# qui peut encore échouer. Ce qui reste après elle — l'état — se répare par un verbe nommé dans le
# message, sans rien dupliquer.
gl_reste_cree() {
  local source="$1" titre_source="$2" fichier="$3" empreinte="$4"
  local user milestone corps nouveau note titre
  user="$(gl_current_user)" || {
    echo "gl_reste_claude : compte de la forge indéterminable — le ticket naîtrait LIBRE, donc prenable par un run. Rien n'a été écrit." >&2
    return 1
  }
  # Le jalon est best-effort, comme dans /ticket-create : son absence est une dérive que doctor.sh
  # signale, jamais une raison de perdre le correctif.
  milestone="$(gl_current_milestone "$GL_RESTE_RAIL" 2>/dev/null)" || milestone=""

  # Brouillon que personne ne relit — il part aussitôt vers la forge, où il devient le corps du
  # ticket : temporaire du système, pas `.maestro/` (règle #234, docs/10 §8.5).
  corps="$(mktemp "${TMPDIR:-/tmp}/maestro-reste.XXXXXX")" || return 1
  gl_reste_entete "$source" "$titre_source" > "$corps"
  gl_reste_section "$fichier" "$empreinte" >> "$corps"

  titre="Reste à appliquer sous .claude/ (#$source) — $titre_source"
  nouveau="$(gh_create_issue "$titre" "$corps" "$GL_RESTE_LABELS" "$milestone" "$user")" || {
    rm -f "$corps"
    echo "gl_reste_claude : création du ticket de reprise de #$source en échec — rien n'a été écrit." >&2
    return 1
  }
  rm -f "$corps"

  # Même raison que ci-dessus, et que gh_issue_link : un commentaire qui part n'a pas de lecteur
  # local (règle #234).
  note="$(mktemp "${TMPDIR:-/tmp}/maestro-reste-note.XXXXXX")" || return 1
  gl_reste_ancre "$nouveau" > "$note"
  if ! gl_issue_note "$source" "$note" >/dev/null; then
    rm -f "$note"
    printf 'Ticket de reprise #%s créé, MAIS #%s ne le nomme pas : un rejeu ouvrirait un doublon.\n' \
      "$nouveau" "$source" >&2
    printf '  Réparer en postant le lien sur #%s, ou vérifier avant de rejouer.\n' "$source" >&2
    return 1
  fi
  rm -f "$note"

  if ! gl_project_add "$nouveau" >/dev/null 2>&1; then
    printf 'Ticket de reprise #%s créé et lié à #%s, MAIS SANS ÉTAT — il ne remonte dans aucune vue.\n' \
      "$nouveau" "$source" >&2
    printf '  Rejouer : bash scripts/gitlab/lib.sh project-add %s\n' "$nouveau" >&2
    gl_issue_url "$nouveau"
    return 1
  fi

  printf 'Ticket de reprise #%s créé pour #%s — assigné à @%s, « À faire »%s.\n' \
    "$nouveau" "$source" "$user" "${milestone:+, jalon « $milestone »}"
  printf '  Assigné À DESSEIN : c'\''est ce qui le tient hors des plans de queue.sh (« À faire ET libre »).\n'
  gl_issue_url "$nouveau"
}

# gl_reste_entete <source> <titre-source> — l'en-tête du corps, écrit UNE FOIS à la création. Il
# nomme la source et dit au lecteur ce qu'il a à faire : sans ça, un ticket de reprise trouvé six
# semaines plus tard dans `/backlog` est un correctif sans contexte.
gl_reste_entete() {
  local source="$1" titre_source="$2"
  cat <<ENTETE
Reprise de #$source — $titre_source

Ce ticket porte un **correctif qu'une session autonome n'a pas pu appliquer** : l'écriture sous
\`.claude/\` est refusée par un garde-fou du CLI, en amont de l'allowlist comme des hooks (#238,
re-mesuré par #614). La session a fait ce qu'il fallait — elle a rendu le contenu au lieu de
contourner.

Il existe parce qu'une **description de PR meurt au merge** (#608) : depuis #418/#419 le pilote merge
sans attendre personne, et la section « reste à appliquer » finit dans le corps d'une PR fermée que
plus rien ne rouvre. Le ticket, lui, survit.

**Pour le solder** : \`/ticket-start\` sur ce ticket, en session **interactive** — un run autonome se
ferait refuser exactement la même écriture et reproduirait le résidu. Appliquer chaque correctif
ci-dessous, puis \`/ticket-ship\`.

⚠ Il naît **assigné**, et c'est ce qui le tient hors des plans de \`queue.sh\`, qui filtre sur
« À faire **et** libre ». Le libérer sans l'appliquer le rendrait prenable par un run.

ENTETE
}

# gl_reste_section <fichier> <empreinte> — une section par correctif. L'empreinte est en clair dans
# le titre de section : c'est elle que le rejeu relit, et elle doit rester greppable sur UNE ligne.
gl_reste_section() {
  local fichier="$1" empreinte="$2"
  printf '\n## Correctif — empreinte %s\n\n' "$empreinte"
  cat "$fichier"
  printf '\n'
}

# gl_reste_ancre <iid-reprise> — le commentaire posé sur le ticket SOURCE. Sa forme est un contrat :
# c'est « ticket de reprise #<n> » que relit gl_reste_claude au tour suivant.
gl_reste_ancre() {
  cat <<ANCRE
Écriture refusée sous \`.claude/\` : le correctif est rendu dans la description de la PR, mais une PR
se ferme au merge. Il vit donc aussi dans son **ticket de reprise #$1**, qui lui survit (#608).
ANCRE
}

# gl_reste_claude_de <iid-source> -> l'iid du ticket de reprise, ou rien. LECTURE SEULE.
# Codes : 0 il en a un (imprimé) · 3 aucun · 1 illisible (source inconnue, forge muette) · 2 usage.
#
# LE PENDANT EN LECTURE de gl_reste_claude, et la moitié dont le pilote a besoin (#611) : « ce ticket
# a-t-il bien son ticket de reprise ? ». Il ne réinterroge rien de plus — `gh_reste_source` répond
# déjà aux deux questions en UN aller, et c'est lui qui porte la forme de l'ancre (« ticket de
# reprise #<n> »). La relire ici, ne serait-ce qu'en `grep`, ferait deux définitions d'un contrat que
# l'écriture et la lecture doivent partager au caractère près.
#
# TOUT CE QUI N'EST PAS UNE LECTURE PROPRE VAUT 1, source introuvable comprise : l'appelant a trois
# conduites et pas quatre — nommer le ticket de reprise, dire qu'il manque, ou dire qu'il n'a pas pu
# regarder. Confondre « pas de reprise » et « je n'ai pas su lire » ferait annoncer un résidu perdu
# sur une forge momentanément muette, c'est-à-dire crier au loup depuis un signalement best-effort.
gl_reste_claude_de() {
  local source="$1" vue rc reprise
  if [ -z "$source" ]; then echo "usage: gl_reste_claude_de <iid-source>" >&2; return 2; fi
  case "$source" in
    ''|*[!0-9]*) return 1 ;;
  esac
  vue="$(gh_reste_source "$source" 2>/dev/null)"; rc=$?
  [ "$rc" -eq 0 ] || return 1
  reprise="${vue##*$'\t'}"
  [ -n "$reprise" ] || return 3
  printf '%s\n' "$reprise"
}

# --- Pipelines CI ---------------------------------------------------------------------------------
# Helpers REST pour le diagnostic de pipeline (/mr-fix — voir docs/10-workflow-git.md §8.3).
# Même parti pris que le reste du fichier : parsing shell pur (grep/sed/awk), pas de jq/python.

# gl_host -> hôte de la forge, déduit du remote `origin` (défaut : celui de la forge active). Rien
# n'est codé en dur : le workflow doit tenir sur une instance auto-hébergée. Gère les deux formes
# d'URL (https://hote/groupe/projet et git@hote:groupe/projet).
gl_host() {
  local url racine defaut="github.com"
  racine="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
  url="$(git -C "$racine" remote get-url origin 2>/dev/null)" || { printf '%s\n' "$defaut"; return 0; }
  case "$url" in
    *://*) url="${url#*://}"; url="${url#*@}"; url="${url%%/*}" ;;
    *@*:*) url="${url#*@}"; url="${url%%:*}" ;;
    *)     url="" ;;
  esac
  printf '%s\n' "${url:-$defaut}"
}

# gl_pipeline_latest <ref> -> dernier pipeline de la branche, en une ligne TSV :
#   id <TAB> status <TAB> sha <TAB> web_url
# Cherche d'abord du côté de la PR ouverte de la branche — le cas NORMAL depuis #165, et cet
# endpoint remonte AUSSI les pipelines de branche ou manuels du même sha, donc la même vue que le
# garde-fou de merge ; puis, à défaut de PR, les runs portant la ref (`main`, branche sans PR,
# déclenchement manuel `workflow_dispatch`).
# Code 1 (et message) si aucun pipeline n'existe ni pour la PR, ni pour la ref.
gl_pipeline_latest() {
  local ref="$1"
  if [ -z "$ref" ]; then echo "usage: gl_pipeline_latest <ref>" >&2; return 2; fi
  gh_pipeline_latest "$@"
}

# gl_pipeline_status <pipeline-id> -> imprime le statut courant du pipeline (created|pending|
# running|success|failed|canceled|skipped|manual…). Le premier "status" du JSON détaillé est
# celui du pipeline lui-même (les objets imbriqués — user, commit — viennent après).
gl_pipeline_status() {
  local pid="$1"
  if [ -z "$pid" ]; then echo "usage: gl_pipeline_status <pipeline-id>" >&2; return 2; fi
  gh_pipeline_status "$@"
}

# gl_pipeline_failed_jobs <pipeline-id> -> jobs en échec du pipeline, une ligne TSV par job :
#   id <TAB> name <TAB> stage <TAB> failure_reason
# S'appuie sur le filtre serveur `scope[]=failed` (seuls les jobs rouges reviennent) et sur
# l'ordre stable des premiers champs du JSON job ("id","status","stage","name") pour ne matcher
# que les objets job de tête — jamais les objets imbriqués (user/commit/pipeline), dont l'ordre
# de champs diffère. Le failure_reason est cherché dans le corps du job courant uniquement.
gl_pipeline_failed_jobs() {
  local pid="$1"
  if [ -z "$pid" ]; then echo "usage: gl_pipeline_failed_jobs <pipeline-id>" >&2; return 2; fi
  gh_pipeline_failed_jobs "$@"
}

# gl_job_trace <job-id> [lignes] -> queue de la trace du job (défaut : 100 dernières lignes).
# C'est la matière première du diagnostic ; l'appelant en extrait les lignes d'erreur utiles
# plutôt que de recopier le log brut.
gl_job_trace() {
  local jid="$1" lines="${2:-100}"
  if [ -z "$jid" ]; then echo "usage: gl_job_trace <job-id> [lignes]" >&2; return 2; fi
  gh_job_trace "$@"
}

# --- Attendre un verdict de pipeline (#416, parent #413) ----------------------------------------
# Défauts du régime d'attente, surchargeables par l'environnement — jamais figés dans un appelant :
# un plafond recopié dans un prompt se périme le jour où la CI change de durée.
#
# ⚠ TROIS RÉGLAGES ET NON DEUX DEPUIS #595, parce qu'il y a trois échelles et pas deux. Le plafond
# borne un run QUI TOURNE (~2-4 min ici) ; la naissance borne l'APPARITION du run, et cette
# apparition n'a pas la même échelle selon qu'un événement est dû ou non :
#   · aucune PR ouverte → aucun événement ne viendra jamais, la réponse est acquise en deux minutes ;
#   · une PR ouverte → l'événement est dû, il ne reste qu'à savoir QUAND. Le 2026-08-26, GitHub a
#     mis 18 à 20 min à le livrer sur trois PR consécutives (§8.9) — sous les 120 s d'alors,
#     `pipeline-wait` déclarait anormal le cas normal du jour, et le remède était un
#     `gh workflow run` trouvé à la main. 1800 s tient la mesure avec de la marge.
# Les fondre en un seul chiffre est exactement ce qui a cassé : un plafond censé borner une attente
# anormale devenait le mécanisme qui déclarait anormale une attente normale.
GL_PIPELINE_TIMEOUT="${MAESTRO_PIPELINE_TIMEOUT:-900}"     # plafond d'un run QUI TOURNE
GL_PIPELINE_SONDAGE="${MAESTRO_PIPELINE_SONDAGE:-15}"      # intervalle entre deux lectures
GL_PIPELINE_NAISSANCE="${MAESTRO_PIPELINE_NAISSANCE:-120}" # naissance quand AUCUN événement n'est dû
GL_PIPELINE_NAISSANCE_PR="${MAESTRO_PIPELINE_NAISSANCE_PR:-1800}" # naissance quand une PR est ouverte

# gl_pipeline_wait <ref|run-id> [--timeout <s>] -> attend un VERDICT et imprime le statut final.
#
# ⚠ C'est un verbe d'ATTENTE, et rien d'autre : il ne relance rien, ne corrige rien, ne juge rien
# et n'écrit nulle part — ni sur la forge, ni dans le dépôt. Un pipeline rouge se remédie par
# /mr-fix (§8.3) ; ici on se contente de le nommer.
#
# POURQUOI IL EXISTE. La CI ne se déclenche que sur les Pull Requests (#165, §8) et `/ticket-finish`
# pousse PUIS ouvre : le pipeline naît donc APRÈS la PR, et tout merge qui suit une clôture arrive
# trop tôt. `merge-mr` le constate et rend 3, « repasser plus tard » (§6) — c'est ici qu'on repasse,
# de façon bornée.
#
# LA CIBLE se lit d'elle-même. Une REF est le cas normal : on attend le run que la branche va
# produire, et on le redemande à `pipeline-latest` à chaque tour — donc un run qui n'existait pas au
# premier tour est vu au suivant. Un RUN-ID est la cible de /mr-fix, qui tient déjà l'identifiant du
# pipeline qu'il vient de déclencher et veut suivre CELUI-LÀ, pas le plus récent de la branche. Un
# id est tout en chiffres, une branche de ticket ne l'est jamais (`<type>/<iid>-<slug>`, §2).
#
# TROIS BORNES, PARCE QU'IL Y A TROIS IGNORANCES (#595). Le plafond (défaut 15 min) couvre une file
# d'attente, pas une panne : un pipeline complet tourne en ~2-4 min. Mais l'attendre en entier pour
# conclure « aucun pipeline » serait payer quinze minutes une réponse acquise en deux. D'où un délai
# de NAISSANCE, qui ne borne que la première apparition — et qui vaut, lui, DEUX chiffres :
#   · AUCUNE PR OUVERTE (120 s) : rien ne déclenchera jamais ce run, la CI ne se déclenchant que sur
#     les PR (§8). Attendre plus longtemps serait attendre un événement sans émetteur. Ce délai-là
#     reste ramené au plafond quand celui-ci est plus court, comme avant.
#   · UNE PR OUVERTE (30 min) : l'événement est DÛ, seule son heure est inconnue — et le 2026-08-26
#     GitHub l'a livré 18 à 20 min après l'ouverture de la PR, trois fois de suite (§8.9). Ce
#     délai-là n'est PAS ramené au plafond : les deux ne mesurent pas la même chose, et leur faire
#     partager un chiffre est exactement ce qui a fait déclarer anormale une attente normale.
# Le plafond, lui, court À PARTIR DU MOMENT OÙ LE RUN EST VU et non depuis l'appel — sans quoi une
# naissance de vingt minutes mangerait les quinze minutes du run qu'elle vient de faire apparaître.
#
# Codes de retour — une cause, une conduite. `4`, `5` et `6` sont trois ignorances, et les confondre
# serait le faux verdict que ce verbe existe pour éviter : un plafond dit « pas encore », une
# absence dit « il n'y en aura pas », une naissance dit « il n'est pas là, mais il vient ». Aucun
# des trois n'est un échec du ticket, et aucun des trois n'appelle `/mr-fix` — il n'y a là ni
# conflit ni job rouge à réparer.
#   0 = vert (success)                    4 = plafond atteint, run toujours en cours
#   3 = verdict terminal NON vert         5 = aucun pipeline, et aucun n'est dû (pas de PR ouverte)
#       (failed/canceled/skipped/manual   6 = run PAS ENCORE NÉ, alors qu'une PR le rend dû
#        — le statut imprimé dit lequel)  2 = usage
#                                         1 = lecture impossible (outil, run introuvable)
#
# `--timeout 0` n'est pas un plafond mais un RÉGIME : « sonde une fois, ne dors jamais ». Il éteint
# donc les deux délais de naissance en même temps que le plafond — de quoi relire l'état d'une
# branche avec les mêmes codes, sans dupliquer la table de correspondance chez l'appelant.
#
# ⚠ IL NE JUGE TOUJOURS PAS LA MERGEABILITÉ, mais il sait désormais QUEL run il attend, et la nuance
# tient en une phrase : c'est `merge-mr` qui décide qu'un vert vaut merge (il compare les sha et rend
# 3 « verdict périmé », §6) ; ici on se contente de ne pas prendre le run de la PUSH PRÉCÉDENTE pour
# celui qu'on attend. Sans cette distinction, un vieux vert faisait rendre `0` INSTANTANÉMENT — la
# reprise unique de `/ticket-finish` (§6) repassait alors sans avoir attendu une seule seconde, et
# les deux appels rendaient le même verdict pour la même raison. Un `0` ici n'est toujours pas une
# promesse de merge, c'est la fin d'une attente.
#
# LA LECTURE QUI DIT LES DEUX. `gh_merge_facts <ref>`, UNE fois avant la boucle et jamais par tour,
# répond du même coup à « un événement est-il dû ? » (une PR ouverte) et à « quel sha attend-on ? »
# (sa tête). Une ref sans PR — ou une forge muette — retombe exactement sur le régime d'avant #595 :
# aucun sha attendu, délai court, code `5`.
#
# ⚠ ET IL NE DÉCLENCHE RIEN, surtout pas ici. Le remède du 2026-08-26 fut un `gh workflow run
# ci.yml --ref <branche>` lancé à la main, et le câbler dans ce verbe était la piste évidente : elle
# est écartée (§8.9). Deux raisons, dont une seule suffirait — ce verbe ne relance rien par
# construction (`guard.sh` l'autorise en session parce qu'il n'écrit pas), et un run de dispatch
# porte `refs/heads/<branche>` là où l'événement `pull_request` porte la ref de MERGE : il a le même
# `head_sha`, donc `merge-mr` l'accepterait, mais il n'a pas vérifié la même chose. Substituer en
# silence une vérification plus faible à celle qu'on attendait est le contraire de « aucun merge non
# vérifié ». Le geste reste donc humain — et le message du code `6` le NOMME, au seul moment où il
# sert : ce qui manquait n'était pas le remède, c'était de le connaître à cet instant-là.
#
# À appeler en `bash … pipeline-wait <ref> || verdict=$?` pour lire le verdict sans interrompre une
# boucle sous `set -e`.
gl_pipeline_wait() {
  local cible="" timeout="$GL_PIPELINE_TIMEOUT" timeout_pose=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --timeout)
        shift
        if [ $# -eq 0 ]; then
          echo "gl_pipeline_wait : --timeout attend une durée en secondes." >&2; return 2
        fi
        timeout="$1"; timeout_pose=1 ;;
      --timeout=*) timeout="${1#--timeout=}"; timeout_pose=1 ;;
      -h | --help)
        echo "usage: gl_pipeline_wait <ref|run-id> [--timeout <s>]" >&2; return 2 ;;
      -*) echo "gl_pipeline_wait : option inconnue « $1 »" >&2; return 2 ;;
      *)
        if [ -z "$cible" ]; then
          cible="$1"
        elif [ "$timeout_pose" -eq 0 ]; then
          # Forme historique `pipeline-wait <id> <timeout-s>`, conservée : /mr-fix et docs/10 §8.3
          # la nomment, et une signature qui casse chez ses appelants n'est pas une amélioration.
          timeout="$1"; timeout_pose=1
        else
          echo "gl_pipeline_wait : argument en trop « $1 »." >&2; return 2
        fi ;;
    esac
    shift
  done

  if [ -z "$cible" ]; then
    echo "usage: gl_pipeline_wait <ref|run-id> [--timeout <s>]" >&2; return 2
  fi
  case "$timeout" in
    '' | *[!0-9]*)
      printf 'gl_pipeline_wait : durée invalide « %s » (secondes attendues).\n' "$timeout" >&2
      return 2 ;;
  esac

  gl_require || return 1

  local mode="ref"
  case "$cible" in *[!0-9]*) ;; *) mode="id" ;; esac

  local poll="$GL_PIPELINE_SONDAGE"
  [ "$poll" -ge 1 ] || poll=1

  # --- Un événement est-il DÛ, et pour quel sha ? UNE lecture, avant la boucle (#595) -----------
  # Deux questions, une réponse : `gh_merge_facts` rend l'état de la PR de cette ref ET la tête
  # qu'elle porte. Muet ou sans PR, on retombe sur le régime d'avant — délai court, aucun sha
  # attendu —, ce qui fait de cette lecture un enrichissement et jamais un prérequis.
  local naissance="$GL_PIPELINE_NAISSANCE" attendu_sha="" du=0 mr=""
  if [ "$mode" = "ref" ]; then
    local faits etat_pr sha_pr
    if faits="$(gh_merge_facts "$cible" 2>/dev/null)" && [ -n "$faits" ]; then
      # Les deux derniers champs (brouillon, fermetures) relèvent de `merge-mr` : ici on n'a besoin
      # que de « la PR est-elle ouverte ? » et « quelle tête porte-t-elle ? ».
      IFS=$'\t' read -r etat_pr mr sha_pr _ _ <<< "$faits"
      if [ "$etat_pr" = "opened" ]; then
        du=1
        naissance="$GL_PIPELINE_NAISSANCE_PR"
        case "$sha_pr" in '' | -) ;; *) attendu_sha="$sha_pr" ;; esac
      fi
    fi
  fi
  # Le délai « aucun événement dû » se range sous le plafond ; celui de la PR, non — c'est tout
  # l'objet de #595. `--timeout 0` les éteint tous les deux : il ne dit pas « plafond nul », il dit
  # « ne dors pas ».
  [ "$du" -eq 1 ] || [ "$naissance" -le "$timeout" ] || naissance="$timeout"
  [ "$timeout" -gt 0 ] || naissance=0

  local attendu=0 vu=0 vu_a=0 statut="" ligne id sha url=""
  while :; do
    statut=""
    if [ "$mode" = "id" ]; then
      if ! statut="$(gl_pipeline_status "$cible" 2>/dev/null)" || [ -z "$statut" ]; then
        # Un id qu'on ne sait pas lire n'est pas « pas encore né » : il a été donné par un appelant
        # qui l'avait en main, donc c'est une erreur de lecture et pas une attente.
        printf 'gl_pipeline_wait : run %s illisible dans %s.\n' "$cible" "$GL_GH_REPO" >&2
        return 1
      fi
      vu=1
    elif ligne="$(gl_pipeline_latest "$cible" 2>/dev/null)" && [ -n "$ligne" ]; then
      IFS=$'\t' read -r id statut sha url <<< "$ligne"
      if [ -n "$attendu_sha" ] && [ "$sha" != "$attendu_sha" ]; then
        # Le run de la push précédente n'est PAS celui qu'on attend : le nôtre n'est pas né. On
        # l'écarte au lieu de le prendre pour un verdict — c'est la moitié de #595 que le
        # chronomètre ne montre pas, un vieux vert faisant sinon rendre `0` en une seconde.
        statut=""; url=""
      elif [ "$vu" -eq 0 ]; then
        vu=1
        vu_a="$attendu"
        # Une naissance qui a DURÉ se dit, même quand elle aboutit : c'est ce qui permet au compte
        # rendu de l'appelant de la nommer au lieu de la fondre dans « attente » (#595). Le seuil
        # est le délai court — au-delà, la naissance n'est plus celle du régime normal.
        if [ "$attendu" -gt "$GL_PIPELINE_NAISSANCE" ]; then
          printf 'gl_pipeline_wait : run né après %s d'\''attente — le déclencheur a tardé (§8.9).\n' \
            "$(gl_duree_lisible "$attendu")" >&2
        fi
      fi
    fi

    case "$statut" in
      success)
        printf '%s\n' "$statut"
        return 0 ;;
      failed | canceled | skipped | manual)
        # Terminal sans être vert. Le regrouper sous un seul code est délibéré : l'appelant qui
        # doit distinguer `failed` de `skipped` lit le statut imprimé, celui qui décide « ne pas
        # merger » n'a pas à énumérer quatre mots pour la même conduite.
        [ -z "$url" ] || printf '  %s\n' "$url" >&2
        printf '%s\n' "$statut"
        return 3 ;;
    esac

    if [ "$vu" -eq 0 ] && [ "$attendu" -ge "$naissance" ]; then
      if [ "$du" -eq 1 ]; then
        # `6` et non `5` : une PR est ouverte, donc l'événement était dû — ce run n'est pas
        # « inexistant », il est EN RETARD, et la conduite n'est pas la même (§8.9). Le remède
        # manuel est nommé ICI parce que c'est le seul instant où il sert ; le câbler ferait de ce
        # verbe un verbe qui écrit, et vérifierait la branche au lieu de la ref de merge.
        printf 'gl_pipeline_wait : run pas encore né pour « %s » après %s — PR #%s ouverte, l'\''événement est dû.\n' \
          "$cible" "$(gl_duree_lisible "$attendu")" "${mr:-?}" >&2
        printf '  le déclencher à la main si l'\''attente n'\''est plus tenable : gh workflow run ci.yml --ref %s\n' \
          "$cible" >&2
        return 6
      fi
      printf 'gl_pipeline_wait : aucun pipeline pour « %s » après %ss — la CI ne se déclenche que sur les PR (§8).\n' \
        "$cible" "$attendu" >&2
      return 5
    fi
    if [ "$vu" -eq 1 ] && [ $((attendu - vu_a)) -ge "$timeout" ]; then
      printf 'gl_pipeline_wait : plafond atteint (%ss) — dernier statut : %s\n' "$timeout" "${statut:-inconnu}" >&2
      [ -z "$url" ] || printf '  %s\n' "$url" >&2
      printf '%s\n' "${statut:-inconnu}"
      return 4
    fi
    sleep "$poll"
    attendu=$((attendu + poll))
  done
}

# --- Revue best-effort : file de revue + relecteur posé à la main --------------------------------
# Arbitrage du chantier « travail à plusieurs » (#155/#161) : l'approbation n'est PAS rendue
# obligatoire (`approvals_before_merge` reste à 0 — une approbation bloquante recréerait une
# dépendance entre personnes, et ce que le merge exige vit dans `merge-mr` — aucun merge non
# vérifié, §6/#417). Ce qui est outillé, c'est
# la VISIBILITÉ : la file d'attente est affichée en tête de /backlog (gl_review_queue), la plus
# ancienne d'abord. La pose d'un relecteur (gl_set_reviewer) reste OUTILLÉE mais n'est plus
# AUTOMATIQUE : depuis #196, /ticket-finish ne l'appelle plus — désigner un relecteur est un geste
# humain explicite, la file de revue portant seule le signal « cette PR attend quelqu'un ».

# gl_project_humans [access-min] -> membres HUMAINS du projet éligibles à une revue, une ligne TSV
# par membre : username <TAB> access_level, triés par username (ordre stable, d'où la reproductibilité
# de gl_pick_reviewer). Sont écartés : les bots GitLab (`User.bot`), les comptes non actifs, les
# comptes d'automatisation listés dans GL_BOT_USERS, et les niveaux d'accès < access-min
# (défaut GL_REVIEWER_MIN_ACCESS). Membres directs ET hérités du groupe.
gl_project_humans() {
  gh_project_humans "$@"
}

# gl_pick_reviewer [auteur] [graine] -> imprime le username d'un relecteur humain DIFFÉRENT de
# l'auteur (défaut : l'utilisateur authentifié). Aucun nom n'est codé en dur : les candidats
# viennent de l'API des membres (gl_project_humans).
# La graine (l'iid de la PR en pratique) sert de ROTATION : même PR -> même relecteur (la pose est
# donc reproductible et idempotente), PR différentes -> relecteurs répartis plutôt que toujours le
# même. Code 1 si aucun candidat (projet à une seule personne) : l'appelant continue sans relecteur,
# la revue est best-effort.
gl_pick_reviewer() {
  local auteur="${1:-}" graine="${2:-0}"
  [ -n "$auteur" ] || auteur="$(gl_current_user 2>/dev/null)"
  local candidats n idx
  candidats="$(gl_project_humans | awk -F'\t' -v a="$auteur" '$1 != a { print $1 }')" || return 1
  n="$(printf '%s\n' "$candidats" | grep -c .)"
  if [ "$n" -eq 0 ]; then
    echo "gl_pick_reviewer : aucun relecteur humain disponible (hors auteur « ${auteur:-?} » et comptes d'automatisation « $GL_BOT_USERS »)" >&2
    return 1
  fi
  graine="$(printf '%s' "$graine" | tr -cd '0-9')"
  [ -n "$graine" ] || graine=0
  idx=$(( graine % n + 1 ))
  printf '%s\n' "$candidats" | sed -n "${idx}p"
}

# gl_mr_iid [mr|branche] -> imprime l'iid de la PR OUVERTE désignée : un nombre est rendu tel quel,
# un nom de branche est résolu via l'API (défaut : la branche courante). Code 1 si aucune PR ouverte.
gl_mr_iid() {
  local ref="${1:-}"
  [ -n "$ref" ] || ref="$(git branch --show-current 2>/dev/null)"
  if [ -z "$ref" ]; then echo "gl_mr_iid : ni PR ni branche à résoudre" >&2; return 2; fi
  case "$ref" in
    *[!0-9]*) ;;
    *) printf '%s\n' "$ref"; return 0 ;;
  esac
  gh_mr_iid "$ref"
}

# gl_mr_review_info <mr|branche> -> « auteur <TAB> relecteurs » (relecteurs séparés par des virgules,
# champ vide si aucun). Une seule lecture GraphQL, parsing shell pur.
gl_mr_review_info() {
  local ref="${1:-}" mr raw auteur rev
  mr="$(gl_mr_iid "$ref")" || return 1
  gh_mr_review_info "$mr"
}

# gl_mr_reviewers <mr|branche> -> relecteurs actuellement posés sur la PR (CSV, vide si aucun).
gl_mr_reviewers() {
  local info
  info="$(gl_mr_review_info "$@")" || return 1
  printf '%s\n' "$info" | cut -f2
}

# gl_set_reviewer [mr|branche] [username] -> pose un relecteur humain sur la PR (défaut : la PR
# ouverte de la branche courante ; relecteur choisi par gl_pick_reviewer, graine = iid de la PR).
# APPEL EXPLICITE UNIQUEMENT (#196) : aucune commande du workflow ne l'invoque — /ticket-finish ne
# pose plus de relecteur d'office, la désignation étant un geste humain.
# IDEMPOTENT et non destructif : si un relecteur est DÉJÀ posé (par un humain ou par un passage
# précédent), il est conservé tel quel — la fonction ne remplace jamais. Refuse de désigner l'auteur.
# Best-effort par nature : sur un projet à une seule personne, elle échoue proprement (code 1) et
# l'appelant poursuit sans relecteur.
gl_set_reviewer() {
  local ref="${1:-}" who="${2:-}" mr info auteur rev out
  mr="$(gl_mr_iid "$ref")" || return 1
  info="$(gl_mr_review_info "$mr")" || return 1
  IFS=$'\t' read -r auteur rev <<< "$info"
  if [ -n "$rev" ]; then
    printf 'PR #%s : relecteur déjà posé (@%s) — inchangé.\n' "$mr" "$rev"
    return 0
  fi
  if [ -z "$who" ]; then
    who="$(gl_pick_reviewer "$auteur" "$mr")" || return 1
  fi
  if [ "$who" = "$auteur" ]; then
    echo "gl_set_reviewer : @$who est l'auteur de la PR #$mr — le relecteur doit en être distinct." >&2
    return 1
  fi
  gh_set_reviewer "$mr" "$who" "$auteur"
}

# gl_review_queue -> file des PR OUVERTES en attente de revue, la plus ANCIENNE d'abord, une ligne
# TSV par PR (en-tête préfixée « # » à ignorer côté machine) :
#     mr <TAB> age_j <TAB> etat <TAB> pipeline <TAB> auteur <TAB> relecteur <TAB> branche <TAB> titre
# `age_j` = jours écoulés depuis la création (c'est l'ancienneté qui déclenche la relecture),
# `etat` ∈ draft|ready, `pipeline` = statut du dernier pipeline en minuscules (success/failed/
# running/…, « - » si aucun), `relecteur` = CSV des relecteurs posés (« - » si personne).
# Le préfixe « Draft: » du titre est retiré : l'information est déjà dans la colonne `etat`.
gl_review_queue() {
  gh_review_queue "$@"
}

# --- Nettoyage des branches locales -------------------------------------------------------------
# gl_mr_brief <branche> -> « etat<TAB>numéro<TAB>sha » de la PR portant cette
# branche source. Troisième primitive du commutateur de forge (cf. en-tête) : gl_mr_state et
# gl_worktree_done en descendent tous les deux, donc ni l'un ni l'autre n'a de variante par forge.
#
#   etat    opened | closed | merged — le vocabulaire GITLAB, y compris côté GitHub, qui dit
#           OPEN/CLOSED/MERGED : les comparaisons en dur des appelants (gl_cleanup_merged,
#           worktree.sh, /branch-cleanup) sont ainsi hors du chantier de la migration.
#   numéro  le number de la PR, « ? » si illisible.
#   sha     la tête de la branche source AU MOMENT du merge, « - » hors de ce cas. Le projet merge
#           en SQUASH : les commits de la branche ne sont pas des ancêtres de main et la branche
#           distante disparaît au merge, donc c'est la seule référence locale exploitable.
#
# Aucune PR : rien sur stdout, code 1 — « pas de PR » n'est pas un échec de lecture, l'appelant
# décide (gl_cleanup_merged garde la branche, gl_worktree_done interroge le ticket).
gl_mr_brief() {
  local branche="$1"
  if [ -z "$branche" ]; then echo "usage: gl_mr_brief <branche>" >&2; return 2; fi
  gh_mr_brief "$branche"
}

# gl_mr_briefs <branche…> -> le même brief, pour N branches, en UNE lecture :
#     <branche><TAB>etat<TAB>numéro<TAB>sha
# Une branche sans PR ne rend AUCUNE LIGNE (cf. gh_mr_briefs pour le contrat complet). Quatrième
# primitive du commutateur de forge, et la seule dont l'existence tient à un COÛT et non à une
# question nouvelle : elle répond exactement ce que gl_mr_brief répond, pour N branches au prix
# d'une (#602).
gl_mr_briefs() {
  if [ "$#" -eq 0 ]; then echo "usage: gl_mr_briefs <branche…>" >&2; return 2; fi
  gh_mr_briefs "$@"
}

# gl_open_mr_branches -> la branche SOURCE de chaque PR ouverte, une par ligne (non triées).
# Répond à « ce ticket "En revue" a-t-il bien une PR ouverte ? » (doctor.sh §4a) en UNE lecture, là
# où un gl_mr_state par ticket en ferait autant que de tickets en revue.
#
# gl_review_queue rend déjà cette colonne, mais elle en rend sept autres — dont l'âge, calculé
# ticket par ticket — pour une question qui n'en demande qu'une : la file de revue est une VUE, pas
# une primitive, et s'en servir ici coûterait la lecture des relecteurs et du rollup de checks.
gl_open_mr_branches() {
  gh_open_mr_branches
}

# gl_mr_state <branche> -> imprime l'état de la PR associée à la branche (opened|closed|merged),
# vide si aucune PR n'est trouvée.
gl_mr_state() {
  local branch="$1" brief
  if [ -z "$branch" ]; then echo "gl_mr_state : branche manquante" >&2; return 2; fi
  brief="$(gl_mr_brief "$branch")" || return 0
  printf '%s\n' "${brief%%$'\t'*}"
}

# gl_cleanup_merged [--auto] -> supprime les branches LOCALES (hors main et hors branche courante)
# dont la forge confirme la PR à l'état « merged ». Conçu pour tourner automatiquement (appelé par
# `worktree.sh ensure`, donc tout /ticket-start) — c'est le pendant non-interactif de
# /branch-cleanup :
#   • ne supprime QUE ce que la forge confirme mergé (garde-fou docs/10 §6) — jamais une branche au
#     statut incertain (opened/closed/aucune PR) ;
#   • `git branch -D` est sûr ici car le merge est confirmé (le projet merge en squash) ;
#   • ne change jamais de branche, n'écrit rien côté forge, et s'abstient si l'arbre est sale.
#
# Opère sur le CLONE PRINCIPAL d'où qu'on l'appelle (#305) — même parti pris que gl_sync_main et
# que worktree.sh gc, et pour une raison précise. Les refs, elles, sont partagées par tous les
# worktrees d'un dépôt : la liste des branches et le résultat des suppressions seraient les mêmes
# de partout. Ce qui change, c'est ce sur quoi portent les deux garde-fous — l'arbre regardé est
# celui du clone principal, normalement propre et sur `main`, et non celui d'un worktree en plein
# travail, qui ferait sauter la purge en silence à chaque reprise de session.
#
# ⚠ Une branche EMPRUNTÉE PAR UN WORKTREE ne se supprime pas : `git branch -D` la refuse (« checked
# out at … ») quel que soit le répertoire d'où on l'appelle — c'est une protection de git, pas un
# effet de bord du chemin choisi. Elle est donc comptée À PART et NOMMÉE (#305) : jusque-là l'échec
# n'incrémentait AUCUN des deux compteurs, si bien que la branche sortait du compte rendu sans un
# mot — 3 branches sur 41 lors de la purge de rattrapage du 2026-08-07, et un bilan qui annonçait
# moins de branches qu'il n'en avait examinées. C'est aussi pourquoi le ramassage des worktrees
# passe AVANT cette purge dans `ensure` comme dans /branch-cleanup (#197, docs/10 §9.2) : sans lui,
# les branches des worktrees soldés resteraient indéfiniment.
#
# En `--auto` (appel d'office par un point de passage), muet quand il n'y a rien à dire : aucune
# suppression et aucun refus = aucune ligne. Même parti pris que `worktree.sh gc --auto`.
gl_cleanup_merged() {
  local auto=0
  # Des branches NOMMÉES en font une purge ciblée (#438) : celles-là, et rien d'autre. Le drain d'un
  # run merge N PR, et un balayage complet lui coûterait une lecture de forge par branche locale ET
  # par merge. Ce que la cible ne change pas : le garde-fou. `gl_mr_state` reste interrogé pour
  # chacune — le nom d'une branche n'a jamais valu preuve de merge (docs/10 §6), et ce n'est pas
  # parce que l'appelant croit savoir qu'on cesse de demander.
  local -a cibles=()
  # `--sans-fetch` : l'appelant vient de rafraîchir les refs et n'a pas à le repayer (#602). Le
  # `fetch` ci-dessous est du pruning COSMÉTIQUE — la décision s'appuie sur l'état de la PR côté
  # forge, jamais sur lui —, et `worktree.sh ensure` le fait précéder de `sync-main`, qui fetche.
  # C'était donc ~5 s de doublon à chaque /ticket-start. Le drapeau est EXPLICITE et non une
  # fraîcheur devinée d'un horodatage de `FETCH_HEAD` : seul l'appelant sait ce qu'il vient de
  # faire, et une heuristique se tromperait dans le sens qui coûte (sauter un fetch nécessaire).
  local sans_fetch=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --auto) auto=1 ;;
      --sans-fetch) sans_fetch=1 ;;
      '') ;;
      -*) echo "usage: gl_cleanup_merged [--auto] [--sans-fetch] [<branche>…]" >&2; return 2 ;;
      *) cibles+=("$1") ;;
    esac
    shift
  done

  local principal
  principal="$(gl_depot_principal)" || {
    echo "Nettoyage des branches ignoré : hors d'un dépôt git." >&2
    return 0
  }
  if [ -n "$(git -C "$principal" status --porcelain 2>/dev/null)" ]; then
    echo "Nettoyage des branches ignoré : changements non commités présents." >&2
    return 0
  fi
  # Pruning cosmétique des refs de suivi ; non bloquant (jamais de prompt d'identifiants) et non
  # fatal : la décision de suppression s'appuie sur l'état de la PR côté forge, pas sur ce fetch.
  [ "$sans_fetch" = 1 ] || GIT_TERMINAL_PROMPT=0 git -C "$principal" fetch --prune origin >/dev/null 2>&1
  local current branch state porteur deleted=0 kept=0 empruntees=0
  current="$(git -C "$principal" branch --show-current 2>/dev/null)"

  # DEUX TEMPS : on retient d'abord les branches à interroger, puis on les interroge TOUTES EN UNE
  # LECTURE (#602). Une lecture par branche coûtait un aller réseau chacune — 8 branches sur le
  # poste de référence, ~2,5 s l'aller, soit 21 s des 25 que pesait cette purge dans `ensure`.
  # Les filtres LOCAUX (main, branche courante, ref absente) restent AVANT : ils ne coûtent rien et
  # retirent autant de branches de la question.
  local -a examinees=()
  while IFS= read -r branch; do
    [ -z "$branch" ] && continue
    [ "$branch" = "main" ] && continue
    [ "$branch" = "$current" ] && continue
    # Une branche nommée peut ne plus être là (déjà purgée, jamais créée ici) : sans ce contrôle,
    # `git branch -D` échouerait et la ligne « conservée, suppression refusée par git » dirait le
    # contraire de ce qui s'est passé. Sans objet pour le balayage, dont git dicte la liste.
    git -C "$principal" show-ref --verify --quiet "refs/heads/$branch" || continue
    examinees+=("$branch")
  done < <(if [ "${#cibles[@]}" -gt 0 ]; then
             printf '%s\n' "${cibles[@]}"
           else
             git -C "$principal" branch --format='%(refname:short)'
           fi)

  if [ "${#examinees[@]}" -eq 0 ]; then
    [ "$auto" = 1 ] && return 0
    printf 'Nettoyage des branches : 0 supprimée(s), 0 conservée(s).\n'
    return 0
  fi

  # Une branche absente de la réponse n'a pas de PR — donc pas « merged », donc conservée : le
  # silence de gl_mr_briefs vaut exactement ce que valait l'échec de gl_mr_state, et le garde-fou
  # est inchangé (on ne supprime QUE ce que la forge confirme mergé, docs/10 §6). Si la lecture
  # échoue EN ENTIER, la table est vide et tout est conservé : ne rien savoir n'autorise rien.
  local etats
  etats="$(gl_mr_briefs "${examinees[@]}" 2>/dev/null)" || etats=""

  for branch in "${examinees[@]}"; do
    state="$(printf '%s\n' "$etats" | ST_BRANCHE="$branch" awk -F'\t' '$1 == ENVIRON["ST_BRANCHE"] { print $2; exit }')"
    if [ "$state" != "merged" ]; then
      kept=$((kept + 1))
      continue
    fi
    if git -C "$principal" branch -D "$branch" >/dev/null 2>&1; then
      printf '  supprimée : %s (PR merged)\n' "$branch"
      deleted=$((deleted + 1))
      continue
    fi
    porteur="$(gl_worktree_de_branche "$principal" "$branch")"
    if [ -n "$porteur" ]; then
      printf '  ⚠ conservée : %s (PR merged, empruntée par le worktree %s)\n' "$branch" "$porteur"
    else
      printf '  ⚠ conservée : %s (PR merged, suppression refusée par git)\n' "$branch"
    fi
    empruntees=$((empruntees + 1))
  done

  [ "$auto" = 1 ] && [ "$deleted" -eq 0 ] && [ "$empruntees" -eq 0 ] && return 0
  if [ "$empruntees" -gt 0 ]; then
    printf 'Nettoyage des branches : %s supprimée(s), %s conservée(s), %s mergée(s) mais empruntée(s) par un worktree.\n' \
      "$deleted" "$kept" "$empruntees"
  else
    printf 'Nettoyage des branches : %s supprimée(s), %s conservée(s).\n' "$deleted" "$kept"
  fi
}

# --- Fin de vie d'un worktree -------------------------------------------------------------------
# gl_worktree_done <iid> [branche] -> « <verdict><TAB><sha><TAB><raison> » : la seule question que se
# pose le ramassage de scripts/git/worktree.sh (#197) — ce worktree a-t-il encore une raison
# d'exister ? La réponse vient de GitLab, JAMAIS du nom de la branche (garde-fou docs/10 §6).
#
#   fini     PR de la branche MERGÉE, ou ticket FERMÉ (réalisé, abandonné, doublon) ;
#   actif    travail en cours (ticket ouvert, PR absente ou ouverte) — on n'y touche pas ;
#   inconnu  forge illisible (gh absent, hors ligne, ticket introuvable) — on n'y touche pas
#            non plus, et le code de retour 1 le dit : ne rien savoir n'autorise rien.
#
# Le <sha> n'est renseigné que sur un « fini » par merge, et vaut « - » sinon. C'est la tête de la
# branche source AU MOMENT du merge, et la seule référence locale fiable pour distinguer « tout est
# parti » de « il reste des commits ici » : le projet mergeant en SQUASH, les commits de la branche
# ne sont pas des ancêtres de `main`, et GitLab supprime la branche distante au merge — il ne reste
# donc ni `origin/<branche>` à comparer, ni ascendance à tester.
#
# « - » et non un champ vide : dans un TSV lu par `IFS=$'\t' read`, la tabulation est un séparateur
# BLANC, donc deux tabulations consécutives comptent pour une seule et le champ suivant se décale
# (le sha atterrirait dans la raison). Même convention que le plan de scripts/orchestrate/run.sh.
#
# DEUX LECTURES AU PLUS, quel que soit le nombre de worktrees : ce verbe délègue à
# `gl_worktree_done_lot`, qui est la SEULE écriture de la règle (#602). Une seconde formule ici — la
# même question posée d'une autre façon — finirait par ne plus rendre le même verdict que celle qui
# décide, et c'est le ramassage d'un worktree qui en dépend.
gl_worktree_done() {
  local iid="$1" branche="${2:-}" ligne
  if [ -z "$iid" ]; then echo "usage: gl_worktree_done <iid> [branche]" >&2; return 2; fi
  ligne="$(gl_worktree_done_lot "$iid:$branche")"
  # La colonne iid, qui n'a de sens qu'en lot, est retirée : le contrat de CE verbe reste
  # « <verdict><TAB><sha><TAB><raison> », inchangé pour worktree.sh et pour /branch-cleanup.
  ligne="${ligne#*$'\t'}"
  printf '%s\n' "$ligne"
  case "$ligne" in inconnu*) return 1 ;; esac
  return 0
}

# gl_worktree_done_lot <iid>:<branche> … -> le verdict de N worktrees en DEUX LECTURES AU PLUS, une
# ligne TSV par paire, dans l'ordre demandé :
#     <iid><TAB><verdict><TAB><sha><TAB><raison>
# Verdicts, sha et raisons : voir `gl_worktree_done` ci-dessus, dont c'est le corps.
#
# POURQUOI GROUPER (#602, docs/10 §9.8). `worktree.sh gc` posait la question une fois par worktree,
# et chaque appel était un sous-processus complet : chargement de lib.sh, vérification du jeton,
# puis une lecture de la PR et, si elle n'était pas mergée, une lecture du ticket. Soit jusqu'à
# DEUX ALLERS PAR WORKTREE, à 2,5 s l'aller (latence mesurée le 2026-08-27, irréductible). Le prix
# ne se voit pas sur un poste qui n'a qu'un worktree ; il se voit après un run, qui en laisse un par
# ticket traité — 14 worktrees, c'est 28 allers, soit plus d'une minute pour une question qui tient
# en deux. Ici : UNE lecture des PR (`gl_mr_briefs`), puis UNE lecture des tickets que la première
# n'a pas tranchés (`gh_issues_state`) — et la seconde est SAUTÉE quand tout est déjà tranché.
#
# L'ORDRE DES DEUX LECTURES EST LA RÈGLE, et il ne s'inverse pas : la PR d'abord, le ticket ensuite
# et seulement pour ce qu'elle n'a pas soldé. C'est ce qui fait que le sha de merge — la seule
# référence locale exploitable quand le projet merge en squash — accompagne toujours le verdict qui
# en a besoin.
gl_worktree_done_lot() {
  if [ "$#" -eq 0 ]; then echo "usage: gl_worktree_done_lot <iid>:<branche> …" >&2; return 2; fi
  local paire iid branche
  local -a numeros=() branches=()
  for paire in "$@"; do
    iid="${paire%%:*}"; branche="${paire#*:}"
    [ "$branche" = "$paire" ] && branche=""
    case "$iid" in
      ''|*[!0-9]*) echo "gl_worktree_done_lot : iid invalide « $iid »" >&2; return 2 ;;
    esac
    numeros+=("$iid"); branches+=("$branche")
  done

  if ! gl_require >/dev/null 2>&1; then
    for iid in "${numeros[@]}"; do
      printf '%s\tinconnu\t-\tCLI de forge indisponible ou non authentifiée\n' "$iid"
    done
    return 1
  fi

  # 1. Les PR, en une lecture. Une branche vide n'est pas demandée (il n'y a rien à demander) ;
  #    une branche sans PR ne rend simplement aucune ligne.
  local etats='' i
  local -a interrogeables=()
  for i in "${!branches[@]}"; do
    [ -n "${branches[$i]}" ] && interrogeables+=("${branches[$i]}")
  done
  if [ "${#interrogeables[@]}" -gt 0 ]; then
    etats="$(gl_mr_briefs "${interrogeables[@]}" 2>/dev/null)" || etats=""
  fi

  # 2. Les tickets que la première lecture n'a pas soldés, en une lecture — et zéro si la liste est
  #    vide. C'est le cas nominal d'un run tout mergé : une seule lecture pour tous ses worktrees.
  local brief etat mr sha
  local -a restants=()
  local -a v_etat=() v_mr=() v_sha=()
  for i in "${!numeros[@]}"; do
    etat=""; mr=""; sha=""
    if [ -n "${branches[$i]}" ]; then
      brief="$(printf '%s\n' "$etats" | ST_BRANCHE="${branches[$i]}" awk -F'\t' '$1 == ENVIRON["ST_BRANCHE"] { print $2 "\t" $3 "\t" $4; exit }')"
      if [ -n "$brief" ]; then IFS=$'\t' read -r etat mr sha <<< "$brief"; fi
    fi
    v_etat+=("$etat"); v_mr+=("$mr"); v_sha+=("$sha")
    [ "$etat" = "merged" ] || restants+=("${numeros[$i]}")
  done

  local ouverts=''
  if [ "${#restants[@]}" -gt 0 ]; then
    ouverts="$(gh_issues_state "${restants[@]}" 2>/dev/null)" || ouverts=""
  fi

  local etat_ticket
  for i in "${!numeros[@]}"; do
    if [ "${v_etat[$i]}" = "merged" ]; then
      printf '%s\tfini\t%s\tPR #%s mergée\n' "${numeros[$i]}" "${v_sha[$i]:--}" "${v_mr[$i]:-?}"
      continue
    fi
    # Un iid absent de la réponse est un ticket illisible — même parti pris que gh_issues_state, et
    # `inconnu` est exactement ce que le ramassage doit en faire : ne rien savoir n'autorise rien.
    etat_ticket="$(printf '%s\n' "$ouverts" | ST_IID="${numeros[$i]}" awk -F'\t' '$1 == ENVIRON["ST_IID"] { print $2; exit }')"
    case "$etat_ticket" in
      closed) printf '%s\tfini\t-\tticket #%s fermé (PR « %s »)\n' "${numeros[$i]}" "${numeros[$i]}" "${v_etat[$i]:-aucune}" ;;
      open)   printf '%s\tactif\t-\tticket #%s « open » (PR « %s »)\n' "${numeros[$i]}" "${numeros[$i]}" "${v_etat[$i]:-aucune}" ;;
      *)      printf '%s\tinconnu\t-\tticket #%s illisible dans %s\n' "${numeros[$i]}" "${numeros[$i]}" "$(gl_depot_courant)" ;;
    esac
  done
}

# --- Réglages du dépôt : garde-fous de merge (#341) -----------------------------------------------
# La lecture de doctor.sh qui porte sur le DÉPÔT et non sur ses tickets. Elle est ici plutôt que
# dans doctor.sh pour que le CLI de la forge ne soit appelé QUE dans ce fichier, ce qui rend le grep
# du contrat vérifiable d'un coup d'œil.
#
# Ses deux voisines — colonnes du Kanban, runners de projet — sont parties avec la branche GitLab
# (#344) : elles n'avaient pas d'équivalent GitHub et rendaient un CODE DE RETOUR 3, réservé à « ce
# contrôle ne s'applique pas à cette forge ». Le 3 n'a plus d'objet ici, et cette distinction
# comptait : sans elle, le bilan afficherait « board illisible »
# sur un dépôt GitHub qui n'a tout simplement pas de board GitLab — un avertissement inventé, que
# `--strict` ferait remonter en échec de CI.

# gl_merge_settings -> les garde-fous de merge du dépôt, NORMALISÉS (une clé par ligne) :
#     pipeline_requis          <TAB> true|false|-
#     merge_si_pipeline_saute  <TAB> true|false|-
#     suppression_branche      <TAB> true|false|-
# « - » = la forge n'a pas répondu sur ce point. Les trois clés existent des deux côtés parce que ce
# sont les trois PROMESSES du workflow (docs/10 §6), pas trois champs d'API : côté GitLab elles se
# lisent dans les réglages du projet, côté GitHub dans la protection de branche de `main` et le
# réglage `delete_branch_on_merge` du dépôt.

# gl_champ_booleen <json> <clé> -> « true » / « false » / « - » (clé absente, ou `null` : ces champs
# reviennent null selon le tier GitLab, et null n'est PAS false — seule la valeur explicite attendue
# vaut un ✓ chez l'appelant).
gl_champ_booleen() {
  case "$1" in
    *"\"$2\":true"*)  printf 'true\n' ;;
    *"\"$2\":false"*) printf 'false\n' ;;
    *)                printf -- '-\n' ;;
  esac
}

gl_merge_settings() {
  gh_merge_settings
}

# --- Utilitaires de nommage ---------------------------------------------------------------------
# gl_slug <titre> -> slug de branche : minuscules, accents retirés, non-alphanum -> '-',
# tirets collapsés, tronqué à 40 caractères, sans tiret de bord.
gl_slug() {
  local s="$1"
  if command -v iconv >/dev/null 2>&1; then
    # glibc TRANSLIT rend « é » -> « 'e », « è » -> « `a »… : on retire ces marques d'accent
    # (', `, ^, ~, ") pour que l'accent disparaisse au lieu de couper le mot en deux.
    s="$(printf '%s' "$s" | iconv -f UTF-8 -t ASCII//TRANSLIT 2>/dev/null | sed "s/[\`'^~\"]//g")"
  fi
  printf '%s' "$s" \
    | tr '[:upper:]' '[:lower:]' \
    | sed -e "s/[^a-z0-9]\+/-/g" -e "s/^-\+//" -e "s/-\+$//" \
    | cut -c1-40 \
    | sed -e "s/-\+$//"
}

# gl_branch_prefix <type> -> préfixe de branche depuis un type (accepte "feature" ou
# "type::feature"). feature->feat, bug->fix, infra->chore, doc->docs.
gl_branch_prefix() {
  case "${1#type::}" in
    feature) echo feat ;;
    bug)     echo fix ;;
    infra)   echo chore ;;
    doc)     echo docs ;;
    *) echo "Type inconnu : « $1 » (attendu : feature|bug|infra|doc)" >&2; return 1 ;;
  esac
}

# --- Branche de travail & worktrees --------------------------------------------------------------
# gl_branch_from_raw <iid> (stdin = sortie brute de gl_issue_raw) -> nom de la branche de
# travail : <préfixe du label type::>/<iid>-<slug du titre>. Fonction PURE : elle ne lit pas le
# ticket, ce qui permet à gl_start_brief de la nourrir avec la lecture qu'il a déjà faite.
# Sans label type::, imprime le préfixe littéral « <type> » et renvoie 3 — à l'appelant de le
# déduire du titre plutôt que de fabriquer une branche mal nommée.
gl_branch_from_raw() {
  local iid="$1" raw title labels type prefix slug
  raw="$(cat)"
  title="$(printf '%s\n' "$raw" | sed -n 's/^title:[[:space:]]*//p' | head -1)"
  labels="$(printf '%s\n' "$raw" | sed -n 's/^labels:[[:space:]]*//p' | head -1)"
  type="$(printf '%s' "$labels" | grep -o 'type::[a-z]*' | head -1)"
  slug="$(gl_slug "$title")"
  if [ -n "$type" ] && prefix="$(gl_branch_prefix "$type" 2>/dev/null)"; then
    printf '%s/%s-%s\n' "$prefix" "$iid" "$slug"
    return 0
  fi
  printf '<type>/%s-%s\n' "$iid" "$slug"
  return 3
}

# gl_branch_for <iid> -> même chose, en lisant le ticket (une lecture). Sert à scripts/git/worktree.sh,
# qui n'a pas de brief sous la main.
gl_branch_for() {
  local iid="$1" raw
  if [ -z "$iid" ]; then echo "usage: gl_branch_for <iid>" >&2; return 2; fi
  gl_require || return 1
  raw="$(gl_issue_raw "$iid")" || return 1
  printf '%s\n' "$raw" | gl_branch_from_raw "$iid"
}

# Sommes-nous dans un worktree LIÉ (`git worktree add`) plutôt que dans le clone principal ?
# Signature universelle et sans dépendance de version : à la racine d'un worktree lié, `.git` est
# un FICHIER (« gitdir: … ») là où le clone principal porte un répertoire.
gl_in_linked_worktree() {
  local top
  top="$(git rev-parse --show-toplevel 2>/dev/null)" || return 1
  [ -f "$top/.git" ]
}

# gl_depot_principal -> racine du clone PRINCIPAL, d'où que l'on appelle (worktree lié compris) :
# le répertoire git commun est partagé par tous les worktrees d'un dépôt, son parent est le clone
# principal. Jumeau de `depot_principal` dans scripts/git/worktree.sh, qui appelle ce fichier en
# SOUS-PROCESSUS (jamais en le sourçant) et ne peut donc pas la lui emprunter.
gl_depot_principal() {
  local commun
  commun="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)"
  if [ -z "$commun" ]; then
    commun="$(git rev-parse --git-common-dir 2>/dev/null)" || return 1
    commun="$(cd "$commun" 2>/dev/null && pwd)" || return 1
  fi
  [ -n "$commun" ] || return 1
  dirname "$commun"
}

# gl_start_branch <branche> -> place le dépôt sur la branche de travail, que l'on soit dans le
# clone principal ou dans un worktree lié (docs/10-workflow-git.md §9). Idempotent, trois cas :
#   - déjà sur la branche (situation normale dans un worktree créé par scripts/git/worktree.sh) ;
#   - branche locale existante -> bascule ;
#   - branche absente -> création depuis `origin/main` à jour.
# Dans le clone principal, `main` est rafraîchi au passage. Dans un worktree lié on ne passe JAMAIS
# par `git checkout main` : `main` est déjà emprunté par le clone principal, et git refuse
# d'emprunter deux fois la même branche.
#
# ⚠ Ce helper ne purge PLUS les branches mergées (#305). Il l'a fait de #23 à #305, à l'époque où
# il était le point de passage qui mettait `main` à jour ; depuis #181 c'est `worktree.sh ensure`
# qui tient ce rôle, et l'appel n'était plus joignable — /ticket-start appelle `ensure` d'abord, si
# bien que `start-branch` sort soit par « déjà sur la branche », soit par la voie worktree lié,
# jamais par celle qui purgeait. Le résultat s'est vu à l'œil nu : 35 branches mergées accumulées
# sur le clone principal, la plus ancienne remontant à #220. Garder un second point d'appel
# inatteignable est exactement ce qui a rendu la régression invisible — la purge a donc UN seul
# déclencheur automatique, `ensure` (plus /branch-cleanup à la demande).
gl_start_branch() {
  local branche="$1" courante
  if [ -z "$branche" ]; then echo "usage: gl_start_branch <branche>" >&2; return 2; fi
  case "$branche" in
    *'<type>'*)
      echo "Branche sans préfixe : « $branche » — déduire le type (feat|fix|chore|docs) avant de démarrer." >&2
      return 2 ;;
  esac

  courante="$(git branch --show-current 2>/dev/null)"
  if [ "$courante" = "$branche" ]; then
    GIT_TERMINAL_PROMPT=0 git fetch origin main >/dev/null 2>&1
    printf 'Déjà sur %s — rien à créer.\n' "$branche"
    return 0
  fi

  if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
    echo "Arbre de travail non propre : committer, stasher ou annuler avant de changer de branche." >&2
    return 1
  fi

  if git show-ref --verify --quiet "refs/heads/$branche"; then
    git checkout "$branche" || return 1
    printf 'Branche existante : %s (bascule).\n' "$branche"
    return 0
  fi

  if gl_in_linked_worktree; then
    git fetch origin main || return 1
    git checkout -b "$branche" origin/main || return 1
  else
    git checkout main || return 1
    git pull origin main || return 1
    git checkout -b "$branche" || return 1
  fi
  printf 'Branche créée : %s (depuis origin/main).\n' "$branche"
}

# --- Repères des worktrees ------------------------------------------------------------------------
# gl_worktree_de_branche <clone-principal> <branche> -> chemin du répertoire de travail qui a cette
# branche en HEAD, vide si elle n'est empruntée nulle part. Deux appelants, deux questions : pour
# gl_sync_main, COMMENT avancer `main` (poser la ref, ou merge --ff-only dans le répertoire qui
# l'emprunte) ; pour gl_cleanup_merged, QUI retient une branche mergée que `git branch -D` refuse
# de supprimer (#305).
#
# Le motif du `case` est entre GUILLEMETS : sans eux le nom de branche serait interprété comme un
# motif, et un slug porteur d'un `?` ou d'un `*` matcherait la mauvaise ligne.
gl_worktree_de_branche() {
  local principal="$1" branche="$2" courant="" ligne
  while IFS= read -r ligne; do
    case "$ligne" in
      worktree\ *)                  courant="${ligne#worktree }" ;;
      "branch refs/heads/$branche") printf '%s' "$courant"; return 0 ;;
    esac
  done < <(git -C "$principal" worktree list --porcelain 2>/dev/null)
  return 1
}

# --- Mise à jour de la branche main locale --------------------------------------------------------
# gl_worktree_de_main <clone-principal> -> le cas particulier de `main` (voir gl_sync_main).
gl_worktree_de_main() { gl_worktree_de_branche "${1:-}" main; }

# gl_sync_main [--check] -> avance `refs/heads/main` du CLONE PRINCIPAL sur `origin/main`, en
# FAST-FORWARD seulement (#205).
#
# Le retard n'est pas un détail cosmétique : depuis #181, /ticket-start monte un worktree et y
# relocalise la session, donc le clone principal ne change plus de branche et la branche « clone
# principal » de gl_start_branch (`git checkout main && git pull`) n'est plus jamais empruntée.
# Plus rien ne faisait avancer `main` — ce que montrent l'IDE, `git log` et un diff local sur le
# clone principal restait figé au dernier /branch-cleanup. À NE PAS confondre avec `origin/main`,
# lui déjà rafraîchi partout (gl_start_branch, gl_cleanup_merged, worktree.sh) : c'est de lui que
# part chaque worktree de ticket, le code produit n'a donc jamais été en cause.
#
# Il n'existe aucun événement local à écouter : le merge a lieu sur GitLab et aucun hook git ne se
# déclenche à ce moment-là (`post-merge` ne réagit qu'à un merge ou un pull LOCAL). D'où le
# câblage aux points de passage obligés — `worktree.sh ensure` (donc tout /ticket-start, manuel
# comme autonome) et /branch-cleanup — plutôt qu'un déclencheur événementiel qui n'existe pas.
#
# Deux façons d'avancer la ref, selon que `main` est empruntée ou non par un répertoire de travail :
# posée directement (`update-ref`, aucun fichier touché, marche depuis un worktree), ou par
# `merge --ff-only` DANS ce répertoire — sans quoi l'index y resterait sur l'ancien arbre et tout
# le delta apparaîtrait en « supprimé/modifié ».
#
# S'ABSTIENT plutôt que de forcer, dans la lignée de gl_behind_main et de worktree.sh gc : ça dit,
# ça ne casse pas. Jamais de `reset --hard`, jamais de non-fast-forward — un `main` local divergent
# porte un commit que personne n'a poussé, l'écraser serait une perte de données.
#
# Codes de retour, pour l'appelant (best-effort : un code non nul n'est PAS une erreur fatale, il
# ne doit interrompre ni un /ticket-start ni un run /orchestrate) :
#   0 = à jour, ou mise à jour faite      3 = main local divergent (non fast-forward) — abstention
#   1 = état illisible (hors dépôt git, origin/main absent)
#   2 = usage                             4 = répertoire porteur de main non propre — abstention
gl_sync_main() {
  local check=0
  case "${1:-}" in
    --check) check=1 ;;
    '') ;;
    *) echo "usage: gl_sync_main [--check]" >&2; return 2 ;;
  esac

  local principal
  principal="$(gl_depot_principal)" || {
    echo "sync-main : hors d'un dépôt git — mise à jour de main sautée." >&2
    return 1
  }

  # Fetch non bloquant (jamais de prompt d'identifiants, cf. gl_behind_main) : hors ligne on
  # retombe sur le dernier origin/main connu, qu'un fetch précédent a pu avancer.
  GIT_TERMINAL_PROMPT=0 git -C "$principal" fetch origin main >/dev/null 2>&1
  local cible locale
  cible="$(git -C "$principal" rev-parse --verify --quiet refs/remotes/origin/main 2>/dev/null)"
  if [ -z "$cible" ]; then
    echo "sync-main : origin/main introuvable — mise à jour de main sautée." >&2
    return 1
  fi
  locale="$(git -C "$principal" rev-parse --verify --quiet refs/heads/main 2>/dev/null)"

  # Le cas de loin le plus fréquent, et le seul qui ne mérite aucune ligne : rien à faire.
  [ "$locale" = "$cible" ] && return 0

  if [ -n "$locale" ] && ! git -C "$principal" merge-base --is-ancestor "$locale" "$cible" 2>/dev/null; then
    printf '⚠ sync-main : main local a divergé de origin/main — mise à jour sautée (jamais de force).\n' >&2
    printf '  à trancher à la main : git -C "%s" log --oneline origin/main..main\n' "$principal" >&2
    return 3
  fi

  local retard porteur
  if [ -n "$locale" ]; then
    retard="$(git -C "$principal" rev-list --count "$locale..$cible" 2>/dev/null)" || retard="?"
  else
    retard="0"   # `main` locale absente : ce n'est pas un retard, c'est une création
  fi
  porteur="$(gl_worktree_de_main "$principal")"

  if [ -n "$porteur" ]; then
    if [ -n "$(git -C "$porteur" status --porcelain 2>/dev/null)" ]; then
      printf '⚠ sync-main : main en retard de %s commit(s), mais son répertoire de travail a des changements non commités — mise à jour sautée.\n' "$retard" >&2
      printf '  %s\n' "$porteur" >&2
      return 4
    fi
    if [ "$check" = 1 ]; then
      printf 'sync-main : main avancerait de %s commit(s) (merge --ff-only dans %s).\n' "$retard" "$porteur"
      return 0
    fi
    if ! git -C "$porteur" merge --ff-only origin/main >/dev/null 2>&1; then
      printf '⚠ sync-main : fast-forward de main refusé par git — mise à jour sautée.\n' >&2
      return 3
    fi
  else
    if [ "$check" = 1 ]; then
      printf 'sync-main : main avancerait de %s commit(s) (pose de la ref, aucun répertoire de travail concerné).\n' "$retard"
      return 0
    fi
    # `main` n'est empruntée nulle part : la ref se pose directement, sans toucher au moindre
    # fichier — c'est ce qui rend l'appel valide depuis un worktree. Le fast-forward vient d'être
    # vérifié ; l'ancienne valeur est passée en dernier argument pour que git refuse d'écrire si
    # quelqu'un a bougé la ref entre-temps.
    if ! git -C "$principal" update-ref -m "sync-main : fast-forward sur origin/main" \
        refs/heads/main "$cible" ${locale:+"$locale"} 2>/dev/null; then
      printf '⚠ sync-main : pose de refs/heads/main refusée — mise à jour sautée.\n' >&2
      return 3
    fi
  fi

  printf 'main mis à jour : %s commit(s) repris depuis origin/main.\n' "$retard"
}

# --- « Quelqu'un s'occupe-t-il encore de ce ticket ? » (#328) --------------------------------------
# Sixième membre de la famille des réconciliations — `worktree.sh gc` (§9.2), `reconcile-workflow`
# (#275), `sync-main` (§9.3), `setup --derive` (§9.4), `cleanup-merged` (§9.5) — et celui qui
# manquait le plus. Un ticket entre en « En cours » (et s'assigne) à /ticket-start ; il n'en sort que
# par /ticket-ship, /ticket-finish ou /ticket-abandon. LA TROISIÈME SORTIE EST L'ABSENCE DE SORTIE :
# session coupée par un délai, pilote arrêté au `taskkill` (aucun trap ne s'exécute), console fermée,
# limite d'usage épuisée, session interactive laissée en plan. Le ticket reste « En cours » ET
# assigné — c'est-à-dire exactement le filtre par lequel `queue.sh` l'écarte : la règle
# d'anti-collision qui protège le travail vivant cache définitivement le travail mort. Deux tickets
# dans cet état au constat du 2026-08-11 : #316 (2047 lignes commitées, jamais poussées, plus sept
# lots sautés en cascade) et #325 (396 lignes non commitées dans son worktree).
#
# LE RENVERSEMENT DE QUESTION FAIT TOUT LE TRAVAIL. On ne demande pas « ce run a-t-il échoué ? » — un
# pilote tué ne pose aucun verdict, or c'est précisément lui qui fabrique l'orphelin — mais
# « quelqu'un s'occupe-t-il encore de ce ticket ? ». Posée ainsi, la question couvre tous les modes de
# mort, run ou pas, session interactive comprise.
#
# DEUX SOURCES, DANS CET ORDRE :
#   1. la CARTE DU PILOTE (#213) — un run vivant qui nomme le ticket parmi ceux en vol. Elle est
#      VÉRIFIABLE (PID, naissance du processus, hôte), donc c'est une preuve et non un indice ;
#   2. sinon la FRAÎCHEUR DU WORKTREE, annoncée comme une DÉDUCTION — précédent de `status.sh`, dont
#      l'état « en cours » se lit dans la carte quand elle est là et se déduit sinon.
#
# ⚠ La carte ne prouve JAMAIS la mort, seulement la vie : un pilote mort ne dit rien du ticket, qu'une
# session interactive a très bien pu reprendre depuis. Même asymétrie que dans `pilote.sh`, et elle va
# toujours dans le même sens — désigner à tort le ticket d'une session vivante coûte infiniment plus
# cher que de rater un orphelin d'un tour, puisque #329 rendra l'orphelin prenable.
#
# TROIS VERDICTS et non deux, parce que « je ne sais pas » est une réponse :
#   vivant       quelqu'un est dessus (carte du pilote, ou worktree écrit récemment) ;
#   orphelin     worktree présent ICI, silencieux depuis plus de GL_ORPHELIN_SEUIL, et aucun pilote
#                vivant ne le nomme ;
#   hors-portee  aucun worktree sur cette machine — rien à en dire, et surtout pas que c'est un
#                orphelin : le ticket peut être en plein travail sur le clone de quelqu'un d'autre.
#                La couverture est celle des worktrees de CETTE machine, comme `gc` et
#                `cleanup-merged` ; elle se dit dans la sortie plutôt que de laisser croire à un
#                balayage global.
#
# EN LECTURE SEULE, entièrement : ce verbe SIGNALE. Il ne pose aucun label, ne touche à aucune
# assignation, ne retire aucun worktree et n'écrit rien côté GitLab. Le geste de reprise est celui de
# #329, et il est explicite — toute la famille signale sans décider. `--check` est accepté par
# cohérence de famille et n'a donc aucun effet : le refuser serait un piège pour la main qui vient de
# taper `reconcile-workflow --check`.
#
# Modes :
#   --auto        ne parle que s'il y a un orphelin. C'est ainsi que `worktree.sh gc` l'appelle, donc
#                 /ticket-start, /branch-cleanup et le démarrage d'un run — mêmes points de passage
#                 que #275 et pour la même raison : greffer sur `gc` les sert tous les trois d'un
#                 coup, sans ajouter d'étape à `ensure` ;
#   --tsv         la surface machine « iid <TAB> verdict <TAB> source <TAB> détail <TAB> titre »,
#                 que #329 consommera plutôt que de relire une phrase en français ;
#   --sauf <iid>  écarte un ticket. `ensure` s'en sert pour celui qu'il est en train de démarrer :
#                 le signaler orphelin serait vrai une seconde et faux la suivante.
#
# Codes de retour : 0 = rien à signaler · 3 = au moins un orphelin (même convention que
# `setup.sh --derive`) · 1 = backlog illisible · 2 = usage. AUCUN n'est un motif de blocage pour un
# appelant : ce verbe ne doit jamais empêcher un ticket de démarrer ni un run de continuer.

# gl_mtime <chemin> -> date de dernière modification, en secondes Unix. GNU (`stat -c`) puis BSD
# (`stat -f`) puis `date -r` : le dépôt tourne sous Git Bash comme sur un runner Linux. Jumeau de
# `mtime` dans scripts/orchestrate/status.sh, qui ne source pas ce fichier.
gl_mtime() {
  stat -c %Y "$1" 2>/dev/null || stat -f %m "$1" 2>/dev/null || date -r "$1" +%s 2>/dev/null
}

# gl_duree_lisible <secondes> -> « 42s », « 12min30 », « 3h05 ». Même formule que status.sh, pour
# que deux sorties qui parlent du même silence l'écrivent pareil.
gl_duree_lisible() {
  local s="${1:-0}"
  [ "$s" -ge 0 ] 2>/dev/null || s=0
  if [ "$s" -lt 60 ]; then printf '%ds' "$s"
  elif [ "$s" -lt 3600 ]; then printf '%dmin%02d' $((s / 60)) $((s % 60))
  else printf '%dh%02d' $((s / 3600)) $(((s % 3600) / 60)); fi
}

# gl_orch_dir -> le journal d'orchestration du CLONE PRINCIPAL (`.maestro/orchestrate`), d'où qu'on
# appelle. Même résolution que `journal.sh` (#307) et pour la même raison : depuis un worktree, le
# `.maestro/orchestrate` local est un répertoire vide, et la carte des pilotes serait invisible.
gl_orch_dir() {
  local principal
  principal="$(gl_depot_principal)" || return 1
  printf '%s/.maestro/orchestrate' "$principal"
}

# gl_pilotes_en_vol -> une ligne « run-id <TAB> pid <TAB> iids-en-vol » par run VIVANT. Sortie vide =
# personne ne tourne, et c'est le cas courant : le silence est la réponse normale.
#
# La lecture est DÉLÉGUÉE à scripts/orchestrate/pilote.sh, seul endroit qui sache relire une carte et
# la vérifier (PID recyclé, zombie, autre machine) — deux formules qui divergeraient se remarqueraient
# trop tard. Le fichier peut manquer (dépôt jetable des tests, clone partiel) : on rend alors le
# silence, et la déduction tranche seule.
gl_pilotes_en_vol() {
  local orch
  orch="$(gl_orch_dir)" || return 1
  [ -d "$orch" ] || return 1
  [ -r "$GL_ICI/../orchestrate/pilote.sh" ] || return 1
  # shellcheck source=scripts/orchestrate/pilote.sh
  . "$GL_ICI/../orchestrate/pilote.sh" || return 1
  pilotes_vivants "$orch"
}

# gl_branche_du_iid <clone-principal> <iid> -> la branche LOCALE du ticket, d'après la convention
# `<type>/<iid>-<slug>`. Lue dans git plutôt que demandée à GitLab : c'est gratuit, hors ligne, et ça
# marche encore quand le label `type::` a changé depuis (même formule que status.sh).
gl_branche_du_iid() {
  git -C "$1" for-each-ref --format='%(refname:short)' refs/heads 2>/dev/null |
    awk -v iid="$2" 'index($0, "/" iid "-") || $0 ~ ("/" iid "$") { print; exit }'
}

# gl_worktree_activite <chemin> -> l'instant de la dernière écriture attribuable à une session qui
# travaille là, en secondes Unix. Rien (code 1) si le répertoire a disparu ou n'apprend rien.
#
# TROIS TÉMOINS, le plus récent l'emporte — aucun ne suffit seul :
#   • l'INDEX git, touché par tout `git add`/`commit`/`status` de la session (même témoin que
#     status.sh, pour qui c'est « le signal de progression le plus fiable ») ;
#   • les FICHIERS que `git status` rend modifiés ou non suivis : une session qui édite du code
#     pendant quarante minutes sans lancer une seule commande git laisse l'index froid et les
#     fichiers brûlants ;
#   • l'ATELIER DE SESSION `.maestro/session/` (#307), gitignoré donc invisible du deuxième témoin,
#     et par construction l'endroit où une session pose ses fichiers de travail.
#
# ⚠ L'index est lu AVANT le `git status`, et celui-ci passe par `--no-optional-locks` : un
# `git status` ordinaire RÉÉCRIT l'index (rafraîchissement du cache de stat), donc le mesurer le
# rendrait frais — l'outil de mesure produirait la fraîcheur qu'il mesure, et plus aucun worktree ne
# serait jamais silencieux.
gl_worktree_activite() {
  local wt="$1" index="" f t max="" n=0 atelier
  [ -d "$wt" ] || return 1

  index="$(git -C "$wt" rev-parse --git-path index 2>/dev/null)"
  # `--git-path` rend un chemin ABSOLU pour un worktree lié, mais RELATIF (« .git/index ») pour un
  # répertoire de travail principal : sans cette reprise il serait résolu depuis le répertoire
  # courant, et l'activité passerait pour nulle (même piège que status.sh).
  case "$index" in /* | ?:[/\\]*) ;; *) [ -n "$index" ] && index="$wt/$index" ;; esac
  if [ -n "$index" ] && t="$(gl_mtime "$index")" && [ -n "$t" ]; then max="$t"; fi

  # Sortie en `-z` : un chemin porteur d'espaces ou d'accents y voyage tel quel, là où le format
  # ordinaire le met entre guillemets et l'échappe. Bornée à cent entrées — dater une session au
  # travail n'en demande pas plus, et un worktree qui en porte mille ne doit pas coûter mille `stat`.
  # Les lignes de renommage rendent l'ancien chemin dans un enregistrement à part, qui ne se `stat`
  # pas : il est sauté comme tout chemin illisible, sans conséquence sur le maximum.
  while IFS= read -r -d '' f; do
    n=$((n + 1)); [ "$n" -gt 100 ] && break
    f="${f:3}"
    [ -n "$f" ] || continue
    t="$(gl_mtime "$wt/$f")" || continue
    [ -n "$t" ] || continue
    if [ -z "$max" ] || [ "$t" -gt "$max" ]; then max="$t"; fi
  done < <(git --no-optional-locks -C "$wt" status --porcelain -z 2>/dev/null)

  atelier="$wt/.maestro/session"
  if [ -d "$atelier" ]; then
    for f in "$atelier" "$atelier"/*; do
      [ -e "$f" ] || continue
      t="$(gl_mtime "$f")" || continue
      [ -n "$t" ] || continue
      if [ -z "$max" ] || [ "$t" -gt "$max" ]; then max="$t"; fi
    done
  fi

  [ -n "$max" ] || return 1
  printf '%s' "$max"
}

# gl_en_cours_des_worktrees <clone-principal> -> les tickets « En cours » PARMI CEUX QUI ONT UN
# WORKTREE ICI, dans la forme de `gl_backlog_table` (colonne pour colonne, titre vide) pour que
# `gl_reconcile_en_cours` les lise sans changer d'une ligne la règle qui départage un vivant d'un
# orphelin (#602).
#
# LA LECTURE EST BORNÉE PAR LE DISQUE, ET C'EST TOUT LE GAIN : les iid viennent de `git worktree
# list` — local, gratuit —, et un seul aller (`st_statuts`, #577) rend leur état. La table complète
# en demandait sept, dont cinq de pagination, pour finir par écarter tout ce qui n'a pas de worktree.
#
# Rend zéro ligne — et le code 0 — quand aucun worktree ne porte d'iid : il n'y a rien à demander,
# et une lecture pour l'apprendre serait une lecture de trop. Le code 1 est réservé à la LECTURE EN
# ÉCHEC, que l'appelant annonce comme un contrôle sauté.
gl_en_cours_des_worktrees() {
  local principal="$1" ligne branche nom iid
  if [ -z "$principal" ]; then echo "usage: gl_en_cours_des_worktrees <clone-principal>" >&2; return 2; fi

  local -a numeros=()
  while IFS= read -r ligne; do
    case "$ligne" in
      branch\ refs/heads/*)
        branche="${ligne#branch refs/heads/}"
        nom="${branche#*/}"; iid="${nom%%-*}"
        case "$iid" in ''|*[!0-9]*) continue ;; esac
        numeros+=("$iid") ;;
    esac
  done < <(git -C "$principal" worktree list --porcelain 2>/dev/null)

  [ "${#numeros[@]}" -gt 0 ] || return 0

  local statuts
  statuts="$(st_statuts "${numeros[@]}" 2>/dev/null)" || return 1
  # « - » aux trois colonnes du milieu et non deux tabulations d'affilée : dans un TSV lu par
  # `IFS=$'\t' read`, la tabulation est un séparateur BLANC, donc un champ vide décalerait tous les
  # suivants (même convention que le plan de run.sh et que gl_worktree_done).
  printf '%s\n' "$statuts" | awk -F'\t' '$2 == "En cours" { print $1 "\t" $2 "\t-\t-\t-\t" }'
}

gl_reconcile_en_cours() {
  local auto=0 tsv=0 sauf=""
  while [ $# -gt 0 ]; do
    case "$1" in
      # Accepté et sans effet : ce verbe est en lecture seule par nature (cf. en-tête).
      --check) ;;
      --auto)  auto=1 ;;
      --tsv)   tsv=1 ;;
      --sauf)  sauf="${2:-}"; shift ;;
      *) echo "usage: gl_reconcile_en_cours [--check] [--auto] [--tsv] [--sauf <iid>]" >&2; return 2 ;;
    esac
    shift
  done

  local principal
  principal="$(gl_depot_principal)" || {
    echo "reconcile-en-cours : hors d'un dépôt git — contrôle sauté." >&2
    return 1
  }
  # DEUX LECTURES POSSIBLES, ET C'EST LE MODE QUI TRANCHE (#602, docs/10 §9.8).
  #
  # `--auto` — l'appel d'office, celui de `worktree.sh gc`, donc de tout /ticket-start — ne peut
  # rendre QU'UN ORPHELIN : il sort muet sur les vivants comme sur les hors-de-portée (voir plus
  # bas). Or « orphelin » est un verdict qui se déduit d'un WORKTREE PRÉSENT ICI et de son silence :
  # un ticket sans worktree sur cette machine est hors de portée, par construction et quel que soit
  # son état. Partir des worktrees ne peut donc RIEN perdre en `--auto` — et ça évite de payer le
  # backlog entier pour une question qui ne porte que sur deux ou trois tickets.
  # Le prix, lui, était le poste le plus lourd de tout `ensure` : `gl_backlog_table` résout le
  # projet puis en pagine les items (5 pages à 577 tickets) et lit les issues ouvertes, soit SEPT
  # allers — 29,5 s mesurées le 2026-08-27, sur un `ensure` qui en pesait 68,7. `st_statuts` répond
  # à la même question pour des iid NOMMÉS en un seul aller (#577).
  #
  # Les modes HUMAIN et `--tsv`, eux, gardent la lecture d'ensemble : ils rendent un RECENSEMENT,
  # avec ses lignes « hors de portée » et son compte des trois verdicts, et c'est justement ce que
  # la lecture bornée ne peut pas produire. Ils sont demandés explicitement, par quelqu'un qui lit —
  # jamais sur le chemin d'un démarrage de ticket.
  local table
  if [ "$auto" = 1 ]; then
    table="$(gl_en_cours_des_worktrees "$principal")" || {
      echo "reconcile-en-cours : états des worktrees illisibles — contrôle sauté." >&2
      return 1
    }
  else
    table="$(gl_backlog_table opened)" || {
      echo "reconcile-en-cours : backlog illisible — contrôle sauté." >&2
      return 1
    }
  fi
  # Une lecture des cartes pour tout le monde aussi : `pilotes_vivants` balaie tous les runs.
  local pilotes
  pilotes="$(gl_pilotes_en_vol 2>/dev/null)" || pilotes=""

  local maintenant iid statut titre run branche wt activite silence
  local verdict origine detail lignes="" orphelins=0 vivants=0 hors=0
  maintenant="$(date +%s)"

  while IFS=$'\t' read -r iid statut _ _ _ titre; do
    case "$iid" in ''|'#'*|*[!0-9]*) continue ;; esac
    [ "$statut" = "En cours" ] || continue
    [ -n "$sauf" ] && [ "$iid" = "$sauf" ] && continue

    # 1. La carte du pilote, qui fait foi quand elle est là.
    run="$(printf '%s\n' "$pilotes" | awk -F'\t' -v iid="$iid" '
      { n = split($3, v, ","); for (i = 1; i <= n; i++) if (v[i] == iid) { print $1 "\t" $2; exit } }')"
    if [ -n "$run" ]; then
      verdict="vivant"; origine="carte du pilote"
      detail="run ${run%%$'\t'*}, pilote pid ${run##*$'\t'}"
      vivants=$((vivants + 1))
    else
      # 2. Sinon la déduction, annoncée comme telle.
      branche="$(gl_branche_du_iid "$principal" "$iid")"
      wt=""
      [ -n "$branche" ] && wt="$(gl_worktree_de_branche "$principal" "$branche")"
      if [ -z "$wt" ] || [ ! -d "$wt" ]; then
        verdict="hors-portee"; origine="hors de portée"
        detail="aucun worktree sur cette machine"
        hors=$((hors + 1))
      elif ! activite="$(gl_worktree_activite "$wt")" || [ -z "$activite" ]; then
        verdict="hors-portee"; origine="hors de portée"
        detail="worktree illisible : $wt"
        hors=$((hors + 1))
      else
        silence=$((maintenant - activite))
        [ "$silence" -ge 0 ] || silence=0
        if [ "$silence" -lt "$GL_ORPHELIN_SEUIL" ]; then
          verdict="vivant"; origine="déduction"
          detail="worktree écrit il y a $(gl_duree_lisible "$silence")"
          vivants=$((vivants + 1))
        else
          verdict="orphelin"; origine="déduction"
          detail="worktree silencieux depuis $(gl_duree_lisible "$silence") — $wt"
          orphelins=$((orphelins + 1))
        fi
      fi
    fi

    if [ "$tsv" = 1 ]; then
      # Le TITRE est la cinquième colonne : #329 aura à dire QUOI il propose de reprendre, et une
      # relecture du backlog rien que pour ça serait une lecture de plus sur un verbe qui n'en fait
      # qu'une. Il ne va pas dans le rendu humain, où c'est le chemin du worktree qui est actionnable.
      lignes="$lignes$iid"$'\t'"$verdict"$'\t'"$origine"$'\t'"$detail"$'\t'"$titre"$'\n'
      continue
    fi
    case "$verdict" in
      orphelin)    lignes="$lignes$(printf '  ⚠ #%s orphelin — %s : %s' "$iid" "$origine" "$detail")"$'\n' ;;
      vivant)      lignes="$lignes$(printf '  ✓ #%s vivant — %s : %s' "$iid" "$origine" "$detail")"$'\n' ;;
      *)           lignes="$lignes$(printf '  ~ #%s — %s' "$iid" "$detail")"$'\n' ;;
    esac
  done <<< "$table"

  # En `--auto` (appel d'office par un point de passage), on ne parle que des orphelins : un ticket
  # bien vivant n'est pas une nouvelle, et le silence est le cas normal. Même parti pris que
  # `worktree.sh gc --auto` et `cleanup-merged --auto`.
  if [ "$auto" = 1 ]; then
    [ "$orphelins" -eq 0 ] && return 0
    printf '%s' "$lignes" | grep -F ' orphelin — '
    printf '  → détail : bash scripts/gitlab/lib.sh reconcile-en-cours\n'
    # Le geste de reprise est NOMMÉ ici, à l'unique endroit dont héritent les trois points de
    # passage de `gc` (/ticket-start, /branch-cleanup, démarrage d'un run) — un constat qui ne dit
    # pas quoi en faire se relit trois fois et ne se traite jamais. Nommer n'est pas décider : rien
    # ne se reprend d'office, ce serait défaire l'asymétrie qui fonde tout ce dispositif (#329).
    printf '  → le reprendre (« À faire » + libéré, worktree intact) : bash scripts/gitlab/lib.sh reprendre-en-cours <iid>\n'
    return 3
  fi

  if [ "$tsv" = 1 ]; then
    printf '# iid\tverdict\tsource\tdetail\ttitre\n'
    printf '%s' "$lignes"
  else
    printf '\nTickets « En cours » — quelqu'\''un s'\''en occupe-t-il encore ?\n\n'
    if [ -z "$lignes" ]; then
      printf '  aucun ticket « En cours » dans le backlog ouvert.\n'
    else
      printf '%s' "$lignes"
    fi
    printf '\n%s vivant(s), %s orphelin(s), %s hors de portée.\n' "$vivants" "$orphelins" "$hors"
    printf 'Portée : les worktrees de CETTE machine (comme le ramassage et la purge) — un ticket\n'
    printf 'travaillé sur un autre clone est « hors de portée », jamais orphelin.\n\n'
  fi
  [ "$orphelins" -gt 0 ] && return 3
  return 0
}

# --- Rendre un orphelin prenable (#329) -----------------------------------------------------------
# Le lot précédent DÉSIGNE (`reconcile-en-cours`, #328) ; celui-ci REND PRENABLE. Sans lui, la
# détection ne fait que nommer une perte : #316 serait resté exactement où il était, avec ses
# 2047 lignes commitées et jamais poussées.
#
# « Prenable » est une CONJONCTION, parce que le filtre de `queue.sh` en est une : un ticket entre
# dans un plan s'il est « À faire » ET libre. Poser le cycle de vie sans retirer l'assignation
# laisserait le ticket écarté par la seconde moitié du filtre, et l'inverse par la première — d'où
# une seule mutation qui fait les deux, comme `gl_begin` fait l'aller.
#
# CE QUE LA REPRISE NE TOUCHE PAS, et c'est tout son intérêt : le worktree, la branche, les commits
# non poussés, les fichiers non commités. Elle n'écrit QUE dans GitLab. Le travail attend là où la
# session l'a laissé, et `worktree.sh ensure` l'y retrouve au démarrage suivant — c'est exactement ce
# qu'on veut de #316 et de ses 2047 lignes. Aucun `gc`, aucun `git`, aucune suppression.
#
# TROIS GARDE-FOUS, et le premier est le seul qui compte vraiment :
#   1. NE JAMAIS REPRENDRE UN VIVANT. Le verdict est demandé à `reconcile-en-cours`, jamais
#      redéduit ici — deux formules qui divergeraient se remarqueraient trop tard, et celle du lot 1
#      sait ce que cette fonction ignore (carte du pilote, fraîcheur du worktree, seuil généreux).
#      Seul « orphelin » ouvre la porte ; « vivant » et « hors de portée » la ferment, le second
#      parce que ne rien savoir n'autorise rien (le ticket peut être en plein travail ailleurs).
#   2. LE PLAFOND (GL_REPRISES_MAX) : au-delà, la reprise se demande explicitement. Voir plus haut
#      pourquoi ce n'est pas un détail de confort.
#   3. « Abandonné »/« Doublon » ne sont jamais concernés — ils ne sont pas « En cours », donc
#      `reconcile-en-cours` ne les rend même pas. Même filtre que `reconcile-workflow`, obtenu ici
#      gratuitement.
# `--force` lève les deux premiers, JAMAIS en silence : c'est le geste de qui sait quelque chose que
# la machine ignore (le worktree a été retiré, la session d'en face est morte pour de bon).
#
# LA TRACE EST DOUBLE, parce qu'elle répond à deux questions qui n'ont pas le même lecteur :
#   • un COMMENTAIRE sur le ticket — « d'où sort ce ticket revenu à “À faire” ? » se pose devant
#     GitLab, des semaines plus tard, par quelqu'un qui n'a pas la machine sous la main ;
#   • une ligne dans `.maestro/orchestrate/reprises.tsv` — c'est elle qui PORTE LE PLAFOND, et un
#     compteur qui vit dans un fil de commentaires se relit en parsant du texte libre.
# Les deux sont BEST-EFFORT et postérieures à la mutation : une trace qui échoue ne doit pas laisser
# croire que la reprise n'a pas eu lieu — elle a eu lieu, GitLab fait foi. Le compteur, lui, le dit
# franchement quand il n'a pas pu compter : un plafond qu'on croit tenu et qui ne l'est pas serait
# pire que pas de plafond du tout.
#
# Codes de retour : 0 = tout repris · 1 = au moins un échec · 2 = usage · 3 = au moins un REFUS
# (vivant, hors de portée, plafond atteint). Le 3 est un refus, pas une panne : rien n'a été écrit.

# gl_reprises_fichier -> le registre des reprises, dans le journal d'orchestration du CLONE
# PRINCIPAL (même résolution que `gl_orch_dir`, donc juste depuis un worktree).
#
# Il vit à CÔTÉ des répertoires de run et non dedans : une reprise est une propriété du TICKET, pas
# d'un run — celle de #325 n'a jamais eu de run du tout (session interactive laissée en plan). Le
# ménage du journal (#198) ne balaie que les répertoires `<run-id>/`, ce fichier lui est donc
# invisible, et c'est voulu : le plafond doit survivre à la rétention des dix derniers runs.
gl_reprises_fichier() {
  local orch
  orch="$(gl_orch_dir)" || return 1
  printf '%s/reprises.tsv' "$orch"
}

# gl_reprises_de <iid> -> le nombre de reprises DÉJÀ consignées pour ce ticket. Imprime toujours un
# nombre (0 quand il n'y a rien à lire) mais rend 1 si le registre est INATTEIGNABLE : l'appelant
# doit pouvoir distinguer « jamais repris » de « je ne sais pas compter », le plafond n'ayant aucun
# sens dans le second cas.
gl_reprises_de() {
  local iid="$1" f
  if ! f="$(gl_reprises_fichier)"; then printf '0'; return 1; fi
  if [ ! -f "$f" ]; then printf '0'; return 0; fi
  awk -F '\t' -v iid="$iid" '$1 !~ /^#/ && $2 == iid { n++ } END { printf "%d", n + 0 }' "$f"
}

# gl_consigne_reprise <iid> <run> <verdict> <rang> -> ajoute une ligne au registre. Best-effort :
# rend 1 sans rien dire de plus si le journal est hors d'atteinte, l'appelant portant le message.
gl_consigne_reprise() {
  local iid="$1" run="$2" verdict="$3" rang="$4" f
  f="$(gl_reprises_fichier)" || return 1
  mkdir -p "$(dirname "$f")" 2>/dev/null || return 1
  if [ ! -f "$f" ]; then
    printf '# date\tiid\trun_origine\tverdict_origine\trang\tpar\n' >"$f" 2>/dev/null || return 1
  fi
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$(date +%FT%T)" "$iid" "${run:--}" "${verdict:--}" "$rang" "$(gl_current_user 2>/dev/null || printf '?')" \
    >>"$f" 2>/dev/null || return 1
}

# gl_origine_du_ticket <iid> -> « run <TAB> verdict <TAB> raison » du dernier run qui a eu ce ticket
# en main, vide s'il n'y en a jamais eu (une session interactive n'écrit aucun journal). DÉLÉGUÉ à
# `journal.sh`, qui est le fichier dont c'est le métier de lire `.maestro/orchestrate/` — et qui le
# résout déjà vers le clone principal d'où qu'on l'appelle.
gl_origine_du_ticket() {
  local j="$GL_ICI/../orchestrate/journal.sh"
  [ -r "$j" ] || return 1
  bash "$j" origine "$1" 2>/dev/null
}

# gl_worktree_du_ticket <iid> -> le répertoire de travail où dort le travail de ce ticket, vide s'il
# n'y en a pas sur cette machine. Recomposé à partir des deux helpers du lot 1 plutôt que découpé
# dans la colonne `detail` de son TSV : cette colonne est une phrase écrite pour un humain, et la
# lire comme une donnée reviendrait à figer sa formulation.
gl_worktree_du_ticket() {
  local principal branche
  principal="$(gl_depot_principal)" || return 1
  branche="$(gl_branche_du_iid "$principal" "$1")"
  [ -n "$branche" ] || return 1
  gl_worktree_de_branche "$principal" "$branche"
}

# gl_liberer_ticket <iid> -> le geste inverse de `gl_begin` : cycle de vie « À faire » ET liste des
# assignés VIDÉE. Muet en cas de succès (le compte rendu appartient à l'appelant), message sur
# stderr et code 1 sinon.
#
# « Prenable » est une CONJONCTION, parce que le filtre de `queue.sh` en est une (« À faire » ET
# libre) : les deux écritures vont ensemble, et l'intervalle entre elles est un état que personne
# n'a voulu. Le champ Status vivant sur l'item de projet et l'assignation sur l'issue, elles ne
# peuvent plus tenir dans UNE mutation comme du temps des labels — st_liberer_ticket les ordonne
# donc, et son en-tête dit dans quel sens et pourquoi.
gl_liberer_ticket() {
  local iid="$1"
  if [ -z "$iid" ]; then echo "usage: gl_liberer_ticket <iid>" >&2; return 2; fi
  st_liberer_ticket "$iid"
}

gl_reprendre_en_cours() {
  local check=0 force=0 iids=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --check) check=1 ;;
      --force) force=1 ;;
      -h|--help|--*) echo "usage: gl_reprendre_en_cours [--check] [--force] <iid>…" >&2; return 2 ;;
      *) iids="$iids $1" ;;
    esac
    shift
  done
  if [ -z "$iids" ]; then
    echo "usage: gl_reprendre_en_cours [--check] [--force] <iid>… (le ticket se NOMME : c'est le geste)" >&2
    return 2
  fi

  # UNE lecture des verdicts pour tous les iid demandés. En `--force` on ne la fait pas du tout :
  # elle ne servirait qu'à être ignorée, et elle coûte deux appels GitLab plus un balayage des
  # worktrees — or `--force` existe justement pour les cas où elle conclut mal (worktree retiré).
  local table="" code_table=0
  if [ "$force" = 0 ]; then
    table="$(gl_reconcile_en_cours --tsv)" || code_table=$?
    # 3 = « il y a des orphelins », qui est le cas nominal ici. Seul 1 (backlog illisible) est une
    # panne : ne rien savoir n'autorise rien.
    if [ "$code_table" != 0 ] && [ "$code_table" != 3 ]; then
      echo "reprendre-en-cours : état des tickets « En cours » illisible — aucune reprise tentée." >&2
      return 1
    fi
  fi

  local iid verdict detail titre run verdict_run raison deja code_deja wt rang
  local echecs=0 refus=0 repris=0
  for iid in $iids; do
    case "$iid" in ''|*[!0-9]*) printf '  ✗ « %s » n'\''est pas un iid.\n' "$iid" >&2; echecs=$((echecs + 1)); continue ;; esac

    verdict=""; detail=""; titre=""
    if [ "$force" = 1 ]; then
      # `--force` ne se joue JAMAIS en silence : il lève le garde-fou qui protège le travail des
      # autres, et une sortie qui n'en dirait rien laisserait croire que la machine a conclu à
      # l'abandon — alors que c'est l'appelant qui en répond.
      printf '  ! #%s : --force — ni le verdict ni le plafond ne sont vérifiés (vous en répondez).\n' "$iid"
    else
      IFS=$'\t' read -r verdict detail titre <<< "$(printf '%s\n' "$table" |
        awk -F '\t' -v i="$iid" '$1 == i { print $2 "\t" $4 "\t" $5; exit }')"
      case "$verdict" in
        orphelin) ;;
        vivant)
          printf '  ⚠ #%s : quelqu'\''un s'\''en occupe (%s) — pas repris.\n' "$iid" "$detail"
          refus=$((refus + 1)); continue ;;
        hors-portee)
          printf '  ⚠ #%s : %s — hors de portée d'\''ici, donc rien ne dit qu'\''il est abandonné.\n' "$iid" "$detail"
          printf '      (--force si vous savez que plus personne n'\''est dessus)\n'
          refus=$((refus + 1)); continue ;;
        *)
          printf '  ⚠ #%s n'\''est pas « En cours » — il n'\''y a rien à reprendre.\n' "$iid"
          refus=$((refus + 1)); continue ;;
      esac
    fi

    # Le plafond, et l'aveu quand il n'a pas pu être vérifié.
    deja="$(gl_reprises_de "$iid")"; code_deja=$?
    if [ "$code_deja" != 0 ]; then
      printf '  ~ #%s : registre des reprises hors d'\''atteinte — cette reprise ne sera pas comptée.\n' "$iid"
    elif [ "$deja" -ge "$GL_REPRISES_MAX" ] && [ "$force" = 0 ]; then
      printf '  ⚠ #%s déjà repris %s fois (plafond %s) — pas repris.\n' "$iid" "$deja" "$GL_REPRISES_MAX"
      printf '      Un ticket qui retombe à chaque run brûle une session entière à chaque fois :\n'
      printf '      lisez sa trace (bash scripts/gitlab/lib.sh reprises %s) avant d'\''insister par --force.\n' "$iid"
      refus=$((refus + 1)); continue
    fi

    # D'où il sort — la moitié « lisible » de la trace, et la seule information qu'un humain n'a
    # aucun moyen de retrouver seul.
    run=""; verdict_run=""; raison=""
    IFS=$'\t' read -r run verdict_run raison <<< "$(gl_origine_du_ticket "$iid")"
    wt="$(gl_worktree_du_ticket "$iid" 2>/dev/null)" || wt=""
    rang=$((deja + 1))

    if [ "$check" = 1 ]; then
      printf '  → #%s passerait à « À faire » et serait libéré (reprise %s/%s)\n' "$iid" "$rang" "$GL_REPRISES_MAX"
      [ -n "$run" ] && printf '      origine : run %s — %s%s\n' "$run" "$verdict_run" \
        "$([ -n "$raison" ] && printf ' (%s)' "$raison")"
      [ -n "$wt" ] && printf '      worktree : %s (intact)\n' "$wt"
      repris=$((repris + 1)); continue
    fi

    # LA mutation, déléguée à gl_liberer_ticket : « À faire » ET la liste des assignés VIDÉE, en un
    # seul appel. Les deux moitiés voyagent ensemble parce que le filtre de `queue.sh` est une
    # conjonction — n'en poser qu'une laisse le ticket dans un état que personne n'a voulu.
    if ! gl_liberer_ticket "$iid"; then echecs=$((echecs + 1)); continue; fi
    repris=$((repris + 1))

    printf '  ✓ #%s repris — « À faire » et libre%s\n' "$iid" \
      "$([ -n "$titre" ] && printf ' : %s' "$titre")"
    if [ -n "$run" ]; then
      printf '      origine  : run %s — %s%s\n' "$run" "$verdict_run" \
        "$([ -n "$raison" ] && printf ' (%s)' "$raison")"
    else
      printf '      origine  : aucun run ne l'\''a jugé (session interactive laissée en plan ?)\n'
    fi
    if [ -n "$wt" ]; then
      printf '      worktree : %s — INTACT (commits et fichiers non commités conservés)\n' "$wt"
    fi
    printf '      reprise  : %s/%s\n' "$rang" "$GL_REPRISES_MAX"

    # Les deux traces, après coup et sans jamais faire échouer la reprise elle-même.
    gl_consigne_reprise "$iid" "$run" "$verdict_run" "$rang" ||
      printf '      ~ trace locale non écrite (journal hors d'\''atteinte) — le plafond ne comptera pas celle-ci.\n'
    # Le commentaire voyage par FICHIER, jamais sur la ligne de commande (#233) : un `-m "$(cat …)"`
    # multi-ligne n'est matchable par aucune règle de permission. Le brouillon reste dans le
    # répertoire temporaire du système — personne ne le relit, rien n'y renvoie (règle #234).
    local note
    note="$(mktemp "${TMPDIR:-/tmp}/maestro-reprise.XXXXXX")" 2>/dev/null && {
      {
        printf '🔁 **Ticket repris** — il était « En cours » sans que personne ne s'\''en occupe encore.\n\n'
        if [ -n "$run" ]; then
          printf -- '- origine : run `%s`, verdict `%s`%s\n' "$run" "$verdict_run" \
            "$([ -n "$raison" ] && printf ' (%s)' "$raison")"
        else
          printf -- '- origine : aucun run ne l'\''a jugé (session interactive laissée en plan).\n'
        fi
        [ -n "$wt" ] && printf -- '- worktree conservé : `%s` — commits et travail non commité intacts.\n' "$wt"
        printf -- '- cycle de vie remis à « À faire », assignation retirée : le ticket est de nouveau prenable.\n'
        printf -- '- reprise %s/%s (au-delà du plafond, une reprise se demande par `--force`).\n' "$rang" "$GL_REPRISES_MAX"
      } >"$note"
      gl_issue_note "$iid" "$note" >/dev/null 2>&1 ||
        printf '      ~ commentaire non posté sur #%s (la reprise, elle, a bien eu lieu).\n' "$iid"
      rm -f "$note"
    }
  done

  [ "$check" = 1 ] && printf '\n(--check : rien n'\''a été écrit.)\n'
  [ "$echecs" -gt 0 ] && return 1
  [ "$refus" -gt 0 ] && return 3
  [ "$repris" -gt 0 ] || return 3
  return 0
}

# gl_reprises [<iid>] -> le registre, en clair. La trace n'a de valeur que si elle se lit : c'est ce
# qu'on consulte avant d'insister par `--force` sur un ticket qui a déjà rechuté deux fois.
gl_reprises() {
  local iid="${1:-}" f
  if ! f="$(gl_reprises_fichier)"; then
    echo "reprises : journal d'orchestration hors d'atteinte (hors dépôt git ?)." >&2
    return 1
  fi
  if [ ! -f "$f" ]; then
    printf 'Aucune reprise consignée%s.\n' "$([ -n "$iid" ] && printf ' pour #%s' "$iid")"
    return 0
  fi
  printf '\nReprises consignées%s — %s\n\n' "$([ -n "$iid" ] && printf ' pour #%s' "$iid")" "$f"
  awk -F '\t' -v iid="$iid" '
    $1 ~ /^#/ { next }
    iid != "" && $2 != iid { next }
    { n++; printf "  %s  #%-5s reprise %s — origine : run %s, verdict %s (par %s)\n", $1, $2, $5, $3, $4, $6 }
    END { if (!n) printf "  aucune ligne.\n" }
  ' "$f"
  printf '\n'
}

# --- Retard sur origin/main ----------------------------------------------------------------------
# gl_behind_main [branche] -> « ma branche a-t-elle pris du retard sur origin/main ? », à consulter
# AVANT le push (/ticket-finish). Purement CONSULTATIF : cette fonction ne rebase pas, ne pousse
# pas et n'écrit rien — elle imprime le constat et la commande de rebase, dont le déclenchement
# reste une décision humaine. Un rebase réécrit l'historique d'une branche déjà poussée et
# appellerait un force-push, interdit par les garde-fous (docs/10 §6).
#
# Le « conflit probable » est une heuristique de FICHIERS : ceux modifiés des deux côtés depuis la
# base commune. Volontairement grossière (git seul tranche vraiment), mais c'est exactement le
# signal qui manque sur les fichiers aimants à conflits — CLAUDE.md, docs/10, ce fichier-ci.
#
# Codes de retour, pour l'appelant :
#   0 = à jour, rien à faire          3 = en retard, aucun fichier commun (rebase a priori serein)
#   4 = en retard + conflit probable  2 = usage   1 = état illisible (pas d'origin/main, etc.)
# Un code non nul n'est donc PAS une erreur, juste un constat : l'appeler en
# `bash … behind-main || echo "verdict=$?"` pour lire le verdict sans interrompre une clôture
# sous `set -e` — c'est ce que fait /ticket-finish.
gl_behind_main() {
  local branche="${1:-}" base derriere devant communs nb
  branche="${branche:-$(git branch --show-current 2>/dev/null)}"
  if [ -z "$branche" ]; then
    echo "gl_behind_main : branche indéterminée (HEAD détachée ?) — la préciser en argument." >&2
    return 2
  fi
  case "$branche" in
    main|master)
      printf 'Branche « %s » : rien à comparer avec origin/main.\n' "$branche"
      return 0 ;;
  esac

  # Fetch non bloquant (jamais de prompt d'identifiants) : sans réseau on compare au dernier
  # origin/main connu, ce qui reste plus utile que de ne rien dire.
  GIT_TERMINAL_PROMPT=0 git fetch origin main >/dev/null 2>&1
  if ! git rev-parse --verify --quiet origin/main >/dev/null 2>&1; then
    echo "gl_behind_main : origin/main introuvable — contrôle du retard sauté." >&2
    return 1
  fi
  if ! base="$(git merge-base "$branche" origin/main 2>/dev/null)" || [ -z "$base" ]; then
    echo "gl_behind_main : aucune base commune entre « $branche » et origin/main." >&2
    return 1
  fi
  derriere="$(git rev-list --count "$branche..origin/main" 2>/dev/null)" || derriere=0
  devant="$(git rev-list --count "origin/main..$branche" 2>/dev/null)" || devant=0

  if [ "${derriere:-0}" -eq 0 ]; then
    printf "Branche « %s » à jour avec origin/main (%s commit(s) d'avance).\n" "$branche" "${devant:-0}"
    return 0
  fi

  # Intersection des fichiers touchés de part et d'autre de la base commune.
  communs="$(comm -12 \
      <(git diff --name-only "$base" "$branche" 2>/dev/null | sort -u) \
      <(git diff --name-only "$base" origin/main 2>/dev/null | sort -u))"

  printf "⚠ Branche « %s » en retard : %s commit(s) derrière origin/main (%s d'avance).\n" \
    "$branche" "$derriere" "${devant:-0}"
  if [ -n "$communs" ]; then
    nb="$(printf '%s\n' "$communs" | wc -l | tr -d '[:space:]')"
    printf '  conflit probable — %s fichier(s) modifié(s) des deux côtés :\n' "$nb"
    printf '%s\n' "$communs" | sed 's/^/    - /'
  else
    printf '  aucun fichier modifié des deux côtés — rebase a priori sans conflit.\n'
  fi
  printf '  rebase proposé (décision humaine, jamais automatique) :\n'
  printf '    git fetch origin main && git rebase origin/main\n'

  if [ -n "$communs" ]; then return 4; fi
  return 3
}

# --- Conflit réel avec origin/main --------------------------------------------------------------
# gl_mr_conflict [branche] -> « cette branche se merge-t-elle proprement dans origin/main ? », le
# verdict RÉEL, à consulter avant de remédier une PR (/mr-fix, docs/10 §8.3). Purement CONSULTATIF
# et en lecture seule : ni checkout, ni index touché, ni écriture — d'où l'appel possible depuis le
# clone principal comme depuis un worktree, sur une branche qu'on ne sort jamais.
#
# Le verdict vient de `git merge-tree --write-tree`, qui joue un VRAI merge 3-way en base d'objets.
# C'est ce qui le sépare des deux sources déjà présentes, et pourquoi aucune des deux ne suffisait :
#   • gl_behind_main (ci-dessus) est une heuristique de FICHIERS — modifiés des deux côtés —,
#     pessimiste par construction : elle est vraie presque partout sur les fichiers aimants du dépôt
#     (CLAUDE.md, docs/10-workflow-git.md, ce fichier-ci), donc sans valeur prédictive. Elle répond
#     par ailleurs à une autre question, posée AVANT le push, quand le conflit naît des merges qui
#     suivent ;
#   • `has_conflicts`/`detailed_merge_status` de GitLab est ASYNCHRONE : à la mesure du 2026-08-07,
#     5 MR ouvertes sur 6 répondaient `checking` ou `unchecked`. Il se lit en complément, jamais en
#     source unique, et surtout jamais en l'attendant.
#
# Forme de la sortie de git, sur laquelle repose le parsing (mesurée, git 2.50) : ligne 1 = l'OID de
# l'arbre produit, puis un chemin en conflit par ligne, puis une LIGNE VIDE qui sépare des messages
# (« CONFLICT (content): … »). D'où la lecture « à partir de la ligne 2, jusqu'à la première ligne
# vide ».
#
# Codes de retour, alignés sur gl_behind_main — un code non nul est un CONSTAT, pas une erreur :
#   0 = se merge proprement           3 = conflit (fichiers listés)
#   2 = usage                         1 = état illisible (pas d'origin/main, histoires sans ancêtre
#                                         commun — que git rend en 128, à ne pas confondre avec le
#                                         1 d'un conflit)
# À appeler en `bash … mr-conflict || echo "verdict=$?"` pour lire le verdict sans interrompre une
# remédiation sous `set -e`.
gl_mr_conflict() {
  local branche="${1:-}" sortie rc fichiers nb
  branche="${branche:-$(git branch --show-current 2>/dev/null)}"
  if [ -z "$branche" ]; then
    echo "gl_mr_conflict : branche indéterminée (HEAD détachée ?) — la préciser en argument." >&2
    return 2
  fi
  case "$branche" in
    main|master)
      printf 'Branche « %s » : rien à merger dans origin/main.\n' "$branche"
      return 0 ;;
  esac

  # Fetch non bloquant (jamais de prompt d'identifiants), même politique que gl_behind_main : sans
  # réseau on tranche contre le dernier origin/main connu, ce qui reste plus utile que se taire.
  GIT_TERMINAL_PROMPT=0 git fetch origin main >/dev/null 2>&1
  if ! git rev-parse --verify --quiet origin/main >/dev/null 2>&1; then
    echo "gl_mr_conflict : origin/main introuvable — contrôle de conflit sauté." >&2
    return 1
  fi
  if ! git rev-parse --verify --quiet "$branche" >/dev/null 2>&1; then
    echo "gl_mr_conflict : branche « $branche » introuvable." >&2
    return 1
  fi

  sortie="$(git merge-tree --write-tree --name-only origin/main "$branche" 2>&1)"
  rc=$?
  case "$rc" in
    0)
      printf 'Branche « %s » : se merge proprement dans origin/main.\n' "$branche"
      return 0 ;;
    1) ;;  # conflit — seul cas où l'on poursuit
    *)
      # 128 & consorts : histoires sans ancêtre commun, ref illisible… Ce n'est pas un conflit, et
      # le dire serait un faux positif qui enverrait /mr-fix résoudre un merge impossible.
      printf 'gl_mr_conflict : merge impossible à évaluer (git a rendu %s) — %s\n' \
        "$rc" "$(printf '%s\n' "$sortie" | tail -1)" >&2
      return 1 ;;
  esac

  fichiers="$(printf '%s\n' "$sortie" | awk 'NR == 1 { next } /^$/ { exit } { print }')"
  nb="$(printf '%s\n' "$fichiers" | sed '/^$/d' | wc -l | tr -d '[:space:]')"
  printf "⚠ Branche « %s » en conflit avec origin/main — %s fichier(s) :\n" "$branche" "$nb"
  printf '%s\n' "$fichiers" | sed '/^$/d; s/^/    - /'
  printf '  résolution proposée (merge, jamais rebase — un rebase appellerait un force-push) :\n'
  printf '    git merge origin/main\n'
  return 3
}

# --- Le merge, et ses prérequis en un seul endroit (#415, parent #413) --------------------------
# Depuis #413, le merge n'attend plus un humain — mais il n'attend pas moins de vérifications pour
# autant. Ce verbe est le SEUL chemin de merge du dépôt : ce qui disparaît est l'attente d'un
# humain pour vérifier, pas la vérification.
#
# ⚠ POURQUOI PAS L'AUTO-MERGE NATIF DE GITHUB. `gh pr merge --auto` ne tient ses promesses que
# derrière une protection de branche : ce sont les CHECKS REQUIS qui suspendent le merge jusqu'au
# vert. Or la protection de branche n'existe pas sur un dépôt privé d'un compte Free (§8.8, mesuré
# le 2026-08-14) et `allow_auto_merge` est à false sur ce dépôt. Activée telle quelle, la
# fonctionnalité mergerait donc IMMÉDIATEMENT, pipeline rouge compris — exactement le faux verdict
# que ce chantier existe pour empêcher. Les prérequis sont à notre charge, et ils vivent ici.
#
# ⚠ `gh pr merge` RESTE REFUSÉ par la couche permissions et par le hook guard.sh, et ce n'est pas
# une incohérence : ces deux filets jugent le TEXTE de la commande que la session lance, pas ce
# qu'un script appelle en interne. Le geste nu reste donc impossible — une session ne peut pas
# merger à la main, ni par accident — et le geste vérifié passe par ici. L'interdit n'est pas « ne
# jamais merger », il est « aucun merge non vérifié ».
#
# Les quatre prérequis, dans l'ordre où ils sont éprouvés — du plus décisif au plus cher. L'ordre
# est le contenu de la décision : attendre un pipeline sur une branche qui ne pourra pas être
# mergée de toute façon, c'est payer une attente pour un verdict sans objet.
#   1. une PR OUVERTE, non brouillon, qui FERME le ticket ;
#   2. rien de NON POUSSÉ — merger moins que ce qui existe est une perte silencieuse ;
#   3. aucun CONFLIT réel avec origin/main ;
#   4. un pipeline VERT, et vert SUR LA TÊTE DE LA PR.
#
# Codes de retour — une cause, un remède. Un booléen ne suffirait pas : c'est sur ce code que le
# pilote (#419) décide entre « repasser plus tard », « faire réparer » et « laisser à un humain ».
#   0 = mergé (en --check : mergeable)     4 = pipeline ROUGE          → réparer (/mr-fix)
#   3 = pipeline pas encore rendu          5 = CONFLIT avec origin/main → résoudre (/mr-fix)
#       (en cours, absent, ou périmé)      6 = PR absente/fermée sans merge/brouillon/
#       → repasser plus tard                   sans « Closes »/commits non poussés
#   7 = PR DÉJÀ mergée — rien à faire          → geste humain
#   1 = pré-requis outil manquant          2 = usage
#
# ⚠ POURQUOI 7 EXISTE (#593). « Déjà mergée » a longtemps voyagé dans le 6, avec cinq causes qui
# appellent toutes un geste humain — PR absente, fermée sans merge, brouillon, sans « Closes »,
# commits non poussés. Elle n'en est pas une : l'état visé est ATTEINT, il l'a simplement été par
# un autre chemin — une session interactive, un /mr-fix, un run jumeau. Personne n'a rien à faire,
# et le confondre avec les cinq autres faisait annoncer au pilote « PR #590 (#582) non mergée » à
# propos d'une PR mergée, compter un ticket LIVRÉ parmi les bloqués de son bilan, et sauter le
# ramassage de son worktree (accroché au seul code 0). Ce n'est pas un cas de bord : une REPRISE de
# run le rend courant, une PR pouvant être mergée entre la coupure et la reprise.
#
# Ce n'est pas 0 non plus, et la nuance n'est pas cosmétique : 0 dit « j'ai mergé », ce que le verbe
# n'a pas fait. Un appelant qui attribue au run un merge qu'il n'a pas commis raconte un run faux.
#
# À appeler en `bash … merge-mr <iid> || verdict=$?` pour lire le verdict sans interrompre une
# boucle sous `set -e`.
GL_MERGE_METHOD="${MAESTRO_MERGE_METHOD:-squash}"

# gh_merge_facts <branche> -> « etat<TAB>pr<TAB>sha<TAB>brouillon<TAB>tickets-fermés(CSV) », en UNE
# lecture. Trois raisons de ne pas réutiliser gh_mr_brief ici : il ne rend ni le brouillon ni les
# fermetures, et trois lectures pour une décision qui se prend d'un coup se paient en latence à
# chaque tour du drain de #419.
#
# On demande à GitHub `closingIssuesReferences` plutôt que de chercher « Closes #<iid> » dans le
# corps : c'est la forge elle-même qui dit quels tickets la PR fermera, donc les trois verbes
# (`Closes`/`Fixes`/`Resolves`), leurs casses et la forme URL sont couverts sans qu'on ait à les
# énumérer — et surtout, ce qui est vérifié est exactement ce qui se produira au merge.
#
# Parsing : la réponse GraphQL rend les champs dans l'ordre de la requête, donc le premier
# « number » est celui de la PR ; les fermetures sont isolées par gh_bloc, sans quoi un grep global
# les confondrait avec lui (même clé, deux objets — c'est précisément le cas pour lequel gh_bloc
# existe).
gh_merge_facts() {
  local branche="$1" raw etat mr sha brouillon fermetures
  if [ -z "$branche" ]; then echo "usage: gh_merge_facts <branche>" >&2; return 2; fi
  raw="$(gh_graphql_read '{ '"$(gh_depot_gql)"' { pullRequests(headRefName: "'"$branche"'", first: 1, orderBy: {field: CREATED_AT, direction: DESC}) { nodes { number state isDraft headRefOid closingIssuesReferences(first: 20) { nodes { number } } } } } }')" || return 1
  mr="$(printf '%s' "$raw" | grep -o '"number":[0-9]*' | head -1 | sed 's/.*://')"
  [ -n "$mr" ] || return 1
  case "$(printf '%s' "$raw" | grep -o '"state":"[A-Z_]*"' | head -1)" in
    *MERGED*) etat="merged" ;;
    *CLOSED*) etat="closed" ;;
    *OPEN*)   etat="opened" ;;
    *)        return 1 ;;
  esac
  sha="$(printf '%s' "$raw" | grep -o '"headRefOid":"[0-9a-f]*"' | head -1 | sed 's/.*:"//; s/"$//')"
  case "$(printf '%s' "$raw" | grep -o '"isDraft":[a-z]*' | head -1)" in
    *true*) brouillon="oui" ;;
    *)      brouillon="non" ;;
  esac
  fermetures="$(printf '%s' "$raw" | gh_bloc closingIssuesReferences \
                | grep -o '"number":[0-9]*' | sed 's/.*://' | paste -sd, - 2>/dev/null)"
  printf '%s\t%s\t%s\t%s\t%s\n' "$etat" "$mr" "${sha:--}" "$brouillon" "${fermetures:--}"
}

# gh_branche_fermante <iid> -> la branche de tête de la PR qui FERME ce ticket, PR déjà mergées ou
# fermées comprises ; rien (code 1) si aucune ne le ferme.
#
# C'est le MIROIR de `closingIssuesReferences` (gh_merge_facts) : là on part de la PR pour savoir
# quels tickets elle fermera, ici on part du ticket pour savoir quelle PR le ferme. La forge répond
# dans les deux sens et c'est elle qui fait autorité — recalculer un nom de branche depuis le titre
# dériverait, pour la raison déjà écrite sous `gl_branche_du_ticket`.
#
# Sert au seul cas où `gl_branche_du_ticket` ne peut rien dire : il ne regarde que les PR OUVERTES,
# donc une PR mergée y est indiscernable d'une PR jamais créée (#593) — deux situations qui n'ont
# ni le même verdict ni le même remède.
#
# La PR MERGÉE l'emporte quand plusieurs ferment le ticket (une tentative fermée sans merge peut
# coexister avec celle qui a abouti) : c'est celle qui décrit l'état du dépôt.
gh_branche_fermante() {
  local iid="$1" raw branche
  if [ -z "$iid" ]; then echo "usage: gh_branche_fermante <iid>" >&2; return 2; fi
  case "$iid" in *[!0-9]*) echo "gh_branche_fermante : iid attendu, reçu « $iid »" >&2; return 2 ;; esac
  raw="$(gh_graphql_read '{ '"$(gh_depot_gql)"' { issue(number: '"$iid"') { closedByPullRequestsReferences(first: 10, includeClosedPrs: true) { nodes { state headRefName } } } } }')" || return 1
  branche="$(printf '%s' "$raw" | grep -o '"state":"MERGED","headRefName":"[^"]*"' \
             | head -1 | sed 's/.*"headRefName":"//; s/"$//')"
  [ -n "$branche" ] || branche="$(printf '%s' "$raw" | grep -o '"headRefName":"[^"]*"' \
                                  | head -1 | sed 's/.*:"//; s/"$//')"
  [ -n "$branche" ] || return 1
  printf '%s\n' "$branche"
}

# gl_branche_du_ticket <iid> -> la branche SOURCE de la PR ouverte du ticket, rien (code 1) sinon.
# On part des PR ouvertes et non du nom de branche que `branch-for` recalculerait : ce qu'on merge
# est une PR, donc sa branche de tête est l'autorité. Un slug qui aurait dérivé du titre du ticket
# depuis la création de la branche rendrait le calcul faux là où cette lecture reste juste.
gl_branche_du_ticket() {
  local iid="$1" branche
  if [ -z "$iid" ]; then echo "usage: gl_branche_du_ticket <iid>" >&2; return 2; fi
  branche="$(gl_open_mr_branches | grep -E "^[a-z]+/${iid}(-|$)" | head -1)"
  [ -n "$branche" ] || return 1
  printf '%s\n' "$branche"
}

gl_merge_mr() {
  local cible="" check=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --check) check=1 ;;
      -h | --help)
        echo "usage: gl_merge_mr [<iid>|<branche>] [--check]" >&2
        return 2 ;;
      -*) echo "gl_merge_mr : option inconnue « $1 »" >&2; return 2 ;;
      *)
        if [ -n "$cible" ]; then
          echo "gl_merge_mr : une seule cible attendue (« $cible », puis « $1 »)." >&2
          return 2
        fi
        cible="$1" ;;
    esac
    shift
  done

  gl_require || return 1

  # --- La cible : un iid, un nom de branche, ou la branche courante ---------------------------
  local branche iid
  cible="${cible:-$(git branch --show-current 2>/dev/null)}"
  if [ -z "$cible" ]; then
    echo "gl_merge_mr : cible indéterminée (HEAD détachée ?) — préciser un iid ou une branche." >&2
    return 2
  fi
  case "$cible" in
    *[!0-9]*) branche="$cible" ;;
    *)
      if ! branche="$(gl_branche_du_ticket "$cible")"; then
        # Aucune PR OUVERTE — mais « aucune PR ouverte » n'est pas un verdict, c'est l'absence de
        # deux verdicts opposés : le ticket n'a jamais eu de PR (geste humain), ou la sienne est
        # déjà mergée (rien à faire). On demande donc à la forge laquelle des deux (#593), et on
        # laisse le flux normal juger la branche trouvée — une seule voie de sortie pour le 7.
        if ! branche="$(gh_branche_fermante "$cible")" || [ -z "$branche" ]; then
          printf '✗ #%s : aucune PR ouverte dont la branche porte ce ticket, et aucune PR ne le ferme.\n' "$cible" >&2
          return 6
        fi
      fi ;;
  esac
  case "$branche" in
    main | master)
      echo "gl_merge_mr : « $branche » n'est pas une branche de ticket." >&2
      return 2 ;;
  esac
  iid="$(gl_branch_iid "$branche")" || iid=""

  # --- Prérequis 1 : une PR ouverte, non brouillon, qui ferme le ticket -----------------------
  local faits etat mr sha brouillon fermetures
  if ! faits="$(gh_merge_facts "$branche")"; then
    printf '✗ %s : aucune PR trouvée pour cette branche.\n' "$branche" >&2
    return 6
  fi
  IFS=$'\t' read -r etat mr sha brouillon fermetures <<< "$faits"

  if [ "$etat" = "merged" ]; then
    # Le seul « pas ouverte » qui soit un SUCCÈS (#593) : le travail est dans `main`, le ticket est
    # fermé par son « Closes ». Sur stdout et sous « ✓ », comme les deux autres verdicts positifs du
    # verbe — un refus s'imprime sur stderr, et ceci n'en est pas un.
    printf '✓ PR #%s (%s) : déjà mergée — rien à faire.\n' "$mr" "$branche"
    return 7
  fi
  if [ "$etat" != "opened" ]; then
    printf '✗ PR #%s (%s) : état « %s » — seule une PR ouverte se merge.\n' "$mr" "$branche" "$etat" >&2
    return 6
  fi
  if [ "$brouillon" = "oui" ]; then
    # Le brouillon n'est pas levé ici, et c'est délibéré : « Draft » dit « pas fini », et le lever
    # au passage ferait changer un verbe de merge un état qu'il est censé constater. La commande
    # qui clôt le ticket (#418) le lève ; ici on le nomme.
    printf '✗ PR #%s (%s) : brouillon — GitHub refuse de merger un brouillon.\n' "$mr" "$branche" >&2
    printf '  la passer en prête d'\''abord : gh pr ready %s\n' "$mr" >&2
    return 6
  fi
  if [ -n "$iid" ]; then
    case ",$fermetures," in
      *",$iid,"*) ;;
      *)
        # Sans cette référence, le merge laisserait le ticket ouvert ET sans état : plus personne
        # ne le poserait, le workflow `issues: closed` (#377) n'ayant pas d'événement à écouter.
        printf '✗ PR #%s (%s) : ne ferme pas #%s (tickets fermés : %s).\n' \
          "$mr" "$branche" "$iid" "$fermetures" >&2
        printf '  ajouter « Closes #%s » à sa description : bash scripts/gitlab/lib.sh set-mr-description %s <fichier>\n' \
          "$iid" "$mr" >&2
        return 6 ;;
    esac
  else
    # Une branche hors convention (`<type>/<iid>-<slug>`) n'est pas forcément illégitime, mais le
    # contrôle de fermeture n'a plus de cible. On le DIT plutôt que de le sauter en silence : un
    # contrôle absent qui ne se voit pas est un contrôle dont on croit qu'il a eu lieu.
    printf '  ⚠ %s : branche sans iid — contrôle « ferme bien son ticket » sauté.\n' "$branche" >&2
  fi

  # --- Prérequis 2 : rien de non poussé -------------------------------------------------------
  # Le local EN RETARD n'est pas notre affaire (quelqu'un a poussé depuis). Ce qui bloque est le
  # local EN AVANCE : des commits que la PR ne porte pas, donc du travail qui disparaîtrait du
  # merge sans que rien ne le dise.
  local sha_local="" inverifiable=""
  if sha_local="$(git rev-parse --verify --quiet "refs/heads/$branche" 2>/dev/null)" && [ -n "$sha_local" ]; then
    if [ "$sha_local" != "$sha" ]; then
      GIT_TERMINAL_PROMPT=0 git fetch origin "$branche" >/dev/null 2>&1
      if ! git cat-file -e "$sha^{commit}" 2>/dev/null; then
        # Ne pas pouvoir vérifier n'est pas la même chose que vérifier et trouver bon : on le DIT
        # et on continue — l'inverse bloquerait tout merge sur un poste sans réseau.
        inverifiable="tête de la PR ($sha) absente du dépôt local — avance locale non vérifiée"
      elif ! git merge-base --is-ancestor "$sha_local" "$sha" 2>/dev/null; then
        printf '✗ PR #%s (%s) : la branche locale porte des commits que la PR n'\''a pas.\n' "$mr" "$branche" >&2
        printf '  local %s, tête de la PR %s — pousser d'\''abord : git push\n' \
          "${sha_local:0:8}" "${sha:0:8}" >&2
        return 6
      fi
    fi
  fi

  # --- Prérequis 3 : aucun conflit réel avec origin/main ---------------------------------------
  # Le verdict vient de `git merge-tree --write-tree` (un merge 3-way réel, en lecture seule) et
  # jamais des deux sources déjà là, dont aucune ne peut porter la décision : `behind-main` est une
  # heuristique de fichiers pessimiste, et le champ de mergeabilité de la forge est asynchrone
  # (§8.3).
  local conflit=0
  gl_mr_conflict "$branche" >/dev/null 2>&1 || conflit=$?
  case "$conflit" in
    0) ;;
    3)
      printf '✗ PR #%s (%s) : en conflit avec origin/main.\n' "$mr" "$branche" >&2
      gl_mr_conflict "$branche" >&2 || true
      return 5 ;;
    *)
      # Ni propre ni en conflit : histoires sans ancêtre commun, origin/main introuvable… Le dire
      # « en conflit » enverrait résoudre un merge qui ne peut pas avoir lieu (git rend 128 et non
      # 1 dans ce cas — les confondre est le piège nommé en §8.3).
      printf '✗ PR #%s (%s) : mergeabilité impossible à évaluer (mr-conflict a rendu %s).\n' \
        "$mr" "$branche" "$conflit" >&2
      return 6 ;;
  esac

  # --- Prérequis 4 : un pipeline vert, SUR LA TÊTE DE LA PR ------------------------------------
  local pl id statut sha_run url
  if ! pl="$(gl_pipeline_latest "$branche" 2>/dev/null)"; then
    printf '⏳ PR #%s (%s) : pipeline pas encore né — aucun pipeline pour cette branche.\n' "$mr" "$branche" >&2
    return 3
  fi
  IFS=$'\t' read -r id statut sha_run url <<< "$pl"
  if [ "$sha_run" != "$sha" ]; then
    # Un vert porté par un commit antérieur ne dit RIEN du commit qu'on merge. C'est le cas
    # nominal juste après un push : le run précédent est terminé, le nouveau n'a pas démarré.
    # Les DEUX branches disent « pas encore né », et c'est voulu (#595) : ce sont deux formes de la
    # même attente — celle de l'événement — et l'appelant qui la nomme les traite pareil, là où
    # « périmé » (conservé, il dit la cause) ne se rapproche d'aucune des deux tout seul.
    printf '⏳ PR #%s (%s) : pipeline pas encore né — verdict périmé, run sur %s, tête de la PR %s.\n' \
      "$mr" "$branche" "${sha_run:0:8}" "${sha:0:8}" >&2
    return 3
  fi
  case "$statut" in
    success) ;;
    failed | canceled)
      printf '✗ PR #%s (%s) : pipeline %s — %s\n' "$mr" "$branche" "$statut" "$url" >&2
      return 4 ;;
    *)
      printf '⏳ PR #%s (%s) : pipeline « %s » — %s\n' "$mr" "$branche" "$statut" "$url" >&2
      return 3 ;;
  esac

  # --- Verdict ---------------------------------------------------------------------------------
  [ -z "$inverifiable" ] || printf '  ⚠ %s\n' "$inverifiable" >&2
  if [ "$check" -eq 1 ]; then
    printf '✓ PR #%s (%s) : mergeable — pipeline vert sur %s, aucun conflit, ferme #%s.\n' \
      "$mr" "$branche" "${sha:0:8}" "${iid:-?}"
    return 0
  fi

  # --- Le merge --------------------------------------------------------------------------------
  # Par l'API REST et non par `gh pr merge`, pour la raison qui vaut déjà à gh_create_pr (l'appel
  # explicite ne déduit rien du remote git) et pour une seconde qui compte davantage : le champ
  # `sha` fait ÉCHOUER le merge si la tête a bougé depuis la vérification. Sans lui, une course
  # entre le dernier contrôle et le merge passerait en silence — et ce serait précisément un merge
  # non vérifié, c'est-à-dire la seule chose que ce verbe existe pour empêcher.
  local out code=0
  out="$(gh api -X PUT "repos/$GL_GH_REPO/pulls/$mr/merge" \
        -f merge_method="$GL_MERGE_METHOD" -f sha="$sha" 2>&1)" || code=$?
  if [ "$code" -ne 0 ] || ! printf '%s' "$out" | grep -q '"merged":[[:space:]]*true'; then
    printf '✗ PR #%s (%s) : le merge a été refusé par GitHub.\n' "$mr" "$branche" >&2
    printf '%s\n' "$out" | tail -3 >&2
    return 6
  fi

  printf '✓ PR #%s mergée (%s, %s) — ferme #%s.\n' "$mr" "$GL_MERGE_METHOD" "${sha:0:8}" "${iid:-?}"
  # La branche distante part avec le merge (`delete_branch_on_merge`, #384) ; la locale est du
  # ressort de `cleanup-merged`, qui exige que la forge confirme le merge — ce qui vient d'arriver.
  # `sync-main` est best-effort et muet en échec, au même titre qu'ailleurs : un run merge désormais
  # autant de fois qu'il ouvre de PR, et c'est ce qui fait vieillir `main` le plus vite (#205).
  gl_sync_main >/dev/null 2>&1 || true
  return 0
}

# --- L'ordre de merge le moins conflictuel (#416, parent #413) ----------------------------------
# gl_merge_order [<branche>…] -> dans quel ORDRE merger les PR ouvertes pour payer le moins de
# résolutions de conflit. Une ligne TSV par branche (en-tête préfixée « # » à ignorer côté
# machine) :
#     rang <TAB> branche <TAB> pr <TAB> degre <TAB> voisines
# `degre` = nombre de branches avec lesquelles celle-ci entre en conflit ; `voisines` = leurs noms
# en CSV (« - » si aucune). Sans argument, les branches des PR ouvertes (`review-queue`).
#
# Repris du cadrage de #299 : le ticket a été abandonné, son analyse ne l'est pas.
#
# LE GRAPHE. Une arête par paire de branches qui ne se mergent pas proprement l'une dans l'autre,
# mesurée par `git merge-tree --write-tree` — un merge 3-way RÉEL en base d'objets, en lecture
# seule : aucun checkout, aucun index touché, aucune branche sortie. C'est la même primitive que
# `mr-conflict` (§8.3), pour la même raison : `behind-main` est une heuristique de fichiers
# pessimiste, et le champ de mergeabilité de la forge est asynchrone.
#
# LE TRI, ET LE MODÈLE DE COÛT QUI LE JUSTIFIE. Rang par degré CROISSANT : une PR sans voisine
# passe en premier, une PR carrefour en dernier. Ce n'est pas une préférence esthétique — une PR ne
# paie QU'UNE résolution quel que soit le nombre de voisines mergées avant elle (un seul
# `git merge origin/main` les absorbe toutes), donc le coût d'un ordre est le nombre de PR ayant au
# moins une voisine mergée avant elles. Une PR carrefour mergée en premier force CHACUNE de ses
# voisines à payer ; mergée en dernier, elle ne paie qu'une fois. Sur la mesure du 2026-08-07
# (6 PR, 5 arêtes) : 2 résolutions par degré croissant contre 4 en commençant par le carrefour.
#
# ⚠ C'EST UNE HEURISTIQUE, PAS UN OPTIMUM, et il ne faut ni le promettre ni chercher l'exact :
# l'ordre optimal est un ensemble indépendant maximum du graphe, NP-difficile en général. À
# l'échelle d'une dizaine de PR l'écart est au plus d'une résolution, et le tri par degré se relit.
#
# À degré égal, l'ordre d'entrée tranche — donc la PR la plus ANCIENNE d'abord quand la liste vient
# de `review-queue`, qui est déjà triée ainsi. Un départage arbitraire rendrait deux appels
# successifs incomparables pour rien.
#
# Le graphe coûte n(n-1)/2 appels à `merge-tree` — 66 pour une douzaine de PR ouvertes, l'échelle
# de ce dépôt. Aucun plafond n'est posé : tronquer la liste rendrait un ordre calculé sur une
# partie du graphe en le présentant comme l'ordre, ce qui est pire que lent. Un dépôt qui ouvrirait
# des centaines de PR demanderait autre chose que ce verbe, pas une troncature silencieuse.
#
# Lecture seule intégrale : ce verbe ne merge rien, ne pousse rien et n'écrit ni dans la forge ni
# dans le dépôt. Codes : 0 = ordre rendu, 1 = liste illisible, 2 = usage.
gl_merge_order() {
  local -a demandees=()
  while [ $# -gt 0 ]; do
    case "$1" in
      -h | --help) echo "usage: gl_merge_order [<branche>…]" >&2; return 2 ;;
      -*) echo "gl_merge_order : option inconnue « $1 »" >&2; return 2 ;;
      *) demandees+=("$1") ;;
    esac
    shift
  done

  # La file de revue rend la branche ET le numéro de PR en UNE lecture, ce que `open-mr-branches`
  # ne fait pas. Elle est INDISPENSABLE sans argument (c'est elle qui donne la liste) et seulement
  # DÉCORATIVE avec (la colonne `pr`) : d'où un pré-requis d'outil exigé dans le premier cas et une
  # dégradation silencieuse dans le second — ordonner des branches nommées est du git pur, et
  # refuser de le faire faute de réseau serait perdre la moitié utile du verbe.
  if [ "${#demandees[@]}" -eq 0 ]; then
    gl_require || return 1
  fi
  local file=""
  file="$(gl_review_queue 2>/dev/null)" || file=""

  # La file, dépliée une fois en deux colonnes — `mr` (1) et `branche` (7) de gl_review_queue, dont
  # l'ordre des colonnes est le contrat : la table est relue une fois par branche retenue, et une
  # dizaine de PR ne justifie pas d'y mettre autre chose que du shell.
  local -a q_branche=() q_pr=()
  local c_mr c_br
  while IFS=$'\t' read -r c_mr c_br; do
    case "$c_mr" in '#'* | '') continue ;; esac
    [ -n "$c_br" ] && [ "$c_br" != "-" ] || continue
    q_branche+=("$c_br"); q_pr+=("$c_mr")
  done <<< "$(printf '%s\n' "$file" | cut -f1,7)"

  local -a demandes=()
  if [ "${#demandees[@]}" -gt 0 ]; then
    demandes=("${demandees[@]}")
  else
    demandes=("${q_branche[@]}")
    if [ "${#demandes[@]}" -eq 0 ]; then
      echo "gl_merge_order : aucune PR ouverte à ordonner." >&2
      return 1
    fi
  fi

  # Fetch non bloquant (jamais de prompt d'identifiants), même politique que `mr-conflict` : sans
  # réseau on tranche contre les dernières refs distantes connues, ce qui reste plus utile que se
  # taire. On préfère `origin/<branche>` à la locale — c'est la PR qu'on ordonne, donc ce que la
  # forge porte fait autorité ; une locale en avance appartient à `merge-mr`, pas à ce verbe.
  GIT_TERMINAL_PROMPT=0 git fetch origin >/dev/null 2>&1 || true

  local -a noms=() refs=() prs=()
  local b ref k deja pr
  for b in "${demandes[@]}"; do
    [ -n "$b" ] || continue
    case "$b" in
      main | master)
        printf 'gl_merge_order : « %s » n'\''est pas une branche de ticket — écartée.\n' "$b" >&2
        continue ;;
    esac
    deja=0
    for k in "${noms[@]}"; do if [ "$k" = "$b" ]; then deja=1; fi; done
    [ "$deja" -eq 0 ] || continue

    if git rev-parse --verify --quiet "refs/remotes/origin/$b" >/dev/null 2>&1; then
      ref="refs/remotes/origin/$b"
    elif git rev-parse --verify --quiet "refs/heads/$b" >/dev/null 2>&1; then
      ref="refs/heads/$b"
    else
      printf 'gl_merge_order : branche « %s » introuvable (ni distante ni locale) — écartée.\n' "$b" >&2
      continue
    fi

    pr="-"
    for k in "${!q_branche[@]}"; do
      if [ "${q_branche[k]}" = "$b" ]; then pr="${q_pr[k]}"; break; fi
    done
    noms+=("$b"); refs+=("$ref"); prs+=("$pr")
  done

  local n="${#noms[@]}"
  if [ "$n" -eq 0 ]; then
    echo "gl_merge_order : aucune branche exploitable." >&2
    return 1
  fi

  local -a deg=() vois=()
  local i j rc
  for ((i = 0; i < n; i++)); do deg[i]=0; vois[i]=""; done
  for ((i = 0; i < n; i++)); do
    for ((j = i + 1; j < n; j++)); do
      rc=0
      git merge-tree --write-tree --name-only "${refs[i]}" "${refs[j]}" >/dev/null 2>&1 || rc=$?
      case "$rc" in
        0) continue ;;  # se mergent proprement : pas d'arête
        1) ;;           # conflit — la SEULE valeur qui en soit un
        *)
          # ⚠ `git merge-tree` rend 128, et non 1, quand le merge est impossible à évaluer
          # (histoires sans ancêtre commun, ref illisible). Le compter comme un conflit gonflerait
          # un degré, donc fausserait tout l'ordre — c'est le piège nommé en §8.3.
          printf 'gl_merge_order : %s ↔ %s impossible à évaluer (git a rendu %s) — arête ignorée.\n' \
            "${noms[i]}" "${noms[j]}" "$rc" >&2
          continue ;;
      esac
      deg[i]=$(( deg[i] + 1 )); vois[i]="${vois[i]:+${vois[i]},}${noms[j]}"
      deg[j]=$(( deg[j] + 1 )); vois[j]="${vois[j]:+${vois[j]},}${noms[i]}"
    done
  done

  # Tri par degré croissant, l'INDICE d'entrée départageant les ex æquo. Il voyage donc comme
  # deuxième clé, et le `cut` le retire aussitôt : ce qui sort est un rang, pas une position
  # d'entrée.
  local trie rang=0 t_deg t_nom t_pr t_vois
  trie="$(
    for ((i = 0; i < n; i++)); do
      printf '%s\t%s\t%s\t%s\t%s\n' "${deg[i]}" "$i" "${noms[i]}" "${prs[i]}" "${vois[i]:--}"
    done | LC_ALL=C sort -t"$(printf '\t')" -k1,1n -k2,2n | cut -f1,3-
  )"

  printf '# rang\tbranche\tpr\tdegre\tvoisines\n'
  # Chaque champ est garanti NON VIDE en amont (« - » à défaut) : `IFS=$'\t' read` fusionne deux
  # tabulations consécutives, donc un champ vide au milieu d'une ligne décalerait tous les suivants.
  while IFS=$'\t' read -r t_deg t_nom t_pr t_vois; do
    [ -n "$t_nom" ] || continue
    rang=$(( rang + 1 ))
    printf '%s\t%s\t%s\t%s\t%s\n' "$rang" "$t_nom" "$t_pr" "$t_deg" "$t_vois"
  done <<< "$trie"
  return 0
}

# --- Garde-fou de clôture : la session traite-t-elle bien ce ticket ? ----------------------------
# gl_branch_iid [branche] -> imprime l'iid porté par le NOM de la branche (motif
# `<type>/<iid>-<slug>`, docs/10 §2), et rien (code 1) si le nom n'en porte pas — `main`, branche
# hors convention, HEAD détachée. Purement local : aucune lecture GitLab, donc disponible sans
# réseau et vérifiable sans dépôt distant.
gl_branch_iid() {
  local branche="${1:-}" iid
  branche="${branche:-$(git branch --show-current 2>/dev/null)}"
  [ -n "$branche" ] || return 1
  # Le slug est toléré absent (`chore/164`) : c'est l'iid qui porte l'information.
  iid="$(printf '%s\n' "$branche" | sed -n 's|^[a-z]\{1,\}/\([0-9]\{1,\}\)\(-.*\)\{0,1\}$|\1|p')"
  [ -n "$iid" ] || return 1
  printf '%s\n' "$iid"
}

# gl_close_guard <iid> [branche] -> « cette session traite-t-elle vraiment ce ticket ? », à
# consulter AVANT toute écriture de /ticket-finish et /ticket-ship (commit, push, PR, statut,
# temps). C'est le pendant en SORTIE du garde-fou d'entrée de /ticket-start
# (gl_issue_taken, #159) : rien n'empêchait jusqu'ici un `/ticket-finish 158` lancé depuis
# `chore/163-…` de faire basculer #158 « En revue » et d'y logger le temps du travail d'un autre,
# ni une session ayant récupéré la branche d'un collègue de clôturer à sa place.
#
# Deux contrôles, de force très inégale :
#   1. cohérence iid ↔ branche courante — LOCAL, toujours disponible, c'est le contrôle FORT :
#      la branche est le seul témoin fiable de ce que la session travaille réellement ;
#   2. propriété du ticket (assignés, via gl_issue_owner) — une lecture GitLab, contrôle FAIBLE
#      tant que l'équipe partage un même compte de forge (le bot, cf. GL_BOT_USERS) : il n'attrape que
#      les tickets assignés à une personne nommée. Il reste utile — c'est exactement le cas d'un
#      ticket pris à la main par un humain — mais ne doit jamais être le seul filet.
#
# Comme gl_behind_main, la fonction est CONSULTATIVE : elle n'écrit rien, imprime son constat et
# laisse la décision à l'appelant — le refus reste franchissable sur demande explicite de
# l'utilisateur (reprise assumée d'un ticket laissé en plan), jamais en silence.
#
# Codes de retour, pour l'appelant :
#   0 = cohérent, rien à signaler         3 = la branche porte un AUTRE ticket
#   4 = ticket assigné à quelqu'un d'autre
#   5 = branche sans iid (`main`, hors convention) — cohérence invérifiable
#   1 = ticket illisible (GitLab injoignable) : verdict partiel, à signaler sans bloquer
#   2 = usage
# Priorité quand plusieurs constats tombent : 3 > 4 > 5. Appeler en
# `bash … close-guard <iid> || verdict=$?` pour ne pas interrompre une clôture sous `set -e`.
gl_close_guard() {
  local iid="$1" branche="${2:-}" iid_branche owner statut assignes moi
  local decalage=0 tiers=0 inverifiable=0
  if [ -z "$iid" ]; then echo "usage: gl_close_guard <iid> [branche]" >&2; return 2; fi
  branche="${branche:-$(git branch --show-current 2>/dev/null)}"
  if [ -z "$branche" ]; then
    echo "gl_close_guard : branche indéterminée (HEAD détachée ?) — la préciser en argument." >&2
    return 2
  fi

  # 1. Cohérence iid ↔ branche (local).
  iid_branche="$(gl_branch_iid "$branche")" || iid_branche=""
  if [ -z "$iid_branche" ]; then
    printf "⚠ branche « %s » : aucun iid dans son nom — cohérence avec #%s invérifiable.\n" "$branche" "$iid"
    printf "  (convention « <type>/<iid>-<slug> », docs/10 §2 ; sur main aucune clôture n'a lieu d'être)\n"
    inverifiable=1
  elif [ "$iid_branche" != "$iid" ]; then
    printf "⚠ décalage ticket ↔ branche : « %s » porte le ticket #%s, pas #%s.\n" "$branche" "$iid_branche" "$iid"
    printf "  clôturer #%s d'ici poserait la PR de #%s sur #%s — statut et temps compris.\n" \
      "$iid" "$iid_branche" "$iid"
    printf "  cette session peut clôturer #%s ; pour #%s, reprendre sa branche (bash scripts/gitlab/lib.sh branch-for %s).\n" \
      "$iid_branche" "$iid" "$iid"
    decalage=1
  else
    printf "ticket #%s ↔ branche « %s » : cohérents.\n" "$iid" "$branche"
  fi

  # 2. Propriété du ticket (une lecture GitLab). Son échec ne masque jamais le constat local.
  local owner_code=0
  owner="$(gl_issue_owner "$iid" 2>/dev/null)" || owner_code=$?
  # Le verdict d'illisibilité se lit sur le CODE DE RETOUR de gl_issue_owner, pas sur la vacuité
  # de sa sortie. Du temps du statut natif les deux se confondaient — un ticket réel portait
  # toujours un statut (« À faire » par défaut), donc deux champs vides trahissaient une réponse
  # dégradée. Depuis que le cycle de vie est un LABEL (#209), un ticket peut légitimement n'en
  # porter aucun (dérive que doctor.sh traque) : conserver le test sur la vacuité classerait un
  # ticket sans label et sans assigné comme « illisible » et bloquerait sa clôture à tort.
  # gl_issue_owner, lui, distingue déjà les vrais échecs (GraphQL muet, ticket introuvable, projet
  # illisible) — le sens du doute continue donc d'aller vers le refus, sur un signal plus juste.
  if [ "$owner_code" -ne 0 ]; then
    printf "  propriété de #%s : indéterminée (ticket illisible — GitLab injoignable ?).\n" "$iid"
    [ "$decalage" -eq 1 ] && return 3
    [ "$inverifiable" -eq 1 ] && return 5
    return 1
  fi
  IFS=$'\t' read -r statut assignes <<< "$owner"
  moi="$(gl_current_user 2>/dev/null)"
  if [ -z "$assignes" ]; then
    printf "propriété de #%s : « %s », aucun assigné (ticket libre).\n" "$iid" "${statut:-statut non posé}"
  elif [ -n "$moi" ] && printf '%s' ",$assignes," | grep -q ",$moi,"; then
    printf "propriété de #%s : « %s », assigné à %s — dont moi (%s).\n" \
      "$iid" "${statut:-statut non posé}" "$assignes" "$moi"
  else
    printf "⚠ #%s appartient à quelqu'un d'autre : « %s », assigné à %s (moi : %s).\n" \
      "$iid" "${statut:-statut non posé}" "$assignes" "${moi:-inconnu}"
    printf "  clôturer à sa place lui pose une PR et un temps qu'il n'a pas demandés.\n"
    tiers=1
  fi

  [ "$decalage" -eq 1 ] && return 3
  [ "$tiers" -eq 1 ] && return 4
  [ "$inverifiable" -eq 1 ] && return 5
  return 0
}

# ==================================================================================================
# BACKEND GITHUB — ticket #339, parent #335
# ==================================================================================================
# Tout ce qui suit est la couche qui PARLE AU RÉSEAU, appelée par les verbes `gl_*` posés plus
# haut ; rien ici n'est appelé directement par un script du dépôt (cf. l'en-tête du fichier).
#
# TROIS RÈGLES QUI VALENT POUR TOUTES LES FONCTIONS DE CETTE SECTION :
#
#   1. LA SORTIE EST CELLE DU BACKEND GITLAB, au caractère près — mêmes colonnes TSV, mêmes
#      libellés, mêmes messages, mêmes codes de retour. C'est le contrat que lisent queue.sh,
#      run.sh, status.sh, doctor.sh, worktree.sh et les commandes /ticket-*, et c'est lui qui rend
#      la bascule (lot 8) invisible pour eux. Quand une information n'existe pas côté GitHub, la
#      colonne vaut « - » — jamais une colonne en moins.
#   2. LE DÉPÔT EST TOUJOURS EXPLICITE (`repos/$GL_GH_REPO/…`, `repository(owner:…, name:…)`),
#      jamais déduit du remote git : tant que la bascule n'a pas eu lieu, `origin` pointe encore sur
#      GitLab, et un `gh` qui résout le dépôt tout seul se tromperait de cible.
#   3. LES LECTURES PASSENT PAR GRAPHQL quand elles ont le choix. Ce n'est pas une préférence :
#      `GET /issues` de l'API REST renvoie AUSSI les pull requests (une seule séquence de numéros
#      côté GitHub), si bien qu'un backlog lu en REST compte les PR comme des tickets. La connexion
#      `repository.issues` de GraphQL les exclut PAR CONSTRUCTION — la requête dit ce qu'elle veut
#      au lieu de le filtrer après coup, exactement l'argument de scripts/migration/inventaire.sh.
#
# L'AUTHENTIFICATION est celle du GH_CONFIG_DIR du projet (#334, docs/10 §7.4) : on ne bascule
# jamais la machine entière par `gh auth switch`, et ce fichier ne pose pas la variable — il se
# contente de nommer le compte actif dans son diagnostic, pour qu'un mauvais compte se voie.

# --- Socle ----------------------------------------------------------------------------------------

# gh_depot_gql -> le fragment « repository(owner:…, name:…) » en tête de chaque requête GraphQL.
gh_depot_gql() {
  printf 'repository(owner:"%s", name:"%s")' "${GL_GH_REPO%%/*}" "${GL_GH_REPO##*/}"
}

# gh_require -> gh installé ET un jeton configuré. C'est le corps de gl_require.
#
# ⚠ `gh auth token` ET NON `gh auth status` (#602) : le second VALIDE le jeton par un aller vers
# l'API — 4,3 à 5,3 s mesurées le 2026-08-27, soit plus cher qu'un aller GraphQL (2,5 s) —, et il
# est payé UNE FOIS PAR SOUS-PROCESSUS. Comme chaque verbe de ce fichier est son propre processus,
# un seul `worktree.sh ensure` en enchaînait cinq : ~24 s à re-vérifier une authentification déjà
# vérifiée, invisibles d'une décomposition par poste. `gh auth token` lit `hosts.yml` EN LOCAL
# (0,35 s), et c'est ce coup-là qui a été mesuré sur les deux formes avant de trancher.
#
# CE QU'ON PERD, ET POURQUOI CE N'EST PAS UN GARDE-FOU EN MOINS. La question posée ici est « peut-on
# parler à la forge ? », et sa réponse utile est « un jeton est configuré » : sans jeton, le message
# est le même qu'avant, au mot près. Le VERDICT d'authentification, lui, est rendu par le premier
# appel d'API — que tout verbe fait de toute façon, et dont l'échec est déjà traité chez chacun. Un
# jeton RÉVOQUÉ n'est donc plus nommé ici mais à la première lecture : un message de moins sur un
# chemin d'échec, jamais une écriture de plus ni une vérification sautée.
#
# On lit le CODE DE RETOUR et jamais la sortie : `gh auth token` imprime le jeton, qui n'a rien à
# faire dans une variable de ce fichier ni dans une trace de session.
gh_require() {
  if ! command -v gh >/dev/null 2>&1; then
    echo "gh n'est pas installé. Voir https://cli.github.com" >&2
    return 1
  fi
  if ! gh auth token >/dev/null 2>&1; then
    echo "gh non authentifié. Lancer d'abord : gh auth login" >&2
    return 1
  fi
}

# gh_graphql_read <query> [args gh api…] -> lecture GraphQL avec retry sur réponse VIDE. Même
# contrat et mêmes réglages (GL_GQL_RETRIES/GL_GQL_RETRY_DELAY) que gl_graphql_read, y compris
# l'interdiction d'y envelopper une mutation : un retry pourrait la ré-appliquer.
#
# Les arguments SUPPLÉMENTAIRES sont passés tels quels à `gh api` — en pratique `--jq`, dont
# st_contexte se sert pour aplatir une réponse à trois niveaux d'imbrication. ⚠ La garde « réponse
# vide » porte alors sur la réponse RENDUE et non sur celle reçue : un filtre qui peut légitimement
# ne rien rendre y déclencherait trois tentatives puis une erreur, et ne doit donc pas passer par
# ici (celui de st_contexte rend toujours au moins une ligne, `ticket` ou `erreur`).
gh_graphql_read() {
  local query="$1"
  if [ -z "$query" ]; then echo "gh_graphql_read : requête manquante" >&2; return 2; fi
  shift
  local attempt=1 out
  while :; do
    out="$(gh api graphql -f query="$query" "$@" 2>/dev/null)"
    if [ -n "$out" ]; then printf '%s\n' "$out"; return 0; fi
    if [ "$attempt" -ge "$GL_GQL_RETRIES" ]; then
      echo "gh_graphql_read : réponse vide de l'API GraphQL GitHub après $attempt tentative(s)" >&2
      return 1
    fi
    sleep "$GL_GQL_RETRY_DELAY"
    attempt=$((attempt + 1))
  done
}

# gh_bloc <clé> (stdin = JSON) -> le contenu du tableau « "<clé>":{"nodes":[ … ] ». Sert à isoler
# les `login` des assignés de celui de l'auteur, et les labels du ticket de ceux du dépôt : deux
# objets qui portent la MÊME clé dans une seule réponse, qu'un grep global confondrait.
gh_bloc() {
  local cle="$1"
  sed 's/.*"'"$cle"'":{"nodes":\[//; s/\].*//'
}

# gh_current_user -> le login du compte gh authentifié.
gh_current_user() {
  local u
  u="$(gh api user 2>/dev/null | grep -o '"login":"[^"]*"' | head -1 | sed 's/.*"login":"//; s/"$//')"
  if [ -z "$u" ]; then
    echo "gh_current_user : login introuvable (gh authentifié ? cf. require)" >&2
    return 1
  fi
  printf '%s\n' "$u"
}

# gh_workitem_gid <iid> -> l'identifiant global du ticket (node id GraphQL), pendant du GID GitLab.
gh_workitem_gid() {
  local iid="$1" id
  if [ -z "$iid" ]; then echo "gh_workitem_gid : iid manquant" >&2; return 2; fi
  id="$(gh_graphql_read '{ '"$(gh_depot_gql)"' { issue(number:'"$iid"') { id } } }' \
        | grep -o '"id":"[^"]*"' | head -1 | sed 's/.*:"//; s/"$//')"
  if [ -z "$id" ]; then echo "Ticket #$iid introuvable dans $GL_GH_REPO" >&2; return 1; fi
  printf '%s\n' "$id"
}

# ================================================================================================
# CYCLE DE VIE — GitHub Projects v2, seul backend (ticket #360, chantier #358)
# ================================================================================================
# Troisième implémentation du cycle de vie, et la première qui ne le range pas sur l'issue : l'état
# vit dans le champ « Status » d'un ITEM de projet. Le préfixe `st_` la distingue des `gh_`, qui
# parlent à GitHub elles aussi mais à un autre objet — et confondre les deux est exactement la
# dérive que #363 aura à diagnostiquer. `grep -n '^st_'` en donne l'inventaire exact.
#
# La sortie fut celle du backend labels, AU CARACTÈRE PRÈS : mêmes libellés, mêmes colonnes, mêmes
# messages de succès, mêmes codes de retour. C'est ce contrat qui a permis à `/ticket-start`,
# `close-guard`, `run.sh` et `status.sh` de ne rien changer — d'abord à la bascule du défaut (#364),
# puis au retrait du backend labels (#365), tous deux invisibles à leurs appelants. Périmètre exact
# des sept verbes : en-tête du fichier.
#
# UNE SEULE LECTURE porte tout le backend : `st_contexte`. Elle rend des lignes CLÉ<TAB>… plutôt
# qu'un JSON à re-parser à chaque usage, et c'est ce qui évite d'avoir une requête par verbe.

# st_gql_contexte <iid> -> la requête, en une ligne. Deux racines dans le même appel : le TICKET
# (ses assignés, et l'item qui le représente dans chaque projet, avec la valeur courante du champ) et
# le PROPRIÉTAIRE (ses projets, chacun avec l'id de son champ Status et ceux de ses options). Les
# projets ne sont pas filtrés côté GitHub : `projectsV2(query:)` est une recherche FLOUE, comme le
# `query:` des labels, et le titre du projet est une CLÉ — il se compare en égalité, dans le shell.
st_gql_contexte() {
  local iid="$1"
  printf '{ %s { issue(number:%s) { number assignees(first:10){nodes{login}} projectItems(first:20){nodes{ id project{ id title } fieldValueByName(name:"Status"){ ... on ProjectV2ItemFieldSingleSelectValue { name } } }} } } repositoryOwner(login:"%s") { ... on ProjectV2Owner { projectsV2(first:100){nodes{ id title field(name:"Status"){ ... on ProjectV2SingleSelectField { id options { id name } } } }} } } }' \
    "$(gh_depot_gql)" "$iid" "${GL_GH_REPO%%/*}"
}

# st_jq_contexte -> le programme jq qui aplatit la réponse en lignes. C'est le SEUL endroit du
# cycle de vie qui ne parse pas en grep/sed/awk, et la raison est dans la forme des données : trois
# niveaux d'imbrication (projet → champ → options[]) et des valeurs qui portent espaces et accents
# (« À faire »). Un `grep -o` y serait un parseur déguisé, pas une projection. Le jq utilisé est
# celui EMBARQUÉ dans `gh` (`--jq`), donc aucune dépendance de plus — c'est déjà le choix de
# scripts/env-pull.sh et de scripts/github/bootstrap-project.sh, qui monte ce même projet.
#
# Cinq clés, toutes en première colonne, et le TITRE DU PROJET en deuxième partout où la ligne
# appartient à un projet — c'est lui que le shell compare :
#     erreur  <depot|ticket>
#     ticket  <numéro>
#     assigne <login>
#     item    <titre projet>  <id projet>  <id item>  <libellé du Status, vide si non posé>
#     projet  <titre projet>  <id projet>  <id du champ Status, vide si absent>
#     option  <titre projet>  <id option>  <libellé>
st_jq_contexte() {
  cat <<'JQ'
[
  (if .data.repository == null then "erreur\tdepot"
   elif .data.repository.issue == null then "erreur\tticket"
   else "ticket\t" + (.data.repository.issue.number|tostring) end),
  (.data.repository.issue.assignees.nodes[]? | "assigne\t" + .login),
  (.data.repository.issue.projectItems.nodes[]? | "item\t" + .project.title + "\t" + .project.id + "\t" + .id + "\t" + (.fieldValueByName.name // "")),
  (.data.repositoryOwner.projectsV2.nodes[]? | "projet\t" + .title + "\t" + .id + "\t" + (.field.id // "")),
  (.data.repositoryOwner.projectsV2.nodes[]? as $p | $p.field.options[]? | "option\t" + $p.title + "\t" + .id + "\t" + .name)
] | .[]
JQ
}

# st_contexte <iid> -> les lignes ci-dessus, ou une erreur franche. Mêmes garde-fous que
# gh_issue_owner, et pour la même raison : sans eux, un dépôt illisible rendrait zéro ligne, que
# l'appelant lirait comme « ticket sans état », c'est-à-dire un feu vert.
st_contexte() {
  local iid="$1" lignes
  if [ -z "$iid" ]; then echo "usage: st_contexte <iid>" >&2; return 2; fi
  lignes="$(gh_graphql_read "$(st_gql_contexte "$iid")" --jq "$(st_jq_contexte)")" || return 1
  case "$lignes" in
    "erreur	depot"*)  echo "Dépôt $GL_GH_REPO illisible (inconnu ou droits insuffisants)" >&2; return 1 ;;
    "erreur	ticket"*) echo "Ticket #$iid introuvable dans $GL_GH_REPO" >&2; return 1 ;;
  esac
  printf '%s\n' "$lignes"
}

# st_lignes <clé> [titre-de-projet] (stdin = sortie de st_contexte) -> les lignes de cette clé, et
# de ce projet si un titre est donné. La comparaison est une ÉGALITÉ DE CHAMP et non un `grep` : le
# titre d'un projet est une donnée, pas un motif, et « Maestro » ne doit pas ramener les lignes de
# « Maestro v2 ». Les valeurs voyagent par ENVIRON et jamais par `awk -v`, qui INTERPRÈTE les
# échappements de son argument — un titre porteur d'un antislash y changerait de valeur en silence.
st_lignes() {
  ST_CLE="$1" ST_TITRE="${2-}" ST_AVEC_TITRE="${2+oui}" awk -F'\t' '
    $1 != ENVIRON["ST_CLE"] { next }
    ENVIRON["ST_AVEC_TITRE"] != "" && $2 != ENVIRON["ST_TITRE"] { next }
    { print }
  '
}

# st_cible <iid> <libellé> -> « <id projet><TAB><id champ><TAB><id item><TAB><id option> », tout
# résolu PAR NOM en une lecture (cf. contrat en tête de fichier : aucun ID n'est figé dans le dépôt,
# ni ici ni ailleurs). Chacun des quatre a son message d'échec, parce que les quatre causes appellent
# quatre gestes différents — et que « la mutation a échoué » n'en désigne aucun.
st_cible() {
  local iid="$1" libelle="$2" ctx ligne projet_id champ_id item_id option_id
  ctx="$(st_contexte "$iid")" || return 1

  ligne="$(printf '%s\n' "$ctx" | st_lignes projet "$GL_PROJET_TITRE" | head -1)"
  if [ -z "$ligne" ]; then
    echo "Projet « $GL_PROJET_TITRE » introuvable chez ${GL_GH_REPO%%/*} — le monter : bash scripts/github/bootstrap-project.sh" >&2
    return 1
  fi
  projet_id="$(printf '%s' "$ligne" | cut -f3)"
  champ_id="$(printf '%s' "$ligne" | cut -f4)"
  if [ -z "$champ_id" ]; then
    echo "Le projet « $GL_PROJET_TITRE » n'a pas de champ « Status » — bash scripts/github/bootstrap-project.sh --check" >&2
    return 1
  fi

  option_id="$(printf '%s\n' "$ctx" | st_lignes option "$GL_PROJET_TITRE" \
               | ST_LIBELLE="$libelle" awk -F'\t' '$4 == ENVIRON["ST_LIBELLE"] { print $3; exit }')"
  if [ -z "$option_id" ]; then
    echo "Le champ « Status » du projet « $GL_PROJET_TITRE » n'a pas d'option « $libelle » — bash scripts/github/bootstrap-project.sh --check" >&2
    return 1
  fi

  ligne="$(printf '%s\n' "$ctx" | st_lignes item "$GL_PROJET_TITRE" | head -1)"
  if [ -z "$ligne" ]; then
    echo "#$iid n'est pas un item du projet « $GL_PROJET_TITRE » : aucun état à poser." >&2
    echo "  Le Status vit sur l'item de projet, et non sur l'issue — un ticket hors projet n'a donc" >&2
    echo "  aucun état. L'y ajouter : bash scripts/gitlab/lib.sh project-add $iid \"$libelle\"" >&2
    return 1
  fi
  item_id="$(printf '%s' "$ligne" | cut -f4)"

  printf '%s\t%s\t%s\t%s\n' "$projet_id" "$champ_id" "$item_id" "$option_id"
}

# st_set_workflow <iid> <valeur> -> pose le cycle de vie dans le champ Status. Même signature, même
# entrée (libellé OU slug) et même ligne de sortie que gh_set_workflow.
#
# L'EXCLUSION MUTUELLE N'EST PLUS À NOTRE CHARGE, et c'est tout l'intérêt du chantier : un champ
# single-select n'a qu'une valeur, donc « poser la cible » ne s'accompagne d'aucun « retirer les cinq
# autres ». La classe de bug que doctor.sh traque sur les labels (0 ou ≥ 2) devient impossible par
# construction — restera celle que #363 aura à traquer : un ticket hors projet, donc sans état.
st_set_workflow() {
  local iid="$1" valeur="$2" slug libelle cible mutation out
  if [ -z "$iid" ] || [ -z "$valeur" ]; then echo "usage: st_set_workflow <iid> <valeur>" >&2; return 2; fi
  slug="$(gl_workflow_slug "$valeur")" || return 1
  libelle="$(gl_workflow_label "$slug")"

  cible="$(st_cible "$iid" "$libelle")" || return 1

  # Les quatre identifiants sont interpolés DIRECTEMENT dans la mutation, sans passer par des
  # variables GraphQL : ce sont des identifiants opaques que l'API vient de rendre à la ligne
  # précédente, aucun ne peut porter de guillemet, et c'est la forme de toutes les requêtes de ce
  # fichier. Aucun n'est écrit nulle part — les recopier, fût-ce en exemple dans un commentaire,
  # ferait mentir le `grep` par lequel se vérifie « aucun ID en dur ». `gh_graphql_read` n'est PAS
  # utilisé ici : son retry sur réponse vide ré-appliquerait la mutation (règle posée avec lui, et
  # valable pour les trois backends).
  mutation="mutation { updateProjectV2ItemFieldValue(input: {projectId: \"$(printf '%s' "$cible" | cut -f1)\", itemId: \"$(printf '%s' "$cible" | cut -f3)\", fieldId: \"$(printf '%s' "$cible" | cut -f2)\", value: {singleSelectOptionId: \"$(printf '%s' "$cible" | cut -f4)\"}}) { projectV2Item { id } } }"
  out="$(gh api graphql -f query="$mutation" 2>&1)"
  case "$out" in
    *'"projectV2Item"'*) ;;
    *)
      printf '%s\n' "$out" >&2
      echo "Échec de la pose du cycle de vie sur #$iid" >&2
      return 1 ;;
  esac
  # La carte des états que ce processus a pu mémoriser vient de se périmer (#362) : l'oublier ici,
  # au SEUL endroit qui écrit le champ, évite d'avoir à raisonner appelant par appelant sur « ce
  # processus relit-il une table après avoir écrit ? ».
  st_carte_oublie
  printf 'Cycle de vie de #%s → « %s »\n' "$iid" "$libelle"
}

# st_issue_owner <iid> -> « <statut><TAB><assignés> » : champ vide = « non posé » pour l'état,
# « personne » (ticket LIBRE) pour les assignés ; erreur franche sur un ticket ou un dépôt illisible.
#
# Là où l'écriture REFUSE un ticket hors projet, la lecture rend un statut VIDE — la même valeur que
# « non posé », que gl_close_guard et gl_start_brief savent déjà lire. Ce n'est pas une inconséquence
# : écrire sur un ticket hors projet ne peut RIEN produire de juste, tandis que le lire produit une
# information vraie (« ce ticket n'a pas d'état »). Échouer ici arrêterait des appelants dont ce
# n'est pas le sujet, pour dire ce que le champ vide dit déjà.
st_issue_owner() {
  local iid="$1" ctx statut assignes slug
  if [ -z "$iid" ]; then echo "usage: st_issue_owner <iid>" >&2; return 2; fi
  ctx="$(st_contexte "$iid")" || return 1

  statut="$(printf '%s\n' "$ctx" | st_lignes item "$GL_PROJET_TITRE" | head -1 | cut -f5)"
  # Renormalisation : les six options portent déjà les libellés du contrat (bootstrap-project.sh les
  # y écrit), mais une option renommée à la main dans l'UI sortirait sinon telle quelle. Une valeur
  # inconnue est rendue TELLE QUELLE, exactement comme le fait gl_workflow_label — une lecture ne
  # doit pas échouer sur un état exotique ; le signaler est le rôle de doctor.sh (#363).
  if [ -n "$statut" ] && slug="$(gl_workflow_slug "$statut" 2>/dev/null)"; then
    statut="$(gl_workflow_label "$slug")"
  fi

  assignes="$(printf '%s\n' "$ctx" | st_lignes assigne | cut -f2 \
              | awk '{ out = (NR == 1 ? $0 : out "," $0) } END { if (NR) print out }')"
  printf '%s\t%s\n' "$statut" "$assignes"
}

# st_begin <iid> [username] -> assignation + « En cours » + dates, comme gh_begin et avec la MÊME
# sortie.
#
# CE QUI CHANGE, ET POURQUOI CE N'EST PAS UNE RÉGRESSION. Côté labels, l'assignation et l'état
# voyagent dans UN SEUL `PATCH /issues/:n` — le geste est indivisible, ce qui compte parce que le
# filtre de queue.sh est une CONJONCTION (« À faire » ET libre). Côté Status, l'argument tombe de
# lui-même : l'état ne vit plus sur l'issue, donc les deux écritures touchent deux objets et rien ne
# peut les grouper. L'ORDRE porte alors ce que l'atomicité portait, et c'est l'ÉTAT D'ABORD :
# st_set_workflow est le seul des deux qui puisse refuser (ticket hors projet), et un refus après
# l'assignation laisserait le ticket pris par quelqu'un sans que rien ne l'ait décidé. Refuser avant
# d'écrire quoi que ce soit laisse le ticket exactement dans l'état où on l'a trouvé.
#
# Aucun label n'est touché ici, et il n'y a plus rien à y toucher : les six du cycle de vie sont
# partis avec #365. Le `PATCH /issues/:n` ci-dessous ne porte QUE la liste des assignés — lui faire
# porter des labels reviendrait à réécrire l'ensemble complet (l'endpoint remplace, il n'ajoute
# pas), donc à devoir d'abord les lire pour ne rien perdre, pour une écriture que personne ne
# demande.
st_begin() {
  local iid="$1" user="${2:-}" prio start today delay due out
  if [ -z "$iid" ]; then echo "usage: st_begin <iid> [username]" >&2; return 2; fi

  [ -n "$user" ] || user="$(gh_current_user)" || return 1

  prio="$(gh_prio "$iid")"
  today="$(date +%F)"
  start="$(gh_get_start_date "$iid")"
  [ -z "$start" ] && start="$today"
  delay="$(gl_prio_delay "$prio")"
  due="$(date -d "$start +$delay days" +%F 2>/dev/null)"
  if [ -z "$due" ]; then echo "st_begin : calcul de l'échéance impossible (commande date indisponible ?)" >&2; return 1; fi

  st_set_workflow "$iid" "en-cours" >/dev/null || return 1

  out="$(gh api -X PATCH "repos/$GL_GH_REPO/issues/$iid" -f "assignees[]=$user" 2>&1)"
  case "$out" in
    *'"number"'*) ;;
    *) echo "Échec du démarrage groupé de #$iid : $out" >&2; return 1 ;;
  esac

  # Les dates ensuite : leur échec ne défait pas le démarrage (le ticket est pris, c'est ce qui
  # compte pour l'anti-collision), mais il se dit — un début non posé fausserait /ticket-finish.
  gh_set_dates "$iid" "$start" "$due" >/dev/null ||
    printf '  ~ dates non posées sur #%s (suivi maison hors d'\''atteinte) — à reposer à la main.\n' "$iid" >&2

  printf '#%s démarré : assigné=%s, cycle de vie « En cours », début=%s, échéance=%s\n' "$iid" "$user" "$start" "$due"
  printf '  (priorité %s → échéance à +%s j)\n' "${prio:-prio::moyenne (défaut)}" "$delay"
}

# st_liberer_ticket <iid> -> le geste inverse : « À faire » ET liste des assignés vidée. Muet en cas
# de succès, le compte rendu appartenant à l'appelant.
#
# Deux appels au lieu d'un, pour la raison exposée dans st_begin, et le même ordre — l'état d'abord :
# il peut refuser, et il vaut mieux refuser sans avoir rien touché. L'intervalle entre les deux ne
# rend le ticket prenable ni dans un sens ni dans l'autre : le filtre est une conjonction, et ici
# comme là, chacune de ses deux moitiés reste fausse tant que les deux écritures ne sont pas passées.
st_liberer_ticket() {
  local iid="$1" out
  if [ -z "$iid" ]; then echo "usage: st_liberer_ticket <iid>" >&2; return 2; fi
  st_set_workflow "$iid" "a-faire" >/dev/null || return 1
  out="$(gh api -X PATCH "repos/$GL_GH_REPO/issues/$iid" -F 'assignees[]' 2>&1)"
  case "$out" in
    *'"number"'*) return 0 ;;
    *) printf '%s\n' "$out" >&2; return 1 ;;
  esac
}

# ================================================================================================
# LECTURES D'ENSEMBLE SUR LE STATUS (ticket #362, chantier #358)
# ================================================================================================
# #360 a porté l'unité — lire et écrire l'état d'UN ticket. Ce bloc porte l'ENSEMBLE, et c'est là
# qu'est la charge du chantier : quatre consommateurs changent de source (`/backlog`, le plan d'un
# run, les dérives de doctor.sh, la réconciliation d'après-merge). Ils n'ont pourtant pas une ligne
# à changer, parce qu'aucun ne parle au réseau : tous lisent la COLONNE `statut` de deux tables
# plates, `backlog-table` et `milestone-issues` (inventaire en tête de fichier). Basculer les deux
# producteurs bascule les six appelants — `subtickets` et `startables` compris.
#
# LA MÉTHODE EST UN RECOUVREMENT, PAS UNE RÉÉCRITURE. Le JSON des tickets reste la source de QUI
# EXISTE, et la carte des items celle de QUEL ÉTAT. Le contraire — lister les tickets depuis les
# items du projet — ferait DISPARAÎTRE de `/backlog` tout ticket hors projet, c'est-à-dire
# exactement ceux qu'on veut voir signalés. Un ticket hors projet sort donc avec un statut « - »,
# qui était déjà, au caractère près, ce que rendait un ticket à 0 label du cycle de vie. Les six
# appelants héritent de ce contrat sans le savoir, et la projection awk des tables n'existe toujours
# qu'à un seul endroit (`gh_backlog_table`, `gh_milestone_issues`).
#
# LE COÛT EST LE VRAI RISQUE DU CHANTIER, et il est MESURÉ plutôt que supposé : un filtre par label
# était UN appel REST rendu par le serveur, là où les items d'un projet se PAGINENT par 100 et se
# filtrent chez nous. Mesure, verdict et raison de l'absence de cache : #362 et docs/10 §3.6.
#
# CE QUI N'EST PAS RECOUVERT, ET POURQUOI : `backlog` (le JSON brut). Son contrat est de rendre LA
# RÉPONSE DE LA FORGE telle quelle — y injecter un Status en ferait une projection déguisée, et le
# seul verbe qui montre la donnée non interprétée n'existerait plus. Conséquence à connaître : on
# n'y lit AUCUN état, l'état ne vivant pas sur l'issue. Qui veut l'état lit la table.
#
# CE QUI RESTE À #363 : distinguer « hors projet » de « Status vide » et en faire un diagnostic.
# `st_workflow_derives` ci-dessous porte la dérive, pas sa nouvelle sémantique.

# st_projet_id -> l'id du projet dont le TITRE vaut GL_PROJET_TITRE. Résolu PAR NOM à chaque appel,
# comme partout dans ce fichier (contrat en tête) : un id de projet figé est un clone qui ne démarre
# pas. La comparaison est une ÉGALITÉ DE CHAMP faite dans le shell et non un `grep` — `projectsV2`
# ne sait filtrer que par recherche FLOUE, où « Maestro » ramènerait « Maestro v2 ».
st_projet_id() {
  local lignes id
  lignes="$(gh_graphql_read '{ repositoryOwner(login:"'"${GL_GH_REPO%%/*}"'") { ... on ProjectV2Owner { projectsV2(first:100){nodes{ id title }} } } }' \
            --jq '["projets"] + [.data.repositoryOwner.projectsV2.nodes[]? | "projet\t" + .title + "\t" + .id] | .[]')" || return 1
  id="$(printf '%s\n' "$lignes" | st_lignes projet "$GL_PROJET_TITRE" | head -1 | cut -f3)"
  if [ -z "$id" ]; then
    echo "Projet « $GL_PROJET_TITRE » introuvable chez ${GL_GH_REPO%%/*} — le monter : bash scripts/github/bootstrap-project.sh" >&2
    return 1
  fi
  printf '%s\n' "$id"
}

# st_gql_items <id-projet> [curseur] -> la requête d'UNE PAGE d'items. Le projet est désigné par
# `node(id:)` : l'id vient d'être résolu par st_projet_id, il n'est écrit nulle part.
st_gql_items() {
  local pid="$1" curseur="${2-}" apres=''
  [ -n "$curseur" ] && apres=", after:\"$curseur\""
  printf '{ node(id:"%s") { ... on ProjectV2 { items(first:100%s){ pageInfo{ hasNextPage endCursor } nodes{ content{ ... on Issue { number } } fieldValueByName(name:"Status"){ ... on ProjectV2ItemFieldSingleSelectValue { name } } } } } } }' \
    "$pid" "$apres"
}

# st_jq_items -> aplatit une page en lignes clé<TAB>…, même parti pris que st_jq_contexte (deux
# niveaux d'imbrication et des valeurs à accents : un `grep -o` y serait un parseur déguisé) :
#     erreur  projet
#     page    <hasNextPage>  <curseur de fin>
#     item    <numéro du ticket>  <libellé du Status, vide si non posé>
# La ligne `page` est TOUJOURS émise, y compris sur un projet vide : quand un `--jq` est passé, la
# garde « réponse vide » de gh_graphql_read porte sur la réponse RENDUE (cf. son commentaire), et un
# projet sans item déclencherait sinon trois tentatives puis une erreur.
st_jq_items() {
  cat <<'JQ'
[
  (if .data.node == null then "erreur\tprojet"
   else "page\t" + (.data.node.items.pageInfo.hasNextPage|tostring) + "\t" + (.data.node.items.pageInfo.endCursor // "") end),
  (.data.node.items.nodes[]? | select(.content.number != null)
   | "item\t" + (.content.number|tostring) + "\t" + (.fieldValueByName.name // ""))
] | .[]
JQ
}

# st_carte_statuts -> « <iid><TAB><libellé> » pour tout ticket qui est un item du projet, toutes
# pages confondues. C'est LA lecture d'ensemble : une page de 100 items par appel, là où le backend
# labels filtre côté serveur en un seul.
#
# Le libellé sort TEL QUEL quand il n'est pas l'un des six — même parti pris que st_issue_owner :
# une lecture ne doit pas échouer sur un état exotique (option renommée dans l'UI), le signaler est
# le rôle de doctor.sh (#363).
#
# ELLE EST MÉMORISÉE POUR LA DURÉE DU PROCESSUS, et la mesure est ce qui l'a décidé (#362, docs/10
# §3.6) : la carte coûte ~13 s sur 366 items — un appel pour résoudre le projet, puis une page de
# 100 par ~2,7 s — et `queue.sh` en demandait DEUX, une par table. Le prix n'est pas dans le nombre
# d'appels (+11 sur un plan) mais dans le prix unitaire d'une page de Projects v2 ; le seul levier
# est donc de ne pas la demander deux fois.
#
# LA MÉMOIRE EST OUBLIÉE À CHAQUE ÉCRITURE (`st_set_workflow`), ce qui règle la péremption par
# construction plutôt que par un raisonnement sur les appelants : un processus qui pose un état puis
# relit une table voit son écriture. Elle ne franchit ni les sous-shells (une substitution la
# re-remplit, sans dommage) ni les processus, et `MAESTRO_CYCLE_MEMO=0` l'éteint. À ne pas étendre
# aux verbes UNITAIRES : `gl_issue_owner` est appelé par `run.sh` pendant des heures, sur des
# tickets dont l'état change entre deux appels — c'est le contraire de ce cas-ci.
GL_ST_CARTE_MEMO=''
GL_ST_CARTE_MEMO_POSEE=''

st_carte_oublie() { GL_ST_CARTE_MEMO=''; GL_ST_CARTE_MEMO_POSEE=''; }

# st_carte_charge -> remplit GL_ST_CARTE_MEMO, et rend 1 si la lecture échoue.
#
# ELLE NE S'APPELLE PAS PAR SUBSTITUTION, et c'est tout le sujet : `carte="$(st_carte_…)"` s'exécute
# dans un SOUS-SHELL, où l'affectation meurt avec lui — une mémoire écrite là ne serait jamais
# relue, et le cache mesurerait zéro gain (constaté avant de le corriger : 13 appels des deux
# côtés). Les trois verbes appellent donc `st_carte_charge` DIRECTEMENT puis lisent la variable.
# Ce qui la rend efficace là où il faut : `queue.sh` demande ses deux tables par REDIRECTION, donc
# dans un seul et même shell. Un appelant qui capture par substitution garde une mémoire limitée à
# son sous-shell — sans gain, mais sans dommage non plus.
st_carte_charge() {
  if [ "${MAESTRO_CYCLE_MEMO:-1}" != 0 ] && [ -n "$GL_ST_CARTE_MEMO_POSEE" ]; then return 0; fi
  GL_ST_CARTE_MEMO="$(st_carte_lire)" || return 1
  [ "${MAESTRO_CYCLE_MEMO:-1}" != 0 ] && GL_ST_CARTE_MEMO_POSEE=oui
  return 0
}

# st_carte_statuts -> la carte sur la sortie standard. Reste exposée pour le diagnostic ; les verbes
# du fichier passent par st_carte_charge, pour la raison ci-dessus.
st_carte_statuts() {
  st_carte_charge || return 1
  [ -n "$GL_ST_CARTE_MEMO" ] && printf '%s\n' "$GL_ST_CARTE_MEMO"
  return 0
}

# st_carte_lire -> le corps réseau de la carte, sans mémoire. Séparé pour que la mémorisation soit
# lisible d'un coup d'œil et testable en l'éteignant.
st_carte_lire() {
  local pid curseur='' lignes page
  pid="$(st_projet_id)" || return 1
  while :; do
    lignes="$(gh_graphql_read "$(st_gql_items "$pid" "$curseur")" --jq "$(st_jq_items)")" || return 1
    case "$lignes" in
      "erreur	projet"*)
        echo "Projet « $GL_PROJET_TITRE » illisible (id résolu, node vide)" >&2; return 1 ;;
    esac
    printf '%s\n' "$lignes" | st_lignes item | cut -f2,3
    page="$(printf '%s\n' "$lignes" | st_lignes page | head -1)"
    [ "$(printf '%s' "$page" | cut -f2)" = "true" ] || break
    curseur="$(printf '%s' "$page" | cut -f3)"
    # Une page suivante annoncée sans curseur ferait REDEMANDER la première indéfiniment : mieux
    # vaut une erreur franche qu'une boucle qui paginerait sur place.
    if [ -z "$curseur" ]; then
      echo "st_carte_lire : page suivante annoncée sans curseur — pagination interrompue" >&2
      return 1
    fi
  done
}

# st_statuts <iid…> -> « <iid><TAB><libellé> » pour N tickets NOMMÉS, en UNE lecture. Les iid sont
# demandés sous des ALIAS GraphQL (`i390: issue(number:390)`), exactement comme `gh_issues_state` —
# le nom d'un alias ne pouvant pas commencer par un chiffre.
#
# C'EST LE PENDANT UNITAIRE DE LA CARTE, ET IL RÉPOND À UNE AUTRE QUESTION (#577, docs/10 §3.6).
# `st_carte_lire` répond à « quel est l'état de TOUT le monde ? » et le paie en pages de 100 items ;
# ce verbe répond à « quel est l'état de CES tickets-ci ? », qu'un appelant connaissant ses iid n'a
# aucune raison de payer au prix de l'ensemble. Mesuré le 2026-08-26 sur les 8 lots d'un parent :
# **1,6 s en un aller**, contre **20 à 32 s** pour la carte (6 allers : la résolution du projet, puis
# 5 pages) — le facteur vingt tient au fait qu'on ne pagine plus 577 tickets pour en lire huit.
#
# ⚠ IL CORRIGE AUSSI UN FAUX SILENCE, et c'est la moitié qui ne se voit pas au chronomètre. La carte
# se lisait à travers `gl_backlog_table`, bornée à `first: 100` : tout lot plus ancien que cette
# fenêtre sortait « ? », donc jamais « À faire », donc JAMAIS DÉMARRABLE. Mesuré le 2026-08-26 :
# `subtickets 167` rendait « ? » sur ses cinq lots, tous « Terminé ». Ici, aucune fenêtre — on
# demande les tickets par leur numéro.
#
# LE CONTRAT DE SORTIE EST CELUI DE LA TABLE, colonne pour colonne, pour que les appelants ne
# changent pas : un ticket qui existe mais n'est pas un item du projet (ou dont le Status est vide)
# sort « - », comme le rendait `st_overlay_statut`. Un ticket qui N'EXISTE PAS ne rend AUCUNE LIGNE —
# même parti pris que `gh_issues_state`, et c'est l'appelant qui décide de ce que vaut ce silence
# (`gl_subtickets_enrich` le compte « ? », un iid de checklist illisible n'étant pas un état).
#
# LE LIBELLÉ SORT TEL QUEL, sans renormalisation, exactement comme `st_carte_statuts` : une option
# renommée à la main dans l'UI traverse, et la signaler est le rôle de `doctor.sh` (#363). C'est
# aussi ce qui évite N forks de `gl_workflow_slug` pour une valeur que bootstrap-project.sh écrit
# déjà au bon libellé.
st_statuts() {
  if [ "$#" -eq 0 ]; then echo "usage: st_statuts <iid…>" >&2; return 2; fi
  local iid champs='' raw
  for iid in "$@"; do
    case "$iid" in ''|*[!0-9]*) echo "st_statuts : iid invalide « $iid »" >&2; return 2 ;; esac
    champs="$champs i$iid: issue(number:$iid) { number projectItems(first:20){nodes{ project{title} fieldValueByName(name:\"Status\"){ ... on ProjectV2ItemFieldSingleSelectValue { name } } }} }"
  done
  raw="$(gh_graphql_read '{ '"$(gh_depot_gql)"' {'"$champs"' } }')" || return 1
  case "$raw" in
    *'"repository":null'*) echo "Dépôt $GL_GH_REPO illisible (inconnu ou droits insuffisants)" >&2; return 1 ;;
  esac
  # Parsing en awk et non par `--jq`, et ce n'est pas le goût de la maison : un alias NOT_FOUND
  # (un iid de checklist qui ne désigne aucune issue — le cas d'un numéro de PR) fait rendre à
  # l'API un tableau `errors`, sur quoi `gh api graphql --jq` recrache la réponse BRUTE sans
  # appliquer le filtre. Le résultat aurait été zéro ligne, c'est-à-dire « aucun état » pour tous
  # les lots, avec le code de succès. Même raison, et même forme, que `gh_issues_state`.
  #
  # Le titre du projet est borné par le champ SUIVANT et non par un guillemet fermant (un titre
  # porteur d'un guillemet échappé le traverse ainsi), même parti pris que `gh_backlog_table` ; et
  # il voyage par ENVIRON, jamais par `awk -v`, qui interprète les échappements (#340).
  printf '%s' "$raw" | ST_TITRE="$GL_PROJET_TITRE" awk '
    {
      n = split($0, parts, /"number":/)
      for (i = 2; i <= n; i++) {
        chunk = parts[i]
        if (match(chunk, /^[0-9]+/) == 0) continue
        iid = substr(chunk, RSTART, RLENGTH)
        reste = chunk; statut = ""
        while (match(reste, /\{"project":\{"title":"/)) {
          reste = substr(reste, RSTART + RLENGTH)
          if (match(reste, /"\},"fieldValueByName":/) == 0) break
          titre = substr(reste, 1, RSTART - 1)
          reste = substr(reste, RSTART + RLENGTH)
          val = ""
          if (match(reste, /^\{"name":"/)) {
            apres = substr(reste, RLENGTH + 1)
            if (match(apres, /"\}/)) val = substr(apres, 1, RSTART - 1)
          }
          if (titre == ENVIRON["ST_TITRE"]) { statut = val; break }
        }
        printf "%s\t%s\n", iid, (statut == "" ? "-" : statut)
      }
    }
  '
}

# st_overlay_statut <carte> (stdin = une table plate) -> la même table, colonne `statut` relue dans
# la carte. C'est la 2e colonne des DEUX tables du fichier (`iid statut …`), et un ticket absent de
# la carte sort « - ».
#
# La carte voyage par ENVIRON et jamais par `awk -v`, qui INTERPRÈTE les échappements de son
# argument — un titre porteur d'un antislash y changerait de valeur en silence (#340).
st_overlay_statut() {
  ST_CARTE="$1" awk -F'\t' -v OFS='\t' '
    BEGIN {
      n = split(ENVIRON["ST_CARTE"], lignes, "\n")
      for (i = 1; i <= n; i++) {
        p = index(lignes[i], "\t")
        if (p == 0) continue
        val = substr(lignes[i], p + 1)
        carte[substr(lignes[i], 1, p - 1)] = (val == "" ? "-" : val)
      }
    }
    /^#/ { print; next }
    # Une ligne VIDE ressortirait sinon en « <TAB>- » : affecter $2 fabrique le champ manquant, et
    # la table gagnerait un ticket sans numéro que le consommateur suivant compterait. (Aucune
    # apostrophe dans ce bloc : il vit DANS le programme awk, lui-même entre apostrophes.)
    NF == 0 { next }
    { $2 = (($1 in carte) ? carte[$1] : "-"); print }
  '
}

# st_backlog_table [state] / st_milestone_issues <titre> -> les deux tables du contrat, colonne pour
# colonne identiques à leurs jumelles `gh_`, la carte en plus. Le recouvrement est une SECONDE PASSE
# et non une projection concurrente : la table garde une seule implémentation.
#
# LA TABLE EST CAPTURÉE AVANT D'ÊTRE RECOUVERTE, dans les trois verbes, et ce n'est pas un détail de
# style : `gh_… | st_overlay_statut` rendrait le code du DERNIER maillon du tube (bash, sans
# `pipefail`), donc 0 même quand la lecture des tickets a échoué. L'appelant recevrait un en-tête
# seul avec un code de succès — et `queue.sh`, dont le `|| exit 1` ne verrait rien, partirait sur un
# backlog vide où chaque ticket paraît LIBRE, c'est-à-dire prenable à quelqu'un d'autre.
st_backlog_table() {
  local state="${1:-opened}" rows
  case "$state" in opened|closed|all) ;; *) echo "state invalide : $state (opened|closed|all)" >&2; return 2 ;; esac
  st_carte_charge || return 1
  rows="$(gh_backlog_table "$state")" || return 1
  printf '%s\n' "$rows" | st_overlay_statut "$GL_ST_CARTE_MEMO"
}

st_milestone_issues() {
  local title="$1" rows
  if [ -z "$title" ]; then echo "usage: st_milestone_issues <titre-exact-du-milestone>" >&2; return 2; fi
  st_carte_charge || return 1
  rows="$(gh_milestone_issues "$title")" || return 1
  printf '%s\n' "$rows" | st_overlay_statut "$GL_ST_CARTE_MEMO"
}

# st_workflow_derives [state] -> « <iid><TAB>0 » pour les tickets SANS état. Le portage est direct,
# mais la dérive change de NATURE et le compte le dit : un champ single-select ne peut pas porter
# deux valeurs, donc le « ≥ 2 » que traquait le backend labels est impossible par construction —
# c'est le gain du chantier (cf. en-tête). Il ne reste que « 0 », qui recouvre ici deux causes
# distinctes, « hors projet » et « Status vide » ; les DISTINGUER et les formuler est #363.
st_workflow_derives() {
  local state="${1:-opened}" rows
  case "$state" in opened|closed|all) ;; *) echo "state invalide : $state (opened|closed|all)" >&2; return 2 ;; esac
  st_carte_charge || return 1
  rows="$(gh_backlog_table "$state")" || return 1
  printf '%s\n' "$rows" | st_overlay_statut "$GL_ST_CARTE_MEMO" \
    | awk -F'\t' '$1 !~ /^#/ && $2 == "-" { printf "%s\t0\n", $1 }'
}

# --- Lecture : backlog, ticket, propriétaire --------------------------------------------------------

# gh_backlog [state] -> JSON BRUT des tickets avec labels et assignés. La forme est celle de GitHub
# (les appelants qui projettent passent par gh_backlog_table, qui est le contrat commun) ; ce verbe
# reste exposé pour la même raison que son jumeau : rendre la réponse de la forge, telle quelle.
# Même plafond de 100 tickets que gl_backlog, et pour le même usage.
gh_backlog() {
  local state="${1:-opened}" etats
  case "$state" in
    opened) etats='[OPEN]' ;;
    closed) etats='[CLOSED]' ;;
    all)    etats='[OPEN, CLOSED]' ;;
    *) echo "state invalide : $state (opened|closed|all)" >&2; return 2 ;;
  esac
  gh_graphql_read '{ '"$(gh_depot_gql)"' { issues(first: 100, states: '"$etats"', orderBy: {field: CREATED_AT, direction: DESC}) { nodes { number title labels(first: 30) { nodes { name } } assignees(first: 10) { nodes { login } } } } } }'
}

# gh_backlog_table [state] -> LA table plate TSV, colonne pour colonne identique à gl_backlog_table :
#     iid <TAB> statut <TAB> prio <TAB> agent <TAB> assigne <TAB> titre
#
# ⚠ LA COLONNE `statut` SORT D'ICI TOUJOURS VIDE (« - »), ET C'EST VOULU. L'état ne vit pas sur
# l'issue : ce verbe répond à « qui existe ? », et `st_backlog_table` RECOUVRE la colonne depuis la
# carte des items du projet (cf. st_overlay_statut). La garder dans la table plutôt que de la
# retirer tient au contrat : c'est la 2e colonne des deux tables du fichier, et ses six appelants
# la lisent par sa position. Un producteur qui la supprimerait décalerait tout ce qui suit.
gh_backlog_table() {
  local state="${1:-opened}" json
  json="$(gh_backlog "$state")" || return 1
  printf '# iid\tstatut\tprio\tagent\tassigne\ttitre\n'
  printf '%s\n' "$json" | awk '
    {
      n = split($0, parts, /\{"number":/)
      for (i = 2; i <= n; i++) {
        node = parts[i]
        match(node, /^[0-9]+/); iid = substr(node, RSTART, RLENGTH)

        # Le titre est borné par le champ SUIVANT de la requête, jamais par un guillemet fermant :
        # un guillemet échappé dans le titre le traverse ainsi sans couper la ligne. Même parti
        # pris — et mêmes limites — que la projection GitLab.
        title = "-"
        if (match(node, /,"title":"/)) {
          rest = substr(node, RSTART + RLENGTH)
          if (match(rest, /","labels":/)) title = substr(rest, 1, RSTART - 1)
        }
        gsub(/\\u0026/, "\\&", title); gsub(/\\u003e/, ">", title); gsub(/\\u003c/, "<", title)

        prio = "-"; agent = "-"
        if (match(node, /prio::[a-z]+/))  prio  = substr(node, RSTART + 6, RLENGTH - 6)
        if (match(node, /agent::[a-z]+/)) agent = substr(node, RSTART + 7, RLENGTH - 7)

        # « login » ne figure que dans le bloc des assignés : la requête ne demande pas l auteur.
        assignee = "-"
        if (match(node, /"login":"[^"]*"/)) {
          m = substr(node, RSTART, RLENGTH); sub(/.*"login":"/, "", m); sub(/"$/, "", m); assignee = m
        }

        printf "%s\t%s\t%s\t%s\t%s\t%s\n", iid, "-", prio, agent, assignee, title
      }
    }
  '
}

# gh_issue_raw <iid> -> LA VUE TEXTE CANONIQUE (cf. gl_issue_raw). C'est la fonction qui dispense six
# verbes d'avoir une variante : ils lisent ce format, pas une API.
#
# Le corps voyage par gl_json_string_field, donc déséchappé et byte-transparent — c'est le même
# décodeur que celui de get-description, écrit pour le piège d'encodage de #141.
gh_issue_raw() {
  local iid="$1" raw etat titre corps labels assignes auteur jalon regime champs_lots='' lots=''
  if [ -z "$iid" ]; then echo "usage: gh_issue_raw <iid>" >&2; return 2; fi
  regime="$(gl_lots_regime)" || return 2
  # LES DEUX CHAMPS DU DÉCOUPAGE NATIF (#390) ne sont demandés QU'EN RÉGIME `natif` : en défaut, la
  # requête reste identique au caractère près, donc rien de ce que ce fichier lit ne change avant la
  # bascule du lot 4. Ils sont placés AVANT `body` et non après, pour deux raisons qui se répondent :
  # le bloc des lots est ainsi borné par le champ suivant (`]},"body":`) au lieu de courir jusqu'à
  # la fin de la réponse — où le CORPS du ticket le suivrait —, et la requête garde sa terminaison
  # `body } } }`, sur laquelle le double de `tests/harnais_forge.py` reconnaît la vue canonique.
  # `parent` voyage sous l'ALIAS `pnum`, exactement pour la raison du `jalon:` ci-dessous : sans lui
  # la clé `"number"` désignerait à la fois le parent et chaque lot, et le découpage du bloc des
  # lots prendrait le parent pour un premier lot.
  if [ "$regime" = natif ]; then
    champs_lots=' parent { pnum: number } lots: subIssues(first: 100) { nodes { number title state labels(first: 20) { nodes { name } } } }'
  fi
  # Le titre du JALON voyage sous un ALIAS (`jalon:`) et non sous `title` : sans lui, les deux
  # champs partagent la même clé et `gl_json_string_field title` rend le premier trouvé — c'est-à-dire
  # le titre du TICKET quand le jalon est `null`, puisqu'il n'y a alors aucun objet à isoler. Un
  # alias tranche à la source ; une extraction plus fine ne ferait que déplacer l'ambiguïté.
  raw="$(gh_graphql_read '{ '"$(gh_depot_gql)"' { issue(number:'"$iid"') { title state author { login } labels(first: 30) { nodes { name } } assignees(first: 10) { nodes { login } } milestone { jalon: title }'"$champs_lots"' body } } }')" || return 1
  case "$raw" in
    *'"repository":null'*) echo "Dépôt $GL_GH_REPO illisible (inconnu ou droits insuffisants)" >&2; return 1 ;;
    *'"issue":null'*)      echo "Ticket #$iid introuvable dans $GL_GH_REPO" >&2; return 1 ;;
  esac

  titre="$(printf '%s' "$raw" | gl_json_string_field title)"
  # LES QUATRE CHAMPS PLATS SORTENT D'UN SEUL AWK (#577, docs/10 §3.6). Ils sortaient de quatre
  # chaînes — `grep | head`, `grep | head | sed`, et deux `gh_bloc | grep | sed | awk` —, soit
  # TREIZE processus pour quatre valeurs. Ce n'est pas une préciosité : `gh_issue_raw` est la
  # primitive dont descendent six verbes, elle est lue une douzaine de fois par ticket, et sous
  # MSYS un fork coûte ~120 ms là où il en coûte moins d'un sous Linux (même écart que #372).
  # Mesuré le 2026-08-26 : ~1,3 s par lecture de ticket, sur un poste où l'aller GraphQL lui-même
  # en coûte 1,4. Le prix du script cessait d'être négligeable devant celui du réseau.
  #
  # Le déséchappage, lui, N'EST PAS RECOPIÉ ICI : titre, jalon et corps continuent de passer par
  # `gl_json_string_field`, seul décodeur du fichier (#141). Les quatre champs ci-dessous sont des
  # atomes ASCII — un état, un login, des noms de label —, sans échappement à défaire ; les fondre
  # dans le même awk demanderait de dupliquer le décodeur, c'est-à-dire d'en avoir deux à tenir
  # d'accord pour économiser trois forks.
  #
  # `index`/`substr` plutôt qu'une expression rationnelle : le programme traverse mawk (conteneur
  # du filet, #372) autant que gawk (poste), et l'accolade de `"author":{` y est un début
  # d'intervalle qu'il faudrait échapper différemment selon l'un et l'autre.
  local plats=()
  mapfile -t plats < <(printf '%s' "$raw" | LC_ALL=C awk '
    { buf = buf $0 }
    function valeur(s, tete,   i, v) {
      i = index(s, tete)
      if (i == 0) return ""
      v = substr(s, i + length(tete))
      i = index(v, "\"")
      return (i ? substr(v, 1, i - 1) : v)
    }
    # Le contenu du tableau « "<cle>":{"nodes":[ … ] », borné au premier « ] » — les nœuds demandés
    # ici ne portent aucun tableau imbriqué. Isoler le bloc est ce qui empêche de confondre deux
    # objets qui partagent une clé : le « login » de l assigné et celui de l auteur, le « name » du
    # label et celui du dépôt.
    function bloc(s, cle,   i, tete, j) {
      tete = "\"" cle "\":{\"nodes\":["
      i = index(s, tete)
      if (i == 0) return ""
      s = substr(s, i + length(tete))
      j = index(s, "]")
      return (j ? substr(s, 1, j - 1) : s)
    }
    function joints(s, cle,   out, tete, i, v) {
      tete = "\"" cle "\":\""
      out = ""
      while ((i = index(s, tete)) > 0) {
        s = substr(s, i + length(tete))
        i = index(s, "\"")
        v = (i ? substr(s, 1, i - 1) : s)
        out = (out == "" ? v : out ", " v)
      }
      return out
    }
    END {
      # OPEN/CLOSED -> open/closed : gl_worktree_done compare sur « closed », gl_issue_brief_render
      # ne lit pas ce champ. Traduire ici plutôt que chez eux garde la migration hors de leur code.
      print (valeur(buf, "\"state\":\"") == "CLOSED") ? "closed" : "open"
      print valeur(buf, "\"author\":{\"login\":\"")
      print joints(bloc(buf, "labels"), "name")
      print joints(bloc(buf, "assignees"), "login")
    }
  ')
  etat="${plats[0]:-open}"
  auteur="${plats[1]-}"
  labels="${plats[2]-}"
  assignes="${plats[3]-}"
  jalon="$(printf '%s' "$raw" | gl_json_string_field jalon)"
  corps="$(printf '%s' "$raw" | gl_json_string_field body)"
  [ "$regime" = natif ] && lots="$(printf '%s' "$raw" | gh_lots_natifs)"

  printf 'title:\t%s\n' "$titre"
  printf 'state:\t%s\n' "$etat"
  printf 'author:\t%s\n' "$auteur"
  printf 'labels:\t%s\n' "$labels"
  printf 'assignees:\t%s\n' "$assignes"
  printf 'milestone:\t%s\n' "$jalon"
  # Le découpage natif, s'il y en a un : « parent:<TAB><iid> » puis un « lot:<TAB>… » par
  # sub-issue. AUCUNE LIGNE quand il n'y a rien — un ticket sans parent n'en porte pas plus qu'un
  # ticket sans marqueur dans son corps, et un `parent:` vide se lirait comme un parent nommé « ».
  [ -n "$lots" ] && printf '%s\n' "$lots"
  printf -- '--\n'
  printf '%s\n' "$corps"
}

# gh_lots_natifs (stdin = la réponse GraphQL de gh_issue_raw) -> les lignes de découpage de la vue
# canonique, dans l'ordre où elle les imprime :
#     parent:<TAB><iid>                                      (absente s'il n'y a pas de parent)
#     lot:<TAB><iid><TAB><coche x|-><TAB><par ∥|-><TAB><titre>   (une par sub-issue, dans l'ordre)
#
# UN SEUL AWK pour les deux, et pour la raison de #577 : `gh_issue_raw` est la primitive dont six
# verbes descendent, et sous MSYS un fork coûte ~120 ms. Deux fonctions se liraient mieux et
# coûteraient le double sur le chemin le plus chaud du fichier.
#
# LA COCHE EST DÉRIVÉE DE L'ÉTAT, ET C'EST UN CHOIX (#390). En checklist, `[x]` est posée à la main
# — tenue au fil de l'eau, donc best-effort : un lot mergé depuis l'interface web n'en coche
# aucune (#515). En natif il n'y a rien à cocher, et la seule source honnête est « ce lot est-il
# FERMÉ ? » — la même définition de « soldé » que `gl_lots_ouverts` et `gl_ferme_parent`, où un lot
# abandonné solde au même titre qu'un lot terminé. La coche cesse donc de pouvoir mentir, ce qui
# est un gain et non une perte : aucune règle ne la lit (`gl_subtickets_startables` ne lit que
# l'iid, le cycle de vie et le marqueur — jamais la coche), elle n'est qu'un affichage.
#
# LE MARQUEUR VIENT DU LABEL `lot::parallele`, provisionné par `scripts/gitlab/bootstrap.sh`. Il ne
# pouvait PAS vivre dans le titre du sous-ticket : `gl_branch_from_raw` en dérive le slug de
# branche, et un « (parallèle) » salirait tous les noms de branches. Le label voyage par ENVIRON et
# non par `awk -v`, qui interprète les échappements (#340) — la valeur n'en porte aucun, mais la
# règle du fichier ne souffre pas d'exception qu'il faudrait ensuite justifier.
#
# LE TITRE EST DÉSÉCHAPPÉ COMME DANS `gh_backlog_table`, et pas par `gl_json_string_field` : ce
# décodeur-là rend UN champ nommé, et il en faudrait un fork par lot. Le compromis est le même
# qu'à la table du backlog — les trois `\uXXXX` que GitHub émet (`&`, `<`, `>`) — augmenté du `\"`,
# qui ne coûte rien ici. Reste dehors le `\\` d'un antislash littéral en fin de titre : aucun des
# 630 tickets du dépôt n'en porte, et le traiter demanderait le balayage complet, c'est-à-dire un
# second décodeur à tenir d'accord avec le premier (#141).
gh_lots_natifs() {
  LC_ALL=C GL_LOT_PARALLELE="$GL_LABEL_LOT_PARALLELE" awk '
    { buf = buf $0 }
    END {
      tete = "\"parent\":{\"pnum\":"
      i = index(buf, tete)
      if (i > 0) {
        v = substr(buf, i + length(tete))
        if (match(v, /^[0-9]+/)) printf "parent:\t%s\n", substr(v, RSTART, RLENGTH)
      }

      tete = "\"lots\":{\"nodes\":["
      i = index(buf, tete)
      if (i == 0) exit
      s = substr(buf, i + length(tete))
      # Borné par le champ SUIVANT de la requête. Sans cette coupe, le dernier lot hériterait du
      # corps du ticket, et un corps qui cite le label rendrait ce lot « parallèle ».
      j = index(s, "]},\"body\":")
      if (j > 0) s = substr(s, 1, j - 1)

      etiquette = "\"name\":\"" ENVIRON["GL_LOT_PARALLELE"] "\""
      n = split(s, lots, /\{"number":/)
      for (k = 2; k <= n; k++) {
        node = lots[k]
        if (!match(node, /^[0-9]+/)) continue
        id = substr(node, RSTART, RLENGTH)

        titre = ""
        if (match(node, /,"title":"/)) {
          reste = substr(node, RSTART + RLENGTH)
          if (match(reste, /","state":/)) titre = substr(reste, 1, RSTART - 1)
        }
        gsub(/\\u0026/, "\\&", titre); gsub(/\\u003e/, ">", titre); gsub(/\\u003c/, "<", titre)
        gsub(/\\"/, "\"", titre)

        coche = (index(node, "\"state\":\"CLOSED\"") > 0) ? "x" : "-"
        par   = (index(node, etiquette) > 0) ? "∥" : "-"
        printf "lot:\t%s\t%s\t%s\t%s\n", id, coche, par, titre
      }
    }
  '
}

# gh_issues_state <iid…> -> « <iid><TAB>open|closed » pour chacun des tickets demandés, en UNE
# lecture. Les iid sont demandés sous des ALIAS GraphQL (`i390: issue(number:390)`), le nom d'un
# alias ne pouvant pas commencer par un chiffre.
#
# POURQUOI PAS `gh_backlog_table all`, QUI EST DÉJÀ UNE SEULE LECTURE : elle est bornée à
# `first: 100` (mesuré le 2026-08-25 : #349 → #516 sur 515 tickets), donc muette sur tout lot plus
# ancien que sa fenêtre — un parent de la Phase 4 n'y verrait aucun de ses lots. Et elle ne porte
# pas l'ouvert/fermé mais le CYCLE DE VIE, qui est justement posé par l'événement d'où l'on vient :
# un lot fermé à l'instant y ressort « En revue » le temps que la pose passe. La question est ici
# « ce ticket est-il fermé ? », et elle se pose à la forge — sans fenêtre, sans délai.
#
# UN ALIAS SANS RÉPONSE NE REND PAS DE LIGNE : un ticket inexistant rend `null`, donc rien. C'est
# l'appelant qui décide de ce que vaut ce silence — et `gl_lots_ouverts` le compte OUVERT, fermer un
# parent sur une donnée manquante étant la seule erreur d'ici qui ne se rattrape pas toute seule.
gh_issues_state() {
  if [ "$#" -eq 0 ]; then echo "usage: gh_issues_state <iid…>" >&2; return 2; fi
  local iid champs='' raw
  for iid in "$@"; do
    case "$iid" in ''|*[!0-9]*) echo "gh_issues_state : iid invalide « $iid »" >&2; return 2 ;; esac
    champs="$champs i$iid: issue(number:$iid) { number state }"
  done
  raw="$(gh_graphql_read '{ '"$(gh_depot_gql)"' {'"$champs"' } }')" || return 1
  case "$raw" in
    *'"repository":null'*) echo "Dépôt $GL_GH_REPO illisible (inconnu ou droits insuffisants)" >&2; return 1 ;;
  esac
  # OPEN/CLOSED -> open/closed, comme gh_issue_raw : les appelants comparent sur « closed ».
  printf '%s' "$raw" | grep -o '"number":[0-9]*,"state":"[A-Z_]*"' \
    | sed 's/"number"://; s/,"state":"/\t/; s/"$//' \
    | awk -F '\t' '{ print $1 "\t" (($2 == "CLOSED") ? "closed" : "open") }'
}

# gh_issue_close <iid> -> ferme le ticket comme RÉALISÉ (cf. gl_ferme_parent).
#
# EN REST (`-X PATCH`) ET NON `gh issue close`, pour la raison qui vaut déjà pour gh_issue_note et
# gh_set_description : c'est la forme que prennent toutes les écritures de ce fichier. Elle a ici un
# second mérite — `gh issue close` est en `ask` dans la couche permissions (.claude/settings.json),
# donc une session qui passerait par ce verbe verrait une confirmation surgir au milieu d'un script.
#
# `state_reason: completed` est POSÉ EXPLICITEMENT plutôt que laissé au défaut de l'API : c'est lui
# que lit la liste blanche de scripts/github/ticket-ferme.sh au tour suivant, et un parent fermé sans
# raison en ressortirait « rien à poser » — fermé à la forge et resté « En cours » au board, la
# dérive exacte que #377 avait supprimée.
gh_issue_close() {
  local iid="$1"
  if [ -z "$iid" ]; then echo "usage: gh_issue_close <iid>" >&2; return 2; fi
  if ! gh api -X PATCH "repos/$GL_GH_REPO/issues/$iid" \
       -f state=closed -f state_reason=completed >/dev/null 2>&1; then
    echo "Échec de la fermeture de #$iid" >&2; return 1
  fi
  printf 'Ticket #%s fermé.\n' "$iid"
}

# gh_issue_title <iid> / gh_get_description <iid> / gh_set_description <iid> <fichier>
# Les deux lectures passent par GraphQL — un champ demandé, un champ rendu, aucune ambiguïté de clé
# (le JSON REST d'un ticket porte plusieurs `title`, dont celui du jalon).
gh_issue_title() {
  local iid="$1" titre
  if [ -z "$iid" ]; then echo "usage: gh_issue_title <iid>" >&2; return 2; fi
  titre="$(gh_graphql_read '{ '"$(gh_depot_gql)"' { issue(number:'"$iid"') { title } } }' | gl_json_string_field title)"
  if [ -z "$titre" ]; then echo "gh_issue_title : titre de #$iid illisible" >&2; return 1; fi
  printf '%s\n' "$titre"
}

gh_get_description() {
  local iid="$1"
  if [ -z "$iid" ]; then echo "usage: gh_get_description <iid>" >&2; return 2; fi
  gh_graphql_read '{ '"$(gh_depot_gql)"' { issue(number:'"$iid"') { body } } }' | gl_json_string_field body
}

# L'écriture passe par `-F body=@<fichier>` : gh lit le fichier et l'envoie tel quel. Vérifié
# byte pour byte contre l'API (accents, em-dash, guillemets, antislash, sauts de ligne) — c'est ce
# qui rend l'aller-retour de #141 aussi fidèle ici que côté GitLab, où bash porte la même promesse.
gh_set_description() {
  local iid="$1" fichier="$2"
  if [ -z "$iid" ] || [ -z "$fichier" ]; then echo "usage: gh_set_description <iid> <fichier>" >&2; return 2; fi
  if [ ! -f "$fichier" ]; then echo "fichier introuvable : $fichier" >&2; return 1; fi
  if ! gh api -X PATCH "repos/$GL_GH_REPO/issues/$iid" -F body=@"$fichier" >/dev/null 2>&1; then
    echo "Échec de la mise à jour de la description de #$iid" >&2; return 1
  fi
  printf 'Description de #%s mise à jour.\n' "$iid"
}

# gh_add_label <iid> <label> -> ajoute UN label sans toucher aux autres.
# L'API `POST /issues/<n>/labels` ajoute et ne remplace pas — c'est le point : un `gh issue edit
# --add-label` relit puis réécrit la liste, et deux écritures concurrentes s'y perdraient l'une
# l'autre. Idempotent côté forge : reposer un label déjà là ne fait rien et ne rend pas d'erreur.
gh_add_label() {
  local iid="$1" label="$2"
  if [ -z "$iid" ] || [ -z "$label" ]; then echo "usage: gh_add_label <iid> <label>" >&2; return 2; fi
  if ! gh api -X POST "repos/$GL_GH_REPO/issues/$iid/labels" -f "labels[]=$label" >/dev/null 2>&1; then
    echo "Échec de la pose du label « $label » sur #$iid" >&2; return 1
  fi
}

gh_get_mr_description() {
  local mr="$1"
  if [ -z "$mr" ]; then echo "usage: gh_get_mr_description <mr>" >&2; return 2; fi
  gh_graphql_read '{ '"$(gh_depot_gql)"' { pullRequest(number:'"$mr"') { body } } }' | gl_json_string_field body
}

gh_set_mr_description() {
  local mr="$1" fichier="$2"
  if [ -z "$mr" ] || [ -z "$fichier" ]; then echo "usage: gh_set_mr_description <mr> <fichier>" >&2; return 2; fi
  if [ ! -f "$fichier" ]; then echo "fichier introuvable : $fichier" >&2; return 1; fi
  if ! gh api -X PATCH "repos/$GL_GH_REPO/pulls/$mr" -F body=@"$fichier" >/dev/null 2>&1; then
    echo "Échec de la mise à jour de la description de #$mr" >&2; return 1
  fi
  printf 'Description de #%s mise à jour.\n' "$mr"
}

gh_issue_note() {
  local iid="$1" fichier="$2"
  if [ -z "$iid" ] || [ -z "$fichier" ]; then echo "usage: gh_issue_note <iid> <fichier>" >&2; return 2; fi
  if [ ! -f "$fichier" ]; then echo "fichier introuvable : $fichier" >&2; return 1; fi
  if [ ! -s "$fichier" ]; then echo "gh_issue_note : $fichier est vide — rien à poster" >&2; return 1; fi
  if ! gh api -X POST "repos/$GL_GH_REPO/issues/$iid/comments" -F body=@"$fichier" >/dev/null 2>&1; then
    echo "Échec de la publication du commentaire sur #$iid" >&2; return 1
  fi
  printf 'Commentaire posté sur #%s.\n' "$iid"
}

# gh_reste_source <iid> -> « titre <TAB> iid-du-ticket-de-reprise » (second champ vide s'il n'y en a
# pas encore). Code 3 si le ticket source n'existe pas, 1 si la forge est muette.
#
# UNE SEULE LECTURE répond aux DEUX questions de gl_reste_claude — « ce ticket existe-t-il ? » et
# « a-t-il déjà son ticket de reprise ? » —, et c'est ce qui permet au refus de tomber avant toute
# écriture sans rien coûter de plus (même souci de compte que #602 : ce n'est pas la durée qui se
# garde, c'est le NOMBRE d'allers).
#
# Le marqueur est cherché APRÈS la clé « comments » : le titre voyage dans la même réponse, et un
# ticket dont le titre parlerait de tickets de reprise ne doit pas passer pour en avoir un.
gh_reste_source() {
  local iid="$1" raw titre reprise
  if [ -z "$iid" ]; then echo "usage: gh_reste_source <iid>" >&2; return 2; fi
  raw="$(gh_graphql_read '{ '"$(gh_depot_gql)"' { issue(number:'"$iid"') { title comments(first: 100) { nodes { body } } } } }')" || return 1
  case "$raw" in
    *'"issue":null'*)
      echo "gl_reste_claude : ticket source #$iid introuvable dans $GL_GH_REPO — rien n'a été écrit." >&2
      return 3 ;;
  esac
  titre="$(printf '%s' "$raw" | gl_json_string_field title)"
  if [ -z "$titre" ]; then
    echo "gl_reste_claude : ticket source #$iid illisible (titre absent) — rien n'a été écrit." >&2
    return 1
  fi
  reprise="$(printf '%s' "$raw" | sed 's/.*"comments"//' \
             | grep -o 'ticket de reprise #[0-9][0-9]*' | head -1 | grep -o '[0-9][0-9]*$')"
  printf '%s\t%s\n' "$titre" "$reprise"
}

# gh_create_issue <titre> <fichier-corps> <labels-csv> <jalon|""> <assigné|""> -> imprime l'iid créé.
#
# En REST (`gh api -X POST`) et non `gh issue create`, pour la raison qui vaut déjà à gh_create_pr et
# gh_issue_close : c'est la forme que prennent toutes les écritures de ce fichier, elle nomme le
# dépôt explicitement au lieu de le déduire du remote, et elle porte le corps par `-F body=@<fichier>`
# — le seul chemin dont #141 a prouvé la fidélité aux octets.
#
# Le JALON entre PAR SON NUMÉRO : l'API REST ne connaît pas les titres. C'est ce qui vaut la
# résolution en deux temps, exactement comme gh_milestone_issues — le contrat porte sur la sortie,
# pas sur le nombre d'allers.
gh_create_issue() {
  local titre="$1" fichier="$2" labels="$3" jalon="$4" assigne="$5" out iid numero lbl
  if [ -z "$titre" ] || [ -z "$fichier" ]; then
    echo "usage: gh_create_issue <titre> <fichier> [labels] [jalon] [assigné]" >&2; return 2
  fi
  if [ ! -s "$fichier" ]; then echo "gh_create_issue : corps absent ou vide : $fichier" >&2; return 1; fi

  local -a args
  args=(-X POST "repos/$GL_GH_REPO/issues" -f "title=$titre" -F "body=@$fichier")
  if [ -n "$labels" ]; then
    local reste="$labels"
    while [ -n "$reste" ]; do
      lbl="${reste%%,*}"
      if [ "$lbl" = "$reste" ]; then reste=""; else reste="${reste#*,}"; fi
      [ -n "$lbl" ] && args+=(-f "labels[]=$lbl")
    done
  fi
  [ -n "$assigne" ] && args+=(-f "assignees[]=$assigne")
  if [ -n "$jalon" ]; then
    if numero="$(gh_milestone_number "$jalon")"; then
      args+=(-F "milestone=$numero")
    else
      echo "gh_create_issue : jalon « $jalon » non résolu — le ticket naîtra sans jalon." >&2
    fi
  fi

  out="$(gh api "${args[@]}" 2>&1)"
  iid="$(printf '%s' "$out" | grep -o '"number":[0-9]*' | head -1 | sed 's/.*://')"
  if [ -z "$iid" ]; then
    printf '%s\n' "$out" >&2
    echo "gh_create_issue : création refusée par $GL_GH_REPO" >&2
    return 1
  fi
  printf '%s\n' "$iid"
}

# gh_labels -> tous les labels du dépôt, un nom par ligne (cf. gl_labels).
# `first: 100` comme partout ailleurs dans ce fichier : au-delà, la pagination — et le dépôt en porte
# 29 aujourd'hui, dont les neuf labels par défaut de GitHub que personne n'a retirés.
gh_labels() {
  gh_graphql_read '{ '"$(gh_depot_gql)"' { labels(first: 100) { nodes { name } } } }' \
    | grep -o '"name":"[^"]*"' | sed 's/.*:"//; s/"$//'
}

# gh_issues_sans_milestone -> numéros des tickets OUVERTS sans jalon (cf. gl_issues_sans_milestone).
# `milestone` est demandé sous un ALIAS (`jalon:`) pour la raison exposée dans gh_issue_raw : sans
# lui, le `title` du jalon et celui du ticket partagent une clé, et « jalon absent » deviendrait
# indistinguable de « jalon présent » dès qu'on cherche la clé plutôt que l'objet.
gh_issues_sans_milestone() {
  gh_graphql_read '{ '"$(gh_depot_gql)"' { issues(first: 100, states: OPEN) { nodes { number milestone { jalon: title } } } } }' \
    | sed 's/{"number":/\n{"number":/g' \
    | awk '/^\{"number":[0-9]/ && !/"jalon":"/ { match($0, /[0-9]+/); print substr($0, RSTART, RLENGTH) }'
}

# gh_open_mr_branches -> branche source de chaque PR ouverte (cf. gl_open_mr_branches).
gh_open_mr_branches() {
  gh_graphql_read '{ '"$(gh_depot_gql)"' { pullRequests(states: OPEN, first: 100) { nodes { headRefName } } } }' \
    | grep -o '"headRefName":"[^"]*"' | sed 's/.*:"//; s/"$//'
}

# gh_merge_settings -> les trois garde-fous de merge, dans le vocabulaire normalisé de
# gl_merge_settings. Deux lectures, parce que les trois promesses vivent à deux endroits :
#
#   • `pipeline_requis` ← la PROTECTION DE BRANCHE de `main` exige-t-elle des checks ? C'est le
#     pendant exact d'`only_allow_merge_if_pipeline_succeeds`. ⚠ Sur un dépôt PRIVÉ d'un compte Free,
#     la protection de branche n'existe pas : GitHub répond 404 et la valeur est « false », pas
#     « - ». La nuance est le contenu du diagnostic — « la forge a répondu, et elle a répondu qu'il
#     n'y a aucune protection » (docs/10 §8.8, décision du 2026-08-14 : ni Pro ni public retenus).
#   • `merge_si_pipeline_saute` ← se DÉDUIT : un check requis qui n'est pas rapporté laisse la PR
#     bloquée, donc un pipeline sauté ne permet jamais de merger. La valeur suit celle de la
#     protection au lieu d'être inventée — sans protection, tout est mergeable.
#   • `suppression_branche` ← `delete_branch_on_merge`, un réglage du DÉPÔT (côté GitLab c'est un
#     réglage de projet ET une option par MR ; côté GitHub la PR n'a pas cette option, cf.
#     gh_create_pr). ⚠ Ce champ n'est présent dans la réponse QUE si le jeton a le droit
#     d'ADMINISTRATION du dépôt : avec le PAT à portée restreinte du projet (#336), `GET /repos/:r`
#     l'omet purement et simplement, et la valeur rendue est « - ». Mesuré le 2026-08-17. C'est
#     pourquoi « - » ne doit jamais être lu comme « false » chez l'appelant : ici, l'absence dit
#     quelque chose sur le JETON, rien sur le dépôt.
gh_merge_settings() {
  local prot depot requis
  prot="$(gh api "repos/$GL_GH_REPO/branches/main/protection" 2>/dev/null)"
  case "$prot" in
    *'"required_status_checks"'*) requis=true ;;
    # 404 (aucune protection, ou plan sans la fonctionnalité) comme réponse vide : dans les deux cas
    # rien ne bloque un merge, et c'est ce que le contrôle doit rendre.
    *) requis=false ;;
  esac
  depot="$(gh api "repos/$GL_GH_REPO" 2>/dev/null)"
  printf 'pipeline_requis\t%s\n' "$requis"
  printf 'merge_si_pipeline_saute\t%s\n' "$([ "$requis" = true ] && printf 'false' || printf 'true')"
  if [ -z "$depot" ]; then
    printf 'suppression_branche\t-\n'
    return 1
  fi
  printf 'suppression_branche\t%s\n' "$(gl_champ_booleen "$depot" delete_branch_on_merge)"
}

# --- Jalons et lots ---------------------------------------------------------------------------------

# --- Rail d'un jalon (#617) -------------------------------------------------------------------------
# Le RAIL sépare les jalons d'OUTILLAGE de la forge (workflow git, lib.sh, orchestrate, filet CI,
# worktrees, prompts .claude/**) des jalons PRODUIT (moteur, API, Control Tower). Sans lui,
# `current-milestone` n'a qu'une réponse — « l'actif le plus ancien non soldé » — et TOUT ticket créé
# tombe dans le jalon produit courant, qui devient la décharge des deux (mesuré le 2026-08-27 :
# 15 des 28 tickets de « Boucle fermée : du brief au livrable » étaient de l'outillage, et ses
# 8 tickets ouverts l'étaient tous).
#
# ⚠ Le rail est POSÉ, jamais DÉRIVÉ des labels, et c'est une mesure qui l'a décidé — pas un
# principe. Vérité terrain établie sur 113 tickets en classant chacun par les FICHIERS que ses
# commits ont touchés (même technique que #544 : le rattachement se lit, il ne se devine pas) :
#
#     type::infra                        81 %   rate 17 outillages
#     agent::orchestrateur|devops        91 %   rate 9
#     type::infra OU agent::orch|devops  91 %   rate 5, sur-classe 5 produits
#     type::infra ET agent::orch|devops  81 %   rate 21
#
# Aucun critère ne dépasse 91 %, soit ~1 ticket sur 10 mal aiguillé, et les manques sont
# SYSTÉMATIQUES : le lot final « tests + doc » d'un chantier d'outillage porte `agent::qa` (#345,
# #363, #366, #414) et l'outillage de présentation porte `agent::dev` (#544→#547). Hériter du
# PARENT ne corrige rien — mesuré aussi, 91 % à l'identique : les labels du parent sont tout aussi
# trompeurs que ceux du lot. Ce qui EST vrai, c'est que le rail est cohérent au sein d'un chantier
# (8 lots sur 8 du même rail que leur parent) : le rail d'un lot s'hérite donc de son parent, mais
# c'est le RAIL du parent qu'il hérite, pas ses labels.
#
# Support : une ligne « rail: outillage » dans la DESCRIPTION du jalon. Pas un titre (les titres
# sont lus par les humains et par /milestone-presentation), pas un identifiant figé dans le dépôt
# (règle de #358 : tout se résout par son nom), pas une variable d'environnement nommant UN jalon
# (il faudrait la changer à chaque jalon d'outillage soldé). Un jalon SANS marqueur est du PRODUIT :
# c'est le rail historique, et le défaut évite d'avoir à marquer les 14 jalons déjà fermés, dont le
# bilan est écrit.
GL_RAIL_MOTIF='rail:[[:space:]]*outillage'

# gl_rail_de <texte> -> « outillage » si le texte porte le marqueur, « produit » sinon. UNE seule
# définition de la règle, rejouée telle quelle par les deux lecteurs (gh_current_milestone la
# filtre, gh_milestones la rend en colonne) : deux formulations finiraient par ne plus rendre le
# même verdict, et c'est le jalon d'un ticket qui en dépend (même raison que gl_arbitrage_de, #562).
gl_rail_de() {
  if printf '%s' "$1" | grep -Eqi "$GL_RAIL_MOTIF"; then
    printf 'outillage\n'
  else
    printf 'produit\n'
  fi
}

# gl_rail_valide <rail> -> 0 si le rail est l'un des deux connus. Un rail inconnu est refusé AVANT
# la lecture de forge : « produit »/« outillage » est un ensemble fermé de deux valeurs, comme
# l'effort de run l'est de cinq (#217), et une faute de frappe rendrait sinon le jalon par défaut
# en silence — soit exactement le mélange qu'on corrige.
gl_rail_valide() {
  case "$1" in
    produit|outillage) return 0 ;;
    *) return 1 ;;
  esac
}

# gh_current_milestone [rail] -> le titre du jalon de la phase courante. MÊME règle que côté GitLab :
# le jalon OUVERT le plus ancien par échéance qui porte ENCORE DU TRAVAIL — appliquée À L'INTÉRIEUR
# du rail demandé (défaut « produit », voir GL_RAIL_MOTIF ci-dessus).
#
# ⚠ DEUX ABSTENTIONS, NOMMÉES SÉPARÉMENT (#619). Un jalon écarté l'est pour l'une OU pour l'autre,
# et les confondre en une seule (« non soldé ») est ce qui faisait tomber tout ticket produit dans
# un contenant qu'on garde vide exprès :
#
#   · SOLDÉ — N fermés / N total, N > 0. La phase est finie ; seule sa FERMETURE reste à faire, et
#     c'est une décision humaine (jalon go/no-go de la roadmap) que `doctor.sh` suggère. Sauté
#     depuis toujours.
#   · VIDE — 0 / 0. La phase n'est pas découpée, et parfois à dessein : `docs/06-roadmap.md` pose
#     que la « Phase 9 reste un contenant vide à dessein : on n'empaquette pas une cible mouvante ».
#     Un contenant vide n'est pas un contenant courant — y ranger un ticket défait le découpage
#     différé. Mesuré le 2026-08-27, au lendemain de #617 : `current-milestone produit` rendait
#     « Phase 9 — Poste de travail : distribution », 0 ouvert et 0 fermé, et RIEN ne le signalait ;
#     un run au défaut y planifiait zéro ticket. Sauté depuis #619.
#
# Chaque saut est donc NOMMÉ sur stderr avec sa cause, et l'abstention finale compte les deux
# séparément : « aucun jalon utilisable » ne dit pas s'il faut fermer une phase ou en découper une.
#
# Le prix est assumé et se dit plutôt que de se découvrir : le PREMIER ticket d'une phase neuve ne
# s'y range plus tout seul — il se crée avec `--milestone "<titre>"` explicite, et les suivants
# suivent d'eux-mêmes puisque le jalon n'est alors plus vide. Ne rien rendre (code 1) reste normal
# et non bloquant : `/ticket-create` omet simplement l'option.
gh_current_milestone() {
  local raw verdicts title rail="${1:-produit}"
  local genre champ titre_saute fermes total n_solde=0 n_vide=0
  if ! gl_rail_valide "$rail"; then
    echo "gh_current_milestone : rail inconnu « $rail » (attendu : produit | outillage)" >&2
    return 2
  fi
  raw="$(gh_graphql_read '{ '"$(gh_depot_gql)"' { milestones(first: 20, states: OPEN, orderBy: {field: DUE_DATE, direction: ASC}) { nodes { title description total: issues { totalCount } fermes: issues(states: CLOSED) { totalCount } } } } }')" || return 1
  # Le motif et le rail voyagent par ENVIRON et jamais par `awk -v`, qui INTERPRÈTE les
  # échappements de la valeur (leçon de #340, où un `-v` a failli importer trois phases de tickets
  # sans jalon, en silence).
  #
  # L'awk ne DÉCIDE pas de la sortie, il rend un verdict par jalon du rail — « T<TAB>titre » pour
  # le candidat retenu (et il s'arrête là), « S<TAB>cause<TAB>titre<TAB>fermés<TAB>total » pour
  # chaque jalon sauté avant lui. Le shell en fait un titre sur stdout et des lignes de diagnostic
  # sur stderr : mêler les deux flux dans awk rendrait la cause d'un saut indissociable du titre.
  verdicts="$(printf '%s' "$raw" | GL_RAIL_MOTIF="$GL_RAIL_MOTIF" GL_RAIL_CIBLE="$rail" awk '
    BEGIN { motif = ENVIRON["GL_RAIL_MOTIF"]; cible = ENVIRON["GL_RAIL_CIBLE"] }
    # La règle vit ICI et nulle part ailleurs — un second endroit qui répondrait « ce jalon est-il
    # courant ? » finirait par ne plus rendre le même verdict (même raison que gl_rail_de, #617).
    function etat_jalon(nb_fermes, nb_total) {
      if (nb_total == 0)       return "vide"
      if (nb_fermes >= nb_total) return "solde"
      return "candidat"
    }
    {
      n = split($0, parts, /\{"title":"/)
      for (i = 2; i <= n; i++) {
        node = parts[i]
        t = node; sub(/".*$/, "", t)
        gsub(/\\u0026/, "\\&", t); gsub(/\\u003e/, ">", t); gsub(/\\u003c/, "<", t)
        total = 0; closed = 0
        if (match(node, /"total":\{"totalCount":[0-9]+/))  { m = substr(node, RSTART, RLENGTH); sub(/.*:/, "", m); total = m + 0 }
        if (match(node, /"fermes":\{"totalCount":[0-9]+/)) { m = substr(node, RSTART, RLENGTH); sub(/.*:/, "", m); closed = m + 0 }
        # Le marqueur ne peut vivre que dans la description : le titre a déjà été coupé, et les
        # autres champs du nœud sont des compteurs. Chercher dans le nœud entier évite d`extraire
        # une description qui porte, elle, des guillemets échappés.
        rail = (tolower(node) ~ motif) ? "outillage" : "produit"
        if (rail != cible) continue
        etat = etat_jalon(closed, total)
        if (etat == "candidat") { print "T\t" t; exit }
        print "S\t" etat "\t" t "\t" closed "\t" total
      }
    }
  ')"
  title=""
  while IFS=$'\t' read -r genre champ titre_saute fermes total; do
    case "$genre" in
      T) title="$champ" ;;
      S)
        if [ "$champ" = vide ]; then
          n_vide=$((n_vide + 1))
          echo "gh_current_milestone : « $titre_saute » sautée — VIDE (aucun ticket) : un contenant vide n'est pas un contenant courant (#619)." >&2
        else
          n_solde=$((n_solde + 1))
          echo "gh_current_milestone : « $titre_saute » sautée — SOLDÉE ($fermes/$total fermés) : sa fermeture est une décision humaine." >&2
        fi
        ;;
    esac
  done <<< "$verdicts"
  if [ -z "$title" ]; then
    echo "gh_current_milestone : aucun jalon utilisable sur le rail « $rail » — $n_solde soldé(s) à fermer, $n_vide vide(s) à découper (rien à poser)" >&2
    return 1
  fi
  printf '%s\n' "$title"
}

# gh_milestones -> table plate des jalons, colonnes identiques à gl_milestones :
#     titre <TAB> etat <TAB> debut <TAB> echeance <TAB> fermes <TAB> total
# `debut` vaut TOUJOURS « - » : un jalon GitHub n'a pas de date de début. La colonne est conservée
# — un consommateur qui compte ses champs ne doit pas changer de code selon la forge.
gh_milestones() {
  local raw
  raw="$(gh_graphql_read '{ '"$(gh_depot_gql)"' { milestones(first: 50, orderBy: {field: DUE_DATE, direction: ASC}) { nodes { title description state dueOn total: issues { totalCount } fermes: issues(states: CLOSED) { totalCount } } } } }')" || return 1
  printf '# titre\tetat\tdebut\techeance\tfermes\ttotal\trail\n'
  printf '%s' "$raw" | GL_RAIL_MOTIF="$GL_RAIL_MOTIF" awk '
    BEGIN { motif = ENVIRON["GL_RAIL_MOTIF"] }
    {
      n = split($0, parts, /\{"title":"/)
      for (i = 2; i <= n; i++) {
        node = parts[i]
        title = node; sub(/".*$/, "", title)
        gsub(/\\u0026/, "\\&", title); gsub(/\\u003e/, ">", title); gsub(/\\u003c/, "<", title)

        # OPEN/CLOSED -> active/closed : le vocabulaire de gl_milestones, que /milestone-presentation
        # compare en dur.
        etat = "-"
        if (node ~ /"state":"OPEN"/)   etat = "active"
        if (node ~ /"state":"CLOSED"/) etat = "closed"

        echeance = "-"
        if (match(node, /"dueOn":"[0-9-]+/)) { m = substr(node, RSTART, RLENGTH); sub(/.*:"/, "", m); echeance = m }

        total = 0; fermes = 0
        if (match(node, /"total":\{"totalCount":[0-9]+/))  { m = substr(node, RSTART, RLENGTH); sub(/.*:/, "", m); total = m + 0 }
        if (match(node, /"fermes":\{"totalCount":[0-9]+/)) { m = substr(node, RSTART, RLENGTH); sub(/.*:/, "", m); fermes = m + 0 }

        rail = (tolower(node) ~ motif) ? "outillage" : "produit"

        printf "%s\t%s\t%s\t%s\t%d\t%d\t%s\n", title, etat, "-", echeance, fermes, total, rail
      }
    }
  '
}

# gh_milestone_issues <titre-exact> -> tickets d'un jalon, colonnes identiques à gl_milestone_issues.
# DEUX lectures : le filtre GraphQL de GitHub désigne un jalon par son NUMÉRO, pas par son titre. On
# résout donc le titre d'abord. Le contrat porte sur la sortie, pas sur le nombre d'allers-retours.
#
# ⚠ Comme gh_backlog_table, ce verbe répond à « qui existe ? » et rend la colonne `statut` VIDE :
# c'est `st_milestone_issues` qui la recouvre depuis la carte des items du projet.
# gh_milestone_number <titre-exact> -> le NUMÉRO du jalon, la seule prise que l'API REST accepte.
# Extrait de gh_milestone_issues, qui le résolvait déjà : gh_create_issue en a besoin pour la même
# raison, et deux résolutions à tenir d'accord seraient le premier moyen d'en voir une se périmer.
gh_milestone_number() {
  local title="$1" raw numero
  if [ -z "$title" ]; then echo "usage: gh_milestone_number <titre-exact-du-milestone>" >&2; return 2; fi
  raw="$(gh_graphql_read '{ '"$(gh_depot_gql)"' { milestones(first: 50) { nodes { number title } } } }')" || return 1
  numero="$(printf '%s' "$raw" | awk -v cible="$title" '
    {
      n = split($0, parts, /\{"number":/)
      for (i = 2; i <= n; i++) {
        node = parts[i]
        match(node, /^[0-9]+/); num = substr(node, RSTART, RLENGTH)
        t = "-"
        if (match(node, /,"title":"/)) { t = substr(node, RSTART + RLENGTH); sub(/"\}.*$/, "", t); sub(/",".*$/, "", t) }
        if (t == cible) { print num; exit }
      }
    }
  ')"
  if [ -z "$numero" ]; then
    echo "aucun jalon « $title » dans $GL_GH_REPO (titre exact attendu — cf. lib.sh milestones)" >&2
    return 1
  fi
  printf '%s\n' "$numero"
}

gh_milestone_issues() {
  local title="$1" raw numero rows
  if [ -z "$title" ]; then echo "usage: gh_milestone_issues <titre-exact-du-milestone>" >&2; return 2; fi

  numero="$(gh_milestone_number "$title")" || {
    echo "gh_milestone_issues : jalon « $title » non résolu" >&2
    return 1
  }

  raw="$(gh_graphql_read '{ '"$(gh_depot_gql)"' { milestone(number: '"$numero"') { issues(first: 100, orderBy: {field: CREATED_AT, direction: DESC}) { nodes { number title labels(first: 30) { nodes { name } } } } } } }')" || return 1
  rows="$(printf '%s\n' "$raw" | awk '
    {
      n = split($0, parts, /\{"number":/)
      for (i = 2; i <= n; i++) {
        node = parts[i]
        match(node, /^[0-9]+/); iid = substr(node, RSTART, RLENGTH)

        titre = "-"
        if (match(node, /,"title":"/)) {
          rest = substr(node, RSTART + RLENGTH)
          if (match(rest, /","labels":/)) titre = substr(rest, 1, RSTART - 1)
        }
        gsub(/\\u0026/, "\\&", titre); gsub(/\\u003e/, ">", titre); gsub(/\\u003c/, "<", titre)

        type = "-"; agent = "-"; prio = "-"
        if (match(node, /type::[a-z]+/))  type  = substr(node, RSTART + 6, RLENGTH - 6)
        if (match(node, /agent::[a-z]+/)) agent = substr(node, RSTART + 7, RLENGTH - 7)
        if (match(node, /prio::[a-z]+/))  prio  = substr(node, RSTART + 6, RLENGTH - 6)

        printf "%s\t%s\t%s\t%s\t%s\t%s\n", iid, "-", type, agent, prio, titre
      }
    }
  ')"

  if [ -z "$rows" ]; then
    echo "gh_milestone_issues : aucun ticket pour le jalon « $title »" >&2
    return 1
  fi
  printf '# iid\tstatut\ttype\tagent\tprio\ttitre\n'
  printf '%s\n' "$rows"
}

# gh_issue_link <iid> <iid-cible> -> le pendant du lien « relates to », qui n'existe pas côté GitHub.
#
# Ce qui existe, et qui fait le même travail : une RÉFÉRENCE CROISÉE. Un « #<n> » écrit dans un corps
# ou un commentaire apparaît dans la chronologie du ticket cité — c'est nativement bidirectionnel, et
# c'est déjà ce que produit la convention de découpage (« Sous-ticket de #<parent> » en tête de
# description). La fonction se contente donc de garantir la mention, et ne poste un commentaire que
# s'il n'y en a aucune : idempotent comme son jumeau, qui traite un lien déjà posé comme un succès.
gh_issue_link() {
  local iid="$1" target="$2" raw note
  if [ -z "$iid" ] || [ -z "$target" ]; then echo "usage: gh_issue_link <iid> <iid-cible>" >&2; return 2; fi
  raw="$(gh_graphql_read '{ '"$(gh_depot_gql)"' { issue(number:'"$iid"') { body comments(first: 100) { nodes { body } } } } }')" || return 1
  case "$raw" in
    *'"issue":null'*) echo "Ticket #$iid introuvable dans $GL_GH_REPO" >&2; return 1 ;;
  esac
  # Borné à droite : « #33 » ne doit pas passer pour une mention de « #339 ».
  if printf '%s' "$raw" | grep -qE "#$target([^0-9]|$)"; then
    printf 'Lien déjà présent : #%s ↔ #%s\n' "$iid" "$target"
    return 0
  fi
  # Brouillon que personne ne relit : répertoire temporaire du système, pas .maestro/ (règle #234).
  note="$(mktemp "${TMPDIR:-/tmp}/maestro-lien.XXXXXX")" || return 1
  printf 'Lié à #%s.\n' "$target" > "$note"
  if ! gh_issue_note "$iid" "$note" >/dev/null; then
    rm -f "$note"
    echo "Échec du lien #$iid ↔ #$target" >&2
    return 1
  fi
  rm -f "$note"
  printf 'Lien posé : #%s ↔ #%s\n' "$iid" "$target"
}

# gh_subticket_add <iid-parent> <iid-lot> -> le rattachement natif, corps de gl_subticket_add.
#
# LES DEUX IDENTIFIANTS DE NŒUD ET LE PARENT COURANT VIENNENT D'UNE SEULE LECTURE, SOUS ALIAS, pour
# la raison du `jalon:` de gh_issue_raw : sans eux, la clé `id` désignerait à la fois le parent et le
# lot, et `number` à la fois le lot et le parent qu'il porte déjà — un `grep` global prendrait le
# premier trouvé, c'est-à-dire tantôt l'un tantôt l'autre selon l'ordre de la réponse. Un alias
# tranche à la source ; une extraction plus fine ne ferait que déplacer l'ambiguïté.
#
# LES DEUX ABSENCES SE DISTINGUENT (`"p":null` / `"l":null`), et ce n'est pas de la coquetterie :
# « #392 introuvable » envoie vérifier le lot, « #389 introuvable » envoie vérifier le parent, et
# c'est le backfill (#392) qui lira ces messages sur 41 parents.
gh_subticket_add() {
  local parent="$1" lot="$2" raw pid lid actuel mutation out
  if [ -z "$parent" ] || [ -z "$lot" ]; then echo "usage: gh_subticket_add <iid-parent> <iid-lot>" >&2; return 2; fi
  raw="$(gh_graphql_read '{ '"$(gh_depot_gql)"' { p: issue(number:'"$parent"') { pid: id } l: issue(number:'"$lot"') { lid: id parent { ppnum: number } } } }')" || return 1
  case "$raw" in
    *'"repository":null'*) echo "Dépôt $GL_GH_REPO illisible (inconnu ou droits insuffisants)" >&2; return 1 ;;
    *'"p":null'*) echo "Ticket #$parent introuvable dans $GL_GH_REPO" >&2; return 1 ;;
    *'"l":null'*) echo "Ticket #$lot introuvable dans $GL_GH_REPO" >&2; return 1 ;;
  esac
  pid="$(printf '%s' "$raw" | grep -o '"pid":"[^"]*"' | head -1 | sed 's/.*:"//; s/"$//')"
  lid="$(printf '%s' "$raw" | grep -o '"lid":"[^"]*"' | head -1 | sed 's/.*:"//; s/"$//')"
  if [ -z "$pid" ] || [ -z "$lid" ]; then
    echo "gh_subticket_add : identifiants de nœud illisibles pour #$parent / #$lot" >&2
    return 1
  fi
  # Absent quand `parent` est `null` : le motif ne matche alors rien et la variable reste vide, ce
  # qui est exactement « ce lot n'a pas encore de parent ».
  actuel="$(printf '%s' "$raw" | grep -o '"ppnum":[0-9]*' | head -1 | sed 's/.*://')"
  if [ -n "$actuel" ]; then
    if [ "$actuel" = "$parent" ]; then
      printf 'Lot déjà rattaché : #%s → #%s\n' "$lot" "$parent"
      return 0
    fi
    echo "#$lot est déjà un lot de #$actuel — le rattacher à #$parent le lui retirerait." >&2
    echo "  Si c'est voulu, détacher d'abord côté forge ; sinon, vérifier les deux iid." >&2
    return 1
  fi
  # `gh_graphql_read` n'est PAS utilisé ici : son retry sur réponse vide ré-appliquerait la mutation
  # (règle posée avec lui, et valable pour toutes les écritures du fichier). Les identifiants sont
  # interpolés directement, comme partout ici — ce sont des valeurs opaques que l'API vient de
  # rendre, aucune ne peut porter de guillemet.
  mutation="mutation { addSubIssue(input: {issueId: \"$pid\", subIssueId: \"$lid\"}) { subIssue { number } } }"
  out="$(gh api graphql -f query="$mutation" 2>&1)"
  case "$out" in
    *'"errors"'*)   printf '%s\n' "$out" >&2; echo "Échec du rattachement de #$lot à #$parent" >&2; return 1 ;;
    *'"subIssue"'*) ;;
    *)              printf '%s\n' "$out" >&2; echo "Échec du rattachement de #$lot à #$parent" >&2; return 1 ;;
  esac
  printf 'Lot rattaché : #%s → #%s\n' "$lot" "$parent"
}

# gh_subticket_order <iid-parent> <iid…> -> la pose de l'ordre, corps de gl_subticket_order.
#
# UNE LECTURE, UNE ÉCRITURE, quel que soit le nombre de lots. La lecture rend l'identifiant de nœud
# du parent ET celui de chacun de ses lots — c'est ce que la vue texte de #390 ne peut pas porter,
# elle ne transporte que des numéros, là où les mutations veulent des identifiants de nœud. Les
# deux verbes lisent donc la même relation par deux chemins, sans que l'un puisse remplacer l'autre.
#
# TOUT LE CALCUL TIENT DANS UN SEUL AWK : appariement numéro → identifiant, validation, puis
# construction du document de mutation. Un fork par lot serait le réflexe, et coûterait ~120 ms
# pièce sous MSYS (#577) sur un verbe que le backfill appellera 41 fois. La liste demandée voyage
# par ENVIRON et jamais par `awk -v`, qui interprète les échappements (#340) — elle n'en porte
# aucun, les iid étant validés en chiffres par l'appelant, mais la règle du fichier ne souffre pas
# d'exception qu'il faudrait ensuite justifier.
#
# LA RÉPONSE EST ACCUMULÉE AVANT D'ÊTRE LUE (`buf = buf $0`), comme dans `gh_lots_natifs` : rien ne
# promet que `gh api` rende son JSON sur une seule ligne, et une paire numéro/identifiant coupée en
# deux par un saut de ligne ferait disparaître un lot — donc échouer la validation sur un parent
# parfaitement sain.
gh_subticket_order() {
  local parent="$1" raw pid mutation out ordre
  # IFS est fixé pour que le `$*` ci-dessous joigne les iid par un espace, quel qu'ait été l'IFS de
  # l'appelant : c'est ce séparateur exact que le `split` de l'awk attend.
  local IFS=' '
  if [ -z "$parent" ] || [ -z "$2" ]; then echo "usage: gh_subticket_order <iid-parent> <iid>…" >&2; return 2; fi
  shift
  ordre="$*"
  raw="$(gh_graphql_read '{ '"$(gh_depot_gql)"' { issue(number:'"$parent"') { pid: id lots: subIssues(first: 100) { nodes { number lid: id } } } } }')" || return 1
  case "$raw" in
    *'"repository":null'*) echo "Dépôt $GL_GH_REPO illisible (inconnu ou droits insuffisants)" >&2; return 1 ;;
    *'"issue":null'*)      echo "Ticket #$parent introuvable dans $GL_GH_REPO" >&2; return 1 ;;
  esac
  pid="$(printf '%s' "$raw" | grep -o '"pid":"[^"]*"' | head -1 | sed 's/.*:"//; s/"$//')"
  if [ -z "$pid" ]; then
    echo "gh_subticket_order : identifiant de nœud illisible pour #$parent" >&2
    return 1
  fi
  mutation="$(printf '%s' "$raw" | LC_ALL=C GL_ORDRE="$ordre" GL_PID="$pid" awk '
    { buf = buf $0 }
    END {
      s = buf
      while (match(s, /"number":[0-9]+,"lid":"[^"]*"/)) {
        paire = substr(s, RSTART, RLENGTH)
        s = substr(s, RSTART + RLENGTH)
        split(paire, ch, /,"lid":"/)
        num = ch[1]; sub(/^"number":/, "", num)
        nid = ch[2]; sub(/"$/, "", nid)
        id[num] = nid
      }

      n = split(ENVIRON["GL_ORDRE"], veut, / /)
      manque = ""
      for (k = 1; k <= n; k++) {
        if (veut[k] in vu) { printf "!#%s est nommé deux fois dans la liste demandée.\n", veut[k]; exit }
        vu[veut[k]] = 1
        if (!(veut[k] in id)) manque = manque " #" veut[k]
      }
      if (manque != "") { printf "!Pas des lots de ce parent :%s\n", manque; exit }

      # Chaque lot est déplacé APRÈS son prédécesseur dans la liste demandée. Le premier ne bouge
      # pas : il est le point fixe dont tout le reste hérite sa place.
      out = "mutation {"
      for (k = 2; k <= n; k++)
        out = out sprintf(" m%d: reprioritizeSubIssue(input: {issueId: \"%s\", subIssueId: \"%s\", afterId: \"%s\"}) { issue { number } }", \
                          k, ENVIRON["GL_PID"], id[veut[k]], id[veut[k - 1]])
      print out " }"
    }
  ')"
  case "$mutation" in
    '!'*)       printf '%s\n' "${mutation#'!'}" >&2
                echo "  Aucun ordre n'a été posé sur #$parent." >&2
                return 1 ;;
    'mutation'*) ;;
    *)          echo "gh_subticket_order : ordre incalculable pour #$parent" >&2; return 1 ;;
  esac
  out="$(gh api graphql -f query="$mutation" 2>&1)"
  case "$out" in
    *'"errors"'*) printf '%s\n' "$out" >&2; echo "Échec de la pose de l'ordre des lots de #$parent" >&2; return 1 ;;
    *'"issue"'*)  ;;
    *)            printf '%s\n' "$out" >&2; echo "Échec de la pose de l'ordre des lots de #$parent" >&2; return 1 ;;
  esac
  printf 'Ordre posé sur les %s lots de #%s : %s\n' "$#" "$parent" "$ordre"
}

gh_prio() {
  local iid="$1"
  if [ -z "$iid" ]; then echo "gh_prio : iid manquant" >&2; return 2; fi
  gh_graphql_read '{ '"$(gh_depot_gql)"' { issue(number:'"$iid"') { labels(first: 30) { nodes { name } } } } }' \
    | grep -o 'prio::[a-z]*' | head -1
}

# --- Suivi maison : dates et temps passé ------------------------------------------------------------
# Le format est décrit en tête de fichier ; ce qui suit en est la seule implémentation. Quatre
# fonctions de plomberie (durées, lecture, écriture, champ) et quatre verbes qui s'y branchent.

# gh_secondes <durée> -> convertit une durée au format GitLab (« 2h », « 1h 30m », « 1d », « 1w 2d »,
# « 90m », collé ou espacé) en SECONDES. Refuse une valeur sans unité : « 30 » ne veut rien dire, et
# le deviner (minutes ? heures ?) fausserait un cumul qu'on ne relit jamais.
gh_secondes() {
  local d="$1"
  if [ -z "$d" ]; then echo "gh_secondes : durée manquante" >&2; return 2; fi
  printf '%s' "$d" | LC_ALL=C awk -v hj="$GL_HEURES_PAR_JOUR" -v js="$GL_JOURS_PAR_SEMAINE" '
    {
      s = tolower($0); gsub(/[ \t]+/, "", s)
      total = 0; reste = s
      while (match(reste, /^[0-9]+(w|d|h|m|s)/)) {
        seg = substr(reste, RSTART, RLENGTH)
        reste = substr(reste, RSTART + RLENGTH)
        unite = substr(seg, length(seg), 1)
        n = substr(seg, 1, length(seg) - 1) + 0
        if      (unite == "w") total += n * js * hj * 3600
        else if (unite == "d") total += n * hj * 3600
        else if (unite == "h") total += n * 3600
        else if (unite == "m") total += n * 60
        else                   total += n
      }
      if (reste != "" || s == "") { exit 1 }
      printf "%d", total
    }
  ' || { echo "gh_secondes : durée « $d » illisible (attendu : 1w 2d 3h 30m, unités w/d/h/m/s)" >&2; return 1; }
}

# gh_duree <secondes> -> le chemin inverse, dans le MÊME vocabulaire : ce qu'on lit est ce qu'on
# pourrait retaper. Composantes nulles omises ; « 0m » pour un total nul.
gh_duree() {
  local s="${1:-0}"
  [ "$s" -ge 0 ] 2>/dev/null || s=0
  LC_ALL=C awk -v s="$s" -v hj="$GL_HEURES_PAR_JOUR" -v js="$GL_JOURS_PAR_SEMAINE" '
    BEGIN {
      out = ""
      sem = int(s / (js * hj * 3600)); s -= sem * js * hj * 3600
      jou = int(s / (hj * 3600));      s -= jou * hj * 3600
      heu = int(s / 3600);             s -= heu * 3600
      min = int(s / 60);               s -= min * 60
      if (sem) out = out (out ? " " : "") sem "w"
      if (jou) out = out (out ? " " : "") jou "d"
      if (heu) out = out (out ? " " : "") heu "h"
      if (min) out = out (out ? " " : "") min "m"
      if (s)   out = out (out ? " " : "") s "s"
      printf "%s", (out == "" ? "0m" : out)
    }
  '
}

# gh_suivi_lire <iid> -> l'identifiant du commentaire de suivi en PREMIÈRE ligne (« - » s'il n'y en a
# pas encore), puis le BLOC MACHINE, une paire « clé=valeur » par ligne. Une seule lecture.
#
# Plafond de 100 commentaires, et `first` plutôt que `last` : le suivi est créé au démarrage du
# ticket puis réécrit EN PLACE, il garde donc sa position d'origine — tout au début du fil.
#
# DEUX MARQUEURS SONT CHERCHÉS, ET LEURS RÔLES SONT DISSYMÉTRIQUES (#400). Le commentaire de suivi
# est la donnée VIVANTE : il porte l'identifiant qu'on rendra, et c'est lui seul qu'on réécrira. Le
# commentaire d'import (`maestro:meta v1`) est une ARCHIVE : son identifiant n'est jamais rendu,
# donc jamais réécrit — le rendre reviendrait à faire écraser par le premier `gh_suivi_ecrire` le
# lien GitLab, le tableau et les relevés détaillés que l'import y a déposés, sur 352 tickets et sans
# retour. Ce qu'on en tire est RECOPIÉ dans le bloc du suivi par gh_suivi_fusion.
gh_suivi_lire() {
  local iid="$1" raw noeuds suivi meta id bloc
  if [ -z "$iid" ]; then echo "usage: gh_suivi_lire <iid>" >&2; return 2; fi
  raw="$(gh_graphql_read '{ '"$(gh_depot_gql)"' { issue(number:'"$iid"') { comments(first: 100) { nodes { databaseId createdAt body } } } } }')" || return 1
  case "$raw" in
    *'"issue":null'*) echo "Ticket #$iid introuvable dans $GL_GH_REPO" >&2; return 1 ;;
  esac
  # Une seule passe pour les deux nœuds : le suivi en ligne 1, l'import en ligne 2, vides s'ils
  # manquent. Un nœud tient sur UNE ligne — le JSON de `gh` est compact et échappe ses sauts de
  # ligne —, ce qui est exactement ce qui permet d'en rendre deux sans les confondre.
  noeuds="$(printf '%s' "$raw" | LC_ALL=C awk -v m_suivi="$GL_SUIVI_MARQUEUR" -v m_meta="$GL_META_MARQUEUR" '
    {
      n = split($0, parts, /\{"databaseId":/)
      for (i = 2; i <= n; i++) {
        if (suivi == "" && index(parts[i], m_suivi)) suivi = "{\"databaseId\":" parts[i]
        if (meta  == "" && index(parts[i], m_meta))  meta  = "{\"databaseId\":" parts[i]
      }
    }
    END { print suivi; print meta }
  ')"
  suivi="$(printf '%s\n' "$noeuds" | sed -n 1p)"
  meta="$(printf '%s\n' "$noeuds" | sed -n 2p)"

  id="-"
  bloc=""
  if [ -n "$suivi" ]; then
    id="$(printf '%s' "$suivi" | grep -o '"databaseId":[0-9]*' | head -1 | sed 's/.*://')"
    # Le corps déséchappé, puis les seules lignes situées ENTRE le marqueur et la fin du commentaire
    # HTML : le rendu humain qui suit ne doit jamais être relu comme de la donnée.
    bloc="$(printf '%s' "$suivi" | gl_json_string_field body | LC_ALL=C awk -v marqueur="$GL_SUIVI_MARQUEUR" '
      dans && /^-->/ { exit }
      dans { if ($0 ~ /^[a-z_]+=/) print }
      index($0, marqueur) { dans = 1 }
    ')"
  fi
  if [ -n "$meta" ]; then
    bloc="$(printf '%s\n' "$bloc" | gh_suivi_fusion "$meta")"
  fi

  printf '%s\n' "${id:--}"
  [ -n "$bloc" ] && printf '%s\n' "$bloc"
  return 0
}

# gh_suivi_fusion <nœud JSON du commentaire d'import> (stdin = bloc du suivi, éventuellement vide)
# -> le bloc machine augmenté de ce que l'import a écrit. C'est LA JOINTURE qui manquait (#400) :
# l'import et le quotidien écrivaient deux formats que rien ne lisait ensemble, si bien qu'un ticket
# importé repartait de zéro au premier log de temps.
#
# Trois règles, et la deuxième est le contenu de la décision :
#   1. LES DATES DU SUIVI L'EMPORTENT — un /ticket-start postérieur a pu reposer un début ; l'import
#      ne comble que ce qui manque. Sur un ticket jamais démarré depuis la bascule, le bloc est vide
#      et ce sont donc les dates GitLab qui sortent.
#   2. LE TEMPS IMPORTÉ DEVIENT UNE ENTRÉE `log=` comme une autre. Il n'est PAS rangé dans une clé à
#      part, et ce n'est pas un détail de forme : `temps` est RECALCULÉ comme la somme des `log=`
#      (gh_log_time), donc un total posé à côté du détail serait effacé au premier log — c'est
#      exactement la panne qu'on répare. Y entrer par le détail est ce qui rend l'addition acquise.
#   3. LA FUSION EST IDEMPOTENTE, et sa mémoire est l'entrée elle-même : son résumé vaut
#      $GL_SUIVI_IMPORT, on ne la repose donc jamais deux fois. Aucune clé de témoin à tenir
#      d'accord avec la donnée qu'elle décrit.
gh_suivi_fusion() {
  local noeud="$1" bloc lu jour cle val temps
  bloc="$(cat)"
  # La ligne de clés de l'import, rendue au format du bloc (« clé=valeur » par ligne). Seules les
  # trois clés que le suivi sait porter sont reprises : `iid`, `assignes` et `lies` restent dans le
  # commentaire d'import, qui n'est ni réécrit ni perdu — les recopier ferait deux archives.
  lu="$(printf '%s' "$noeud" | gl_json_string_field body | LC_ALL=C awk -v marqueur="$GL_META_MARQUEUR" '
    index($0, marqueur) {
      for (i = 1; i <= NF; i++) if ($i ~ /^(temps_s|debut|echeance)=[^ ]+$/) print $i
      exit
    }')"

  for cle in debut echeance; do
    val="$(printf '%s\n' "$lu" | gh_suivi_champ "$cle")"
    if [ -n "$val" ] && [ -z "$(printf '%s\n' "$bloc" | gh_suivi_champ "$cle")" ]; then
      bloc="$bloc"$'\n'"$cle=$val"
    fi
  done

  temps="$(printf '%s\n' "$lu" | gh_suivi_champ temps_s)"
  case "$temps" in ''|0|*[!0-9]*) temps="" ;; esac
  if [ -n "$temps" ] && ! printf '%s\n' "$bloc" | grep -q "^log=.*|$GL_SUIVI_IMPORT\$"; then
    # La date de l'entrée est celle du commentaire d'import — le jour où cet historique est entré
    # dans le registre, seule date que l'import ait laissée. À défaut, le début GitLab du ticket.
    jour="$(printf '%s' "$noeud" | grep -o '"createdAt":"[0-9-]*' | head -1 | sed 's/.*:"//')"
    [ -n "$jour" ] || jour="$(printf '%s\n' "$lu" | gh_suivi_champ debut)"
    [ -n "$jour" ] || jour="$(date +%F)"
    bloc="$bloc"$'\n'"log=$jour|$temps|$GL_SUIVI_IMPORT"
    bloc="$(printf '%s\n' "$bloc" | grep -v '^temps=')"
    bloc="$bloc"$'\n'"temps=$(printf '%s\n' "$bloc" | gh_suivi_total)"
  fi
  printf '%s\n' "$bloc" | sed '/^$/d'
}

# gh_suivi_champ <clé> (stdin = bloc machine) -> la valeur de la clé, vide si absente.
gh_suivi_champ() {
  sed -n 's/^'"$1"'=//p' | head -1
}

# gh_suivi_total (stdin = bloc machine) -> la somme des entrées `log=`, en secondes. La règle du
# format en une ligne, appelée par les DEUX endroits qui l'appliquent (gh_log_time et la fusion) :
# deux copies de cette somme divergeraient le jour où l'une apprend à ignorer une entrée.
gh_suivi_total() {
  LC_ALL=C sed -n 's/^log=//p' | LC_ALL=C awk -F'|' '{ t += $2 } END { printf "%d", t + 0 }'
}

# gh_suivi_ecrire <iid> <id-commentaire|-> (stdin = bloc machine) -> écrit ou réécrit le commentaire
# de suivi. Le bloc machine fait foi ; le rendu humain en est DÉRIVÉ ici même, donc les deux ne
# peuvent pas diverger. Les clés inconnues traversent intactes (règle 4 du format).
gh_suivi_ecrire() {
  local iid="$1" id="$2" bloc fichier debut echeance temps
  if [ -z "$iid" ] || [ -z "$id" ]; then echo "usage: gh_suivi_ecrire <iid> <id|->" >&2; return 2; fi
  bloc="$(cat)"
  debut="$(printf '%s\n' "$bloc" | gh_suivi_champ debut)"
  echeance="$(printf '%s\n' "$bloc" | gh_suivi_champ echeance)"
  temps="$(printf '%s\n' "$bloc" | gh_suivi_champ temps)"

  # Brouillon que personne ne relit (règle #234) : répertoire temporaire du système.
  fichier="$(mktemp "${TMPDIR:-/tmp}/maestro-suivi.XXXXXX")" || return 1
  {
    printf '<!-- %s\n' "$GL_SUIVI_MARQUEUR"
    printf '%s\n' "$bloc" | grep -v '^$'
    printf -- '-->\n'
    printf '**⏱ Suivi Maestro** — début %s · échéance %s · temps passé **%s**\n' \
      "${debut:--}" "${echeance:--}" "$(gh_duree "${temps:-0}")"
    if printf '%s\n' "$bloc" | grep -q '^log='; then
      printf '\n'
      printf '%s\n' "$bloc" | sed -n 's/^log=//p' | while IFS='|' read -r d s r; do
        printf -- '- %s — %s%s\n' "$d" "$(gh_duree "${s:-0}")" "$([ -n "$r" ] && printf ' — %s' "$r")"
      done
    fi
  } > "$fichier"

  local ok=0
  if [ "$id" = "-" ]; then
    gh api -X POST "repos/$GL_GH_REPO/issues/$iid/comments" -F body=@"$fichier" >/dev/null 2>&1 && ok=1
  else
    gh api -X PATCH "repos/$GL_GH_REPO/issues/comments/$id" -F body=@"$fichier" >/dev/null 2>&1 && ok=1
  fi
  rm -f "$fichier"
  [ "$ok" = 1 ] || { echo "gh_suivi_ecrire : écriture du suivi de #$iid en échec" >&2; return 1; }
}

gh_get_start_date() {
  local iid="$1"
  if [ -z "$iid" ]; then echo "gh_get_start_date : iid manquant" >&2; return 2; fi
  gh_suivi_lire "$iid" 2>/dev/null | tail -n +2 | gh_suivi_champ debut
}

# gh_get_time_spent <iid> [--hors-import] -> le temps total en secondes ; avec l'option, ce qui a
# été loggé DEPUIS LA BASCULE, l'historique importé retranché.
#
# L'option existe parce que la jointure de #400 déplace une question que /ticket-finish posait à ce
# verbe : « du temps est-il déjà loggé ? » servait d'idempotence — ne pas compter deux fois le cycle
# de dev sur une ré-exécution —, et un ticket importé répond désormais « oui » avant qu'aucune
# session n'ait travaillé dessus. Le garde-fou aurait donc avalé en silence le temps de la session
# qui finit le ticket, ce qui est le contraire de ce que la jointure vient acquérir.
gh_get_time_spent() {
  local iid="$1" mode="${2:-}" bloc v
  if [ -z "$iid" ]; then echo "gh_get_time_spent : iid manquant" >&2; return 2; fi
  bloc="$(gh_suivi_lire "$iid" 2>/dev/null | tail -n +2)"
  case "$mode" in
    --hors-import) v="$(printf '%s\n' "$bloc" | grep -v "^log=.*|$GL_SUIVI_IMPORT\$" | gh_suivi_total)" ;;
    '')            v="$(printf '%s\n' "$bloc" | gh_suivi_champ temps)" ;;
    *)             echo "gh_get_time_spent : option « $mode » inconnue (attendu : --hors-import)" >&2; return 2 ;;
  esac
  printf '%s\n' "${v:-0}"
}

# gh_set_dates <iid> [début] [échéance] -> pose les dates dans le suivi. Un argument vide laisse le
# champ INCHANGÉ (même contrat que gl_set_dates), ce qui suppose de relire le bloc avant d'écrire.
gh_set_dates() {
  local iid="$1" start="$2" due="$3" lu id bloc
  if [ -z "$iid" ]; then echo "usage: gh_set_dates <iid> [début YYYY-MM-DD] [échéance YYYY-MM-DD]" >&2; return 2; fi
  if [ -z "$start" ] && [ -z "$due" ]; then echo "gh_set_dates : au moins une date (début ou échéance) requise" >&2; return 2; fi
  lu="$(gh_suivi_lire "$iid")" || return 1
  id="$(printf '%s\n' "$lu" | head -1)"
  bloc="$(printf '%s\n' "$lu" | tail -n +2)"
  [ -n "$start" ] && bloc="$(printf '%s\n' "$bloc" | grep -v '^debut=')"$'\n'"debut=$start"
  [ -n "$due" ]   && bloc="$(printf '%s\n' "$bloc" | grep -v '^echeance=')"$'\n'"echeance=$due"
  if ! printf '%s\n' "$bloc" | gh_suivi_ecrire "$iid" "$id"; then
    echo "Échec de la pose des dates sur #$iid" >&2; return 1
  fi
  printf 'Dates de #%s → début=%s, échéance=%s\n' "$iid" "${start:-inchangé}" "${due:-inchangé}"
}

# gh_log_time <iid> <durée> [résumé] -> ajoute une entrée de temps. ADDITIF comme côté GitLab : on
# ajoute une ligne `log=` et on RECALCULE `temps` comme leur somme — la valeur agrégée ne peut donc
# jamais dériver de son détail, même après une reprise ou une écriture partielle.
gh_log_time() {
  local iid="$1" dur="$2" resume="${3:-}" secondes lu id bloc total
  if [ -z "$iid" ] || [ -z "$dur" ]; then echo "usage: gh_log_time <iid> <durée> [résumé]" >&2; return 2; fi
  secondes="$(gh_secondes "$dur")" || return 1
  # Le résumé est APLATI : « | » est le séparateur du format, un retour à la ligne en casserait la
  # lecture ligne à ligne. Un séparateur qui peut apparaître dans la valeur n'en est pas un.
  resume="$(printf '%s' "$resume" | tr '\n|' '  ')"
  lu="$(gh_suivi_lire "$iid")" || return 1
  id="$(printf '%s\n' "$lu" | head -1)"
  bloc="$(printf '%s\n' "$lu" | tail -n +2)"
  bloc="$(printf '%s\n' "$bloc" | grep -v '^temps=' | grep -v '^$')"$'\n'"log=$(date +%F)|$secondes|$resume"
  total="$(printf '%s\n' "$bloc" | gh_suivi_total)"
  bloc="$bloc"$'\n'"temps=$total"
  if ! printf '%s\n' "$bloc" | gh_suivi_ecrire "$iid" "$id"; then
    echo "Échec du log de temps sur #$iid" >&2; return 1
  fi
  printf 'Temps loggé sur #%s : %s\n' "$iid" "$dur"
}

# --- Pull requests ----------------------------------------------------------------------------------

# gh_mr_brief <branche> -> « etat<TAB>numéro<TAB>sha » (cf. gl_mr_brief pour le contrat).
# `headRefOid` et NON `mergeCommit` : le sha attendu est la tête de la branche SOURCE, la seule
# référence locale utilisable quand le projet merge en squash. GitHub la conserve après le merge et
# après la suppression de la branche.
gh_mr_brief() {
  local branche="$1" raw etat mr sha
  if [ -z "$branche" ]; then echo "usage: gh_mr_brief <branche>" >&2; return 2; fi
  raw="$(gh_graphql_read '{ '"$(gh_depot_gql)"' { pullRequests(headRefName: "'"$branche"'", first: 1, orderBy: {field: CREATED_AT, direction: DESC}) { nodes { number state headRefOid } } } }')" || return 1
  mr="$(printf '%s' "$raw" | grep -o '"number":[0-9]*' | head -1 | sed 's/.*://')"
  [ -n "$mr" ] || return 1
  case "$(printf '%s' "$raw" | grep -o '"state":"[A-Z_]*"' | head -1)" in
    *MERGED*) etat="merged" ;;
    *CLOSED*) etat="closed" ;;
    *OPEN*)   etat="opened" ;;
    *)        return 1 ;;
  esac
  sha="$(printf '%s' "$raw" | grep -o '"headRefOid":"[0-9a-f]*"' | head -1 | sed 's/.*:"//; s/"$//')"
  printf '%s\t%s\t%s\n' "$etat" "$mr" "${sha:--}"
}

# gh_mr_briefs <branche…> -> le brief de la PR de N branches en UNE lecture, une ligne TSV par
# branche QUI EN A UNE : « <branche><TAB><etat><TAB><numéro><TAB><sha> ». Contrat de sortie
# identique à gh_mr_brief, colonne pour colonne, aux deux différences près qui font tout l'intérêt :
# la branche est rendue en tête (l'appelant ne peut plus se fier à l'ordre) et une branche SANS PR
# ne rend AUCUNE LIGNE — même parti pris que gh_issues_state, et c'est l'appelant qui décide de ce
# que vaut ce silence (gl_cleanup_merged garde la branche, gl_worktree_done interroge le ticket).
#
# C'EST LE PENDANT GROUPÉ DE gh_mr_brief, ET IL RÉPOND À LA MÊME QUESTION POUR N BRANCHES (#602,
# docs/10 §9.8). Deux appelants la posaient une branche à la fois : `gl_cleanup_merged`, une lecture
# par branche locale (8 sur le poste de référence), et le ramassage de `worktree.sh gc`, une par
# worktree. À 2,5 s l'aller — latence réseau mesurée, irréductible — le nombre d'allers faisait tout
# le prix, exactement comme dans #577.
#
# Les branches sont demandées sous des ALIAS INDEXÉS (`b0:`, `b1:`…) et non sous leur nom : un nom
# de branche porte des `/` et des `-`, qu'un alias GraphQL n'accepte pas. C'est donc le RANG qui
# fait le lien, et il est tenu des deux côtés — d'où le parsing par `"b<N>":` plutôt que par
# l'ordre des réponses, que rien dans le contrat de l'API n'oblige à conserver.
#
# Parsing en awk et non par `--jq`, pour la raison de st_statuts : une réponse partiellement en
# erreur fait recracher à `gh api graphql --jq` le JSON BRUT sans appliquer le filtre, et le
# résultat serait zéro ligne — c'est-à-dire « aucune PR nulle part », avec le code de succès. Sur ce
# verbe-ci, cela se traduirait par « aucune branche mergée » : une purge qui ne supprime rien passe
# inaperçue, là où l'inverse détruirait du travail. On ne parie pas là-dessus.
gh_mr_briefs() {
  if [ "$#" -eq 0 ]; then echo "usage: gh_mr_briefs <branche…>" >&2; return 2; fi
  local branche champs='' raw rang=0 table=''
  for branche in "$@"; do
    case "$branche" in
      ''|*'"'*) echo "gh_mr_briefs : nom de branche invalide « $branche »" >&2; return 2 ;;
    esac
    champs="$champs b$rang: pullRequests(headRefName: \"$branche\", first: 1, orderBy: {field: CREATED_AT, direction: DESC}) { nodes { number state headRefOid } }"
    table="$table$rang"$'\t'"$branche"$'\n'
    rang=$((rang + 1))
  done
  raw="$(gh_graphql_read '{ '"$(gh_depot_gql)"' {'"$champs"' } }')" || return 1
  case "$raw" in
    *'"repository":null'*) echo "Dépôt $GL_GH_REPO illisible (inconnu ou droits insuffisants)" >&2; return 1 ;;
  esac

  # Le rang est retraduit en nom de branche par la table ci-dessus, jamais par un `awk -v` : elle
  # porte des noms de branche, et `-v` interpréterait leurs échappements (#340).
  printf '%s' "$raw" | awk '
    {
      s = $0
      while (match(s, /"b[0-9]+":\{"nodes":\[/)) {
        tag = substr(s, RSTART, RLENGTH)
        rang = tag; sub(/^"b/, "", rang); sub(/":.*/, "", rang)
        s = substr(s, RSTART + RLENGTH)
        fin = index(s, "]")
        corps = (fin > 0) ? substr(s, 1, fin - 1) : ""
        if (corps == "") continue          # nodes vide : aucune PR sur cette branche
        num = ""; etat = ""; sha = ""
        if (match(corps, /"number":[0-9]+/))          num  = substr(corps, RSTART + 9,  RLENGTH - 9)
        if (match(corps, /"state":"[A-Z_]+"/))        etat = substr(corps, RSTART + 9,  RLENGTH - 10)
        if (match(corps, /"headRefOid":"[0-9a-f]*"/)) sha  = substr(corps, RSTART + 14, RLENGTH - 15)
        if (num == "" || etat == "") continue
        print rang "\t" etat "\t" num "\t" sha
      }
    }
  ' | ST_TABLE="$table" awk -F'\t' '
    BEGIN {
      n = split(ENVIRON["ST_TABLE"], lignes, "\n")
      for (i = 1; i <= n; i++) {
        if (lignes[i] == "") continue
        p = index(lignes[i], "\t")
        nom[substr(lignes[i], 1, p - 1)] = substr(lignes[i], p + 1)
      }
    }
    {
      # Vocabulaire GITLAB en sortie, comme gh_mr_brief : les comparaisons en dur des appelants
      # (« merged ») sont ainsi hors du chantier de la migration.
      etat = ($2 == "MERGED") ? "merged" : (($2 == "CLOSED") ? "closed" : (($2 == "OPEN") ? "opened" : ""))
      if (etat == "" || !($1 in nom)) next
      print nom[$1] "\t" etat "\t" $3 "\t" (($4 == "") ? "-" : $4)
    }
  '
}

gh_mr_iid() {
  local ref="$1" mr
  if [ -z "$ref" ]; then echo "gh_mr_iid : branche manquante" >&2; return 2; fi
  mr="$(gh_graphql_read '{ '"$(gh_depot_gql)"' { pullRequests(headRefName: "'"$ref"'", states: OPEN, first: 1) { nodes { number } } } }' \
        | grep -o '"number":[0-9]*' | head -1 | sed 's/.*://')"
  if [ -z "$mr" ]; then echo "Aucune PR ouverte pour la branche « $ref » dans $GL_GH_REPO" >&2; return 1; fi
  printf '%s\n' "$mr"
}

# gh_create_pr <iid> <branche> <titre> <fichier> -> ouvre la PR en Draft vers main. Appelée par
# gl_create_mr APRÈS ses validations et son test d'idempotence, qui sont communs aux deux forges.
#
# Passe par l'API REST et non par `gh pr create` : cette dernière déduit le dépôt et la branche
# distante du remote git, qui a pointé sur GitLab jusqu'à la bascule (#343). L'appel explicite
# marche des deux côtés de la bascule.
#
# Pas d'équivalent de `--remove-source-branch` : sur GitHub la suppression de la branche au merge est
# un réglage du DÉPÔT (`delete_branch_on_merge`), pas une option de la PR. Il est POSÉ depuis le
# 2026-08-19 (#384) ; il ne l'était pas depuis la bascule, et comme rien ici ne pouvait le
# remplacer, 22 branches distantes s'étaient accumulées. Ne pas chercher à le rattraper d'ici : une
# PR n'a pas de prise dessus, c'est doctor.sh §6 qui en surveille la dérive.
gh_create_pr() {
  local iid="$1" branche="$2" titre="$3" fichier="$4" out mr url
  out="$(gh api -X POST "repos/$GL_GH_REPO/pulls" \
        -f title="$titre" -f head="$branche" -f base=main -F draft=true -F body=@"$fichier" 2>&1)"
  mr="$(printf '%s' "$out" | grep -o '"number":[0-9]*' | head -1 | sed 's/.*://')"
  if [ -z "$mr" ]; then
    printf '%s\n' "$out" >&2
    echo "Échec de la création de la PR pour #$iid (branche « $branche »)" >&2
    return 1
  fi
  url="$(printf '%s' "$out" | grep -o '"html_url":"[^"]*/pull/[0-9]*"' | head -1 | sed 's/.*:"//; s/"$//')"
  printf '%s\n' "${url:-$(gl_mr_url "$mr")}"
}

# gh_mr_review_info <mr> -> « auteur <TAB> relecteurs » (CSV, champ vide si aucun).
gh_mr_review_info() {
  local mr="$1" raw auteur rev
  if [ -z "$mr" ]; then echo "usage: gh_mr_review_info <mr>" >&2; return 2; fi
  raw="$(gh_graphql_read '{ '"$(gh_depot_gql)"' { pullRequest(number: '"$mr"') { author { login } reviewRequests(first: 20) { nodes { requestedReviewer { ... on User { login } } } } } } }')" || return 1
  case "$raw" in
    *'"reviewRequests"'*) ;;
    *) echo "gh_mr_review_info : PR #$mr illisible dans $GL_GH_REPO" >&2; return 1 ;;
  esac
  auteur="$(printf '%s' "$raw" | grep -o '"author":{"login":"[^"]*"' | head -1 | sed 's/.*"login":"//; s/"$//')"
  # Les relecteurs se lisent APRÈS la clé "reviewRequests" : l'auteur, lu plus haut, ne doit pas y entrer.
  rev="$(printf '%s' "$raw" | sed 's/.*"reviewRequests"//' | grep -o '"login":"[^"]*"' \
         | sed 's/.*"login":"//; s/"$//' | awk '{ out = (NR == 1 ? $0 : out "," $0) } END { if (NR) print out }')"
  printf '%s\t%s\n' "$auteur" "$rev"
}

gh_set_reviewer() {
  local mr="$1" who="$2" auteur="$3" out
  out="$(gh api -X POST "repos/$GL_GH_REPO/pulls/$mr/requested_reviewers" -f "reviewers[]=$who" 2>&1)" || {
    echo "gh_set_reviewer : échec de la pose du relecteur @$who sur #$mr : $out" >&2
    return 1
  }
  printf 'PR #%s : relecteur → @%s (auteur @%s).\n' "$mr" "$who" "$auteur"
}

# gh_project_humans [access-min] -> collaborateurs humains éligibles : « username <TAB> niveau ».
# Les permissions GitHub sont traduites dans l'ÉCHELLE GITLAB (10/20/30/40/50) parce que c'est elle
# que porte GL_REVIEWER_MIN_ACCESS et que compare gl_pick_reviewer : READ=10, TRIAGE=20, WRITE=30
# (le « Developer » de GitLab, seuil par défaut), MAINTAIN=40, ADMIN=50.
#
# Lecture best-effort : `collaborators` exige un droit d'écriture sur le dépôt et répond 403 sinon.
# Une sortie vide fait échouer gl_pick_reviewer proprement, et la revue reste best-effort (#196).
gh_project_humans() {
  local min="${1:-$GL_REVIEWER_MIN_ACCESS}" raw
  raw="$(gh_graphql_read '{ '"$(gh_depot_gql)"' { collaborators(first: 100) { edges { permission node { login __typename } } } } }')" || return 1
  printf '%s\n' "$raw" | awk -v min="$min" -v bots=",$GL_BOT_USERS," '
    {
      n = split($0, parts, /\{"permission":"/)
      for (i = 2; i <= n; i++) {
        node = parts[i]
        perm = node; sub(/".*$/, "", perm)
        lvl = 0
        if      (perm == "READ")     lvl = 10
        else if (perm == "TRIAGE")   lvl = 20
        else if (perm == "WRITE")    lvl = 30
        else if (perm == "MAINTAIN") lvl = 40
        else if (perm == "ADMIN")    lvl = 50
        if (lvl < min) continue
        if (node ~ /"__typename":"Bot"/) continue
        if (!match(node, /"login":"[^"]*"/)) continue
        u = substr(node, RSTART, RLENGTH); sub(/^"login":"/, "", u); sub(/"$/, "", u)
        if (index(bots, "," u ",")) continue
        printf "%s\t%s\n", u, lvl
      }
    }
  ' | sort -u
}

# gh_review_queue -> la file de revue, colonnes identiques à gl_review_queue :
#     mr <TAB> age_j <TAB> etat <TAB> pipeline <TAB> auteur <TAB> relecteur <TAB> branche <TAB> titre
gh_review_queue() {
  local raw lignes
  raw="$(gh_graphql_read '{ '"$(gh_depot_gql)"' { pullRequests(states: OPEN, orderBy: {field: CREATED_AT, direction: ASC}, first: 50) { nodes { number title createdAt isDraft headRefName author { login } reviewRequests(first: 20) { nodes { requestedReviewer { ... on User { login } } } } commits(last: 1) { nodes { commit { statusCheckRollup { state } } } } } } } }')" || return 1
  printf '# mr\tage_j\tetat\tpipeline\tauteur\trelecteur\tbranche\ttitre\n'
  lignes="$(printf '%s\n' "$raw" | awk '
    {
      n = split($0, parts, /\{"number":/)
      for (i = 2; i <= n; i++) {
        node = parts[i]
        match(node, /^[0-9]+/); iid = substr(node, RSTART, RLENGTH)

        titre = "-"
        if (match(node, /,"title":"/)) {
          rest = substr(node, RSTART + RLENGTH)
          if (match(rest, /","createdAt":"/)) titre = substr(rest, 1, RSTART - 1)
        }
        gsub(/\\u0026/, "\\&", titre); gsub(/\\u003e/, ">", titre); gsub(/\\u003c/, "<", titre)
        sub(/^Draft: /, "", titre)

        cree = "-"
        if (match(node, /"createdAt":"[0-9-]+/)) { cree = substr(node, RSTART, RLENGTH); sub(/^"createdAt":"/, "", cree) }

        etat = (node ~ /"isDraft":true/) ? "draft" : "ready"

        branche = "-"
        if (match(node, /"headRefName":"[^"]*"/)) {
          branche = substr(node, RSTART, RLENGTH); sub(/^"headRefName":"/, "", branche); sub(/"$/, "", branche)
        }

        auteur = "-"
        if (match(node, /"author":\{"login":"[^"]*"/)) {
          auteur = substr(node, RSTART, RLENGTH); sub(/^.*"login":"/, "", auteur); sub(/"$/, "", auteur)
        }

        # Relecteurs : uniquement le bloc "reviewRequests" de CE nœud (l auteur est déjà consommé).
        rel = "-"
        if (match(node, /"reviewRequests":\{"nodes":\[[^]]*\]/)) {
          bloc = substr(node, RSTART, RLENGTH); liste = ""
          while (match(bloc, /"login":"[^"]*"/)) {
            u = substr(bloc, RSTART, RLENGTH); sub(/^"login":"/, "", u); sub(/"$/, "", u)
            liste = (liste == "" ? u : liste "," u)
            bloc = substr(bloc, RSTART + RLENGTH)
          }
          if (liste != "") rel = liste
        }

        # Le rollup des checks, traduit dans le vocabulaire de pipeline GitLab (cf. gh_etat_run).
        pipe = "-"
        if (match(node, /"statusCheckRollup":\{"state":"[A-Z_]*"/)) {
          p = substr(node, RSTART, RLENGTH); sub(/^.*"state":"/, "", p); sub(/"$/, "", p)
          if      (p == "SUCCESS")  pipe = "success"
          else if (p == "FAILURE")  pipe = "failed"
          else if (p == "ERROR")    pipe = "failed"
          else if (p == "PENDING")  pipe = "running"
          else if (p == "EXPECTED") pipe = "pending"
          else                      pipe = tolower(p)
        }

        printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n", iid, cree, etat, pipe, auteur, rel, branche, titre
      }
    }
  ')"
  [ -n "$lignes" ] || return 0
  local mr cree etat pipe auteur rel branche titre age
  while IFS=$'\t' read -r mr cree etat pipe auteur rel branche titre; do
    [ -n "$mr" ] || continue
    age="$(gl_elapsed_days "$cree" 2>/dev/null)" || age="-"
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$mr" "$age" "$etat" "$pipe" "$auteur" "$rel" "$branche" "$titre"
  done <<< "$lignes"
}

# --- CI : GitHub Actions ------------------------------------------------------------------------------
# Ces verbes servent /mr-fix, qui raisonne en vocabulaire GitLab (created/pending/running/success/
# failed/canceled/skipped/manual). C'est ce vocabulaire qui sort d'ici — sans quoi gl_pipeline_wait,
# partagé par les deux forges, ne saurait plus dire qu'un run est terminé.
#
# UNE SIMPLIFICATION ASSUMÉE : GitLab a UN pipeline par commit, GitHub a un run PAR FICHIER de
# workflow. On rend le run le plus récent de la branche. Tant que le dépôt porte un workflow de CI
# unique (c'est l'objet du lot 3, #338), les deux notions coïncident ; au-delà, `pipeline-latest`
# désignerait un run parmi d'autres — à revoir avec le lot qui multiplie les workflows, pas avant.

# gh_etat_run <status> <conclusion> -> le statut d'un run, dans le vocabulaire GitLab.
gh_etat_run() {
  case "$1" in
    completed)
      case "$2" in
        success)          printf 'success\n' ;;
        failure|timed_out) printf 'failed\n' ;;
        cancelled)        printf 'canceled\n' ;;
        skipped)          printf 'skipped\n' ;;
        action_required)  printf 'manual\n' ;;
        neutral)          printf 'success\n' ;;
        *)                printf 'failed\n' ;;
      esac ;;
    queued|waiting|pending|requested) printf 'pending\n' ;;
    in_progress)                      printf 'running\n' ;;
    '')                               printf 'created\n' ;;
    *)                                printf '%s\n' "$1" ;;
  esac
}

gh_pipeline_latest() {
  local ref="$1" raw id status conclusion sha url
  if [ -z "$ref" ]; then echo "usage: gh_pipeline_latest <ref>" >&2; return 2; fi
  raw="$(gh api "repos/$GL_GH_REPO/actions/runs?branch=$ref&per_page=1" 2>/dev/null)"
  id="$(printf '%s' "$raw" | grep -o '"id":[0-9]*' | head -1 | sed 's/.*://')"
  if [ -z "$id" ]; then
    echo "Aucun run Actions pour « $ref » dans $GL_GH_REPO" >&2
    return 1
  fi
  status="$(printf '%s' "$raw" | grep -o '"status":"[a-z_]*"' | head -1 | sed 's/.*:"//; s/"//')"
  conclusion="$(printf '%s' "$raw" | grep -o '"conclusion":"[a-z_]*"' | head -1 | sed 's/.*:"//; s/"//')"
  sha="$(printf '%s' "$raw" | grep -o '"head_sha":"[0-9a-f]*"' | head -1 | sed 's/.*:"//; s/"//')"
  url="$(printf '%s' "$raw" | grep -o '"html_url":"[^"]*/actions/runs/[0-9]*"' | head -1 | sed 's/.*:"//; s/"$//')"
  printf '%s\t%s\t%s\t%s\n' "$id" "$(gh_etat_run "$status" "$conclusion")" "$sha" "$url"
}

gh_pipeline_status() {
  local pid="$1" raw status conclusion
  if [ -z "$pid" ]; then echo "usage: gh_pipeline_status <run-id>" >&2; return 2; fi
  raw="$(gh api "repos/$GL_GH_REPO/actions/runs/$pid" 2>/dev/null)"
  status="$(printf '%s' "$raw" | grep -o '"status":"[a-z_]*"' | head -1 | sed 's/.*:"//; s/"//')"
  if [ -z "$status" ]; then echo "Run $pid introuvable dans $GL_GH_REPO" >&2; return 1; fi
  conclusion="$(printf '%s' "$raw" | grep -o '"conclusion":"[a-z_]*"' | head -1 | sed 's/.*:"//; s/"//')"
  gh_etat_run "$status" "$conclusion"
}

# gh_pipeline_failed_jobs <run-id> -> jobs rouges, mêmes colonnes que côté GitLab :
#   id <TAB> name <TAB> stage <TAB> failure_reason
# `stage` vaut « - » : GitHub Actions n'a pas d'étages. En échange, `failure_reason` porte le NOM DE
# L'ÉTAPE qui a échoué — plus actionnable que le `script_failure` de GitLab, et disponible sans
# lecture supplémentaire puisque les étapes voyagent avec le job.
gh_pipeline_failed_jobs() {
  local pid="$1" raw
  if [ -z "$pid" ]; then echo "usage: gh_pipeline_failed_jobs <run-id>" >&2; return 2; fi
  raw="$(gh api "repos/$GL_GH_REPO/actions/runs/$pid/jobs?per_page=50" 2>/dev/null)"
  if [ -z "$raw" ]; then echo "Jobs du run $pid illisibles dans $GL_GH_REPO" >&2; return 1; fi
  local rows
  rows="$(printf '%s' "$raw" | awk '
    {
      # Un job commence par « {"id":<n>,"run_id": » : la clé `run_id` qui suit immédiatement sépare
      # le job de tête des objets imbriqués (steps), qui portent aussi un `id` mais pas celui-là.
      n = split($0, parts, /\{"id":/)
      for (i = 2; i <= n; i++) {
        node = parts[i]
        if (node !~ /^[0-9]+,"run_id":/) continue
        match(node, /^[0-9]+/); id = substr(node, RSTART, RLENGTH)
        # Chaque part se termine au job suivant : on ne lit donc que le corps du job courant.
        concl = ""
        if (match(node, /"conclusion":"[a-z_]*"/)) { concl = substr(node, RSTART, RLENGTH); sub(/.*:"/, "", concl); sub(/"$/, "", concl) }
        if (concl != "failure" && concl != "timed_out") continue
        nom = "-"
        if (match(node, /"name":"[^"]*"/)) { nom = substr(node, RSTART, RLENGTH); sub(/^"name":"/, "", nom); sub(/"$/, "", nom) }
        # Étape fautive : la première du tableau "steps" dont la conclusion est un échec.
        raison = "-"
        if (match(node, /"steps":\[.*/)) {
          steps = substr(node, RSTART, RLENGTH)
          while (match(steps, /\{"name":"[^"]*","status":"[a-z_]*","conclusion":"[a-z_]*"/)) {
            seg = substr(steps, RSTART, RLENGTH)
            steps = substr(steps, RSTART + RLENGTH)
            if (seg ~ /"conclusion":"(failure|timed_out)"/) {
              raison = seg; sub(/^\{"name":"/, "", raison); sub(/",".*$/, "", raison)
              break
            }
          }
        }
        printf "%s\t%s\t%s\t%s\n", id, nom, "-", raison
      }
    }
  ')"
  if [ -z "$rows" ]; then echo "Aucun job en échec dans le run $pid." >&2; return 0; fi
  printf '# id\tname\tstage\tfailure_reason\n'
  printf '%s\n' "$rows"
}

# gh_job_trace <job-id> [lignes] -> queue du journal du job. `gh api` suit la redirection vers le
# fichier de log et rend du texte brut, pas du JSON.
gh_job_trace() {
  local jid="$1" lines="${2:-100}" raw
  if [ -z "$jid" ]; then echo "usage: gh_job_trace <job-id> [lignes]" >&2; return 2; fi
  raw="$(gh api "repos/$GL_GH_REPO/actions/jobs/$jid/logs" 2>/dev/null)"
  if [ -z "$raw" ]; then echo "Journal du job $jid vide ou illisible dans $GL_GH_REPO" >&2; return 1; fi
  printf '%s\n' "$raw" | tail -n "$lines"
}

# ================================================================================================
# DÉRIVES PROPRES AU BACKEND STATUS — lecture d'ENSEMBLE pour doctor.sh (#363, chantier #358)
# ================================================================================================
# Le backend `st_` (#360) répond sur UN ticket ; ce verbe-ci répond sur l'ENSEMBLE, et uniquement
# pour le diagnostic. Il existe parce que la bascule vers le champ Status (#364) n'a pas supprimé la
# panne du cycle de vie, elle l'a DÉPLACÉE : l'exclusion mutuelle des six labels devient impossible
# par construction (un champ single-select n'a qu'une valeur — la dérive « ≥ 2 » de
# `gl_workflow_derives` disparaît avec les labels, #365), mais le Status vit sur l'ITEM DE PROJET et
# non sur l'issue. Le « 0 » qui reste, lui, a DEUX causes — qui appellent deux gestes différents, et
# que rien ne distinguait :
#   • TICKET HORS PROJET — il n'a aucun état, et rien à l'écran ne le sépare d'un ticket filtré ;
#   • ITEM SANS STATUS  — présent dans le projet, colonne vide : un état que personne n'a voulu.
#
# LE CONTRÔLE DU CHAMP LUI-MÊME N'EST PAS ICI, et c'est un retrait délibéré. `pj_resoudre` (#361)
# lit déjà les options du champ Status — même requête GraphQL, même comparaison du titre en ÉGALITÉ,
# mêmes causes d'échec nommées — et les MÉMORISE pour l'appel suivant. Ce lot en avait écrit un
# second exemplaire (`st_options`), les deux ayant été menés en parallèle et chacun ignorant
# l'autre ; le doublon est tombé à la fusion, et §3 de `doctor.sh` s'appuie sur `PJ_OPTIONS`, dont
# l'ordre est celui du champ. Deux lectures de la même donnée, ce sont deux formulations à tenir
# d'accord — et une seule des deux qu'on penserait à corriger.
#
# ⚠ AUCUN ID EN DUR, ici comme ailleurs : le projet se résout par son TITRE (`GL_PROJET_TITRE`) et
# le champ par son NOM, à chaque appel.

# st_erreur_graphql <réponse> -> 0 et le message de l'API si la réponse est un JSON d'erreur au lieu
# du TSV attendu ; 1 sinon (réponse exploitable).
#
# ⚠ CE GARDE-FOU N'EST PAS UNE CEINTURE DE PLUS, il est LE garde-fou — mesuré le 2026-08-18 sur un
# dépôt inexistant. Quand GraphQL rend un bloc `errors`, `gh api` IGNORE le `--jq` et recrache le
# JSON brut sur la sortie standard (le message partant, lui, sur stderr, que `gh_graphql_read`
# jette). La branche « erreur » du programme jq ne s'exécute donc JAMAIS dans le seul cas qu'elle
# était censée couvrir : la réponse arrive non filtrée, l'awk qui suit n'y reconnaît aucune de ses
# clés, et le verbe rend « 0 ticket examiné, aucune dérive » avec un code 0. Soit, dans le fichier
# dont le métier est de détecter les dérives, un ✓ sur une question jamais posée — exactement le
# défaut qu'a corrigé #341, retrouvé par un autre chemin.
#
# Le test porte sur la FORME et non sur le contenu : le TSV rendu par nos programmes jq ne commence
# jamais par « { », une réponse JSON toujours. Les branches « erreur » des deux jq restent en place
# comme seconde ligne — elles couvrent le cas, permis par le schéma, d'un `data` nul SANS bloc
# `errors` — mais c'est celle-ci qui attrape ce qui arrive réellement.
st_erreur_graphql() {
  case "$1" in
    '{'*) printf '%s' "$1" | gl_json_string_field message || printf 'réponse GraphQL inattendue' ;;
    *) return 1 ;;
  esac
}

# st_gql_derives -> la requête des tickets : les issues OUVERTES et, pour chacune, les items de
# projet qui la représentent, avec la valeur courante du champ Status.
#
# POURQUOI PAS `st_carte_statuts` (#362), QUI CARTOGRAPHIE DÉJÀ LE PROJET. Parce que les deux
# questions n'ont pas la même forme. La carte est CENTRÉE PROJET — elle pagine tous les items pour
# rendre l'état de chacun (~5 appels, ~13 s sur 366 items) et ne peut pas, par construction, nommer
# un ticket qui n'y est pas ; or « hors projet » est précisément la moitié qu'on cherche. Cette
# requête-ci est CENTRÉE TICKET, part des issues ouvertes et regarde ce qui les représente : un seul
# appel, et l'absence devient une réponse au lieu d'un silence. C'est aussi la forme qu'a
# `st_cible`, donc celle par laquelle l'ÉCRITURE juge un ticket hors projet — diagnostiquer avec la
# source qui décide plutôt qu'avec une autre. Enfin la mémoire de la carte ne franchit pas les
# sous-shells (cf. son en-tête) : la capturer ici la re-remplirait sans rien réutiliser.
#
# ⚠ LA BORNE EST ASSUMÉE, ET ELLE SE DIT. `first: 100` est la borne de tout ce fichier (`gh_backlog`,
# `gh_issues_sans_milestone`…) et non un choix propre à ce verbe ; mais ici l'appelant est le fichier
# dont le métier est de détecter les dérives, et une borne franchie en silence y produirait
# exactement le défaut qu'a corrigé #341 — un ✓ sur une question posée à moitié. D'où `totalCount`
# dans la MÊME requête et la ligne d'en-tête « #examines » ci-dessous : la borne voyage avec le
# résultat, à charge pour `doctor.sh` de la nommer quand elle est atteinte.
st_gql_derives() {
  printf '{ %s { issues(states: OPEN, first: 100) { totalCount nodes { number projectItems(first: 20) { nodes { project { title } fieldValueByName(name:"Status") { ... on ProjectV2ItemFieldSingleSelectValue { name } } } } } } } }' \
    "$(gh_depot_gql)"
}

# st_jq_derives -> l'aplatissement en lignes CLÉ<TAB>… :
#     erreur  depot
#     total   <nombre de tickets ouverts dans le dépôt>
#     ticket  <numéro>
#     item    <numéro>  <titre du projet>  <libellé du Status, vide si non posé>
st_jq_derives() {
  cat <<'JQ'
[
  (if .data.repository == null then "erreur\tdepot"
   else "total\t" + (.data.repository.issues.totalCount|tostring) end),
  (.data.repository.issues.nodes[]? | "ticket\t" + (.number|tostring)),
  (.data.repository.issues.nodes[]? as $i | $i.projectItems.nodes[]?
   | "item\t" + ($i.number|tostring) + "\t" + .project.title + "\t" + (.fieldValueByName.name // ""))
] | .[]
JQ
}

# st_derives -> les deux dérives propres au Status, une par ligne, dans l'ordre où la forge rend les
# tickets :
#     #examines <TAB> <tickets examinés> <TAB> <tickets ouverts du dépôt>   ← toujours, en tête
#     <iid> <TAB> hors-projet     le ticket n'est item d'AUCUN projet de ce titre : il n'a aucun état
#     <iid> <TAB> sans-etat       il est bien dans le projet, mais son Status est vide
#
# La ligne d'en-tête est préfixée « # » comme celle de `backlog-table` : les consommateurs machine
# l'ignorent par le même filtre (`$1 !~ /^#/`), et celui qui veut la borne la lit.
#
# LA SECONDE COLONNE EST UNE CAUSE, et non le nombre de `gl_workflow_derives`. Le nombre n'aurait
# plus rien à compter (un champ single-select vaut 0 ou 1) et les deux causes appellent deux gestes
# différents — ajouter le ticket au projet, ou lui poser un état. Les confondre sous un « 0 » commun
# rendrait le diagnostic vrai et inutilisable.
st_derives() {
  local reponse message
  reponse="$(gh_graphql_read "$(st_gql_derives)" --jq "$(st_jq_derives)")" || return 1
  if message="$(st_erreur_graphql "$reponse")"; then
    echo "Dépôt $GL_GH_REPO illisible (inconnu ou droits insuffisants) : $message" >&2
    return 1
  fi
  case "$reponse" in
    "erreur	depot"*) echo "Dépôt $GL_GH_REPO illisible (inconnu ou droits insuffisants)" >&2; return 1 ;;
  esac

  # Le titre du projet voyage par ENVIRON et jamais par `awk -v`, qui INTERPRÈTE les échappements de
  # son argument (#340) ; la comparaison est une ÉGALITÉ DE CHAMP et non un `grep`, pour la même
  # raison qu'ailleurs — un titre est une donnée, pas un motif.
  printf '%s\n' "$reponse" | ST_TITRE="$GL_PROJET_TITRE" awk -F'\t' '
    $1 == "total"  { total = $2; next }
    $1 == "ticket" { ordre[++n] = $2; next }
    $1 == "item" && $3 == ENVIRON["ST_TITRE"] {
      dans[$2] = 1
      if ($4 != "") etat[$2] = $4
      next
    }
    END {
      printf "#examines\t%d\t%s\n", n, total
      for (i = 1; i <= n; i++) {
        iid = ordre[i]
        if (!(iid in dans))       printf "%s\thors-projet\n", iid
        else if (etat[iid] == "") printf "%s\tsans-etat\n", iid
      }
    }
  '
}

# ==================================================================================================
# PEUPLEMENT DU PROJET — tout ticket est un item du projet (ticket #361, chantier #358)
# ==================================================================================================
# LA PANNE PROPRE AU DISPOSITIF, TRAITÉE AVANT QU'ELLE EXISTE. Le Status vit sur l'ITEM DE PROJET et
# non sur l'issue : un ticket qui n'est pas dans le projet n'a AUCUN état, et aucune requête de cycle
# de vie ne le voit — en plus silencieux qu'un ticket sans état, puisque rien à l'écran ne le
# distingue d'un ticket absent du filtre. UN SEUL VERBE s'en occupe, `gl_project_add`, appelé par
# /ticket-create dans la foulée de la création, et c'est aussi la réparation à l'unité quand
# doctor.sh (#363) signale un ticket hors projet.
#
# ⚠ PEUPLER LE PROJET N'EST PAS DÉCIDER DU CYCLE DE VIE : c'est poser la DONNÉE DE PLUS sans
# laquelle il n'y aurait rien où l'écrire. C'est ce qui a permis à #361 de précéder la bascule de
# #364 — sans quoi celle-ci aurait trouvé un projet vide et autant de tickets sans état —, et c'est
# pourquoi ce bloc n'a jamais été derrière le commutateur du temps où il en existait un.
#
# ⚠ LE BACKFILL EST PARTI AVEC LES LABELS (#365), ET CE N'EST PAS UN OUBLI. `gl_project_backfill`
# dérivait le Status du label `workflow::*` courant et de RIEN D'AUTRE : c'était la bonne source
# tant que le label faisait foi, ce fut une photo périmée après la bascule, et il n'en reste aucune
# après leur retrait. Son dernier usage légitime — `--realigner`, pour préparer un RETOUR sur les
# labels — a disparu avec le retour lui-même. Ce qui restait de lui sans les labels (« ce ticket
# est-il un item ? ») est une question de DÉTECTION, que #363 a donnée à doctor.sh ; la réparation
# est `gl_project_add`, ticket par ticket. Ne pas le réécrire « en masse » : un verbe qui poserait
# un état par défaut sur des tickets anciens inventerait la donnée qu'on vient de perdre.
#
# AUCUN ID EN DUR, JAMAIS : l'ID du projet, celui du champ et ceux de ses six options se dérivent
# PAR NOM en une lecture (`pj_resoudre`). Le projet se désigne par son TITRE
# (`MAESTRO_PROJECT_TITRE`, la même clé que scripts/github/bootstrap-project.sh), les options par
# leur LIBELLÉ — ceux que rend `gl_workflow_label`, si bien que le vocabulaire du cycle de vie n'a
# pas changé en changeant de support.
#
# LE PRÉFIXE `pj_` DÉSIGNE LES INTERNES de ce bloc : `grep -n '^pj_'` en donne l'inventaire exact.
# ⚠ Ne pas les confondre avec `gl_project_humans`, qui est du vocabulaire GitLab hérité — là-bas
# « project » nomme le DÉPÔT, ici il nomme le projet Projects v2.
#
# CE BLOC EST EN FIN DE BACKEND et non dans la section « Cycle de vie », parce qu'il n'en écrit
# aucun : il rend un ticket CAPABLE d'en porter un.

# --- Résolution du projet, par nom ----------------------------------------------------------------
# Trois variables de PROCESSUS, remplies une fois par `pj_resoudre`. ⚠ Le cache ne remonte pas d'une
# substitution de commande — d'où l'appel explicite à `pj_resoudre` en tête du verbe public, avant
# tout `$( … )`.
PJ_PROJET_ID=""
PJ_CHAMP_ID=""
PJ_OPTIONS=""   # « <id option><TAB><libellé> », une par ligne

# pj_resoudre -> remplit les trois ci-dessus en UNE lecture. Idempotent : ne relit rien si c'est déjà
# fait. Chacune des causes d'échec a son message, parce qu'elles appellent des gestes différents —
# et que « le projet est introuvable » n'en désigne aucun.
pj_resoudre() {
  local lignes ligne
  [ -n "$PJ_CHAMP_ID" ] && return 0

  # Les projets ne sont pas filtrés côté GitHub : `projectsV2(query:)` est une recherche FLOUE, alors
  # que le titre du projet est une CLÉ — il se compare en ÉGALITÉ, dans le shell, et « Maestro » ne
  # doit pas ramener « Maestro v2 ». Les valeurs voyagent par ENVIRON et jamais par `awk -v`, qui
  # INTERPRÈTE les échappements de son argument : un titre porteur d'un antislash y changerait de
  # valeur en silence.
  lignes="$(gh api graphql -f query='{ repositoryOwner(login:"'"${GL_GH_REPO%%/*}"'") { ... on ProjectV2Owner { projectsV2(first:100){nodes{ id title field(name:"Status"){ ... on ProjectV2SingleSelectField { id options{id name} } } }} } } }' \
            --jq '[ (.data.repositoryOwner.projectsV2.nodes[]? | "projet\t" + .title + "\t" + .id + "\t" + (.field.id // "")), (.data.repositoryOwner.projectsV2.nodes[]? as $p | $p.field.options[]? | "option\t" + $p.title + "\t" + .id + "\t" + .name) ] | .[]' 2>&1)" || {
    printf '%s\n' "$lignes" >&2
    echo "Projets de ${GL_GH_REPO%%/*} illisibles — le jeton porte-t-il le scope « project » ? (gh auth status)" >&2
    return 1
  }

  ligne="$(printf '%s\n' "$lignes" | PJ_TITRE="$GL_PROJET_TITRE" awk -F'\t' '$1 == "projet" && $2 == ENVIRON["PJ_TITRE"] { print; exit }')"
  if [ -z "$ligne" ]; then
    echo "Projet « $GL_PROJET_TITRE » introuvable chez ${GL_GH_REPO%%/*} — le monter : bash scripts/github/bootstrap-project.sh" >&2
    return 1
  fi
  PJ_PROJET_ID="$(printf '%s' "$ligne" | cut -f3)"
  PJ_CHAMP_ID="$(printf '%s' "$ligne" | cut -f4)"
  if [ -z "$PJ_CHAMP_ID" ]; then
    PJ_PROJET_ID=""
    echo "Le projet « $GL_PROJET_TITRE » n'a pas de champ « Status » — bash scripts/github/bootstrap-project.sh --check" >&2
    return 1
  fi

  PJ_OPTIONS="$(printf '%s\n' "$lignes" | PJ_TITRE="$GL_PROJET_TITRE" awk -F'\t' '$1 == "option" && $2 == ENVIRON["PJ_TITRE"] { print $3 "\t" $4 }')"
  if [ -z "$PJ_OPTIONS" ]; then
    PJ_PROJET_ID=""; PJ_CHAMP_ID=""
    echo "Le champ « Status » du projet « $GL_PROJET_TITRE » ne porte aucune option — bash scripts/github/bootstrap-project.sh --check" >&2
    return 1
  fi
}

# pj_option_id <libellé> -> l'identifiant de l'option du champ Status portant ce libellé.
pj_option_id() {
  local libelle="$1" id
  pj_resoudre || return 1
  id="$(printf '%s\n' "$PJ_OPTIONS" | PJ_LIB="$libelle" awk -F'\t' '$2 == ENVIRON["PJ_LIB"] { print $1; exit }')"
  if [ -z "$id" ]; then
    echo "Le champ « Status » du projet « $GL_PROJET_TITRE » n'a pas d'option « $libelle » — bash scripts/github/bootstrap-project.sh --check" >&2
    return 1
  fi
  printf '%s\n' "$id"
}

# --- Écritures unitaires --------------------------------------------------------------------------
# Aucune des deux ne passe par `gh_graphql_read` : son retry sur réponse vide RÉ-APPLIQUERAIT la
# mutation (règle posée avec lui, et valable partout dans ce fichier).

# pj_ajouter_item <node id du ticket> -> l'id de l'item, créé ou DÉJÀ LÀ.
#
# `addProjectV2ItemById` est idempotent côté GitHub : un contenu déjà dans le projet rend l'item
# existant au lieu d'échouer. C'est ce qui dispense de vérifier avant d'ajouter, et la raison pour
# laquelle `gl_project_add` est rejouable sans doublon et n'a besoin d'aucune lecture d'ensemble.
pj_ajouter_item() {
  local content="$1" out id
  if [ -z "$content" ]; then echo "usage: pj_ajouter_item <node-id>" >&2; return 2; fi
  pj_resoudre || return 1
  out="$(gh api graphql -f query="mutation { addProjectV2ItemById(input: {projectId: \"$PJ_PROJET_ID\", contentId: \"$content\"}) { item { id } } }" 2>&1)"
  id="$(printf '%s' "$out" | grep -o '"id":"[^"]*"' | head -1 | sed 's/.*:"//; s/"$//')"
  if [ -z "$id" ]; then
    printf '%s\n' "$out" >&2
    return 1
  fi
  printf '%s\n' "$id"
}

# pj_poser_status <id item> <id option> -> pose la valeur du champ Status sur l'item. Muet si ça
# passe. Idempotent par nature : reposer la valeur déjà présente ne change rien.
pj_poser_status() {
  local item="$1" option="$2" out
  if [ -z "$item" ] || [ -z "$option" ]; then echo "usage: pj_poser_status <item> <option>" >&2; return 2; fi
  pj_resoudre || return 1
  out="$(gh api graphql -f query="mutation { updateProjectV2ItemFieldValue(input: {projectId: \"$PJ_PROJET_ID\", itemId: \"$item\", fieldId: \"$PJ_CHAMP_ID\", value: {singleSelectOptionId: \"$option\"}}) { projectV2Item { id } } }" 2>&1)"
  case "$out" in
    *'"projectV2Item"'*) return 0 ;;
    *) printf '%s\n' "$out" >&2; return 1 ;;
  esac
}

# --- Le verbe ---------------------------------------------------------------------------------------

# gl_project_add <iid> [valeur] -> fait du ticket un ITEM du projet et pose son Status. Défaut
# « À faire », c'est-à-dire l'état d'un ticket qui vient de naître.
#
# Rien côté forge ne pose d'état par défaut, et un ticket créé sans état est une dérive : /ticket-create
# l'appelle DANS LA FOULÉE de la création — pas plus tard, pas « quand on y pensera ».
#
# Ce verbe ÉCRASE un Status déjà posé, et c'est ce qui en fait aussi la RÉPARATION d'un ticket que
# doctor.sh signale hors projet ou sans état : l'appelant NOMME la valeur qu'il veut, personne ne la
# devine. Idempotent : rejoué à l'identique il ne change rien, l'ajout comme la pose l'étant chacun.
gl_project_add() {
  local iid="$1" valeur="${2:-a-faire}" slug libelle option node item
  if [ -z "$iid" ]; then echo "usage: gl_project_add <iid> [valeur]" >&2; return 2; fi
  slug="$(gl_workflow_slug "$valeur")" || return 1
  libelle="$(gl_workflow_label "$slug")"

  pj_resoudre || return 1
  option="$(pj_option_id "$libelle")" || return 1
  node="$(gh_workitem_gid "$iid")" || return 1
  item="$(pj_ajouter_item "$node")" || {
    echo "Ajout de #$iid au projet « $GL_PROJET_TITRE » en échec." >&2
    return 1
  }
  pj_poser_status "$item" "$option" || {
    echo "#$iid est bien un item du projet « $GL_PROJET_TITRE », mais son Status n'a pas pu être posé." >&2
    echo "  Rejouer la commande : l'ajout ne sera pas dupliqué." >&2
    return 1
  }
  printf '#%s → item du projet « %s », Status « %s »\n' "$iid" "$GL_PROJET_TITRE" "$libelle"
}


# --- Dispatcher (uniquement quand exécuté directement, pas quand sourcé) -------------------------
if [ "${BASH_SOURCE[0]:-$0}" = "$0" ]; then
  cmd="${1:-}"; [ "$#" -gt 0 ] && shift
  case "$cmd" in
    require)        gl_require ;;
    forge-nom)      gl_forge_nom ;;
    depot-courant)  gl_depot_courant ;;
    forge-cli)      gl_forge_cli ;;
    current-user)   gl_current_user ;;
    issue-raw)      gl_issue_raw "$@" ;;
    mr-brief)       gl_mr_brief "$@" ;;
    workitem-gid)   gl_workitem_gid "$@" ;;
    set-workflow)   gl_set_workflow "$@" ;;
    reconcile-workflow) gl_reconcile_workflow "$@" ;;
    workflow-slug)  gl_workflow_slug "$@" ;;
    workflow-label) gl_workflow_label "$@" ;;
    backlog)        gl_backlog "$@" ;;
    backlog-table)  gl_backlog_table "$@" ;;
    labels)         gl_labels ;;
    workflow-derives)      gl_workflow_derives "$@" ;;
    status-derives)        st_derives ;;
    issues-sans-milestone) gl_issues_sans_milestone ;;
    open-mr-branches)      gl_open_mr_branches ;;
    merge-settings) gl_merge_settings ;;
    issue-brief)    gl_issue_brief "$@" ;;
    issue-owner)    gl_issue_owner "$@" ;;
    statuts)        st_statuts "$@" ;;
    issue-taken)    gl_issue_taken "$@" ;;
    current-milestone) gl_current_milestone "$@" ;;
    milestones)        gl_milestones ;;
    milestone-issues)  gl_milestone_issues "$@" ;;
    milestone-rail)    gl_milestone_rail "$@" ;;
    issue-link)     gl_issue_link "$@" ;;
    subticket-add)   gl_subticket_add "$@" ;;
    subticket-order) gl_subticket_order "$@" ;;
    parent-of)      gl_parent_of "$@" ;;
    subtickets)     gl_subtickets "$@" ;;
    startables)     gl_subtickets "$@" | tail -n +2 | gl_subtickets_startables ;;
    lots-ouverts)   gl_lots_ouverts "$@" ;;
    arbitrage)      gl_arbitrage "$@" ;;
    arbitre)        gl_arbitre "$@" ;;
    touche-claude)  gl_touche_claude "$@" ;;
    ferme-parent)   gl_ferme_parent "$@" ;;
    demarre-parent) gl_demarre_parent "$@" ;;
    start-brief)    gl_start_brief "$@" ;;
    begin)          gl_begin "$@" ;;
    prio)           gl_prio "$@" ;;
    prio-delay)     gl_prio_delay "$@" ;;
    get-start-date) gl_get_start_date "$@" ;;
    get-time-spent) gl_get_time_spent "$@" ;;
    elapsed-days)   gl_elapsed_days "$@" ;;
    set-dates)      gl_set_dates "$@" ;;
    start-dates)    gl_start_dates "$@" ;;
    log-time)       gl_log_time "$@" ;;
    mr-state)       gl_mr_state "$@" ;;
    project-humans) gl_project_humans "$@" ;;
    pick-reviewer)  gl_pick_reviewer "$@" ;;
    mr-iid)         gl_mr_iid "$@" ;;
    mr-reviewers)   gl_mr_reviewers "$@" ;;
    set-reviewer)   gl_set_reviewer "$@" ;;
    review-queue)   gl_review_queue "$@" ;;
    cleanup-merged) gl_cleanup_merged "$@" ;;
    worktree-done)  gl_worktree_done "$@" ;;
    worktree-done-lot) gl_worktree_done_lot "$@" ;;
    mr-briefs)      gl_mr_briefs "$@" ;;
    reconcile-en-cours) gl_reconcile_en_cours "$@" ;;
    reprendre-en-cours) gl_reprendre_en_cours "$@" ;;
    reprises)       gl_reprises "$@" ;;
    branch-for)     gl_branch_for "$@" ;;
    start-branch)   gl_start_branch "$@" ;;
    sync-main)      gl_sync_main "$@" ;;
    behind-main)    gl_behind_main "$@" ;;
    mr-conflict)    gl_mr_conflict "$@" ;;
    merge-mr)       gl_merge_mr "$@" ;;
    merge-order)    gl_merge_order "$@" ;;
    branche-du-ticket) gl_branche_du_ticket "$@" ;;
    branch-iid)     gl_branch_iid "$@" ;;
    close-guard)    gl_close_guard "$@" ;;
    get-description)    gl_get_description "$@" ;;
    set-description)    gl_set_description "$@" ;;
    get-mr-description) gl_get_mr_description "$@" ;;
    set-mr-description) gl_set_mr_description "$@" ;;
    roundtrip-description) gl_roundtrip_description "$@" ;;
    issue-title)    gl_issue_title "$@" ;;
    create-mr)      gl_create_mr "$@" ;;
    issue-note)     gl_issue_note "$@" ;;
    issue-url)      gl_issue_url "$@" ;;
    reste-claude)   gl_reste_claude "$@" ;;
    reste-claude-de) gl_reste_claude_de "$@" ;;
    project-add)      gl_project_add "$@" ;;
    pipeline-latest)      gl_pipeline_latest "$@" ;;
    pipeline-status)      gl_pipeline_status "$@" ;;
    pipeline-failed-jobs) gl_pipeline_failed_jobs "$@" ;;
    job-trace)            gl_job_trace "$@" ;;
    pipeline-wait)        gl_pipeline_wait "$@" ;;
    host)           gl_host ;;
    slug)           gl_slug "$@" ;;
    branch-prefix)  gl_branch_prefix "$@" ;;
    *)
      echo "usage: bash scripts/gitlab/lib.sh <sous-commande> [args]" >&2
      echo "  Forge : GitHub, dépôt \$MAESTRO_GITHUB_REPO (défaut $GL_GH_REPO)" >&2
      echo "    forge-nom | forge-cli          (« GitHub » et « gh », pour les MESSAGES des appelants)" >&2
      echo "    depot-courant                  (le dépôt visé, « propriétaire/nom »)" >&2
      echo "  require | current-user | workitem-gid <iid>" >&2
      echo "  issue-raw <iid>                  (vue TEXTE canonique du ticket — la primitive dont six verbes descendent)" >&2
      echo "  mr-brief <branche>               (etat/numéro/sha de la PR de la branche)" >&2
      echo "  Cycle de vie — le champ Status de Projects v2, projet \$MAESTRO_PROJECT_TITRE" >&2
      echo "  (défaut « $GL_PROJET_TITRE ») ; cf. contrat en tête de lib.sh :" >&2
      echo "    set-workflow <iid> <valeur>   (pose la valeur ; refuse si le ticket n'est pas un item du projet)" >&2
      echo "                                  valeur = « À faire »… ou le slug « a-faire »… ; sortie toujours en libellé" >&2
      echo "    workflow-slug <valeur>        (normalise en slug)   workflow-label <slug> (rend le libellé)" >&2
      echo "    reconcile-workflow [--check] [<iid>…]" >&2
      echo "                                  (pose « Terminé » sur les tickets soldés restés actifs ;" >&2
      echo "                                   sans iid : balaie tout le backlog fermé. N'écrase jamais" >&2
      echo "                                   « Abandonné »/« Doublon ». Best-effort, jamais bloquant)" >&2
      echo "  backlog [opened|closed|all]        (JSON brut du backlog)" >&2
      echo "  backlog-table [opened|closed|all]  (table plate compacte TSV — voir en-tête gl_backlog_table)" >&2
      echo "  labels                             (tous les labels du dépôt, un nom par ligne)" >&2
      echo "  workflow-derives [opened|closed|all]  (tickets sans état — hors projet ou Status vide ; iid/nombre)" >&2
      echo "  status-derives                     (tickets ouverts hors projet ou sans Status — iid/cause," >&2
      echo "                                      précédés de « #examines <examinés> <ouverts> »)" >&2
      echo "  issues-sans-milestone              (iid des tickets ouverts sans jalon)" >&2
      echo "  issue-brief <iid>                  (titre + labels + critères d'acceptation)" >&2
      echo "  issue-owner <iid>                  (cycle de vie + assignés du ticket, TSV — vide = libre)" >&2
      echo "  statuts <iid…>                     (cycle de vie de N tickets NOMMÉS en UNE lecture, TSV iid/libellé ;" >&2
      echo "                                      « - » = hors projet ou Status vide, aucune ligne = ticket inexistant." >&2
      echo "                                      Pendant unitaire de backlog-table, sans sa fenêtre de 100 — docs/10 §3.6)" >&2
      echo "  issue-taken <iid> [username]       (0 + assignés si le ticket est « En cours » chez quelqu'un d'autre)" >&2
      echo "  touche-claude <iid>                (ce ticket nomme-t-il « .claude/ », où une session autonome ne peut" >&2
      echo "                                      pas écrire ? verdict + lignes. 0=touche, 3=non — docs/10 §11.7)" >&2
      echo "  current-milestone [produit|outillage] (titre du milestone courant du rail — le plus ancien actif portant" >&2
      echo "                                      encore un ticket ouvert ; soldé et vide sont sautés, chacun nommé sur stderr. Défaut produit)" >&2
      echo "  milestones                         (tous les milestones : titre/état/dates/avancement, TSV)" >&2
      echo "  milestone-issues <titre-exact>     (tickets d'un milestone : iid/statut/type/agent/prio/titre, TSV)" >&2
      echo "  milestone-rail <titre> <rail>      (pose le rail produit|outillage d'un milestone — idempotent)" >&2
      echo "  slug <titre> | branch-prefix <type> | host   (hôte de la forge, déduit du remote)" >&2
      echo "  Sous-tickets (découpage parent/lots, docs/10 §5.1) :" >&2
      echo "    issue-link <iid-parent> <iid-lot>  (rattache un lot à son parent — alias de subticket-add)" >&2
      echo "    subticket-add <iid-parent> <iid-lot>   (rattache un lot — sub-issue native, idempotent ;" >&2
      echo "                                      refuse un lot déjà rattaché à un AUTRE parent)" >&2
      echo "    subticket-order <iid-parent> <iid>…    (impose cet ordre aux lots nommés, en un seul aller ;" >&2
      echo "                                      ne pose rien si l'un des iid n'est pas un lot de ce parent)" >&2
      echo "    parent-of <iid>                 (iid du parent si <iid> est un sous-ticket)" >&2
      echo "    subtickets <iid-parent>         (checklist ## Sous-tickets : iid/coche/statut/par/titre)" >&2
      echo "    startables <iid-parent>         (lots « À faire » démarrables maintenant)" >&2
      echo "    lots-ouverts <iid-parent>       (lots encore OUVERTS — 0 = parent soldé, 3 = il en reste)" >&2
      echo "    ferme-parent [--check] <iid>    (<iid> = un lot qui vient de se fermer : ferme son parent s'il était le dernier)" >&2
      echo "    demarre-parent [--check] <iid>  (<iid> = un lot qui vient de démarrer : passe son parent « En cours » s'il était « À faire »)" >&2
      echo "  Démarrage de ticket (/ticket-start) :" >&2
      echo "    start-brief <iid>            (préflight en une lecture : pré-requis, arbre sale signalé, brief, parent/sous-ticket, branche proposée)" >&2
      echo "    branch-for <iid>             (nom de la branche de travail du ticket)" >&2
      echo "    start-branch <branche>       (place le dépôt sur la branche — clone principal ou worktree lié, idempotent)" >&2
      echo "    begin <iid> [username]       (assignation + « En cours » + dates en une mutation groupée)" >&2
      echo "  Dates & temps :" >&2
      echo "    start-dates <iid>            (début=aujourd'hui + échéance selon prio)" >&2
      echo "    set-dates <iid> [début] [échéance]   get-start-date <iid>" >&2
      echo "    prio <iid>   prio-delay <prio>   elapsed-days <date>" >&2
      echo "    log-time <iid> <durée> [résumé]   get-time-spent <iid> [--hors-import]" >&2
      echo "  Descriptions (aller-retour fidèle aux octets — à utiliser au lieu d'improviser une lecture) :" >&2
      echo "    get-description <iid>              (description du ticket, UTF-8 intact, sur stdout)" >&2
      echo "    set-description <iid> <fichier>    (remplace la description du ticket par le fichier)" >&2
      echo "    get-mr-description <mr>            (idem pour une PR)" >&2
      echo "    set-mr-description <mr> <fichier>  (idem pour une PR)" >&2
      echo "    roundtrip-description <iid>        (valide la fidélité : lit/réécrit/relit et compare les octets)" >&2
      echo "  Création depuis un FICHIER (jamais de description multi-ligne ni de \$(cat …) sur la ligne de commande) :" >&2
      echo "    create-mr <iid> <fichier> [branche]  (PR en Draft vers main, titre du ticket, description du fichier ;" >&2
      echo "                                         idempotent : met à jour la PR ouverte existante au lieu d'échouer)" >&2
      echo "    issue-note <iid> <fichier>          (poste le fichier en commentaire sur le ticket)" >&2
      echo "    issue-title <iid>                   (titre du ticket, UTF-8 intact)" >&2
      echo "    issue-url <iid>                     (URL web du ticket)" >&2
      echo "  Reste à appliquer sous .claude/ — le résidu devient un TICKET (#610, chantier #608) :" >&2
      echo "    reste-claude <iid-source> <fichier> (crée le ticket de reprise portant le correctif du" >&2
      echo "                                         fichier : ASSIGNÉ — donc hors des plans de queue.sh," >&2
      echo "                                         qui filtre sur « À faire ET libre » —, état « À faire »" >&2
      echo "                                         posé, source nommée, jalon du rail outillage." >&2
      echo "                                         S'AJOUTE au rendu dans la PR, jamais à sa place (#188)." >&2
      echo "                                         Rejoué : complète le même ticket au lieu d'en ouvrir" >&2
      echo "                                         un second ; un correctif déjà présent ne réécrit rien." >&2
      echo "                                         0=créé/complété/déjà à jour, 2=usage, 3=iid source" >&2
      echo "                                         inconnu, 4=fichier absent ou vide, 1=échec de forge." >&2
      echo "                                         3 et 4 tombent AVANT toute écriture)" >&2
      echo "    reste-claude-de <iid-source>       (LIT : imprime l'iid du ticket de reprise, ou rien." >&2
      echo "                                         0=il en a un, 3=aucun, 1=illisible. C'est ce que le" >&2
      echo "                                         pilote demande en fin de run (#611) — même ancre que" >&2
      echo "                                         l'écriture, jamais un second motif)" >&2
      echo "  Peuplement du projet Projects v2 (le Status vit sur l'ITEM, pas sur l'issue — #361) :" >&2
      echo "    project-add <iid> [valeur]  (fait du ticket un item du projet \$MAESTRO_PROJECT_TITRE —" >&2
      echo "                                 défaut « $GL_PROJET_TITRE » — et pose son Status. Défaut « À faire » ;" >&2
      echo "                                 appelé par /ticket-create dans la foulée de la création, et seule" >&2
      echo "                                 réparation d'un ticket que doctor.sh signale hors projet ou sans état." >&2
      echo "                                 Rejouable sans doublon ; ÉCRASE un Status déjà posé)" >&2
      echo "  Branches :" >&2
      echo "    cleanup-merged [--auto] [--sans-fetch] [<branche>…]  (supprime les branches locales dont la PR est mergée ; sans argument, toutes ; --auto = muet si rien ; --sans-fetch quand l'appelant vient de fetcher)" >&2
      echo "    sync-main [--check]         (avance main du clone principal sur origin/main, fast-forward seul ; 0=à jour/fait, 3=divergent, 4=arbre sale)" >&2
      echo "    mr-state <branche>          (opened|closed|merged)" >&2
      echo "    mr-briefs <branche>…        (etat+numéro+sha de N PR en UNE lecture ; aucune ligne pour une branche sans PR)" >&2
      echo "    open-mr-branches            (branche source de chaque PR ouverte, une par ligne)" >&2
      echo "    worktree-done <iid> [branche] (fini|actif|inconnu + sha de merge + raison — fin de vie d'un worktree)" >&2
      echo "    worktree-done-lot <iid>:<branche>…  (le même verdict pour N worktrees, en DEUX lectures au plus)" >&2
      echo "    behind-main [branche]       (retard sur origin/main + conflit probable ; 0=à jour, 3=en retard, 4=+conflit)" >&2
      echo "  Tickets « En cours » orphelins (lecture seule — signale, ne répare rien) :" >&2
      echo "    reconcile-en-cours [--check] [--auto] [--tsv] [--sauf <iid>]" >&2
      echo "                                (« quelqu'un s'occupe-t-il encore de ce ticket ? » : vivant / orphelin /" >&2
      echo "                                 hors de portée, avec sa source — carte du pilote, ou déduction annoncée." >&2
      echo "                                 Portée : les worktrees de CETTE machine. 0=rien à signaler, 3=orphelin(s))" >&2
      echo "    reprendre-en-cours [--check] [--force] <iid>…" >&2
      echo "                                (LE GESTE : remet l'orphelin « À faire » ET le libère, en une mutation." >&2
      echo "                                 N'écrit que dans GitLab — worktree, branche et commits intacts." >&2
      echo "                                 Refuse un ticket vivant, hors de portée, ou déjà repris $GL_REPRISES_MAX fois" >&2
      echo "                                 (--force lève les deux derniers). 0=repris, 3=refusé, 1=échec)" >&2
      echo "    reprises [<iid>]            (la trace : d'où venait chaque ticket repris, et combien de fois)" >&2
      echo "    mr-conflict [branche]       (conflit RÉEL avec origin/main via merge-tree ; 0=propre, 3=conflit)" >&2
      echo "  Merge (SEUL chemin de merge du dépôt — « gh pr merge » reste refusé, cf. docs/10 §6) :" >&2
      echo "    merge-mr [<iid>|<branche>] [--check]" >&2
      echo "                                (merge en $GL_MERGE_METHOD, et SEULEMENT si : PR ouverte non brouillon" >&2
      echo "                                 qui ferme le ticket, rien de non poussé, aucun conflit, pipeline VERT" >&2
      echo "                                 SUR LA TÊTE DE LA PR. --check s'arrête au verdict, sans écriture." >&2
      echo "                                 0=mergé/mergeable, 3=pipeline pas rendu (repasser), 4=pipeline rouge," >&2
      echo "                                 5=conflit, 6=geste humain requis, 1=outil manquant)" >&2
      echo "    branche-du-ticket <iid>     (branche source de la PR ouverte du ticket)" >&2
      echo "    merge-order [<branche>…]    (l'ordre le MOINS conflictuel — degré croissant dans le graphe des" >&2
      echo "                                 conflits deux à deux, par merge-tree, sans sortir aucune branche." >&2
      echo "                                 Défaut : les PR ouvertes. TSV rang/branche/pr/degre/voisines." >&2
      echo "                                 Heuristique, pas un optimum — lecture seule)" >&2
      echo "  Garde-fou de clôture (session ↔ ticket, avant toute écriture de /ticket-finish|ship) :" >&2
      echo "    branch-iid [branche]        (iid porté par le nom de la branche ; rien si hors convention)" >&2
      echo "    close-guard <iid> [branche] (0=cohérent, 3=autre ticket, 4=ticket d'un tiers, 5=branche sans iid, 1=ticket illisible)" >&2
      echo "  Revue best-effort (file de revue ; relecteur posé à la main seulement) :" >&2
      echo "    review-queue                     (PR ouvertes en attente de revue, la plus ancienne d'abord — TSV)" >&2
      echo "    set-reviewer [mr|branche] [user] (pose un relecteur humain ≠ auteur — appel MANUEL, aucune commande ne l'invoque)" >&2
      echo "    mr-reviewers [mr|branche]        (relecteurs posés, CSV — vide si aucun)" >&2
      echo "    pick-reviewer [auteur] [graine]  (choisit un relecteur humain, rotation par graine)" >&2
      echo "    project-humans [access-min]      (membres humains éligibles : username/niveau, TSV)" >&2
      echo "    mr-iid [mr|branche]              (iid de la PR ouverte — défaut : branche courante)" >&2
      echo "  Réglages du dépôt (doctor.sh, bootstrap.sh) :" >&2
      echo "    merge-settings              (garde-fous de merge normalisés : pipeline_requis, merge_si_pipeline_saute, suppression_branche)" >&2
      echo "  Pipelines CI :" >&2
      echo "    pipeline-latest <ref>            (id/status/sha/url du dernier pipeline de la branche)" >&2
      echo "    pipeline-status <pipeline-id>    (statut courant)" >&2
      echo "    pipeline-failed-jobs <pipeline-id>  (jobs rouges : id/name/stage/failure_reason)" >&2
      echo "    job-trace <job-id> [lignes]      (queue de la trace du job)" >&2
      echo "    pipeline-wait <ref|run-id> [--timeout <s>]" >&2
      echo "                                (ATTEND un verdict, de façon bornée — ne relance ni ne corrige rien." >&2
      echo "                                 Défauts : plafond ${GL_PIPELINE_TIMEOUT}s (un run qui TOURNE, compté depuis qu'il est vu)," >&2
      echo "                                 sondage ${GL_PIPELINE_SONDAGE}s, naissance ${GL_PIPELINE_NAISSANCE}s sans PR ouverte / ${GL_PIPELINE_NAISSANCE_PR}s avec (#595)." >&2
      echo "                                 0=vert, 3=terminal non vert, 4=plafond atteint," >&2
      echo "                                 5=aucun pipeline et aucun n'est dû, 6=pas encore né mais dû)" >&2
      exit 2 ;;
  esac
fi

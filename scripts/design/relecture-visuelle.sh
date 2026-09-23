#!/usr/bin/env bash
# LA RELECTURE VISUELLE — ce qu'il faut regarder, et de quoi le regarder (#932, lot 2 de #930).
#
#   bash scripts/design/relecture-visuelle.sh --plan <iid>   # ce qu'il y a à regarder. Ne démarre rien.
#   bash scripts/design/relecture-visuelle.sh <iid>          # le plan, + les stacks réelles : après et avant
#   bash scripts/design/relecture-visuelle.sh <iid> --etat vide       # un autre état de la vraie stack (#1165)
#   bash scripts/design/relecture-visuelle.sh <iid> --etat injoignable  # la vraie panne, sur la stack montée
#   bash scripts/design/relecture-visuelle.sh --couverture <iid>      # écran par écran, les états capturés
#   bash scripts/design/relecture-visuelle.sh --saisine <iid>         # ce que le regard neuf reçoit (#980)
#   bash scripts/design/relecture-visuelle.sh --planche <iid>         # la planche HTML avant/après (#980)
#   bash scripts/design/relecture-visuelle.sh --fin          # arrête les stacks et retire ce qu'elles ont posé
#   bash scripts/design/relecture-visuelle.sh <iid> --regime applique  # le régime imposé (#1243, section 8)
#
# Le maillon qui manquait à la chaîne de docs/30 §5.1 : PERSONNE NE REGARDE LE RENDU. La décision et
# ses raisons sont en docs/30 §5.6 ; ce qui l'APPELLE — l'étape 4bis de `/ticket-finish` — en §5.5. `verify` répond
# du câblage, `banc-mise-en-page` de la géométrie, `contraste.test.ts` / `a11y.test.tsx` /
# `sobriete.test.tsx` des règles — aucun ne dit « à quoi ça ressemble ». Une session peut écrire une
# interface, voir tous ses tests verts, et n'avoir jamais ouvert l'écran qu'elle vient de changer.
#
# CE SCRIPT NE REGARDE PAS. Il prépare ce qui est mécanique — quels écrans, sur quels ports, où déposer
# les captures — et s'arrête là : le jugement demande des yeux, et c'est la session qui les a (skill
# `relecture-visuelle`). Le partage est celui de #562, #612 et #714 : ce qui est automatique est la
# DÉSIGNATION de ce qui manque, jamais le verdict.
#
# --- 1. QUELS ÉCRANS : trois questions, une seule règle de classement ------------------------------
#
# `scripts/presentation/ecrans-touches.sh` (#544) sait déjà dire à quel écran appartient un fichier, et
# c'est SA règle qui répond ici — recopiée, elle finirait par ne plus nommer le même écran pour le même
# fichier. Ce script lui pose les trois questions dans l'ordre :
#
#   a. LES COMMITS ET L'ARBRE (`--ref HEAD --travail-en-cours`). La relecture a lieu AVANT la clôture :
#      le ticket n'a souvent aucun commit, jamais de merge, et `origin/main` ne sait rien de lui. On lit
#      donc la branche ET ce que l'arbre a de plus qu'elle.
#   b. LES ÉCRANS QUI AFFICHENT UN COMPOSANT PARTAGÉ. #544 range `apps/web/components/**` sous une ligne
#      « indéterminée », et il a raison : un composant n'EST aucune route. Mais on peut remonter à celles
#      qui le MONTRENT, en suivant les imports — question différente, donc pas un doublon de #544. Sans
#      cette remonte, le geste serait muet sur une bonne part des tickets d'interface : `components/` est
#      l'endroit le plus édité de `apps/web`.
#   c. CE QUI RESTE INDÉTERMINÉ EST NOMMÉ, jamais deviné (règle de #544) : la coquille de tous les écrans
#      (`app/layout.tsx`, `globals.css`) n'appartient à aucun, et un composant que personne n'importe
#      encore ne s'affiche nulle part. La session le relaie dans son jugement, à « ce que je n'ai pas pu
#      voir » — c'est une réponse, pas un trou.
#
# ⚠ LA REMONTE PAR LES IMPORTS EST UNE APPROXIMATION, et il faut que ça se voie. Elle apparie sur le NOM
# DE MODULE (`from "…/Composeur"`), donc deux composants homonymes dans deux dossiers se confondent, et
# un import construit à l'exécution lui échappe. Les deux erreurs ne coûtent pas la même chose : un écran
# de trop se regarde en vingt secondes, un écran manquant ne se regarde jamais. On ratisse donc large, et
# le plan dit toujours PAR QUEL FICHIER un écran est arrivé.
#
# --- 2. SUR QUELS PORTS : le fichier du worktree, jamais l'environnement ---------------------------
#
# Une session relocalisée par `/ticket-start` garde les ports du CLONE PRINCIPAL dans son bloc `env`
# (docs/10 §9.1 — `EnterWorktree` ne réévalue que les caches liés au CWD). L'environnement ment donc
# précisément là où ce script sert, et viser 8000/3000 arrêterait la stack d'à côté. La source de vérité
# est `.claude/settings.local.json` DU DÉPÔT COURANT, que `worktree.sh` écrit au montage ; l'environnement
# n'est qu'un repli, et 8000/3000 le repli du repli (clone principal, poste sans worktree).
#
# --- 3. CE QUE LE SCRIPT ÉCRIT ---------------------------------------------------------------------
#
#   - `.maestro/relecture/<iid>/` — les captures et le jugement. Sous la racine du dépôt et en chemin
#     RELATIF : un chemin absolu hors du répertoire de travail demande une approbation qu'une session
#     autonome n'a personne pour donner (#234, docs/10 §11.7). Le `filename` du MCP se donne relatif
#     lui aussi, et c'est mesuré : sa racine autorisée EST le worktree de la session, mais son contrôle
#     compare les chemins LITTÉRALEMENT — un `e:/…` (la forme qu'on obtient en convertissant la racine
#     MSYS) est refusé « outside allowed roots » face à un `E:/…` pourtant identique. Le relatif rend la
#     question sans objet plutôt que de la traiter une fois de plus.
#   - Rien dans les données de la copie : la stack se sert sur le JEU DE DONNÉES DU BANC
#     (`<espace>.banc`, `.maestro/banc/`, #1164), que `start.sh` rouvre ou remet à neuf à chaque état.
#     Ce qu'un worktree porte en propre — ses fils, ses projets, ses runs — n'est jamais touché.
#   - Le projet neuf de l'état « vide » (section 5) : déclaré PAR L'API, qui crée son dossier sous
#     `${MAESTRO_RELECTURE_ATELIER:-~/maestro-relecture}/<iid>/projet-neuf` — hors d'`AppData` et
#     du dépôt, que la validation des racines refuse (EF-38). Son témoin,
#     `.maestro/relecture/.projet-neuf`, dit à `--fin` quel dossier retirer.
#   - `.maestro/relecture/.etat` — l'état que les stacks servent (section 5), pour qu'« injoignable »,
#     qui coupe une stack au lieu d'en monter une, sache qu'il y en a une.
#   - `.maestro/relecture/.avant` — le témoin de l'avant (section 6) : où il est monté et sur quels
#     ports, pour que `--fin`, qui ne prend pas d'iid, sache quoi arrêter et quoi retirer.
#   - `.maestro/relecture/<iid>/saisine.md`, `paires.tsv` et `planche.html` — le regard neuf et sa
#     planche (section 7) ; la planche est recopiée au même chemin dans le CLONE PRINCIPAL, seule
#     écriture de ce script hors du dépôt courant.
#   - `.maestro/relecture/<iid>/.regime` et `.etats` — le régime de la relecture et les états montés
#     (section 8), pour que la couverture, la saisine et la planche disent ce que la préparation a fait.
#
# Il ne commite rien, ne merge rien, et n'ÉCRIT dans aucune forge : il monte des processus locaux sur
# les ports d'un worktree, et les arrête. Sa seule lecture de forge est `lib.sh relecture-attente` —
# jamais un `gh` écrit ici —, UNE fois par appel au plus : la saisine en tire l'attente (section 7), le
# régime les décisions consignées (section 8), et un ticket sans écran ne la paie pas.
#
# --- 4. LE PRIX EST DU TEMPS DE MUR, ET IL S'ANNONCE (règle de #418) -------------------------------
#
# Monter la stack coûte, et un run à concurrence 3 en monterait trois — sur des ports distincts, ce que
# `worktree.sh` garantit depuis #152. C'est pourquoi `--plan` existe SÉPARÉMENT et ne démarre rien : un
# ticket sans surface visible rend `3` en une seconde, et personne ne paie la stack pour apprendre qu'il
# n'y avait rien à regarder. L'avant ajoute sa part — ~50 s de plus la première fois, mesurés au
# cadrage de #977 —, et la préparation dit ce qu'elle a réellement coûté, chronomètre en main.
#
# --- 5. LES ÉTATS : ceux que la vraie stack produit, et ceux qu'elle ne produit pas (#1165) ----------
#
# L'état peuplé est rarement celui qui casse : c'est une file vide, une API en panne, une liste longue.
# De #978 à #1165, la démo les SIMULAIT — cinq scénarios factices, qui montraient ce qu'on avait
# scénarisé et pas ce que le produit fait (docs/41 §4). Chaque état vient désormais de la VRAIE stack,
# et un état qu'elle ne sait pas produire est NOMMÉ NON COUVERT, jamais fabriqué :
#
#   - `peuple` (le défaut) — l'état réel que le dernier passage du banc des scénarios a laissé (#1148),
#     rouvert par l'API réelle sans rien rejouer (`start.sh --etat-banc`, #1164). Son âge est dit au
#     démarrage : un passage se rejoue à la demande (`--etat-banc --rejouer`, vrai modèle, des dizaines
#     de minutes), JAMAIS d'office ici. C'est aussi la « charge » : celle que le passage a laissée.
#   - `vide` — une stack neuve (`start.sh --etat-neuf`), puis un projet neuf déclaré par l'API : ce
#     que voit quelqu'un qui vient de déclarer son premier projet. Sans projet, le shell resterait sur
#     sa porte (#279), et la porte d'une stack neuve n'est pas l'état vide des écrans.
#   - `injoignable` — la vraie panne (#996) : l'API COUPÉE (`start.sh --couper-api`) sous la stack
#     déjà montée, l'UI encore servie. Ce n'est pas un montage mais une coupure : les écrans ouverts
#     avant elle montrent leur panne, et c'est l'ordre d'un vrai crash. Relancer un état la rétablit.
#
# Ne se produisent pas, et se disent comme tels (`NON_COUVERTS`, dans le plan, la couverture et la
# saisine) : une API qui RÉPOND EN ERREUR (mesuré le 2026-09-22 : son magasin coupé, au démarrage
# comme en route, la vraie API dit « ok » et sert des listes vides — aucune lecture d'écran ne rend
# 500), et une charge AU-DELÀ de ce que le passage a laissé — rien n'est gonflé.
#
# CE QUI A ÉTÉ VU SE COMPTE SUR LE DISQUE, pas dans une déclaration. `--couverture` croise les écrans
# du plan, les états et les deux thèmes avec les captures déposées — celles de l'état peuplé à la
# racine du dossier du ticket (le chemin d'avant #978, inchangé), celles d'un autre état dans un
# sous-dossier à son nom. Le script ne sait voir qu'une CAPTURE, jamais un regard : une capture qu'on
# n'a pas relue ne vaut rien, et c'est au skill de le tenir. Il ne décide pas non plus quels états il
# FALLAIT couvrir — c'est la rubrique « États à couvrir » du ticket (#976), un texte, que seule la
# session sait juger (#746) : ce mode constate, il ne rend aucun verdict.
#
# Les noms des états sont ceux de CE script, et de lui seul : `start.sh` ne connaît que ses options
# (`--etat-banc`, `--etat-neuf`, `--couper-api`), le skill décrit chaque nom et un test le garde.
# Chaque état monté redémarre la stack, puisque ce sont des données que l'API rejoue à son démarrage.
#
# --- 6. L'AVANT : `origin/main`, servi à côté et jamais à la place (#977) --------------------------
#
# Une capture seule ne dit ni ce qui a changé, ni si le changement a abîmé ce qui allait. Chaque écran
# du plan se regarde donc DEUX fois : sur la branche (l'après) et sur `origin/main` (l'avant), dans le
# même thème et le même état. L'avant est servi par une SECONDE stack, montée depuis un SECOND
# worktree, détaché (`worktree.sh avant`, dont l'en-tête dit pourquoi cette voie et pas les deux
# autres) — le travail de la session n'est donc jamais mis en jeu pour l'obtenir : dans l'arbre du
# ticket, rien n'est écrit hors de `.maestro/`, et aucun fichier suivi n'est touché — pas même le temps
# d'une capture.
#
#   - SES PORTS sont ceux de l'après, décalés de 200. Les worktrees de ticket occupent 8001-8100 et
#     3001-3100 (`worktree.sh`, #152), le clone principal 8000/3000 : 8200-8300 et 3200-3300 ne
#     croisent personne. Dérivés de l'après et non de l'iid, pour qu'un `--ports` imposé au montage
#     du worktree emporte l'avant avec lui.
#   - SA CAPTURE EST À CÔTÉ DE L'APRÈS : même dossier d'état (§5), même clé, suffixe `-avant`. Le nom de
#     l'après ne bouge pas — `--couverture` le compte, et il ne compte que les regards sur la branche.
#   - UN ÉCRAN NOUVEAU n'a pas d'avant, et il est NOMMÉ comme tel — jamais capturé sur une 404. La
#     question « cet écran existe-t-il sur origin/main ? » se pose à la règle de #544 : les pages
#     d'origin/main, classées par `ecrans-touches.sh --chemins`. Seules comptent les pages SANS
#     segment dynamique, parce que `/runs/[runId]/page.tsx` se range sous `/runs` sans que `/runs`
#     réponde pour autant.
#   - UN ÉTAT (§5) qu'origin/main ne sait pas servir n'a pas d'avant non plus : l'avant sert le même
#     état que l'après ou ne sert rien — comparer une stack neuve à un état peuplé ferait voir une
#     différence que le ticket n'a pas faite. C'est le LANCEUR d'origin/main qui répond, sans rien
#     démarrer (`--diagnostic-navigateur` refuse une option qu'il ne connaît pas) : aucune option
#     n'est cherchée dans son source. Le même état veut dire les mêmes données : l'état du banc se
#     rouvre du même passage des deux côtés ; le projet neuf, déclaré par chaque API, n'a pas le même
#     identifiant de part et d'autre, et le plan dit les deux.
#   - L'AVANT EST BEST-EFFORT : `origin/main` introuvable, montage ou stack en échec — l'après reste
#     prêt, l'avant est dit indisponible avec sa cause, et c'est la session qui le reporte à « ce que je
#     n'ai pas pu voir ». Un avant manquant ne vaut jamais une relecture manquante.
#   - `MAESTRO_RELECTURE_AVANT=0` l'éteint, et le plan le dit : ne pas payer l'avant est un choix, pas
#     un oubli.
#
# --- 7. LE REGARD NEUF ET SA PLANCHE (#980) ---------------------------------------------------------
#
# « Est-ce que ça a l'air juste ? » n'a pas de réponse sans référence, et son juge était celui qui avait
# écrit l'écran : l'auteur voit ce qu'il a VOULU faire. Le jugement est donc rendu par un sous-agent
# (`.claude/agents/regard-neuf.md`, outil `Read` seul), et ce script prépare ce qu'il reçoit :
#
#   - `--saisine <iid>` écrit `saisine.md` : les PAIRES capturées (chemins absolus — l'outil `Read`
#     n'en prend pas d'autres), le RENDU ATTENDU et les DÉCISIONS déjà prises à l'écran (lus par
#     `lib.sh relecture-attente`, par ancre), la GRILLE et le gabarit à rendre. Rien d'autre : ni le
#     code, ni le diff, ni un mot de la session. C'est ce qui rend « ne reçoit que » VÉRIFIABLE — le
#     prompt du sous-agent n'est que le chemin de ce fichier, et le fichier reste sur le disque.
#     La forge muette ne bloque pas : la saisine le dit, et les rubriques deviennent « non vu ».
#     `--partis-pris <fichier>` y ajoute une veille antérieure à l'ancre, que la session recopie.
#   - LA GRILLE N'EST PAS ÉCRITE ICI : elle est lue dans `scripts/design/grille-relecture.tsv`, que
#     `lib.sh relecture-note` lit aussi pour refuser un jugement incomplet.
#   - `--planche <iid>` écrit `planche.html` : autonome, avant et après côte à côte, le jugement en
#     tête. Recopiée dans le `.maestro/relecture/<iid>/` du CLONE PRINCIPAL, qui survit au ramassage
#     du worktree après le merge ; sa dernière ligne, `PLANCHE <chemin absolu>`, est cette copie. Le gabarit est `scripts/design/planche.py`, qui reprend la mécanique de
#     `scripts/presentation/build.py` (images en `data:`, plafond de taille, bascule de thème,
#     visionneuse) au lieu d'en écrire une seconde. Rien n'est envoyé à la forge : `gh` ne sait pas
#     joindre une image à un commentaire, et le jugement consigné reste du texte.
#
# LES DEUX LISENT LES MÊMES PAIRES, et un seul endroit les dresse (`paires_de`) : le nom d'une capture
# se construit ici et nulle part ailleurs (`nom_capture`), faute de quoi la planche et la saisine
# finiraient par ne plus montrer les mêmes fichiers.
#
# --- 8. LE RÉGIME : complet pour un ticket qui DÉCIDE, proportionné pour un ticket qui APPLIQUE (#1243)
#
# Tout ce qui précède — l'avant, les trois états, le regard neuf — a été mesuré sur 13 tickets du 20 au
# 23 septembre : 30 min en médiane entre le premier appel du skill et la note, 6,3 h en tout, pour
# moins de 0,1 h de corrections trouvées. Un ticket qui APPLIQUE une décision déjà prise (critère du
# §7.2 de `/design-veille`) se relit donc en régime proportionné : l'APRÈS SEUL — aucune seconde stack,
# ni `worktree.sh avant`, ni `npm ci` —, les ÉTATS QU'IL NOMME (le défaut s'il n'en nomme aucun), les
# deux thèmes, et le jugement rendu par la session (#1151). Un ticket qui DÉCIDE d'un écran garde le
# régime complet : avant et après, les trois états, le regard neuf.
#
# « Décide ou applique » est un JUGEMENT, que ce script ne rend pas (#746) : il en lit l'ACTE. Un
# ticket qui décide a consigné sa décision avant d'écrire une ligne — la veille (`## Veille de
# conception`), puis le choix (`## Variante retenue`) —, et ce sont les ancres que `lib.sh
# relecture-attente` tire déjà pour le regard neuf : une décision consignée → `decide`, aucune →
# `applique`. Un ticket ILLISIBLE garde le régime complet, par prudence : un avant de trop se paie
# une minute, un regard manqué ne se rattrape pas. La session peut imposer l'autre (`--regime`), et le
# plan dit toujours d'où vient le régime.
#
# Ce que la préparation a fait se CONSIGNE (`<iid>/.regime`, `<iid>/.etats`) : la couverture, la
# saisine et la planche le relisent au lieu de le redemander, et une préparation suivante le garde —
# `--etat vide` sans `--regime` reste dans le régime imposé au premier montage. Quels états le ticket
# nomme est un TEXTE (rubrique « États à couvrir », #976), que la session juge : ce script ne le lit
# pas, il compte ceux qu'elle a MONTÉS ou capturés, et l'état par défaut quand elle n'en a monté aucun.
#
# Codes de retour : 0 = il y a à regarder · 3 = aucune surface visible (abstention nominale, pas une
# panne) · 4 = `--saisine`/`--planche` sans aucune capture sur le disque · 1 = échec · 2 = usage.

set -uo pipefail

RACINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SOUS_DOSSIER=".maestro/relecture"

MODE="preparer"
TSV=0
IID=""
ETAT=""
PARTIS_PRIS=""
REGIME_OPTION=""

usage() {
  cat <<'USAGE'
La relecture visuelle : ce qu'il faut regarder, et de quoi le regarder — sur la vraie stack.

  bash scripts/design/relecture-visuelle.sh --plan <iid>          Ce qu'il y a à regarder. Ne démarre rien.
  bash scripts/design/relecture-visuelle.sh <iid>                 Le plan, puis les stacks réelles : après et avant.
  bash scripts/design/relecture-visuelle.sh <iid> --etat <nom>    Les mêmes, dans un autre état de la vraie stack.
  bash scripts/design/relecture-visuelle.sh --couverture <iid>    Écran par écran, les états capturés.
  bash scripts/design/relecture-visuelle.sh --saisine <iid>       Ce que le regard neuf reçoit : paires, attente, grille.
  bash scripts/design/relecture-visuelle.sh --planche <iid>       La planche HTML autonome, avant et après côte à côte.
  bash scripts/design/relecture-visuelle.sh --fin                 Arrête les stacks et retire ce qu'elles ont posé.

Options :
  --plan            N'écrit rien, ne démarre rien : dit seulement s'il y a matière, et laquelle.
  --etat <nom>      peuple (défaut) : l'état du dernier passage du banc · vide : une stack neuve et un
                    projet neuf · injoignable : l'API coupée sous la stack montée, l'UI servie.
  --regime <nom>    decide : avant et après, les trois états, le regard neuf · applique : l'après
                    seul, les états montés (le défaut sinon), jugé par la session. Sans lui, le
                    régime consigné par la préparation, sinon celui du ticket : une décision
                    consignée à l'écran (« ## Veille de conception », « ## Variante retenue ») le
                    fait décider, aucune le fait appliquer, un ticket illisible garde « decide ».
  --couverture      N'écrit rien, ne démarre rien : croise écrans, états et thèmes avec les captures.
  --saisine         Écrit .maestro/relecture/<iid>/saisine.md (lecture seule de la forge) ; sa
                    dernière ligne est « SAISINE <chemin absolu> », à donner au sous-agent regard-neuf.
  --partis-pris <fichier>  Avec --saisine : une veille consignée sans l'ancre « ## Veille de
                    conception », recopiée telle quelle du ticket.
  --planche         Écrit .maestro/relecture/<iid>/planche.html (images en data:, jugement en tête).
                    MAESTRO_RELECTURE_PLANCHE_MAX (Mio, 0 = aucun) plafonne sa taille.
  --tsv             Le plan (ou la couverture) en TSV, pour un appelant machine. La dernière colonne
                    du plan, `avant`, vaut l'URL sur origin/main, `nouveau` pour un écran absent
                    d'origin/main, ou `-` quand l'avant n'est pas évalué.
  -h, --help        Cette aide.

L'avant (origin/main) se sert sur les ports de l'après + 200. MAESTRO_RELECTURE_AVANT=0 l'éteint.
Le projet neuf de l'état « vide » naît sous MAESTRO_RELECTURE_ATELIER (défaut ~/maestro-relecture).

Codes de retour : 0 = il y a à regarder · 3 = aucune surface visible · 4 = aucune capture (saisine,
planche) · 1 = échec · 2 = usage.
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --plan) MODE="plan" ;;
    --fin) MODE="fin" ;;
    --couverture) MODE="couverture" ;;
    --saisine) MODE="saisine" ;;
    --planche) MODE="planche" ;;
    --partis-pris)
      if [ $# -lt 2 ] || [ -z "$2" ]; then
        printf 'relecture-visuelle.sh : --partis-pris attend un fichier.\n\n' >&2
        usage >&2; exit 2
      fi
      PARTIS_PRIS="$2"; shift ;;
    --tsv) TSV=1 ;;
    --etat)
      if [ $# -lt 2 ] || [ -z "$2" ]; then
        printf 'relecture-visuelle.sh : --etat attend un nom d'\''état.\n\n' >&2
        usage >&2; exit 2
      fi
      ETAT="$2"; shift ;;
    --regime)
      if [ $# -lt 2 ] || [ -z "$2" ]; then
        printf 'relecture-visuelle.sh : --regime attend decide ou applique.\n\n' >&2
        usage >&2; exit 2
      fi
      case "$2" in
        decide | applique) REGIME_OPTION="$2" ;;
        *)
          printf 'relecture-visuelle.sh : régime inconnu « %s » (decide ou applique).\n' "$2" >&2
          exit 2 ;;
      esac
      shift ;;
    # Le geste de la démo (#978), retiré par #1165 : le dire vaut mieux qu'un « option inconnue ».
    --scenario)
      printf 'relecture-visuelle.sh : --scenario montait un état de la démo, retirée (#1165) — les états\n' >&2
      printf 'viennent de la vraie stack : --etat <nom>.\n\n' >&2
      usage >&2; exit 2 ;;
    -h | --help) usage; exit 0 ;;
    -*) printf 'Option inconnue : %s\n\n' "$1" >&2; usage >&2; exit 2 ;;
    *)
      brut="${1#\#}"
      case "$brut" in
        '' | *[!0-9]*)
          printf 'relecture-visuelle.sh : « %s » n'\''est pas un iid de ticket.\n\n' "$1" >&2
          usage >&2; exit 2 ;;
      esac
      IID="$brut" ;;
  esac
  shift
done

dire() { [ "$TSV" = 1 ] || printf '%s\n' "$*"; }


# --- Les ports du dépôt courant ---------------------------------------------------------------------
# Lecture d'un JSON à deux niveaux en sed : le fichier est écrit par `worktree.sh` (json.dump indenté),
# donc une clé par ligne. `jq` serait plus juste et n'est pas un prérequis du dépôt ; Python le serait
# aussi et coûterait un démarrage d'interpréteur pour deux entiers.
port_de() {
  local cle="$1" defaut="$2" valeur="" fichier="$RACINE/.claude/settings.local.json"
  if [ -f "$fichier" ]; then
    valeur="$(sed -n 's/.*"'"$cle"'"[[:space:]]*:[[:space:]]*"\{0,1\}\([0-9]\{2,5\}\)"\{0,1\}.*/\1/p' \
      "$fichier" | head -n 1)"
  fi
  # L'environnement N'EST QU'UN REPLI (voir l'en-tête) : dans une session relocalisée il porte les ports
  # du clone principal, c'est-à-dire ceux de la stack de quelqu'un d'autre.
  if [ -z "$valeur" ]; then valeur="${!cle:-}"; fi
  if [ -z "$valeur" ]; then valeur="$defaut"; fi
  printf '%s' "$valeur"
}

PORT_API="$(port_de MAESTRO_PORT_API 8000)"
PORT_UI="$(port_de MAESTRO_PORT_UI 3000)"

# --- L'avant (section 6 de l'en-tête) -----------------------------------------------------------------
DECALAGE_AVANT=200
REF_AVANT="origin/main"
WORKTREE_SH="$RACINE/scripts/git/worktree.sh"
TEMOIN_AVANT="$RACINE/$SOUS_DOSSIER/.avant"
PORT_API_AVANT=""
PORT_UI_AVANT=""
case "$PORT_API$PORT_UI" in
  '' | *[!0-9]*) ;;   # un port venu de l'environnement qui n'est pas un nombre : pas d'avant à dériver
  *) PORT_API_AVANT=$((PORT_API + DECALAGE_AVANT)); PORT_UI_AVANT=$((PORT_UI + DECALAGE_AVANT)) ;;
esac

# --- Les états de la vraie stack (section 5 de l'en-tête) --------------------------------------------
# Dans l'ordre de l'annonce ; le premier est le défaut. Ces noms sont ceux de ce script et de lui seul :
# `start.sh` ne connaît que ses options, et le skill décrit chacun (un test le garde).
ETATS="peuple vide injoignable"
ETAT_DEFAUT="peuple"
TEMOIN_ETAT="$RACINE/$SOUS_DOSSIER/.etat"

# Ce que montre chaque état, en une ligne : le plan, la couverture et la saisine disent la même.
description_etat() {
  case "$1" in
    peuple) printf "l'état du dernier passage du banc, rouvert par l'API réelle — son âge est dit au montage" ;;
    vide) printf "une stack neuve, puis un projet neuf déclaré par l'API — rien n'y a été lancé" ;;
    injoignable) printf "l'API coupée sous la stack montée, l'UI encore servie — la vraie panne (#996)" ;;
  esac
}

# Les options de `start.sh` qui MONTENT un état. `injoignable` n'en a pas : il coupe, il ne monte rien.
options_start() {
  case "$1" in
    peuple) printf '%s' "--etat-banc" ;;
    vide) printf '%s' "--etat-neuf" ;;
  esac
}

# Les états que la vraie stack NE PRODUIT PAS, avec leur raison — `nom <TAB> raison`. Ils sont nommés
# partout où les autres se comptent (plan, couverture, saisine), et jamais imités (en-tête, §5).
NON_COUVERTS="$(printf '%s\t%s\n%s\t%s' \
  erreur "une API qui répond en erreur : depuis #1206 la vraie stack la produit — son magasin (Redis) coupé, elle refuse ses lectures en 503 nommé —, mais ce script ne la monte pas encore ; à la main, REDIS_URL de la stack pointé sur un relais TCP qu'on coupe" \
  charge "au-delà de ce que le dernier passage du banc a laissé — des listes de centaines de lignes, des noms de 80 caractères : rien n'est gonflé")"

# Le dossier des captures d'un état, RELATIF à la racine. L'état par défaut garde le chemin d'avant
# #978 — le dossier du ticket lui-même —, un autre état a le sien dessous : deux états d'un même écran
# ne s'écrasent pas, et une relecture qui n'ouvre que le défaut n'a rien à apprendre de neuf.
dossier_captures() {
  local iid="$1" etat="${2:-}"
  if [ -z "$etat" ] || [ "$etat" = "$ETAT_DEFAUT" ]; then
    printf '%s/%s' "$SOUS_DOSSIER" "$iid"
  else
    printf '%s/%s/%s' "$SOUS_DOSSIER" "$iid" "$etat"
  fi
}

# Le nom d'une capture : la clé d'écran de #544 (celle de `captures.mjs`) et le thème. Un seul endroit
# le construit, pour que le skill, le plan et la couverture parlent du même fichier.
nom_capture() { printf '%s-%s.png' "$1" "$2"; }
# Son avant (#977), à côté : même clé, même thème, suffixe `-avant`.
nom_capture_avant() { printf '%s-%s-avant.png' "$1" "$2"; }

# --- Le régime (section 8 de l'en-tête) -------------------------------------------------------------
REGIMES="decide applique"
REGIME=""          # decide · applique — vide tant qu'aucun écran n'a été trouvé
REGIME_SOURCE=""   # option · preparation · ticket
REGIME_RAISON=""   # pourquoi ce régime, en une ligne — ce que le témoin garde

# La lecture de l'attente du ticket (`lib.sh relecture-attente`), UNE fois par appel (#602) : le
# régime en tire les décisions consignées, la saisine l'attente entière. Résultat en variables
# globales — appelée en `$(…)`, la fonction tournerait dans un sous-shell et relirait la forge.
ATTENTE_BRUTE=""
CODE_ATTENTE=""
lire_attente() {
  [ -n "$CODE_ATTENTE" ] && return 0
  CODE_ATTENTE=0
  ATTENTE_BRUTE="$(bash "$LIB_SH" relecture-attente "$IID" 2>/dev/null)" || CODE_ATTENTE=$?
  return 0
}

# Les décisions consignées à l'écran : le second bloc de l'attente, après `@@decisions@@`.
decisions_de_l_attente() { printf '%s\n' "$ATTENTE_BRUTE" | awk 'p { print } /^@@decisions@@$/ { p = 1 }'; }

temoin_regime() { printf '%s/%s/%s/.regime' "$RACINE" "$SOUS_DOSSIER" "$IID"; }
temoin_etats() { printf '%s/%s/%s/.etats' "$RACINE" "$SOUS_DOSSIER" "$IID"; }

# resoudre_regime : l'option, sinon ce que la préparation a consigné, sinon l'acte du ticket.
resoudre_regime() {
  local regime raison temoin
  temoin="$(temoin_regime)"
  if [ -n "$REGIME_OPTION" ]; then
    REGIME="$REGIME_OPTION"; REGIME_SOURCE="option"; REGIME_RAISON="imposé par --regime"
    return 0
  fi
  if [ -s "$temoin" ]; then
    IFS=$'\t' read -r regime raison <"$temoin"
    case " $REGIMES " in
      *" $regime "*)
        REGIME="$regime"; REGIME_SOURCE="preparation"; REGIME_RAISON="${raison:-?}"
        return 0 ;;
    esac
  fi
  REGIME_SOURCE="ticket"
  lire_attente
  if [ "$CODE_ATTENTE" -ne 0 ]; then
    REGIME="decide"
    REGIME_RAISON="ticket illisible (relecture-attente : code $CODE_ATTENTE) — le régime complet, par prudence"
  elif [ -n "$(decisions_de_l_attente | tr -d '[:space:]')" ]; then
    REGIME="decide"
    REGIME_RAISON="le ticket porte une décision consignée à l'écran (« ## Veille de conception », « ## Variante retenue »)"
  else
    REGIME="applique"
    REGIME_RAISON="aucune décision consignée à l'écran sur le ticket"
  fi
}

# Ce que le régime regarde, en une ligne : le plan, la préparation, la couverture et la saisine
# disent la même.
description_regime() {
  case "$REGIME" in
    applique) printf "le ticket applique une décision déjà prise — l'après seul, les états qu'il nomme (le défaut sinon), les deux thèmes, jugé par la session" ;;
    *) printf "le ticket décide d'un écran — avant et après, les trois états, les deux thèmes, jugé par le regard neuf" ;;
  esac
}

# D'où vient le régime : c'est ce qui permet de le contester.
origine_regime() {
  case "$REGIME_SOURCE" in
    preparation) printf '%s (consigné par la préparation)' "$REGIME_RAISON" ;;
    *) printf '%s' "$REGIME_RAISON" ;;
  esac
}

# consigne_regime : ce que la préparation vient de monter — le régime et l'état —, relu ensuite par
# la couverture, la saisine et la planche (en-tête, §8). La raison de BASE est gardée, jamais celle
# qu'on vient de relire, pour qu'un montage suivant ne l'enveloppe pas une fois de plus.
consigne_regime() {
  local dossier="$RACINE/$SOUS_DOSSIER/$IID"
  mkdir -p "$dossier" 2>/dev/null
  printf '%s\t%s\n' "$REGIME" "$REGIME_RAISON" >"$(temoin_regime)"
  grep -qxF -- "$ETAT" "$(temoin_etats)" 2>/dev/null || printf '%s\n' "$ETAT" >>"$(temoin_etats)"
}

# etat_capture <iid> <etat> : au moins une capture sur le disque dans le dossier de cet état.
etat_capture() {
  local capture
  for capture in "$RACINE/$(dossier_captures "$1" "$2")"/*.png; do
    [ -s "$capture" ] && return 0
  done
  return 1
}

# etats_demandes <iid> : les états que cette relecture regarde, dans l'ordre de l'annonce. Tous pour
# un ticket qui décide ; pour un ticket qui applique, ceux que la session a MONTÉS ou capturés — c'est
# son jugement sur la rubrique « États à couvrir », rendu par un acte —, et le défaut si elle n'en a
# monté aucun. Rien de capturé n'est jamais caché : un état capturé est un état demandé.
etats_demandes() {
  local iid="$1" etat demandes=""
  if [ "$REGIME" != "applique" ]; then printf '%s' "$ETATS"; return 0; fi
  for etat in $ETATS; do
    if grep -qxF -- "$etat" "$(temoin_etats)" 2>/dev/null || etat_capture "$iid" "$etat"; then
      demandes="${demandes}${demandes:+ }${etat}"
    fi
  done
  printf '%s' "${demandes:-$ETAT_DEFAUT}"
}

# Les états que le régime ne demande pas — nommés, pour qu'on sache qu'ils n'ont pas été oubliés.
etats_non_demandes() {
  local demandes etat reste=""
  demandes=" $(etats_demandes "$1") "
  for etat in $ETATS; do
    case "$demandes" in *" $etat "*) ;; *) reste="${reste}${reste:+ }${etat}" ;; esac
  done
  printf '%s' "$reste"
}

# --- Les projets de la vraie stack --------------------------------------------------------------------
# Sans projet actif, le shell ne rend que sa porte d'entrée (#279) : la session pose l'identifiant d'un
# projet dans le `localStorage` de chaque origine. Il vient de l'API, jamais d'une constante : l'état du
# banc en porte un par scénario joué, et le projet neuf de l'état « vide » est déclaré par elle. Tout
# passe par `relecture-projets.py`, qui ne parle qu'à l'API — pas de JSON lu en shell.
RELECTURE_PROJETS="$RACINE/scripts/design/relecture-projets.py"
TEMOIN_PROJET_NEUF="$RACINE/$SOUS_DOSSIER/.projet-neuf"

# chemin_natif <chemin> : la forme qu'un programme Windows comprend (`E:/…`) — la forme MSYS `/e/…` n'y
# désigne rien —, le chemin tel quel ailleurs.
chemin_natif() {
  if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi
}

# Le dossier du projet neuf : sous l'atelier des relectures, dans le profil de l'utilisateur. Ni
# `AppData` (le temporaire d'un poste Windows) ni le dépôt : la validation des racines les refuse
# (EF-38, mesuré en #221) — même raison, même parade que l'atelier du banc (`~/maestro-scenarios`).
racine_projet_neuf() {
  local base="${MAESTRO_RELECTURE_ATELIER:-$HOME/maestro-relecture}"
  printf '%s/%s/projet-neuf' "${base//\\//}" "$1"
}

# annonce_projets <port api> <côté> : les projets que la stack sert, et le nombre de runs de chacun —
# ce qui dit dans lequel un écran a quelque chose à montrer. PROJETS_SERVIS garde la liste de l'après,
# pour ne redire celle de l'avant que si elle diffère.
PROJETS_SERVIS=""
annonce_projets() {
  local port="$1" cote="$2" python lignes id nom runs _racine
  python="$(python_du_depot)" || { dire "  ⚠ projets  : [$cote] aucun interpréteur Python pour les lire"; return 0; }
  # `relecture-projets.py` n'a que la bibliothèque standard : le jeton lui arrive par l'environnement.
  resoudre_jeton_api
  if ! lignes="$(PYTHONIOENCODING=utf-8 MAESTRO_API_JETON="$JETON_API" \
    "$python" "$RELECTURE_PROJETS" lister --port "$port" 2>&1)"; then
    dire "  ⚠ projets  : [$cote] liste illisible — $(printf '%s\n' "$lignes" | tail -n 1)"
    return 0
  fi
  if [ "$cote" = "avant" ] && [ "$(printf '%s\n' "$lignes" | cut -f1)" = "$(printf '%s\n' "$PROJETS_SERVIS" | cut -f1)" ]; then
    dire "  projets    : [avant] les mêmes"
    return 0
  fi
  [ "$cote" = "après" ] && PROJETS_SERVIS="$lignes"
  if [ -z "$lignes" ]; then
    dire "  projets    : [$cote] aucun — l'état n'en porte pas, les écrans resteront sur la porte d'entrée"
    return 0
  fi
  dire "  projets    : [$cote] l'identifiant à poser comme projet actif de cette origine :"
  while IFS=$'\t' read -r id nom runs _racine; do
    [ -n "$id" ] && dire "$(printf '               %-16s %s — %s run(s)' "$id" "$nom" "$runs")"
  done <<<"$lignes"
}

# declare_projet_neuf <port api> <côté> : le projet de l'état « vide », déclaré par l'API de ce côté-là
# (`origine: nouveau` : c'est elle qui valide la racine et crée le dossier, comme pour quelqu'un qui
# démarre). Le même dossier pour les deux stacks, un identifiant différent pour chacune — dit.
declare_projet_neuf() {
  local port="$1" cote="$2" racine python sortie
  racine="$(racine_projet_neuf "$IID")"
  # Le témoin AVANT la déclaration : un dossier créé par une API qui aurait ensuite échoué reste à nous.
  mkdir -p "$(dirname "$TEMOIN_PROJET_NEUF")" 2>/dev/null
  printf '%s\n' "$racine" >"$TEMOIN_PROJET_NEUF"
  python="$(python_du_depot)" || { dire "  ⚠ projet   : [$cote] aucun interpréteur Python pour le déclarer"; return 0; }
  resoudre_jeton_api
  if sortie="$(PYTHONIOENCODING=utf-8 MAESTRO_API_JETON="$JETON_API" \
    "$python" "$RELECTURE_PROJETS" declarer --port "$port" \
    --racine "$(chemin_natif "$racine")" 2>&1)"; then
    dire "  projet     : [$cote] « Projet neuf » déclaré par l'API — $(printf '%s\n' "$sortie" | tail -n 1)"
  else
    dire "  ⚠ projet   : [$cote] non déclaré — $(printf '%s\n' "$sortie" | tail -n 1) ; l'écran restera sur sa porte"
  fi
}

# retire_projet_neuf : le dossier du projet neuf, s'il est celui que le témoin nomme et qu'il est bien
# SOUS l'atelier (`…/<iid>/projet-neuf`) — la déclaration, elle, vit dans le banc, que le prochain état
# réécrit. Le dossier de l'iid part avec lui s'il n'y reste rien.
retire_projet_neuf() {
  [ -f "$TEMOIN_PROJET_NEUF" ] || return 0
  local cible
  cible="$(head -n 1 "$TEMOIN_PROJET_NEUF")"
  case "$cible" in
    */[0-9]*/projet-neuf)
      if [ -d "$cible" ]; then
        rm -rf "$cible" && printf '  ✓ projet neuf retiré (%s)\n' "$cible"
      fi
      rmdir "$(dirname "$cible")" 2>/dev/null
      ;;
  esac
  rm -f "$TEMOIN_PROJET_NEUF"
}

# resoudre_jeton_api : le jeton de l'API locale (#638), demandé UNE FOIS à `maestro-api --jeton`.
# L'API sert durcie : sans lui, la sonde d'espace ci-dessous et `relecture-projets.py` liraient un 401
# au lieu de l'état de la stack — donc « aucune API ne sert le banc » sous une stack en marche. Vide en
# régime ouvert, et vide aussi sans venv : un poste sans dépendances ne sert de toute façon pas l'API
# qu'on regarde. Le résultat vit dans une variable globale — une fonction appelée en `$(…)` tournerait
# dans un sous-shell et redemanderait le jeton à chaque écran.
JETON_API=""
JETON_API_RESOLU=0
resoudre_jeton_api() {
  local python
  [ "$JETON_API_RESOLU" = 1 ] && return 0
  JETON_API_RESOLU=1
  python="$(python_du_depot)" || return 0
  JETON_API="$( (cd "$RACINE" && "$python" -m maestro.controltower.cli --jeton 2>/dev/null) )" || JETON_API=""
  return 0
}

# sert_le_banc <port api> : une API de CETTE relecture, restée d'un état précédent, sert-elle le jeu de
# données du banc sur ce port ? Le préflight du banc la refuserait (elle republierait dans le journal
# qu'on réécrit), et elle est à nous : on l'arrête. Une autre stack sur ces ports — celle que la
# session a lancée sur les données de sa copie — est remplacée par le lanceur sans que ses runs soient
# soldés, comme avant ce ticket.
sert_le_banc() {
  local corps
  resoudre_jeton_api
  if [ -n "$JETON_API" ]; then
    corps="$(curl -s --max-time 3 -H "Authorization: Bearer $JETON_API" \
      "http://127.0.0.1:$1/api/sante" 2>/dev/null)"
  else
    corps="$(curl -s --max-time 3 "http://127.0.0.1:$1/api/sante" 2>/dev/null)"
  fi
  printf '%s' "$corps" | grep -q '"espace" *: *"[^"]*\.banc"'
}

# monte <start.sh> <port api> <port ui> <options…> : (re)démarre une stack sur ses ports, dans un état.
monte() {
  local lanceur="$1" api="$2" ui="$3"
  shift 3
  if sert_le_banc "$api"; then
    MAESTRO_PORT_API="$api" MAESTRO_PORT_UI="$ui" bash "$RACINE/scripts/controltower/start.sh" --stop \
      >/dev/null 2>&1
  fi
  MAESTRO_PORT_API="$api" MAESTRO_PORT_UI="$ui" bash "$lanceur" "$@"
}

# --- Les écrans ---------------------------------------------------------------------------------------
ECRANS_TOUCHES="$RACINE/scripts/presentation/ecrans-touches.sh"

# Les fichiers rangés sous la ligne « indéterminée » de #544 — ceux dont aucune route ne se dérive.
indetermines_de() {
  awk -F'\t' '$1 !~ /^#/ && $2 == "-" { n = split($4, f, ","); for (i = 1; i <= n; i++) print f[i] }'
}

# Les écrans DIRECTS : route, clé, fichiers.
directs_de() {
  awk -F'\t' '$1 !~ /^#/ && $2 != "-" { print $2 "\t" $3 "\t" $4 }'
}

# La remonte : quels fichiers importent ceux qu'on lui donne, transitivement. L'appariement se fait sur
# le nom de module — `from "…/Composeur"`, `import("…/Composeur")` —, avec le `/` obligatoire pour ne pas
# confondre un composant avec un paquet npm homonyme. Bornée à 6 tours : un graphe d'imports de
# `apps/web` est plat (page → composant → primitive), et une borne vaut mieux qu'un point fixe qu'un
# cycle d'imports empêcherait d'atteindre.
importateurs_de() {
  local vus vague tour=0 trouves motif base fichier
  vague="$(LC_ALL=C sort -u | sed '/^$/d')"
  [ -z "$vague" ] && return 0
  vus="$vague"
  while [ -n "$vague" ] && [ "$tour" -lt 6 ]; do
    tour=$((tour + 1))
    motif=""
    while IFS= read -r fichier; do
      [ -z "$fichier" ] && continue
      base="$(basename "$fichier")"
      base="${base%.*}"
      # Un fichier d'index est importé par le nom de son DOSSIER, jamais par le sien.
      if [ "$base" = "index" ]; then base="$(basename "$(dirname "$fichier")")"; fi
      motif="${motif}${motif:+|}/${base}"
    done <<<"$vague"
    [ -z "$motif" ] && break
    trouves="$(grep -rlE "(from|import\()[[:space:]]*[\"'][^\"']*(${motif})[\"']" \
      "$RACINE/apps/web/app" "$RACINE/apps/web/components" \
      --include='*.tsx' --include='*.ts' 2>/dev/null \
      | sed "s|^${RACINE}/||" | LC_ALL=C sort -u)"
    # Ne repartir que de ce qu'on ne connaissait pas : sans ça, deux composants qui s'importent l'un
    # l'autre feraient tourner la boucle jusqu'à sa borne pour rien.
    vague="$(printf '%s\n@@@\n%s\n' "$vus" "$trouves" | awk '
      /^@@@$/ { phase = 1; next }
      phase == 0 { vu[$0] = 1; next }
      $0 != "" && !($0 in vu) { print }
    ')"
    if [ -n "$vague" ]; then vus="$(printf '%s\n%s' "$vus" "$vague")"; fi
  done
  printf '%s\n' "$vus"
}

# Le plan : une ligne par écran, `route <TAB> cle <TAB> origine <TAB> fichiers`, où `origine` vaut
# `direct` (le ticket a touché la page) ou `via` (il a touché un composant qu'elle affiche). `direct`
# l'emporte quand les deux se présentent : c'est le rattachement le plus sûr, et le plus court à lire.
plan_de() {
  local iid="$1" brut directs indet composants autres remontes="" orphelins="" composant routes r c
  brut="$(bash "$ECRANS_TOUCHES" --ref HEAD --travail-en-cours "$iid" 2>/dev/null)"
  directs="$(printf '%s\n' "$brut" | directs_de)"

  indet="$(printf '%s\n' "$brut" | indetermines_de)"
  composants="$(printf '%s\n' "$indet" | grep '^apps/web/components/' || true)"
  autres="$(printf '%s\n' "$indet" | grep -v '^apps/web/components/' | sed '/^$/d' || true)"

  # UN COMPOSANT À LA FOIS, et non tous ensemble : la question « quels écrans affichent CE fichier ? »
  # est celle qu'on veut lire dans le plan (un « ← Conversation.tsx » dit quoi regarder ; un
  # « ← app/agents/…/page.tsx » nomme l'importateur, c'est-à-dire la réponse et non la cause). C'est
  # aussi ce qui permet de repérer le composant QUI NE MÈNE NULLE PART — un `Shell.tsx`, importé du
  # seul `app/layout.tsx`, ou une primitive que personne n'affiche encore : fondu dans une remonte
  # collective, il disparaîtrait en silence, alors que la règle de #544 est que l'inconnu se nomme.
  while IFS= read -r composant; do
    [ -z "$composant" ] && continue
    routes="$(printf '%s\n' "$composant" | importateurs_de \
      | grep '^apps/web/app/' \
      | bash "$ECRANS_TOUCHES" --chemins "$iid" 2>/dev/null \
      | directs_de | cut -f1,2 | LC_ALL=C sort -u)"
    if [ -z "$routes" ]; then
      orphelins="${orphelins}${orphelins:+$'\n'}${composant}"
      continue
    fi
    while IFS=$'\t' read -r r c; do
      [ -z "$r" ] && continue
      remontes="${remontes}${remontes:+$'\n'}${r}"$'\t'"${c}"$'\t'"${composant}"
    done <<<"$routes"
  done <<<"$composants"

  # Les indéterminés sortent par le MÊME canal que les écrans, sous la route `-` — le contrat de #544,
  # repris tel quel. Une globale ne pourrait pas les porter : `plan_de` est appelée en substitution de
  # commande, donc dans un sous-shell, où toute affectation meurt avec lui.
  if [ -n "$orphelins" ]; then
    autres="${autres}${autres:+$'\n'}${orphelins}"
  fi

  {
    printf '%s\n@@@\n%s\n' "$directs" "$remontes" | awk -F'\t' '
      /^@@@$/ { phase = 1; next }
      $1 == "" { next }
      {
        origine = (phase == 0) ? "direct" : "via"
        if (!($1 in vu)) { vu[$1] = 1; ordre[++n] = $1; cle[$1] = $2; org[$1] = origine; src[$1] = $3 }
        else {
          if (org[$1] == "via" && origine == "direct") { org[$1] = "direct" }
          if (index(src[$1], $3) == 0) { src[$1] = src[$1] "," $3 }
        }
      }
      END { for (i = 1; i <= n; i++) { r = ordre[i]; print r "\t" cle[r] "\t" org[r] "\t" src[r] } }
    ' | LC_ALL=C sort
    # Une ligne par fichier indéterminé, et jamais regroupées : chacun est une question à part, et la
    # session doit pouvoir les nommer un par un dans « ce que je n'ai pas pu voir ».
    while IFS= read -r fichier; do
      [ -n "$fichier" ] && printf -- '-\t-\tindetermine\t%s\n' "$fichier"
    done <<<"$autres"
  }
}

# --- L'avant : ce qu'origin/main sait servir --------------------------------------------------------
AVANT_ETAT=""      # actif · eteint · indisponible · applique
AVANT_SHA=""
AVANT_RAISON=""
ROUTES_AVANT=""

# Le libellé de l'avant qu'un ticket qui applique ne monte pas (en-tête, §8) : le plan, la
# préparation, la couverture et la saisine disent le même.
AVANT_SANS_OBJET="aucun — le ticket applique une décision déjà prise : l'après seul (#1243)"

evalue_avant() {
  # Le régime d'abord : un avant qu'on ne montera pas n'a pas à être évalué.
  if [ "$REGIME" = "applique" ]; then
    AVANT_ETAT="applique"; AVANT_RAISON="le ticket applique une décision déjà prise"
    return 0
  fi
  if [ "${MAESTRO_RELECTURE_AVANT:-1}" = 0 ]; then
    AVANT_ETAT="eteint"; AVANT_RAISON="MAESTRO_RELECTURE_AVANT=0"
    return 0
  fi
  if [ -z "$PORT_UI_AVANT" ]; then
    AVANT_ETAT="indisponible"; AVANT_RAISON="ports de l'après illisibles (UI « $PORT_UI »)"
    return 0
  fi
  AVANT_SHA="$(git -C "$RACINE" rev-parse --verify --quiet "$REF_AVANT^{commit}" 2>/dev/null)"
  if [ -z "$AVANT_SHA" ]; then
    AVANT_ETAT="indisponible"; AVANT_RAISON="$REF_AVANT introuvable dans ce dépôt"
    return 0
  fi
  AVANT_ETAT="actif"
  # Les écrans qu'origin/main SERT, à la règle de #544 : ses pages, classées comme celles du ticket.
  # Une page sous un segment dynamique est écartée — elle se range sous la route de sa liste sans que
  # cette route réponde, et l'avant serait alors capturé sur une 404.
  ROUTES_AVANT="$(git -C "$RACINE" ls-tree -r --name-only "$AVANT_SHA" -- apps/web/app 2>/dev/null \
    | grep -E '/page\.(tsx|ts|jsx|js|mdx)$' | grep -v '\[' \
    | bash "$ECRANS_TOUCHES" --chemins "$IID" 2>/dev/null \
    | directs_de | cut -f1 | LC_ALL=C sort -u)"
}

# avant_de <route> : ce que l'avant rend pour cet écran — son URL, `nouveau`, ou `-` (non évalué).
avant_de() {
  local route="$1"
  if [ "$AVANT_ETAT" != "actif" ] || [ "$route" = "-" ]; then
    printf -- '-'
  elif printf '%s\n' "$ROUTES_AVANT" | grep -qxF -- "$route"; then
    printf 'http://localhost:%s%s' "$PORT_UI_AVANT" "$route"
  else
    printf 'nouveau'
  fi
}

# arrete_stack_avant : arrête la stack de l'avant nommée par le témoin. Par le `start.sh` de CE dépôt :
# l'arrêt se fait par les ports, et il doit rester possible quand l'arbre de l'avant a déjà disparu.
arrete_stack_avant() {
  [ -f "$TEMOIN_AVANT" ] || return 0
  local _iid _chemin api ui
  IFS=$'\t' read -r _iid _chemin api ui <"$TEMOIN_AVANT"
  [ -n "$api" ] && [ -n "$ui" ] || return 0
  MAESTRO_PORT_API="$api" MAESTRO_PORT_UI="$ui" \
    bash "$RACINE/scripts/controltower/start.sh" --stop 2>&1 | sed 's/^/  [avant] /'
}

# retire_avant : retire le worktree de l'avant nommé par le témoin, et le témoin avec — seulement si le
# retrait a abouti, pour qu'un `--fin` rejoué retrouve ce qui reste à retirer.
retire_avant() {
  [ -f "$TEMOIN_AVANT" ] || return 0
  local iid _chemin _api _ui
  IFS=$'\t' read -r iid _chemin _api _ui <"$TEMOIN_AVANT"
  if [ -z "$iid" ] || [ ! -f "$WORKTREE_SH" ]; then
    rm -f "$TEMOIN_AVANT"
    return 0
  fi
  if bash "$WORKTREE_SH" avant --retirer "$iid" 2>&1; then
    rm -f "$TEMOIN_AVANT"
  fi
}

# prepare_avant : monte l'avant et sa stack, APRÈS l'après — l'après est ce qui compte, et un avant ne
# se paie pas pour une relecture qui ne pourra pas avoir lieu. Best-effort de bout en bout : il ne
# change jamais le code de retour.
prepare_avant() {
  local debut sortie code chemin journal ligne
  case "$AVANT_ETAT" in
    applique) dire "  avant      : $AVANT_SANS_OBJET"; return 0 ;;
    eteint) dire "  avant      : éteint ($AVANT_RAISON) — l'après seul"; return 0 ;;
    actif) ;;
    *) dire "  avant      : indisponible — $AVANT_RAISON ; l'après seul"; return 0 ;;
  esac
  if [ "$NB_AVANT" -eq 0 ]; then
    dire "  avant      : rien à monter — tous les écrans du plan sont nouveaux"
    return 0
  fi
  if [ ! -f "$WORKTREE_SH" ]; then
    dire "  avant      : indisponible — scripts/git/worktree.sh introuvable ; l'après seul"
    return 0
  fi

  dire "  avant      : montage d'$REF_AVANT — worktree détaché et seconde stack (~50 s la première fois)"
  debut=$SECONDS
  sortie="$(bash "$WORKTREE_SH" avant --sans-fetch "$IID" 2>&1)"; code=$?
  chemin="$(printf '%s\n' "$sortie" | sed -n 's/^AVANT //p' | tail -n 1)"
  if [ "$code" -ne 0 ] || [ -z "$chemin" ]; then
    dire "  ⚠ avant indisponible — montage en échec : $(printf '%s\n' "$sortie" \
      | grep -v '^AVANT ' | sed '/^[[:space:]]*$/d' | tail -n 1 | sed 's/^[[:space:]]*//')"
    dire "    l'après reste prêt ; l'avant va à « ce que je n'ai pas pu voir »."
    return 0
  fi
  chemin="${chemin//\\//}"
  mkdir -p "$(dirname "$TEMOIN_AVANT")" 2>/dev/null
  printf '%s\t%s\t%s\t%s\n' "$IID" "$chemin" "$PORT_API_AVANT" "$PORT_UI_AVANT" >"$TEMOIN_AVANT"

  # Le même état que l'après, ou rien (en-tête, §6). C'est le lanceur d'ORIGIN/MAIN qui dit s'il sait
  # le servir : son diagnostic ne démarre rien et refuse une option qu'il ne connaît pas — une
  # branche plus jeune que son lanceur n'a pas à le deviner dans son source. Refusé, la stack d'avant
  # restée d'un autre état est arrêtée : elle servirait cet autre état sous la même URL.
  local lanceur="$chemin/scripts/controltower/start.sh"
  if ! MAESTRO_BROWSER_DEFAUT=firefox MAESTRO_PORT_API="$PORT_API_AVANT" MAESTRO_PORT_UI="$PORT_UI_AVANT" \
    bash "$lanceur" "${ARGS_ETAT[@]}" --diagnostic-navigateur >/dev/null 2>&1; then
    arrete_stack_avant >/dev/null
    dire "  avant      : $REF_AVANT ne sait pas servir l'état « $ETAT » (son lanceur refuse ${ARGS_ETAT[*]}) — l'après seul pour cet état"
    return 0
  fi

  # Le `start.sh` de l'AVANT, et non celui de la branche : l'avant est origin/main tel qu'il se lance.
  if monte "$lanceur" "$PORT_API_AVANT" "$PORT_UI_AVANT" "${ARGS_ETAT[@]}" --no-browser >/dev/null 2>&1; then
    dire "  ✓ avant prêt en $((SECONDS - debut)) s : http://localhost:$PORT_UI_AVANT ($REF_AVANT ${AVANT_SHA:0:7}, état « $ETAT »)"
    case "$ETAT" in
      vide) declare_projet_neuf "$PORT_API_AVANT" "avant" ;;
      *) annonce_projets "$PORT_API_AVANT" "avant" ;;
    esac
    return 0
  fi
  dire "  ⚠ avant indisponible — la stack d'$REF_AVANT n'a pas démarré dans l'état « $ETAT ». Fin de ses journaux :"
  for journal in api ui; do
    while IFS= read -r ligne; do
      dire "      [$journal] $ligne"
    done < <(tail -n 5 "$chemin/.maestro/controltower/$PORT_API_AVANT-$PORT_UI_AVANT/$journal.log" 2>/dev/null)
  done
  arrete_stack_avant >/dev/null
  retire_avant >/dev/null
  dire "    l'après reste prêt ; l'avant va à « ce que je n'ai pas pu voir »."
}

# --- Le regard neuf et sa planche (section 7 de l'en-tête) ------------------------------------------
GRILLE="$RACINE/scripts/design/grille-relecture.tsv"
LIB_SH="$RACINE/scripts/gitlab/lib.sh"

# paires_de <iid> <lignes du plan> : une ligne par écran, état et thème —
# `etat <TAB> route <TAB> cle <TAB> theme <TAB> apres <TAB> avant`, où `apres` et `avant` sont le chemin
# RELATIF de la capture quand elle est sur le disque, `-` sinon, et `avant` vaut `nouveau` pour un écran
# absent d'origin/main, `sans-avant` quand le régime n'en monte pas (§8). Toutes les combinaisons des
# états DEMANDÉS y sont, capturées ou non : ce qui manque se nomme.
paires_de() {
  local iid="$1" lignes="$2" etat dossier route cle _origine _fichiers av theme apres avant
  for etat in $(etats_demandes "$iid"); do
    dossier="$(dossier_captures "$iid" "$etat")"
    while IFS=$'\t' read -r route cle _origine _fichiers; do
      [ -z "$route" ] && continue
      av="$(avant_de "$route")"
      for theme in clair sombre; do
        apres="$dossier/$(nom_capture "$cle" "$theme")"
        avant="$dossier/$(nom_capture_avant "$cle" "$theme")"
        [ -s "$RACINE/$apres" ] || apres="-"
        if [ ! -s "$RACINE/$avant" ]; then
          avant="-"
          [ "$av" = "nouveau" ] && avant="nouveau"
          [ "$AVANT_ETAT" = "applique" ] && avant="sans-avant"
        fi
        printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$etat" "$route" "$cle" "$theme" "$apres" "$avant"
      done
    done <<<"$lignes"
  done
}

# Le nombre de captures RÉELLEMENT sur le disque dans une liste de paires.
compte_captures() {
  awk -F'\t' '
    $5 != "-" { n++ }
    $6 != "-" && $6 != "nouveau" && $6 != "sans-avant" { n++ }
    END { print n + 0 }'
}

# La racine sous la forme que l'outil `Read` du sous-agent accepte (voir `chemin_natif`).
racine_native() { chemin_natif "$RACINE"; }

# cellule_capture <chemin|-|nouveau> <racine> : ce qu'une case du tableau de la saisine dit d'un côté.
cellule_capture() {
  case "$1" in
    -) printf 'non capturé' ;;
    nouveau) printf 'écran nouveau — aucun avant' ;;
    sans-avant) printf 'non monté — le ticket applique' ;;
    *) printf '`%s/%s`' "$2" "$1" ;;
  esac
}

# en_citation : recopie stdin tel quel, en citation Markdown. Une décision recopiée garde ses propres
# titres (`## Veille de conception…`) : citée, elle ne se confond pas avec les sections de la saisine.
en_citation() { awk '{ print ($0 == "" ? ">" : "> " $0) }'; }

# ecris_saisine <iid> <paires> <fichier> : la saisine du regard neuf. Rend 1 si la grille est
# introuvable — une saisine sans grille ferait rendre un texte libre, c'est-à-dire ce que #980 retire.
ecris_saisine() {
  local iid="$1" paires="$2" sortie="$3" racine code_attente rendu="" decisions="" etats titre
  local etat du_etat nb route _cle theme apres avant libelle question source n nom raison
  if [ ! -f "$GRILLE" ]; then
    printf 'relecture-visuelle.sh : grille introuvable (%s) — pas de saisine.\n' "$GRILLE" >&2
    return 1
  fi
  racine="$(racine_native)"
  # La lecture déjà faite par le régime, s'il l'a faite : un aller par appel (#602).
  lire_attente
  code_attente="$CODE_ATTENTE"
  if [ "$code_attente" -eq 0 ]; then
    rendu="$(printf '%s\n' "$ATTENTE_BRUTE" | awk '/^@@decisions@@$/ { exit } { print }')"
    decisions="$(decisions_de_l_attente)"
  fi
  etats="$(printf '%s\n' "$paires" | cut -f1 | awk 'NF && !vu[$0]++')"
  # Le titre du regard dit qui juge (§8) : c'est lui que `lib.sh relecture-note` relit.
  titre="Regard neuf"
  [ "$REGIME" = "applique" ] && titre="Regard de la session"

  {
    if [ "$REGIME" = "applique" ]; then
      printf '# Saisine de la relecture — ticket #%s\n\n' "$iid"
    else
      printf '# Saisine du regard neuf — ticket #%s\n\n' "$iid"
    fi
    cat <<'TETE'
Préparée par `scripts/design/relecture-visuelle.sh --saisine`. Tout ce que tu as à juger est ici, et
rien d'autre n'est à lire : ouvre chaque capture nommée avec `Read` (les chemins sont absolus), puis
rends le gabarit de la section 5, rempli.
TETE
    if [ "$REGIME" = "applique" ]; then
      printf '\n**Régime : %s** (#1243) — %s.\n' "$(description_regime)" "$(origine_regime)"
      cat <<'REGIME'
Il n'y a pas d'avant : chaque capture se juge seule, et ce que la grille compare à l'avant se juge
contre les autres écrans de cette saisine. Seuls les états que le ticket nomme ont été demandés, le
défaut s'il n'en nomme aucun.

**À reporter dans « ce que je n'ai pas pu voir »** : l'avant — non monté, le ticket applique une
décision déjà prise, et rien n'a été comparé à `origin/main`.
REGIME
      nom="$(etats_non_demandes "$iid")"
      [ -n "$nom" ] && printf 'Et les états que le ticket ne demande pas, non ouverts : %s.\n' "${nom// /, }"
    else
      printf '\n**Régime : %s** — %s.\n' "$(description_regime)" "$(origine_regime)"
    fi
    cat <<'TETE'

## 1. Les captures

Une ligne = une paire : même écran, même thème, même état. « écran nouveau » : il n'existe pas avant
ce ticket et n'a que son après. « non capturé » : personne ne l'a pris — ce qui en dépend est « non vu ».
Chaque état vient de la vraie stack, jamais d'un scénario factice : deux stacks qui servent le même
état ne le servent pas au même instant, et un âge relatif ou un identifiant peut différer d'un côté
à l'autre sans que le ticket y soit pour rien.
TETE
    while IFS= read -r etat; do
      [ -z "$etat" ] && continue
      du_etat="$(printf '%s\n' "$paires" | ETAT="$etat" awk -F'\t' '$1 == ENVIRON["ETAT"]')"
      nb="$(printf '%s\n' "$du_etat" | compte_captures)"
      if [ "$nb" -eq 0 ]; then
        printf '\n### État « %s » — aucune capture\n\n%s.\n\nRien de cet état n'\''a été capturé : tout ce qui le concerne est « non vu ».\n' \
          "$etat" "$(description_etat "$etat")"
        continue
      fi
      printf '\n### État « %s »\n\n%s.\n\n| Écran | Thème | Après | Avant |\n|---|---|---|---|\n' \
        "$etat" "$(description_etat "$etat")"
      while IFS=$'\t' read -r _e route _cle theme apres avant; do
        [ -z "$route" ] && continue
        printf '| `%s` | %s | %s | %s |\n' "$route" "$theme" \
          "$(cellule_capture "$apres" "$racine")" "$(cellule_capture "$avant" "$racine")"
      done <<<"$du_etat"
    done <<<"$etats"

    printf '\n### États que la vraie stack ne produit pas — non couverts\n\n'
    while IFS=$'\t' read -r nom raison; do
      [ -n "$nom" ] && printf -- '- `%s` — %s.\n' "$nom" "$raison"
    done <<<"$NON_COUVERTS"
    printf '\nAucune capture ne les montre et aucune ne les imite : ce qui en dépend est « non vu ».\n'

    printf '\n## 2. Le rendu attendu\n\n'
    if [ "$code_attente" -ne 0 ]; then
      printf "Le ticket n'a pas pu être lu (lib.sh relecture-attente : code %s) : le rendu attendu est inconnu.\n" "$code_attente"
    elif [ -z "$(printf '%s' "$rendu" | tr -d '[:space:]')" ]; then
      printf 'Le ticket ne porte pas de rendu attendu.\n'
    else
      printf 'La section du ticket, telle qu'\''elle est écrite :\n\n'
      printf '%s\n' "$rendu" | sed '1d' | en_citation
    fi

    printf '\n## 3. Les décisions déjà prises à l'\''écran\n\n'
    if [ "$code_attente" -ne 0 ]; then
      printf "Le ticket n'a pas pu être lu : les décisions consignées sont inconnues.\n"
    elif [ -z "$(printf '%s' "$decisions" | tr -d '[:space:]')" ] && [ -z "$PARTIS_PRIS" ]; then
      printf 'Aucune décision consignée sur le ticket.\n'
    fi
    if [ "$code_attente" -eq 0 ] && [ -n "$(printf '%s' "$decisions" | tr -d '[:space:]')" ]; then
      printf 'Les commentaires du ticket qui les portent, tels qu'\''ils sont écrits — une citation par\n'
      printf 'commentaire :\n\n'
      # Le séparateur de `relecture-attente` devient une ligne HORS citation : deux commentaires font
      # deux citations, et aucun ne se lit comme la suite de l'autre.
      printf '%s\n' "$decisions" | awk '
        /^@@decision@@$/ { print ""; next }
        { print ($0 == "" ? ">" : "> " $0) }'
    fi
    if [ -n "$PARTIS_PRIS" ]; then
      if [ "$code_attente" -ne 0 ] || [ -n "$(printf '%s' "$decisions" | tr -d '[:space:]')" ]; then
        printf '\n'
      fi
      printf 'Une veille consignée sans ancre, recopiée du ticket par la session :\n\n'
      en_citation <"$PARTIS_PRIS"
    fi

    printf '\n## 4. La grille\n\n'
    printf 'Elle nomme ce qui se voit ; elle ne mesure ni contraste ni géométrie (d'\''autres outils le font).\n\n'
    n=0
    while IFS=$'\t' read -r libelle question source; do
      case "$libelle" in '' | '#'*) continue ;; esac
      n=$((n + 1))
      printf '%s. **%s** — %s *Source : %s.*\n' "$n" "$libelle" "$question" "$source"
    done <"$GRILLE"

    printf '\n## 5. Le gabarit à rendre — rempli, et rien d'\''autre\n\n'
    printf 'Réponses permises : ✓, ✗ (avec écran · thème · état), ou « non vu » (avec ce qui manque).\n'
    printf 'Recopie les titres `### %s — …` tels quels.\n\n' "$titre"
    printf '### %s — grille\n\n| Ligne | Réponse | Où, et ce qui se voit |\n|---|---|---|\n' "$titre"
    while IFS=$'\t' read -r libelle _question _source; do
      case "$libelle" in '' | '#'*) continue ;; esac
      printf '| %s |  |  |\n' "$libelle"
    done <"$GRILLE"

    printf '\n### %s — contre le rendu attendu\n\n' "$titre"
    if [ "$code_attente" -ne 0 ]; then
      printf "Rendu attendu illisible sur le ticket : non confronté.\n"
    elif [ -z "$(printf '%s' "$rendu" | tr -d '[:space:]')" ]; then
      printf 'Le ticket ne porte pas de rendu attendu : rien à confronter.\n'
    else
      printf "Une rubrique que le ticket ne remplit pas se répond « non renseignée ».\n\n"
      printf '| Rubrique | Réponse | Ce qui se voit |\n|---|---|---|\n'
      printf '| Question |  |  |\n| Référence |  |  |\n| Ce qui ne bouge pas |  |  |\n| États à couvrir |  |  |\n'
    fi

    printf '\n### %s — contre les décisions déjà prises\n\n' "$titre"
    if [ "$code_attente" -ne 0 ] && [ -z "$PARTIS_PRIS" ]; then
      printf 'Décisions illisibles sur le ticket : non confrontées.\n'
    elif [ -z "$(printf '%s' "$decisions" | tr -d '[:space:]')" ] && [ -z "$PARTIS_PRIS" ]; then
      printf 'Aucune décision consignée : rien à confronter.\n'
    else
      printf -- '- <chaque parti pris ou choix, en une ligne> — tenu · plié · non vu : <ce qui se voit>\n'
    fi
  } >"$sortie"
  return 0
}

# python_du_depot : l'interpréteur du venv du dépôt, sinon celui du poste. La planche n'importe que la
# bibliothèque standard (et `build.py`, qui n'en importe pas plus) : un poste sans venv la produit quand
# même, là où tout le reste du dépôt exigerait le venv.
python_du_depot() {
  local candidat
  for candidat in "$RACINE/.venv/Scripts/python.exe" "$RACINE/.venv/bin/python"; do
    if [ -x "$candidat" ]; then printf '%s' "$candidat"; return 0; fi
  done
  for candidat in python3 python; do
    if command -v "$candidat" >/dev/null 2>&1; then printf '%s' "$candidat"; return 0; fi
  done
  return 1
}

# --- Les modes ------------------------------------------------------------------------------------
affiche_plan() {
  local iid="$1" lignes="$2" nb="$3" indet="$4" route _cle origine fichiers suffixe f avant e raison
  dire "Relecture visuelle du ticket #$iid"
  dire ""
  dire "  ports      : UI $PORT_UI · API $PORT_API"
  if [ -n "$REGIME" ]; then
    dire "  régime     : $(description_regime)"
    dire "               ($REGIME : $(origine_regime) ; --regime decide|applique l'impose)"
  fi
  case "$AVANT_ETAT" in
    actif)  dire "  avant      : $REF_AVANT (${AVANT_SHA:0:7}) — UI $PORT_UI_AVANT · API $PORT_API_AVANT" ;;
    applique) dire "  avant      : $AVANT_SANS_OBJET" ;;
    eteint) dire "  avant      : éteint ($AVANT_RAISON) — l'après seul" ;;
    *)      dire "  avant      : indisponible — $AVANT_RAISON ; l'après seul" ;;
  esac
  if [ "$AVANT_ETAT" = "actif" ]; then
    dire "  captures   : $SOUS_DOSSIER/$iid/<ecran>-<theme>.png, et <ecran>-<theme>-avant.png à côté — chemin RELATIF"
  else
    dire "  captures   : $SOUS_DOSSIER/$iid/<ecran>-<theme>.png — chemin RELATIF, jamais absolu"
  fi
  dire "  thèmes     : clair, sombre — les deux, toujours (le socle en porte deux, on en garde deux)"
  if [ "$REGIME" = "applique" ]; then
    dire "  états      : ceux que la rubrique « États à couvrir » du ticket nomme, le premier sinon —"
    dire "               aucun autre n'est demandé ; par --etat <nom>, captures sous $SOUS_DOSSIER/$iid/<état>/ :"
  else
    dire "  états      : ceux de la vraie stack — le premier par défaut, les autres par --etat <nom>,"
    dire "               leurs captures sous $SOUS_DOSSIER/$iid/<état>/ :"
  fi
  for e in $ETATS; do
    dire "$(printf '                 %-12s %s' "$e" "$(description_etat "$e")")"
  done
  dire "  non couverts : la vraie stack ne les produit pas — à nommer dans le jugement, jamais à imiter :"
  while IFS=$'\t' read -r e raison; do
    [ -n "$e" ] && dire "$(printf '                 %-12s %s' "$e" "$raison")"
  done <<<"$NON_COUVERTS"
  dire ""
  if [ "$nb" -eq 0 ]; then
    dire "  aucun écran : ce ticket n'a touché aucune surface visible."
  else
    dire "  écrans à regarder ($nb) :"
    while IFS=$'\t' read -r route _cle origine fichiers; do
      [ -z "$route" ] && continue
      suffixe=""
      [ "$origine" = "via" ] && suffixe="  (composant affiché ici)"
      dire "$(printf '    %-12s http://localhost:%s%-12s ← %s%s' \
        "$route" "$PORT_UI" "$route" "$fichiers" "$suffixe")"
      avant="$(avant_de "$route")"
      case "$avant" in
        -) ;;
        nouveau) dire "$(printf '    %-12s avant : aucun — écran NOUVEAU, absent d'\''%s' "" "$REF_AVANT")" ;;
        *) dire "$(printf '    %-12s avant : %s' "" "$avant")" ;;
      esac
    done <<<"$lignes"
  fi
  if [ -n "$indet" ]; then
    dire ""
    dire "  indéterminé — aucune route ne s'en dérive, à relayer tel quel dans le jugement :"
    while IFS=$'\t' read -r _route _cle _origine f; do
      [ -n "$f" ] && dire "    $f"
    done <<<"$indet"
  fi
}

if [ -n "$PARTIS_PRIS" ]; then
  if [ "$MODE" != "saisine" ]; then
    printf 'relecture-visuelle.sh : --partis-pris ne vaut qu'\''avec --saisine (c'\''est ce que le regard neuf reçoit).\n' >&2
    exit 2
  fi
  if [ ! -s "$PARTIS_PRIS" ]; then
    printf 'relecture-visuelle.sh : --partis-pris : fichier introuvable ou vide : %s\n' "$PARTIS_PRIS" >&2
    exit 2
  fi
fi

case "$MODE" in
  fin)
    if [ -n "$IID" ]; then
      printf 'relecture-visuelle.sh : --fin ne prend pas d'\''iid (la stack est celle du dépôt courant).\n' >&2
      exit 2
    fi
    if [ -n "$ETAT" ]; then
      printf 'relecture-visuelle.sh : --fin ne prend pas d'\''état (il arrête la stack, quel que soit le sien).\n' >&2
      exit 2
    fi
    if [ -n "$REGIME_OPTION" ]; then
      printf 'relecture-visuelle.sh : --fin ne prend pas de régime (il arrête ce qui a été monté, quel qu'\''il soit).\n' >&2
      exit 2
    fi
    printf 'Fin de la relecture visuelle — ports UI %s · API %s\n' "$PORT_UI" "$PORT_API"
    # Les DEUX stacks avant tout retrait — et l'avant d'abord, ce qui laisse à ses processus le temps
    # de lâcher leurs fichiers pendant que l'après s'arrête : un dossier encore tenu résisterait au
    # retrait de son worktree (#422). Le projet neuf ensuite : son dossier sert encore une API vivante.
    arrete_stack_avant
    MAESTRO_PORT_API="$PORT_API" MAESTRO_PORT_UI="$PORT_UI" \
      bash "$RACINE/scripts/controltower/start.sh" --stop
    retire_projet_neuf
    rm -f "$TEMOIN_ETAT"
    retire_avant
    exit 0
    ;;
  plan | preparer | couverture | saisine | planche)
    if [ -z "$IID" ]; then
      printf 'relecture-visuelle.sh : un iid de ticket est attendu.\n\n' >&2
      usage >&2
      exit 2
    fi
    if [ -n "$ETAT" ] && [ "$MODE" != "preparer" ]; then
      printf 'relecture-visuelle.sh : --etat ne vaut que pour monter la stack (le plan et la couverture valent pour tous les états).\n' >&2
      exit 2
    fi
    if [ -n "$ETAT" ]; then
      case " $ETATS " in
        *" $ETAT "*) ;;
        *)
          # Un état que la vraie stack ne produit pas se REFUSE avec sa raison : c'est la même
          # réponse que le plan, au moment où on le demande.
          raison="$(printf '%s\n' "$NON_COUVERTS" | ETAT="$ETAT" awk -F'\t' '$1 == ENVIRON["ETAT"] { print $2 }')"
          if [ -n "$raison" ]; then
            printf 'relecture-visuelle.sh : l'\''état « %s » n'\''est pas couvert — %s.\n' "$ETAT" "$raison" >&2
            printf '  Il se nomme dans « ce que je n'\''ai pas pu voir », jamais ne s'\''imite.\n' >&2
          else
            printf 'relecture-visuelle.sh : état inconnu « %s » (la vraie stack sert : %s).\n' \
              "$ETAT" "$ETATS" >&2
          fi
          exit 2
          ;;
      esac
    fi
    ;;
esac
ETAT="${ETAT:-$ETAT_DEFAUT}"

# La couverture : pour chaque écran du plan, chaque état DEMANDÉ et chaque thème, la capture est-elle
# là ? Lecture du disque — ni stack, ni navigateur, et la forge une fois au plus, seulement quand
# aucune préparation n'a consigné le régime (§8). Voir l'en-tête, §5 : elle CONSTATE.
# La largeur VISIBLE d'une cellule est passée à la main : `printf '%-10s'` compte des octets sous une
# locale C, et `✓`, `—` en pèsent trois chacun — le tableau se décalait d'une ligne à l'autre selon ce
# qu'elle contenait (même piège que la vue de `run.sh`, #325).
#
# Une colonne par état : 12 de large, pour que « injoignable » tienne sans coller au suivant.
cellule() { printf '%s%*s' "$1" "$((12 - $2))" ''; }

affiche_couverture() {
  local iid="$1" lignes="$2" route cle etat theme fichier marque clair sombre nom raison demandes
  demandes="$(etats_demandes "$iid")"
  if [ "$TSV" = 1 ]; then
    printf '# route\tcle\tetat\tclair\tsombre\n'
    printf '# regime\t%s\t%s\n' "$REGIME" "$(origine_regime)"
  else
    dire "Couverture de la relecture du ticket #$iid — captures sous $SOUS_DOSSIER/$iid/"
    dire ""
    dire "  écran         $(for etat in $demandes; do printf '%-12s' "$etat"; done)"
  fi
  while IFS=$'\t' read -r route cle _origine _fichiers; do
    [ -z "$route" ] && continue
    marque=""
    for etat in $demandes; do
      clair=0; sombre=0
      for theme in clair sombre; do
        fichier="$(dossier_captures "$iid" "$etat")/$(nom_capture "$cle" "$theme")"
        if [ -s "$RACINE/$fichier" ]; then
          [ "$theme" = clair ] && clair=1
          [ "$theme" = sombre ] && sombre=1
        fi
      done
      if [ "$TSV" = 1 ]; then
        printf '%s\t%s\t%s\t%s\t%s\n' "$route" "$cle" "$etat" "$clair" "$sombre"
      else
        case "$clair$sombre" in
          11) marque="${marque}$(cellule '✓' 1)" ;;
          10) marque="${marque}$(cellule 'clair' 5)" ;;
          01) marque="${marque}$(cellule 'sombre' 6)" ;;
          *) marque="${marque}$(cellule '—' 1)" ;;
        esac
      fi
    done
    [ "$TSV" = 1 ] || dire "$(printf '  %-14s' "$route")$marque"
  done <<<"$lignes"
  # Les états que la vraie stack ne produit pas SE COMPTENT AUSSI, en le disant : la couverture est ce
  # que le jugement recopie, et c'est ce qui fait qu'un état non couvert y est nommé (#1165).
  while IFS=$'\t' read -r nom raison; do
    [ -z "$nom" ] && continue
    if [ "$TSV" = 1 ]; then
      printf '# non-couvert\t%s\t%s\n' "$nom" "$raison"
    fi
  done <<<"$NON_COUVERTS"
  if [ "$TSV" != 1 ]; then
    dire ""
    dire "  ✓ les deux thèmes · clair / sombre : un seul · — rien de capturé"
    dire "  une capture n'est pas un regard : ne compte comme vue que celle qu'on a relue."
    dire "  fichiers attendus : $SOUS_DOSSIER/$iid/[<état>/]<clé>-<thème>.png — clés : $(
      printf '%s\n' "$lignes" | cut -f2 | sed '/^$/d' | tr '\n' ' ')"
    dire ""
    dire "  non couverts — la vraie stack ne les produit pas ; nommés dans « ce que je n'ai pas pu voir » :"
    while IFS=$'\t' read -r nom raison; do
      [ -n "$nom" ] && dire "$(printf '    %-12s %s' "$nom" "$raison")"
    done <<<"$NON_COUVERTS"
    dire ""
    dire "  régime     : $(description_regime)"
    dire "               ($REGIME : $(origine_regime))"
    # Un ticket qui applique se relit sans avant (§8) : c'est une absence choisie, et elle se nomme
    # dans le jugement comme les autres — le pied de la couverture est ce qu'il recopie.
    if [ "$REGIME" = "applique" ]; then
      dire "  sans avant — l'après seul, rien n'est comparé à $REF_AVANT ; nommé dans « ce que je n'ai pas pu voir »."
      nom="$(etats_non_demandes "$iid")"
      [ -n "$nom" ] && dire "  non demandés — le ticket ne les nomme pas, non ouverts : ${nom// /, }"
    fi
  fi
}

BRUT="$(plan_de "$IID")"
# Deux natures dans un seul flux (le contrat de #544) : les ÉCRANS, qui portent une route, et les
# INDÉTERMINÉS, qui portent `-`. Ce sont les premiers, et eux seuls, qui décident s'il y a matière à
# monter une stack — un `layout.tsx` ne s'ouvre pas dans un navigateur.
LIGNES="$(printf '%s\n' "$BRUT" | grep -v $'^-\t' | sed '/^$/d' || true)"
INDET="$(printf '%s\n' "$BRUT" | grep $'^-\t' || true)"
NB="$(printf '%s\n' "$LIGNES" | sed '/^$/d' | wc -l | tr -d ' ')"

# Le régime (§8) ne se demande que s'il y a un écran : un ticket sans surface visible ne paie pas
# l'aller vers la forge pour apprendre qu'il n'y avait rien à regarder.
[ "$NB" -gt 0 ] && resoudre_regime

if [ "$MODE" = "couverture" ]; then
  if [ "$NB" -eq 0 ]; then
    dire "Couverture de la relecture du ticket #$IID — aucun écran : rien à couvrir."
    exit 3
  fi
  affiche_couverture "$IID" "$LIGNES"
  exit 0
fi

# L'avant se juge sur l'origin/main LOCAL en `--plan` — gratuit, hors réseau, comme le reste du plan.
# La préparation, elle, va chercher le plus frais d'abord : c'est lui qu'on va servir, et un écran
# mergé entre-temps ne doit pas y être annoncé nouveau. Best-effort : hors ligne, on sert ce qu'on a.
if [ "$MODE" = "preparer" ] && [ "$NB" -gt 0 ] && [ "${MAESTRO_RELECTURE_AVANT:-1}" != 0 ] \
  && [ "$REGIME" != "applique" ]; then
  GIT_TERMINAL_PROMPT=0 git -C "$RACINE" fetch origin main >/dev/null 2>&1
fi
evalue_avant
NB_AVANT=0
while IFS=$'\t' read -r route _cle _origine _fichiers; do
  [ -z "$route" ] && continue
  case "$(avant_de "$route")" in http*) NB_AVANT=$((NB_AVANT + 1)) ;; esac
done <<<"$LIGNES"

# --- Le regard neuf et sa planche (section 7) : lecture du disque, rien ne démarre ---------------------
if [ "$MODE" = "saisine" ] || [ "$MODE" = "planche" ]; then
  if [ "$NB" -eq 0 ]; then
    printf 'Relecture visuelle du ticket #%s — aucun écran : rien à juger.\n' "$IID"
    exit 3
  fi
  PAIRES="$(paires_de "$IID" "$LIGNES")"
  NB_CAPTURES="$(printf '%s\n' "$PAIRES" | compte_captures)"
  NB_PAIRES="$(printf '%s\n' "$PAIRES" | sed '/^$/d' | wc -l | tr -d ' ')"
  NB_PAIRES_VUES="$(printf '%s\n' "$PAIRES" \
    | awk -F'\t' 'NF && ($5 != "-" || ($6 != "-" && $6 != "nouveau" && $6 != "sans-avant")) { n++ }
      END { print n + 0 }')"
  DOSSIER="$SOUS_DOSSIER/$IID"
  if [ "$NB_CAPTURES" -eq 0 ]; then
    printf 'Relecture visuelle du ticket #%s — aucune capture sous %s/ : rien à juger.\n' "$IID" "$DOSSIER" >&2
    printf '  La relecture n'\''a pas eu lieu : sa raison se consigne (lib.sh relecture-note --raison).\n' >&2
    exit 4
  fi
  mkdir -p "$RACINE/$DOSSIER" 2>/dev/null

  if [ "$MODE" = "saisine" ]; then
    SAISINE="$DOSSIER/saisine.md"
    ecris_saisine "$IID" "$PAIRES" "$RACINE/$SAISINE" || exit 1
    printf 'Saisine de la relecture — ticket #%s\n\n' "$IID"
    printf '  régime     : %s — %s\n' "$REGIME" "$(origine_regime)"
    printf '  captures   : %s — %s paire(s) sur %s en portent au moins une (écran × état × thème)\n' \
      "$NB_CAPTURES" "$NB_PAIRES_VUES" "$NB_PAIRES"
    if grep -q "^Le ticket n'a pas pu être lu" "$RACINE/$SAISINE"; then
      printf '  ⚠ attente  : ticket illisible — rendu attendu et décisions iront à « non vu »\n'
    else
      printf '  attente    : rendu attendu et décisions lus sur le ticket (lib.sh relecture-attente)\n'
    fi
    [ -n "$PARTIS_PRIS" ] && printf '  partis pris: %s, recopié tel quel\n' "$PARTIS_PRIS"
    printf '  grille     : scripts/design/grille-relecture.tsv\n'
    if [ "$REGIME" = "applique" ]; then
      # #1151 : un ticket qui applique est jugé par la session, sur la même grille — sans sous-agent.
      printf '  ensuite    : la session remplit elle-même le gabarit, sous « ### Regard de la session » :\n'
    else
      printf '  ensuite    : sous-agent « regard-neuf », dont le prompt est ce seul chemin :\n'
    fi
    printf 'SAISINE %s/%s\n' "$(racine_native)" "$SAISINE"
    exit 0
  fi

  # La planche : les paires sur le disque, puis `planche.py` depuis la racine — les chemins restent
  # RELATIFS de bout en bout, ce qui évite de traduire `/e/…` pour un Python Windows.
  printf '%s\n' "$PAIRES" >"$RACINE/$DOSSIER/paires.tsv"
  if ! PYTHON="$(python_du_depot)"; then
    printf 'relecture-visuelle.sh : aucun interpréteur Python — pas de planche.\n' >&2
    exit 1
  fi
  # PYTHONIOENCODING sur l'interpréteur lui-même : sous Windows sa sortie serait en cp1252, et ses
  # « ⚠ » arriveraient en mojibake (même piège que #141, dans l'autre sens).
  if ! (cd "$RACINE" && PYTHONIOENCODING=utf-8 "$PYTHON" scripts/design/planche.py --iid "$IID" \
    --paires "$DOSSIER/paires.tsv" --dossier "$DOSSIER" --sortie "$DOSSIER/planche.html"); then
    printf 'relecture-visuelle.sh : la planche n'\''a pas pu être écrite.\n' >&2
    exit 1
  fi
  # ELLE SURVIT AU WORKTREE. `/ticket-finish` ramasse le worktree juste après le merge, avant son
  # résumé : une planche nommée là, mais laissée ici, serait un lien mort au moment où on le lit. Elle
  # est donc recopiée sous le `.maestro/relecture/` du CLONE PRINCIPAL — le parent du répertoire git
  # commun, comme `worktree.sh` le trouve —, et c'est ce chemin-là que la dernière ligne rend. Un
  # seul fichier suffit : les captures y sont en `data:`. Best-effort : une copie ratée laisse la
  # planche du worktree, et le dit.
  PLANCHE="$DOSSIER/planche.html"
  COMMUN="$(git -C "$RACINE" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)"
  PRINCIPAL=""
  [ -n "$COMMUN" ] && PRINCIPAL="$(cd "$(dirname "$COMMUN")" 2>/dev/null && pwd)"
  if [ -n "$PRINCIPAL" ] && [ "$PRINCIPAL" != "$(cd "$RACINE" && pwd)" ]; then
    if mkdir -p "$PRINCIPAL/$DOSSIER" 2>/dev/null \
      && cp "$RACINE/$PLANCHE" "$PRINCIPAL/$PLANCHE" 2>/dev/null; then
      printf '  copie de travail : %s (ramassée avec le worktree)\n' "$PLANCHE"
      if command -v cygpath >/dev/null 2>&1; then PRINCIPAL="$(cygpath -m "$PRINCIPAL")"; fi
      printf 'PLANCHE %s/%s\n' "$PRINCIPAL" "$PLANCHE"
      exit 0
    fi
    printf '  ⚠ copie vers le clone principal impossible : la planche partira avec le worktree.\n' >&2
  fi
  printf 'PLANCHE %s/%s\n' "$(racine_native)" "$PLANCHE"
  exit 0
fi

if [ "$TSV" = 1 ]; then
  # `avant` en DERNIÈRE colonne : les quatre premières sont un contrat qu'un appelant lit par leur rang.
  printf '# route\turl\torigine\tfichiers\tavant\n'
  # Les états de la vraie stack, et ceux qu'elle ne produit pas, en commentaire : un appelant machine
  # les lit, et un lecteur de TSV qui ignore les `#` n'y voit rien de changé.
  printf '# etats\t%s\n' "$ETATS"
  [ -n "$REGIME" ] && printf '# regime\t%s\t%s\n' "$REGIME" "$(origine_regime)"
  while IFS=$'\t' read -r nom raison; do
    [ -n "$nom" ] && printf '# non-couvert\t%s\t%s\n' "$nom" "$raison"
  done <<<"$NON_COUVERTS"
  while IFS=$'\t' read -r route _cle origine fichiers; do
    [ -z "$route" ] && continue
    if [ "$route" = "-" ]; then
      printf -- '-\t-\t%s\t%s\t-\n' "$origine" "$fichiers"
    else
      printf '%s\thttp://localhost:%s%s\t%s\t%s\t%s\n' \
        "$route" "$PORT_UI" "$route" "$origine" "$fichiers" "$(avant_de "$route")"
    fi
  done <<<"$BRUT"
else
  affiche_plan "$IID" "$LIGNES" "$NB" "$INDET"
fi

# Un ticket sans surface visible n'est pas une panne : c'est la réponse, et elle vaut `3` — le même code
# que l'abstention de `current-milestone` ou de `touche-surface`. L'appelant qui enchaînerait une stack
# sur un plan vide paierait une minute pour ne rien voir.
if [ "$NB" -eq 0 ]; then
  [ "$MODE" = "preparer" ] && dire "" && dire "  rien à monter : la stack n'est pas démarrée."
  exit 3
fi

[ "$MODE" = "plan" ] && exit 0

# --- Préparation ------------------------------------------------------------------------------------
dire ""
CAPTURES="$(dossier_captures "$IID" "$ETAT")"
mkdir -p "$RACINE/$CAPTURES" 2>/dev/null
LANCEUR="$RACINE/scripts/controltower/start.sh"

# « injoignable » ne monte rien : il COUPE l'API de la stack que cette relecture a montée, et laisse
# l'UI servie (en-tête, §5). Sans stack montée, il n'y aurait rien à voir tomber.
if [ "$ETAT" = "injoignable" ]; then
  if [ ! -f "$TEMOIN_ETAT" ]; then
    dire "  ✗ aucune stack montée par cette relecture : « injoignable » coupe l'API d'une stack servie."
    dire "    D'abord un état (bash scripts/design/relecture-visuelle.sh $IID), les écrans ouverts, puis celui-ci."
    exit 1
  fi
  IFS=$'\t' read -r _iid ETAT_MONTE <"$TEMOIN_ETAT"
  dire "  panne      : l'API coupée sous l'état « ${ETAT_MONTE:-?} » — l'UI reste servie, rien n'est soldé"
  MAESTRO_PORT_API="$PORT_API" MAESTRO_PORT_UI="$PORT_UI" bash "$LANCEUR" --couper-api | sed 's/^/    /'
  consigne_regime
  if [ -f "$TEMOIN_AVANT" ]; then
    IFS=$'\t' read -r _iid _chemin api_avant ui_avant <"$TEMOIN_AVANT"
    if [ -n "$api_avant" ] && [ -n "$ui_avant" ]; then
      MAESTRO_PORT_API="$api_avant" MAESTRO_PORT_UI="$ui_avant" bash "$LANCEUR" --couper-api \
        | sed 's/^/    [avant] /'
    fi
  fi
  dire ""
  dire "    à faire ensuite — un écran ouvert AVANT la coupure montre sa panne : passer d'un écran à"
  dire "    l'autre PAR LE MENU. Une navigation par l'URL recharge la page, et le shell, sans API pour"
  dire "    confirmer son projet, reste sur sa porte — qui montre sa propre panne, à capturer une fois."
  dire "    Captures sous $CAPTURES/ ; remonter un état (bash scripts/design/relecture-visuelle.sh $IID)"
  dire "    rétablit l'API, et pour finir :"
  dire "        bash scripts/design/relecture-visuelle.sh --couverture $IID"
  dire "        bash scripts/design/relecture-visuelle.sh --fin"
  exit 0
fi

# La vraie stack, sur le jeu de données du banc (#1164) : l'état du dernier passage rouvert, ou une
# stack neuve. `--no-browser` est obligatoire : sans lui le lanceur ouvre sa propre fenêtre et arrête
# la stack dès qu'elle se ferme (#149), ce qui couperait l'API sous le navigateur qu'on pilote. La
# sortie du lanceur passe telle quelle : c'est elle qui dit l'âge de l'état du banc.
read -r -a ARGS_ETAT <<<"$(options_start "$ETAT")"
dire "  stack      : démarrage de la vraie stack, état « $ETAT » — $(description_etat "$ETAT")"
dire "               (sans navigateur ; le premier passage construit l'UI)"
code=0
monte "$LANCEUR" "$PORT_API" "$PORT_UI" "${ARGS_ETAT[@]}" --no-browser || code=$?
if [ "$code" -eq 0 ]; then
  mkdir -p "$(dirname "$TEMOIN_ETAT")" 2>/dev/null
  printf '%s\t%s\n' "$IID" "$ETAT" >"$TEMOIN_ETAT"
  consigne_regime
  dire ""
  dire "  ✓ prête : http://localhost:$PORT_UI — état « $ETAT »"
  case "$ETAT" in
    vide) declare_projet_neuf "$PORT_API" "après" ;;
    *) annonce_projets "$PORT_API" "après" ;;
  esac
  prepare_avant
  dire ""
  if [ "$REGIME" = "applique" ]; then
    dire "    à faire ensuite — poser le localStorage (guide vu, thème, projet actif ci-dessus), ouvrir"
    dire "    chaque écran dans les deux thèmes, capturer l'après sous $CAPTURES/ — un autre état"
    dire "    (--etat <nom>) seulement si la rubrique « États à couvrir » du ticket le nomme —, et pour finir :"
  else
    dire "    à faire ensuite — poser le localStorage (guide vu, thème, projet actif ci-dessus) SUR CHAQUE"
    dire "    ORIGINE servie, ouvrir chaque écran dans les deux thèmes, capturer l'après et l'avant côte à"
    dire "    côte sous $CAPTURES/, puis l'état suivant (--etat <nom>), et pour finir :"
  fi
  dire "        bash scripts/design/relecture-visuelle.sh --couverture $IID"
  dire "        bash scripts/design/relecture-visuelle.sh --fin"
  exit 0
fi

dire ""
if [ "$ETAT" = "peuple" ] && [ "$code" -eq 4 ]; then
  # Aucun passage n'a laissé d'état sur ce poste : l'en jouer un coûte du vrai modèle (des dizaines de
  # minutes), ce qui ne se décide pas au détour d'une relecture — on le nomme, et on s'arrête.
  dire "  ✗ état « peuple » non servi : aucun passage du banc n'a laissé d'état sur ce poste."
  dire "    En jouer un est une demande, jamais un geste d'office (vrai modèle, des dizaines de minutes) :"
  dire "        bash scripts/controltower/start.sh --etat-banc --rejouer"
  dire "    Les autres états restent montables (--etat vide) ; celui-ci va à « ce que je n'ai pas pu voir »."
else
  dire "  ✗ la stack n'a pas démarré (code $code) — journaux sous .maestro/controltower/$PORT_API-$PORT_UI/"
fi
dire "    rien n'est laissé derrière : la stack est arrêtée."
MAESTRO_PORT_API="$PORT_API" MAESTRO_PORT_UI="$PORT_UI" bash "$LANCEUR" --stop >/dev/null 2>&1
rm -f "$TEMOIN_ETAT"
exit 1

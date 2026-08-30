#!/usr/bin/env bash
# L'écart run ↔ interactif, MESURÉ plutôt que décrit de mémoire (#789, parent #788).
#
#   bash scripts/orchestrate/ecart-run.sh            # le rapport, en clair
#   bash scripts/orchestrate/ecart-run.sh --tsv      # le même inventaire, lisible par un script
#   bash scripts/orchestrate/ecart-run.sh --regles <json>…   # sur d'AUTRES règles (voir plus bas)
#
# --- Ce que ce verbe existe pour empêcher -----------------------------------------------------------
# Le parent nomme cinq écarts (G1→G5) à partir d'une lecture faite à la main. Une lecture à la main
# se périme au premier ticket qui touche `settings.run.json` — et les lots 2 à 5 de #788 vont
# précisément le toucher. La question « qu'est-ce qu'un run ne peut pas faire ? » doit donc avoir
# une réponse qu'on REJOUE, au même titre que `guard.sh --check` répond à « le `deny` du run a-t-il
# dérivé de celui du dépôt ? ». L'étalon est le banc de #614
# (`scripts/claude/essai-ecriture-claude.py`) : un script versionné qui mesure au lieu de raisonner
# à distance, et dont le verdict se relit un mois plus tard.
#
# --- Le constat que le rapport rend lisible ---------------------------------------------------------
# L'union des deux allowlists fait 86 règles, et la tentation est de croire que l'écart se comble en
# l'allongeant. Il ne s'y comble pas :
#
#     Interactif = `allow` + UNE PERSONNE QUI APPROUVE TOUT LE RESTE.  Run = `allow`, point final.
#
# La même liste ne décrit donc pas le même objet — en interactif elle ne retire que de la friction,
# en run elle définit la frontière. C'est pour cela que la PREMIÈRE question du rapport porte sur le
# bloc `ask` et non sur le bloc `allow` : un `ask` est un refus sec dès qu'il n'y a personne, et
# c'est l'écart qu'aucune lecture de `allow` ne montre, parce qu'il vit dans un autre bloc.
#
# --- Trois questions, et c'est l'ordre qui porte le sens (comme la taxonomie de #307) ---------------
#   Q1  ce que le dépôt met en `ask`        — approuvable en interactif, refusé sec en run ;
#   Q2  ce qu'aucun `allow` ne couvre       — geste par geste, dont `WebSearch`/`WebFetch` ;
#   Q3  ce que le CLI refuse EN AMONT       — l'écriture sous `.claude/`, mesurée et non déduite.
#
# Et il DISTINGUE l'écart de l'interdit voulu : `merge-mr`/`pipeline-wait` (G5) et les refus mérités
# de #307/#528 sortent avec leur raison, jamais dans la colonne « manquant ». Un rapport qui les y
# rangerait enverrait quelqu'un « corriger » ce qui est juste.
#
# --- Trois choses à ne pas défaire -------------------------------------------------------------------
#  1. LES RÈGLES SE LISENT LÀ OÙ ELLES VIVENT (`.claude/settings.json` ∪ `settings.run.json`),
#     jamais dans une copie — règle que `journal.sh refus` s'est donnée en #307. Une copie dériverait
#     en silence pendant que les lots 2 à 5 modifient les originaux, et le rapport rendrait
#     « aucun écart » sur une question jamais posée. C'est `permissions.sh` qui lit, et le matching
#     vit dans `permissions.awk`, partagé avec `journal.sh` : deux formules divergentes finiraient
#     par ne plus rendre le même verdict sur la même règle.
#  2. LE MOTIF SE PROUVE SUR UN ÉCHANTILLON FAUTIF AVANT DE BALAYER (règle de #534/#537). C'est ce
#     que sert `--regles` : passer un jeu de règles FABRIQUÉ — une allowlist qui couvre tout, une
#     qui ne couvre rien — et vérifier que le verdict bascule. Sans cette épreuve, « aucun écart »
#     est indiscernable d'un rapport mal branché, et c'est le pire des verdicts parce qu'il rassure.
#     Le défaut, lui, lit toujours le dépôt : l'option sert l'épreuve, jamais le rapport.
#  3. COMPTER, JAMAIS CHRONOMÉTRER (règle de #577/#602). Ce rapport compte des gestes et des règles.
#     Il ne mesure aucune durée, n'ouvre aucune socket et n'écrit aucun fichier.
#
# --- Ce que ce verbe ne fait pas ---------------------------------------------------------------------
# Il ne TRANCHE pas. Les cinq `ask` sans répondant et le geste que #788 range parmi les trous sans
# que sa forme couverte soit tranchée sortent en « à arbitrer », qui est le travail du lot 2 (#790) ;
# le régime de permission face à `.claude/` est celui du lot 3 (#791) ; l'accès web celui du lot 4
# (#792) ; la survie d'une question celui du lot 5 (#795). Un inventaire qui déciderait à leur place
# rendrait leur arbitrage sans objet — et l'aurait rendu sans que personne le juge.
#
# Codes de sortie — ils portent sur CE QU'UNE RÈGLE PEUT CHANGER (Q1 et Q2), jamais sur Q3 :
#   0  inventaire rendu, aucun écart imputable aux listes — l'état que #788 vise, pas celui
#      d'aujourd'hui ;
#   3  inventaire rendu, des écarts subsistent (dont ceux « à arbitrer ») ;
#   1  les règles n'ont pas pu être lues — surtout pas un « aucun écart » rassurant.
#
# POURQUOI Q3 NE COMPTE PAS. Le blocage `.claude/` ne se comble par AUCUNE règle : il est en amont
# des deux listes, et ce qui le lèverait est une décision de politique (le lot 3), pas une ligne
# ajoutée quelque part. L'y compter rendrait le code CONSTANT, et un code qui ne varie jamais
# n'apprend rien — ni à un humain, ni au test qui garde ce verbe. C'est le partage même que le
# parent pose : ce qui se comble par une règle (G1), ce qui se comble par un arbitrage (G2, G3), ce
# qui se comble en différant la question (G4). Q3 sort donc en « constat », et le rapport le rend
# quel que soit le code.

set -uo pipefail

ICI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RACINE="$(cd "$ICI/../.." && pwd)"
# shellcheck source=scripts/orchestrate/permissions.sh
. "$ICI/permissions.sh"

BANC="$RACINE/scripts/claude/essai-ecriture-claude.py"
LIB="$RACINE/scripts/gitlab/lib.sh"

usage() {
  cat <<'USAGE'
L'écart entre une session de run et une session interactive — inventaire rejouable (#789).

  bash scripts/orchestrate/ecart-run.sh                  Le rapport, en clair.
  bash scripts/orchestrate/ecart-run.sh --tsv            Le même inventaire, en TSV : « question,
                                                         verdict, geste, origine, détail,
                                                         contradiction », séparés par des tabulations.
  bash scripts/orchestrate/ecart-run.sh --regles <json>  Lire les règles AILLEURS que dans le dépôt
                                                         (répétable). Sert à prouver le motif sur un
                                                         échantillon fautif — pas à produire un rapport.
  bash scripts/orchestrate/ecart-run.sh -h | --help      Cette aide.

Sans réseau, sans écriture. Codes : 0 aucun écart imputable aux listes · 3 des écarts subsistent ·
1 règles illisibles. Q3 (le blocage « .claude/ ») est un CONSTAT hors du compte : aucune règle ne
le comble, et l'y ranger rendrait le code constant.

L'ÉPREUVE DU MOTIF, à rejouer avant de croire un « aucun écart » (deux échantillons versionnés) :

  bash scripts/orchestrate/ecart-run.sh --regles tests/fixtures/ecart_run/tout-couvert.json --tsv
      tout passe en « couvert », code 0 — et les huit refus MÉRITÉS ressortent en contradiction
      (dernière colonne) : une règle qui bénirait « rm » ou « merge-mr » se voit, elle ne s'avale pas.

  bash scripts/orchestrate/ecart-run.sh --regles tests/fixtures/ecart_run/rien-couvert.json --tsv
      rien n'est couvert, code 3 : les écarts restent des écarts et les refus mérités sortent par
      leur RAISON ÉCRITE — la preuve que la classe déclarée n'est qu'un repli, jamais le verdict.

  bash scripts/orchestrate/ecart-run.sh --regles tests/fixtures/ecart_run/ask-contre-deny.json
      « pwd » mis en `ask` sort en ÉCART, « rm » mis en `deny` sort en VOULU : la distinction que
      ce verbe porte et que `journal.sh refus` a raison de ne pas faire (voir le BEGIN du awk).
USAGE
}

# --- Q1 : ce qu'un `ask` empêche, quand personne n'est là pour répondre -----------------------------
# Le bloc `ask` est LU dans le dépôt — c'est lui qui fait foi, et une règle nouvelle sortira même
# sans entrée ici. Ce catalogue n'ajoute que la conséquence, qui ne se déduit d'aucune règle : ce
# qu'un run ne peut donc pas faire. Une note manquante rend « — », jamais un silence.
NOTES_ASK=$(
  cat <<'TSV'
Bash(gh issue close:*)	abandonner un ticket — /ticket-abandon est hors de portée d'un run. Mesuré : refusé à la session de #273 (« gh issue close 663 --reason not planned »).
Bash(git commit --no-verify:*)	committer malgré le hook commit-msg. L'interdit est juste ; ce qui manque est quelqu'un pour juger le cas légitime.
Bash(git reset --hard:*)	repartir d'un arbre propre après une manœuvre ratée. Le run n'a que « git restore », que le prompt lui indique.
Bash(git clean:*)	retirer les fichiers non suivis d'un essai — le worktree les garde jusqu'à son ramassage.
mcp__chrome-maestro__browser_run_code_unsafe	exécuter du JS dans la page pendant une vérification de bout en bout.
TSV
)

# --- Q2 : les gestes confrontés à l'union des `allow` -----------------------------------------------
# Colonnes : classe déclarée <TAB> type <TAB> geste <TAB> origine <TAB> détail.
#
# La CLASSE DÉCLARÉE n'est qu'un repli : le verbe interroge d'abord les règles. Un geste couvert par
# `deny` sort « voulu » en citant la règle ; par `ask`, il sort « ecart » (approuvable en interactif,
# refusé sec en run) ; par `allow`, « couvert ». La classe écrite ici ne sert que lorsque AUCUNE
# règle ne parle — c'est-à-dire là où seule une raison écrite peut distinguer un trou d'un refus
# mérité. Et si elle contredit les règles, le rapport le dit plutôt que de choisir en silence.
#
#   ecart     rien ne le couvre et il devrait l'être — un humain l'approuverait au prompt ;
#   voulu     rien ne le couvre et c'est le but, la raison est écrite et la forme couverte existe ;
#   arbitrer  #788 le range parmi les trous, mais une règle déjà tranchée s'y applique. Le lot 2.
#
# Les comptes « mesurés » viennent de `journal.sh refus --tous` (27 sessions, 40 refus, 2026-08-29).
# Ils datent la ligne ; ils ne la fondent pas — c'est la confrontation aux règles qui tranche, et
# c'est elle qui rejouera toute seule quand le lot 2 aura élargi la liste.
GESTES=$(
  cat <<'TSV'
voulu	outil	WebSearch	#788 G3 · #714 · tranché au lot 4 (#792)	Confirmé fermé : une veille rend des PARTIS PRIS — un jugement, laissé à un humain comme l'arbitrage de #562 et le rail de #617 — et « chrome-maestro » passant déjà l'union, ouvrir la seule recherche donnerait une veille à moitié. Jamais demandé : 0 refus sur 56. Forme couverte : la question SURVIT au run (lot 5, #795), un humain joue /design-veille ensuite.
voulu	outil	WebFetch	#788 G3 · mesuré 1× (#271) · tranché au lot 4 (#792)	Fermé aussi, mais par une raison qui lui est PROPRE : #714 le rangeait sous la veille, or le seul usage mesuré n'en est pas une — lire une référence citée par le ticket. « L'URL vient d'un humain » n'est pas exprimable dans une règle, qui ne borne qu'un préfixe (raison de curl, #528), et le produit d'un run est mergé sans relecture (#418/#419). Forme couverte : référence versionnée, ou porte d'admission humaine (#678) — ce que #271 a fini par faire.
ecart	bash	pwd	mesuré 3× (#484, #695, #696)	Lecture pure. Le premier geste après un « cd » dont on doute.
ecart	bash	cut -f2	mesuré 1×	Découper une ligne TSV — le pendant de « awk »/« sed », déjà autorisés.
ecart	bash	tr -d ' '	mesuré 1×	Lecture pure, bornée au tube.
ecart	bash	chmod +x scripts/x.sh	mesuré 1×	Rendre exécutable un script qu'on vient d'écrire ; borné au worktree.
ecart	bash	git mv a b	mesuré 1×	Renommer un fichier suivi, quand « git add » et « git rm » sont autorisés.
ecart	bash	git merge-base main HEAD	mesuré 1×	Lecture pure de l'historique.
ecart	bash	git check-ignore -v x	mesuré 1×	Lecture pure du .gitignore.
arbitrer	bash	python -c 'print(1)'	#788 G1 · mesuré 1×	#788 le range parmi les trous, mais #528 a tranché « python » nu pour une raison qui vaut ici : ce n'est pas le venv du dépôt, que CLAUDE.md impose. La forme couverte existe déjà — « .venv/Scripts/python.exe -c … ». À trancher au lot 2.
voulu	bash	for f in a b; do echo $f; done	#528	Une règle est un préfixe de COMMANDE, une tête de boucle n'en est pas une : « Bash(for:*) » bénirait la forme sans rien juger de ce qu'elle répète. Forme couverte : une commande qui prend la liste, ou un appel par élément.
voulu	bash	curl -s http://x/y	#528	Le pouvoir de curl est dans son ARGUMENT, là où une règle borne un préfixe — et c'est la seule forme capable d'envoyer le worktree hors de la machine. Forme couverte : « node -e "fetch(…)" » en boucle locale.
voulu	bash	python - <<'PY'	#528	Deux fois refusé plutôt qu'une : heredoc (immatchable par construction) et « python » nu hors venv. Forme couverte : Write dans .maestro/session/ puis .venv/Scripts/python.exe <fichier>.
voulu	bash	rm -rf build	settings.run.json	Destructif, et aucune règle de préfixe ne borne sa cible : « Bash(rm:*) » autoriserait « rm -rf » n'importe où. Réécrire un fichier vaut le nettoyer, et le scratchpad est jetable.
voulu	bash	bash /c/tmp/x.sh	settings.run.json	« Bash(bash:*) » ferait sauter la borne des règles « Bash(bash scripts/…) », qui limite l'interpréteur aux scripts versionnés. Forme couverte : le chemin relatif, depuis le worktree.
voulu	bash	PYTHONPATH=. .venv/Scripts/python.exe x.py	#307	Une règle est un préfixe de commande, or la commande commence par la variable : la figer en dur ne couvrirait que cette valeur-là. Forme couverte : « env VAR=… <commande> », que Bash(env:*) couvre.
voulu	bash	bash scripts/gitlab/lib.sh merge-mr 42	#788 G5 · #419	Le merge appartient AU PILOTE : N sessions qui mergent en parallèle périment mutuellement leur verdict de conflit. La parité serait ici une régression.
voulu	bash	bash scripts/gitlab/lib.sh pipeline-wait main	#788 G5 · #419	Attendre un pipeline dans une session, c'est brûler du quota à ne rien faire en tenant un worktree et un créneau de concurrence. Le pilote attend, hors quota.
TSV
)

# --- G4 : les questions qu'un run rencontre et que personne ne reverra -------------------------------
# Ce n'est pas une affaire de règles, donc rien ici ne se confronte à une allowlist. Ce qui SE
# vérifie, et c'est tout l'objet de la colonne « survie », est l'existence d'un verbe qui fasse
# survivre la question au run — le précédent qui marche étant `reste-claude` (#610), né du même
# constat sur le résidu `.claude/`. Colonnes : question <TAB> où elle se pose <TAB> verbe de survie.
QUESTIONS=$(
  cat <<'TSV'
veille de conception (« qu'est-ce qu'on vise ? »)	/ticket-start étape 5 · #714
reprise d'un ticket orphelin	/orchestrate, feu vert · #327
choix du milestone du run	/orchestrate, feu vert · §11.2
arbitrage lot::arbitre d'un parent	/orchestrate, feu vert · #562
correctif resté sous .claude/	session de run · #608	reste-claude
TSV
)

# --- Lecture des règles -----------------------------------------------------------------------------
# « bloc <TAB> origine <TAB> règle », origine étant l'étiquette courte du fichier — c'est elle qui
# permet de CITER la règle qui couvre un geste, et de dire d'où elle vient.
FICHIERS=()
etiquette() {
  case "$1" in
    *"/.claude/settings.json") printf 'dépôt' ;;
    *"/settings.run.json") printf 'run' ;;
    *) printf '%s' "$(basename "$1")" ;;
  esac
}

regles_lues() {
  local f bloc
  for f in "${FICHIERS[@]}"; do
    for bloc in allow ask deny; do
      perm_bloc "$f" "$bloc" | sed "s/\t/\t$(etiquette "$f")\t/"
    done
  done
}

# --- Le classement, en awk : le matching est celui de `permissions.awk` -----------------------------
# Une seule invocation pour tout le catalogue plutôt qu'une par ligne : le verdict ne dépend d'aucun
# ordre, et N forks pour N gestes seraient N forks de trop sous MSYS (#577).
AWK_CLASSE=$(
  cat <<'AWK'
BEGIN {
  FS = "\t"; OFS = "\t"
  # Les règles arrivent par un FICHIER et non par `-v` : awk applique ses séquences d'échappement
  # aux valeurs de `-v`, et un antislash ajouté un jour à une règle y serait mangé en silence.
  # TROIS jeux et non deux, là où `journal.sh refus` n'en fait que deux — et l'écart est le sujet
  # même de ce verbe. Pour classer un refus DÉJÀ SURVENU, `ask` et `deny` disent la même chose :
  # personne n'était là pour répondre. Pour dire ce qu'un run NE PEUT PAS FAIRE, ils disent
  # l'inverse l'un de l'autre — un `deny` est un interdit des deux côtés, donc pas un écart ; un
  # `ask` est approuvable en interactif, donc l'écart lui-même. Les confondre ici rangerait G1 tout
  # entier parmi les interdits voulus, c'est-à-dire hors de ce que le chantier vient corriger.
  while ((getline ligne < regles) > 0) {
    if (ligne == "") continue
    n = split(ligne, ch, "\t")
    if (n < 3) continue
    if (ch[1] == "allow")     { allow[++n_allow] = ch[3]; a_ou[n_allow] = ch[2] }
    else if (ch[1] == "deny") { deny[++n_deny] = ch[3];   d_ou[n_deny] = ch[2] }
    else                      { ask[++n_ask] = ch[3];     k_ou[n_ask] = ch[2] }
  }
  lues = n_allow + n_deny + n_ask
}

# regle_qui_couvre(...) : LAQUELLE des règles couvre ce geste ? On rejoue `matche` sur une règle
# ISOLÉE plutôt que d'écrire une seconde fonction qui rendrait la règle — deux formules pour la
# même question finiraient par ne plus rendre le même verdict.
function regle_qui_couvre(geste, type, regles, origines, n, large,   i, un) {
  for (i = 1; i <= n; i++) {
    un[1] = regles[i]
    if (type == "outil" ? outil_couvert(geste, un, 1) : matche(geste, un, 1, large))
      return regles[i] "\t" origines[i]
  }
  return ""
}

NF >= 3 {
  classe = $1; type = $2; geste = $3; origine = $4; detail = $5
  # L'ORDRE DE DÉCISION est le contenu du classement, comme dans la taxonomie de #307 : le bloc le
  # plus restrictif décide, même si un `allow` couvre le geste par ailleurs — c'est exactement le
  # cas de `lib.sh merge-mr`, que `Bash(bash scripts/gitlab/lib.sh:*)` couvre et que le `deny` du
  # run reprend. `deny` avant `ask` : les deux refusent en run, seul le second était approuvable.
  r = regle_qui_couvre(geste, type, deny, d_ou, n_deny, 1)
  if (r != "") { verdict = "voulu"; par = r; pourquoi = "règle" }
  else {
    r = regle_qui_couvre(geste, type, ask, k_ou, n_ask, 1)
    if (r != "") { verdict = "ecart"; par = r; pourquoi = "règle ask" }
    else {
      r = regle_qui_couvre(geste, type, allow, a_ou, n_allow, 0)
      if (r != "") { verdict = "couvert"; par = r; pourquoi = "règle" }
      else { verdict = classe; par = "\t"; pourquoi = "raison écrite" }
    }
  }
  # La classe déclarée qui contredit les règles est SIGNALÉE et jamais tranchée en silence : un
  # geste écrit « voulu » que l'`allow` couvre est une contradiction à lever, pas un détail de
  # rendu — c'est le premier symptôme d'une raison écrite qui a survécu à la règle qui la portait.
  # UNE SEULE divergence n'en est pas une : un « ecart » devenu « couvert » est exactement ce que
  # le chantier vise, et le signaler apprendrait à ne plus lire les signalements.
  contradiction = ""
  if (pourquoi != "raison écrite" && verdict != classe && !(classe == "ecart" && verdict == "couvert"))
    contradiction = classe
  print verdict, type, geste, origine, detail, par, pourquoi, contradiction
}

END { if (lues == 0) exit 9 }
AWK
)

MODE=clair
while [ "$#" -gt 0 ]; do
  case "$1" in
    -h | --help)
      usage
      exit 0
      ;;
    --tsv)
      MODE=tsv
      shift
      ;;
    --regles)
      [ "$#" -ge 2 ] || {
        printf 'ecart-run.sh : --regles attend un fichier.\n' >&2
        exit 2
      }
      FICHIERS+=("$2")
      shift 2
      ;;
    *)
      printf 'Option inconnue : %s\n\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done
# Sans `--regles`, les règles du DÉPÔT — le rapport lit toujours là où elles vivent.
[ "${#FICHIERS[@]}" -gt 0 ] || mapfile -t FICHIERS < <(perm_fichiers)

# Le corpus de règles va dans le temporaire du SYSTÈME et non sous `.maestro/` : c'est un brouillon
# de calcul que personne n'ouvre, et la règle de docs/10 §8.5 (#234) ne vise que ce qu'un script
# INVITE À LIRE. Ce que ce verbe invite à lire, lui, est sa propre sortie.
TMP="$(mktemp "${TMPDIR:-/tmp}/maestro-ecart.XXXXXX" 2>/dev/null)" || TMP=""
[ -n "$TMP" ] || {
  printf 'ecart-run.sh : impossible de préparer la lecture des règles.\n' >&2
  exit 1
}
trap 'rm -f "$TMP" "$TMP.q1" "$TMP.q2" 2>/dev/null' EXIT
regles_lues > "$TMP"

# Un corpus vide n'est PAS « aucun écart » : c'est une lecture ratée, et le dire est tout l'objet
# du code 1 (voir l'en-tête, point 2 des choses à ne pas défaire).
if ! [ -s "$TMP" ]; then
  printf 'ecart-run.sh : aucune règle lue dans %s — pas de verdict.\n' "${FICHIERS[*]}" >&2
  exit 1
fi

classe() { LC_ALL=C awk -v regles="$TMP" "$(perm_awk)$AWK_CLASSE"; }

# Q1 se DÉRIVE des règles `ask` réellement présentes, jamais d'un catalogue : une sixième règle
# ajoutée demain doit sortir ici sans qu'on y pense. Le catalogue n'ajoute que la conséquence.
awk -F'\t' -v notes="$NOTES_ASK" '
  BEGIN { n = split(notes, l, "\n"); for (i = 1; i <= n; i++) { p = index(l[i], "\t"); if (p) note[substr(l[i], 1, p - 1)] = substr(l[i], p + 1) } }
  $1 == "ask" { print $3 "\t" $2 "\t" (($3 in note) ? note[$3] : "—") }
' "$TMP" | sort -u > "$TMP.q1"

printf '%s\n' "$GESTES" | classe > "$TMP.q2"
code_awk=$?
if [ "$code_awk" -eq 9 ]; then
  printf 'ecart-run.sh : aucune règle exploitable — pas de verdict.\n' >&2
  exit 1
fi

# --- Q3 : ce qui se vérifie ici, sans réseau ---------------------------------------------------------
# Le blocage `.claude/` ne se lit dans aucune règle : c'est le CLI qui refuse, en amont des deux
# listes. Ce qui se vérifie sur disque, en revanche, ce sont les APPUIS de cette conclusion — le banc
# qui la mesure, et le support qui fait survivre le résidu qu'elle laisse. Une citation dont le banc
# aurait disparu serait orpheline, et c'est cela qu'on refuse de laisser passer.
regles_claude="$(awk -F'\t' '$1 == "allow" && $3 ~ /\.claude/ { print $3 " [" $2 "]" }' "$TMP")"
nb_ecart="$(awk -F'\t' '$1 == "ecart" { n++ } END { print n + 0 }' "$TMP.q2")"
nb_arbitrer="$(awk -F'\t' '$1 == "arbitrer" { n++ } END { print n + 0 }' "$TMP.q2")"
nb_ask="$(wc -l < "$TMP.q1" | tr -d ' ')"

if [ "$MODE" = tsv ]; then
  # Une colonne de plus qu'en clair : la CONTRADICTION (un geste déclaré « voulu » que les règles
  # couvrent). La taire ici ferait du mode machine une vue plus pauvre que le rapport sur le seul
  # point où le catalogue et les règles se désavouent — c'est-à-dire là où il faut regarder.
  awk -F'\t' '{ print "Q1\tecart\t" $1 "\task [" $2 "]\t" $3 "\t" }' "$TMP.q1"
  # $6/$7 portent la règle qui couvre et son origine (la colonne « par » est elle-même tabulée),
  # $8 dit d'où vient le verdict, $9 la contradiction — c'est celle-là qu'on rend.
  awk -F'\t' '{ print "Q2\t" $1 "\t" $3 "\t" $4 "\t" $5 "\t" $9 }' "$TMP.q2"
  printf 'Q3\t%s\t%s\t%s\t%s\t\n' \
    "$([ -f "$BANC" ] && printf 'constat' || printf 'inconnu')" \
    "écriture sous .claude/" \
    "#614 · 2026-08-27" \
    "$([ -f "$BANC" ] && printf "refus du CLI en amont des deux listes — aucune règle ne le comble (lot 3) ; banc rejouable présent" || printf "banc absent — la citation de #614 n'a plus d'appui dans le dépôt")"
else
  printf 'Écart run ↔ interactif — inventaire rejouable (#789, parent #788)\n\n'
  printf '  Règles lues, là où elles vivent :\n'
  # Les chemins sont en ASCII, donc leur colonne s'aligne ; tout libellé ACCENTUÉ est laissé hors
  # d'un `%-Ns`, qui compte des octets sous une locale C et des caractères sous UTF-8 — la largeur
  # dépendrait alors du poste (leçon de `colonnes`, #325).
  for f in "${FICHIERS[@]}"; do
    printf '    %-40s %2s allow · %s ask · %s deny\n' \
      "${f#"$RACINE/"}" \
      "$(perm_bloc "$f" allow | wc -l | tr -d ' ')" \
      "$(perm_bloc "$f" ask | wc -l | tr -d ' ')" \
      "$(perm_bloc "$f" deny | wc -l | tr -d ' ')"
  done
  printf '    union, le régime réel d'\''une session de run : %s allow · %s ask · %s deny\n' \
    "$(awk -F'\t' '$1 == "allow" { print $3 }' "$TMP" | sort -u | wc -l | tr -d ' ')" \
    "$(awk -F'\t' '$1 == "ask" { print $3 }' "$TMP" | sort -u | wc -l | tr -d ' ')" \
    "$(awk -F'\t' '$1 == "deny" { print $3 }' "$TMP" | sort -u | wc -l | tr -d ' ')"
  printf '\n  Ce que ces comptes ne disent pas, et qui fait tout l'\''écart : une session interactive,\n'
  printf '  c'\''est « allow » + une personne qui approuve le reste ; une session de run, c'\''est\n'
  printf '  « allow », point final. À liste identique, un run est plus contraint (#788).\n'

  printf '\n── Q1. Ce que le dépôt met en « ask » — approuvable en interactif, refusé SEC en run\n'
  if [ "$nb_ask" -eq 0 ]; then
    printf '  Aucune règle « ask » — rien ne dépend ici d'\''un répondant absent.\n'
  else
    printf '  %s règle(s). Un « ask » sans personne pour répondre est un « deny » qui ne dit pas son nom ;\n' "$nb_ask"
    printf '  c'\''est l'\''écart qu'\''aucune lecture de « allow » ne montre, parce qu'\''il vit dans un autre bloc.\n\n'
    while IFS=$'\t' read -r regle ou note; do
      printf '  ÉCART  %s  [%s]\n' "$regle" "$ou"
      printf '         ce qu'\''un run ne peut donc pas faire : %s\n' "$note"
    done < "$TMP.q1"
    printf '\n  → à trancher au lot 2 (#790) : lever, remplacer par un geste borné, ou assumer par écrit.\n'
  fi

  printf '\n── Q2. Ce qu'\''aucun « allow » ne couvre — geste par geste\n'
  printf '  Le verdict vient des RÈGLES quand elles parlent, de la raison écrite sinon.\n\n'
  # Le TYPE (outil / bash) a servi au classement ; il n'apprend rien au lecteur du rapport, où le
  # geste se lit tout seul. La colonne est donc consommée sans être nommée.
  while IFS=$'\t' read -r verdict _ geste origine detail par ou pourquoi contradiction; do
    case "$verdict" in
      ecart) etiq='ÉCART   ' ;;
      arbitrer) etiq='ARBITRER' ;;
      voulu) etiq='VOULU   ' ;;
      *) etiq='COUVERT ' ;;
    esac
    printf '  %s %-46s %s\n' "$etiq" "$geste" "$origine"
    case "$pourquoi" in
      règle) printf '           par %s [%s]\n' "$par" "$ou" ;;
      "règle ask") printf '           par %s [%s] — approuvable en interactif, refusé sec en run\n' "$par" "$ou" ;;
    esac
    printf '           %s\n' "$detail"
    [ -z "$contradiction" ] ||
      printf '           ⚠ déclaré « %s » ici mais COUVERT par une règle — contradiction à lever.\n' "$contradiction"
  done < "$TMP.q2"

  printf '\n── Q3. Ce que le CLI refuse EN AMONT des deux listes — l'\''écriture sous « .claude/ »\n'
  printf '  Mesuré, jamais déduit : #229 l'\''avait déduit, #238 puis #614 l'\''ont mis à l'\''épreuve.\n'
  printf '  Verdict du 2026-08-27 (CLI 2.1.215) — règle nue, règle à chemin explicite (relative comme\n'
  printf '  absolue) et hook rendant « permissionDecision: allow » : TOUTES refusées. Le garde-fou est\n'
  printf '  en amont du « allow » comme des hooks. Seul « --permission-mode bypassPermissions » ouvre,\n'
  printf '  au prix de renverser la politique — le « allow » cesse alors de contraindre quoi que ce soit.\n'
  if [ -f "$BANC" ]; then
    printf '  ✓ le banc est dans le dépôt et se rejoue (~0,15 $, quelques minutes) :\n'
    printf '      .venv/Scripts/python.exe %s\n' "${BANC#"$RACINE/"}"
  else
    printf '  ⚠ le banc a disparu du dépôt (%s) : la citation ci-dessus n'\''a plus d'\''appui rejouable.\n' \
      "${BANC#"$RACINE/"}"
  fi
  if [ -n "$regles_claude" ]; then
    printf '  ⚠ une règle « allow » vise « .claude/ » — elle ne lève RIEN (#614), le refus étant en amont :\n'
    printf '%s\n' "$regles_claude" | sed 's/^/      /'
  else
    printf '  ✓ aucune règle « allow » ne vise « .claude/ » — rien ne laisse croire que la voie est ouverte.\n'
  fi
  if [ -f "$LIB" ] && grep -q 'reste-claude' "$LIB"; then
    printf '  ✓ le résidu a un support qui SURVIT au run : « lib.sh reste-claude » (#610), et le pilote\n'
    printf '    le signale en fin de run (#611). Le blocage reste entier ; c'\''est sa perte qui est traitée.\n'
  else
    printf '  ⚠ « lib.sh reste-claude » est introuvable : un correctif refusé sous « .claude/ » ne\n'
    printf '    survivrait plus au merge de sa PR (la panne de #608).\n'
  fi
  printf '  → régime de permission à arbitrer au lot 3 (#791). Mesure fraîche des refus de cette\n'
  printf '    famille : bash scripts/orchestrate/journal.sh refus --tous --claude\n'

  printf '\n── Les cinq écarts de #788, sur l'\''état actuel du dépôt\n'
  printf '  G1  ask sans répondant + trous d'\''allowlist  → %s règle(s) « ask », %s geste(s) sans règle,\n' \
    "$nb_ask" "$nb_ecart"
  printf '      %s à arbitrer. REPRODUIT.\n' "$nb_arbitrer"
  if [ "$nb_arbitrer" -gt 0 ]; then
    printf '      ⚠ diffère de #788 sur %s geste(s) : rangé(s) parmi les trous par le parent, ils tombent\n' "$nb_arbitrer"
    printf '        sous une règle déjà tranchée ailleurs. Le rapport ne choisit pas — il le dit (lot 2).\n'
  fi
  if [ -f "$BANC" ]; then
    printf '  G2  écriture sous « .claude/ »                 → REPRODUIT, et mesuré (Q3).\n'
  else
    printf '  G2  écriture sous « .claude/ »                 → INVÉRIFIABLE ICI : le banc a disparu.\n'
  fi
  web_ouvert=$(awk -F'\t' '($3 == "WebSearch" || $3 == "WebFetch") && $1 == "couvert" { n++ } END { print n + 0 }' "$TMP.q2")
  web_voulu=$(awk -F'\t' '($3 == "WebSearch" || $3 == "WebFetch") && $1 == "voulu" { n++ } END { print n + 0 }' "$TMP.q2")
  if [ "$web_ouvert" -gt 0 ]; then
    printf '  G3  WebSearch / WebFetch                      → NE SE REPRODUIT PLUS : %s des deux est couvert.\n' "$web_ouvert"
  elif [ "$web_voulu" -eq 2 ]; then
    printf '  G3  WebSearch / WebFetch                      → INTERDIT VOULU, pas un écart : tranché au lot 4\n'
    printf '      (#792). Les deux restent hors des DEUX allowlists, chacun avec SA raison et sa forme\n'
    printf '      couverte (Q2) — celle de WebFetch n%sétant pas celle de la veille.\n' "'"
  else
    printf '  G3  WebSearch / WebFetch                      → REPRODUIT : hors des DEUX allowlists.\n'
  fi
  printf '  G4  les questions sans répondant              → recensées ci-dessous.\n'
  while IFS=$'\t' read -r question ou survie; do
    [ -n "$question" ] || continue
    if [ -n "$survie" ] && [ -f "$LIB" ] && grep -q -- "$survie" "$LIB"; then
      printf '      ✓ %s — survit par « lib.sh %s »\n' "$question" "$survie"
    else
      printf '      ✗ %s — aucun support, la question meurt avec le run [%s]\n' "$question" "$ou"
    fi
  done <<EOF
$QUESTIONS
EOF
  printf '      → un support qui survit est le travail du lot 5 (#795) ; le précédent qui marche est\n'
  printf '        « reste-claude », né du même constat sur le résidu « .claude/ » (#608 → #610).\n'
  g5=$(awk -F'\t' '$3 ~ /(merge-mr|pipeline-wait)/ && $1 == "voulu" { n++ } END { print n + 0 }' "$TMP.q2")
  if [ "$g5" -eq 2 ]; then
    printf '  G5  merge-mr / pipeline-wait                  → INTERDIT VOULU, pas un écart : les deux sont\n'
    printf '      refusés par une règle, avec leur raison (Q2). La parité serait ici une régression.\n'
  else
    printf '  G5  merge-mr / pipeline-wait                  → ⚠ %s des deux seulement est refusé par une règle,\n' "$g5"
    printf '      alors que #788 les donne pour interdits l'\''un comme l'\''autre. À regarder.\n'
  fi

  printf '\n  Bilan : %s écart(s) imputable(s) aux listes — %s en Q1, %s en Q2, dont %s à arbitrer.\n' \
    "$((nb_ask + nb_ecart + nb_arbitrer))" "$nb_ask" "$((nb_ecart + nb_arbitrer))" "$nb_arbitrer"
  printf '  Q3 n'\''y entre pas : aucune règle ne comble ce que le CLI refuse en amont (voir l'\''en-tête).\n'

  printf '\n  Ce que ce rapport ne dit pas : ce qui a été RÉELLEMENT refusé à des sessions —\n'
  printf '  « bash scripts/orchestrate/journal.sh refus --tous ». Il ne tranche rien non plus : les\n'
  printf '  arbitrages appartiennent aux lots 2 à 5 de #788.\n'
fi

[ $((nb_ecart + nb_arbitrer + nb_ask)) -eq 0 ] || exit 3
exit 0

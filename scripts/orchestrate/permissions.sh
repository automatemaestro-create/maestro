#!/usr/bin/env bash
# Lire les règles de permission LÀ OÙ ELLES VIVENT (#789, parent #788).
#
# Sourçable, sans effet de bord :
#
#   . scripts/orchestrate/permissions.sh
#   perm_bloc <fichier.json> <allow|ask|deny>   # « <bloc><TAB><règle> », une par ligne
#   perm_union [<fichier.json>…]                # les trois blocs de tous les fichiers, triés
#   perm_fichiers                               # les deux fichiers du dépôt, dans l'ordre de lecture
#   perm_awk                                    # le programme awk partagé (fonctions de matching)
#
# POURQUOI CETTE BIBLIOTHÈQUE. `journal.sh refus` s'était donné la règle en #307 : le classement
# doit suivre les règles RÉELLEMENT EN VIGUEUR, pas une copie, sans quoi il se périme sans que rien
# ne le dise. #789 pose la même question à un second verbe (`ecart-run.sh`) : deux lecteurs, un
# seul lu-où-ça-vit. Le corpus est l'UNION de `.claude/settings.json` et de
# `scripts/orchestrate/settings.run.json`, parce que c'est l'union que le CLI applique à une
# session de run (`--settings` AJOUTE une couche, il ne remplace pas la chaîne — settings.run.json
# le dit dans son `$comment`, preuve à l'appui).
#
# CE QUI N'EST PAS ICI, ET POURQUOI. `guard.sh` garde sa propre lecture du bloc `deny` : c'est un
# hook `PreToolUse`, joué à chaque appel Bash d'une session autonome, et son isolement est une
# qualité — un fichier de plus à sourcer est un mode de panne de plus sur le chemin du garde-fou,
# pour une question qui n'est pas la même (« le run a-t-il tous les `deny` du dépôt ? », un contrôle
# de dérive entre DEUX fichiers, là où l'on cherche ici ce que leur union couvre).

# Racine du dépôt, déduite de l'emplacement de ce fichier — il est sourcé depuis des répertoires
# de travail variés (clone principal, worktree, journal d'un run).
PERM_RACINE="${PERM_RACINE:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"

# perm_fichiers : les deux fichiers de règles du dépôt, dans l'ordre de lecture. Le dépôt d'abord,
# le run ensuite : c'est l'ordre dans lequel une session les reçoit, et il rend l'origine d'une
# règle lisible quand les deux la portent.
perm_fichiers() {
  printf '%s\n' \
    "$PERM_RACINE/.claude/settings.json" \
    "$PERM_RACINE/scripts/orchestrate/settings.run.json"
}

# perm_bloc <fichier json> <allow|ask|deny> : les règles d'un bloc, une par ligne, préfixées du nom
# du bloc. Lecture en awk plutôt qu'en JSON : ce dépôt n'a ni `jq` ni Python sur le chemin de ses
# scripts shell (#180), et un bloc de permissions est une liste plate de chaînes.
#
# LA LECTURE EST BORNÉE AUX DEUX BOUTS, et ce n'est pas de la coquetterie : la version d'origine
# gardait la ligne du `"allow": [` en entier et s'arrêtait au premier `]` de la ligne, ce qui suffit
# tant que les fichiers sont formatés une règle par ligne — la forme des deux fichiers du dépôt, et
# la raison pour laquelle le défaut ne s'était jamais vu. Sur un JSON COMPACT
# (`{"permissions":{"allow":[],"ask":[],"deny":[]}}`), elle rendait `permissions`, `ask` et `deny`
# comme s'il s'agissait de règles — trois règles fantômes, dont une dans le bloc `ask`, c'est-à-dire
# précisément la colonne que `ecart-run.sh` compte. Un rapport qui invente des règles est pire
# qu'un rapport absent.
#
# Le crochet fermant est cherché HORS CHAÎNE : une règle a le droit de contenir un `]`
# (`Bash(sed 's/[a]//':*)`), et couper dessus la tronquerait au milieu.
# Le programme voyage par un heredoc QUOTÉ et non entre apostrophes simples : ses commentaires sont
# en français, et la première apostrophe de « l'état » refermerait le quoting du shell — panne
# franche, mais qui se répare mal quand on la découvre au fond d'un pipeline.
PERM_AWK_BLOC=$(
  cat <<'AWK'
  # fin_hors_chaine(s) : position du premier `]` structurel, 0 s il n y en a pas. On suit l état
  # « dans une chaîne » caractère par caractère, en sautant ce qu un antislash échappe.
  function fin_hors_chaine(s,   i, c, dedans, prec) {
    for (i = 1; i <= length(s); i++) {
      c = substr(s, i, 1)
      if (dedans) {
        if (c == "\\" && prec != "\\") { prec = c; continue }
        if (c == "\"" && prec != "\\") dedans = 0
      } else if (c == "\"") dedans = 1
      else if (c == "]") return i
      prec = c
    }
    return 0
  }
  !dans && match($0, "\"" bloc "\"[[:space:]]*:") {
    dans = 1
    reste = substr($0, RSTART + RLENGTH)
  }
  dans {
    if (reste == "") reste = $0
    if (!ouvert) {
      p = index(reste, "[")
      if (!p) { reste = ""; next }        # le crochet ouvrant est sur une ligne suivante
      ouvert = 1
      reste = substr(reste, p + 1)
    }
    q = fin_hors_chaine(reste)
    if (q) { print substr(reste, 1, q - 1); exit }
    print reste
    reste = ""
  }
AWK
)

perm_bloc() {
  [ -f "$1" ] || return 0
  awk -v bloc="$2" "$PERM_AWK_BLOC" "$1" 2>/dev/null |
    grep -o '"[^"]*"' | tr -d '"' | sed "s/^/$2	/"
}

# perm_union [<fichier>…] : les trois blocs de tous les fichiers, dédoublonnés. Sans argument, les
# fichiers du dépôt. Une même règle présente des deux côtés ne compte qu'une fois — c'est bien
# l'union qui s'applique à la session, pas la somme.
perm_union() {
  local f bloc
  # `mapfile` et non `set -- $(…)` : la racine de ce dépôt porte une ESPACE sur le poste de
  # référence (« E:\Projects Solutions\Maestro »), et le découpage de mots couperait chaque chemin
  # en deux fichiers introuvables — donc un corpus vide, donc le verdict rassurant qu'on refuse.
  if [ "$#" -eq 0 ]; then
    local defauts=()
    mapfile -t defauts < <(perm_fichiers)
    set -- "${defauts[@]}"
  fi
  for f in "$@"; do
    for bloc in allow ask deny; do perm_bloc "$f" "$bloc"; done
  done | sort -u
}

# perm_awk : le programme awk partagé, à concaténer DEVANT celui de l'appelant. Absent, on rend une
# chaîne vide : l'appelant échouera sur un `matche` inconnu, ce qui est un échec franc — bien
# préférable à un classement silencieusement dégradé, qui rendrait « aucun écart » sur une question
# jamais posée.
perm_awk() {
  cat "$PERM_RACINE/scripts/orchestrate/permissions.awk" 2>/dev/null
}

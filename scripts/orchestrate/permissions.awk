# Le matching des règles de permission de Claude Code — la règle vit ICI, une fois (#789).
#
# Programme awk sans action : il ne porte que des FONCTIONS, destinées à être concaténées devant le
# programme de l'appelant (`awk "$(cat permissions.awk)$MON_PROGRAMME"`). C'est ce qui permet de le
# partager sans fichier temporaire ni substitution de processus — un `awk -f a.awk -f b.awk`
# obligerait l'appelant à verser son propre programme dans un fichier, ce que `journal.sh` ne peut
# pas faire (il passe le sien en chaîne).
#
# POURQUOI PARTAGÉ, ET PAS RECOPIÉ. Deux verbes posent la même question au même corpus de règles :
# `journal.sh refus` demande « ce maillon REFUSÉ était-il couvert ? » et `ecart-run.sh` demande « ce
# geste SERAIT-il couvert ? ». Deux implémentations finiraient par ne plus rendre le même verdict
# sur la même règle, et c'est précisément ce verdict qui décide si l'on élargit une allowlist ou
# non. Même raison que `gl_arbitrage_de` / `gl_rail_de` côté `lib.sh`.
#
# Ce que ces fonctions ne font PAS : lire les fichiers de règles. Les règles se lisent là où elles
# vivent (`.claude/settings.json` ∪ `scripts/orchestrate/settings.run.json`), jamais dans une copie
# — c'est `permissions.sh` qui s'en charge, et l'appelant les lui redemande à chaque invocation.

# matche(seg, regles, n, large) : une de ces règles couvre-t-elle ce maillon ? On rejoue le matching
# du CLI, qui est un matching de PRÉFIXE DE COMMANDE : `Bash(git status:*)` couvre « git status » et
# tout ce qui commence par « git status » ; sans `:*` la règle est exacte. Le maillon est jugé sur
# son TEXTE et non sur son premier mot — une règle borne « command -v » ou « bash scripts/… »,
# qu'un verbe seul ne rendrait pas.
#
# `large` sert les règles `ask`/`deny`, et l'écart est délibéré : leurs OPTIONS peuvent être
# n'importe où dans la commande. Le CLI comprend les options, un préfixe non — sans cela
# `git commit --no-edit --no-verify` échapperait à `Bash(git commit --no-verify:*)`, et le refus
# VOULU qu'il déclenche irait grossir « inclassé ». La tête de la règle, elle, reste un préfixe :
# la relâcher aussi ferait tomber `git commit -m "clean up"` sous `Bash(git clean:*)`.
function matche(seg, regles, n, large,   i, r, p, k, mots, j, tete, ok) {
  for (i = 1; i <= n; i++) {
    r = regles[i]
    if (r !~ /^Bash\(.*\)$/) continue
    p = substr(r, 6, length(r) - 6)
    if (p !~ /:\*$/) { if (seg == p) return 1; continue }
    p = substr(p, 1, length(p) - 2)
    if (!large) {
      if (seg == p || index(seg, p " ") == 1) return 1
      continue
    }
    k = split(p, mots, /[ \t]+/)
    tete = ""
    for (j = 1; j <= k && substr(mots[j], 1, 1) != "-"; j++)
      tete = tete (tete == "" ? "" : " ") mots[j]
    if (tete != "" && seg != tete && index(seg, tete " ") != 1) continue
    ok = 1
    for (; j <= k; j++) if (seg !~ ("(^|[ \t])" mots[j] "([ \t]|$)")) { ok = 0; break }
    if (ok) return 1
  }
  return 0
}

# outil_couvert(nom, regles, n) : l'outil lui-même est-il autorisé, nu (« Write ») ou paramétré
# (« Write(…) ») ? Le tableau est passé en ARGUMENT plutôt que lu dans une globale : c'est ce qui
# permet de poser la question à l'`allow` comme au couple `ask`/`deny`, dont le second dit ce
# qu'aucun élargissement ne doit lever — un outil en `ask` est un refus VOULU dès qu'il n'y a
# personne pour répondre, c'est-à-dire à chaque session autonome.
function outil_couvert(nom, regles, n,   i) {
  for (i = 1; i <= n; i++)
    if (regles[i] == nom || index(regles[i], nom "(") == 1) return 1
  return 0
}

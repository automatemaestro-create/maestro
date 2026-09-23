"""« Lire n'est pas exécuter » — la règle **appliquée**, et non plus seulement écrite (#1197).

#1102 avait posé la règle dans le prompt : *pour ouvrir un fichier, sers-toi de
`Read`, jamais d'une commande shell* (`maestro.agents.playbooks_defaut._cadre_outille`).
Elle a été fermée sur ce texte. Exercé le 2026-09-22 par le banc des scénarios de
référence, le comportement ne suivait pas : le premier geste de l'agent de S3 est
un `find . | sort; echo …; find .agents`, celui de S1 un `ls -la …; git status`.
Une commande, donc une demande de validation ; personne pour y répondre pendant la
tâche, donc un run « en attente d'arbitrage » et un scénario rouge. **Une consigne
écrite ne suffit pas** : c'est le fait que ce module transforme en règle exécutée.

## Ce que ce module décide, et ce qu'il ne décide pas

Il répond à **une** question, sur le texte d'une commande : *celle-ci ne fait-elle
que lire ?* Il ne décide pas qui tranche (`maestro.decideur`), ni ce qu'un agent a
le droit d'appeler (`maestro.agents.permissions`), ni où il a le droit d'écrire
(`maestro.sandbox.en_place`). Il est **feuille** — il n'importe rien de `maestro` —
pour la raison qui vaut déjà pour `maestro.acte` : la réponse est lue par le hook
du fournisseur, et elle doit pouvoir s'éprouver sans monter ni politique, ni
session, ni projet.

## Trois partis pris, et le sens dans lequel ils se trompent

**Le défaut est l'arbitrage, jamais le laissez-passer.** C'est l'asymétrie
d'EF-08/ENF-04, la même qui fait retomber un cran illisible sur `humain`
(`maestro.decideur.decideur_depuis`) : ce que ce module ne **reconnaît pas** comme
une lecture repart vers la personne qui tranchait déjà. Une commande composée, un
verbe inconnu, un opérateur qu'on ne sait pas lire, des guillemets déséquilibrés —
tout cela rend `False`, c'est-à-dire *rien de changé*. Le pire que ce module puisse
faire est donc de laisser attendre une lecture qu'il n'a pas su reconnaître, et
c'est réparable en nommant son verbe ; l'inverse ne l'est pas.

**Une liste de verbes, et c'est assumé.** « Maestro juge, il ne bride pas » écarte
les catalogues fermés *comme mécanisme principal* — ce que le modèle comprend et
propose. Ici le catalogue n'ouvre rien à l'agent : il **retire une attente** sur ce
qui est déjà permis, et tout ce qu'il ne cite pas garde exactement le régime
d'avant. Un garde-fou qui s'élargit sur une liste courte et relisible est plus sûr
qu'un jugement au vol sur ce qu'une commande *a l'air* de faire.

**Ce module ne dit rien de *ce que* la commande lit, et n'essaie pas.** La
frontière d'écriture (#839) ne s'applique pas à `Bash`, et docs/24 §2.5 dit
pourquoi : une commande shell ne se borne pas par l'analyse de son texte. Un
premier jet retirait la dispense à toute commande qui **nomme** un chemin exclu du
périmètre, pour qu'un `cat .env` continue d'attendre quelqu'un. Le premier passage
réel du banc l'a réfuté en deux coups : l'agent écrit
`find . -path ./.git -prune -o -type f -print | grep -v node_modules`, c'est-à-dire
qu'il nomme `.git` et `node_modules` **pour les éviter** — la garde punissait
exactement le bon geste. Et elle n'aurait rien fermé : `grep -rn motif .` lit le
`.env` sans le nommer. *Un garde-fou qui saute est pire qu'un garde-fou absent* ; un
garde-fou qui refuse le geste juste et laisse passer le mauvais l'est encore plus.

Ce qui borne les secrets n'a donc pas bougé d'un pouce, et se lit ailleurs : les
outils de fichiers restent tenus par la frontière (`Read` sur un `.env` est
**refusé**, pas arbitré), les exclusions du périmètre valent à l'analyse comme au
relevé, et la rédaction des valeurs (#109) tient ce qui sort. Ce module, lui, ne
prétend rien : il dit qu'une commande **n'agit pas**, pas qu'elle lit peu.
"""

from __future__ import annotations

import shlex
from collections.abc import Mapping
from typing import Any

#: L'outil d'exécution des agents — le seul que ce module juge. Même valeur que
#: `maestro.equipe.proposition.OUTIL_EXECUTION`, et les deux la portent pour deux
#: questions différentes : là-bas *sous quel cran l'agent exécute*, ici *cet
#: appel-ci exécute-t-il quelque chose*.
OUTIL_SHELL = "Bash"

#: La clé du `tool_input` de `Bash` qui porte la commande.
CLE_COMMANDE = "command"

#: L'outil par lequel un agent **ouvre un fichier** sans exécuter quoi que ce
#: soit. Il n'est pas jugé ici : il est la **mesure** de la dispense. Le
#: laissez-passer accordé au shell ne donne jamais plus que ce que cet outil-là
#: donne déjà — si une politique met `Read` en arbitrage ou le refuse, lire au
#: shell n'a plus rien d'anodin, et la dispense tombe (le hook du fournisseur
#: pose la question). Sans cela, `ask: Read` serait contournable d'un `cat`.
OUTIL_LECTURE = "Read"

#: Ce qui enchaîne deux commandes sans rien faire de plus. Un `|` en fait partie :
#: il ne crée aucun fichier, il branche une sortie sur une entrée — et chaque
#: maillon est jugé pour lui-même, comme la couche permissions juge une commande
#: composée à son maillon le plus faible.
SEPARATEURS = frozenset({";", "&&", "||", "|"})

#: Les caractères que `shlex` isole en jetons de ponctuation. Tout amas qui n'est
#: ni un séparateur ni une redirection reconnue en sort : un sous-shell (`(`,
#: `)`), un lancement en arrière-plan (`&`), une entrée depuis un fichier (`<`).
#: Aucun n'est une lecture, et aucun n'a à être reconnu plus finement —
#: l'inconnu repart vers l'arbitrage.
PONCTUATION = frozenset("();<>|&")

#: Les redirections de **sortie**, et la seule chose qu'on les laisse viser.
#: `2>/dev/null` est l'idiome d'un `find` qui traverse un disque : il n'écrit
#: nulle part, il jette ce qui l'encombre — le refuser ferait attendre un humain
#: sur la commande la plus ordinaire qui soit (mesuré au premier passage réel du
#: banc, run `c4a4c14806a3`). Tout autre cible est un fichier écrit, donc un acte.
#: `>&` suivi d'un **chiffre** (`2>&1`) ne vise aucun fichier non plus : c'est un
#: descripteur branché sur un autre.
REDIRECTIONS = frozenset({">", ">>", ">&", "&>"})

#: Le puits : la seule destination d'écriture qui n'écrit rien.
PUITS = "/dev/null"

#: Les amorces de substitution : ce qu'elles exécutent n'est pas dans le texte
#: qu'on juge. Cherchées **dans les jetons**, guillemets retirés, parce que c'est
#: là qu'elles se cachent : `cat "$(ls)"` ne donne qu'un jeton.
SUBSTITUTIONS = ("$(", "`")

#: Les verbes qui ne font que lire, et pour chacun **les options qui l'en
#: feraient sortir**. Une entrée vide dit « ce verbe n'a aucune forme écrivante »,
#: et c'est une affirmation qu'un test tient (`tests/test_lecture.py`).
#:
#: Ce que cette liste couvre est exactement ce que le comportement attendu de
#: #1197 nomme — *lister, chercher, ouvrir un fichier* —, et rien de plus. Elle
#: s'allonge sur des **gestes constatés**, jamais sur ce qu'on imagine qu'un
#: agent pourrait taper : `cd` y est entré parce qu'un run réel a écrit
#: `cd "<racine>" && ls -la .maestro`, et personne ne l'avait prévu ; `test` et
#: `[` parce que le passage `20260922-164402` a fait attendre un humain sur
#: `ls -la .agents/skills/ 2>/dev/null; test -f …/SKILL.md && echo "skill present"`
#: (#1211).
#:
#: Cinq absences sont des décisions, pas des oublis :
#:
#: - `sed` et `awk` **écrivent** (`sed -i`, `sed 's/…/…/w f'`, `print > "f"` dans
#:   un programme awk, à l'abri des guillemets) : les reconnaître demanderait de
#:   lire leur langage, pas leur ligne de commande ;
#: - `uniq` prend un **second fichier en sortie** (`uniq entree sortie`), sans
#:   option pour le signaler — un argument positionnel ne se distingue pas d'un
#:   autre ;
#: - `less` et `more` ne rendent jamais la main sans terminal : les laisser
#:   passer échangerait une attente d'humain contre une attente sans fin ;
#: - `env` et `printenv` sont des lectures, et pourtant non : l'environnement
#:   d'un agent est l'endroit où vivent ses **secrets** (#109), et `env` sert
#:   surtout à lancer une commande sous un autre environnement ;
#: - `[[` n'est pas `[` : c'est un mot-clé de bash, qui **évalue** ses opérandes
#:   arithmétiques — `[[ $x -eq 0 ]]` exécute la substitution que la *valeur* de
#:   `x` contient, sans qu'aucun `$(` n'apparaisse dans le texte qu'on juge
#:   (mesuré sous bash 5.2 pour #1211, là où `[ "$x" -eq 0 ]` reste inerte).
VERBES_LECTURE: dict[str, frozenset[str]] = {
    # Se placer — `cd` ne touche à rien : il déplace le pied des chemins de sa
    # propre commande, et chaque maillon est jugé pour lui-même de toute façon.
    "cd": frozenset(),
    # Lister ce qu'il y a
    "ls": frozenset(),
    "tree": frozenset({"-o"}),  # `tree -o fichier` écrit l'arborescence
    "find": frozenset(
        {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0",
         "-fprintf", "-fls"}
    ),
    "stat": frozenset(),
    "file": frozenset(),
    "du": frozenset(),
    "pwd": frozenset(),
    # Ouvrir un fichier
    "cat": frozenset(),
    "head": frozenset(),
    "tail": frozenset(),
    "nl": frozenset(),
    "wc": frozenset(),
    # Tester un fichier — son existence, sa nature, ses droits (`-f`, `-d`,
    # `-e`, `-s`, `-x`, `-w`…) : le shell regarde le disque et rend un code,
    # rien d'autre. `-x` et `-w` en sont : ils demandent si l'on *pourrait*,
    # ils ne le font pas. `[` est le même builtin sous un autre nom ; son `]`
    # final n'est pas exigé, parce qu'un `[` qui l'oublie échoue sans rien
    # faire, et la seule question ici est *agit-il ?*.
    #
    # ⚠ `-v` en est retiré, et ce n'est pas un test de fichier : il demande si
    # une **variable** existe, et bash évalue l'indice d'un tableau —
    # `test -v "a[y]"` exécute la substitution que la valeur de `y` contient
    # (mesuré sous bash 5.2 pour #1211). Les tests de variables ne sont pas
    # l'affaire de ce module ; celui-là fait exécuter.
    "test": frozenset({"-v"}),
    "[": frozenset({"-v"}),
    # Chercher dedans
    "grep": frozenset(),
    "egrep": frozenset(),
    "fgrep": frozenset(),
    "rg": frozenset(),
    # Mettre en forme ce qui sort — jamais dans un fichier
    "sort": frozenset({"-o", "--output"}),
    "cut": frozenset(),
    "tr": frozenset(),
    "echo": frozenset(),
    # Résoudre un chemin ou trouver un outil, sans rien ouvrir
    "basename": frozenset(),
    "dirname": frozenset(),
    "realpath": frozenset(),
    "readlink": frozenset(),
    "which": frozenset(),
    # Interroger le dépôt
    "git": frozenset(),
}

#: Les options **globales** de `git` — celles qui précèdent la sous-commande —
#: qu'on sait franchir pour aller la chercher. Deux formes : celles qui prennent
#: une valeur (elle occupe la place où l'on cherche la sous-commande, et
#: `git -C . status` s'est fait arbitrer pour cette seule raison sur le run
#: `5508ebb01cb8`) et celles qui n'en prennent pas.
#:
#: ⚠ **`-c <clé>=<valeur>` n'y est pas, et c'est une décision.** Il règle la
#: configuration de l'appel, `core.pager` compris — c'est-à-dire qu'il choisit
#: une commande que `git` lancera. Une option qui fait exécuter n'est pas une
#: option de lecture. Toute globale inconnue renvoie de même vers l'arbitrage.
GIT_GLOBALES_A_VALEUR = frozenset({"-C", "--git-dir", "--work-tree", "--namespace"})
GIT_GLOBALES_NUES = frozenset(
    {
        "--no-pager",
        "--paginate",
        "--bare",
        "--literal-pathspecs",
        "--no-literal-pathspecs",
        "--no-replace-objects",
        "--no-optional-locks",
    }
)

#: Les sous-commandes de `git` qui ne font que lire, et leurs options écrivantes.
#: `git` est le seul verbe dont le sens tient au **second** mot : `git status` et
#: `git push` n'ont en commun que leur exécutable. Ce qui n'est pas cité ici —
#: `config`, `remote`, `tag`, `add`, `commit`… — repart vers l'arbitrage.
#:
#: Les options de diff voyagent d'une sous-commande à l'autre (`--output=<f>` est
#: acceptée par `log`, `diff` et `show` dès qu'un patch est demandé) : elles sont
#: donc refusées sur les trois, pas seulement sur `diff`.
GIT_LECTURE: dict[str, frozenset[str]] = {
    "status": frozenset(),
    "log": frozenset({"-o", "--output"}),
    "diff": frozenset({"-o", "--output"}),
    "show": frozenset({"-o", "--output"}),
    "blame": frozenset(),
    "shortlog": frozenset(),
    "describe": frozenset(),
    "rev-parse": frozenset(),
    "ls-files": frozenset(),
    "ls-tree": frozenset(),
    "cat-file": frozenset(),
    "show-ref": frozenset(),
    "for-each-ref": frozenset(),
    "grep": frozenset({"-O", "--open-files-in-pager"}),  # `-O` lance le pager
    "branch": frozenset(
        {"-d", "-D", "-m", "-M", "-c", "-C", "-u", "-f", "--delete", "--move",
         "--copy", "--force", "--set-upstream-to", "--unset-upstream",
         "--edit-description"}
    ),
}


def commande_de(arguments: Any) -> str:
    """La commande portée par un `tool_input` de `Bash` — vide s'il n'y en a pas.

    Même régime de relecture que `maestro.acte.arguments_depuis` : ce qui arrive
    vient du SDK et n'a pas à faire échouer quoi que ce soit. Ce qui n'est pas un
    objet, ou ne porte pas une commande en texte, rend la chaîne vide — donc, en
    bout de course, l'arbitrage.
    """
    if not isinstance(arguments, Mapping):
        return ""
    brut = arguments.get(CLE_COMMANDE)
    return brut if isinstance(brut, str) else ""


def est_lecture(commande: str) -> bool:
    """`commande` ne fait-elle que **lire** ? Au moindre doute, non.

    Chaque commande simple de l'enchaînement est jugée pour elle-même, et il
    suffit d'une qui agisse pour que l'ensemble agisse : c'est la règle que la
    couche permissions applique déjà à une commande composée — *elle vaut son
    maillon le plus faible*.
    """
    segments = _segments(commande)
    if not segments:
        return False
    return all(_segment_lit(jetons) for jetons in segments)


def lecture_sans_arbitrage(outil: str, arguments: Any) -> bool:
    """L'appel `outil(arguments)` est-il une lecture qui n'a personne à déranger ?

    Le verbe que consulte le hook du fournisseur
    (`maestro.providers.claude._hook_permissions`), et le seul de ce module qui
    parle d'outils. Faux pour tout autre outil que `Bash` : un `ask` posé sur
    `Read`, `Glob` ou `Grep` est un choix de politique, et le lever au nom de « ce
    n'est qu'une lecture » reviendrait à défaire la politique qu'on applique.
    """
    if outil != OUTIL_SHELL:
        return False
    return est_lecture(commande_de(arguments))


def _segments(commande: str) -> tuple[tuple[str, ...], ...] | None:
    """Les commandes enchaînées, découpées en jetons — `None` si le texte échappe.

    `shlex` en mode POSIX avec `punctuation_chars` : les guillemets sont respectés
    (un `;` dans une chaîne n'est pas un séparateur) et chaque amas d'opérateurs
    sort en un jeton. Les commentaires sont désarmés (`commenters`) : un `#` est
    un motif de recherche bien plus souvent qu'une fin de ligne, et l'ignorer
    ferait juger une commande tronquée.

    Une **redirection vers le puits** est traversée plutôt que refusée
    (`REDIRECTIONS`) : elle n'écrit nulle part, et le chiffre de descripteur qui
    la précède (`2` de `2>/dev/null`) repart avec elle — sans quoi il resterait
    dans les arguments du verbe, où il ne veut rien dire.

    `None` pour trois raisons, qui mènent toutes trois à l'arbitrage : des
    guillemets déséquilibrés (`ValueError`), un opérateur qui n'est ni un
    enchaînement ni une redirection vers le puits, ou une substitution — ce
    qu'elle exécute n'est pas dans le texte qu'on juge.
    """
    lexeur = shlex.shlex(commande, posix=True, punctuation_chars=True)
    lexeur.whitespace_split = True
    lexeur.commenters = ""
    try:
        jetons = list(lexeur)
    except ValueError:
        return None
    segments: list[tuple[str, ...]] = []
    courant: list[str] = []
    reste = list(reversed(jetons))
    while reste:
        jeton = reste.pop()
        if jeton in SEPARATEURS:
            if courant:
                segments.append(tuple(courant))
            courant = []
            continue
        if jeton in REDIRECTIONS:
            cible = reste.pop() if reste else ""
            if cible != PUITS and not (jeton == ">&" and cible.isdigit()):
                return None
            if courant and courant[-1].isdigit():
                courant.pop()
            continue
        if set(jeton) <= PONCTUATION or any(amorce in jeton for amorce in SUBSTITUTIONS):
            return None
        courant.append(jeton)
    if courant:
        segments.append(tuple(courant))
    return tuple(segments) or None


def _segment_lit(jetons: tuple[str, ...]) -> bool:
    """Une commande simple ne fait-elle que lire ?"""
    verbe, *arguments = jetons
    ecrivantes = VERBES_LECTURE.get(verbe)
    if ecrivantes is None:
        return False
    if verbe == "git":
        lue = _git_lue(arguments)
        if lue is None:
            return False
        ecrivantes, arguments = lue
    return not any(_ecrit(jeton, ecrivantes) for jeton in arguments)


def _git_lue(arguments: list[str]) -> tuple[frozenset[str], list[str]] | None:
    """La sous-commande `git` et ce qui la suit, globales franchies — `None` si on doute.

    Rend les options écrivantes de la sous-commande **et les arguments qui lui
    reviennent** : les globales sont laissées derrière, sans quoi le `-C` d'un
    `git -C . branch` se lirait comme le `-C` qui copie une branche.

    On ne **devine** pas : une globale qu'on ne connaît pas, ou dont la valeur
    manque, rend `None` et la commande repart vers l'arbitrage. C'est la même
    asymétrie que partout ici — ne pas savoir n'est jamais une raison de laisser
    passer.
    """
    reste = list(reversed(arguments))
    while reste:
        jeton = reste.pop()
        if not jeton.startswith("-"):
            ecrivantes = GIT_LECTURE.get(jeton)
            return None if ecrivantes is None else (ecrivantes, list(reversed(reste)))
        nom = jeton.split("=", 1)[0]
        if nom in GIT_GLOBALES_A_VALEUR:
            if "=" in jeton:
                continue
            if not reste:
                return None
            reste.pop()
            continue
        if jeton in GIT_GLOBALES_NUES:
            continue
        return None
    return None


def _ecrit(jeton: str, ecrivantes: frozenset[str]) -> bool:
    """`jeton` est-il l'une des options qui feraient écrire ce verbe ?

    Trois formes de la même option : nue (`-o`), collée à sa valeur
    (`--output=f`), et **groupée** avec d'autres lettres courtes (`-ko`, que les
    outils GNU acceptent). La dernière n'est cherchée que pour les options d'une
    seule lettre — un `-delete` ne se groupe pas —, et elle vaut mieux
    qu'exacte : c'est elle qui ferait passer un `sort -ko fichier` pour une
    lecture.
    """
    for option in ecrivantes:
        if jeton == option or jeton.startswith(f"{option}="):
            return True
        if len(option) == 2 and not jeton.startswith("--") and jeton.startswith("-"):
            if option[1] in jeton[1:]:
                return True
    return False

"""« Agir dans son projet n'est pas agir dehors » — la portée d'un cran (#1226).

Le pendant de [`maestro.lecture`](./lecture.py), et le second des deux verbes que
le hook du fournisseur consulte avant de déranger quelqu'un. Là-bas la question
était *cette commande ne fait-elle que lire ?* ; ici c'est *cet acte reste-t-il
dans le projet qu'on lui a confié ?*.

## Le fait qui l'a rendu nécessaire

Retour d'expérience du 2026-09-22, projet neuf, équipe proposée par Maestro et
validée telle quelle : **14 demandes de validation `Bash`, 14 approbations**. Les
commandes étaient `mkdir .maestro/…`, `python app.py`, `pytest`, `python
--version`, et le ménage des caches que ses propres exécutions venaient de
produire. Aucune ne sortait du dossier du projet. `maestro.equipe.proposition`
proposait `Bash: ask, humain` dès qu'aucune commande du rôle n'avait été lue dans
le projet — c'est-à-dire **toujours** sur un projet neuf —, si bien qu'un agent
recruté pour écrire du code devait faire approuver le fait de le lancer.

[docs/32 §8](../docs/32-decision-cran-orchestrateur.md) avait prévu ce cas et son
remède : *« un taux d'approbation proche de 1 dit que ces actes-là méritaient
`auto` »*, et, quand le verdict dépend des arguments, **une portée sur l'entrée de
politique** (porte 2) — jamais un décideur intermédiaire, jamais un modèle qui
juge un appel d'outil. Ce module est cette portée.

## Ce qu'il décide, et dans quel sens il se trompe

Une seule question, sur le texte d'une commande : *peut-elle toucher autre chose
que ce que l'agent a produit dans la racine qu'on lui a donnée ?* Deux familles
remontent à la personne, et **elles seules** :

1. **ce qui sort du projet** — un chemin hors de la racine, et les actes qui
   sortent sans nommer de chemin (élévation de privilèges, gestionnaire de
   paquets du système, installation globale d'un langage, machine distante) ;
2. **ce qui détruit ce que l'agent n'a pas produit** — un verbe destructeur dont
   une cible était déjà là quand la tâche a commencé (`presents`), ou dont la
   cible ne se résout pas (un `rm -rf *` ne se juge pas).

⚠ **L'asymétrie est l'inverse de celle de `maestro.lecture`, et c'est délibéré.**
Là-bas, ce qui n'était pas reconnu repartait vers l'arbitrage : le défaut sûr
était de faire attendre. Ici le défaut sûr est l'inverse — *une personne a déjà
décidé*, à froid, à la validation de l'équipe, que cet agent exécuterait dans ce
projet (docs/37 §4.3), et il n'y a personne pendant une tâche pour répondre à ce
qu'on lui renverrait (EF-08). Un verbe inconnu dont les chemins restent dans la
racine est donc **dans la portée**. Ce qui borne le reste n'a pas bougé d'un
pouce : la frontière d'écriture sur les outils de fichiers (#839), les exclusions
du périmètre, la rédaction des valeurs (#109), et la liste `deny` de la politique,
qui refuse toujours avant qu'on arrive ici.

Le prix de cette asymétrie est nommé : ce module ne prétend pas qu'une commande
ne sortira pas du projet, il dit qu'elle **ne le dit pas**. Un shell peut écrire
n'importe où (docs/24 §2.5), c'était déjà vrai de la copie et du worktree, et ce
que cela ferme est le **mode isolé** (`maestro.sandbox.container`), pas une
analyse de texte plus fine. Le réseau n'est ni l'une ni l'autre des deux
familles — un `curl` rapporte dans le projet plutôt qu'il n'en sort —, et le
prétendre ici ferait croire à une garde qui n'existe pas.

⚠ **Ce module ne referme pas ce que #1197 a ouvert.** Une commande qui ne fait
que lire est dans la portée sans plus d'examen, y compris si elle lit hors de la
racine : lire n'est pas un acte, la dispense de lecture s'applique de toute façon
avant que le décideur ne compte, et ce qui borne les secrets est ailleurs (la
frontière sur les outils de fichiers, les exclusions du périmètre, la rédaction
des valeurs).

## Comment il lit (#1348)

Le passage `20260927-070605` du banc a rendu **55 commandes** à une personne, dont
44 restaient dans leur projet : ce module lisait le texte avec `shlex`, et tout ce
qui n'était pas une suite de mots — une substitution, un heredoc, une boucle —
était « illisible », donc remontait. Il lit désormais comme bash, par
`maestro.shell`, et juge **ce que le texte dit, là où il le dit** :

- **chaque maillon pour lui-même**, substitutions et corps de heredoc donnés à un
  shell compris : ce qu'ils exécutent est dans le texte. Une lecture n'agit pas,
  et cela vaut maillon par maillon ;
- **depuis le dossier où il est** : un `cd` déplace le pied des chemins qui le
  suivent, noms nus compris — `cd src && …; cd ..` revient à la racine,
  `cd /tmp && rm x` efface `/tmp/x`. Un sous-shell garde ses `cd` pour lui, et
  l'enchaînement se lit comme s'il réussissait ;
- **avec les valeurs que le texte donne** : une variable affectée plus haut, `$PWD`,
  `$HOME`, le temporaire (`$TEMP`, `$TMP`, `$TMPDIR`), ce que rendent `pwd`,
  `cygpath`, `dirname`. Une valeur que le texte ne donne pas **ne se juge pas** —
  sauf quand elle est la cible d'une destruction ou d'une écriture : là, une
  personne tranche ;
- **ce qu'une enveloppe lance** (`env`, `timeout`, `nohup`, `xargs`…) et ce qu'on
  donne à un autre shell (`bash -c`, un heredoc donné à `sh`) sont des commandes,
  jugées comme telles. Le programme donné à PowerShell ou à `cmd` est parcouru
  pour ses verbes qui sortent ; le reste est une donnée, comme celui de
  `python -c` ;
- **un `find` qui efface décrit ses cibles** par ses racines et son `-name` : il
  se confronte au relevé de ce qui était là, comme un `rm` à ses arguments. Ce qui
  ne se confronte pas (aucun nom, un `-o`, un `-regex`) remonte comme avant.

**L'établi.** Ce que l'agent crée par `mktemp` **dans la commande même** est à lui :
il y écrit et le détruit comme dans sa racine — c'est ainsi que S2 vérifie son
livrable sans rien laisser dans le projet. Un **nom fixe** dans le temporaire, lui,
n'est pas à l'agent : Maestro y range les espaces de travail des autres tâches
(`maestro.sandbox.ramassage.racine_des_espaces`), parfois en parallèle dans le même
run, et un `rm -rf /tmp/<nom>` pourrait y effacer le travail d'une voisine. Il
remonte comme tout chemin hors de la racine. La mémoire d'un `mktemp` ne passe pas
d'une commande à l'autre — l'évaluation reste une fonction pure (docs/32 §8) —,
mais le nom qu'un `mktemp` sans gabarit fabrique (`/tmp/tmp.XXXXXXXXXX`) se
reconnaît à lui seul : chaque appel `Bash` étant un shell neuf, l'agent réutilise
en toutes lettres, dans l'appel suivant, le chemin que le précédent a créé.

**Installer dans le projet n'est pas en sortir.** Un `pip install` sort du projet,
sauf quand c'est un interpréteur du projet qui installe
(`.venv/Scripts/python.exe -m pip install`), qu'un venv du projet a été activé
plus haut dans la commande, ou que `--target` vise le projet. `--user` sort
toujours.

## Ce qu'il ne décide pas

Ni qui tranche (`maestro.decideur`), ni ce qu'un agent a le droit d'appeler
(`maestro.agents.permissions`), ni où il a le droit d'écrire
(`maestro.sandbox.en_place`). Il est **feuille** — il n'importe de `maestro` que
`lecture` et `shell`, feuilles eux-mêmes — pour la raison qui vaut déjà là-bas :
la réponse est lue par le hook du fournisseur, et elle doit s'éprouver sans monter
ni politique, ni session, ni projet.

⚠ À ne pas confondre avec `maestro.controltower.portee`, qui répond à une tout
autre question : *quelle configuration de projet sert cette requête HTTP ?*.
"""

from __future__ import annotations

import fnmatch
import ntpath
import os
import posixpath
import re
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from maestro.lecture import OUTIL_SHELL, commande_de, est_lecture, simple_lit
from maestro.shell import (
    LITTERAL,
    SUBSTITUTION,
    TILDE,
    VARIABLE,
    Boucle,
    Commande,
    Document,
    Groupe,
    Illisible,
    Mot,
    Partie,
    Redirection,
    Script,
    Selon,
    Si,
    Simple,
    affectation,
    lis,
)

#: Le nom de la seule portée que le dépôt sache évaluer : *l'acte reste dans le
#: projet confié à l'agent*. Une portée inconnue est refusée au chargement de la
#: politique (`maestro.agents.permissions`) — un garde-fou qu'on ne sait pas lire
#: ne s'applique pas à moitié.
PORTEE_PROJET = "projet"

#: Les portées admissibles dans une entrée de politique. Une seule aujourd'hui, et
#: c'est **l'ensemble** qui fait foi : la validation s'en compose, si bien qu'en
#: ajouter une ne demande pas de retoucher un message d'erreur.
PORTEES: tuple[str, ...] = (PORTEE_PROJET,)

#: Les verbes qui **sortent du projet sans nommer de chemin**. Trois familles,
#: chacune avec sa raison d'y être :
#:
#: - **l'élévation de privilèges** — ce que `sudo` lance ne se juge plus par ce
#:   qu'on lit de son texte, et le geste lui-même dépasse le projet ;
#: - **les gestionnaires du système** — paquets, services, montages : ils
#:   modifient la machine, jamais le dossier du projet ;
#: - **la machine distante** — ce qui part ailleurs est, par construction, hors du
#:   projet.
#:
#: Elle ne cherche pas à être exhaustive et n'a pas à l'être : ce qu'elle rate
#: retombe sur la règle des chemins, et ce qu'elle rate vraiment est le prix
#: assumé de l'asymétrie (cf. l'en-tête du module).
VERBES_HORS_PROJET: frozenset[str] = frozenset(
    {
        # élévation
        "sudo", "su", "doas", "runas",
        # gestionnaires du système
        "apt", "apt-get", "aptitude", "dpkg", "yum", "dnf", "zypper", "pacman",
        "apk", "brew", "port", "choco", "winget", "scoop", "snap",
        # état de la machine
        "systemctl", "service", "mount", "umount", "shutdown", "reboot",
        # machine distante
        "ssh", "scp", "sftp", "telnet",
    }
)

#: Les installations qui atterrissent **hors du dossier du projet** : le verbe,
#: et ce qui, dans ses arguments, en fait une installation. Une entrée vide dit
#: « ce verbe installe toujours dehors » — **sauf** pour `pip`, que l'interpréteur
#: du projet fait installer dans le projet (`_installe_dans_le_projet`, #1348).
#:
#: `pip install` en est par défaut : il écrit dans l'interpréteur qui l'exécute,
#: et un `python` pris dans le `PATH` est celui du poste. Les gestionnaires de
#: paquets d'un projet (`npm install`, `cargo build`) n'y sont, eux, **que sous
#: leur drapeau global** : sans lui, ils remplissent le dossier du projet, ce qui
#: est exactement leur travail.
INSTALLATIONS_HORS_PROJET: dict[str, tuple[str, ...]] = {
    # `pip3`, `pip3.12` se reconnaissent ici sans leur version (`_forme_nue`).
    "pip": (),
    "pipx": (),
    "gem": (),
    "npm": ("-g", "--global", "--location=global"),
    "pnpm": ("-g", "--global"),
    "yarn": ("-g", "--global"),
    "bun": ("-g", "--global"),
}

#: Les sous-commandes qui **installent** — cherchées dans les arguments des
#: gestionnaires ci-dessus. `npm run build` n'installe rien ; `npm ci` remplit le
#: `node_modules` du projet.
SOUS_COMMANDES_INSTALLATION: frozenset[str] = frozenset({"install", "add"})

#: Les options de `pip` qui disent **où** il installe, et celle qui l'envoie chez
#: l'utilisateur quoi qu'il arrive.
OPTIONS_DESTINATION_PIP: frozenset[str] = frozenset({"--target", "-t", "--prefix", "--root"})
OPTION_UTILISATEUR_PIP = "--user"

#: Les verbes qui **détruisent ou déplacent** ce qu'ils visent. `mv` en est : en
#: écriture en place, rien ne passe par une fusion ni par un diff (docs/24 §2.4),
#: donc déplacer un fichier que la personne a posé le lui retire aussi sûrement
#: que l'effacer.
#:
#: Ce qui n'y est **pas** : `mkdir`, `touch`, `cp`, `chmod`. Ils créent ou
#: recouvrent sans faire disparaître — et `cp` vers une cible existante est le
#: seul cas limite, laissé dedans parce que la cible reste lisible là où un `rm`
#: ne laisse rien.
VERBES_DESTRUCTEURS: frozenset[str] = frozenset(
    {"rm", "rmdir", "unlink", "mv", "shred", "truncate", "dd", "del", "erase"}
)

#: Les options qui font **agir** un `find` au lieu de lister (les mêmes que
#: `maestro.lecture` lui refuse). Sa cible n'est alors pas un argument mais le
#: résultat d'un parcours : elle se **décrit** par ses tests (`PorteeProjet._find`),
#: et ce qui ne se confronte pas au relevé remonte.
FIND_AGISSANTES: frozenset[str] = frozenset(
    {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0", "-fprintf", "-fls"}
)

#: Les tests de `find` qu'on sait confronter au relevé de ce qui était là (#1348) :
#: un nom, et les tests qui ne font qu'écarter des cibles ou n'en ajoutent pas.
#: Tout autre (`-o`, `-regex`, `-newer`, les parenthèses…) rend le `find` illisible.
FIND_NOMS: frozenset[str] = frozenset({"-name", "-iname"})
FIND_CHEMINS: frozenset[str] = frozenset({"-path", "-ipath", "-wholename"})
FIND_NEUTRES_A_VALEUR: frozenset[str] = frozenset({"-type", "-maxdepth", "-mindepth"})
FIND_NEUTRES: frozenset[str] = frozenset({"-print", "-print0", "-depth", "-mount", "-xdev"})

#: Les périphériques qu'une commande nomme sans toucher au disque : `cp /dev/null
#: x` vide `x`, il n'écrit rien hors du projet (passage `20260927-131322`, S6).
PERIPHERIQUES: frozenset[str] = frozenset(
    {"/dev/null", "/dev/zero", "/dev/random", "/dev/urandom", "/dev/stdin", "/dev/stdout",
     "/dev/stderr", "/dev/tty"}
)  # fmt: skip

#: Les redirections qui **écrasent** leur cible. Les autres ajoutent (`>>`) ou
#: branchent un descripteur : elles ne font disparaître aucun contenu.
REDIRECTIONS_ECRASANTES: frozenset[str] = frozenset({">", ">|", "&>"})

#: Les redirections qui **lisent** : elles n'agissent pas plus qu'un `cat`.
REDIRECTIONS_LUES: frozenset[str] = frozenset({"<", "<<<", "<&"})

#: Les cibles d'écriture qui n'écrivent dans aucun fichier.
PUITS_DE_SORTIE: frozenset[str] = frozenset({"/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty"})

#: Les **enveloppes** : elles lancent la commande qui les suit, et c'est elle qu'on
#: juge (#1348). Pour chacune, les options qui prennent une valeur — à franchir
#: avec elle pour trouver la commande. `env` franchit aussi ses `NOM=valeur`,
#: `timeout` sa durée ; `xargs` donne à sa commande des cibles lues sur son entrée.
ENVELOPPES: dict[str, frozenset[str]] = {
    "env": frozenset({"-u", "--unset", "-C", "--chdir", "-S", "--split-string"}),
    "command": frozenset(),
    "builtin": frozenset(),
    "exec": frozenset({"-a"}),
    "nohup": frozenset(),
    "time": frozenset({"-f", "--format", "-o", "--output"}),
    "nice": frozenset({"-n", "--adjustment"}),
    "timeout": frozenset({"-s", "--signal", "-k", "--kill-after"}),
    "stdbuf": frozenset({"-i", "-o", "-e", "--input", "--output", "--error"}),
    "xargs": frozenset(
        {
            "-I",
            "-n",
            "-P",
            "-L",
            "-s",
            "-d",
            "-E",
            "-a",
            "--arg-file",
            "--delimiter",
            "--max-args",
            "--max-procs",
            "--replace",
            "--max-lines",
            "--max-chars",
            "--eof",
        }
    ),  # fmt: skip
}

#: Les shells POSIX : ce qu'on leur donne à exécuter (`-c`, ou un heredoc sur leur
#: entrée) est une commande, jugée comme telle.
COQUILLES: frozenset[str] = frozenset({"bash", "sh", "zsh", "dash", "ksh"})

#: Les shells d'un **autre langage**, et l'option qui leur donne un programme. Ce
#: programme est parcouru pour les verbes qui sortent du projet (`pip install`,
#: `winget`…) ; le reste est une donnée, comme le code de `python -c`.
COQUILLES_ETRANGERES: dict[str, frozenset[str]] = {
    "powershell": frozenset({"-command", "-c"}),
    "pwsh": frozenset({"-command", "-c"}),
    # Sous Git Bash, `/c` deviendrait un chemin : l'agent écrit `//c`.
    "cmd": frozenset({"/c", "/k", "//c", "//k"}),
}

#: Un programme PowerShell encodé ne se lit pas : il remonte.
PROGRAMMES_ENCODES: frozenset[str] = frozenset({"-encodedcommand", "-enc", "-ec", "-e"})

#: Les options d'un interpréteur dont la valeur est **du code ou un module**, jamais
#: un chemin : `python -c "…"`, `python -m pytest`. La juger comme un chemin ferait
#: sortir le code d'un agent qui `cd` dans un dossier de vérification.
OPTIONS_DE_PROGRAMME: dict[str, frozenset[str]] = {
    "python": frozenset({"-c", "-m", "-W", "-X"}),
    "py": frozenset({"-c", "-m", "-W", "-X"}),
    "node": frozenset({"-e", "--eval", "-p", "--print", "-r", "--require"}),
    "perl": frozenset({"-e", "-E"}),
    "ruby": frozenset({"-e"}),
    "php": frozenset({"-r"}),
}

#: Les verbes dont le **premier opérande est un programme** (le script de `sed`, le
#: programme d'`awk`, le filtre de `jq`) — sauf quand l'une de ces options l'a
#: déjà donné. `sed -n '/^x/p' f` ne nomme pas le chemin `/^x/p`.
PROGRAMME_EN_PREMIER: dict[str, frozenset[str]] = {
    "sed": frozenset({"-e", "--expression", "-f", "--file"}),
    "awk": frozenset({"-f", "--file", "-e", "--source"}),
    "gawk": frozenset({"-f", "--file", "-e", "--source"}),
    "mawk": frozenset({"-f"}),
    "jq": frozenset({"-f", "--from-file"}),
}

#: Les verbes dont les opérandes **ne désignent aucun fichier** qu'ils touchent :
#: une chaîne à formater, une durée, une conversion de chemin, un test.
VERBES_SANS_FICHIER: frozenset[str] = frozenset(
    {"printf", ":", "true", "false", "sleep", "exit", "return", "shift", "set", "wait",
     "type", "hash", "cygpath", "date", "shopt", "umask", "[[", "(("}
)  # fmt: skip

#: Les verbes qui **déclarent** des variables : leurs `nom=valeur` sont des
#: affectations, qui valent pour la suite de la commande.
DECLARATIONS: frozenset[str] = frozenset({"export", "declare", "local", "readonly", "typeset"})

#: Les variables d'environnement dont la valeur se dit sans le poste : le
#: temporaire (tel que Git Bash le nomme), la maison. Les autres sont inconnues.
VARIABLES_TEMPORAIRE: tuple[str, ...] = ("TEMP", "TMP", "TMPDIR")
TEMPORAIRE = "/tmp"
VARIABLES_MAISON: tuple[str, ...] = ("HOME", "USERPROFILE")

#: Au-delà, les valeurs d'un `for` se jugent comme une seule valeur inconnue : le
#: corps d'une boucle n'a pas à être relu cent fois.
MAX_VALEURS_DE_BOUCLE = 16

#: Le nom qu'un `mktemp` **sans gabarit** fabrique (`tmp.` et dix caractères tirés
#: au hasard), directement sous un dossier temporaire (`/tmp`, `…\Temp`). Chaque
#: appel `Bash` est un shell neuf : un agent qui a créé `T=$(mktemp -d)` dans un
#: appel réutilise **en toutes lettres** le chemin rendu dans le suivant (passage
#: `20260927-104414`, S6 : `T=/tmp/tmp.gv9kMtoQdP && …`). Ce nom ne se vise pas
#: sans l'avoir lu, et ce n'est pas celui des espaces de travail de Maestro
#: (`maestro-<agent>-…`) : il se reconnaît sans mémoire d'un appel à l'autre, et
#: l'évaluation reste une fonction pure.
_NOM_MKTEMP = re.compile(r"tmp\.[A-Za-z0-9]{10}")
_DOSSIERS_TEMPORAIRES = frozenset({"tmp", "temp"})

#: Un chemin absolu à la mode Windows (`C:\\…`, `C:/…`, `\\\\serveur\\part`).
#: Il sert **deux** questions, et c'est d'avoir oublié la seconde que #1278 est
#: né : un chemin d'une racine POSIX qu'il faut faire sortir (un test joué sous
#: Linux doit pouvoir prouver qu'un `C:\\Windows` sort de la racine), et une
#: **racine** Windows, qu'il faut alors juger à la mode Windows sur tous les OS.
_ABSOLU_WINDOWS = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")

#: Un lecteur écrit à la mode MSYS (`/e/…`), la forme que Git Bash donne à
#: l'agent sous Windows. Sous une racine Windows, `/e/Projets` **est**
#: `E:\\Projets` ; sous une racine POSIX, c'est un dossier `/e` comme un autre, et
#: ce motif n'y est jamais consulté.
_LECTEUR_MSYS = re.compile(r"^/([A-Za-z])(?=/|$)")

#: Les suffixes qu'un exécutable porte sous Windows et que le shell n'exige pas :
#: `pip.exe install` **est** `pip install`. Ce ne sont pas les verbes qu'on juge
#: mais leur orthographe, d'où une liste close — celle du `PATHEXT` par défaut
#: pour ce qui se lance d'une ligne de commande —, jamais lue sur le poste : le
#: verdict ne dépend pas de la machine qui juge.
SUFFIXES_EXECUTABLES: tuple[str, ...] = (".exe", ".cmd", ".bat", ".com")

#: Le numéro de version qu'un interpréteur ou son installeur porte dans son nom
#: (`python3.12`, `pip3.12`) : le même programme, sous une autre orthographe.
_VERSION = re.compile(r"[\d.]+$")

#: Les interpréteurs Python, une fois le verbe ramené à sa forme nue : leur
#: `-m pip install` est un `pip install`.
INTERPRETES_PYTHON: frozenset[str] = frozenset({"python", "py"})

#: Les quatre lieux où un chemin peut mener.
RACINE, ETABLI, DEHORS, INCONNU = "racine", "etabli", "dehors", "inconnu"


@dataclass(frozen=True)
class _Etabli:
    """Un dossier que l'agent crée par `mktemp` dans la commande qu'on juge : le sien."""

    numero: int


@dataclass(frozen=True)
class _Inconnu:
    """Une valeur que le texte ne donne pas — sa source, pour le dire."""

    source: str


@dataclass(frozen=True)
class _Motif:
    """Du texte que le shell étendra (`*.png`) : il situe un dossier, il ne nomme pas une cible."""

    texte: str


#: Une valeur : du texte, et ce que le texte ne dit pas, dans l'ordre.
_Morceau = str | _Motif | _Etabli | _Inconnu
_Valeur = tuple[_Morceau, ...]


@dataclass(frozen=True)
class _Lieu:
    """Où mène un chemin. `texte` : le chemin relatif POSIX sous la racine ou sous
    l'établi, le chemin écrit quand il est dehors. `incertain` : sa fin n'est pas
    dans le texte (`src/$x`)."""

    genre: str
    texte: str = ""
    incertain: bool = False


@dataclass
class _Contexte:
    """Ce que le shell sait en lisant la commande : où il est, ce qu'il a affecté.

    `compteur` est partagé entre les copies : chaque `mktemp` est un établi neuf,
    dans un sous-shell comme ailleurs.
    """

    ou: _Valeur
    avant: _Valeur
    variables: dict[str, _Valeur]
    venv: bool
    compteur: list[int]

    def copie(self) -> _Contexte:
        return _Contexte(self.ou, self.avant, dict(self.variables), self.venv, self.compteur)

    def etabli(self) -> _Etabli:
        self.compteur[0] += 1
        return _Etabli(self.compteur[0])


@dataclass(frozen=True)
class PorteeProjet:
    """La portée « projet » d'une session : sa racine, et ce qui s'y trouvait déjà.

    `racine` est le dossier confié à l'agent — l'espace de travail de sa tâche,
    quel que soit le régime : la racine d'un projet non versionné (#839), un
    worktree, ou le répertoire jetable historique.

    `presents` sont les chemins relatifs POSIX **présents quand la tâche a
    commencé** — fichiers et dossiers qui les portent —, c'est-à-dire *ce que
    l'agent n'a pas produit*. Vide hors du régime en place, et c'est une
    décision : un worktree et un répertoire jetable sont des copies, où rien ne
    se perd qu'une fusion ne rattrape ; l'écriture en place, elle, n'a ni l'un ni
    l'autre. La chaîne vide `""` y désigne la racine elle-même.
    """

    racine: Path
    presents: frozenset[str] = frozenset()

    def hors_portee(self, outil: str, arguments: Any) -> str:
        """Le motif qui fait remonter cet appel à une personne — `""` s'il reste dedans.

        Seul l'outil d'exécution est jugé : un `ask` posé sur `Write` ou sur un
        serveur MCP est un choix de politique, et la frontière d'écriture (#839)
        tient déjà les outils de fichiers à la racine. Lever leur arbitrage au nom
        d'une portée reviendrait à défaire la politique qu'on applique.
        """
        if outil != OUTIL_SHELL:
            return ""
        return self.commande_hors_portee(commande_de(arguments))

    def commande_hors_portee(self, commande: str) -> str:
        """Le motif qui fait remonter `commande`, ou `""`.

        Une lecture est dans la portée sans plus d'examen : elle n'agit pas, donc
        elle ne sort de nulle part et ne détruit rien (`maestro.lecture`). Le
        reste est lu par `maestro.shell` et jugé maillon par maillon — il suffit
        d'un qui sorte pour que l'ensemble sorte, la même règle que la couche
        permissions applique à une commande composée.
        """
        if not commande.strip():
            return (
                "appel de l'outil d'exécution sans commande lisible : sa portée ne "
                "peut pas être jugée, donc il revient à une personne."
            )
        if est_lecture(commande):
            return ""
        return self._code(commande, self._contexte())

    # --- La lecture de la commande ------------------------------------------

    def _contexte(self) -> _Contexte:
        variables: dict[str, _Valeur] = {nom: (TEMPORAIRE,) for nom in VARIABLES_TEMPORAIRE}
        variables.update({nom: ("~",) for nom in VARIABLES_MAISON})
        return _Contexte(
            ou=(self._canonique(str(self.racine)),),
            avant=(_Inconnu("$OLDPWD"),),
            variables=variables,
            venv=False,
            compteur=[0],
        )

    def _code(self, code: str, ctx: _Contexte) -> str:
        """Lit `code` et le juge — la commande entière, ou ce qu'elle donne à un shell."""
        try:
            script = lis(code)
        except Illisible as exc:
            return (
                f"commande illisible pour la portée « {PORTEE_PROJET} » ({code[:160]}) : "
                f"{exc} — ce qu'elle exécute ne se lit pas dans son texte."
            )
        return self._script(script, ctx)

    def _script(self, script: Script, ctx: _Contexte) -> str:
        for commande in script.commandes:
            motif = self._commande(commande, ctx)
            if motif:
                return motif
        return ""

    def _commande(self, commande: Commande, ctx: _Contexte) -> str:
        if isinstance(commande, Simple):
            return self._simple(commande, ctx)
        motif = self._executes(_scripts_des(commande.redirections, commande.documents), ctx)
        motif = motif or self._redirections(commande.redirections, ctx)
        if motif:
            return motif
        if isinstance(commande, Groupe):
            return self._script(commande.corps, ctx.copie() if commande.sous_shell else ctx)
        if isinstance(commande, Si):
            return self._si(commande, ctx)
        if isinstance(commande, Selon):
            return self._selon(commande, ctx)
        return self._boucle(commande, ctx)

    def _selon(self, selon: Selon, ctx: _Contexte) -> str:
        """Un `case` : son sujet, puis chaque branche comme celles d'un `if`."""
        motif = self._executes(selon.sujet.scripts(), ctx)
        if motif:
            return motif
        tours = [ctx.copie()]  # aucune branche prise
        for corps in selon.branches:
            tour = ctx.copie()
            motif = self._script(corps, tour)
            if motif:
                return motif
            tours.append(tour)
        _fusionne(ctx, tours)
        return ""

    def _si(self, si: Si, ctx: _Contexte) -> str:
        """Chaque branche est jugée ; ce qu'elles laissent de différent devient inconnu."""
        tours: list[_Contexte] = []
        for condition, corps in si.branches:
            motif = self._script(condition, ctx)
            if motif:
                return motif
            tour = ctx.copie()
            motif = self._script(corps, tour)
            if motif:
                return motif
            tours.append(tour)
        tour = ctx.copie()
        motif = self._script(si.sinon, tour) if si.sinon is not None else ""
        if motif:
            return motif
        tours.append(tour)
        _fusionne(ctx, tours)
        return ""

    def _boucle(self, boucle: Boucle, ctx: _Contexte) -> str:
        """Le corps est jugé une fois par valeur que le texte donne — une seule fois sinon."""
        if boucle.condition is not None:
            motif = self._script(boucle.condition, ctx)
            if motif:
                return motif
        valeurs: list[_Valeur] = [(_Inconnu(f"${boucle.variable}"),)]
        if boucle.valeurs is not None:
            motif = self._executes([s for mot in boucle.valeurs for s in mot.scripts()], ctx)
            if motif:
                return motif
            if len(boucle.valeurs) <= MAX_VALEURS_DE_BOUCLE:
                valeurs = [self._valeur(mot, ctx) for mot in boucle.valeurs]
        tours = [ctx.copie()]  # la boucle peut ne pas tourner
        for valeur in valeurs:
            tour = ctx.copie()
            if boucle.variable:
                tour.variables[boucle.variable] = valeur
            motif = self._script(boucle.corps, tour)
            if motif:
                return motif
            tours.append(tour)
        _fusionne(ctx, tours)
        return ""

    def _simple(self, simple: Simple, ctx: _Contexte) -> str:
        """Une commande simple : ce qu'elle exécute d'autre, ce qu'elle écrit, ce qu'elle lance."""
        scripts = [s for mot in simple.mots for s in mot.scripts()]
        scripts += [s for _, mot in simple.affectations for s in mot.scripts()]
        scripts += _scripts_des(simple.redirections, simple.documents)
        motif = self._executes(scripts, ctx) or self._redirections(simple.redirections, ctx)
        if motif:
            return motif
        if not simple.mots:
            for nom, mot in simple.affectations:
                ctx.variables[nom] = self._valeur(mot, ctx)
            return ""
        mots = [(mot, self._valeur(mot, ctx)) for mot in simple.mots]
        return self._appel(simple, mots, ctx, flux=False)

    def _executes(self, scripts: Sequence[Script], ctx: _Contexte) -> str:
        """Ce qu'une substitution ou un heredoc exécute — dans un sous-shell, donc une copie."""
        for script in scripts:
            motif = self._script(script, ctx.copie())
            if motif:
                return motif
        return ""

    def _appel(
        self,
        simple: Simple,
        mots: list[tuple[Mot, _Valeur]],
        ctx: _Contexte,
        *,
        flux: bool,
    ) -> str:
        """Le programme que lance cette commande, et ce qu'il fait de ses arguments."""
        (premier, valeur), *arguments = mots
        verbe = _texte(valeur)
        if verbe is None:
            if not isinstance(valeur[0], _Etabli):
                return (
                    f"commande hors de la portée « {PORTEE_PROJET} » : le programme qu'elle "
                    f"lance (« {premier.rendu()} ») n'est pas dans son texte. Une personne "
                    "tranche."
                )
            verbe = premier.rendu()  # un programme que l'agent a posé dans son établi
        nom = _nom_du_verbe(verbe)
        motif = self._sort_par_le_verbe(valeur, verbe, nom, arguments, ctx)
        if motif:
            return motif
        if nom in ENVELOPPES:
            deballe = _deballe(nom, arguments)
            if deballe is None:
                return ""
            return self._appel(simple, deballe, ctx, flux=flux or nom == "xargs")
        rendus = [_texte(v) or mot.rendu() for mot, v in arguments]
        if nom in ("cd", "pushd"):
            self._cd(arguments, ctx)
            return ""
        if nom == "popd":
            ctx.avant, ctx.ou = ctx.ou, (_Inconnu("popd"),)
            return ""
        if nom in DECLARATIONS:
            for mot, _ in arguments:
                paire = affectation(mot)
                if paire is not None:
                    ctx.variables[paire[0]] = self._valeur(paire[1], ctx)
            return ""
        if nom in ("read", "unset"):
            for rendu in rendus:
                if not rendu.startswith("-"):
                    ctx.variables[rendu] = (_Inconnu(f"${rendu}"),)
            return ""
        if nom == "deactivate":
            ctx.venv = False
            return ""
        if nom == "mktemp" or nom in VERBES_SANS_FICHIER:
            return ""
        if nom in ("source", ".") and self._active_un_venv(arguments, ctx):
            ctx.venv = True
        if nom == "trap":
            code = next((r for r in rendus if not r.startswith("-")), "")
            return self._code(code, ctx.copie()) if code else ""
        if nom == "eval":
            textes = [_texte(v) for _, v in arguments]
            if any(texte is None for texte in textes):
                return _motif_programme_d_entree("eval")
            return self._code(" ".join(t for t in textes if t is not None), ctx)
        if nom in COQUILLES:
            motif_coquille = self._coquille(simple, nom, arguments, ctx)
            if motif_coquille is not None:
                return motif_coquille
        if nom in COQUILLES_ETRANGERES:
            return _coquille_etrangere(
                nom,
                ntpath.basename(verbe),
                rendus,
                lambda chemin: self._lieu((chemin,), ctx).genre in (RACINE, ETABLI),
            )
        if simple_lit([verbe, *rendus]):
            return ""
        for valeur_operande, rendu in _operandes(nom, arguments):
            texte = _texte(valeur_operande) or ""
            if texte in PERIPHERIQUES or texte.startswith("/dev/fd/"):
                continue  # un périphérique n'est pas un chemin du disque
            lieu = self._lieu(valeur_operande, ctx)
            if lieu.genre == DEHORS:
                return _motif_hors_racine(rendu, self.racine, self._ailleurs(lieu, ctx))
        if nom == "find":
            return self._find(simple, arguments, ctx)
        return self._destruction(verbe, nom, arguments, ctx, flux=flux)

    def _find(self, simple: Simple, arguments: list[tuple[Mot, _Valeur]], ctx: _Contexte) -> str:
        """Un `find` qui agit : ce que ses tests désignent, confronté à ce qui était là.

        Le geste que #1226 voulait laisser passer — le ménage des caches que ses
        propres exécutions viennent de produire — s'écrit le plus souvent ainsi :
        `find src -name __pycache__ -exec rm -rf {} +` (passage `20260927-131322`,
        S3). Ses cibles ne sont pas des arguments, mais elles se **décrivent** : sous
        ces racines, les entrées dont le nom répond à ce motif. Si aucune entrée
        présente avant la tâche n'y répond, l'agent n'efface que ce qu'il a produit.

        Ce qui ne se confronte pas remonte, comme avant : un test qu'on ne sait pas
        lire (`-o`, `-regex`, `-newer`…), une destruction sans `-name`, une racine
        qu'on ne situe pas. Une commande donnée à `-exec` qui lit passe ; une autre
        est jugée comme n'importe quelle commande dont les cibles viennent d'ailleurs.
        """
        rendus = [mot.rendu() for mot, _ in arguments]
        if not any(rendu in FIND_AGISSANTES for rendu in rendus):
            return ""
        illisible = (
            "commande hors de la portée : `find` agit ici au lieu de lister "
            "(-delete/-exec), et ce qu'il atteindra ne se lit pas dans ses "
            "arguments. Une personne tranche."
        )
        index = 0
        racines: list[_Valeur] = []
        while index < len(rendus) and not rendus[index].startswith(("-", "(", ")", "!")):
            racines.append(arguments[index][1])
            index += 1
        noms: list[tuple[str, bool]] = []
        exclus: list[tuple[str, bool]] = []
        commande: list[tuple[Mot, _Valeur]] = []
        supprime, lisible, negation = False, True, False
        while index < len(rendus):
            rendu = rendus[index]
            suivant = rendus[index + 1] if index + 1 < len(rendus) else None
            if rendu in ("-not", "!"):
                negation = True
                index += 1
                continue
            if rendu in FIND_NOMS and suivant is not None:
                (exclus if negation else noms).append((suivant, rendu == "-iname"))
                negation = False
                index += 2
                continue
            if rendu in FIND_CHEMINS and suivant is not None and negation:
                negation = False  # une exclusion ne fait que retirer des cibles
                index += 2
                continue
            if rendu in FIND_NEUTRES_A_VALEUR and suivant is not None:
                index += 2
                continue
            if rendu in FIND_NEUTRES:
                index += 1
                continue
            if rendu == "-delete":
                supprime = True
                index += 1
                continue
            if rendu in ("-exec", "-execdir", "-ok", "-okdir"):
                fin = next(
                    (j for j in range(index + 1, len(rendus)) if rendus[j] in (";", "+")), None
                )
                if fin is None:
                    return illisible
                commande = arguments[index + 1 : fin]
                index = fin + 1
                continue
            lisible = False
            index += 1
        if commande:
            verbe = _texte(commande[0][1]) or commande[0][0].rendu()
            if _nom_du_verbe(verbe) in VERBES_DESTRUCTEURS:
                supprime = True
            else:
                sans_cible = [(m, v) for m, v in commande if m.rendu() != "{}"]
                return self._appel(simple, sans_cible, ctx, flux=True) if sans_cible else ""
        if not supprime:
            return "" if lisible else illisible
        if not lisible or not noms:
            return illisible
        for racine in racines or [(".",)]:
            lieu = self._lieu(racine, ctx)
            if lieu.genre == ETABLI and not lieu.incertain:
                continue
            if lieu.genre == DEHORS:
                return _motif_hors_racine(_texte(racine) or ".", self.racine, lieu.texte)
            if lieu.genre != RACINE or lieu.incertain:
                return illisible
            present = self._present_repondant(lieu.texte, noms, exclus)
            if present is not None:
                return (
                    f"commande hors de la portée : `find` effacerait « {present} », qui se "
                    "trouvait déjà dans le projet quand la tâche a commencé — l'agent ne "
                    "l'a pas produit. En écriture en place, rien ne passe par une fusion ni "
                    "par un diff : une personne tranche."
                )
        return ""

    def _present_repondant(
        self, racine: str, noms: list[tuple[str, bool]], exclus: list[tuple[str, bool]]
    ) -> str | None:
        """Une entrée présente avant la tâche, sous `racine`, dont le nom répond aux motifs."""
        windows = _racine_windows(self.racine)
        for present in sorted(self.presents):
            if not present:
                continue
            dessous = not racine or present == racine or present.startswith(racine + "/")
            if windows:
                bas = present.casefold()
                dessous = not racine or bas == racine.casefold() or bas.startswith(
                    racine.casefold() + "/"
                )
            if not dessous:
                continue
            nom = present.rsplit("/", 1)[-1]
            if all(_repond(nom, motif, casse or windows) for motif, casse in noms) and not any(
                _repond(nom, motif, casse or windows) for motif, casse in exclus
            ):
                return present
        return None

    # --- Les deux familles qui remontent -------------------------------------

    def _sort_par_le_verbe(
        self,
        valeur: _Valeur,
        verbe: str,
        nom: str,
        arguments: list[tuple[Mot, _Valeur]],
        ctx: _Contexte,
    ) -> str:
        """Le motif d'un verbe qui sort du projet sans nommer de chemin — `""` sinon."""
        rendus = [_texte(v) or mot.rendu() for mot, v in arguments]
        motif = _verbe_hors_projet(verbe, rendus)
        if motif and nom not in VERBES_HORS_PROJET:
            if self._installe_dans_le_projet(valeur, verbe, arguments, ctx):
                return ""
        return motif

    def _installe_dans_le_projet(
        self, valeur: _Valeur, verbe: str, arguments: list[tuple[Mot, _Valeur]], ctx: _Contexte
    ) -> bool:
        """Cette installation `pip` atterrit-elle dans le projet ? (P6 du bouclage, #1348)

        Trois façons, et seulement pour `pip` — un `npm -g` est global par
        définition : l'interpréteur qui l'exécute est **dans** le projet (ou dans
        l'établi), un venv du projet a été activé plus haut dans la commande, ou
        `--target` (`--prefix`, `--root`) vise le projet. `--user` l'envoie chez
        l'utilisateur quoi qu'il arrive.
        """
        if _forme_nue(_nom_du_verbe(verbe)) not in INTERPRETES_PYTHON | {"pip"}:
            return False
        rendus = [mot.rendu() for mot, _ in arguments]
        if OPTION_UTILISATEUR_PIP in rendus:
            return False
        if any(separateur in verbe for separateur in "/\\") or isinstance(valeur[0], _Etabli):
            if self._lieu(valeur, ctx).genre in (RACINE, ETABLI):
                return True
        if ctx.venv:
            return True
        destination = _valeur_d_option(arguments, OPTIONS_DESTINATION_PIP)
        if destination is None:
            return False
        lieu = self._lieu(destination, ctx)
        return lieu.genre in (RACINE, ETABLI) and not lieu.incertain

    def _active_un_venv(self, arguments: list[tuple[Mot, _Valeur]], ctx: _Contexte) -> bool:
        """`source <venv>/bin/activate` : un venv du projet prend la place de l'interpréteur."""
        for mot, valeur in arguments:
            if mot.rendu().startswith("-"):
                continue
            lieu = self._lieu(valeur, ctx)
            nom = lieu.texte.rsplit("/", 1)[-1].lower()
            return lieu.genre in (RACINE, ETABLI) and nom.startswith("activate")
        return False

    def _coquille(
        self, simple: Simple, nom: str, arguments: list[tuple[Mot, _Valeur]], ctx: _Contexte
    ) -> str | None:
        """Ce qu'on donne à un shell POSIX — `None` s'il lance un script, jugé comme un fichier."""
        for index, (mot, _) in enumerate(arguments):
            rendu = mot.rendu()
            if rendu == "-c" or (re.fullmatch(r"-[a-z]*c[a-z]*", rendu) is not None):
                if index + 1 >= len(arguments):
                    return _motif_programme_d_entree(nom)
                code = _texte(arguments[index + 1][1])
                if code is None:
                    return _motif_programme_d_entree(nom)
                return self._code(code, ctx.copie())
            if not rendu.startswith("-") or rendu == "-":
                if rendu != "-":
                    return None
                break
        if simple.documents:
            for document in simple.documents:
                motif = self._code(document.corps, ctx.copie())
                if motif:
                    return motif
            return ""
        if any(r.operateur == "<" for r in simple.redirections):
            return None  # un script lu dans un fichier : une donnée, comme `bash script.sh`
        return _motif_programme_d_entree(nom)

    def _destruction(
        self,
        verbe: str,
        nom: str,
        arguments: list[tuple[Mot, _Valeur]],
        ctx: _Contexte,
        *,
        flux: bool,
    ) -> str:
        """Ce que ce verbe détruirait et que l'agent n'a pas produit — `""` sinon.

        Le verbe se reconnaît sous toutes ses orthographes (`_nom_du_verbe`) : un
        `/bin/rm` ou un `rm.exe` efface autant qu'un `rm`.
        """
        if nom not in VERBES_DESTRUCTEURS:
            return ""
        if flux:
            return (
                f"commande hors de la portée : `{verbe}` reçoit ses cibles de son entrée "
                "(`xargs`), donc des chemins qu'on ne peut pas juger ici. Une personne "
                "tranche."
            )
        for rendu, cible in _cibles(arguments):
            if any(isinstance(morceau, _Motif) for morceau in cible):
                return (
                    f"commande hors de la portée : `{verbe} {rendu}` vise un motif "
                    "que le shell étendra, donc des chemins qu'on ne peut pas juger "
                    "ici. Une personne tranche."
                )
            lieu = self._lieu(cible, ctx)
            if lieu.genre == INCONNU or lieu.incertain:
                return (
                    f"commande hors de la portée : `{verbe} {rendu}` vise une cible que "
                    "son texte ne donne pas, donc un chemin qu'on ne peut pas juger ici. "
                    "Une personne tranche."
                )
            if lieu.genre == DEHORS:
                return _motif_hors_racine(rendu, self.racine, self._ailleurs(lieu, ctx))
            if lieu.genre == RACINE and self._presente(lieu.texte):
                return (
                    f"commande hors de la portée : `{verbe}` viserait « {rendu} », "
                    "qui se trouvait déjà dans le projet quand la tâche a commencé — "
                    "l'agent ne l'a pas produit. En écriture en place, rien ne passe "
                    "par une fusion ni par un diff : une personne tranche."
                )
        return ""

    def _redirections(self, redirections: Sequence[Redirection], ctx: _Contexte) -> str:
        """Une redirection qui sortirait, écrirait on ne sait où, ou écraserait du préexistant."""
        for redirection in redirections:
            operateur, rendu = redirection.operateur, redirection.cible.rendu()
            if operateur in REDIRECTIONS_LUES:
                continue
            if operateur in (">&", "<&") and (rendu.isdigit() or rendu == "-"):
                continue
            valeur = self._valeur(redirection.cible, ctx)
            texte = _texte(valeur)
            if texte in PUITS_DE_SORTIE or (texte or "").startswith("/dev/fd/"):
                continue
            lieu = self._lieu(valeur, ctx)
            if lieu.genre == ETABLI and not lieu.incertain:
                continue
            if lieu.genre == INCONNU or lieu.incertain:
                return (
                    f"commande hors de la portée : elle écrirait dans « {rendu} », dont "
                    "son texte ne dit pas où il se trouve. Une personne tranche."
                )
            if lieu.genre == DEHORS:
                return _motif_hors_racine(rendu, self.racine, self._ailleurs(lieu, ctx))
            if operateur in REDIRECTIONS_ECRASANTES and self._presente(lieu.texte):
                return (
                    f"commande hors de la portée : la redirection écraserait « {rendu} », "
                    "présent dans le projet avant la tâche. Une personne tranche."
                )
        return ""

    # --- Ce que valent les mots ---------------------------------------------

    def _valeur(self, mot: Mot, ctx: _Contexte) -> _Valeur:
        """Ce que `mot` vaut, autant que le texte le dit."""
        morceaux: list[_Morceau] = []
        for partie in mot.parties:
            if partie.genre in (LITTERAL, TILDE):
                morceaux.append(partie.texte)
            elif partie.genre == VARIABLE and not partie.modifiee:
                morceaux.extend(self._variable(partie.texte, ctx))
            elif partie.genre == SUBSTITUTION:
                morceaux.extend(self._substitution(partie, ctx))
            else:
                morceaux.append(_Inconnu(partie.source))
        valeur = _fond(morceaux)
        if mot.glob:
            return tuple(_Motif(m) if isinstance(m, str) else m for m in valeur)
        return valeur

    @staticmethod
    def _variable(nom: str, ctx: _Contexte) -> _Valeur:
        if nom == "PWD":
            return ctx.ou
        if nom == "OLDPWD":
            return ctx.avant
        return ctx.variables.get(nom, (_Inconnu(f"${nom}"),))

    def _substitution(self, partie: Partie, ctx: _Contexte) -> _Valeur:
        """Ce que rend une substitution, quand son texte le dit : `mktemp`, `pwd`, `cygpath`…"""
        inconnue: _Valeur = (_Inconnu(partie.source),)
        (script,) = partie.scripts
        if len(script.commandes) != 1 or not isinstance(script.commandes[0], Simple):
            return inconnue
        simple = script.commandes[0]
        if not simple.mots or simple.affectations:
            return inconnue
        verbe = _texte(self._valeur(simple.mots[0], ctx))
        nom = _nom_du_verbe(verbe) if verbe else ""
        if nom == "mktemp":
            return (ctx.etabli(),)
        if nom == "pwd":
            return ctx.ou
        operandes = [
            self._valeur(mot, ctx) for mot in simple.mots[1:] if not mot.rendu().startswith("-")
        ]
        if not operandes:
            return inconnue
        dernier = operandes[-1]
        if nom in ("cygpath", "realpath", "readlink"):
            return dernier
        texte = _texte(dernier)
        if texte is None:
            return inconnue
        if nom == "dirname":
            return (re.sub(r"[\\/][^\\/]*$", "", texte.rstrip("\\/")) or ".",)
        if nom == "basename":
            return (re.split(r"[\\/]", texte.rstrip("\\/"))[-1],)
        if nom == "echo":
            textes = [_texte(v) for v in operandes]
            return (" ".join(textes),) if all(t is not None for t in textes) else inconnue  # type: ignore[arg-type]
        return inconnue

    def _cd(self, arguments: list[tuple[Mot, _Valeur]], ctx: _Contexte) -> None:
        """`cd` : le dossier d'où partent désormais les chemins relatifs."""
        operandes = [
            (m, v) for m, v in arguments if not (m.rendu().startswith("-") and m.rendu() != "-")
        ]
        if not operandes:
            cible: _Valeur = ("~",)
        elif operandes[0][0].rendu() == "-":
            cible = ctx.avant
        else:
            cible = operandes[0][1]
        ctx.avant, ctx.ou = ctx.ou, self._absolu(cible, ctx)

    # --- Ce que la racine répond ------------------------------------------

    def _absolu(self, valeur: _Valeur, ctx: _Contexte) -> _Valeur:
        """`valeur` relue depuis le dossier courant : un chemin absolu, un établi, ou l'inconnu."""
        if not valeur:
            return ctx.ou
        tete = valeur[0]
        if isinstance(tete, (_Etabli, _Inconnu)):
            return valeur
        texte = _prefixe(valeur)
        reste = valeur[_longueur_du_prefixe(valeur) :]
        if texte.startswith("~"):
            texte = os.path.expanduser(texte)
        if self._est_absolu(texte):
            return (self._canonique(texte), *reste)
        ou = ctx.ou
        if isinstance(ou[0], _Inconnu):
            return (ou[0],)
        if isinstance(ou[0], _Etabli):
            return (ou[0], _prefixe(ou[1:]) + "/" + texte, *reste)
        return (self._canonique(self._joint(_prefixe(ou), texte)), *reste)

    def _lieu(self, valeur: _Valeur, ctx: _Contexte) -> _Lieu:
        """Où mène `valeur`, lue depuis le dossier courant."""
        absolu = self._absolu(valeur, ctx)
        tete = absolu[0] if absolu else ""
        incertain = any(isinstance(m, (_Etabli, _Inconnu)) for m in absolu[1:])
        if isinstance(tete, _Inconnu):
            return _Lieu(INCONNU)
        if isinstance(tete, _Etabli):
            suite = _prefixe(absolu[1:])
            if suite and suite[0] not in "\\/":
                return _Lieu(DEHORS, f"(dossier de mktemp){suite}")
            relatif = _sous_etabli(suite)
            if relatif is None:
                return _Lieu(DEHORS, f"(dossier de mktemp){suite}")
            return _Lieu(ETABLI, relatif, incertain)
        texte = _prefixe(absolu)
        relatif = self._sous_la_racine(texte)
        if relatif is not None:
            return _Lieu(RACINE, relatif, incertain)
        sous_mktemp = _sous_un_mktemp(texte)
        if sous_mktemp is not None:
            return _Lieu(ETABLI, sous_mktemp, incertain)
        return _Lieu(DEHORS, texte, incertain)

    def _ailleurs(self, lieu: _Lieu, ctx: _Contexte) -> str:
        """Le chemin résolu, à dire quand un `cd` a quitté la racine — `""` sinon.

        Après un `cd` dehors, « carnet.md » ne dit pas où il se trouve. Depuis la
        racine, le chemin écrit suffit, et le taire garde hors du motif ce que
        l'appelant retire (la copie de vérification de l'outillage).
        """
        if ctx.ou == (self._canonique(str(self.racine)),):
            return ""
        return lieu.texte

    def _est_absolu(self, texte: str) -> bool:
        return bool(_ABSOLU_WINDOWS.match(texte)) or texte.startswith(("/", "\\"))

    def _joint(self, base: str, texte: str) -> str:
        if _racine_windows(self.racine):
            return ntpath.join(base, texte)
        return posixpath.join(base, texte)

    def _canonique(self, texte: str) -> str:
        """`texte` dans l'orthographe de la racine : lecteur MSYS ramené, `..` replié."""
        if not _racine_windows(self.racine):
            return texte if _ABSOLU_WINDOWS.match(texte) else posixpath.normpath(texte)
        lecteur = _LECTEUR_MSYS.match(texte)
        if lecteur:
            texte = f"{lecteur.group(1)}:{texte[lecteur.end() :] or '/'}"
        return ntpath.normpath(texte)

    def _presente(self, relatif: str) -> bool:
        """`relatif` était-il déjà dans le projet quand la tâche a commencé ?

        À la casse près sous une racine Windows, pour la raison de la racine
        elle-même : `NOTES.md` y **est** `notes.md`, et l'effacer efface ce que la
        personne avait posé.
        """
        if not _racine_windows(self.racine):
            return relatif in self.presents
        return relatif.casefold() in {present.casefold() for present in self.presents}

    def relatif(self, brut: str) -> str:
        """Le chemin de `brut` relatif à la racine, en POSIX — `""` pour la racine.

        Public parce que c'est aussi la forme dans laquelle `presents` se relève
        (`maestro.sandbox.en_place.presents_de`) : deux orthographes du même
        chemin feraient un relevé qui ne répond jamais. Un chemin hors de la
        racine rend aussi `""` : ce verbe ne sert qu'après qu'on l'a situé dedans.
        """
        return self._sous_la_racine(brut) or ""

    def _sous_la_racine(self, brut: str) -> str | None:
        """Le chemin relatif POSIX de `brut` sous la racine, `""` pour elle, `None` dehors.

        La seule lecture d'un chemin de ce module, pour que « est-ce dedans ? »
        et « qu'est-ce que c'est ? » ne divergent jamais (#1278). Elle se fait
        **selon la racine**, pas selon l'OS qui juge : une racine Windows se lit
        à la mode Windows — lecteur, deux séparateurs, casse, forme MSYS de Git
        Bash —, une racine POSIX à la mode POSIX. C'est ce qui rend le même
        verdict sur le poste qui exécute et dans le conteneur Linux qui l'éprouve.
        """
        texte = brut.strip()
        if texte.startswith("~"):
            texte = os.path.expanduser(texte)
        if _racine_windows(self.racine):
            return _sous_racine_windows(texte, str(self.racine))
        return _sous_racine_posix(texte, str(self.racine))


def hors_de_portee(portee: str, evaluateur: PorteeProjet | None, outil: str, arguments: Any) -> str:
    """Le motif qui fait sortir cet appel de `portee` — `""` s'il y reste.

    Le verbe que consulte le hook du fournisseur
    (`maestro.providers.claude._hook_permissions`). Trois cas, et le sens de
    chacun est le même : *on ne franchit une portée que si l'on sait qu'on y
    reste*.

    - **aucune portée déclarée** : le cran vaut pour tout appel, rien à juger ;
    - **portée déclarée, évaluateur absent** — un appelant qui n'ouvre pas
      d'espace de travail : on ne sait pas juger, donc le défaut reprend la main ;
    - **portée inconnue** : pareil. Le chargement de la politique la refuse déjà
      (`maestro.agents.permissions`), et ceci est la seconde moitié de la même
      asymétrie, pour ce qui arriverait ici par un autre chemin.
    """
    if not portee:
        return ""
    if portee != PORTEE_PROJET:
        return (
            f"portée « {portee} » inconnue de l'exécution : le cran ne peut pas "
            "être appliqué, donc l'appel revient à une personne."
        )
    if evaluateur is None:
        return (
            f"portée « {portee} » non évaluable ici : aucune racine de projet n'est "
            "montée sur cette session, donc l'appel revient à une personne."
        )
    return evaluateur.hors_portee(outil, arguments)


# --- Les verbes, par leur nom ---------------------------------------------


def _verbe_hors_projet(verbe: str, arguments: Sequence[str]) -> str:
    """Le motif d'un verbe qui sort du projet sans nommer de chemin — `""` sinon."""
    ecrit = ntpath.basename(verbe)
    nom = _nom_du_verbe(verbe)
    if nom in VERBES_HORS_PROJET:
        return (
            f"commande hors de la portée « {PORTEE_PROJET} » : `{ecrit}` agit sur la "
            "machine et non dans le dossier du projet. Une personne tranche."
        )
    if _installe_hors_projet(nom, list(arguments)):
        return (
            f"commande hors de la portée « {PORTEE_PROJET} » : `{ecrit}` installe "
            "hors du dossier du projet. Une personne tranche."
        )
    return ""


def _nom_du_verbe(verbe: str) -> str:
    """Le nom sous lequel on reconnaît `verbe`, quelle que soit son orthographe (#1278).

    Le verbe d'une commande s'écrit de bien des façons pour un même programme :
    un chemin (`/usr/bin/pip`, `C:\\Python313\\python.exe`, la forme MSYS
    `/c/…/python.exe`), une casse (`PIP.EXE`), un suffixe d'exécutable Windows
    (`.exe`, `.cmd`…). Le comparer tel quel aux listes de ce module laissait
    `python.exe -m pip install` **dans** la portée : le run `e5e1a7058fc5` a
    installé `rich` dans le Python du poste sans que personne le voie.

    Ramené ici à son nom nu, en minuscules et sans suffixe. Le numéro de
    version, lui, reste — c'est `_forme_nue` qui l'ôte, là où il le faut.
    """
    nom = ntpath.basename(verbe).lower()
    for suffixe in SUFFIXES_EXECUTABLES:
        if nom.endswith(suffixe) and len(nom) > len(suffixe):
            return nom[: -len(suffixe)]
    return nom


def _forme_nue(nom: str) -> str:
    """`nom` sans son numéro de version : `python3.12` → `python`, `pip3` → `pip`."""
    return _VERSION.sub("", nom) or nom


def _installe_hors_projet(nom: str, arguments: list[str]) -> bool:
    """Cette commande installe-t-elle, par défaut, ailleurs que dans le dossier du projet ?

    `python -m pip install` compte comme `pip install` : c'est le même geste
    écrit autrement, et l'agent l'écrit souvent ainsi quand l'exécutable n'est pas
    dans son chemin. L'interpréteur et l'installeur se reconnaissent **sans leur
    numéro de version** : `python3.12` est un `python`, `pip3.12` un `pip`. Ce
    qu'on sait de **cette** commande — l'interpréteur du projet, un venv activé —
    est l'affaire de `PorteeProjet._installe_dans_le_projet`.
    """
    nom = _forme_nue(nom)
    if nom in INTERPRETES_PYTHON and "pip" in arguments:
        nom, arguments = "pip", arguments[arguments.index("pip") + 1 :]
    drapeaux = INSTALLATIONS_HORS_PROJET.get(nom)
    if drapeaux is None:
        return False
    if not any(argument in SOUS_COMMANDES_INSTALLATION for argument in arguments):
        return False
    return not drapeaux or any(argument in drapeaux for argument in arguments)


def _coquille_etrangere(
    nom: str, ecrit: str, rendus: Sequence[str], du_projet: Callable[[str], bool]
) -> str:
    """Le programme donné à PowerShell ou à `cmd`, parcouru pour ses verbes qui sortent.

    Un autre langage : on n'en lit pas la grammaire, seulement la tête de chacune
    de ses commandes — c'est là que vivent `pip install`, `winget` et leurs
    voisins. Le reste est une donnée, comme le code de `python -c`.

    `du_projet` dit si un programme nommé par son chemin vit dans le projet : un
    `.venv\\Scripts\\python -m pip install` installe dans le projet, ici comme au
    shell (passage `20260927-104414`, S9).
    """
    options = COQUILLES_ETRANGERES[nom]
    for index, rendu in enumerate(rendus):
        if nom != "cmd" and rendu.lower() in PROGRAMMES_ENCODES:
            return (
                f"commande hors de la portée « {PORTEE_PROJET} » : `{ecrit}` exécute un "
                "programme encodé, illisible dans son texte. Une personne tranche."
            )
        if rendu.lower() in options:
            for segment in re.split(r"&&|\|\||[;|&\n]", " ".join(rendus[index + 1 :])):
                jetons = [j.strip("'\"()") for j in segment.split()]
                while jetons and jetons[0] in ("&", ".", ""):
                    jetons.pop(0)
                if len(jetons) > 2 and jetons[1] == "=":
                    jetons = jetons[2:]
                if not jetons:
                    continue
                verbe = jetons[0]
                motif = _verbe_hors_projet(verbe, jetons[1:])
                chemin = any(separateur in verbe for separateur in "/\\")
                if motif and chemin and _nom_du_verbe(verbe) not in VERBES_HORS_PROJET:
                    if du_projet(verbe):
                        continue  # l'interpréteur du projet installe dans le projet
                if motif:
                    return motif
            return ""
    return ""


def _deballe(nom: str, arguments: list[tuple[Mot, _Valeur]]) -> list[tuple[Mot, _Valeur]] | None:
    """La commande qu'une enveloppe lance — `None` si elle n'en lance aucune."""
    index = 0
    while index < len(arguments):
        rendu = arguments[index][0].rendu()
        if rendu == "--":
            index += 1
            break
        if nom == "env" and affectation(arguments[index][0]) is not None:
            index += 1
            continue
        if rendu.startswith("-") and rendu != "-":
            if nom == "command" and rendu in ("-v", "-V"):
                return None  # elle décrit la commande, elle ne la lance pas
            index += 2 if rendu in ENVELOPPES[nom] else 1
            continue
        break
    if nom == "timeout":
        index += 1  # la durée
    return arguments[index:] or None


def _operandes(nom: str, arguments: list[tuple[Mot, _Valeur]]) -> Iterator[tuple[_Valeur, str]]:
    """Ce que les arguments **nomment** comme chemins, à situer depuis le dossier courant.

    Un opérande compte tel qu'il est, nom nu compris : relatif, il désigne un
    fichier du dossier courant — de la racine en général, de là-bas après un
    `cd` dehors. Une option ne compte que par la valeur qu'elle porte, et
    seulement si cette valeur ressemble à un chemin (`--sortie=../x`,
    `-I/usr/include`) : `--headless=new` n'en nomme aucun. Le code d'un
    interpréteur (`-c`, `-m`) et le programme de `sed` ou d'`awk` n'en sont pas.
    """
    nu = _forme_nue(nom)
    programme = OPTIONS_DE_PROGRAMME.get(nu, frozenset())
    premier = PROGRAMME_EN_PREMIER.get(nu)
    rendus = [mot.rendu() for mot, _ in arguments]
    deja_donne = premier is not None and any(
        rendu.split("=", 1)[0] in premier for rendu in rendus if rendu.startswith("-")
    )
    attend_programme = premier is not None and not deja_donne
    saute, options_finies = False, False
    for (_, valeur), rendu in zip(arguments, rendus, strict=True):
        if saute:
            saute = False
            continue
        if not options_finies and rendu == "--":
            options_finies = True
            continue
        if not options_finies and rendu.startswith("-") and rendu != "-":
            if rendu in programme:
                saute = True
                continue
            option = _valeur_collee(valeur)
            if option is not None:
                yield option, rendu
            continue
        if rendu == "-" or not valeur:
            continue  # l'entrée standard, ou un argument vide : aucun chemin
        if attend_programme:
            attend_programme = False
            continue
        yield valeur, rendu


def _valeur_collee(valeur: _Valeur) -> _Valeur | None:
    """La valeur qu'une option porte (`--x=…`, `-I…`), si elle ressemble à un chemin."""
    tete = valeur[0] if valeur else ""
    if not isinstance(tete, str):
        return None
    if "=" in tete:
        suite: _Valeur = (tete.partition("=")[2], *valeur[1:])
    else:
        coupe = min((i for i in (tete.find("/"), tete.find("\\")) if i > 0), default=-1)
        if coupe < 0:
            return None
        suite = (tete[coupe:], *valeur[1:])
    suite = _fond(list(suite))
    if not suite:
        return None
    debut = suite[0]
    if isinstance(debut, (_Etabli, _Inconnu)):
        return suite
    texte = debut.texte if isinstance(debut, _Motif) else debut
    if "/" in texte or "\\" in texte or texte.startswith("~") or texte == "..":
        return suite
    return None


def _valeur_d_option(
    arguments: list[tuple[Mot, _Valeur]], options: frozenset[str]
) -> _Valeur | None:
    """La valeur de la première de `options`, séparée (`--target x`) ou collée (`--target=x`)."""
    for index, (mot, valeur) in enumerate(arguments):
        rendu = mot.rendu()
        if rendu in options and index + 1 < len(arguments):
            return arguments[index + 1][1]
        nom, egal, _ = rendu.partition("=")
        if egal and nom in options:
            tete = valeur[0]
            if isinstance(tete, str):
                return _fond([tete.partition("=")[2], *valeur[1:]])
    return None


def _cibles(arguments: list[tuple[Mot, _Valeur]]) -> Iterator[tuple[str, _Valeur]]:
    """Les arguments d'un verbe destructeur qui désignent ce qu'il va détruire.

    Tout ce qui n'est pas une option : `rm -rf build .cache` vise `build` et
    `.cache`. Un nom nu compte ici — c'est la cible du geste.

    Un opérande `clé=valeur` rend **les deux** : `dd of=sortie` ne nomme pas plus
    sa cible autrement, et ne garder que le jeton entier ferait passer un `dd`
    pour un geste sans cible.
    """
    for mot, valeur in arguments:
        rendu = mot.rendu()
        if rendu == "--" or rendu.startswith("-") or not valeur:
            continue
        yield rendu, valeur
        tete = valeur[0] if valeur else ""
        if isinstance(tete, str) and "=" in tete:
            yield rendu, _fond([tete.partition("=")[2], *valeur[1:]])


# --- Les valeurs ----------------------------------------------------------


def _fond(morceaux: Sequence[_Morceau]) -> _Valeur:
    """Les morceaux d'une valeur, les textes voisins réunis."""
    fondus: list[_Morceau] = []
    for morceau in morceaux:
        if isinstance(morceau, str) and fondus and isinstance(fondus[-1], str):
            fondus[-1] = fondus[-1] + morceau
        elif morceau != "":
            fondus.append(morceau)
    return tuple(fondus)


def _texte(valeur: _Valeur) -> str | None:
    """Le texte de `valeur` quand le texte la donne tout entière — `None` sinon."""
    if _longueur_du_prefixe(valeur) < len(valeur):
        return None
    return _prefixe(valeur)


def _prefixe(valeur: Sequence[_Morceau]) -> str:
    """Le texte qui ouvre `valeur`, jusqu'au premier morceau que le texte ne donne pas."""
    textes: list[str] = []
    for morceau in valeur:
        if isinstance(morceau, str):
            textes.append(morceau)
        elif isinstance(morceau, _Motif):
            textes.append(morceau.texte)
        else:
            break
    return "".join(textes)


def _longueur_du_prefixe(valeur: Sequence[_Morceau]) -> int:
    for index, morceau in enumerate(valeur):
        if not isinstance(morceau, (str, _Motif)):
            return index
    return len(valeur)


def _fusionne(ctx: _Contexte, tours: Sequence[_Contexte]) -> None:
    """Après une branche ou une boucle : ce que les tours disent tous, le reste inconnu."""
    if any(tour.ou != tours[0].ou for tour in tours):
        ctx.ou = (_Inconnu("le dossier après une branche ou une boucle"),)
    else:
        ctx.ou = tours[0].ou
    noms = {nom for tour in tours for nom in tour.variables}
    for nom in noms:
        valeurs = [tour.variables.get(nom) for tour in tours]
        premiere = valeurs[0]
        accord = premiere is not None and all(valeur == premiere for valeur in valeurs)
        ctx.variables[nom] = premiere if accord and premiere is not None else (_Inconnu(f"${nom}"),)
    ctx.venv = all(tour.venv for tour in tours)


def _repond(nom: str, motif: str, sans_casse: bool) -> bool:
    """`nom` répond-il au motif d'un `-name` (à la casse près pour `-iname`, et sous Windows) ?"""
    if sans_casse:
        return fnmatch.fnmatchcase(nom.casefold(), motif.casefold())
    return fnmatch.fnmatchcase(nom, motif)


def _sous_un_mktemp(texte: str) -> str | None:
    """Le chemin relatif sous un dossier nommé par `mktemp` — `None` s'il n'y est pas.

    `texte` est déjà replié (`_canonique`) : un `..` qui en sortirait n'y est plus.
    """
    segments = [s for s in re.split(r"[\\/]+", texte) if s]
    for index in range(1, len(segments)):
        if _NOM_MKTEMP.fullmatch(segments[index]) and (
            segments[index - 1].lower() in _DOSSIERS_TEMPORAIRES
        ):
            return "/".join(segments[index + 1 :])
    return None


def _sous_etabli(suite: str) -> str | None:
    """Le chemin relatif sous un établi, `..` replié — `None` s'il en sort."""
    segments: list[str] = []
    for segment in re.split(r"[\\/]+", suite):
        if segment in ("", "."):
            continue
        if segment == "..":
            if not segments:
                return None
            segments.pop()
            continue
        segments.append(segment)
    return "/".join(segments)


def _scripts_des(
    redirections: Sequence[Redirection], documents: Sequence[Document]
) -> list[Script]:
    """Ce qu'exécutent les cibles de redirections et les heredocs non cités."""
    scripts = [s for redirection in redirections for s in redirection.cible.scripts()]
    return scripts + [s for document in documents for s in document.scripts]


def _motif_programme_d_entree(ecrit: str) -> str:
    return (
        f"commande hors de la portée « {PORTEE_PROJET} » : `{ecrit}` exécute un programme "
        "qui ne se lit pas dans son texte. Une personne tranche."
    )


# --- Les chemins ----------------------------------------------------------


def _racine_windows(racine: Path) -> bool:
    """`racine` est-elle un chemin Windows — donc à juger à la mode Windows ?

    Lue sur son **texte** et non sur l'OS qui juge : c'est ce qui laisse un test
    Linux poser `E:/Projets/p3` et obtenir le verdict du poste Windows.
    """
    return bool(_ABSOLU_WINDOWS.match(str(racine)))


def _sous_racine_windows(texte: str, racine: str) -> str | None:
    """`_sous_la_racine` pour une racine Windows — casse et séparateurs indifférents.

    Trois orthographes d'un chemin absolu y mènent : `E:\\…`, `E:/…` et la forme
    MSYS `/e/…` de Git Bash, ramenée à son lecteur. Un chemin enraciné **sans**
    lecteur (`/tmp`, `\\x`) est dehors : sous Git Bash, `/` est la racine de son
    installation — `/tmp` est `%TEMP%` —, jamais celle du projet.
    """
    lecteur = _LECTEUR_MSYS.match(texte)
    if lecteur:
        texte = f"{lecteur.group(1)}:{texte[lecteur.end() :] or '/'}"
    if _ABSOLU_WINDOWS.match(texte):
        chemin = texte
    elif texte.startswith(("/", "\\")):
        return None
    else:
        chemin = ntpath.join(racine, texte)
    base = ntpath.normpath(racine)
    normalise = ntpath.normpath(chemin)
    if ntpath.normcase(normalise) == ntpath.normcase(base):
        return ""
    prefixe = base if base.endswith("\\") else base + "\\"
    if not ntpath.normcase(normalise).startswith(ntpath.normcase(prefixe)):
        return None
    return normalise[len(prefixe) :].replace("\\", "/")


def _sous_racine_posix(texte: str, racine: str) -> str | None:
    """`_sous_la_racine` pour une racine POSIX — où tout chemin Windows est dehors."""
    if _ABSOLU_WINDOWS.match(texte):
        return None
    base = posixpath.normpath(racine)
    normalise = posixpath.normpath(posixpath.join(base, texte))
    if normalise == base:
        return ""
    prefixe = base if base.endswith("/") else base + "/"
    if not normalise.startswith(prefixe):
        return None
    return normalise[len(prefixe) :]


def _motif_hors_racine(chemin: str, racine: Path, lieu: str = "") -> str:
    """Le motif d'un chemin qui sort de la racine — lu par celui qui tranche.

    `lieu` est le chemin tel que le dossier courant le résout, dit quand il
    diffère de ce qui est écrit : après un `cd` dehors, « carnet.md » ne dit pas
    où il se trouve.
    """
    ou = f" ({lieu})" if lieu and lieu != chemin else ""
    return (
        f"commande hors de la portée « {PORTEE_PROJET} » : « {chemin} »{ou} sort du "
        f"dossier du projet ({racine}). Ce qui sort du projet revient à une personne."
    )

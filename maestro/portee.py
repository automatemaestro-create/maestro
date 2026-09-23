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

## Ce qu'il ne décide pas

Ni qui tranche (`maestro.decideur`), ni ce qu'un agent a le droit d'appeler
(`maestro.agents.permissions`), ni où il a le droit d'écrire
(`maestro.sandbox.en_place`). Il est **feuille** — il n'importe de `maestro` que
`lecture`, feuille elle-même — pour la raison qui vaut déjà là-bas : la réponse
est lue par le hook du fournisseur, et elle doit s'éprouver sans monter ni
politique, ni session, ni projet.

⚠ À ne pas confondre avec `maestro.controltower.portee`, qui répond à une tout
autre question : *quelle configuration de projet sert cette requête HTTP ?*.
"""

from __future__ import annotations

import os
import re
import shlex
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from maestro.lecture import (
    OUTIL_SHELL,
    PONCTUATION,
    PUITS,
    REDIRECTIONS,
    SEPARATEURS,
    SUBSTITUTIONS,
    commande_de,
    est_lecture,
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
#: « ce verbe installe toujours dehors ».
#:
#: `pip install` est de ceux-là sans condition : il écrit dans l'interpréteur qui
#: l'exécute, et le régime *en place* — un projet non versionné rempli dans sa
#: racine — n'en a aucun qui lui appartienne. Les gestionnaires de paquets d'un
#: projet (`npm install`, `cargo build`) n'y sont, eux, **que sous leur drapeau
#: global** : sans lui, ils remplissent le dossier du projet, ce qui est
#: exactement leur travail.
INSTALLATIONS_HORS_PROJET: dict[str, tuple[str, ...]] = {
    "pip": (),
    "pip3": (),
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
#: résultat d'un parcours : elle ne se résout pas, donc elle remonte.
FIND_AGISSANTES: frozenset[str] = frozenset(
    {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0", "-fprintf", "-fls"}
)

#: Ce qui, dans un argument, empêche de résoudre le chemin qu'il désigne. Un
#: `rm -rf build/*` ne se juge pas : on ne sait pas ce que l'étoile recouvre au
#: moment où le shell l'étendra.
GLOBS = ("*", "?", "[")

#: Un chemin absolu à la mode Windows (`C:\\…`, `\\\\serveur\\part`), que
#: `Path.is_absolute()` ne reconnaît pas quand le dépôt tourne sous POSIX. La
#: portée doit rendre le même verdict des deux côtés : un test écrit sous Linux
#: doit pouvoir prouver qu'un `C:\\Windows` sort de la racine.
_ABSOLU_WINDOWS = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")

#: La redirection qui **écrase** sa cible. Les autres ajoutent (`>>`) ou branchent
#: un descripteur (`>&`, `&>`) : elles ne font disparaître aucun contenu.
REDIRECTION_ECRASANTE = ">"


@dataclass(frozen=True)
class CommandeSimple:
    """Une commande de l'enchaînement : ses jetons, et ce vers quoi elle redirige.

    Les redirections voyagent **à part** des jetons, parce qu'elles ne sont pas
    des arguments du verbe : `python app.py > sortie.txt` lance `app.py` et écrit
    `sortie.txt`, et confondre les deux ferait juger `sortie.txt` comme un
    argument de `python`.
    """

    jetons: tuple[str, ...]
    redirections: tuple[tuple[str, str], ...] = ()


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
        reste est découpé et jugé commande simple par commande simple — il suffit
        d'une qui sorte pour que l'ensemble sorte, la même règle que la couche
        permissions applique à une commande composée.
        """
        if not commande.strip():
            return (
                "appel de l'outil d'exécution sans commande lisible : sa portée ne "
                "peut pas être jugée, donc il revient à une personne."
            )
        if est_lecture(commande):
            return ""
        simples = decoupe(commande)
        if simples is None:
            return (
                f"commande illisible pour la portée « {PORTEE_PROJET} » "
                f"({commande[:160]}) : substitution, guillemets déséquilibrés ou "
                "opérateur inconnu — ce qu'elle exécute n'est pas dans son texte."
            )
        for simple in simples:
            motif = self._simple_hors_portee(simple)
            if motif:
                return motif
        return ""

    # --- Les deux familles qui remontent ---------------------------------

    def _simple_hors_portee(self, simple: CommandeSimple) -> str:
        """Le motif qui fait sortir une commande simple, ou `""`.

        Les **redirections d'abord**, parce qu'elles peuvent être tout ce que la
        commande porte (`> fichier`) et qu'un verbe absent ne doit pas les faire
        sauter. Puis l'ordre des questions **est** le message qu'on veut lire :
        ce qui sort du projet (le verbe, puis ses chemins), ce qui détruit
        ensuite. Un `sudo rm -rf /` doit se lire « sort du projet », pas
        « détruit un chemin présent ».
        """
        for operateur, cible in simple.redirections:
            motif = self._redirection_hors_portee(operateur, cible)
            if motif:
                return motif
        verbe, *arguments = simple.jetons or ("",)
        if not verbe:
            # Une redirection sans verbe (`> fichier`) : elle vient d'être jugée,
            # il n'y a rien d'autre à regarder.
            return ""
        motif = _verbe_hors_projet(verbe, arguments)
        if motif:
            return motif
        for chemin in _chemins_nommes(arguments):
            if not self._dans_la_racine(chemin):
                return _motif_hors_racine(chemin, self.racine)
        return self._destruction_hors_portee(verbe, arguments)

    def _destruction_hors_portee(self, verbe: str, arguments: list[str]) -> str:
        """Ce que ce verbe détruirait et que l'agent n'a pas produit — `""` sinon."""
        if verbe == "find" and any(jeton in FIND_AGISSANTES for jeton in arguments):
            return (
                "commande hors de la portée : `find` agit ici au lieu de lister "
                "(-delete/-exec), et ce qu'il atteindra ne se lit pas dans ses "
                "arguments. Une personne tranche."
            )
        if verbe not in VERBES_DESTRUCTEURS:
            return ""
        for cible in _cibles(arguments):
            if any(glob in cible for glob in GLOBS):
                return (
                    f"commande hors de la portée : `{verbe} {cible}` vise un motif "
                    "que le shell étendra, donc des chemins qu'on ne peut pas juger "
                    "ici. Une personne tranche."
                )
            if not self._dans_la_racine(cible):
                return _motif_hors_racine(cible, self.racine)
            if self._presente(cible):
                return (
                    f"commande hors de la portée : `{verbe}` viserait « {cible} », "
                    "qui se trouvait déjà dans le projet quand la tâche a commencé — "
                    "l'agent ne l'a pas produit. En écriture en place, rien ne passe "
                    "par une fusion ni par un diff : une personne tranche."
                )
        return ""

    def _redirection_hors_portee(self, operateur: str, cible: str) -> str:
        """Une redirection qui sortirait de la racine, ou qui écraserait du préexistant."""
        if cible == PUITS or (operateur == ">&" and cible.isdigit()):
            return ""
        if not self._dans_la_racine(cible):
            return _motif_hors_racine(cible, self.racine)
        if operateur == REDIRECTION_ECRASANTE and self._presente(cible):
            return (
                f"commande hors de la portée : la redirection écraserait « {cible} », "
                "présent dans le projet avant la tâche. Une personne tranche."
            )
        return ""

    # --- Ce que la racine répond ------------------------------------------

    def _dans_la_racine(self, brut: str) -> bool:
        """`brut`, replié lexicalement, désigne-t-il la racine ou quelque chose dessous ?

        **Lexical et non résolu** : on juge ce que la commande *dit*, et un lien
        symbolique n'est pas suivi ici — c'est l'affaire de la frontière
        d'écriture, qui a les outils de fichiers sous la main. Le repli suffit à
        faire sortir un `..`, un chemin absolu d'ailleurs et un `~`, qui sont les
        trois façons de nommer le dehors.
        """
        chemin = Path(brut.strip()).expanduser()
        if _ABSOLU_WINDOWS.match(brut.strip()):
            return False
        if not chemin.is_absolute():
            chemin = self.racine / chemin
        normalise = os.path.normcase(os.path.normpath(str(chemin)))
        racine = os.path.normcase(os.path.normpath(str(self.racine)))
        return normalise == racine or normalise.startswith(racine + os.sep)

    def _presente(self, brut: str) -> bool:
        """`brut` était-il déjà dans le projet quand la tâche a commencé ?"""
        return self.relatif(brut) in self.presents

    def relatif(self, brut: str) -> str:
        """Le chemin de `brut` relatif à la racine, en POSIX — `""` pour la racine.

        Public parce que c'est aussi la forme dans laquelle `presents` se relève
        (`maestro.sandbox.en_place.presents_de`) : deux orthographes du même
        chemin feraient un relevé qui ne répond jamais.
        """
        chemin = Path(brut.strip()).expanduser()
        if not chemin.is_absolute():
            chemin = self.racine / chemin
        normalise = Path(os.path.normpath(str(chemin)))
        try:
            relatif = normalise.relative_to(self.racine).as_posix()
        except ValueError:
            return ""
        return "" if relatif == "." else relatif


def hors_de_portee(
    portee: str, evaluateur: PorteeProjet | None, outil: str, arguments: Any
) -> str:
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


def decoupe(commande: str) -> tuple[CommandeSimple, ...] | None:
    """Les commandes enchaînées, jetons et redirections séparés — `None` si le texte échappe.

    Le pendant de `maestro.lecture._segments`, avec **une** différence qui est
    tout l'objet de ce module : les redirections ne sont pas jetées mais
    **gardées**, parce qu'écrire dans un fichier est un acte à situer. Le reste
    est identique, jusqu'aux raisons de rendre `None` — guillemets déséquilibrés,
    opérateur qu'on ne sait pas lire, substitution dont le contenu n'est pas dans
    le texte qu'on juge.
    """
    lexeur = shlex.shlex(commande, posix=True, punctuation_chars=True)
    lexeur.whitespace_split = True
    lexeur.commenters = ""
    try:
        jetons = list(lexeur)
    except ValueError:
        return None
    simples: list[CommandeSimple] = []
    courant: list[str] = []
    redirections: list[tuple[str, str]] = []

    def solde() -> None:
        if courant or redirections:
            simples.append(CommandeSimple(tuple(courant), tuple(redirections)))

    reste = list(reversed(jetons))
    while reste:
        jeton = reste.pop()
        if jeton in SEPARATEURS:
            solde()
            courant, redirections = [], []
            continue
        if jeton in REDIRECTIONS:
            if not reste:
                return None
            cible = reste.pop()
            # Le chiffre de descripteur qui précède (`2` de `2>/dev/null`) repart
            # avec la redirection : laissé dans les arguments du verbe, il ne
            # voudrait rien dire.
            if courant and courant[-1].isdigit():
                courant.pop()
            redirections.append((jeton, cible))
            continue
        if set(jeton) <= PONCTUATION or any(amorce in jeton for amorce in SUBSTITUTIONS):
            return None
        courant.append(jeton)
    solde()
    return tuple(simples) or None


def _verbe_hors_projet(verbe: str, arguments: list[str]) -> str:
    """Le motif d'un verbe qui sort du projet sans nommer de chemin — `""` sinon."""
    nom = Path(verbe).name
    if nom in VERBES_HORS_PROJET:
        return (
            f"commande hors de la portée « {PORTEE_PROJET} » : `{nom}` agit sur la "
            "machine et non dans le dossier du projet. Une personne tranche."
        )
    if _installe_hors_projet(nom, arguments):
        return (
            f"commande hors de la portée « {PORTEE_PROJET} » : `{nom}` installe "
            "hors du dossier du projet. Une personne tranche."
        )
    return ""


def _installe_hors_projet(nom: str, arguments: list[str]) -> bool:
    """Cette commande installe-t-elle ailleurs que dans le dossier du projet ?

    `python -m pip install` compte comme `pip install` : c'est le même geste
    écrit autrement, et l'agent l'écrit souvent ainsi quand l'exécutable n'est pas
    dans son chemin.
    """
    if nom in {"python", "python3", "py"} and "pip" in arguments:
        nom, arguments = "pip", arguments[arguments.index("pip") + 1 :]
    drapeaux = INSTALLATIONS_HORS_PROJET.get(nom)
    if drapeaux is None:
        return False
    if not any(argument in SOUS_COMMANDES_INSTALLATION for argument in arguments):
        return False
    return not drapeaux or any(argument in drapeaux for argument in arguments)


def _chemins_nommes(jetons: list[str]) -> Iterator[str]:
    """Les arguments qui **désignent un chemin**, valeur d'option dépliée.

    Un argument ne compte que s'il porte un séparateur de chemin, un `~` ou un
    `..` : un nom nu (`app.py`, `__pycache__`) ne peut pas sortir de la racine, et
    le chercher ferait juger des mots qui n'en sont pas (`rm` a `-rf`, `grep` a son
    motif).

    ⚠ **Le verbe n'en est jamais** : l'appelant ne passe que les arguments.
    Lancer `/usr/bin/python3 app.py` est un geste ordinaire — l'interpréteur vit
    hors du projet par construction —, et le compter ferait sortir la commande la
    plus banale qui soit.
    """
    for jeton in jetons:
        valeur = _valeur_de(jeton)
        if valeur and _ressemble_a_un_chemin(valeur):
            yield valeur


def _valeur_de(jeton: str) -> str:
    """Ce que, dans un argument, on confronte à la racine.

    Trois formes : l'argument nu (`src/app.py`), la valeur d'une option longue
    (`--output=../x` → `../x`) et le chemin collé à une option courte
    (`-I/usr/include` → `/usr/include`). Une option sans chemin ne rend rien.
    """
    if not jeton.startswith("-"):
        return jeton
    if "=" in jeton:
        return jeton.partition("=")[2]
    coupe = min((i for i in (jeton.find("/"), jeton.find("\\")) if i > 0), default=-1)
    return jeton[coupe:] if coupe > 0 else ""


def _ressemble_a_un_chemin(valeur: str) -> bool:
    """`valeur` désigne-t-elle un chemin, au point qu'il faille le situer ?"""
    return (
        "/" in valeur
        or "\\" in valeur
        or valeur.startswith("~")
        or valeur == ".."
    )


def _cibles(arguments: list[str]) -> Iterator[str]:
    """Les arguments d'un verbe destructeur qui désignent ce qu'il va détruire.

    Tout ce qui n'est pas une option : `rm -rf build .cache` vise `build` et
    `.cache`. Un nom nu compte ici, à la différence de `_chemins_nommes` — c'est
    la cible du geste, pas un mot de passage.

    Un opérande `clé=valeur` rend **les deux** : `dd of=sortie` ne nomme pas plus
    sa cible autrement, et ne garder que le jeton entier ferait passer un `dd`
    pour un geste sans cible.
    """
    for argument in arguments:
        if argument == "--" or argument.startswith("-"):
            continue
        yield argument
        if "=" in argument:
            yield argument.partition("=")[2]


def _motif_hors_racine(chemin: str, racine: Path) -> str:
    """Le motif d'un chemin qui sort de la racine — lu par celui qui tranche."""
    return (
        f"commande hors de la portée « {PORTEE_PROJET} » : « {chemin} » sort du "
        f"dossier du projet ({racine}). Ce qui sort du projet revient à une personne."
    )

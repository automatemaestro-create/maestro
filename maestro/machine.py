"""Les faits de la machine que Maestro constate, et qu'il dit à l'agent (#1403).

Rien ne disait à un agent sur quelle machine il travaille. Le prompt système de
Maestro **remplace** celui du CLI (`maestro.providers.claude`), et avec lui le bloc
d'environnement que le CLI y aurait mis : Maestro ne compte pas dessus (docs/44 — un
CLI n'est qu'un adaptateur, et ce qu'il dit de lui-même n'est pas un contrat). Les
agents sondaient donc à l'aveugle — `uname`, `which pandoc python python3 py`, un
`ls` dans `Program Files` —, et ce tâtonnement pesait dans les 233 commandes en échec
ou « introuvable » relevées sur 713 sessions d'agents du poste (audit #1395).

Ce module rend ce que Maestro **constate**, une fois par run, et rien d'autre :

- le **système** — ce que la bibliothèque standard en sait (`platform`) ;
- le **shell** des commandes — le bash avec lequel Maestro joue lui-même les
  commandes de l'outillage (`maestro.sandbox.verification.interprete` : Git Bash sous
  Windows), et sa version ;
- le **répertoire temporaire** — celui que `maestro.emplacements` retient, et que le
  bash de l'agent appelle `/tmp` sous Windows (montage `usertemp` de MSYS) ;
- les **programmes que les commandes de l'outillage du projet appellent** — lus sur
  les commandes que le manifeste garde (`verifications`, docs/38 §4.1) par le lexique
  commun (`maestro.outillage.verification.programmes`), jamais sur un catalogue —,
  présents ou absents, et leur **version**.

## Lire une version, c'est lancer le programme — et c'est voulu, ici seulement

La sonde du poste (`maestro.poste`, #487) n'exécute **aucun** binaire, pas même pour
lire sa version. Cet interdit est **levé pour ce seul usage** par la décision de la
personne du 2026-10-01, consignée en docs/28 §7 : les programmes que l'outillage du
projet appelle, et leur seule version. Ce qui le borne :

- la **présence** est demandée au bash des agents par `type` (`sonde`), qui ne lance
  rien — c'est la question que pose déjà la vérification d'un outillage (#1343) ;
- seul un programme **présent** est lancé, avec `--version` et rien d'autre, dans un
  dossier vide et jetable (`dossier_de_sonde`), l'entrée fermée, sous un délai court
  par programme et un délai pour l'ensemble (`Delais`) ;
- le **code de retour** fait foi (#1315) : une sortie n'est lue que pour y trouver un
  numéro de version (`version_de`), jamais pour reconnaître une panne. Un programme
  qui ne connaît pas `--version`, ou qui ne rend pas la main à temps, a une version
  **inconnue** — jamais devinée.

Une absence ne se suppose pas non plus : un programme sur lequel le poste n'a pas
répondu (aucun bash, délai écoulé, interpréteur qui ne se lance pas) est dit **non
sondé**, pas absent — ne pas savoir n'est pas « absent » (`Verificateur.sonder`).

## Une fois par run

Le constat coûte un bash par programme, deux pour un programme présent : sous
Windows, quelques centaines de millisecondes chacun. Il est donc fait une fois par
run et par projet, par l'exécuteur (`LocalExecutor`), et le même texte part dans le
message de chaque tâche du run. Il date du début du run, et la section le dit : un
programme qu'un agent installe en cours de route n'y figure pas.

**Doublable de bout en bout**, comme le `Verificateur` : `interprete` et `joueur`
valent ceux de `maestro.sandbox.verification`, **relus à chaque appel** — c'est ce qui
laisse `tests/conftest.py` retirer l'interpréteur pour toute la suite (#1160). Sans
bash, le constat ne lance rien : système et temporaire restent dits, les programmes
sont « non sondés ».
"""

from __future__ import annotations

import platform
import shlex
import sys
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from maestro.emplacements import repertoire_temporaire
from maestro.outillage.clients import version_de
from maestro.outillage.generation import verifications_declarees
from maestro.outillage.verification import Joueur, programmes, sondable, sonde
from maestro.projets.modele import Projet
from maestro.sandbox import verification as execution

#: Le titre de la section, dans le message de la tâche.
TITRE_MACHINE = "## La machine"

#: Ce qu'un programme est sur le poste, tel que le constat le dit.
PRESENT = "present"
ABSENT = "absent"
NON_SONDE = "non-sonde"

#: Ce qui fait écrire `echo $BASH_VERSION` plutôt que `bash --version` : le shell est
#: déjà lancé pour répondre, et sa version est une variable qu'il pose lui-même.
_VERSION_DU_SHELL = 'printf %s "$BASH_VERSION"'


@dataclass(frozen=True)
class Delais:
    """Les délais du constat, en secondes — des garde-fous, jamais des verdicts.

    `sonde_s` borne la question de présence (`type` répond en une fraction de
    seconde), `version_s` le `--version` d'un programme (un CLI Node démarre en une
    seconde), `total_s` l'ensemble : le constat retient la première tâche du run, et
    ce qu'il n'a pas eu le temps de demander est dit « non sondé ».
    """

    sonde_s: float = 10.0
    version_s: float = 10.0
    total_s: float = 60.0


@dataclass(frozen=True)
class Programme:
    """Un programme que l'outillage appelle, et ce que le poste en a répondu.

    `etat` vaut `PRESENT`, `ABSENT` ou `NON_SONDE`. `version` n'existe que pour un
    programme présent dont le `--version` a rendu un numéro ; `None` sinon.
    """

    nom: str
    etat: str
    version: str | None = None

    def ligne(self) -> str:
        """La ligne de la section, pour ce programme."""
        if self.etat == PRESENT:
            if self.version:
                return f"- `{self.nom}` {self.version}"
            return f"- `{self.nom}` — présent, version inconnue (`--version` n'en a rendu aucune)"
        if self.etat == ABSENT:
            return (
                f"- `{self.nom}` — **absent** : une commande qui l'appelle échouera tant qu'il "
                "ne sera pas installé"
            )
        return f"- `{self.nom}` — non sondé : le poste n'a pas répondu à Maestro"


@dataclass(frozen=True)
class Machine:
    """Ce que Maestro a constaté de la machine où les commandes de l'agent s'exécutent.

    `shell` est le chemin du bash trouvé, `""` sans bash ; `version_shell` sa version,
    `None` inconnue. `programmes` suit l'ordre où l'outillage les appelle.
    """

    systeme: str
    temporaire: str
    shell: str = ""
    version_shell: str | None = None
    programmes: tuple[Programme, ...] = field(default_factory=tuple)
    windows: bool = False

    def consigne(self) -> str:
        """La section « La machine » du message de la tâche.

        Toujours rendue : le système et le temporaire sont toujours connus. Les
        programmes n'y ont de ligne que si l'outillage du projet en appelle — sans
        outillage, Maestro n'a aucun programme à nommer sans en inventer la liste.
        """
        lignes = [
            TITRE_MACHINE,
            "",
            "Ce que Maestro a constaté de la machine où tes commandes s'exécutent, au début "
            "du run. Tu n'as pas à le sonder toi-même (`uname`, `which`, `--version`) : "
            "pars de ces faits. Un programme installé depuis le début du run n'y figure pas.",
            "",
            f"- **Système** : {self.systeme}",
            f"- **Shell** : {self._shell()}",
            f"- **Répertoire temporaire** : {self._temporaire()}",
        ]
        if self.programmes:
            lignes += [
                "- **Programmes que les commandes de l'outillage du projet appellent** :",
                *(f"  {programme.ligne()}" for programme in self.programmes),
            ]
        return "\n".join(lignes)

    def _shell(self) -> str:
        if not self.shell:
            return "Maestro n'a trouvé aucun bash sur ce poste"
        nom = f"bash {self.version_shell}" if self.version_shell else "bash"
        provenance = " (Git Bash)" if self.windows else ""
        return f"{nom}{provenance}, `{self.shell}` — écris tes commandes pour lui"

    def _temporaire(self) -> str:
        if self.windows:
            return f"`{self.temporaire}`, que bash appelle `/tmp`"
        return f"`{self.temporaire}`"


def systeme() -> str:
    """Le système de ce poste, tel que la bibliothèque standard le dit — `Windows 11 (…)`."""
    nom = platform.system() or sys.platform
    version = ", ".join(v for v in (platform.version(), platform.machine()) if v)
    titre = f"{nom} {platform.release()}".strip()
    return f"{titre} ({version})" if version else titre


def programmes_de_l_outillage(projet: Projet | None) -> tuple[str, ...]:
    """Les programmes que les commandes de l'outillage de `projet` appellent, chacun une fois.

    Lus sur les commandes que le **manifeste** garde (`verifications_declarees`) : ce
    sont celles que l'outillage écrit dans `AGENTS.md` et ses skills, donc celles qu'un
    agent jouera. Dans leur ordre, et le programme de chacune dans l'ordre de la
    commande. `()` sans projet, sans manifeste, ou sans commande lisible.
    """
    if projet is None:
        return ()
    vus: dict[str, None] = {}
    for verification in verifications_declarees(projet.racine_chemin):
        for nom in programmes(verification.commande):
            vus.setdefault(nom, None)
    return tuple(vus)


def constater(
    noms: Iterable[str] = (),
    *,
    interprete: Sequence[str] | None = None,
    joueur: Joueur | None = None,
    delais: Delais | None = None,
) -> Machine:
    """Le constat de cette machine — système, shell, temporaire, et les programmes `noms`.

    Ne lève pas : ce qui empêche de sonder devient « non sondé », jamais une panne de
    la tâche qui attend ce constat. `interprete` et `joueur` valent, sans argument,
    ceux de `maestro.sandbox.verification`, relus à l'appel.
    """
    delais = delais or Delais()
    uniques = tuple(dict.fromkeys(nom for nom in noms if sondable(nom)))
    interprete = interprete if interprete is not None else execution.interprete()
    joueur = joueur if joueur is not None else execution.jouer
    machine = Machine(
        systeme=systeme(),
        temporaire=str(repertoire_temporaire()),
        windows=sys.platform == "win32",
    )
    non_sondes = tuple(Programme(nom, NON_SONDE) for nom in uniques)
    if not interprete:
        return replace(machine, programmes=non_sondes)
    machine = replace(machine, shell=interprete[0])
    try:
        with execution.dossier_de_sonde() as dossier:
            return _sonder(machine, uniques, dossier, tuple(interprete), joueur, delais)
    except execution.CopieImpossible:
        return replace(machine, programmes=non_sondes)


def _sonder(
    machine: Machine,
    noms: tuple[str, ...],
    dossier: Path,
    interprete: tuple[str, ...],
    joueur: Joueur,
    delais: Delais,
) -> Machine:
    """Le shell, puis chaque programme : présent ou absent par `type`, puis sa version."""
    debut = time.monotonic()

    def jouer(commande: str, borne_s: float) -> execution.Execution | None:
        reste = delais.total_s - (time.monotonic() - debut)
        if reste <= 0:
            return None
        try:
            resultat = joueur(commande, dossier, interprete=interprete, delai_s=min(borne_s, reste))
        except OSError:
            return None
        return None if resultat.expiree or resultat.code is None else resultat

    version_shell = _version(jouer(_VERSION_DU_SHELL, delais.version_s))
    trouves: list[Programme] = []
    for nom in noms:
        presence = jouer(sonde(nom), delais.sonde_s)
        if presence is None:
            trouves.append(Programme(nom, NON_SONDE))
        elif presence.code != 0:
            trouves.append(Programme(nom, ABSENT))
        else:
            version = _version(jouer(f"{shlex.quote(nom)} --version", delais.version_s))
            trouves.append(Programme(nom, PRESENT, version=version))
    return replace(machine, version_shell=version_shell, programmes=tuple(trouves))


def _version(resultat: execution.Execution | None) -> str | None:
    """Le numéro qu'une commande **réussie** a rendu — `None` sinon : jamais deviné."""
    if resultat is None or resultat.code != 0:
        return None
    return version_de(resultat.sortie)


__all__ = [
    "ABSENT",
    "NON_SONDE",
    "PRESENT",
    "TITRE_MACHINE",
    "Delais",
    "Machine",
    "Programme",
    "constater",
    "programmes_de_l_outillage",
    "systeme",
]

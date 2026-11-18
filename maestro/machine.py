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
  présents ou absents, et leur **version** ;
- **Maestro lui-même** (#1502) — le nom d'image de l'interpréteur qui le fait tourner,
  lu sur le processus qui constate : un agent a arrêté tous les `python.exe` du poste
  pour arrêter le sien, et Maestro avec. Les rendre intouchables demanderait de
  séparer leurs droits de ceux de l'agent, qui tourne sous le même compte — et le
  lanceur les arrête lui-même par `taskkill` ; à défaut, l'agent sait qu'ils sont
  là, et la portée (`maestro.portee`) rend à une personne l'arrêt qui ne désigne
  pas ce qu'il arrête.

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
from dataclasses import dataclass, replace
from pathlib import Path

from maestro.emplacements import repertoire_temporaire
from maestro.outillage.clients import version_de
from maestro.outillage.generation import verifications_declarees
from maestro.outillage.verification import Joueur, programmes, sondable, sonde
from maestro.projects.model import Project
from maestro.sandbox import container
from maestro.sandbox import verification as execution
from maestro.sandbox.container_broker_client import NOT_RUN as RIEN_N_A_ETE_JOUE
from maestro.sandbox.container_broker_client import PROGRAM as PROGRAMME_DU_COURTIER

#: Le titre de la section, dans le message de la tâche.
TITRE_MACHINE = "## La machine"

#: Ce que le poste borne quand les commandes de l'agent tournent à l'intégrité basse
#: (#1515, docs/17 §7) — et ce qu'il ne borne pas, nommé plutôt que découvert. La
#: portée garde l'arbitrage : un acte qu'elle renvoie à une personne y revient, que le
#: poste l'eût refusé ou non.
LIGNE_INTEGRITE_BASSE = (
    "- **Ce que le poste borne** : tes commandes tournent à l'intégrité basse de Windows. "
    "Elles écrivent dans ton espace de travail, son dépôt Git et ton établi, et nulle part "
    "ailleurs : le profil de la personne, ses autres dossiers et le registre leur sont "
    "refusés. Elles ne voient ni n'arrêtent aucun processus de la personne ni de Maestro — "
    "`taskkill //IM` ne les trouve pas —, seulement ce que ta tâche a lancé. Ce qui n'est "
    "pas borné : tu lis ce que la personne lit, hors ce qui est à Maestro ; tu joins ce qui "
    "écoute sur la machine, `127.0.0.1` compris ; et un autre agent du poste écrit là où "
    "tu écris."
)

#: Comment une commande de l'agent abaissé joue dans un conteneur (#1725, #1726, docs/57 §4) :
#: par le courtier de Maestro, qui compose lui-même le conteneur et construit lui-même
#: l'image d'un Dockerfile, jamais par le moteur, que l'intégrité basse ne joint pas. Ce que
#: la demande peut nommer, ce que Maestro compose et construit, et ce qui n'en revient pas.
LIGNE_COURTIER = (
    "- **Un conteneur, par le courtier de Maestro** : `docker` ne répond pas à l'intégrité "
    f"basse. Pour jouer une commande dans un conteneur Linux, lance `{PROGRAMME_DU_COURTIER} "
    "IMAGE COMMANDE [ARGUMENT...]` : tu nommes l'image et la commande, rien d'autre. Pour "
    f"l'image du projet, nomme plutôt un Dockerfile de ton espace : `{PROGRAMME_DU_COURTIER} "
    "--dockerfile CHEMIN COMMANDE [ARGUMENT...]`. Maestro la construit sur ce que ton "
    "`.dockerignore` garde de l'espace, le Dockerfile compris et jamais ce que le périmètre "
    "exclut, sans réseau de l'hôte, secret ni agent ssh ; il ne la reconstruit que si ce "
    "contenu change, et l'image neuve remplace la précédente du projet. Un conteneur à la "
    "fois par projet : une seconde demande attend son tour. Maestro "
    "compose le conteneur sur ton espace de travail, monté sur `/workspace` avec les masques "
    "du périmètre (et un dépôt Git à lui pour un projet versionné), le joue, puis te rend sa "
    "sortie et son code de retour. Sa racine est en lecture seule, il tourne sous "
    f"l'utilisateur {container.UID_AGENT} sans aucun privilège, avec {container.CPUS_MAX} CPU, "
    f"{container.MEMOIRE_MAX.removesuffix('g')} Go de mémoire et {container.PIDS_MAX} processus "
    "au plus, "
    "et son réseau sort sans rien recevoir. Un montage, un réseau, un privilège ou un dossier "
    "que tu demanderais sont refusés, avec leur raison. Un commit fait dans le conteneur "
    "n'en revient pas : committe depuis ton shell. Un outil qui commande docker lui-même n'y "
    f"passe pas. Le code {RIEN_N_A_ETE_JOUE} dit que rien n'a été joué, et la raison suit sur "
    "stderr."
)

#: Ce qu'un programme est sur le poste, tel que le constat le dit.
PRESENT = "present"
ABSENT = "absent"
NON_SONDE = "non-sonde"

#: La version du shell se lit dans `$BASH_VERSION` plutôt que par `bash --version` : le
#: shell est déjà lancé pour répondre, et c'est une variable qu'il pose lui-même.
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

    `processus_maestro` (#1502) est le nom d'image de l'interpréteur qui fait tourner
    Maestro — celui du processus qui constate (`python.exe`) —, `""` s'il n'est pas
    dit. Un agent qui l'ignore a tué tous les `python.exe` du poste pour arrêter le
    sien, et Maestro avec.
    """

    systeme: str
    temporaire: str
    shell: str = ""
    version_shell: str | None = None
    programmes: tuple[Programme, ...] = ()
    windows: bool = False
    processus_maestro: str = ""

    def consigne(
        self,
        *,
        port_d_essai: int | None = None,
        integrite_basse: bool = False,
        courtier: bool = False,
    ) -> str:
        """La section « La machine » du message de la tâche.

        Toujours rendue : le système et le temporaire sont toujours connus. Les
        programmes n'y ont de ligne que si l'outillage du projet en appelle — sans
        outillage, Maestro n'a aucun programme à nommer sans en inventer la liste.

        `port_d_essai` (#1491) est le port que Maestro réserve à **cette** tâche pour
        essayer ses serveurs (`maestro.sandbox.ports.port_d_essai`) : propre à la
        tâche, il ne fait pas partie du constat du run, et sa ligne vient en dernier.
        Une ressource donnée, pas une borne : la ligne dit ce qu'il garantit, et ce
        qu'un autre port ne garantit pas. None : aucune ligne, la section d'avant.

        `integrite_basse` (#1515) : les commandes de l'agent tournent à l'intégrité
        basse (`maestro.sandbox.low_session`). Le temporaire est alors son établi,
        propre à la session — son chemin n'est pas celui que Maestro a constaté —, et
        une ligne dit ce que le poste borne et ce qu'il ne borne pas : l'agent n'a
        pas à découvrir un refus pour apprendre où il peut écrire.

        `courtier` (#1725) : une session abaissée reçoit le courtier de conteneur
        (`maestro.sandbox.container_broker`). Sa ligne dit comment l'appeler, ce qu'il
        compose et ce qu'il refuse. Sans intégrité basse, il n'y a pas de courtier.
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
            f"- **Répertoire temporaire** : {self._temporaire(integrite_basse)}",
        ]
        if self.programmes:
            lignes += [
                "- **Programmes que les commandes de l'outillage du projet appellent** :",
                *(f"  {programme.ligne()}" for programme in self.programmes),
            ]
        if self.processus_maestro:
            lignes.append(self._maestro())
        if integrite_basse:
            lignes.append(LIGNE_INTEGRITE_BASSE)
            if courtier:
                lignes.append(LIGNE_COURTIER)
        if port_d_essai is not None:
            lignes.append(
                f"- **Ton port d'essai** : `{port_d_essai}`. Maestro le réserve à ta tâche "
                "tant qu'elle dure, et ne le donne à aucune autre : c'est sur lui que tu "
                "essaies tes serveurs. Un port que tu prendrais à sa place n'est réservé à "
                "personne."
            )
        return "\n".join(lignes)

    def _shell(self) -> str:
        if not self.shell:
            return "Maestro n'a trouvé aucun bash sur ce poste"
        nom = f"bash {self.version_shell}" if self.version_shell else "bash"
        provenance = " (Git Bash)" if self.windows else ""
        return f"{nom}{provenance}, `{self.shell}` — écris tes commandes pour lui"

    def _maestro(self) -> str:
        """La ligne qui dit que Maestro tourne ici, et comment ne pas l'arrêter (#1502).

        Un fait et sa conséquence, pas une interdiction : un arrêt par nom ou par
        motif reste possible, il revient seulement à une personne
        (`maestro.portee`), et la ligne le dit pour que l'agent n'attende pas une
        réponse qu'il pouvait s'éviter.
        """
        par_numero, par_nom = (
            ("`taskkill //PID <n>`", "`taskkill //IM`")
            if self.windows
            else ("`kill <n>`", "`pkill`")
        )
        return (
            f"- **Maestro tourne sur ce poste** : son API et l'hôte de ce run sont des "
            f"processus `{self.processus_maestro}`, comme les programmes de la personne "
            "écrits dans ce langage. Pour arrêter ce que tu as lancé, désigne-le par son "
            f"numéro ({par_numero}, `$!`, celui qui écoute sur ton port) ; un arrêt par nom "
            f"ou par motif ({par_nom}, `Stop-Process -Name`) les emporterait tous, et "
            "revient à une personne."
        )

    def _temporaire(self, integrite_basse: bool = False) -> str:
        if integrite_basse:
            return (
                "ton établi, que bash appelle `/tmp` : un dossier à toi seul, qui part avec "
                "ta tâche — ce qui doit rester va dans ton espace de travail"
            )
        if self.windows:
            return f"`{self.temporaire}`, que bash appelle `/tmp`"
        return f"`{self.temporaire}`"


def systeme() -> str:
    """Le système de ce poste, tel que la bibliothèque standard le dit — `Windows 11 (…)`."""
    nom = platform.system() or sys.platform
    version = ", ".join(v for v in (platform.version(), platform.machine()) if v)
    titre = f"{nom} {platform.release()}".strip()
    return f"{titre} ({version})" if version else titre


def programmes_de_l_outillage(projet: Project | None) -> tuple[str, ...]:
    """Les programmes que les commandes de l'outillage de `projet` appellent, chacun une fois.

    Lus sur les commandes que le **manifeste** garde (`verifications_declarees`) : ce
    sont celles que l'outillage écrit dans `AGENTS.md` et ses skills, donc celles qu'un
    agent jouera. Dans leur ordre, et le programme de chacune dans l'ordre de la
    commande. `()` sans projet, sans manifeste, ou sans commande lisible.
    """
    if projet is None:
        return ()
    vus: dict[str, None] = {}
    for verification in verifications_declarees(projet.root_path):
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
        processus_maestro=Path(sys.executable).name,
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
    "LIGNE_COURTIER",
    "LIGNE_INTEGRITE_BASSE",
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

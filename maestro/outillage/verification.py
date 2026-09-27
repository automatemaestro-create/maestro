"""Ce que Maestro écrit dans un projet est **vérifié en l'exécutant** (#1160).

Jusqu'ici, les commandes qu'un outillage écrit (`AGENTS.md`, les skills) sortaient de
constats — lus dans le projet, ou impliqués par des réponses — sans avoir jamais été
jouées. Une commande de « convention » pouvait donc être fausse en silence : `uv
sync` sur un projet sous poetry, `npm ci` sans verrou, un module `app` qui n'existe
pas. La leçon des produits comparables est écrite en toutes lettres par GitHub pour
son agent cloud : la découverte par le modèle *« can be slow and unreliable, given
the non-deterministic nature of large language models »* ; ce qui a été vérifié
s'exécute ensuite de façon déterministe. **Le modèle propose, l'exécution tranche.**

## Ce que ce module décide

Pour chaque commande que la rédaction va écrire (`commandes_ecrites`), un
**verdict** (`Verification`), et un seul des trois :

- `verifiee` — jouée, elle a rendu la main sans erreur (un démarrage : il tournait
  encore au bout du délai d'observation) ;
- `echouee` — jouée, elle a rendu la main en erreur. Son code et **la fin de sa
  sortie** sont gardés : c'est ce que la personne lit pour savoir pourquoi ;
- `a-verifier` — **pas jouée**, avec sa raison : le projet ne peut pas encore la
  jouer (un projet neuf dont `package.json` n'existe pas), la portée « projet » la
  renvoie à une personne, aucun bash sur le poste, le délai a mordu sans verdict.

Le verdict voyage ensuite trois fois, et c'est tout le critère du ticket : dans le
**texte écrit** (`maestro.outillage.redaction` — une commande échouée n'est jamais
écrite comme une convention qui marche), dans le **manifeste** (`verifications`,
docs/38 §4.1) et dans le **rapport** montré à la personne, commande par commande.

## Où l'on joue, et ce qu'on ne joue pas

- **Dans une copie**, jamais dans la racine ni dans le worktree qu'on commite
  (`maestro.sandbox.verification`, qui en donne la raison) ;
- **dans l'ordre des usages** (`USAGES`) : l'installation d'abord, parce que tout le
  reste en dépend sur un arbre frais ;
- **sous l'arbitrage des actes** : une commande que la portée « projet » renvoie à
  une personne (`maestro.portee` — elle sort du dossier du projet : `sudo`, un
  gestionnaire du système, `pip install` hors d'un environnement du projet, une
  machine distante) **n'est pas jouée**. Maestro ne décide pas seul d'un acte qu'un
  agent n'aurait pas eu le droit de faire seul. Elle est écrite « à vérifier », avec
  le motif de la portée ;
- **sous des délais** (`Delais`) : par commande, pour l'ensemble, et une fenêtre
  d'observation pour un démarrage. Ce sont des garde-fous — une vérification ne
  doit pas tenir la génération une heure —, pas des verdicts : un délai atteint
  rend `a-verifier`, jamais `echouee`.

⚠ **L'analyse n'exécute toujours rien** (docs/38, promesse n° 2 de #1030) : ce
module n'est appelé qu'à l'écriture (`maestro.outillage.ecriture`), après le geste
de la personne qui la déclenche. Il n'importe pas lui-même de quoi lancer un
processus — la mécanique est dans `maestro.sandbox`, qui lance déjà Git.

⚠ **Le code de retour fait foi, jamais le texte de la sortie** (#1315) : un `npm
test` qui échoue parce que les tests sont rouges et un `npm test` sans script ne se
distinguent pas ici, et c'est voulu — la sortie gardée le dit à qui la lit.

## Ce que le poste répond, même quand le projet ne peut rien jouer (#1343)

Une commande que le projet ne peut **pas encore** jouer — un dossier neuf, le fichier
où elle vivra pas encore écrit — n'en appelle pas moins des **programmes**, et leur
présence ne dépend pas du projet : `uv --version` se joue sur un dossier vide. Le
banc de #1162 l'a montré sur la vraie stack : `uv`, `ruff`, `typst` et `pandoc`,
absents du poste, étaient écrits « à vérifier » parce que le dossier était vide, et
`AGENTS.md` prescrivait aux agents des commandes qui ne pouvaient qu'échouer.

Ces commandes-là sont donc **sondées** : chaque programme qu'elles appellent
(`programmes`, lu sur la découpe de la portée, jamais sur un catalogue) est demandé
au bash des agents (`sonde` : `type`), dans la copie. Un programme introuvable rend
la commande **échouée**, avec le code et la sortie de la sonde — ce que l'exécution
dit déjà, et le texte la nomme sans jamais l'écrire comme la marche à suivre. Un
programme présent la laisse **à vérifier**, avec la raison du projet : c'est lui qui
manque encore. La portée passe avant : ce qu'une personne tranche n'est ni joué ni
sondé. Le même verbe répond au questionnaire d'un projet neuf, qui n'a pas encore
de dossier (`Verificateur.sonder`).
"""

from __future__ import annotations

import re
import shlex
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from maestro.outillage.modele import USAGES, Constats, Entree, Recommandation
from maestro.outillage.recommandation import SKILL_PAR_USAGE
from maestro.portee import PorteeProjet
from maestro.projets.modele import Perimetre
from maestro.projets.perimetre import motifs_compiles
from maestro.sandbox import verification as execution
from maestro.sandbox.en_place import DOSSIER_ATELIER, fichiers_du_perimetre
from maestro.shell import Illisible, Simple, lis, simples

#: Jouée, elle a rendu la main sans erreur.
VERIFIEE = "verifiee"
#: Jouée, elle a rendu la main en erreur — code et sortie gardés.
ECHOUEE = "echouee"
#: Pas jouée, ou jouée sans verdict — avec la raison.
A_VERIFIER = "a-verifier"

#: Les trois verdicts, tels qu'ils voyagent en JSON (manifeste, API, écran).
ETATS_VERIFICATION: frozenset[str] = frozenset({VERIFIEE, ECHOUEE, A_VERIFIER})

#: L'usage d'une commande de démarrage : elle ne rend pas la main quand elle marche.
USAGE_DEMARRER = "demarrer"

#: Nom de skill → usage, dérivé de la table de la recommandation (jamais recopié).
_USAGE_DU_SKILL: dict[str, str] = {nom: usage for usage, (nom, _) in SKILL_PAR_USAGE.items()}

#: Une affectation de variable en tête d'une commande simple (`CI=1 npm test`) : ce
#: n'est pas le programme appelé, c'est son environnement.
_AFFECTATION = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

#: Ce qui fait d'un nom un **programme à chercher sur le poste** plutôt qu'un fichier du
#: projet ou une phrase : un seul mot, sans séparateur de chemin. `./gradlew` est un
#: fichier du projet — il existera, ou pas, avec lui.
_PROGRAMME = re.compile(r"^[^\s/\\\-][^\s/\\]{0,99}$")

_SANS_BASH = (
    "aucun bash n'a été trouvé sur ce poste pour jouer les commandes — celui que "
    "les agents utilisent (Git Bash sous Windows)"
)


def sonde(programme: str) -> str:
    """La commande qui demande au bash des agents s'il trouve `programme` (#1343).

    `type` et non `--version` : il répond pour tout programme, sans le lancer ni rien
    supposer de ses options, et il connaît ce que bash connaît — une fonction, un
    intégré comme `cd`. Son **code** fait foi ; sa sortie (« bash: type: uv: not
    found ») est gardée pour être montrée.
    """
    return f"type -- {shlex.quote(programme)}"


def sondable(nom: str) -> bool:
    """`nom` est-il un programme qu'on peut chercher sur le poste — un mot, sans chemin ?"""
    return bool(_PROGRAMME.match(nom))


def programmes(commande: str) -> tuple[str, ...]:
    """Les programmes que `commande` appelle, dans l'ordre, chacun une fois (#1343).

    Lus par le **lexique commun des commandes** (`maestro.shell`, #1348), la seule
    lecture d'une commande du dépôt : le verbe de chaque commande simple, blocs et
    boucles compris, ses affectations de tête et un `env` retirés. Un verbe qui est un
    chemin (`./gradlew`) est un fichier du projet, pas un outil du poste ; une commande
    que le lexique ne lit pas, ou qui porte une substitution, ne rend rien — ce
    qu'elle exécute n'est pas dans son texte.
    """
    try:
        script = lis(commande)
    except Illisible:
        return ()
    lues = list(simples(script))
    if any(_substitue(simple) for simple in lues):
        return ()
    vus: dict[str, None] = {}
    for simple in lues:
        verbe = _verbe_appele([mot.rendu() for mot in simple.mots])
        if sondable(verbe):
            vus.setdefault(verbe, None)
    return tuple(vus)


def _substitue(simple: Simple) -> bool:
    """Cette commande simple exécute-t-elle autre chose que ce que ses mots nomment ?"""
    mots = [*simple.mots, *(mot for _, mot in simple.affectations)]
    mots += [redirection.cible for redirection in simple.redirections]
    return any(mot.scripts() for mot in mots) or any(d.scripts for d in simple.documents)


def _verbe_appele(jetons: Sequence[str]) -> str:
    """Le programme qu'une commande simple lance — `CI=1 env LANG=C npm test` → `npm`."""
    reste = list(jetons)
    while reste and _AFFECTATION.match(reste[0]):
        reste.pop(0)
    if reste and reste[0] == "env":
        reste.pop(0)
        while reste and (reste[0].startswith("-") or _AFFECTATION.match(reste[0])):
            reste.pop(0)
    return reste[0] if reste else ""


@dataclass(frozen=True)
class Verification:
    """Le verdict d'une commande écrite dans le projet.

    `raison` est **déterministe** — ni durée, ni horodatage — parce qu'elle entre
    dans le texte d'`AGENTS.md` et des skills, et qu'un texte qui changerait à chaque
    génération rendrait inatteignable le cas « empreinte identique » de docs/38
    §4.2. Ce qui varie d'un passage à l'autre (`duree_s`, `sortie`) reste au
    manifeste et au rapport, jamais dans le texte.

    `code` est `None` quand la commande n'a pas été jouée, ou qu'elle n'a pas rendu
    la main dans son délai.
    """

    usage: str
    commande: str
    etat: str
    raison: str
    code: int | None = None
    sortie: str = ""
    duree_s: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """La forme du manifeste et du rapport."""
        return {
            "usage": self.usage,
            "commande": self.commande,
            "etat": self.etat,
            "raison": self.raison,
            "code": self.code,
            "sortie": self.sortie,
            "duree_s": self.duree_s,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Verification:
        """Relit un verdict depuis sa forme stockée — sans rejouer quoi que ce soit."""
        code = data.get("code")
        return cls(
            usage=str(data.get("usage") or ""),
            commande=str(data.get("commande") or ""),
            etat=str(data.get("etat") or A_VERIFIER),
            raison=str(data.get("raison") or ""),
            code=code if isinstance(code, int) else None,
            sortie=str(data.get("sortie") or ""),
            duree_s=float(data.get("duree_s") or 0.0),
        )


@dataclass(frozen=True)
class Delais:
    """Les délais d'une vérification, en secondes — des garde-fous, pas des verdicts.

    `commande_s` borne chaque commande : une installation sur un projet réel prend
    une à deux minutes, une suite de tests autant. `total_s` borne l'ensemble,
    puisque la génération attend la vérification. `demarrage_s` est la fenêtre
    d'observation d'un démarrage : un serveur qui tourne encore au bout de ce temps
    a démarré — il est alors arrêté, et c'est une réussite. `sonde_s` borne la
    question posée au poste sur un programme (#1343) : `type` répond en une fraction
    de seconde, et une sonde qui ne répond pas ne dit rien — ni présent, ni absent.
    """

    commande_s: float = 300.0
    total_s: float = 900.0
    demarrage_s: float = 15.0
    sonde_s: float = 10.0


@dataclass(frozen=True)
class CommandeEcrite:
    """Une commande que la rédaction écrira : son usage, son texte, où elle vit.

    `chemin` est le fichier qui la justifie — celui qu'on a lu, ou celui où elle
    **vivra** sur un projet neuf. Absent du projet, il dit que la commande ne peut
    pas encore se jouer.
    """

    usage: str
    commande: str
    chemin: str = ""


#: La signature de `maestro.sandbox.verification.jouer` — ce qu'un test double.
Joueur = Callable[..., execution.Execution]


def commandes_ecrites(
    constats: Constats,
    recommandation: Recommandation,
    *,
    portees: Mapping[str, str] | None = None,
) -> tuple[CommandeEcrite, ...]:
    """Les commandes que `rediger` écrira pour cet outillage — chacune une fois.

    Deux sources, les mêmes que la rédaction : `AGENTS.md` écrit la **première**
    commande constatée de chaque usage (`Constats.commande_de`), et chaque skill écrit
    les commandes de son entrée (dont `bash scripts/…` quand le projet a son script).
    Une entrée que la rédaction saute ne compte pas — un skill `deja-present` que le
    manifeste ne déclare pas n'est pas écrit, ses commandes ne le sont donc pas par
    Maestro.

    Rangées dans l'ordre des usages : l'installation d'abord, parce que c'est dans cet
    ordre qu'un agent les joue sur un arbre frais.
    """
    declarees = portees or {}
    trouvees: dict[str, CommandeEcrite] = {}
    ecrites = [e for e in recommandation.entrees if _sera_ecrite(e, declarees)]
    if any(e.type == "instructions" for e in ecrites):
        for usage in USAGES:
            commande = constats.commande_de(usage)
            if commande is not None and commande.commande.strip():
                trouvees.setdefault(
                    commande.commande, CommandeEcrite(usage, commande.commande, commande.chemin)
                )
    lues = {c.commande: c for c in constats.commandes}
    for entree in ecrites:
        if entree.type != "skill":
            continue
        for texte in entree.commandes:
            if not texte.strip() or texte in trouvees:
                continue
            lue = lues.get(texte)
            trouvees[texte] = (
                CommandeEcrite(lue.usage, texte, lue.chemin)
                if lue is not None
                else CommandeEcrite(
                    _USAGE_DU_SKILL.get(entree.nom, ""),
                    texte,
                    entree.justification.chemin if entree.justification is not None else "",
                )
            )
    rang = {usage: i for i, usage in enumerate(USAGES)}
    return tuple(sorted(trouvees.values(), key=lambda c: rang.get(c.usage, len(USAGES))))


def _sera_ecrite(entree: Entree, declarees: Mapping[str, str]) -> bool:
    """La rédaction rendra-t-elle un fichier pour cette entrée ? (la règle de `rediger`)."""
    return not (entree.etat == "deja-present" and entree.chemin not in declarees)


@dataclass(frozen=True)
class Verificateur:
    """Joue les commandes d'un outillage avant qu'il ne soit écrit, et rend leurs verdicts.

    Sans argument, il joue pour de vrai : `joueur` et `interprete` valent alors ceux
    de `maestro.sandbox.verification`, **relus à chaque appel** — c'est ce qui laisse
    la suite de tests les neutraliser d'un seul endroit (`tests/conftest.py`), comme
    elle neutralise le fournisseur de modèle du poste. Un test qui veut un verdict
    précis passe son `joueur`.
    """

    delais: Delais = field(default_factory=Delais)
    joueur: Joueur | None = None
    interprete: tuple[str, ...] | None = None

    def verifier(
        self,
        racine: Path,
        constats: Constats,
        recommandation: Recommandation,
        *,
        perimetre: Perimetre,
        portees: Mapping[str, str] | None = None,
        connues: Mapping[str, Verification] | None = None,
    ) -> tuple[Verification, ...]:
        """Les verdicts des commandes que cet outillage écrira, dans l'ordre des usages.

        `racine` est l'arbre où l'outillage va s'écrire — la racine d'un projet non
        versionné, le worktree d'un projet versionné : c'est lui qui est **copié**,
        jamais lui qui est joué. Ne lève pas : ce qui empêche de jouer devient la
        raison d'un `a-verifier`.

        `connues` (#1161) — commande → verdict — sont les commandes **déjà jouées**
        pour ce projet : quand l'outillage s'écrit pièce par pièce, la commande de
        tests qu'`AGENTS.md` a fait jouer est celle que le skill de tests écrira, et la
        rejouer paierait une seconde fois une installation ou une suite entière. Seul
        un verdict **joué** (`verifiee`, `echouee`) est repris : un `a-verifier` dit
        qu'on n'a pas pu jouer, et c'est peut-être possible maintenant. Une commande
        **corrigée** a un autre texte, donc n'est jamais connue : elle est jouée.
        """
        commandes = commandes_ecrites(constats, recommandation, portees=portees)
        if not commandes:
            return ()
        verdicts: dict[str, Verification] = {
            c.commande: connue
            for c in commandes
            if (connue := (connues or {}).get(c.commande)) is not None
            and connue.etat in (VERIFIEE, ECHOUEE)
        }
        interprete = self.interprete if self.interprete is not None else execution.interprete()
        vide = _projet_vide(racine, perimetre, frozenset((portees or {}).keys()))
        # Chaque commande à jouer, avec ce qui l'empêche **encore** — `""` si rien : le
        # projet peut la jouer. Les autres ne se jouent pas, mais leurs programmes se
        # sondent (#1343) : le poste répond même quand le projet ne peut rien jouer.
        a_jouer: list[tuple[CommandeEcrite, str]] = []
        for commande in commandes:
            if commande.commande in verdicts:
                continue
            pas_encore = _pas_encore(racine, commande, vide=vide)
            if interprete is None:
                # Dans l'ordre où la personne veut le lire : le projet d'abord (ce qui
                # changera de lui-même quand il sera écrit), le poste ensuite.
                verdicts[commande.commande] = _a_verifier(commande, pas_encore or _SANS_BASH)
            else:
                a_jouer.append((commande, pas_encore))
        if a_jouer and interprete is not None:
            verdicts.update(self._jouer(racine, a_jouer, perimetre, interprete))
        return tuple(verdicts[c.commande] for c in commandes)

    def sonder(self, noms: Iterable[str]) -> dict[str, bool]:
        """Ce que le poste répond de chaque programme : présent (`True`), absent (`False`).

        La question que pose le questionnaire d'un projet neuf, qui n'a pas encore de
        dossier (#1343) : les outils qu'une option demande sont-ils sur le poste ? Posée
        au bash des agents (`sonde`), dans un dossier vide et jetable. Un programme sur
        lequel le poste **ne répond pas** — aucun bash, une sonde qui n'a pas rendu la
        main, un interpréteur qui ne se lance pas — est **absent du résultat** : ne pas
        savoir n'est pas « absent », et le dire ferait écarter un outil qui est là.
        """
        uniques = tuple(dict.fromkeys(nom for nom in noms if sondable(nom)))
        interprete = self.interprete if self.interprete is not None else execution.interprete()
        if not uniques or interprete is None:
            return {}
        joueur = self.joueur if self.joueur is not None else execution.jouer
        poste: dict[str, bool] = {}
        try:
            with execution.dossier_de_sonde() as dossier:
                for nom in uniques:
                    try:
                        resultat = joueur(
                            sonde(nom), dossier, interprete=interprete, delai_s=self.delais.sonde_s
                        )
                    except OSError:
                        continue
                    if not resultat.expiree and resultat.code is not None:
                        poste[nom] = resultat.code == 0
        except execution.CopieImpossible:
            return poste
        return poste

    def _jouer(
        self,
        racine: Path,
        commandes: Sequence[tuple[CommandeEcrite, str]],
        perimetre: Perimetre,
        interprete: tuple[str, ...],
    ) -> dict[str, Verification]:
        """Joue `commandes` dans **une** copie de `racine`, l'une après l'autre.

        Une commande que le projet ne peut pas encore jouer (sa raison non vide) n'y est
        pas jouée : ses programmes y sont sondés (`_en_attente`), une fois chacun pour
        toute la vérification.
        """
        joueur = self.joueur if self.joueur is not None else execution.jouer
        verdicts: dict[str, Verification] = {}
        sondes: dict[str, execution.Execution] = {}
        try:
            with execution.copie_de_verification(
                racine,
                exclus=motifs_compiles(perimetre.exclus),
                hors=(DOSSIER_ATELIER,),
            ) as copie:
                portee = PorteeProjet(racine=copie)
                debut = time.monotonic()
                for commande, pas_encore in commandes:
                    if pas_encore:
                        verdicts[commande.commande] = self._en_attente(
                            commande, pas_encore, copie, portee, joueur, interprete, debut, sondes
                        )
                    else:
                        verdicts[commande.commande] = self._une(
                            commande, copie, portee, joueur, interprete, debut
                        )
        except execution.CopieImpossible as exc:
            raison = f"la copie de vérification n'a pas pu être faite — {exc}"
            for commande, pas_encore in commandes:
                verdicts.setdefault(commande.commande, _a_verifier(commande, pas_encore or raison))
        return verdicts

    def _en_attente(
        self,
        commande: CommandeEcrite,
        pas_encore: str,
        copie: Path,
        portee: PorteeProjet,
        joueur: Joueur,
        interprete: tuple[str, ...],
        debut: float,
        sondes: dict[str, execution.Execution],
    ) -> Verification:
        """Le verdict d'une commande que le projet ne peut pas encore jouer (#1343).

        La portée d'abord, comme pour une commande jouée : ce qu'une personne tranche
        n'est ni joué ni sondé. Puis chaque programme qu'elle appelle est demandé au
        poste — **par son code** : un programme introuvable la rend échouée, avec ce que
        la sonde a répondu. Une sonde sans réponse (délai, interpréteur qui ne se lance
        pas, temps alloué épuisé) ne dit rien, et la commande reste à vérifier.
        """
        motif = portee.commande_hors_portee(commande.commande).replace(f" ({copie})", "")
        if motif:
            return _a_verifier(commande, f"pas jouée — {motif}")
        absents: list[tuple[str, execution.Execution]] = []
        for programme in programmes(commande.commande):
            resultat = sondes.get(programme)
            if resultat is None:
                reste = self.delais.total_s - (time.monotonic() - debut)
                if reste <= 0:
                    break
                try:
                    resultat = joueur(
                        sonde(programme),
                        copie,
                        interprete=interprete,
                        delai_s=min(self.delais.sonde_s, reste),
                    )
                except OSError:
                    continue
                sondes[programme] = resultat
            if not resultat.expiree and resultat.code not in (None, 0):
                absents.append((programme, resultat))
        if absents:
            return _outils_absents(commande, absents)
        return _a_verifier(commande, pas_encore)

    def _une(
        self,
        commande: CommandeEcrite,
        copie: Path,
        portee: PorteeProjet,
        joueur: Joueur,
        interprete: tuple[str, ...],
        debut: float,
    ) -> Verification:
        """Le verdict d'une commande — la portée d'abord, le temps ensuite, puis le jeu.

        Le motif de la portée nomme la racine qu'elle a jugée, ici la **copie** : un
        chemin temporaire qui changerait à chaque passage et n'apprendrait rien à qui
        lit `AGENTS.md`. Il en est retiré — « le dossier du projet » le dit déjà.
        """
        motif = portee.commande_hors_portee(commande.commande).replace(f" ({copie})", "")
        if motif:
            # Le motif de la portée dit déjà qu'une personne tranche : la raison ne dit
            # que le fait, « pas jouée » (relevé deux fois par le regard neuf de #1160).
            return _a_verifier(commande, f"pas jouée — {motif}")
        reste = self.delais.total_s - (time.monotonic() - debut)
        if reste <= 0:
            return _a_verifier(
                commande,
                "le temps alloué à la vérification de cet outillage était épuisé : rien "
                "de plus n'a été joué",
            )
        demarrage = commande.usage == USAGE_DEMARRER
        delai = self.delais.demarrage_s if demarrage else min(self.delais.commande_s, reste)
        try:
            resultat = joueur(commande.commande, copie, interprete=interprete, delai_s=delai)
        except OSError as exc:
            return _a_verifier(commande, f"l'interpréteur n'a pas pu la lancer ({exc})")
        return _verdict(commande, resultat, demarrage=demarrage, fenetre=self.delais.demarrage_s)


def _projet_vide(racine: Path, perimetre: Perimetre, declares: frozenset[str]) -> bool:
    """Le projet ne porte-t-il encore aucun fichier **à lui** ?

    Ni l'atelier, ni ce que le manifeste déclare (`declares`) : c'est l'outillage de
    Maestro, pas le projet. Sans cette exclusion, la génération qui suit la première
    trouverait le dossier « non vide » — il porte `AGENTS.md` — et changerait de
    raison, donc de texte, pour un projet qui n'a pas bougé (l'idempotence de docs/38
    §4.2, gardée par `test_regenerer_en_place_ne_duplique_pas_agents_md_dans_lui_meme`).
    """
    fichiers = fichiers_du_perimetre(
        racine, motifs_compiles(perimetre.exclus), hors=(DOSSIER_ATELIER,)
    )
    return all(relatif in declares for relatif in fichiers)


def _pas_encore(racine: Path, commande: CommandeEcrite, *, vide: bool) -> str:
    """Pourquoi le **projet** ne peut pas encore jouer cette commande — `""` s'il le peut.

    Ce qui changera de lui-même quand le projet sera écrit : un dossier sans fichier à
    lui, le fichier où la commande vit pas encore là. Le poste, lui, se sonde
    (`Verificateur._en_attente`, #1343).
    """
    if vide:
        # « Le dossier est vide » se lisait juste sous « 7 fichiers écrits dans … » et
        # le contredisait (regard neuf de #1160) : ce qui manque est le projet, pas des
        # fichiers — l'outillage, lui, vient d'y être écrit.
        return (
            "le projet n'a encore aucun fichier en dehors de son outillage : rien ne "
            "peut s'y jouer avant, tout le sera à la prochaine écriture de l'outillage"
        )
    # Des raisons **neutres en nombre** : l'écran dit une seule fois celle que plusieurs
    # commandes partagent (« Les 5 commandes à vérifier, pour une même raison : … »),
    # et un « elle » y renvoyait à une commande quand la phrase en comptait cinq.
    if commande.chemin and not (racine / PurePosixPath(commande.chemin)).exists():
        return (
            f"`{commande.chemin}` n'existe pas encore dans le projet : à jouer quand il "
            "existera, à la prochaine écriture de l'outillage"
        )
    return ""


def _a_verifier(commande: CommandeEcrite, raison: str) -> Verification:
    """Un verdict « pas jouée », avec sa raison."""
    return Verification(
        usage=commande.usage, commande=commande.commande, etat=A_VERIFIER, raison=raison
    )


def _outils_absents(
    commande: CommandeEcrite, absents: Sequence[tuple[str, execution.Execution]]
) -> Verification:
    """Le verdict d'une commande dont un programme est introuvable sur le poste (#1343).

    **Échouée**, et non « à vérifier » : l'exécution l'a déjà dit — la sonde a rendu la
    main en erreur —, et c'est tout ce qui compte pour ne jamais l'écrire comme la
    marche à suivre. Le code et la sortie sont ceux de la sonde (la première, puis les
    sorties mises bout à bout) : c'est ce que bash a répondu, montré tel quel.

    La raison est **déterministe** et **neutre en nombre de commandes** — l'écran la
    dit une fois pour toutes celles qu'elle touche —, et elle nomme l'outil : c'est
    lui qu'une personne installera, ou qu'une correction remplacera.
    """
    noms = [f"`{programme}`" for programme, _ in absents]
    if len(noms) == 1:
        raison = (
            f"{noms[0]} est introuvable sur ce poste, et rien de ce qui l'appelle ne "
            "passera tant qu'il n'y sera pas installé"
        )
    else:
        raison = (
            f"{', '.join(noms[:-1])} et {noms[-1]} sont introuvables sur ce poste, et rien "
            "de ce qui les appelle ne passera tant qu'ils n'y seront pas installés"
        )
    premier = absents[0][1]
    return Verification(
        usage=commande.usage,
        commande=commande.commande,
        etat=ECHOUEE,
        raison=raison,
        code=premier.code,
        sortie="\n".join(resultat.sortie for _, resultat in absents if resultat.sortie),
        duree_s=round(sum(resultat.duree_s for _, resultat in absents), 2),
    )


def _verdict(
    commande: CommandeEcrite,
    resultat: execution.Execution,
    *,
    demarrage: bool,
    fenetre: float,
) -> Verification:
    """Le verdict d'une commande jouée — par son code de retour, jamais par sa sortie."""
    if demarrage and resultat.expiree:
        etat = VERIFIEE
        raison = (
            f"elle a démarré et tournait encore au bout de {fenetre:g} s ; Maestro l'a "
            "ensuite arrêtée"
        )
    elif resultat.expiree:
        etat = A_VERIFIER
        raison = (
            "elle n'a pas rendu la main dans le temps alloué ; Maestro l'a arrêtée, "
            "sans verdict"
        )
    elif resultat.code == 0:
        etat = VERIFIEE
        raison = "elle a rendu la main sans erreur"
    else:
        etat = ECHOUEE
        raison = f"elle a rendu la main en erreur (code {resultat.code})"
    return Verification(
        usage=commande.usage,
        commande=commande.commande,
        etat=etat,
        raison=raison,
        code=resultat.code,
        sortie=resultat.sortie,
        duree_s=resultat.duree_s,
    )

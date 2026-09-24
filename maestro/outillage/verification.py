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
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
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
    a démarré — il est alors arrêté, et c'est une réussite.
    """

    commande_s: float = 300.0
    total_s: float = 900.0
    demarrage_s: float = 15.0


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
    ) -> tuple[Verification, ...]:
        """Les verdicts des commandes que cet outillage écrira, dans l'ordre des usages.

        `racine` est l'arbre où l'outillage va s'écrire — la racine d'un projet non
        versionné, le worktree d'un projet versionné : c'est lui qui est **copié**,
        jamais lui qui est joué. Ne lève pas : ce qui empêche de jouer devient la
        raison d'un `a-verifier`.
        """
        commandes = commandes_ecrites(constats, recommandation, portees=portees)
        if not commandes:
            return ()
        verdicts: dict[str, Verification] = {}
        interprete = self.interprete if self.interprete is not None else execution.interprete()
        vide = _projet_vide(racine, perimetre, frozenset((portees or {}).keys()))
        jouables: list[CommandeEcrite] = []
        for commande in commandes:
            raison = _injouable(racine, commande, vide=vide, interprete=interprete)
            if raison:
                verdicts[commande.commande] = _a_verifier(commande, raison)
            else:
                jouables.append(commande)
        if jouables and interprete is not None:
            verdicts.update(self._jouer(racine, jouables, perimetre, interprete))
        return tuple(verdicts[c.commande] for c in commandes)

    def _jouer(
        self,
        racine: Path,
        commandes: Sequence[CommandeEcrite],
        perimetre: Perimetre,
        interprete: tuple[str, ...],
    ) -> dict[str, Verification]:
        """Joue `commandes` dans **une** copie de `racine`, l'une après l'autre."""
        joueur = self.joueur if self.joueur is not None else execution.jouer
        verdicts: dict[str, Verification] = {}
        try:
            with execution.copie_de_verification(
                racine,
                exclus=motifs_compiles(perimetre.exclus),
                hors=(DOSSIER_ATELIER,),
            ) as copie:
                portee = PorteeProjet(racine=copie)
                debut = time.monotonic()
                for commande in commandes:
                    verdicts[commande.commande] = self._une(
                        commande, copie, portee, joueur, interprete, debut
                    )
        except execution.CopieImpossible as exc:
            raison = f"la copie de vérification n'a pas pu être faite — {exc}"
            for commande in commandes:
                verdicts.setdefault(commande.commande, _a_verifier(commande, raison))
        return verdicts

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
            return _a_verifier(
                commande,
                f"Maestro ne la joue pas sans vous, elle revient à une personne ({motif})",
            )
        reste = self.delais.total_s - (time.monotonic() - debut)
        if reste <= 0:
            return _a_verifier(
                commande,
                "le temps alloué à la vérification de cet outillage était épuisé avant "
                "son tour",
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


def _injouable(
    racine: Path, commande: CommandeEcrite, *, vide: bool, interprete: tuple[str, ...] | None
) -> str:
    """Pourquoi cette commande ne peut pas se jouer **ici et maintenant** — `""` si elle peut.

    Dans l'ordre où la personne veut le lire : le projet d'abord (ce qui changera de
    lui-même quand il sera écrit), le poste ensuite.
    """
    if vide:
        return (
            "le dossier du projet est encore vide, rien ne peut encore la jouer ; elle "
            "le sera à la prochaine écriture de l'outillage"
        )
    if commande.chemin and not (racine / PurePosixPath(commande.chemin)).exists():
        return (
            f"`{commande.chemin}` n'existe pas encore dans le projet ; elle sera jouée "
            "quand il existera, à la prochaine écriture de l'outillage"
        )
    if interprete is None:
        return (
            "aucun bash n'a été trouvé sur ce poste pour la jouer — celui que les agents "
            "utilisent (Git Bash sous Windows)"
        )
    return ""


def _a_verifier(commande: CommandeEcrite, raison: str) -> Verification:
    """Un verdict « pas jouée », avec sa raison."""
    return Verification(
        usage=commande.usage, commande=commande.commande, etat=A_VERIFIER, raison=raison
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

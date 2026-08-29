"""Politique de permissions par agent et par outil (ticket #110, parent #102).

Chaque agent peut déclarer une politique **allow/ask/deny par outil** — outils
intégrés du runtime (`Read`, `Bash`…) comme outils MCP (`mcp__<serveur>` pour
un serveur entier, `mcp__<serveur>__<outil>` pour un outil précis) — appliquée
à **l'exécution** :

- les outils intégrés refusés sont **retirés de la session** avant son
  ouverture (`PolitiqueOutils.filtre_outils` — l'agent ne les voit jamais) ;
- un serveur MCP entièrement refusé n'est **jamais monté** (ses secrets ne
  sont même pas résolus — `serveur_autorise`) ;
- tout appel qui passerait quand même (outil MCP refusé individuellement,
  outil résiduel de la session) est **refusé au vol** par le fournisseur
  (hook PreToolUse de la couche SDK) : l'agent reçoit un refus propre motivé
  et **poursuit sa tâche** — la violation est tracée (journal + fil temps
  réel Control Tower), jamais fatale au run.

Sémantique : `deny` l'emporte toujours ; `allow` vide = tout ce que le profil
expose est permis (comportement historique) ; `allow` non vide = liste
fermée — tout le reste est refusé. Une entrée vaut pour l'outil exact ou, aux
frontières `__`, pour tout ce qu'elle préfixe (`mcp__slack` couvre
`mcp__slack__send_message`).

Entre « laisser passer » et « refuser », un **troisième cran** (#580, parent
#573) : `ask` — l'appel est suspendu le temps qu'une personne tranche. La
priorité est `deny` > `ask` > `allow`, et `PolitiqueOutils.decide` en est le
verbe : il rend le verdict (`Verdict.PASSE` / `ARBITRAGE` / `REFUS`) avec son
motif. Deux conséquences à ne pas défaire — un outil classé `ask` n'est **pas**
interdit (`autorise` le dit permis, `filtre_outils`/`serveur_autorise`
continuent de le monter : un outil retiré de la session n'atteindrait jamais
l'arbitrage), et `ask` l'emporte sur `allow`, donc un outil cité en `ask` est
arbitré **même** quand une liste `allow` fermée ne le cite pas — sinon le cran
du milieu serait lettre morte dès qu'une politique ferme sa liste.

Et depuis #586, une entrée `ask` porte **qui la tranche** : `auto` ou `humain`
(`maestro.decideur`). Le cran est posé ici, à froid et versionné avec le dépôt,
parce qu'un « et si la machine répondait elle-même ? » décidé au vol par un LLM ne
serait ni traçable ni testable. Deux formes se relisent donc pour `ask` — une
**liste** (`["Bash"]`, tout `humain` : c'est le fichier d'avant ce lot, au bit
près) ou un **objet** (`{"Bash": "auto"}`) —, et il n'y en a qu'une en écriture :
`to_dict` réémet toujours l'objet, seule forme qui porte l'information entière. Un
cran absent vaut `humain` (*un cran non précisé escalade, il ne s'auto-approuve
pas*) ; un cran **inconnu** est une erreur franche, comme toute politique douteuse
— le repli tolérant vit chez `decideur_depuis`, pour ce qui se relit après coup et
ne peut plus être corrigé.

⚠ Un troisième cran, `orchestrateur`, a été retiré par #715 (décision de cadrage
#647, [docs/31](../../docs/31-decision-cran-orchestrateur.md)) : il n'avait aucun
canal en production, et promettait donc une décision là où il rendait un refus.
Une politique qui l'écrit **échoue franchement au chargement** depuis — c'est le
versant écriture de l'asymétrie ci-dessus, et il est **acquis sans une ligne à
tenir** : l'ensemble admissible est `tuple(Decideur)` (`_ask_validee`), donc il
s'est réduit tout seul en même temps que l'énumération. Ne pas y substituer une
liste en dur, qui serait un second endroit à tenir d'accord.

**Ce module est devenu le déclencheur de l'arbitrage humain, et le déclencheur
est l'acte.** C'est le renversement du parent #573 : ce qui suspend un appel est
l'**outil que l'agent s'apprête à appeler**, jugé ici, et non le texte de ce
qu'on lui a demandé d'écrire. La chaîne est courte — `decide` rend `ARBITRAGE`,
le hook `PreToolUse` suspend l'appel (#583), la demande part avec l'outil et ses
arguments (#581, `maestro.acte`) et aboutit à
`maestro.engine.guardrails.Guardrails.demande_validation`, donc au fail-safe
commun : sans personne pour trancher, l'acte est **refusé** (EF-08, ENF-04).

**Ce qui reste du régime par mots-clés** vit ailleurs et n'est plus armé : une
tâche pouvait être classée sensible sur des radicaux cherchés dans son titre et
sa description (`Guardrails.raison_sensible`, `MOTS_SENSIBLES`). Le mécanisme est
intact, mais sa liste est **vide par défaut** depuis #585 — le mot venait du
brief et se propageait à toutes les descriptions que la décomposition en tirait
(#568 : 3 tâches sur 3 sensibles, « Rédiger le README » comprise). Développer une
fonction de suppression n'est pas exécuter une suppression. Les deux régimes ne
se disputent donc rien : celui-ci juge un **acte**, l'autre un **énoncé**, et
c'est le premier qui est branché.

Le dépôt suit le pattern des voisins (`maestro.agents.mcp`, `capacity`) : un
fichier JSON par agent (`core/permissions/<agent>.json`, versionné avec le
dépôt Git), **validé à la lecture** (une politique douteuse est refusée avec
sa cause, jamais appliquée à moitié) et **relu à chaud** à chaque tâche par
l'exécuteur — corriger une politique vaut pour la tâche suivante, sans
redémarrage. Pas de fichier = pas de politique = tout permis. En V1 le dépôt
passera en base sans changer ce contrat.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from maestro.config import Settings, load_settings
from maestro.decideur import DECIDEUR_DEFAUT, Decideur, decideur_depuis

#: Nom d'agent admissible comme fichier de stockage — même verrou que les
#: dépôts voisins (`maestro.agents.mcp`, `store`) : slug sûr, jamais un chemin.
_NOM_AGENT = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

#: Une entrée de politique admissible : un nom d'outil intégré (`Read`, `Bash`)
#: ou un nom préfixé MCP (`mcp__slack`, `mcp__slack__send_message`) — segments
#: alphanumériques joints par `__`, jamais d'espace ni de vide.
_ENTREE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")

#: Préfixe des outils MCP dans les sessions SDK : `mcp__<serveur>__<outil>`.
_PREFIXE_MCP = "mcp__"


def _correspond(entree: str, outil: str) -> bool:
    """`entree` couvre-t-elle `outil` ? Nom exact, ou préfixe à une frontière `__`.

    `mcp__slack` couvre `mcp__slack__send_message` mais pas `mcp__slackbot__x` :
    le préfixe ne vaut qu'aux frontières de segments — jamais en plein mot.
    """
    return outil == entree or outil.startswith(entree + "__")


def _cite_serveur(entrees: Iterable[str], prefixe: str) -> bool:
    """Une entrée cite-t-elle le serveur `prefixe` — en entier, ou un seul de ses outils ?

    Le sens inverse de `_correspond` : `mcp__slack__send_message` cite le
    serveur `mcp__slack`, alors que le serveur ne « correspond » pas à l'outil.
    C'est la question que pose le montage d'un serveur MCP, pas l'appel.
    """
    return any(
        entree == prefixe or entree.startswith(prefixe + "__") for entree in entrees
    )


class EntreeArbitrage(str):
    """Une entrée `ask` : le nom de l'outil, **et qui tranche son appel** (#586).

    Sous-classe de `str` à dessein, et ce n'est pas une commodité d'écriture :
    l'entrée *est* son nom d'outil partout où ce module la manipulait déjà —
    `_correspond`, `_cite_serveur`, la comparaison de deux politiques,
    `json.dumps` — si bien que le cran s'y ajoute sans qu'aucun de ces
    endroits ait à connaître son existence. Une politique écrite avant ce lot
    se relit donc à l'identique, sous le défaut `humain`, et une politique
    construite en Python avec des chaînes nues (`PolitiqueOutils(ask=("Bash",))`)
    continue de dire exactement ce qu'elle disait.

    L'égalité et le hachage sont ceux de `str` : deux entrées de même nom sont
    la même entrée quel que soit leur cran. C'est ce qu'il faut — une entrée ne
    peut porter qu'un cran (la forme objet a des clés uniques, la forme liste
    n'en porte aucun), et le dédoublonnage de `_liste_validee` garde ainsi son
    sens sans avoir à départager deux crans qui ne peuvent pas coexister.
    """

    __slots__ = ("decideur",)

    decideur: Decideur

    def __new__(cls, entree: str, decideur: Decideur = DECIDEUR_DEFAUT) -> EntreeArbitrage:
        objet = super().__new__(cls, entree)
        objet.decideur = decideur
        return objet


def _entree_arbitrage(entree: str) -> EntreeArbitrage:
    """Normalise une entrée `ask` — une chaîne nue vaut le cran par défaut.

    Le point de passage unique par lequel `PolitiqueOutils` s'assure que sa
    liste `ask` ne porte que des `EntreeArbitrage`, d'où qu'elle vienne : un
    fichier en forme liste, un appelant Python qui passe des chaînes, ou une
    politique déjà normalisée qu'on reconstruit.
    """
    return entree if isinstance(entree, EntreeArbitrage) else EntreeArbitrage(entree)


class Verdict(StrEnum):
    """Ce qu'une politique dit d'un appel d'outil — trois crans, plus deux (#580).

    `PASSE` laisse l'appel au flux normal, `ARBITRAGE` le suspend le temps
    qu'une personne tranche, `REFUS` l'écarte. Valeurs en clair : elles
    voyagent telles quelles en JSON (journal, API, fil temps réel).
    """

    PASSE = "passe"
    ARBITRAGE = "arbitrage"
    REFUS = "refus"


@dataclass(frozen=True)
class DecisionOutil:
    """Le verdict rendu pour un outil, et le motif qui l'explique.

    Le motif est le texte lisible servi à l'agent (refus) ou à la personne qui
    arbitre — il nomme l'outil et la liste en cause, comme `raison_refus`
    aujourd'hui. Il est **vide sur `PASSE`** : rien ne s'oppose à l'appel, il
    n'y a rien à en dire.

    `decideur` (#586) dit **qui tranche**, et il n'est renseigné que sur
    `ARBITRAGE` — `None` ailleurs, exactement pour la raison qui laisse le motif
    vide sur `PASSE` : un appel qu'on laisse passer ou qu'on refuse d'office
    n'est soumis à personne, il n'a donc pas de décideur. Le nommer quand même
    ferait lire « humain » là où aucune personne n'a été ni ne sera sollicitée.
    """

    verdict: Verdict
    motif: str = ""
    decideur: Decideur | None = None


def _motif_deny(outil: str) -> str:
    """Le motif d'un refus par la liste `deny` — servi à l'agent et tracé."""
    return (
        f"outil {outil!r} interdit par la politique de permissions de "
        "l'agent (liste deny). Poursuis la tâche sans cet outil."
    )


def _motif_allow(outil: str) -> str:
    """Le motif d'un refus par une liste `allow` fermée — servi à l'agent et tracé."""
    return (
        f"outil {outil!r} hors de la politique de permissions de l'agent "
        "(liste allow fermée). Poursuis la tâche sans cet outil."
    )


def _motif_ask(outil: str, decideur: Decideur) -> str:
    """Le motif d'une mise en arbitrage — lu par celui qui tranche, et tracé.

    Il nomme l'**acte** (l'outil appelé) et jamais le titre de la tâche : c'est
    tout l'objet du parent #573, où un mot du livrable déclenchait l'arbitrage.

    Il nomme aussi **qui décide** (#586), et c'est la moitié qui manquait : le
    motif est ce que le journal consigne en `sortie` et ce que l'écran affiche
    sous la question. Sans le décideur dedans, « qui a tranché » se déduirait de
    l'endroit d'où la ligne vient — c'est-à-dire pas du tout, une fois la ligne
    relue. Le champ (`DecisionOutil.decideur`, `DemandeValidation.decideur`)
    reste la source ; ce texte est ce qui la rend lisible.
    """
    return (
        f"outil {outil!r} soumis à arbitrage par la politique de permissions "
        f"de l'agent (liste ask, décideur « {decideur} »)."
    )


@dataclass(frozen=True)
class PolitiqueOutils:
    """La politique allow/ask/deny d'un agent — la partie versionnable du contrat.

    Priorité explicite : `deny` l'emporte sur `ask`, qui l'emporte sur
    `allow`. `allow` vide laisse tout passer (hors `deny`/`ask`) ; non vide,
    c'est une liste fermée — mais `ask` la déborde, un outil cité en `ask`
    étant arbitré et non refusé. Les entrées des trois listes ont la même
    forme : un outil intégré (`Bash`), un serveur MCP entier (`mcp__slack`) ou
    un outil MCP précis (`mcp__slack__send_message`).

    Celles d'`ask` portent en plus leur **décideur** (#586,
    `EntreeArbitrage`) : une chaîne nue vaut `humain`, et la normalisation a
    lieu ici, une fois, pour que le reste du module n'ait jamais affaire qu'à
    des `EntreeArbitrage`.

    ⚠ L'annotation d'`ask` décrit donc ce qu'on **lit** après construction, pas
    ce que le constructeur **accepte** : `PolitiqueOutils(ask=("Bash",))` reste
    valide et dit exactement ce qu'il disait avant ce lot — c'est
    `__post_init__` qui promeut la chaîne. Annoter l'union rendrait l'inverse :
    la tolérance à l'entrée serait exacte, et tous les appelants devraient
    prouver à chaque lecture qu'ils ne tiennent pas une chaîne nue, ce qui n'est
    jamais le cas.
    """

    allow: tuple[str, ...] = ()
    ask: tuple[EntreeArbitrage, ...] = ()
    deny: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # Frozen : la normalisation passe par `object.__setattr__`, comme dans
        # les autres dataclasses immuables du dépôt. Elle est **idempotente** —
        # reconstruire une politique déjà normalisée ne recrée aucune entrée —,
        # ce dont dépend l'aller-retour `from_dict(to_dict(p)) == p`.
        object.__setattr__(
            self, "ask", tuple(_entree_arbitrage(entree) for entree in self.ask)
        )

    def decideur(self, outil: str) -> Decideur | None:
        """Qui tranche `outil` s'il est soumis à arbitrage — None s'il ne l'est pas.

        Le raccourci de `decide(outil).decideur`, pour les appelants qui ne
        posent que cette question — l'exécuteur qui compose la demande, le
        journal qui la consigne. Comme `_consigne_refus_outil` redemande son
        verdict à la politique plutôt que de le déduire d'un texte (#583), on
        redemande ici le cran plutôt que de le faire voyager : la politique rend
        la même réponse au moment de consigner qu'au moment où le hook l'a lue.
        """
        return self.decide(outil).decideur

    def decide(self, outil: str) -> DecisionOutil:
        """Le verdict de la politique sur `outil`, motif compris.

        L'ordre des trois tests **est** la priorité annoncée (`deny` > `ask` >
        `allow`) : chacun tranche avant que le suivant ne soit posé. Deux
        conséquences voulues — un outil à la fois en `deny` et en `ask` est
        refusé (le cran le plus fermé gagne), et un outil en `ask` absent
        d'une liste `allow` fermée est **arbitré** plutôt que refusé, sans
        quoi fermer sa liste `allow` suffirait à rendre `ask` lettre morte.

        La **première** entrée `ask` qui couvre l'outil donne son décideur
        (#586). Première et non la plus précise, à dessein : c'est la même règle
        que les deux autres listes, où l'ordre du fichier fait foi, et une
        « plus précise » demanderait de départager `mcp__slack` de
        `mcp__slack__send_message` par une convention que personne n'a écrite —
        alors qu'un auteur de politique n'a qu'à mettre le cas particulier
        d'abord.
        """
        if any(_correspond(entree, outil) for entree in self.deny):
            return DecisionOutil(Verdict.REFUS, _motif_deny(outil))
        for entree in self.ask:
            if _correspond(entree, outil):
                return DecisionOutil(
                    Verdict.ARBITRAGE,
                    _motif_ask(outil, entree.decideur),
                    entree.decideur,
                )
        if not self.allow or any(_correspond(entree, outil) for entree in self.allow):
            return DecisionOutil(Verdict.PASSE)
        return DecisionOutil(Verdict.REFUS, _motif_allow(outil))

    def autorise(self, outil: str) -> bool:
        """L'appel de `outil` est-il permis par cette politique ?

        Contrat inchangé : la question est « **non interdit** », pas « laissé
        passer sans question ». Un outil classé `ask` y répond donc oui — il
        doit être monté sur la session pour que l'arbitrage puisse avoir lieu
        au vol ; un outil retiré avant l'ouverture n'atteindrait jamais le
        point de contrôle qui doit le suspendre.
        """
        return self.decide(outil).verdict is not Verdict.REFUS

    def raison_refus(self, outil: str) -> str:
        """Le motif du refus de `outil` — le message servi à l'agent et tracé.

        N'est appelé que sur un outil refusé, et ne connaît donc que les deux
        motifs de refus : `decide` est le verbe qui distingue les trois crans.
        """
        if any(_correspond(entree, outil) for entree in self.deny):
            return _motif_deny(outil)
        return _motif_allow(outil)

    def filtre_outils(self, outils: Sequence[str]) -> tuple[str, ...]:
        """Les outils intégrés de `outils` que la politique permet de monter."""
        return tuple(outil for outil in outils if self.autorise(outil))

    def serveur_autorise(self, nom: str) -> bool:
        """Le serveur MCP `nom` mérite-t-il d'être monté sur la session ?

        Faux si le serveur est refusé **en entier** (`deny` porte
        `mcp__<nom>`), ou si une liste `allow` fermée n'en cite aucun outil —
        dans les deux cas il n'est pas monté et ses secrets ne sont jamais
        résolus. Un refus **individuel** (`deny` sur un seul de ses outils)
        laisse le serveur monté : c'est le refus au vol qui l'applique. Une
        entrée `ask` le monte de même, et déborde la liste `allow` fermée
        comme dans `decide` : un serveur qu'on n'a pas monté n'a aucun appel à
        soumettre à l'arbitrage.
        """
        prefixe = f"{_PREFIXE_MCP}{nom}"
        if any(_correspond(entree, prefixe) for entree in self.deny):
            return False
        if _cite_serveur(self.ask, prefixe):
            return True
        if not self.allow:
            return True
        return _cite_serveur(self.allow, prefixe)

    def to_dict(self) -> dict[str, Any]:
        """Réémet la politique en dict JSON-sérialisable (la forme stockée/publique).

        `ask` sort **toujours** en objet `{entrée: décideur}` (#586), y compris
        quand toutes ses entrées sont au défaut. L'asymétrie avec la lecture,
        qui accepte les deux formes, est voulue : à l'entrée on relit ce qui
        existe déjà — des fichiers écrits avant ce lot —, à la sortie on n'écrit
        qu'une forme, la seule qui porte l'information entière. Deux formes en
        sortie obligeraient chaque consommateur à savoir les distinguer, pour
        n'économiser que quelques caractères sur le cas par défaut.
        """
        return {
            "allow": list(self.allow),
            "ask": {str(entree): str(entree.decideur) for entree in self.ask},
            "deny": list(self.deny),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PolitiqueOutils:
        """Reconstruit une politique depuis sa forme stockée (sans la valider).

        `ask` absent vaut liste vide : une politique écrite avant #580 se
        relit à l'identique, donc sous le régime d'hier. En **liste**, toutes
        ses entrées valent `humain` — le régime d'avant #586, au bit près ; en
        **objet**, chacune porte le sien.

        Sans validation, donc **tolérant** sur le cran (`decideur_depuis` :
        inconnu ⇒ `humain`). C'est `PermissionStore.lire` qui refuse franchement
        une politique douteuse, et ce partage est celui qui existe déjà entre ce
        verbe et `_liste_validee` : ici on reconstruit une valeur déjà admise,
        là-bas on décide si elle l'est.
        """
        return cls(
            allow=tuple(str(entree) for entree in data.get("allow", ())),
            ask=_ask_depuis(data.get("ask", ())),
            deny=tuple(str(entree) for entree in data.get("deny", ())),
        )


def _ask_depuis(brut: Any) -> tuple[EntreeArbitrage, ...]:
    """Relit la liste `ask` sous ses **deux** formes admises (#586).

    Un mapping porte un cran par entrée ; toute autre séquence est la forme
    d'avant ce lot, où le cran n'existait pas — donc `humain` partout.
    """
    if isinstance(brut, Mapping):
        return tuple(
            EntreeArbitrage(str(entree), decideur_depuis(cran))
            for entree, cran in brut.items()
        )
    return tuple(EntreeArbitrage(str(entree)) for entree in brut)


class PermissionStore:
    """Dépôt des politiques de permissions par agent (`<racine>/<agent>.json`).

    Un fichier par agent : `{"allow": [...], "ask": [...], "deny": [...]}` — le
    nom d'agent fait foi côté fichier, comme pour les autres dépôts. Une liste
    absente vaut vide, `ask` comprise : un fichier écrit avant #580 se relit
    tel quel. `ask` accepte en plus la forme `{"<outil>": "<décideur>"}` (#586),
    où un cran **inconnu** est refusé avec sa cause : une politique de
    garde-fou qu'on ne sait pas lire ne s'applique pas à moitié. Relu à chaque tâche
    (application à chaud, comme les playbooks #78) : une politique corrigée
    vaut pour la tâche suivante, sans redémarrage. `lire` valide le fichier et
    lève `ValueError` avec sa cause exacte s'il est invalide — on n'applique
    jamais une politique douteuse (ni ne l'ignore en silence).
    """

    def __init__(self, racine: Path) -> None:
        self._racine = racine

    @property
    def racine(self) -> Path:
        """La racine du dépôt (un fichier JSON par agent)."""
        return self._racine

    @classmethod
    def default(cls, settings: Settings | None = None) -> PermissionStore:
        """Le dépôt configuré : `MAESTRO_PERMISSIONS_DIR`, sinon `core/permissions/`."""
        settings = settings or load_settings()
        if settings.permissions_dir:
            return cls(Path(settings.permissions_dir))
        return cls(Path(__file__).resolve().parents[2] / "core" / "permissions")

    def lire(self, agent: str) -> PolitiqueOutils | None:
        """La politique de `agent`, validée — None s'il n'en déclare pas (tout permis).

        Lève `ValueError` (cause exacte, agent nommé) si le fichier est
        illisible ou la politique invalide : l'appelant (exécuteur, API) la
        mue en échec propre plutôt que d'exécuter sous une politique douteuse.
        """
        chemin = self._chemin(agent)
        if not chemin.is_file():
            return None
        try:
            data = json.loads(chemin.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"politique de permissions illisible pour l'agent {agent!r} "
                f"({chemin.name}) : {exc}"
            ) from exc
        if not isinstance(data, dict):
            raise ValueError(
                f"politique de permissions invalide pour l'agent {agent!r} "
                f'({chemin.name}) : objet {{"allow": [...], "ask": [...], '
                '"deny": [...]} attendu.'
            )
        return PolitiqueOutils(
            allow=_liste_validee(data, "allow", agent=agent),
            ask=_ask_validee(data, agent=agent),
            deny=_liste_validee(data, "deny", agent=agent),
        )

    def agents(self) -> tuple[str, ...]:
        """Les noms des agents ayant une politique stockée, triés (vide si aucun)."""
        if not self._racine.is_dir():
            return ()
        return tuple(
            sorted(
                chemin.stem
                for chemin in self._racine.glob("*.json")
                if _NOM_AGENT.match(chemin.stem)
            )
        )

    def _chemin(self, agent: str) -> Path:
        """Le fichier de politique de `agent`, nom validé (jamais un chemin arbitraire)."""
        if not _NOM_AGENT.match(agent):
            raise ValueError(f"nom d'agent invalide : {agent!r} (slug [a-z0-9_-] attendu).")
        return self._racine / f"{agent}.json"


def _liste_validee(data: Mapping[str, Any], cle: str, *, agent: str) -> tuple[str, ...]:
    """La liste `cle` (`allow`/`ask`/`deny`) du fichier, entrées validées et dédoublonnées.

    Chaque entrée doit être un nom d'outil admissible (`_ENTREE` par segment
    `__`) : une politique fautive est refusée en bloc avec sa cause — jamais
    appliquée en partie.
    """
    brut = data.get(cle, [])
    if not isinstance(brut, list):
        raise ValueError(
            f"politique de permissions invalide pour l'agent {agent!r} : "
            f"{cle} doit être une liste de noms d'outils."
        )
    entrees: list[str] = []
    for entree in brut:
        _valide_entree(entree, cle, agent=agent)
        if entree not in entrees:
            entrees.append(entree)
    return tuple(entrees)


def _ask_validee(data: Mapping[str, Any], *, agent: str) -> tuple[EntreeArbitrage, ...]:
    """La liste `ask` du fichier, sous ses deux formes, entrées **et crans** validés (#586).

    En **liste**, c'est `_liste_validee` mot pour mot, et chaque entrée retombe
    sur le cran par défaut : un fichier écrit avant ce lot passe par le même
    code qu'avant et rend la même chose.

    En **objet**, chaque clé est validée comme une entrée et chaque valeur doit
    être l'un des crans admis. Un cran inconnu est refusé **avec la liste de ce
    qui est admis** : c'est le seul message qui évite d'aller chercher les
    valeurs dans le code, et ce fichier est celui d'un garde-fou — on n'en
    applique jamais une version approximative. Le repli tolérant existe, mais
    ailleurs et pour autre chose (`decideur_depuis`, sur ce qui se relit après
    coup et ne peut plus être corrigé).

    ⚠ L'ensemble admis est lu de `tuple(Decideur)`, jamais recopié : c'est ce qui
    a fait échouer d'office les politiques écrivant `"orchestrateur"` quand #715 a
    retiré ce cran, sans qu'une seule ligne d'ici ait à bouger. Le message d'erreur
    se compose de la même source, donc il ne peut pas nommer un cran qui n'existe
    plus.
    """
    brut = data.get("ask", [])
    if not isinstance(brut, Mapping):
        return tuple(
            EntreeArbitrage(entree) for entree in _liste_validee(data, "ask", agent=agent)
        )
    entrees: list[EntreeArbitrage] = []
    for entree, cran in brut.items():
        _valide_entree(entree, "ask", agent=agent)
        if not isinstance(cran, str) or cran not in tuple(Decideur):
            admis = ", ".join(f"« {valeur} »" for valeur in Decideur)
            raise ValueError(
                f"politique de permissions invalide pour l'agent {agent!r} : "
                f"décideur {cran!r} de l'entrée ask {entree!r} inconnu "
                f"({admis} attendus)."
            )
        if entree not in entrees:
            entrees.append(EntreeArbitrage(entree, Decideur(cran)))
    return tuple(entrees)


def _valide_entree(entree: Any, cle: str, *, agent: str) -> None:
    """Refuse une entrée qui n'est pas un nom d'outil admissible — la même pour les trois listes."""
    if (
        not isinstance(entree, str)
        or not entree
        or not all(_ENTREE.match(segment) for segment in entree.split("__"))
    ):
        raise ValueError(
            f"politique de permissions invalide pour l'agent {agent!r} : "
            f"entrée {cle} {entree!r} (nom d'outil attendu — ex. « Bash », "
            "« mcp__slack » ou « mcp__slack__send_message »)."
        )

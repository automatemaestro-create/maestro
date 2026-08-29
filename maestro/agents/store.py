"""Catalogue dynamique d'agents : définitions persistées hors du code (ticket #72).

Matérialise la moitié « configuration » d'EF-03 (création d'agents personnalisés,
parent #70) : la définition d'un agent — nom, rôle, playbook, compétences,
fournisseur/modèle/effort — devient un document **persisté hors du code**, créé et
modifié depuis l'API Control Tower (`maestro.controltower.app`, endpoints
`/api/catalogue`). Ce que ces trois réglages de modèle peuvent valoir se lit, lui,
sur `GET /api/fournisseurs` (#253) — le dépôt stocke un choix, il ne dit pas
l'offre.
Au POC le dépôt est sur fichiers (`core/agents/<nom>.json`) ; en V1 il passera en
base (table AGENT, docs/03) sans changer ce contrat.

Le catalogue **effectif** d'une exécution est l'assemblage `catalogue()` : les
agents par défaut du code (`DEFAULT_AGENTS`) suivis des agents personnalisés du
dépôt — l'ordre préserve le départage déterministe du routeur (les rôles du code
restent prioritaires à score égal). Un dépôt vide rend le catalogue par défaut à
l'identique, ce qui rend ce lot mergeable seul.

**Trois états, et non deux** (#259). « Du code » et « personnalisé » ne suffisaient
pas : changer le modèle d'un agent du code obligeait à le **dupliquer** en agent
personnalisé, c'est-à-dire à recopier son playbook pour ne toucher qu'un réglage —
après quoi les deux exemplaires divergent en silence. Le troisième état est « du
code, **surchargé** » : `SurchargeStore` persiste, à côté de l'agent et sans le
remplacer, les trois réglages de modèle (`fournisseur`, `modele`, `effort`) que
l'on veut poser sur lui. Ce qui n'est pas surchargé reste **hérité** du code et le
suit — un playbook amélioré dans `maestro.agents.catalog` continue de valoir pour
un agent dont on a seulement changé le modèle. La surcharge **s'annule**
(`supprimer`) là où un agent personnalisé se **supprime** : retirer la surcharge
rend l'agent du code, retirer un agent personnalisé le fait disparaître — deux
gestes que rien ne doit confondre.

Le chargement se fait **au câblage** (construction du moteur, premier message d'un
worker, démarrage de l'API) : un agent créé est routable et exécutable par les
moteurs construits ensuite — sans runtime outillé, il produit son livrable par le
chemin texte (`LocalExecutor._produce`), cadré par son playbook et son modèle.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from maestro.agents.catalog import (
    DEFAULT_AGENTS,
    MODELE_EXECUTANT_DEFAUT,
    Agent,
    agents_pour,
)
from maestro.config import Settings, load_settings

#: Nom d'agent admissible comme fichier de stockage : slug sûr, sans séparateur ni
#: point — verrouille toute traversée de chemin depuis un nom venu de l'API.
_NOM_AGENT = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

#: Noms réservés : les agents par défaut du code (un agent personnalisé ne peut pas
#: les masquer) et les acteurs système de la Control Tower — l'orchestrateur, et
#: l'assistant du canal d'aide (#123, `maestro.controltower.assistance.NOM_ASSISTANCE` :
#: la chaîne est répétée ici plutôt qu'importée, la couche agents ne dépendant pas de
#: la Control Tower). Sans cette réserve, un agent personnalisé homonyme partagerait le
#: fil `core/chat/assistance.jsonl` de l'assistant et serait masqué par lui au chat.
NOMS_RESERVES: frozenset[str] = frozenset(
    {agent.nom for agent in DEFAULT_AGENTS} | {"orchestrateur", "assistance"}
)

#: Les agents **du code** : ceux que `DEFAULT_AGENTS` définit, et les seuls qu'une
#: surcharge puisse viser (#259). Strictement plus étroit que `NOMS_RESERVES`, qui
#: couvre aussi l'orchestrateur et l'assistant : ces deux-là ne sont pas des agents
#: du catalogue, ils n'ont ni fiche ni réglage de modèle à surcharger.
NOMS_DU_CODE: frozenset[str] = frozenset(agent.nom for agent in DEFAULT_AGENTS)

#: Les **trois états** d'une fiche du catalogue, tels que l'API les nomme (#259).
#: Ils vivent ici, avec la règle qui les produit, plutôt qu'en littéraux dans
#: `maestro.controltower.app` : le troisième est né d'avoir eu à écrire deux fois
#: la même chaîne, et le front en tient déjà le miroir (`apps/web/lib/types.ts`).
AGENT_SOURCE_DEFAUT = "defaut"
AGENT_SOURCE_SURCHARGE = "defaut_surcharge"
AGENT_SOURCE_PERSONNALISE = "personnalise"

#: Les trois réglages de modèle qu'une surcharge peut porter (#259), dans l'ordre
#: où ils se lisent : on choisit un fournisseur, puis un de ses modèles, puis
#: l'effort que ce modèle admet (#811). Le tuple est la source unique de la liste —
#: `SurchargeAgent.herite()` et les fiches de l'API la dérivent, personne ne la
#: recopie.
REGLAGES_SURCHARGEABLES: tuple[str, ...] = ("fournisseur", "modele", "effort")


@dataclass(frozen=True)
class AgentDefinition:
    """La définition persistée d'un agent personnalisé — l'entité AGENT (docs/03).

    `playbook` porte les instructions du rôle (le prompt système d'exécution,
    docs/04 §1) ; `modele` le modèle conseillé (None : le modèle par défaut des
    exécutants) ; `fournisseur` est **déclaratif au POC** — le moteur exécute sur
    un fournisseur unique (`MAESTRO_PROVIDER`), le champ prépare l'exécution
    multi-fournisseurs sans la promettre. `cree_le`/`modifie_le` sont posés par
    le dépôt à l'écriture (ISO 8601, UTC).

    `effort` (#253) est le troisième réglage de modèle, à côté de `fournisseur`
    et `modele` — et lui, contrairement au fournisseur, **atteint l'exécution**
    (`Agent.effort`, puis la frontière). Il est stocké **tel qu'il a été posé**,
    sans confrontation à ce que le fournisseur admet : le dépôt ne connaît aucun
    fournisseur, et un catalogue qui bouge (modèle changé, gamme élargie) ne doit
    pas rendre invalide une définition écrite hier. Le tri se fait à
    l'exécution, où il est **ignoré sans erreur** s'il n'a plus cours.
    """

    nom: str
    role: str
    competences: tuple[str, ...]
    playbook: str
    modele: str | None = None
    fournisseur: str | None = None
    effort: str | None = None
    cree_le: str = ""
    modifie_le: str = ""

    def to_agent(self, modele_impose: str | None = None) -> Agent:
        """La définition muée en `Agent` du catalogue, routable et exécutable.

        `modele_impose` (#69, `MAESTRO_MODEL`) prime sur le modèle de la
        définition — même bascule globale que pour les agents par défaut. Elle ne
        touche **pas** à l'effort (#253) : `MAESTRO_MODEL` bascule le modèle, et
        un effort n'est pas un modèle — l'écraser au passage retirerait en silence
        un réglage que personne n'a demandé de retirer.
        """
        return Agent(
            nom=self.nom,
            role=self.role,
            competences=frozenset(self.competences),
            modele=modele_impose or self.modele or MODELE_EXECUTANT_DEFAUT,
            prompt_systeme=self.playbook,
            effort=self.effort,
        )

    def to_dict(self, *, avec_playbook: bool = True) -> dict[str, Any]:
        """Réémet la définition en dict JSON-sérialisable (métadonnées seules si demandé)."""
        fiche: dict[str, Any] = {
            "nom": self.nom,
            "role": self.role,
            "competences": list(self.competences),
            "modele": self.modele,
            "fournisseur": self.fournisseur,
            "effort": self.effort,
            "cree_le": self.cree_le,
            "modifie_le": self.modifie_le,
        }
        if avec_playbook:
            fiche["playbook"] = self.playbook
        return fiche

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentDefinition:
        """Reconstruit une définition depuis sa forme `to_dict` (le fichier stocké).

        `effort` est lu en `get` comme les autres réglages optionnels : les
        fichiers écrits avant #253 n'en portent pas, et se relisent sans
        migration ni valeur par défaut inventée.
        """
        return cls(
            nom=data["nom"],
            role=data["role"],
            competences=tuple(data.get("competences", ())),
            playbook=data.get("playbook", ""),
            modele=data.get("modele"),
            fournisseur=data.get("fournisseur"),
            effort=data.get("effort"),
            cree_le=data.get("cree_le", ""),
            modifie_le=data.get("modifie_le", ""),
        )


@dataclass(frozen=True)
class SurchargeAgent:
    """Les réglages de modèle posés **sur** un agent du code, sans le dupliquer (#259).

    Le troisième état du catalogue (cf. docstring du module) : l'agent reste celui
    de `maestro.agents.catalog` — son rôle, ses compétences et son playbook
    continuent de venir du code et d'en suivre les évolutions — et seuls les
    réglages ici renseignés le recouvrent. Un champ à `None` n'est **pas** un
    réglage vide : c'est un réglage **hérité**, que `herite()` nomme pour que l'UI
    puisse le marquer comme tel plutôt que de le faire deviner.

    `fournisseur` est déclaratif au POC, comme sur une définition personnalisée
    (le moteur exécute sur `MAESTRO_PROVIDER`) ; `modele` et `effort`, eux,
    atteignent l'exécution par `catalogue()`. Aucun des trois n'est confronté à ce
    que le fournisseur admet : le dépôt ne connaît aucun fournisseur, et une gamme
    qui bouge ne doit pas rendre invalide une surcharge écrite hier — c'est la
    frontière qui trie, en ignorant sans erreur (même règle qu'`AgentDefinition`).

    `modifie_le` est posé par le dépôt à l'écriture (ISO 8601, UTC) ; vide sur la
    surcharge **absente** que `SurchargeStore.lire` rend pour un agent jamais
    surchargé.
    """

    nom: str
    fournisseur: str | None = None
    modele: str | None = None
    effort: str | None = None
    modifie_le: str = ""

    @property
    def vide(self) -> bool:
        """True quand rien n'est surchargé — l'agent est celui du code, tel quel."""
        return all(getattr(self, reglage) is None for reglage in REGLAGES_SURCHARGEABLES)

    def herite(self) -> tuple[str, ...]:
        """Les réglages qui restent **hérités du code** : ceux que la surcharge ne pose pas.

        C'est le pendant lisible de `vide` : une surcharge vide hérite des trois,
        une surcharge complète d'aucun. L'UI s'en sert pour marquer d'où vient
        chaque valeur affichée, et pour n'offrir « revenir au défaut » que sur ce
        qui a effectivement été surchargé.
        """
        return tuple(
            reglage
            for reglage in REGLAGES_SURCHARGEABLES
            if getattr(self, reglage) is None
        )

    def to_dict(self) -> dict[str, Any]:
        """Réémet la surcharge en dict JSON-sérialisable (le fichier stocké)."""
        return {
            "nom": self.nom,
            "fournisseur": self.fournisseur,
            "modele": self.modele,
            "effort": self.effort,
            "modifie_le": self.modifie_le,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SurchargeAgent:
        """Reconstruit une surcharge depuis sa forme `to_dict` (le fichier stocké)."""
        return cls(
            nom=data["nom"],
            fournisseur=data.get("fournisseur"),
            modele=data.get("modele"),
            effort=data.get("effort"),
            modifie_le=data.get("modifie_le", ""),
        )


class SurchargeStore:
    """Dépôt des surcharges d'agents du code, sur fichiers (`<racine>/<nom>.json`).

    Même pattern que `maestro.agents.capacity.CapacityStore` — un fichier par
    agent, écrit atomiquement, et un agent sans fichier a la surcharge **vide** :
    `lire` ne rend jamais None, ce qui évite à chaque appelant de distinguer
    « pas de fichier » de « rien de surchargé », deux façons de dire la même
    chose. Un seul écrivain à la fois au POC (l'API Control Tower) : pas de
    verrou de concurrence.

    ⚠ **Une surcharge vide ne se stocke pas** : `ecrire` d'une surcharge dont les
    trois réglages sont absents *retire* le fichier. Sans cette règle, « du code,
    surchargé avec rien » existerait sur le disque à côté de « du code », deux
    états indiscernables à l'usage dont l'un afficherait pourtant un agent comme
    modifié — c'est le même piège que la chaîne vide d'`effort` dans `_valide`.
    Annuler une surcharge et n'en poser aucune sont ainsi le **même** état.
    """

    def __init__(self, racine: Path) -> None:
        self._racine = racine

    @property
    def racine(self) -> Path:
        """La racine du dépôt (un fichier JSON par agent surchargé)."""
        return self._racine

    @classmethod
    def default(cls, settings: Settings | None = None) -> SurchargeStore:
        """Le dépôt configuré : `MAESTRO_SURCHARGES_DIR`, sinon `core/surcharges/`."""
        settings = settings or load_settings()
        if settings.surcharges_dir:
            return cls(Path(settings.surcharges_dir))
        return cls(Path(__file__).resolve().parents[2] / "core" / "surcharges")

    def lire(self, nom: str) -> SurchargeAgent:
        """La surcharge de l'agent `nom` — vide s'il n'a jamais été surchargé."""
        chemin = self._chemin(nom)
        if not chemin.is_file():
            return SurchargeAgent(nom=nom)
        surcharge = SurchargeAgent.from_dict(json.loads(chemin.read_text(encoding="utf-8")))
        # Le nom fait foi côté fichier, comme pour les définitions et les capacités.
        return replace(surcharge, nom=nom)

    def lister(self) -> tuple[SurchargeAgent, ...]:
        """Les surcharges **posées** (stockées), par nom — les autres agents sont au code."""
        if not self._racine.is_dir():
            return ()
        return tuple(
            self.lire(chemin.stem)
            for chemin in sorted(self._racine.glob("*.json"))
            if _NOM_AGENT.match(chemin.stem)
        )

    def ecrire(self, surcharge: SurchargeAgent) -> SurchargeAgent:
        """Persiste `surcharge` (remplacement intégral) et la renvoie datée.

        Écriture atomique (fichier temporaire puis renommage). Les réglages sont
        épurés et ramenés à `None` quand il n'en reste rien ; si les **trois** le
        sont, la surcharge est retirée du dépôt et rendue vide (cf. docstring de
        la classe). Lève `ValueError` si le nom n'est pas celui d'un agent du code
        — un agent personnalisé ne se surcharge pas, sa définition **est** son
        réglage et l'API l'édite directement : deux chemins d'écriture pour la
        même valeur sont exactement ce que #259 supprime.
        """
        propre = _valide_surcharge(surcharge)
        if propre.vide:
            self.supprimer(propre.nom)
            return propre
        propre = replace(propre, modifie_le=_maintenant())
        self._racine.mkdir(parents=True, exist_ok=True)
        chemin = self._chemin(propre.nom)
        temporaire = chemin.with_suffix(".json.tmp")
        temporaire.write_text(
            json.dumps(propre.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporaire, chemin)
        return propre

    def supprimer(self, nom: str) -> bool:
        """Annule la surcharge de `nom` (retour au code) ; False s'il n'y en avait pas.

        Annule, ne détruit pas : l'agent reste au catalogue, avec les réglages du
        code. C'est la différence de fond avec `AgentStore.supprimer`, qui fait
        disparaître un agent personnalisé.
        """
        chemin = self._chemin(nom)
        if not chemin.is_file():
            return False
        chemin.unlink()
        return True

    def _chemin(self, nom: str) -> Path:
        """Le fichier de stockage de `nom`, nom validé (jamais un chemin arbitraire)."""
        if not _NOM_AGENT.match(nom):
            raise ValueError(f"nom d'agent invalide : {nom!r} (slug [a-z0-9_-] attendu).")
        return self._racine / f"{nom}.json"


class AgentStore:
    """Dépôt des définitions d'agents personnalisés, sur fichiers (`<racine>/<nom>.json`).

    Un fichier par agent, écrit atomiquement ; `ecrire` crée ou remplace la
    définition (la date de création survit au remplacement), `supprimer` la
    retire. Un seul écrivain à la fois au POC (l'API Control Tower) : le dépôt
    ne porte pas de verrou de concurrence.
    """

    def __init__(self, racine: Path) -> None:
        self._racine = racine

    @property
    def racine(self) -> Path:
        """La racine du dépôt (un fichier JSON par agent)."""
        return self._racine

    @classmethod
    def default(cls, settings: Settings | None = None) -> AgentStore:
        """Le dépôt configuré : `MAESTRO_AGENTS_DIR`, sinon `core/agents/` du dépôt."""
        settings = settings or load_settings()
        if settings.agents_dir:
            return cls(Path(settings.agents_dir))
        return cls(Path(__file__).resolve().parents[2] / "core" / "agents")

    def noms(self) -> tuple[str, ...]:
        """Les noms des agents personnalisés stockés, triés (vide si aucun)."""
        if not self._racine.is_dir():
            return ()
        return tuple(
            sorted(
                chemin.stem
                for chemin in self._racine.glob("*.json")
                if _NOM_AGENT.match(chemin.stem)
            )
        )

    def lister(self) -> tuple[AgentDefinition, ...]:
        """Les définitions stockées, dans l'ordre des noms — déterministe pour le routage."""
        return tuple(
            definition
            for nom in self.noms()
            if (definition := self.lire(nom)) is not None
        )

    def lire(self, nom: str) -> AgentDefinition | None:
        """La définition de l'agent `nom`, ou None s'il n'est pas dans le dépôt."""
        chemin = self._chemin(nom)
        if not chemin.is_file():
            return None
        definition = AgentDefinition.from_dict(json.loads(chemin.read_text(encoding="utf-8")))
        # Le nom fait foi côté fichier : un contenu recopié sous un autre nom
        # reste adressé (et donc routé) par le nom du fichier.
        return replace(definition, nom=nom)

    def ecrire(self, definition: AgentDefinition) -> AgentDefinition:
        """Persiste `definition` (création ou remplacement intégral) et la renvoie datée.

        Écriture atomique (fichier temporaire puis renommage) : une définition
        n'apparaît dans le dépôt que complète. Lève `ValueError` si la définition
        est invalide (nom hors slug ou réservé, rôle/playbook vides, aucune
        compétence) — le dépôt ne stocke jamais un agent inexécutable.
        """
        propre = _valide(definition)
        existante = self.lire(propre.nom)
        maintenant = _maintenant()
        propre = replace(
            propre,
            cree_le=existante.cree_le if existante is not None else maintenant,
            modifie_le=maintenant,
        )
        self._racine.mkdir(parents=True, exist_ok=True)
        chemin = self._chemin(propre.nom)
        temporaire = chemin.with_suffix(".json.tmp")
        temporaire.write_text(
            json.dumps(propre.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporaire, chemin)
        return propre

    def supprimer(self, nom: str) -> bool:
        """Retire l'agent `nom` du dépôt ; False s'il n'y était pas (rien à faire)."""
        chemin = self._chemin(nom)
        if not chemin.is_file():
            return False
        chemin.unlink()
        return True

    def _chemin(self, nom: str) -> Path:
        """Le fichier de stockage de `nom`, nom validé (jamais un chemin arbitraire)."""
        if not _NOM_AGENT.match(nom):
            raise ValueError(f"nom d'agent invalide : {nom!r} (slug [a-z0-9_-] attendu).")
        return self._racine / f"{nom}.json"


def catalogue(
    store: AgentStore | None = None,
    modele: str | None = None,
    surcharges: SurchargeStore | None = None,
) -> tuple[Agent, ...]:
    """Le catalogue effectif : les agents par défaut, puis les personnalisés du dépôt.

    C'est le point de chargement « au démarrage » du #72 : les moteurs
    (`OrchestrationEngine.default`), les workers (`maestro.queue.worker`) et
    l'état Control Tower assemblent leur catalogue ici. Les agents par défaut
    gardent la tête (leur ordre départage les ex æquo de routage) ; les
    personnalisés suivent, par nom. `modele` (#69, `MAESTRO_MODEL`) bascule
    l'ensemble sur un modèle unique, définitions personnalisées comprises.

    `surcharges` (#259) recouvre les réglages de modèle des agents **du code**,
    ici et non ailleurs : c'est le seul endroit où le catalogue effectif
    s'assemble, donc le seul par lequel une surcharge posée depuis l'UI atteint
    l'exécution — les trois appelants (moteur, worker, activité durable) en
    héritent sans une ligne. Un dépôt de surcharges vide rend exactement le
    catalogue d'avant.
    """
    store = store if store is not None else AgentStore.default()
    surcharges = surcharges if surcharges is not None else SurchargeStore.default()
    posees = {surcharge.nom: surcharge for surcharge in surcharges.lister()}
    return tuple(
        _surcharge_appliquee(agent, posees.get(agent.nom), modele)
        for agent in agents_pour(modele)
    ) + tuple(definition.to_agent(modele) for definition in store.lister())


def _surcharge_appliquee(
    agent: Agent, surcharge: SurchargeAgent | None, modele_impose: str | None
) -> Agent:
    """`agent` recouvert de sa surcharge — inchangé s'il n'en a pas (#259).

    `modele_impose` (#69, `MAESTRO_MODEL`) prime sur la surcharge comme il prime
    sur le modèle d'une définition personnalisée : c'est une bascule globale, et
    lui faire céder devant un réglage par agent la viderait de son sens. Il ne
    touche **pas** à l'effort, pour la raison qu'`AgentDefinition.to_agent`
    donne déjà : un effort n'est pas un modèle.

    Le `fournisseur` n'atteint pas l'exécution — il est déclaratif au POC, ici
    comme sur une définition personnalisée (le moteur exécute sur
    `MAESTRO_PROVIDER`) : il est stocké et affiché, il n'entre pas dans l'`Agent`,
    qui ne porte pas ce champ.
    """
    if surcharge is None:
        return agent
    return replace(
        agent,
        modele=modele_impose or surcharge.modele or agent.modele,
        effort=surcharge.effort if surcharge.effort is not None else agent.effort,
    )


def _valide(definition: AgentDefinition) -> AgentDefinition:
    """La définition normalisée (compétences épurées), ou `ValueError` si invalide.

    L'`effort` (#253) est **normalisé, jamais refusé** : épuré, et ramené à `None`
    s'il ne reste rien — une chaîne vide et « pas de réglage » ne doivent pas
    coexister dans le dépôt, sans quoi l'UI aurait deux façons de dire la même
    absence. Le refuser, en revanche, exigerait de connaître ici le fournisseur et
    sa gamme du jour ; c'est la frontière qui tranche, et elle ignore.
    """
    if not _NOM_AGENT.match(definition.nom):
        raise ValueError(
            f"nom d'agent invalide : {definition.nom!r} (slug [a-z0-9_-] attendu)."
        )
    if definition.nom in NOMS_RESERVES:
        raise ValueError(
            f"nom d'agent réservé : {definition.nom!r} (agent par défaut ou acteur système)."
        )
    if not definition.role.strip():
        raise ValueError(f"rôle vide pour l'agent {definition.nom!r}.")
    if not definition.playbook.strip():
        raise ValueError(f"playbook vide pour l'agent {definition.nom!r}.")
    competences = tuple(
        dict.fromkeys(c.strip() for c in definition.competences if c.strip())
    )
    if not competences:
        raise ValueError(
            f"aucune compétence pour l'agent {definition.nom!r} : il ne serait "
            "jamais retenu par les règles de routage."
        )
    effort = (definition.effort or "").strip() or None
    return replace(
        definition,
        role=definition.role.strip(),
        competences=competences,
        effort=effort,
    )


def _valide_surcharge(surcharge: SurchargeAgent) -> SurchargeAgent:
    """La surcharge normalisée (réglages épurés), ou `ValueError` si elle ne vise rien.

    Le **nom** est la seule chose refusée, et il l'est fermement : une surcharge
    ne se pose que sur un agent du code (`NOMS_DU_CODE`). Sur un agent
    personnalisé elle ferait un second chemin d'écriture vers les mêmes trois
    réglages, que sa définition porte déjà — le doublon même que #259 supprime
    côté playbook. Sur `orchestrateur`/`assistance`, elle viserait des acteurs
    qui n'ont pas de fiche au catalogue.

    Les **réglages**, eux, sont normalisés et jamais refusés : épurés, et ramenés
    à `None` s'il ne reste rien — « chaîne vide » et « hérité du code » ne doivent
    pas coexister, sans quoi l'UI aurait deux façons de dire la même absence
    (même règle que l'`effort` de `_valide`). Ce que le fournisseur admet se
    tranche à l'exécution, qui ignore sans erreur.
    """
    if surcharge.nom not in NOMS_DU_CODE:
        raise ValueError(
            f"surcharge impossible sur {surcharge.nom!r} : seuls les agents du "
            "code se surchargent (un agent personnalisé se modifie directement)."
        )
    # Les trois réglages sont nommés un à un plutôt que dérivés de
    # `REGLAGES_SURCHARGEABLES` : `replace()` veut des mots-clés littéraux pour
    # être typable, et un `**dict` les lui cacherait. La constante reste la
    # source unique là où elle sert vraiment — l'ordre de lecture d'`herite()`.
    return replace(
        surcharge,
        fournisseur=_epure(surcharge.fournisseur),
        modele=_epure(surcharge.modele),
        effort=_epure(surcharge.effort),
    )


def _epure(valeur: str | None) -> str | None:
    """Un réglage épuré, la chaîne vide valant « pas de réglage » (donc hérité)."""
    return (valeur or "").strip() or None


def _maintenant() -> str:
    """L'horodatage d'écriture d'une définition (ISO 8601, UTC, à la seconde)."""
    return datetime.now(tz=UTC).isoformat(timespec="seconds")

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
agents **du dépôt**, et eux seuls. Les cinq fiches du code n'y entrent plus
(#1042, [docs/37 §2.1](../../docs/37-decision-equipe-sur-mesure.md)) — elles sont
devenues des **gabarits de rôle** (`gabarits_du_code`), une matière que l'analyse
d'équipe consulte et que personne n'instancie. Un dépôt vide rend donc un
catalogue vide, et c'est exactement ce que veut dire *un projet naît sans agent* :
un projet créé ou importé n'a aucun agent tant que son équipe n'a pas été
proposée, validée et écrite (`maestro.equipe`).

**Trois états, et non deux** (#259). « Du code » et « personnalisé » ne suffisaient
pas : changer le modèle d'un agent du code obligeait à le **dupliquer** en agent
personnalisé, c'est-à-dire à recopier son playbook pour ne toucher qu'un réglage —
après quoi les deux exemplaires divergent en silence. Le troisième état est « du
code, **surchargé** » : `SurchargeStore` persiste, à côté de la fiche et sans la
remplacer, les trois réglages de modèle (`fournisseur`, `modele`, `effort`) que
l'on veut poser sur elle. Ce qui n'est pas surchargé reste **hérité** du code et le
suit — un playbook amélioré dans `maestro.agents.catalog` continue de valoir pour
une fiche dont on a seulement changé le modèle. La surcharge **s'annule**
(`supprimer`) là où un agent personnalisé se **supprime** : retirer la surcharge
rend la fiche du code, retirer un agent personnalisé le fait disparaître — deux
gestes que rien ne doit confondre.

⚠ Depuis #1042 une surcharge est le réglage d'un **gabarit**, et non plus d'un
agent que le catalogue effectif porterait : elle se lit par `gabarits_du_code()`,
que l'API sert et que l'analyse d'équipe consulte. Elle n'atteint donc plus une
exécution — `catalogue()` ne rend que des agents de projet, et un nom de gabarit
reste réservé (`NOMS_RESERVES`), donc aucun agent de projet ne peut en porter un.
C'est la conséquence assumée de docs/37 §2.1 : ce qu'un projet règle, il le règle
sur **ses** agents, et ce que le code livre, il le livre en gabarit.

Le chargement se fait **au câblage** (construction du moteur, premier message d'un
worker, démarrage de l'API) : un agent créé est routable et exécutable par les
moteurs construits ensuite. Et depuis #1037 il est exécutable **outillé** : le
runtime de chaque tâche se dérive de la fiche de l'agent routé
(`maestro.agents.fiche_outillee`), si bien qu'un agent défini ici lit, écrit dans
le projet et exécute sous ses autorisations (#110) et avec ses serveurs MCP (#104),
par le même chemin que les rôles du code. Le chemin texte (`LocalExecutor._produce`)
n'est plus que le repli d'un fournisseur sans exécution outillée.
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
    GABARITS_DU_CODE,
    MODELE_EXECUTANT_DEFAUT,
    Agent,
    gabarits_pour,
)
from maestro.agents.rangement import RangeParProjet
from maestro.config import Settings, load_settings

#: Nom d'agent admissible comme fichier de stockage : slug sûr, sans séparateur ni
#: point — verrouille toute traversée de chemin depuis un nom venu de l'API.
_NOM_AGENT = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

#: Les noms **du code** : ceux des gabarits de rôle, et les seuls qu'une surcharge
#: puisse viser (#259). Strictement plus étroit que `NOMS_RESERVES`, qui couvre aussi
#: l'orchestrateur et l'assistant : ces deux-là n'ont ni fiche ni réglage de modèle à
#: surcharger.
NOMS_DU_CODE: frozenset[str] = frozenset(agent.nom for agent in GABARITS_DU_CODE)

#: Les acteurs **système** de la Control Tower — l'orchestrateur, et l'assistant du
#: canal d'aide (#123, `maestro.controltower.assistance.NOM_ASSISTANCE` : la chaîne
#: est répétée ici plutôt qu'importée, la couche agents ne dépendant pas de la Control
#: Tower). Sans cette réserve, un agent homonyme partagerait le fil
#: `core/chat/assistance.jsonl` de l'assistant et serait masqué par lui au chat.
NOMS_SYSTEME: frozenset[str] = frozenset({"orchestrateur", "assistance"})

#: Noms qu'une fiche d'agent ne peut pas porter — et **la réserve a changé de raison**
#: avec #1042, pas de contenu. Elle disait : « un agent personnalisé ne peut pas
#: masquer un agent par défaut ». Il n'y a plus d'agent par défaut à masquer. Ce qui
#: reste, et qui suffit : le paquet livre un **document de playbook** sous chacun de
#: ces cinq noms (`maestro.agents.playbook_du_code.roles_du_code`), et
#: `maestro.agents.fiche_outillee.playbook_outille` le sert **à la place** du playbook
#: de la fiche pour tout agent qui en porte un. Un rôle de projet nommé `developpeur`
#: partirait donc en exécution avec le playbook du code, et le texte écrit pour ce
#: projet-là (#257) n'atteindrait jamais le modèle — en silence. C'est aussi ce qui
#: fait que les rôles proposés par l'analyse d'équipe portent d'autres noms que leur
#: gabarit (`maestro.equipe.gabarits`, `dev` descend de `developpeur`).
#:
#: Les acteurs système s'y ajoutent pour leur raison propre, ci-dessus.
NOMS_RESERVES: frozenset[str] = NOMS_DU_CODE | NOMS_SYSTEME

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
        définition — même bascule globale que pour les gabarits du code. Elle ne
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


class SurchargeStore(RangeParProjet):
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

    Cadré sur un projet (#1038), il **recouvre** le gabarit : une surcharge que
    le projet n'a pas posée est celle du gabarit. Annuler une surcharge dans un
    projet le ramène donc au gabarit — c'est-à-dire à l'agent du code tant que
    rien n'y est surchargé, l'invariant du #259 dans le nouveau rangement.
    """

    @classmethod
    def default(cls, settings: Settings | None = None) -> SurchargeStore:
        """Le dépôt configuré : `MAESTRO_SURCHARGES_DIR`, sinon `core/surcharges/`."""
        settings = settings or load_settings()
        if settings.surcharges_dir:
            return cls(Path(settings.surcharges_dir))
        return cls(Path(__file__).resolve().parents[2] / "core" / "surcharges")

    def lire(self, nom: str) -> SurchargeAgent:
        """La surcharge de l'agent `nom` — celle du gabarit, ou vide, à défaut."""
        chemin = self._chemin(nom)
        if not chemin.is_file():
            if self._gabarits is not None:
                return self._gabarits.lire(nom)
            return SurchargeAgent(nom=nom)
        surcharge = SurchargeAgent.from_dict(json.loads(chemin.read_text(encoding="utf-8")))
        # Le nom fait foi côté fichier, comme pour les définitions et les capacités.
        return replace(surcharge, nom=nom)

    def lister(self) -> tuple[SurchargeAgent, ...]:
        """Les surcharges **posées**, par nom — celles du projet par-dessus celles du gabarit.

        L'union, et non le remplacement : c'est elle que `catalogue()` applique,
        donc la seule façon qu'une surcharge héritée atteigne l'exécution.
        """
        posees = {surcharge.nom: surcharge for surcharge in self._stockees()}
        if self._gabarits is not None:
            heritees = {s.nom: s for s in self._gabarits.lister()}
            heritees.update(posees)
            posees = heritees
        return tuple(posees[nom] for nom in sorted(posees))

    def _stockees(self) -> tuple[SurchargeAgent, ...]:
        """Les surcharges posées **dans ce dépôt-ci**, sans rien hériter."""
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


class AgentStore(RangeParProjet):
    """Dépôt des définitions d'agents personnalisés, sur fichiers (`<racine>/<nom>.json`).

    Un fichier par agent, écrit atomiquement ; `ecrire` crée ou remplace la
    définition (la date de création survit au remplacement), `supprimer` la
    retire. Un seul écrivain à la fois au POC (l'API Control Tower) : le dépôt
    ne porte pas de verrou de concurrence.

    ⚠ **Seul des six dépôts à n'hériter de rien** (#1038) : ce qu'il stocke
    décide de l'**existence** d'un agent, et l'appartenance n'a pas de défaut
    sensé là où un réglage en a un. Une définition rangée au niveau gabarit est
    un *gabarit de rôle* (ce que #1039 consultera), pas un membre de l'équipe
    d'un projet — et c'est la reprise (`maestro.agents.reprise`) qui rattache au
    projet ceux qui y travaillaient déjà, sans rien supprimer.
    """

    herite_du_gabarit = False

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
        propre = definition_validee(definition)
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
) -> tuple[Agent, ...]:
    """Le catalogue effectif : les agents **du dépôt**, par nom — et eux seuls (#1042).

    C'est le point de chargement « au démarrage » du #72 : les moteurs
    (`OrchestrationEngine.default`), les workers (`maestro.queue.worker`) et
    l'état Control Tower assemblent leur catalogue ici. `modele` (#69,
    `MAESTRO_MODEL`) bascule l'ensemble sur un modèle unique.

    **Cadré sur un projet** (#1038) quand le dépôt l'est (`store.pour_projet(id)`) :
    le catalogue rend alors les agents **de ce projet**, jamais ceux d'un autre —
    l'existence d'un agent ne s'héritant pas du gabarit (`AgentStore`).

    ⚠ **Les cinq fiches du code n'y entrent plus** (#1042, docs/37 §2.1). Elles y
    étaient en tête, et c'est ce qui donnait cinq agents à tout projet sans que
    personne les ait recrutés. Elles sont désormais des gabarits
    (`gabarits_du_code`), consultés par l'analyse d'équipe et jamais instanciés.
    Conséquence directe, et c'est le critère : **un dépôt de projet neuf rend un
    catalogue vide**. Un moteur construit sans équipe garde, lui, son repli de
    câblage (`LocalExecutor`, `OrchestrationEngine`), qui ne décrit aucun projet.
    """
    store = store if store is not None else AgentStore.default()
    return tuple(definition.to_agent(modele) for definition in store.lister())


def catalogue_hors_projet(
    store: AgentStore | None = None,
    surcharges: SurchargeStore | None = None,
    modele: str | None = None,
) -> tuple[Agent, ...]:
    """Le catalogue de **câblage** d'un moteur : avec quoi travailler hors de tout projet.

    Les trois câblages de production le partagent — moteur en process
    (`OrchestrationEngine.default`), worker Celery, activité durable —, et c'est
    pour qu'ils ne répondent pas trois fois, différemment, à la même question :
    *que route un moteur quand la tâche n'appartient à aucun projet ?*

    La règle, en une phrase : **les agents rangés hors projet, et à défaut les
    gabarits du code.** Le repli est ce qui fait qu'un `maestro-run` — qui n'a
    pas de projet et n'en a jamais eu — continue de travailler avec les cinq rôles
    que le paquet livre. Il ne contredit pas #1042 :
    hors de tout projet il n'y a pas d'équipe où chercher, alors que **dans** un
    projet il y en a une, et c'est `LocalExecutor._equipe` qui la relit à chaque
    tâche — vide, elle laisse la tâche « à assigner » sans jamais retomber ici.
    """
    hors_projet = catalogue(store, modele)
    if hors_projet:
        return hors_projet
    return gabarits_du_code(surcharges, modele)


def gabarits_du_code(
    surcharges: SurchargeStore | None = None,
    modele: str | None = None,
) -> tuple[Agent, ...]:
    """Les gabarits de rôle du code, leurs réglages posés — **jamais instanciés** (#1042).

    Le pendant de `catalogue()` pour ce que le paquet **livre** plutôt que pour ce
    qu'un projet **a** : les cinq fiches de `maestro.agents.catalog`, dans leur
    ordre, recouvertes des surcharges (#259) posées sur elles. C'est ce que l'API
    sert au niveau gabarit (`GET /api/catalogue` sans `?projet=`), ce que les
    écrans de réglages éditent, et la matière dont l'analyse d'équipe tire les
    rôles proposés (`maestro.equipe.gabarits`).

    Rien ici n'est exécuté : un gabarit ne reçoit pas de tâche, il se recopie dans
    un projet qui le valide. `modele` (#69) bascule les cinq fiches comme ailleurs ;
    un dépôt de surcharges vide rend les gabarits tels que le code les écrit.
    """
    surcharges = surcharges if surcharges is not None else SurchargeStore.default()
    posees = {surcharge.nom: surcharge for surcharge in surcharges.lister()}
    return tuple(
        _surcharge_appliquee(agent, posees.get(agent.nom), modele)
        for agent in gabarits_pour(modele)
    )


def catalogue_du_projet(
    store: AgentStore | None,
    projet_id: str | None,
    modele: str | None = None,
) -> tuple[Agent, ...] | None:
    """L'équipe d'un projet — `None` quand il faut s'en tenir au catalogue du câblage.

    La **règle unique** de « quels agents pour ce travail-là », écrite ici parce
    qu'elle a deux lecteurs depuis #1041 et qu'ils doivent lire la même chose :
    l'exécuteur, qui route la tâche (`LocalExecutor._equipe`), et la boucle, qui
    fait découper l'objectif (`OrchestrationEngine._plan`). Un plan proposé sur
    une équipe et exécuté sur une autre enverrait toutes ses tâches en repli
    « à assigner » sans que rien ne le dise.

    `None` dans trois cas, tous à ramener au catalogue du câblage par l'appelant :
    tâche (ou run) **sans projet**, dépôt **non câblé** — tests et câblages sans
    Control Tower —, et dépôt **illisible** : un incident de stockage ne doit pas
    faire partir toutes les tâches en repli.

    ⚠ Un **tuple vide** n'est aucun des trois (#1042) : c'est un projet qui n'a
    encore recruté personne, et non une absence d'information. C'est la différence
    entre « je ne sais pas » et « il n'y a personne ». Le dépôt de **surcharges**
    a disparu d'ici pour la même raison : une surcharge règle un **gabarit**
    (`gabarits_du_code`), et un catalogue de projet n'en porte aucun.
    """
    if projet_id is None or store is None:
        return None
    try:
        return catalogue(store.pour_projet(projet_id), modele)
    except (OSError, ValueError):  # dépôt illisible : on garde le catalogue câblé
        return None


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


def definition_validee(definition: AgentDefinition) -> AgentDefinition:
    """La définition normalisée (compétences épurées), ou `ValueError` si invalide.

    **Publique** depuis #1040, au même titre et pour la même raison que
    `politique_validee` dans `maestro.agents.permissions` : la validation d'une
    équipe entière se fait **avant** d'écrire le premier fichier
    (`maestro.equipe.creation.refus_de`), et une seconde définition de « fiche
    valide » finirait par refuser ce que le dépôt accepte, ou l'inverse. Une
    seule règle, appelée des deux côtés.

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
            f"nom d'agent réservé : {definition.nom!r} (gabarit de rôle ou acteur système)."
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

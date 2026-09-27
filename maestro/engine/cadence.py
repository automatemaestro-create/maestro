"""Pourquoi les tâches d'un run passent une à une — lu dans le moteur, jamais deviné (#1298).

Le retour d'expérience du 2026-09-24 (projet `p3`) : *« je n'ai jamais remarqué un
parallélisme dans le traitement des tâches jusqu'ici »*. Le moteur sait pourtant
paralléliser — la boucle lance tout ce qui n'attend personne (`maestro.engine.loop`)
—, mais trois choses l'en empêchaient, et **aucun écran ne le disait** : « jusqu'à N
de front » n'apparaissait que sur un plan large, et rien n'expliquait pourquoi un
plan large avançait quand même une tâche après l'autre.

Ce module porte le **fait** — `Cadence` — et les trois façons de le constater, chacune
là où le moteur le sait, jamais par une prédiction :

- **la chaîne** (`CAUSE_CHAINE`) — le plan est une chaîne : chaque tâche attend la
  précédente, rien ne peut partir de front. Constaté sur le plan, figé au départ
  (`cadence_du_plan`) ; il n'y a rien à proposer, seulement à le dire ;
- **le projet non versionné** (`CAUSE_PROJET_NON_VERSIONNE`) — l'atelier de #839 : sans
  versions, deux agents écriraient dans le même dossier, et l'exécuteur n'y fait
  travailler qu'une tâche à la fois. Constaté au départ, sur le plan (qui en
  permettrait plusieurs) et sur ce que l'exécuteur **applique** à ce projet
  (`TaskExecutor.atelier_du_projet`) — le garde-fou ne bouge pas. Ce qui le lève se
  propose : **versionner le projet**, dans le fil, sur accord (`question_du_versionnement`) ;
- **les instances d'un agent** (`CAUSE_INSTANCES`) — une tâche prête attend qu'un
  créneau de son agent se libère (#86, `INSTANCES_DEFAUT = 1`). Constaté **au moment
  où elle attend**, dans la jauge (`JaugeInstances.creneau`) : le routage se décide
  tâche par tâche, et prédire qu'un agent recevra deux tâches du même niveau serait
  deviner. Le remède n'est pas proposé ici : le plafond d'instances dérivé du plan
  est un chantier voisin, et les instances se règlent déjà sur la fiche de l'agent.

## Où le fait voyage

Une étape de **run** (`ETAPE_CADENCE`), comme la confrontation de l'équipe au plan
(#1227) : elle ne porte sur aucune tâche, à usage nul, et son champ structuré
`cadence` (`Cadence.to_dict`) est ce que le pont publie en `run.cadence`. La
projection le range sur le run, la vue du pipeline en montre la **mention** à côté de
« N tâches · N niveaux », et les faits du run que lit le fil en portent la **phrase**.
Les mots sont composés ici, une fois, là où le fait est su.

## Une cause levée se dit aussi

Le versionnement accordé pendant le run **lève** la cause du projet non versionné :
une seconde ligne, `liberee`, qui la retire de la vue (« projet versionné et plan
large : aucune mention »). Un refus, un silence, une mise sous Git qui échoue ou un
run qui finit avant la réponse la laissent en place, et chaque issue est consignée.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from maestro.orchestrator.schema import Task
from maestro.telemetry import RunJournal, StepUsage
from maestro.telemetry.costs import ETAPE_CADENCE

#: Le plan est une chaîne : chaque tâche attend celle qui la précède.
CAUSE_CHAINE = "chaine"
#: Le projet n'est pas versionné : l'atelier de #839 n'y laisse travailler qu'une tâche.
CAUSE_PROJET_NON_VERSIONNE = "projet_non_versionne"
#: Un agent est au complet : ses autres tâches attendent qu'une instance se libère.
CAUSE_INSTANCES = "instances"
CAUSES = (CAUSE_CHAINE, CAUSE_PROJET_NON_VERSIONNE, CAUSE_INSTANCES)

#: Les statuts de l'étape `cadence`. Le premier dit la cause constatée ; les autres
#: disent ce qu'est devenue la proposition qui la lèverait. Même partage que
#: l'équipe confrontée au plan (#1227) : le constat est un **fait**, l'issue une
#: **décision**, et fondre les deux effacerait le constat dès qu'on décline.
STATUT_UNE_A_UNE = "une_a_une"
STATUT_VERSIONNEMENT_PROPOSE = "versionnement_propose"
STATUT_PROJET_VERSIONNE = "projet_versionne"
STATUT_VERSIONNEMENT_DECLINE = "versionnement_decline"
STATUT_VERSIONNEMENT_SANS_REPONSE = "versionnement_sans_reponse"
STATUT_VERSIONNEMENT_ECHOUE = "versionnement_echoue"
STATUT_VERSIONNEMENT_SANS_SUITE = "versionnement_sans_suite"

#: Où en est la proposition de versionner — portée par la cause elle-même, pour que
#: les faits du run disent au fil si la carte attend encore quelqu'un.
PROPOSITION_EN_ATTENTE = "en_attente"
PROPOSITION_ACCEPTEE = "acceptee"
PROPOSITION_DECLINEE = "declinee"
PROPOSITION_SANS_REPONSE = "sans_reponse"
PROPOSITION_ECHOUEE = "echouee"
PROPOSITION_SANS_SUITE = "sans_suite"

#: Le geste qui versionne, sur la carte de la question — et **le seul** qui écrive dans
#: le projet. Une phrase tapée n'est pas un accord d'écriture : elle laisse tout en
#: l'état, et la cause le dit. C'est le garde-fou de l'arbitrage des actes, pas une
#: bride : le geste est à un clic, sur la même carte.
CHOIX_VERSIONNER = "Versionner le projet"
#: Le geste qui décline, pour qu'un refus ne demande pas d'écrire une phrase.
CHOIX_GARDER = "Garder tel quel"

#: Le verbe sous lequel la proposition entre dans l'espace des clés d'actes
#: (`maestro.deliberation.cle_acte`) : à elle, pour que sa réponse ne soit jamais
#: celle d'une question d'agent ni d'une proposition de prérequis (#1181).
VERBE_VERSIONNEMENT = "proposer_le_versionnement"

#: Ce qui se passe sans réponse — montré sur la carte avec la question, puisque le run
#: ne l'attend pas.
HYPOTHESE_VERSIONNEMENT = "le run continue une tâche à la fois, et le projet reste tel quel"


@dataclass(frozen=True)
class Cadence:
    """Pourquoi des tâches passent une à une, et ce qu'on en a fait (#1298).

    `cause` est l'une des `CAUSES`. Les autres champs ne servent que la cause qui les
    nomme : `largeur` (ce que le plan permettrait de front) et `projet` (son nom) pour
    le projet non versionné, `agent` et `instances` pour un agent au complet.
    `liberee` dit que la cause ne tient plus — le projet a été versionné pendant le
    run —, `proposition` où en est ce qui la lèverait (`PROPOSITION_*`), `detail` le
    motif d'une mise sous Git refusée.
    """

    cause: str
    largeur: int = 0
    projet: str = ""
    agent: str = ""
    instances: int = 0
    liberee: bool = False
    proposition: str = ""
    detail: str = ""

    @property
    def cle(self) -> str:
        """Ce qui identifie la cause dans un run : une par nature, une par agent au complet."""
        return f"{self.cause}:{self.agent}"

    def mention(self) -> str:
        """La cause en quelques mots, pour la vue d'un run — vide quand elle ne tient plus."""
        if self.liberee:
            return ""
        if self.cause == CAUSE_CHAINE:
            return "une tâche à la fois : chaque tâche attend la précédente"
        if self.cause == CAUSE_PROJET_NON_VERSIONNE:
            return "une tâche à la fois : projet non versionné"
        if self.cause == CAUSE_INSTANCES:
            if self.instances <= 1:
                return f"une tâche à la fois pour « {self.agent} » : une seule instance"
            return (
                f"{self.instances} tâches à la fois au plus pour « {self.agent} » : "
                f"{self.instances} instances"
            )
        return ""

    def phrase(self) -> str:
        """La cause en toutes lettres — la ligne du journal, et le fait que lit le fil."""
        if self.cause == CAUSE_CHAINE:
            return (
                "Les tâches de ce run passent une à une : le plan est une chaîne, chacune "
                "attend celle qui la précède — rien ne peut partir de front."
            )
        if self.cause == CAUSE_PROJET_NON_VERSIONNE:
            if self.liberee:
                return (
                    f"Le projet « {self.projet} » a été versionné pendant le run, sur "
                    "accord : les tâches suivantes travaillent chacune dans sa copie, et "
                    "partent de front."
                )
            return (
                f"Les tâches de ce run passent une à une : le projet « {self.projet} » "
                "n'est pas versionné, et sans versions deux agents écriraient dans le "
                "même dossier — Maestro n'y fait donc travailler qu'une tâche à la fois. "
                f"Le plan en permettrait {self.largeur} de front : versionné (un dépôt "
                "Git local, sans forge), chaque tâche travaillerait dans sa copie."
                + self._suite_de_la_proposition()
            )
        if self.cause == CAUSE_INSTANCES:
            if self.instances <= 1:
                return (
                    f"Les tâches confiées à « {self.agent} » passent une à une : il n'a "
                    "qu'une instance, et ses autres tâches attendent qu'elle se libère. "
                    "Ses instances se règlent sur sa fiche."
                )
            return (
                f"Au plus {self.instances} tâches de « {self.agent} » avancent à la fois : "
                "c'est son nombre d'instances, et ses autres tâches attendent qu'une se "
                "libère. Ses instances se règlent sur sa fiche."
            )
        return ""

    def _suite_de_la_proposition(self) -> str:
        """Ce qu'est devenue la proposition de versionner — rien tant qu'il n'y en a pas eu."""
        motif = f" ({self.detail.strip().rstrip('.')})" if self.detail.strip() else ""
        suites = {
            PROPOSITION_EN_ATTENTE: " Le versionner est proposé dans le fil ; le run "
            "n'attend pas la réponse.",
            PROPOSITION_DECLINEE: " Le versionner a été proposé dans le fil, et décliné : "
            "le projet reste tel quel.",
            PROPOSITION_SANS_REPONSE: " Le versionner a été proposé dans le fil, resté "
            "sans réponse : le projet reste tel quel.",
            PROPOSITION_ECHOUEE: " Le versionner a été accepté, mais la mise sous Git a "
            f"échoué{motif} : le projet reste tel quel.",
            PROPOSITION_SANS_SUITE: " Le versionner a été proposé dans le fil ; le run "
            "s'est achevé avant la réponse, et le projet reste tel quel.",
        }
        return suites.get(self.proposition, "")

    def to_dict(self) -> dict[str, Any]:
        """La cause en JSON — le champ `cadence` de l'étape, donc de `run.cadence`.

        `cle`, `mention` et `phrase` sont **servies** plutôt que laissées à composer :
        la vue et le fil lisent les mêmes mots, écrits une fois, ici.
        """
        return {
            "cle": self.cle,
            "cause": self.cause,
            "largeur": self.largeur,
            "projet": self.projet,
            "agent": self.agent,
            "instances": self.instances,
            "liberee": self.liberee,
            "proposition": self.proposition,
            "detail": self.detail,
            "mention": self.mention(),
            "phrase": self.phrase(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Cadence:
        """Relit une cause depuis sa forme `to_dict` — tolérante, comme toute relecture du bus."""
        return cls(
            cause=str(data.get("cause") or ""),
            largeur=_entier(data.get("largeur")),
            projet=str(data.get("projet") or ""),
            agent=str(data.get("agent") or ""),
            instances=_entier(data.get("instances")),
            liberee=bool(data.get("liberee")),
            proposition=str(data.get("proposition") or ""),
            detail=str(data.get("detail") or ""),
        )


def _entier(valeur: Any) -> int:
    """Un entier relu du flux — 0 pour tout ce qui n'en est pas un (booléens compris)."""
    if isinstance(valeur, bool) or not isinstance(valeur, int) or valeur < 0:
        return 0
    return valeur


def largeur_du_plan(tasks: Sequence[Task]) -> int:
    """Combien de tâches le plan laisse partir de front, au plus — son niveau le plus peuplé.

    Le niveau d'une tâche est **le plus long chemin qui y mène**, la règle du graphe
    servi (`maestro.controltower.graphe`) : deux tâches sans dépendance entre elles
    tombent au même niveau. Une dépendance qui ne désigne aucune tâche du plan est
    ignorée ; un cycle — refusé avant tout run par `validate_plan` — range ce qui
    reste sur un dernier niveau plutôt que de boucler.
    """
    connus = {tache.id for tache in tasks}
    amont = {tache.id: [d for d in tache.dependances if d in connus] for tache in tasks}
    niveau: dict[str, int] = {}
    restants = list(tasks)
    while restants:
        differes = [t for t in restants if not all(d in niveau for d in amont[t.id])]
        ranges = [t for t in restants if t not in differes]
        if not ranges:
            dernier = 1 + max(niveau.values(), default=-1)
            niveau.update({tache.id: dernier for tache in differes})
            break
        for tache in ranges:
            niveau[tache.id] = 1 + max((niveau[d] for d in amont[tache.id]), default=-1)
        restants = differes
    comptes: dict[int, int] = {}
    for rang in niveau.values():
        comptes[rang] = comptes.get(rang, 0) + 1
    return max(comptes.values(), default=0)


def cadence_du_plan(tasks: Sequence[Task], *, atelier: str | None) -> Cadence | None:
    """La cause que le plan et le régime du projet imposent au départ — None s'il n'y en a pas.

    `atelier` est le nom du projet dont l'exécuteur fait passer les tâches une à une
    (`TaskExecutor.atelier_du_projet`), None s'il n'en applique aucun — projet
    versionné, pas de projet, ou exécuteur qui ne le sait pas.

    Dans cet ordre, et l'ordre est la décision : un plan d'une seule tâche n'a rien
    à dire ; un plan **en chaîne** passe une à une quoi qu'on fasse, et versionner
    n'y changerait rien — on le dit sans rien proposer ; un plan **large** sur un
    projet non versionné passe une à une à cause du garde-fou, et c'est là seulement
    que la proposition a un objet. Un plan large sur un projet versionné n'a rien à
    dire : ses tâches partent de front (les instances se constatent en route).
    """
    if len(tasks) < 2:
        return None
    largeur = largeur_du_plan(tasks)
    if largeur <= 1:
        return Cadence(cause=CAUSE_CHAINE, largeur=largeur)
    if atelier is not None:
        return Cadence(cause=CAUSE_PROJET_NON_VERSIONNE, largeur=largeur, projet=atelier)
    return None


def question_du_versionnement(cadence: Cadence) -> str:
    """Ce que la carte du fil demande : pourquoi une à une, ce que versionner change (#1298).

    Les **faits** seulement, écrits ici, comme la question d'un prérequis (#1181) : la
    cause est constatée par le moteur, et ce que versionner écrit dans le dossier est
    dit avant qu'on accepte — un dépôt local, un premier commit de l'existant, rien
    d'envoyé nulle part. La phrase de fin dit que le run n'attend pas : c'est la
    réponse à « et si je ne réponds pas tout de suite ? ».
    """
    return "\n".join(
        [
            f"Ce run pourrait faire avancer jusqu'à {cadence.largeur} tâches de front, mais "
            f"le projet « {cadence.projet} » n'est pas versionné : sans versions, deux "
            "agents écriraient dans le même dossier, alors Maestro n'y fait travailler "
            "qu'une tâche à la fois.",
            "",
            "Le versionner en fait un dépôt Git local — rien n'est envoyé nulle part, "
            "aucune forge n'est créée —, avec un premier commit de ce que le dossier "
            "contient déjà. Chaque tâche travaille ensuite dans sa propre copie, et son "
            "travail rejoint le projet quand elle se termine : les tâches suivantes de ce "
            "run partent de front.",
            "",
            "Le run ne vous attend pas : il continue une tâche à la fois tant que vous "
            "n'avez pas répondu.",
        ]
    )


def consigne_cadence(
    journal: RunJournal,
    cadence: Cadence,
    statut: str,
    *,
    agent: str,
    role: str,
    projet_id: str | None,
    sortie: str = "",
) -> None:
    """Écrit la cause au journal — étape de run `cadence`, usage nul (#1298).

    `sortie` est la ligne que le fil d'activité prononce : la phrase de la cause par
    défaut, ou ce qu'une issue de la proposition a donné. Le champ structuré porte la
    cause telle qu'elle est **après** cette ligne — c'est lui que la projection range.
    """
    journal.consigne(
        etape=ETAPE_CADENCE,
        nom=_nom_de_l_etape(cadence, statut),
        agent=agent,
        role=role,
        statut=statut,
        entree="",
        sortie=sortie or cadence.phrase(),
        usage=StepUsage(),
        projet_id=projet_id,
        cadence=cadence.to_dict(),
    )


def _nom_de_l_etape(cadence: Cadence, statut: str) -> str:
    """Le titre de la ligne : la cadence du run, ou le versionnement qui la lève."""
    if statut == STATUT_UNE_A_UNE:
        return "Cadence du run"
    return f"Versionnement du projet « {cadence.projet} »"


def avec_proposition(cadence: Cadence, proposition: str, detail: str = "") -> Cadence:
    """La même cause, la proposition avancée d'un cran — levée si elle est acceptée."""
    return replace(
        cadence,
        proposition=proposition,
        detail=detail,
        liberee=proposition == PROPOSITION_ACCEPTEE,
    )

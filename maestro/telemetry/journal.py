"""Journal d'exécution structuré — une ligne JSON par étape (ticket #8).

Chaque exécution reçoit un `run_id` ; chaque étape (planification, tâche) est
consignée en `StepRecord` : entrée, sortie, outils, tokens, coût, durée, issue.
Les enregistrements sont émis en **JSON Lines** sur le logger `maestro.trace`
(silencieux tant qu'aucun handler n'est configuré — cf. `maestro-run --trace`).

Le format plat `run_id` / `etape` / `horodatage` / `usage` est pensé pour se
mapper sur les traces et observations de **Langfuse** sans changer les
appelants : c'est ce que fait l'exporteur `maestro.telemetry.langfuse` (#81),
posé en handler sur ce même logger — purement configuratif, no-op sans clés.

Aucun secret ne doit atteindre les logs : entrée, sortie et erreur passent par
`redact_secrets` avant d'être conservées ou émises.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from maestro.detail_tache import (
    EtapeTache,
    LienUtile,
    etapes_en_liste,
    liens_en_liste,
)
from maestro.plan_run import NoeudPlan, noeuds_en_liste
from maestro.references import ReferenceTicket, ticket_en_dict
from maestro.telemetry.redact import redact_secrets
from maestro.telemetry.usage import StepUsage

#: Logger d'émission du journal (une ligne JSON par étape).
LOGGER_NAME = "maestro.trace"

#: Suffixe des **relevés d'usage** d'une tâche en cours (#835) : `<tache>:usage`,
#: une ligne à chaque fois que le moteur relève ce que la tâche a consommé
#: jusqu'ici — tokens, appels, tours, outils, et le coût quand le fournisseur
#: l'a déjà tarifé. Il vit **ici**, avec le format de ligne, et non dans le
#: moteur : quatre lecteurs de ce format doivent le reconnaître (le pont Control
#: Tower, l'exporteur Langfuse, le moteur qui l'émet, et ce journal qui le tient
#: hors de ses étapes), et un suffixe recopié quatre fois serait quatre chaînes à
#: tenir d'accord pour une ligne qui, mal reconnue, **compte double**.
#:
#: ⚠ Un relevé n'est pas une étape. Il porte un **cumul** (« jusqu'ici ») là où
#: une étape porte une **part** (« ce que cette étape a coûté ») — les additionner
#: compterait chaque tour autant de fois qu'il y a eu de relevés après lui. C'est
#: pourquoi `RunJournal.releve` l'émet sans le conserver : `records`,
#: `usage_totale`, le grand livre (`RunCost.depuis_journal`) et le plafond de
#: dépense qui le relit ne le voient jamais, par construction et non par filtre.
#: Les deux lecteurs du **logger** qui ne veulent pas de lui le reconnaissent à
#: ce suffixe (`est_releve_usage`).
SUFFIXE_ETAPE_USAGE = ":usage"


def est_releve_usage(etape: str) -> bool:
    """Cette étape est-elle un relevé d'usage en cours (#835), et non une étape du run ?"""
    return etape.endswith(SUFFIXE_ETAPE_USAGE)


@dataclass(frozen=True)
class StepRecord:
    """Trace d'une étape : qui a fait quoi, avec quelle entrée, quelle issue, quel coût.

    `etape` est l'identifiant de l'étape dans l'exécution (id de tâche, ou
    « planification ») ; `entree`/`sortie`/`erreur` sont déjà expurgées des secrets.
    `playbook_version` (#78) est la version du playbook stocké avec laquelle
    l'agent a exécuté la tâche — None hors tâche d'agent, ou si l'agent a exécuté
    avec son prompt du code (playbook jamais édité).
    `ticket` (#187) est le ticket dont relève la tâche, quand il y en
    a un : le journal est le **seul chemin** par lequel il atteint la Control
    Tower (le pont #46 ne lit que ces lignes), d'où sa présence ici plutôt que
    dans le seul plan.
    `projet_id` (#222) est le projet auquel la tâche appartient, quand il y en a
    un — même raison d'être ici que `ticket` : le pont ne lit que ces lignes, et
    sans elles l'appartenance n'atteindrait jamais les vues.
    `description`, `etapes` et `liens` (#246) portent le **détail** de la tâche —
    ce qu'elle demande, où elle en est, ce qu'il faut ouvrir pour la traiter.
    Même raison d'être ici encore : sans ces lignes, le panneau de détail (#251)
    resterait vide. Absents (chaîne vide, listes vides) tant que rien ne les
    renseigne, et rendus `null` par `to_dict` pour que le pont sache distinguer
    « l'étape n'en dit rien » de « plus aucune étape ».
    `plan` (#490) est le **graphe du run** — un nœud par tâche, ses dépendances,
    son ossature de checklist —, porté par la seule étape `planification` et par
    elle seule : c'est l'instant où le plan existe et où il est figé. Même raison
    d'être ici que les précédents, et la plus nette de toutes — les arêtes ne
    quittaient le moteur par aucun autre chemin.
    """

    run_id: str
    etape: str
    nom: str
    agent: str
    role: str
    statut: str
    horodatage: str
    entree: str
    sortie: str
    erreur: str | None
    usage: StepUsage
    playbook_version: int | None = None
    ticket: ReferenceTicket | None = None
    projet_id: str | None = None
    description: str = ""
    etapes: list[EtapeTache] = field(default_factory=list)
    liens: list[LienUtile] = field(default_factory=list)
    plan: list[NoeudPlan] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Réémet la trace en dict JSON-sérialisable (la ligne du journal)."""
        return {
            "run_id": self.run_id,
            "etape": self.etape,
            "nom": self.nom,
            "agent": self.agent,
            "role": self.role,
            "statut": self.statut,
            "horodatage": self.horodatage,
            "entree": self.entree,
            "sortie": self.sortie,
            "erreur": self.erreur,
            "usage": self.usage.to_dict(),
            "playbook_version": self.playbook_version,
            "ticket": ticket_en_dict(self.ticket),
            "projet_id": self.projet_id,
            # `null` plutôt que `""`/`[]` quand rien n'est renseigné (#246) : le
            # pont ne doit pas lire une liste vide là où l'étape ne dit rien, ou
            # chaque ligne de journal effacerait le détail posé par la
            # précédente.
            "description": self.description or None,
            "etapes": etapes_en_liste(self.etapes) or None,
            "liens": liens_en_liste(self.liens) or None,
            # Même règle (#490) : `null` quand l'étape ne porte pas de plan,
            # c'est-à-dire partout sauf sur la planification. Le pont n'en tire
            # un `run.plan` que sur une liste non vide — un plan vide n'est pas
            # un plan, et le publier ferait annoncer un graphe sans nœud.
            "plan": noeuds_en_liste(self.plan) or None,
        }


class RunJournal:
    """Journal d'une exécution : consigne chaque étape et émet sa ligne JSON.

    Conserve les enregistrements en mémoire (inspection, agrégation) en plus de
    les émettre sur le logger — c'est l'agrégat mémoire qui alimente le coût
    total, le logger n'étant qu'un canal de sortie.
    """

    def __init__(self, *, run_id: str | None = None, logger: logging.Logger | None = None) -> None:
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self._logger = logger or logging.getLogger(LOGGER_NAME)
        self._records: list[StepRecord] = []

    @property
    def records(self) -> tuple[StepRecord, ...]:
        """Les étapes consignées, dans l'ordre de consignation.

        C'est l'ordre d'**achèvement** : des tâches exécutées en parallèle (#7) y
        apparaissent dans l'ordre où elles finissent, chacune reliée à sa tâche par
        `etape`. Le rapport (`RunReport`), lui, garde l'ordre du plan.
        """
        return tuple(self._records)

    @property
    def usage_totale(self) -> StepUsage:
        """Agrégat de l'usage de toutes les étapes consignées."""
        total = StepUsage()
        for record in self._records:
            total = total.fusion(record.usage)
        return total

    def consigne(
        self,
        *,
        etape: str,
        nom: str,
        agent: str,
        role: str,
        statut: str,
        entree: str,
        sortie: str,
        usage: StepUsage,
        erreur: str | None = None,
        playbook_version: int | None = None,
        ticket: ReferenceTicket | None = None,
        projet_id: str | None = None,
        description: str = "",
        etapes: Sequence[EtapeTache] = (),
        liens: Sequence[LienUtile] = (),
        plan: Sequence[NoeudPlan] = (),
    ) -> StepRecord:
        """Consigne une étape (textes expurgés des secrets) et émet sa ligne JSON."""
        record = StepRecord(
            run_id=self.run_id,
            etape=etape,
            nom=nom,
            agent=agent,
            role=role,
            statut=statut,
            horodatage=datetime.now(UTC).isoformat(timespec="seconds"),
            entree=redact_secrets(entree),
            sortie=redact_secrets(sortie),
            erreur=redact_secrets(erreur) if erreur is not None else None,
            usage=usage,
            playbook_version=playbook_version,
            ticket=ticket,
            projet_id=projet_id,
            # La description passe par `redact_secrets` comme entrée et sortie :
            # c'est un texte de tâche, il a pu être composé avec un secret.
            # Étapes et liens sont des libellés et des URL déjà normalisés.
            description=redact_secrets(description),
            etapes=list(etapes),
            liens=list(liens),
            # Le plan (#490) n'est pas expurgé, au même titre que `nom` : il
            # porte des identifiants et des **titres** de tâches, exactement ce
            # que chaque ligne de tâche consigne déjà en clair. L'expurgation
            # vise ce qui a été dit au modèle et ce qu'il a rendu
            # (`entree`/`sortie`/`erreur`), et les étapes de checklist suivent
            # `etapes` (#246) : des libellés, déjà bornés.
            plan=list(plan),
        )
        self._records.append(record)
        self._logger.info(json.dumps(record.to_dict(), ensure_ascii=False))
        return record

    def releve(
        self,
        *,
        tache_id: str,
        nom: str,
        agent: str,
        role: str,
        statut: str,
        usage: StepUsage,
        sortie: str = "",
        projet_id: str | None = None,
    ) -> StepRecord:
        """Émet un **relevé d'usage** de la tâche `tache_id` en cours (#835) — sans le conserver.

        Même ligne JSON qu'une étape (le pont Control Tower la lit avec le même
        lecteur), sous l'étape `<tache_id>:usage`, et `usage` y est un **cumul** :
        ce que la tâche a consommé jusqu'ici, toutes tentatives confondues.

        ⚠ Il n'entre pas dans `records`, et ce n'est pas un oubli : un relevé
        n'est pas une étape du run, c'est la lecture d'une jauge que l'étape
        finale de la tâche remplacera. Le garder ferait compter chaque tour
        autant de fois qu'il aura été relevé — dans `usage_totale`, dans le grand
        livre, donc dans le **plafond de dépense** qui le relit à chaque mesure
        (`PlafondDepense`, #56), et dans la reprise d'un run (#96). Tenu dehors,
        aucun de ces lecteurs n'a de règle à connaître ; seuls ceux du logger en
        ont une (`est_releve_usage`).

        Le texte de `sortie` est expurgé comme les autres — il ne porte que des
        chiffres, mais la règle du journal ne fait pas d'exception.
        """
        record = StepRecord(
            run_id=self.run_id,
            etape=f"{tache_id}{SUFFIXE_ETAPE_USAGE}",
            nom=nom,
            agent=agent,
            role=role,
            statut=statut,
            horodatage=datetime.now(UTC).isoformat(timespec="seconds"),
            entree="",
            sortie=redact_secrets(sortie),
            erreur=None,
            usage=usage,
            projet_id=projet_id,
        )
        self._logger.info(json.dumps(record.to_dict(), ensure_ascii=False))
        return record

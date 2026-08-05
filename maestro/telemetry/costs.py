"""Comptabilité de coût par tâche — le grand livre d'une exécution (ticket #55).

Le journal (#8) consigne déjà chaque étape avec son usage (tokens, coût, durée) ;
cette brique le **réorganise en comptabilité** : une entrée par tâche
(`TaskCost`), l'étape de planification à part, et l'agrégat de l'exécution
(`RunCost`) — la vue « coût de l'exécution, traçable par tâche » du critère MVP
n°6 (parent #49), que l'API Control Tower exposera (#57) et sur laquelle le
plafond de dépense s'adosse (`PlafondDepense`, #56) : le garde-fou (#9) relit ce
grand livre à chaque mesure d'usage, sans compteur parallèle.

L'attribution suit la convention d'étape du journal : `planification` pour
l'orchestrateur, `<tache>` pour l'étape de la tâche elle-même, et
`<tache>:<annexe>` pour ses étapes annexes (validation humaine #48, message
inter-agents #44) — chaque annexe est rattachée à sa tâche. Chaque ligne du
journal est ainsi comptée exactement une fois : le total de la comptabilité
retombe sur `RunJournal.usage_totale`.

La comptabilité n'évalue **aucun prix** : le coût estimé est celui rapporté par
le fournisseur via la couche `ModelProvider` (#32) — la tarification par modèle
reste de son côté, jamais en dur ici. Un fournisseur qui ne rapporte pas de coût
laisse la tâche à coût « inconnu » (None, à distinguer d'un coût nul).

Ce coût inconnu rendrait le plafond de dépense inopérant sur un tel fournisseur
(#113) : `PlafondDepense` accepte donc un **plafond en tokens** en complément (ou
à la place) du plafond de coût — les tokens, eux, sont toujours rapportés. Le
plafond en USD garde la main dès que le coût est connu ; le plafond en tokens
prend le relais sinon, et `resume_controle_depense` dit à l'opérateur lequel des
deux tient réellement (au lieu d'un plafond silencieusement sans prise).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from maestro.references import ReferenceTicket, ticket_en_dict
from maestro.telemetry.journal import RunJournal
from maestro.telemetry.usage import StepUsage

#: Étape du journal qui n'appartient à aucune tâche : la planification (#8).
ETAPE_PLANIFICATION = "planification"

#: Étape du journal qui n'appartient à aucune tâche : la reprise d'un run
#: interrompu (#96) — un marqueur de run, pas un travail d'agent. Usage nul par
#: construction (aucun modèle n'est sollicité pour reprendre) : l'exclure du
#: grand livre lui évite une entrée de tâche fantôme, sans rien changer au total.
ETAPE_REPRISE = "reprise"


@dataclass(frozen=True)
class TaskCost:
    """L'entrée « tâche » du grand livre : qui a fait quoi, pour quel usage.

    `usage` fusionne l'étape de la tâche et ses étapes annexes (validation,
    message) — celles-ci rapportent un usage nul aujourd'hui, la fusion les
    garderait comptées si elles en portaient un demain. `nom`, `agent`, `role`
    et `statut` viennent de l'étape de la tâche elle-même ; ils restent vides
    tant que seule une annexe a été consignée (tâche encore en cours).

    `ticket` (#187) est le ticket dont relève la tâche : à la
    différence de l'identité, elle est prise sur **n'importe quelle** étape qui
    en porte une — l'annexe `<tache>:reference` en est justement le seul porteur
    quand un agent la pose en cours d'exécution.

    `projet_id` (#222) est le projet auquel la tâche appartient, pris au même
    régime que `ticket` : sur **n'importe quelle** étape qui en porte un — c'est
    ce qui permet de filtrer la comptabilité par projet.
    """

    tache_id: str
    nom: str = ""
    agent: str = ""
    role: str = ""
    statut: str = ""
    usage: StepUsage = StepUsage()
    ticket: ReferenceTicket | None = None
    projet_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Réémet l'entrée en dict JSON-sérialisable (la forme de l'API, #57)."""
        return {
            "tache_id": self.tache_id,
            "nom": self.nom,
            "agent": self.agent,
            "role": self.role,
            "statut": self.statut,
            "usage": self.usage.to_dict(),
            "ticket": ticket_en_dict(self.ticket),
            "projet_id": self.projet_id,
        }


@dataclass(frozen=True)
class RunCost:
    """Le grand livre d'une exécution : le coût par tâche et l'agrégat du run.

    `taches` suit l'ordre de première apparition au journal — l'ordre
    d'achèvement des tâches (#7), chaque entrée restant reliée au plan par
    `tache_id` ; `planification` porte l'usage de l'orchestrateur.
    """

    run_id: str
    planification: StepUsage = StepUsage()
    taches: tuple[TaskCost, ...] = ()

    @property
    def total(self) -> StepUsage:
        """Usage agrégé de l'exécution — retombe sur `RunJournal.usage_totale`."""
        total = self.planification
        for tache in self.taches:
            total = total.fusion(tache.usage)
        return total

    @classmethod
    def depuis_journal(cls, journal: RunJournal) -> RunCost:
        """Comptabilise `journal` : chaque ligne attribuée à sa tâche, comptée une fois.

        S'appuie sur `RunJournal.records` (l'agrégat mémoire) : utilisable en
        fin d'exécution comme en cours de run (comptabilité partielle — une
        tâche dont seule une annexe est consignée apparaît sans identité ni
        statut, son usage déjà compté).
        """
        planification = StepUsage()
        entrees: dict[str, TaskCost] = {}
        for record in journal.records:
            if record.etape == ETAPE_PLANIFICATION:
                planification = planification.fusion(record.usage)
                continue
            if record.etape == ETAPE_REPRISE:
                # Marqueur de run (#96), rattaché à aucune tâche : rien à
                # comptabiliser — les étapes qu'il annonce sont, elles, réintégrées
                # au journal (`RunJournal.reconstitue`) et comptées à leur place.
                continue
            tache_id = record.etape.split(":", 1)[0]
            entree = entrees.get(tache_id)
            if entree is None:
                entree = TaskCost(tache_id=tache_id)
            if record.ticket is not None:
                # Le ticket externe (#187) vient de n'importe quelle étape de la
                # tâche — l'annexe `:reference` n'en porte même que ça.
                entree = replace(entree, ticket=record.ticket)
            if record.projet_id is not None:
                # Le projet (#222) suit la même règle : n'importe quelle étape
                # de la tâche fait foi, une étape sans projet n'efface rien.
                entree = replace(entree, projet_id=record.projet_id)
            if record.etape == tache_id:
                # L'étape de la tâche elle-même : elle fait foi pour l'identité.
                entree = replace(
                    entree,
                    nom=record.nom,
                    agent=record.agent,
                    role=record.role,
                    statut=record.statut,
                    usage=entree.usage.fusion(record.usage),
                )
            else:
                entree = replace(entree, usage=entree.usage.fusion(record.usage))
            entrees[tache_id] = entree
        return cls(
            run_id=journal.run_id,
            planification=planification,
            taches=tuple(entrees.values()),
        )

    def to_dict(self) -> dict[str, Any]:
        """Réémet le grand livre en dict JSON-sérialisable (la forme de l'API, #57)."""
        return {
            "run_id": self.run_id,
            "planification": self.planification.to_dict(),
            "total": self.total.to_dict(),
            "taches": [tache.to_dict() for tache in self.taches],
        }


class PlafondDepenseDepasse(RuntimeError):
    """Levée quand la dépense d'une exécution dépasse son plafond (#9).

    Émise depuis `report_usage` (donc depuis le fournisseur, entre deux appels
    modèle) quand `PlafondDepense.verifie` constate le dépassement : elle
    interrompt l'étape en cours, que l'appelant consigne comme stoppée par le
    garde-fou.
    """


class PlafondDepense:
    """Garde-fou de dépense d'une exécution (#9), adossé au grand livre (#56).

    C'est le contrôle que le collecteur d'usage consulte à chaque mesure
    (`collect_usage(plafond=...)`). Il ne tient **aucun compteur** : à chaque
    vérification, la dépense déjà engagée est relue dans la comptabilité de
    l'exécution (`RunCost.depuis_journal`) — la télémétrie est la source unique
    du coût — et complétée de l'usage de l'étape en cours, pas encore consignée
    au journal. Les étapes parallèles encore en vol ne comptent qu'à leur
    consignation : léger sous-comptage transitoire assumé (POC), jamais de
    double comptage.

    Deux seuils, dont au moins un doit être posé : `plafond_cout_usd` (budget en
    USD, sans prise sur un fournisseur qui ne rapporte pas de coût) et
    `plafond_tokens` (budget en tokens, opérant sur tout fournisseur — les tokens
    sont toujours rapportés, #113). Les deux sont vérifiés : le franchissement de
    l'un ou l'autre stoppe la tâche.
    """

    def __init__(
        self,
        journal: RunJournal,
        plafond_cout_usd: float | None = None,
        *,
        plafond_tokens: int | None = None,
    ) -> None:
        if plafond_cout_usd is None and plafond_tokens is None:
            raise ValueError(
                "PlafondDepense exige au moins un plafond (coût en USD ou tokens)."
            )
        if plafond_cout_usd is not None and plafond_cout_usd <= 0:
            raise ValueError(
                f"plafond_cout_usd doit être > 0 (reçu : {plafond_cout_usd})."
            )
        if plafond_tokens is not None and plafond_tokens <= 0:
            raise ValueError(
                f"plafond_tokens doit être > 0 (reçu : {plafond_tokens})."
            )
        self._journal = journal
        self._plafond_cout_usd = plafond_cout_usd
        self._plafond_tokens = plafond_tokens

    def verifie(self, en_cours: StepUsage) -> None:
        """Lève `PlafondDepenseDepasse` si la dépense du run, `en_cours` compris, dépasse.

        Le coût passe d'abord (le message le plus parlant quand il est connu), les
        tokens ensuite — le seuil en tokens tient même quand le coût est inconnu.
        """
        total = RunCost.depuis_journal(self._journal).total.fusion(en_cours)
        cout = total.cout_usd
        if (
            self._plafond_cout_usd is not None
            and cout is not None
            and cout > self._plafond_cout_usd
        ):
            raise PlafondDepenseDepasse(
                f"plafond de dépense dépassé : {cout:.4f} $ consommés sur l'exécution "
                f"pour un plafond de {self._plafond_cout_usd:.4f} $ — tâche stoppée."
            )
        if (
            self._plafond_tokens is not None
            and total.tokens_total > self._plafond_tokens
        ):
            raise PlafondDepenseDepasse(
                f"plafond de tokens dépassé : {total.tokens_total} tokens consommés sur "
                f"l'exécution pour un plafond de {self._plafond_tokens} — tâche stoppée."
            )


def resume_controle_depense(
    plafond_cout_usd: float | None,
    plafond_tokens: int | None,
    usage: StepUsage,
) -> str:
    """Décrit en une ligne le contrôle de dépense actif, pour l'opérateur (#113).

    Rend visible le cas « plafond silencieusement inopérant » : un plafond de coût
    armé sur un fournisseur qui ne rapporte pas de coût (`usage.cout_usd` None) n'a
    aucune prise — seul un plafond en tokens plafonne alors réellement. Destinée à
    la synthèse du run et au rapport JSON (`RunReport`), pas à la logique de
    contrôle (portée par `PlafondDepense.verifie`).
    """
    controles: list[str] = []
    if plafond_tokens is not None:
        controles.append(f"tokens ({usage.tokens_total}/{plafond_tokens})")
    if plafond_cout_usd is not None:
        if usage.cout_usd is not None:
            controles.append(f"coût réel ({usage.cout_usd:.4f}/{plafond_cout_usd:.4f} $)")
        elif plafond_tokens is None:
            # Le seul plafond posé est sans prise : la dépense n'est bornée par rien.
            return (
                f"plafond de coût de {plafond_cout_usd:.4f} $ armé mais SANS PRISE — "
                "le fournisseur ne rapporte pas de coût ; armez un plafond en tokens "
                "(--plafond-tokens) pour plafonner ce run."
            )
        else:
            controles.append(
                f"coût inopérant ({plafond_cout_usd:.4f} $ — fournisseur sans coût rapporté)"
            )
    if not controles:
        return "aucun plafond armé"
    return "plafond actif — " + ", ".join(controles)

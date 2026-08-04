"""Référence de ticket externe portée par une tâche (ticket #187, contrat #183).

Une carte du Kanban n'avait aucun lien vers le ticket qui l'a motivée. Maestro
n'ayant pas de connecteur de gestion de tickets et n'en prévoyant pas (docs/16),
la référence est une **donnée transportée**, jamais une intégration : un
identifiant lisible (`id`) et une `url`, sans aucun champ propre à un outil —
GitLab, Jira ou Linear passent par la même forme.

Le modèle (`ReferenceTicket`) est **celui figé par le contrat #183**, à la clé
près de son emplacement : il vit ici, dans un module **feuille** qui n'importe
rien de `maestro`, parce que le chemin de la référence traverse des couches qui
ne peuvent pas dépendre les unes des autres — le journal (`maestro.telemetry`)
la transporte, et les événements (`maestro.controltower.events`) la diffusent,
or `controltower` importe déjà `telemetry`. Le définir dans `events` aurait donc
fermé un cycle d'imports dès que `StepRecord` s'est mis à la porter.
`maestro.controltower.events` le ré-exporte : les imports existants (`from
maestro.controltower.events import ReferenceTicket`) restent valides.

Deux chemins de pose, tous deux passifs pour le moteur :

- **à la création d'un run** (le run part d'un ticket) : la référence est posée
  sur les tâches du plan par `OrchestrationEngine.run(..., ticket=…)` ;
- **au fil de l'exécution** : un agent équipé du serveur MCP de son outil de
  ticketing consigne `consigne_ticket(...)` — le journal la diffuse comme tout
  le reste, sans que le moteur ait à savoir d'où elle sort.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - annotations seulement
    from maestro.telemetry.journal import RunJournal

#: Suffixe de l'étape de journal qui pose un ticket en cours d'exécution (cf.
#: `consigne_ticket`) — même convention que `:message` (#44) ou `:validation`
#: (#48) : une annexe rattachée à la tâche `<tache>`, sans usage.
SUFFIXE_ETAPE_TICKET = ":ticket"

#: Longueur au-delà de laquelle un identifiant n'est plus « lisible » : il ne
#: tiendrait pas sur une carte et trahit un collage accidentel. Reprend la borne
#: du schéma partagé (`packages/shared/schemas/task.schema.json`).
LONGUEUR_MAX_ID = 64

#: Les seuls schémas d'URL retenus. Le filtrage est **redit** côté UI
#: (`apps/web/lib/liens.ts`, #192) : ce qui arrive par le flux n'est pas fiable,
#: et un `href` non filtré exécuterait du code (`javascript:`).
SCHEMAS_URL = ("http://", "https://")


@dataclass(frozen=True)
class ReferenceTicket:
    """La référence d'un ticket externe portée par une tâche (#187, contrat #183).

    Générique par construction — un identifiant lisible (`id`, ex. « #183 »,
    « PROJ-42 ») et son `url` : GitLab, Jira ou Linear passent par la même forme,
    aucun champ propre à un outil (`gitlab_iid`…). `url` reste vide quand seul
    l'identifiant est connu (l'agent a nommé le ticket sans en donner le lien).
    Une tâche sans ticket ne porte **pas** de référence (`None`) — un plan sans
    référence reste valide.
    """

    id: str
    url: str = ""

    def to_dict(self) -> dict[str, str]:
        """Réémet la référence en dict JSON-sérialisable (`{id, url}`)."""
        return {"id": self.id, "url": self.url}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ReferenceTicket:
        """Reconstruit une référence depuis sa forme `to_dict` (clés absentes → vides)."""
        return cls(id=data.get("id", ""), url=data.get("url", ""))

    @property
    def vide(self) -> bool:
        """La référence n'apprend-elle rien (ni identifiant, ni URL) ?"""
        return not self.id and not self.url

    @classmethod
    def depuis(cls, data: Any) -> ReferenceTicket | None:
        """Construit une référence depuis un dict brut — **None** si elle n'apprend rien.

        Le pendant tolérant de `from_dict`, pour tout ce qui arrive de
        l'extérieur : une ligne de journal relue, un événement JSON, un corps de
        requête. Rend None sur autre chose qu'un mapping, ignore les clés
        inconnues et normalise par `valide` — ce qui ressort est montrable tel
        quel.
        """
        if not isinstance(data, Mapping):
            return None
        reference = cls(
            id=str(data.get("id") or "").strip(),
            url=str(data.get("url") or "").strip(),
        ).valide()
        return None if reference.vide else reference

    def valide(self) -> ReferenceTicket:
        """Rend la référence **normalisée** : identifiant borné, URL suivable ou vide.

        La validation ne lève pas et ne refuse pas la tâche : une référence est
        un confort d'humain, pas une donnée dont dépend l'exécution — la
        dégrader vaut mieux que faire échouer un run pour une URL douteuse. Un
        identifiant trop long est tronqué, une URL de schéma inattendu
        (`javascript:`, chemin relatif) est **jetée**, l'identifiant restant.
        """
        identifiant = " ".join(self.id.split())[:LONGUEUR_MAX_ID]
        url = self.url.strip()
        if not url.lower().startswith(SCHEMAS_URL) or any(c.isspace() for c in url):
            url = ""
        return ReferenceTicket(id=identifiant, url=url)


def ticket_en_dict(ticket: ReferenceTicket | None) -> dict[str, str] | None:
    """La forme JSON d'une référence **optionnelle** : son dict, ou None s'il n'y en a pas.

    Le raccourci des `to_dict` qui portent le champ : côté client, `null` dit
    « aucun ticket » et la vue reste strictement inchangée (#192).
    """
    return None if ticket is None else ticket.to_dict()


def consigne_ticket(
    journal: RunJournal,
    tache_id: str,
    ticket: ReferenceTicket,
    *,
    agent: str = "",
    role: str = "",
) -> None:
    """Pose un ticket sur une tâche **en cours d'exécution**, par le journal (#8).

    Le pendant de `consigne_message` (#44) pour le ticketing : l'agent qui
    découvre le ticket dont relève sa tâche — typiquement via le serveur MCP de
    son outil (#104) — consigne une étape `<tache>:ticket`. La ligne part dans
    les traces comme les autres, le pont Control Tower (#46) la convertit en
    événement `tache.reference` et la projection la pose sur la carte.

    Le moteur n'est donc **jamais** au courant de l'outil de ticketing : il ne
    voit qu'une étape de journal de plus, sans usage (rien n'a été dépensé pour
    la poser — elle n'entre pas au grand livre). Une référence vide est ignorée
    plutôt que consignée : il n'y a rien à montrer.
    """
    # Import local : le module reste une feuille (cf. docstring), le journal
    # dépendant lui-même de `ReferenceTicket`.
    from maestro.telemetry.usage import StepUsage

    ticket = ticket.valide()
    if ticket.vide or not tache_id:
        return
    journal.consigne(
        etape=f"{tache_id}{SUFFIXE_ETAPE_TICKET}",
        nom="",
        agent=agent,
        role=role,
        statut="",
        entree="",
        sortie=f"ticket externe {ticket.id or ticket.url}",
        usage=StepUsage(),
        ticket=ticket,
    )

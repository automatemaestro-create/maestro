"""Garde-fous du POC : plafond de dépense, time-out, validation humaine (ticket #9).

Matérialise les « limites globales » de docs/01 §5 au niveau de la boucle
d'orchestration, en trois protections **par tâche** :

- **plafond de dépense** (`plafond_cout_usd`) : la tâche est stoppée dès que son
  coût cumulé dépasse le plafond. L'application passe par le canal d'usage de la
  télémétrie (`maestro.telemetry.collect_usage(plafond_cout_usd=...)`) — c'est là
  que le coût est visible au fil des appels modèle, donc là qu'il se contrôle ;
- **time-out** (`timeout_s`) : la tâche est annulée si sa réalisation excède le
  délai (l'attente d'une validation humaine n'y est pas comptée) ;
- **validation humaine** : une tâche classée **sensible** (déploiement, production,
  suppression… — cf. `MOTS_SENSIBLES`) déclenche une `DemandeValidation` soumise au
  `validateur` configuré avant toute exécution. **Fail-safe** : sans validateur, ou
  si le validateur échoue, la demande est refusée — l'action sensible ne part
  jamais sans accord humain explicite (EF-08, ENF-04).

`Guardrails()` sans argument laisse plafond et time-out inactifs mais garde la
détection d'actions sensibles (refus par défaut). La classification par mots-clés
est assumée comme heuristique de POC : la V1 la remplacera par une liste d'actions
outillées classées côté serveur (docs/03, entité APPROVAL) sans changer ce contrat.
"""

from __future__ import annotations

import inspect
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from maestro.orchestrator.schema import Task

#: Mots-clés (normalisés sans accents) classant une tâche comme sensible, d'après
#: les fiches de rôles (docs/04 : déploiement, migration destructive, suppression).
#: Des radicaux plutôt que des mots entiers : « deploi » couvre déploiement/déploie,
#: « supprim » couvre supprimer/suppression, « destructi » destructive/destructif.
MOTS_SENSIBLES: tuple[str, ...] = (
    "deploi",
    "deploy",
    "production",
    "supprim",
    "destructi",
    "drop table",
    "truncate",
    "secret",
    "mot de passe",
    "credential",
)


@dataclass(frozen=True)
class DemandeValidation:
    """Demande de validation humaine sur une action sensible (docs/03, entité APPROVAL).

    Porte tout ce qu'un humain doit voir pour trancher : la tâche (id, titre,
    description), l'agent qui l'exécuterait, et la `raison` pour laquelle elle a
    été classée sensible.
    """

    task_id: str
    titre: str
    description: str
    agent: str
    role: str
    raison: str


#: Validateur humain : reçoit la demande, répond vrai (approuvée) ou faux (refusée).
#: Synchrone ou asynchrone — le moteur attend le résultat dans les deux cas.
Validateur = Callable[[DemandeValidation], bool | Awaitable[bool]]


@dataclass(frozen=True)
class Guardrails:
    """Garde-fous appliqués par la boucle à chaque tâche. Immuable.

    `plafond_cout_usd` et `timeout_s` sont inactifs à None ; `mots_sensibles` pilote
    la classification (vide = détection désactivée) ; `validateur` est le canal de
    la décision humaine — absent, toute action sensible est refusée (fail-safe).
    """

    plafond_cout_usd: float | None = None
    timeout_s: float | None = None
    validateur: Validateur | None = None
    mots_sensibles: tuple[str, ...] = MOTS_SENSIBLES

    def __post_init__(self) -> None:
        if self.plafond_cout_usd is not None and self.plafond_cout_usd <= 0:
            raise ValueError(
                f"plafond_cout_usd doit être > 0 (reçu : {self.plafond_cout_usd})."
            )
        if self.timeout_s is not None and self.timeout_s <= 0:
            raise ValueError(f"timeout_s doit être > 0 (reçu : {self.timeout_s}).")

    def raison_sensible(self, task: Task) -> str | None:
        """Classe la tâche : la raison si elle est sensible, None sinon.

        Heuristique POC : recherche des mots-clés dans le titre et la description,
        après normalisation (minuscules, accents retirés) pour tolérer les variantes.
        """
        texte = _normalise(f"{task.titre} {task.description}")
        for mot in self.mots_sensibles:
            if _normalise(mot) in texte:
                return f"mot sensible « {mot} » détecté dans la tâche"
        return None

    async def demande_validation(self, demande: DemandeValidation) -> tuple[bool, str]:
        """Soumet la demande au validateur ; renvoie (approuvée ?, détail traçable).

        Fail-safe : sans validateur, ou si le validateur lève une exception, la
        demande est **refusée** — jamais d'action sensible sans accord explicite.
        Le détail est destiné au journal et au message d'erreur de la tâche.
        """
        if self.validateur is None:
            return False, "aucun validateur humain configuré — refus par défaut"
        try:
            decision: Any = self.validateur(demande)
            if inspect.isawaitable(decision):
                decision = await decision
        except Exception as exc:  # fail-safe : un validateur en panne ne laisse rien passer
            return False, f"validateur en erreur ({exc}) — refus par défaut"
        if decision:
            return True, "approuvée par le validateur humain"
        return False, "refusée par le validateur humain"


def _normalise(texte: str) -> str:
    """Minuscules et accents retirés — la forme sous laquelle les mots-clés matchent."""
    decompose = unicodedata.normalize("NFKD", texte.lower())
    return "".join(c for c in decompose if not unicodedata.combining(c))

"""Ce que le fil dépense entre au grand livre (#1534).

Un appel modèle signale son usage au collecteur actif (`maestro.telemetry.usage
.report_usage`), et **sans collecteur, la mesure est perdue**. Le moteur en ouvre
un autour de chaque tâche et de chaque étape d'un run ; le fil n'en ouvrait aucun.
Sa déclaration d'un projet, ses questions d'outillage, ses pièces, son équipe, ses
récits de fin de run : tout ce que Maestro dépense en conversation était payé et
jamais compté. Sur le projet de p5, 1,548 $ des 15,97 $ réellement dépensés
(#1508, docs/50 § 1.2) — un grand livre qui sous-compte de 28 % fait paraître
Maestro moins cher qu'une session seule, et c'est précisément ce que le go/no-go
mesure.

`au_compte_du_fil` est le seul geste : un bloc qui ouvre un collecteur, et qui à sa
sortie publie ce qu'il a recueilli en `fil.depense` (`EVENEMENT_FIL_DEPENSE`). Il
se pose là où le fil travaille pour quelqu'un — chaque échange de `ServiceChat`,
les routes de l'équipe et de l'outillage, le récit de fin d'un run —, et nulle part
dans les répondeurs eux-mêmes : la mesure suit le contexte, donc tout ce qui est
appelé dans le bloc est compté, lectures du disque, pièces et images comprises,
sans qu'un appelant de plus ait à s'en souvenir.

Trois propriétés, et chacune tient à la mécanique du collecteur :

- **pas de double compte** : un bloc imbriqué masque l'englobant
  (`collect_usage`). Un geste du fil qui en appelle un autre publie deux parts,
  chacune la sienne ; le bilan d'un run, qui ouvre son propre collecteur et publie
  sur le run, n'entre jamais dans celui du fil ;
- **aucune dépense hors du bloc n'y est attribuée** : un run lancé depuis le fil
  travaille dans son hôte, sous ses propres collecteurs ;
- **l'observateur ne casse jamais l'observé** : une publication qui échoue est
  tracée et avalée, la réponse du fil part quand même.

Le projet se lit **après** le travail (`projet` est un appelable) : c'est souvent
ce travail qui le fait connaître — le projet qui vient de naître, la pièce qui le
nomme.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from maestro.controltower.events import EVENEMENT_FIL_DEPENSE, Event, EventBus
from maestro.telemetry.usage import StepUsage, collect_usage

_LOGGER = logging.getLogger("maestro.controltower")


def _a_depense(usage: StepUsage) -> bool:
    """La mesure dit-elle quoi que ce soit — un appel, des tokens, un coût ?"""
    return bool(usage.appels or usage.tokens_total or usage.cout_usd)


def en_clair(usage: StepUsage) -> str:
    """La dépense en une phrase courte — « 0,0523 $ · 12 480 tokens » —, pour `detail`.

    Composée ici et non par l'écran : le journal requêtable (#478) ne garde que les
    champs de texte d'un événement, si bien qu'un fil d'activité relu après
    rechargement n'aurait plus de montant à dire. Même partage que le relevé en
    cours d'une tâche (`tache.usage`, #835). Un coût inconnu se dit tel, jamais
    « 0 $ ».
    """
    tokens = f"{usage.tokens_total:,}".replace(",", " ") + " tokens"
    if usage.cout_usd is None:
        return f"{tokens} · coût pas encore tarifé"
    return f"{usage.cout_usd:.4f} $".replace(".", ",") + f" · {tokens}"


@asynccontextmanager
async def au_compte_du_fil(
    bus: EventBus,
    *,
    titre: str,
    agent: str = "",
    role: str = "",
    projet: Callable[[], str | None] = lambda: None,
    conversation: str = "",
) -> AsyncIterator[None]:
    """Compte ce que le bloc dépense, puis le publie en `fil.depense` (#1534).

    `titre` dit en mots d'écran ce qui a été payé (« réponse du fil », « équipe
    proposée », « récit de fin de run »). `projet` est appelé à la sortie du bloc,
    `conversation` range au projet né de la conversation les tours qui l'ont
    précédé (`ControlTowerState._applique_depense_du_fil`).

    Publié même quand le bloc lève — une réponse en panne, un échange arrêté :
    ce qui a été dépensé l'a été. Rien n'est publié quand rien n'a été mesuré.
    """
    with collect_usage() as collecteur:
        try:
            yield
        finally:
            usage = collecteur.total
            if _a_depense(usage):
                await _publier(
                    bus,
                    usage,
                    titre=titre,
                    agent=agent,
                    role=role,
                    projet=projet,
                    conversation=conversation,
                )


async def _publier(
    bus: EventBus,
    usage: StepUsage,
    *,
    titre: str,
    agent: str,
    role: str,
    projet: Callable[[], str | None],
    conversation: str,
) -> None:
    """Publie la dépense ; une panne se trace et s'avale (l'observateur ne casse rien)."""
    try:
        projet_id = projet()
    except Exception:  # noqa: BLE001 — un projet illisible n'efface pas la dépense
        _LOGGER.exception("Projet d'une dépense du fil illisible : rangée hors projet.")
        projet_id = None
    try:
        await bus.publish(
            Event(
                type=EVENEMENT_FIL_DEPENSE,
                agent=agent,
                role=role,
                titre=titre,
                detail=en_clair(usage),
                projet_id=projet_id or None,
                conversation=conversation,
                usage=usage,
                cout_usd=usage.cout_usd,
            )
        )
    except Exception:  # noqa: BLE001 — l'observateur ne casse jamais l'observé
        _LOGGER.exception("Dépense du fil non publiée (%s) : %s", titre, usage.resume_court())

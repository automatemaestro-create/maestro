"""L'hôte en process : un run est une tâche de fond de l'API (#442).

L'unique implémentation de `HoteRun` aujourd'hui, et le **défaut** : elle fait,
au geste près, ce que `ServiceExecutions` faisait lui-même depuis #185 — une
`asyncio.Task` par run, gardée dans un dict, annulée à la demande, retirée
quand elle s'éteint. Rien n'est ajouté, rien n'est retiré : ce lot déplace du
code derrière un nom, il ne change aucun comportement.

Ce qui change, et c'est tout l'intérêt, c'est **où la connaissance vit**. « Un
run est une tâche de ce process » ne se lit plus dans cinq méthodes du service
mais dans cette classe, qui est aussi celle par laquelle la propriété gênante
s'énonce d'une phrase : *ses runs ne survivent pas à leur hôte*. C'est ce
qu'écrit `fermer`, seul endroit du dispositif où la frontière est visible à
l'œil nu — un hôte détaché (#443) y écrira le contraire, et l'appelant, lui,
n'aura pas une ligne à changer.

Le **dérouleur** est passé à la construction, et non déduit ici : ce qu'un run
fait (bâtir un moteur, poser le journal, consigner l'issue) appartient au
service, pas à son hôte. L'hôte en process est le seul qui puisse en recevoir
un — une coroutine ne se sérialise pas —, et c'est exactement ce que dit le
contrat en ne passant à `lancer` qu'un `OrdreRun` : un hôte qui exécute
ailleurs reconstruit le travail à partir de l'ordre, il ne le reçoit pas.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

from maestro.controltower.hote import HoteRun, OrdreRun

#: Ce qu'un run *fait*, du point de vue de l'hôte en process : une coroutine
#: construite à partir de l'ordre, et qui ne remonte rien — son issue part dans
#: le statut du run, pas dans un retour. Le type reste ici et non dans le
#: contrat : il n'a de sens que pour l'hôte capable de partager sa mémoire avec
#: l'appelant.
DerouleurRun = Callable[[OrdreRun], Coroutine[Any, Any, None]]


class HoteRunEnProcess(HoteRun):
    """Chaque run est une tâche de fond de la boucle courante — le défaut (#185).

    `derouler` est ce que la tâche exécute : le service y passe sa propre méthode
    de déroulement, qui bâtit le moteur du run et consigne son issue. L'hôte n'en
    sait rien d'autre — il crée la tâche, la retient tant qu'elle tourne, et la
    laisse partir quand elle s'éteint.

    Les runs **ne survivent pas** à ce process, et c'est la propriété assumée du
    POC (#185) : annulation à portée (`asyncio.Task.cancel`), aucune dépendance
    d'infrastructure ajoutée à `maestro-api`. Ce que cette perte coûte se voit
    (#348) et se rattrape (#349) ; l'empêcher est le chantier #441, dont #446 a
    fait le **défaut** l'autre hôte — celui-ci reste disponible et se nomme
    (`MAESTRO_HOTE_RUN=process`).

    `ramasser` (#446) n'est pas redéfini, et le no-op hérité est ici la réponse
    juste et non un trou : le dérouleur de cette classe *est* le run, il consigne
    donc lui-même son issue avant de rendre la main — il n'existe aucun cas où une
    tâche s'éteint en laissant un run `en_cours`, sauf l'annulation de `fermer`,
    que l'appelant a déjà soldée.
    """

    def __init__(self, derouler: DerouleurRun) -> None:
        self._derouler = derouler
        self._taches: dict[str, asyncio.Task[None]] = {}

    async def lancer(self, ordre: OrdreRun) -> None:
        """Ouvre la tâche de fond du run et rend la main aussitôt.

        Aucun `await` interne, à dessein : la coroutine ne cède pas la main, donc
        l'appelant continue exactement comme avant ce lot — l'événement de
        lancement et le premier battement sont déjà écrits quand la tâche démarre.
        Elle est asynchrone parce que le **contrat** l'est (un hôte qui crée un
        process, lui, aura quelque chose à attendre), jamais parce qu'elle attend.

        La tâche se retire du registre en s'éteignant (`add_done_callback`) : sans
        ce retrait, la mémoire du service croîtrait d'une entrée par run et le
        cœur (#348) aurait à filtrer des tâches finies à chaque battement.
        """
        tache = asyncio.get_running_loop().create_task(self._derouler(ordre))
        self._taches[ordre.run_id] = tache
        tache.add_done_callback(lambda _: self._taches.pop(ordre.run_id, None))

    async def annuler(self, run_id: str, *, delai_s: float) -> bool:
        """Annule la tâche du run et attend son extinction au plus `delai_s`.

        Rend False si ce process ne porte pas (ou plus) le run : le cas normal
        d'un run orphelin, dont l'hôte est tombé — l'appelant a déjà consigné son
        issue, il n'y a rien à interrompre.
        """
        tache = self._taches.get(run_id)
        if tache is None or tache.done():
            return False
        tache.cancel()
        await asyncio.wait({tache}, timeout=delai_s)
        return True

    def en_vol(self, run_id: str) -> bool:
        """La tâche de ce run existe-t-elle ici, et tourne-t-elle encore ?"""
        tache = self._taches.get(run_id)
        return tache is not None and not tache.done()

    def runs_en_vol(self) -> tuple[str, ...]:
        """Les runs dont la tâche tourne encore, dans l'ordre de leur lancement.

        La copie (`list(...)`) n'est pas une précaution de style : l'appelant est
        le cœur du service, qui `await` entre deux runs de la liste — une tâche
        peut donc s'éteindre, et se retirer du registre, en plein parcours.
        """
        return tuple(run_id for run_id, tache in list(self._taches.items()) if not tache.done())

    async def fermer(self, *, delai_s: float) -> None:
        """L'API se retire : **aucun run ne lui survit**, tous sont annulés.

        La contrepartie assumée de la tâche de fond, énoncée ici et à un seul
        endroit. Ce qu'elle emporte n'est pas silencieux pour autant : le dernier
        battement de chaque run reste au registre et vieillit (#348), ce qui le
        fera ressortir `orphelin` au lieu de rester `en_cours` pour toujours — et
        `relancer` (#349) sait le rejouer sur son brief approuvé.
        """
        taches = {tache for tache in self._taches.values() if not tache.done()}
        for tache in taches:
            tache.cancel()
        if taches:
            await asyncio.wait(taches, timeout=delai_s)

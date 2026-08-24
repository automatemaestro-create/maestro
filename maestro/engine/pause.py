"""La **porte** d'un run en pause : on ne lance plus, on ne tue rien (#477).

Mettre un run en pause n'est pas l'annuler à moitié, et c'est toute la difficulté
du geste. Annuler, c'est `Task.cancel()` : les tâches en vol meurent là où elles
en sont, et ce qu'elles avaient produit — un appel modèle payé, un livrable à
moitié écrit — est perdu. Suspendre, c'est l'inverse : **ce qui est parti va à son
terme**, et seul ce qui n'est pas encore parti attend. La différence se paie en
un seul endroit, et c'est celui-ci.

D'où une porte, et non un drapeau qu'on consulterait :

- une tâche prête **franchit** la porte avant d'atteindre l'exécuteur ; si elle
  est fermée, elle attend là, sans avoir rien engagé — ni appel modèle, ni mise
  en file, ni créneau de parallélisme ;
- une tâche **déjà** chez l'exécuteur n'a plus de porte devant elle : elle finit.

C'est un `asyncio.Event` inversé (« ouverte » plutôt que « fermée ») enveloppé
dans un objet nommé, pour trois raisons qui ne tiennent pas dans un booléen :

1. **le sens de lecture**. `await evenement.wait()` demande au lecteur de se
   rappeler ce que « posé » veut dire ici ; `await porte.franchir()` le dit ;
2. **le défaut**. Une porte neuve est **ouverte** — un moteur qui n'a jamais
   entendu parler de pause ne doit pas attendre, et l'oubli du `set()` initial
   figerait tous les runs du dépôt. En faire l'état de construction retire
   l'oubli possible ;
3. **la frontière**. Le moteur ne connaît ni bus, ni Control Tower, ni ordre
   HTTP : il connaît une porte. Qui la ferme, et pourquoi, est le problème de
   l'appelant — `maestro.controltower.hote_detache` la ferme sur l'ordre lu au
   bus, `ServiceExecutions` la ferme de la main à la main. Les deux hôtes
   partagent ainsi la sémantique sans partager le transport.

Un point à ne pas défaire : la porte est **réutilisable**. Un run peut être
suspendu, repris, suspendu à nouveau — ce qui interdit la forme « à usage
unique » (un `Future`, ou un guet qui rendrait `True` et sortirait). C'est la
différence de nature avec l'annulation, dont l'ordre est définitif.
"""

from __future__ import annotations

import asyncio

__all__ = ["PorteExecution"]


class PorteExecution:
    """Le passage qu'une tâche franchit avant d'être exécutée — ouverte par défaut.

    Créée ouverte : sans pause demandée, `franchir()` rend la main sans céder le
    contrôle à la boucle plus qu'un `await` déjà posé sur le chemin.
    """

    __slots__ = ("_ouverte",)

    def __init__(self, *, ouverte: bool = True) -> None:
        self._ouverte = asyncio.Event()
        if ouverte:
            self._ouverte.set()

    @property
    def ouverte(self) -> bool:
        """La porte laisse-t-elle passer ? — l'état, jamais l'attente."""
        return self._ouverte.is_set()

    def ouvrir(self) -> None:
        """Reprend : toutes les tâches qui attendaient repartent. Idempotent."""
        self._ouverte.set()

    def fermer(self) -> None:
        """Suspend : plus aucune tâche ne passe. Idempotent.

        Ne touche à **rien** de ce qui est déjà passé : une tâche chez
        l'exécuteur ne repasse pas par ici, c'est la définition même de la pause.
        """
        self._ouverte.clear()

    async def franchir(self) -> None:
        """Attend que la porte soit ouverte — retour immédiat si elle l'est déjà.

        Reste annulable : une annulation demandée pendant une pause emporte les
        tâches en attente comme n'importe quelle autre (`CancelledError` se
        propage), sans quoi un run suspendu serait un run qu'on ne peut plus
        arrêter.
        """
        await self._ouverte.wait()

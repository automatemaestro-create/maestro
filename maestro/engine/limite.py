"""À la limite d'usage du fournisseur, le run se met en pause et reprend — le contrat (#1543).

Le défaut que ce module referme, relevé sur le run `0300f3ffdddf` (2026-10-15,
docs/51 § 4.2) : la tâche « vérifier et commiter » a été coupée par la limite de
session du compte, elle a fini en échec, et le run avec elle — « tâches en échec »,
une cause qui ne se relance pas —, alors que son commit conforme était écrit et
qu'il ne restait que la fusion. La chaîne du dépôt fait mieux sur la même limite
depuis #171 : elle attend le reset et rouvre **la même session** par son
identifiant, contexte intact (docs/10 § 11.4).

## Ce qui se passe à la limite

1. l'adaptateur du fournisseur reconnaît la limite à son **signal typé** — jamais au
   texte de l'erreur (docs/44) — et lève `LimiteUsageAtteinte`, avec l'instant où
   elle se lève quand il le donne ;
2. la tâche n'échoue pas : son runtime **attend**, l'espace de travail ouvert
   (`maestro.agents.runtime`), et le run se met **en pause**, cause « limite
   d'usage », l'heure de reprise dite — par l'attente câblée (`AttenteLimite`), que
   la Control Tower tient (`maestro.controltower.limite`) ;
3. au reset, ou sur « Reprendre », la tâche repart **dans sa session** : le
   fournisseur la rouvre par l'identifiant que Maestro lui a donné (`SessionAgent`),
   et l'agent reçoit `maestro.agents.runtime.NOTE_REPRISE_LIMITE` à la suite de ce
   qu'il avait fait. Rien de ce qui est fait n'est refait : ni les tâches soldées,
   ni ce que celle-ci avait écrit.

## Une attente pour le run

À plusieurs tâches en vol, la limite tombe sur toutes à quelques secondes
d'intervalle. Les laisser attendre chacune dans son coin poserait N pauses sur un
seul run, et la tâche la moins bien informée — celle dont le flux n'a pas porté
l'heure de reprise — se réveillerait sur une autre règle que sa voisine. D'où le
**rendez-vous** (`RendezVousLimite`) : une attente par run, que toutes rejoignent,
et la règle de la chaîne (#291) — **la meilleure information l'emporte, jamais la
plus récente** : une heure de reprise connue remplace une attente aveugle, et à
information égale on ne fait que rallonger.

## Ce qui n'est pas inventé

Sans heure de reprise — un solde épuisé, un fournisseur qui ne la dit pas —, le run
attend « Reprendre » : aucun palier de quelques minutes n'est posé à la place de ce
que le fournisseur n'a pas dit. Sans attente câblée (un moteur sans Control Tower),
la tâche échoue sur sa limite, nommée par son type, comme avant ce lot.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import datetime

__all__ = [
    "AttenteLimite",
    "LimiteDuRun",
    "RendezVousLimite",
    "heure_de_reprise",
    "phrase_de_la_limite",
    "phrase_de_la_reprise",
]


def heure_de_reprise(instant: datetime) -> str:
    """L'heure de reprise en heure du poste, à la minute — c'est ce qu'on se dit d'un run."""
    return instant.astimezone().strftime("%Y-%m-%d %H:%M")


@dataclass(frozen=True)
class LimiteDuRun:
    """La limite d'usage telle que le run l'attend — des faits, jamais une phrase.

    `tache_id` et `titre` sont ceux de la tâche qu'elle a coupée la première (celle
    qui ouvre l'attente), `reprise_a` l'instant où le fournisseur dit qu'elle se
    lève — None quand il ne le dit pas —, `detail` ce qu'il en a dit, pour qu'une
    personne le lise.
    """

    run_id: str
    projet_id: str | None
    tache_id: str
    titre: str
    reprise_a: datetime | None = None
    detail: str = ""

    def mieux_informee_que(self, autre: LimiteDuRun) -> bool:
        """Cette limite en dit-elle plus long sur la reprise que `autre` ? (#291)

        Une heure connue l'emporte sur l'absence d'heure ; entre deux heures, la
        plus tardive — se réveiller avant la voisine, c'est retomber sur une limite
        toujours en cours.
        """
        if self.reprise_a is None:
            return False
        return autre.reprise_a is None or self.reprise_a > autre.reprise_a


def phrase_de_la_limite(limite: LimiteDuRun) -> str:
    """Ce que le journal, la frise et le fil disent du run mis en pause par la limite."""
    if limite.reprise_a is not None:
        quand = (
            f"il reprendra de lui-même à {heure_de_reprise(limite.reprise_a)}, "
            "quand le fournisseur la lève, ou sur « Reprendre »"
        )
    else:
        quand = (
            "le fournisseur n'a pas dit quand elle se lève : il reprendra sur "
            "« Reprendre »"
        )
    return (
        f"limite d'usage du fournisseur atteinte pendant « {limite.titre} » — le run "
        f"est en pause ; {quand}. La tâche reprendra dans sa session."
    )


def phrase_de_la_reprise(limite: LimiteDuRun) -> str:
    """Ce qui se dit quand le run repart de lui-même, la limite levée."""
    return (
        "limite d'usage du fournisseur levée — le run reprend, et la tâche coupée "
        "dans sa session"
    )


#: L'attente d'un run à la limite : met le run en pause, le dit, et rend la main
#: quand la tâche peut reprendre — au reset, ou sur « Reprendre ». Elle **lève**
#: quand personne ne peut la tenir (le bus s'est refermé) : la tâche échoue alors
#: sur sa limite, plutôt que d'attendre un signal qui ne viendra pas. En pratique
#: `maestro.controltower.limite.AttenteLimiteControlTower`.
AttenteLimite = Callable[[LimiteDuRun], Awaitable[None]]


@dataclass(eq=False)
class _Attente:
    """Une attente en vol : la limite qu'elle tient, sa tâche, et qui l'attend."""

    limite: LimiteDuRun
    tache: asyncio.Task[None]
    #: Les tâches du run qui l'attendent en ce moment.
    presents: int = 0
    #: L'attente mieux informée qui l'a remplacée — None tant qu'elle tient.
    remplacee_par: _Attente | None = None


class RendezVousLimite:
    """Une attente par run, que toutes ses tâches coupées rejoignent (#1543, règle de #291).

    La première tâche coupée ouvre l'attente ; les suivantes la rejoignent. Une
    tâche qui arrive **mieux informée** — elle porte l'heure de reprise que la
    première n'avait pas, ou une plus tardive — la remplace : l'attente en cours est
    abandonnée et repart sur la meilleure information, que toutes attendent alors.
    Une fois l'attente rendue, elle est oubliée : une limite qui retombe plus tard
    ouvre une attente neuve. Quand plus aucune tâche ne l'attend — le run est
    annulé pendant la pause —, elle est abandonnée : la pause n'a plus d'objet.

    Tenu par l'exécuteur, qui sert plusieurs runs : les attentes sont **par
    `run_id`**, et une limite d'un run n'en suspend aucun autre.
    """

    def __init__(self, attente: AttenteLimite) -> None:
        self._attente = attente
        self._en_vol: dict[str, _Attente] = {}

    async def attendre(self, limite: LimiteDuRun) -> None:
        """Rend la main quand le run de `limite` peut reprendre — lève si l'attente n'a pu tenir."""
        attente = self._inscrire(limite)
        attente.presents += 1
        try:
            while True:
                # `wait` et non `await` : une tâche annulée de l'extérieur n'emporte
                # pas l'attente que les autres tâches du run partagent avec elle.
                await asyncio.wait({attente.tache})
                suivante = attente.remplacee_par
                if suivante is None or not attente.tache.cancelled():
                    break
                # Remplacée par une attente mieux informée : on la rejoint.
                attente.presents -= 1
                suivante.presents += 1
                attente = suivante
        finally:
            attente.presents -= 1
            if attente.presents == 0:
                if self._en_vol.get(limite.run_id) is attente:
                    del self._en_vol[limite.run_id]
                if not attente.tache.done():
                    attente.tache.cancel()
        if attente.tache.cancelled():
            raise RuntimeError("l'attente de la limite d'usage a été abandonnée")
        erreur = attente.tache.exception()
        if erreur is not None:
            raise erreur

    def _inscrire(self, limite: LimiteDuRun) -> _Attente:
        """L'attente du run à rejoindre — ouverte, ou remplacée si `limite` en dit plus long."""
        en_vol = self._en_vol.get(limite.run_id)
        if en_vol is not None and not en_vol.tache.done():
            if not limite.mieux_informee_que(en_vol.limite):
                return en_vol
            # La tâche qui a ouvert l'attente reste celle qu'elle nomme.
            limite = replace(limite, tache_id=en_vol.limite.tache_id, titre=en_vol.limite.titre)
        nouvelle = _Attente(limite, asyncio.create_task(self._tenir(limite)))
        if en_vol is not None and not en_vol.tache.done():
            en_vol.remplacee_par = nouvelle
            en_vol.tache.cancel()
        self._en_vol[limite.run_id] = nouvelle
        return nouvelle

    async def _tenir(self, limite: LimiteDuRun) -> None:
        """L'attente câblée, dans une tâche à elle : celle que toutes rejoignent."""
        await self._attente(limite)

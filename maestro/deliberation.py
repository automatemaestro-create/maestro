"""La délibération humaine : ce qu'elle rend au délai, ce qu'elle laisse derrière (#584).

Le lot 4 du chantier #573 a déplacé l'arbitrage **dans** l'appel d'outil — donc
dans la réalisation de la tâche, là où l'attente d'une décision était jusque-là
soigneusement tenue **avant** l'armement du time-out (`_realise_gardee` :
« le time-out ne court que sur la réalisation elle-même »). Deux propriétés que
personne n'avait eu à écrire tombent avec ce déplacement :

- **un délai posé se met à courir pendant qu'un humain délibère** — un
  `timeout_s` de 600 s tuerait une tâche suspendue quatre minutes sur un acte
  sensible, en pleine question à l'opérateur, pour une lenteur qui n'est pas la
  sienne ;
- **une décision qui arrive trop tard n'est reçue par personne** — le hook a
  déjà rendu son verdict, la demande reste en vol, et son issue est absorbée
  puis jetée (`_absorbe_arbitrage_tardif`).

Ce module porte les deux réponses, et une seule idée les relie : *le temps
d'arbitrage n'appartient pas à la tâche*. Il ne lui est donc ni facturé
(`CreditArbitrage`), ni perdu (`MemoireArbitrage`).

Il vit **à la racine** du paquet, comme `maestro.acte` (#581), parce qu'il est
lu des deux côtés d'une frontière : le **moteur** décompte le crédit de son
échéance et le consigne au journal, le **fournisseur** ouvre et ferme les
fenêtres d'attente. Le ranger sous `maestro.engine` obligerait
`maestro.providers` à en dépendre, ce que la frontière interdit ; le ranger sous
`maestro.providers` ferait dépendre le garde-fou d'un délai d'une couche de
transport.

## Qui ouvre la fenêtre, et pourquoi ce n'est pas le moteur

Le moteur *compose* les deux canaux d'arbitrage (`_arbitre`, `_arbitre_acte`),
et il aurait donc pu mesurer lui-même la durée de ses propres callbacks. Ce
serait faux, et d'un facteur non borné : le hook `PreToolUse` **cesse
d'attendre** à `BornesArbitrage.attente_effective` et rend son verdict, pendant
que la demande, elle, reste en vol (c'est tout le dispositif de #583). Une
mesure prise dans le callback continuerait donc de courir longtemps après que
l'agent a repris son travail — et rendrait à la tâche du délai qu'elle a passé à
travailler.

La fenêtre est ouverte **là où l'appel est suspendu** : le hook pour l'acte
intercepté (#583), l'outil `demander_arbitrage` pour l'agent qui lève la main
(#582), et l'exécuteur lui-même pour la validation d'une tâche sensible (#9).
C'est la seule position d'où « combien de temps la tâche est-elle restée
bloquée ? » a une réponse exacte.

## L'union, jamais la somme

Deux arbitrages simultanés dans une même tâche ne coûtent pas deux fois leur
durée : la tâche n'est bloquée qu'une fois. Le crédit est donc l'**union** des
intervalles d'attente et non leur somme — la règle même que `journal.sh audit`
applique au temps passé sous outil (#497), et pour la même raison : additionner
des intervalles qui se recouvrent rendrait une part de 110 %.

## Ce qui n'est pas plafonné, et pourquoi

Le crédit n'a pas de plafond. Le plafonner rendrait au délai le pouvoir de tuer
une tâche en pleine question — c'est-à-dire exactement ce que ce ticket lui
retire. Ce qui borne le nombre d'attentes est ailleurs, et existait déjà : le
plafond de tours borne les appels d'outil de l'agent (#239), donc les actes à
arbitrer ; le plafond de dépense borne ce que la tâche consomme (#9) ; et
l'attente de *chaque* arbitrage est bornée par le fournisseur
(`maestro.providers.arbitrage.BornesArbitrage`). Un délai par tâche n'a jamais
été le garde-fou d'un humain qui ne répond pas.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from time import perf_counter

#: L'issue d'un arbitrage, telle qu'elle voyage partout ailleurs : `(approuvé ?,
#: détail traçable)` — le couple de `Guardrails.demande_validation`.
Issue = tuple[bool, str]


class CreditArbitrage:
    """Le temps qu'une tâche a passé suspendue à une décision humaine.

    Un compteur, deux gestes. `attente()` ouvre une fenêtre autour de l'attente
    elle-même ; `ecoule()` rend le cumul, **fenêtres encore ouvertes comprises** —
    sans quoi une échéance atteinte pendant une délibération conclurait au
    dépassement d'une tâche qui n'a rien consommé.

    L'objet est celui d'**une** tâche et ne vit que dans la boucle asyncio de son
    exécution : aucun verrou, aucune synchronisation entre boucles. Deux tâches
    exécutées de front ont chacune le sien (`LocalExecutor.execute`).
    """

    def __init__(self) -> None:
        # Cumul des fenêtres déjà refermées, en secondes.
        self._acquis: float = 0.0
        # Profondeur d'imbrication des fenêtres ouvertes. C'est ce compteur qui
        # rend l'**union** plutôt que la somme : deux attentes qui se recouvrent
        # n'ouvrent qu'un seul intervalle, du premier `attente()` au dernier
        # `finally`. Les compter séparément facturerait deux fois un temps que
        # la tâche n'a passé qu'une fois.
        self._profondeur: int = 0
        # Début de l'intervalle courant (sans objet quand `_profondeur` est nul).
        self._ouverture: float = 0.0
        # Posé tant qu'aucune attente n'est en cours : c'est ce que l'échéance
        # du moteur attend pour reprendre son décompte. `asyncio.Event` ne
        # s'attache plus à une boucle à la construction (≥ 3.10), donc l'objet
        # se construit hors de toute boucle — un test synchrone en construit.
        self._repos = asyncio.Event()
        self._repos.set()

    @contextmanager
    def attente(self) -> Iterator[None]:
        """Ouvre une fenêtre d'attente autour du bloc — refermée quoi qu'il arrive.

        Le `finally` n'est pas une politesse : les trois issues d'un arbitrage
        passent par une sortie différente (retour, `TimeoutError` du hook,
        exception d'un canal en panne), et une fenêtre laissée ouverte sur l'une
        d'elles créditerait la tâche jusqu'à la fin du run.
        """
        self._ouvre()
        try:
            yield
        finally:
            self._ferme()

    def _ouvre(self) -> None:
        if self._profondeur == 0:
            self._ouverture = perf_counter()
            self._repos.clear()
        self._profondeur += 1

    def _ferme(self) -> None:
        self._profondeur -= 1
        if self._profondeur == 0:
            self._acquis += perf_counter() - self._ouverture
            self._repos.set()

    def en_attente(self) -> bool:
        """Une délibération est-elle en cours à cet instant ?"""
        return self._profondeur > 0

    def ecoule(self) -> float:
        """Le temps d'arbitrage cumulé, en secondes — attente en cours comprise."""
        if self._profondeur == 0:
            return self._acquis
        return self._acquis + (perf_counter() - self._ouverture)

    def ecoule_ms(self) -> int:
        """Le même cumul, en millisecondes — l'unité du journal (`StepUsage`)."""
        return int(self.ecoule() * 1000)

    async def repos(self) -> None:
        """Rend la main quand plus aucune attente n'est en cours (immédiatement s'il n'y en a pas).

        C'est ce que le moteur attend au lieu de conclure au dépassement : tant
        qu'une personne délibère, l'échéance n'avance pas, et il n'y a rien à
        recalculer avant que la délibération finisse.
        """
        await self._repos.wait()


def cle_acte(outil: str, arguments: Mapping[str, str]) -> str:
    """L'identité d'un **acte** — ce sur quoi une décision humaine porte réellement.

    Un arbitrage ne se retrouve pas par tâche : une tâche en contient plusieurs,
    et c'est déjà ce qui fait qu'une demande écrase la précédente dans la
    projection de la Control Tower, qui indexe par `tache_id`. Ce qu'une personne
    a tranché est un appel d'outil précis, avec ses arguments précis — approuver
    `rm build/` n'approuve pas `rm /`.

    La clé est donc l'outil et ses arguments, **déjà bornés** par `maestro.acte`
    (#581, 1000 caractères par valeur) : elle ne peut pas grossir sans mesure. Le
    tri des clés la rend stable d'un appel à l'autre, `ensure_ascii=False` évite
    qu'un accent la fasse enfler, et le JSON évite l'ambiguïté qu'une
    concaténation introduirait (`{"a": "b:c"}` et `{"a:b": "c"}`).
    """
    return json.dumps([outil, dict(sorted(arguments.items()))], ensure_ascii=False, sort_keys=True)


class MemoireArbitrage:
    """Ce qu'une tâche retient des arbitrages qu'elle a soumis — décisions et demandes en vol.

    Deux dictionnaires, et c'est tout le second critère de #584 :

    - une décision **déjà rendue** est servie telle quelle, sans nouvelle
      attente ni nouvelle demande. C'est ce qui fait qu'un appel rejoué par
      l'agent retrouve une décision arrivée après que le hook a cessé
      d'attendre — la personne a tranché, sa décision existe, elle n'avait
      simplement personne à qui être rendue ;
    - une demande **encore en vol** est partagée : un second appel sur le même
      acte se rebranche dessus au lieu d'en ouvrir une deuxième. Sans ce
      partage, un agent qui réessaie fabriquerait une file de demandes
      identiques devant l'opérateur, dont chacune écraserait la précédente dans
      la projection (indexée par `tache_id`) — et la seule qui compte serait la
      dernière.

    La forme est celle de `maestro.messaging.handoff` (#38) : une attente
    partagée, relevée par qui la trouve. Elle est **volontairement en mémoire et
    liée à la tâche** — la mémoire est créée par `LocalExecutor.execute` et
    traverse les relances (#91), qui est la portée exacte que demande le critère
    (« la tâche relancée reprend sur elle »). Au-delà, la décision n'est pas
    perdue non plus : elle vit dans le journal d'événements de la Control Tower,
    qui est durable, et c'est là qu'un autre run irait la chercher.

    Rien n'est purgé, et rien n'a besoin de l'être : seuls les actes **classés
    `ask`** y entrent, c'est-à-dire ceux dont chacun a coûté une décision
    humaine. Une tâche qui en accumulerait assez pour peser aurait un problème
    bien avant celui-là.
    """

    def __init__(self) -> None:
        self._rendues: dict[str, Issue] = {}
        self._en_vol: dict[str, asyncio.Future[Issue]] = {}

    def decision(self, cle: str) -> Issue | None:
        """La décision déjà rendue pour cet acte, ou None — lecture seule, sans attente."""
        return self._rendues.get(cle)

    def retient(self, cle: str, issue: Issue) -> None:
        """Retient une décision rendue. Idempotent : la **première** fait foi.

        Comme le `409` de la Control Tower sur une demande déjà tranchée : une
        décision humaine ne se réécrit pas parce qu'un second chemin l'a
        redemandée.
        """
        self._rendues.setdefault(cle, issue)

    async def tranche(self, cle: str, soumettre: Callable[[], Awaitable[Issue]]) -> Issue:
        """Rend l'issue de cet acte : déjà connue, déjà en vol, ou soumise maintenant.

        `soumettre` n'est appelé que dans le troisième cas — c'est lui qui compose
        la `DemandeValidation` et la porte au validateur. Les deux premiers cas
        n'écrivent rien nulle part : ils *retrouvent*.

        L'attente partagée est **protégée** (`asyncio.shield`) : l'appelant qui
        renonce est le hook, qui cesse d'attendre à sa borne sans jamais annuler
        la demande (#583). Sans cette protection, le premier appelant qui renonce
        emporterait avec lui la décision de tous les autres — et il renonce par
        construction.

        La mise en mémoire est posée **à la création** de la demande et non au
        retour de l'attente, et c'est là que se joue le critère : au retour, il
        n'y a par définition plus personne pour une décision tardive. Le relais
        (`_range`) est accroché sur la demande elle-même, donc il reçoit son
        issue que quelqu'un l'attende encore ou non.
        """
        rendue = self._rendues.get(cle)
        if rendue is not None:
            return rendue
        en_vol = self._en_vol.get(cle)
        if en_vol is None:
            en_vol = asyncio.ensure_future(soumettre())
            self._en_vol[cle] = en_vol
            en_vol.add_done_callback(lambda finie: self._range(cle, finie))
        return await asyncio.shield(en_vol)

    def _range(self, cle: str, finie: asyncio.Future[Issue]) -> None:
        """Range l'issue d'une demande soldée — y compris quand plus personne ne l'attend.

        Une **décision** est retenue : c'est elle que l'appel rejoué retrouvera.
        Une demande annulée ou en **panne** n'en est pas une et n'est pas retenue,
        pour que le rejeu la soumette à nouveau plutôt que de rejouer la panne.

        Relever l'exception est ce qui l'**absorbe**, au même titre que
        `_absorbe_arbitrage_tardif` côté fournisseur (#583) et
        `_absorbe_issue_tardive` côté moteur (#64) : sans elle, asyncio
        signalerait une « exception never retrieved » sur une demande dont le
        verdict a déjà été rendu.
        """
        self._en_vol.pop(cle, None)
        if finie.cancelled() or finie.exception() is not None:
            return
        self.retient(cle, finie.result())


@dataclass
class Deliberation:
    """Ce qu'une tâche accumule à force d'attendre des décisions humaines (#584).

    Les deux moitiés voyagent ensemble parce qu'elles ont exactement la même
    portée — une tâche, relances comprises — et le même producteur
    (`LocalExecutor.execute`). Les séparer obligerait à threader deux paramètres
    sur le même chemin pour qu'ils soient créés et jetés au même instant.

    Seul le **crédit** descend jusqu'au fournisseur : la mémoire est affaire de
    demandes et de validateur, deux choses dont la couche de transport n'a pas à
    connaître l'existence.
    """

    credit: CreditArbitrage = field(default_factory=CreditArbitrage)
    memoire: MemoireArbitrage = field(default_factory=MemoireArbitrage)

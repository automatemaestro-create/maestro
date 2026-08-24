"""Le contrat d'hôte de run — lancer, annuler, observer, sans savoir **où** (#442).

Un run lancé depuis la Control Tower s'exécute aujourd'hui en tâche de fond du
process de `maestro-api` : `ServiceExecutions` appelle
`asyncio.get_running_loop().create_task(...)` et garde la tâche dans un dict.
Rien là-dedans n'est faux — c'est le choix assumé du POC (#185) — mais la
frontière y est **implicite**, et c'est ce qui la rend impossible à déplacer :
la connaissance « un run est une `asyncio.Task` de ce process » est répandue
dans cinq endroits du service (le lancement, l'annulation, la fermeture, le
cœur qui bat, la question « ce run est-il en vol ? »).

Ce module ne déplace rien : il **nomme** la frontière. Un hôte de run est ce à
quoi la Control Tower confie un run, et à qui elle ne demande que trois
choses :

- **lancer** un run décrit par un `OrdreRun` ;
- **annuler** un run qu'il porte ;
- **observer** ce qu'il porte encore — un run précis (`en_vol`), ou tous
  (`runs_en_vol`), parce que le cœur (#348) a N runs à faire battre et pas un —
  et, depuis #446, ce qu'il a **vu mourir** (`ramasser`).

Ce quatrième verbe est une observation et non un quatrième pouvoir, et c'est ce
qui le rend sûr : l'hôte rapporte un **fait** — ce process est mort, voici son
code et sa trace —, il ne dit pas ce que ce fait signifie. C'est l'appelant qui
tranche, en confrontant la dépouille à la projection : un hôte qui a publié son
issue avant de partir n'a rien laissé à ramasser, un hôte tué net a laissé un run
`en_cours` que plus personne ne portera. Faire dire à l'hôte « ce run a échoué »
lui demanderait de connaître le statut du run, c'est-à-dire précisément ce que ce
contrat existe pour lui épargner.

**Le contrat ne connaît pas son transport**, et c'est sa seule règle de
conception — la leçon reprise de la veille AionUi (#352) : « lancer un
sous-process » n'est pas un contrat, c'est une implémentation qui s'est donné
le nom de sa mise en œuvre. D'où l'absence, ici, de tout `asyncio`, de tout
Redis, de tout sous-process : un hôte détaché (lot 2, #443), et un jour peut-être
un hôte Temporal, s'y branchent sans que l'appelant change d'une ligne. C'est
aussi pourquoi l'implémentation vit dans un module **séparé**
(`maestro.controltower.hote_en_process`) là où `battement.py` garde les siennes
à côté du contrat : un hôte en process importe `asyncio` dès sa première ligne,
et la propriété qu'on veut tenir se lit alors d'un coup d'œil sur ce fichier-ci.

**L'ordre de lancement est fait de données, jamais d'un travail à exécuter.**
C'est le point qui décide de tout le reste. Un contrat qui prendrait « la
coroutine à dérouler » serait honoré par le seul hôte capable de la partager —
celui du process courant : on ne sérialise pas une fermeture. `OrdreRun` porte
donc ce qu'un lancement *dit* (l'objectif, ses plafonds, son ticket, son projet,
son régime de brief), c'est-à-dire exactement ce que la route a reçu, et rien de
ce qu'il *faut faire* pour l'honorer.

Deux corollaires, dont le second n'est pas évident :

- les **garde-fous** (`Guardrails`) ne voyagent pas entiers. Ils mêlent un
  réglage du lancement — les plafonds, qui sont des nombres — et un câblage de
  déploiement — le validateur humain, branché sur le bus de *cette* app. Seuls
  les nombres entrent dans l'ordre ; le validateur se construit là où le run se
  déroule. C'est le partage déjà fait pour le brief (#320) entre le **mode**
  (choix du lancement) et l'**arbitre** (câblage de déploiement) ;
- `fermer` n'est **pas** « annuler tout ». C'est « l'API se retire » — et ce qu'il
  advient des runs est la propriété qui distingue les hôtes : celui en process
  les annule, faute de pouvoir leur survivre ; un hôte détaché les laisse vivre,
  et c'est tout l'objet du chantier #441. La méthode est abstraite pour cette
  raison précise : un no-op par défaut ferait du choix le plus important du
  chantier un oubli silencieux.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from maestro.engine.brief import MODE_BRIEF_HUMAIN
from maestro.references import ReferenceTicket

#: Les hôtes que le dépôt sait construire, par leur **nom** — le vocabulaire du
#: réglage de déploiement `MAESTRO_HOTE_RUN` (#443), résolu par
#: `create_default_app`. Les noms vivent ici, avec le contrat, et non dans les
#: modules d'implémentation : les nommer suppose seulement qu'ils existent, alors
#: qu'aller les chercher ferait importer un sous-process (et demain un client
#: Temporal) à toute app qui n'en veut pas.
#:
#: Depuis #446, `detache` est le **défaut** du déploiement — `process` reste
#: disponible, et le nommer est un choix, plus un silence. Le défaut de
#: *construction* (`ServiceExecutions` sans hôte) reste l'hôte en process pour une
#: raison qui n'est pas un reste : c'est le seul à qui l'on puisse passer une
#: coroutine, donc le seul qu'une app puisse se donner sans process à fabriquer.
HOTE_RUN_EN_PROCESS = "process"
HOTE_RUN_DETACHE = "detache"
HOTES_RUN: tuple[str, ...] = (HOTE_RUN_EN_PROCESS, HOTE_RUN_DETACHE)


@dataclass(frozen=True, slots=True)
class HoteMort:
    """Un hôte que l'appelant a vu mourir, avec ce qu'on sait d'elle (#446).

    Le rendu de `ramasser` — un **constat**, jamais un verdict : `run_id` désigne
    le run qui y vivait, `cause` dit ce que l'hôte a pu en apprendre (code de
    sortie, dernières lignes de son journal, chemin de ce journal), dans la forme
    exacte où elle atterrira dans le `detail` du run.

    Elle ne porte **pas** de statut, et c'est le point : « ce process est mort » ne
    dit pas « ce run a échoué ». Un hôte qui vient de publier son issue meurt
    aussi, et son run n'a rien à ramasser — seul l'appelant, qui lit la projection,
    peut faire la différence.
    """

    run_id: str
    cause: str


class DemarrageHoteRate(RuntimeError):
    """L'hôte n'a pas réussi à **partir** — la seule panne que `lancer` remonte (#443).

    Un hôte en process ne peut pas rater son départ : créer une `asyncio.Task`
    n'échoue pas. Un hôte qui doit fabriquer quelque chose — un process, demain un
    workflow — le peut, et c'est le prix que la veille AionUi conseille de garder
    (docs/28 §7) : « on peut rater un démarrage ».

    C'est une panne du **lancement**, jamais du run : elle dit que rien n'est
    parti, donc que plus rien ne viendra — ni événement, ni battement, ni statut
    de fin. D'où l'exception plutôt qu'un statut publié par l'hôte : à cet
    instant, l'appelant est encore là et il est le seul à pouvoir écrire. Il en
    fait un run **soldé** avec sa cause (`ServiceExecutions.lancer`) au lieu d'un
    run laissé `en_cours` que seul le seuil d'orphelinat viendrait éteindre, une
    demi-heure plus tard.

    Le message porte la cause telle qu'on la connaît — code de sortie, dernières
    lignes du journal de l'hôte, chemin de ce journal : c'est ce qui atterrit dans
    le `detail` du run, donc sous les yeux de quelqu'un.
    """


@dataclass(frozen=True, slots=True)
class OrdreRun:
    """Ce qu'un lancement **dit** — l'ordre confié à un hôte, sans son exécution.

    Champ pour champ, ce que `POST /api/executions` a reçu et validé, une fois le
    `run_id` tiré : l'objectif, les plafonds du lancement (#9), le ticket dont le
    run part (#187), le projet dans lequel il travaille (#222) et le régime de son
    brief (#320). Rien d'autre — ni bus, ni journal, ni moteur : ce sont des
    rouages de l'hôte, pas des propriétés du run.

    **Des données, et rien que des données.** C'est ce qui permet à un hôte de
    porter l'ordre ailleurs que dans la mémoire de l'appelant : chaque champ est
    un scalaire ou une référence qui sait se réémettre en dict
    (`ReferenceTicket.to_dict`). La **forme** sérialisée, elle, n'est pas ici :
    elle appartient au transport qui en aura besoin (le lot 2 et sa ligne de
    commande), et la figer d'avance reviendrait à faire entrer dans le contrat ce
    qu'il existe pour ignorer.

    Les plafonds sont supposés **déjà validés** (`> 0` ou `None`) : l'ordre est
    construit après les refus du service, jamais à leur place — un ordre qui
    refuserait serait un second endroit où le message d'erreur s'écrit.
    """

    run_id: str
    objectif: str
    plafond_cout_usd: float | None = None
    plafond_tokens: int | None = None
    timeout_tache_s: float | None = None
    parallelisme: int | None = None
    ticket: ReferenceTicket | None = None
    projet_id: str | None = None
    mode_brief: str = MODE_BRIEF_HUMAIN


class HoteRun(ABC):
    """L'hôte d'un run : ce à quoi la Control Tower confie une exécution (#442).

    Trois verbes, et le module explique pourquoi ce sont ceux-là. `lancer` confie
    un `OrdreRun` ; `annuler` interrompt ce que l'hôte porte ; `en_vol` et
    `runs_en_vol` **observent** — respectivement pour répondre d'un run et pour
    faire battre le cœur de tous (#348). `fermer` dit que l'appelant se retire, ce
    qui n'est pas la même chose qu'annuler (cf. l'en-tête du module).

    Même forme de contrat que `RegistreBattements`, `EventBus` ou `EventLog` : une
    classe abstraite, une implémentation par frontière. À une différence près, et
    elle est le chantier : les autres ont deux implémentations *équivalentes*
    (mémoire / Redis) là où deux hôtes n'offrent délibérément pas les mêmes
    garanties de survie. Ce que l'appelant peut en supposer se lit donc ici, et
    nulle part ailleurs : un run confié à un hôte lui appartient — l'appelant ne
    tient plus ni tâche, ni process, ni identifiant système.

    Les délais (`delai_s`) sont **passés**, jamais choisis par l'hôte : combien de
    temps on attend l'extinction d'un run est une décision de l'appelant (celui
    qui a une requête HTTP à rendre), là où l'hôte ne sait que l'exécuter. Le
    contrat n'en propose donc aucun défaut.
    """

    @abstractmethod
    async def lancer(self, ordre: OrdreRun) -> None:
        """Confie `ordre` à l'hôte — rend la main **sans attendre** la fin du run.

        Rendre la main tout de suite est un point du contrat, pas une commodité
        d'implémentation : la route de lancement répond avec le résumé du run
        (donc son `run_id`) pendant que celui-ci démarre. Un hôte qui aurait
        besoin d'attendre — le temps de créer un process, par exemple — attend son
        *démarrage*, jamais son issue.

        Ce qui arrive ensuite au run ne remonte pas par un retour : il se lit dans
        son **statut**, publié sur le bus et projeté par la Control Tower, seul
        canal qui survive déjà au redémarrage de l'API (#97).

        **Une seule exception à cette règle, et c'est le démarrage** (#443) : un
        hôte qui n'a pas réussi à partir lève `DemarrageHoteRate`. Rien ne partira
        alors sur aucun canal — pas même le statut — donc le seul moment où la
        panne est dicible est celui-ci, tant que l'appelant est encore là pour
        l'écrire.
        """
        raise NotImplementedError

    @abstractmethod
    async def annuler(self, run_id: str, *, delai_s: float) -> bool:
        """Interrompt le run `run_id` s'il est porté ici — **True** s'il l'était.

        Attend au plus `delai_s` que le run s'éteigne : un run qui avale son
        annulation ne doit pas suspendre l'appelant (même parti pris que
        `maestro.engine.runner`). Le retour dit seulement si cet hôte portait le
        run, jamais si le run a fini de mourir dans le délai — l'issue, elle, a
        déjà été consignée par l'appelant, qui la tient pour acquise.

        **False n'est pas un échec** : c'est le cas normal d'un run orphelin, dont
        l'hôte est justement tombé. Rien à interrompre, rien à signaler.
        """
        raise NotImplementedError

    @abstractmethod
    def en_vol(self, run_id: str) -> bool:
        """Cet hôte porte-t-il `run_id`, et le run tourne-t-il encore ?

        Synchrone, à dessein : c'est une question sur ce que l'hôte a en main, pas
        sur l'état du run — celui-là se lit dans la projection, qui connaît aussi
        les runs que personne ne porte plus. Un hôte qui devrait aller sur le
        réseau pour répondre rendrait cette question coûteuse là où elle est posée
        à chaque tour d'horloge.
        """
        raise NotImplementedError

    @abstractmethod
    def runs_en_vol(self) -> tuple[str, ...]:
        """Les runs que cet hôte porte encore — l'observation en **un** appel.

        Le pendant collectif de `en_vol`, et il existe pour un appelant précis : le
        cœur du service (#348), qui pose un battement par run en vol à chaque
        période. Le faire à coups de `en_vol` supposerait de connaître d'avance la
        liste des runs, c'est-à-dire de tenir le registre que ce contrat existe
        pour reprendre.
        """
        raise NotImplementedError

    def ramasser(self) -> tuple[HoteMort, ...]:
        """Les hôtes morts **depuis le dernier appel** — vides par défaut (#446).

        L'observation qui manquait : `runs_en_vol` dit ce qui vit, et ne dit donc
        rien de ce qui vient de cesser. Un hôte qui fabrique quelque chose peut
        voir ce quelque chose mourir sans un mot — process tué, machine qui
        s'endort, panne au milieu d'un run — et le run reste alors `en_cours` dans
        la projection jusqu'à ce que le seuil d'orphelinat l'y laisse pour de bon.

        Chaque mort n'est rendue **qu'une fois** : l'appelant en fait un run soldé,
        et la redire ferait réécrire l'issue d'un run à chaque tour d'horloge.

        Concret et non abstrait, contrairement à `fermer`, parce qu'ici le no-op a
        un sens plein et le silence ne cache aucune décision : un hôte dont les
        runs consignent eux-mêmes leur issue — celui en process *est* le run —
        n'a jamais rien à ramasser. Même parti pris que `RegistreBattements.close`.
        """
        return ()

    @abstractmethod
    async def fermer(self, *, delai_s: float) -> None:
        """L'appelant se retire : à l'hôte de dire ce qu'il advient de ses runs.

        Ce n'est **pas** « annuler tout », et c'est la méthode par laquelle deux
        hôtes diffèrent le plus : un hôte en process annule (il ne peut pas
        survivre à l'arrêt de l'API), un hôte détaché ne fait rien (ses runs
        continuent, ce qui est tout l'objet de #441). `delai_s` borne l'attente de
        celui qui a quelque chose à éteindre ; un hôte qui n'éteint rien l'ignore.

        Abstraite plutôt que no-op : un défaut silencieux ferait du choix le plus
        important du chantier quelque chose qu'on peut oublier d'écrire.
        """
        raise NotImplementedError

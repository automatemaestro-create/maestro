"""L'hôte détaché : le run vit dans un process que l'API ne possède pas (#443).

L'implémentation de `HoteRun` qui **survit à son lanceur**. Arrêter `maestro-api`
— fermer la fenêtre du navigateur (le chien de garde #149 arrête l'API avec
elle), relancer après une modification, jouer `start.sh --stop` — n'arrête plus
le run : c'est la panne de chaque heure de développement, et docs/28 §5 la donne
comme la première raison d'écrire ce module.

Le patron n'est pas neuf ici, il est en production depuis des mois :
`scripts/orchestrate/run.sh --detach` (#173) lance son pilote dans une console
indépendante pour la raison exacte qui vaut ici — *un pilote qui vit dans ce
qu'il pilote meurt avec*. Et le prix qu'AionUi paie pour la même décision (#352)
n'est pas le nôtre : même interpréteur, même `.venv`, même machine, aucun binaire
à résoudre par plateforme. Ce qu'il faut garder de leur expérience est ailleurs,
et c'est le troisième critère du ticket : **on peut rater un démarrage**.

**Les deux côtés de la frontière vivent ici**, et c'est délibéré : `HoteRunDetache`
sérialise l'ordre, `main` le relit. Une forme sérialisée écrite à un endroit et
relue à un autre est une forme que rien n'oblige à rester d'accord avec elle-même
— c'est le seul couplage réel du module, autant qu'il tienne sur un écran. Le
contrat, lui, ne connaît toujours pas ce transport : `OrdreRun` ne fige aucune
sérialisation (`hote.py`), et c'est ce qui laisse `ordre_vers_dict` être un détail
d'ici plutôt qu'un point du contrat.

**Ce qui traverse.** L'ordre — objectif, plafonds, `ticket`, `projet_id`,
`mode_brief` — et rien d'autre. Les **sources** sont absentes parce qu'elles sont
déjà **résolues** : la Control Tower les canonicalise, les plafonne et copie les
octets téléversés vers l'emplacement d'ingestion du run **avant** le lancement
(#315/#317) ; ce qui atteint le process est un objectif, et le disque a déjà la
matière. Le `projet_id` est le **prérequis commun** que ce lot paie pour tout hôte
qui n'est pas l'API (docs/28 §3) : sans lui, `espace_de_travail(None)` retombe sur
un `mkdtemp()` et le livrable n'atteint jamais le projet.

**Ce que le process fait de son côté** est, au geste près, ce que fait déjà
`maestro-run --publier` : il publie ses étapes sur le **même** Redis par le pont
télémétrie (#46), journalise au même `RunJournal` — sous le `run_id` que l'API a
tiré, sans quoi ses étapes rejoindraient un autre run — et **bat son cœur**
(#348, `CoeurRun` + `batteur_redis`). Rien de nouveau côté observation : c'est le
canal de toujours, et c'est ce qui fait qu'une API redémarrée le retrouve
`vivant` sans rien avoir gardé en mémoire.

**L'annulation traverse la frontière par le bus** (#444). Le process n'est pas
seulement un émetteur : il **écoute**. Il s'abonne au canal d'événements avant de
se déclarer armé, guette l'issue `annulee` de **son** run — celle que
`ServiceExecutions._solder` consigne, donc publie, *avant* de demander
l'interruption, parce qu'une annulation est une décision humaine et non le verdict
de l'exécution — et annule alors sa propre tâche `asyncio`. `Task.cancel` reste
donc le mécanisme réel, à un aller Redis près : c'est la propriété que le POC
avait protégée en choisissant la tâche de fond (#185), et que docs/28 §4.3 refuse
de repayer.

Trois conséquences qu'il vaut mieux lire ici que découvrir :

- **l'ordre ne dépend d'aucun process.** Une API redémarrée ne tient plus le
  `Popen` du run, mais elle consigne toujours son issue sur le même Redis : le
  process l'entend et s'arrête. C'est l'autre moitié du trou que ce module
  laissait ouvert, et le `False` que rend `annuler` ne veut plus dire « rien ne
  l'interrompra » ;
- **l'issue *est* l'ordre** — aucun second événement n'est publié, et ce n'est pas
  une économie de canal. Deux façons de dire un même fait finissent par se
  séparer, l'une émise sans l'autre, et l'on aurait alors un run soldé qui
  travaille encore ; là où « ce run est soldé annulé » a pour conséquence
  **nécessaire** « ce run doit cesser de travailler ». C'est la raison inverse de
  celle qui donne au brief son canal propre (#320) : sa décision, elle, dit autre
  chose que l'état du run ;
- **le repli franc reste**, et c'est lui qui rend la course inoffensive. Entre le
  témoin posé et l'abonnement Redis effectif, un ordre peut passer sans être vu ;
  le lanceur attend l'extinction, puis éteint le groupe de process. Un ordre
  manqué coûte donc une mort brutale, jamais un run qui continue.

**Les trois attentes humaines traversent la même frontière, par le même bus**
(#445) : la décision sur le brief (#320), les réponses de clarification (#321) et
la validation d'une action sensible (#9/#48). C'est le lot le moins cher du
chantier, et c'est une des raisons qui ont fait retenir l'hôte détaché plutôt
qu'un workflow durable (docs/28 §4.3) : les trois arbitres attendaient **déjà**
sur un abonnement au bus, et le bus avait **déjà** une implémentation
inter-process. Il n'y avait donc rien à réinventer, seulement à brancher —
`ArbitreBriefControlTower`, `ArbitreClarificationControlTower` et
`ValidateurControlTower`, sur le bus de *ce* process, au geste près comme
`ServiceExecutions._derouler` les branche sur celui de l'API.

**Un seul bus les sert tous les quatre**, guet d'annulation compris, et ce n'est
pas une économie de style : `RedisEventBus.subscribe` ouvre un `pubsub` par appel
sur un client partagé, si bien que quatre bus coûteraient quatre connexions là où
une suffit — dans un process qui vit des heures, c'est exactement la fuite que
`_observer_annulation` refusait déjà pour son propre compte. C'est aussi la forme
de l'API, qui n'a jamais eu qu'un `self._bus`. Les fabriques `arbitre_brief_redis`
/ `validateur_redis` en ouvrent un chacune parce que leur appelant (`maestro-run`)
n'en a aucun sous la main ; ce n'est pas notre cas.

**Le fail-safe est la partie qui compte, et il n'est pas réécrit ici : il est
hérité**, deux fois plutôt qu'une. Un bus refermé sans décision fait **lever**
l'attente du brief et celle des clarifications (`brief.py`) — donc échouer le run,
sans qu'aucune tâche ait été créée —, et fait **refuser** l'action sensible
(`Guardrails.demande_validation`) — donc solder la tâche. Aucune des deux ne rend
d'approbation par défaut, que le bus tombe, se referme ou n'ait jamais répondu.

Ce que ce lot ajoute est **en amont de ces deux-là**, et c'est le seul cas neuf :
un bus qu'on n'a pas pu **construire** ne câble rien du tout — pas d'arbitre, pas
de validateur — et les deux mêmes fail-safes retombent alors d'eux-mêmes sur le
bon comportement, le moteur refusant un mode « humain » sans arbitre *avant le
premier appel modèle* (`loop.py`) et les garde-fous refusant sans validateur.
Inventer ici un troisième refus n'aurait fait qu'ajouter un endroit de plus où la
règle peut diverger de celle qu'elle recopie.

**Une propriété reste explicitement hors de ce lot**, et il vaut mieux la lire ici
que la découvrir : l'hôte **ne publie pas son issue** en partant, comme
`--publier` ne le fait pas aujourd'hui — un run détaché terminé normalement finira
donc `orphelin`, le verdict portant sur son hôte (« plus personne ne veille ») et
jamais sur son travail (`battement.py`, corollaire assumé). C'est le lot 5 (#446),
et le corollaire vaut aussi pour un run que le fail-safe fait échouer : le process
sort en 1, sa cause est au journal de l'hôte, et personne ne la publie encore.

Le mode reste **opt-in** (`MAESTRO_HOTE_RUN=detache`, résolu par
`create_default_app`) : le défaut demeure la tâche de fond de l'API jusqu'au
lot 5.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

from maestro.controltower.events import (
    EVENEMENT_EXECUTION_STATUT,
    EventBus,
    RedisEventBus,
)
from maestro.controltower.hote import DemarrageHoteRate, HoteRun, OrdreRun
from maestro.controltower.state import EXECUTION_ANNULEE
from maestro.references import ReferenceTicket

if TYPE_CHECKING:
    from maestro.engine.loop import RunReport

#: Le module que le process fils exécute (`python -m …`). Un **module** et non un
#: script installé (`[project.scripts]`) : `sys.executable -m` désigne le même
#: interpréteur et le même `.venv` que l'API, sans dépendre d'une réinstallation
#: du paquet — c'est la moitié de ce qui nous dispense du chantier de résolution
#: de binaire d'AionUi (docs/28 §7). Le nom est écrit en toutes lettres parce que
#: le fils, lancé par `-m`, s'appelle `__main__` : il ne peut pas le rendre.
MODULE_HOTE = "maestro.controltower.hote_detache"

#: L'ordre confié au process, dans l'**atelier** du run. Écrit par le lanceur,
#: relu **puis effacé** par le fils : il porte l'objectif, c'est-à-dire du texte
#: libre écrit par un humain, où un secret collé est plausible (même raison que
#: `redact_secrets`).
FICHIER_ORDRE = "ordre.json"

#: Le journal du process (stdout **et** stderr) — la seule trace d'un hôte qui n'a
#: pas de console. C'est là que le lanceur va chercher la **cause** d'un démarrage
#: raté, et le chemin voyage avec elle pour qu'on puisse lire le reste.
FICHIER_JOURNAL = "hote.log"

#: Le témoin de démarrage, posé par le fils **une fois armé** — publication
#: branchée, cœur battant. Sa seule raison d'être est de rendre l'attente du
#: lanceur *courte* : sans lui, celui-ci devrait dormir un délai fixe à chaque
#: lancement pour savoir si le process a tenu, et payer sur la requête HTTP de
#: chaque run le prix du run qui rate.
FICHIER_PRET = "pret"

#: Plafond de l'attente de démarrage, en secondes. Le cas courant ne le voit
#: jamais — il rend la main sur le témoin — et il est **généreux à dessein** :
#: l'atteindre fait tenir pour parti un process qui ne l'est peut-être pas, et
#: c'est alors le seuil d'orphelinat (trente minutes) qui éteindra le run.
#: L'attendre plus longtemps ne coûte, en face, que sur un process vivant qui
#: tarde à s'armer — c'est-à-dire précisément le cas où attendre est juste.
#:
#: Mesuré le 2026-08-24 sur le poste de référence (Windows) : **1,3 s** pour
#: s'armer quand Redis répond, **5,5 s** quand il ne répond pas — le premier
#: battement est synchrone (#348) et une connexion refusée coûte ses quatre
#: secondes de tentatives —, plus la création du process. Trente secondes laissent
#: donc le triple de marge au pire cas observé.
DELAI_DEMARRAGE_S = 30.0

#: Pas de la boucle d'attente du démarrage. Assez fin pour que le cas courant
#: rende la main aussitôt, assez large pour ne pas faire d'un lancement une
#: rafale de `stat()`.
PAS_DEMARRAGE_S = 0.05

#: Ce qu'on retient du journal pour nommer la cause d'un démarrage raté : les
#: dernières lignes, jamais le tout. La dernière ligne d'une trace Python est
#: celle qui dit *quoi* (`ModuleNotFoundError: …`) ; les précédentes disent *où*.
#: Au-delà, on ne remplit plus qu'un champ que personne ne lira jusqu'au bout.
LIGNES_CAUSE = 5
LONGUEUR_CAUSE = 800

#: Temps laissé à la tâche du run pour **dérouler** son annulation, une fois
#: l'ordre reçu (#444). Au-delà, elle est abandonnée : même parti pris que
#: `maestro.engine.runner` et que `HoteRunEnProcess.annuler` — une réalisation qui
#: avale son annulation ne doit suspendre personne. Ce qui traîne encore sera de
#: toute façon repris par la fermeture bornée de `run_borne`, puis, s'il le faut,
#: par le repli franc du lanceur : trois filets pour la même obstination.
DELAI_EXTINCTION_S = 5.0

#: Code de sortie d'un run **annulé** — 128 + SIGINT, la convention shell de
#: « interrompu ». Ni 0 ni 1, et c'est le point : le dépôt refuse ailleurs de
#: confondre une annulation avec un échec (`_solder` consigne « annulee » et non
#: « echec » précisément parce que rien n'a raté), et un journal d'hôte qui rendrait
#: 1 ferait chercher une panne là où quelqu'un a simplement dit stop.
CODE_ANNULE = 130

_USAGE = f"Usage : python -m {MODULE_HOTE} <atelier du run>"


class RunAnnule(RuntimeError):
    """Le run a été interrompu depuis la Control Tower — ni panne, ni échec (#444).

    Levée par `_derouler` quand l'ordre d'annulation a été vu sur le bus et que la
    tâche du run a été annulée. C'est une exception et non un statut rendu parce
    qu'elle doit **court-circuiter** le chemin nominal : il n'y a pas de rapport à
    imprimer, pas de synthèse à rendre, et le seul geste qui reste est de sortir.

    Son issue, elle, n'est pas ici : elle a été consignée par l'API *avant* que
    l'ordre ne parte (`ServiceExecutions._solder`), et la republier ferait dire deux
    fois un fait déjà acquis.
    """


# --------------------------------------------------------------------- transport


def ordre_vers_dict(ordre: OrdreRun) -> dict[str, Any]:
    """La forme sérialisée d'un ordre — JSON pur, celle que le process relit.

    Elle vit ici et non dans `hote.py` parce qu'elle appartient à **ce**
    transport : un hôte Temporal passerait les mêmes champs par un argument de
    workflow, et figer une sérialisation dans le contrat ferait entrer dedans ce
    qu'il existe pour ignorer (cf. l'en-tête de `hote.py`).

    Champ pour champ ceux d'`OrdreRun`, `ticket` par sa propre réémission
    (`ReferenceTicket.to_dict`) : rien n'est aplati, rien n'est renommé — un ordre
    relu doit être *le même*, et le seul moyen de s'en assurer est que la lecture
    n'ait rien à deviner.
    """
    return {
        "run_id": ordre.run_id,
        "objectif": ordre.objectif,
        "plafond_cout_usd": ordre.plafond_cout_usd,
        "plafond_tokens": ordre.plafond_tokens,
        "timeout_tache_s": ordre.timeout_tache_s,
        "parallelisme": ordre.parallelisme,
        "ticket": None if ordre.ticket is None else ordre.ticket.to_dict(),
        "projet_id": ordre.projet_id,
        "mode_brief": ordre.mode_brief,
    }


def ordre_depuis_dict(data: Mapping[str, Any]) -> OrdreRun:
    """Reconstruit l'ordre depuis sa forme sérialisée — lève sur ce qui manque.

    Le pendant de `ordre_vers_dict`, et **strict** sur les deux seuls champs sans
    défaut possible : un `run_id` absent rattacherait les étapes du run à un autre
    (ou à aucun), un objectif vide n'a rien à orchestrer. Tout le reste a un défaut
    au contrat, donc une clé manquante y retombe.

    Les plafonds sont relus sans être revalidés : ils l'ont été par le service
    avant que l'ordre n'existe, et `Guardrails` refuserait de toute façon une
    valeur ≤ 0. Redire le refus ici en ferait un second endroit où le message
    s'écrit.
    """
    run_id = str(data.get("run_id") or "").strip()
    if not run_id:
        raise ValueError("ordre sans run_id : ses étapes ne rejoindraient aucun run.")
    objectif = str(data.get("objectif") or "").strip()
    if not objectif:
        raise ValueError(f"ordre {run_id} sans objectif : il n'y a rien à orchestrer.")
    defaut = OrdreRun(run_id=run_id, objectif=objectif)
    return OrdreRun(
        run_id=run_id,
        objectif=objectif,
        plafond_cout_usd=_nombre(data.get("plafond_cout_usd")),
        plafond_tokens=_entier(data.get("plafond_tokens")),
        timeout_tache_s=_nombre(data.get("timeout_tache_s")),
        parallelisme=_entier(data.get("parallelisme")),
        ticket=ReferenceTicket.depuis(data.get("ticket")),
        projet_id=str(data.get("projet_id") or "").strip() or None,
        mode_brief=str(data.get("mode_brief") or "").strip() or defaut.mode_brief,
    )


def _nombre(valeur: Any) -> float | None:
    """Relit un plafond flottant — None sur l'absence comme sur l'illisible."""
    if valeur is None:
        return None
    try:
        return float(valeur)
    except (TypeError, ValueError):
        return None


def _entier(valeur: Any) -> int | None:
    """Relit un plafond entier — None sur l'absence comme sur l'illisible."""
    nombre = _nombre(valeur)
    return None if nombre is None else int(nombre)


# ------------------------------------------------------------------- le lanceur


class HoteRunDetache(HoteRun):
    """Chaque run part dans son propre process, que l'arrêt de l'API n'emporte pas.

    `python` est l'interpréteur du process fils — celui de l'API par défaut
    (`sys.executable`), donc le même `.venv`, ce qui est toute la raison pour
    laquelle ce lot est bon marché. `atelier` est la racine sous laquelle chaque
    run reçoit son dossier de travail (ordre, journal, témoin) ; par défaut un
    dossier temporaire par run, parce que **personne n'est invité à le lire** — sa
    seule sortie utile, la cause d'un démarrage raté, remonte dans le statut du
    run. `delai_demarrage_s` borne l'attente du départ (cf. `DELAI_DEMARRAGE_S`).

    L'hôte garde les `Popen` des runs qu'il a lancés, et **c'est tout ce qu'il
    tient** : la question « ce run vit-il ? » se répond par `poll()`, sans réseau,
    ce qu'exige un `en_vol` synchrone appelé à chaque tour d'horloge du cœur. Ce
    registre est celui d'un process, pas celui des runs : une API qui redémarre le
    perd et ne prétend plus rien porter — les runs, eux, continuent de battre pour
    leur compte, et c'est ainsi qu'ils ressortent `vivant`.
    """

    def __init__(
        self,
        *,
        python: str | None = None,
        atelier: Path | None = None,
        delai_demarrage_s: float = DELAI_DEMARRAGE_S,
    ) -> None:
        self._python = python or sys.executable
        self._atelier = atelier
        self._delai = delai_demarrage_s
        self._process: dict[str, subprocess.Popen[bytes]] = {}

    async def lancer(self, ordre: OrdreRun) -> None:
        """Ouvre le process du run et **attend son démarrage**, jamais son issue.

        Deux temps. On **écrit** l'ordre dans l'atelier du run et on lance le
        process, détaché de la console et du groupe de l'API. On **regarde** ensuite
        s'il a tenu — témoin posé, ou process déjà mort.

        Il y en avait trois jusqu'à #445 : un **refus** du brief `humain` ouvrait la
        méthode, parce que la décision n'avait alors aucun canal jusqu'ici et qu'un
        run parti dans ces conditions serait resté suspendu pour toujours. Le canal
        existe (cf. l'en-tête du module), donc le refus a disparu — et **aucun mode
        de brief n'est plus refusé au lancement**. Ce qui manquerait pour trancher
        se dit désormais là où on s'en aperçoit vraiment, c'est-à-dire dans le
        process : sans arbitre, le moteur refuse le mode « humain » avant le premier
        appel modèle.

        Lève `DemarrageHoteRate` dans les deux cas de non-départ (`Popen` en échec,
        process mort aussitôt) ; l'appelant en fait un run soldé avec sa cause. Un
        process **vivant mais lent** au-delà du plafond est tenu pour parti : on ne
        tue pas ce qui n'a rien fait de mal, et l'orphelinat (#348) reste le filet
        de ce qui mourra plus tard.
        """
        atelier = self._ouvrir_atelier(ordre.run_id)
        journal = atelier / FICHIER_JOURNAL
        (atelier / FICHIER_ORDRE).write_text(
            json.dumps(ordre_vers_dict(ordre), ensure_ascii=False),
            encoding="utf-8",
        )
        try:
            process = await asyncio.to_thread(self._ouvrir_process, atelier, journal)
        except OSError as exc:
            raise DemarrageHoteRate(
                f"l'hôte détaché du run {ordre.run_id} n'a pas pu être lancé "
                f"({self._python} -m {MODULE_HOTE}) : {type(exc).__name__} — {exc}"
            ) from exc
        self._process[ordre.run_id] = process
        await self._attendre_demarrage(ordre.run_id, process, atelier, journal)

    async def annuler(self, run_id: str, *, delai_s: float) -> bool:
        """Laisse le run s'éteindre de lui-même, puis l'achève s'il s'obstine (#444).

        Le geste **gracieux ne s'écrit pas ici**, et c'est tout le lot 3 : il est
        déjà parti quand on arrive. L'issue que `ServiceExecutions._solder`
        consigne avant d'appeler cette méthode est publiée sur le bus, le process
        la guette (`_observer_annulation`), annule sa propre tâche `asyncio` et
        déroule ses tâches proprement. Ce qui reste à faire ici est de **lui en
        laisser le temps** — borné par `delai_s`, jamais plus.

        Passé ce délai, le repli de #443 : on éteint l'hôte **et sa descendance**
        (`_eteindre`). Ce n'est pas l'aveu d'un geste raté — un process qui a
        entendu l'ordre a déjà annulé ses tâches et signalé ses sous-process quand
        on l'achève, si bien que ce qu'on interrompt alors est une fermeture de
        boucle et non du travail. Ce qu'on refuse, c'est qu'un run **soldé**
        continue de coûter parce que personne n'écoutait.

        L'attente est le seul endroit borné ; le repli, lui, n'attend rien. Il n'y
        a pas d'issue à observer après avoir tué un groupe de process, et
        `runs_en_vol` ramasse la dépouille au tour d'horloge suivant : c'est ce qui
        fait qu'un hôte muet ne suspend pas la requête au-delà de ce que l'appelant
        a accordé.

        `False` garde son sens de contrat — ce process ne porte pas le run — mais
        plus sa conclusion. Après un redémarrage de l'API, le run n'est plus tenu
        ici et s'arrête **quand même** : l'ordre voyage par le bus, qui ne dépend
        d'aucun process. C'est la moitié du trou que ce module laissait ouverte, et
        c'est aussi pourquoi rien n'est publié depuis ici — l'appelant l'a déjà
        fait, pour tous les hôtes à la fois.
        """
        process = self._process.get(run_id)
        if process is None or process.poll() is not None:
            self._process.pop(run_id, None)
            return False
        if await asyncio.to_thread(self._attendre_extinction, process, delai_s):
            return True
        await asyncio.to_thread(_eteindre, process)
        return True

    def en_vol(self, run_id: str) -> bool:
        """Ce process porte-t-il `run_id`, et son hôte tourne-t-il encore ?

        `poll()` et rien d'autre : la réponse est locale, donc gratuite, ce qu'exige
        une question posée à chaque battement. Elle ne dit pas « ce run vit » mais
        « **je** le porte encore » — après un redémarrage de l'API la réponse est
        non pour tout, et c'est le registre des battements qui sait le reste.
        """
        process = self._process.get(run_id)
        return process is not None and process.poll() is None

    def runs_en_vol(self) -> tuple[str, ...]:
        """Les runs dont le process tourne encore, dans l'ordre de leur lancement.

        Le parcours **ramasse** au passage les process éteints : c'est le pendant
        de l'`add_done_callback` de l'hôte en process — un `Popen` qu'on n'attend
        jamais laisse un zombie sous POSIX, et `poll()` est ce qui le récolte. Le
        cœur appelle cette méthode à chaque période : le ménage se fait donc tout
        seul, sans tâche à lui.
        """
        vivants: list[str] = []
        for run_id, process in list(self._process.items()):
            if process.poll() is None:
                vivants.append(run_id)
            else:
                self._process.pop(run_id, None)
        return tuple(vivants)

    async def fermer(self, *, delai_s: float) -> None:
        """L'API se retire : **aucun run ne s'arrête**. C'est tout l'objet de #441.

        La méthode où deux hôtes disent le contraire l'un de l'autre — celui en
        process annule tout, faute de pouvoir survivre ; celui-ci ne fait rien, et
        ce rien *est* la livraison. Les process gardent leur registre : ils sont
        vivants, ils publient, ils battent, et l'API qui redémarre les retrouve
        `vivant` sans avoir eu à se souvenir d'eux.

        `delai_s` est ignoré, comme le contrat le prévoit : il borne l'attente de
        celui qui a quelque chose à éteindre.
        """

    # ------------------------------------------------------------------ interne

    def _ouvrir_atelier(self, run_id: str) -> Path:
        """Le dossier de travail du run — temporaire par défaut, et c'est voulu.

        Rien de ce qu'il contient n'est fait pour être lu : l'ordre est effacé par
        le fils dès sa lecture, le journal ne sert qu'à nommer une cause qui, elle,
        remonte dans le statut du run. C'est la règle du dépôt sur les deux
        emplacements (docs/10 §8.5) — ce qu'on invite à lire va sous `.maestro/`,
        ce que personne ne lit reste au répertoire temporaire. Le journal y **reste**
        quand même après un démarrage réussi : c'est la seule trace d'un run qui
        mourra plus tard, et le système d'exploitation la ramassera.

        Un atelier **déjà là** est vidé de son témoin, et ce n'est pas du ménage :
        un témoin périmé ferait dire « démarré » d'un process qui n'a pas encore
        ouvert la bouche, c'est-à-dire exactement l'inverse du contrôle qu'il sert.
        Le cas ne se présente pas avec la racine temporaire (un dossier neuf par
        lancement) mais avec une racine imposée, où le nom du dossier est celui du
        run.
        """
        if self._atelier is None:
            return Path(tempfile.mkdtemp(prefix=f"maestro-hote-{run_id}-"))
        atelier = self._atelier / run_id
        atelier.mkdir(parents=True, exist_ok=True)
        (atelier / FICHIER_PRET).unlink(missing_ok=True)
        return atelier

    def _ouvrir_process(self, atelier: Path, journal: Path) -> subprocess.Popen[bytes]:
        """Lance le process, détaché, sa sortie entière dans `journal`.

        `stdin` sur le vide : un hôte sans console ne doit jamais attendre une
        frappe — c'est aussi le fail-safe de la validation console, qui refuse sur
        EOF plutôt que de suspendre. `stderr` fondu dans `stdout` pour que la trace
        d'une panne soit **un** fichier dans l'ordre où elle s'est produite : deux
        flux séparés obligeraient à recoller ce qu'on lit pour diagnostiquer.
        """
        sortie = journal.open("ab")
        try:
            return subprocess.Popen(  # noqa: S603 - argv fixe, aucun shell
                [self._python, "-m", MODULE_HOTE, str(atelier)],
                stdin=subprocess.DEVNULL,
                stdout=sortie,
                stderr=subprocess.STDOUT,
                env=self._environnement(),
                close_fds=True,
                **_detachement(),
            )
        finally:
            # `Popen` a dupliqué le descripteur : le nôtre n'a plus d'usage, et le
            # garder ouvert retiendrait le fichier sous Windows.
            sortie.close()

    @staticmethod
    def _environnement() -> dict[str, str]:
        """L'environnement du fils : celui de l'API, plus de quoi être lisible.

        Hérité — même `.env`, même `REDIS_URL`, même configuration de fournisseur :
        c'est ce qui fait que le run détaché publie sur *le même* Redis sans qu'on
        ait à lui passer une seule adresse.

        Deux réglages s'y ajoutent, tous deux au service du seul fichier que
        quelqu'un lira un jour. `PYTHONUNBUFFERED` d'abord, et il n'est pas
        cosmétique : le lanceur lit ce journal **dans la milliseconde** qui suit la
        mort du process, et un tampon non vidé rendrait « cause inconnue » sur la
        panne même que ce dispositif existe pour nommer. `PYTHONIOENCODING` ensuite,
        pour que la trace soit en UTF-8 et non dans l'encodage d'une console
        héritée — le dépôt a déjà payé ce mojibake une fois (#141). Ni l'un ni
        l'autre n'écrase un choix explicite de l'environnement.
        """
        env = dict(os.environ)
        env.setdefault("PYTHONUNBUFFERED", "1")
        env.setdefault("PYTHONIOENCODING", "utf-8")
        return env

    async def _attendre_demarrage(
        self,
        run_id: str,
        process: subprocess.Popen[bytes],
        atelier: Path,
        journal: Path,
    ) -> None:
        """Attend le témoin du fils, ou sa mort — au plus `self._delai`.

        L'ordre des deux questions est le contenu de la méthode : le **témoin
        d'abord**. Un process qui l'a posé *est* parti, même s'il meurt dans la
        seconde qui suit — cette mort-là est celle d'un run, pas d'un démarrage, et
        elle se lit dans son battement qui vieillit. Interroger `poll()` en premier
        ferait dire « n'a pas démarré » d'un hôte qui avait démarré, publié et
        battu.
        """
        temoin = atelier / FICHIER_PRET
        echeance = time.monotonic() + self._delai
        while True:
            if temoin.exists():
                return
            code = process.poll()
            if code is not None:
                self._process.pop(run_id, None)
                raise DemarrageHoteRate(
                    f"l'hôte détaché du run {run_id} s'est arrêté aussitôt "
                    f"(code {code}) : {_cause(journal)}"
                )
            if time.monotonic() >= echeance:
                return
            await asyncio.sleep(PAS_DEMARRAGE_S)

    @staticmethod
    def _attendre_extinction(process: subprocess.Popen[bytes], delai_s: float) -> bool:
        """Attend la fin du process au plus `delai_s` — **True** s'il s'est éteint.

        L'échéance n'est pas un échec : elle dit seulement que le geste gracieux
        n'a pas abouti dans le temps accordé, et c'est exactement ce qui déclenche
        le repli. Un hôte tombé sans être joignable et un hôte qui prend son temps
        rendent donc la même chose, ce qui est la bonne lecture — on ne sait pas
        lequel des deux on a, et le repli convient aux deux.
        """
        try:
            process.wait(timeout=delai_s)
        except subprocess.TimeoutExpired:
            return False
        return True


def _detachement() -> dict[str, Any]:
    """Ce qui coupe le process fils du cycle de vie de l'API, par plateforme.

    Sous Windows, deux drapeaux et pas un : `DETACHED_PROCESS` lui retire la
    console de l'API, `CREATE_NEW_PROCESS_GROUP` le sort de son groupe — sans le
    second, un Ctrl-C dans la console de l'API emporterait le run, c'est-à-dire la
    panne que ce module supprime. Sous POSIX, `start_new_session` fait les deux
    (nouvelle session, donc plus de terminal de contrôle, donc pas de SIGHUP).

    Les deux font aussi du fils un **chef de groupe**, ce dont `_eteindre` a
    besoin : ce n'est pas un effet de bord, c'est la seconde raison de ces
    drapeaux.

    `CREATE_BREAKAWAY_FROM_JOB` n'y est **pas** : il échoue en `ACCESS_DENIED`
    quand le job de l'appelant n'autorise pas l'évasion, et ferait alors rater
    *tous* les démarrages pour couvrir un cas qui n'est pas le nôtre.

    Le test porte sur `sys.platform` et non sur `os.name` : c'est la forme que
    `mypy` sait restreindre, donc celle qui laisse `DETACHED_PROCESS` — déclaré
    pour Windows seulement — passer le typage joué sous Linux.
    """
    if sys.platform == "win32":
        return {
            "creationflags": (
                subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            )
        }
    return {"start_new_session": True}


def _eteindre(process: subprocess.Popen[bytes]) -> None:
    """Éteint le process du run **et sa descendance** — jamais lui seul.

    La leçon est déjà écrite dans ce dépôt, sur l'arrêt d'un run d'orchestration
    (#291) : *tuer un parent avant ses enfants est ce qui fabrique l'orphelin
    qu'on veut éviter*. Elle vaut mot pour mot ici — un hôte de run est le père
    d'un `claude.exe` par tâche en vol, et un `terminate()` sur lui seul les
    laisserait travailler pour un run déjà soldé, sans que rien ne les nomme.
    Mesuré au premier essai de ce lot : un hôte détaché tenait une descendance de
    cinq process.

    D'où les deux gestes, un par plateforme, tous deux adressés au **groupe** que
    `_detachement` a créé : `taskkill /T` sous Windows, où un groupe de process ne
    reçoit que le Ctrl-C et pas un signal de mort, et `killpg` sous POSIX, où le
    fils est chef de sa session. Le repli sur le seul `terminate()` couvre le cas
    où le groupe s'est déjà défait — mieux vaut éteindre le père que rien.
    """
    if sys.platform == "win32":
        subprocess.run(  # noqa: S603 - argv fixe, aucun shell
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    except (OSError, ProcessLookupError):
        process.terminate()


def _cause(journal: Path, *, lignes: int = LIGNES_CAUSE, longueur: int = LONGUEUR_CAUSE) -> str:
    """La cause d'un démarrage raté, tirée des dernières lignes du journal.

    Les **dernières**, parce que la dernière ligne d'une trace Python est celle qui
    nomme la panne. Le chemin du journal voyage avec, et pas seulement par
    politesse : ce qui est rendu ici atterrit dans le `detail` d'un run, où une
    trace entière n'a pas sa place — mais où « allez voir là » est ce qui manque
    quand cinq lignes ne suffisent pas.
    """
    try:
        texte = journal.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"journal illisible ({journal}) : {exc}"
    utiles = [ligne.strip() for ligne in texte.splitlines() if ligne.strip()]
    if not utiles:
        return f"le process n'a rien écrit (journal vide : {journal})"
    extrait = " | ".join(utiles[-lignes:])
    if len(extrait) > longueur:
        extrait = f"…{extrait[-longueur:]}"
    return f"{extrait} (journal : {journal})"


# ---------------------------------------------------------------------- le fils


def main(argv: Sequence[str] | None = None) -> int:
    """Le process détaché : déroule le run décrit par l'ordre de son atelier.

    Le pendant de `ServiceExecutions._derouler`, de l'autre côté de la frontière —
    et la ressemblance s'arrête où les deux hôtes diffèrent. Ici, pas de bus en
    mémoire ni de projection à écrire : les étapes partent sur Redis par le pont
    télémétrie (#46) et rejoignent la Control Tower par le canal de toujours, celui
    qu'emprunte déjà `maestro-run --publier`.

    L'ordre des six gestes d'armement est le sujet, et chacun a sa raison :

    1. **relire l'ordre**, et l'effacer — il porte l'objectif, donc du texte libre
       où un secret est plausible, et il n'a plus d'usage une fois lu ;
    2. **brancher la publication** avant tout appel modèle, faute de quoi les
       premières étapes du run n'atteindraient personne ;
    3. **battre**, tout de suite et non au premier tour d'horloge : l'étape la plus
       lente d'un run est son cadrage, et c'est celle qui n'aurait aucun signal de
       vie. Et le cœur bat depuis un **fil**, ce qui est ce qui rend une attente
       humaine sûre au sens de #348 : suspendu sur son brief, ce process continue de
       dire qu'il est là, donc « personne n'a encore répondu » reste distinguable de
       « celui qui posait la question est mort » ;
    4. **ouvrir le bus** (#445) — un seul pour les quatre abonnements qui suivent,
       et une ouverture qui ne peut pas échouer bruyamment (`_bus_du_run`) ;
    5. **écouter l'annulation** (#444) : le lanceur rend la main sur le témoin, et
       l'ordre peut suivre à la milliseconde — s'abonner après lui serait s'abonner
       trop tard. Même précaution qu'en #48 et #320 (« s'abonner avant de
       publier »), à une différence près qui compte : ici une course perdue ne coûte
       pas la décision, le repli franc du lanceur restant derrière ;
    6. **poser le témoin** en dernier, parce qu'il ne dit pas « je suis né » mais
       « je suis armé » — c'est sur cette promesse-là que le lanceur rend la main,
       et depuis #444 « armé » veut aussi dire « à l'écoute ».

    Les trois derniers vivent dans `_derouler`, et non ici, pour une raison
    mécanique : s'abonner est **asynchrone**, donc n'a de lieu qu'une fois la boucle
    ouverte. Ce qui reste dans cette fonction est ce qui ne l'est pas.

    Les **imports du moteur sont locaux** aux deux fonctions, et ce n'est pas une
    économie de démarrage : `maestro.controltower` est importé par toute app, et y
    faire entrer `maestro.engine.loop` au niveau du module reviendrait à résoudre
    un fournisseur à la construction de l'API — exactement ce que
    `moteur_par_defaut` évite depuis #185.

    Rend 0 si toutes les tâches réussissent, 1 sinon (ou sur une panne), 2 sur un
    appel mal formé, `CODE_ANNULE` sur une **annulation** — qui n'est ni l'un ni
    l'autre, exactement comme `_solder` solde en « annulee » et non en « echec ».
    Un **brief refusé** (#445) rejoint ce troisième cas et non le deuxième, pour la
    même raison et parce que l'hôte en process en fait déjà un run annulé : rien n'a
    raté, quelqu'un a dit non. **Personne ne lit ce code** — le process n'a pas
    d'appelant — et c'est assumé : ce qui compte est la trace laissée au journal, et
    le statut que l'hôte publiera en partant au lot 5 (#446).
    """
    from maestro.config import ConfigError, load_settings
    from maestro.controltower.battement import CoeurRun, batteur_redis
    from maestro.engine.brief import BriefRefuse
    from maestro.engine.cli import activer_publication_evenements, console_tolerante
    from maestro.engine.runner import run_borne
    from maestro.orchestrator.errors import OrchestratorError
    from maestro.telemetry import activer_export_langfuse, redact_secrets

    console_tolerante()
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] in (["-h"], ["--help"]):
        print(_USAGE, file=sys.stderr)
        return 0
    if len(args) != 1:
        print(_USAGE, file=sys.stderr)
        return 2
    atelier = Path(args[0])
    try:
        ordre = lire_ordre(atelier)
    except (OSError, ValueError) as exc:
        print(f"Ordre de run illisible : {exc}", file=sys.stderr)
        return 2

    # Export Langfuse (#81) : purement configuratif, no-op sans clés — même bascule
    # que pour un run CLI, pour qu'un run détaché ne perde pas sa trace.
    activer_export_langfuse()
    activer_publication_evenements()
    coeur = CoeurRun(ordre.run_id, batteur_redis(load_settings().redis_url))
    coeur.demarrer()

    try:
        rapport = run_borne(_derouler(ordre, atelier))
    except RunAnnule as annulation:
        # Ni panne ni échec : quelqu'un a dit stop, et son issue est déjà consignée
        # côté API. Il ne reste qu'à le dire au journal de l'hôte, seule trace que
        # ce process laisse derrière lui.
        print(f"Run annulé : {annulation}", file=sys.stderr)
        return CODE_ANNULE
    except ConfigError as exc:
        print(f"Configuration : {exc}", file=sys.stderr)
        return 1
    except BriefRefuse as refus:
        # **Annulé, pas échoué** — et ce chemin n'existait pas avant #445 : le brief
        # « humain » était refusé au lancement, et les deux autres modes ne
        # sollicitent personne, donc rien ne pouvait lever ici. Il devient
        # atteignable avec le canal humain, et la question qu'il pose a déjà sa
        # réponse dans le dépôt : `ServiceExecutions._derouler` solde un refus de
        # brief en « annulée », parce que rien n'a raté — quelqu'un a dit non, avant
        # qu'une seule tâche existe. Rendre 1 ferait chercher une panne là où le
        # dispositif a fonctionné, et divergerait de l'hôte en process sur le seul
        # point où les deux doivent dire la même chose : l'issue d'un run.
        print(f"Brief refusé : {refus}", file=sys.stderr)
        return CODE_ANNULE
    except OrchestratorError as exc:
        print(f"Orchestration : {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - le fils n'a pas d'appelant à qui lever
        print(f"Exécution interrompue — {type(exc).__name__} : {exc}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        return 1
    finally:
        # Le cœur s'arrête avec le run, quelle qu'en soit l'issue — mais **rien
        # n'est effacé** : le dernier battement reste et vieillit, ce qui fera
        # passer ce run `orphelin` faute de publier son issue. Le corollaire est
        # celui de `maestro-run --publier` (#348), et le lot 5 (#446) le lève.
        coeur.arreter()

    print(redact_secrets(rapport.synthese()))
    return 0 if not rapport.echouees else 1


async def _derouler(ordre: OrdreRun, atelier: Path) -> RunReport:
    """Déroule le run **sous la garde du bus** : il s'arrête si on l'annule (#444).

    La moitié asynchrone de `main`, et elle existe pour une raison qui tient en une
    phrase : on ne s'abonne pas hors d'une boucle. D'où les deux derniers gestes
    d'armement ici — le guet, puis le témoin — dans cet ordre, parce que le témoin
    est la promesse sur laquelle le lanceur rend la main : la tenir avant d'écouter
    reviendrait à s'annoncer joignable en étant sourd.

    L'attente est une course entre **deux** tâches, et sa lecture est le sujet :

    - le **run** finit d'abord → son résultat est rendu, l'ordre éventuellement
      arrivé dans la même passe est arrivé trop tard (un run terminé ne s'annule
      pas, et sa synthèse a plus de valeur qu'une annulation nominale) ;
    - le **guet** finit d'abord en ayant *vu* l'ordre → la tâche du run est annulée
      puis attendue au plus `DELAI_EXTINCTION_S`, et `RunAnnule` court-circuite
      tout le reste ;
    - le **guet** finit d'abord sans avoir rien vu (bus refermé, Redis injoignable)
      → le run **continue**, simplement sans son canal gracieux. C'est le cas où
      l'on tient à ne pas confondre « je n'écoute plus » avec « on m'a dit stop » :
      la première lecture coûterait un run tué par une panne de Redis.

    Le bus, lui, **apparaît ici depuis #445** — il n'est plus au seul usage du
    guet : les trois attentes humaines s'y abonnent aussi, et un process qui vit
    des heures n'a pas à ouvrir quatre connexions pour un canal. Il est ouvert par
    `_bus_du_run`, qui **ne lève pas** : ce qui était garanti tant que le guet
    l'ouvrait seul — aucune façon de manquer Redis ne peut emporter le run — reste
    donc vrai, et un `None` traverse tranquillement jusqu'aux fail-safes d'en face,
    qui savent quoi en faire. Il est refermé ici et pas ailleurs, en dernier : le
    premier abonné n'est pas propriétaire d'un bus qu'il partage.
    """
    from maestro.controltower.brief import (
        ArbitreBriefControlTower,
        ArbitreClarificationControlTower,
    )
    from maestro.controltower.validation import ValidateurControlTower
    from maestro.engine.guardrails import Guardrails
    from maestro.engine.loop import OrchestrationEngine
    from maestro.telemetry import RunJournal

    bus = _bus_du_run()
    guet = asyncio.create_task(_observer_annulation(ordre.run_id, bus=bus))
    # Laisse l'abonnement partir avant de se déclarer armé — même précaution qu'en
    # #48 et #320. Elle ne suffit pas à garantir le SUBSCRIBE Redis, et c'est
    # assumé : ce qui rattrape la course ici, et qui manquait là-bas, est le repli
    # franc du lanceur.
    await asyncio.sleep(0)
    _poser_temoin(atelier)
    try:
        # Les garde-fous se recomposent ici pour la raison exacte qui vaut côté API
        # (`ServiceExecutions._derouler`) : les plafonds sont un réglage du
        # **lancement**, donc ils voyagent dans l'ordre ; le **validateur** est un
        # câblage de déploiement, donc il se branche là où le run se déroule (#445).
        # Idem pour les deux arbitres du brief : le **mode** voyage, l'**arbitre**
        # se branche. Aucun des trois n'est conditionné au mode — le moteur ignore
        # de lui-même un arbitre qu'il n'a pas à consulter (`loop.py`), et une
        # seconde règle ici serait une règle de plus à tenir d'accord avec la
        # sienne.
        #
        # Sans bus, les trois sont `None` : les fail-safes d'en face refusent alors
        # ce qu'ils ne peuvent pas faire trancher, et c'est tout ce qu'on veut d'eux
        # (cf. l'en-tête du module).
        garde_fous = Guardrails(
            plafond_cout_usd=ordre.plafond_cout_usd,
            plafond_tokens=ordre.plafond_tokens,
            timeout_s=ordre.timeout_tache_s,
            validateur=None if bus is None else ValidateurControlTower(bus),
        )
        moteur = OrchestrationEngine.default(
            guardrails=garde_fous,
            max_parallele=ordre.parallelisme,
            arbitre_brief=None if bus is None else ArbitreBriefControlTower(bus),
            # Le **plafond** de tours n'est pas passé, comme côté API : c'est un
            # réglage du moteur, dont le défaut vaut pour tout run de la Control
            # Tower — le poser ici en ferait un réglage de l'hôte.
            arbitre_clarification=(
                None if bus is None else ArbitreClarificationControlTower(bus)
            ),
        )
        run = asyncio.create_task(
            moteur.run(
                ordre.objectif,
                # Le `run_id` de l'API, jamais un neuf : c'est lui qui rattache les
                # étapes de ce process au run que la projection connaît déjà. Un
                # journal neuf ferait un second run, invisible depuis l'écran du
                # premier.
                journal=RunJournal(run_id=ordre.run_id),
                ticket=ordre.ticket,
                # Le prérequis commun du chantier (docs/28 §3) : sans lui,
                # `espace_de_travail(None)` retombe sur un `mkdtemp()` et le
                # livrable n'atteint jamais la racine du projet.
                projet_id=ordre.projet_id,
                mode_brief=ordre.mode_brief,
            )
        )
        attendus: set[asyncio.Future[Any]] = {run, guet}
        termines, _ = await asyncio.wait(attendus, return_when=asyncio.FIRST_COMPLETED)
        if run not in termines and _annulation_vue(guet):
            run.cancel()
            await asyncio.wait({run}, timeout=DELAI_EXTINCTION_S)
            raise RunAnnule(
                f"run {ordre.run_id} interrompu depuis la Control Tower — tâches en vol annulées"
            )
        return await run
    finally:
        guet.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await guet
        # **Après** le guet, jamais avant : fermer le client sous un abonnement
        # encore vivant ferait lever là où on a promis de ne pas lever. Le bus est
        # celui de ce process (#445), donc c'est à lui de le refermer — les
        # abonnements des arbitres, eux, sont déjà retombés avec le run.
        if bus is not None:
            with suppress(asyncio.CancelledError, Exception):
                await bus.close()


async def _observer_annulation(run_id: str, *, bus: EventBus | None = None) -> bool:
    """Guette l'ordre d'annulation de `run_id` — **True** quand il est passé (#444).

    L'ordre n'a pas de canal à lui : c'est l'**issue** du run, `execution.statut`
    au statut « annulee », consignée par `ServiceExecutions._solder` avant qu'il ne
    demande l'interruption. Un fait dont la conséquence est nécessaire vaut mieux
    qu'un second message qui pourrait manquer (cf. l'en-tête du module).

    Tout le reste du bus est ignoré, y compris les issues des **autres** runs : ce
    process n'en porte qu'un, et il est nommé.

    Le bus est en principe **passé** par l'appelant depuis #445 (`_bus_du_run`, un
    seul pour tout le process) ; il lui appartient alors, et n'est **pas** refermé
    ici — seul le flux l'est. L'ouverture de secours reste, pour le cas où il n'y en
    a pas : celui de la config, comme le battement et la publication, refermé en
    partant, parce qu'un abonnement Redis est une connexion et que la laisser
    derrière soi dans un process qui vit des heures est le genre de fuite qu'on ne
    remarque qu'au trentième run. C'est aussi ce que passe un test qui veut un bus
    en mémoire (#447).

    **Ne lève jamais**, et c'est le point délicat : client Redis impossible à
    construire, abonnement refusé, flux tari, événement illisible — tout rend
    `False`, c'est-à-dire « je n'écoute plus », jamais « on m'a dit stop ».
    Confondre les deux ferait tuer un run par une panne de bus, alors que le run,
    lui, se passe très bien de ce canal (le lanceur garde son repli franc). C'est
    pour tenir cette promesse d'un seul tenant que l'ouverture du bus est **dans**
    le `try` : la laisser dehors, chez l'appelant, y laisserait la seule panne
    capable d'emporter le run.
    """
    from maestro.config import load_settings

    flux = None
    propre: EventBus | None = None
    try:
        if bus is None:
            bus = propre = RedisEventBus(load_settings().redis_url)
        flux = bus.subscribe()
        async for event in flux:
            if event.type != EVENEMENT_EXECUTION_STATUT:
                continue
            if event.run_id != run_id:
                continue
            if event.statut == EXECUTION_ANNULEE:
                return True
        return False
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - un guet en panne n'emporte pas le run
        print(
            f"Guet de l'annulation interrompu — {type(exc).__name__} : {exc} "
            "(le run continue, son annulation passera par le repli du lanceur)",
            file=sys.stderr,
        )
        return False
    finally:
        aclose = getattr(flux, "aclose", None)
        if aclose is not None:
            with suppress(asyncio.CancelledError, Exception):
                await aclose()
        if propre is not None:
            with suppress(asyncio.CancelledError, Exception):
                await propre.close()


def _bus_du_run() -> RedisEventBus | None:
    """Le bus de ce process — **un seul**, pour le guet et les trois attentes (#445).

    Celui de la config, comme le battement et la publication : même Redis, même
    canal `maestro.evenements`, donc les mêmes demandes et les mêmes décisions que
    pour un run porté par l'API. Un seul objet parce qu'un seul suffit —
    `RedisEventBus.subscribe` ouvre un `pubsub` par appel sur un client partagé, si
    bien que quatre abonnements concurrents tiennent sur une connexion.

    **Ne lève jamais**, et c'est le point. Cette fonction reprend la charge que
    `_observer_annulation` portait pour son propre compte : l'ouverture était
    **dans** son `try` précisément pour qu'aucune façon de manquer Redis ne puisse
    emporter le run, et sortir cette ligne de là sans rien mettre à la place aurait
    rendu fatal ce qui ne l'était pas (`redis` absent, URL illisible, configuration
    en défaut).

    Un `None` n'est donc pas une panne à traiter ici : c'est un fait que l'appelant
    fait descendre tel quel, et que les fail-safes d'en face muent en refus — le
    moteur pour un brief que personne ne pourrait trancher, les garde-fous pour une
    action sensible que personne ne pourrait approuver. La cause part au journal de
    l'hôte, seule trace que ce process laisse derrière lui.
    """
    from maestro.config import load_settings

    try:
        return RedisEventBus(load_settings().redis_url)
    except Exception as exc:  # noqa: BLE001 - un bus manquant se dit, il n'emporte rien
        print(
            f"Bus d'événements indisponible — {type(exc).__name__} : {exc} "
            "(pas de décision humaine possible : un brief « humain » sera refusé "
            "avant le premier appel modèle, et toute action sensible sera refusée)",
            file=sys.stderr,
        )
        return None


def _annulation_vue(guet: asyncio.Task[bool]) -> bool:
    """Le guet a-t-il **vu** l'ordre ? — False sur tout le reste, sans lever.

    `_observer_annulation` promet de ne pas lever, mais lire le résultat d'une tâche
    est précisément l'endroit où une promesse tenue ailleurs se paie ici : une
    tâche annulée ou en erreur relèverait à la lecture, et transformerait un guet en
    panne du run. On la traite donc comme un guet qui n'a rien vu, ce qu'elle est.
    """
    if guet.cancelled() or guet.exception() is not None:
        return False
    return guet.result()


def lire_ordre(atelier: Path) -> OrdreRun:
    """Relit l'ordre du run dans son atelier, **puis efface le fichier**.

    L'effacement n'est pas du ménage : l'ordre porte l'objectif, c'est-à-dire du
    texte libre écrit par un humain — la matière même que `redact_secrets` expurge
    partout ailleurs. Il a été lu, il n'a plus d'usage, il n'a pas à rester sur le
    disque le temps d'un run. Un échec d'effacement ne fait pas échouer le
    démarrage : le fichier est déjà dans un dossier temporaire.
    """
    fichier = atelier / FICHIER_ORDRE
    ordre = ordre_depuis_dict(json.loads(fichier.read_text(encoding="utf-8")))
    try:
        fichier.unlink()
    except OSError:
        pass
    return ordre


def _poser_temoin(atelier: Path) -> None:
    """Pose le témoin de démarrage — l'hôte est armé, le lanceur peut rendre la main.

    Best-effort et jamais une levée : un témoin qu'on n'a pas su écrire coûte au
    lanceur son plafond d'attente, jamais le run — qui, lui, est bel et bien parti.
    """
    try:
        (atelier / FICHIER_PRET).write_text("", encoding="utf-8")
    except OSError:
        pass


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

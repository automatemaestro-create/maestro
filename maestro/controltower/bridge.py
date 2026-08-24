"""Pont télémétrie → événements : le journal (#8) alimente la Control Tower (#46).

Les événements temps réel sont **sourcés depuis la télémétrie** : chaque ligne
du journal d'exécution (`RunJournal`, une ligne JSON par étape sur le logger
`maestro.trace`) devient un ou zéro événement du bus. Le pont se pose en
`logging.Handler` (`JournalEventHandler`) : aucun changement du moteur ni du
journal — là où le worker ou l'orchestrateur consigne déjà, la Control Tower
écoute.

Trois pièces :

- `evenements_depuis_step(record)` : la conversion pure d'une ligne de journal
  (forme `StepRecord.to_dict`) en événements — testable sans logger ni Redis ;
- `JournalEventHandler` + `publieur_redis` : le branchement production — le
  handler publie chaque événement via un callable synchrone, typiquement le
  `PUBLISH` Redis (le côté API consomme le canal via `RedisEventBus`) ;
- `solder_le_run(...)` (#446) : le seul événement qu'un hôte publie **de
  lui-même**, sans passer par le journal — son **issue**. Le journal ne parle que
  de tâches ; le cycle de vie du run, lui, n'a longtemps eu qu'un écrivain, le
  service de pilotage de l'API. Un hôte qui vit ailleurs (process détaché #443,
  `maestro-run --publier`) doit pouvoir dire comment il finit, faute de quoi son
  run reste `en_cours` puis vieillit en `orphelin` alors qu'il s'est très bien
  passé.

Le journal amont expurge déjà les secrets (`redact_secrets`) : ce qui part sur
le bus est ce qui partait déjà dans les logs.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from maestro.appartenance import projet_id_valide
from maestro.controltower.events import (
    ACTEUR_RUN,
    CANAL_EVENEMENTS,
    EVENEMENT_AGENT_ACTIVITE,
    EVENEMENT_EXECUTION_STATUT,
    EVENEMENT_MESSAGE_INTER_AGENTS,
    EVENEMENT_RUN_PLAN,
    EVENEMENT_TACHE_DETAIL,
    EVENEMENT_TACHE_REFERENCE,
    EVENEMENT_TACHE_STATUT,
    REDIS_URL_DEFAUT,
    ROLE_RUN,
    Event,
)
from maestro.detail_tache import SUFFIXE_ETAPE_DETAIL, etapes_depuis, liens_depuis
from maestro.plan_run import NoeudPlan, noeuds_depuis
from maestro.references import SUFFIXE_ETAPE_TICKET, ReferenceTicket
from maestro.telemetry import LOGGER_NAME, redact_secrets
from maestro.telemetry.usage import StepUsage

_LOGGER = logging.getLogger("maestro.controltower")

#: Étape du journal qui n'est pas une tâche : la planification de l'orchestrateur.
_ETAPE_PLANIFICATION = "planification"

#: Étape du journal qui n'est pas une tâche : la reprise d'un run interrompu
#: (#96 — cf. `maestro.telemetry.costs.ETAPE_REPRISE`).
_ETAPE_REPRISE = "reprise"

#: Étape du journal qui n'est pas une tâche : le brief structuré rédigé avant
#: décomposition (#318 — cf. `maestro.telemetry.costs.ETAPE_BRIEF`). Même raison
#: d'être déclarée ici que là-bas : sans elle, la règle par défaut (« toute autre
#: étape est l'issue d'une tâche ») ferait apparaître une carte de tâche fantôme
#: nommée « brief » sur le Kanban, alors que l'étape porte sur le run entier.
_ETAPE_BRIEF = "brief"

#: Étapes rattachées au **run**, pas à une tâche : elles deviennent des activités
#: d'agent sans `tache_id` (l'orchestrateur cadre puis planifie, le moteur reprend).
_ETAPES_RUN = (_ETAPE_PLANIFICATION, _ETAPE_BRIEF, _ETAPE_REPRISE)

#: Suffixe des étapes de validation humaine (cf. `LocalExecutor._valide_si_sensible`).
_SUFFIXE_VALIDATION = ":validation"

#: Suffixe des étapes de relance automatique (#91 — cf.
#: `maestro.engine.executor`, `SUFFIXE_ETAPE_RELANCE`).
_SUFFIXE_RELANCE = ":relance"

#: Suffixe des étapes de début d'exécution (#98 — cf.
#: `maestro.engine.executor`, `SUFFIXE_ETAPE_DEBUT`).
_SUFFIXE_DEBUT = ":debut"

#: Suffixe des étapes de refus d'outil (#110 — cf.
#: `maestro.engine.executor`, `SUFFIXE_ETAPE_REFUS`).
_SUFFIXE_REFUS = ":refus-outil"

#: Suffixe des étapes d'activité (#479 — cf. `maestro.engine.executor`,
#: `SUFFIXE_ETAPE_ACTIVITE`) : ce que l'agent fait **pendant** sa tâche, publié à
#: débit borné par le fournisseur. Rangé avec `:validation`, `:relance` et
#: `:refus-outil` — même nature (une activité d'agent rattachée à sa tâche, qui
#: ne la fait pas changer de colonne) et donc même traitement.
_SUFFIXE_ACTIVITE = ":activite"

#: Suffixe des étapes de messagerie inter-agents (#44 — cf.
#: `maestro.messaging.mailbox.consigne_message`, `SUFFIXE_ETAPE_MESSAGE`).
_SUFFIXE_MESSAGE = ":message"

#: Suffixe des étapes qui posent un ticket externe sur une tâche (#187 — cf.
#: `maestro.references.consigne_ticket`).
_SUFFIXE_REFERENCE = SUFFIXE_ETAPE_TICKET

#: Suffixe des étapes qui posent le détail d'une tâche (#246 — cf.
#: `maestro.detail_tache.consigne_detail`).
_SUFFIXE_DETAIL = SUFFIXE_ETAPE_DETAIL


def evenements_depuis_step(record: Mapping[str, Any]) -> tuple[Event, ...]:
    """Convertit une ligne de journal (`StepRecord.to_dict`) en événements du bus.

    - les étapes `<tache>:message` (#44) deviennent des **messages
      inter-agents** (entité AGENT_MESSAGE — handoff, notification…) ;
    - les étapes `planification`, `brief` (#318) et `reprise` (#96) et les étapes
      `<tache>:validation`, `<tache>:relance` (#91), `<tache>:refus-outil`
      (#110) et `<tache>:activite` (#479) deviennent des **activités d'agent**
      (l'orchestrateur cadre puis planifie, le moteur reprend un run interrompu,
      un humain tranche, le moteur relance, la politique de permissions refuse un
      outil, l'agent travaille — la raison voyage dans `detail`) ;
      `planification`, `brief` et `reprise` portent sur le run entier, donc sans
      `tache_id` ;
    - les étapes `<tache>:debut` (#98) deviennent le **début** de leur tâche :
      événement `tache.statut` au statut `en_cours` (agent, heure de début),
      sans usage ni coût — rien n'entre au grand livre avant l'issue ;
    - les étapes `<tache>:reference` (#187) deviennent un `tache.reference` :
      elles ne portent que le **ticket externe** dont relève la tâche, sans rien
      changer d'autre — c'est ainsi qu'un agent la rattache en cours de route ;
    - les étapes `<tache>:detail` (#246) deviennent un `tache.detail` : elles ne
      portent que la **description**, les **étapes** et les **liens utiles** de
      la tâche, sans rien changer d'autre — c'est ainsi qu'un agent la renseigne
      en cours de route sans la faire changer de colonne ;
    - toute autre étape est l'issue d'une **tâche** : événement `tache.statut`
      portant statut, agent, rôle et coût rapporté (#8).

    L'étape `planification` est la seule à produire **deux** événements (#490) :
    son `agent.activite`, inchangé, et — quand elle porte un plan — un
    `run.plan` qui transporte le **graphe** décidé (nœuds, arêtes, ossatures de
    checklist). Deux faits distincts sur une même ligne : ce que le cadrage a
    coûté, et ce qu'il a décidé. Sans plan (ligne d'échec, journal antérieur à ce
    lot, producteur minimaliste), l'étape rend exactement l'événement d'avant.

    Chaque événement embarque la **référence externe** de son étape quand le
    journal en porte une (#187) : c'est le seul chemin par lequel le ticket d'un
    run atteint la Control Tower — le moteur ne fait que la transporter. Il en va
    de même du **projet** auquel le travail appartient (`projet_id`, #222) : posé
    au lancement du run, hérité par chaque tâche, il remonte aux vues par ces
    seules lignes de journal.

    Chaque événement embarque la **mesure d'usage** de son étape (tokens,
    coût, durée — forme `StepUsage`) quand le journal en porte une : c'est la
    matière de la comptabilité par tâche côté Control Tower (#57), annexes
    comprises (validation, message — usage nul aujourd'hui, compté s'il vient).

    Une ligne illisible (pas un objet JSON de journal) ne produit **aucun**
    événement plutôt qu'un événement faux — le flux temps réel est un miroir,
    pas une source de vérité.
    """
    etape = record.get("etape")
    if not isinstance(etape, str) or not etape:
        return ()
    usage = record.get("usage")
    mesure = StepUsage.from_dict(usage) if isinstance(usage, Mapping) else None
    cout_brut = usage.get("cout_usd") if isinstance(usage, Mapping) else None
    est_message = etape.endswith(_SUFFIXE_MESSAGE)
    est_debut = etape.endswith(_SUFFIXE_DEBUT)
    est_reference = etape.endswith(_SUFFIXE_REFERENCE)
    est_detail = etape.endswith(_SUFFIXE_DETAIL)
    est_activite = etape in _ETAPES_RUN or etape.endswith(
        (_SUFFIXE_VALIDATION, _SUFFIXE_RELANCE, _SUFFIXE_REFUS, _SUFFIXE_ACTIVITE)
    )
    if est_reference:
        type_evenement = EVENEMENT_TACHE_REFERENCE
        tache_id = etape.removesuffix(_SUFFIXE_REFERENCE)
        detail = str(record.get("sortie") or "")
        # Poser une référence ne coûte rien : rien à faire entrer au grand livre.
        mesure = None
        cout_brut = None
    elif est_detail:
        type_evenement = EVENEMENT_TACHE_DETAIL
        tache_id = etape.removesuffix(_SUFFIXE_DETAIL)
        detail = str(record.get("sortie") or "")
        # Idem : renseigner une tâche ne dépense rien.
        mesure = None
        cout_brut = None
    elif est_message:
        type_evenement = EVENEMENT_MESSAGE_INTER_AGENTS
        tache_id = etape.removesuffix(_SUFFIXE_MESSAGE)
        detail = str(record.get("sortie") or "")
    elif est_debut:
        type_evenement = EVENEMENT_TACHE_STATUT
        tache_id = etape.removesuffix(_SUFFIXE_DEBUT)
        detail = str(record.get("sortie") or "")
        # Le début ne porte aucune dépense : la mesure viendra avec l'issue de
        # la tâche — rien n'entre au grand livre (ni ne s'affiche) avant.
        mesure = None
        cout_brut = None
    elif est_activite:
        type_evenement = EVENEMENT_AGENT_ACTIVITE
        tache_id = (
            ""
            if etape in _ETAPES_RUN
            else etape.removesuffix(_SUFFIXE_VALIDATION)
            .removesuffix(_SUFFIXE_RELANCE)
            .removesuffix(_SUFFIXE_REFUS)
            .removesuffix(_SUFFIXE_ACTIVITE)
        )
        detail = str(record.get("sortie") or record.get("erreur") or "")
    else:
        type_evenement = EVENEMENT_TACHE_STATUT
        tache_id = etape
        detail = str(record.get("erreur") or "")
    # Le graphe du plan (#490) : lu **avant** de construire quoi que ce soit,
    # parce qu'il décide s'il y a un ou deux événements. Une liste vide n'est pas
    # un plan — annoncer un graphe sans nœud ferait remplacer, dans la
    # projection, un plan déjà posé par rien du tout.
    noeuds = noeuds_depuis(record.get("plan")) if etape == _ETAPE_PLANIFICATION else []
    return (
        Event(
            type=type_evenement,
            run_id=str(record.get("run_id", "")),
            tache_id=tache_id,
            titre=str(record.get("nom", "")),
            agent=str(record.get("agent", "")),
            role=str(record.get("role", "")),
            statut=str(record.get("statut", "")),
            detail=detail,
            cout_usd=float(cout_brut) if isinstance(cout_brut, int | float) else None,
            usage=mesure,
            ticket=ReferenceTicket.depuis(record.get("ticket")),
            projet_id=projet_id_valide(record.get("projet_id")),
            # Le détail de la tâche (#246) traverse le journal comme le ticket
            # externe (#187) : ce qui a été consigné une fois est rejoué à
            # l'identique, donc le panneau de détail (#251) se remplit aussi
            # après un redémarrage. Clé absente → None, et la projection ne
            # touche alors à rien.
            description=str(record.get("description") or ""),
            etapes=(etapes_depuis(record["etapes"]) if record.get("etapes") is not None else None),
            liens=(liens_depuis(record["liens"]) if record.get("liens") is not None else None),
            horodatage=str(record.get("horodatage", "")),
        ),
        *(
            (
                Event(
                    type=EVENEMENT_RUN_PLAN,
                    run_id=str(record.get("run_id", "")),
                    # Sur le run entier, jamais sur une tâche : `tache_id` reste
                    # vide, comme pour `execution.statut`. En poser un ferait
                    # naître une carte fantôme au Kanban — le défaut que la
                    # branche par défaut de cette fonction fabrique dès qu'une
                    # étape du run n'est pas nommée.
                    agent=str(record.get("agent", "")),
                    role=str(record.get("role", "")),
                    titre=str(record.get("nom", "")),
                    # Le **volume** du graphe en clair, et c'est ce que la ligne
                    # d'activité prononce. Il est ici plutôt que déduit de `plan`
                    # côté client parce que le journal requêtable (#478) ne garde
                    # pas les charges lourdes d'un événement : un fil relu après
                    # rechargement compterait alors zéro nœud et l'annoncerait.
                    # Historique et direct disent ainsi la même phrase par
                    # construction, et non par deux calculs à tenir d'accord.
                    detail=_resume_du_plan(noeuds),
                    projet_id=projet_id_valide(record.get("projet_id")),
                    plan=noeuds,
                    horodatage=str(record.get("horodatage", "")),
                ),
            )
            if noeuds
            else ()
        ),
    )


def _resume_du_plan(noeuds: Sequence[NoeudPlan]) -> str:
    """Ce que le plan dit en clair — la ligne du fil d'activité n'affiche que ça (#490).

    Le volume, et rien de plus : un graphe se regarde, il ne se raconte pas.
    « aucune dépendance » plutôt que « 0 enchaînement » parce que c'est un cas
    **normal** et le plus courant — la plupart des plans n'en déclarent aucune —,
    et qu'un compteur à zéro se lit comme un manque.
    """
    aretes = sum(len(noeud.dependances) for noeud in noeuds)
    enchainements = f"{aretes} enchaînement(s)" if aretes else "aucune dépendance"
    return f"{len(noeuds)} tâche(s), {enchainements}"


class JournalEventHandler(logging.Handler):
    """Handler du logger `maestro.trace` : chaque ligne consignée part sur le bus.

    `publier` est un callable **synchrone** (le journal consigne en synchrone) ;
    en production c'est le `PUBLISH` Redis de `publieur_redis`. Une ligne
    inconvertible ou une publication en échec est signalée via `handleError`
    (silencieuse par défaut, comme tout handler logging) : la télémétrie ne
    doit jamais faire échouer l'exécution qu'elle observe.
    """

    def __init__(self, publier: Callable[[Event], None]) -> None:
        super().__init__(level=logging.INFO)
        self._publier = publier

    def emit(self, record: logging.LogRecord) -> None:
        try:
            data = json.loads(record.getMessage())
            if not isinstance(data, dict):
                return
            for evenement in evenements_depuis_step(data):
                self._publier(evenement)
        except Exception:
            self.handleError(record)


def publieur_redis(
    url: str | None = None, *, canal: str = CANAL_EVENEMENTS
) -> Callable[[Event], None]:
    """Construit le callable de publication Redis (synchrone) du pont.

    Client Redis **synchrone** (le handler logging l'est) sur l'instance du
    docker-compose par défaut — le pendant producteur du `RedisEventBus`
    consommé par l'API. La connexion est paresseuse (ouverte au premier
    `PUBLISH`).
    """
    # Import local : seule la publication Redis dépend du client.
    import redis

    client = redis.Redis.from_url(url or REDIS_URL_DEFAUT)

    def publier(evenement: Event) -> None:
        client.publish(canal, evenement.to_json())

    return publier


def solder_le_run(
    run_id: str,
    statut: str,
    detail: str = "",
    *,
    cause: str = "",
    url: str | None = None,
    canal: str = CANAL_EVENEMENTS,
) -> bool:
    """Un hôte **publie son issue en partant** et se tait (#446) — jamais une levée.

    Deux gestes, et c'est volontairement le même appel : consigner l'issue du run
    (`execution.statut`, statut terminal), puis **retirer son battement**. C'est,
    à l'identique, ce que fait `ServiceExecutions._derouler` de l'autre côté de la
    frontière (`_consigne` puis `_oublier`), et l'**ordre** y a la même raison :
    entre les deux, un lecteur verrait un run encore en cours et sans battement,
    c'est-à-dire un orphelin qui n'en est pas un. Les séparer en deux appels
    laisserait un hôte publier l'un sans l'autre, ce qui est précisément la moitié
    de panne que ce lot supprime.

    Ce que ce geste lève, c'est le **corollaire de #348** : un run publié hors de
    l'API n'émettait aucun statut de fin, donc son dernier battement vieillissait
    et le faisait apparaître `orphelin` alors qu'il avait très bien fini. Le
    verdict portait sur son hôte, jamais sur son travail — mais depuis que l'hôte
    détaché est le **défaut** des lancements Control Tower, ce corollaire porterait
    sur tous les runs, et il cesse d'être acceptable.

    L'oubli du battement n'a lieu **qu'après** une publication réussie, et c'est le
    point délicat : effacer le signal de vie d'un run dont l'issue n'a pas atteint
    l'API le ferait passer d'`orphelin` à `indetermine`, c'est-à-dire remplacer un
    verdict juste par une absence d'information. Sur un Redis muet, on préfère donc
    laisser le dispositif d'avant faire son travail.

    Rend True si l'issue est partie. **Ne lève jamais** : un hôte qui n'a pas pu
    publier a de toute façon fini son travail, et le faire échouer sur son dernier
    geste ferait perdre la synthèse d'un run réussi.
    """
    try:
        publieur_redis(url, canal=canal)(
            Event(
                type=EVENEMENT_EXECUTION_STATUT,
                run_id=run_id,
                agent=ACTEUR_RUN,
                role=ROLE_RUN,
                statut=statut,
                # Même filet que `_consigne` côté API : le détail d'une issue
                # reprend une exception, où un secret servi pendant le run peut
                # ressurgir. Ce qui part sur le bus est montrable.
                detail=redact_secrets(detail),
                # La cause (#479) n'est pas expurgée : c'est un code d'un
                # ensemble fermé, pas du texte libre — même raison que côté API.
                cause=cause,
            )
        )
    except Exception:
        _LOGGER.exception(
            "Issue du run %s non publiée : son travail est fait, mais l'API le "
            "verra vieillir en « orphelin » faute de statut de fin.",
            run_id,
        )
        return False
    try:
        # Import local, comme celui du client Redis : seul ce geste-ci a besoin du
        # registre des battements, et `bridge` est importé par tout producteur.
        from maestro.controltower.battement import oublieur_redis

        oublieur_redis(url)(run_id)
    except Exception:
        _LOGGER.exception(
            "Battement du run %s non retiré : sans effet sur son statut, déjà "
            "terminal — seule l'entrée du registre reste.",
            run_id,
        )
    return True


def activer_publication(publier: Callable[[Event], None]) -> logging.Handler:
    """Branche le pont : le journal (#8) publie désormais ses étapes en événements.

    Pose un `JournalEventHandler` sur le logger `maestro.trace` et le renvoie
    (à retirer via `logging.getLogger(LOGGER_NAME).removeHandler(...)` pour
    débrancher). S'utilise côté **producteur** : process de l'orchestrateur
    (`maestro-run --publier`) comme workers de la file (#41).
    """
    handler = JournalEventHandler(publier)
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    return handler

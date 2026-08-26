"""Validateur human-in-the-loop adossé à la Control Tower (ticket #48).

Relie le garde-fou de validation humaine du moteur (#9,
`maestro.engine.guardrails`) à l'UI de la Control Tower (#46/#47) : quand une
tâche est classée sensible, `ValidateurControlTower` publie la demande sur le
bus d'événements (`validation.demande` — l'API la projette, l'UI l'affiche
avec le contexte : agent, tâche, action demandée, justification) puis **attend
la décision humaine** (`validation.decision`, publiée par
`POST /api/validations/{tache_id}/decision`). La tâche reste en pause tant que
personne n'a tranché — pas de time-out silencieux : le time-out par tâche du
moteur ne court pas pendant cette attente (cf. `LocalExecutor._realise_gardee`).

Le contrat est celui du `Validateur` des garde-fous : approbation → le moteur
reprend la tâche ; refus → il l'annule proprement avant toute exécution ; dans
les deux cas la décision est consignée au journal (#8, étape
`<tâche>:validation`). Fail-safe hérité : si le bus est en panne (Redis
injoignable…), l'exception remonte aux garde-fous qui **refusent** la demande —
jamais d'action sensible sans accord explicite.

Même bus que le reste de la Control Tower : `InMemoryEventBus` en test ou en
mono-process, `RedisEventBus` en production (`validateur_redis`, pendant
moteur du `publieur_redis` du pont télémétrie).

Depuis #227 ce canal porte un **second type d'action sensible** :
`appliquer_sous_validation` soumet « appliquer ce travail dans mon projet ? »
au même validateur, **diff en pièce jointe** (EF-37, docs/24 §2.4). Rien du
mécanisme ci-dessus n'a changé pour l'accueillir — c'était le but : la Phase 7
n'invente pas de garde-fou, elle branche une action de plus sur celui qui existe.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress
from pathlib import Path

from maestro.controltower.events import (
    EVENEMENT_VALIDATION_DECISION,
    EVENEMENT_VALIDATION_DEMANDE,
    Event,
    EventBus,
    RedisEventBus,
)
from maestro.controltower.state import VALIDATION_APPROUVEE, VALIDATION_EN_ATTENTE
from maestro.engine.guardrails import DemandeValidation, Guardrails, Validateur
from maestro.projets.application import (
    APPLICATION_APPROUVEE,
    APPLICATION_REFUSEE,
    APPLICATION_SANS_OBJET,
    ResultatApplication,
    appliquer,
    diff_du_travail,
    verifier_perimetre,
)
from maestro.projets.modele import Projet
from maestro.telemetry import redact_secrets


def evenement_demande(demande: DemandeValidation) -> Event:
    """Mue une `DemandeValidation` du moteur en événement `validation.demande`.

    Porte tout ce que l'UI montre pour trancher : la tâche (id, titre), l'agent
    qui l'exécuterait, la `raison` de la classification (dans `detail`) et
    l'action demandée (`description`). Expurgé des secrets avant publication —
    même filet que le journal (#8) : ce qui part sur le bus est montrable.

    Et depuis #570, **d'où elle vient** : `run_id` et `projet_id`, sans lesquels
    la demande est montrable mais jamais montrée — le journal du run ne la
    contient pas et les vues, cadrées sur le projet actif, l'écartent. Ni l'un ni
    l'autre n'est expurgé : ce sont des identifiants que nous produisons, pas du
    texte d'agent.
    """
    return Event(
        type=EVENEMENT_VALIDATION_DEMANDE,
        tache_id=demande.task_id,
        run_id=demande.run_id,
        projet_id=demande.projet_id,
        titre=redact_secrets(demande.titre),
        agent=demande.agent,
        role=demande.role,
        statut=VALIDATION_EN_ATTENTE,
        detail=redact_secrets(demande.raison),
        description=redact_secrets(demande.description),
        # Le diff (#227) voyage **structuré** et non expurgé : il ne porte que
        # des chemins et des décomptes de lignes — jamais de contenu de fichier —,
        # si bien qu'il n'y a rien à y masquer, et le passer au rédacteur de
        # secrets abîmerait des noms de fichiers légitimes (`config/secret.md`).
        diff=demande.diff,
    )


class ValidateurControlTower:
    """Validateur (#9) qui soumet la demande à l'UI et attend la décision humaine.

    S'abonne au bus **avant** de publier la demande : même une décision
    immédiate ne peut pas être manquée. L'attente est indéfinie — la pause
    d'une tâche sensible ne se résout que par une décision humaine (ou l'arrêt
    du run), jamais par un time-out silencieux.
    """

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus

    async def __call__(self, demande: DemandeValidation) -> bool:
        """Publie la demande puis rend la décision humaine (True = approuvée)."""
        flux = self._bus.subscribe()
        ecoute = asyncio.create_task(_premiere_decision(flux, demande.task_id))
        # Laisse l'abonnement se poser avant de publier (bus mémoire : un tour
        # de boucle suffit ; sur Redis, la décision humaine arrive de toute
        # façon bien après l'aller-retour du SUBSCRIBE).
        await asyncio.sleep(0)
        try:
            await self._bus.publish(evenement_demande(demande))
            return await ecoute
        finally:
            if not ecoute.done():
                ecoute.cancel()
                with suppress(asyncio.CancelledError):
                    await ecoute


async def _premiere_decision(flux: AsyncIterator[Event], tache_id: str) -> bool:
    """Attend sur `flux` la décision visant `tache_id` ; True si approuvée.

    Ignore tout le reste du bus (statuts de tâches, autres validations…). Si le
    flux se tarit sans décision (bus refermé), lève — les garde-fous muent
    l'exception en refus (fail-safe), pas en fausse décision humaine.
    """
    try:
        async for event in flux:
            if event.type != EVENEMENT_VALIDATION_DECISION:
                continue
            if event.tache_id != tache_id:
                continue
            return event.statut == VALIDATION_APPROUVEE
    finally:
        aclose = getattr(flux, "aclose", None)
        if aclose is not None:
            await aclose()
    raise RuntimeError(
        f"le bus d'événements s'est refermé sans décision pour la tâche {tache_id}"
    )


async def appliquer_sous_validation(
    projet: Projet,
    *,
    tache_id: str,
    validateur: Validateur | None,
    branche: str = "",
    espace: Path | str | None = None,
    agent: str = "",
    role: str = "",
    titre: str = "",
    run_id: str = "",
) -> ResultatApplication:
    """Applique le travail de la tâche dans `projet` — **après accord humain** (EF-37).

    C'est le nouveau type d'action sensible de la Phase 7, et c'est tout ce qu'il
    est : un `DemandeValidation` de plus, soumis au **même** validateur que le
    déploiement ou la suppression (docs/24 §2.4 — « le chantier n'invente pas de
    mécanisme de sûreté, il branche un nouveau type d'action sur celui qui
    existe »). En pratique le validateur est `ValidateurControlTower`, et la
    tâche reste donc en pause tant que personne n'a tranché depuis l'UI.

    Le déroulé, dans cet ordre — il compte :

    1. le **diff** est calculé (`maestro.projets.application.diff_du_travail`) ;
       vide, on s'arrête là (`sans_objet`) : déranger quelqu'un pour approuver
       zéro fichier userait la seule chose qui rend la validation utile ;
    2. le **périmètre** est vérifié sur le diff entier (EF-38). Un chemin qui en
       sort lève `ApplicationRefusee` **avant** la demande : on ne fait pas
       approuver ce qu'on refusera d'écrire ;
    3. la demande part au validateur, diff en pièce jointe, et l'attente est
       celle du mécanisme existant — indéfinie, sans time-out silencieux ;
    4. **sur accord seulement**, l'écriture a lieu (fusion de la branche de tâche
       vers la branche de travail déclarée, ou recopie des fichiers).

    Sur refus, rien n'est écrit et le travail **reste consultable** : la branche
    de tâche n'est jamais supprimée (#224), la copie reste où elle est.

    Le fail-safe est celui des garde-fous (#9), réutilisé tel quel plutôt que
    réécrit : `Guardrails.demande_validation` refuse sans validateur configuré,
    refuse si le validateur lève, et exécute un validateur synchrone hors de la
    boucle d'événements. Un `Guardrails` monté ici pour ses seuls services de
    délibération ne porte donc **ni plafond ni time-out** — ceux du run
    s'appliquent ailleurs, et les redéclarer ici en inventerait un second jeu.

    `run_id` (#570) est le run au nom duquel on applique : seul l'appelant le
    sait, et sans lui la demande sort du journal de son run — le projet, lui, est
    déduit de `projet`, qui est le sujet même de la question.

    `branche` est la branche de tâche posée par l'espace de travail dérivé
    (#224, `branche_de_tache`) — requise pour un projet versionné, jamais
    recalculée ici. `espace` est le répertoire de travail de la tâche : requis
    pour un projet non versionné (c'est la copie qu'on compare), utile pour un
    projet versionné tant qu'il est monté (le travail non commité entre alors
    dans le diff, et est commité sur la branche avant la fusion).

    Lève `ApplicationRefusee` (motivée) si le diff n'est pas calculable, si un
    chemin sort du périmètre, ou si l'écriture est impossible — jamais à moitié
    appliquée.
    """
    diff = diff_du_travail(projet, branche=branche, espace=espace)
    if diff.vide:
        return ResultatApplication(
            statut=APPLICATION_SANS_OBJET,
            diff=diff,
            detail=f"aucune modification à appliquer dans le projet {projet.nom!r}",
        )
    verifier_perimetre(projet, diff)
    demande = DemandeValidation(
        task_id=tache_id,
        titre=titre or f"Appliquer le travail dans {projet.nom}",
        description=diff.resume(),
        agent=agent,
        role=role,
        raison=(
            f"application des modifications dans le projet « {projet.nom} » "
            f"({diff.fichiers} fichier(s), +{diff.ajouts} / −{diff.suppressions})"
        ),
        diff=diff,
        # Le projet n'est pas à déduire ici : la demande *porte* sur lui (#570).
        # `run_id` vient de l'appelant, seul à savoir au nom de quel run il applique.
        run_id=run_id,
        projet_id=projet.id,
    )
    approuve, detail = await Guardrails(validateur=validateur).demande_validation(demande)
    if not approuve:
        return ResultatApplication(statut=APPLICATION_REFUSEE, diff=diff, detail=detail)
    appliques = appliquer(projet, diff, espace=espace)
    return ResultatApplication(
        statut=APPLICATION_APPROUVEE, diff=diff, detail=detail, appliques=appliques
    )


def validateur_redis(url: str | None = None) -> ValidateurControlTower:
    """Le validateur de production : demandes et décisions via Redis Pub/Sub.

    Pendant moteur du `publieur_redis` du pont télémétrie : même instance Redis
    (celle du docker-compose par défaut), même canal `maestro.evenements` que
    consomme l'API (`maestro-api`). La connexion est paresseuse (ouverte à la
    première demande).
    """
    return ValidateurControlTower(RedisEventBus(url))

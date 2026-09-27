"""Le rattrapage d'une tâche en échec — ce que le Chef de projet lit, et ce qu'il rend (#1178).

Avant ce ticket, une tâche en échec barrait tout ce qui dépendait d'elle, et ses
nouvelles tentatives étaient **aveugles** : trois essais identiques, toute erreur
étant présumée passagère hors d'une courte liste (`maestro.engine.retry`). Un accès
refusé, un modèle inconnu ou un contexte trop long échouaient donc trois fois à
l'identique, puis la chaîne aval s'arrêtait, et la seule issue était de relancer le
run entier.

Ce module porte le **contrat** du geste qui manquait : le Chef de projet lit la
cause et juge ce qui s'est passé, puis dit ce qu'on change avant de retenter. Il
vit ici, à côté de `Brief` et de `Clarification`, pour la raison qui les y a
rangés : ce sont des valeurs du domaine de l'orchestrateur, que son prompt lit et
que la boucle ne fait que transporter — l'inverse obligerait `maestro.orchestrator`
à importer `maestro.engine`, à contresens des dépendances du paquet.

## Trois natures, des gestes — et pourquoi ce ne sont pas des catalogues

La **nature** est le jugement que le ticket demande en toutes lettres : *passager*,
*configuration* ou *approche*. Elle ne classe pas l'erreur à sa place — c'est le
modèle qui lit la cause, jamais un motif sur son texte (#1315) — et une seule de
ses valeurs décide d'une ligne de code : **seul un échec passager se rejoue à
l'identique**. Les deux autres disent pourquoi il faut changer quelque chose.

Les **gestes** sont les verbes que Maestro sait exécuter, pas une liste de causes :
rejouer, retenter autrement (une tâche reprise, ou redécoupée en plusieurs),
demander à l'utilisateur, abandonner sur sa réponse — et, depuis #1181, lui
proposer le prérequis qui manque (ci-dessous). Ce qui varie à l'infini — la
nouvelle approche, les compétences qui routent vers un autre agent, le
redécoupage, l'ajustement des tâches qui attendent — s'écrit **dans** le geste, en
tâches au format du plan, et c'est le modèle qui l'écrit.

## Ce que l'exécution vérifie de ce qu'il écrit

`valide_rattrapage` tient les règles que le modèle pourrait oublier, en Python
donc quoi qu'il ait répondu — la même garantie que `Brief.questions_en_hypotheses`
donne au plafond de clarification :

- un échec jugé autre que passager **ne se rejoue pas** à l'identique — sauf
  après une réponse de l'utilisateur, qui a pu changer l'environnement (#1181) ;
- une nouvelle tentative **change quelque chose** au regard de celles qui ont déjà
  échoué — l'approche (description), le métier (compétences), ou le découpage ;
- elle n'ajoute **aucun acte accordé** que la tâche d'origine ne portait pas : un
  rattrapage ne sert jamais à contourner un arbitrage (#1198) ;
- on n'**abandonne** que sur une réponse de l'utilisateur : ce que Maestro ne sait
  pas lever, il le demande, il ne le barre pas en silence.

## Un cinquième geste : proposer ce qui manque (#1181)

Un agent qui bute le **dit** (`signaler_blocage`, #719) — « il me manque le jeton
Stripe », « personne ici ne sait faire le design » —, et cette raison ne faisait
que s'écrire au journal. Elle arrive maintenant au Chef de projet avec l'échec
(`Tentative.blocages`), et il peut en tirer un **prérequis** à proposer : un
secret, un serveur, un outil ou un rôle (`maestro.prerequis`). Ce n'est pas une
question ouverte — `demander` le reste —, c'est une chose nommée, avec la façon de
la donner, que la personne accepte d'un geste ; la tâche reprend alors telle
quelle, sans relancer le run.

Et une réponse de l'utilisateur **change l'environnement** : « c'est fait, le jeton
est dans le coffre » rend légitime de rejouer à l'identique un échec qui n'était
pas passager. Rejouer reste donc réservé à un échec passager — **ou** à une tâche
dont l'utilisateur vient de répondre.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from maestro.orchestrator.errors import RattrapageValidationError, TaskValidationError
from maestro.orchestrator.schema import Task, validate_plan, validate_task
from maestro.prerequis import GENRE_ROLE, GENRES, PrerequisManquant

#: L'échec ne se reproduira pas : un aléa (coupure, processus mort sans cause dans
#: la tâche, limite de débit). Le seul cas où rejouer à l'identique a un sens.
NATURE_PASSAGER = "passager"
#: Ce qui manque est dans l'environnement : un accès, un secret, un modèle, un
#: outil, une limite de contexte. Rejouer ne changerait rien.
NATURE_CONFIGURATION = "configuration"
#: La façon de prendre la tâche échoue : trop grosse, mauvaise hypothèse, mauvais
#: outil, mauvais métier. C'est la tâche qu'il faut reprendre autrement.
NATURE_APPROCHE = "approche"
NATURES = (NATURE_PASSAGER, NATURE_CONFIGURATION, NATURE_APPROCHE)

#: Rejouer la tâche telle quelle — réservé à un échec passager.
GESTE_REJOUER = "rejouer"
#: Retenter autrement : une tâche reprise (autre approche, autre métier) ou
#: plusieurs (redécoupage), qui remplacent celle qui a échoué.
GESTE_RETENTER = "retenter"
#: Demander à l'utilisateur ce que Maestro ne sait pas lever seul.
GESTE_DEMANDER = "demander"
#: Laisser la tâche en échec — seulement sur la réponse de l'utilisateur.
GESTE_ABANDONNER = "abandonner"
#: Proposer à l'utilisateur le prérequis qui manque (#1181) — un secret, un
#: serveur, un outil, un rôle —, puis reprendre la tâche telle quelle.
GESTE_PROPOSER = "proposer"
GESTES = (GESTE_REJOUER, GESTE_RETENTER, GESTE_DEMANDER, GESTE_ABANDONNER, GESTE_PROPOSER)


@dataclass(frozen=True)
class Tentative:
    """Une exécution de la tâche — ou de ce qui la remplaçait — et son échec.

    `taches` est ce qui a été exécuté : la tâche du plan à la première tentative,
    sa version reprise ou les tâches du redécoupage ensuite. C'est ce qui permet de
    juger qu'une proposition **change quelque chose** au regard de ce qui a déjà
    échoué. `geste` dit en quelques mots comment elle avait été prise (« telle que
    planifiée », « rejouée à l'identique »…), pour le prompt et pour la question
    posée dans le fil ; `diagnostic` est ce que le modèle en avait dit.

    `blocages` (#1181) porte ce que l'agent a **signalé** pendant la tentative —
    ce qui lui manquait, dans ses mots. C'est souvent la vraie cause, que
    l'erreur ne dit pas : un agent bloqué rend un livrable vide.
    """

    taches: tuple[Task, ...]
    agent: str
    role: str
    erreur: str
    geste: str = "telle que planifiée"
    diagnostic: str = ""
    blocages: tuple[str, ...] = ()


@dataclass(frozen=True)
class EchecDeTache:
    """Ce que le Chef de projet lit pour juger un échec (#1178).

    `tache` est la tâche **du plan**, telle qu'elle a été planifiée — c'est elle
    que les tâches aval attendent, quoi qu'on exécute à sa place. `tentatives` est
    l'histoire, dans l'ordre : la dernière est celle qui vient d'échouer.

    `aval` sont les tâches qui attendent celle-ci (directement ou non) : le modèle
    peut en ajuster la description quand ce qu'il change en amont change ce
    qu'elles recevront. `objectif` est ce que l'utilisateur a demandé au run —
    une tâche ne se juge pas sans savoir à quoi elle sert.

    `question`/`reponse` portent le dernier échange avec l'utilisateur, quand on
    lui a demandé : sa réponse **fait autorité**, au-dessus de tout diagnostic.
    """

    tache: Task
    tentatives: tuple[Tentative, ...]
    objectif: str = ""
    aval: tuple[Task, ...] = ()
    question: str = ""
    reponse: str | None = None

    @property
    def derniere(self) -> Tentative:
        """La tentative qui vient d'échouer."""
        return self.tentatives[-1]


@dataclass(frozen=True)
class Rattrapage:
    """Le jugement du Chef de projet sur un échec, et ce qu'il fait ensuite (#1178).

    `nature` et `diagnostic` sont le **jugement** : ce qui s'est passé, lu dans la
    cause. `geste` est la **décision**, et ses champs propres :

    - `taches` pour `retenter` — une seule : la tâche reprise autrement ; plusieurs :
      le redécoupage, dont les livrables remplacent ensemble celui qui manquait ;
    - `ajustements` pour `retenter` aussi : les tâches aval dont la description
      change, parce que ce qu'elles recevront a changé ;
    - `question` pour `demander` — ce qu'on attend de l'utilisateur ;
    - `prerequis` pour `proposer` (#1181) — ce qui manque, et comment le donner.
    """

    nature: str
    diagnostic: str
    geste: str
    taches: tuple[Task, ...] = ()
    ajustements: tuple[tuple[str, str], ...] = ()
    question: str = ""
    prerequis: PrerequisManquant | None = None

    @property
    def rejoue(self) -> bool:
        """Le verdict est-il de rejouer la tâche telle quelle (échec passager) ?"""
        return self.geste == GESTE_REJOUER

    @property
    def execute(self) -> bool:
        """Le verdict engage-t-il une nouvelle exécution (rejouer ou retenter) ?"""
        return self.geste in (GESTE_REJOUER, GESTE_RETENTER)


def valide_rattrapage(data: Mapping[str, Any], echec: EchecDeTache) -> Rattrapage:
    """Valide la réponse brute du modèle contre ce que l'exécution exige (#1178).

    Lève `RattrapageValidationError` avec un message qui dit **ce qui a été
    refusé** : il est consigné au journal, et c'est lui qui explique pourquoi la
    suite est une question plutôt qu'une nouvelle tentative.
    """
    if not isinstance(data, Mapping):
        raise RattrapageValidationError("le rattrapage n'est pas un objet JSON.")
    nature = _texte(data, "nature")
    if nature not in NATURES:
        raise RattrapageValidationError(
            f"nature inconnue {nature!r} (attendues : {', '.join(NATURES)})."
        )
    geste = _texte(data, "geste")
    if geste not in GESTES:
        raise RattrapageValidationError(
            f"geste inconnu {geste!r} (attendus : {', '.join(GESTES)})."
        )
    diagnostic = _texte(data, "diagnostic")
    if not diagnostic:
        raise RattrapageValidationError("le diagnostic est vide.")
    if geste == GESTE_REJOUER and nature != NATURE_PASSAGER and echec.reponse is None:
        # Une réponse de l'utilisateur peut avoir changé l'environnement (#1181) :
        # un accès accordé, un secret fourni. Sans elle, rien n'a bougé.
        raise RattrapageValidationError(
            f"un échec jugé « {nature} » ne se rejoue pas à l'identique : "
            "il faut changer quelque chose, ou demander."
        )
    if geste == GESTE_ABANDONNER and echec.reponse is None:
        raise RattrapageValidationError(
            "abandonner n'appartient qu'à l'utilisateur : ce que Maestro ne sait pas "
            "lever se demande, il ne se barre pas en silence."
        )
    question = _texte(data, "question")
    if geste == GESTE_DEMANDER and not question:
        raise RattrapageValidationError("demander sans question : rien à poser.")
    taches: tuple[Task, ...] = ()
    ajustements: tuple[tuple[str, str], ...] = ()
    if geste == GESTE_RETENTER:
        taches = _taches_de_rattrapage(data.get("taches"), echec)
        ajustements = _ajustements(data.get("aval"), echec)
    prerequis = _prerequis(data.get("prerequis")) if geste == GESTE_PROPOSER else None
    return Rattrapage(
        nature=nature,
        diagnostic=diagnostic,
        geste=geste,
        taches=taches,
        ajustements=ajustements,
        question=question if geste == GESTE_DEMANDER else "",
        prerequis=prerequis,
    )


def _prerequis(brut: Any) -> PrerequisManquant:
    """Le prérequis que le Chef de projet propose, validé (#1181).

    Il doit **nommer** ce qui manque (`objet`), dire pourquoi (`raison`), et être
    de l'un des genres que Maestro sait proposer (`maestro.prerequis.GENRES`) — un
    remède qu'on ne saurait pas montrer dans le fil n'est pas un prérequis, c'est
    une question. Un rôle nomme en plus les compétences qu'il couvrirait : c'est
    par elles que l'équipe le recrute, jamais par son libellé.
    """
    if not isinstance(brut, Mapping):
        raise RattrapageValidationError("proposer sans « prerequis » : rien à proposer.")
    genre = _texte(brut, "genre")
    if genre not in GENRES:
        raise RattrapageValidationError(
            f"genre de prérequis inconnu {genre!r} (attendus : {', '.join(GENRES)})."
        )
    objet = _texte(brut, "objet")
    raison = _texte(brut, "raison")
    if not objet or not raison:
        raise RattrapageValidationError(
            "un prérequis nomme ce qui manque (« objet ») et pourquoi (« raison »)."
        )
    competences: tuple[str, ...] = ()
    if genre == GENRE_ROLE:
        brutes = brut.get("competences")
        competences = tuple(
            c.strip() for c in brutes if isinstance(c, str) and c.strip()
        ) if isinstance(brutes, list) else ()
        if not competences:
            raise RattrapageValidationError(
                "un rôle à recruter nomme les compétences qu'il couvrirait (« competences »)."
            )
    return PrerequisManquant(
        genre=genre,
        objet=objet,
        raison=raison,
        procedure=_texte(brut, "procedure"),
        competences=competences,
    )


def _texte(data: Mapping[str, Any], cle: str) -> str:
    """La valeur texte de `cle`, nettoyée — vide si absente ou d'un autre type."""
    valeur = data.get(cle)
    return valeur.strip() if isinstance(valeur, str) else ""


def _taches_de_rattrapage(brutes: Any, echec: EchecDeTache) -> tuple[Task, ...]:
    """Les tâches qui remplacent celle qui a échoué, validées comme un plan.

    Même contrat que le plan (`task.schema.json`, ids uniques, dépendances
    résolubles **entre elles**, graphe acyclique) : `validate_plan` le tient déjà,
    et une seconde règle écrite ici finirait par dire autre chose que lui. Les
    tâches ne dépendent que les unes des autres — ce que la tâche d'origine avait
    reçu de ses propres dépendances, elles le reçoivent toutes.
    """
    if not isinstance(brutes, list) or not brutes:
        raise RattrapageValidationError(
            "retenter sans tâche : dis ce qu'on exécute à la place."
        )
    for index, brute in enumerate(brutes):
        if not isinstance(brute, Mapping):
            raise RattrapageValidationError(
                f"la tâche de rattrapage #{index + 1} n'est pas un objet JSON."
            )
    try:
        for index, brute in enumerate(brutes):
            validate_task(brute, where=f"tâche de rattrapage #{index + 1}")
        taches = validate_plan(brutes)
    except TaskValidationError as exc:
        raise RattrapageValidationError(str(exc)) from exc
    accorde = echec.tache.acte_accorde
    for tache in taches:
        if tache.acte_accorde and tache.acte_accorde != accorde:
            raise RattrapageValidationError(
                f"la tâche {tache.id!r} porte un acte accordé que la tâche d'origine "
                "n'avait pas : un rattrapage ne contourne jamais un arbitrage."
            )
    nouvelle = _empreinte(taches)
    for tentative in echec.tentatives:
        if _empreinte(tentative.taches) == nouvelle:
            raise RattrapageValidationError(
                "la proposition est identique à une tentative qui a déjà échoué : "
                "mêmes descriptions, mêmes compétences — rien n'y a changé."
            )
    return tuple(taches)


def _empreinte(taches: Sequence[Task]) -> frozenset[tuple[str, frozenset[str]]]:
    """Ce qui fait qu'une tentative est « la même » : ses approches et ses métiers.

    La description porte l'approche, les compétences désignent l'agent qui la
    prendra. Le titre et l'identifiant n'en sont pas : les réécrire ne change rien
    à ce qui sera exécuté — ni l'ordre des tâches d'un redécoupage, d'où l'ensemble.
    """
    return frozenset(
        (" ".join(tache.description.split()), frozenset(tache.competences_requises))
        for tache in taches
    )


def _ajustements(bruts: Any, echec: EchecDeTache) -> tuple[tuple[str, str], ...]:
    """Les tâches aval dont la description change — `(id, description)`.

    Une tâche aval n'a pas encore démarré (elle attend celle qui a échoué) : lui
    donner la description qui tient compte de ce qui a changé en amont est sans
    risque, et c'est ce qui évite de lui faire consommer un livrable qu'on ne lui
    livrera plus. Un identifiant qui n'est pas dans l'aval est refusé : ajuster
    une tâche déjà partie ou étrangère à l'échec réécrirait l'histoire.
    """
    if bruts is None:
        return ()
    if not isinstance(bruts, list):
        raise RattrapageValidationError("« aval » doit être un tableau.")
    connus = {tache.id for tache in echec.aval}
    ajustements: list[tuple[str, str]] = []
    for brut in bruts:
        if not isinstance(brut, Mapping):
            raise RattrapageValidationError("un ajustement d'aval n'est pas un objet JSON.")
        ident = _texte(brut, "id")
        description = _texte(brut, "description")
        if ident not in connus:
            raise RattrapageValidationError(
                f"ajustement de {ident!r}, qui n'attend pas la tâche en échec."
            )
        if not description:
            raise RattrapageValidationError(f"ajustement de {ident!r} sans description.")
        ajustements.append((ident, description))
    return tuple(ajustements)


def tache_reprise(tache: Task, reprise: Task) -> Task:
    """La tâche du plan, reprise autrement — **sous son identifiant**.

    Une reprise à une seule tâche est la même tâche prise autrement : elle garde
    son identifiant, son titre, son ticket, son projet et ses dépendances, si bien
    que sa carte repasse « en cours » puis « terminée » au lieu qu'une autre
    apparaisse à côté. Ce qui change est ce que le modèle a changé : l'approche
    (`description`), le métier (`competences_requises`), le livrable attendu, et
    l'ossature de checklist qui va avec.
    """
    return replace(
        tache,
        description=reprise.description,
        competences_requises=reprise.competences_requises,
        format_sortie=reprise.format_sortie,
        etapes=reprise.etapes,
    )


def taches_redecoupees(tache: Task, taches: Sequence[Task], tour: int) -> tuple[Task, ...]:
    """Les tâches d'un redécoupage, rangées sous la tâche du plan qu'elles remplacent.

    Les identifiants sont préfixés (`<tâche>-r<tour>-<id>`) : ceux que le modèle
    choisit sont locaux au rattrapage, et deux tours pourraient les réemployer —
    ou croiser ceux du plan. Le ticket et le projet sont hérités, comme au
    lancement d'un run.
    """
    prefixe = f"{tache.id}-r{tour}-"
    return tuple(
        replace(
            sous,
            id=f"{prefixe}{sous.id}",
            dependances=tuple(f"{prefixe}{dep}" for dep in sous.dependances),
            ticket=tache.ticket,
            projet_id=tache.projet_id,
        )
        for sous in taches
    )

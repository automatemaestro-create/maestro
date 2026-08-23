"""Relance d'un run interrompu sur son brief déjà validé (#351, lot 4/4 de #347).

La suite **différée** de #349, qui a livré `POST /api/executions/{run_id}/relancer`
sans test dédié. Elle couvre le second critère du lot final — « la relance est
couverte de bout en bout : brief rejoué sans clarification, référence au run repris,
refus sur un run vivant et sur un run soldé ».

**Ni Redis, ni réseau, ni appel de modèle** (`tests/conftest.py`, #195). L'app est la
vraie (`create_app`), sur bus mémoire, journal mémoire et registre de battements
mémoire ; seul le **moteur** est un double, par l'injection prévue à cet effet — sans
elle, la première relance résoudrait un fournisseur et appellerait un modèle, puisque
relancer *est* lancer.

Ce que ce fichier vérifie, et qui ne se voit nulle part ailleurs :

① **Le cadrage repart, et lui seul.** Un run relancé part de la **synthèse** du brief
   retenu, en mode `sans` : c'est la seule forme qui ne repaie ni la rédaction, ni la
   clarification, ni la validation. Ce qui suit le cadrage (projet, ticket) le suit ;
   ce qui appartenait au run mort (ses sources, résolues vers *son* emplacement
   d'ingestion) reste avec lui.

② **Les quatre refus**, chacun pour sa raison — et le cinquième cas, `indetermine`,
   qui **passe** : les quatre runs fantômes du 2026-08-17 sont tous antérieurs au
   battement, et un refus ici rendrait la route inutile précisément pour ceux qui l'ont
   motivée.

③ **Deux pièges que le code a dû éviter**, tous deux invisibles à l'écran. Le soldage
   du run repris doit atteindre le **journal durable** sur une API qui n'a encore rien
   lancé — c'est-à-dire le cas exact d'un run orphelin, dont l'hôte est justement
   tombé — sans quoi le run réapparaît `en_cours` au redémarrage suivant, la panne même
   que #347 traite. Et un **double clic** ne doit pas partir deux fois.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from maestro.controltower import (
    ControlTowerState,
    Event,
    InMemoryEventBus,
    InMemoryEventLog,
    RegistreBattementsMemoire,
    create_app,
)
from maestro.controltower.battement import SEUIL_ORPHELIN_S, horodatage_battement
from maestro.controltower.brief import evenement_demande_brief
from maestro.controltower.events import (
    EVENEMENT_BRIEF_DECISION,
    EVENEMENT_EXECUTION_STATUT,
    ReferenceTicket,
)
from maestro.controltower.executions import (
    MOTIF_RELANCE_RUN_INCONNU,
    MOTIF_RELANCE_RUN_SOLDE,
    MOTIF_RELANCE_RUN_VIVANT,
    MOTIF_RELANCE_SANS_CADRAGE,
)
from maestro.controltower.state import (
    BRIEF_APPROUVE,
    EXECUTION_ANNULEE,
    EXECUTION_EN_ATTENTE_BRIEF,
    EXECUTION_EN_COURS,
    EXECUTION_TERMINEE,
)
from maestro.engine import MODE_BRIEF_HUMAIN, MODE_BRIEF_SANS, DemandeBrief
from maestro.orchestrator import Brief
from maestro.sources import Source

#: Le run **mort** de tous les scénarios — celui dont l'hôte est tombé.
MORT = "3ff0bcb065f9"

#: Plafond d'attente d'un fait asynchrone (pompe de diffusion, journal durable).
#: La boucle de l'app rend la main tout de suite : c'est un ordonnancement qu'on
#: attend, pas un travail — largement au-dessus du nécessaire, jamais atteint
#: quand tout va bien.
DELAI_ATTENTE_S = 5.0

MAINTENANT = datetime(2026, 8, 23, 12, 0, 0, tzinfo=UTC)

#: Le brief **approuvé** du run mort : c'est lui, et rien d'autre, que la relance
#: rejoue. Sept sections renseignées, pour que la synthèse soit reconnaissable dans
#: l'objectif du nouveau run.
BRIEF = Brief.from_dict(
    {
        "objectif": "Prototyper un mini-CRM",
        "perimetre": ["Fiches contacts", "Recherche"],
        "hors_perimetre": ["Facturation"],
        "contraintes": ["Python 3.11"],
        "criteres_acceptation": ["Une fiche se crée et se relit"],
        "hypotheses": ["Une seule langue"],
        "questions": [],
    }
)

TICKET = ReferenceTicket(id="#42", url="https://exemple.test/issues/42")
PROJET = "prj-7f3a1c2b"


# ------------------------------------------------------------------ doubles


class MoteurEnVol:
    """Moteur injecté qui ne rend **jamais** la main : le run relancé reste en cours.

    Deux raisons, et la seconde est celle qui compte ici. Sans injection, relancer
    résoudrait un vrai fournisseur et appellerait un modèle, ce que
    `tests/conftest.py` (#195) interdit. Et un run qui se solderait pendant le test
    rendrait `vitalite: null` et une `fin` posée au lieu de l'état qu'on vient
    vérifier : la course serait tranchée par l'ordonnanceur, pas par le code.

    Il **retient** ce qu'on lui donne (objectif, régime de brief, ticket, projet) :
    c'est le seul observable qui dise que la relance a rejoué la synthèse du brief,
    et non l'objectif brut du run mort.
    """

    def __init__(self) -> None:
        self.objectifs: list[str] = []
        self.modes_brief: list[str] = []
        self.tickets: list[ReferenceTicket | None] = []
        self.projets: list[str | None] = []

    def __call__(self, **_reglages: object) -> MoteurEnVol:
        return self

    async def run(
        self,
        objectif: str,
        *,
        journal: object = None,
        ticket: ReferenceTicket | None = None,
        projet_id: str | None = None,
        mode_brief: str = MODE_BRIEF_SANS,
    ) -> None:
        self.objectifs.append(objectif)
        self.modes_brief.append(mode_brief)
        self.tickets.append(ticket)
        self.projets.append(projet_id)
        await asyncio.Event().wait()


# ------------------------------------------------------------------ décor


def _il_y_a(secondes: float) -> str:
    """L'horodatage d'un battement posé `secondes` avant maintenant."""
    return horodatage_battement(datetime.now(UTC) - timedelta(seconds=secondes))


def _lancement(**surcharges: object) -> Event:
    """L'événement de lancement du run mort — celui qui le fait exister."""
    champs: dict[str, object] = {
        "type": EVENEMENT_EXECUTION_STATUT,
        "run_id": MORT,
        "titre": "Fais-moi un CRM",
        "agent": "orchestrateur",
        "role": "Orchestrateur",
        "statut": EXECUTION_EN_COURS,
        "ticket": TICKET,
        "projet_id": PROJET,
        "mode_brief": MODE_BRIEF_HUMAIN,
    }
    champs.update(surcharges)
    return Event(**champs)  # type: ignore[arg-type]


def _evenements_du_run_mort(*, approuve: bool = True) -> list[Event]:
    """La trace d'un run qui a payé son cadrage, puis dont l'hôte est tombé.

    Trois événements, dans l'ordre où ils sont arrivés en vrai : le lancement, la
    demande de brief (le run se suspend), et la décision humaine. **Rien après** —
    c'est tout le sujet : un hôte qui tombe ne publie pas « je suis mort », donc la
    projection en reste au dernier fait connu, `en_cours`.

    `approuve=False` s'arrête juste avant la décision : le run est mort **pendant**
    l'attente. Il porte alors un brief — celui qui était *proposé* — sans que
    personne ne l'ait validé, et c'est précisément la distinction que
    `brief_approuve` existe pour tenir.
    """
    evenements = [
        _lancement(),
        evenement_demande_brief(
            DemandeBrief(run_id=MORT, objectif="Fais-moi un CRM", brief=BRIEF)
        ),
    ]
    if approuve:
        evenements.append(
            Event(
                type=EVENEMENT_BRIEF_DECISION,
                run_id=MORT,
                statut=BRIEF_APPROUVE,
                brief=BRIEF,
            )
        )
    return evenements


def _journal(*evenements: Event) -> InMemoryEventLog:
    """Un journal durable portant `evenements` — l'app les rejouera au démarrage.

    C'est par là, et non par la projection, que le décor est posé : un run orphelin
    est justement un run dont *rien en mémoire* ne subsiste, et le seul chemin par
    lequel une API redémarrée le retrouve est le rejeu du journal (#97).
    """
    journal = InMemoryEventLog()

    async def _consigner() -> None:
        for evenement in evenements:
            await journal.consigner(evenement)

    asyncio.run(_consigner())
    return journal


def _app(
    journal: InMemoryEventLog,
    battements: RegistreBattementsMemoire,
    moteur: MoteurEnVol,
    state: ControlTowerState | None = None,
) -> TestClient:
    """L'app réelle sur bus, journal et registre mémoire — seul le moteur est double."""
    return TestClient(
        create_app(
            bus=InMemoryEventBus(),
            state=state if state is not None else ControlTowerState(),
            event_log=journal,
            battements=battements,
            fabrique_moteur=moteur,
        )
    )


def _decor(
    *, battement: str | None = None, approuve: bool = True
) -> tuple[InMemoryEventLog, RegistreBattementsMemoire, MoteurEnVol]:
    """Le décor commun : un run mort dans le journal, son battement, un moteur double.

    `battement=None` laisse le run **sans aucun battement** — donc `indetermine`,
    l'état des quatre runs fantômes du 2026-08-17, tous antérieurs à #348. Un
    horodatage vieilli le rend `orphelin`, un horodatage frais le rend `vivant`.
    """
    battements = RegistreBattementsMemoire()
    if battement is not None:
        asyncio.run(battements.battre(MORT, horodatage=battement))
    return _journal(*_evenements_du_run_mort(approuve=approuve)), battements, MoteurEnVol()


def _attendre(condition, quoi: str) -> None:
    """Attend qu'un fait **asynchrone** advienne — pompe de diffusion, journal.

    Le REST répond sur la projection, déjà à jour ; la persistance, elle, passe par
    le bus puis la pompe. Un test qui lit le journal juste après la réponse HTTP
    court après un ordonnancement, pas après un travail.
    """
    limite = time.monotonic() + DELAI_ATTENTE_S
    while not condition():
        if time.monotonic() > limite:  # pragma: no cover - filet anti-blocage
            pytest.fail(f"{quoi} n'est jamais arrivé en {DELAI_ATTENTE_S} s")
        time.sleep(0.02)


def _statuts_journalises(journal: InMemoryEventLog, run_id: str) -> list[str]:
    """Les statuts d'exécution consignés pour `run_id`, dans l'ordre du journal."""
    return [
        e.statut
        for e in asyncio.run(journal.relire())
        if e.type == EVENEMENT_EXECUTION_STATUT and e.run_id == run_id
    ]


# ------------------------------------------- ① le cadrage repart, et lui seul


def test_la_relance_rejoue_la_synthese_du_brief_sans_repasser_par_la_validation():
    """Le critère, mot pour mot : « brief rejoué sans clarification ».

    Deux assertions, et ni l'une ni l'autre ne suffit. Le nouveau run part de la
    **synthèse** du brief retenu — pas de l'objectif brut « Fais-moi un CRM », qui
    ferait tout recommencer y compris les deux tours de clarification déjà payés —,
    et il part en mode **`sans`**, seul régime qui ne repasse ni par la rédaction, ni
    par les questions, ni par l'approbation. Le mode seul laisserait décomposer le
    mauvais texte ; le texte seul laisserait redemander une validation sur un cadrage
    déjà validé.
    """
    journal, battements, moteur = _decor(battement=_il_y_a(SEUIL_ORPHELIN_S + 60))
    with _app(journal, battements, moteur) as client:
        reponse = client.post(f"/api/executions/{MORT}/relancer")

        assert reponse.status_code == 202
        nouveau = reponse.json()
        assert nouveau["run_id"] != MORT  # c'est un nouveau run, pas une résurrection
        # `.strip()` parce que `lancer` élague tout objectif qu'on lui donne, celui-ci
        # comme celui d'un humain : la synthèse finit par un saut de ligne (#318).
        assert nouveau["objectif"] == BRIEF.synthese().strip()
        assert nouveau["statut"] == EXECUTION_EN_COURS

        _attendre(lambda: moteur.objectifs, "le moteur du run relancé")
        assert moteur.objectifs == [BRIEF.synthese().strip()]
        assert moteur.modes_brief == [MODE_BRIEF_SANS]


def test_le_nouveau_run_dit_de_qui_il_est_la_suite_et_le_repris_ne_dit_rien():
    """`reprise_de` se lit dans **un** sens, comme le fichier `reprise-de` de #204.

    Le run repris n'est jamais réécrit pour désigner son successeur : il peut donc
    être repris sans que sa trace change de forme, et un journal rejoué rend la même
    chose qu'au premier passage. Vérifier les deux côtés est le seul moyen de le
    dire — la présence du lien sur le nouveau run ne prouve pas son absence sur
    l'ancien.
    """
    journal, battements, moteur = _decor(battement=_il_y_a(SEUIL_ORPHELIN_S + 60))
    with _app(journal, battements, moteur) as client:
        nouveau = client.post(f"/api/executions/{MORT}/relancer").json()

        assert nouveau["reprise_de"] == MORT
        assert client.get(f"/api/executions/{MORT}").json()["reprise_de"] == ""


def test_le_run_repris_est_solde_et_perd_son_verdict_de_vitalite():
    """« Au lieu de rester `en_cours` » — et « annulée », jamais « échec ».

    Rien n'a raté : son hôte est tombé et quelqu'un a repris la main, exactement
    comme un brief refusé (#320). Le battement part avec l'issue — un run soldé n'a
    plus d'hôte à guetter, et laisser l'entrée le ferait ressortir orphelin dans un
    registre que plus personne n'interroge à son sujet.
    """
    journal, battements, moteur = _decor(battement=_il_y_a(SEUIL_ORPHELIN_S + 60))
    with _app(journal, battements, moteur) as client:
        client.post(f"/api/executions/{MORT}/relancer")

        repris = client.get(f"/api/executions/{MORT}").json()
        assert repris["statut"] == EXECUTION_ANNULEE
        assert repris["fin"] is not None
        assert repris["vitalite"] is None
        assert MORT not in asyncio.run(battements.battements())


def test_le_projet_et_le_ticket_du_run_repris_suivent_le_cadrage():
    """Le travail reprend **là où il portait** : même projet, même ticket.

    Le projet est le critère du ticket — sans lui, le run relancé repartirait hors de
    tout projet, donc sans la racine où son travail s'applique. Le ticket suit pour
    une raison plus simple : ce n'est qu'une référence, et la perdre ferait sortir du
    Kanban du ticket un travail qui n'a pas changé de sujet.
    """
    journal, battements, moteur = _decor(battement=_il_y_a(SEUIL_ORPHELIN_S + 60))
    with _app(journal, battements, moteur) as client:
        nouveau = client.post(f"/api/executions/{MORT}/relancer").json()

        assert nouveau["projet_id"] == PROJET
        assert nouveau["ticket"] == {"id": TICKET.id, "url": TICKET.url}
        _attendre(lambda: moteur.projets, "le moteur du run relancé")
        assert moteur.projets == [PROJET]
        assert moteur.tickets == [TICKET]


def test_les_sources_ne_repartent_pas_avec_le_cadrage():
    """Ce qui appartenait au run mort reste avec lui — et c'est un choix, pas un oubli.

    Une source résolue pointe l'emplacement d'ingestion **du run mort**, propre à son
    `run_id` : la rattacher au nouveau ferait pointer sa matière vers le dossier d'un
    autre. Et elle n'a plus rien à apprendre — le brief a été rédigé *après* l'avoir
    lue, il en est la synthèse validée.
    """
    source = Source(
        type="fichier",
        nom="CDC-v2.docx",
        chemin=f"core/ingestion/{MORT}/CDC-v2.docx",
        taille=184320,
    )
    journal = _journal(
        _lancement(sources=[source]),
        *_evenements_du_run_mort()[1:],
    )
    battements = RegistreBattementsMemoire()
    asyncio.run(battements.battre(MORT, horodatage=_il_y_a(SEUIL_ORPHELIN_S + 60)))

    with _app(journal, battements, MoteurEnVol()) as client:
        assert client.get(f"/api/executions/{MORT}").json()["sources"] != []

        nouveau = client.post(f"/api/executions/{MORT}/relancer").json()

        assert nouveau["sources"] == []


def test_la_relance_survit_au_rejeu_du_journal_durable():
    """Les deux faces du geste sont **durables**, pas des vues en mémoire.

    Une API redémarre : projection neuve, journal rejoué. Le nouveau run doit encore
    dire de qui il est la suite, et le run repris rester soldé — sans quoi le
    rattrapage se déferait au premier redémarrage, ce qui est exactement l'accident
    qu'il répare.
    """
    journal, battements, moteur = _decor(battement=_il_y_a(SEUIL_ORPHELIN_S + 60))
    with _app(journal, battements, moteur) as client:
        neuf = client.post(f"/api/executions/{MORT}/relancer").json()["run_id"]
        _attendre(
            lambda: _statuts_journalises(journal, neuf),
            "le lancement du run relancé au journal",
        )
        _attendre(
            lambda: EXECUTION_ANNULEE in _statuts_journalises(journal, MORT),
            "l'issue du run repris au journal",
        )

    # L'API redémarre : même journal, projection reconstruite à partir de lui seul.
    with _app(journal, RegistreBattementsMemoire(), MoteurEnVol()) as redemarree:
        assert redemarree.get(f"/api/executions/{neuf}").json()["reprise_de"] == MORT
        assert redemarree.get(f"/api/executions/{MORT}").json()["statut"] == (
            EXECUTION_ANNULEE
        )


# --------------------------------------------------------- ② les quatre refus


def test_un_run_inconnu_rend_404():
    """Aucun run de cet identifiant : la projection ne connaît rien à reprendre."""
    journal, battements, moteur = _decor(battement=_il_y_a(SEUIL_ORPHELIN_S + 60))
    with _app(journal, battements, moteur) as client:
        reponse = client.post("/api/executions/jamais-vu/relancer")

        assert reponse.status_code == 404
        assert reponse.json()["detail"]["motif"] == MOTIF_RELANCE_RUN_INCONNU


@pytest.mark.parametrize("statut", [EXECUTION_TERMINEE, EXECUTION_ANNULEE])
def test_un_run_deja_solde_rend_409(statut):
    """Il a rendu son issue : rien à reprendre, et le relancer le dupliquerait.

    Le refus tombe **avant** la lecture du registre : un run soldé n'a pas de verdict
    de vitalité (`vitalite` rend `None`), donc le juger sur son battement reviendrait
    à demander « son hôte bat-il ? » d'un run dont plus personne n'est l'hôte.
    """
    journal = _journal(
        *_evenements_du_run_mort(),
        Event(type=EVENEMENT_EXECUTION_STATUT, run_id=MORT, statut=statut),
    )
    with _app(journal, RegistreBattementsMemoire(), MoteurEnVol()) as client:
        reponse = client.post(f"/api/executions/{MORT}/relancer")

        assert reponse.status_code == 409
        assert reponse.json()["detail"]["motif"] == MOTIF_RELANCE_RUN_SOLDE


def test_un_run_encore_vivant_rend_409():
    """Son hôte bat toujours : le reprendre ferait deux runs sur le même cadrage.

    Le refus s'appuie sur le verdict de `vitalite` (#348) et sur lui seul —
    re-déduire l'orphelinat ici donnerait une seconde formule à tenir d'accord avec la
    première, ce que le lot 1 existe précisément pour éviter.
    """
    journal, battements, moteur = _decor(battement=_il_y_a(5))
    with _app(journal, battements, moteur) as client:
        reponse = client.post(f"/api/executions/{MORT}/relancer")

        assert reponse.status_code == 409
        assert reponse.json()["detail"]["motif"] == MOTIF_RELANCE_RUN_VIVANT
        # Le message dit quoi faire : l'interrompre d'abord si c'est bien voulu.
        assert "annuler" in reponse.json()["detail"]["message"].lower()


def test_un_run_mort_pendant_l_attente_de_validation_rend_422():
    """Un brief **proposé** n'est pas un brief **approuvé** — et c'est tout l'écart.

    Le run est mort en `en_attente_brief` : le détail porte donc un brief, celui que
    le Chef de projet venait de soumettre, que personne n'a validé. Le relancer
    reviendrait à décomposer un texte que personne n'a lu, c'est-à-dire à sauter la
    validation que le run attendait encore — un nouveau run déguisé en reprise. Le
    cas est le seul qui distingue `brief_approuve` de « `brief` est renseigné », et
    donc le seul qui justifie l'existence du champ.
    """
    journal, battements, moteur = _decor(
        battement=_il_y_a(SEUIL_ORPHELIN_S + 60), approuve=False
    )
    with _app(journal, battements, moteur) as client:
        assert client.get(f"/api/executions/{MORT}").json()["statut"] == (
            EXECUTION_EN_ATTENTE_BRIEF
        )
        assert client.get(f"/api/executions/{MORT}").json()["brief"] is not None

        reponse = client.post(f"/api/executions/{MORT}/relancer")

        assert reponse.status_code == 422
        assert reponse.json()["detail"]["motif"] == MOTIF_RELANCE_SANS_CADRAGE


def test_un_run_sans_aucun_brief_rend_422():
    """Le mode `sans` n'a jamais rédigé de brief : il n'y a rien à rejouer non plus.

    Même refus que ci-dessus par un autre chemin — `brief` est `None`, et pas
    seulement non approuvé. Les deux moitiés de la garde sont éprouvées séparément :
    ne tester que l'une laisserait passer un code qui ne regarderait que l'autre.
    """
    journal = _journal(_lancement(mode_brief=MODE_BRIEF_SANS))
    battements = RegistreBattementsMemoire()
    asyncio.run(battements.battre(MORT, horodatage=_il_y_a(SEUIL_ORPHELIN_S + 60)))

    with _app(journal, battements, MoteurEnVol()) as client:
        reponse = client.post(f"/api/executions/{MORT}/relancer")

        assert reponse.status_code == 422
        assert reponse.json()["detail"]["motif"] == MOTIF_RELANCE_SANS_CADRAGE


def test_un_run_qui_n_a_jamais_battu_se_relance_quand_meme():
    """`indetermine` **passe**, et c'est le choix le moins évident du lot.

    Les quatre runs fantômes du 2026-08-17 sont tous antérieurs au battement : refuser
    ici rendrait la route inutile précisément pour ceux qui l'ont motivée. Le rapport
    de coûts penche du même côté que le seuil de #348 — rejouer un run qui travaillait
    encore coûte un run en double, qu'on annule ; refuser coûte le cadrage,
    définitivement.

    L'**UI**, elle, ne propose pas le geste sur ce verdict (`apps/web/lib/execution.ts`,
    couvert par `apps/web/tests/runs-perdus.test.tsx`) : accepter n'est pas proposer.
    """
    journal, battements, moteur = _decor(battement=None)
    with _app(journal, battements, moteur) as client:
        assert client.get(f"/api/executions/{MORT}").json()["vitalite"] == "indetermine"

        assert client.post(f"/api/executions/{MORT}/relancer").status_code == 202


@pytest.mark.parametrize(
    ("battement", "approuve"),
    [
        pytest.param(_il_y_a(5), True, id="run-vivant"),
        pytest.param(_il_y_a(SEUIL_ORPHELIN_S + 60), False, id="cadrage-absent"),
    ],
)
def test_un_refus_ne_solde_ni_ne_lance_rien(battement, approuve):
    """Un refus laisse le monde **exactement** comme il l'a trouvé.

    C'est ce qui distingue un refus d'un échec en cours de route : le run visé reste
    en vol avec son battement, et aucun run n'est apparu. Sans ce contrôle, un ordre
    d'écriture inversé — solder d'abord, juger ensuite — passerait tous les tests de
    refus ci-dessus tout en détruisant le run qu'il refuse de reprendre.
    """
    journal, battements, moteur = _decor(battement=battement, approuve=approuve)
    with _app(journal, battements, moteur) as client:
        avant = client.get("/api/executions?projet=tous").json()

        assert client.post(f"/api/executions/{MORT}/relancer").status_code in (409, 422)

        assert client.get("/api/executions?projet=tous").json() == avant
        assert asyncio.run(battements.battements()).get(MORT) == battement
        assert moteur.objectifs == []


# ------------------------------------------- ③ deux pièges qui ne se voient pas


@pytest.mark.parametrize("geste", ["relancer", "annuler"])
def test_solder_un_orphelin_atteint_le_journal_sur_une_api_qui_n_a_rien_lance(geste):
    """Le cas **exact** d'un run orphelin — et le défaut qu'il a coûté.

    L'API vient de redémarrer et n'a encore rien lancé : son câblage de publication
    n'est donc pas armé, et `_pousser` **abandonne** les événements tant qu'il ne
    l'est pas. Sans l'armement que `_solder` fait avant de consigner, l'issue du run
    serait appliquée à la projection en mémoire **sans jamais atteindre le bus**,
    donc ni le journal durable (#97) ni le prochain rejeu : le run réapparaîtrait
    `en_cours` au redémarrage suivant, c'est-à-dire la panne même que #347 traite.

    Les **deux** gestes qui soldent sont éprouvés, et pas seulement celui du lot :
    `annuler` (#185) passe par `_solder` sans rien lancer d'autre, `relancer` (#349)
    l'arme aussi de son côté avant de lancer la suite. N'en tester qu'un laisserait
    l'autre chemin découvert — et c'est `annuler` qui est resté le plus longtemps
    seul à emprunter celui-là.

    Le test regarde le **journal**, pas la projection : la projection dirait oui dans
    les deux cas, ce qui est précisément ce qui rendait le défaut invisible.
    """
    journal, battements, moteur = _decor(battement=_il_y_a(SEUIL_ORPHELIN_S + 60))
    with _app(journal, battements, moteur) as client:
        # Rien n'a été lancé depuis ce process : le run mort vient du rejeu.
        assert client.post(f"/api/executions/{MORT}/{geste}").status_code in (200, 202)

        _attendre(
            lambda: EXECUTION_ANNULEE in _statuts_journalises(journal, MORT),
            f"l'issue du run soldé par « {geste} » au journal durable",
        )

    # Le redémarrage suivant, celui qui aurait ressuscité le fantôme.
    with _app(journal, RegistreBattementsMemoire(), MoteurEnVol()) as apres:
        assert apres.get(f"/api/executions/{MORT}").json()["statut"] == EXECUTION_ANNULEE


def test_un_double_clic_ne_relance_pas_deux_fois():
    """Deux requêtes concurrentes, un seul run relancé — par construction, pas par chance.

    Rien n'attend entre la lecture du statut et le soldage : `_solder` commence par
    une écriture synchrone, donc la seconde requête trouve le run déjà `annulee` et
    sort en « déjà soldée ». Deux runs partis sur le même cadrage seraient deux runs
    à annuler à la main, et l'un des deux resterait invisible dans le panneau qui
    vient de disparaître.
    """
    journal, battements, moteur = _decor(battement=_il_y_a(SEUIL_ORPHELIN_S + 60))
    with _app(journal, battements, moteur) as client:
        premiere = client.post(f"/api/executions/{MORT}/relancer")
        seconde = client.post(f"/api/executions/{MORT}/relancer")

        assert premiere.status_code == 202
        assert seconde.status_code == 409
        assert seconde.json()["detail"]["motif"] == MOTIF_RELANCE_RUN_SOLDE

        suites = [
            r
            for r in client.get("/api/executions?projet=tous").json()
            if r["reprise_de"] == MORT
        ]
        assert len(suites) == 1

"""Éteindre Maestro solde ses runs — l'accident ne les touche pas (#486, #700).

La **cinquième porte** de [docs/28](../docs/28-decision-frontiere-execution-run.md),
ouverte par la revue #470 et livrée ici. Depuis #441 un run de la Control Tower vit
dans un process qui **survit à son API**, et c'était toute la valeur du chantier :
relancer après une modification, planter ne tuent plus le travail en cours. Mais les
trois gestes que ce corollaire énumérait n'étaient pas de même nature — `start.sh
--stop` n'est pas un accident, c'est une **décision**, et un run qui lui survit
continue de consommer du quota et d'écrire dans le projet sans écran pour le suivre
ni bouton pour l'arrêter.

⚠ **Un quatrième contrôle a changé de camp avec #700** (docs/28 §11.2, 2026-08-28) :
**fermer la fenêtre du navigateur solde aussi**. Deux choses l'ont fait basculer —
#699 a mesuré que la survie ne préserve plus le run mais lui fait *perdre son
historique* (bus Pub/Sub éphémère, journal durable alimenté par la seule pompe de
l'API), et le chien de garde #149 coupait déjà l'API et l'UI avec la fenêtre, ce
qu'on n'appelle pas un accident. Ce qui reste subi, et que ce fichier garde
inchangé : le **redémarrage** (`arreter_session`, rejouée pour remplacer la session
précédente), le plantage, le `SIGTERM`.

**Ni Redis, ni réseau, ni appel de modèle, ni process de run** (`tests/conftest.py`,
#195). L'app est la vraie (`create_app`), sur bus, journal et registre de battements
mémoire ; seul l'**hôte** est un double, par l'injection prévue à cet effet — ce qu'on
éprouve ici est *qui décide d'éteindre*, pas *comment on éteint un groupe de process*,
qui est le sujet de `tests/test_hote_detache.py` et s'y joue sur de vrais process.

Ce que ce fichier garde, et qui ne se voit nulle part ailleurs :

① **L'arrêt volontaire solde.** `POST /api/extinction` — la porte, et la seule —
   consigne chaque run en vol `annulee` avec la cause `extinction`, prie l'hôte de
   l'éteindre, solde ses tâches (donc libère ses agents) et retire son battement.
   Jamais laissé `en_cours`, ce qui est le premier critère du ticket.

② **L'arrêt subi ne touche à rien**, et c'est la propriété qu'on ne défait pas
   (#441/#451). Le `lifespan` de l'API — donc un `SIGTERM`, un plantage, l'API qu'on
   tue pour la relancer — passe par `ServiceExecutions.fermer`, qui ne solde rien et
   ne demande aucune extinction. Deux tests plutôt qu'un : ici l'API, et là-bas
   l'hôte, sur de vrais process
   (`test_l_extinction_volontaire_emporte_ce_que_fermer_laisse_vivre`).

③ **Ce qui a été éteint se reprend**, par le bouton existant (#349) et sur la seule
   foi de la **cause** — un run délibérément annulé, lui, reste refusé. Le
   laissez-passer est consommé à la reprise, ce qui garde le garde-fou du double clic.

④ **`start.sh` solde depuis ses deux gestes d'arrêt, et depuis eux seuls.** La porte
   est poussée par la branche `--stop` et par le chien de garde qui constate la
   fenêtre fermée (#700) — dans les deux cas **avant** de libérer les ports, après
   quoi l'API qui tient les hôtes n'existe plus. Jamais depuis `arreter_session`, que
   le *démarrage* rejoue pour remplacer la session précédente : l'y mettre solderait
   les runs à chaque relance, c'est-à-dire fabriquerait l'accident qu'on protège.
   Vérifié par la **forme** du script — démarrer demande Redis, l'UI et une fenêtre,
   et le chemin du chien de garde attend qu'un vrai navigateur s'ouvre puis se
   ferme —, chaque contrôle prouvant son motif sur un **échantillon fautif** avant de
   conclure. Ce que la forme ne dit pas, le comportement de `--stop` le dit : c'est
   la **même** fonction `solder_les_runs` que les deux appelants invoquent, et les
   tests ci-dessus l'exercent pour de vrai.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

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
from maestro.controltower.brief import evenement_demande_brief
from maestro.controltower.causes import CAUSE_ANNULATION, CAUSE_EXTINCTION
from maestro.controltower.events import (
    EVENEMENT_BRIEF_DECISION,
    EVENEMENT_EXECUTION_STATUT,
    EVENEMENT_TACHE_STATUT,
)
from maestro.controltower.executions import (
    DELAI_ANNULATION_S,
    MOTIF_RELANCE_RUN_SOLDE,
)
from maestro.controltower.hote import HoteMort, HoteRun, OrdreRun
from maestro.controltower.state import (
    AGENT_LIBRE,
    AGENT_OCCUPE,
    BRIEF_APPROUVE,
    EXECUTION_ANNULEE,
    EXECUTION_EN_COURS,
    EXECUTION_TERMINEE,
    STATUTS_TACHE_TERMINAUX,
)
from maestro.engine import MODE_BRIEF_HUMAIN, STATUT_EN_COURS, DemandeBrief
from maestro.orchestrator import Brief

RACINE = Path(__file__).resolve().parent.parent
SCRIPT = RACINE / "scripts" / "controltower" / "start.sh"
BASH = shutil.which("bash")

#: Plafond d'attente d'un fait **asynchrone** (pompe de diffusion, journal durable).
#: La boucle de l'app rend la main tout de suite : c'est un ordonnancement qu'on
#: attend, pas un travail — jamais atteint quand tout va bien.
DELAI_ATTENTE_S = 5.0

#: Le run **éteint** des scénarios de reprise : celui que Maestro a emporté en
#: partant, et qu'une API redémarrée retrouve dans son journal durable.
ETEINT = "9c1d4a7b20e5"

#: Le brief **approuvé** de ce run : c'est lui, et rien d'autre, que la reprise
#: rejoue. Sans lui la relance refuse en 422, éteint ou pas.
BRIEF = Brief.from_dict(
    {
        "objectif": "Prototyper un mini-CRM",
        "perimetre": ["Fiches contacts"],
        "hors_perimetre": ["Facturation"],
        "contraintes": ["Python 3.11"],
        "criteres_acceptation": ["Une fiche se crée et se relit"],
        "hypotheses": ["Une seule langue"],
        "questions": [],
    }
)


# ------------------------------------------------------------------ le double


class HoteDouble(HoteRun):
    """Un hôte qui ne lance aucun process et **retient tout ce qu'on lui demande**.

    Ce qu'on éprouve dans ce fichier est la décision — *qui* solde, *quand*, *avec
    quelle cause* —, jamais la mécanique d'extinction d'un groupe de process, qui
    demande de vrais process et se joue dans `tests/test_hote_detache.py`. Un double
    ici rend les scénarios déterministes et permet de poser la seule question qui
    compte de ce côté-ci de la frontière : **ce verbe a-t-il été appelé, et lui
    seul ?**

    Il se comporte comme le vrai sur le point qui change les scénarios : un run
    annulé **quitte** `runs_en_vol`, comme le process qui vient d'être éteint quitte
    le registre du lanceur.
    """

    def __init__(self) -> None:
        self.lances: list[str] = []
        self.annules: list[tuple[str, float]] = []
        self.fermetures: list[float] = []

    async def lancer(self, ordre: OrdreRun) -> None:
        self.lances.append(ordre.run_id)

    async def annuler(self, run_id: str, *, delai_s: float) -> bool:
        self.annules.append((run_id, delai_s))
        if run_id not in self.lances:
            return False
        self.lances.remove(run_id)
        return True

    def en_vol(self, run_id: str) -> bool:
        return run_id in self.lances

    def runs_en_vol(self) -> tuple[str, ...]:
        return tuple(self.lances)

    def ramasser(self) -> tuple[HoteMort, ...]:
        return ()

    async def fermer(self, *, delai_s: float) -> None:
        self.fermetures.append(delai_s)


# ------------------------------------------------------------------ le décor


def _app(
    hote: HoteDouble,
    *,
    state: ControlTowerState | None = None,
    journal: InMemoryEventLog | None = None,
    battements: RegistreBattementsMemoire | None = None,
) -> TestClient:
    """L'app réelle sur bus, journal et registre mémoire — seul l'hôte est double."""
    return TestClient(
        create_app(
            bus=InMemoryEventBus(),
            state=state if state is not None else ControlTowerState(),
            event_log=journal if journal is not None else InMemoryEventLog(),
            battements=battements if battements is not None else RegistreBattementsMemoire(),
            hote_run=hote,
        )
    )


def _lancer(client: TestClient, objectif: str = "Prototyper un mini-CRM") -> str:
    """Lance un run par la vraie route et rend son `run_id`."""
    reponse = client.post("/api/executions", json={"objectif": objectif})
    assert reponse.status_code == 202, reponse.text
    return str(reponse.json()["run_id"])


def _resume(client: TestClient, run_id: str) -> dict[str, Any]:
    """Le résumé d'un run, lu par la route qui le sert."""
    reponse = client.get(f"/api/executions/{run_id}")
    assert reponse.status_code == 200, reponse.text
    return dict(reponse.json())


def _tache_en_vol(state: ControlTowerState, run_id: str, tache_id: str, agent: str) -> None:
    """Donne au run une tâche **en cours**, portée par un agent occupé.

    Posée sur la projection plutôt que par un moteur : ce qu'on veut ici est l'état
    qu'un run en vol présente au moment où on éteint, pas le chemin qui l'y a mené.
    """
    state.appliquer(
        Event(
            type=EVENEMENT_TACHE_STATUT,
            run_id=run_id,
            tache_id=tache_id,
            titre="Modéliser les fiches",
            agent=agent,
            role="Développeur",
            statut=STATUT_EN_COURS,
        )
    )


def _journal_du_run_eteint(
    *, approuve: bool = True, cause: str = CAUSE_EXTINCTION
) -> InMemoryEventLog:
    """La trace d'un run cadré, approuvé, puis **soldé par l'extinction de Maestro**.

    C'est par le journal durable et non par la projection que le décor est posé, et
    c'est le scénario même du troisième critère : Maestro s'est éteint, l'API a
    redémarré, et tout ce qu'elle sait de ce run est ce qu'elle vient de rejouer
    (#97). `cause` permet de rejouer le **même** run soldé par une annulation
    ordinaire — le seul champ qui les distingue, et donc le seul qui décide.
    """
    journal = InMemoryEventLog()
    evenements = [
        Event(
            type=EVENEMENT_EXECUTION_STATUT,
            run_id=ETEINT,
            titre="Fais-moi un CRM",
            agent="orchestrateur",
            role="Orchestrateur",
            statut=EXECUTION_EN_COURS,
            mode_brief=MODE_BRIEF_HUMAIN,
        ),
        evenement_demande_brief(
            DemandeBrief(run_id=ETEINT, objectif="Fais-moi un CRM", brief=BRIEF)
        ),
    ]
    if approuve:
        evenements.append(
            Event(
                type=EVENEMENT_BRIEF_DECISION,
                run_id=ETEINT,
                statut=BRIEF_APPROUVE,
                brief=BRIEF,
            )
        )
    evenements.append(
        Event(
            type=EVENEMENT_EXECUTION_STATUT,
            run_id=ETEINT,
            agent="orchestrateur",
            role="Orchestrateur",
            statut=EXECUTION_ANNULEE,
            detail="Maestro s'est éteint",
            cause=cause,
        )
    )

    async def _consigner() -> None:
        for evenement in evenements:
            await journal.consigner(evenement)

    asyncio.run(_consigner())
    return journal


def _attendre(condition, quoi: str) -> None:
    """Attend qu'un fait **asynchrone** advienne — pompe de diffusion, journal."""
    limite = time.monotonic() + DELAI_ATTENTE_S
    while not condition():
        if time.monotonic() > limite:  # pragma: no cover - filet anti-blocage
            pytest.fail(f"{quoi} n'est jamais arrivé en {DELAI_ATTENTE_S} s")
        time.sleep(0.02)


# ------------------------------------------- ① l'arrêt volontaire solde les runs


def test_l_extinction_solde_chaque_run_en_vol_avec_sa_cause() -> None:
    """Le premier critère, mot pour mot : « jamais laissé `en_cours` ».

    Trois faits en un, et aucun ne suffit seul. Le run est **soldé** — statut
    terminal, `fin` posée —, il l'est avec la **cause** qui dit *qui* l'a arrêté, et
    son hôte a été **prié de l'éteindre** dans le même geste. Un run soldé sans
    extinction continuerait de tourner sous un statut qui dit le contraire ; un run
    éteint sans issue resterait `en_cours` pour toujours.

    Deux runs plutôt qu'un : l'extinction porte sur *tout* ce que l'API tient, et un
    seul run ne dirait pas si la boucle s'arrête au premier.
    """
    hote = HoteDouble()
    with _app(hote) as client:
        premier = _lancer(client, "Prototyper un mini-CRM")
        second = _lancer(client, "Écrire la doc")

        reponse = client.post("/api/extinction")

        assert reponse.status_code == 200, reponse.text
        assert reponse.json()["nb"] == 2
        assert {r["run_id"] for r in reponse.json()["runs"]} == {premier, second}
        for run_id in (premier, second):
            resume = _resume(client, run_id)
            assert resume["statut"] == EXECUTION_ANNULEE, run_id
            assert resume["cause"] == CAUSE_EXTINCTION, run_id
            assert resume["fin"], f"{run_id} soldé sans heure de fin"
    # L'hôte a reçu l'ordre d'éteindre chacun, borné comme une annulation : c'est ce
    # verbe qui, sur l'hôte détaché, emporte le groupe de process et sa descendance
    # (`hote_detache._eteindre`, éprouvé sur de vrais process dans test_hote_detache).
    assert sorted(hote.annules) == sorted(
        [(premier, DELAI_ANNULATION_S), (second, DELAI_ANNULATION_S)]
    )


def test_l_extinction_solde_les_taches_du_run_donc_libere_ses_agents() -> None:
    """Ce que le run portait s'éteint avec lui (#466) — par le geste commun, pas par une copie.

    Le correctif de #466 vit dans `_solder` et non chez ses appelants, précisément
    pour qu'un troisième appelant en hérite sans une ligne. C'est ce que ce test
    garde : posée dans `annuler`, la libération aurait laissé l'extinction solder un
    run sans libérer ses agents, et la panne aurait survécu à sa propre correction
    par le chemin neuf.
    """
    state = ControlTowerState()
    hote = HoteDouble()
    with _app(hote, state=state) as client:
        run_id = _lancer(client)
        _tache_en_vol(state, run_id, "modeliser-les-fiches", "dev")
        agent = state.agent("dev")
        assert agent is not None
        assert agent.statut == AGENT_OCCUPE

        assert client.post("/api/extinction").status_code == 200

        # Le soldage des tâches se **pousse** sur le bus (#466) au lieu de s'appliquer
        # à la projection : c'est la pompe qui l'applique, donc un ordonnancement
        # qu'on attend et non un travail.
        _attendre(
            lambda: agent.statut == AGENT_LIBRE,
            "l'agent libéré par le soldage des tâches du run éteint",
        )
        tache = state.tache("modeliser-les-fiches")
        assert tache is not None
        assert tache.statut in STATUTS_TACHE_TERMINAUX


def test_l_extinction_retire_le_battement_des_runs_qu_elle_solde() -> None:
    """Un run soldé ne bat plus : sans ce retrait, il vieillirait vers `orphelin`.

    L'ordre compte et il est celui de `_solder` : le battement part **après** le
    statut terminal, jamais avant — entre les deux, un lecteur verrait un run encore
    en cours et sans battement, c'est-à-dire un orphelin qui n'en est pas un.
    """
    battements = RegistreBattementsMemoire()
    hote = HoteDouble()
    with _app(hote, battements=battements) as client:
        run_id = _lancer(client)
        asyncio.run(battements.battre(run_id))
        assert run_id in asyncio.run(battements.battements())

        assert client.post("/api/extinction").status_code == 200

        assert run_id not in asyncio.run(battements.battements())


def test_l_issue_de_l_extinction_atteint_le_journal_durable() -> None:
    """Le run doit rester soldé **au redémarrage suivant**, pas seulement en mémoire.

    La projection est reconstruite en rejouant le journal (#97) : une issue appliquée
    en mémoire sans atteindre le bus ferait réapparaître le run `en_cours` à la
    prochaine ouverture — c'est-à-dire exactement au moment où quelqu'un rallume
    Maestro et vient chercher ce qu'il avait éteint.
    """
    journal = InMemoryEventLog()
    hote = HoteDouble()
    with _app(hote, journal=journal) as client:
        run_id = _lancer(client)

        assert client.post("/api/extinction").status_code == 200

        _attendre(
            lambda: any(
                e.type == EVENEMENT_EXECUTION_STATUT
                and e.run_id == run_id
                and e.statut == EXECUTION_ANNULEE
                and e.cause == CAUSE_EXTINCTION
                for e in asyncio.run(journal.relire())
            ),
            "l'issue de l'extinction consignée au journal durable",
        )


def test_eteindre_une_control_tower_au_repos_n_est_pas_une_erreur() -> None:
    """Le cas courant : rien ne tournait. `200` et une liste vide, jamais un échec.

    Un code d'erreur ferait chercher une panne là où il n'y avait rien à éteindre, et
    `start.sh --stop` sortirait en rouge sur l'usage le plus fréquent qu'on en fait.
    """
    with _app(HoteDouble()) as client:
        reponse = client.post("/api/extinction")

    assert reponse.status_code == 200
    assert reponse.json() == {"runs": [], "nb": 0}


def test_un_run_deja_solde_n_est_pas_solde_une_seconde_fois() -> None:
    """L'hôte porte encore un run une fraction de seconde après sa dernière publication.

    Le re-solder écraserait l'issue qu'il vient de rendre — un run **terminé** de
    lui-même deviendrait « annulé par l'extinction », c'est-à-dire un verdict faux sur
    un travail abouti.

    Le décor passe par le **journal**, rejoué au démarrage, et non par un lancement
    suivi d'un statut posé à la main : l'issue d'un lancement voyage par le bus et la
    pompe la réapplique, si bien qu'un `terminee` écrit directement sur la projection
    serait remis `en_cours` par l'événement de lancement qui le rattrape. L'hôte,
    lui, porte encore le run — c'est toute la situation qu'on éprouve.
    """
    journal = InMemoryEventLog()

    async def _consigner() -> None:
        for evenement in (
            Event(
                type=EVENEMENT_EXECUTION_STATUT,
                run_id=ETEINT,
                titre="Fais-moi un CRM",
                agent="orchestrateur",
                role="Orchestrateur",
                statut=EXECUTION_EN_COURS,
            ),
            Event(
                type=EVENEMENT_EXECUTION_STATUT,
                run_id=ETEINT,
                agent="orchestrateur",
                role="Orchestrateur",
                statut=EXECUTION_TERMINEE,
                detail="3/3 tâche(s) réussie(s)",
            ),
        ):
            await journal.consigner(evenement)

    asyncio.run(_consigner())
    hote = HoteDouble()
    hote.lances.append(ETEINT)
    with _app(hote, journal=journal) as client:
        reponse = client.post("/api/extinction")

        assert reponse.json() == {"runs": [], "nb": 0}
        assert _resume(client, ETEINT)["statut"] == EXECUTION_TERMINEE
        assert _resume(client, ETEINT)["cause"] == ""
    assert hote.annules == []


# ------------------------------- ② l'arrêt subi ne touche à rien (#441/#451)


def test_l_arret_de_l_api_ne_solde_aucun_run() -> None:
    """**La propriété qu'on ne défait pas** : un accident ne touche pas au run.

    Relancer après une modification (le démarrage tue l'API en place pour remplacer
    la session précédente), planter, recevoir un `SIGTERM` : ceux-là passent par le
    `lifespan`, donc par `ServiceExecutions.fermer`, qui dit seulement que le service
    se retire. Sortir du `TestClient` **est** cet arrêt, joué par la vraie app.

    ⚠ La fermeture de la fenêtre du navigateur ne fait plus partie de cette liste
    depuis #700 : le chien de garde #149 pousse la porte de l'extinction *lui-même*
    avant de tuer l'API, si bien que ce chemin-ci ne la voit jamais. Ce qui est gardé
    ici est l'arrêt que personne n'a demandé, et lui seul.

    Deux assertions, et la seconde est celle qui garde vraiment. Le run reste
    `en_cours` — mais un run resterait aussi `en_cours` si on l'avait tué sans le
    dire, et c'est précisément la panne que #348 traite. On vérifie donc qu'aucune
    extinction n'a été **demandée** : `fermer` a été appelé, `annuler` jamais.
    """
    state = ControlTowerState()
    hote = HoteDouble()
    with _app(hote, state=state) as client:
        run_id = _lancer(client)

    execution = state.execution(run_id)
    assert execution is not None
    assert execution.statut == EXECUTION_EN_COURS
    assert execution.cause == ""
    assert execution.fin is None
    assert hote.annules == [], "l'arrêt de l'API a demandé une extinction : #441 est défait"
    assert hote.fermetures == [DELAI_ANNULATION_S]


# --------------------------------------- ③ ce qui a été éteint se reprend (#349)


def test_un_run_eteint_se_reprend_par_le_bouton_existant() -> None:
    """Le troisième critère : « reprenable, pas orphelin ».

    Le run est **soldé** — donc invisible pour `vitalite`, et refusé par la règle
    d'origine de #349 (« il a rendu son issue ») — et il repart quand même, sur la
    seule foi de sa cause. C'est le tour de force du ticket : rien de neuf côté UI,
    rien de neuf côté reprise, un mot qui change la réponse d'un refus.
    """
    hote = HoteDouble()
    with _app(hote, journal=_journal_du_run_eteint()) as client:
        reponse = client.post(f"/api/executions/{ETEINT}/relancer")

        assert reponse.status_code == 202, reponse.text
        nouveau = reponse.json()
        assert nouveau["run_id"] != ETEINT
        assert nouveau["reprise_de"] == ETEINT
        # Le cadrage repart, et lui seul : la synthèse du brief approuvé, jamais
        # l'objectif brut « Fais-moi un CRM » qui referait payer la clarification.
        # `strip()` parce que le lancement normalise son objectif, ici comme partout.
        assert nouveau["objectif"] == BRIEF.synthese().strip()


def test_un_run_annule_a_la_main_reste_refuse() -> None:
    """L'exception ne s'étend pas à toutes les annulations, et c'est tout son intérêt.

    Le statut consigné est le **même** (`annulee`) : seule la cause distingue « on a
    éteint l'application qui tenait ce run » de « quelqu'un a arrêté ce run-là ». Les
    confondre reproposerait de reprendre un run que son auteur venait délibérément
    d'annuler — sur le tableau de bord, à chaque rechargement.
    """
    hote = HoteDouble()
    with _app(hote, journal=_journal_du_run_eteint(cause=CAUSE_ANNULATION)) as client:
        reponse = client.post(f"/api/executions/{ETEINT}/relancer")

    assert reponse.status_code == 409, reponse.text
    assert reponse.json()["detail"]["motif"] == MOTIF_RELANCE_RUN_SOLDE


def test_un_run_eteint_sans_brief_approuve_reste_refuse() -> None:
    """L'extinction ouvre la porte du statut, jamais celle du cadrage.

    Un run mort **avant** la validation de son brief n'a rien de payé à rejouer, qu'il
    ait été éteint ou perdu : le relancer reviendrait à repartir de son objectif brut,
    c'est-à-dire à sauter la validation qu'il attendait encore.
    """
    hote = HoteDouble()
    with _app(hote, journal=_journal_du_run_eteint(approuve=False)) as client:
        reponse = client.post(f"/api/executions/{ETEINT}/relancer")

    assert reponse.status_code == 422, reponse.text


def test_le_laissez_passer_de_l_extinction_est_consomme_a_la_reprise() -> None:
    """Le garde-fou du double clic de #349 tient aussi sur un run éteint.

    Il tenait par construction : `_solder` écrit le statut terminal avant tout
    `await`, si bien qu'une seconde requête trouvait le run déjà soldé. Sur un run
    **déjà** soldé, cet appui-là ne dit plus rien — c'est la **cause** qui devient le
    verrou, et la reprise la remplace par `annulation`. Sans ce remplacement, chaque
    rechargement du tableau de bord reproposerait un run déjà repris.
    """
    hote = HoteDouble()
    with _app(hote, journal=_journal_du_run_eteint()) as client:
        assert client.post(f"/api/executions/{ETEINT}/relancer").status_code == 202

        seconde = client.post(f"/api/executions/{ETEINT}/relancer")
        resume = _resume(client, ETEINT)

    assert seconde.status_code == 409, seconde.text
    assert seconde.json()["detail"]["motif"] == MOTIF_RELANCE_RUN_SOLDE
    assert resume["cause"] == CAUSE_ANNULATION


# ------------------------------------- ④ `start.sh` ne solde que sur `--stop`


def _fauxbin(tmp_path: Path, reponse: str = "") -> tuple[Path, Path]:
    """Des shims pour tout ce que `--stop` invoque — et le journal de leurs appels.

    Trois raisons, une par shim. `curl` est **l'observable** : ce test existe pour
    savoir si la porte a été poussée, et par quelle méthode — et il rend au besoin la
    `reponse` d'une API, ce que le script doit savoir relire. `netstat`/`lsof` rendent
    le vide, ce qui n'est pas du confort — sans eux, un `--stop` joué sur des ports
    arbitraires irait tuer les process qui les écoutent vraiment. `taskkill`/`kill`
    sont le filet de ce filet.
    """
    fauxbin = tmp_path / "fauxbin"
    fauxbin.mkdir()
    appels = tmp_path / "appels.log"
    corps = fauxbin / "reponse.json"
    corps.write_text(reponse, encoding="utf-8", newline="\n")
    for nom in ("curl", "taskkill", "kill"):
        rendu = f'\ncat "{corps.as_posix()}"' if nom == "curl" else ""
        shim = fauxbin / nom
        shim.write_text(
            f'#!/usr/bin/env bash\necho "{nom} $*" >> "{appels.as_posix()}"{rendu}\n',
            encoding="utf-8",
            newline="\n",
        )
        shim.chmod(0o755)
    for nom in ("netstat", "lsof"):
        shim = fauxbin / nom
        shim.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8", newline="\n")
        shim.chmod(0o755)
    return fauxbin, appels


def _stop(tmp_path: Path, reponse: str = "", **env_extra: str) -> tuple[str, str]:
    """Joue `start.sh --stop` sous shims, et rend `(stdout, journal des appels)`.

    Le navigateur par défaut est **imposé hors famille Chromium** : le script n'a
    alors ni fenêtre à surveiller ni binaire à interroger, donc aucun appel à
    PowerShell — ce test porte sur l'extinction, pas sur la résolution du navigateur
    (couverte par `test_controltower_start.py`).
    """
    assert BASH is not None
    fauxbin, appels = _fauxbin(tmp_path, reponse)
    environnement = os.environ.copy()
    environnement.pop("MAESTRO_BROWSER", None)
    environnement.pop("MAESTRO_EXTINCTION", None)
    environnement.update(
        {
            "PATH": os.pathsep.join([str(fauxbin), environnement.get("PATH", "")]),
            "TMPDIR": str(tmp_path / "etat"),
            "MAESTRO_BROWSER_DEFAUT": "firefox",
            "MAESTRO_PORT_API": "18456",
            "MAESTRO_PORT_UI": "13456",
        }
    )
    environnement.update(env_extra)
    acheve = subprocess.run(  # noqa: S603 - argv fixe, aucun shell
        [BASH, str(SCRIPT), "--stop"],
        cwd=str(RACINE),
        env=environnement,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert acheve.returncode == 0, acheve.stderr
    journal = appels.read_text(encoding="utf-8") if appels.exists() else ""
    return acheve.stdout, journal


@pytest.mark.skipif(BASH is None, reason="bash introuvable")
def test_stop_pousse_la_porte_de_l_extinction_avant_de_tuer_l_api(tmp_path: Path) -> None:
    """`--stop` appelle la porte, et il l'appelle en POST sur le port de l'API.

    C'est le geste qui manquait : jusqu'ici le script libérait les ports et les runs
    détachés continuaient de tourner derrière une Control Tower éteinte. L'appel doit
    partir **avant** le nettoyage — après, l'API qui tient les hôtes n'existe plus.
    """
    stdout, appels = _stop(tmp_path)

    assert "api/extinction" in appels, f"la porte n'a pas été poussée : {stdout}"
    assert "-X POST" in appels
    assert "127.0.0.1:18456/api/extinction" in appels


@pytest.mark.skipif(BASH is None, reason="bash introuvable")
def test_une_api_muette_n_empeche_pas_d_arreter_le_reste(tmp_path: Path) -> None:
    """Best-effort de bout en bout : le shim rend une réponse vide, le script continue.

    C'est le cas d'un `--stop` joué alors que la fenêtre vient d'être fermée : l'API
    est déjà partie, il n'y a rien à solder par ici, et refuser d'arrêter le reste
    serait faire d'une absence une panne.
    """
    stdout, _ = _stop(tmp_path)

    assert "l'API ne répond pas" in stdout
    assert "Control Tower arrêtée." in stdout


@pytest.mark.skipif(BASH is None, reason="bash introuvable")
def test_stop_nomme_les_runs_qu_il_vient_de_solder(tmp_path: Path) -> None:
    """Ce qui a été éteint se **dit**, run par run, avec ce qu'on en fera.

    Un arrêt muet laisserait chercher plus tard d'où vient une dépense, et pire :
    laisserait croire que le travail en cours a été perdu, alors qu'il attend d'être
    repris. La lecture du JSON se fait sans `jq`, qui n'est pas un prérequis du
    dépôt — c'est du `grep`/`sed`, donc du code à éprouver plutôt qu'à supposer, et
    la réponse rendue ici a la forme exacte de celle de la route (des **résumés**,
    dont bien d'autres champs sont des chaînes).
    """
    reponse = (
        '{"runs":[{"run_id":"1e6a34be830e","objectif":"Prototyper un mini-CRM",'
        '"statut":"annulee","cause":"extinction"},'
        '{"run_id":"4b33ea332e60","objectif":"Ecrire la doc",'
        '"statut":"annulee","cause":"extinction"}],"nb":2}'
    )

    stdout, _ = _stop(tmp_path, reponse=reponse)

    assert "run 1e6a34be830e interrompu" in stdout
    assert "run 4b33ea332e60 interrompu" in stdout
    assert "Reprendre" in stdout
    # Les autres champs du résumé ne sont **pas** pris pour des identifiants :
    # l'extraction porte sur la clé, pas sur la première chaîne venue.
    assert "run annulee" not in stdout
    assert "run Prototyper" not in stdout


@pytest.mark.skipif(BASH is None, reason="bash introuvable")
def test_une_control_tower_au_repos_le_dit_sans_rien_nommer(tmp_path: Path) -> None:
    """Le cas courant vu du script : l'API a répondu, et il n'y avait rien à éteindre.

    À distinguer de l'API muette ci-dessus, qui est une **absence de réponse** : ici
    quelqu'un a répondu « aucun run », et le dire autrement ferait passer une Control
    Tower au repos pour une API en panne.
    """
    stdout, appels = _stop(tmp_path, reponse='{"runs":[],"nb":0}')

    assert "api/extinction" in appels
    assert "aucun run en vol" in stdout


@pytest.mark.skipif(BASH is None, reason="bash introuvable")
def test_l_extinction_se_desactive_et_le_dit(tmp_path: Path) -> None:
    """`MAESTRO_EXTINCTION=0` : une **option** sur un geste qui solde par défaut.

    Elle est annoncée et jamais silencieuse — c'est le mot de docs/28 §11, et la
    différence entre « je pars en laissant tourner » et « quelque chose tourne sans
    que je le sache », qui est le défaut que ce ticket supprime.
    """
    stdout, appels = _stop(tmp_path, '{"runs":[],"nb":0}', MAESTRO_EXTINCTION="0")

    assert "api/extinction" not in appels
    assert "désactivée" in stdout


def _appels_a_solder(texte: str) -> list[int]:
    """Les lignes qui **appellent** `solder_les_runs` — jamais celle qui la définit."""
    return [
        rang
        for rang, ligne in enumerate(texte.splitlines())
        if ligne.strip() == "solder_les_runs"
    ]


def _bloc(texte: str, ouverture: str, fermeture: str) -> tuple[int, int]:
    """Les rangs `[début, fin[` du bloc qui s'ouvre sur `ouverture`.

    Découpage volontairement bête — la première ligne qui *est* `ouverture`, puis la
    première qui *est* `fermeture` après elle, toutes deux en colonne 0. Le script
    indente tout ce qu'il imbrique, donc un `}` ou un `fi` de premier niveau ferme
    bien ce qu'on croit ; un analyseur de shell coûterait une dépendance pour lire
    six lignes.
    """
    lignes = texte.splitlines()
    debut = lignes.index(ouverture)
    fin = lignes.index(fermeture, debut + 1)
    return debut, fin


def _region_arreter_session(texte: str) -> tuple[int, int]:
    return _bloc(texte, "arreter_session() {", "}")


def _region_chien_de_garde(texte: str) -> tuple[int, int]:
    return _bloc(texte, 'if [ "$MODE" = "surveiller" ]; then', "fi")


def test_le_soldage_a_deux_appelants_les_deux_gestes_d_arret() -> None:
    """L'invariant de forme du script, et il vaut mieux qu'un test de comportement.

    Arrêter la Control Tower solde ses runs, et il y a **deux** façons de l'arrêter
    (#700) : `--stop`, et fermer la fenêtre du navigateur — que le chien de garde
    #149 constate. Ni plus (un troisième appelant serait un chemin qui solde sans
    qu'on sache lequel), ni moins.

    On ne le vérifie pas en jouant ces chemins-là — démarrer demande Redis et l'UI,
    et le chien de garde attend qu'un vrai navigateur s'ouvre puis se ferme — mais
    par une propriété plus forte : on **compte** les appelants et on dit où ils sont.
    Aucun autre chemin ne peut alors joindre la porte.

    Le motif est prouvé sur un **échantillon fautif** avant de conclure : un `grep`
    qui ne trouve rien parce qu'il ne sait pas chercher rend le même vert qu'un
    invariant tenu.
    """
    texte = SCRIPT.read_text(encoding="utf-8")
    lignes = texte.splitlines()
    appels = _appels_a_solder(texte)

    assert len(appels) == 2, f"appels trouvés aux lignes {[r + 1 for r in appels]}"

    chien_debut, chien_fin = _region_chien_de_garde(texte)
    dans_le_chien = [rang for rang in appels if chien_debut < rang < chien_fin]
    hors_du_chien = [rang for rang in appels if rang not in dans_le_chien]
    assert len(dans_le_chien) == 1, "le chien de garde ne solde plus la fenêtre fermée"
    assert len(hors_du_chien) == 1
    assert '"$MODE" = "arreter"' in lignes[hors_du_chien[0] - 1], (
        "l'appel hors du chien de garde n'est plus gardé par le mode : la ligne qui "
        f"le précède est {lignes[hors_du_chien[0] - 1]!r}"
    )

    # L'échantillon fautif : le même script, un appel glissé dans `arreter_session`.
    fautif = texte.replace(
        "arreter_session() {\n", "arreter_session() {\n  solder_les_runs\n", 1
    )
    assert len(_appels_a_solder(fautif)) == 3, (
        "le contrôle ne sait pas voir un appelant de plus : son verdict vert ne "
        "prouve rien."
    )


def test_arreter_session_ne_solde_jamais_car_le_demarrage_la_rejoue() -> None:
    """Le seul accident qui reste, et la seule chose que #441 protège encore.

    `arreter_session` est **partagée** : le démarrage la rejoue pour remplacer la
    session en place. Y glisser le soldage solderait donc les runs à chaque relance —
    le geste le plus fréquent du développement —, et la reprise (#349) ne repart pas
    de l'interruption : elle rejoue *toutes* les tâches depuis le brief approuvé, et
    **refuse net** un run qui n'en a pas (mode `auto`), dont le travail serait perdu
    sans retour. C'est ce qui a tranché le sort de cette fonction en #700, quand les
    deux autres gestes ont basculé.

    Le contrôle porte sur le **corps de la fonction**, pas sur le fichier : c'est
    exactement là que la régression s'écrirait, et un `grep` global dirait le
    contraire de ce qu'on veut savoir maintenant qu'il y a deux appelants légitimes.
    Motif prouvé sur un échantillon fautif, l'appel glissé dans ce corps-là.
    """
    texte = SCRIPT.read_text(encoding="utf-8")
    debut, fin = _region_arreter_session(texte)
    appels = _appels_a_solder(texte)

    assert not [rang for rang in appels if debut < rang < fin], (
        "`arreter_session` solde les runs : le démarrage les emporterait à chaque "
        "relance."
    )

    fautif = texte.replace(
        "arreter_session() {\n", "arreter_session() {\n  solder_les_runs\n", 1
    )
    debut_f, fin_f = _region_arreter_session(fautif)
    assert [rang for rang in _appels_a_solder(fautif) if debut_f < rang < fin_f], (
        "le contrôle ne sait pas voir un appel dans `arreter_session` : son verdict "
        "vert ne prouve rien."
    )


def test_le_chien_de_garde_solde_avant_de_liberer_les_ports() -> None:
    """L'ordre **est** le contenu de la décision, ici comme dans la branche `--stop`.

    C'est l'API qui tient les hôtes détachés et sait les éteindre avec leur
    descendance : libérer les ports d'abord, c'est la tuer, donc pousser ensuite une
    porte que plus personne n'ouvre. L'appel partirait, `curl` ne trouverait rien, et
    le script annoncerait « l'API ne répond pas » sur un arrêt qu'il vient lui-même
    de rendre impossible — un run laissé en vol derrière un message rassurant.

    Motif prouvé sur un échantillon fautif : les deux lignes permutées.
    """
    texte = SCRIPT.read_text(encoding="utf-8")

    def _ordre_tenu(source: str) -> bool:
        debut, fin = _region_chien_de_garde(source)
        lignes = source.splitlines()[debut:fin]
        solde = next(
            i for i, ligne in enumerate(lignes) if ligne.strip() == "solder_les_runs"
        )
        libere = next(
            i
            for i, ligne in enumerate(lignes)
            if ligne.strip().startswith("liberer_port")
        )
        return solde < libere

    assert _ordre_tenu(texte), (
        "le chien de garde libère les ports avant de solder : l'API qui tient les "
        "hôtes est déjà morte quand la porte est poussée."
    )

    fautif = texte.replace(
        '  solder_les_runs\n  liberer_port "$PORT_API" "API"\n',
        '  liberer_port "$PORT_API" "API"\n  solder_les_runs\n',
        1,
    )
    assert not _ordre_tenu(fautif), (
        "le contrôle ne sait pas voir l'ordre inversé : son verdict vert ne prouve "
        "rien."
    )


@pytest.mark.skipif(BASH is None, reason="bash introuvable")
def test_le_chien_de_garde_qui_abandonne_ne_solde_rien(tmp_path: Path) -> None:
    """Sans fenêtre à surveiller, il n'y a **rien à arrêter** — donc rien à solder.

    Le seul chemin du chien de garde qu'un test peut jouer de bout en bout : hors du
    mode `isole`, il n'a pas de fenêtre à lui, renonce et sort en `1` sans rien
    toucher. C'est ce qui prouve que le soldage est bien placé **après** ce renoncement
    et après le contrôle du jeton, et non en tête du bloc : là, il solderait les runs
    d'une session qu'il ne surveille même pas.

    Le chemin crée son dossier de journaux avant de renoncer — c'est le script et non
    le test : il vit sous `.maestro/`, gitignoré, et reste vide.
    """
    assert BASH is not None
    fauxbin, appels = _fauxbin(tmp_path)
    environnement = os.environ.copy()
    environnement.pop("MAESTRO_BROWSER", None)
    environnement.pop("MAESTRO_EXTINCTION", None)
    environnement.update(
        {
            "PATH": os.pathsep.join([str(fauxbin), environnement.get("PATH", "")]),
            "TMPDIR": str(tmp_path / "etat"),
            "MAESTRO_BROWSER_DEFAUT": "firefox",
            "MAESTRO_PORT_API": "18457",
            "MAESTRO_PORT_UI": "13457",
        }
    )
    acheve = subprocess.run(  # noqa: S603 - argv fixe, aucun shell
        [BASH, str(SCRIPT), "--chien-de-garde", "jeton-de-test"],
        cwd=str(RACINE),
        env=environnement,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )

    assert acheve.returncode == 1, acheve.stdout
    assert "pas de fenêtre isolée à surveiller" in acheve.stdout
    journal = appels.read_text(encoding="utf-8") if appels.exists() else ""
    assert "api/extinction" not in journal, (
        "un chien de garde qui renonce a quand même soldé les runs"
    )

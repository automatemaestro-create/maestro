"""Le travail se répartit sur l'**équipe du projet** (#1041), née d'une validation.

Lot final du chantier #1021 (#1043) — le lot 5 avait livré sans tests, par la
convention de découpage ([docs/10 §5.1](../docs/10-workflow-git.md)).

Une équipe créée dans un projet (#1040) ne sert à rien tant que le travail ne lui
parvient pas. Ce qui relie les deux tient en quatre points, et ce sont eux qu'on
garde ici :

① **une règle, deux lecteurs** (`catalogue_du_projet`) : l'exécuteur, qui route
   tâche par tâche, et la boucle, qui fait découper l'objectif. Un plan proposé
   sur une équipe et exécuté sur une autre enverrait toutes ses tâches en repli
   « à assigner » sans que rien ne le dise ;
② **le catalogue est remplacé pour l'appel**, jamais trié : le catalogue du
   routeur est figé au câblage, et un agent recruté pour un projet naît après lui
   et ailleurs. L'ordre reçu départage les ex æquo, il ne se réordonne pas ;
③ **ce que personne ne couvre est un fait constaté au routage** — seul endroit
   qui connaisse à la fois les compétences demandées, les agents actifs et
   l'équipe reçue pour cet appel-là. `maestro.equipe.manque` en fait un **poste
   nommé**, parce qu'un signal qui dirait « personne ne couvre `sql`, `migration` »
   demanderait à qui le lit de traduire lui-même ;
④ **un agent ne recrute pas pendant un run** (§3.5 de
   [docs/37](../docs/37-decision-equipe-sur-mesure.md)) : le manque est signalé au
   fil, et recruter reste un geste validé hors du run.

Ni réseau ni appel modèle réel : fournisseur factice, dépôts jetables.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from maestro.agents.catalog import Agent
from maestro.agents.store import AgentDefinition, AgentStore, SurchargeStore, catalogue_du_projet
from maestro.controltower.bridge import evenements_depuis_step
from maestro.controltower.events import EVENEMENT_TACHE_BLOCAGE
from maestro.engine.executor import (
    STATUT_ROLE_MANQUANT,
    SUFFIXE_ETAPE_MANQUE,
    LocalExecutor,
)
from maestro.equipe import RoleManquant, competences_non_couvertes, role_manquant
from maestro.orchestrator.prompt import ORCHESTRATOR_SYSTEM_PROMPT, prompt_orchestrateur
from maestro.orchestrator.schema import Task
from maestro.providers.base import ModelProvider
from maestro.router import Router, assign
from maestro.telemetry import RunJournal

PROJET = "prj-depensio"
AUTRE = "prj-autre"


class _Constant(ModelProvider):
    """Fournisseur factice — aucune exécution réelle n'est attendue ici."""

    name = "constant"

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        return "LIVRABLE"


def _agent(nom: str, *competences: str, role: str = "Rôle") -> Agent:
    return Agent(
        nom=nom,
        role=role,
        competences=frozenset(competences),
        modele="m",
        prompt_systeme="…",
    )


def _tache(id_: str = "t1", *competences: str, projet_id: str | None = None) -> Task:
    return Task(
        id=id_,
        titre=f"Tâche {id_}",
        description=f"Faire {id_}.",
        competences_requises=list(competences),
        format_sortie="markdown",
        dependances=[],
        projet_id=projet_id,
    )


def _fiche(nom: str, *competences: str) -> AgentDefinition:
    return AgentDefinition(
        nom=nom,
        role="Rôle du projet",
        competences=competences,
        playbook="Tu travailles sur ce projet.",
    )


# --- ② Le catalogue de l'appel : l'équipe du projet --------------------------


def test_la_tache_d_un_projet_est_routee_sur_l_equipe_de_ce_projet() -> None:
    """Le catalogue du routeur est figé au câblage ; les agents d'un projet
    naissent après lui, et pas dans le même dossier."""
    cablage = [_agent("cablage", "backend")]
    equipe = [_agent("dev-du-projet", "backend")]

    decision = asyncio.run(
        Router(cablage).route(_tache("t1", "backend"), agents=equipe)
    )

    assert decision.agent is not None
    assert decision.agent.nom == "dev-du-projet"


def test_l_ordre_de_l_equipe_recue_departage_les_ex_aequo() -> None:
    """L'ordre reçu fait foi et ne doit pas être trié ici : c'est lui qui rend la
    règle pure déterministe — à score égal, le premier de l'équipe l'emporte."""
    equipe = [_agent("premier", "backend"), _agent("second", "backend")]

    assert assign(_tache("t1", "backend"), equipe).agent.nom == "premier"
    assert assign(_tache("t1", "backend"), equipe[::-1]).agent.nom == "second"


def test_un_ex_aequo_sans_classifieur_part_en_repli_plutot_qu_au_hasard() -> None:
    """Le routage combiné, lui, ne tranche pas un ex æquo tout seul : sans
    classifieur, la tâche est marquée « à assigner » — jamais routée au hasard."""
    equipe = [_agent("premier", "backend"), _agent("second", "backend")]

    decision = asyncio.run(Router([_agent("cablage", "backend")]).route(
        _tache("t1", "backend"), agents=equipe
    ))

    assert decision.a_assigner
    assert "premier, second" in decision.raison


def test_un_agent_desactive_ne_prend_pas_la_tache_de_son_projet() -> None:
    equipe = [_agent("titulaire", "backend"), _agent("remplacant", "backend")]

    decision = asyncio.run(Router([_agent("cablage", "backend")]).route(
        _tache("t1", "backend"), agents=equipe, exclus={"titulaire"}
    ))

    assert decision.agent is not None
    assert decision.agent.nom == "remplacant"


def test_une_equipe_entierement_desactivee_replie_sans_lever() -> None:
    """Repli explicite plutôt qu'un mauvais routage — et l'équipe a le rôle : il
    est éteint, pas absent, donc rien n'est signalé comme manquant."""
    equipe = [_agent("titulaire", "backend")]

    decision = asyncio.run(Router([_agent("cablage", "backend")]).route(
        _tache("t1", "backend"), agents=equipe, exclus={"titulaire"}
    ))

    assert decision.a_assigner
    assert decision.non_couvertes == ()


# --- ① Une règle, deux lecteurs ----------------------------------------------


def test_sans_projet_il_n_y_a_pas_d_equipe_a_donner(tmp_path: Path) -> None:
    """`None` dit « je n'ai pas d'équipe à te donner » : l'appelant s'en tient au
    catalogue de son câblage."""
    agents = AgentStore(tmp_path / "agents")
    surcharges = SurchargeStore(tmp_path / "surcharges")

    assert catalogue_du_projet(agents, surcharges, None) is None


def test_sans_depots_cables_il_n_y_a_pas_d_equipe_a_donner() -> None:
    """Tests et câblages sans Control Tower : la question ne se pose pas."""
    assert catalogue_du_projet(None, None, PROJET) is None


def test_un_depot_illisible_ne_fait_pas_partir_toutes_les_taches_en_repli(
    tmp_path: Path,
) -> None:
    """Un incident de stockage n'est pas une équipe vide : on garde le catalogue
    du câblage plutôt que de router toutes les tâches nulle part."""
    agents = AgentStore(tmp_path / "agents")
    surcharges = SurchargeStore(tmp_path / "surcharges")

    assert catalogue_du_projet(agents, surcharges, "prj/../evade") is None


def test_l_equipe_d_un_projet_porte_les_agents_de_ce_projet(tmp_path: Path) -> None:
    agents = AgentStore(tmp_path / "agents")
    surcharges = SurchargeStore(tmp_path / "surcharges")
    agents.pour_projet(PROJET).ecrire(_fiche("dev-du-projet", "backend"))
    agents.pour_projet(AUTRE).ecrire(_fiche("dev-du-voisin", "backend"))

    ici = catalogue_du_projet(agents, surcharges, PROJET)

    assert ici is not None
    noms = {agent.nom for agent in ici}
    assert "dev-du-projet" in noms
    assert "dev-du-voisin" not in noms


def test_le_decoupage_se_fait_sur_l_equipe_du_projet() -> None:
    """Le second lecteur de la règle : les rôles et les compétences transmis à
    l'orchestrateur sont ceux de l'équipe qui exécutera. C'est ce qui fait qu'un
    tag proposé au découpage est un tag que quelqu'un sait prendre."""
    equipe = [_agent("dev-du-projet", "comptabilite", role="Comptable")]

    prompt = prompt_orchestrateur(equipe)

    assert "comptabilite" in prompt
    assert "dev-du-projet" in prompt
    assert prompt != ORCHESTRATOR_SYSTEM_PROMPT


# --- ③ Ce que personne ne couvre ---------------------------------------------


def test_les_competences_non_couvertes_sont_le_fait_brut_du_routage() -> None:
    decision = asyncio.run(Router([_agent("cablage", "backend")]).route(
        _tache("t1", "sql", "migration"), agents=[_agent("dev", "backend")]
    ))

    assert decision.a_assigner
    assert decision.non_couvertes == ("migration", "sql")


def test_une_tache_prise_par_quelqu_un_ne_signale_aucun_manque() -> None:
    decision = asyncio.run(Router([_agent("cablage", "backend")]).route(
        _tache("t1", "backend"), agents=[_agent("dev", "backend")]
    ))

    assert decision.agent is not None
    assert decision.non_couvertes == ()


def test_un_ex_aequo_non_departage_n_est_pas_un_manque() -> None:
    """L'ambiguïté est celle du départage, pas d'un rôle absent : la tâche est
    routable, quelqu'un la couvre."""
    equipe = [_agent("un", "backend", "api"), _agent("deux", "backend", "api")]

    decision = asyncio.run(Router([_agent("cablage", "backend")]).route(
        _tache("t1", "backend", "api"), agents=equipe
    ))

    assert decision.a_assigner
    assert decision.non_couvertes == ()


def test_un_agent_desactive_ne_couvre_rien() -> None:
    """Constaté sur les candidats **actifs** : compter les compétences d'un agent
    désactivé ferait taire le signal juste au moment où il est vrai."""
    equipe = [_agent("titulaire", "sql"), _agent("dev", "backend")]

    decision = asyncio.run(Router([_agent("cablage", "backend")]).route(
        _tache("t1", "sql"), agents=equipe, exclus={"titulaire"}
    ))

    assert decision.non_couvertes == ("sql",)


def test_les_competences_non_couvertes_sont_triees() -> None:
    """`Agent.competences` est un `frozenset` : un signal dont l'ordre bouge d'un
    run à l'autre n'est pas comparable."""
    non_couvertes = competences_non_couvertes(
        ("sql", "monitoring", "migration"), [_agent("dev", "backend", "sql")]
    )

    assert non_couvertes == ("migration", "monitoring")


# --- ③ bis Un manque nommé est un poste, pas une liste de tags ---------------


def test_le_manque_nomme_le_gabarit_qui_couvre_le_plus() -> None:
    manque = role_manquant(("sql", "migration"))

    assert manque is not None
    assert manque.gabarit == "donnees"
    assert manque.role == "Base de données"


def test_un_manque_qu_aucun_gabarit_ne_couvre_rend_les_competences_telles_quelles() -> None:
    """Un rôle fabriqué se lirait comme un rôle existant : proposer « Designer »
    pour une tâche qui demande `comptabilite` serait un recrutement au hasard."""
    manque = role_manquant(("comptabilite",))

    assert manque is not None
    assert manque.gabarit is None
    assert manque.role == "comptabilite"


def test_rien_a_combler_ne_nomme_aucun_poste() -> None:
    assert role_manquant(()) is None


def test_la_phrase_du_manque_dit_les_trois_choses_qu_il_faut_pour_agir() -> None:
    """Ce qui manque, le poste que cela désigne, et que le recrutement ne se fait
    pas ici — sans la dernière, le lecteur d'un run peut croire que Maestro va
    s'en charger, et la tâche resterait « à assigner » sans que personne bouge."""
    phrase = RoleManquant(competences=("sql",), role="Expert BDD", gabarit="donnees").phrase()

    assert "sql" in phrase
    assert "gabarit `donnees`" in phrase
    assert "Le recrutement se fait hors du run" in phrase


# --- ④ Le manque arrive au fil, et la tâche reste à assigner -----------------


def _executeur_de_projet(tmp_path: Path, *competences: str) -> LocalExecutor:
    """Un exécuteur dont l'équipe du projet ne couvre que `competences`."""
    agents = AgentStore(tmp_path / "agents")
    surcharges = SurchargeStore(tmp_path / "surcharges")
    agents.pour_projet(PROJET).ecrire(_fiche("dev-du-projet", *competences))
    return LocalExecutor(
        _Constant(),
        agents=[_agent("cablage", "backend")],
        runtimes={},
        agents_store=agents,
        surcharges=surcharges,
    )


def test_une_tache_que_personne_ne_sait_prendre_signale_son_poste(tmp_path: Path) -> None:
    journal = RunJournal()
    executeur = _executeur_de_projet(tmp_path, "backend")

    resultat = asyncio.run(
        executeur.execute(_tache("t1", "comptabilite", projet_id=PROJET), [], journal)
    )

    assert resultat.statut == "echec"
    assert resultat.agent == "—"
    assert "à assigner" in (resultat.erreur or "")
    assert "aucun rôle de l'équipe ne couvre" in (resultat.erreur or "")


def test_le_signal_est_consigne_au_nom_de_l_orchestrateur_et_ne_coute_rien(
    tmp_path: Path,
) -> None:
    """C'est lui qui répartit le travail et lui qui recrute, et la tâche n'a par
    définition aucun agent à nommer. Constater un manque ne dépense rien : le
    routage a échoué avant tout appel modèle."""
    journal = RunJournal()
    executeur = _executeur_de_projet(tmp_path, "backend")

    asyncio.run(executeur.execute(_tache("t1", "comptabilite", projet_id=PROJET), [], journal))

    manque = next(r for r in journal.records if r.etape.endswith(SUFFIXE_ETAPE_MANQUE))
    assert manque.agent == "orchestrateur"
    assert manque.statut == STATUT_ROLE_MANQUANT
    assert (manque.usage.appels, manque.usage.tokens_entree, manque.usage.tokens_sortie) == (
        0,
        0,
        0,
    )
    assert "Le recrutement se fait hors du run" in manque.sortie


def test_une_equipe_qui_couvre_la_tache_ne_signale_aucun_manque(tmp_path: Path) -> None:
    journal = RunJournal()
    executeur = _executeur_de_projet(tmp_path, "comptabilite")

    asyncio.run(executeur.execute(_tache("t1", "comptabilite", projet_id=PROJET), [], journal))

    assert all(not r.etape.endswith(SUFFIXE_ETAPE_MANQUE) for r in journal.records)


def test_le_manque_arrive_au_fil_sans_creer_de_carte_fantome() -> None:
    """Il rejoint `tache.blocage` plutôt que d'ouvrir un type : le fait est le
    même — ça ne peut pas avancer, et il faut quelqu'un. Ce qui l'en distingue est
    le `statut`, qui voyage tel quel."""
    (event,) = evenements_depuis_step(
        {
            "run_id": "run-1",
            "etape": f"t1{SUFFIXE_ETAPE_MANQUE}",
            "nom": "Rôle manquant — Tâche t1",
            "agent": "orchestrateur",
            "role": "Chef de projet",
            "statut": STATUT_ROLE_MANQUANT,
            "entree": "",
            "sortie": "aucun rôle de l'équipe ne couvre : sql — il manque un rôle …",
            "erreur": None,
        }
    )

    assert event.type == EVENEMENT_TACHE_BLOCAGE
    assert event.tache_id == "t1"
    assert event.statut == STATUT_ROLE_MANQUANT


@pytest.mark.parametrize("competence", ["sql", "comptabilite"])
def test_le_moteur_ne_recrute_jamais_de_lui_meme(tmp_path: Path, competence: str) -> None:
    """La frontière de docs/37 §3.5, prise là où elle se tiendrait mal : après un
    manque signalé, l'équipe du projet est **inchangée**."""
    agents = AgentStore(tmp_path / "agents")
    surcharges = SurchargeStore(tmp_path / "surcharges")
    agents.pour_projet(PROJET).ecrire(_fiche("dev-du-projet", "backend"))
    executeur = LocalExecutor(
        _Constant(),
        agents=[_agent("cablage", "backend")],
        runtimes={},
        agents_store=agents,
        surcharges=surcharges,
    )

    asyncio.run(
        executeur.execute(_tache("t1", competence, projet_id=PROJET), [], RunJournal())
    )

    assert agents.pour_projet(PROJET).noms() == ("dev-du-projet",)

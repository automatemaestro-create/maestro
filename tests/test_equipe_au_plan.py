"""Après la décomposition, l'orchestrateur confronte l'équipe au plan (#1227).

Le défaut, mesuré le 2026-09-22 sur le projet `p1` : l'équipe proposée au projet
vide était un seul développeur — ✅ pour la demande d'alors. Puis la personne
demande « une petite animation du logo Maestro ». Le run se décompose en quatre
tâches (logo stylisé, script d'animation, test de lancement, documentation) et
les quatre vont au `dev`. *« Aucune modification de l'équipe n'a été suggérée,
du coup un seul agent développeur a tout fait. »*

Deux moitiés, et il fallait les deux — c'est ce qu'on garde ici :

① **la décomposition planifie pour le besoin.** Le playbook du Chef de projet
   interdisait tout tag qu'un rôle en place ne portait pas (« tu n'emploies que
   les compétences de l'équipe ci-dessous ») : le plan se taillait donc à
   l'équipe, jamais au besoin, et le rôle qui manquait n'était même pas nommé ;
② **l'orchestrateur confronte, puis propose.** Entre le plan et la première
   tâche, le manque est calculé, consigné, et proposé à une personne. Accepter
   complète l'équipe et le run repart avec elle ; décliner ou ne pas répondre
   laisse le run continuer, **en le disant**.

Ce que #1227 renverse, et ce qu'il ne touche pas : les deux dernières puces de
[docs/37 §3](../docs/37-decision-equipe-sur-mesure.md) — « recruter reste un
geste validé hors du run », « l'équipe ne se forme pas dans un run » — tombent.
Rien n'est recruté sans accord (#1040), un **agent** ne recrute jamais
([docs/31 §3.5](../docs/31-decision-surface-ecriture-agents.md) : ici c'est
l'orchestrateur qui propose et une personne qui décide), et le brief est validé
avant la décomposition (D5).

Ni réseau ni appel modèle réel : **faux fournisseur de modèle**, dépôts jetables,
bus mémoire. Le plan de l'essai est rejoué tel quel — un objectif d'animation, un
projet qui n'a qu'un développeur.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from maestro.agents.catalog import GABARITS_DU_CODE, Agent
from maestro.agents.store import AgentDefinition, AgentStore
from maestro.controltower.bridge import evenements_depuis_step
from maestro.controltower.events import EVENEMENT_AGENT_ACTIVITE
from maestro.engine.executor import STATUT_ROLE_MANQUANT
from maestro.engine.loop import OrchestrationEngine
from maestro.engine.renfort import (
    STATUT_RENFORT_DECLINE,
    STATUT_RENFORT_RECRUTE,
    STATUT_RENFORT_SANS_REPONSE,
    DecisionRenfort,
    DemandeRenfort,
)
from maestro.equipe import manque_au_plan, proposer_renfort, role_manquant
from maestro.orchestrator.orchestrator import Orchestrator
from maestro.orchestrator.prompt import prompt_orchestrateur
from maestro.orchestrator.schema import Task
from maestro.outillage.modele import Constats, Langage
from maestro.outillage.recommandation import recommander
from maestro.providers.base import ModelProvider
from maestro.telemetry import ETAPE_EQUIPE, RunJournal

PROJET = "prj-p1"

#: L'objectif de l'essai, mot pour mot.
OBJECTIF = "une petite animation du logo Maestro"

#: Le plan que l'essai a produit — quatre tâches —, mais **écrit pour le besoin**
#: comme le playbook le demande désormais : l'animation et le logo demandent `ui`,
#: que l'équipe d'un seul développeur ne couvre pas.
PLAN = [
    {
        "id": "logo-stylise",
        "titre": "Dessiner le logo stylisé",
        "description": "Objectif… Périmètre… Latitude… Critères…",
        "competences_requises": ["ui", "design-system"],
        "format_sortie": "SVG",
        "dependances": [],
    },
    {
        "id": "script-animation",
        "titre": "Écrire le script d'animation",
        "description": "Objectif… Périmètre… Latitude… Critères…",
        "competences_requises": ["ui"],
        "format_sortie": "JS",
        "dependances": ["logo-stylise"],
    },
    {
        "id": "test-lancement",
        "titre": "Vérifier le lancement",
        "description": "Objectif… Périmètre… Latitude… Critères…",
        "competences_requises": ["frontend"],
        "format_sortie": "rapport",
        "dependances": ["script-animation"],
    },
]


def _agent(nom: str, *competences: str, role: str = "Rôle") -> Agent:
    return Agent(
        nom=nom,
        role=role,
        competences=frozenset(competences),
        modele="m",
        prompt_systeme="…",
    )


def _tache(titre: str, *competences: str) -> Task:
    return Task(
        id=titre.lower().replace(" ", "-"),
        titre=titre,
        description=f"Faire {titre}.",
        competences_requises=competences,
        format_sortie="markdown",
    )


def _fiche(nom: str, *competences: str) -> AgentDefinition:
    return AgentDefinition(
        nom=nom,
        role="Développeur",
        competences=competences,
        playbook="Tu écris le code de p1.",
    )


def _constats() -> Constats:
    """Le projet de l'essai : du Python, et rien qui désigne un designer."""
    return Constats(
        langages=(Langage(nom="Python", fichiers=12, part=1.0, exemple="src/app.py"),)
    )


# --- ① La décomposition nomme le besoin, même hors de l'équipe ---------------


def test_le_playbook_offre_les_metiers_que_maestro_sait_recruter() -> None:
    """Le plan se taillait à l'équipe : un seul développeur, donc jamais de `ui`.

    L'échantillon fautif est la garde d'avant ce lot — « tu n'emploies que les
    compétences de l'équipe ci-dessous » —, qui ne doit plus se lire telle quelle,
    et la liste des métiers recrutables qui doit s'y être substituée.
    """
    dev = next(a for a in GABARITS_DU_CODE if a.nom == "developpeur")

    prompt = prompt_orchestrateur([dev])

    assert "Découpe pour le besoin, pas pour l'équipe" in prompt
    assert "nomme-le quand même" in prompt
    # Les métiers que l'équipe n'a pas y sont, avec leur gabarit.
    assert "ui" in prompt
    assert "gabarit `interface`" in prompt
    # …et la garde d'avant ne s'y lit plus.
    assert "eux seuls, pour `competences_requises`" not in prompt


def test_le_playbook_ne_propose_aucun_renfort_a_une_equipe_complete() -> None:
    """Le témoin : une équipe qui couvre tout le catalogue n'a rien à recruter.

    Et le document le **dit** plutôt que de laisser un blanc — un trou dans un
    prompt système se lit comme un marqueur oublié.
    """
    prompt = prompt_orchestrateur(GABARITS_DU_CODE)

    assert "l'équipe couvre déjà tous les métiers" in prompt


def test_le_playbook_dit_ce_qu_il_advient_d_un_metier_nomme() -> None:
    """Nommer un métier absent n'est pas gratuit : c'est une question posée.

    Le playbook doit dire les deux : que le manque est **proposé** (sinon le
    modèle n'a aucune raison de nommer un tag que personne ne prend), et qu'un
    tag ajouté par précaution coûte une attente humaine.
    """
    prompt = prompt_orchestrateur([_agent("dev", "backend")])

    assert "confronte l'équipe à ce que tu as demandé" in prompt
    assert "par précaution" in prompt


def test_un_plan_qui_nomme_un_metier_absent_est_valide_et_conserve_ses_tags() -> None:
    """Le contrat de sortie ne borne pas les tags, et il ne doit pas commencer.

    Faux fournisseur de modèle : il rend le plan de l'essai, écrit pour le besoin.
    Ce qu'on vérifie est que `ui` traverse la validation de schéma — un tag hors
    équipe est une **information**, pas une erreur de forme.
    """
    plan = asyncio.run(
        Orchestrator(_PlanScripte(), model="m").plan(OBJECTIF, equipe=[_agent("dev", "backend")])
    )

    assert [t.id for t in plan] == ["logo-stylise", "script-animation", "test-lancement"]
    assert "ui" in plan[0].competences_requises


# --- ① bis Le manque du plan : calculé, nommé, et un seul ---------------------


def test_le_manque_du_plan_nomme_le_poste_et_les_taches_qui_l_attendent() -> None:
    """Le fait brut de #1227 : l'union de ce que le plan demande, moins l'équipe.

    Les tâches nommées sont celles que **ce rôle-là** prendrait, jamais toutes
    celles qui demandent quelque chose d'absent : le gabarit retenu ne couvre pas
    forcément tout le manque, et une liste trop large vendrait le recrutement pour
    plus qu'il n'est.
    """
    taches = [
        _tache("Dessiner le logo", "ui", "design-system"),
        _tache("Animer le logo", "ui"),
        _tache("Vérifier le lancement", "backend"),
    ]

    manque = manque_au_plan(taches, [_agent("dev", "backend")])

    assert manque is not None
    assert manque.manque.gabarit == "interface"
    assert manque.manque.role == "Designer"
    assert manque.recrutable
    assert manque.taches == ("Dessiner le logo", "Animer le logo")


def test_le_manque_ne_garde_que_les_taches_du_role_propose() -> None:
    """Deux métiers manquent, un seul poste est proposé — et ses tâches avec lui.

    `sql` n'est pas couvert non plus, mais le designer ne le prendra pas : sa
    tâche n'entre donc pas dans ce que la carte annonce.
    """
    taches = [
        _tache("Dessiner le logo", "ui"),
        _tache("Écrire la migration", "sql", "migration"),
    ]

    manque = manque_au_plan(taches, [_agent("dev", "backend")])

    assert manque is not None
    assert manque.taches in (("Dessiner le logo",), ("Écrire la migration",))
    # Un seul poste, et ses tâches sont les siennes : jamais les deux listes.
    assert len(manque.taches) == 1


@pytest.mark.parametrize(
    ("taches", "equipe"),
    [
        pytest.param(
            [_tache("Ajouter la pagination", "backend")],
            [_agent("dev", "backend")],
            id="tout-est-couvert",
        ),
        pytest.param(
            [_tache("Dessiner le logo", "ui")],
            [],
            id="equipe-vide",
        ),
        pytest.param([], [_agent("dev", "backend")], id="plan-vide"),
    ],
)
def test_rien_ne_manque_quand_rien_ne_manque(
    taches: list[Task], equipe: list[Agent]
) -> None:
    """Trois abstentions, et la deuxième est la plus importante.

    Une **équipe vide** n'est pas une équipe incomplète : ce projet-là se voit
    proposer son équipe entière avant même d'ouvrir un run (#1146), et en déduire
    ici « il manque un rôle » ferait proposer un poste unique là où il faut une
    équipe.
    """
    assert manque_au_plan(taches, equipe) is None


def test_un_manque_sans_gabarit_se_nomme_mais_ne_se_propose_pas() -> None:
    """Maestro ne sait pas recruter pour `comptabilite` : il le dit, sans inventer.

    Un rôle fabriqué se lirait comme un rôle du catalogue, et ce qu'on proposerait
    de créer n'aurait ni playbook ni compétences.
    """
    manque = manque_au_plan(
        [_tache("Tenir les comptes", "comptabilite")], [_agent("dev", "backend")]
    )

    assert manque is not None
    assert not manque.recrutable
    assert "comptabilite" in manque.phrase()


def test_la_phrase_du_journal_nomme_les_taches_et_la_raison_ne_les_redit_pas() -> None:
    """Deux textes, deux lecteurs : le journal raconte, la carte affiche à part.

    La raison part dans la carte du fil, qui liste les tâches dans son propre
    bloc ; les redire dans la phrase ferait lire la même chose comme deux faits.
    """
    manque = manque_au_plan([_tache("Dessiner le logo", "ui")], [_agent("dev", "backend")])

    assert manque is not None
    assert "Dessiner le logo" in manque.phrase()
    assert "Dessiner le logo" not in manque.raison()


# --- ① ter Le rôle proposé est un rôle de plein droit ------------------------


def test_le_renfort_propose_un_seul_role_justifie_par_le_plan() -> None:
    """Un designer n'est justifié par aucun constat d'un projet en Python.

    C'est pour cela qu'il ne peut pas sortir de la dérivation ordinaire : celle-ci
    l'écarterait, et avec raison. Sa justification est le **plan**, que l'appelant
    est seul à connaître.
    """
    constats = _constats()
    manque = role_manquant(("ui", "ux"))
    assert manque is not None

    proposition = proposer_renfort(
        constats,
        recommander(constats),
        manque,
        "le plan de ce travail demande ui, ux",
        projet_id=PROJET,
        noms_pris=("dev",),
    )

    assert [r.gabarit for r in proposition.roles] == ["designer"]
    assert proposition.roles[0].raison == "le plan de ce travail demande ui, ux"
    # Aucune pièce : aucun fichier du projet ne désigne ce rôle, et en inventer
    # une serait pire que de n'en pas avoir.
    assert proposition.roles[0].justification is None
    # Rien n'est créé, et la proposition le dit comme les autres.
    assert proposition.to_dict()["cree"] is False


def test_le_role_de_renfort_porte_playbook_autorisations_et_intention() -> None:
    """Pas une fiche au rabais : tout ce qu'un rôle proposé porte, il le porte."""
    constats = _constats()
    manque = role_manquant(("ui",))
    assert manque is not None

    role = proposer_renfort(
        constats, recommander(constats), manque, "le plan le demande", projet_id=PROJET
    ).roles[0]

    assert role.playbook.strip()
    assert role.playbook_origine
    assert role.intention.strip()
    assert role.competences


def test_un_renfort_sans_gabarit_est_refuse_plutot_qu_invente() -> None:
    constats = _constats()
    manque = role_manquant(("comptabilite",))
    assert manque is not None and manque.gabarit is None

    with pytest.raises(ValueError, match="aucun gabarit ne couvre"):
        proposer_renfort(constats, recommander(constats), manque, "raison")


def test_un_renfort_ecarte_l_orchestrateur_et_personne_d_autre() -> None:
    """Les rôles déjà en place n'ont pas été examinés : les nommer écartés serait
    faux. L'orchestrateur, lui, l'est quel que soit le moment (docs/37 §4.2)."""
    constats = _constats()
    manque = role_manquant(("ui",))
    assert manque is not None

    proposition = proposer_renfort(
        constats, recommander(constats), manque, "raison", projet_id=PROJET
    )

    assert [e.nom for e in proposition.ecartes] == ["orchestrateur"]


# --- ② La boucle confronte, consigne, et repart quoi qu'il arrive ------------


class _PlanScripte(ModelProvider):
    """Faux fournisseur : il rend **le plan de l'essai**, écrit pour le besoin.

    Un seul double pour tout le lot, et il tient les trois rôles que la boucle lui
    demande : décomposer (le plan ci-dessus), **classer** un routage ambigu — le
    repli du routeur sur un agent actif, celui que l'essai a subi — et produire un
    livrable quelconque. C'est ce qui permet de jouer la boucle entière sans
    réseau et sans appel modèle réel.

    Le classifieur est rendu **concluant** à dessein : sans lui, une tâche que
    personne ne couvre partirait « à assigner », et le test ne montrerait plus ce
    qui a motivé le ticket — une animation confiée à un développeur.
    """

    name = "plan-scripte"

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt: str, *, model: str, system_prompt: str | None = None) -> str:
        import json
        import re

        if system_prompt is not None and "Chef de projet" in system_prompt:
            return json.dumps(PLAN)
        candidats = re.findall(r"^- (\S+) \(", prompt, flags=re.MULTILINE)
        if "Agents candidats" in prompt and candidats:
            # Le premier candidat, c'est-à-dire l'ordre de l'équipe reçue : le
            # départage déterministe que le vrai classifieur rendrait en langage.
            return json.dumps({"agent": candidats[0], "confiance": 0.6})
        return "LIVRABLE"


class _ArbitreEspion:
    """Un `ArbitreRenfort` qui note la demande reçue et rend la décision voulue.

    Il **recrute pour de vrai** quand `agents` lui est passé : il écrit la fiche
    dans le dépôt du projet, exactement comme la création réelle (#1040) le fait.
    C'est ce qui permet de vérifier le second critère de bout en bout — les tâches
    vont au nouveau rôle — sans monter l'API.
    """

    def __init__(
        self,
        decision: DecisionRenfort,
        *,
        agents: AgentStore | None = None,
        fiche: AgentDefinition | None = None,
    ) -> None:
        self.demandes: list[DemandeRenfort] = []
        self._decision = decision
        self._agents = agents
        self._fiche = fiche

    async def __call__(self, demande: DemandeRenfort) -> DecisionRenfort:
        self.demandes.append(demande)
        if self._agents is not None and self._fiche is not None:
            self._agents.pour_projet(demande.projet_id).ecrire(self._fiche)
        return self._decision


class _PlanScripteHesitant(_PlanScripte):
    """Le classifieur **de la vraie stack** (#1260) : il s'abstient quand personne n'a le métier.

    Rejoué du bouclage du 2026-09-24, essai 2 : « le classifieur n'a pas départagé
    dev avec assez de confiance (0.10 < seuil 0.60) ». C'est la réponse honnête à
    la question qu'on lui pose d'ordinaire — *qui est compétent ?* — quand aucun
    candidat n'a le métier, et c'est elle qui faisait partir « à assigner » les
    tâches d'un run que la personne avait pourtant laissé continuer.

    À la question du **plus proche**, en revanche, il désigne : `prefere` s'il est
    candidat, sinon le premier. C'est ce qui permet de voir, sur une équipe de
    plusieurs rôles, que le choix vient bien du modèle et non de l'ordre.
    """

    def __init__(self, prefere: str = "") -> None:
        self._prefere = prefere
        self.questions_plus_proche = 0

    async def generate(self, prompt: str, *, model: str, system_prompt: str | None = None) -> str:
        import json
        import re

        from maestro.router.classifier import CLASSIFIER_PLUS_PROCHE_SYSTEM_PROMPT

        if system_prompt is not None and "Chef de projet" in system_prompt:
            return json.dumps(PLAN)
        candidats = re.findall(r"^- (\S+) \(", prompt, flags=re.MULTILINE)
        if "Agents candidats" in prompt and candidats:
            if system_prompt == CLASSIFIER_PLUS_PROCHE_SYSTEM_PROMPT:
                self.questions_plus_proche += 1
                choisi = self._prefere if self._prefere in candidats else candidats[0]
                return json.dumps({"agent": choisi, "confiance": 0.3})
            return json.dumps({"agent": None, "confiance": 0.1})
        return "LIVRABLE"


def _moteur(
    tmp_path: Path,
    *,
    equipe: AgentDefinition | None = None,
    arbitre: Any = None,
    fournisseur: ModelProvider | None = None,
    renforts: tuple[AgentDefinition, ...] = (),
) -> tuple[OrchestrationEngine, AgentStore]:
    """Le moteur de l'essai : un projet qui n'a qu'un développeur."""
    agents = AgentStore(tmp_path / "agents")
    if equipe is not None:
        agents.pour_projet(PROJET).ecrire(equipe)
    for fiche in renforts:
        agents.pour_projet(PROJET).ecrire(fiche)
    fournisseur = fournisseur if fournisseur is not None else _PlanScripte()
    moteur = OrchestrationEngine(
        fournisseur,
        Orchestrator(fournisseur, model="m"),
        agents=[_agent("cablage", "backend")],
        runtimes={},
        agents_store=agents,
        arbitre_renfort=arbitre,
    )
    return moteur, agents


def _lignes_equipe(journal: RunJournal) -> list[Any]:
    """Les lignes `equipe` du journal — le manque, puis l'issue de la demande."""
    return [r for r in journal.records if r.etape == ETAPE_EQUIPE]


def test_la_demande_porte_le_role_sa_raison_et_les_taches_qu_il_prendrait(
    tmp_path: Path,
) -> None:
    """Le premier critère, de bout en bout : plan → manque → proposition.

    Faux fournisseur de modèle, projet à un seul `dev`, objectif d'animation. Ce
    que l'arbitre reçoit est ce que la carte du fil affichera.
    """
    arbitre = _ArbitreEspion(DecisionRenfort(approuve=False, detail="non merci"))
    moteur, _ = _moteur(tmp_path, equipe=_fiche("dev", "backend", "frontend"), arbitre=arbitre)
    journal = RunJournal()

    asyncio.run(moteur.run(OBJECTIF, journal=journal, projet_id=PROJET))

    assert len(arbitre.demandes) == 1
    demande = arbitre.demandes[0]
    assert demande.projet_id == PROJET
    assert demande.objectif == OBJECTIF
    assert demande.run_id == journal.run_id
    assert demande.manque.manque.role == "Designer"
    assert demande.manque.taches == (
        "Dessiner le logo stylisé",
        "Écrire le script d'animation",
    )
    assert "ui" in demande.manque.raison()
    # La borne voyage sur la demande, et c'est l'arbitre qui la tient.
    assert demande.attente_s > 0


def test_la_confrontation_a_lieu_avant_la_premiere_tache(tmp_path: Path) -> None:
    """Un recrutement décidé après le routage n'aurait servi à rien.

    La preuve est prise dans le journal : la ligne du manque précède toute ligne
    de tâche, et l'arbitre a été appelé avant qu'aucune tâche ne soit exécutée.
    """
    vu: list[str] = []

    async def arbitre(demande: DemandeRenfort) -> DecisionRenfort:
        vu.append("demande")
        return DecisionRenfort(approuve=False, detail="non")

    moteur, _ = _moteur(tmp_path, equipe=_fiche("dev", "backend", "frontend"), arbitre=arbitre)
    journal = RunJournal()

    asyncio.run(moteur.run(OBJECTIF, journal=journal, projet_id=PROJET))

    etapes = [r.etape for r in journal.records]
    premiere_tache = next(i for i, e in enumerate(etapes) if e.startswith("logo-stylise"))
    assert etapes.index(ETAPE_EQUIPE) < premiere_tache
    assert vu == ["demande"]


def test_le_manque_est_consigne_meme_sans_personne_a_qui_le_proposer(
    tmp_path: Path,
) -> None:
    """« Aucune modification de l'équipe n'a été suggérée » était vrai jusque dans
    le journal : sans arbitre, le manque se **dit** quand même, et une seule fois.

    Usage nul : confronter des tags à des fiches ne sollicite aucun modèle.
    """
    moteur, _ = _moteur(tmp_path, equipe=_fiche("dev", "backend", "frontend"))
    journal = RunJournal()

    asyncio.run(moteur.run(OBJECTIF, journal=journal, projet_id=PROJET))

    lignes = _lignes_equipe(journal)
    assert len(lignes) == 1
    assert lignes[0].statut == STATUT_ROLE_MANQUANT
    assert lignes[0].agent == "orchestrateur"
    assert lignes[0].projet_id == PROJET
    assert "Dessiner le logo stylisé" in lignes[0].sortie
    assert lignes[0].usage.appels == 0


def test_accepter_recrute_et_les_taches_vont_au_nouveau_role(tmp_path: Path) -> None:
    """Le second critère : l'équipe complétée prend les tâches qui l'appelaient.

    Rien n'est réécrit dans le plan — c'est le routage qui relit le dépôt
    d'agents à chaque tâche, si bien qu'un rôle né pendant l'attente prend son
    travail sans qu'une ligne du plan change.
    """
    arbitre = _ArbitreEspion(
        DecisionRenfort(approuve=True, detail="Équipe créée : Designer"),
        agents=AgentStore(tmp_path / "agents"),
        fiche=AgentDefinition(
            nom="interface",
            role="Designer",
            competences=("ui", "ux", "design-system"),
            playbook="Tu dessines les écrans de p1.",
        ),
    )
    moteur, _ = _moteur(tmp_path, equipe=_fiche("dev", "backend", "frontend"), arbitre=arbitre)
    journal = RunJournal()

    rapport = asyncio.run(moteur.run(OBJECTIF, journal=journal, projet_id=PROJET))

    par_tache = {r.task_id: r.agent for r in rapport.resultats}
    assert par_tache["logo-stylise"] == "interface"
    assert par_tache["script-animation"] == "interface"
    # La tâche que l'équipe couvrait déjà n'a pas changé de main.
    assert par_tache["test-lancement"] == "dev"
    issue = _lignes_equipe(journal)[-1]
    assert issue.statut == STATUT_RENFORT_RECRUTE
    assert "l'équipe complétée" in issue.sortie


def test_decliner_laisse_le_run_continuer_et_le_dit(tmp_path: Path) -> None:
    """Décliner n'annule rien : le run finit, avec l'équipe qu'on a."""
    arbitre = _ArbitreEspion(
        DecisionRenfort(approuve=False, detail="Entendu : je ne recrute personne")
    )
    moteur, _ = _moteur(tmp_path, equipe=_fiche("dev", "backend", "frontend"), arbitre=arbitre)
    journal = RunJournal()

    rapport = asyncio.run(moteur.run(OBJECTIF, journal=journal, projet_id=PROJET))

    assert len(rapport.resultats) == 3
    issue = _lignes_equipe(journal)[-1]
    assert issue.statut == STATUT_RENFORT_DECLINE
    assert "l'équipe actuelle" in issue.sortie
    # Les tâches sont allées au seul rôle en place, sans son métier.
    assert {r.agent for r in rapport.resultats} == {"dev"}


def test_ne_pas_repondre_n_est_pas_un_refus(tmp_path: Path) -> None:
    """Trois mots et pas un seul « refusé » : ce qu'on relit d'un run est *qui a
    décidé*, et personne n'a regardé passer celle-ci."""
    arbitre = _ArbitreEspion(
        DecisionRenfort(approuve=False, detail="personne n'a répondu", sans_reponse=True)
    )
    moteur, _ = _moteur(tmp_path, equipe=_fiche("dev", "backend", "frontend"), arbitre=arbitre)
    journal = RunJournal()

    rapport = asyncio.run(moteur.run(OBJECTIF, journal=journal, projet_id=PROJET))

    assert len(rapport.resultats) == 3
    assert _lignes_equipe(journal)[-1].statut == STATUT_RENFORT_SANS_REPONSE


def test_un_arbitre_qui_tombe_ne_condamne_pas_le_plan(tmp_path: Path) -> None:
    """Fail-safe dans le sens utile : ce qui est en jeu est une **amélioration**
    de l'équipe, pas un acte sensible. Un bus refermé ne coûte pas un run."""

    async def arbitre(demande: DemandeRenfort) -> DecisionRenfort:
        raise RuntimeError("bus refermé")

    moteur, _ = _moteur(tmp_path, equipe=_fiche("dev", "backend", "frontend"), arbitre=arbitre)
    journal = RunJournal()

    rapport = asyncio.run(moteur.run(OBJECTIF, journal=journal, projet_id=PROJET))

    assert len(rapport.resultats) == 3
    issue = _lignes_equipe(journal)[-1]
    assert issue.statut == STATUT_RENFORT_SANS_REPONSE
    assert "bus refermé" in issue.sortie


# --- ② ter Sans renfort, le travail va au rôle le plus proche (#1260) --------
#
# Le bouclage du 2026-09-24 a rejoué l'essai sur la vraie stack : la tâche de
# conception est partie « à assigner » — le classifieur, interrogé sur *qui est
# compétent*, s'abstenait honnêtement —, les deux suivantes sont restées
# bloquées, et le run a fini en échec 0/3. Les tests de #1227 ne le voyaient pas :
# leur classifieur désignait toujours quelqu'un. Ceux-ci rejouent le classifieur
# réel, qui s'abstient.


@pytest.mark.parametrize(
    "decision",
    [
        pytest.param(DecisionRenfort(approuve=False, detail="non merci"), id="decliner"),
        pytest.param(
            DecisionRenfort(approuve=False, detail="personne", sans_reponse=True),
            id="sans-reponse",
        ),
    ],
)
def test_sans_renfort_les_taches_vont_au_role_le_plus_proche_au_lieu_d_echouer(
    tmp_path: Path, decision: DecisionRenfort
) -> None:
    """Le critère de #1260 : la voie « décliner / pas de réponse » ne fait échouer
    aucune tâche. Le run de l'essai 2 finissait 0/3 ; il doit finir 3/3, tout au
    seul rôle en place — et le journal le dit."""
    moteur, _ = _moteur(
        tmp_path,
        equipe=_fiche("dev", "backend", "frontend"),
        arbitre=_ArbitreEspion(decision),
        fournisseur=_PlanScripteHesitant(),
    )
    journal = RunJournal()

    rapport = asyncio.run(moteur.run(OBJECTIF, journal=journal, projet_id=PROJET))

    assert [r.erreur for r in rapport.resultats if not r.ok] == []
    assert {r.task_id: r.agent for r in rapport.resultats} == {
        "logo-stylise": "dev",
        "script-animation": "dev",
        "test-lancement": "dev",
    }
    assert "rôle le plus proche" in _lignes_equipe(journal)[-1].sortie


def test_sans_personne_a_qui_proposer_le_travail_va_aussi_au_plus_proche(
    tmp_path: Path,
) -> None:
    """Sans arbitre (un run en ligne de commande), le manque se dit et le run
    continue avec l'équipe actuelle : c'est la même décision, prise par défaut."""
    moteur, _ = _moteur(
        tmp_path,
        equipe=_fiche("dev", "backend", "frontend"),
        fournisseur=_PlanScripteHesitant(),
    )

    rapport = asyncio.run(moteur.run(OBJECTIF, journal=RunJournal(), projet_id=PROJET))

    assert all(r.ok for r in rapport.resultats)
    assert {r.agent for r in rapport.resultats} == {"dev"}


def test_le_plus_proche_est_designe_par_le_modele_et_non_par_l_ordre(
    tmp_path: Path,
) -> None:
    """Deux rôles en place, aucun n'a le métier : c'est le classifieur qui dit
    lequel s'en approche le plus, à une question qui n'admet pas l'abstention.
    Le rédacteur est **second** dans le dépôt : le prendre n'est pas un défaut
    d'ordre."""
    fournisseur = _PlanScripteHesitant(prefere="redaction")
    moteur, _ = _moteur(
        tmp_path,
        equipe=_fiche("dev", "backend", "frontend"),
        renforts=(
            AgentDefinition(
                nom="redaction",
                role="Rédacteur",
                competences=("documentation",),
                playbook="Tu écris la documentation de p1.",
            ),
        ),
        arbitre=_ArbitreEspion(DecisionRenfort(approuve=False, detail="non")),
        fournisseur=fournisseur,
    )

    rapport = asyncio.run(moteur.run(OBJECTIF, journal=RunJournal(), projet_id=PROJET))

    par_tache = {r.task_id: r.agent for r in rapport.resultats}
    assert par_tache["logo-stylise"] == "redaction"
    assert par_tache["script-animation"] == "redaction"
    # La tâche que l'équipe couvre ne passe pas par cette question.
    assert par_tache["test-lancement"] == "dev"
    assert fournisseur.questions_plus_proche == 2


class _PlanMixte(_PlanScripte):
    """Le plan du bouclage, run `2f7aae8437f4` : **une** tâche, qui demande le métier
    absent (`ui`) et un métier que le développeur a (`frontend`).

    Le Designer recruté couvre l'un, le développeur l'autre : un ex æquo, que le
    classifieur a tranché pour le développeur — le premier candidat, comme ce double.
    Le fil venait de promettre « les tâches qui demandaient ces compétences iront au
    nouveau rôle ».
    """

    async def generate(self, prompt: str, *, model: str, system_prompt: str | None = None) -> str:
        import json

        if system_prompt is not None and "Chef de projet" in system_prompt:
            return json.dumps(
                [
                    {
                        "id": "logo-anime",
                        "titre": "Créer logo-anime.svg",
                        "description": "Objectif… Périmètre… Latitude… Critères…",
                        "competences_requises": ["ui", "frontend"],
                        "format_sortie": "SVG",
                        "dependances": [],
                    }
                ]
            )
        return await super().generate(prompt, model=model, system_prompt=system_prompt)


def test_accepter_donne_au_nouveau_role_les_taches_qui_demandaient_son_metier(
    tmp_path: Path,
) -> None:
    """La promesse du fil, tenue même quand le développeur couvre une partie de la
    tâche : le rôle recruté **pour ce plan** prend les tâches qui demandaient son
    métier. Vu une fois sur deux sur la vraie stack (S6, 2026-09-24)."""
    arbitre = _ArbitreEspion(
        DecisionRenfort(approuve=True, detail="Équipe créée : Designer"),
        agents=AgentStore(tmp_path / "agents"),
        fiche=AgentDefinition(
            nom="interface",
            role="Designer",
            competences=("ui", "ux", "design-system"),
            playbook="Tu dessines les écrans de p1.",
        ),
    )
    moteur, _ = _moteur(
        tmp_path,
        equipe=_fiche("dev", "backend", "frontend"),
        arbitre=arbitre,
        fournisseur=_PlanMixte(),
    )

    rapport = asyncio.run(moteur.run(OBJECTIF, journal=RunJournal(), projet_id=PROJET))

    assert {r.task_id: r.agent for r in rapport.resultats} == {"logo-anime": "interface"}


class _PlanDeuxMetiers(_PlanScripteHesitant):
    """Le plan du run `2e7f7278a991` (S6, 2026-09-24) : **deux** métiers absents.

    La direction visuelle demande le design, la validation demande la QA. #1227 ne
    propose qu'un poste par run — le plus couvrant, ici le Designer —, et la
    personne ne se voit jamais demander la QA. Le classifieur est celui de la vraie
    stack : il s'abstient à la question ordinaire.
    """

    async def generate(self, prompt: str, *, model: str, system_prompt: str | None = None) -> str:
        import json

        if system_prompt is not None and "Chef de projet" in system_prompt:
            return json.dumps(
                [
                    {
                        "id": "direction-visuelle",
                        "titre": "Définir la direction visuelle",
                        "description": "Objectif… Périmètre… Latitude… Critères…",
                        "competences_requises": ["ui", "design-system"],
                        "format_sortie": "note",
                        "dependances": [],
                    },
                    {
                        "id": "svg-anime",
                        "titre": "Produire logo-anime.svg",
                        "description": "Objectif… Périmètre… Latitude… Critères…",
                        "competences_requises": ["frontend"],
                        "format_sortie": "SVG",
                        "dependances": ["direction-visuelle"],
                    },
                    {
                        "id": "validation",
                        "titre": "Valider le SVG dans un navigateur",
                        "description": "Objectif… Périmètre… Latitude… Critères…",
                        "competences_requises": ["tests", "qa"],
                        "format_sortie": "rapport",
                        "dependances": ["svg-anime"],
                    },
                ]
            )
        return await super().generate(prompt, model=model, system_prompt=system_prompt)


def test_ce_que_le_renfort_accepte_ne_couvre_pas_va_au_plus_proche_et_se_dit(
    tmp_path: Path,
) -> None:
    """Accepter le Designer ne doit pas faire échouer la QA qu'on n'a jamais proposée.

    Le run `2e7f7278a991` a fini 2/3 : Designer recruté et au travail, mais la
    validation partie « à assigner ». Personne n'a été interrogé sur ce second
    métier — c'est la décision par défaut de « personne à qui proposer », et elle
    a la même conséquence : le rôle le plus proche. Le journal le dit, à côté du
    recrutement.
    """
    arbitre = _ArbitreEspion(
        DecisionRenfort(approuve=True, detail="Équipe créée : Designer"),
        agents=AgentStore(tmp_path / "agents"),
        fiche=AgentDefinition(
            nom="interface",
            role="Designer",
            competences=("ui", "ux", "design-system"),
            playbook="Tu dessines les écrans de p1.",
        ),
    )
    moteur, _ = _moteur(
        tmp_path,
        equipe=_fiche("dev", "backend", "frontend"),
        arbitre=arbitre,
        fournisseur=_PlanDeuxMetiers(),
    )
    journal = RunJournal()

    rapport = asyncio.run(moteur.run(OBJECTIF, journal=journal, projet_id=PROJET))

    assert [r.erreur for r in rapport.resultats if not r.ok] == []
    par_tache = {r.task_id: r.agent for r in rapport.resultats}
    assert par_tache["direction-visuelle"] == "interface"
    assert par_tache["svg-anime"] == "dev"
    assert par_tache["validation"] in {"dev", "interface"}
    issue = _lignes_equipe(journal)[-1]
    assert issue.statut == STATUT_RENFORT_RECRUTE
    assert "qa, tests" in issue.sortie
    assert "rôle le plus proche" in issue.sortie


@pytest.mark.parametrize(
    ("equipe", "projet"),
    [
        pytest.param(None, PROJET, id="projet-sans-agent"),
        pytest.param(_fiche("dev", "backend", "frontend"), None, id="hors-projet"),
    ],
)
def test_aucune_confrontation_sans_equipe_de_projet(
    tmp_path: Path, equipe: AgentDefinition | None, projet: str | None
) -> None:
    """Deux abstentions, et elles ne se confondent pas avec un manque.

    Un projet **sans agent** se voit proposer son équipe entière avant d'ouvrir un
    run (#1146) ; un run **hors projet** est routé sur le catalogue du câblage,
    dont l'équipe ne se recrute pas. Ni l'un ni l'autre n'a de renfort à proposer,
    et ni l'un ni l'autre ne consigne de manque.
    """
    arbitre = _ArbitreEspion(DecisionRenfort(approuve=False))
    moteur, _ = _moteur(tmp_path, equipe=equipe, arbitre=arbitre)
    journal = RunJournal()

    asyncio.run(moteur.run(OBJECTIF, journal=journal, projet_id=projet))

    assert arbitre.demandes == []
    assert _lignes_equipe(journal) == []


def test_une_equipe_complete_ne_fait_poser_aucune_question(tmp_path: Path) -> None:
    """Le témoin : quand le plan tient dans l'équipe, rien n'est proposé ni écrit."""
    arbitre = _ArbitreEspion(DecisionRenfort(approuve=False))
    moteur, _ = _moteur(
        tmp_path,
        equipe=_fiche("polyvalent", "backend", "frontend", "ui", "design-system"),
        arbitre=arbitre,
    )
    journal = RunJournal()

    asyncio.run(moteur.run(OBJECTIF, journal=journal, projet_id=PROJET))

    assert arbitre.demandes == []
    assert _lignes_equipe(journal) == []


# --- ② bis Les lignes du run ne fabriquent aucune carte de tâche -------------


def test_les_lignes_de_l_etape_equipe_sont_des_activites_de_run(tmp_path: Path) -> None:
    """Étape du **run**, comme la planification et le brief : elle ne porte sur
    aucune tâche. Sans cette déclaration, la règle par défaut du pont ferait une
    carte de Kanban fantôme nommée « equipe » (#924)."""
    arbitre = _ArbitreEspion(DecisionRenfort(approuve=False, detail="non"))
    moteur, _ = _moteur(tmp_path, equipe=_fiche("dev", "backend", "frontend"), arbitre=arbitre)
    journal = RunJournal()

    asyncio.run(moteur.run(OBJECTIF, journal=journal, projet_id=PROJET))

    for ligne in _lignes_equipe(journal):
        evenements = evenements_depuis_step(ligne.to_dict())
        assert [e.type for e in evenements] == [EVENEMENT_AGENT_ACTIVITE]
        assert evenements[0].tache_id == ""
        assert evenements[0].etape_run == ETAPE_EQUIPE


def test_l_attente_de_renfort_ne_cree_aucune_entree_au_grand_livre(
    tmp_path: Path,
) -> None:
    """Usage nul, et surtout **aucune tâche fantôme** : le grand livre d'un run
    suspendu sur un recrutement compte exactement ses tâches."""
    arbitre = _ArbitreEspion(DecisionRenfort(approuve=False, detail="non"))
    moteur, _ = _moteur(tmp_path, equipe=_fiche("dev", "backend", "frontend"), arbitre=arbitre)
    journal = RunJournal()

    rapport = asyncio.run(moteur.run(OBJECTIF, journal=journal, projet_id=PROJET))

    assert {t.tache_id for t in rapport.grand_livre.taches} == {
        "logo-stylise",
        "script-animation",
        "test-lancement",
    }


# --- ③ Le fil : la demande y est relayée, la décision revient au run --------
#
# Depuis #1260, deux moitiés de part et d'autre du bus : l'**arbitre** vit avec le
# moteur (dans l'un ou l'autre hôte) et ne connaît que le bus ; le **relais** vit
# dans l'API, seul écrivain du fil. Ce qu'on garde ici est ce qui les distingue de
# leurs trois aînés : la demande finit **dans le fil** — là où #1146 a déjà posé la
# carte d'équipe et le geste qui la valide —, et la borne est tenue des deux côtés,
# sur la même date.


def _demande_de_renfort(run_id: str = "run-1", *, attente_s: float = 30.0) -> DemandeRenfort:
    """La demande du moteur : le manque de l'essai, sur le projet de l'essai."""
    manque = manque_au_plan(
        [
            _tache("Dessiner le logo stylisé", "ui", "design-system"),
            _tache("Vérifier le lancement", "frontend"),
        ],
        [_agent("dev", "backend", "frontend")],
    )
    assert manque is not None
    return DemandeRenfort(
        run_id=run_id,
        projet_id=PROJET,
        objectif=OBJECTIF,
        manque=manque,
        attente_s=attente_s,
    )


class _RepondeurQuiRedige:
    """La parole du fil, réduite à ce que le relais en attend (#1262).

    Le relais ne compose plus de phrase : il transmet des **faits**, et c'est le
    répondeur du fil qui rédige (`RepondeurChat.rediger`). Ce double note les
    faits reçus et rend une phrase qu'aucun gabarit ne contient — si bien que
    `contenu == "Message rédigé n°1."` prouve qu'aucun texte du code ne s'y est
    glissé.
    """

    def __init__(self) -> None:
        self.faits: list[str] = []

    async def rediger(self, agent: Any, fil: Any, *, faits: str) -> str:
        self.faits.append(faits)
        return f"Message rédigé n°{len(self.faits)}."


def _fil_dorchestration(tmp_path: Path, bus: Any, repondeur: Any = None) -> Any:
    """Un `ServiceChat` du fil global, sur un dépôt jetable et le bus donné."""
    from maestro.controltower.chat import ChatStore, RepondeurScripte, ServiceChat
    from maestro.messaging import InMemoryMailbox

    return ServiceChat(
        store=ChatStore(tmp_path / "chat"),
        repondeur=repondeur if repondeur is not None else RepondeurScripte(),
        mailbox=InMemoryMailbox(),
        bus=bus,
    )


def test_l_arbitre_publie_la_demande_avec_ce_que_le_fil_montrera(tmp_path: Path) -> None:
    """La demande traverse le bus **déjà composée** : le relais n'a rien à juger.

    Ce qu'il faut pour décider voyage sur la demande que la carte lira — le rôle,
    pourquoi, les tâches, et ce qui a été demandé — et la borne en date, la même
    que l'arbitre tient de son côté. `detail` ne porte plus de phrase du fil
    (#1262) : c'est le manque en une ligne, pour le journal.
    """
    from maestro.controltower.events import EVENEMENT_RENFORT_DEMANDE, InMemoryEventBus
    from maestro.controltower.renfort import ArbitreRenfortControlTower

    bus = InMemoryEventBus()
    arbitre = ArbitreRenfortControlTower(bus)

    async def scenario() -> tuple[list[Any], DecisionRenfort]:
        vus: list[Any] = []
        flux = bus.subscribe()

        async def ecouter() -> None:
            async for event in flux:
                vus.append(event)

        ecoute = asyncio.create_task(ecouter())
        await asyncio.sleep(0)
        decision = await arbitre(_demande_de_renfort(attente_s=0.05))
        ecoute.cancel()
        return vus, decision

    vus, decision = asyncio.run(scenario())

    demandes = [e for e in vus if e.type == EVENEMENT_RENFORT_DEMANDE]
    assert len(demandes) == 1
    publiee = demandes[0]
    assert publiee.run_id == "run-1"
    assert publiee.projet_id == PROJET
    assert publiee.titre == "Designer"
    assert publiee.recrutement is not None
    assert publiee.recrutement["run_id"] == "run-1"
    assert publiee.recrutement["gabarit"] == "interface"
    assert publiee.recrutement["taches"] == ["Dessiner le logo stylisé"]
    assert publiee.recrutement["objectif"] == OBJECTIF
    assert publiee.detail == _demande_de_renfort().manque.phrase()
    assert publiee.echeance
    # Personne n'a répondu : le run reprend, et ce n'est pas un refus.
    assert decision.sans_reponse and not decision.approuve


def test_le_relais_pose_la_demande_dans_le_fil_puis_dit_l_echeance(tmp_path: Path) -> None:
    """Le message porte la demande — la carte de #1146 la voit — et la parole du fil.

    Personne ne répond : à l'échéance, le fil le **dit**, et la demande ne s'y
    repose pas — un bouton « Créer l'équipe » promettrait de faire reprendre un
    run déjà parti.

    Depuis #1262, les deux messages sont **rédigés par le répondeur du fil** sur
    les faits que le relais lui passe : ce que le fil dit est ce qu'il a rédigé, à
    la lettre, et c'est lui qui a reçu le rôle, la raison, les tâches, l'échéance.
    """
    from maestro.controltower.chat import recrutement_en_attente
    from maestro.controltower.events import InMemoryEventBus
    from maestro.controltower.orchestration import AGENT_ORCHESTRATION, NOM_ORCHESTRATION
    from maestro.controltower.renfort import RelaisRenfort, evenement_demande

    bus = InMemoryEventBus()
    repondeur = _RepondeurQuiRedige()
    fil = _fil_dorchestration(tmp_path, bus, repondeur)
    relais = RelaisRenfort(bus, fil, AGENT_ORCHESTRATION)

    asyncio.run(relais.relayer(evenement_demande(_demande_de_renfort(attente_s=0.05))))

    messages = fil.fil(NOM_ORCHESTRATION)
    pose = messages[0]
    assert pose.recrutement is not None
    assert pose.recrutement.run_id == "run-1"
    assert pose.recrutement.projet_id == PROJET
    assert pose.recrutement.role == "Designer"
    assert pose.recrutement.gabarit == "interface"
    assert pose.recrutement.taches == ("Dessiner le logo stylisé",)
    assert pose.contenu == "Message rédigé n°1."
    proposee, echue = repondeur.faits
    assert OBJECTIF in proposee
    assert "Designer" in proposee
    assert "Dessiner le logo stylisé" in proposee
    assert len(messages) == 2
    assert messages[1].contenu == "Message rédigé n°2."
    assert "Designer" in echue
    assert "avant l'échéance" in echue
    assert messages[1].recrutement is None
    assert recrutement_en_attente(messages) is None


def test_le_relais_pose_la_demande_dans_la_conversation_qui_a_lance_le_run(
    tmp_path: Path,
) -> None:
    """« Dans le fil qui a lancé le run » — et pas dans la conversation la plus
    récente. La personne qui en a ouvert une autre entre-temps doit trouver la
    question là où elle a demandé le travail (#268, `conversation_du_run`)."""
    from maestro.controltower.chat import ChatStore, MessageChat
    from maestro.controltower.events import InMemoryEventBus
    from maestro.controltower.orchestration import AGENT_ORCHESTRATION, NOM_ORCHESTRATION
    from maestro.controltower.renfort import RelaisRenfort, evenement_demande

    bus = InMemoryEventBus()
    fil = _fil_dorchestration(tmp_path, bus)
    depot = ChatStore(tmp_path / "chat")
    depot.ajouter(
        MessageChat(
            agent=NOM_ORCHESTRATION,
            conversation="origine",
            auteur=NOM_ORCHESTRATION,
            contenu="C'est parti.",
            run_id="run-1",
        )
    )
    depot.ajouter(
        MessageChat(
            agent=NOM_ORCHESTRATION,
            conversation="plus-recente",
            auteur="utilisateur",
            contenu="Autre chose.",
        )
    )
    relais = RelaisRenfort(bus, fil, AGENT_ORCHESTRATION)

    asyncio.run(relais.relayer(evenement_demande(_demande_de_renfort(attente_s=0.02))))

    origine = fil.fil(NOM_ORCHESTRATION, "origine")
    assert [m.recrutement is not None for m in origine] == [False, True, False]
    assert len(fil.fil(NOM_ORCHESTRATION, "plus-recente")) == 1


def test_une_decision_avant_l_echeance_ne_fait_rien_redire_au_fil(tmp_path: Path) -> None:
    """Le geste a déjà écrit sa trace (`POST …/recrutement`) : le relais se tait."""
    from maestro.controltower.events import EVENEMENT_RENFORT_DECISION, Event, InMemoryEventBus
    from maestro.controltower.orchestration import AGENT_ORCHESTRATION, NOM_ORCHESTRATION
    from maestro.controltower.renfort import RelaisRenfort, evenement_demande
    from maestro.controltower.state import RENFORT_DECLINE

    bus = InMemoryEventBus()
    fil = _fil_dorchestration(tmp_path, bus)
    relais = RelaisRenfort(bus, fil, AGENT_ORCHESTRATION)

    async def scenario() -> None:
        relai = asyncio.create_task(
            relais.relayer(evenement_demande(_demande_de_renfort(attente_s=5.0)))
        )
        await asyncio.sleep(0.05)
        await bus.publish(
            Event(type=EVENEMENT_RENFORT_DECISION, run_id="run-1", statut=RENFORT_DECLINE)
        )
        await asyncio.wait_for(relai, timeout=2.0)

    asyncio.run(scenario())

    assert len(fil.fil(NOM_ORCHESTRATION)) == 1


def test_la_decision_publiee_sur_le_bus_fait_repartir_le_run(tmp_path: Path) -> None:
    """Ce qui revient par le bus est la décision, et la clé est le `run_id` — un run
    n'a qu'une demande en vol."""
    from maestro.controltower.events import EVENEMENT_RENFORT_DECISION, Event, InMemoryEventBus
    from maestro.controltower.renfort import ArbitreRenfortControlTower
    from maestro.controltower.state import RENFORT_ACCORDE

    bus = InMemoryEventBus()
    arbitre = ArbitreRenfortControlTower(bus)

    async def scenario() -> DecisionRenfort:
        attente = asyncio.create_task(arbitre(_demande_de_renfort()))
        # Laisse la demande partir, puis tranche — comme un geste humain.
        await asyncio.sleep(0.05)
        await bus.publish(
            Event(
                type=EVENEMENT_RENFORT_DECISION,
                run_id="run-1",
                statut=RENFORT_ACCORDE,
                detail="Équipe créée : Designer.",
            )
        )
        return await attente

    decision = asyncio.run(scenario())

    assert decision.approuve
    assert decision.detail == "Équipe créée : Designer."


def test_une_decision_qui_vise_un_autre_run_n_est_pas_lue(tmp_path: Path) -> None:
    """Le filtre est le run : deux runs suspendus ne se volent pas leur décision."""
    from maestro.controltower.events import EVENEMENT_RENFORT_DECISION, Event, InMemoryEventBus
    from maestro.controltower.renfort import ArbitreRenfortControlTower
    from maestro.controltower.state import RENFORT_ACCORDE

    bus = InMemoryEventBus()
    arbitre = ArbitreRenfortControlTower(bus)

    async def scenario() -> DecisionRenfort:
        attente = asyncio.create_task(arbitre(_demande_de_renfort("run-1", attente_s=0.2)))
        await asyncio.sleep(0.05)
        await bus.publish(
            Event(
                type=EVENEMENT_RENFORT_DECISION,
                run_id="run-2",
                statut=RENFORT_ACCORDE,
                detail="pas pour vous",
            )
        )
        return await attente

    decision = asyncio.run(scenario())

    assert decision.sans_reponse and not decision.approuve


def test_la_demande_voyage_intacte_sur_un_bus_qui_serialise() -> None:
    """Le bus réel est Redis : la demande y passe en JSON, et doit en revenir telle
    quelle — sans quoi la carte du fil lirait une demande vide."""
    from maestro.controltower.chat import DemandeRecrutement
    from maestro.controltower.events import Event
    from maestro.controltower.renfort import evenement_demande

    publiee = evenement_demande(_demande_de_renfort())
    relue = Event.from_json(publiee.to_json())

    assert relue.recrutement == publiee.recrutement
    assert DemandeRecrutement.from_dict(relue.recrutement or {}).pendant_un_run
    assert relue.echeance == publiee.echeance


# --- ④ L'API : le geste du fil, et le rôle proposé ---------------------------
#
# Les deux routes que ce lot touche, sur l'app réelle. Rien de nouveau côté
# contrat : `POST …/recrutement` est celui de #1146 — ce qui s'y ajoute est la
# **publication de la décision** quand un run attend ; `POST …/equipe/proposition`
# est celui de #1039 — ce qui s'y ajoute est `renfort`.


@pytest.fixture()
def atelier(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Le dossier où naissent les projets, sous un dossier utilisateur factice.

    Même raison qu'en #221 : sous Windows le `tmp_path` de pytest vit dans
    `AppData/Local/Temp`, que `valider_racine` refuse à raison.
    """
    maison = tmp_path / "maison"
    maison.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: maison))
    atelier = tmp_path / "atelier"
    atelier.mkdir()
    return atelier


class _GenerateurHorsLigne:
    """Un générateur de playbooks qui ne répond jamais — zéro appel modèle.

    La route rattrape toute panne et fait retomber le rôle sur le playbook de son
    gabarit : c'est donc aussi le chemin nominal d'un poste sans quota, et c'est
    ce qui garde ces tests hors du fournisseur du poste (#782).
    """

    async def proposer(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("hors ligne (test)")


def _client_de_projet(tmp_path: Path, atelier: Path) -> Any:
    """L'app réelle, projets bornés à l'atelier, générateur hors ligne."""
    from fastapi.testclient import TestClient

    from maestro.controltower.app import create_app
    from maestro.controltower.events import InMemoryEventBus
    from maestro.controltower.projets import ServiceProjets
    from maestro.projets import ProjetStore

    return TestClient(
        create_app(
            bus=InMemoryEventBus(),
            projets=ServiceProjets(
                ProjetStore(tmp_path / "depot"), racines_exploration=(atelier,)
            ),
            agents_store=AgentStore(tmp_path / "agents"),
            generateur_agent=_GenerateurHorsLigne(),
        )
    )


def _projet_python(atelier: Path) -> Path:
    """Le projet de l'essai : du Python, et rien qui désigne un designer."""
    racine = atelier / "p1"
    (racine / "src").mkdir(parents=True)
    (racine / "src" / "app.py").write_text("print('salut')\n", encoding="utf-8")
    (racine / "pyproject.toml").write_text(
        '[project]\nname = "p1"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    return racine


def test_la_route_propose_le_seul_role_du_renfort_et_le_justifie_par_le_plan(
    tmp_path: Path, atelier: Path
) -> None:
    """Sans `renfort`, ce projet n'appelle aucun designer — et c'est bien le cas.

    L'échantillon fautif est la proposition ordinaire : le designer y est
    **écarté**, avec sa raison. C'est le plan qui le demande, et lui seul.
    """
    racine = _projet_python(atelier)
    with _client_de_projet(tmp_path, atelier) as client:
        projet = client.post(
            "/api/projets",
            json={"nom": "p1", "racine": str(racine), "origine": "existant"},
        ).json()["id"]

        ordinaire = client.post(f"/api/projets/{projet}/equipe/proposition").json()
        renfort = client.post(
            f"/api/projets/{projet}/equipe/proposition",
            json={
                "renfort": {
                    "gabarit": "interface",
                    "raison": "le plan de ce travail demande ui, ux",
                }
            },
        )

    assert "designer" not in {r["gabarit"] for r in ordinaire["roles"]}
    assert "designer" in {e["nom"] for e in ordinaire["ecartes"]} or "interface" in {
        e["nom"] for e in ordinaire["ecartes"]
    }
    assert renfort.status_code == 200, renfort.text
    corps = renfort.json()
    assert [r["gabarit"] for r in corps["roles"]] == ["designer"]
    assert corps["roles"][0]["raison"] == "le plan de ce travail demande ui, ux"
    assert corps["cree"] is False
    assert [e["nom"] for e in corps["ecartes"]] == ["orchestrateur"]


def test_un_gabarit_de_renfort_inconnu_est_refuse(tmp_path: Path, atelier: Path) -> None:
    """422 motivé, jamais un rôle inventé : ce qui naîtrait n'aurait ni playbook
    ni compétences, et se lirait comme un rôle du catalogue."""
    racine = _projet_python(atelier)
    with _client_de_projet(tmp_path, atelier) as client:
        projet = client.post(
            "/api/projets",
            json={"nom": "p1", "racine": str(racine), "origine": "existant"},
        ).json()["id"]

        reponse = client.post(
            f"/api/projets/{projet}/equipe/proposition",
            json={"renfort": {"gabarit": "comptable", "raison": "au hasard"}},
        )

    assert reponse.status_code == 422, reponse.text
    assert "comptable" in reponse.text


class _BusEspion:
    """Le bus mémoire de l'app, qui **note** ce qui y est publié.

    Sous-classe et non abonné : un abonnement réclamerait une boucle asyncio en
    parallèle du `TestClient`, qui est synchrone. Ce qu'on vérifie est qu'un
    événement a été publié — pas qu'il traverse un transport, ce dont le bus a ses
    propres tests.
    """

    def __init__(self) -> None:
        from maestro.controltower.events import InMemoryEventBus

        self._vrai = InMemoryEventBus()
        self.publies: list[Any] = []

    async def publish(self, event: Any) -> None:
        self.publies.append(event)
        await self._vrai.publish(event)

    def subscribe(self) -> Any:
        return self._vrai.subscribe()

    async def close(self) -> None:
        await self._vrai.close()

    def decisions(self) -> list[Any]:
        """Les seules `renfort.decision` publiées, dans l'ordre."""
        from maestro.controltower.events import EVENEMENT_RENFORT_DECISION

        return [e for e in self.publies if e.type == EVENEMENT_RENFORT_DECISION]


class _ModeleQuiRedige(ModelProvider):
    """Le fournisseur du fil pour ces routes : il ne sert qu'à rédiger la réponse au geste.

    Depuis #1262, le geste de recrutement fait parler le modèle ; sans fournisseur
    injecté, le répondeur résoudrait celui du poste (#782). Ce qui se garde ici
    est la décision publiée au run, jamais les mots.
    """

    name = "modele-qui-redige"

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt: str, *, model: str, system_prompt: str | None = None) -> str:
        return "Rédigé par le modèle."


def _poser_la_demande(depot: Any, *, run_id: str) -> None:
    """Écrit dans le fil la demande qu'un run suspendu y aurait posée.

    Écriture directe dans le dépôt : ce qu'on éprouve ici est la **route**, pas
    l'arbitre (il a sa propre section). Monter un run pour arranger l'état d'un
    fil coûterait un moteur pour vérifier un `publish`.
    """
    from maestro.controltower.chat import DemandeRecrutement, MessageChat
    from maestro.controltower.orchestration import NOM_ORCHESTRATION

    depot.ajouter(
        MessageChat(
            agent=NOM_ORCHESTRATION,
            auteur=NOM_ORCHESTRATION,
            contenu="Le plan appelle un Designer. Je le recrute ?",
            recrutement=DemandeRecrutement(
                objectif=OBJECTIF,
                projet_id=PROJET,
                run_id=run_id,
                role="Designer",
                gabarit="interface",
                raison="le plan de ce travail demande ui",
                taches=("Dessiner le logo stylisé",),
            ),
        )
    )


@pytest.mark.parametrize(
    ("approuve", "statut_attendu", "detail_attendu"),
    [
        pytest.param(True, "recrute", "recruté : Designer — 1 agent", id="accepter"),
        pytest.param(False, "decline", "", id="decliner"),
    ],
)
def test_le_geste_du_fil_publie_la_decision_au_run_qui_attend(
    tmp_path: Path, approuve: bool, statut_attendu: str, detail_attendu: str
) -> None:
    """Le second critère, côté API : le run apprend ce qui a été décidé de lui.

    L'ordre compte et c'est celui de tout le module : le fil d'abord (les deux
    messages du geste), la diffusion ensuite — un run qui repartirait avant que sa
    trace soit écrite laisserait le fil en retard sur ce qui se passe.

    Le `detail` est le **fait** — ce qui a été recruté —, et non les mots de la
    réponse (#1262) : il finit au journal du run, où l'on relit ce qui a été
    recruté, pas ce que l'orchestrateur en a dit.
    """
    from fastapi.testclient import TestClient

    from maestro.controltower.app import create_app
    from maestro.controltower.chat import ChatStore
    from maestro.controltower.orchestration import NOM_ORCHESTRATION, RepondeurOrchestration

    async def recruter(projet_id: str, roles: Any, proposition_id: str = "") -> dict[str, Any]:
        return {
            "projet_id": projet_id,
            "agents": [{"nom": r.nom, "role": r.role, "instances": r.instances} for r in roles],
            "instances_total": sum(r.instances for r in roles),
        }

    bus = _BusEspion()
    depot = ChatStore(tmp_path / "chat")
    _poser_la_demande(depot, run_id="run-42")

    app = create_app(
        bus=bus,
        chat_store=depot,
        orchestration_repondeur=RepondeurOrchestration(
            recruteur=recruter, provider=_ModeleQuiRedige()
        ),
    )
    with TestClient(app) as client:
        reponse = client.post(
            f"/api/chat/{NOM_ORCHESTRATION}/recrutement",
            json={
                "approuve": approuve,
                "roles": (
                    [
                        {
                            "nom": "interface",
                            "role": "Designer",
                            "competences": ["ui"],
                            "playbook": "Tu dessines.",
                            "instances": 1,
                        }
                    ]
                    if approuve
                    else []
                ),
            },
        )

    assert reponse.status_code == 201, reponse.text
    decisions = bus.decisions()
    assert len(decisions) == 1
    assert decisions[0].run_id == "run-42"
    assert decisions[0].statut == statut_attendu
    assert decisions[0].projet_id == PROJET
    assert decisions[0].detail == detail_attendu
    assert reponse.json()["messages"][1]["contenu"] == "Rédigé par le modèle."


def test_un_recrutement_sans_run_ne_publie_aucune_decision(tmp_path: Path) -> None:
    """Le témoin : la demande de #1146 ne suspend aucun run, donc n'en prévient aucun.

    Publier là ferait apparaître, sur le canal qu'un run écoute, une décision qui
    ne concerne personne — et sur un bus durable, elle serait rejouée.
    """
    from fastapi.testclient import TestClient

    from maestro.controltower.app import create_app
    from maestro.controltower.chat import ChatStore, DemandeRecrutement, MessageChat
    from maestro.controltower.orchestration import NOM_ORCHESTRATION, RepondeurOrchestration

    bus = _BusEspion()
    depot = ChatStore(tmp_path / "chat")
    depot.ajouter(
        MessageChat(
            agent=NOM_ORCHESTRATION,
            auteur=NOM_ORCHESTRATION,
            contenu="Ce projet n'a aucun agent. Je vous propose son équipe.",
            recrutement=DemandeRecrutement(objectif=OBJECTIF, projet_id=PROJET),
        )
    )

    app = create_app(
        bus=bus,
        chat_store=depot,
        orchestration_repondeur=RepondeurOrchestration(provider=_ModeleQuiRedige()),
    )
    with TestClient(app) as client:
        reponse = client.post(
            f"/api/chat/{NOM_ORCHESTRATION}/recrutement", json={"approuve": False}
        )

    assert reponse.status_code == 201, reponse.text
    assert bus.decisions() == []

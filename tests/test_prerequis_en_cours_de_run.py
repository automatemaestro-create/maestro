"""Un prérequis qui manque en cours de run se propose dans le fil (ticket #1181).

Trois situations faisaient **échouer** une tâche là où Maestro savait proposer ce
qui lui manquait : un serveur MCP à authentifier ou injoignable, un agent qui dit
être bloqué, un rôle absent de l'équipe. La règle que #1146 tient avant le run se
tient désormais pendant : ce qui manque se propose au moment où il manque, et la
tâche reprend une fois qu'on le lui a donné — sans relancer le run.

Aucun appel réseau : le Chef de projet, le classifieur et les exécutants sont un
même `ModelProvider` factice, qui rend des rattrapages **écrits d'avance** — ce
que ces tests éprouvent n'est pas la qualité d'un modèle (le banc des scénarios le
fait sur le réel) mais ce que Maestro **fait** de ce qui manque.

Critère ① — un serveur MCP à authentifier, ou injoignable, **suspend** la tâche au
lieu de la faire échouer ; le fil propose la procédure que la bibliothèque connaît,
et la tâche reprend une fois le serveur joignable.

Critère ② — un rôle absent de l'équipe, ou un blocage que l'agent signale, devient
une proposition concrète dans le fil (rôle à recruter, secret, serveur, outil) ;
son acceptation reprend la tâche, sans relancer le run.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from maestro.agents.mcp import ServeurMcp, resolus
from maestro.agents.mcp_registry import RegistreMcp
from maestro.agents.store import AgentDefinition, AgentStore
from maestro.controltower.bridge import evenements_depuis_step
from maestro.controltower.progression import EN_COURS, compartiment
from maestro.engine import STATUT_BLOQUEE, STATUT_ECHEC, OrchestrationEngine
from maestro.engine.executor import (
    STATUT_EN_ATTENTE_VALIDATION,
    STATUT_QUESTION_SANS_REPONSE,
    STATUT_TERMINEE,
    SUFFIXE_ETAPE_QUESTION,
    TaskResult,
)
from maestro.engine.questions import DemandeQuestion
from maestro.engine.rattrapage import (
    CHOIX_PREREQUIS_LEVE,
    STATUT_PREREQUIS_DECLINE,
    STATUT_PREREQUIS_LEVE,
    STATUT_PREREQUIS_SANS_REPONSE,
    SUFFIXE_ETAPE_RATTRAPAGE,
    PolitiqueRattrapage,
)
from maestro.engine.renfort import DecisionRenfort, DemandeRenfort
from maestro.equipe import manque_au_plan
from maestro.orchestrator import Orchestrator, RattrapageValidationError, Task
from maestro.orchestrator.prompt import build_rattrapage_user_prompt
from maestro.orchestrator.rattrapage import EchecDeTache, Tentative, valide_rattrapage
from maestro.prerequis import (
    GENRE_ROLE,
    GENRE_SECRET,
    GENRE_SERVEUR,
    PrerequisManquant,
    prerequis_du_mcp,
)
from maestro.providers.arbitrage import BornesArbitrage
from maestro.providers.base import (
    MCP_A_AUTHENTIFIER,
    MCP_NON_MONTABLE,
    McpServerUnavailable,
    ModelProvider,
    ServeurInjoignable,
)
from maestro.router.classifier import CLASSIFIER_PLUS_PROCHE_SYSTEM_PROMPT
from maestro.telemetry import RunJournal

#: La première ligne du playbook de rattrapage : c'est ainsi que le fournisseur
#: factice sait qu'on lui demande un diagnostic plutôt qu'un plan.
_ENTETE_RATTRAPAGE = "# Playbook — Chef de projet : le rattrapage"

PROJET = "prj-p1"


@dataclass(frozen=True)
class Bloque:
    """Une issue d'exécution : l'agent **signale** un blocage, puis rend un livrable vide."""

    raison: str


class Fournisseur(ModelProvider):
    """Chef de projet, classifieur et exécutant factices, en un seul double.

    - le **plan** d'abord, puis les **rattrapages** écrits d'avance, dans l'ordre ;
    - le **classifieur** s'abstient — la réponse honnête quand personne n'a le
      métier (#1260) : c'est ce qui laisse une tâche sans rôle « à assigner » au
      lieu de la donner au premier venu. À la question du **plus proche**, il
      désigne `plus_proche` ;
    - l'**exécutant** outillé (`run_agent`) rend, pour chaque marqueur que porte la
      tâche, la suite d'issues voulue — un livrable, une exception, un blocage.
    """

    name = "fournisseur"

    def __init__(
        self,
        plan: list[dict],
        issues: dict[str, list[object]],
        rattrapages: list[dict] = (),
        *,
        plus_proche: str | None = None,
    ) -> None:
        self._plan = json.dumps(plan, ensure_ascii=False)
        self._issues = issues
        self._rattrapages = list(rattrapages)
        self._plus_proche = plus_proche
        self.diagnostics: list[str] = []
        self.appels: list[str] = []
        self.prompts: dict[str, list[str]] = {}

    def supports(self, model: str) -> bool:
        return True

    def compte(self, marqueur: str) -> int:
        return self.appels.count(marqueur)

    async def generate(self, prompt, *, model, system_prompt=None, **_):
        if system_prompt and system_prompt.startswith(_ENTETE_RATTRAPAGE):
            self.diagnostics.append(prompt)
            if not self._rattrapages:
                raise AssertionError(f"diagnostic inattendu :\n{prompt}")
            return json.dumps(self._rattrapages.pop(0), ensure_ascii=False)
        if system_prompt and "Chef de projet" in system_prompt:
            return self._plan
        if "Agents candidats" in prompt:
            if system_prompt == CLASSIFIER_PLUS_PROCHE_SYSTEM_PROMPT:
                return json.dumps({"agent": self._plus_proche, "confiance": 0.3})
            return json.dumps({"agent": None, "confiance": 0.1})
        raise AssertionError(f"appel texte inattendu :\n{prompt[:300]}")

    async def run_agent(self, prompt, **kw):
        for marqueur, issues in self._issues.items():
            if marqueur in prompt:
                self.appels.append(marqueur)
                self.prompts.setdefault(marqueur, []).append(prompt)
                issue = issues[min(self.compte(marqueur), len(issues)) - 1]
                if isinstance(issue, Bloque):
                    kw["on_blocage"](issue.raison)
                    return ""
                if isinstance(issue, BaseException):
                    raise issue
                return issue
        raise AssertionError(f"tâche inattendue :\n{prompt[:300]}")


def _tache(
    ident: str,
    description: str,
    *,
    dependances: tuple[str, ...] = (),
    competences: tuple[str, ...] = ("backend",),
) -> dict:
    return {
        "id": ident,
        "titre": f"Tâche {ident}",
        "description": description,
        "competences_requises": list(competences),
        "format_sortie": "Texte",
        "dependances": list(dependances),
    }


def _plan(marqueur: str) -> list[dict]:
    """Une tâche qui manquera de quelque chose (`marqueur`), et une tâche aval qui l'attend."""
    return [
        _tache("t1", f"Réaliser le module ({marqueur})."),
        _tache("t2", "Documenter le module (AVAL-T2).", dependances=("t1",)),
    ]


def _github_a_authentifier() -> McpServerUnavailable:
    """Ce que l'adaptateur Claude lève sur un `needs-auth` : le message, et les faits."""
    return McpServerUnavailable(
        "serveur(s) MCP indisponible(s) — github : état « needs-auth ».",
        (ServeurInjoignable("github", MCP_A_AUTHENTIFIER),),
    )


class Questionneur:
    """Le fil, vu du moteur : il note chaque carte posée et y répond comme on le lui dit."""

    def __init__(self, *reponses: str | None) -> None:
        self._reponses = list(reponses)
        self.demandes: list[DemandeQuestion] = []
        self.pendant: list[Any] = []

    async def __call__(self, demande: DemandeQuestion) -> str:
        self.demandes.append(demande)
        reponse = self._reponses.pop(0) if self._reponses else None
        if reponse is None:
            await asyncio.Event().wait()  # personne ne répond
        return reponse


def _moteur(
    fournisseur: Fournisseur,
    *,
    questionneur: Any = None,
    arbitre_renfort: Any = None,
    agents: AgentStore | None = None,
    rattrapage: PolitiqueRattrapage | None = None,
    attente_s: float = 5.0,
) -> OrchestrationEngine:
    return OrchestrationEngine(
        fournisseur,
        Orchestrator(fournisseur, model="chef"),
        rattrapage=rattrapage if rattrapage is not None else PolitiqueRattrapage(),
        questionneur=questionneur,
        arbitre_renfort=arbitre_renfort,
        agents_store=agents,
        bornes_question=BornesArbitrage(attente_s=attente_s),
    )


def _statuts(journal: RunJournal, etape: str) -> list[str]:
    return [r.statut for r in journal.records if r.etape == etape]


def _etapes(journal: RunJournal, suffixe: str):
    return [r for r in journal.records if r.etape.endswith(suffixe)]


# --- Critère ① : un serveur MCP à authentifier suspend la tâche -----------------------------


def test_un_serveur_mcp_a_authentifier_suspend_la_tache_propose_la_procedure_et_la_reprend():
    """Le cas du ticket, de bout en bout : suspendue, proposée, reprise — un seul run.

    Pendant que la carte attend, la tâche **n'est pas en échec** : son étape dit
    « en attente d'un humain », et rien de l'aval n'est parti. La procédure est
    celle de la bibliothèque MCP pour `github` (jeton, écran, lien), et le geste
    « c'est fait » reprend la tâche — qui trouve alors son serveur joignable.
    """
    fournisseur = Fournisseur(
        _plan("MCP-GITHUB"),
        {"MCP-GITHUB": [_github_a_authentifier(), "tickets lus"], "AVAL-T2": ["documenté"]},
    )
    journal = RunJournal()

    class Fil(Questionneur):
        async def __call__(self, demande: DemandeQuestion) -> str:
            # La carte attend : la tâche est suspendue, pas échouée, et l'aval attend.
            self.pendant.append((_statuts(journal, "t1"), fournisseur.compte("AVAL-T2")))
            return await super().__call__(demande)

    fil = Fil(CHOIX_PREREQUIS_LEVE)

    rapport = asyncio.run(_moteur(fournisseur, questionneur=fil).run("Objectif", journal=journal))

    ((statuts_pendant, aval_pendant),) = fil.pendant
    assert statuts_pendant == [STATUT_EN_ATTENTE_VALIDATION]
    assert aval_pendant == 0
    (carte,) = fil.demandes
    # La procédure que la bibliothèque connaît pour ce serveur-là.
    assert "« github » demande une authentification" in carte.question
    assert "GITHUB_TOKEN" in carte.question
    assert "l'écran Intégrations du projet" in carte.question
    assert "core/mcp/README.md#obtention-du-token-github" in carte.question
    assert "sans relancer le run" in carte.question
    assert carte.choix == (CHOIX_PREREQUIS_LEVE,)
    assert (carte.tache_id, carte.agent, carte.run_id) == ("t1", "orchestrateur", journal.run_id)
    assert "Tâche t2" in carte.hypothese
    # La tâche a repris, a trouvé son serveur, et l'aval est parti sur son livrable.
    t1, t2 = rapport.resultats
    assert t1.ok and t1.sortie == "tickets lus"
    assert t2.ok
    assert fournisseur.compte("MCP-GITHUB") == 2
    assert _statuts(journal, "t1") == [STATUT_EN_ATTENTE_VALIDATION, STATUT_TERMINEE]
    # Un constat ne se juge pas : aucun diagnostic du Chef de projet n'a été payé.
    assert fournisseur.diagnostics == []
    # Et le run n'a pas été relancé : un seul plan.
    assert len(_statuts(journal, "planification")) == 1
    (issue,) = _etapes(journal, SUFFIXE_ETAPE_RATTRAPAGE)
    assert issue.statut == STATUT_PREREQUIS_LEVE and issue.etape == f"t1{SUFFIXE_ETAPE_RATTRAPAGE}"


def test_la_carte_suspendue_se_lit_attente_humaine_et_compte_en_cours():
    """Ce que l'écran reçoit de l'étape suspendue : un statut de tâche, « en cours »."""
    fournisseur = Fournisseur(
        _plan("MCP-GITHUB"),
        {"MCP-GITHUB": [_github_a_authentifier(), "ok"], "AVAL-T2": ["ok"]},
    )
    journal = RunJournal()

    asyncio.run(
        _moteur(fournisseur, questionneur=Questionneur(CHOIX_PREREQUIS_LEVE)).run(
            "Objectif", journal=journal
        )
    )

    suspendue = next(r for r in journal.records if r.etape == "t1")
    (event,) = evenements_depuis_step(suspendue.to_dict())
    assert event.type == "tache.statut"
    assert event.statut == STATUT_EN_ATTENTE_VALIDATION
    assert compartiment(event.statut) == EN_COURS


def test_un_serveur_toujours_injoignable_se_repropose_puis_la_carte_dit_l_echec():
    """La reprise rencontre le même manque : la carte le dit (« toujours »), deux fois au plus.

    Au-delà, c'est le Chef de projet qui juge ; sans question permise, la tâche
    reste en échec — et sa carte, suspendue jusque-là, le **dit** au lieu de rester
    en attente d'un geste que plus rien ne reprendra.
    """
    fournisseur = Fournisseur(
        _plan("MCP-GITHUB"),
        {"MCP-GITHUB": [_github_a_authentifier()], "AVAL-T2": ["ok"]},
        rattrapages=[
            {
                "nature": "configuration",
                "diagnostic": "Le jeton GitHub n'est toujours pas accepté.",
                "geste": "demander",
                "question": "Le jeton est-il bien celui du dépôt ?",
            }
        ],
    )
    fil = Questionneur(CHOIX_PREREQUIS_LEVE, CHOIX_PREREQUIS_LEVE)
    journal = RunJournal()

    rapport = asyncio.run(
        _moteur(
            fournisseur,
            questionneur=fil,
            rattrapage=PolitiqueRattrapage(max_questions=0),
        ).run("Objectif", journal=journal)
    )

    premiere, seconde = fil.demandes
    assert "est suspendue" in premiere.question
    assert "est toujours suspendue" in seconde.question
    assert fournisseur.compte("MCP-GITHUB") == 3
    assert len(fournisseur.diagnostics) == 1
    t1, t2 = rapport.resultats
    assert t1.statut == STATUT_ECHEC and "plus aucune question" in (t1.erreur or "")
    assert t2.statut == STATUT_BLOQUEE
    assert _statuts(journal, "t1")[-1] == STATUT_ECHEC


def test_une_proposition_sans_reponse_laisse_la_tache_en_echec_et_solde_sa_carte():
    fournisseur = Fournisseur(
        _plan("MCP-GITHUB"), {"MCP-GITHUB": [_github_a_authentifier()], "AVAL-T2": ["ok"]}
    )
    journal = RunJournal()

    rapport = asyncio.run(
        _moteur(fournisseur, questionneur=Questionneur(None), attente_s=0.05).run(
            "Objectif", journal=journal
        )
    )

    t1, t2 = rapport.resultats
    assert t1.statut == STATUT_ECHEC and "sans réponse" in (t1.erreur or "")
    assert t2.statut == STATUT_BLOQUEE
    assert _statuts(journal, "t1") == [STATUT_EN_ATTENTE_VALIDATION, STATUT_ECHEC]
    (question,) = _etapes(journal, SUFFIXE_ETAPE_QUESTION)
    assert question.statut == STATUT_QUESTION_SANS_REPONSE
    assert any(
        r.statut == STATUT_PREREQUIS_SANS_REPONSE
        for r in _etapes(journal, SUFFIXE_ETAPE_RATTRAPAGE)
    )
    # Personne n'a répondu : il n'y avait rien à juger de plus.
    assert fournisseur.diagnostics == []


def test_une_reponse_en_mots_part_au_chef_de_projet_qui_peut_rejouer_la_tache():
    """Une phrase n'est lue par aucun motif : le Chef de projet la juge, et elle fait autorité.

    Sa réponse change l'environnement — un jeton déposé —, ce qui rend légitime de
    rejouer à l'identique un échec qui n'était pas passager.
    """
    fournisseur = Fournisseur(
        _plan("MCP-GITHUB"),
        {"MCP-GITHUB": [_github_a_authentifier(), "tickets lus"], "AVAL-T2": ["ok"]},
        rattrapages=[
            {
                "nature": "configuration",
                "diagnostic": "L'utilisateur a déposé le jeton : le serveur doit répondre.",
                "geste": "rejouer",
            }
        ],
    )
    reponse = "J'ai mis un jeton neuf dans le coffre du projet."

    rapport = asyncio.run(
        _moteur(fournisseur, questionneur=Questionneur(reponse)).run(
            "Objectif", journal=RunJournal()
        )
    )

    (diagnostic,) = fournisseur.diagnostics
    assert diagnostic.rstrip().endswith(reponse)
    assert "Tâche t1" in diagnostic and "github" in diagnostic
    t1, t2 = rapport.resultats
    assert t1.ok and t1.sortie == "tickets lus"
    assert t2.ok


def test_sans_fil_ou_proposer_la_tache_echoue_et_se_rattrape_comme_avant():
    """Personne à qui proposer : aucune carte ne reste en attente d'un geste impossible."""
    fournisseur = Fournisseur(
        _plan("MCP-GITHUB"),
        {"MCP-GITHUB": [_github_a_authentifier()], "AVAL-T2": ["ok"]},
        rattrapages=[
            {
                "nature": "configuration",
                "diagnostic": "Le serveur demande une authentification.",
                "geste": "demander",
                "question": "Pouvez-vous authentifier GitHub ?",
            }
        ],
    )
    journal = RunJournal()

    rapport = asyncio.run(_moteur(fournisseur).run("Objectif", journal=journal))

    t1, _ = rapport.resultats
    assert t1.statut == STATUT_ECHEC and "personne" in (t1.erreur or "")
    assert _statuts(journal, "t1") == [STATUT_ECHEC]
    assert len(fournisseur.diagnostics) == 1


# --- Critère ① : la procédure que la bibliothèque connaît ----------------------------------


def test_la_procedure_d_un_serveur_vient_de_la_bibliotheque():
    registre = RegistreMcp.curee()

    github = prerequis_du_mcp(_github_a_authentifier(), registre)
    figma = prerequis_du_mcp(
        McpServerUnavailable(
            "…", (ServeurInjoignable("figma-officiel", MCP_A_AUTHENTIFIER, "token expiré"),)
        ),
        registre,
    )
    slack = prerequis_du_mcp(
        McpServerUnavailable(
            "…",
            (ServeurInjoignable("slack", MCP_NON_MONTABLE, "…", ("SLACK_BOT_TOKEN",)),),
        ),
        registre,
    )
    inconnu = prerequis_du_mcp(
        McpServerUnavailable("…", (ServeurInjoignable("maison", MCP_A_AUTHENTIFIER),)),
        registre,
    )

    assert github is not None and github.genre == GENRE_SERVEUR and github.objet == "github"
    assert "fournissez GITHUB_TOKEN" in github.procedure
    # Un jeton OAuth importé se réimporte : il expire.
    assert figma is not None and "importez un jeton OAuth neuf" in figma.procedure
    assert "token expiré" in figma.raison
    # Un serveur non montable nomme ce qui manquait, et seulement ça.
    assert slack is not None and "SLACK_BOT_TOKEN" in slack.procedure
    assert "SLACK_TEAM_ID" not in slack.procedure
    assert "il lui manque SLACK_BOT_TOKEN" in slack.raison
    # Hors de la bibliothèque, rien n'est inventé : la déclaration fait foi.
    assert inconnu is not None and "n'est pas dans la bibliothèque" in inconnu.procedure


def test_une_indisponibilite_sans_faits_ne_propose_rien():
    """Le message n'est jamais lu : sans faits en données, rien n'est deviné."""
    assert prerequis_du_mcp(McpServerUnavailable("github : état « needs-auth »")) is None


def test_un_secret_absent_rend_un_serveur_non_montable_en_donnees():
    serveur = ServeurMcp(
        nom="tickets", type="stdio", commande="npx", env={"TOKEN": "${GITLAB_TOKEN}"}
    )

    with pytest.raises(McpServerUnavailable) as excinfo:
        resolus((serveur,), {})

    (injoignable,) = excinfo.value.serveurs
    assert injoignable.nom == "tickets"
    assert injoignable.etat == MCP_NON_MONTABLE
    assert injoignable.references == ("GITLAB_TOKEN",)


def test_le_prerequis_et_les_blocages_voyagent_avec_le_resultat():
    """Aller-retour JSON — la remontée d'un résultat de worker (#41) les garde."""
    resultat = TaskResult(
        task_id="t1",
        titre="Tâche t1",
        agent="dev",
        role="Développeur",
        competences_requises=("backend",),
        score=1,
        statut=STATUT_ECHEC,
        sortie="",
        erreur="…",
        prerequis=PrerequisManquant(
            genre=GENRE_ROLE,
            objet="Designer",
            raison="…",
            competences=("ui",),
            gabarit="interface",
            couvre=("ui",),
        ),
        blocages=("il me manque un designer",),
    )

    relu = TaskResult.from_dict(json.loads(json.dumps(resultat.to_dict())))

    assert relu == resultat
    assert TaskResult.from_dict({**resultat.to_dict(), "prerequis": None}).prerequis is None


# --- Critère ② : un rôle absent se propose en cours de run ---------------------------------


class Recruteur:
    """Un `ArbitreRenfort` : note la demande, et **recrute pour de vrai** sur un oui.

    Il écrit la fiche dans le dépôt du projet, comme la création réelle (#1040) :
    c'est ce qui permet de voir la tâche reprise aller au nouveau venu.
    """

    FICHE = AgentDefinition(
        nom="interface",
        role="Designer",
        competences=("ui", "ux", "design-system"),
        playbook="Tu dessines les écrans de p1.",
    )

    def __init__(self, agents: AgentStore, decision: DecisionRenfort) -> None:
        self._agents = agents
        self._decision = decision
        self.demandes: list[DemandeRenfort] = []

    async def __call__(self, demande: DemandeRenfort) -> DecisionRenfort:
        self.demandes.append(demande)
        if self._decision.approuve:
            self._agents.pour_projet(demande.projet_id).ecrire(self.FICHE)
        return self._decision


def _equipe_d_un_developpeur(tmp_path: Path) -> AgentStore:
    agents = AgentStore(tmp_path / "agents")
    agents.pour_projet(PROJET).ecrire(
        AgentDefinition(
            nom="dev",
            role="Développeur",
            competences=("backend", "frontend"),
            playbook="Tu écris le code de p1.",
        )
    )
    return agents


def _retenter_en_ui() -> dict:
    """Le Chef de projet reprend la tâche en travail d'**écran**, que personne ne sait faire."""
    return {
        "nature": "approche",
        "diagnostic": "Le module est avant tout un écran : c'est un travail de design.",
        "geste": "retenter",
        "taches": [_tache("t1", "Maquetter l'écran du module (MAQUETTE).", competences=("ui",))],
    }


def test_un_role_absent_en_cours_de_run_se_propose_et_son_recrutement_reprend_la_tache(
    tmp_path: Path,
):
    """Une tâche que personne ne sait prendre : suspendue, le rôle proposé, recruté, reprise.

    Le plan, lui, était couvert — l'équipe n'avait donc rien à compléter avant le
    run (#1227). C'est la reprise de t1 qui appelle un métier absent, **en cours
    de run** : elle ne part plus « à assigner », elle attend le recrutement, et le
    Designer recruté la prend sans que rien d'autre ne soit relancé.
    """
    agents = _equipe_d_un_developpeur(tmp_path)
    recruteur = Recruteur(agents, DecisionRenfort(approuve=True, detail="recruté : Designer"))
    fil = Questionneur()
    fournisseur = Fournisseur(
        _plan("APPROCHE-A"),
        {
            "APPROCHE-A": [RuntimeError("le module n'a rien à montrer")],
            "MAQUETTE": ["maquette livrée"],
            "AVAL-T2": ["documenté"],
        },
        rattrapages=[_retenter_en_ui()],
    )
    journal = RunJournal()

    rapport = asyncio.run(
        _moteur(fournisseur, questionneur=fil, arbitre_renfort=recruteur, agents=agents).run(
            "Objectif", journal=journal, projet_id=PROJET
        )
    )

    (demande,) = recruteur.demandes
    assert demande.tache == "Tâche t1"
    assert demande.manque.manque.role == "Designer"
    assert demande.manque.manque.gabarit == "interface"
    assert demande.manque.taches == ("Tâche t1",)
    assert (demande.run_id, demande.projet_id, demande.objectif) == (
        journal.run_id,
        PROJET,
        "Objectif",
    )
    # Le rôle part sur la carte d'équipe, pas sur une question.
    assert fil.demandes == []
    t1, t2 = rapport.resultats
    assert t1.ok and t1.agent == "interface" and t1.sortie == "maquette livrée"
    assert t2.ok and t2.agent == "dev"
    # La reprise « à assigner » a suspendu la carte, puis elle a repris.
    assert STATUT_EN_ATTENTE_VALIDATION in _statuts(journal, "t1")
    assert _statuts(journal, "t1")[-1] == STATUT_TERMINEE
    assert len(_statuts(journal, "planification")) == 1
    assert any(
        r.statut == STATUT_PREREQUIS_LEVE for r in _etapes(journal, SUFFIXE_ETAPE_RATTRAPAGE)
    )


def test_un_role_decline_fait_reprendre_la_tache_au_plus_proche(tmp_path: Path):
    """Décliner ne laisse pas la tâche « à assigner » : elle va au plus proche (#1260)."""
    agents = _equipe_d_un_developpeur(tmp_path)
    recruteur = Recruteur(agents, DecisionRenfort(approuve=False, detail="pas de designer"))
    fournisseur = Fournisseur(
        _plan("APPROCHE-A"),
        {
            "APPROCHE-A": [RuntimeError("le module n'a rien à montrer")],
            "MAQUETTE": ["maquette faite par le développeur"],
            "AVAL-T2": ["documenté"],
        },
        rattrapages=[_retenter_en_ui()],
        plus_proche="dev",
    )
    journal = RunJournal()

    rapport = asyncio.run(
        _moteur(
            fournisseur, questionneur=Questionneur(), arbitre_renfort=recruteur, agents=agents
        ).run("Objectif", journal=journal, projet_id=PROJET)
    )

    t1, t2 = rapport.resultats
    assert t1.ok and t1.agent == "dev"
    assert t2.ok
    issue = next(
        r for r in _etapes(journal, SUFFIXE_ETAPE_RATTRAPAGE) if r.statut.startswith("prerequis")
    )
    assert issue.statut == STATUT_PREREQUIS_DECLINE
    assert "rôle le plus proche" in issue.sortie


# --- Critère ② : un blocage signalé devient une proposition --------------------------------


def test_un_blocage_signale_devient_un_secret_propose_et_son_acceptation_reprend_la_tache():
    """L'agent dit ce qui lui manque ; le Chef de projet le lit et le propose ; la tâche reprend.

    Le blocage arrive au Chef de projet avec l'échec — c'est la vraie cause, que
    « réponse vide » ne dit pas. Il en fait un **secret** à fournir, avec sa
    procédure ; accepté d'un geste, la tâche est rejouée telle quelle.
    """
    raison = "il me manque la clé STRIPE_API_KEY pour appeler l'API de paiement"
    fournisseur = Fournisseur(
        _plan("PAIEMENT"),
        {"PAIEMENT": [Bloque(raison), "paiement encaissé"], "AVAL-T2": ["ok"]},
        rattrapages=[
            {
                "nature": "configuration",
                "diagnostic": "L'agent n'a pas la clé de l'API de paiement.",
                "geste": "proposer",
                "prerequis": {
                    "genre": "secret",
                    "objet": "STRIPE_API_KEY",
                    "raison": "la tâche appelle l'API de paiement Stripe",
                    "procedure": "Renseignez STRIPE_API_KEY dans le coffre du projet.",
                },
            }
        ],
    )
    fil = Questionneur(CHOIX_PREREQUIS_LEVE)
    journal = RunJournal()

    rapport = asyncio.run(
        _moteur(fournisseur, questionneur=fil).run("Objectif", journal=journal)
    )

    (diagnostic,) = fournisseur.diagnostics
    assert "Blocages que l'agent a signalés" in diagnostic and raison in diagnostic
    (carte,) = fil.demandes
    assert "il manque le secret « STRIPE_API_KEY »" in carte.question
    assert "Renseignez STRIPE_API_KEY dans le coffre du projet." in carte.question
    assert carte.choix == (CHOIX_PREREQUIS_LEVE,)
    t1, t2 = rapport.resultats
    assert t1.ok and t1.sortie == "paiement encaissé"
    assert t2.ok
    # Reprise **telle quelle** : la même tâche, le même prompt.
    premier, second = fournisseur.prompts["PAIEMENT"]
    assert premier == second
    # L'exécuteur a rendu l'échec — le blocage n'est pas un constat ; dès que le
    # Chef de projet propose, la carte attend un geste, puis la tâche aboutit.
    assert _statuts(journal, "t1") == [STATUT_ECHEC, STATUT_EN_ATTENTE_VALIDATION, STATUT_TERMINEE]
    assert len(_statuts(journal, "planification")) == 1


def test_un_blocage_qui_appelle_un_role_le_fait_recruter_et_la_tache_va_au_nouveau_venu(
    tmp_path: Path,
):
    """« Personne ici ne sait dessiner cet écran » : un rôle, recruté, qui prend la tâche."""
    agents = _equipe_d_un_developpeur(tmp_path)
    recruteur = Recruteur(agents, DecisionRenfort(approuve=True, detail="recruté : Designer"))
    fournisseur = Fournisseur(
        _plan("ECRAN"),
        {
            "ECRAN": [Bloque("je ne sais pas dessiner cet écran"), "écran dessiné"],
            "AVAL-T2": ["ok"],
        },
        rattrapages=[
            {
                "nature": "approche",
                "diagnostic": "L'écran demande un designer, que l'équipe n'a pas.",
                "geste": "proposer",
                "prerequis": {
                    "genre": "role",
                    "objet": "Designer",
                    "raison": "l'agent dit ne pas savoir dessiner l'écran",
                    "competences": ["ui"],
                },
            }
        ],
    )

    rapport = asyncio.run(
        _moteur(
            fournisseur, questionneur=Questionneur(), arbitre_renfort=recruteur, agents=agents
        ).run("Objectif", journal=RunJournal(), projet_id=PROJET)
    )

    (demande,) = recruteur.demandes
    # Le rôle nommé par le Chef de projet devient un poste que Maestro sait pourvoir.
    assert demande.manque.manque.gabarit == "interface"
    assert demande.manque.manque.role == "Designer"
    assert demande.tache == "Tâche t1"
    t1, t2 = rapport.resultats
    assert t1.ok and t1.agent == "interface" and t1.sortie == "écran dessiné"
    assert t2.ok


# --- Le contrat du Chef de projet : proposer, et rejouer après une réponse ------------------


def _echec(**kwargs) -> EchecDeTache:
    tache = Task.from_dict(_tache("t1", "Réaliser le module."))
    return EchecDeTache(
        tache=tache,
        tentatives=(
            Tentative(
                taches=(tache,),
                agent="dev",
                role="Développeur",
                erreur="réponse vide de l'agent.",
                blocages=("il me manque la clé Stripe",),
            ),
        ),
        **kwargs,
    )


def test_proposer_se_valide_en_prerequis():
    verdict = valide_rattrapage(
        {
            "nature": "configuration",
            "diagnostic": "…",
            "geste": "proposer",
            "prerequis": {"genre": "secret", "objet": "STRIPE_API_KEY", "raison": "paiement"},
        },
        _echec(),
    )
    assert verdict.prerequis == PrerequisManquant(
        genre=GENRE_SECRET, objet="STRIPE_API_KEY", raison="paiement"
    )


@pytest.mark.parametrize(
    ("prerequis", "motif"),
    [
        pytest.param(None, "rien à proposer", id="absent"),
        pytest.param({"genre": "licence", "objet": "x", "raison": "y"}, "genre", id="genre"),
        pytest.param({"genre": "secret", "objet": "", "raison": "y"}, "objet", id="objet"),
        pytest.param(
            {"genre": "role", "objet": "Designer", "raison": "y"}, "compétences", id="role"
        ),
    ],
)
def test_un_prerequis_mal_forme_est_refuse(prerequis: Any, motif: str):
    verdict = {"nature": "configuration", "diagnostic": "…", "geste": "proposer"}
    with pytest.raises(RattrapageValidationError, match=motif):
        valide_rattrapage({**verdict, "prerequis": prerequis}, _echec())


def test_rejouer_un_echec_non_passager_n_est_permis_qu_apres_une_reponse():
    verdict = {"nature": "configuration", "diagnostic": "Jeton déposé.", "geste": "rejouer"}
    with pytest.raises(RattrapageValidationError, match="ne se rejoue pas"):
        valide_rattrapage(verdict, _echec())
    assert valide_rattrapage(verdict, _echec(reponse="c'est fait")).rejoue


def test_le_prompt_montre_les_blocages_comme_une_donnee():
    prompt = build_rattrapage_user_prompt(_echec())
    assert "Blocages que l'agent a signalés pendant la tentative (donnée)" in prompt
    assert "il me manque la clé Stripe" in prompt


# --- La Control Tower : la demande de renfort d'une tâche en cours de run -------------------


def _demande_en_cours(attente_s: float = 30.0) -> DemandeRenfort:
    manque = manque_au_plan(
        [Task.from_dict(_tache("t1", "Maquetter.", competences=("ui",)))],
        [AgentDefinition(nom="dev", role="Dev", competences=("backend",), playbook="…").to_agent()],
    )
    assert manque is not None
    return DemandeRenfort(
        run_id="run-1",
        projet_id=PROJET,
        objectif="Objectif",
        manque=manque,
        attente_s=attente_s,
        tache="Tâche t1",
    )


def test_la_demande_d_une_tache_suspendue_voyage_et_se_relit():
    from maestro.controltower.chat import DemandeRecrutement
    from maestro.controltower.renfort import evenement_demande

    event = evenement_demande(_demande_en_cours())

    assert event.recrutement is not None and event.recrutement["tache"] == "Tâche t1"
    relue = DemandeRecrutement.from_dict(event.recrutement)
    assert relue.tache == "Tâche t1" and relue.pendant_un_run
    # Une demande d'avant #1181 se relit sans tâche : le plan entier.
    assert DemandeRecrutement.from_dict({**event.recrutement, "tache": None}).tache == ""


def test_le_relais_dit_qu_une_tache_est_suspendue_et_non_qu_un_plan_attend(tmp_path: Path):
    """Les faits que le fil reçoit : la tâche qui attend, et qu'elle reprendra sans relancer."""
    from maestro.controltower.chat import ChatStore, ServiceChat
    from maestro.controltower.events import InMemoryEventBus
    from maestro.controltower.orchestration import AGENT_ORCHESTRATION, NOM_ORCHESTRATION
    from maestro.controltower.renfort import RelaisRenfort, evenement_demande
    from maestro.messaging import InMemoryMailbox

    class Repondeur:
        def __init__(self) -> None:
            self.faits: list[str] = []

        async def rediger(self, agent: Any, fil: Any, *, faits: str) -> str:
            self.faits.append(faits)
            return f"Message n°{len(self.faits)}."

    bus = InMemoryEventBus()
    repondeur = Repondeur()
    fil = ServiceChat(
        store=ChatStore(tmp_path / "chat"),
        repondeur=repondeur,
        mailbox=InMemoryMailbox(),
        bus=bus,
    )

    asyncio.run(
        RelaisRenfort(bus, fil, AGENT_ORCHESTRATION).relayer(
            evenement_demande(_demande_en_cours(attente_s=0.05))
        )
    )

    posee, echue = repondeur.faits
    assert "la tâche « Tâche t1 »" in posee
    assert "sans relancer le run" in posee
    assert "vient d'écrire son plan" not in posee
    assert "la tâche a repris" in echue
    pose = fil.fil(NOM_ORCHESTRATION)[0]
    assert pose.recrutement is not None and pose.recrutement.tache == "Tâche t1"

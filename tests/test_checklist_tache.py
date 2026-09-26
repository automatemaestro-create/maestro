"""La **checklist d'une tâche** : qui la pose, et comment elle avance (#489, lot 1
de #488 ; couvert ici par #492).

#246 avait tout posé sauf l'appelant — les étapes définies, le journal qui les
transporte, `tache.detail` qui les diffuse, le panneau qui les affiche — et
`consigne_detail` n'était appelé par personne. Le lot 1 a tranché **qui pose la
checklist** (ossature au plan, complétée et cochée par l'agent) et branché les
deux moitiés ; les tests étaient différés au lot final
([docs/10 §5.1](../docs/10-workflow-git.md)), les voici.

Six volets, dans l'ordre où la donnée descend — du plan jusqu'à la carte servie :

① **L'ossature au plan** (`Task.etapes`) — le plan dit *ce qu'il y a à faire*,
   jamais *où l'on en est*. La clé est **omise** de `to_dict` quand il n'y en a
   pas : un plan sans ossature doit rester sérialisable tel quel, comme pour
   `ticket` et `projet_id`.

② **La réconciliation** (`SuiviChecklist`) — c'est là que vivent les trois
   règles du ticket, et chacune est éprouvée **seule** : le premier relevé
   *supplante* l'ossature, rien ne recule ensuite (ni un état, ni une étape
   qu'un relevé oublie), et le dénominateur *peut* grandir. Plus la quatrième,
   qui n'est pas dans les critères mais paie le prix des trois autres :
   `rapporte` rend `None` quand rien n'a changé.

③ **Le verbe** (`maestro.providers.checklist`, #1291) — la checklist est un
   verbe de Maestro, `tenir_checklist`, et non plus la lecture d'un outil du CLI
   Claude : c'est cette lecture qui a laissé toutes les checklists à 0/N le jour
   où le CLI a changé d'outil (docs/44). L'appeler alimente `on_etapes` avec
   l'état complet ; une entrée invalide est **dite** à l'agent, jamais relevée à
   moitié, et un canal en panne ne tue pas la tâche.

④ **Le fournisseur** (`ClaudeProvider`) — il sert le verbe par le serveur
   in-process `maestro`, comme `consigner_decision`, et **ne lit plus aucun
   outil du CLI** : un `TodoWrite`, un `TaskCreate` ou un `TaskUpdate` dans le
   flux ne cochent rien. Le socle des playbooks nomme le verbe, et aucun outil de
   liste du CLI n'est plus confié à l'agent.

⑤ **Le moteur** (`LocalExecutor`) — l'ossature part **avant** la première
   tentative, les relevés se consignent par `consigne_detail`, et le suivi vit à
   l'échelle de l'**exécution** : à travers une relance, l'avancement acquis ne
   recule pas.

⑥ **L'API** — le chemin entier en un seul contrôle : ce que l'agent coche
   ressort sur la carte que `GET /api/taches?run=` sert. Les cinq volets
   ci-dessus éprouvent chacun leur maillon ; celui-ci est le seul qui rougirait
   si un maillon du **milieu** se taisait.

**Ni réseau, ni appel modèle, ni CLI** : le fournisseur est un double, le verbe
est appelé comme le ferait le SDK (patron de `tests/test_decisions_autonomes.py`),
le flux SDK est simulé par des blocs factices substitués aux types du module (le
même harnais que `tests/test_providers.py`), et l'app du volet ⑥ est la vraie
(`create_app`) sur bus mémoire.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from maestro.agents import DEVELOPER_PROFILE, AgentRuntime
from maestro.agents.playbook_du_code import cadre_outille
from maestro.agents.runtime import DEFAULT_TOOLS
from maestro.controltower import (
    ControlTowerState,
    Event,
    InMemoryEventBus,
    InMemoryEventLog,
    create_app,
)
from maestro.controltower.bridge import evenements_depuis_step
from maestro.controltower.events import (
    EVENEMENT_TACHE_DETAIL,
)
from maestro.controltower.state import EVENEMENT_EXECUTION_STATUT, EXECUTION_EN_COURS
from maestro.detail_tache import (
    ETAPE_A_FAIRE,
    ETAPE_EN_COURS,
    ETAPE_FAITE,
    SUFFIXE_ETAPE_DETAIL,
    EtapeTache,
    SuiviChecklist,
    phrase_checklist_sans_releve,
    phrase_ecart_checklist,
)
from maestro.engine.executor import (
    SUFFIXE_ETAPE_ACTIVITE,
    LocalExecutor,
    _build_task_description,
)
from maestro.engine.retry import PolitiqueRelance
from maestro.orchestrator.schema import Task
from maestro.providers import checklist as checklist_mod
from maestro.providers import claude as claude_mod
from maestro.providers.arbitrage import NOM_SERVEUR
from maestro.providers.base import Credentials, ModelProvider, UnsupportedCapability
from maestro.providers.claude import ClaudeProvider, _outil_checklist, _outils_maestro
from maestro.telemetry.journal import RunJournal

# ------------------------------------------------------------------ harnais


def _tache(**kwargs) -> Task:
    """Une tâche routée sur `developpeur` par sa compétence — le chemin outillé."""
    champs: dict[str, object] = {
        "id": "api-crud",
        "titre": "API CRUD",
        "description": "Exposer les routes.",
        "competences_requises": ("backend",),
        "format_sortie": "Module d'API",
    }
    champs.update(kwargs)
    return Task(**champs)  # type: ignore[arg-type]


def _releve(*paires: tuple[str, str]) -> list[EtapeTache]:
    """Un relevé d'agent : des couples (libellé, état)."""
    return [EtapeTache(libelle=libelle, etat=etat) for libelle, etat in paires]


def _entree_verbe(*paires: tuple[str, str]) -> dict[str, object]:
    """L'entrée d'un appel au verbe `tenir_checklist`, telle que le SDK la fait passer."""
    return {"etapes": [{"libelle": libelle, "etat": etat} for libelle, etat in paires]}


def _appelle_verbe(on_etapes, entree: object) -> str:
    """Appelle le verbe comme le ferait le SDK, et rend le texte servi à l'agent.

    Le patron de `tests/test_decisions_autonomes.py` : l'outil est rendu
    séparément de son serveur, donc il s'éprouve sans CLI, sans sous-processus et
    sans quota.
    """
    reponse = asyncio.run(_outil_checklist(on_etapes).handler(entree))
    (bloc,) = reponse["content"]
    assert bloc["type"] == "text"
    # Jamais une erreur d'outil : une erreur inviterait l'agent à rejouer le même
    # appel, or une entrée invalide se corrige, et un canal en panne ne se rejoue pas.
    assert not reponse.get("isError")
    return bloc["text"]


def _todos(*paires: tuple[str, str]) -> dict[str, object]:
    """L'entrée qu'un outil de liste du CLI Claude (`TodoWrite`) portait — **plus lue**."""
    return {
        "todos": [
            {"content": contenu, "activeForm": f"{contenu}…", "status": statut}
            for contenu, statut in paires
        ]
    }


class FournisseurChecklist(ModelProvider):
    """Exécutant outillé factice : rejoue, tentative par tentative, des relevés.

    `releves[n]` est la liste des relevés que l'agent rapporte à la n-ième
    tentative ; `echecs` dit combien de tentatives lèvent (aléa transitoire), ce
    qui exerce la relance sans dépendre d'un vrai fournisseur.
    """

    name = "checklist"

    def __init__(
        self,
        releves: list[list[list[EtapeTache]]] | None = None,
        *,
        echecs: int = 0,
    ) -> None:
        self._releves = releves or []
        self._echecs = echecs
        self.tentatives = 0
        self.prompts: list[str] = []
        self.generate_calls: list[str] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        self.generate_calls.append(prompt)
        return "TEXTE"

    async def run_agent(
        self, prompt, *, model, system_prompt=None, workspace, tools,
        mcp_serveurs=(), politique=None, on_refus=None, on_arbitrage_acte=None,
        on_activite=None, on_etapes=None,
        on_arbitrage=None, on_blocage=None, on_decision=None, credit_arbitrage=None,
        on_courrier=None, on_question=None,
        plafond_tours=None, projet=None,
    ):
        self.tentatives += 1
        self.prompts.append(prompt)
        if on_etapes is not None and self.tentatives <= len(self._releves):
            for releve in self._releves[self.tentatives - 1]:
                on_etapes(releve)
        if self.tentatives <= self._echecs:
            raise RuntimeError("aléa fournisseur")
        (Path(workspace) / "livrable.txt").write_text("fait", encoding="utf-8")
        return f"OUTILLE #{self.tentatives}"


def _executeur(provider: ModelProvider, *, outille: bool = True, relance=None) -> LocalExecutor:
    """Un exécuteur local, outillé (chemin `run_agent`) ou en repli texte."""
    runtimes = (
        {DEVELOPER_PROFILE.nom: AgentRuntime(provider, DEVELOPER_PROFILE)} if outille else {}
    )
    return LocalExecutor(provider, runtimes=runtimes, relance=relance)


def _joue(executeur: LocalExecutor, task: Task) -> RunJournal:
    """Exécute `task` et rend le journal qu'elle a rempli."""
    journal = RunJournal(run_id="run-checklist")
    asyncio.run(executeur.execute(task, [], journal))
    return journal


def _details(journal: RunJournal) -> list[list[tuple[str, str]]]:
    """Les checklists consignées, une entrée par ligne `<tache>:detail`."""
    return [
        [(etape.libelle, etape.etat) for etape in record.etapes]
        for record in journal.records
        if record.etape.endswith(SUFFIXE_ETAPE_DETAIL)
    ]


# ---------------------------- ① L'ossature au plan : ce qu'il y a à faire, pas où l'on en est


def test_le_plan_transporte_l_ossature_et_l_omet_quand_il_n_y_en_a_pas():
    """Aller-retour fidèle, et clé **absente** plutôt que liste vide.

    Même régime que `ticket` et `projet_id` : un plan écrit avant ce lot doit
    rester sérialisable et relisible tel quel.
    """
    avec = Task(
        id="t1", titre="T", description="d", competences_requises=("backend",),
        format_sortie="md", etapes=("Lister les entités", "Écrire la migration"),
    )
    assert avec.to_dict()["etapes"] == ["Lister les entités", "Écrire la migration"]
    assert Task.from_dict(avec.to_dict()).etapes == avec.etapes

    sans = Task(
        id="t2", titre="T", description="d", competences_requises=(), format_sortie="md"
    )
    assert "etapes" not in sans.to_dict()
    assert Task.from_dict(sans.to_dict()).etapes == ()


def test_l_ossature_ne_porte_jamais_d_avancement():
    """Le schéma partagé déclare des **libellés seuls** : pas d'état dans un plan.

    C'est la moitié « le plan ne dit pas où l'on en est » de l'arbitrage, et elle
    se vérifie là où elle est opposable à un producteur de plan — le schéma, pas
    la dataclasse.
    """
    schema = json.loads(
        Path("packages/shared/schemas/task.schema.json").read_text(encoding="utf-8")
    )
    etapes = schema["properties"]["etapes"]
    assert etapes["type"] == "array"
    assert etapes["items"]["type"] == "string"
    assert "etapes" not in schema.get("required", [])


def test_l_ossature_est_donnee_a_l_agent_comme_une_proposition():
    """Sans elle sous les yeux, l'agent ouvre sa liste sur ce qu'il imagine.

    Et comme son relevé **supplante** l'ossature, ce que l'orchestrateur avait
    annoncé disparaîtrait de l'écran au premier relevé — d'où la reprise dans la
    description, en toutes lettres et comme une proposition.
    """
    entree = _build_task_description(_tache(etapes=("Lister les entités",)), [])

    assert "Lister les entités" in entree
    assert "proposition" in entree
    # Sans ossature, pas de bloc vide qui promettrait une liste absente.
    assert "proposition" not in _build_task_description(_tache(), [])


# ------------------------------- ② La réconciliation : supplanter, puis ne plus jamais reculer


def test_avant_tout_releve_la_checklist_est_celle_du_plan():
    """C'est ce qui rend la tâche lisible **avant** qu'elle démarre."""
    suivi = SuiviChecklist(["Lister les entités", "Écrire la migration"])

    assert not suivi.vide
    assert [(e.libelle, e.etat) for e in suivi.etapes()] == [
        ("Lister les entités", ETAPE_A_FAIRE),
        ("Écrire la migration", ETAPE_A_FAIRE),
    ]


def test_le_premier_releve_supplante_l_ossature_au_lieu_de_s_y_apparier():
    """Apparier serait un pari sur la formulation du modèle — et un pari perdu
    **double** la checklist d'étapes qui ne se cocheront jamais.

    Supplanter est déterministe, et gratuit : au premier relevé, l'ossature est
    tout entière « à faire », donc rien d'acquis n'est perdu.
    """
    suivi = SuiviChecklist(["Écrire la migration"])

    etapes = suivi.rapporte(_releve(("Rédiger la migration SQL", ETAPE_EN_COURS)))

    assert etapes is not None
    assert [e.libelle for e in etapes] == ["Rédiger la migration SQL"]


def test_les_releves_suivants_fusionnent_au_lieu_de_remplacer():
    """Le remplacement est un privilège du **premier** relevé, et d'aucun autre."""
    suivi = SuiviChecklist(["Ossature"])
    suivi.rapporte(_releve(("Lire l'existant", ETAPE_FAITE)))

    suivi.rapporte(_releve(("Écrire le code", ETAPE_EN_COURS)))

    assert [(e.libelle, e.etat) for e in suivi.etapes()] == [
        ("Écrire le code", ETAPE_EN_COURS),
        ("Lire l'existant", ETAPE_FAITE),
    ]


@pytest.mark.parametrize(
    ("acquis", "releve"),
    [
        (ETAPE_FAITE, ETAPE_EN_COURS),
        (ETAPE_FAITE, ETAPE_A_FAIRE),
        (ETAPE_EN_COURS, ETAPE_A_FAIRE),
    ],
)
def test_un_etat_ne_redescend_jamais(acquis, releve):
    """Le numérateur de l'avancement est **monotone** : c'est le critère du ticket.

    Un agent qui rappelle sa liste en la retapant peut très bien rétrograder une
    ligne ; ce qui a été vu acquis reste acquis.
    """
    suivi = SuiviChecklist()
    suivi.rapporte(_releve(("Écrire le code", acquis)))

    suivi.rapporte(_releve(("Écrire le code", releve)))

    assert suivi.etapes()[0].etat == acquis


def test_une_etape_qu_un_releve_oublie_garde_sa_place_et_son_etat():
    """Ce qu'un relevé oublie n'est pas ce qu'il retire.

    Un agent qui recompose sa liste en cours de route en perd volontiers une
    ligne ; la faire disparaître ferait *reculer* le dénominateur **et** le
    numérateur d'un coup.
    """
    suivi = SuiviChecklist()
    suivi.rapporte(_releve(("Lire l'existant", ETAPE_FAITE), ("Écrire le code", ETAPE_A_FAIRE)))

    suivi.rapporte(_releve(("Écrire le code", ETAPE_EN_COURS)))

    assert [(e.libelle, e.etat) for e in suivi.etapes()] == [
        ("Écrire le code", ETAPE_EN_COURS),
        ("Lire l'existant", ETAPE_FAITE),
    ]


def test_le_denominateur_peut_grandir():
    """Le prix d'une checklist juste — payé à l'écran (une case par étape), pas ici.

    Brider ce que l'agent a le droit de découvrir aurait rendu la checklist
    fausse pour protéger une jauge.
    """
    suivi = SuiviChecklist(["Écrire le code"])
    suivi.rapporte(_releve(("Écrire le code", ETAPE_FAITE)))

    suivi.rapporte(
        _releve(("Écrire le code", ETAPE_FAITE), ("Corriger le test rouge", ETAPE_EN_COURS))
    )

    assert len(suivi.etapes()) == 2
    assert suivi.etapes()[0].etat == ETAPE_FAITE


def test_deux_libelles_qui_ne_different_que_par_la_casse_sont_la_meme_etape():
    """Un agent ne retape pas ses libellés à la virgule près.

    La normalisation reste **minimale** — espaces et casse : deux formulations
    réellement différentes restent deux étapes, et c'est justement pour ça que
    l'ossature est supplantée plutôt qu'appariée.
    """
    suivi = SuiviChecklist()
    suivi.rapporte(_releve(("Écrire   le code", ETAPE_A_FAIRE)))

    suivi.rapporte(_releve(("écrire le code", ETAPE_FAITE)))

    assert len(suivi.etapes()) == 1
    # Le libellé **déjà connu** est gardé : réécrire ferait scintiller la ligne
    # d'un relevé à l'autre sans rien changer au fond.
    assert suivi.etapes()[0].libelle == "Écrire le code"
    assert suivi.etapes()[0].etat == ETAPE_FAITE


def test_un_etat_inconnu_passe_mais_ne_defait_aucun_acquis():
    """« Rien ne se refuse » et « rien ne recule » tiennent ensemble.

    Sur une étape neuve, l'état inconnu passe tel quel (le front le ramènera à
    « à faire ») ; sur une étape déjà en cours ou faite, on ne saurait pas dire
    s'il avance ou recule — donc on garde l'acquis.
    """
    neuve = SuiviChecklist()
    neuve.rapporte(_releve(("Déployer", "teleporte")))
    assert neuve.etapes()[0].etat == "teleporte"

    acquise = SuiviChecklist()
    acquise.rapporte(_releve(("Déployer", ETAPE_FAITE)))
    acquise.rapporte(_releve(("Déployer", "teleporte")))
    assert acquise.etapes()[0].etat == ETAPE_FAITE


@pytest.mark.parametrize(
    "relevees",
    [[], _releve(("", ETAPE_FAITE)), _releve(("   ", ETAPE_FAITE))],
)
def test_un_releve_qui_n_apprend_rien_laisse_la_checklist_intacte(relevees):
    """Un agent qui n'a rien dit n'est pas un agent qui annonce n'avoir plus rien
    à faire — l'ossature comprise reste en place."""
    suivi = SuiviChecklist(["Écrire le code"])

    assert suivi.rapporte(relevees) is None
    assert [e.libelle for e in suivi.etapes()] == ["Écrire le code"]


def test_rapporte_rend_none_quand_rien_n_a_change():
    """Un agent rappelle volontiers sa liste à l'identique.

    Republier coûterait une ligne de journal, un événement de bus et un rendu
    pour rien — d'où le `None`, que l'appelant lit comme « ne consigne pas ».
    """
    suivi = SuiviChecklist()
    assert suivi.rapporte(_releve(("Écrire le code", ETAPE_EN_COURS))) is not None

    assert suivi.rapporte(_releve(("Écrire le code", ETAPE_EN_COURS))) is None


def test_un_premier_releve_identique_a_l_ossature_ne_republie_pas_non_plus():
    """Le cas limite du supplantement : remplacer par le même contenu n'est pas
    un changement, et rien ne part."""
    suivi = SuiviChecklist(["Écrire le code"])

    assert suivi.rapporte(_releve(("Écrire le code", ETAPE_A_FAIRE))) is None


def test_une_ossature_vide_se_remplace_aussi_bien_qu_une_pleine():
    """C'est le drapeau, pas le contenu, qui dit si le prochain relevé supplante.

    Sans lui, une tâche sans ossature ferait du premier relevé une *fusion* avec
    un dictionnaire vide — même résultat aujourd'hui, mais pour une raison qui
    cesserait de tenir le jour où la fusion changerait.
    """
    suivi = SuiviChecklist()
    assert suivi.vide

    assert suivi.rapporte(_releve(("Écrire le code", ETAPE_FAITE))) is not None
    assert [e.etat for e in suivi.etapes()] == [ETAPE_FAITE]


def test_les_etapes_inachevees_sont_tout_ce_qui_n_est_pas_fait():
    """La quatrième règle (#944) : ce qui n'est pas acquis se lit aussi.

    Tout ce qui n'est pas « faite » en est — y compris un **état inconnu**, que
    « rien ne se refuse » laisse passer sans autoriser pour autant à le lire
    comme un acquis.
    """
    suivi = SuiviChecklist()
    suivi.rapporte(
        _releve(
            ("Lire le schéma", ETAPE_FAITE),
            ("Écrire les routes", ETAPE_EN_COURS),
            ("Tester", ETAPE_A_FAIRE),
            ("Documenter", "skipped"),
        )
    )

    assert [e.libelle for e in suivi.inachevees()] == [
        "Écrire les routes",
        "Tester",
        "Documenter",
    ]


def test_une_checklist_entierement_faite_n_a_aucune_etape_inachevee():
    suivi = SuiviChecklist()
    suivi.rapporte(_releve(("Écrire le code", ETAPE_FAITE)))

    assert suivi.inachevees() == []


def test_une_ossature_jamais_relevee_est_entierement_inachevee():
    """Un agent qui n'a rien dit laisse l'ossature du plan telle quelle, donc « à
    faire » de bout en bout : l'écart est alors la checklist entière."""
    suivi = SuiviChecklist(["Lire l'existant", "Écrire le code"])

    assert len(suivi.inachevees()) == 2


# ---------------- ③ Le verbe : un contrat de Maestro, que n'importe quel fournisseur sert


def test_le_verbe_porte_le_nom_sous_lequel_une_politique_le_designe():
    """Le nom complet est un **contrat** (docs/04 §1.4ter), comme celui des quatre
    autres verbes du serveur `maestro` : une politique de permissions (#110) le
    cite pour l'autoriser ou le refuser, et un renommage de `NOM_OUTIL` qui
    emporterait `OUTIL_CHECKLIST` en silence la ferait désigner un outil qui
    n'existe plus."""
    assert checklist_mod.OUTIL_CHECKLIST == f"mcp__{NOM_SERVEUR}__{checklist_mod.NOM_OUTIL}"
    assert checklist_mod.OUTIL_CHECKLIST == "mcp__maestro__tenir_checklist"


def test_appeler_le_verbe_alimente_on_etapes_avec_l_etat_complet():
    """Le premier critère de #1291, et le canal de #489 inchangé : l'état
    **complet** de la liste, dans l'ordre, traduit dans les états du contrat —
    `SuiviChecklist` en décide ensuite, comme avant."""
    vus: list[list[EtapeTache]] = []

    servi = _appelle_verbe(
        vus.append,
        _entree_verbe(
            ("Lire l'existant", ETAPE_FAITE),
            ("Écrire le code", ETAPE_EN_COURS),
            ("Lancer les tests", ETAPE_A_FAIRE),
        ),
    )

    (releve,) = vus
    assert [(e.libelle, e.etat) for e in releve] == [
        ("Lire l'existant", ETAPE_FAITE),
        ("Écrire le code", ETAPE_EN_COURS),
        ("Lancer les tests", ETAPE_A_FAIRE),
    ]
    # L'accusé dit où en est la liste, et que **personne ne répondra** : sans
    # cette phrase, un agent peut attendre un tour de plus une réponse qui ne
    # viendra jamais (même raison qu'en #719).
    assert servi == checklist_mod.CHECKLIST_RELEVEE.format(faites=1, total=3)
    assert "Personne ne va te répondre" in servi


def test_l_etat_se_lit_sans_egard_a_la_casse_ni_aux_blancs():
    """La forme se tolère, le vocabulaire non : « Faite » est « faite »."""
    vus: list[list[EtapeTache]] = []

    _appelle_verbe(vus.append, {"etapes": [{"libelle": "  Écrire   le code ", "etat": " Faite "}]})

    assert [(e.libelle, e.etat) for e in vus[0]] == [("Écrire le code", ETAPE_FAITE)]


def test_une_etape_sans_etat_est_a_faire():
    """C'est l'état qu'une étape a quand on la pose (`EtapeTache`) — pas un défaut
    inventé ici."""
    vus: list[list[EtapeTache]] = []

    _appelle_verbe(vus.append, {"etapes": [{"libelle": "Écrire le code"}]})

    assert vus[0][0].etat == ETAPE_A_FAIRE


@pytest.mark.parametrize(
    ("entree", "faute"),
    [
        (None, "n'est pas un objet"),
        ({}, "« etapes » manque"),
        ({"etapes": "Écrire le code"}, "« etapes » n'est pas une liste"),
        ({"etapes": []}, "« etapes » est vide"),
        ({"etapes": [None]}, "l'étape 1 n'est pas un objet"),
        ({"etapes": [{"etat": ETAPE_FAITE}]}, "l'étape 1 n'a pas de libellé"),
        (
            {"etapes": [{"libelle": "A", "etat": "faite"}, {"libelle": "B", "etat": "cancelled"}]},
            "l'étape 2 (« B ») a l'état « cancelled »",
        ),
        ({"etapes": [{"libelle": "A", "etat": 3}]}, "l'étape 1 (« A ») a l'état « 3 »"),
    ],
)
def test_une_entree_invalide_est_dite_a_l_agent_sans_rien_relever(entree, faute):
    """Une entrée invalide le dit à l'agent **sans tuer la tâche** (critère 1).

    Rien n'est relevé, pas même les lignes lisibles d'une liste fautive : une
    checklist à moitié relevée ferait dire à l'écran ce que l'agent n'a pas dit,
    et le rappel qu'on lui demande donne de toute façon la liste complète. La
    faute est **nommée**, rang compris — un « entrée invalide » sans plus
    l'enverrait deviner.
    """
    vus: list[list[EtapeTache]] = []

    servi = _appelle_verbe(vus.append, entree)

    assert vus == []
    assert servi.startswith("Checklist NON relevée")
    assert faute in servi
    # Et ce qu'il faut écrire à la place : les trois états admis, en toutes lettres.
    for etat in (ETAPE_A_FAIRE, ETAPE_EN_COURS, ETAPE_FAITE):
        assert f"« {etat} »" in servi


def test_un_canal_en_erreur_est_dit_a_l_agent_et_ne_tue_rien():
    """Le callback a levé : on le **dit** — l'agent croirait sinon sa liste à jour à
    l'extérieur — et l'exception ne remonte pas, elle tuerait la tâche à l'instant
    précis où l'agent rend compte de son avancement."""

    def explose(_etapes):
        raise RuntimeError("journal injoignable")

    servi = _appelle_verbe(explose, _entree_verbe(("Écrire le code", ETAPE_FAITE)))

    assert servi.startswith("Checklist NON relevée")
    assert "journal injoignable" in servi
    assert "compte-rendu" in servi


def test_le_verbe_n_est_monte_que_si_un_canal_lui_est_cable():
    """Règle du porte-outils (#718) : sans `on_etapes`, personne n'est au bout du
    fil, et servir un verbe qui n'aboutit nulle part serait pire que ne pas le
    servir."""
    assert _outils_maestro() == []
    assert [outil.name for outil in _outils_maestro(on_etapes=lambda _etapes: None)] == [
        checklist_mod.NOM_OUTIL
    ]


# -------------- ④ Le fournisseur : il sert le verbe, et ne lit plus aucun outil du CLI


class _BlocTexte:
    def __init__(self, text):
        self.text = text


class _BlocOutil:
    def __init__(self, name, entree):
        self.name = name
        self.input = entree


class _MessageAssistant:
    def __init__(self, content):
        self.content = content


@pytest.fixture()
def flux_sdk(monkeypatch):
    """Substitue les types du SDK par des doubles — même harnais que test_providers."""
    monkeypatch.setattr(claude_mod, "AssistantMessage", _MessageAssistant)
    monkeypatch.setattr(claude_mod, "TextBlock", _BlocTexte)
    monkeypatch.setattr(claude_mod, "ToolUseBlock", _BlocOutil)


#: Les outils de liste de travail qu'a portés le CLI Claude : `TodoWrite` jusqu'au
#: SDK 0.2.159, `TaskCreate`/`TaskUpdate` depuis. Ils ne servent ici qu'à prouver
#: qu'**aucun** n'est plus lu — la prochaine version du CLI en nommera un autre, et
#: c'est précisément pourquoi la checklist ne dépend plus d'aucun (docs/44).
OUTILS_DE_LISTE_DU_CLI = ("TodoWrite", "TaskCreate", "TaskUpdate")


def _run_agent(provider: ClaudeProvider, workspace: Path, **canaux) -> str:
    """`run_agent` sur le chemin one-shot, sans serveur déclaré — celui d'un run ordinaire."""
    return asyncio.run(
        provider.run_agent(
            "Fais la tâche.",
            model="claude-sonnet-5",
            workspace=workspace,
            tools=DEFAULT_TOOLS,
            **canaux,
        )
    )


def _flux_qui_appelle(monkeypatch, vu: dict[str, object], *blocs) -> None:
    """Un `query` factice : relève les options de la session, puis rend `blocs`."""

    async def fake_query(*, prompt, options):
        vu["mcp_servers"] = dict(options.mcp_servers)
        vu["tools"] = list(options.tools)
        yield _MessageAssistant(list(blocs))

    monkeypatch.setattr(claude_mod, "query", fake_query)


@pytest.mark.parametrize("outil", OUTILS_DE_LISTE_DU_CLI)
def test_un_outil_de_liste_du_cli_ne_coche_rien(flux_sdk, monkeypatch, tmp_path, outil):
    """Le défaut de #1291, et sa garde : **plus aucun outil interne du CLI** n'est
    lu comme source de la checklist.

    Le 2026-09-22, le CLI a remplacé `TodoWrite` par `TaskCreate`/`TaskUpdate`,
    et toutes les checklists sont restées à 0/N sans que rien ne rougisse. Lire le
    nouvel outil aurait remplacé une dépendance par une autre ; le flux peut donc
    porter n'importe lequel des trois, avec une entrée qui ressemble à une liste,
    et rien ne doit être coché.
    """
    vus: list[list[EtapeTache]] = []
    vu: dict[str, object] = {}
    entree = _todos(("Écrire le code", "completed")) | {
        "subject": "Écrire le code",
        "status": "completed",
    }
    _flux_qui_appelle(monkeypatch, vu, _BlocOutil(outil, entree), _BlocTexte("Livré."))

    assert _run_agent(ClaudeProvider(Credentials()), tmp_path, on_etapes=vus.append) == "Livré."

    assert vus == []


def test_le_serveur_maestro_sert_le_verbe_des_qu_un_canal_l_attend(
    flux_sdk, monkeypatch, tmp_path
):
    """Le verbe passe par le serveur in-process `maestro`, comme `consigner_decision`.

    Avec `on_etapes`, le serveur est monté même quand l'agent ne déclare aucun
    serveur — c'est le cas de presque tous les runs. Sans aucun canal, rien n'est
    monté : un serveur vide serait une surface qui promet sans servir (#718).
    """
    vu: dict[str, object] = {}
    _flux_qui_appelle(monkeypatch, vu, _BlocTexte("Livré."))
    provider = ClaudeProvider(Credentials())

    _run_agent(provider, tmp_path, on_etapes=lambda _etapes: None)
    assert list(vu["mcp_servers"]) == [NOM_SERVEUR]

    _run_agent(provider, tmp_path)
    assert vu["mcp_servers"] == {}


def test_un_appel_d_outil_reste_un_geste_compte_au_grand_livre(flux_sdk):
    """Ne plus lire un outil n'est pas le taire : `outils` compte toujours ce que
    l'agent a employé, verbe de checklist compris (`StepUsage.outils`)."""
    outils: list[str] = []

    claude_mod._absorbe(
        _MessageAssistant([_BlocOutil(checklist_mod.OUTIL_CHECKLIST, _entree_verbe())]),
        [],
        outils,
    )

    assert outils == [checklist_mod.OUTIL_CHECKLIST]


def test_aucun_outil_de_liste_du_cli_n_est_confie_a_l_agent():
    """L'agent n'a qu'une liste à tenir, celle de Maestro.

    Lui confier aussi celle du CLI le ferait tenir deux listes — dont une que
    personne ne lit —, et la seconde d'un outil que la prochaine version du CLI
    renommera. Les outils confiés sont ceux qui **agissent** : lire, écrire,
    explorer, exécuter.
    """
    assert not set(OUTILS_DE_LISTE_DU_CLI) & set(DEFAULT_TOOLS)


def test_le_socle_des_playbooks_nomme_le_verbe():
    """Le cadre d'exécution outillée dit à l'agent **par où** tenir sa checklist :
    le verbe de Maestro, et plus l'outil d'un CLI."""
    cadre = cadre_outille()

    assert checklist_mod.NOM_OUTIL in cadre
    assert not any(outil in cadre for outil in OUTILS_DE_LISTE_DU_CLI)


# ------------------- ⑤ Le moteur : l'ossature d'abord, puis ce que l'agent en fait


def test_l_ossature_part_avant_que_l_agent_n_ait_rien_dit():
    """C'est ce qui donne à lire la tâche pendant qu'elle démarre.

    La ligne est une **annexe** `<tache>:detail` (#246) : la tâche ne change pas
    de colonne au passage, un avancement *dans* une tâche n'étant pas une tâche
    qui avance.
    """
    provider = FournisseurChecklist()
    journal = _joue(_executeur(provider), _tache(etapes=("Lire l'existant", "Écrire le code")))

    assert _details(journal)[0] == [
        ("Lire l'existant", ETAPE_A_FAIRE),
        ("Écrire le code", ETAPE_A_FAIRE),
    ]
    # L'annexe est rattachée à la tâche, et ne porte aucun statut de tâche.
    (annexe,) = [r for r in journal.records if r.etape.endswith(SUFFIXE_ETAPE_DETAIL)]
    assert annexe.etape == f"api-crud{SUFFIXE_ETAPE_DETAIL}"
    assert annexe.statut == ""


def test_une_tache_sans_ossature_ni_releve_ne_consigne_aucune_checklist():
    """Règle de #246 : pas de checklist vide, pas de bloc qui promette un contenu
    absent. Une tâche reste exactement ce qu'elle était sans ce lot."""
    journal = _joue(_executeur(FournisseurChecklist()), _tache())

    assert _details(journal) == []


def test_le_releve_de_l_agent_supplante_l_ossature_et_se_consigne():
    provider = FournisseurChecklist(
        [[_releve(("Lire le schéma", ETAPE_FAITE), ("Écrire les routes", ETAPE_EN_COURS))]]
    )

    journal = _joue(_executeur(provider), _tache(etapes=("Étape annoncée",)))

    assert _details(journal) == [
        [("Étape annoncée", ETAPE_A_FAIRE)],
        [("Lire le schéma", ETAPE_FAITE), ("Écrire les routes", ETAPE_EN_COURS)],
    ]


def test_un_releve_qui_ne_change_rien_ne_consigne_pas_une_seconde_ligne():
    """La contrepartie du `None` de `rapporte`, vue du journal."""
    releve = _releve(("Écrire les routes", ETAPE_EN_COURS))
    provider = FournisseurChecklist([[releve, list(releve)]])

    journal = _joue(_executeur(provider), _tache())

    assert _details(journal) == [[("Écrire les routes", ETAPE_EN_COURS)]]


def test_la_checklist_consignee_devient_un_evenement_de_detail():
    """Le chemin **existant** et pas un second transport : la ligne devient
    `tache.detail`, la projection la pose sur la carte, le panneau la rend."""
    provider = FournisseurChecklist([[_releve(("Écrire les routes", ETAPE_EN_COURS))]])
    journal = _joue(_executeur(provider), _tache())

    lignes = [r for r in journal.records if r.etape.endswith(SUFFIXE_ETAPE_DETAIL)]
    (event,) = evenements_depuis_step(lignes[-1].to_dict())

    assert event.type == EVENEMENT_TACHE_DETAIL
    assert event.tache_id == "api-crud"
    assert [(e.libelle, e.etat) for e in event.etapes] == [
        ("Écrire les routes", ETAPE_EN_COURS)
    ]
    # Poser une case à cocher ne dépense rien : rien n'entre au grand livre.
    assert event.cout_usd is None


def test_le_repli_texte_garde_l_ossature_sans_jamais_la_cocher():
    """Un appel texte n'a pas de liste de travail à tenir.

    C'est exact, et c'est **dit** : la tâche montre ce que le plan annonçait,
    personne n'ayant rapporté d'avancement.
    """
    provider = FournisseurChecklist()
    journal = _joue(
        _executeur(provider, outille=False), _tache(etapes=("Lire l'existant",))
    )

    assert provider.generate_calls  # le chemin texte a bien été pris
    assert _details(journal) == [[("Lire l'existant", ETAPE_A_FAIRE)]]


class FournisseurSansOutils(FournisseurChecklist):
    """Un fournisseur texte seul : il refuse l'exécution outillée, comme `openai_compat`."""

    name = "texte-seul"

    async def run_agent(self, prompt, **kw):
        raise UnsupportedCapability("pas d'exécution outillée ici")


def _lignes_checklist(journal: RunJournal) -> list[str]:
    """Ce que le journal dit de la checklist à la clôture — écart ou relevé impossible."""
    return [
        record.sortie
        for record in journal.records
        if record.etape.endswith(SUFFIXE_ETAPE_ACTIVITE)
        and record.sortie.startswith("Checklist")
    ]


def test_un_fournisseur_sans_outils_dit_qu_il_ne_pouvait_rien_cocher():
    """#1291, règle de #246 : la tâche **dit** qu'aucun relevé n'était possible.

    Un fournisseur texte seul ne sert aucun verbe à son agent, donc personne ne
    pouvait cocher l'ossature du plan. Dire « non cochée(s) par l'agent » lui
    ferait porter un manque qui n'est pas le sien, et prétendrait un relevé que la
    tâche ne pouvait pas tenir : la ligne dit ce qui s'est passé, et nomme le
    fournisseur, puisque c'est lui qui en décide.
    """
    provider = FournisseurSansOutils()

    journal = _joue(
        _executeur(provider), _tache(etapes=("Lire l'existant", "Écrire le code"))
    )

    assert provider.generate_calls  # le repli texte a bien été pris
    assert _lignes_checklist(journal) == [phrase_checklist_sans_releve("texte-seul", 2)]
    (ligne,) = _lignes_checklist(journal)
    assert "texte-seul" in ligne
    assert "non cochée(s) par l'agent" not in ligne
    # L'ossature reste ce que le plan annonçait : rien n'est coché à sa place.
    assert _details(journal) == [
        [("Lire l'existant", ETAPE_A_FAIRE), ("Écrire le code", ETAPE_A_FAIRE)]
    ]


def test_un_fournisseur_sans_outils_ne_dit_rien_d_une_tache_sans_checklist():
    """Pas de checklist, rien à dire : la règle de #246 vaut dans les deux sens."""
    journal = _joue(_executeur(FournisseurSansOutils()), _tache())

    assert _lignes_checklist(journal) == []


def test_a_travers_une_relance_l_avancement_acquis_ne_recule_pas():
    """Le suivi vit à l'échelle de l'**exécution**, jamais de la tentative.

    Un agent relancé repart de zéro et rapporte sa liste à « à faire » ; le
    remettre à neuf ferait reculer l'avancement à l'instant précis où l'on veut
    savoir ce qui était déjà acquis.
    """
    provider = FournisseurChecklist(
        [
            [_releve(("Lire le schéma", ETAPE_FAITE), ("Écrire les routes", ETAPE_EN_COURS))],
            [_releve(("Lire le schéma", ETAPE_A_FAIRE), ("Écrire les routes", ETAPE_FAITE))],
        ],
        echecs=1,
    )
    executeur = _executeur(
        provider, relance=PolitiqueRelance(max_tentatives=2, backoff_s=0.0)
    )

    journal = _joue(executeur, _tache(etapes=("Étape annoncée",)))

    assert provider.tentatives == 2
    assert _details(journal)[-1] == [
        ("Lire le schéma", ETAPE_FAITE),
        ("Écrire les routes", ETAPE_FAITE),
    ]


def test_l_ossature_n_est_posee_qu_une_fois_malgre_la_relance():
    """Elle part **avant** la boucle de tentative : la reposer à chaque relance
    ferait une ligne de journal et un événement de bus par aléa fournisseur."""
    provider = FournisseurChecklist(echecs=1)
    executeur = _executeur(
        provider, relance=PolitiqueRelance(max_tentatives=2, backoff_s=0.0)
    )

    journal = _joue(executeur, _tache(etapes=("Étape annoncée",)))

    assert provider.tentatives == 2
    assert _details(journal) == [[("Étape annoncée", ETAPE_A_FAIRE)]]


# ---- ⑤bis L'écart entre le verdict et la checklist (#944, retex du 2026-09-11 G12)


def _ecarts(journal: RunJournal) -> list[str]:
    """Ce que le journal dit de l'écart — les lignes d'activité qui le nomment."""
    return [
        record.sortie
        for record in journal.records
        if record.etape.endswith(SUFFIXE_ETAPE_ACTIVITE)
        and record.sortie.startswith("Checklist incomplète")
    ]


def test_une_tache_terminee_sur_une_checklist_incomplete_dit_l_ecart():
    """Le défaut mesuré : « Terminée » à 14/15, sans que rien ne dise laquelle.

    L'agent conclut sans cocher sa dernière ligne, la tâche livre et se solde en
    succès. La cocher d'office lui ferait dire ce qu'il n'a pas dit, refuser le
    verdict ferait échouer une tâche qui a livré : reste à **dire** l'écart, et à
    nommer l'étape en cause.
    """
    provider = FournisseurChecklist(
        [[_releve(("Lire le schéma", ETAPE_FAITE), ("Écrire les routes", ETAPE_EN_COURS))]]
    )

    journal = _joue(_executeur(provider), _tache())

    (ecart,) = _ecarts(journal)
    assert "1 étape(s) sur 2 non cochée(s)" in ecart
    assert "Écrire les routes" in ecart
    # Ce que l'agent a coché n'est pas rappelé comme un manque.
    assert "Lire le schéma" not in ecart


def test_une_checklist_entierement_cochee_ne_dit_rien():
    """Le cas nominal ne gagne aucune ligne : ce lot est muet quand tout va bien."""
    provider = FournisseurChecklist(
        [[_releve(("Lire le schéma", ETAPE_FAITE), ("Écrire les routes", ETAPE_FAITE))]]
    )

    journal = _joue(_executeur(provider), _tache())

    assert _ecarts(journal) == []


def test_une_tache_sans_checklist_ne_dit_aucun_ecart():
    """Pas de checklist, pas d'écart : un agent qui ne tient pas de liste laisse la
    tâche exactement ce qu'elle était (règle de #246)."""
    journal = _joue(_executeur(FournisseurChecklist()), _tache())

    assert _ecarts(journal) == []


def test_une_tache_en_echec_ne_dit_aucun_ecart():
    """Sur un échec, une checklist inachevée n'apprend rien — c'est ce qu'un échec
    veut dire. L'écart n'est une information que sous un verdict de succès."""
    provider = FournisseurChecklist(
        [[_releve(("Écrire les routes", ETAPE_EN_COURS))]], echecs=1
    )

    journal = _joue(_executeur(provider), _tache())

    assert _ecarts(journal) == []


def test_l_ecart_est_dit_avant_que_la_tache_ne_s_annonce_terminee():
    """L'ordre au journal est la moitié du critère : la ligne d'écart précède
    l'étape terminale, pour qu'on ne lise jamais « Terminée » sans elle."""
    provider = FournisseurChecklist([[_releve(("Écrire les routes", ETAPE_EN_COURS))]])

    journal = _joue(_executeur(provider), _tache())

    etapes = [record.etape for record in journal.records]
    assert etapes.index(f"api-crud{SUFFIXE_ETAPE_ACTIVITE}") < etapes.index("api-crud")


def test_l_ecart_ne_cree_aucune_tache_fantome():
    """Aucun suffixe neuf (#944) : le pont traite ce qu'il ne reconnaît pas comme
    l'issue d'une tâche, et un `<tache>:ecart` ferait une tâche de plus dans les
    comptes — le défaut C8 du même retex, corrigé par #924."""
    provider = FournisseurChecklist([[_releve(("Écrire les routes", ETAPE_EN_COURS))]])
    journal = _joue(_executeur(provider), _tache())

    (ligne,) = [
        r for r in journal.records
        if r.etape.endswith(SUFFIXE_ETAPE_ACTIVITE) and r.sortie.startswith("Checklist")
    ]
    (event,) = evenements_depuis_step(ligne.to_dict())

    assert event.tache_id == "api-crud"
    assert event.type != EVENEMENT_TACHE_DETAIL  # une activité, pas un détail
    assert event.cout_usd is None  # rien au grand livre


def test_une_tache_cochee_par_le_verbe_finit_a_n_sur_n_sans_ecart():
    """Le comportement attendu de #1291 : un agent qui a tout fait finit à N/N.

    Posée au départ, recochée au fil de l'eau, soldée avant de conclure — par le
    verbe et lui seul. « relevé incomplet » ne reste alors que pour un écart réel
    (#1112), et ici il n'y en a pas.
    """
    provider = FournisseurQuiCoche(
        [
            _entree_verbe(("Lire le schéma", ETAPE_EN_COURS), ("Écrire les routes", ETAPE_A_FAIRE)),
            _entree_verbe(("Lire le schéma", ETAPE_FAITE), ("Écrire les routes", ETAPE_EN_COURS)),
            _entree_verbe(("Lire le schéma", ETAPE_FAITE), ("Écrire les routes", ETAPE_FAITE)),
        ]
    )

    journal = _joue(_executeur(provider), _tache(etapes=("Étape annoncée",)))

    assert _details(journal)[-1] == [
        ("Lire le schéma", ETAPE_FAITE),
        ("Écrire les routes", ETAPE_FAITE),
    ]
    assert _lignes_checklist(journal) == []


def test_la_phrase_de_l_ecart_a_une_seule_source():
    """Une formulation, tenue en un seul endroit (#1112).

    Le moteur la consigne à la clôture d'une vraie tâche par
    `phrase_ecart_checklist`, la même fonction que les tests lisent : recopiée
    ailleurs, elle finirait par diverger de ce qu'un vrai run envoie à l'écran.
    """
    provider = FournisseurChecklist([[_releve(("Écrire les routes", ETAPE_EN_COURS))]])
    journal = _joue(_executeur(provider), _tache())

    (ecart,) = _ecarts(journal)
    assert ecart == phrase_ecart_checklist(
        [EtapeTache(libelle="Écrire les routes", etat=ETAPE_EN_COURS)], 1
    )


# ------------------- ⑥ De bout en bout : ce que l'agent coche ressort par l'API


class FournisseurQuiCoche(FournisseurChecklist):
    """Exécutant outillé factice dont l'agent tient sa checklist **par le verbe**.

    Chaque appel passe par l'outil que le fournisseur Claude sert
    (`_outil_checklist`), appelé comme le SDK l'appelle : c'est le chemin réel de
    #1291, du verbe jusqu'à la carte, sans CLI ni quota.
    """

    name = "qui-coche"

    def __init__(self, appels: list[dict[str, object]]) -> None:
        super().__init__()
        self._appels = appels
        self.servis: list[str] = []

    async def run_agent(self, prompt, *, workspace, on_etapes=None, **kw):
        if on_etapes is not None:
            outil = _outil_checklist(on_etapes)
            for entree in self._appels:
                reponse = await outil.handler(entree)
                self.servis.append(reponse["content"][0]["text"])
        (Path(workspace) / "livrable.txt").write_text("fait", encoding="utf-8")
        return "OUTILLE"


def test_la_checklist_de_l_agent_ressort_sur_la_carte_servie_par_l_api():
    """Le chantier n'existe que si la case cochée arrive **jusqu'à l'écran**.

    Le chemin entier, sans raccourci : l'agent appelle le verbe (#1291),
    l'exécuteur réconcilie et consigne, le pont (#46) mue la ligne en
    `tache.detail`, la projection la pose sur la carte, et `GET /api/taches?run=`
    la rend. C'est le seul contrôle du fichier qui rougirait si un maillon du
    milieu se taisait — les autres l'éprouvent chacun de son côté.
    """
    provider = FournisseurQuiCoche(
        [
            _entree_verbe(("Lire le schéma", ETAPE_EN_COURS), ("Écrire les routes", ETAPE_A_FAIRE)),
            _entree_verbe(("Lire le schéma", ETAPE_FAITE), ("Écrire les routes", ETAPE_EN_COURS)),
        ]
    )
    journal = _joue(_executeur(provider), _tache(etapes=("Étape annoncée",)))
    assert all(servi.startswith("Checklist relevée") for servi in provider.servis)

    log = InMemoryEventLog()
    asyncio.run(
        log.consigner(
            Event(
                type=EVENEMENT_EXECUTION_STATUT,
                run_id=journal.run_id,
                statut=EXECUTION_EN_COURS,
                titre="Objectif",
            )
        )
    )
    for record in journal.records:
        for event in evenements_depuis_step(record.to_dict()):
            asyncio.run(log.consigner(event))

    with TestClient(
        create_app(bus=InMemoryEventBus(), state=ControlTowerState(), event_log=log)
    ) as client:
        # `tous` : la vue transverse, seule portée lisible sans dépôt de projets
        # déclaré — `?projet=` reste obligatoire, `?run=` s'y ajoute (#473).
        taches = client.get(
            "/api/taches", params={"projet": "tous", "run": journal.run_id}
        ).json()

    (carte,) = [tache for tache in taches if tache["id"] == "api-crud"]
    assert [(e["libelle"], e["etat"]) for e in carte["etapes"]] == [
        ("Lire le schéma", ETAPE_FAITE),
        ("Écrire les routes", ETAPE_EN_COURS),
    ]
    # La checklist n'a pas fait bouger la tâche de colonne : un avancement *dans*
    # une tâche n'est pas une tâche qui avance.
    assert carte["statut"] == "terminee"

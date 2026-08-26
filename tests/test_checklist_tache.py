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

③ **La lecture de l'outil** (`maestro.providers.checklist`) — le seul point du
   dispositif qui connaisse `TodoWrite`. Tolérante de bout en bout : le pire cas
   d'une lecture ratée doit être qu'il ne se passe **rien**.

④ **Le fournisseur** (`ClaudeProvider._absorbe`) — la checklist part du seul
   endroit où le flux du SDK est observé (#479), *en plus* du régulateur et
   jamais à sa place. Observer ne casse pas l'observé : ni une entrée illisible,
   ni un callback qui lève.

⑤ **Le moteur** (`LocalExecutor`) — l'ossature part **avant** la première
   tentative, les relevés se consignent par `consigne_detail`, et le suivi vit à
   l'échelle de l'**exécution** : à travers une relance, l'avancement acquis ne
   recule pas.

⑥ **L'API** — le chemin entier en un seul contrôle : ce que l'agent coche
   ressort sur la carte que `GET /api/taches?run=` sert. Les cinq volets
   ci-dessus éprouvent chacun leur maillon ; celui-ci est le seul qui rougirait
   si un maillon du **milieu** se taisait.

**Ni réseau, ni appel modèle, ni SDK** : le fournisseur est un double, le flux
SDK est simulé par des blocs factices substitués aux types du module (le même
harnais que `tests/test_providers.py`), et l'app du volet ⑥ est la vraie
(`create_app`) sur bus mémoire.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from maestro.agents import DEVELOPER_PROFILE, AgentRuntime
from maestro.agents.runtime import DEFAULT_TOOLS
from maestro.controltower import (
    ControlTowerState,
    Event,
    InMemoryEventBus,
    InMemoryEventLog,
    create_app,
)
from maestro.controltower.bridge import evenements_depuis_step
from maestro.controltower.events import EVENEMENT_TACHE_DETAIL
from maestro.controltower.state import EVENEMENT_EXECUTION_STATUT, EXECUTION_EN_COURS
from maestro.detail_tache import (
    ETAPE_A_FAIRE,
    ETAPE_EN_COURS,
    ETAPE_FAITE,
    SUFFIXE_ETAPE_DETAIL,
    EtapeTache,
    SuiviChecklist,
)
from maestro.engine.executor import LocalExecutor, _build_task_description
from maestro.engine.retry import PolitiqueRelance
from maestro.orchestrator.schema import Task
from maestro.providers import claude as claude_mod
from maestro.providers.base import ModelProvider
from maestro.providers.checklist import (
    OUTIL_CHECKLIST,
    est_checklist,
    etapes_depuis_outil,
)
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


def _todos(*paires: tuple[str, str]) -> dict[str, object]:
    """L'entrée d'un appel `TodoWrite`, telle que le SDK la fait passer."""
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
        mcp_serveurs=(), politique=None, on_refus=None, on_arbitrage=None,
        on_activite=None, on_etapes=None,
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


# ------------------------- ③ La lecture de l'outil : le pire cas est qu'il ne se passe rien


def test_seul_l_outil_de_liste_de_travail_porte_une_checklist():
    assert est_checklist(OUTIL_CHECKLIST)
    assert not est_checklist("Bash")
    assert not est_checklist("")


def test_les_avancements_de_l_outil_se_traduisent_dans_les_etats_du_contrat():
    etapes = etapes_depuis_outil(
        _todos(
            ("Lire l'existant", "completed"),
            ("Écrire le code", "in_progress"),
            ("Lancer les tests", "pending"),
        )
    )

    assert [(e.libelle, e.etat) for e in etapes] == [
        ("Lire l'existant", ETAPE_FAITE),
        ("Écrire le code", ETAPE_EN_COURS),
        ("Lancer les tests", ETAPE_A_FAIRE),
    ]


def test_un_avancement_hors_table_passe_tel_quel():
    """La table dit ce qu'on sait **traduire**, pas ce qu'on accepte de recevoir."""
    (etape,) = etapes_depuis_outil(_todos(("Déployer", "cancelled")))

    assert etape.etat == "cancelled"


def test_la_forme_en_cours_d_action_sert_de_repli_au_libelle():
    """Mieux vaut une ligne au gérondif qu'une ligne écartée faute d'énoncé."""
    (etape,) = etapes_depuis_outil(
        {"todos": [{"activeForm": "Rédaction de la migration", "status": "in_progress"}]}
    )

    assert etape.libelle == "Rédaction de la migration"


@pytest.mark.parametrize(
    "entree",
    [
        None,
        "todos",
        42,
        {},
        {"todos": None},
        {"todos": "Écrire le code"},
        {"todos": [None, 7, "texte"]},
        {"todos": [{"status": "completed"}]},
    ],
)
def test_une_entree_illisible_rend_une_liste_vide_sans_lever(entree):
    """Une entrée d'outil vient du modèle : tronquée, mal typée, ou porteuse de
    clés inconnues. Observer ne doit pas casser l'observé."""
    assert etapes_depuis_outil(entree) == []


def test_une_ligne_illisible_ne_fait_pas_perdre_les_autres():
    etapes = etapes_depuis_outil(
        {"todos": [{"content": "Écrire le code", "status": "pending"}, "cassé", {}]}
    )

    assert [e.libelle for e in etapes] == ["Écrire le code"]


# -------------------------- ④ Le fournisseur : la checklist part d'où le flux est observé


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


def _absorbe(bloc, on_etapes=None, outils=None):
    """Passe un bloc par le seul endroit où le flux du SDK est observé."""
    claude_mod._absorbe(
        _MessageAssistant([bloc]), [], outils if outils is not None else [], None, on_etapes
    )


def test_un_appel_de_liste_de_travail_publie_la_checklist(flux_sdk):
    """Il n'y avait ni protocole à inventer ni transport à ouvrir : seulement un
    appel d'outil qu'on jetait."""
    vus: list[list[EtapeTache]] = []

    _absorbe(_BlocOutil(OUTIL_CHECKLIST, _todos(("Écrire le code", "in_progress"))), vus.append)

    assert [(e.libelle, e.etat) for e in vus[0]] == [("Écrire le code", ETAPE_EN_COURS)]


def test_la_checklist_reste_un_appel_d_outil_comme_un_autre(flux_sdk):
    """`outils` continue de le compter : le grand livre voit ce que l'agent a employé."""
    outils: list[str] = []

    _absorbe(_BlocOutil(OUTIL_CHECKLIST, _todos(("Écrire le code", "pending"))), None, outils)

    assert outils == [OUTIL_CHECKLIST]


def test_un_autre_outil_ne_publie_aucune_checklist(flux_sdk):
    vus: list[list[EtapeTache]] = []

    _absorbe(_BlocOutil("Bash", {"command": "pytest"}), vus.append)

    assert vus == []


def test_une_entree_vide_ne_publie_rien(flux_sdk):
    """Un appel illisible dirait « l'agent n'a plus rien à faire » là où il ne dit
    rien du tout — et `SuiviChecklist` effacerait une checklist en place."""
    vus: list[list[EtapeTache]] = []

    _absorbe(_BlocOutil(OUTIL_CHECKLIST, {"todos": []}), vus.append)

    assert vus == []


def test_un_rappel_qui_leve_ne_casse_pas_l_execution_observee(flux_sdk):
    """Même règle que `on_refus` et que le régulateur d'activité.

    Et le flux **continue** : l'outil est relevé après coup, ce qui distingue
    « l'exception a été absorbée » de « le bloc n'a jamais été traité ».
    """
    outils: list[str] = []

    def explose(_etapes):
        raise RuntimeError("le consommateur a lâché")

    _absorbe(
        _BlocOutil(OUTIL_CHECKLIST, _todos(("Écrire le code", "pending"))), explose, outils
    )

    assert outils == [OUTIL_CHECKLIST]


def test_l_outil_de_liste_de_travail_est_confie_aux_roles_outilles():
    """Le seul outil de la liste qui n'agisse sur rien : il ne lit, n'écrit ni
    n'exécute — il **dit** où l'agent en est.

    Sans lui dans les outils confiés, le canal existe et personne ne le remplit :
    exactement le défaut que #489 est venu fermer.
    """
    assert OUTIL_CHECKLIST in DEFAULT_TOOLS


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


# ------------------- ⑥ De bout en bout : ce que l'agent coche ressort par l'API


def test_la_checklist_de_l_agent_ressort_sur_la_carte_servie_par_l_api():
    """Le chantier n'existe que si la case cochée arrive **jusqu'à l'écran**.

    Le chemin entier, sans raccourci : l'agent rapporte, l'exécuteur réconcilie et
    consigne, le pont (#46) mue la ligne en `tache.detail`, la projection la pose
    sur la carte, et `GET /api/taches?run=` la rend. C'est le seul contrôle du
    fichier qui rougirait si un maillon du milieu se taisait — les autres
    l'éprouvent chacun de son côté.
    """
    provider = FournisseurChecklist(
        [[_releve(("Lire le schéma", ETAPE_FAITE), ("Écrire les routes", ETAPE_EN_COURS))]]
    )
    journal = _joue(_executeur(provider), _tache(etapes=("Étape annoncée",)))

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

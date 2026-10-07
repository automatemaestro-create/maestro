"""L'outillage d'un projet neuf regarde le poste, et se revérifie quand le projet le permet (#1343).

Relevé par S9 sur la vraie stack : l'outillage d'un projet neuf recommandait `uv`,
`ruff`, `typst`, `pandoc` — absents du poste —, écrivait leurs commandes « à vérifier »
parce que le dossier était vide, et personne ne les revérifiait une fois le projet
construit. `AGENTS.md` prescrivait donc aux agents des commandes qui échouent, et
l'équipe construisait autrement.

Quatre étages, du moteur au banc :

① **ce que le poste répond se joue même sur un dossier vide** — une commande qui ne peut
   pas encore se jouer n'en appelle pas moins des programmes, et leur présence se
   vérifie : un outil absent rend la commande **échouée**, avec la sortie de la sonde,
   jamais écrite comme la marche à suivre (`maestro.outillage.verification`) ;
② **la recommandation regarde le poste** — chaque option dit les outils qu'elle
   demande, Maestro les sonde, une option qui repose sur un outil absent ne reste pas
   recommandée, et ce que le poste a répondu est dit au modèle
   (`maestro.outillage.questionnaire`, `maestro.controltower.outillage`) ;
③ **ce qui ne pouvait pas encore se jouer se joue dès que le projet le permet** — à la
   fin d'un run, les commandes que l'outillage n'avait pas pu vérifier sont rejouées
   sur le projet construit, et la pièce qui change est proposée dans le fil, portée par
   le récit de fin (`maestro.controltower.pieces`, `maestro.controltower.recit`) ;
④ le banc (S9, S10) tranche cette pièce avant de rejouer lui-même les commandes — dans
   `tests/test_scenarios.py`, avec les autres oracles des scénarios.

Les commandes sont jouées par un joueur doublé, sauf sous `@pytest.mark.commandes_jouees`
où un vrai bash répond.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from maestro.agents.playbook_du_code import registre
from maestro.controltower.chat import (
    DECISION_ECRIRE,
    DECISION_PASSER,
    ORIGINE_NOUVEAU,
    UTILISATEUR,
    ChatStore,
    MessageChat,
    PieceProposee,
    RepondeurChat,
    ReponseChat,
    ServiceChat,
    piece_en_attente,
)
from maestro.controltower.events import EVENEMENT_EXECUTION_STATUT, Event, InMemoryEventBus
from maestro.controltower.orchestration import AGENT_ORCHESTRATION, NOM_ORCHESTRATION
from maestro.controltower.outillage import (
    _PROMPT_COMPREHENSION,
    ComprehensionModele,
    ConducteurOutillage,
    ServiceOutillage,
)
from maestro.controltower.pieces import ServicePieces
from maestro.controltower.projets import ServiceProjets
from maestro.controltower.recit import ConteurDeFin
from maestro.controltower.state import EXECUTION_EN_COURS, EXECUTION_TERMINEE, ControlTowerState
from maestro.messaging import InMemoryMailbox
from maestro.outillage import (
    CHEMIN_MANIFESTE,
    Commande,
    Constats,
    generer_outillage,
    recommander,
)
from maestro.outillage.correction import (
    CorrectionPrise,
    adopter,
    corrections_en_texte,
    corrections_lues,
)
from maestro.outillage.modele import ORIGINE_PROPOSEE
from maestro.outillage.questionnaire import (
    Choix,
    Option,
    QuestionOutillage,
    au_poste,
    comprehension_depuis_texte,
    programmes_a_sonder,
    repose_sur_un_absent,
)
from maestro.outillage.verification import (
    A_VERIFIER,
    ECHOUEE,
    VERIFIEE,
    Verificateur,
    Verification,
    programmes,
    sonde,
)
from maestro.projets import ProjetStore
from maestro.projets.modele import Perimetre, Projet
from maestro.projets.reglages import ReglagesProjetsStore
from maestro.providers.base import ModelProvider
from maestro.sandbox import verification as execution

#: L'interpréteur réel, relevé **avant** que la garde du conftest ne le retire.
BASH = execution.interprete()
besoin_de_bash = pytest.mark.skipif(BASH is None, reason="aucun bash sur ce poste")

#: Un interpréteur factice : le joueur doublé ne le lance jamais.
FAUX_BASH = ("bash", "-c")

SOURCE_CHOIX = {
    "type": "choix",
    "projet_id": "prj-chorale",
    "reference": "nature=carnet de chants",
    "resume": "carnet de chants",
}
QUAND = "2026-09-27T12:00:00+00:00"

#: Ce que bash répond d'un programme qu'il ne trouve pas — la sortie gardée, jamais lue.
INTROUVABLE = execution.Execution(code=1, sortie="bash: type: uv: not found", duree_s=0.1)

#: La phrase de S9, qui fait naître le projet.
DEMANDE = (
    "Je veux tenir le carnet de chants de ma chorale : un fichier texte par chant, et un "
    "carnet assemblé automatiquement, avec le sommaire des titres."
)


@pytest.fixture(autouse=True)
def _maison(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """`Path.home()` sous `tmp_path` : `valider_racine` refuse `AppData` sous Windows (#221)."""
    maison = tmp_path / "maison"
    maison.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: maison))
    return maison


class _Joueur:
    """Un joueur doublé : un résultat par commande (0 par défaut), et ce qu'il a joué."""

    def __init__(self, resultats: Mapping[str, execution.Execution] | None = None) -> None:
        self._resultats = dict(resultats or {})
        self.joues: list[str] = []

    def __call__(
        self, commande: str, cwd: Path, *, interprete: Any, delai_s: float
    ) -> execution.Execution:
        self.joues.append(commande)
        return self._resultats.get(commande, execution.Execution(code=0, sortie="ok", duree_s=0.1))


def _neuf(*commandes: tuple[str, str]) -> Constats:
    """Les constats d'un projet neuf : des commandes de convention, qui vivront dans un fichier."""
    return Constats(
        commandes=tuple(
            Commande(usage=usage, commande=texte, chemin="pyproject.toml", origine="convention")
            for usage, texte in commandes
        )
    )


def _generer(racine: Path, constats: Constats, verificateur: Verificateur) -> Any:
    return generer_outillage(
        Projet(id="prj-chorale", nom="Chorale", racine=racine.as_posix()),
        constats,
        recommander(constats),
        source=SOURCE_CHOIX,
        horodatage=QUAND,
        verificateur=verificateur,
    )


def _ligne(texte: str, commande: str) -> str:
    return next(ligne for ligne in texte.splitlines() if f"`{commande}`" in ligne)


# --------------------------------------------------------------------------- #
# ① Ce que le poste répond se joue même sur un dossier vide                     #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("commande", "attendus"),
    [
        ("uv run pytest", ("uv",)),
        ("typst compile carnet.typ", ("typst",)),
        ("env CI=1 npx vitest run", ("npx",)),
        ("PYTHONPATH=. python -m pytest", ("python",)),
        ("npm ci && npm test", ("npm",)),
        ("bash scripts/tests.sh", ("bash",)),
        # Un fichier du projet, pas un outil du poste : il existera, ou pas, avec le projet.
        ("./gradlew test", ()),
        # Ce qu'une substitution exécute n'est pas dans le texte : rien ne se sonde.
        ("python $(which app)", ()),
    ],
)
def test_les_programmes_d_une_commande_sont_ceux_qu_elle_appelle(
    commande: str, attendus: tuple[str, ...]
) -> None:
    assert programmes(commande) == attendus


def test_un_projet_neuf_dont_l_outil_manque_au_poste_ecrit_sa_commande_echouee(
    tmp_path: Path,
) -> None:
    """Le défaut de #1343 : `uv` absent, ses commandes étaient écrites « à vérifier »."""
    racine = tmp_path / "maison" / "chorale"
    racine.mkdir(parents=True)
    joueur = _Joueur({sonde("uv"): INTROUVABLE})
    constats = _neuf(
        ("installer", "uv sync"),
        ("construire", "python assembler.py"),
        ("tester", "uv run pytest"),
    )

    preparation = _generer(racine, constats, Verificateur(joueur=joueur, interprete=FAUX_BASH))

    verdicts = {v.commande: v for v in preparation.rapport.verifications}
    for commande in ("uv sync", "uv run pytest"):
        assert verdicts[commande].etat == ECHOUEE
        assert verdicts[commande].code == 1
        assert verdicts[commande].sortie == "bash: type: uv: not found"
        assert verdicts[commande].raison.startswith("`uv` est introuvable sur ce poste")
    # L'outil est là : c'est le projet qui manque encore, et la commande attend.
    assert verdicts["python assembler.py"].etat == A_VERIFIER
    assert "aucun fichier en dehors de son outillage" in verdicts["python assembler.py"].raison
    # Rien du projet n'est joué — seulement ce que le poste dit de ses outils, une fois chacun.
    assert sorted(joueur.joues) == sorted({sonde("uv"), sonde("python")})
    agents = (racine / "AGENTS.md").read_text(encoding="utf-8")
    assert "⚠ **Échouée**" in _ligne(agents, "uv sync")
    assert "`uv` est introuvable sur ce poste" in _ligne(agents, "uv sync")
    assert "**À vérifier**" in _ligne(agents, "python assembler.py")


def test_une_commande_dont_le_fichier_manque_encore_dit_aussi_l_outil_absent(
    tmp_path: Path,
) -> None:
    """Le projet a ses fichiers, pas encore celui où la commande vivra : l'outil se sonde."""
    racine = tmp_path / "maison" / "chorale"
    (racine / "chants").mkdir(parents=True)
    (racine / "chants" / "fontaine.txt").write_text("À la claire fontaine\n", encoding="utf-8")
    joueur = _Joueur({sonde("typst"): INTROUVABLE})

    preparation = _generer(
        racine,
        _neuf(("construire", "typst compile carnet.typ"), ("tester", "python -m unittest")),
        Verificateur(joueur=joueur, interprete=FAUX_BASH),
    )

    verdicts = {v.commande: v for v in preparation.rapport.verifications}
    assert verdicts["typst compile carnet.typ"].etat == ECHOUEE
    assert verdicts["python -m unittest"].etat == A_VERIFIER
    assert verdicts["python -m unittest"].raison.startswith("`pyproject.toml` n'existe pas encore")


def test_un_acte_hors_du_projet_reste_a_une_personne_meme_sur_un_dossier_vide(
    tmp_path: Path,
) -> None:
    """La portée d'abord : ce qu'un agent n'aurait pas fait seul n'est ni joué ni sondé."""
    racine = tmp_path / "maison" / "chorale"
    racine.mkdir(parents=True)
    joueur = _Joueur()

    preparation = _generer(
        racine,
        _neuf(("installer", "winget install --id JohnMacFarlane.Pandoc")),
        Verificateur(joueur=joueur, interprete=FAUX_BASH),
    )

    (verdict,) = preparation.rapport.verifications
    assert verdict.etat == A_VERIFIER
    assert verdict.raison.startswith("pas jouée — commande hors de la portée")
    assert joueur.joues == []


@pytest.mark.commandes_jouees
@besoin_de_bash
def test_un_vrai_bash_dit_quel_outil_manque_au_poste(tmp_path: Path) -> None:
    """La mécanique réelle : `type` dans le bash des agents, dans une copie du projet."""
    racine = tmp_path / "maison" / "chorale"
    racine.mkdir(parents=True)
    absent = "maestro-outil-absent-1343"

    preparation = _generer(
        racine,
        _neuf(("construire", f"{absent} carnet.txt"), ("tester", "bash scripts/tests.sh")),
        Verificateur(),
    )

    verdicts = {v.commande: v for v in preparation.rapport.verifications}
    assert verdicts[f"{absent} carnet.txt"].etat == ECHOUEE, verdicts
    assert verdicts[f"{absent} carnet.txt"].code not in (None, 0)
    assert absent in verdicts[f"{absent} carnet.txt"].sortie
    assert verdicts["bash scripts/tests.sh"].etat == A_VERIFIER
    manifeste = json.loads((racine / CHEMIN_MANIFESTE).read_text(encoding="utf-8"))
    assert {v["etat"] for v in manifeste["verifications"]} == {ECHOUEE, A_VERIFIER}


@pytest.mark.commandes_jouees
@besoin_de_bash
def test_sonder_le_poste_rend_ce_que_bash_trouve_et_ce_qu_il_ne_trouve_pas() -> None:
    """Le verbe que le questionnaire consulte : présent, absent — et rien sur ce qu'il ignore."""
    absent = "maestro-outil-absent-1343"

    poste = Verificateur().sonder(("bash", absent, "bash"))

    assert poste == {"bash": True, absent: False}


def test_sans_bash_le_poste_ne_repond_rien() -> None:
    """Sous la garde du conftest : pas d'interpréteur, aucun fait du poste — jamais « absent »."""
    assert Verificateur().sonder(("uv",)) == {}


def test_une_commande_deja_verifiee_ne_se_sonde_pas(tmp_path: Path) -> None:
    """Un verdict joué est repris tel quel (#1161) : il n'y a rien à redemander au poste."""
    racine = tmp_path / "maison" / "chorale"
    racine.mkdir(parents=True)
    joueur = _Joueur()
    constats = _neuf(("tester", "python -m unittest"))
    connue = Verification(
        usage="tester",
        commande="python -m unittest",
        etat=VERIFIEE,
        raison="elle a rendu la main sans erreur",
    )

    verdicts = Verificateur(joueur=joueur, interprete=FAUX_BASH).verifier(
        racine,
        constats,
        recommander(constats),
        perimetre=Perimetre(),
        connues={connue.commande: connue},
    )

    assert verdicts == (connue,)
    assert joueur.joues == []


# --------------------------------------------------------------------------- #
# ② La recommandation regarde le poste                                          #
# --------------------------------------------------------------------------- #

#: Ce que le modèle propose, sans rien savoir du poste : `uv`, qui y est absent.
QUESTION_GESTIONNAIRE: dict[str, Any] = {
    "cle": "gestionnaire",
    "intitule": "Avec quoi installer ses dépendances ?",
    "options": [
        {"valeur": "uv", "libelle": "uv", "raison": "Rapide et reproductible.", "outils": ["uv"]},
        {
            "valeur": "pip",
            "libelle": "pip et un venv",
            "raison": "Ce que Python fournit.",
            "outils": ["python"],
        },
    ],
    "recommande": "uv",
    "pourquoi": "Le plus rapide pour un petit projet Python.",
}
COMPRIS_CHORALE: dict[str, Any] = {
    "message": "",
    "constats": [
        {"cle": "nature", "valeur": "Un carnet de chants", "parce_que": "vous l'avez dit"},
        {"cle": "langages", "valeur": "Python", "parce_que": "un script assemble le carnet"},
        {"cle": "tester", "valeur": "uv run pytest", "parce_que": "pytest, lancé par uv"},
    ],
    "questions": [QUESTION_GESTIONNAIRE],
}


#: Ce qu'il faut savoir d'un projet pour qu'une autre question que la question ouverte se pose.
NATURE = {"cle": "nature", "valeur": "Un carnet de chants", "parce_que": "vous l'avez dit"}


def _question(brute: Mapping[str, Any]) -> Any:
    return comprehension_depuis_texte(
        json.dumps({"constats": [NATURE], "questions": [brute]}), ()
    ).questions[0]


def test_une_option_dit_les_outils_qu_elle_demande_et_se_relit_a_l_identique() -> None:
    question = _question(QUESTION_GESTIONNAIRE)

    assert [o.outils for o in question.options] == [("uv",), ("python",)]
    assert QuestionOutillage.from_dict(question.to_dict()) == question
    # Une option d'avant #1343 garde sa forme exacte : rien n'est ajouté quand c'est vide.
    assert Option("a", "A", "r").to_dict() == {"valeur": "a", "libelle": "A", "raison": "r"}


def test_des_outils_qui_ne_sont_pas_des_programmes_sont_ecartes() -> None:
    brute = {
        **QUESTION_GESTIONNAIRE,
        "options": [
            {
                "valeur": "uv",
                "libelle": "uv",
                "raison": "…",
                "outils": ["uv", "uv", "rm -rf /", "../bin/x", 3, ""],
            }
        ],
    }

    assert _question(brute).options[0].outils == ("uv",)


def test_une_recommandation_qui_repose_sur_un_outil_absent_se_reporte() -> None:
    comprise = comprehension_depuis_texte(json.dumps(COMPRIS_CHORALE), ())

    question = au_poste(comprise, {"uv": False, "python": True}).questions[0]

    assert question.recommande == "pip"
    assert "`uv` n'est pas installé sur ce poste" in question.pourquoi
    assert "« pip et un venv »" in question.pourquoi
    uv, pip = question.options
    assert uv.absents == ("uv",)
    assert uv.raison == "Rapide et reproductible — `uv` n'est pas installé sur ce poste."
    assert pip.absents == () and pip.raison == "Ce que Python fournit."
    # Confronter deux fois ne dit pas deux fois.
    assert au_poste(au_poste(comprise, {"uv": False}), {"uv": False}).questions[0] == (
        au_poste(comprise, {"uv": False}).questions[0]
    )


def test_ce_que_le_poste_ignore_ne_change_rien_et_tout_absent_laisse_la_personne_choisir() -> (
    None
):
    comprise = comprehension_depuis_texte(json.dumps(COMPRIS_CHORALE), ())

    # Aucun fait du poste (pas de bash, sonde muette) : la compréhension telle quelle.
    assert au_poste(comprise, {}) == comprise
    assert au_poste(comprise, {"uv": True}) == comprise
    # Toutes les options manquent d'un outil : la recommandation reste, chacune le dit.
    question = au_poste(comprise, {"uv": False, "python": False}).questions[0]
    assert question.recommande == "uv"
    assert [o.absents for o in question.options] == [("uv",), ("python",)]


def test_une_option_de_commande_se_sonde_sur_son_texte_meme_sans_outils_nommes() -> None:
    brute = {
        "cle": "tester",
        "intitule": "Comment tester le carnet ?",
        "options": [
            {"valeur": "uv run pytest", "libelle": "pytest par uv", "raison": "…"},
            {"valeur": "python -m unittest", "libelle": "unittest", "raison": "…"},
        ],
        "recommande": "uv run pytest",
        "pourquoi": "…",
    }
    comprise = comprehension_depuis_texte(
        json.dumps({"constats": [NATURE], "questions": [brute]}), ()
    )

    assert programmes_a_sonder(comprise) == ("uv", "python")
    assert au_poste(comprise, {"uv": False}).questions[0].recommande == "python -m unittest"


def test_aucun_n_est_pas_un_programme_a_chercher_sur_le_poste() -> None:
    """Vu sur la vraie stack (passage `20260927-085810`) : l'option « aucun » d'une question de
    commande était sondée comme un programme, et se disait « `aucun` n'est pas installé »."""
    brute = {
        "cle": "formater",
        "intitule": "Faut-il mettre le script en forme ?",
        "options": [
            {"valeur": "aucun", "libelle": "Non", "raison": "Rien à installer."},
            {"valeur": "black .", "libelle": "black", "raison": "…", "outils": ["black"]},
            {"valeur": "python -m tabnanny .", "libelle": "tabnanny", "raison": "…"},
        ],
        "recommande": "aucun",
        "pourquoi": "Un script court se tient à la main.",
    }
    constats = [NATURE, {"cle": "types", "valeur": "aucun", "parce_que": "pas de types"}]
    comprise = comprehension_depuis_texte(
        json.dumps({"constats": constats, "questions": [brute]}), ()
    )

    assert programmes_a_sonder(comprise) == ("black", "python")
    question = au_poste(comprise, {"aucun": False, "black": False, "python": True}).questions[0]
    assert question.recommande == "aucun"
    assert question.options[0].absents == () and question.options[0].raison == "Rien à installer."
    assert not repose_sur_un_absent(comprise, ["aucun"])


def test_une_commande_deduite_sur_un_outil_absent_demande_au_modele_de_se_reprendre() -> None:
    comprise = comprehension_depuis_texte(json.dumps(COMPRIS_CHORALE), ())

    assert repose_sur_un_absent(comprise, ["uv"])
    assert not repose_sur_un_absent(comprise, ["python"])  # l'option n'est pas recommandée
    assert not repose_sur_un_absent(comprise, [])


class _Modele(ModelProvider):
    """Le modèle de la compréhension : il rend ce qu'on lui dicte, dans l'ordre, et retient."""

    name = "modele-double"

    def __init__(self, *reponses: Mapping[str, Any]) -> None:
        self._reponses = [json.dumps(r, ensure_ascii=False) for r in reponses]
        self.prompts: list[str] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(
        self, prompt: str, *, model: str, system_prompt: str | None = None, effort: Any = None
    ) -> str:
        self.prompts.append(prompt)
        return self._reponses[min(len(self.prompts), len(self._reponses)) - 1]


def _conducteur(modele: _Modele, joueur: _Joueur) -> ConducteurOutillage:
    return ConducteurOutillage(
        ComprehensionModele(modele),
        verificateur=Verificateur(joueur=joueur, interprete=FAUX_BASH),
    )


def _dit(contenu: str) -> MessageChat:
    return MessageChat(agent="orchestrateur", auteur=UTILISATEUR, contenu=contenu)


def test_le_modele_apprend_ce_que_le_poste_a_repondu_et_se_reprend() -> None:
    """Le défaut de #1343 : `uv` recommandé sur un poste qui ne l'a pas."""
    repris = {
        **COMPRIS_CHORALE,
        "constats": [
            *COMPRIS_CHORALE["constats"][:2],
            {"cle": "tester", "valeur": "python -m unittest", "parce_que": "sans uv"},
        ],
        "questions": [{**QUESTION_GESTIONNAIRE, "recommande": "pip", "pourquoi": "uv manque."}],
    }
    modele = _Modele(COMPRIS_CHORALE, repris)
    joueur = _Joueur({sonde("uv"): INTROUVABLE})

    reponse = asyncio.run(_conducteur(modele, joueur).ouvrir([_dit(DEMANDE)]))

    # Saisi une seconde fois, avec ce que le poste a répondu — et c'est lui qui se reprend.
    premier, second = modele.prompts
    assert "Ce que le poste a répondu" not in premier
    assert "## Ce que le poste a répondu" in second
    assert "- `uv` : introuvable sur ce poste" in second
    assert "- `python` : installé" in second
    assert reponse.question is not None
    assert reponse.question.recommande == "pip" and reponse.question.pourquoi == "uv manque."
    assert reponse.question.options[0].absents == ("uv",)
    assert {c.cle: c.valeur for c in reponse.comprehension}["tester"] == "python -m unittest"
    assert set(joueur.joues) == {sonde("uv"), sonde("python")}


def test_un_modele_qui_s_obstine_ne_fait_pas_recommander_un_outil_absent() -> None:
    """Le code vérifie ce que le modèle propose : saisi deux fois, la recommandation se reporte."""
    modele = _Modele(COMPRIS_CHORALE)
    joueur = _Joueur({sonde("uv"): INTROUVABLE})

    reponse = asyncio.run(_conducteur(modele, joueur).ouvrir([_dit(DEMANDE)]))

    assert len(modele.prompts) == 2
    assert reponse.question is not None and reponse.question.recommande == "pip"
    assert "`uv` n'est pas installé sur ce poste" in reponse.question.pourquoi


def test_un_poste_qui_a_tout_ne_coute_aucun_appel_de_plus() -> None:
    modele = _Modele(COMPRIS_CHORALE)
    joueur = _Joueur()

    reponse = asyncio.run(_conducteur(modele, joueur).ouvrir([_dit(DEMANDE)]))

    assert len(modele.prompts) == 1
    assert reponse.question is not None and reponse.question.recommande == "uv"


def test_ce_que_le_fil_a_deja_mis_en_jeu_est_dit_au_modele_des_le_premier_appel() -> None:
    """Le modèle ne se souvient de rien d'un tour à l'autre : le poste lui est redit."""
    modele = _Modele({**COMPRIS_CHORALE, "questions": []})
    joueur = _Joueur({sonde("uv"): INTROUVABLE})
    posee = _question(QUESTION_GESTIONNAIRE)
    fil = [
        _dit(DEMANDE),
        MessageChat(agent="orchestrateur", auteur="orchestrateur", contenu="?", question=posee),
        MessageChat(
            agent="orchestrateur",
            auteur=UTILISATEUR,
            contenu="pip",
            choix=Choix("gestionnaire", "pip"),
        ),
    ]

    asyncio.run(_conducteur(modele, joueur).ouvrir(fil))

    assert "- `uv` : introuvable sur ce poste" in modele.prompts[0]


def test_sans_bash_le_questionnaire_est_celui_d_avant() -> None:
    """Sous la garde du conftest : aucun fait du poste, un seul appel, rien de reporté."""
    modele = _Modele(COMPRIS_CHORALE)

    reponse = asyncio.run(ConducteurOutillage(ComprehensionModele(modele)).ouvrir([_dit(DEMANDE)]))

    assert len(modele.prompts) == 1 and "poste a répondu" not in modele.prompts[0]
    assert reponse.question is not None and reponse.question.recommande == "uv"


def test_le_prompt_demande_les_outils_de_chaque_option() -> None:
    assert '"outils"' in _PROMPT_COMPREHENSION
    assert "Ce que le poste a répondu" in _PROMPT_COMPREHENSION
    assert _PROMPT_COMPREHENSION.endswith(registre())


# --------------------------------------------------------------------------- #
# ③ Ce qui ne pouvait pas encore se jouer se joue dès que le projet le permet    #
# --------------------------------------------------------------------------- #

#: Un carnet de chants compris sans question : Python, assemblé par un script.
COMPRIS_CARNET: dict[str, Any] = {
    "message": "",
    "constats": [
        NATURE,
        {"cle": "langages", "valeur": "Python", "parce_que": "un script assemble le carnet"},
        {
            "cle": "construire",
            "valeur": "python assembler.py",
            "parce_que": "le script",
            "pour": "Assembler le carnet avec assembler.py, après l'ajout d'un chant.",
        },
    ],
    "questions": [],
}


@pytest.fixture()
def projets(tmp_path: Path) -> ServiceProjets:
    """Le service des projets sur un dépôt et des réglages jetables — jamais ceux du poste."""
    return ServiceProjets(
        ProjetStore(tmp_path / "depot"), reglages=ReglagesProjetsStore(tmp_path / "reglages")
    )


class _Lecteur(ModelProvider):
    """Le modèle qui **lit** le projet (#1158) : il rend ce qu'on lui dicte, tour après tour.

    Sans rien à dire, il ne doit pas être appelé : un projet neuf se décrit, il ne se lit
    pas, et un outillage tout vérifié ne coûte aucune lecture (#1381).
    """

    name = "lecteur-double"

    def __init__(self, *tours: str) -> None:
        self._tours = list(tours)
        self.prompts: list[str] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt: str, *, model: str, system_prompt: Any = None) -> str:
        # Retenu avant tout : la lecture avale les pannes de son modèle, et c'est sur
        # `prompts` qu'un test voit qu'il a été appelé quand il ne devait pas l'être.
        self.prompts.append(prompt)
        if not self._tours:
            raise AssertionError("le projet n'avait pas à être lu par le modèle")
        return self._tours[min(len(self.prompts), len(self._tours)) - 1]


def _outille(
    projets: ServiceProjets,
    joueur: _Joueur,
    lecteur: _Lecteur | None = None,
    compris: Mapping[str, Any] = COMPRIS_CARNET,
) -> ConducteurOutillage:
    """Le conducteur qui écrit l'outillage pièce par pièce — commandes et sondes doublées."""
    verificateur = Verificateur(joueur=joueur, interprete=FAUX_BASH)
    pieces = ServicePieces(
        ServiceOutillage(projets, provider=lecteur or _Lecteur()),
        projets,
        verificateur=verificateur,
    )
    return ConducteurOutillage(
        ComprehensionModele(_Modele(compris)), pieces=pieces, verificateur=verificateur
    )


def _message(reponse: Any) -> MessageChat:
    """Le message qu'une réponse du conducteur laisse au fil."""
    return MessageChat(
        agent="orchestrateur",
        auteur="orchestrateur",
        contenu=reponse.contenu,
        piece=reponse.piece,
        piece_ecrite=reponse.piece_ecrite,
        comprehension=reponse.comprehension,
        projet_outille=reponse.projet_outille,
    )


def _carnet_outille(
    projets: ServiceProjets,
    maison: Path,
    joueur: _Joueur,
    lecteur: _Lecteur | None = None,
    compris: Mapping[str, Any] = COMPRIS_CARNET,
) -> tuple[ConducteurOutillage, str, Path, list[MessageChat]]:
    """Le carnet né vide, son `AGENTS.md` écrit sur accord — ses commandes « à vérifier »."""
    racine = maison / "Maestro" / "chorale"
    projet_id = str(projets.creer("chorale", str(racine), origine=ORIGINE_NOUVEAU)["id"])
    conducteur = _outille(projets, joueur, lecteur, compris)
    fil = [_dit(DEMANDE)]
    ouverture = asyncio.run(conducteur.ouvrir(fil, projet_id))
    assert ouverture.piece is not None and ouverture.piece.chemin == "AGENTS.md"
    fil.append(_message(ouverture))
    fil.append(_dit("Oui, écris AGENTS.md."))
    ecrite = asyncio.run(
        conducteur.trancher(fil, piece=ouverture.piece, decision=DECISION_ECRIRE)
    )
    assert ecrite.piece_ecrite is not None and ecrite.piece_ecrite.ecrite
    fil.append(_message(ecrite))
    return conducteur, projet_id, racine, fil


def test_apres_le_run_les_commandes_a_verifier_se_jouent_sur_le_projet_construit(
    projets: ServiceProjets, _maison: Path
) -> None:
    """Le défaut de #1343 : « à vérifier » ne se vérifiait jamais, une fois le projet construit."""
    joueur = _Joueur()
    conducteur, projet_id, racine, fil = _carnet_outille(projets, _maison, joueur)
    avant = (racine / "AGENTS.md").read_text(encoding="utf-8")
    assert "**À vérifier**" in _ligne(avant, "python assembler.py")
    assert "python assembler.py" not in joueur.joues  # un dossier vide ne joue rien
    # Le run construit le projet.
    (racine / "assembler.py").write_text("print('carnet')\n", encoding="utf-8")

    revue = asyncio.run(conducteur.apres_le_run(fil, projet_id))

    assert revue is not None and revue.piece is not None
    assert revue.projet_outille == projet_id
    assert revue.piece.chemin == "AGENTS.md"
    assert "python assembler.py" in joueur.joues
    (verdict,) = [v for v in revue.piece.verifications if v.commande == "python assembler.py"]
    assert verdict.etat == VERIFIEE
    assert "**Vérifiée**" in _ligne(revue.piece.contenu, "python assembler.py")
    assert "a maintenant ses fichiers" in revue.contenu
    # Proposée, jamais écrite d'office : le disque porte encore la version d'avant le run.
    assert (racine / "AGENTS.md").read_text(encoding="utf-8") == avant
    assert revue.piece.texte_avant == avant


def test_une_commande_qui_echoue_sur_le_projet_construit_est_dite_echouee_avec_sa_sortie(
    projets: ServiceProjets, _maison: Path
) -> None:
    """Le projet lu ne montre aucune autre commande : l'échec se dit, comme depuis #1343."""
    joueur = _Joueur(
        {"python assembler.py": execution.Execution(code=2, sortie="Traceback: boum", duree_s=1)}
    )
    lecteur = _Lecteur("FIN")
    conducteur, projet_id, racine, fil = _carnet_outille(projets, _maison, joueur, lecteur)
    (racine / "assembler.py").write_text("raise SystemExit(2)\n", encoding="utf-8")

    revue = asyncio.run(conducteur.apres_le_run(fil, projet_id))

    assert revue is not None and revue.piece is not None
    (verdict,) = [v for v in revue.piece.verifications if v.commande == "python assembler.py"]
    assert (verdict.etat, verdict.code, verdict.sortie) == (ECHOUEE, 2, "Traceback: boum")
    assert "⚠ **Échouée**" in _ligne(revue.piece.contenu, "python assembler.py")
    assert len(lecteur.prompts) == 1  # lu, parce qu'une commande a échoué
    assert revue.piece.proposees == () and revue.piece.lues_dans == ()


def test_rien_a_revoir_tant_que_le_projet_est_vide_ou_quand_tout_est_verifie(
    projets: ServiceProjets, _maison: Path
) -> None:
    joueur = _Joueur()
    lecteur = _Lecteur()
    conducteur, projet_id, racine, fil = _carnet_outille(projets, _maison, joueur, lecteur)

    # Encore vide : rien de plus ne se jouerait qu'à l'écriture.
    assert asyncio.run(conducteur.apres_le_run(fil, projet_id)) is None
    # Un fil qui n'a rien écrit de ce projet ne le revoit pas : il n'en sait rien.
    (racine / "assembler.py").write_text("print('carnet')\n", encoding="utf-8")
    assert asyncio.run(conducteur.apres_le_run([_dit(DEMANDE)], projet_id)) is None
    # Revue, puis écrite : tout est vérifié, et un run de plus ne rejoue rien.
    revue = asyncio.run(conducteur.apres_le_run(fil, projet_id))
    assert revue is not None and revue.piece is not None
    fil.append(_message(revue))
    ecrite = asyncio.run(conducteur.trancher(fil, piece=revue.piece, decision=DECISION_ECRIRE))
    assert ecrite.piece_ecrite is not None and ecrite.piece_ecrite.ecrite
    fil.append(_message(ecrite))
    manifeste = json.loads((racine / CHEMIN_MANIFESTE).read_text(encoding="utf-8"))
    assert {v["etat"] for v in manifeste["verifications"]} == {VERIFIEE}
    joues = len(joueur.joues)
    assert asyncio.run(conducteur.apres_le_run(fil, projet_id)) is None
    assert len(joueur.joues) == joues
    # Rien n'a échoué : la lecture du projet, qui coûte un appel par tour, ne s'est pas jouée.
    assert lecteur.prompts == []


# ── Ce que le projet construit montre, quand la commande écrite échoue (#1381) ──

#: La commande que l'outillage a écrite à la naissance du carnet, et celle que le run a
#: construite : S9 l'a relevé, le script est livré sous un autre nom.
ECRITE = "python assembler.py"
MONTREE = "python assembler_carnet.py chants carnet.html"

#: Ce que bash répond d'un script qui n'existe pas — une donnée pour le modèle, jamais lue.
SANS_SCRIPT = execution.Execution(
    code=2, sortie="python: can't open file 'assembler.py': [Errno 2]", duree_s=0.1
)

#: Ce que la commande montrée fait pour le carnet, dit par le modèle qui a lu le script.
POUR_MONTREE = "Assembler les chants en carnet.html avec assembler_carnet.py."

#: Le modèle qui lit le carnet construit : il ouvre le script, et rend la commande qu'il y
#: lit — avec ce qu'elle fait pour le projet.
LECTURE_DU_CARNET = (
    "LIRE: assembler_carnet.py",
    f"COMMANDE: construire | assembler_carnet.py | declaree | l'usage écrit en tête | {MONTREE}\n"
    f"POUR: {POUR_MONTREE} | {MONTREE}\n"
    "FIN",
)


def _carnet_construit(racine: Path) -> None:
    """Ce que le run a laissé : les chants, et le script sous un nom que l'outillage ignore."""
    (racine / "chants").mkdir()
    (racine / "chants" / "ave.txt").write_text("Ave\n", encoding="utf-8")
    (racine / "assembler_carnet.py").write_text(
        f'"""Assemble le carnet.\n\nUsage : {MONTREE}\n"""\n', encoding="utf-8"
    )


def test_la_commande_qui_echoue_apres_le_run_fait_proposer_celle_que_le_projet_montre(
    projets: ServiceProjets, _maison: Path
) -> None:
    """Le cas de S9 : `python assembler.py` écrite, le script livré sous `assembler_carnet.py`."""
    joueur = _Joueur({ECRITE: SANS_SCRIPT})
    lecteur = _Lecteur(*LECTURE_DU_CARNET)
    conducteur, projet_id, racine, fil = _carnet_outille(projets, _maison, joueur, lecteur)
    avant = (racine / "AGENTS.md").read_text(encoding="utf-8")
    _carnet_construit(racine)

    revue = asyncio.run(conducteur.apres_le_run(fil, projet_id))

    assert revue is not None and revue.piece is not None and revue.piece.chemin == "AGENTS.md"
    # Lu parce que la commande écrite a échoué — et ce qu'elle a rendu est dit au modèle.
    assert lecteur.prompts, "le projet construit n'a pas été lu"
    assert f"`{ECRITE}`" in lecteur.prompts[0] and "can't open file" in lecteur.prompts[0]
    # Jouée avant d'être montrée, et vérifiée.
    assert joueur.joues.index(ECRITE) < joueur.joues.index(MONTREE)
    (verdict,) = [v for v in revue.piece.verifications if v.commande == MONTREE]
    assert verdict.etat == VERIFIEE
    assert ECRITE not in {v.commande for v in revue.piece.verifications}
    # Proposée par Maestro, avec le fichier où il l'a lue — pas dite par la personne.
    assert revue.piece.proposees == (MONTREE,)
    assert revue.piece.lues_dans == ("assembler_carnet.py",)
    assert revue.piece.correction == "" and revue.piece.corrigees == ()
    ligne = _ligne(revue.piece.contenu, MONTREE)
    assert "`assembler_carnet.py`" in ligne and "**Vérifiée**" in ligne
    assert "dite par la personne" not in ligne
    assert f"`{ECRITE}`" not in revue.piece.contenu
    assert revue.piece.ecrivable
    assert "L'une a échoué : j'ai lu le projet" in revue.contenu
    # Proposée, jamais écrite d'office.
    assert (racine / "AGENTS.md").read_text(encoding="utf-8") == avant


def test_une_commande_lue_qui_echoue_a_son_tour_n_est_pas_proposee(
    projets: ServiceProjets, _maison: Path
) -> None:
    """Le code de retour décide : ce que le modèle a lu ne passe pas pour ce qui marche."""
    joueur = _Joueur(
        {ECRITE: SANS_SCRIPT, MONTREE: execution.Execution(code=1, sortie="KeyError", duree_s=1)}
    )
    lecteur = _Lecteur(*LECTURE_DU_CARNET)
    conducteur, projet_id, racine, fil = _carnet_outille(projets, _maison, joueur, lecteur)
    _carnet_construit(racine)

    revue = asyncio.run(conducteur.apres_le_run(fil, projet_id))

    assert revue is not None and revue.piece is not None
    assert MONTREE in joueur.joues
    assert revue.piece.proposees == () and revue.piece.lues_dans == ()
    assert MONTREE not in revue.piece.contenu
    # La commande écrite est dite échouée, sortie comprise, comme depuis #1343.
    (verdict,) = [v for v in revue.piece.verifications if v.commande == ECRITE]
    assert (verdict.etat, verdict.code, verdict.sortie) == (ECHOUEE, 2, SANS_SCRIPT.sortie)
    assert "⚠ **Échouée**" in _ligne(revue.piece.contenu, ECRITE)


def test_une_commande_que_le_modele_n_a_pas_lue_n_est_ni_jouee_ni_proposee(
    projets: ServiceProjets, _maison: Path
) -> None:
    """Un constat entre avec le fichier **lu** qui le prouve, ou n'entre pas (#1158)."""
    joueur = _Joueur({ECRITE: SANS_SCRIPT})
    lecteur = _Lecteur(f"COMMANDE: construire | assembler_carnet.py | declaree | vu | {MONTREE}")
    conducteur, projet_id, racine, fil = _carnet_outille(projets, _maison, joueur, lecteur)
    _carnet_construit(racine)

    revue = asyncio.run(conducteur.apres_le_run(fil, projet_id))

    assert revue is not None and revue.piece is not None
    assert MONTREE not in joueur.joues
    assert revue.piece.proposees == ()
    assert "⚠ **Échouée**" in _ligne(revue.piece.contenu, ECRITE)


def test_l_outillage_de_maestro_ne_justifie_pas_la_commande_qu_il_propose(
    projets: ServiceProjets, _maison: Path
) -> None:
    """Vu sur S9 : une commande « lue » dans ce que Maestro a écrit — une preuve circulaire."""
    joueur = _Joueur({ECRITE: SANS_SCRIPT})
    lecteur = _Lecteur(
        "LIRE: AGENTS.md",
        f"COMMANDE: construire | AGENTS.md | declaree | corrigée d'après AGENTS.md | {MONTREE}\n"
        "FIN",
    )
    conducteur, projet_id, racine, fil = _carnet_outille(projets, _maison, joueur, lecteur)
    _carnet_construit(racine)

    revue = asyncio.run(conducteur.apres_le_run(fil, projet_id))

    assert revue is not None and revue.piece is not None
    assert MONTREE not in joueur.joues
    assert revue.piece.proposees == ()
    # Et le modèle sait qu'il doit chercher ailleurs que dans ce que Maestro a écrit.
    assert "ne justifie pas une commande" in lecteur.prompts[0]


def test_sans_phrase_du_modele_la_commande_proposee_garde_ce_que_faisait_l_ancienne(
    projets: ServiceProjets, _maison: Path
) -> None:
    """La règle d'une correction dite (#1350) : la commande change, pas ce qu'elle fait."""
    joueur = _Joueur({ECRITE: SANS_SCRIPT})
    sans_pour = LECTURE_DU_CARNET[1].replace(f"POUR: {POUR_MONTREE} | {MONTREE}\n", "")
    lecteur = _Lecteur(LECTURE_DU_CARNET[0], sans_pour)
    conducteur, projet_id, racine, fil = _carnet_outille(projets, _maison, joueur, lecteur)
    _carnet_construit(racine)

    revue = asyncio.run(conducteur.apres_le_run(fil, projet_id))

    assert revue is not None and revue.piece is not None
    assert revue.piece.proposees == (MONTREE,)
    assert "Assembler le carnet avec assembler.py, après l'ajout d'un chant." in (
        revue.piece.contenu
    )


def test_une_commande_proposee_rejouee_en_echec_ne_s_ecrit_pas(
    projets: ServiceProjets, _maison: Path
) -> None:
    """Retenue, elle se rejoue comme une correction — et son échec se dit sans « vous »."""
    joueur = _Joueur({ECRITE: SANS_SCRIPT})
    lecteur = _Lecteur(*LECTURE_DU_CARNET)
    conducteur, projet_id, racine, fil = _carnet_outille(projets, _maison, joueur, lecteur)
    _carnet_construit(racine)
    revue = asyncio.run(conducteur.apres_le_run(fil, projet_id))
    assert revue is not None and revue.piece is not None
    fil.append(_message(revue))
    ecrite = asyncio.run(conducteur.trancher(fil, piece=revue.piece, decision=DECISION_ECRIRE))
    assert ecrite.piece_ecrite is not None and ecrite.piece_ecrite.ecrite
    fil.append(_message(ecrite))
    # Son verdict n'est plus tenu pour acquis (le manifeste est au projet, on a pu y
    # toucher), et le script ne passe plus : la revue la rejoue, rien ne la remplace.
    chemin = racine / CHEMIN_MANIFESTE
    manifeste = json.loads(chemin.read_text(encoding="utf-8"))
    for verdict in manifeste["verifications"]:
        if verdict["commande"] == MONTREE:
            verdict["etat"] = A_VERIFIER
    chemin.write_text(json.dumps(manifeste, ensure_ascii=False), encoding="utf-8")
    joueur._resultats[MONTREE] = execution.Execution(code=1, sortie="SyntaxError", duree_s=1)
    lecteur._tours = ["FIN"]
    revue = asyncio.run(conducteur.apres_le_run(fil, projet_id))

    assert revue is not None
    piece = revue.piece
    assert piece is not None and not piece.ecrivable
    assert piece.echec.startswith(f"`{MONTREE}`, que j'avais lue dans `assembler_carnet.py`,")
    assert "a échoué à l'exécution" in piece.echec
    assert piece.correction == ""


def test_la_commande_proposee_s_ecrit_sur_accord_et_reste_acquise_au_projet(
    projets: ServiceProjets, _maison: Path
) -> None:
    """Écrite, elle est retenue au manifeste comme une correction (#1334) : plus reproposée."""
    joueur = _Joueur({ECRITE: SANS_SCRIPT})
    lecteur = _Lecteur(*LECTURE_DU_CARNET)
    conducteur, projet_id, racine, fil = _carnet_outille(projets, _maison, joueur, lecteur)
    _carnet_construit(racine)
    revue = asyncio.run(conducteur.apres_le_run(fil, projet_id))
    assert revue is not None and revue.piece is not None
    fil.append(_message(revue))

    ecrite = asyncio.run(conducteur.trancher(fil, piece=revue.piece, decision=DECISION_ECRIRE))

    assert ecrite.piece_ecrite is not None and ecrite.piece_ecrite.ecrite
    assert MONTREE in _ligne((racine / "AGENTS.md").read_text(encoding="utf-8"), MONTREE)
    manifeste = json.loads((racine / CHEMIN_MANIFESTE).read_text(encoding="utf-8"))
    (prise,) = [c for c in manifeste["corrections"] if c["cle"] == "construire"]
    assert (prise["valeur"], prise["origine"], prise["chemin"]) == (
        MONTREE,
        "proposee",
        "assembler_carnet.py",
    )
    # La pièce suivante — le skill de construction — écrit la commande proposée, sans la
    # rejouer : elle a été vérifiée avant d'être montrée.
    assert ecrite.piece is not None and ecrite.piece.nature == "skill"
    assert MONTREE in ecrite.piece.contenu and ECRITE not in ecrite.piece.contenu
    assert ecrite.piece.proposees == (MONTREE,)
    # Sa description dit ce que la commande montrée fait — plus le script qui n'existe pas
    # (vu sur S9 : « Vérifier que le script d'assemblage assembler.py compile… »).
    assert f'description: "{POUR_MONTREE}"' in ecrite.piece.contenu
    assert "assembler.py" not in ecrite.piece.contenu
    # Et d'où elle vient : un fichier lu dans le projet construit, pas un fichier « à créer ».
    assert "Lue par Maestro après un run dans `assembler_carnet.py`" in ecrite.piece.contenu
    assert "À créer" not in ecrite.piece.contenu
    assert joueur.joues.count(MONTREE) == 1
    # Plus aucun fichier n'écrit la commande remplacée — le skill ne l'est pas encore : son
    # verdict quitte le manifeste, qui ne déclare que ce que l'outillage écrit. C'est ce que
    # le banc rejoue (S9, passage 20260929-130422, où il restait « à vérifier »).
    assert {v["commande"]: v["etat"] for v in manifeste["verifications"]} == {MONTREE: VERIFIEE}
    fil.append(_message(ecrite))
    skill = asyncio.run(conducteur.trancher(fil, piece=ecrite.piece, decision=DECISION_ECRIRE))
    assert skill.piece_ecrite is not None and skill.piece_ecrite.ecrite
    fil.append(_message(skill))
    # Un run de plus ne la repropose pas, ne la rejoue pas, et ne relit rien.
    lectures = len(lecteur.prompts)
    assert asyncio.run(conducteur.apres_le_run(fil, projet_id)) is None
    assert len(lecteur.prompts) == lectures
    assert joueur.joues.count(MONTREE) == 1


def test_pas_cette_piece_n_ecrit_rien_de_la_commande_proposee(
    projets: ServiceProjets, _maison: Path
) -> None:
    joueur = _Joueur({ECRITE: SANS_SCRIPT})
    conducteur, projet_id, racine, fil = _carnet_outille(
        projets, _maison, joueur, _Lecteur(*LECTURE_DU_CARNET)
    )
    avant = (racine / "AGENTS.md").read_text(encoding="utf-8")
    _carnet_construit(racine)
    revue = asyncio.run(conducteur.apres_le_run(fil, projet_id))
    assert revue is not None and revue.piece is not None
    fil.append(_message(revue))

    passee = asyncio.run(conducteur.trancher(fil, piece=revue.piece, decision=DECISION_PASSER))

    assert passee.piece_ecrite is not None and not passee.piece_ecrite.ecrite
    assert (racine / "AGENTS.md").read_text(encoding="utf-8") == avant
    manifeste = json.loads((racine / CHEMIN_MANIFESTE).read_text(encoding="utf-8"))
    assert all(c["cle"] != "construire" for c in manifeste.get("corrections", []))
    # Passée, elle ne revient pas telle quelle.
    assert passee.piece is None or passee.piece.chemin != "AGENTS.md"


# ── Ce que la revue remplace ne reste nulle part (#1442) ──

#: La commande de tests comprise à la naissance du carnet — le résumé des réponses la
#: cite —, et celle que le README livré par le run prescrit. Vu sur S9 (passage
#: `20261007-103746`) : la suite livrée est écrite pour pytest, `unittest` n'en joue aucun.
TESTS_REPONDUS = "python -m unittest discover -s tests"
TESTS_LUS = "python -m pytest"

#: Le carnet compris avec sa commande de tests, comme S9 l'a décrit.
COMPRIS_AVEC_TESTS: dict[str, Any] = {
    **COMPRIS_CARNET,
    "constats": [
        *COMPRIS_CARNET["constats"],
        {
            "cle": "tester",
            "valeur": TESTS_REPONDUS,
            "parce_que": "Réponse choisie : tests unittest",
            "pour": "Jouer les tests de l'assemblage du carnet, avant de rendre un changement.",
        },
    ],
}

#: Le modèle qui lit le carnet construit : il ouvre le README, et rend la commande qu'il y lit.
LECTURE_DES_TESTS = (
    "LIRE: README.md",
    f"COMMANDE: tester | README.md | declaree | section Lancer les tests | {TESTS_LUS}\nFIN",
)

#: Ce que `unittest` répond d'une suite écrite pour pytest : code 5, aucun test joué.
AUCUN_TEST = execution.Execution(
    code=5, sortie="Ran 0 tests in 0.000s\n\nNO TESTS RAN", duree_s=0.1
)


def _tout_ecrire(conducteur: ConducteurOutillage, fil: list[MessageChat]) -> None:
    """Écrit sur accord chaque pièce que le fil propose, jusqu'à la dernière."""
    for _ in range(10):
        piece = fil[-1].piece
        if piece is None:
            return
        ecrite = asyncio.run(conducteur.trancher(fil, piece=piece, decision=DECISION_ECRIRE))
        assert ecrite.piece_ecrite is not None and ecrite.piece_ecrite.ecrite, ecrite.contenu
        fil.append(_message(ecrite))
    raise AssertionError("le fil propose des pièces sans fin")


def _carnet_teste(racine: Path) -> None:
    """Ce que le run a laissé : le script, une suite écrite pour pytest, et le README qui le dit."""
    (racine / "assembler.py").write_text("print('carnet')\n", encoding="utf-8")
    (racine / "tests").mkdir()
    (racine / "tests" / "test_carnet.py").write_text(
        "def test_sommaire():\n    assert True\n", encoding="utf-8"
    )
    (racine / "README.md").write_text(
        f"# Carnet\n\n## Lancer les tests\n\n    {TESTS_LUS}\n", encoding="utf-8"
    )


def test_la_commande_de_tests_remplacee_apres_le_run_ne_reste_nulle_part(
    projets: ServiceProjets, _maison: Path
) -> None:
    """Le cas de S9 (passage `20261007-103746`) : la commande de tests des réponses, remplacée
    à la revue d'après le run, restait au manifeste « à vérifier », avec sa raison devenue
    fausse — la ligne « Origine » d'`AGENTS.md` la citait encore —, et le banc la rejouait."""
    joueur = _Joueur({TESTS_REPONDUS: AUCUN_TEST})
    lecteur = _Lecteur(*LECTURE_DES_TESTS)
    conducteur, projet_id, racine, fil = _carnet_outille(
        projets, _maison, joueur, lecteur, COMPRIS_AVEC_TESTS
    )
    _tout_ecrire(conducteur, fil)
    chemin = racine / CHEMIN_MANIFESTE
    ne = json.loads(chemin.read_text(encoding="utf-8"))
    assert {v["commande"]: v["etat"] for v in ne["verifications"]}[TESTS_REPONDUS] == A_VERIFIER
    _carnet_teste(racine)

    revue = asyncio.run(conducteur.apres_le_run(fil, projet_id))
    assert revue is not None and revue.piece is not None
    assert revue.piece.proposees == (TESTS_LUS,)
    fil.append(_message(revue))
    _tout_ecrire(conducteur, fil)

    manifeste = json.loads(chemin.read_text(encoding="utf-8"))
    # Le manifeste ne déclare que ce que l'outillage prescrit : c'est ce que le banc rejoue.
    assert {v["commande"]: v["etat"] for v in manifeste["verifications"]} == {
        ECRITE: VERIFIEE,
        TESTS_LUS: VERIFIEE,
    }
    # Et rien de ce que Maestro a écrit ne cite la commande remplacée, ni ne la dit à vérifier.
    for entree in manifeste["entrees"]:
        texte = (racine / entree["chemin"]).read_text(encoding="utf-8")
        assert TESTS_REPONDUS not in texte, entree["chemin"]
        assert "À vérifier" not in texte, entree["chemin"]
    agents = (racine / "AGENTS.md").read_text(encoding="utf-8")
    assert "**Vérifiée**" in _ligne(agents, TESTS_LUS)


#: Ce que la lecture a rendu du carnet construit : la commande, et le fichier qui la prouve.
LUE = Commande(
    usage="construire",
    commande=MONTREE,
    chemin="assembler_carnet.py",
    extrait="l'usage écrit en tête",
    origine="declaree",
)


def test_une_commande_proposee_se_relit_du_manifeste_sans_passer_pour_dite() -> None:
    proposee = CorrectionPrise.lue(LUE, QUAND)
    dite = CorrectionPrise("tester", "pytest", "c'est pytest", QUAND)

    assert proposee.proposee and not dite.proposee
    assert proposee.to_dict()["origine"] == "proposee"
    assert proposee.to_dict()["chemin"] == "assembler_carnet.py"
    assert corrections_lues([proposee.to_dict(), dite.to_dict()]) == (proposee, dite)
    # Une correction dite garde sa forme d'avant #1381 : un manifeste d'avant se relit.
    assert set(dite.to_dict()) == {"cle", "valeur", "phrase", "prise_le"}
    # Une origine inconnue se relit « dite » ; Maestro ne propose que des commandes.
    assert not corrections_lues([{**proposee.to_dict(), "origine": "autre"}])[0].proposee
    assert corrections_lues([{**proposee.to_dict(), "cle": "gestionnaire"}]) == ()
    # Le modèle qui comprend la phrase suivante ne la prête pas à la personne.
    texte = corrections_en_texte([proposee, dite])
    assert f"« {MONTREE} » — proposée par Maestro, lue dans assembler_carnet.py" in texte
    assert "« pytest » — dit : « c'est pytest »" in texte


def test_adopter_pose_la_commande_proposee_et_garde_ce_qu_elle_fait_pour_le_projet() -> None:
    pour = "Assembler le carnet à partir des chants."
    ecrite = Commande(usage="construire", commande=ECRITE, chemin="", pour=pour)
    constats = Constats(commandes=(ecrite,))

    adoptee = adopter(constats, [CorrectionPrise.lue(LUE, QUAND)]).commande_de("construire")

    assert adoptee is not None
    assert (adoptee.commande, adoptee.origine, adoptee.chemin, adoptee.pour) == (
        MONTREE,
        ORIGINE_PROPOSEE,
        "assembler_carnet.py",
        pour,
    )
    # Ce que le modèle a dit de la commande en la lisant l'emporte, et voyage au manifeste.
    dite_en_lisant = CorrectionPrise.lue(replace(LUE, pour=POUR_MONTREE), QUAND)
    assert dite_en_lisant.to_dict()["pour"] == POUR_MONTREE
    assert corrections_lues([dite_en_lisant.to_dict()]) == (dite_en_lisant,)
    relue = adopter(constats, [dite_en_lisant]).commande_de("construire")
    assert relue is not None and relue.pour == POUR_MONTREE
    # Ce que la personne a dite n'est pas adopté ici : c'est `corriger` qui l'applique.
    assert adopter(constats, [CorrectionPrise("construire", "make", "c'est make", QUAND)]) == (
        constats
    )


def test_une_piece_qui_porte_une_commande_proposee_se_relit_a_l_identique() -> None:
    piece = replace(
        _piece_revue(),
        proposees=(MONTREE,),
        lues_dans=("assembler_carnet.py",),
        corrections_prises=(CorrectionPrise.lue(LUE, QUAND),),
    )

    assert PieceProposee.from_dict(json.loads(json.dumps(piece.to_dict()))) == piece
    assert PieceProposee.from_dict({"chemin": "AGENTS.md"}).proposees == ()
    # Ce que le modèle relit de la carte au tour suivant dit d'où Maestro la tient.
    assert "avec la commande que Maestro propose, lue dans assembler_carnet.py" in (
        piece.en_phrase()
    )


class _Redacteur:
    """Le rédacteur du récit : il rend ce qu'on lui a posé, ou tombe en panne."""

    def __init__(self, texte: str = "Voilà votre carnet.", *, panne: bool = False) -> None:
        self.texte, self.panne = texte, panne
        self.commence = asyncio.Event()
        self.attendre: asyncio.Event | None = None

    async def rediger(self, *, agent: Any, contexte: str) -> str:
        self.commence.set()
        if self.attendre is not None:
            await asyncio.wait_for(self.attendre.wait(), timeout=2)
        if self.panne:
            raise RuntimeError("fournisseur injoignable")
        return self.texte


class _Revue:
    """La revue de l'outillage : elle rend la pièce qu'on lui a posée, ou tombe en panne."""

    def __init__(self, reponse: ReponseChat | None, *, panne: bool = False) -> None:
        self.reponse, self.panne = reponse, panne
        self.commence = asyncio.Event()
        self.attendre: asyncio.Event | None = None
        self.vus: list[tuple[int, str]] = []

    async def apres_le_run(self, fil: Any, projet_id: str) -> ReponseChat | None:
        self.vus.append((len(fil), projet_id))
        self.commence.set()
        if self.attendre is not None:
            await asyncio.wait_for(self.attendre.wait(), timeout=2)
        if self.panne:
            raise RuntimeError("revue impossible")
        return self.reponse


RUN = "run-1343"


def _piece_revue() -> PieceProposee:
    return PieceProposee(
        projet_id="p1",
        projet_nom="chorale",
        cible="/tmp/chorale",
        chemin="AGENTS.md",
        nom="AGENTS.md",
        nature="instructions",
        raison="…",
        role="instructions",
        portee="fichier",
        contenu="# AGENTS.md\n",
        empreinte_avant="sha256:avant",
    )


def _conteur(
    tmp_path: Path, redacteur: _Redacteur, revue: _Revue | None
) -> tuple[ConteurDeFin, ChatStore]:
    depot = ChatStore(tmp_path / "chat")
    for message in (
        MessageChat(agent=NOM_ORCHESTRATION, conversation="c", auteur=UTILISATEUR, contenu="Va"),
        MessageChat(
            agent=NOM_ORCHESTRATION,
            conversation="c",
            auteur=NOM_ORCHESTRATION,
            contenu="C'est parti.",
            run_id=RUN,
        ),
    ):
        depot.ajouter(message)
    state = ControlTowerState()
    for statut in (EXECUTION_EN_COURS, EXECUTION_TERMINEE):
        state.appliquer(
            Event(type=EVENEMENT_EXECUTION_STATUT, run_id=RUN, statut=statut, projet_id="p1")
        )
    chat = ServiceChat(
        store=depot, repondeur=_Muet(), mailbox=InMemoryMailbox(), bus=InMemoryEventBus()
    )
    conteur = ConteurDeFin(
        chat=chat,
        state=state,
        agent=AGENT_ORCHESTRATION,
        projet=lambda _id: None,
        redacteur=redacteur,
        outillage=revue,
    )
    return conteur, depot


class _Muet(RepondeurChat):
    async def repondre(self, agent: Any, fil: Any) -> str:  # pragma: no cover
        raise AssertionError("la fin d'un run ne passe pas par le répondeur du fil")


OUTILLAGE_REVU = "« chorale » a maintenant ses fichiers : j'ai rejoué…"


def test_la_fin_du_run_porte_la_piece_revue_sur_le_message_du_recit(tmp_path: Path) -> None:
    """Une seule bulle, et c'est la dernière : la pièce y attend un geste."""
    revue = _Revue(ReponseChat(contenu=OUTILLAGE_REVU, piece=_piece_revue(), projet_outille="p1"))
    conteur, depot = _conteur(tmp_path, _Redacteur(), revue)

    message = asyncio.run(conteur.raconter(RUN))

    assert message is not None
    assert message.contenu == f"Voilà votre carnet.\n\n{OUTILLAGE_REVU}"
    assert message.run_id == RUN
    assert message.piece == _piece_revue() and message.projet_outille == "p1"
    fil = depot.fil(NOM_ORCHESTRATION, "c")
    assert piece_en_attente(fil) is not None and piece_en_attente(fil).piece == _piece_revue()
    # La revue a lu le fil de la conversation qui a demandé le run, pour son projet.
    assert revue.vus == [(2, "p1")]


def test_le_recit_et_la_revue_se_font_en_meme_temps(tmp_path: Path) -> None:
    """Une barrière, jamais un `sleep` : chacun n'avance que quand l'autre a commencé."""
    redacteur = _Redacteur()
    revue = _Revue(ReponseChat(contenu=OUTILLAGE_REVU, piece=_piece_revue(), projet_outille="p1"))
    redacteur.attendre, revue.attendre = revue.commence, redacteur.commence
    conteur, _depot = _conteur(tmp_path, redacteur, revue)

    message = asyncio.run(conteur.raconter(RUN))

    assert message is not None and message.piece is not None


def test_un_recit_que_le_modele_n_ecrit_pas_ne_fait_pas_taire_l_outillage(tmp_path: Path) -> None:
    revue = _Revue(ReponseChat(contenu=OUTILLAGE_REVU, piece=_piece_revue(), projet_outille="p1"))
    conteur, _depot = _conteur(tmp_path, _Redacteur(panne=True), revue)

    message = asyncio.run(conteur.raconter(RUN))

    assert message is not None
    assert message.contenu == OUTILLAGE_REVU and message.piece is not None
    assert message.run_id == RUN


def test_une_revue_en_panne_ne_coute_pas_le_recit(tmp_path: Path) -> None:
    conteur, _depot = _conteur(tmp_path, _Redacteur(), _Revue(None, panne=True))

    message = asyncio.run(conteur.raconter(RUN))

    assert message is not None
    assert message.contenu == "Voilà votre carnet." and message.piece is None


def test_rien_a_revoir_laisse_le_recit_tel_qu_il_etait(tmp_path: Path) -> None:
    conteur, _depot = _conteur(tmp_path, _Redacteur(), _Revue(None))

    message = asyncio.run(conteur.raconter(RUN))

    assert message is not None
    assert message.contenu == "Voilà votre carnet."
    assert message.piece is None and message.projet_outille == ""

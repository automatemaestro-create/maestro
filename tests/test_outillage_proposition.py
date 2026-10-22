"""Le fil propose l'outillage d'un projet d'un bloc, que la personne corrige en mots (#1533).

Sur p5, le fil posait neuf questions une à une — langage, gestionnaire, cadre, tests,
vérification, formatage, forge, CI, convention de commit — pour un projet Next.js dont le
modèle savait juger l'ensemble : 6 min 13 s, 0,538 $ et neuf gestes (docs/50 § 2). Il
**propose** maintenant l'outillage entier, cohérent pour la pile comprise, et ne pose une
question séparée que pour ce qu'il ne sait pas juger. Cette suite porte le premier critère
du ticket, **sur un modèle doublé** : ce qu'on éprouve est la mécanique réelle — prompt,
lecture vérifiée, fil, poste, routes —, et le double ne fait que rendre le texte qu'un
modèle rendrait.

① la **lecture** : ce que le modèle propose est lu et vérifié — un sujet est un constat,
   une question ou une proposition, jamais deux ; la proposition devient **une** question,
   qui passe après celles que le modèle ne sait pas juger ;
② le **geste qui retient** clique chacun des éléments, et la suite conclut **sans
   rappeler le modèle** — jusqu'à la première pièce, qui porte ce qui a été proposé ;
③ la **correction en mots** redit au modèle la proposition qu'elle corrige, et ce qu'il
   repropose est **revérifié** au poste ;
④ les **routes du fil**, et le banc des scénarios qui, sans avis, retient la proposition.

Ni réseau, ni Redis, ni vrai modèle : les routes tournent sur le bus mémoire via le
TestClient de Starlette, et les commandes sont jouées par un joueur doublé.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from maestro.controltower import InMemoryEventBus, create_app
from maestro.controltower.chat import (
    ORIGINE_NOUVEAU,
    UTILISATEUR,
    ChatStore,
    MessageChat,
    RepondeurScripte,
    ReponseChat,
    choix_du_fil,
)
from maestro.controltower.outillage import (
    _PROMPT_COMPREHENSION,
    ComprehensionModele,
    ConducteurOutillage,
    ServiceOutillage,
)
from maestro.controltower.pieces import ServicePieces
from maestro.controltower.projets import ServiceProjets
from maestro.outillage.questionnaire import (
    INTITULE_PROPOSITION,
    PROPOSITION_RETENUE,
    SUJET_PROPOSITION,
    Choix,
    QuestionOutillage,
    acquis_de,
    au_poste,
    choix_de_la_reponse,
    comprehension_depuis_texte,
    constats_depuis_choix,
    programmes_a_sonder,
    repose_sur_un_absent,
    resume_des_choix,
)
from maestro.outillage.verification import Verificateur, sonde
from maestro.projets import ProjetStore
from maestro.projets.reglages import ReglagesProjetsStore
from maestro.providers.base import ModelProvider
from maestro.sandbox import verification as execution
from maestro.scenarios.scenarios import _reponse_a

# --- Le modèle doublé, et ce qu'un modèle rendrait ----------------------------------


class FauxModele(ModelProvider):
    """Rend, dans l'ordre, les compréhensions qu'on lui a données — et note chaque appel.

    Au-delà de la dernière, il rend la dernière : un modèle qu'on ressaisit sans rien de
    neuf ne comprend pas autre chose.
    """

    name = "faux-modele"

    def __init__(self, *reponses: Mapping[str, Any]) -> None:
        self._reponses = [json.dumps(r, ensure_ascii=False) for r in reponses]
        self.prompts: list[str] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(
        self, prompt: str, *, model: str, system_prompt: str | None = None, effort: Any = None
    ) -> str:
        assert system_prompt == _PROMPT_COMPREHENSION, "seule la compréhension parle ici"
        self.prompts.append(prompt)
        return self._reponses[min(len(self.prompts), len(self._reponses)) - 1]


class _LecteurMuet(ModelProvider):
    """Le modèle qui lit un projet existant : un projet neuf se décrit, il ne se lit pas."""

    name = "lecteur-muet"

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt: str, *, model: str, system_prompt: Any = None) -> str:
        raise AssertionError("un projet neuf n'avait pas à être lu par le modèle")


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


#: Un interpréteur factice : le joueur doublé ne le lance jamais.
FAUX_BASH = ("bash", "-c")

#: Ce que bash répond d'un programme qu'il ne trouve pas.
INTROUVABLE = execution.Execution(code=1, sortie="bash: type: pnpm: not found", duree_s=0.1)


def _constat(cle: str, valeur: str, parce_que: str) -> dict[str, str]:
    return {"cle": cle, "valeur": valeur, "parce_que": parce_que}


def _propose(
    cle: str,
    valeur: str,
    libelle: str,
    raison: str,
    *,
    outils: tuple[str, ...] = (),
    pour: str = "",
) -> dict[str, Any]:
    return {
        "cle": cle,
        "valeur": valeur,
        "libelle": libelle,
        "raison": raison,
        "outils": list(outils),
        "pour": pour,
    }


#: La description de p5, la pile dite : ce que le modèle sait juger d'un bloc.
DESCRIPTION = (
    "Un livre de recettes en ligne, moderne et visuel, avec des images et des animations, "
    "en Next.js et TypeScript."
)

CONSTATS_RECETTES = [
    _constat("nature", "Un livre de recettes en ligne, moderne et visuel", "vous l'avez décrit"),
    _constat("langages", "TypeScript", "vous l'avez dit"),
    _constat("cadre", "Next.js", "vous l'avez dit"),
]

POUR_TESTS = "Jouer les tests des pages de recettes et de leurs composants, avant de rendre."

#: Ce que le modèle propose pour une application Next.js en TypeScript : un ensemble
#: cohérent, chacun avec ce qui le désigne dans ce projet.
PROPOSITIONS_NPM = [
    _propose("manifeste", "package.json", "package.json", "Le manifeste d'un projet Next.js."),
    _propose("gestionnaire", "npm", "npm", "Livré avec Node : rien de plus à installer."),
    _propose(
        "installer",
        "npm install",
        "npm install",
        "Installe ce que package.json déclare.",
        pour="Installer les dépendances du livre de recettes, après un clone.",
    ),
    _propose(
        "tester",
        "npx vitest run",
        "Vitest",
        "Rapide, et natif pour TypeScript.",
        outils=("npx",),
        pour=POUR_TESTS,
    ),
    _propose(
        "lint",
        "npx eslint .",
        "ESLint",
        "L'analyseur que Next.js configure d'office.",
        pour="Vérifier le code des pages et des composants, avant de rendre.",
    ),
    _propose(
        "formater",
        "npx prettier --check .",
        "Prettier",
        "Le formateur qui va avec ESLint dans un projet Next.js.",
        pour="Vérifier le format du code, avant de rendre.",
    ),
    _propose("forge", "github", "GitHub", "La forge la plus courante pour un projet Next.js."),
    _propose(
        "ci", ".github/workflows/ci.yml", "GitHub Actions", "La CI que porte GitHub nativement."
    ),
    _propose(
        "conventions",
        "Conventional Commits",
        "Conventional Commits",
        "Des messages lisibles, que les outils savent lire.",
    ),
]

RECETTES: dict[str, Any] = {
    "message": "",
    "constats": CONSTATS_RECETTES,
    "propositions": PROPOSITIONS_NPM,
    "questions": [],
}

#: Après « pnpm, pas npm » : le gestionnaire est dit, et les commandes qui appelaient npm
#: suivent — c'est le reste de la proposition, ajusté pour rester cohérent.
RECETTES_PNPM: dict[str, Any] = {
    "message": "",
    "constats": [*CONSTATS_RECETTES, _constat("gestionnaire", "pnpm", "vous l'avez dit")],
    "propositions": [
        _propose("manifeste", "package.json", "package.json", "Le manifeste d'un projet Next.js."),
        _propose(
            "installer",
            "pnpm install",
            "pnpm install",
            "Installe ce que package.json déclare, avec pnpm.",
            outils=("pnpm",),
            pour="Installer les dépendances du livre de recettes, après un clone.",
        ),
        _propose(
            "tester",
            "pnpm vitest run",
            "Vitest",
            "Rapide, et natif pour TypeScript.",
            outils=("pnpm",),
            pour=POUR_TESTS,
        ),
    ],
    "questions": [],
}

QUESTION_FORGE = {
    "cle": "forge",
    "intitule": "Où le code vivra-t-il ?",
    "options": [
        {"valeur": "github", "libelle": "GitHub", "raison": "Pull Requests et GitHub Actions."},
        {"valeur": "gitlab", "libelle": "GitLab", "raison": "Merge Requests et GitLab CI."},
    ],
    "recommande": "github",
    "pourquoi": "Rien de ce que vous avez dit ne désigne une forge : c'est à vous de la dire.",
}


def _dit(contenu: str, **champs: Any) -> MessageChat:
    return MessageChat(agent="orchestrateur", auteur=UTILISATEUR, contenu=contenu, **champs)


def _maestro(reponse: ReponseChat) -> MessageChat:
    """Le message qu'une réponse du conducteur laisse au fil."""
    return MessageChat(
        agent="orchestrateur",
        auteur="orchestrateur",
        contenu=reponse.contenu,
        question=reponse.question,
        comprehension=reponse.comprehension,
        piece=reponse.piece,
        projet_outille=reponse.projet_outille,
    )


def _retenir(question: QuestionOutillage) -> MessageChat:
    """Le geste qui retient la proposition, tel que `ServiceChat` l'écrit au fil."""
    return _dit("retenu", choix=Choix(question.cle, PROPOSITION_RETENUE))


def _conducteur(faux: FauxModele, joueur: _Joueur | None = None) -> ConducteurOutillage:
    return ConducteurOutillage(
        ComprehensionModele(faux),
        verificateur=Verificateur(joueur=joueur or _Joueur(), interprete=FAUX_BASH),
    )


def _proposition(reponse: ReponseChat) -> QuestionOutillage:
    question = reponse.question
    assert question is not None and question.proposition, reponse.contenu
    return question


# --- ① La lecture ------------------------------------------------------------------


def test_ce_que_rien_ne_tranche_se_propose_d_un_bloc_avec_ses_raisons() -> None:
    """Critère 1 : une pile comprise, l'outillage entier en une proposition."""
    comprise = comprehension_depuis_texte(json.dumps(RECETTES), [])

    assert comprise.questions == ()
    assert [p.cle for p in comprise.propositions] == [p["cle"] for p in PROPOSITIONS_NPM]
    assert not comprise.terminee
    question = comprise.question_suivante(rang=2)
    assert question is not None
    assert question.cle == SUJET_PROPOSITION and question.intitule == INTITULE_PROPOSITION
    assert question.options == () and "avec vos mots" in question.pourquoi
    tests = next(p for p in question.proposition if p.cle == "tester")
    assert (tests.valeur, tests.libelle, tests.pour) == ("npx vitest run", "Vitest", POUR_TESTS)
    forme = question.to_dict()["proposition"]
    assert {"cle": "tester", "sujet": "tests", "commande": True}.items() <= forme[3].items()
    assert forme[6]["commande"] is False and "pour" not in forme[6]


def test_un_sujet_est_un_constat_une_question_ou_une_proposition_jamais_deux() -> None:
    """Ce qui a été dit fait foi, ce qu'on demande ne se propose pas, et rien hors du schéma."""
    brut = {
        "constats": CONSTATS_RECETTES,
        "questions": [QUESTION_FORGE],
        "propositions": [
            _propose("langages", "JavaScript", "JavaScript", "constaté : TypeScript l'emporte"),
            _propose("forge", "gitlab", "GitLab", "demandée à part"),
            _propose("ci", ".gitlab-ci.yml", "GitLab CI", "tranchée d'un clic"),
            _propose("point_entree", "app/page.tsx", "page", "hors du schéma"),
            _propose("nature", "Un blog", "blog", "se dit, ne se propose pas"),
            _propose("clients", "codex", "Codex", "se trouve sur le poste"),
            _propose("tester", "npx vitest run", "Vitest", "la seule qui tienne"),
            _propose("tester", "npx jest", "Jest", "un doublon du même sujet"),
        ],
    }

    comprise = comprehension_depuis_texte(json.dumps(brut), [Choix("ci", "aucun")])

    assert [(p.cle, p.valeur) for p in comprise.propositions] == [("tester", "npx vitest run")]
    assert [q.cle for q in comprise.questions] == ["forge"]


def test_une_question_separee_passe_avant_la_proposition_et_dit_pourquoi() -> None:
    """Ce que le modèle ne sait pas juger se demande d'abord : la forge décide de la CI."""
    brut = {**RECETTES, "questions": [QUESTION_FORGE], "propositions": PROPOSITIONS_NPM[:6]}

    comprise = comprehension_depuis_texte(json.dumps(brut), [])

    question = comprise.question_suivante(rang=2)
    assert question is not None and question.cle == "forge" and not question.proposition
    assert "c'est à vous de la dire" in question.pourquoi
    assert len(comprise.propositions) == 6


def test_sans_la_sorte_de_projet_rien_ne_se_propose() -> None:
    """Proposer l'outillage d'un projet dont on ne sait pas ce qu'il est, c'est le deviner."""
    brut = {"constats": [], "propositions": PROPOSITIONS_NPM, "questions": []}

    comprise = comprehension_depuis_texte(json.dumps(brut), [])

    assert comprise.propositions == ()
    assert [q.cle for q in comprise.questions] == ["nature"]


def test_la_cle_de_la_proposition_ne_peut_pas_etre_celle_d_une_question() -> None:
    brut = {**RECETTES, "questions": [{**QUESTION_FORGE, "cle": SUJET_PROPOSITION}]}

    assert comprehension_depuis_texte(json.dumps(brut), []).questions == ()


def test_retenir_la_proposition_clique_chacun_de_ses_elements() -> None:
    """Un geste pèse autant que neuf réponses — et rien de ce qui suit ne sait qu'il est seul."""
    question = _proposition_de(RECETTES)

    retenus = choix_de_la_reponse(question, Choix(SUJET_PROPOSITION, PROPOSITION_RETENUE))

    assert [(c.cle, c.valeur) for c in retenus] == [
        (p["cle"], p["valeur"]) for p in PROPOSITIONS_NPM
    ]
    assert all(not c.deduit and not c.libre for c in retenus)
    tests = next(c for c in retenus if c.cle == "tester")
    assert tests.parce_que == "Rapide, et natif pour TypeScript." and tests.pour == POUR_TESTS
    # La recommandation du lot 2 les lit comme des clics : commande, cause, ce qu'elle fait.
    constats = constats_depuis_choix(retenus)
    commande = next(c for c in constats.commandes if c.usage == "tester")
    assert (commande.commande, commande.pour) == ("npx vitest run", POUR_TESTS)
    # Une phrase tapée sur la proposition, elle, reste une phrase : jamais un constat.
    tapee = Choix(SUJET_PROPOSITION, "pnpm, pas npm", libre=True)
    assert choix_de_la_reponse(question, tapee) == (tapee,)
    assert acquis_de([tapee]) == ()


def test_le_cadre_retenu_se_dit_dans_la_ligne_d_origine() -> None:
    """Sans place au schéma, un cadre proposé puis retenu ne s'écrivait nulle part."""
    compris = comprehension_depuis_texte(json.dumps(RECETTES), []).constats

    assert "Next.js" in resume_des_choix(compris)


def test_la_proposition_se_relit_a_l_identique_et_une_question_d_avant_aussi() -> None:
    question = _proposition_de(RECETTES)

    assert QuestionOutillage.from_dict(question.to_dict()) == question
    ordinaire = QuestionOutillage.from_dict(QUESTION_FORGE)
    assert "proposition" not in ordinaire.to_dict() and ordinaire.proposition == ()


def test_un_element_propose_qui_manque_au_poste_le_dit_et_fait_ressaisir_le_modele() -> None:
    comprise = comprehension_depuis_texte(json.dumps(RECETTES_PNPM), [])

    assert "pnpm" in programmes_a_sonder(comprise)
    assert repose_sur_un_absent(comprise, ["pnpm"])
    annotee = au_poste(comprise, {"pnpm": False})
    installer = next(p for p in annotee.propositions if p.cle == "installer")
    assert installer.absents == ("pnpm",)
    assert installer.raison.endswith("— `pnpm` n'est pas installé sur ce poste.")
    assert au_poste(annotee, {"pnpm": False}) == annotee  # idempotent


def _proposition_de(brut: Mapping[str, Any]) -> QuestionOutillage:
    question = comprehension_depuis_texte(json.dumps(brut), []).question_suivante(rang=2)
    assert question is not None and question.proposition
    return question


# --- ② Le conducteur : proposer, retenir ---------------------------------------------


def test_le_prompt_demande_de_proposer_d_un_bloc_et_de_ne_demander_que_l_injugeable() -> None:
    systeme = " ".join(_PROMPT_COMPREHENSION.split())

    assert "PROPOSE l'outillage entier" in systeme
    assert "Ne pose une QUESTION que pour ce que tu ne sais pas juger" in systeme
    assert "Un sujet est un constat, une proposition ou une question, jamais deux" in systeme
    assert '"propositions"' in systeme and f'"{SUJET_PROPOSITION}"' in systeme
    assert "Il n'y a pas de nombre de questions à atteindre" in systeme


def test_une_pile_comprise_recoit_tout_son_outillage_en_une_proposition() -> None:
    """Critère 1 : un projet neuf Next.js en TypeScript — une proposition, un seul tour."""
    faux = FauxModele(RECETTES)

    reponse = asyncio.run(_conducteur(faux).ouvrir([_dit(DESCRIPTION)]))

    question = _proposition(reponse)
    assert len(faux.prompts) == 1
    assert len(question.proposition) == len(PROPOSITIONS_NPM)
    # La bulle dit ce qui est proposé, par ses noms ; la carte en porte les raisons.
    assert reponse.contenu == (
        f"{INTITULE_PROPOSITION} : package.json, npm, npm install, Vitest, ESLint, "
        "Prettier, GitHub, GitHub Actions, Conventional Commits."
    )
    assert "Rapide" not in reponse.contenu
    assert {c.cle for c in reponse.comprehension} == {"nature", "langages", "cadre"}


def test_retenir_la_proposition_conclut_sans_rappeler_le_modele() -> None:
    """Le geste suffit : les éléments sont cliqués, et il n'y a rien à comprendre de plus."""
    faux = FauxModele(RECETTES)
    conducteur = _conducteur(faux)
    fil = [_dit(DESCRIPTION)]
    proposee = asyncio.run(conducteur.ouvrir(fil))
    fil += [_maestro(proposee), _retenir(_proposition(proposee))]

    fin = asyncio.run(conducteur.repondre(fil, _proposition(proposee), PROPOSITION_RETENUE))

    assert len(faux.prompts) == 1
    assert fin.question is None
    assert fin.contenu.startswith("C'est tout ce qu'il me fallait — Un livre de recettes")
    compris = {c.cle: c.valeur for c in fin.comprehension}
    assert compris["tester"] == "npx vitest run" and compris["gestionnaire"] == "npm"
    assert compris["cadre"] == "Next.js"
    assert {c.cle for c in choix_du_fil(fil)} >= {"tester", "lint", "formater", "ci"}


def test_la_proposition_retenue_ouvre_la_premiere_piece_avec_ce_qu_elle_proposait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rien ne s'écrit sans accord (#1161) : la proposition retenue, c'est la première pièce."""
    maison = tmp_path / "maison"
    maison.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: maison))
    projets = ServiceProjets(
        ProjetStore(tmp_path / "depot"), reglages=ReglagesProjetsStore(tmp_path / "reglages")
    )
    racine = maison / "Maestro" / "recettes"
    projet = str(projets.creer("recettes", str(racine), origine=ORIGINE_NOUVEAU)["id"])
    verificateur = Verificateur(joueur=_Joueur(), interprete=FAUX_BASH)
    faux = FauxModele(RECETTES)
    conducteur = ConducteurOutillage(
        ComprehensionModele(faux),
        pieces=ServicePieces(
            ServiceOutillage(projets, provider=_LecteurMuet()), projets, verificateur=verificateur
        ),
        verificateur=verificateur,
    )
    fil = [_dit(DESCRIPTION)]
    proposee = asyncio.run(conducteur.ouvrir(fil, projet_id=projet))
    fil += [_maestro(proposee), _retenir(_proposition(proposee))]

    suite = asyncio.run(conducteur.repondre(fil, _proposition(proposee), PROPOSITION_RETENUE))

    assert len(faux.prompts) == 1
    assert suite.piece is not None and suite.piece.chemin == "AGENTS.md"
    assert "npx vitest run" in suite.piece.contenu and "npx eslint ." in suite.piece.contenu
    assert "Next.js" in suite.piece.contenu
    assert suite.contenu.startswith("C'est tout ce qu'il me fallait")
    assert not racine.exists() or list(racine.iterdir()) == []  # rien n'est écrit


# --- ③ La correction en mots --------------------------------------------------------


def test_une_correction_en_mots_redit_la_proposition_au_modele_et_revient_reverifiee() -> None:
    """Critère 1 : « pnpm, pas npm » — comprise, appliquée, et revérifiée au poste."""
    faux = FauxModele(RECETTES, RECETTES_PNPM)
    joueur = _Joueur({sonde("pnpm"): INTROUVABLE})
    conducteur = _conducteur(faux, joueur)
    fil = [_dit(DESCRIPTION)]
    proposee = asyncio.run(conducteur.ouvrir(fil))
    question = _proposition(proposee)
    fil += [
        _maestro(proposee),
        _dit("pnpm, pas npm", choix=Choix(question.cle, "pnpm, pas npm", libre=True)),
    ]

    corrigee = asyncio.run(conducteur.repondre(fil, question, "pnpm, pas npm"))

    # Le modèle relit ce qu'il corrige : il ne se souvient de rien d'un tour à l'autre.
    correction = faux.prompts[1]
    assert f"- {SUJET_PROPOSITION} (tapée) : « pnpm, pas npm »" in correction
    assert "## La proposition que la personne corrige" in correction
    assert "- gestionnaire : « npm » — Livré avec Node" in correction
    # Revérifiée : `pnpm` est cherché sur le poste, absent, et le modèle est ressaisi.
    assert sonde("pnpm") in joueur.joues
    assert len(faux.prompts) == 3 and "- `pnpm` : introuvable sur ce poste" in faux.prompts[2]
    nouvelle = _proposition(corrigee)
    installer = next(p for p in nouvelle.proposition if p.cle == "installer")
    assert installer.valeur == "pnpm install" and installer.absents == ("pnpm",)
    assert {c.cle: c.valeur for c in corrigee.comprehension}["gestionnaire"] == "pnpm"
    assert "gestionnaire" not in {p.cle for p in nouvelle.proposition}


def test_une_proposition_ordinaire_ne_se_redit_pas_au_modele() -> None:
    """Seule une phrase tapée **sur** la proposition la corrige."""
    faux = FauxModele({**RECETTES, "questions": [QUESTION_FORGE]}, RECETTES)
    conducteur = _conducteur(faux)
    fil = [_dit(DESCRIPTION)]
    posee = asyncio.run(conducteur.ouvrir(fil))
    assert posee.question is not None and posee.question.cle == "forge"
    fil += [_maestro(posee), _dit("Où ? → GitHub", choix=Choix("forge", "github"))]

    suite = asyncio.run(conducteur.repondre(fil, posee.question, "github"))

    assert "La proposition que la personne corrige" not in faux.prompts[1]
    assert _proposition(suite).rang == 2


# --- ④ Les routes du fil, et le banc ------------------------------------------------


@pytest.fixture()
def faux_recettes() -> FauxModele:
    return FauxModele(RECETTES, RECETTES_PNPM)


@pytest.fixture()
def client_chat(tmp_path: Path, faux_recettes: FauxModele) -> Iterator[TestClient]:
    app = create_app(
        bus=InMemoryEventBus(),
        chat_store=ChatStore(tmp_path / "chat"),
        chat_repondeur=RepondeurScripte(conducteur=_conducteur(faux_recettes)),
    )
    with TestClient(app) as client:
        client.post("/api/chat/qa/outillage/questionnaire")
        yield client


def _decrire(client: TestClient) -> dict[str, Any]:
    reponse = client.post("/api/chat/qa/outillage", json={"valeur": DESCRIPTION, "libre": True})
    assert reponse.status_code == 201, reponse.text
    suite = reponse.json()["messages"][1]
    assert suite["question"]["cle"] == SUJET_PROPOSITION
    return dict(suite)


def test_la_route_sert_la_proposition_sur_le_message(client_chat: TestClient) -> None:
    suite = _decrire(client_chat)

    question = suite["question"]
    assert question["options"] == [] and question["intitule"] == INTITULE_PROPOSITION
    assert [p["libelle"] for p in question["proposition"]][:4] == [
        "package.json",
        "npm",
        "npm install",
        "Vitest",
    ]
    assert question["proposition"][3]["sujet"] == "tests"


def test_la_route_retient_la_proposition_d_un_geste_et_le_fil_le_dit(
    client_chat: TestClient, faux_recettes: FauxModele
) -> None:
    """Un geste, et la conclusion vient — sans second appel au modèle."""
    _decrire(client_chat)

    reponse = client_chat.post("/api/chat/qa/outillage", json={"valeur": PROPOSITION_RETENUE})

    assert reponse.status_code == 201, reponse.text
    geste, fin = reponse.json()["messages"]
    assert geste["contenu"].startswith(f"{INTITULE_PROPOSITION} → retenu tel quel : package")
    assert geste["choix"]["valeur"] == PROPOSITION_RETENUE
    assert fin["question"] is None
    compris = {c["cle"]: c["valeur"] for c in fin["comprehension"]}
    assert compris["tester"] == "npx vitest run" and compris["ci"] == ".github/workflows/ci.yml"
    assert len(faux_recettes.prompts) == 1
    tardif = client_chat.post("/api/chat/qa/outillage", json={"valeur": PROPOSITION_RETENUE})
    assert tardif.status_code == 409


def test_la_proposition_ne_se_repond_que_par_le_geste_qui_la_retient(
    client_chat: TestClient,
) -> None:
    _decrire(client_chat)

    reponse = client_chat.post("/api/chat/qa/outillage", json={"valeur": "npm"})

    assert reponse.status_code == 422, reponse.text
    assert "se retient telle quelle" in reponse.json()["detail"]
    assert "avec vos mots" in reponse.json()["detail"]


def test_une_phrase_tapee_pendant_la_proposition_la_corrige(
    client_chat: TestClient, faux_recettes: FauxModele
) -> None:
    """La zone de saisie répond aussi : la phrase part au questionnaire, pas au juge."""
    _decrire(client_chat)

    reponse = client_chat.post("/api/chat/qa/messages", json={"contenu": "pnpm, pas npm"})

    assert reponse.status_code == 201, reponse.text
    message, suite = reponse.json()["messages"]
    assert message["choix"]["cle"] == SUJET_PROPOSITION and message["choix"]["libre"] is True
    assert "## La proposition que la personne corrige" in faux_recettes.prompts[1]
    gestionnaire = {c["cle"]: c["valeur"] for c in suite["comprehension"]}["gestionnaire"]
    assert gestionnaire == "pnpm"
    assert suite["question"]["proposition"][1]["valeur"] == "pnpm install"


def test_le_banc_sans_avis_retient_la_proposition_d_un_geste() -> None:
    """La personne sans avis des scénarios prend la recommandation — ici faite d'un bloc."""
    question = _proposition_de(RECETTES).to_dict()

    assert _reponse_a(question) == (PROPOSITION_RETENUE, False)
    assert _reponse_a(QuestionOutillage.from_dict(QUESTION_FORGE).to_dict()) == ("github", False)

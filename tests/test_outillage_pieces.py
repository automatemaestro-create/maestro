"""L'outillage se construit dans la conversation, pièce par pièce, et se corrige en
langage naturel (#1161, docs/43 §2.2).

Les deux premiers critères du ticket, et ce qui les tient :

① **l'outillage se propose pièce par pièce, et chaque pièce s'écrit sur accord** —
   dans la conversation d'un projet **neuf** (il naît, le questionnaire comprend ce qui
   a été dit, puis la première pièce vient) comme d'un projet **importé** (il se lit,
   puis la première pièce vient). Une pièce est montrée avec ce qui changera sur le
   disque, déjà vérifiée par l'exécution ; l'accord écrit **elle seule**, et le
   manifeste garde les pièces d'avant ;
② **une correction dite avec des mots est comprise, appliquée, puis revérifiée** —
   « Nos tests tournent avec `dotnet test` » change la commande de tests, la pièce
   qu'elle touche revient avec la phrase pour justification, et la commande est jouée ;
   une correction **incomprise** ou **en échec** le dit, et rien ne s'écrit.

Étagés comme le reste de l'outillage : le moteur (`maestro.outillage` — prévoir,
écrire une pièce, corriger), le service (`maestro.controltower.pieces`), le canal
(conducteur et orchestration), la route. Le modèle est un double partout — le juge,
la compréhension d'un projet neuf, celle d'une correction ; ce qu'il comprend vraiment
se mesure sur la vraie stack. Les commandes sont jouées par un joueur doublé
(`Verificateur(joueur=…)`), jamais par le bash du poste.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from maestro.controltower import ControlTowerState, InMemoryEventBus, create_app
from maestro.controltower.chat import (
    DECISION_ECRIRE,
    DECISION_PASSER,
    DECISION_PLUS_TARD,
    FAIT_DE_LA_DEMANDE,
    ORIGINE_NOUVEAU,
    UTILISATEUR,
    ChatStore,
    MessageChat,
    PieceEcrite,
    PieceProposee,
    corrections_du_fil,
    piece_en_attente,
    pieces_tranchees,
    projet_du_fil,
    transcription,
)
from maestro.controltower.naissance import ServiceNaissance
from maestro.controltower.orchestration import (
    _MARQUEUR_VERDICT,
    _PROMPT_ORCHESTRATION,
    _PROMPT_REDACTION,
    AGENT_ORCHESTRATION,
    NOM_ORCHESTRATION,
    VERDICT_ACCORD,
    VERDICT_ECHANGE,
    VERDICT_OUTILLAGE,
    RepondeurOrchestration,
)
from maestro.controltower.outillage import (
    _PROMPT_COMPREHENSION,
    ComprehensionModele,
    ConducteurOutillage,
    ServiceOutillage,
)
from maestro.controltower.pieces import (
    _PROMPT_CORRECTION,
    AccordDeLaCarte,
    CorrectionModele,
    PieceChangee,
    ServicePieces,
    piece_ecartee,
)
from maestro.controltower.projets import ServiceProjets
from maestro.engine.guardrails import DemandeValidation
from maestro.outillage import CHEMIN_MANIFESTE, Commande, Constats, Gestionnaire, recommander
from maestro.outillage.clients import SOURCE_POSTE, Client
from maestro.outillage.correction import corriger, lire_correction
from maestro.outillage.generation import poser_piece, prevoir
from maestro.outillage.modele import ORIGINE_DITE
from maestro.outillage.questionnaire import Choix
from maestro.outillage.recommandation import RAISON_AGENTS
from maestro.outillage.redaction import Fichier, rediger
from maestro.outillage.verification import A_VERIFIER, ECHOUEE, VERIFIEE, Verificateur, Verification
from maestro.projets import ProjetStore
from maestro.projets.application import DiffProjet, Modification
from maestro.projets.modele import Perimetre
from maestro.projets.reglages import ReglagesProjetsStore
from maestro.providers.base import ModelProvider
from maestro.sandbox import verification as execution

GIT = shutil.which("git")
avec_git = pytest.mark.skipif(GIT is None, reason="git introuvable")

#: La correction du critère 2, mot pour mot.
DOTNET = "Nos tests tournent avec `dotnet test`"

#: Un interpréteur factice : le joueur doublé ne le lance jamais.
FAUX_BASH = ("bash", "-c")

#: Ce que la rédaction d'un geste rend — un texte qu'aucun gabarit du code ne produit.
REDIGE = "C'est fait, et voici la suite."

#: La pièce qui suit `AGENTS.md` sur un poste nu (celui de la suite, `tests/conftest.py`) :
#: aucun client d'agent, donc aucun pont (#1295) — le premier skill vient tout de suite.
SKILL_ROUTE = ".agents/skills/mettre-en-route/SKILL.md"


@pytest.fixture(autouse=True)
def _maison(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un dossier utilisateur factice — sous Windows, `tmp_path` est dans `AppData` (#221)."""
    maison = tmp_path / "maison"
    maison.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: maison))
    return maison


@pytest.fixture()
def _git_isole(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Coupe la configuration Git globale du poste (#333), comme `test_projets_api`."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig-absent"))
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Maestro Test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "test@maestro.invalid")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Maestro Test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "test@maestro.invalid")


@pytest.fixture()
def projets(tmp_path: Path) -> ServiceProjets:
    """Le service des projets sur un dépôt et des réglages jetables — jamais ceux du poste."""
    return ServiceProjets(
        ProjetStore(tmp_path / "depot"), reglages=ReglagesProjetsStore(tmp_path / "reglages")
    )


# --------------------------------------------------------------------------- #
# Doubles                                                                      #
# --------------------------------------------------------------------------- #


class _Joueur:
    """Un joueur doublé : un résultat par commande, et ce qu'il a joué, dans l'ordre."""

    def __init__(self, resultats: dict[str, execution.Execution] | None = None) -> None:
        self._resultats = resultats or {}
        self.joues: list[str] = []
        self.dossiers: list[Path] = []

    def __call__(
        self, commande: str, cwd: Path, *, interprete: Any, delai_s: float
    ) -> execution.Execution:
        self.joues.append(commande)
        self.dossiers.append(Path(cwd))
        return self._resultats.get(commande, execution.Execution(code=0, sortie="ok", duree_s=0.1))


class _LecteurMuet(ModelProvider):
    """Le modèle qui lit un projet importé, muet : l'analyse reste celle des tables (#1158)."""

    name = "lecteur-muet"

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt: str, *, model: str, system_prompt: str | None = None) -> str:
        raise RuntimeError("aucun modèle dans ce test")


class _Modele(ModelProvider):
    """Un modèle doublé qui répond selon le prompt système — et retient ce qu'on lui a dit.

    Le juge rend ses verdicts dans l'ordre, la rédaction rend `REDIGE`, la
    compréhension d'un projet neuf et celle d'une correction rendent l'objet JSON
    qu'on leur a dicté.
    """

    name = "modele-double"

    def __init__(
        self,
        *verdicts: str,
        comprehension: Mapping[str, Any] | None = None,
        correction: Mapping[str, Any] | None = None,
    ) -> None:
        self.verdicts = list(verdicts)
        self.comprehension = dict(comprehension or {"constats": [], "questions": []})
        self.correction = dict(correction or {"comprise": True, "corrections": []})
        self.appels: dict[str, list[str]] = {
            "juge": [],
            "redaction": [],
            "comprehension": [],
            "correction": [],
        }

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt: str, *, model: str, system_prompt: str | None = None) -> str:
        if system_prompt == _PROMPT_REDACTION:
            self.appels["redaction"].append(prompt)
            return REDIGE
        if system_prompt == _PROMPT_COMPREHENSION:
            self.appels["comprehension"].append(prompt)
            return json.dumps(self.comprehension, ensure_ascii=False)
        if system_prompt == _PROMPT_CORRECTION:
            self.appels["correction"].append(prompt)
            return json.dumps(self.correction, ensure_ascii=False)
        self.appels["juge"].append(prompt)
        return self.verdicts.pop(0) if self.verdicts else _dicte("…", VERDICT_ECHANGE)


def _dicte(reponse: str, nom: str, **charge: Any) -> str:
    """Le contrat du juge : la prose, puis la dernière ligne marquée."""
    objet = {"verdict": nom, "objectif": "", **charge}
    return f"{reponse}\n{_MARQUEUR_VERDICT} {json.dumps(objet, ensure_ascii=False)}"


def _message(auteur: str, contenu: str, **champs: Any) -> MessageChat:
    return MessageChat(agent=NOM_ORCHESTRATION, auteur=auteur, contenu=contenu, **champs)


# --------------------------------------------------------------------------- #
# Fabriques                                                                    #
# --------------------------------------------------------------------------- #


def _projet_node(maison: Path) -> Path:
    """Un projet existant : un `package.json` qui déclare ses tests et sa construction."""
    racine = maison / "sites" / "depensio"
    (racine / "src").mkdir(parents=True)
    (racine / "package.json").write_text(
        json.dumps({"name": "depensio", "scripts": {"test": "jest", "build": "tsc"}}),
        encoding="utf-8",
    )
    (racine / "src" / "index.js").write_text("console.log('depensio')\n", encoding="utf-8")
    return racine


def _service(
    projets: ServiceProjets,
    joueur: _Joueur,
    modele: _Modele | None = None,
    *,
    poste: tuple[Client, ...] | None = None,
) -> ServicePieces:
    """Le service des pièces, commandes jouées par le joueur doublé.

    `poste` : les clients d'agents du poste, doublés (#1295) ; `None` laisse le poste nu
    de la suite (`tests/conftest.py`).
    """
    return ServicePieces(
        ServiceOutillage(
            projets,
            provider=_LecteurMuet(),
            clients=(lambda: poste) if poste is not None else None,
        ),
        projets,
        verificateur=Verificateur(joueur=joueur, interprete=FAUX_BASH),
        correction=CorrectionModele(modele or _Modele()),
    )


def _importe(projets: ServiceProjets, maison: Path) -> str:
    """Déclare le projet Node existant et rend son identifiant."""
    return str(projets.creer("Dépensio", str(_projet_node(maison)))["id"])


def _manifeste(racine: Path) -> dict[str, Any]:
    return json.loads((racine / CHEMIN_MANIFESTE).read_text(encoding="utf-8"))


def _fil_d_une_piece(piece: PieceProposee, *suite: MessageChat) -> list[MessageChat]:
    """Le fil tel qu'il est quand une pièce attend : le message qui la porte, puis la suite."""
    return [_message(NOM_ORCHESTRATION, "Voici une pièce.", piece=piece), *suite]


# --------------------------------------------------------------------------- #
# ① Le moteur : prévoir, écrire une pièce, ne pas rejouer, corriger             #
# --------------------------------------------------------------------------- #


def test_prevoir_dit_ce_qu_ecrire_ferait_sans_rien_ecrire(tmp_path: Path) -> None:
    racine = tmp_path / "p"
    racine.mkdir()
    fichier = Fichier(chemin="AGENTS.md", role="instructions", portee="fichier", contenu="# A\n")

    prevision = prevoir(racine, fichier)

    assert prevision.ecrirait and prevision.avant is None and prevision.apres == "# A\n"
    assert not (racine / "AGENTS.md").exists()


def test_prevoir_un_fichier_du_projet_n_est_pas_touche(tmp_path: Path) -> None:
    racine = tmp_path / "p"
    racine.mkdir()
    (racine / "CLAUDE.md").write_text("à moi\n", encoding="utf-8")
    fichier = Fichier(chemin="CLAUDE.md", role="pont", portee="fichier", contenu="@AGENTS.md")

    prevision = prevoir(racine, fichier)

    assert prevision.etat == "ignore" and not prevision.ecrirait and prevision.apres is None


def test_prevoir_un_bloc_montre_le_fichier_entier_apres_fusion(tmp_path: Path) -> None:
    """Le diff d'une carte compare des **fichiers** : le bloc ajouté se voit dans le sien."""
    racine = tmp_path / "p"
    racine.mkdir()
    # `newline=""` : sous Windows, `write_text` écrirait des `\r\n` — le texte lu serait autre.
    (racine / "AGENTS.md").write_text("# Nos règles\n\nÀ nous.\n", encoding="utf-8", newline="")
    fichier = Fichier(chemin="AGENTS.md", role="instructions", portee="bloc", contenu="Maestro.")

    prevision = prevoir(racine, fichier)

    assert prevision.ecrirait and prevision.bloc
    assert prevision.avant == "# Nos règles\n\nÀ nous.\n"
    assert prevision.apres is not None and prevision.apres.startswith("# Nos règles\n\nÀ nous.")
    assert "Maestro." in prevision.apres and prevision.apres.count("À nous.") == 1


def test_poser_une_piece_garde_au_manifeste_les_pieces_deja_ecrites(tmp_path: Path) -> None:
    """`generer` retirerait du manifeste ce qu'on ne lui repasse pas : une pièce seule, non."""
    racine = tmp_path / "p"
    racine.mkdir()
    source = {"type": "analyse", "projet_id": "p", "reference": "ana-1", "resume": ""}
    tests = Verification(usage="tester", commande="npm test", etat=VERIFIEE, raison="ok")
    build = Verification(usage="construire", commande="npm run build", etat=ECHOUEE, raison="ko")
    agents = Fichier(chemin="AGENTS.md", role="instructions", portee="fichier", contenu="# A\n")
    skill = Fichier(
        chemin=".agents/skills/lancer-les-tests/SKILL.md",
        role="skill",
        portee="fichier",
        contenu="---\nname: lancer-les-tests\n---\n",
    )

    premiere = poser_piece(racine, agents, source=source, verifications=(tests,))
    seconde = poser_piece(racine, skill, source=source, verifications=(build,))

    assert [e.etat for e in premiere.ecritures] == ["ecrit"]
    assert [e.etat for e in seconde.ecritures] == ["ecrit"]
    manifeste = _manifeste(racine)
    assert {e["chemin"] for e in manifeste["entrees"]} == {agents.chemin, skill.chemin}
    assert {v["commande"] for v in manifeste["verifications"]} == {"npm test", "npm run build"}


def test_une_piece_remise_en_crlf_reste_a_maestro_et_garde_ses_fins_de_ligne(
    tmp_path: Path,
) -> None:
    """Vu sur la vraie stack sous Windows : ce que Maestro écrit en LF, `core.autocrlf` le
    remet en CRLF au checkout. Compté comme une modification, le fichier n'était plus à
    Maestro, et la pièce corrigée ne revenait jamais — sans un mot."""
    racine = tmp_path / "p"
    racine.mkdir()
    source = {"type": "analyse", "projet_id": "p", "reference": "ana-1", "resume": ""}
    ecrit = Fichier(
        chemin="AGENTS.md", role="instructions", portee="fichier", contenu="# A\n\nnpm\n"
    )
    corrige = Fichier(
        chemin="AGENTS.md", role="instructions", portee="fichier", contenu="# A\n\nuv\n"
    )
    poser_piece(racine, ecrit, source=source)
    (racine / "AGENTS.md").write_bytes(b"# A\r\n\r\nnpm\r\n")

    prevision = prevoir(racine, corrige)

    assert prevision.etat == "ecrit" and prevision.ecrirait
    # Le diff qu'une carte montre compare des lignes, pas des fins de ligne.
    assert prevision.avant == "# A\n\nnpm\n"
    assert prevoir(racine, ecrit).etat == "inchange"
    rapport = poser_piece(racine, corrige, source=source)
    assert [e.etat for e in rapport.ecritures] == ["ecrit"]
    # La réécriture garde les fins de ligne du fichier en place : rien ne défait ce choix.
    assert (racine / "AGENTS.md").read_bytes() == b"# A\r\n\r\nuv\r\n"
    assert prevoir(racine, corrige).etat == "inchange"


def test_une_commande_deja_jouee_ne_se_rejoue_pas_mais_un_a_verifier_si(tmp_path: Path) -> None:
    racine = tmp_path / "p"
    racine.mkdir()
    (racine / "package.json").write_text("{}", encoding="utf-8")
    constats = Constats(
        commandes=(
            Commande(usage="installer", commande="npm install", chemin="package.json"),
            Commande(usage="tester", commande="npm test", chemin="package.json"),
        )
    )
    joueur = _Joueur()
    connues = {
        "npm test": Verification(usage="tester", commande="npm test", etat=VERIFIEE, raison="vu"),
        "npm install": Verification(
            usage="installer", commande="npm install", etat=A_VERIFIER, raison="pas jouée"
        ),
    }

    verdicts = Verificateur(joueur=joueur, interprete=FAUX_BASH).verifier(
        racine, constats, recommander(constats), perimetre=Perimetre(), connues=connues
    )

    assert joueur.joues == ["npm install"]
    assert {v.commande: v.raison for v in verdicts}["npm test"] == "vu"


def test_lire_correction_pose_la_phrase_en_cause_et_ecarte_le_hors_schema() -> None:
    texte = json.dumps(
        {
            "comprise": True,
            "corrections": [
                {"cle": "tester", "valeur": "dotnet test"},
                {"cle": "couleur-du-logo", "valeur": "vert"},
                {"cle": "tests", "valeur": "dotnet test --no-build"},
            ],
            "message": "",
        }
    )

    lue = lire_correction(texte, DOTNET)

    assert lue.comprise
    # « tests » est le nom d'écran de `tester` : ramené à sa clé, le dernier l'emporte.
    assert [(c.cle, c.valeur) for c in lue.corrections] == [("tester", "dotnet test --no-build")]
    assert lue.corrections[0].parce_que == DOTNET and lue.corrections[0].deduit


def test_lire_correction_incomprise_ne_corrige_rien() -> None:
    texte = json.dumps(
        {
            "comprise": False,
            "corrections": [{"cle": "tester", "valeur": "?"}],
            "message": "Je ne vois pas quelle commande lance vos tests.",
        }
    )

    lue = lire_correction(texte, "ça ne marche pas comme ça")

    assert not lue.comprise and lue.corrections == ()
    assert lue.message == "Je ne vois pas quelle commande lance vos tests."


def _constats_node() -> Constats:
    return Constats(
        gestionnaires=(Gestionnaire(nom="npm", chemin="package.json", installer="npm install"),),
        commandes=(
            Commande(usage="installer", commande="npm install", chemin="package.json"),
            Commande(usage="tester", commande="npm run test", chemin="package.json"),
        ),
    )


def test_corriger_remplace_la_commande_et_la_dit_de_la_personne() -> None:
    """Critère 2 : l'entrée corrigée porte **la phrase de la personne** pour justification."""
    correction = Choix(cle="tester", valeur="dotnet test", deduit=True, parce_que=DOTNET)

    corriges = corriger(_constats_node(), [correction])

    tests = corriges.commande_de("tester")
    assert tests is not None and tests.commande == "dotnet test"
    assert tests.origine == ORIGINE_DITE and tests.extrait == f"« {DOTNET} »"
    assert tests.chemin == "package.json"
    assert [c.usage for c in corriges.commandes] == ["installer", "tester"]
    fichiers = {f.chemin: f.contenu for f in rediger(corriges, recommander(corriges))}
    assert f"`dotnet test` — dite par la personne (« {DOTNET} »)" in fichiers["AGENTS.md"]
    skill = fichiers[".agents/skills/lancer-les-tests/SKILL.md"]
    assert "dotnet test" in skill
    assert f"Dite par la personne qui a outillé ce projet : « {DOTNET} »" in skill
    assert "Constaté dans" not in skill


def test_corriger_aucun_retire_la_commande_et_le_skill_s_ecarte() -> None:
    aucun = Choix(cle="tester", valeur="aucun", deduit=True, parce_que="pas de tests")

    sans = corriger(_constats_node(), [aucun])

    assert sans.commande_de("tester") is None
    assert "lancer-les-tests" in {e.nom for e in recommander(sans).ecartes}


def test_corriger_ignore_ce_qu_un_projet_lu_ne_corrige_pas() -> None:
    """La part d'un langage est une mesure : la remplacer par ce qu'on nous dit la falsifierait."""
    constats = _constats_node()

    assert corriger(constats, [Choix(cle="langages", valeur="C#", deduit=True)]) is constats


# --------------------------------------------------------------------------- #
# ② Le service : proposer, écrire, passer, revérifier                          #
# --------------------------------------------------------------------------- #


def test_un_projet_importe_propose_sa_premiere_piece_verifiee_sans_rien_ecrire(
    projets: ServiceProjets, _maison: Path
) -> None:
    """Critère 1, projet importé : lu, puis sa première pièce, montrée avec ce qui changera."""
    projet_id = _importe(projets, _maison)
    racine = projets.entite(projet_id).racine_chemin
    joueur = _Joueur()

    piece = asyncio.run(_service(projets, joueur).prochaine(projet_id, []))

    assert piece is not None and piece.chemin == "AGENTS.md" and piece.nature == "instructions"
    assert piece.sort == "cree" and piece.texte_avant == "" and piece.empreinte_avant == ""
    assert piece.texte_apres == piece.contenu and "npm run test" in piece.contenu
    # Rédigée avec **toute** la recommandation : AGENTS.md désigne les skills à venir.
    assert "`.agents/skills/lancer-les-tests/SKILL.md`" in piece.contenu
    # Un poste nu (celui de la suite) : aucun client, aucun pont (#1295) — AGENTS.md et
    # les trois skills.
    assert (piece.rang, piece.total) == (1, 4)
    assert piece.ecrivable and piece.regime == "en-place" and piece.projet_nom == "Dépensio"
    # Vérifiée **avant** d'être montrée, et dans une copie : la racine n'a rien reçu.
    assert set(joueur.joues) == {"npm install", "npm run build", "npm run test"}
    assert all(dossier != racine for dossier in joueur.dossiers)
    assert {v.etat for v in piece.verifications} == {VERIFIEE}
    assert not (racine / "AGENTS.md").exists() and not (racine / ".maestro").exists()


def test_ecrire_une_piece_n_ecrit_qu_elle_et_la_suivante_vient_sans_rien_rejouer(
    projets: ServiceProjets, _maison: Path
) -> None:
    projet_id = _importe(projets, _maison)
    racine = projets.entite(projet_id).racine_chemin
    joueur = _Joueur()
    service = _service(projets, joueur)
    agents = asyncio.run(service.prochaine(projet_id, []))
    assert agents is not None
    joues_avant = list(joueur.joues)

    fait = asyncio.run(service.ecrire(agents))
    fil = _fil_d_une_piece(agents, _message(NOM_ORCHESTRATION, "Écrit.", piece_ecrite=fait))
    suivante = asyncio.run(service.prochaine(projet_id, fil))

    assert fait.ecrite and fait.etat == "ecrit" and fait.empreinte
    assert (racine / "AGENTS.md").read_text(encoding="utf-8") == agents.texte_apres
    assert [e["chemin"] for e in _manifeste(racine)["entrees"]] == ["AGENTS.md"]
    # Seule la pièce écrite : aucun skill n'a atteint le disque.
    assert not (racine / ".agents").exists()
    assert suivante is not None and suivante.chemin == SKILL_ROUTE and suivante.rang == 2
    # Les commandes qu'AGENTS.md a fait jouer ne se rejouent pas pour les pièces suivantes.
    assert joueur.joues == joues_avant


def test_passer_une_piece_ne_l_ecrit_pas_et_elle_ne_revient_pas_telle_quelle(
    projets: ServiceProjets, _maison: Path
) -> None:
    projet_id = _importe(projets, _maison)
    racine = projets.entite(projet_id).racine_chemin
    service = _service(projets, _Joueur())
    agents = asyncio.run(service.prochaine(projet_id, []))
    assert agents is not None

    ecartee = piece_ecartee(agents)
    fil = _fil_d_une_piece(agents, _message(NOM_ORCHESTRATION, "Passée.", piece_ecrite=ecartee))
    suivante = asyncio.run(service.prochaine(projet_id, fil))

    assert not ecartee.ecrite and ecartee.etat == "ecartee"
    assert pieces_tranchees(fil) == {("AGENTS.md", ecartee.empreinte)}
    assert suivante is not None and suivante.chemin == SKILL_ROUTE
    assert not (racine / "AGENTS.md").exists()


def test_les_ponts_des_pieces_suivent_les_clients_du_poste_et_ceux_qu_on_nomme(
    projets: ServiceProjets, _maison: Path
) -> None:
    """Un pont ne s'écrit que pour un client utilisé qui ne lit pas `AGENTS.md` (#1295).

    La pièce par pièce le tient comme l'analyse et le questionnaire, par le même
    `recommander` : les clients du **poste** pour un projet lu, et pour un projet décrit,
    ceux que la personne a **nommés** en plus. Sans eux, la fusion de #1295 avait laissé
    ce chemin-ci sans aucun pont, Gemini CLI installé ou non.
    """
    gemini = Client(cle="gemini", libelle="Gemini CLI", version="0.9.0", source=SOURCE_POSTE)
    projet_id = _importe(projets, _maison)
    service = _service(projets, _Joueur(), poste=(gemini,))
    agents = asyncio.run(service.prochaine(projet_id, []))
    assert agents is not None and agents.chemin == "AGENTS.md" and agents.total == 5

    passee = _message(NOM_ORCHESTRATION, "Passée.", piece_ecrite=piece_ecartee(agents))
    suivante = asyncio.run(service.prochaine(projet_id, _fil_d_une_piece(agents, passee)))

    assert suivante is not None and suivante.chemin == "GEMINI.md" and suivante.rang == 2
    assert "@AGENTS.md" in suivante.texte_apres

    # Un projet décrit, sur un poste nu : c'est la personne qui nomme Gemini CLI.
    racine = _maison / "Maestro" / "padel"
    neuf = str(projets.creer("padel", str(racine), origine=ORIGINE_NOUVEAU)["id"])
    decrit = [Choix(cle="langages", valeur="Dart")]
    nomme = [*decrit, Choix(cle="clients", valeur="Gemini CLI")]
    sans = asyncio.run(_service(projets, _Joueur()).prochaine(neuf, [], acquis=decrit))
    avec = asyncio.run(_service(projets, _Joueur()).prochaine(neuf, [], acquis=nomme))
    assert sans is not None and avec is not None
    assert avec.total == sans.total + 1


def test_revenir_a_une_version_deja_ecrite_puis_remplacee_la_repropose(
    projets: ServiceProjets, _maison: Path
) -> None:
    """Vu sur la vraie stack : « retire dotnet test » ramenait `AGENTS.md` à sa version
    d'avant, déjà écrite une fois — et la pièce ne revenait pas : `(chemin, empreinte)`
    comptait comme tranché, quoi qu'on ait écrit depuis à ce chemin."""
    projet_id = _importe(projets, _maison)
    racine = projets.entite(projet_id).racine_chemin
    service = _service(projets, _Joueur())
    v1 = asyncio.run(service.prochaine(projet_id, []))
    assert v1 is not None and v1.chemin == "AGENTS.md"
    ecrit_v1 = asyncio.run(service.ecrire(v1))
    dotnet = lire_correction(
        json.dumps({"comprise": True, "corrections": [{"cle": "tester", "valeur": "dotnet test"}]}),
        DOTNET,
    ).corrections
    fil = _fil_d_une_piece(v1, _message(NOM_ORCHESTRATION, "Écrit.", piece_ecrite=ecrit_v1))
    v2 = asyncio.run(service.prochaine(projet_id, fil, corrections=dotnet))
    assert v2 is not None and v2.chemin == "AGENTS.md" and v2.empreinte != v1.empreinte
    ecrit_v2 = asyncio.run(service.ecrire(v2))
    fil.append(_message(NOM_ORCHESTRATION, "Corrigé, écrit.", piece_ecrite=ecrit_v2))

    retour = asyncio.run(service.prochaine(projet_id, fil))

    assert retour is not None and retour.chemin == "AGENTS.md" and retour.sort == "reecrit"
    assert retour.empreinte == v1.empreinte
    assert "dotnet test" in (racine / "AGENTS.md").read_text(encoding="utf-8")
    # Ce qui compte d'un chemin est la **dernière** décision prise sur lui.
    assert pieces_tranchees(fil) == {("AGENTS.md", ecrit_v2.empreinte)}


def test_une_piece_deja_ecrite_par_maestro_dit_la_raison_de_ce_que_le_geste_fera(
    projets: ServiceProjets, _maison: Path
) -> None:
    """Vu sur la vraie stack, sur un projet repris : la carte disait « le projet porte déjà
    un AGENTS.md : Maestro n'y écrirait qu'un bloc délimité » à côté de « fichier de
    Maestro modifié ». La raison de l'analyse suit l'état du projet ; celle de la carte
    dit ce que le geste fera."""
    projet_id = _importe(projets, _maison)
    service = _service(projets, _Joueur())
    agents = asyncio.run(service.prochaine(projet_id, []))
    assert agents is not None
    asyncio.run(service.ecrire(agents))
    reprise = _service(projets, _Joueur())  # une conversation neuve : le projet se relit
    dotnet = lire_correction(
        json.dumps({"comprise": True, "corrections": [{"cle": "tester", "valeur": "dotnet test"}]}),
        DOTNET,
    ).corrections

    piece = asyncio.run(reprise.prochaine(projet_id, [], corrections=dotnet))

    assert piece is not None and piece.chemin == "AGENTS.md" and piece.sort == "reecrit"
    assert piece.raison == RAISON_AGENTS


def test_une_piece_dont_le_fichier_a_bouge_depuis_la_carte_ne_s_ecrit_pas(
    projets: ServiceProjets, _maison: Path
) -> None:
    projet_id = _importe(projets, _maison)
    racine = projets.entite(projet_id).racine_chemin
    service = _service(projets, _Joueur())
    agents = asyncio.run(service.prochaine(projet_id, []))
    assert agents is not None
    (racine / "AGENTS.md").write_text("écrit à la main entre-temps\n", encoding="utf-8")

    with pytest.raises(PieceChangee):
        asyncio.run(service.ecrire(agents))

    assert (racine / "AGENTS.md").read_text(encoding="utf-8") == "écrit à la main entre-temps\n"


def _apres_agents_ecrit(
    projets: ServiceProjets, maison: Path, joueur: _Joueur, modele: _Modele
) -> tuple[str, Path, ServicePieces, list[MessageChat], PieceProposee]:
    """Un projet importé dont `AGENTS.md` vient d'être écrit, et la pièce suivante qui attend."""
    projet_id = _importe(projets, maison)
    racine = projets.entite(projet_id).racine_chemin
    service = _service(projets, joueur, modele)
    agents = asyncio.run(service.prochaine(projet_id, []))
    assert agents is not None
    fait = asyncio.run(service.ecrire(agents))
    suivante = asyncio.run(service.prochaine(projet_id, _fil_d_une_piece(agents)))
    assert suivante is not None
    fil = [
        _message(NOM_ORCHESTRATION, "AGENTS.md ?", piece=agents),
        _message(UTILISATEUR, "Oui, écris AGENTS.md."),
        _message(NOM_ORCHESTRATION, "Écrit. La suite ?", piece_ecrite=fait, piece=suivante),
    ]
    return projet_id, racine, service, fil, suivante


def test_une_correction_comprise_repropose_la_piece_revérifiee_avec_la_phrase(
    projets: ServiceProjets, _maison: Path
) -> None:
    """Critère 2 : comprise, appliquée, **revérifiée par l'exécution** — et rien d'écrit."""
    joueur = _Joueur()
    modele = _Modele(
        correction={"comprise": True, "corrections": [{"cle": "tester", "valeur": "dotnet test"}]}
    )
    projet_id, racine, service, fil, _ = _apres_agents_ecrit(projets, _maison, joueur, modele)
    ecrit_avant = (racine / "AGENTS.md").read_text(encoding="utf-8")
    conducteur = ConducteurOutillage(ComprehensionModele(modele), pieces=service)

    reponse = asyncio.run(
        conducteur.corriger(
            [*fil, _message(UTILISATEUR, DOTNET)], projet_id=projet_id, phrase=DOTNET
        )
    )

    # La pièce que la correction touche revient — AGENTS.md, déjà écrit, et c'est
    # son contenu qui a changé : ce n'est plus la pièce tranchée.
    piece = reponse.piece
    assert piece is not None and piece.chemin == "AGENTS.md" and piece.sort == "reecrit"
    assert piece.correction == DOTNET and piece.texte_avant == ecrit_avant
    assert f"`dotnet test` — dite par la personne (« {DOTNET} »)" in piece.texte_apres
    # Revérifiée : la commande corrigée a été jouée, et son verdict est sur la carte.
    assert "dotnet test" in joueur.joues
    assert {v.commande: v.etat for v in piece.verifications}["dotnet test"] == VERIFIEE
    # …et nommée comme la commande corrigée : la carte la montre sous sa légende.
    assert piece.corrigees == ("dotnet test",)
    # La correction voyage sur le message, la phrase pour cause — le tour suivant la relit.
    assert [(c.cle, c.valeur, c.parce_que) for c in reponse.corrections] == [
        ("tester", "dotnet test", DOTNET)
    ]
    # Le juge a dit ce qu'il a compris ; la carte porte la pièce et la phrase : le texte
    # du conducteur ne les redit pas (relecture de #1161).
    assert reponse.contenu == ""
    # Et rien n'est écrit tant qu'on n'a pas accepté.
    assert (racine / "AGENTS.md").read_text(encoding="utf-8") == ecrit_avant
    # Le modèle a relu ce qu'on sait, et la phrase telle quelle.
    (prompt,) = modele.appels["correction"]
    assert 'clé "tester" (tests) : npm run test' in prompt and f"« {DOTNET} »" in prompt


def test_une_correction_en_echec_le_dit_et_ne_s_ecrit_pas(
    projets: ServiceProjets, _maison: Path, tmp_path: Path
) -> None:
    """Critère 2 : jouée, la commande corrigée échoue — la carte le dit, l'écriture est refusée."""
    joueur = _Joueur({"dotnet test": execution.Execution(code=1, sortie="MSB1003", duree_s=0.2)})
    modele = _Modele(
        correction={"comprise": True, "corrections": [{"cle": "tester", "valeur": "dotnet test"}]}
    )
    projet_id, racine, service, fil, _ = _apres_agents_ecrit(projets, _maison, joueur, modele)
    ecrit_avant = (racine / "AGENTS.md").read_text(encoding="utf-8")
    conducteur = ConducteurOutillage(ComprehensionModele(modele), pieces=service)

    reponse = asyncio.run(
        conducteur.corriger(
            [*fil, _message(UTILISATEUR, DOTNET)], projet_id=projet_id, phrase=DOTNET
        )
    )

    piece = reponse.piece
    assert piece is not None and not piece.ecrivable
    assert piece.echec == (
        "`dotnet test` a échoué à l'exécution : elle a rendu la main en erreur (code 1)."
    )
    assert "Rien n'est écrit" in reponse.contenu
    assert (racine / "AGENTS.md").read_text(encoding="utf-8") == ecrit_avant
    # Et le geste d'écrire est refusé, avant toute écriture — pas seulement caché.
    depot = ChatStore(tmp_path / "chat")
    carte = _message(NOM_ORCHESTRATION, reponse.contenu, piece=piece)
    for message in [*fil, _message(UTILISATEUR, DOTNET), carte]:
        depot.ajouter(message)
    app = create_app(
        bus=InMemoryEventBus(),
        state=ControlTowerState(),
        chat_store=depot,
        projets=projets,
        orchestration_repondeur=RepondeurOrchestration(provider=modele, pieces=service),
    )
    with TestClient(app) as client:
        refus = client.post(
            f"/api/chat/{NOM_ORCHESTRATION}/outillage/piece",
            json={"decision": DECISION_ECRIRE, "piece": piece.empreinte},
        )
    assert refus.status_code == 422, refus.text
    assert "ne s'écrit pas" in refus.json()["detail"]
    assert (racine / "AGENTS.md").read_text(encoding="utf-8") == ecrit_avant


def test_une_correction_incomprise_le_dit_et_la_piece_reste_proposee(
    projets: ServiceProjets, _maison: Path
) -> None:
    modele = _Modele(
        correction={"comprise": False, "message": "Je ne vois pas quelle commande changer."}
    )
    projet_id, racine, service, fil, en_attente = _apres_agents_ecrit(
        projets, _maison, _Joueur(), modele
    )
    conducteur = ConducteurOutillage(ComprehensionModele(modele), pieces=service)

    phrase = "change le truc"

    reponse = asyncio.run(
        conducteur.corriger(
            [*fil, _message(UTILISATEUR, phrase)], projet_id=projet_id, phrase=phrase
        )
    )

    # Le fait, et la raison du modèle — une fois : c'est ici, et nulle part ailleurs,
    # que se dit ce qui n'a pas été compris (la seconde relecture l'avait lu deux fois
    # de suite). La phrase du modèle suit les deux-points : sa majuscule tombe.
    assert reponse.contenu == "Rien n'a été écrit : je ne vois pas quelle commande changer."
    assert reponse.piece == en_attente and reponse.corrections == ()
    assert not (racine / ".agents").exists()


def test_sur_l_outillage_le_juge_redit_la_demande_sans_la_deviner() -> None:
    """Le juge parle **avant** la correction : il ne sait pas encore si elle est comprise.

    Vu sur la vraie stack (quatrième relecture) : sur « Change le truc de l'outillage,
    tu vois lequel », le juge écrivait « J'ai compris que vous voulez modifier la
    réécriture d'AGENTS.md… précisez », puis la correction ajoutait « Rien n'a été
    écrit : je ne vois pas de quel élément vous parlez… » — deux paragraphes qui
    demandaient chacun une précision, et dont le premier prétendait avoir compris ce
    que le second n'avait pas compris. Ce qui a été compris ou non, et la question qui
    en découle, sont à la correction seule ; le juge redit la demande, rien de plus.
    """
    prompt = " ".join(_PROMPT_ORCHESTRATION.split())

    assert "ce que tu as compris de la demande ou de la correction" not in prompt
    assert "redit en une phrase ce que la personne demande" in prompt
    assert "sans rien y deviner" in prompt
    assert "Ne demande aucune précision" in prompt


def test_accord_de_la_carte_ne_vaut_que_pour_le_diff_montre() -> None:
    """Sur un projet versionné, l'accord donné sur la carte ne couvre que la pièce."""
    accord = AccordDeLaCarte({"AGENTS.md", CHEMIN_MANIFESTE})

    def demande(*chemins: str) -> DemandeValidation:
        return DemandeValidation(
            task_id="outillage-1",
            titre="Écrire AGENTS.md dans Dépensio",
            description="d",
            agent="",
            role="",
            raison="application de l'outillage",
            diff=DiffProjet(modifications=tuple(Modification(chemin=c) for c in chemins)),
        )

    assert asyncio.run(accord(demande("AGENTS.md", CHEMIN_MANIFESTE)))
    assert not asyncio.run(accord(demande("AGENTS.md", "src/index.js")))
    assert not asyncio.run(accord(demande()))


@avec_git
@pytest.mark.usefixtures("_git_isole")
def test_sur_un_projet_versionne_la_piece_se_fusionne_sous_l_accord_de_la_carte(
    projets: ServiceProjets, _maison: Path
) -> None:
    racine = _projet_node(_maison)
    subprocess.run(["git", "init", "-q"], cwd=racine, check=True)
    subprocess.run(["git", "add", "-A"], cwd=racine, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "départ"], cwd=racine, check=True)
    projet_id = str(projets.creer("Dépensio", str(racine))["id"])
    service = _service(projets, _Joueur())
    agents = asyncio.run(service.prochaine(projet_id, []))
    assert agents is not None and agents.regime == "branche"

    fait = asyncio.run(service.ecrire(agents))

    assert fait.ecrite and fait.regime == "branche"
    assert (racine / "AGENTS.md").read_text(encoding="utf-8") == agents.texte_apres
    journal = subprocess.run(
        ["git", "log", "--oneline", "-3"], cwd=racine, check=True, capture_output=True, text=True
    )
    assert journal.stdout.count("\n") >= 2  # la fusion a laissé sa trace dans l'historique


@avec_git
@pytest.mark.usefixtures("_git_isole")
def test_sur_un_depot_en_autocrlf_une_piece_ecrite_se_corrige_et_se_reecrit(
    projets: ServiceProjets, _maison: Path
) -> None:
    """Critère 2 sur un projet versionné dont Git remet les fichiers en CRLF (Windows).

    Vu sur la vraie stack : `AGENTS.md` écrit puis fusionné revenait en CRLF du checkout,
    et « Nos tests tournent avec `dotnet test` », bien compris, ne le reproposait
    jamais — la pièce suivante venait à sa place.
    """
    racine = _projet_node(_maison)
    subprocess.run(["git", "init", "-q"], cwd=racine, check=True)
    subprocess.run(["git", "config", "core.autocrlf", "true"], cwd=racine, check=True)
    subprocess.run(["git", "add", "-A"], cwd=racine, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "départ"], cwd=racine, check=True)
    projet_id = str(projets.creer("Dépensio", str(racine))["id"])
    modele = _Modele(
        correction={"comprise": True, "corrections": [{"cle": "tester", "valeur": "dotnet test"}]}
    )
    service = _service(projets, _Joueur(), modele)
    agents = asyncio.run(service.prochaine(projet_id, []))
    assert agents is not None and agents.regime == "branche"
    fait = asyncio.run(service.ecrire(agents))
    assert fait.ecrite and b"\r\n" in (racine / "AGENTS.md").read_bytes()
    fil = [
        _message(NOM_ORCHESTRATION, "AGENTS.md ?", piece=agents),
        _message(UTILISATEUR, "Oui, écris AGENTS.md."),
        _message(NOM_ORCHESTRATION, "Écrit.", piece_ecrite=fait),
        _message(UTILISATEUR, DOTNET),
    ]
    conducteur = ConducteurOutillage(ComprehensionModele(modele), pieces=service)

    reponse = asyncio.run(conducteur.corriger(fil, projet_id=projet_id, phrase=DOTNET))

    piece = reponse.piece
    assert piece is not None and piece.chemin == "AGENTS.md" and piece.sort == "reecrit"
    assert "\r" not in piece.texte_avant  # le diff montré ne compte pas les fins de ligne
    fait = asyncio.run(service.ecrire(piece))
    assert fait.ecrite
    ecrit = (racine / "AGENTS.md").read_bytes()
    assert b"dotnet test" in ecrit and b"\r\n" in ecrit


# --------------------------------------------------------------------------- #
# ③ Le canal : un projet neuf naît, et son outillage commence ; le « oui » tapé  #
# --------------------------------------------------------------------------- #

#: Ce qu'un projet neuf est, compris de la conversation qui l'a fait naître.
COMPRIS_FLUTTER = {
    "message": "",
    "constats": [
        {"cle": "nature", "valeur": "Une application mobile Flutter", "parce_que": "dit"},
        {"cle": "langages", "valeur": "Dart", "parce_que": "Flutter s'écrit en Dart"},
        {"cle": "manifeste", "valeur": "pubspec.yaml", "parce_que": "Flutter"},
        {"cle": "tester", "valeur": "flutter test", "parce_que": "Flutter"},
    ],
    "questions": [],
}


def _repondeur(
    projets: ServiceProjets, modele: _Modele, joueur: _Joueur | None = None
) -> RepondeurOrchestration:
    service = _service(projets, joueur or _Joueur(), modele)
    return RepondeurOrchestration(
        provider=modele,
        naissance=ServiceNaissance(projets, git_disponible=lambda: True),
        pieces=service,
        conducteur=ConducteurOutillage(ComprehensionModele(modele), pieces=service),
    )


def test_un_projet_neuf_nait_et_son_outillage_commence_dans_la_meme_reponse(
    projets: ServiceProjets, _maison: Path
) -> None:
    """Critère 1, projet neuf : déclaré sur accord, puis sa première pièce — sans formulaire."""
    modele = _Modele(comprehension=COMPRIS_FLUTTER)
    naissance = ServiceNaissance(projets, git_disponible=lambda: True)
    demande = naissance.verifier(
        {"nom": "padel", "dossier": "", "origine": ORIGINE_NOUVEAU, "versionner": False}
    )
    fil = [
        _message(UTILISATEUR, "Une application mobile Flutter pour réserver des terrains"),
        _message(NOM_ORCHESTRATION, "Je vous propose ce projet.", projet_propose=demande),
        _message(UTILISATEUR, "Oui, crée ce projet."),
    ]

    reponse = asyncio.run(
        _repondeur(projets, modele).declarer_projet(
            AGENT_ORCHESTRATION, fil, demande=demande, approuve=True
        )
    )

    assert reponse.projet_cree is not None
    piece = reponse.piece
    assert piece is not None and piece.chemin == "AGENTS.md"
    assert piece.projet_id == reponse.projet_cree.id
    assert "flutter test" in piece.contenu
    # Ce qui a été compris voyage avec : les pièces suivantes se rédigent sans le modèle.
    assert {c.cle for c in reponse.comprehension} >= {"langages", "tester"}
    # La conversation qui a fait naître le projet est celle que le modèle a comprise.
    assert "réserver des terrains" in modele.appels["comprehension"][0]
    assert "Son outillage commence, pièce par pièce" in modele.appels["redaction"][0]
    racine = Path(reponse.projet_cree.racine)
    assert racine.is_dir() and list(racine.iterdir()) == []


def test_un_projet_neuf_s_outille_piece_par_piece_jusqu_au_bout(
    projets: ServiceProjets, _maison: Path
) -> None:
    """Chaque accord écrit une pièce, puis la suivante vient ; à la fin, plus rien à écrire."""
    modele = _Modele(comprehension=COMPRIS_FLUTTER)
    repondeur = _repondeur(projets, modele)
    racine = _maison / "Maestro" / "padel"
    projet_id = str(projets.creer("padel", str(racine), origine=ORIGINE_NOUVEAU)["id"])
    fil: list[MessageChat] = [_message(UTILISATEUR, "Une application mobile Flutter")]
    ouverture = asyncio.run(
        repondeur.ouvrir_questionnaire(AGENT_ORCHESTRATION, fil, projet_id=projet_id)
    )
    fil.append(
        _message(
            NOM_ORCHESTRATION,
            ouverture.contenu,
            piece=ouverture.piece,
            comprehension=ouverture.comprehension,
        )
    )
    ecrites: list[str] = []

    for _ in range(10):
        piece = piece_en_attente(fil)
        if piece is None:
            break
        assert piece.piece is not None
        fil.append(_message(UTILISATEUR, f"Oui, écris {piece.piece.chemin}."))
        reponse = asyncio.run(
            repondeur.trancher_piece(
                AGENT_ORCHESTRATION, fil, piece=piece.piece, decision=DECISION_ECRIRE
            )
        )
        assert reponse.piece_ecrite is not None and reponse.piece_ecrite.ecrite
        ecrites.append(reponse.piece_ecrite.chemin)
        assert reponse.contenu == REDIGE  # le modèle parle, les cartes portent les faits
        fil.append(
            _message(
                NOM_ORCHESTRATION,
                reponse.contenu,
                piece=reponse.piece,
                piece_ecrite=reponse.piece_ecrite,
            )
        )

    # Un poste nu, et personne n'a nommé d'outil d'agent : aucun pont (#1295).
    assert ecrites == ["AGENTS.md", ".agents/skills/lancer-les-tests/SKILL.md"]
    assert piece_en_attente(fil) is None
    assert {e["chemin"] for e in _manifeste(racine)["entrees"]} == set(ecrites)
    assert "il n'y a plus rien à écrire" in modele.appels["redaction"][-1]


def _ouvre_padel(projets: ServiceProjets, maison: Path, modele: _Modele) -> tuple[
    RepondeurOrchestration, list[MessageChat], PieceProposee
]:
    """Un projet neuf compris, son outillage ouvert : le fil tel que la première pièce le laisse."""
    repondeur = _repondeur(projets, modele)
    racine = maison / "Maestro" / "padel"
    projet_id = str(projets.creer("padel", str(racine), origine=ORIGINE_NOUVEAU)["id"])
    fil: list[MessageChat] = [_message(UTILISATEUR, "Une application mobile Flutter")]
    ouverture = asyncio.run(
        repondeur.ouvrir_questionnaire(AGENT_ORCHESTRATION, fil, projet_id=projet_id)
    )
    assert ouverture.piece is not None
    fil.append(
        _message(
            NOM_ORCHESTRATION,
            ouverture.contenu,
            piece=ouverture.piece,
            comprehension=ouverture.comprehension,
        )
    )
    return repondeur, fil, ouverture.piece


def test_apres_un_geste_sur_une_piece_la_redaction_sait_si_la_suivante_attend(
    projets: ServiceProjets, _maison: Path
) -> None:
    """#1339 : la pièce suivante est sur sa carte, avec ses gestes — la parole le sait.

    Vu sur la vraie stack à la relecture de #1161 : après « Écrire ce fichier », puis
    après « Pas cette pièce », le fil redisait les gestes de la pièce suivante juste
    au-dessus de la carte qui les porte. Le modèle ne pouvait pas savoir qu'une carte
    les montrait ; il l'apprend désormais, et seulement quand c'est vrai — après la
    dernière pièce, plus rien n'attend, et c'est à lui de dire la suite.
    """
    modele = _Modele(comprehension=COMPRIS_FLUTTER)
    repondeur, fil, premiere = _ouvre_padel(projets, _maison, modele)

    ecrite = asyncio.run(
        repondeur.trancher_piece(AGENT_ORCHESTRATION, fil, piece=premiere, decision=DECISION_ECRIRE)
    )
    assert ecrite.piece is not None and ecrite.contenu == REDIGE
    assert FAIT_DE_LA_DEMANDE in modele.appels["redaction"][-1]
    fil.append(
        _message(
            NOM_ORCHESTRATION, ecrite.contenu, piece=ecrite.piece, piece_ecrite=ecrite.piece_ecrite
        )
    )

    passee = asyncio.run(
        repondeur.trancher_piece(
            AGENT_ORCHESTRATION, fil, piece=ecrite.piece, decision=DECISION_PASSER
        )
    )
    assert passee.piece is None and passee.contenu == REDIGE
    assert "il n'y a plus rien à écrire" in modele.appels["redaction"][-1]
    assert FAIT_DE_LA_DEMANDE not in modele.appels["redaction"][-1]


def test_passer_une_piece_qui_a_une_suivante_le_dit_aussi_a_la_redaction(
    projets: ServiceProjets, _maison: Path
) -> None:
    """« Pas cette pièce », le second geste de la relecture de #1161 : la suivante attend."""
    modele = _Modele(comprehension=COMPRIS_FLUTTER)
    repondeur, fil, premiere = _ouvre_padel(projets, _maison, modele)

    passee = asyncio.run(
        repondeur.trancher_piece(
            AGENT_ORCHESTRATION, fil, piece=premiere, decision=DECISION_PASSER
        )
    )

    assert passee.piece is not None and passee.contenu == REDIGE
    assert FAIT_DE_LA_DEMANDE in modele.appels["redaction"][-1]


def test_remettre_l_outillage_a_plus_tard_ne_laisse_aucune_carte_qui_attende(
    projets: ServiceProjets, _maison: Path
) -> None:
    """Plus rien n'attend sous la parole : la suite est à dire, pas à taire."""
    modele = _Modele(comprehension=COMPRIS_FLUTTER)
    repondeur, fil, premiere = _ouvre_padel(projets, _maison, modele)

    reportee = asyncio.run(
        repondeur.trancher_piece(
            AGENT_ORCHESTRATION, fil, piece=premiere, decision=DECISION_PLUS_TARD
        )
    )

    assert reportee.piece is None and reportee.contenu == REDIGE
    assert FAIT_DE_LA_DEMANDE not in modele.appels["redaction"][-1]


def test_un_projet_declare_dont_l_outillage_commence_le_dit_a_la_redaction(
    projets: ServiceProjets, _maison: Path
) -> None:
    """La naissance d'un projet (#1294) : sa première pièce attend — refusé, rien n'attend."""
    naissance = ServiceNaissance(projets, git_disponible=lambda: True)
    demande = naissance.verifier(
        {"nom": "padel", "dossier": "", "origine": ORIGINE_NOUVEAU, "versionner": False}
    )
    fil = [
        _message(UTILISATEUR, "Une application mobile Flutter pour réserver des terrains"),
        _message(NOM_ORCHESTRATION, "Je vous propose ce projet.", projet_propose=demande),
    ]
    refuse, accepte = _Modele(comprehension=COMPRIS_FLUTTER), _Modele(comprehension=COMPRIS_FLUTTER)

    non = asyncio.run(
        _repondeur(projets, refuse).declarer_projet(
            AGENT_ORCHESTRATION, fil, demande=demande, approuve=False
        )
    )
    oui = asyncio.run(
        _repondeur(projets, accepte).declarer_projet(
            AGENT_ORCHESTRATION, fil, demande=demande, approuve=True
        )
    )

    assert not non.porte_une_demande
    assert FAIT_DE_LA_DEMANDE not in refuse.appels["redaction"][-1]
    assert oui.piece is not None
    assert FAIT_DE_LA_DEMANDE in accepte.appels["redaction"][-1]


def test_reprendre_l_outillage_d_un_projet_reporte_leve_le_report(
    projets: ServiceProjets, _maison: Path
) -> None:
    """Vu à la relecture : repris dans la conversation, l'outillage restait « reporté » sur la
    carte du projet — badge et bouton — pendant que sa question attendait dans la colonne.
    Le reprendre, c'est ne plus le remettre à plus tard ; « plus tard » le repose."""
    racine = _maison / "Maestro" / "api"
    projet_id = str(projets.creer("api", str(racine), origine=ORIGINE_NOUVEAU)["id"])
    projets.reporter_outillage(projet_id)
    assert projets.entite(projet_id).outillage.reporte
    repondeur = _repondeur(projets, _Modele(comprehension=COMPRIS_FLUTTER))

    ouverture = asyncio.run(repondeur._conducteur.ouvrir([], projet_id=projet_id))

    assert ouverture.question is not None or ouverture.piece is not None
    assert not projets.entite(projet_id).outillage.reporte
    piece = asyncio.run(
        repondeur._conducteur.ouvrir(
            [_message(UTILISATEUR, "Une application mobile Flutter")], projet_id=projet_id
        )
    ).piece
    assert piece is not None
    asyncio.run(repondeur._conducteur.trancher([], piece=piece, decision="plus-tard"))
    assert projets.entite(projet_id).outillage.reporte


def test_la_fin_du_questionnaire_annonce_les_pieces_et_rien_d_autre(
    projets: ServiceProjets, _maison: Path
) -> None:
    """Vu sur la vraie stack : le dernier mot du modèle (« Dites-moi par quoi commencer,
    par exemple la page d'accueil ») précédait la première pièce, alors que sept
    attendaient un geste. La transition est celle du code : ce qui a été compris, puis
    l'outillage qui s'écrit — la carte dit la pièce."""
    compris = {**COMPRIS_FLUTTER, "message": "Tout est décidé. Dites-moi par quoi commencer."}
    repondeur = _repondeur(projets, _Modele(comprehension=compris))
    racine = _maison / "Maestro" / "padel"
    projet_id = str(projets.creer("padel", str(racine), origine=ORIGINE_NOUVEAU)["id"])

    reponse = asyncio.run(
        repondeur._conducteur.ouvrir(
            [_message(UTILISATEUR, "Une application mobile Flutter")], projet_id=projet_id
        )
    )

    assert reponse.piece is not None and reponse.piece.chemin == "AGENTS.md"
    assert reponse.contenu.startswith("C'est tout ce qu'il me fallait")
    assert "pièce par pièce" in reponse.contenu
    assert "par quoi commencer" not in reponse.contenu and "AGENTS.md" not in reponse.contenu


def test_une_correction_qui_retire_une_commande_porte_sa_phrase_sur_la_carte(
    projets: ServiceProjets, _maison: Path
) -> None:
    """Vu sur la vraie stack : « retire dotnet test » ramenait `AGENTS.md` sans la phrase
    sur la carte — elle ne s'y lisait que si le texte la portait (une commande dite).
    Une version neuve d'un chemin déjà proposé est ce que la correction a touché."""
    joueur = _Joueur()
    modele = _Modele(
        correction={"comprise": True, "corrections": [{"cle": "tester", "valeur": "aucun"}]}
    )
    projet_id, _, service, fil, _ = _apres_agents_ecrit(projets, _maison, joueur, modele)
    conducteur = ConducteurOutillage(ComprehensionModele(modele), pieces=service)
    phrase = "Pas de tests pour l'instant."

    reponse = asyncio.run(
        conducteur.corriger(
            [*fil, _message(UTILISATEUR, phrase)], projet_id=projet_id, phrase=phrase
        )
    )

    piece = reponse.piece
    assert piece is not None and piece.chemin == "AGENTS.md" and piece.sort == "reecrit"
    assert "npm run test" not in piece.texte_apres
    assert piece.correction == phrase


def test_deux_projets_outilles_dans_le_meme_fil_ne_se_melangent_pas(
    projets: ServiceProjets, _maison: Path
) -> None:
    """Vu sur la vraie stack : un projet neuf outillé d'abord, puis un projet importé dans
    la même conversation — le second a reçu les réponses du premier (des tests et une CI
    qu'il n'a pas). Le fil est transverse : ce qu'il sait d'un projet ne vaut pas pour
    un autre."""
    joueur = _Joueur()
    modele = _Modele(comprehension=COMPRIS_FLUTTER)
    service = _service(projets, joueur, modele)
    conducteur = ConducteurOutillage(ComprehensionModele(modele), pieces=service)
    neuf = str(
        projets.creer("padel", str(_maison / "Maestro" / "padel"), origine=ORIGINE_NOUVEAU)["id"]
    )
    demande = _message(UTILISATEUR, "Une application mobile Flutter")
    ouverture = asyncio.run(conducteur.ouvrir([demande], projet_id=neuf))
    assert ouverture.piece is not None and "flutter test" in ouverture.piece.contenu
    fil = [
        demande,
        _message(
            NOM_ORCHESTRATION,
            ouverture.contenu,
            piece=ouverture.piece,
            comprehension=ouverture.comprehension,
            projet_outille=ouverture.projet_outille,
        ),
    ]
    importe = _importe(projets, _maison)

    reponse = asyncio.run(conducteur.ouvrir(fil, projet_id=importe))

    piece = reponse.piece
    assert piece is not None and piece.projet_id == importe
    assert "flutter" not in piece.contenu.lower() and "npm run test" in piece.contenu
    # Le second projet se lit (#1158) : il ne se questionne pas avec les réponses du premier.
    assert len(modele.appels["comprehension"]) == 1


def test_une_question_posee_pendant_une_piece_ne_la_retire_pas(
    projets: ServiceProjets, _maison: Path
) -> None:
    """Vu sur la vraie stack : une phrase vague pendant qu'une pièce attendait — le fil a
    demandé une précision en parlant de « la pièce proposée juste en dessous »… qui n'y
    était plus : une carte ne tient qu'au dernier message. Un échange la garde."""
    modele = _Modele(_dicte("Laquelle voulez-vous changer ?", VERDICT_ECHANGE))
    repondeur = _repondeur(projets, modele)
    projet_id = _importe(projets, _maison)
    agents = asyncio.run(repondeur._conducteur.ouvrir([], projet_id=projet_id)).piece
    assert agents is not None

    reponse = asyncio.run(
        repondeur.produire(
            AGENT_ORCHESTRATION,
            _fil_d_une_piece(agents, _message(UTILISATEUR, "Change le truc de l'outillage.")),
            projet_id=projet_id,
        )
    )

    assert reponse.contenu.startswith("Laquelle voulez-vous changer ?")
    assert reponse.piece == agents and reponse.proposition == ""


def test_un_oui_tape_sur_une_piece_l_ecrit_comme_le_clic(
    projets: ServiceProjets, _maison: Path
) -> None:
    modele = _Modele(_dicte("Je l'écris.", VERDICT_ACCORD))
    repondeur = _repondeur(projets, modele)
    projet_id = _importe(projets, _maison)
    racine = projets.entite(projet_id).racine_chemin
    agents = asyncio.run(repondeur._conducteur.ouvrir([], projet_id=projet_id)).piece
    assert agents is not None

    reponse = asyncio.run(
        repondeur.produire(
            AGENT_ORCHESTRATION, _fil_d_une_piece(agents, _message(UTILISATEUR, "oui vas-y"))
        )
    )

    assert reponse.piece_ecrite is not None and reponse.piece_ecrite.chemin == "AGENTS.md"
    assert (racine / "AGENTS.md").read_text(encoding="utf-8") == agents.texte_apres
    # Les mots du juge d'abord, puis ce que lui ne pouvait pas savoir : c'est écrit.
    assert reponse.contenu.startswith("Je l'écris.")
    assert "AGENTS.md est écrit." in reponse.contenu
    assert reponse.piece is not None and reponse.piece.chemin == SKILL_ROUTE


def test_le_verdict_outillage_comprend_la_correction_sans_rien_ecrire(
    projets: ServiceProjets, _maison: Path
) -> None:
    joueur = _Joueur()
    modele = _Modele(
        _dicte("Vos tests tournent avec dotnet test : je revérifie.", VERDICT_OUTILLAGE),
        correction={
            "comprise": True,
            "corrections": [{"cle": "tester", "valeur": "dotnet test"}],
            "message": "J'ai remplacé npm test par dotnet test.",
        },
    )
    projet_id, racine, service, fil, _ = _apres_agents_ecrit(projets, _maison, joueur, modele)
    repondeur = RepondeurOrchestration(
        provider=modele,
        pieces=service,
        conducteur=ConducteurOutillage(ComprehensionModele(modele), pieces=service),
    )
    ecrit_avant = (racine / "AGENTS.md").read_text(encoding="utf-8")

    reponse = asyncio.run(
        repondeur.produire(
            AGENT_ORCHESTRATION, [*fil, _message(UTILISATEUR, DOTNET)], projet_id=projet_id
        )
    )

    # Le juge a dit ce qu'il a compris, et c'est tout ce que le message dit : ni le
    # message du modèle de correction, ni la pièce — la carte la porte, phrase comprise
    # (la relecture de #1161 avait vu chacun redit en dessous).
    assert reponse.contenu == "Vos tests tournent avec dotnet test : je revérifie."
    assert reponse.piece is not None and reponse.piece.correction == DOTNET
    assert corrections_du_fil([_message(NOM_ORCHESTRATION, "x", corrections=reponse.corrections)])
    assert (racine / "AGENTS.md").read_text(encoding="utf-8") == ecrit_avant


def test_sans_ecriture_branchee_le_verdict_outillage_le_dit() -> None:
    modele = _Modele(_dicte("Je corrige.", VERDICT_OUTILLAGE))

    reponse = asyncio.run(
        RepondeurOrchestration(provider=modele).produire(
            AGENT_ORCHESTRATION, [_message(UTILISATEUR, DOTNET)], projet_id="p-1"
        )
    )

    assert "Aucune écriture d'outillage n'est branchée" in reponse.contenu
    assert reponse.piece is None


def test_une_conclusion_sans_projet_nomme_un_geste_qui_existe(
    projets: ServiceProjets, _maison: Path
) -> None:
    """Ouvert sans projet, le questionnaire conclut sur le geste réel : dire d'outiller.

    Il disait « Aucune écriture n'est branchée sur ce fil » alors qu'elle l'était —
    c'est le projet qui manquait. La phrase nomme le geste, et le geste mène à la
    première pièce, rédigée de ce qui vient d'être compris, sans reposer de question.
    """
    modele = _Modele(
        _dicte("J'outille ce projet.", VERDICT_OUTILLAGE), comprehension=COMPRIS_FLUTTER
    )
    repondeur = _repondeur(projets, modele)
    racine = _maison / "Maestro" / "padel"
    projet_id = str(projets.creer("padel", str(racine), origine=ORIGINE_NOUVEAU)["id"])
    fil = [_message(UTILISATEUR, "Une application mobile Flutter")]

    conclusion = asyncio.run(repondeur.ouvrir_questionnaire(AGENT_ORCHESTRATION, fil))

    assert conclusion.piece is None
    assert "ouvrez-le et dites-moi de l'outiller" in conclusion.contenu
    assert "Aucune écriture n'est branchée" not in conclusion.contenu
    fil += [
        _message(NOM_ORCHESTRATION, conclusion.contenu, comprehension=conclusion.comprehension),
        _message(UTILISATEUR, "Outille ce projet."),
    ]

    reponse = asyncio.run(repondeur.produire(AGENT_ORCHESTRATION, fil, projet_id=projet_id))

    assert reponse.piece is not None and reponse.piece.chemin == "AGENTS.md"
    assert reponse.piece.projet_id == projet_id and "flutter test" in reponse.piece.contenu
    assert len(modele.appels["comprehension"]) == 1
    assert not racine.exists() or list(racine.iterdir()) == []


# --------------------------------------------------------------------------- #
# ④ La route : le geste, le double clic, le report                              #
# --------------------------------------------------------------------------- #


@pytest.fixture()
def client_pieces(
    projets: ServiceProjets, tmp_path: Path, _maison: Path
) -> Iterator[tuple[TestClient, str, Path]]:
    """L'app, un projet Node importé, et un fil d'orchestration qui écrit des pièces."""
    projet_id = _importe(projets, _maison)
    app = create_app(
        bus=InMemoryEventBus(),
        state=ControlTowerState(),
        chat_store=ChatStore(tmp_path / "chat"),
        projets=projets,
        orchestration_repondeur=_repondeur(projets, _Modele()),
    )
    with TestClient(app) as client:
        yield client, projet_id, projets.entite(projet_id).racine_chemin


def test_la_route_ouvre_l_outillage_d_un_projet_ecrit_la_piece_et_refuse_le_double_clic(
    client_pieces: tuple[TestClient, str, Path],
) -> None:
    client, projet_id, racine = client_pieces
    ouverture = client.post(
        f"/api/chat/{NOM_ORCHESTRATION}/outillage/questionnaire", params={"projet": projet_id}
    )
    assert ouverture.status_code == 201, ouverture.text
    (message,) = ouverture.json()["messages"]
    piece = message["piece"]
    assert piece["chemin"] == "AGENTS.md" and piece["ecrivable"] is True
    assert piece["texte_apres"] and piece["empreinte"]
    # Le texte dit ce que la carte ne dit pas — le projet a été lu —, jamais la pièce
    # qu'elle montre déjà (seconde relecture de #1161).
    assert message["contenu"].startswith("J'ai lu « Dépensio »")
    assert "AGENTS.md" not in message["contenu"]

    geste = client.post(
        f"/api/chat/{NOM_ORCHESTRATION}/outillage/piece",
        json={"decision": DECISION_ECRIRE, "piece": piece["empreinte"]},
    )

    assert geste.status_code == 201, geste.text
    clic, suite = geste.json()["messages"]
    assert clic["auteur"] == UTILISATEUR and clic["contenu"] == "Oui, écris AGENTS.md."
    assert suite["piece_ecrite"]["etat"] == "ecrit" and suite["piece_ecrite"]["ecrite"] is True
    assert suite["piece"]["chemin"] == SKILL_ROUTE
    assert (racine / "AGENTS.md").read_text(encoding="utf-8") == piece["texte_apres"]
    # Le double clic vise la pièce d'avant : il n'écrit pas la suivante, qu'on n'a pas vue.
    double = client.post(
        f"/api/chat/{NOM_ORCHESTRATION}/outillage/piece",
        json={"decision": DECISION_ECRIRE, "piece": piece["empreinte"]},
    )
    assert double.status_code == 409, double.text
    assert not (racine / SKILL_ROUTE).exists()


def test_un_outillage_ouvert_pour_un_projet_le_retient_d_une_question_a_l_autre(
    projets: ServiceProjets, tmp_path: Path, _maison: Path
) -> None:
    """Vu sur la vraie stack : ouvert par `?projet=` sur un dossier vide, l'outillage posait
    la question ouverte, puis concluait « aucune écriture n'est branchée » — le projet
    nommé à l'ouverture n'était écrit nulle part dans le fil, et la réponse ne savait
    plus quel projet outiller."""
    racine = _maison / "Maestro" / "api"
    projet_id = str(projets.creer("api", str(racine), origine=ORIGINE_NOUVEAU)["id"])
    app = create_app(
        bus=InMemoryEventBus(),
        state=ControlTowerState(),
        chat_store=ChatStore(tmp_path / "chat"),
        projets=projets,
        orchestration_repondeur=_repondeur(projets, _Modele(comprehension=COMPRIS_FLUTTER)),
    )
    with TestClient(app) as client:
        ouverture = client.post(
            f"/api/chat/{NOM_ORCHESTRATION}/outillage/questionnaire", params={"projet": projet_id}
        )
        question = ouverture.json()["messages"][0]
        reponse = client.post(
            f"/api/chat/{NOM_ORCHESTRATION}/outillage",
            json={"valeur": "Une application mobile Flutter", "libre": True},
        )

    assert question["question"]["cle"] == "nature" and question["projet_outille"] == projet_id
    suite = reponse.json()["messages"][1]
    assert suite["piece"] is not None and suite["piece"]["projet_id"] == projet_id
    assert "Aucune écriture n'est branchée" not in suite["contenu"]


def test_la_route_passe_une_piece_sans_l_ecrire(
    client_pieces: tuple[TestClient, str, Path],
) -> None:
    client, projet_id, racine = client_pieces
    client.post(
        f"/api/chat/{NOM_ORCHESTRATION}/outillage/questionnaire", params={"projet": projet_id}
    )

    geste = client.post(
        f"/api/chat/{NOM_ORCHESTRATION}/outillage/piece", json={"decision": DECISION_PASSER}
    )

    assert geste.status_code == 201, geste.text
    clic, suite = geste.json()["messages"]
    assert clic["contenu"] == "Pas cette pièce : AGENTS.md."
    assert suite["piece_ecrite"]["etat"] == "ecartee" and suite["piece"]["chemin"] == SKILL_ROUTE
    assert not (racine / "AGENTS.md").exists()


def test_la_route_remet_l_outillage_a_plus_tard_sur_la_fiche_du_projet(
    client_pieces: tuple[TestClient, str, Path],
) -> None:
    client, projet_id, racine = client_pieces
    client.post(
        f"/api/chat/{NOM_ORCHESTRATION}/outillage/questionnaire", params={"projet": projet_id}
    )

    geste = client.post(
        f"/api/chat/{NOM_ORCHESTRATION}/outillage/piece", json={"decision": "plus-tard"}
    )

    assert geste.status_code == 201, geste.text
    assert geste.json()["messages"][1]["piece"] is None
    assert client.get(f"/api/projets/{projet_id}").json()["outillage"]["a_faire"] is True
    assert not (racine / "AGENTS.md").exists()


def test_la_route_refuse_une_decision_inconnue_et_un_geste_sans_piece(
    client_pieces: tuple[TestClient, str, Path],
) -> None:
    client, _, _ = client_pieces
    sans = client.post(
        f"/api/chat/{NOM_ORCHESTRATION}/outillage/piece", json={"decision": DECISION_ECRIRE}
    )
    inconnue = client.post(
        f"/api/chat/{NOM_ORCHESTRATION}/outillage/piece", json={"decision": "tout-ecrire"}
    )

    assert sans.status_code == 409
    assert "aucune pièce d'outillage en attente" in sans.json()["detail"]
    assert inconnue.status_code == 422


# --------------------------------------------------------------------------- #
# ⑤ Le message : la pièce et son fait se relisent, et l'attente suit la règle   #
# --------------------------------------------------------------------------- #


def _piece(**champs: Any) -> PieceProposee:
    valeurs: dict[str, Any] = {
        "projet_id": "p-1",
        "projet_nom": "Dépensio",
        "cible": "C:/sites/depensio",
        "chemin": "AGENTS.md",
        "nom": "AGENTS.md",
        "nature": "instructions",
        "raison": "le fichier d'instructions",
        "role": "instructions",
        "portee": "fichier",
        "contenu": "# AGENTS.md\n",
        "texte_apres": "# AGENTS.md\n",
        "verifications": (
            Verification(usage="tester", commande="dotnet test", etat=VERIFIEE, raison="ok"),
        ),
        "correction": DOTNET,
        "source": {"type": "analyse"},
    }
    valeurs.update(champs)
    return PieceProposee(**valeurs)


def test_la_piece_son_fait_et_la_correction_se_relisent_a_l_identique() -> None:
    piece = _piece(correction=DOTNET, corrigees=("dotnet test",))
    fait = PieceEcrite(projet_id="p-1", chemin="AGENTS.md", nom="AGENTS.md", etat="ecrit")
    correction = Choix(cle="tester", valeur="dotnet test", deduit=True, parce_que=DOTNET)
    message = _message(
        NOM_ORCHESTRATION, "Voilà.", piece=piece, piece_ecrite=fait, corrections=(correction,)
    )

    relu = MessageChat.from_dict(json.loads(json.dumps(message.to_ligne())))

    assert relu.piece == piece and relu.piece_ecrite == fait and relu.corrections == (correction,)
    ancienne = MessageChat.from_dict({"agent": NOM_ORCHESTRATION, "contenu": "x"})
    assert ancienne.piece is None and ancienne.piece_ecrite is None and ancienne.corrections == ()


def test_une_piece_attend_tant_que_rien_ne_l_a_suivie_et_le_fil_nomme_son_projet() -> None:
    carte = _message(NOM_ORCHESTRATION, "Voilà.", piece=_piece())

    assert piece_en_attente([carte]) is carte
    assert piece_en_attente([carte, _message(UTILISATEUR, "oui")]) is None
    assert projet_du_fil([carte, _message(UTILISATEUR, "oui")]) == "p-1"
    texte = transcription([carte])
    assert (
        "[Pièce d'outillage proposée sur la carte : AGENTS.md (instructions, pièce 1 sur 1"
        in texte
    )
    assert f"après la correction « {DOTNET} »" in texte


def test_un_echec_de_correction_rend_la_piece_non_ecrivable() -> None:
    assert _piece().ecrivable
    assert not _piece(echec="`dotnet test` a échoué à l'exécution.").ecrivable
    # L'empreinte désigne le **contenu** : la même version, quelle que soit sa justification.
    assert _piece().to_dict()["empreinte"] == _piece(correction="").to_dict()["empreinte"]


def test_les_notes_renversees_renvoient_a_la_decision_et_docs_05_decrit_les_pieces() -> None:
    """Critère 3 : ce que la documentation dit de ce renversement, tenu comme un fait du dépôt.

    Deux notes décidaient de l'étape d'outillage que #1161 retire — docs/37 §4 point 6
    (« première et proposée d'office, mais reportable ») et la ligne #1034 de docs/38 §8.
    Qui les relit doit tomber sur le renversement **là où la règle est écrite**, avec le
    lien vers la note qui le décide (docs/43) ; et docs/05 §6.20 doit décrire ce qui la
    remplace. Un renvoi retiré en réécrivant l'une de ces sections ferait relire une
    règle morte comme une règle vivante.
    """
    docs = Path(__file__).resolve().parent.parent / "docs"
    note = "43-decision-un-projet-nait-dans-la-conversation.md"

    def entre(fichier: str, debut: str, fin: str) -> str:
        texte = (docs / fichier).read_text(encoding="utf-8")
        depart = texte.index(debut)
        return texte[depart : texte.index(fin, depart)]

    point_6 = entre(
        "37-decision-equipe-sur-mesure.md", "6. **L'étape d'outillage", "7. **Le répertoire"
    )
    assert "⚠ **Renversé le 2026-09-24**" in point_6 and note in point_6
    assert "#1161, **livré**" in point_6
    section_8 = entre("38-decision-outillage-universel-du-projet.md", "## 8. ", "| #1035")
    assert "⚠ **Renversé en partie le 2026-09-24**" in section_8 and note in section_8
    assert "⚠ Renversé par #1161" in section_8
    assert "| #1161 — pièce par pièce, dans la conversation" in section_8
    pieces = entre(
        "05-interface-control-tower.md", "#### Pièce par pièce, dans le fil (#1161)", "\n### "
    )
    for fait in ("Corriger avec ses mots", "**La carte**", "PieceDOutillage.tsx", "422"):
        assert fait in pieces, fait

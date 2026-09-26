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
from maestro.outillage.correction import corriger, lire_correction
from maestro.outillage.generation import poser_piece, prevoir
from maestro.outillage.modele import ORIGINE_DITE
from maestro.outillage.questionnaire import Choix
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
    projets: ServiceProjets, joueur: _Joueur, modele: _Modele | None = None
) -> ServicePieces:
    """Le service des pièces, commandes jouées par le joueur doublé."""
    return ServicePieces(
        ServiceOutillage(projets, provider=_LecteurMuet()),
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
    assert (piece.rang, piece.total) == (1, 6)
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
    # Seule la pièce écrite : ni pont ni skill n'ont atteint le disque.
    assert not (racine / "CLAUDE.md").exists() and not (racine / ".agents").exists()
    assert suivante is not None and suivante.chemin == "CLAUDE.md" and suivante.rang == 2
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
    assert suivante is not None and suivante.chemin == "CLAUDE.md"
    assert not (racine / "AGENTS.md").exists()


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
    # La correction voyage sur le message, la phrase pour cause — le tour suivant la relit.
    assert [(c.cle, c.valeur, c.parce_que) for c in reponse.corrections] == [
        ("tester", "dotnet test", DOTNET)
    ]
    assert "corrigée d'après vous" in reponse.contenu
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
    assert "`dotnet test` a échoué à l'exécution" in piece.echec and "code 1" in piece.echec
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

    assert reponse.contenu.startswith(
        "Je n'ai rien changé à l'outillage : Je ne vois pas quelle commande changer."
    )
    assert "Rien n'a été écrit" in reponse.contenu
    assert reponse.piece == en_attente and reponse.corrections == ()
    assert not (racine / ".agents").exists()


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

    assert ecrites == [
        "AGENTS.md",
        "CLAUDE.md",
        "GEMINI.md",
        ".agents/skills/lancer-les-tests/SKILL.md",
    ]
    assert piece_en_attente(fil) is None
    assert {e["chemin"] for e in _manifeste(racine)["entrees"]} == set(ecrites)
    assert "il n'y a plus rien à écrire" in modele.appels["redaction"][-1]


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
    assert reponse.piece is not None and reponse.piece.chemin == "CLAUDE.md"


def test_le_verdict_outillage_comprend_la_correction_sans_rien_ecrire(
    projets: ServiceProjets, _maison: Path
) -> None:
    joueur = _Joueur()
    modele = _Modele(
        _dicte("Vos tests tournent avec dotnet test : je revérifie.", VERDICT_OUTILLAGE),
        correction={"comprise": True, "corrections": [{"cle": "tester", "valeur": "dotnet test"}]},
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

    assert reponse.contenu.startswith("Vos tests tournent avec dotnet test : je revérifie.")
    assert "corrigée d'après vous" in reponse.contenu
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

    geste = client.post(
        f"/api/chat/{NOM_ORCHESTRATION}/outillage/piece",
        json={"decision": DECISION_ECRIRE, "piece": piece["empreinte"]},
    )

    assert geste.status_code == 201, geste.text
    clic, suite = geste.json()["messages"]
    assert clic["auteur"] == UTILISATEUR and clic["contenu"] == "Oui, écris AGENTS.md."
    assert suite["piece_ecrite"]["etat"] == "ecrit" and suite["piece_ecrite"]["ecrite"] is True
    assert suite["piece"]["chemin"] == "CLAUDE.md"
    assert (racine / "AGENTS.md").read_text(encoding="utf-8") == piece["texte_apres"]
    # Le double clic vise la pièce d'avant : il n'écrit pas la suivante, qu'on n'a pas vue.
    double = client.post(
        f"/api/chat/{NOM_ORCHESTRATION}/outillage/piece",
        json={"decision": DECISION_ECRIRE, "piece": piece["empreinte"]},
    )
    assert double.status_code == 409, double.text
    assert not (racine / "CLAUDE.md").exists()


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
    assert suite["piece_ecrite"]["etat"] == "ecartee" and suite["piece"]["chemin"] == "CLAUDE.md"
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
    piece = _piece()
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

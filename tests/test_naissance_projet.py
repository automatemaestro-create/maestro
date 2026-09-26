"""Un projet naît dans la conversation (#1294, docs/43 §2.2).

« Nouveau projet » ouvre le fil de l'orchestration : le modèle comprend ce que la
personne veut faire, propose un nom, un dossier et le versionnement, et le projet
est déclaré **sur accord**. Ce que ces tests tiennent est la part du code — ce que
le modèle propose est un double, sa qualité se mesure sur la vraie stack
(scénario S7, `maestro.scenarios`) :

① **la vérification** (`ServiceNaissance.verifier`) — une proposition est confrontée
   au disque avant d'être montrée : dossier neuf rangé sous le répertoire des
   projets, dossier occupé ou nom pris remplacés par une variante **dite**,
   frontières d'EF-38 et dossier introuvable refusés, Git absent dit ;
② **la déclaration** (`ServiceNaissance.declarer`) — par les verbes de
   `ServiceProjets` : un dossier neuf naît (sous Git s'il était proposé), un dossier
   importé n'est pas touché ;
③ **le canal** (`RepondeurOrchestration`) — le verdict `projet` pose une carte et
   ne déclare rien ; une correction tapée repropose ; un « oui » tapé ou un clic
   déclare **ce que la carte montrait** ; un refus ne crée rien ; un dossier importé
   est lu après l'accord, et ce qu'on en a compris nourrit la réponse ;
④ **la route** `POST /api/chat/orchestrateur/projet` — la paire geste/réponse, le
   projet déclaré visible par `GET /api/projets`, le `409` du double clic, et le
   fait `projet_cree` qui survit à la relecture du fil.

Ni réseau, ni modèle, ni Redis : le fournisseur est un double qui rend ce qu'on lui
dicte, les projets vivent sous un dossier utilisateur factice (sous Windows,
`tmp_path` est dans `AppData`, que la validation refuse à raison).
"""

from __future__ import annotations

import asyncio
import json
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from maestro.controltower import ControlTowerState, InMemoryEventBus, create_app
from maestro.controltower.chat import (
    ORIGINE_EXISTANT,
    ORIGINE_NOUVEAU,
    ChatStore,
    DemandeProjet,
    MessageChat,
    ProjetCree,
    projet_en_attente,
    transcription,
)
from maestro.controltower.naissance import NaissanceRefusee, ServiceNaissance
from maestro.controltower.orchestration import (
    _MARQUEUR_VERDICT,
    _PROMPT_REDACTION,
    AGENT_ORCHESTRATION,
    NOM_ORCHESTRATION,
    VERDICT_ACCORD,
    VERDICT_ECHANGE,
    VERDICT_PROJET,
    RepondeurOrchestration,
)
from maestro.controltower.projets import ServiceProjets
from maestro.projets import ProjetStore
from maestro.projets.reglages import ReglagesProjetsStore
from maestro.providers.base import ModelProvider

UTILISATEUR = "utilisateur"
GIT = shutil.which("git")
avec_git = pytest.mark.skipif(GIT is None, reason="git introuvable")

#: La demande du critère 1, mot pour mot.
KOMBUCHA = "je veux un site vitrine pour mon kombucha"

#: Ce que la rédaction rend : un texte qu'aucun gabarit du code ne produit, pour
#: qu'une assertion sache que c'est bien le modèle qui a parlé.
REDIGE = "Voilà, c'est fait : le projet est ouvert."


@pytest.fixture(autouse=True)
def _maison(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un dossier utilisateur factice — la raison de `tests/test_projets_api.py`."""
    maison = tmp_path / "maison"
    maison.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: maison))
    return maison


@pytest.fixture()
def _git_isole(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Coupe la configuration Git globale du poste (#333), comme `test_projets_api`."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig-absent"))


@pytest.fixture()
def projets(tmp_path: Path) -> ServiceProjets:
    """Le service des projets sur un dépôt et des réglages jetables — jamais ceux du poste."""
    return ServiceProjets(
        ProjetStore(tmp_path / "depot"),
        reglages=ReglagesProjetsStore(tmp_path / "reglages"),
    )


def _naissance(projets: ServiceProjets, **kwargs: Any) -> ServiceNaissance:
    """Le service de naissance, Git déclaré présent sauf mention contraire."""
    kwargs.setdefault("git_disponible", lambda: True)
    return ServiceNaissance(projets, **kwargs)


def _repertoire(maison: Path) -> Path:
    """Le répertoire des projets par défaut — `~/Maestro`, sous la maison factice."""
    return (maison / "Maestro").resolve()


def _brute(**champs: Any) -> dict[str, Any]:
    """Une proposition telle que le modèle l'écrit, raisons comprises."""
    proposition: dict[str, Any] = {
        "nom": "kombucha-vitrine",
        "dossier": "",
        "origine": ORIGINE_NOUVEAU,
        "versionner": True,
        "raisons": {
            "nom": "Ce qu'il est, en deux mots.",
            "dossier": "Un dossier neuf, dans votre répertoire des projets.",
            "versionnement": "Chaque tâche sur sa branche.",
        },
    }
    proposition.update(champs)
    return proposition


# ── ① la vérification ────────────────────────────────────────────────────────


def test_un_projet_neuf_sans_dossier_se_range_sous_le_repertoire_des_projets(
    projets: ServiceProjets, _maison: Path
) -> None:
    demande = _naissance(projets).verifier(_brute())

    assert demande.racine == (_repertoire(_maison) / "kombucha-vitrine").as_posix()
    assert demande.origine == ORIGINE_NOUVEAU
    assert demande.versionner is True
    assert demande.raison_dossier == "Un dossier neuf, dans votre répertoire des projets."
    assert demande.ajustements == ()
    # Vérifier ne crée rien : le dossier naît à l'accord, pas à la proposition.
    assert not (_repertoire(_maison) / "kombucha-vitrine").exists()
    assert not _repertoire(_maison).exists()


def test_un_dossier_neuf_deja_occupe_est_remplace_par_une_variante_dite(
    projets: ServiceProjets, _maison: Path
) -> None:
    occupe = _repertoire(_maison) / "kombucha-vitrine"
    occupe.mkdir(parents=True)
    (occupe / "notes.txt").write_text("à moi", encoding="utf-8")

    demande = _naissance(projets).verifier(_brute())

    assert demande.racine == (_repertoire(_maison) / "kombucha-vitrine-2").as_posix()
    assert len(demande.ajustements) == 1
    assert "existe déjà et n'est pas vide" in demande.ajustements[0]
    assert "kombucha-vitrine-2" in demande.ajustements[0]


def test_un_dossier_neuf_vide_reste_celui_propose(
    projets: ServiceProjets, _maison: Path
) -> None:
    (_repertoire(_maison) / "kombucha-vitrine").mkdir(parents=True)

    demande = _naissance(projets).verifier(_brute())

    assert demande.racine.endswith("/kombucha-vitrine")
    assert demande.ajustements == ()


def test_un_nom_deja_pris_est_remplace_par_une_variante_dite(
    projets: ServiceProjets, _maison: Path
) -> None:
    ailleurs = _maison / "ailleurs"
    ailleurs.mkdir()
    projets.creer("Kombucha-Vitrine", str(ailleurs))

    demande = _naissance(projets).verifier(_brute())

    assert demande.nom == "kombucha-vitrine 2"
    assert any("est déjà celui d'un projet" in a for a in demande.ajustements)


def test_un_dossier_neuf_nomme_d_apres_un_nom_pris_suit_sa_variante(
    projets: ServiceProjets, _maison: Path
) -> None:
    # Vu sur la vraie stack à la relecture : « banc 2 » proposé dans « …/banc », sous
    # une raison du modèle qui disait le dossier « qui porte son nom ».
    ailleurs = _maison / "ailleurs"
    ailleurs.mkdir()
    projets.creer("Kombucha-Vitrine", str(ailleurs))

    demande = _naissance(projets).verifier(_brute())

    assert demande.nom == "kombucha-vitrine 2"
    assert demande.racine == (_repertoire(_maison) / "kombucha-vitrine-2").as_posix()
    assert any(
        "Le dossier suit le nom" in a and "kombucha-vitrine-2" in a
        for a in demande.ajustements
    )
    assert not (_repertoire(_maison) / "kombucha-vitrine-2").exists()


def test_un_dossier_neuf_nomme_autrement_reste_quand_le_nom_change(
    projets: ServiceProjets, _maison: Path
) -> None:
    ailleurs = _maison / "ailleurs"
    ailleurs.mkdir()
    projets.creer("Kombucha-Vitrine", str(ailleurs))

    demande = _naissance(projets).verifier(_brute(dossier="atelier"))

    assert demande.nom == "kombucha-vitrine 2"
    assert demande.racine == (_repertoire(_maison) / "atelier").as_posix()
    assert len(demande.ajustements) == 1


def test_git_absent_du_poste_retire_le_versionnement_et_le_dit(
    projets: ServiceProjets,
) -> None:
    demande = _naissance(projets, git_disponible=lambda: False).verifier(_brute())

    assert demande.versionner is False
    assert any("Git n'est pas disponible" in a for a in demande.ajustements)


def test_un_dossier_existant_s_importe_tel_quel_et_son_git_est_constate(
    projets: ServiceProjets, _maison: Path
) -> None:
    racines = _maison / "sites" / "racines"
    # Un `.git` minimal — ce que `detecter_vcs` lit réellement, sans lancer `git`.
    (racines / ".git").mkdir(parents=True)
    (racines / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

    demande = _naissance(projets).verifier(
        _brute(nom="", dossier=str(racines), origine=ORIGINE_EXISTANT)
    )

    assert demande.origine == ORIGINE_EXISTANT
    assert demande.racine == racines.resolve().as_posix()
    # Sans nom proposé, celui du dossier ; déjà sous Git, rien à proposer.
    assert demande.nom == "racines"
    assert demande.deja_versionne is True
    assert demande.versionner is False


def test_un_dossier_existant_introuvable_est_refuse_avec_son_motif(
    projets: ServiceProjets, _maison: Path
) -> None:
    with pytest.raises(NaissanceRefusee) as refus:
        _naissance(projets).verifier(
            _brute(dossier=str(_maison / "nulle-part"), origine=ORIGINE_EXISTANT)
        )
    assert refus.value.motif == "dossier-absent"


def test_un_dossier_deja_declare_ne_se_propose_pas_une_seconde_fois(
    projets: ServiceProjets, _maison: Path
) -> None:
    racines = _maison / "racines"
    racines.mkdir()
    projets.creer("Racines", str(racines))

    with pytest.raises(NaissanceRefusee) as refus:
        _naissance(projets).verifier(_brute(dossier=str(racines), origine=ORIGINE_EXISTANT))
    assert refus.value.motif == "deja-declare"
    assert "Racines" in str(refus.value)


def test_les_frontieres_d_ef38_refusent_la_proposition(
    projets: ServiceProjets, _maison: Path
) -> None:
    """Le dossier utilisateur nu n'est pas une racine de projet — ni neuve, ni importée."""
    with pytest.raises(NaissanceRefusee) as refus:
        _naissance(projets).verifier(_brute(dossier=str(_maison), origine=ORIGINE_EXISTANT))
    assert refus.value.motif == "dossier-utilisateur-nu"


def test_les_faits_du_poste_disent_le_repertoire_les_projets_et_la_fenetre(
    projets: ServiceProjets, _maison: Path
) -> None:
    racines = _maison / "racines"
    racines.mkdir()
    fiche = projets.creer("Racines", str(racines))

    sans_fenetre = _naissance(projets).contexte(None)
    avec_fenetre = _naissance(projets).contexte(str(fiche["id"]))

    assert _repertoire(_maison).as_posix() in sans_fenetre
    assert "« Racines »" in sans_fenetre
    assert "projet de cette fenêtre : aucun" in sans_fenetre
    assert "projet de cette fenêtre : « Racines »" in avec_fenetre
    assert "Git sur le poste : disponible" in sans_fenetre
    # Lire les faits ne crée pas le répertoire : on a juste parlé au fil.
    assert not _repertoire(_maison).exists()


# ── ② la déclaration ─────────────────────────────────────────────────────────


@avec_git
@pytest.mark.usefixtures("_git_isole")
def test_un_projet_neuf_accorde_nait_sous_git(
    projets: ServiceProjets, _maison: Path
) -> None:
    naissance = _naissance(projets)
    demande = naissance.verifier(_brute())

    cree = asyncio.run(naissance.declarer(demande))

    racine = _repertoire(_maison) / "kombucha-vitrine"
    assert racine.is_dir() and (racine / ".git").exists()
    assert cree.versionne is True and cree.versionnement_refuse == ""
    assert cree.origine == ORIGINE_NOUVEAU
    assert [f["id"] for f in projets.lister()] == [cree.id]


def test_un_projet_neuf_sans_versionnement_nait_sans_git(
    projets: ServiceProjets, _maison: Path
) -> None:
    naissance = _naissance(projets)
    demande = naissance.verifier(_brute(versionner=False))

    cree = asyncio.run(naissance.declarer(demande))

    assert cree.versionne is False
    assert not (_repertoire(_maison) / "kombucha-vitrine" / ".git").exists()


def test_un_dossier_importe_n_est_pas_touche(projets: ServiceProjets, _maison: Path) -> None:
    racines = _maison / "racines"
    racines.mkdir()
    (racines / "index.html").write_text("<h1>Racines</h1>", encoding="utf-8")
    avant = sorted(p.name for p in racines.iterdir())
    naissance = _naissance(projets)
    demande = naissance.verifier(
        _brute(nom="racines", dossier=str(racines), origine=ORIGINE_EXISTANT, versionner=False)
    )

    cree = asyncio.run(naissance.declarer(demande))

    assert cree.origine == ORIGINE_EXISTANT
    assert sorted(p.name for p in racines.iterdir()) == avant


# ── ③ le canal ───────────────────────────────────────────────────────────────


def _dicte(reponse: str, nom: str, *, projet: Mapping[str, Any] | None = None) -> str:
    """Le contrat du juge : la prose, puis la dernière ligne marquée."""
    charge: dict[str, Any] = {"verdict": nom, "objectif": ""}
    if projet is not None:
        charge["projet"] = dict(projet)
    return f"{reponse}\n{_MARQUEUR_VERDICT} {json.dumps(charge, ensure_ascii=False)}"


class ModeleScripte(ModelProvider):
    """Le juge rend ce qu'on lui dicte ; la rédaction rend `REDIGE`. Les prompts sont notés."""

    name = "modele-scripte"

    def __init__(self, *verdicts: str) -> None:
        self.verdicts = list(verdicts)
        self.prompts: list[str] = []
        self.redactions: list[str] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt: str, *, model: str, system_prompt: str | None = None) -> str:
        if system_prompt == _PROMPT_REDACTION:
            self.redactions.append(prompt)
            return REDIGE
        self.prompts.append(prompt)
        return self.verdicts.pop(0) if self.verdicts else _dicte("…", VERDICT_ECHANGE)


def _repondeur(
    projets: ServiceProjets, modele: ModeleScripte, **naissance: Any
) -> RepondeurOrchestration:
    return RepondeurOrchestration(provider=modele, naissance=_naissance(projets, **naissance))


def _message(auteur: str, contenu: str, **champs: Any) -> MessageChat:
    return MessageChat(agent=NOM_ORCHESTRATION, auteur=auteur, contenu=contenu, **champs)


def _propose(projets: ServiceProjets, **brute: Any) -> MessageChat:
    """Le message d'orchestration qui porte une carte de projet, comme le fil la garde."""
    demande = _naissance(projets).verifier(_brute(**brute))
    return _message(NOM_ORCHESTRATION, "Je vous propose ce projet.", projet_propose=demande)


def test_le_verdict_projet_pose_une_carte_verifiee_et_ne_declare_rien(
    projets: ServiceProjets, _maison: Path
) -> None:
    modele = ModeleScripte(
        _dicte("Un site vitrine, je vous le propose.", VERDICT_PROJET, projet=_brute())
    )

    reponse = asyncio.run(
        _repondeur(projets, modele).produire(
            AGENT_ORCHESTRATION, [_message(UTILISATEUR, KOMBUCHA)]
        )
    )

    assert reponse.projet_propose is not None
    assert reponse.projet_propose.racine.endswith("/kombucha-vitrine")
    assert reponse.contenu == "Un site vitrine, je vous le propose."
    assert projets.lister() == []
    # Les faits du poste ont atteint le juge : c'est avec eux qu'il propose.
    assert "Les projets de ce poste :" in modele.prompts[0]
    assert "projet de cette fenêtre : aucun" in modele.prompts[0]


def test_une_proposition_refusee_par_la_verification_ne_pose_aucune_carte(
    projets: ServiceProjets, _maison: Path
) -> None:
    modele = ModeleScripte(
        _dicte(
            "Je l'importe.",
            VERDICT_PROJET,
            projet=_brute(dossier=str(_maison / "nulle-part"), origine=ORIGINE_EXISTANT),
        )
    )

    reponse = asyncio.run(
        _repondeur(projets, modele).produire(
            AGENT_ORCHESTRATION, [_message(UTILISATEUR, "J'ai déjà un dossier nulle-part")]
        )
    )

    assert reponse.projet_propose is None
    assert reponse.contenu.startswith("Je l'importe.")
    assert "Je ne peux pas vous proposer ce projet tel quel" in reponse.contenu
    assert "n'existe pas" in reponse.contenu


def test_une_correction_tapee_repropose_sans_rien_ecrire(
    projets: ServiceProjets, _maison: Path
) -> None:
    """« appelle-le racines » : une proposition nouvelle, jamais une déclaration."""
    fil = [
        _message(UTILISATEUR, KOMBUCHA),
        _propose(projets),
        _message(UTILISATEUR, "appelle-le racines"),
    ]
    modele = ModeleScripte(
        _dicte("D'accord, « racines ».", VERDICT_PROJET, projet=_brute(nom="racines"))
    )

    reponse = asyncio.run(_repondeur(projets, modele).produire(AGENT_ORCHESTRATION, fil))

    assert reponse.projet_propose is not None
    assert reponse.projet_propose.nom == "racines"
    assert reponse.projet_propose.racine.endswith("/racines")
    assert projets.lister() == []
    # Le juge a relu ce que la carte montrait, pas seulement la phrase.
    assert "[Projet proposé sur la carte : nom « kombucha-vitrine »" in modele.prompts[0]


def test_un_oui_tape_declare_ce_que_la_carte_montrait(
    projets: ServiceProjets, _maison: Path
) -> None:
    fil = [
        _message(UTILISATEUR, KOMBUCHA),
        _propose(projets, versionner=False),
        _message(UTILISATEUR, "oui"),
    ]
    modele = ModeleScripte(_dicte("Je déclare le projet.", VERDICT_ACCORD))

    reponse = asyncio.run(_repondeur(projets, modele).produire(AGENT_ORCHESTRATION, fil))

    assert reponse.projet_cree is not None
    assert reponse.projet_cree.nom == "kombucha-vitrine"
    assert (_repertoire(_maison) / "kombucha-vitrine").is_dir()
    assert [f["id"] for f in projets.lister()] == [reponse.projet_cree.id]
    # Un projet neuf n'a rien à ajouter aux mots du juge : sa fiche est sous la bulle.
    assert reponse.contenu == "Je déclare le projet."


def test_un_oui_sans_carte_juste_avant_ne_declare_rien(
    projets: ServiceProjets, _maison: Path
) -> None:
    fil = [
        _message(UTILISATEUR, KOMBUCHA),
        _propose(projets),
        _message(UTILISATEUR, "attends"),
        _message(NOM_ORCHESTRATION, "Je vous écoute."),
        _message(UTILISATEUR, "oui"),
    ]
    modele = ModeleScripte(_dicte("D'accord.", VERDICT_ACCORD))

    reponse = asyncio.run(_repondeur(projets, modele).produire(AGENT_ORCHESTRATION, fil))

    assert reponse.projet_cree is None
    assert projets.lister() == []


def test_le_geste_d_accord_declare_et_le_modele_en_parle(
    projets: ServiceProjets, _maison: Path
) -> None:
    propose = _propose(projets, versionner=False)
    modele = ModeleScripte()

    reponse = asyncio.run(
        _repondeur(projets, modele).declarer_projet(
            AGENT_ORCHESTRATION,
            [propose, _message(UTILISATEUR, "Oui, crée ce projet.")],
            demande=propose.projet_propose,
            approuve=True,
        )
    )

    assert reponse.contenu == REDIGE
    assert reponse.projet_cree is not None
    assert (_repertoire(_maison) / "kombucha-vitrine").is_dir()
    faits = modele.redactions[0]
    assert "« kombucha-vitrine » est déclaré" in faits
    assert "Son dossier a été créé, vide." in faits


def test_le_geste_de_refus_ne_cree_rien(projets: ServiceProjets, _maison: Path) -> None:
    propose = _propose(projets)
    modele = ModeleScripte()

    reponse = asyncio.run(
        _repondeur(projets, modele).declarer_projet(
            AGENT_ORCHESTRATION,
            [propose, _message(UTILISATEUR, "Non, ne crée pas ce projet.")],
            demande=propose.projet_propose,
            approuve=False,
        )
    )

    assert reponse.contenu == REDIGE and reponse.projet_cree is None
    assert projets.lister() == []
    assert not (_repertoire(_maison) / "kombucha-vitrine").exists()
    assert "Rien n'a été créé" in modele.redactions[0]


def test_un_dossier_importe_est_lu_apres_l_accord_et_ce_qu_on_en_a_compris_nourrit_la_reponse(
    projets: ServiceProjets, _maison: Path
) -> None:
    racines = _maison / "racines"
    racines.mkdir()
    lus: list[str] = []

    async def lecteur(projet_id: str) -> Mapping[str, Any]:
        # Lu **après** la déclaration : le projet existe déjà quand on le lit.
        assert [f["id"] for f in projets.lister()] == [projet_id]
        lus.append(projet_id)
        return {
            "resume": "Un site statique en HTML",
            "constats": {"langages": [{"nom": "HTML"}], "commandes": []},
        }

    propose = _propose(
        projets, nom="racines", dossier=str(racines), origine=ORIGINE_EXISTANT, versionner=False
    )
    modele = ModeleScripte()

    reponse = asyncio.run(
        _repondeur(projets, modele, lecteur=lecteur).declarer_projet(
            AGENT_ORCHESTRATION, [propose], demande=propose.projet_propose, approuve=True
        )
    )

    assert reponse.projet_cree is not None and lus == [reponse.projet_cree.id]
    faits = modele.redactions[0]
    assert "Son dossier existant est importé tel quel" in faits
    assert "Résumé de la lecture : Un site statique en HTML" in faits
    assert "Langages : HTML" in faits


@avec_git
@pytest.mark.usefixtures("_git_isole")
def test_un_import_mis_sous_git_ne_se_dit_pas_intouche(
    projets: ServiceProjets, _maison: Path
) -> None:
    """Constaté sur la vraie stack : « rien n'y a été écrit » alors que la mise sous Git
    venait d'y créer `.git` et un premier commit. Les faits disent ce qui a été écrit."""
    racines = _maison / "racines"
    racines.mkdir()
    (racines / "index.html").write_text("<h1>Racines</h1>", encoding="utf-8")
    propose = _propose(
        projets, nom="racines", dossier=str(racines), origine=ORIGINE_EXISTANT, versionner=True
    )
    modele = ModeleScripte()

    reponse = asyncio.run(
        _repondeur(projets, modele).declarer_projet(
            AGENT_ORCHESTRATION, [propose], demande=propose.projet_propose, approuve=True
        )
    )

    assert reponse.projet_cree is not None and reponse.projet_cree.versionne
    assert (racines / ".git").exists()
    faits = modele.redactions[0]
    assert "rien n'y a été écrit" not in faits
    assert "sa mise sous Git" in faits


def test_une_declaration_empechee_reposee_la_proposition_sans_rien_creer(
    projets: ServiceProjets, _maison: Path
) -> None:
    racines = _maison / "racines"
    racines.mkdir()
    propose = _propose(
        projets, nom="racines", dossier=str(racines), origine=ORIGINE_EXISTANT, versionner=False
    )
    racines.rmdir()  # le dossier disparaît entre la proposition et le clic

    reponse = asyncio.run(
        _repondeur(projets, ModeleScripte()).declarer_projet(
            AGENT_ORCHESTRATION, [propose], demande=propose.projet_propose, approuve=True
        )
    )

    assert reponse.projet_cree is None
    assert reponse.projet_propose == propose.projet_propose
    assert reponse.contenu.startswith("Je n'ai créé aucun projet :")
    assert projets.lister() == []


def test_sans_naissance_branchee_le_verdict_projet_le_dit_sans_carte() -> None:
    modele = ModeleScripte(_dicte("Je vous le propose.", VERDICT_PROJET, projet=_brute()))

    reponse = asyncio.run(
        RepondeurOrchestration(provider=modele).produire(
            AGENT_ORCHESTRATION, [_message(UTILISATEUR, KOMBUCHA)]
        )
    )

    assert reponse.projet_propose is None
    assert "Aucune déclaration de projet n'est branchée" in reponse.contenu


# ── ④ la route ───────────────────────────────────────────────────────────────


@pytest.fixture()
def client_fil(projets: ServiceProjets, tmp_path: Path):
    """L'app avec un fil d'orchestration dont le juge propose puis accorde."""
    modele = ModeleScripte(
        _dicte(
            "Un site vitrine : je vous le propose.",
            VERDICT_PROJET,
            projet=_brute(versionner=False),
        )
    )
    depot = ChatStore(tmp_path / "chat")
    app = create_app(
        bus=InMemoryEventBus(),
        state=ControlTowerState(),
        chat_store=depot,
        projets=projets,
        orchestration_repondeur=_repondeur(projets, modele),
    )
    with TestClient(app) as client:
        yield client, depot


def test_la_route_declare_le_projet_propose_et_refuse_le_double_clic(
    client_fil, _maison: Path
) -> None:
    client, depot = client_fil
    envoi = client.post(f"/api/chat/{NOM_ORCHESTRATION}/messages", json={"contenu": KOMBUCHA})
    assert envoi.status_code == 201
    proposee = envoi.json()["messages"][1]["projet_propose"]
    assert proposee["nom"] == "kombucha-vitrine" and proposee["versionner"] is False
    assert client.get("/api/projets").json() == []

    accord = client.post(f"/api/chat/{NOM_ORCHESTRATION}/projet", json={"approuve": True})

    assert accord.status_code == 201
    geste, reponse = accord.json()["messages"]
    assert geste["auteur"] == UTILISATEUR and geste["contenu"] == "Oui, crée ce projet."
    cree = reponse["projet_cree"]
    assert cree["nom"] == "kombucha-vitrine"
    assert [p["id"] for p in client.get("/api/projets").json()] == [cree["id"]]
    # Le fait survit à la relecture du fil : c'est lui que l'écran relit.
    relu = depot.fil(NOM_ORCHESTRATION)[-1]
    assert relu.projet_cree == ProjetCree.from_dict(cree)
    # Le double clic ne déclare pas un second projet.
    assert (
        client.post(f"/api/chat/{NOM_ORCHESTRATION}/projet", json={"approuve": True}).status_code
        == 409
    )


def test_la_route_sans_proposition_en_attente_rend_409(client_fil) -> None:
    client, _ = client_fil
    reponse = client.post(f"/api/chat/{NOM_ORCHESTRATION}/projet", json={"approuve": True})
    assert reponse.status_code == 409


def test_le_geste_sur_un_import_dit_importer_pas_creer(
    projets: ServiceProjets, tmp_path: Path, _maison: Path
) -> None:
    # Vu à la relecture : « Importer le projet » s'écrivait « Oui, crée ce projet. »
    # dans le fil, juste au-dessus de la trace « Projet importé ».
    racines = _maison / "racines"
    racines.mkdir()
    modele = ModeleScripte(
        _dicte(
            "Je l'importe ?",
            VERDICT_PROJET,
            projet=_brute(nom="racines", dossier=str(racines), origine=ORIGINE_EXISTANT),
        )
    )
    app = create_app(
        bus=InMemoryEventBus(),
        state=ControlTowerState(),
        chat_store=ChatStore(tmp_path / "chat"),
        projets=projets,
        orchestration_repondeur=_repondeur(projets, modele),
    )
    with TestClient(app) as client:
        envoi = client.post(
            f"/api/chat/{NOM_ORCHESTRATION}/messages", json={"contenu": "J'ai déjà racines."}
        )
        assert envoi.json()["messages"][1]["projet_propose"]["origine"] == ORIGINE_EXISTANT

        refus = client.post(f"/api/chat/{NOM_ORCHESTRATION}/projet", json={"approuve": False})

    assert refus.status_code == 201
    geste, _ = refus.json()["messages"]
    assert geste["contenu"] == "Non, n'importe pas ce projet."
    assert projets.lister() == []


# ── ⑤ le message ─────────────────────────────────────────────────────────────


def test_la_carte_et_le_fait_se_relisent_a_l_identique() -> None:
    demande = DemandeProjet(
        nom="racines",
        racine="C:/sites/racines",
        origine=ORIGINE_EXISTANT,
        deja_versionne=True,
        raison_nom="Le nom que vous avez donné.",
        ajustements=("Un ajustement.",),
    )
    cree = ProjetCree(id="p-1", nom="racines", racine="C:/sites/racines", origine=ORIGINE_EXISTANT)
    message = _message(NOM_ORCHESTRATION, "Voilà.", projet_propose=demande, projet_cree=cree)

    relu = MessageChat.from_dict(json.loads(json.dumps(message.to_ligne())))

    assert relu.projet_propose == demande and relu.projet_cree == cree
    # Une ligne écrite avant #1294 se relit sans carte ni fait.
    ancienne = MessageChat.from_dict({"agent": NOM_ORCHESTRATION, "contenu": "x"})
    assert ancienne.projet_propose is None and ancienne.projet_cree is None


def test_une_carte_de_projet_attend_tant_que_rien_ne_l_a_suivie() -> None:
    demande = DemandeProjet(nom="a", racine="C:/a", origine=ORIGINE_NOUVEAU)
    carte = _message(NOM_ORCHESTRATION, "Voilà.", projet_propose=demande)

    assert projet_en_attente([carte]) is carte
    assert projet_en_attente([carte, _message(UTILISATEUR, "oui")]) is None
    assert "[Projet proposé sur la carte : nom « a », dossier neuf C:/a" in transcription([carte])

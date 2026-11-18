"""Le banc comparatif : un même objectif sur Maestro et sur un agent seul, et chaque livrable
rejoué sur un clone propre (#1512).

Ce qui est gardé ici, sans réseau ni modèle :

① l'objectif — p5 porte le message de C3 et les choix de la personne, et la personne répond
   aux questions d'outillage par ses choix, avec ses mots ;
② le côté Maestro — l'objectif passe par le fil, chaque geste de la personne est compté, et
   celui que le banc fait pour lui-même ne l'est pas ; le coût vient du grand livre du
   projet ; une limite d'usage est un empêchement, un run en échec une perte ;
③ le côté de l'agent seul — le même message, par l'adaptateur, dans un établi de l'atelier,
   son coût rapporté par l'adaptateur ;
④ la grille — un livrable se rejoue sur un **clone**, par les commandes qu'il déclare ;
⑤ ce qui se compare — médianes et dispersion, sans les empêchements ;
⑥ la commande — le coût annoncé avant de jouer, les côtés alternés, le rapport écrit, les
   refus et `--rejouer` ;
⑦ le projet long (#1513) — dit sans choix technique, avec ses critères et la feuille de la
   personne ; Maestro passe la nuit éteint puis reprend son run, la personne répond aux
   questions des agents ; la session seule est fermée le soir puis rouverte, la personne
   lit chaque fin et répond ; les critères se jugent sur le clone, et un dossier non
   versionné se rejoue moins ce qu'il ignore.

Les doubles : une fausse API du fil (`FilFactice`), un faux fournisseur
(`FournisseurFactice`), un faux lecteur de commandes et de verdicts (`LecteurFactice`), un
poste qui ne lance rien (`PosteDuBanc` aux gestes doublés). Les dépôts, eux, sont de vrais
dépôts git jetables : un clone ne se double pas.
"""

from __future__ import annotations

import asyncio
import io
import json
import re
import shutil
import socket
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from maestro.agents.runtime import DEFAULT_TOOLS
from maestro.controltower.causes import CAUSE_LIMITE_USAGE, CAUSE_TACHES_EN_ECHEC
from maestro.controltower.state import (
    EXECUTION_ECHEC,
    EXECUTION_EN_ATTENTE_ARBITRAGE,
    EXECUTION_EN_COURS,
    EXECUTION_TERMINEE,
    VALIDATION_EN_ATTENTE,
)
from maestro.providers.base import LimiteUsageAtteinte, SessionAgent
from maestro.sandbox.verification import Execution
from maestro.scenarios import comparatif as cmp
from maestro.scenarios import couverture as cv
from maestro.scenarios import grille as gr
from maestro.scenarios.api import FIL, ErreurAPI, Reponse
from maestro.scenarios.gestes import REPONSE_OUTILLAGE, port_de_service
from maestro.scenarios.juge import Avis, JugeModele
from maestro.scenarios.modele import Journal
from maestro.scenarios.projets import Atelier
from maestro.telemetry import StepUsage, report_usage

AVEC_GIT = pytest.mark.skipif(shutil.which("git") is None, reason="git absent du poste")

#: L'identité des commits des dépôts jetables — celle d'un poste sans identité git (#333).
_IDENTITE_GIT = (
    "-c",
    "user.name=banc",
    "-c",
    "user.email=banc@exemple.invalid",
    "-c",
    "commit.gpgsign=false",
)


def _git(racine: Path, *arguments: str) -> str:
    fini = subprocess.run(
        ["git", *_IDENTITE_GIT, "-C", str(racine), *arguments],
        capture_output=True,
        text=True,
        check=True,
    )
    return fini.stdout.strip()


def _depot(racine: Path, message: str = "feat: socle", **fichiers: str) -> Path:
    """Un dépôt git jetable, ses fichiers commités sous `message`."""
    racine.mkdir(parents=True, exist_ok=True)
    if not (racine / ".git").exists():
        _git(racine, "init", "-q", "-b", "main")
    for nom, contenu in fichiers.items():
        chemin = racine / nom
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_text(contenu, encoding="utf-8")
    _git(racine, "add", "-A")
    _git(racine, "commit", "--allow-empty", "-q", "-m", message)
    return racine


def _historique(racine: Path, sujets: Sequence[str]) -> None:
    """Des commits vides au bout de `main`, en un seul `git fast-import`.

    Un long historique se fabrique en un processus : un `git commit` par sujet coûterait
    une seconde par dizaine sous Windows.
    """
    flux = bytearray()
    for rang, sujet in enumerate(sujets):
        message = sujet.encode("utf-8")
        flux += (
            f"commit refs/heads/main\n"
            f"committer banc <banc@exemple.invalid> {1_700_000_000 + rang} +0000\n"
            f"data {len(message)}\n"
        ).encode()
        flux += message + b"\n"
        if rang == 0:
            flux += b"from refs/heads/main^0\n"
        flux += b"\n"
    subprocess.run(
        ["git", "-C", str(racine), "fast-import", "--quiet"], input=bytes(flux), check=True
    )


class Horloge:
    """Une horloge que le sommeil fait avancer : aucune attente réelle."""

    def __init__(self) -> None:
        self.maintenant = 1000.0

    def __call__(self) -> float:
        return self.maintenant

    def dormir(self, secondes: float) -> None:
        self.maintenant += secondes


# =================================================================================================
# ① L'objectif
# =================================================================================================


def test_l_objectif_p5_porte_le_message_de_c3_et_les_choix_de_la_personne() -> None:
    objectif = cmp.charger_objectif("p5")
    assert objectif.nom == "p5"
    assert "livre de recette moderne" in objectif.message
    assert "Construis l'application entière." in objectif.message
    assert "messages de commit : Conventional Commits" in objectif.message
    # Le message ne nomme aucun dossier : chaque côté le reçoit tel quel, là où il travaille.
    assert "p5-seule" not in objectif.message and "Ce dossier" not in objectif.message
    assert len(objectif.choix) == 9
    assert objectif.versionner is True
    assert objectif.axes == gr.AXES
    assert objectif.demande_de_travail == objectif.message
    assert set(objectif.cout_estime_usd) == {cmp.COTE_MAESTRO, cmp.COTE_SEUL}
    assert "p5" in cmp.objectifs_connus()


def test_la_personne_repond_aux_questions_d_outillage_par_ses_choix_avec_ses_mots() -> None:
    """Pas d'option de la carte, et pas d'option cherchée par ses mots (#746) : ses choix."""
    objectif = cmp.charger_objectif("p5")
    valeur, libre = objectif.repondre({"cle": "tests", "options": [{"valeur": "jest"}]})
    assert libre is True
    for un in objectif.choix:
        assert un in valeur
    sans_choix = cmp.Objectif(nom="x", titre="x", message="Fais-le.")
    assert sans_choix.repondre({"cle": "pile"}) == (REPONSE_OUTILLAGE, True)


@pytest.mark.parametrize(
    "donnees, motif",
    [
        ({}, "message"),
        ({"message": "  "}, "message"),
        ({"message": "ok", "choix": "TypeScript"}, "choix"),
        ({"message": "ok", "axes": ["installation", "vitesse"]}, "axes"),
        ({"message": "ok", "cout_estime_usd": {"seul": "cher"}}, "cout_estime_usd"),
    ],
)
def test_un_objectif_mal_forme_est_refuse(donnees: dict[str, Any], motif: str) -> None:
    with pytest.raises(ValueError, match=motif):
        cmp.objectif_depuis("x", donnees)


def test_un_objectif_inconnu_dit_ceux_qui_existent() -> None:
    with pytest.raises(ValueError, match="connus : .*p5"):
        cmp.charger_objectif("inexistant")


def test_un_objectif_restreint_la_grille_dans_son_ordre() -> None:
    objectif = cmp.objectif_depuis("x", {"message": "ok", "axes": ["tests", "installation"]})
    assert objectif.axes == (gr.AXE_INSTALLATION, gr.AXE_TESTS)


# =================================================================================================
# ② Le côté Maestro
# =================================================================================================


def _role() -> dict[str, Any]:
    return {
        "nom": "dev-nextjs",
        "role": "Développeur",
        "competences": ["typescript"],
        "playbook": "# playbook\n",
        "instances": 1,
        "gabarit": "developpeur",
        "skills": [],
        "politique": {"Bash": "auto"},
    }


class FilFactice:
    """Le fil de l'orchestrateur, réduit à ce que le côté Maestro du banc parcourt.

    Un projet y naît d'une phrase — proposé d'abord ailleurs, puis rangé par une correction
    en mots —, s'outille d'une question puis d'une pièce, se dote d'une équipe, puis le run
    part, demande un arbitrage, et se solde comme le test le veut. `moteur` est ce que le
    run fait sur le disque. `couts_projet` est le total du grand livre du projet (`None` :
    la vue répond 404).
    """

    def __init__(
        self,
        ailleurs: Path,
        *,
        horloge: Horloge,
        statut: str = EXECUTION_TERMINEE,
        cause: str = "",
        cout_run: float | None = 10.0,
        couts_projet: float | None = 12.5,
        duree_run_s: float = 600.0,
        moteur: Callable[[Path], None] | None = None,
    ) -> None:
        self._ailleurs = ailleurs
        self._horloge = horloge
        self._statut, self._cause = statut, cause
        self._cout_run, self._couts_projet = cout_run, couts_projet
        self._duree_run_s = duree_run_s
        self._moteur = moteur
        self.sans_projet: list[str] = []
        self.sur_le_projet: list[str] = []
        self.reponses: list[dict[str, Any]] = []
        self.pieces: list[str] = []
        self.decisions: list[str] = []
        self._nouvelle_conversation()

    def _nouvelle_conversation(self) -> None:
        """Une conversation neuve : un projet à faire naître, sans équipe ni run."""
        self.racine: Path | None = None
        self._equipe = False
        self._run_lu = 0
        self._question = True
        self._piece = True
        self._valide = False
        self._fil: list[dict[str, Any]] = []

    def demander(  # noqa: C901
        self,
        methode: str,
        chemin: str,
        *,
        corps: Mapping[str, Any] | None = None,
        params: Mapping[str, str] | None = None,
        delai_s: float | None = None,
    ) -> Reponse:
        corps = dict(corps or {})
        if chemin == "/api/sante":
            return Reponse(200, {"statut": "ok", "espace": "commun"})
        if chemin == f"{FIL}/conversations":
            self._nouvelle_conversation()
            return Reponse(201, {"conversation": {"id": "conv-1"}})
        if chemin == f"{FIL}/messages":
            return self._message(corps)
        if chemin == f"{FIL}/projet":
            assert self.racine is not None
            self.racine.mkdir(parents=True, exist_ok=True)
            cree = {"id": "prj-1", "racine": self.racine.as_posix(), "versionne": True}
            return self._paire({"projet_cree": cree, **self._suite()})
        if chemin == f"{FIL}/outillage":
            self.reponses.append({"valeur": corps.get("valeur"), "libre": corps.get("libre")})
            self._question = False
            return self._paire(self._suite())
        if chemin == f"{FIL}/outillage/piece":
            assert self.racine is not None
            (self.racine / "AGENTS.md").write_text("# Agents\n", encoding="utf-8")
            self.pieces.append(str(corps.get("piece")))
            self._piece = False
            ecrite = {"chemin": "AGENTS.md", "etat": "ecrit", "ecrite": True}
            return self._paire({"piece_ecrite": ecrite})
        if chemin == "/api/projets/prj-1/equipe/proposition":
            return Reponse(200, {"id": "prop-1", "roles": [_role()]})
        if chemin == f"{FIL}/recrutement":
            self._equipe = True
            return self._paire({"proposition": "l'objectif"})
        if chemin == f"{FIL}/cadrage":
            if self._moteur is not None and self.racine is not None:
                self._moteur(self.racine)
            return self._paire({"run_id": "run-1"})
        if chemin == "/api/executions/run-1":
            return Reponse(200, self._run())
        if chemin == "/api/validations":
            demande = {
                "tache_id": "socle",
                "run_id": "run-1",
                "statut": "approuvee" if self._valide else VALIDATION_EN_ATTENTE,
                "outil": "Bash",
                "arguments": {"command": "npm install"},
                "titre": "Socle",
                "horodatage": "h1",
            }
            return Reponse(200, [demande])
        if chemin == "/api/validations/socle/decision":
            self.decisions.append("socle")
            self._valide = True
            return Reponse(200, {})
        if chemin == FIL:
            return Reponse(200, {"messages": list(self._fil)})
        if chemin == "/api/analytics/couts":
            if self._couts_projet is None:
                return Reponse(404, {}, "inconnu")
            assert params == {"projet": "prj-1"}
            # La forme de la vue depuis #1534 : le fil a son poste, à part de l'orchestration.
            return Reponse(
                200,
                {
                    "total": {"cout_usd": self._couts_projet},
                    "orchestration": {"agent": "maestro", "usage": {"cout_usd": 0.9}},
                    "fil": {"agent": "fil", "usage": {"cout_usd": 1.6}},
                    "agents": [],
                },
            )
        raise AssertionError(f"le fil factice ne connaît pas {methode} {chemin}")

    def flux(self, chemin: str, **_: Any) -> Any:
        raise AssertionError(f"le côté Maestro ne lit pas le flux : {chemin}")

    def _message(self, corps: Mapping[str, Any]) -> Reponse:
        contenu = str(corps.get("contenu") or "")
        if not corps.get("projet_id"):
            self.sans_projet.append(contenu)
            nomme = re.search(r"dans le dossier (.+)\.$", contenu)
            racine = Path(nomme.group(1)) if nomme else self._ailleurs
            if nomme:
                self.racine = racine
            proposee = {"nom": "recettes", "racine": racine.as_posix(), "versionner": True}
            return self._paire({"projet_propose": proposee})
        self.sur_le_projet.append(contenu)
        if not self._equipe:
            return self._paire({"recrutement": {"objectif": contenu}, "proposition": ""})
        return self._paire({"proposition": contenu})

    def _suite(self) -> dict[str, Any]:
        if self._question:
            return {"question": {"cle": "tests", "intitule": "Quels tests ?", "options": []}}
        if self._piece:
            return {"piece": {"chemin": "AGENTS.md", "empreinte": "emp-1", "rang": 1, "total": 1}}
        return {}

    def _run(self) -> dict[str, Any]:
        self._run_lu += 1
        if self._run_lu == 1:
            return {"statut": EXECUTION_EN_ATTENTE_ARBITRAGE, "cout_usd": None}
        self._horloge.maintenant += self._duree_run_s
        if not self._fil:
            self._fil.append({"auteur": "orchestrateur", "run_id": "run-1", "contenu": "lancé"})
            self._fil.append({"auteur": "orchestrateur", "run_id": "run-1", "contenu": "fini"})
        return {
            "statut": self._statut,
            "cause": self._cause,
            "cout_usd": self._cout_run,
            "nb_taches": 3,
        }

    def _paire(self, reponse: Mapping[str, Any]) -> Reponse:
        message = {"auteur": "orchestrateur", "contenu": "…", "run_id": "", **dict(reponse)}
        return Reponse(201, {"messages": [{"auteur": "utilisateur"}, message]})


def _maestro(
    tmp_path: Path, **reglages: Any
) -> tuple[cmp.Passage, FilFactice, Atelier, cmp.Objectif]:
    horloge = Horloge()
    fil = FilFactice(tmp_path / "Maestro" / "recettes", horloge=horloge, **reglages)
    atelier = Atelier(tmp_path / "atelier")
    objectif = cmp.charger_objectif("p5")
    passage = cmp.jouer_maestro(
        objectif,
        rang=1,
        client=cmp.ClientCompteur(fil),
        atelier=atelier,
        juge=JugeModele(),
        journal=Journal(),
        horloge=horloge,
        dormir=horloge.dormir,
    )
    return passage, fil, atelier, objectif


def test_le_cote_maestro_confie_l_objectif_par_le_fil_et_compte_les_gestes(
    tmp_path: Path,
) -> None:
    passage, fil, atelier, objectif = _maestro(tmp_path)

    assert passage.issue == cmp.ABOUTI and passage.compte
    # Le message part tel quel dans le fil sans projet, puis redemandé une fois le projet né.
    assert fil.sans_projet[0] == objectif.message
    assert fil.sur_le_projet == [objectif.message]
    # Le projet est rangé dans l'atelier du banc : c'est la correction, faite pour lui.
    assert fil.racine == atelier.racine / "maestro-1"
    assert passage.livrable == str(atelier.racine / "maestro-1")
    # La question d'outillage reçoit les choix de la personne, avec ses mots.
    assert fil.reponses == [{"valeur": objectif.reponse_outillage, "libre": True}]
    # Chaque geste de la personne, dans l'ordre — et pas la correction du banc.
    assert passage.gestes == (
        cmp.GESTE_MESSAGE,
        cmp.GESTE_PROJET,
        cmp.GESTE_QUESTION,
        cmp.GESTE_PIECE,
        cmp.GESTE_MESSAGE,
        cmp.GESTE_EQUIPE,
        cmp.GESTE_RUN,
        cmp.GESTE_ARBITRAGE,
    )
    assert fil.decisions == ["socle"]
    assert passage.run_id == "run-1" and passage.projet_id == "prj-1"
    # Le coût du grand livre du projet, pas celui du run seul.
    assert passage.cout_usd == 12.5
    assert passage.cout_source.startswith("grand livre du projet")
    # Et ses postes hors des agents : ce que le fil et la conduite du run ont coûté (#1534).
    assert dict(passage.cout_postes) == {"orchestration": 0.9, "fil": 1.6}
    # La durée court jusqu'au run soldé, sur l'horloge du banc.
    assert passage.duree_s is not None and passage.duree_s >= 600.0
    assert any(etape.libelle == "run soldé" for etape in passage.etapes)


def test_le_cout_de_maestro_retombe_sur_le_run_sans_grand_livre_du_projet(
    tmp_path: Path,
) -> None:
    passage, *_ = _maestro(tmp_path, couts_projet=None)
    assert passage.cout_usd == 10.0
    assert passage.cout_source == "grand livre du run"
    assert dict(passage.cout_postes) == {}


def test_un_run_coupe_par_la_limite_d_usage_est_un_empechement(tmp_path: Path) -> None:
    """Lu dans la `cause` du run, un champ typé — jamais dans un texte (docs/44)."""
    passage, *_ = _maestro(tmp_path, statut=EXECUTION_ECHEC, cause=CAUSE_LIMITE_USAGE)
    assert passage.issue == cmp.EMPECHE
    assert not passage.compte


def test_un_run_en_echec_est_une_perte_comptee(tmp_path: Path) -> None:
    passage, *_ = _maestro(tmp_path, statut=EXECUTION_ECHEC, cause=CAUSE_TACHES_EN_ECHEC)
    assert passage.issue == cmp.NON_ABOUTI
    assert passage.compte
    assert CAUSE_TACHES_EN_ECHEC in passage.motif
    assert passage.cout_usd == 12.5


def test_le_compteur_ne_compte_pas_ce_que_le_banc_fait_pour_lui_meme() -> None:
    client = cmp.ClientCompteur(FilFactice(Path("ailleurs"), horloge=Horloge()))
    with client.montage():
        client.envoyer("range-le", projet_id="", conversation="conv-1")
    client.envoyer("bonjour", projet_id="", conversation="conv-1")
    assert client.gestes == [cmp.GESTE_MESSAGE]


# =================================================================================================
# ③ Le côté de l'agent seul
# =================================================================================================


class FournisseurFactice:
    """L'adaptateur, réduit à `run_agent` : il écrit le livrable et rapporte son usage."""

    def __init__(
        self,
        *,
        leve: BaseException | None = None,
        duree_s: float = 0.0,
        livrer: Callable[[Path], None] | None = None,
        cout_usd: float | None = 7.5,
    ) -> None:
        self._leve, self._duree_s, self._livrer, self._cout = leve, duree_s, livrer, cout_usd
        self.appels: list[dict[str, Any]] = []
        self.modele_configure = None

    async def run_agent(self, prompt: str, **reglages: Any) -> str:
        self.appels.append({"prompt": prompt, **reglages})
        report_usage(StepUsage(calls=1, output_tokens=100, cost_usd=self._cout))
        if self._duree_s:
            await asyncio.sleep(self._duree_s)
        if self._leve is not None:
            raise self._leve
        if self._livrer is not None:
            self._livrer(Path(reglages["workspace"]))
        return "L'application est construite."


def _seul(
    tmp_path: Path, fournisseur: FournisseurFactice, *, delai_s: float = 60.0
) -> tuple[cmp.Passage, Atelier]:
    atelier = Atelier(tmp_path / "atelier")
    passage = cmp.jouer_seul(
        cmp.charger_objectif("p5"),
        rang=1,
        atelier=atelier,
        fournisseur=fournisseur,
        modele="claude-opus-5-5",
        journal=Journal(),
        delai_s=delai_s,
    )
    return passage, atelier


def test_l_agent_seul_recoit_le_meme_message_par_l_adaptateur(tmp_path: Path) -> None:
    fournisseur = FournisseurFactice()
    passage, atelier = _seul(tmp_path, fournisseur)

    assert passage.issue == cmp.ABOUTI
    (appel,) = fournisseur.appels
    assert appel["prompt"] == cmp.charger_objectif("p5").message
    assert appel["model"] == "claude-opus-5-5"
    assert tuple(appel["tools"]) == DEFAULT_TOOLS
    # Ni prompt système, ni politique, ni canal, ni plafond de tours : l'agent seul.
    assert appel["plafond_tours"] is None
    assert not {"system_prompt", "politique", "on_arbitrage_acte"} & set(appel)
    # Son établi est un dossier de l'atelier du banc, sous les racines déclarées.
    assert Path(appel["workspace"]) == atelier.racine / "seul-1"
    assert passage.livrable == str(atelier.racine / "seul-1")
    assert passage.cout_usd == 7.5 and passage.cout_source == "rapporté par l'adaptateur"
    assert passage.gestes == (cmp.GESTE_MESSAGE,)
    assert passage.modele == "claude-opus-5-5"


def test_l_agent_seul_arrete_par_la_limite_d_usage_est_empeche(tmp_path: Path) -> None:
    """Le classement du produit (`cause_de`), pas un motif relu ici."""
    passage, _ = _seul(tmp_path, FournisseurFactice(leve=LimiteUsageAtteinte("limite atteinte")))
    assert passage.issue == cmp.EMPECHE
    assert not passage.compte


def test_l_agent_seul_en_echec_est_une_perte_comptee_avec_son_cout(tmp_path: Path) -> None:
    passage, _ = _seul(tmp_path, FournisseurFactice(leve=ValueError("le CLI s'est arrêté")))
    assert passage.issue == cmp.NON_ABOUTI
    assert "ValueError" in passage.motif
    assert passage.cout_usd == 7.5


def test_l_agent_seul_qui_depasse_le_delai_est_une_perte(tmp_path: Path) -> None:
    passage, _ = _seul(tmp_path, FournisseurFactice(duree_s=5.0), delai_s=0.05)
    assert passage.issue == cmp.NON_ABOUTI
    assert "n'a pas fini" in passage.motif


# =================================================================================================
# ④ La grille
# =================================================================================================

_COMMANDES = """\
INSTALLATION: npm ci
LINT: npm run lint
FORMATAGE: `npx prettier --check .`
TYPES: npm run typecheck
TESTS: npm test
BUILD: npm run build
SERVIR: npm start
PAGES: /, /recettes, recettes-sans-barre, /recettes/crepes
ABSENTES: /recettes/inconnue
"""


class LecteurFactice:
    """Le modèle de la grille : les commandes qu'il lit, les lignes que la personne écarte, le
    verdict de chaque ligne jugée."""

    def __init__(
        self,
        *,
        commandes: str = _COMMANDES,
        nommes: str = "",
        ecartees: str = "ECARTEES: aucune\nPOURQUOI: aucun choix n'écarte de vérification",
        verdicts: Mapping[str, Avis] | None = None,
        injoignable: bool = False,
    ) -> None:
        self._commandes, self._nommes, self._injoignable = commandes, nommes, injoignable
        self._ecartees = ecartees
        self._verdicts = dict(verdicts or {})
        self.lus: list[str] = []
        self.nominations: list[str] = []
        self.ecartements: list[str] = []
        self.juges: list[str] = []

    def rediger(self, systeme: str, prompt: str) -> str:
        if self._injoignable:
            raise RuntimeError("fournisseur injoignable")
        if systeme == gr.SYSTEME_LECTURES:
            self.nominations.append(prompt)
            return self._nommes
        if systeme == gr.SYSTEME_ECARTEES:
            self.ecartements.append(prompt)
            return self._ecartees
        self.lus.append(prompt)
        return self._commandes

    def juger(self, systeme: str, prompt: str) -> Avis:
        axe = {
            gr.SYSTEME_CI: gr.AXE_CI,
            gr.SYSTEME_COMMITS: gr.AXE_COMMITS,
            gr.SYSTEME_OUTILLAGE: gr.AXE_OUTILLAGE,
        }[systeme]
        self.juges.append(prompt)
        return self._verdicts.get(axe, Avis(nomme=True, pourquoi=f"{axe} tenu"))


class Joueur:
    """Joue une commande sans rien lancer, et retient où : un code par commande, `0` sinon.

    `serveur_s` fait **tenir** le serveur (`PORT=…`) ce temps réel avant de rendre la main,
    comme un vrai serveur tient sa fenêtre : la sonde a alors le temps de repasser.
    `expirees` sont les commandes qui ne rendent pas la main dans leur délai, et `delais`
    retient le délai donné à chacune.
    """

    def __init__(
        self,
        codes: Mapping[str, int] | None = None,
        *,
        serveur_s: float = 0.0,
        expirees: Sequence[str] = (),
    ) -> None:
        self.codes = dict(codes or {})
        self.serveur_s = serveur_s
        self.expirees = tuple(expirees)
        self.joues: list[tuple[str, Path]] = []
        self.delais: dict[str, float] = {}

    def __call__(self, commande: str, dossier: Path, delai_s: float) -> Execution:
        self.joues.append((commande, dossier))
        if commande.startswith("PORT=") and self.serveur_s:
            time.sleep(self.serveur_s)
        nue = re.sub(r"^(CI=1|PORT=\d+) ", "", commande)
        self.delais[nue] = delai_s
        if nue in self.expirees:
            return Execution(code=None, sortie="", duree_s=delai_s, expiree=True)
        code = self.codes.get(nue, 0)
        return Execution(code=code, sortie="ok" if code == 0 else "échec", duree_s=0.5)


def _poste(joueur: Joueur, statuts: Mapping[str, int | None] | None = None) -> gr.PosteDuBanc:
    horloge = Horloge()

    def sonde(url: str) -> int | None:
        chemin = "/" + url.split("/", 3)[3] if url.count("/") >= 3 else "/"
        if statuts is not None and chemin in statuts:
            return statuts[chemin]
        return 404 if "inconnue" in chemin or chemin == gr.PAGE_ABSENTE_DU_BANC else 200

    return gr.PosteDuBanc(joueur=joueur, sonde=sonde, horloge=horloge, dormir=horloge.dormir)


def _livrable(tmp_path: Path) -> Path:
    racine = _depot(
        tmp_path / "livrable",
        "feat: socle Next.js",
        **{
            "package.json": '{"scripts": {"lint": "eslint .", "test": "vitest run"}}\n',
            "README.md": "# Recettes\n",
            ".github/workflows/ci.yml": "jobs: {}\n",
        },
    )
    # Ce que le run a laissé sans le commiter n'est pas le livrable.
    (racine / "non-commite.txt").write_text("brouillon\n", encoding="utf-8")
    return racine


def _rejouer(
    tmp_path: Path,
    lecteur: LecteurFactice,
    joueur: Joueur,
    *,
    statuts: Mapping[str, int | None] | None = None,
    choix: tuple[str, ...] = ("tests : Vitest",),
    depot: Path | None = None,
) -> gr.Grille:
    return gr.rejouer(
        depot or _livrable(tmp_path),
        tmp_path / "clone",
        choix=choix,
        lecteur=lecteur,
        poste=_poste(joueur, statuts),
    )


@AVEC_GIT
def test_la_grille_joue_sur_un_clone_les_commandes_que_le_livrable_declare(
    tmp_path: Path,
) -> None:
    lecteur, joueur = LecteurFactice(), Joueur()
    grille = _rejouer(tmp_path, lecteur, joueur)

    assert [ligne.axe for ligne in grille.lignes] == list(gr.AXES)
    assert grille.tenues == len(gr.AXES) == grille.jugees
    copie = tmp_path / "clone"
    # Tout se joue dans le clone, et le clone ne porte que ce qui est commité.
    assert {dossier for _, dossier in joueur.joues} == {copie}
    assert (copie / "package.json").is_file() and not (copie / "non-commite.txt").exists()
    joues = [commande for commande, _ in joueur.joues]
    assert joues[:6] == [
        "CI=1 npm ci",
        "CI=1 npm run lint",
        "CI=1 npx prettier --check .",
        "CI=1 npm run typecheck",
        "CI=1 npm test",
        "CI=1 npm run build",
    ]
    assert re.fullmatch(r"PORT=\d+ npm start", joues[6])
    # Le lecteur a vu le dépôt commité : ses fichiers, son intégration continue.
    assert ".github/workflows/ci.yml" in lecteur.lus[0] and "non-commite" not in lecteur.lus[0]
    assert "jobs: {}" in lecteur.lus[0]
    # Le juge des commits lit leurs sujets, et celui de l'outillage les choix.
    assert any("feat: socle Next.js" in prompt for prompt in lecteur.juges)
    assert all("tests : Vitest" in prompt for prompt in lecteur.juges)
    pages = grille.ligne(gr.AXE_PAGES)
    assert pages is not None and "/recettes/inconnue → 404" in pages.detail
    assert gr.PAGE_ABSENTE_DU_BANC in pages.detail
    # Un chemin qui ne commence pas par « / » n'est pas une page.
    assert "recettes-sans-barre" not in pages.detail


@AVEC_GIT
def test_le_juge_lit_l_integration_continue_meme_sous_une_racine_chargee(tmp_path: Path) -> None:
    """Mesuré au premier passage réel (2026-10-15) : une racine de vingt fichiers épuisait la
    lecture, et la CI de l'agent seul a été jugée absente « faute d'en avoir lu le contenu »."""
    racine = {f"config-{rang:02d}.json": "{}\n" for rang in range(25)}
    depot = _depot(
        tmp_path / "chargee",
        "feat: socle",
        **racine,
        **{".github/workflows/ci.yml": "jobs:\n  verifier:\n    steps: [npm test]\n"},
    )
    lecteur = LecteurFactice()
    _rejouer(tmp_path, lecteur, Joueur(), depot=depot)
    ci = next(prompt for prompt in lecteur.juges if "steps: [npm test]" in prompt)
    assert '<fichier chemin=".github/workflows/ci.yml">' in ci


def _depot_au_long_historique(racine: Path) -> tuple[Path, str]:
    """Le rejeu de #1505 dans le dépôt de Maestro (docs/51 § 3.2), en petit : plus de fichiers
    que la liste n'en montre, des dossiers de configuration qui épuisent leur quota avant la CI,
    des dizaines de titres de squash d'autres tickets — puis un livrable d'un seul commit.

    Rend le dépôt et sa base : le commit d'où le livrable part.
    """
    fichiers = {f"src/module_{rang:03d}.py": "x = 1\n" for rang in range(gr.FICHIERS_LISTES + 10)}
    fichiers |= {
        f".claude/commands/c{rang:02d}.md": "# commande\n"
        for rang in range(gr.FICHIERS_DE_CONFIGURATION_LUS + 2)
    }
    depot = _depot(
        racine,
        "chore: socle",
        **fichiers,
        **{
            "CLAUDE.md": "Python : `.venv/Scripts/python.exe` sous Windows, "
            "`.venv/bin/python` sous Unix.\n",
            "src/portee.py": "def portee():\n    return None\n",
            ".github/workflows/ci.yml": "jobs:\n  pytest:\n    steps: [pytest maestro]\n",
        },
    )
    _historique(depot, [f"Titre du squash d'un autre ticket ({rang})" for rang in range(70)])
    base = _git(depot, "rev-parse", "HEAD")
    _depot(
        depot,
        "fix(portee): rendre le rm sur motif à une personne (Closes #1505)",
        **{"src/portee.py": "def portee():\n    return 'personne'\n"},
    )
    return depot, base


@AVEC_GIT
def test_un_livrable_rejoue_contre_sa_base_est_juge_sur_ce_qu_il_change(tmp_path: Path) -> None:
    """#1544 : la grille rendait 1/8 au livrable de la chaîne, jugé sur l'historique du dépôt,
    sur une liste de fichiers où la CI n'apparaissait pas, et par la variante Unix d'une
    commande sur un poste Windows."""
    depot, base = _depot_au_long_historique(tmp_path / "depot")
    lecteur = LecteurFactice(nommes="COMMANDES: aucun\nCI: .github/workflows/*\nOUTILLAGE: aucun")
    poste = replace(_poste(Joueur()), systeme="Windows 11 (10.0.26300, AMD64)")

    grille = gr.rejouer(
        depot,
        tmp_path / "clone",
        choix=("tests : pytest",),
        lecteur=lecteur,
        poste=poste,
        base=base[:8],
    )

    assert grille.base == base
    # La liste montrée est tronquée, la CI n'y est pas et le quota des dossiers de
    # configuration ne l'atteint pas : c'est la ligne qui l'a nommée, et elle est lue.
    (nomination,) = lecteur.nominations
    assert "… et" in nomination and "\n.github/workflows/ci.yml\n" not in nomination
    assert '<fichier chemin=".github/workflows/ci.yml">' not in nomination
    ci = next(prompt for prompt in lecteur.juges if "intégration continue écrite ?" in prompt)
    assert '<fichier chemin=".github/workflows/ci.yml">' in ci and "pytest maestro" in ci
    # Les commits jugés sont ceux du livrable depuis sa base, pas ceux du dépôt.
    commits = next(prompt for prompt in lecteur.juges if "<commits>" in prompt)
    assert "fix(portee)" in commits
    assert "autre ticket" not in commits and "chore: socle" not in commits
    # Ce que le livrable change depuis sa base est montré, et lu.
    outillage = next(prompt for prompt in lecteur.juges if "chacun de ces choix" in prompt)
    assert "src/portee.py" in outillage.split("Fichiers lus")[0]
    assert '<fichier chemin="src/portee.py">' in outillage and "'personne'" in outillage
    # Le lecteur de commandes sait sur quel système le clone se rejoue.
    assert "Windows 11 (10.0.26300, AMD64)" in lecteur.lus[0]


@AVEC_GIT
def test_sans_base_les_commits_juges_sont_les_derniers_du_depot(tmp_path: Path) -> None:
    """Un projet neuf (p5, #1512) n'a pas de base : son historique entier est le livrable."""
    depot, _base = _depot_au_long_historique(tmp_path / "depot")
    lecteur = LecteurFactice()
    grille = _rejouer(tmp_path, lecteur, Joueur(), depot=depot)
    assert grille.base == ""
    commits = next(prompt for prompt in lecteur.juges if "<commits>" in prompt)
    assert "fix(portee)" in commits and "autre ticket (69)" in commits


@AVEC_GIT
def test_une_base_que_le_livrable_ne_porte_pas_rend_la_grille_injouable(tmp_path: Path) -> None:
    joueur = Joueur()
    grille = gr.rejouer(
        _livrable(tmp_path),
        tmp_path / "clone",
        choix=(),
        lecteur=LecteurFactice(),
        poste=_poste(joueur),
        base="0123456789abcdef0123456789abcdef01234567",
    )
    assert all(ligne.etat == gr.INJOUABLE for ligne in grille.lignes)
    assert "0123456789ab" in grille.lignes[0].detail
    assert joueur.joues == []


@AVEC_GIT
def test_une_installation_ratee_laisse_les_lignes_suivantes_non_jouees(tmp_path: Path) -> None:
    grille = _rejouer(tmp_path, LecteurFactice(), Joueur({"npm ci": 1}))
    etats = {ligne.axe: ligne.etat for ligne in grille.lignes}
    assert etats[gr.AXE_INSTALLATION] == gr.MANQUE
    for axe in (*gr.AXES_JOUES[1:], gr.AXE_PAGES):
        assert etats[axe] == gr.NON_JOUE, axe
    # Une ligne non jouée compte comme manquée : elle a un verdict.
    assert grille.jugees == len(gr.AXES)
    assert grille.tenues == len(gr.AXES_JUGES)


@AVEC_GIT
def test_une_installation_sans_commande_ne_bloque_pas_ses_suivantes(tmp_path: Path) -> None:
    """#1744 : sans commande d'installation, la grille laissait tout le reste non joué, pages
    comprises. Une ligne non jouée compte déjà comme manquée : jouer les suivantes ne fait rien
    gagner à un livrable qui échoue, et rend au livrable qui tient ce qu'il tient."""
    commandes = _COMMANDES.replace("INSTALLATION: npm ci", "INSTALLATION: aucune")
    joueur = Joueur()
    grille = _rejouer(tmp_path, LecteurFactice(commandes=commandes), joueur)

    installation = grille.ligne(gr.AXE_INSTALLATION)
    assert installation is not None and installation.etat == gr.MANQUE
    assert "ne déclare aucune commande" in installation.detail
    joues = [commande for commande, _ in joueur.joues]
    assert joues[:5] == [
        "CI=1 npm run lint",
        "CI=1 npx prettier --check .",
        "CI=1 npm run typecheck",
        "CI=1 npm test",
        "CI=1 npm run build",
    ]
    assert re.fullmatch(r"PORT=\d+ npm start", joues[5])
    etats = {ligne.axe: ligne.etat for ligne in grille.lignes}
    assert gr.NON_JOUE not in etats.values()
    assert grille.tenues == len(gr.AXES) - 1


#: Le livrable de l'agent seul au passage `20261118-125220` (#1744), en petit : Python sans
#: dépendance, des tests, et un `LISEZ-MOI.md` plus long que ce que les pièces en montrent, qui
#: déclare à sa fin ce qu'il n'y a pas à installer et comment jouer les tests.
_LISEZ_MOI_LONG = (
    "# Les Sentiers du Vexin\n\n"
    + "Double-cliquez sur « Lancer l'application » : elle s'ouvre dans le navigateur.\n" * 60
    + "\n## Pour les techniciens\n\n"
    "- Python 3.10 ou plus récent, aucune bibliothèque à installer. "
    "Lancement : `python lancer.py`\n"
    "- Tests automatiques : `python -m unittest discover tests`\n"
)


class LecteurQuiLit:
    """Un lecteur qui ne répond que d'après ce qu'on lui montre, comme on le demande au modèle :
    il nomme le fichier de tests et le `LISEZ-MOI.md`, puis lit les commandes dans le prompt."""

    def __init__(self) -> None:
        self.nominations: list[str] = []
        self.lus: list[str] = []

    def rediger(self, systeme: str, prompt: str) -> str:
        if systeme == gr.SYSTEME_LECTURES:
            self.nominations.append(prompt)
            return "COMMANDES: tests/test_appli.py, LISEZ-MOI.md"
        self.lus.append(prompt)
        tests = "python -m unittest discover tests"
        rien = "aucune bibliothèque à installer"
        return (
            f"INSTALLATION: aucune\nTESTS: {tests if tests in prompt else 'aucune'}\n"
            'SERVIR: py -3 lancer.py --port "$PORT" --sans-navigateur\nPAGES: /\n'
            f"UNNEEDED: {f'INSTALLATION={rien} (LISEZ-MOI.md)' if rien in prompt else 'aucune'}\n"
        )

    def juger(self, systeme: str, prompt: str) -> Avis:
        return Avis(nomme=True, pourquoi="tenu")


@AVEC_GIT
def test_un_livrable_qui_n_a_rien_a_installer_joue_ses_tests_et_ses_pages(tmp_path: Path) -> None:
    """#1744 : 54 critères sur 54, et 0/7 à la grille. Le `LISEZ-MOI.md` montré tronqué cachait
    la commande des tests et « aucune bibliothèque à installer ». Le lecteur l'a nommé : il se lit
    au-delà de son début. Le livrable dit n'avoir rien à installer : la ligne est tenue, et ses
    tests et ses pages se jouent."""
    assert len(_LISEZ_MOI_LONG) > gr.CARACTERES_PAR_FICHIER
    depot = _depot(
        tmp_path / "seul",
        "feat: l'application du club",
        **{
            "LISEZ-MOI.md": _LISEZ_MOI_LONG,
            "lancer.py": "import argparse\n",
            "tests/test_appli.py": "import unittest\n",
        },
    )
    lecteur, joueur = LecteurQuiLit(), Joueur()
    grille = gr.rejouer(
        depot,
        tmp_path / "clone",
        choix=(),
        lecteur=lecteur,
        poste=_poste(joueur),
        axes=(*gr.AXES_JOUES, gr.AXE_PAGES),
    )

    # Le fichier montré en partie le dit, et le lecteur qui le nomme le reçoit entier, une fois.
    (nomination,) = lecteur.nominations
    lisez_moi = nomination.split('<fichier chemin="LISEZ-MOI.md">')[1].split("</fichier>")[0]
    assert "tronqué" in lisez_moi and "Tests automatiques" not in lisez_moi
    (commandes,) = lecteur.lus
    assert "Tests automatiques" in commandes
    assert commandes.count("# Les Sentiers du Vexin") == 1
    installation = grille.ligne(gr.AXE_INSTALLATION)
    assert installation is not None and installation.etat == gr.TENU
    assert "aucune bibliothèque à installer" in installation.detail
    assert "LISEZ-MOI.md" in installation.detail
    tests = grille.ligne(gr.AXE_TESTS)
    assert tests is not None and tests.etat == gr.TENU
    assert "CI=1 python -m unittest discover tests" in [commande for commande, _ in joueur.joues]
    assert grille.ligne(gr.AXE_PAGES).etat == gr.TENU
    # Ce que le livrable ne déclare pas, il le manque toujours : rien n'est deviné pour lui.
    assert grille.ligne(gr.AXE_TYPES).etat == gr.MANQUE
    assert gr.NON_JOUE not in {ligne.etat for ligne in grille.lignes}


def test_rien_a_faire_ne_vaut_que_pour_preparer_le_livrable() -> None:
    """Un projet peut n'avoir rien à installer ni à construire. Il a toujours du code à vérifier :
    le lint, le formatage, les types et les tests ne s'excusent pas ainsi."""
    commandes = gr.commandes_depuis(
        "INSTALLATION: aucune\nBUILD: npm run build\nTESTS: aucune\n"
        f"{gr.CHAMP_SANS_BESOIN}: INSTALLATION=aucune dépendance (README), "
        "BUILD=se sert tel quel (README), TESTS=pas de tests (README), "
        "INSTALLATION=second\n"
    )
    assert set(commandes.sans_besoin) == {gr.AXE_INSTALLATION, gr.AXE_BUILD}
    installation = commandes.sans_besoin[gr.AXE_INSTALLATION]
    assert (installation.dit, installation.origine) == ("aucune dépendance", "README")
    assert commandes.to_dict()["unneeded"]["installation"] == {
        "says": "aucune dépendance",
        "origin": "README",
    }
    assert gr.commandes_depuis(f"{gr.CHAMP_SANS_BESOIN}: aucune\n").sans_besoin == {}
    assert "rien à faire : Installation" in gr._commandes_en_mots(commandes)
    assert f"{gr.CHAMP_SANS_BESOIN}:" in gr.SYSTEME_COMMANDES


@AVEC_GIT
def test_une_commande_declaree_l_emporte_sur_rien_a_faire(tmp_path: Path) -> None:
    """Le build déclaré se joue, et son échec laisse les pages non jouées, même si le lecteur dit
    aussi qu'il n'y a rien à construire : la commande du livrable fait foi."""
    commandes = _COMMANDES + f"{gr.CHAMP_SANS_BESOIN}: BUILD=rien à construire (README)\n"
    grille = _rejouer(tmp_path, LecteurFactice(commandes=commandes), Joueur({"npm run build": 2}))
    assert grille.ligne(gr.AXE_BUILD).etat == gr.MANQUE
    assert grille.ligne(gr.AXE_PAGES).etat == gr.NON_JOUE


@AVEC_GIT
def test_une_commande_sans_retour_dans_son_delai_ne_vaut_aucun_verdict(tmp_path: Path) -> None:
    """#1681 : coupée à 300 s, l'installation du dépôt de Maestro était un manque et laissait le
    lint, les types et les tests non joués ; la suite entière, sans retour, manquait aussi."""
    joueur = Joueur({"npm run lint": 1}, expirees=("npm ci", "npm test"))
    grille = _rejouer(tmp_path, LecteurFactice(), joueur)

    installation = grille.ligne(gr.AXE_INSTALLATION)
    assert installation is not None and installation.etat == gr.NON_TRANCHE
    assert "ne dit pas combien de temps" in installation.detail
    # Les lignes suivantes se jouent quand même : un succès est tenu, et un échec derrière une
    # installation inachevée ne dit rien du livrable.
    assert [commande for commande, _ in joueur.joues][:6] == [
        "CI=1 npm ci",
        "CI=1 npm run lint",
        "CI=1 npx prettier --check .",
        "CI=1 npm run typecheck",
        "CI=1 npm test",
        "CI=1 npm run build",
    ]
    etats = {ligne.axe: ligne.etat for ligne in grille.lignes}
    assert etats[gr.AXE_LINT] == gr.NON_TRANCHE
    assert "installation" in grille.ligne(gr.AXE_LINT).detail
    assert etats[gr.AXE_FORMATAGE] == etats[gr.AXE_TYPES] == etats[gr.AXE_BUILD] == gr.TENU
    assert etats[gr.AXE_TESTS] == gr.NON_TRANCHE
    assert etats[gr.AXE_PAGES] == gr.TENU
    # Aucune durée n'est un verdict : ni manque, ni ligne non jouée, et le score ne les compte pas.
    assert gr.MANQUE not in etats.values() and gr.NON_JOUE not in etats.values()
    assert grille.jugees == grille.tenues == len(gr.AXES) - 3


@AVEC_GIT
def test_le_delai_d_une_commande_se_prend_du_livrable(tmp_path: Path) -> None:
    """#1681 : le délai d'une commande est celui que le livrable lui laisse (sa CI, ses
    instructions) ; sans rien de déclaré, le banc lui laisse le sien, et le dit."""
    commandes = _COMMANDES + (
        "DELAIS: INSTALLATION=1800 (ci.yml, timeout-minutes: 30 du job), "
        "TESTS=21600 (ci.yml, aucune limite posée : 360 min par défaut chez GitHub Actions), "
        "LINT=0 (rien), PAGES=60\n"
    )
    joueur = Joueur(expirees=("npm test",))
    grille = _rejouer(tmp_path, LecteurFactice(commandes=commandes), joueur)

    assert joueur.delais["npm ci"] == 1800
    assert joueur.delais["npm test"] == 21600
    # Un délai nul n'en est pas un, et un champ hors des lignes jouées ne s'applique à rien.
    assert joueur.delais["npm run lint"] == gr.DELAI_DU_BANC_S
    assert joueur.delais["npm run typecheck"] == gr.DELAI_DU_BANC_S
    tests = grille.ligne(gr.AXE_TESTS)
    assert tests is not None and tests.etat == gr.NON_TRANCHE
    assert "21600 s" in tests.detail and "360 min par défaut chez GitHub Actions" in tests.detail
    assert grille.ligne(gr.AXE_INSTALLATION).etat == gr.TENU


#: Ce que le dépôt de Maestro déclare de sa suite (#1695) : son verdict se rend sous Linux, et
#: rien de ce que le lecteur retient ne l'y joue depuis le poste.
_AILLEURS_LINUX = (
    "ELSEWHERE: TESTS=Linux (CLAUDE.md : le job pytest joue dans un conteneur Linux, "
    "l'OS du verdict), LINT=Linux (même source), PAGES=Linux, TESTS=macOS (second)\n"
)


@AVEC_GIT
def test_un_echec_dont_le_livrable_rend_le_verdict_ailleurs_ne_tranche_pas(tmp_path: Path) -> None:
    """#1695 : la suite de Maestro, jouée en natif sous Windows, échouait sur quatre tests du
    poste, et la ligne Tests disait « manque » quand c'est le poste qui parlait. Le livrable
    déclare que ce verdict se rend sous Linux : un échec ici ne dit rien de lui, un succès
    est tenu, et une ligne dont il ne déclare rien de tel reste jugée."""
    commandes = _COMMANDES + _AILLEURS_LINUX
    joueur = Joueur({"npm test": 1, "npm run typecheck": 1})
    grille = _rejouer(tmp_path, LecteurFactice(commandes=commandes), joueur)

    tests = grille.ligne(gr.AXE_TESTS)
    assert tests is not None and tests.etat == gr.NON_TRANCHE
    assert "code 1" in tests.detail
    assert "Linux" in tests.detail and "l'OS du verdict" in tests.detail
    assert "macOS" not in tests.detail  # le premier de chaque ligne fait foi
    # Un succès, lui, est tenu : rien ne le dément là où le livrable rend son verdict.
    assert grille.ligne(gr.AXE_LINT).etat == gr.TENU
    # Le livrable ne dit rien de tel de son typage : son échec sur le poste reste un manque.
    assert grille.ligne(gr.AXE_TYPES).etat == gr.MANQUE
    assert grille.jugees == len(gr.AXES) - 1


@AVEC_GIT
def test_sans_verdict_declare_ailleurs_un_echec_sur_le_poste_reste_un_manque(
    tmp_path: Path,
) -> None:
    """L'hébergement de la CI n'est pas une déclaration (#1695) : sans `ELSEWHERE`, la ligne
    qui échoue sur le poste manque — c'est ce que le banc existe pour voir (bilan de p5)."""
    grille = _rejouer(tmp_path, LecteurFactice(), Joueur({"npm test": 1}))
    assert grille.ligne(gr.AXE_TESTS).etat == gr.MANQUE


@AVEC_GIT
def test_une_installation_qui_echoue_hors_de_son_verdict_laisse_les_suivantes_se_jouer(
    tmp_path: Path,
) -> None:
    """Une installation que le livrable ne fait que sous Linux, ratée sous Windows, ne bloque
    rien : les lignes suivantes se jouent, un succès est tenu, un échec ne tranche pas."""
    commandes = _COMMANDES + "ELSEWHERE: INSTALLATION=Linux (README : « sous Linux seulement »)\n"
    joueur = Joueur({"npm ci": 1, "npm run lint": 1})
    grille = _rejouer(tmp_path, LecteurFactice(commandes=commandes), joueur)

    etats = {ligne.axe: ligne.etat for ligne in grille.lignes}
    assert etats[gr.AXE_INSTALLATION] == gr.NON_TRANCHE
    assert "Linux" in grille.ligne(gr.AXE_INSTALLATION).detail
    assert len([commande for commande, _ in joueur.joues]) == len(gr.AXES_JOUES) + 1
    assert etats[gr.AXE_LINT] == gr.NON_TRANCHE
    assert "installation" in grille.ligne(gr.AXE_LINT).detail
    assert etats[gr.AXE_FORMATAGE] == etats[gr.AXE_TESTS] == etats[gr.AXE_BUILD] == gr.TENU
    assert gr.MANQUE not in etats.values() and gr.NON_JOUE not in etats.values()


@AVEC_GIT
def test_la_commande_qui_joue_la_verification_la_ou_se_rend_son_verdict_est_jouee(
    tmp_path: Path,
) -> None:
    """#1695 : le lecteur est chargé de retenir la commande que le projet donne pour jouer une
    vérification là où il en rend le verdict (un script qui lance son conteneur), et de nommer
    ce verdict sous son champ quand rien ne l'y joue ; le banc joue ce qu'il retient, tel quel."""
    commandes = _COMMANDES.replace("TESTS: npm test", "TESTS: bash scripts/ci/pytest.sh")
    joueur = Joueur()
    lecteur = LecteurFactice(commandes=commandes)
    traces: list[tuple[str, str]] = []
    grille = gr.rejouer(
        _livrable(tmp_path),
        tmp_path / "clone",
        choix=("tests : pytest",),
        lecteur=lecteur,
        poste=_poste(joueur),
        trace=lambda libelle, detail: traces.append((libelle, detail)),
    )

    assert "CI=1 bash scripts/ci/pytest.sh" in [commande for commande, _ in joueur.joues]
    assert grille.ligne(gr.AXE_TESTS).etat == gr.TENU
    assert f"{gr.CHAMP_AILLEURS}:" in gr.SYSTEME_COMMANDES
    # Sans verdict rendu ailleurs, le déroulé le dit.
    lues = dict(traces)["commandes lues dans le livrable"]
    assert "verdicts rendus ailleurs : aucun" in lues


def test_le_verdict_rendu_ailleurs_se_lit_dans_son_champ() -> None:
    commandes = gr.commandes_depuis(_COMMANDES + _AILLEURS_LINUX + "ELSEWHERE: BUILD=Linux\n")
    assert set(commandes.ailleurs) == {gr.AXE_TESTS, gr.AXE_LINT}  # PAGES n'est pas une commande
    tests = commandes.ailleurs[gr.AXE_TESTS]
    assert tests.systeme == "Linux"
    assert tests.origine.startswith("CLAUDE.md : le job pytest joue dans un conteneur Linux")
    assert commandes.ailleurs[gr.AXE_LINT].origine == "même source"
    assert commandes.to_dict()["elsewhere"]["tests"] == {
        "system": "Linux",
        "origin": tests.origine,
    }
    assert gr.commandes_depuis(_COMMANDES + "ELSEWHERE: aucune\n").ailleurs == {}
    assert "Tests sous Linux" in gr._commandes_en_mots(commandes)


@AVEC_GIT
def test_une_ligne_que_la_personne_a_ecartee_est_sans_objet(tmp_path: Path) -> None:
    """#1681 : « aucun formateur appliqué au dépôt », et la ligne du formatage manquait des deux
    côtés. Une ligne écartée ne se joue ni ne se juge : elle ne compte ni pour ni contre."""
    commandes = _COMMANDES.replace("FORMATAGE: `npx prettier --check .`", "FORMATAGE: aucune")
    lecteur = LecteurFactice(
        commandes=commandes,
        ecartees=(
            "ECARTEES: FORMATAGE, CI, OUTILLAGE, VITESSE\n"
            "POURQUOI: « aucun formateur appliqué » écarte le formatage, « pas d'intégration "
            "continue » la CI."
        ),
    )
    joueur = Joueur()
    choix = ("formatage : aucun formateur appliqué au dépôt", "pas d'intégration continue")
    grille = _rejouer(tmp_path, lecteur, joueur, choix=choix)

    (demande,) = lecteur.ecartements
    assert all(un in demande for un in choix)
    formatage, ci = grille.ligne(gr.AXE_FORMATAGE), grille.ligne(gr.AXE_CI)
    assert formatage is not None and formatage.etat == gr.SANS_OBJET
    assert "aucun formateur appliqué" in formatage.detail
    assert ci is not None and ci.etat == gr.SANS_OBJET
    assert not any("intégration continue écrite ?" in prompt for prompt in lecteur.juges)
    # L'outillage choisi ne s'écarte pas : ce sont les choix eux-mêmes.
    assert grille.ligne(gr.AXE_OUTILLAGE).etat == gr.TENU
    assert grille.jugees == grille.tenues == len(gr.AXES) - 2


@AVEC_GIT
def test_sans_lecture_des_choix_aucune_ligne_n_est_ecartee(tmp_path: Path) -> None:
    """Une réponse hors contrat n'écarte rien : chaque ligne se joue, comme avant."""
    lecteur = LecteurFactice(ecartees="Je pense que le formatage n'est pas demandé.")
    grille = _rejouer(tmp_path, lecteur, Joueur())
    assert gr.SANS_OBJET not in {ligne.etat for ligne in grille.lignes}
    assert grille.tenues == len(gr.AXES)


@AVEC_GIT
def test_la_convention_de_commit_se_juge_sur_les_messages_entiers(tmp_path: Path) -> None:
    """#1681 : le dépôt exige `Refs #<iid>` dans le message, et le juge ne voyait que les sujets
    (« seul le sujet est fourni, donc on ne peut pas vérifier la mention »)."""
    depot = _depot(tmp_path / "depot", "chore: socle")
    base = _git(depot, "rev-parse", "HEAD")
    _depot(
        depot,
        "fix(fil): une seule ponctuation\n\nLa cause est ponctuée une fois.\n\nRefs #1592",
        **{"a.py": "x = 1\n"},
    )
    long = "docs: un long corps\n\n" + "détail. " * 800 + "\n\nRefs #7"
    _depot(depot, long, **{"b.py": "y = 1\n"})
    _depot(depot, "Maestro : intégrer la tâche", **{"c.py": "z = 1\n"})
    lecteur = LecteurFactice()

    gr.rejouer(
        depot,
        tmp_path / "clone",
        choix=("messages de commit : Conventional Commits, avec Refs #<iid> ou Closes #<iid>",),
        lecteur=lecteur,
        poste=_poste(Joueur()),
        axes=(gr.AXE_COMMITS,),
        base=base,
    )

    (commits,) = [prompt for prompt in lecteur.juges if "<commits>" in prompt]
    assert commits.count("<commit>") == 3 and "chore: socle" not in commits
    assert "La cause est ponctuée une fois.\n\nRefs #1592" in commits
    assert "Maestro : intégrer la tâche" in commits
    # Un message trop long garde son début et sa fin : la référence vit dans le pied.
    assert "docs: un long corps" in commits and "Refs #7" in commits
    assert len(commits) < len(long) + 2000


@AVEC_GIT
def test_un_build_rate_laisse_les_pages_non_jouees(tmp_path: Path) -> None:
    grille = _rejouer(tmp_path, LecteurFactice(), Joueur({"npm run build": 2}))
    assert grille.ligne(gr.AXE_BUILD).etat == gr.MANQUE
    pages = grille.ligne(gr.AXE_PAGES)
    assert pages is not None and pages.etat == gr.NON_JOUE and "build" in pages.detail


@AVEC_GIT
def test_une_ligne_que_le_livrable_ne_declare_pas_est_manquee(tmp_path: Path) -> None:
    commandes = _COMMANDES.replace("TYPES: npm run typecheck", "TYPES: aucune")
    grille = _rejouer(tmp_path, LecteurFactice(commandes=commandes), Joueur())
    types = grille.ligne(gr.AXE_TYPES)
    assert types is not None and types.etat == gr.MANQUE
    assert "ne déclare aucune commande" in types.detail


@AVEC_GIT
def test_un_faux_404_manque_la_ligne_des_pages(tmp_path: Path) -> None:
    """Le défaut de C3 : une fiche inconnue servie en `200`."""
    grille = _rejouer(tmp_path, LecteurFactice(), Joueur(), statuts={"/recettes/inconnue": 200})
    pages = grille.ligne(gr.AXE_PAGES)
    assert pages is not None and pages.etat == gr.MANQUE
    assert "/recettes/inconnue (200 au lieu de 404)" in pages.detail


@AVEC_GIT
def test_un_faux_404_qui_se_corrige_ensuite_reste_un_faux_404(tmp_path: Path) -> None:
    """Mesuré au rejeu de C3 (2026-10-15) : une fiche inconnue rend `200` à la première
    visite, puis `404` une fois en cache. La personne voit la première réponse."""
    vues: dict[str, int] = {}

    def sonde(url: str) -> int | None:
        chemin = "/" + url.split("/", 3)[3]
        vues[chemin] = vues.get(chemin, 0) + 1
        if chemin == "/recettes/inconnue":
            return 200 if vues[chemin] == 1 else 404
        return 404 if chemin == gr.PAGE_ABSENTE_DU_BANC else 200

    horloge = Horloge()
    poste = gr.PosteDuBanc(
        joueur=Joueur(serveur_s=0.5), sonde=sonde, horloge=horloge, dormir=horloge.dormir
    )
    grille = gr.rejouer(
        _livrable(tmp_path), tmp_path / "clone", choix=(), lecteur=LecteurFactice(), poste=poste
    )
    pages = grille.ligne(gr.AXE_PAGES)
    assert pages is not None and pages.etat == gr.MANQUE
    assert "/recettes/inconnue (200 au lieu de 404)" in pages.detail
    assert vues["/recettes/inconnue"] == 1


@AVEC_GIT
def test_un_juge_qui_s_abstient_ne_compte_ni_pour_ni_contre(tmp_path: Path) -> None:
    verdicts = {
        gr.AXE_CI: Avis(nomme=False, pourquoi="juge injoignable", lisible=False),
        gr.AXE_COMMITS: Avis(nomme=False, pourquoi="« Maestro : … » n'est pas la convention"),
    }
    grille = _rejouer(tmp_path, LecteurFactice(verdicts=verdicts), Joueur())
    assert grille.ligne(gr.AXE_CI).etat == gr.NON_JUGE
    assert grille.ligne(gr.AXE_COMMITS).etat == gr.MANQUE
    assert grille.jugees == len(gr.AXES) - 1
    assert grille.tenues == len(gr.AXES) - 2


@AVEC_GIT
def test_sans_choix_les_lignes_qui_en_dependent_ne_se_jugent_pas(tmp_path: Path) -> None:
    lecteur = LecteurFactice()
    grille = _rejouer(tmp_path, lecteur, Joueur(), choix=())
    assert lecteur.ecartements == []  # sans choix, rien n'est écarté
    assert grille.ligne(gr.AXE_COMMITS).etat == gr.NON_JUGE
    assert grille.ligne(gr.AXE_OUTILLAGE).etat == gr.NON_JUGE
    assert grille.ligne(gr.AXE_CI).etat == gr.TENU


@AVEC_GIT
def test_un_lecteur_injoignable_laisse_les_commandes_non_jouees(tmp_path: Path) -> None:
    joueur = Joueur()
    grille = _rejouer(tmp_path, LecteurFactice(injoignable=True), joueur)
    for axe in (*gr.AXES_JOUES, gr.AXE_PAGES):
        ligne = grille.ligne(axe)
        assert ligne is not None and ligne.etat == gr.NON_JOUE, axe
        assert "n'ont pas pu être lues" in ligne.detail
    assert joueur.joues == []


def test_un_livrable_qui_ne_se_clone_pas_manque_toutes_ses_lignes(tmp_path: Path) -> None:
    pas_un_depot = tmp_path / "vide"
    pas_un_depot.mkdir()
    joueur = Joueur()
    grille = _rejouer(tmp_path, LecteurFactice(), joueur, depot=pas_un_depot)
    assert all(ligne.etat == gr.MANQUE for ligne in grille.lignes)
    assert "pas un dépôt" in grille.lignes[0].detail
    assert joueur.joues == []


@AVEC_GIT
def test_un_clone_qui_echoue_sur_un_depot_rend_la_grille_injouable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mesuré au rejeu du 2026-10-15 : le poste a refusé de créer `git` (0xC0000142), et le
    livrable de l'agent seul a été compté 0/10. Une panne du poste n'est pas un défaut."""

    def refuse(_depot: Path, _cible: Path) -> None:
        raise OSError("`git clone` du projet refusé : code 3221225794")

    monkeypatch.setattr(gr, "cloner", refuse)
    grille = _rejouer(tmp_path, LecteurFactice(), Joueur())
    assert all(ligne.etat == gr.INJOUABLE for ligne in grille.lignes)
    assert grille.jugees == 0 and grille.tenues == 0


@AVEC_GIT
def test_la_grille_ne_joue_que_ses_lignes(tmp_path: Path) -> None:
    joueur = Joueur()
    grille = gr.rejouer(
        _livrable(tmp_path),
        tmp_path / "clone",
        choix=(),
        lecteur=LecteurFactice(),
        poste=_poste(joueur),
        axes=(gr.AXE_INSTALLATION, gr.AXE_TESTS, gr.AXE_CI),
    )
    assert [ligne.axe for ligne in grille.lignes] == [gr.AXE_INSTALLATION, gr.AXE_TESTS, gr.AXE_CI]
    assert [commande for commande, _ in joueur.joues] == ["CI=1 npm ci", "CI=1 npm test"]


def _ecoute_en_double_pile() -> socket.socket | None:
    """Un serveur qui écoute sur `::` en double pile, comme `next start` — `None` sans IPv6."""
    try:
        prise = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        prise.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        prise.bind(("::", 0))
        prise.listen()
    except (OSError, AttributeError):
        return None
    return prise


def test_le_livrable_ne_se_sert_pas_sur_un_port_qu_un_serveur_ipv6_tient(tmp_path: Path) -> None:
    """Mesuré au rejeu de C3 (2026-10-15) : `next start` est tombé sur `EADDRINUSE :::4600`.

    Le port avait été jugé libre sur 127.0.0.1, et la sonde a interrogé le serveur d'un autre.
    """
    serveur = _ecoute_en_double_pile()
    if serveur is None:
        pytest.skip("le poste n'écoute pas en IPv6")
    with serveur:
        tenu = serveur.getsockname()[1]
        assert port_de_service(tenu) != tenu


def test_les_commandes_se_lisent_dans_leurs_champs() -> None:
    commandes = gr.commandes_depuis(
        "Voici :\nINSTALLATION: `npm ci`\nLINT: aucune.\nTYPES:   \nSERVIR: npm start\n"
        "PAGES: /, /a, b, /a\nABSENTES: aucune\nINSTALLATION: npm install\n"
    )
    assert commandes.par_axe[gr.AXE_INSTALLATION] == "npm ci"  # le premier champ fait foi
    assert commandes.par_axe[gr.AXE_LINT] == ""
    assert commandes.par_axe[gr.AXE_TYPES] == "" and commandes.par_axe[gr.AXE_BUILD] == ""
    assert commandes.servir == "npm start"
    assert commandes.pages == ("/", "/a")
    assert commandes.absentes == ()
    tropes = gr.commandes_depuis("PAGES: " + ", ".join(f"/p{n}" for n in range(20)))
    assert len(tropes.pages) == gr.PAGES_MAX


# =================================================================================================
# ⑤ Ce qui se compare
# =================================================================================================


def _passage(cote: str, rang: int, **champs: Any) -> cmp.Passage:
    reglages: dict[str, Any] = {"issue": cmp.ABOUTI, "motif": "fait", "gestes": ("message",)}
    reglages.update(champs)
    return cmp.Passage(cote=cote, rang=rang, **reglages)


def test_les_medianes_et_la_dispersion_ne_comptent_pas_les_empechements() -> None:
    comparatif = cmp.Comparatif(
        "20261015-120000",
        cmp.charger_objectif("p5"),
        (
            _passage("maestro", 1, duree_s=3000.0, cout_usd=16.0, gestes=("a",) * 20),
            _passage("seul", 1, duree_s=1500.0, cout_usd=8.0),
            _passage("maestro", 2, duree_s=4000.0, cout_usd=12.0, gestes=("a",) * 14),
            _passage("seul", 2, issue=cmp.EMPECHE, duree_s=10.0, cout_usd=0.1),
            _passage("maestro", 3, duree_s=3500.0, cout_usd=None, gestes=("a",) * 16),
        ),
    )
    maestro = comparatif.mesures("maestro")
    seul = comparatif.mesures("seul")
    duree = maestro[cmp.GRANDEUR_DUREE]
    assert duree is not None and duree.mediane == 3500.0
    assert (duree.minimum, duree.maximum) == (3000.0, 4000.0)
    assert duree.dispersion == pytest.approx(1000.0 / 3500.0)
    cout = maestro[cmp.GRANDEUR_COUT]
    assert cout is not None and cout.valeurs == (16.0, 12.0)  # un coût inconnu n'est pas zéro
    assert maestro[cmp.GRANDEUR_GESTES].mediane == 16
    # L'empêchement de l'agent seul n'entre dans aucune médiane.
    seule = seul[cmp.GRANDEUR_DUREE]
    assert seule is not None and seule.valeurs == (1500.0,) and seule.dispersion is None
    assert seul[cmp.GRANDEUR_TENUES] is None  # aucune grille : rien de mesuré
    rapports = comparatif.to_dict()["rapports"]
    assert rapports[cmp.GRANDEUR_DUREE] == pytest.approx(3500.0 / 1500.0, abs=1e-4)
    assert rapports[cmp.GRANDEUR_COUT] == pytest.approx(14.0 / 8.0)
    assert comparatif.to_dict()["cotes"]["seul"]["comptes"] == 1


def test_le_rapport_met_les_deux_cotes_cote_a_cote(tmp_path: Path) -> None:
    grille = gr.Grille(
        depot="d",
        copie="c",
        lignes=(
            gr.Ligne(gr.AXE_INSTALLATION, gr.TENU, "code 0", commande="npm ci"),
            gr.Ligne(gr.AXE_FORMATAGE, gr.MANQUE, "50 fichiers | hors format"),
        ),
    )
    comparatif = cmp.Comparatif(
        "20261015-120000",
        cmp.charger_objectif("p5"),
        (
            _passage(
                "maestro",
                1,
                duree_s=3864.0,
                cout_usd=15.97,
                cout_postes={"orchestration": 0.88, "fil": 1.55},
                grille=grille,
            ),
            _passage("seul", 1, duree_s=1557.0, cout_usd=8.02, grille=grille),
        ),
    )
    dossier = cmp.ecrire(comparatif, racine=tmp_path / "rapports")
    assert dossier == tmp_path / "rapports" / "20261015-120000"
    texte = (dossier / cmp.FICHIER_MARKDOWN).read_text(encoding="utf-8")
    assert "| | maestro | seul | maestro / seul |" in texte
    assert "| Durée | 64 min 24 s | 25 min 57 s | 2.48 |" in texte
    assert "| Coût | 15.97 $ | 8.02 $ | 1.99 |" in texte
    assert "| Installation | 1/1 | 1/1 |" in texte
    assert "| Formatage | 0/1 | 0/1 |" in texte
    assert "| Lint | non jugée | non jugée |" in texte
    # Une barre verticale dans un détail ne casse pas le tableau.
    assert "50 fichiers \\| hors format" in texte
    assert "15.97 $ (—), dont orchestration 0.88 $, fil 1.55 $" in texte
    donnees = json.loads((dossier / cmp.FICHIER_JSON).read_text(encoding="utf-8"))
    assert donnees["objectif"]["nom"] == "p5"
    assert donnees["cotes"]["maestro"]["grille"]["installation"] == {"tenues": 1, "jugees": 1}
    assert donnees["passages"][0]["grille"]["lignes"][1]["etat"] == gr.MANQUE


# =================================================================================================
# ⑥ La commande
# =================================================================================================


def _moteur_qui_commite(racine: Path) -> None:
    _depot(racine, "feat: application", **{"package.json": "{}\n"})


def _livrer_seul(etabli: Path) -> None:
    _depot(etabli, "feat: application", **{"package.json": "{}\n"})


def _main(
    tmp_path: Path,
    *arguments: str,
    fil: FilFactice | None = None,
    fournisseur: FournisseurFactice | None = None,
    lecteur: LecteurFactice | None = None,
    juge_couverture: Any = None,
    horloge: Horloge | None = None,
) -> tuple[int, str, str, Path]:
    horloge = horloge or Horloge()
    fil = fil or FilFactice(
        tmp_path / "Maestro" / "recettes", horloge=horloge, moteur=_moteur_qui_commite
    )
    sortie, erreur = io.StringIO(), io.StringIO()
    rapports = tmp_path / "rapports"
    code = cmp.main(
        list(arguments),
        client=cmp.ClientCompteur(fil),
        fournisseur=fournisseur or FournisseurFactice(livrer=_livrer_seul),
        modele="claude-opus-5-5",
        lecteur=lecteur or LecteurFactice(),
        juge_couverture=juge_couverture,
        redemarrer=lambda: "API rallumée (banc)",
        atelier=Atelier(tmp_path / "atelier" / "20261015-120000"),
        poste=_poste(Joueur()),
        racine_rapports=rapports,
        horloge=horloge,
        dormir=horloge.dormir,
        sortie=sortie,
        erreur=erreur,
    )
    return code, sortie.getvalue(), erreur.getvalue(), rapports


@AVEC_GIT
def test_le_banc_annonce_son_cout_alterne_les_cotes_et_ecrit_son_rapport(
    tmp_path: Path,
) -> None:
    code, sortie, erreur, rapports = _main(tmp_path, "--passages", "2")

    assert code == cmp.CODE_ECRIT, erreur
    premiere = sortie.splitlines()[1]
    assert "~16 $ par passage côté maestro et ~8 $ par passage côté seul" in premiere
    assert "soit ~48 $ pour 2 passage(s)" in premiere
    ordre = re.findall(r"^— (\w+) · passage (\d)/2$", sortie, re.MULTILINE)
    assert ordre == [("maestro", "1"), ("seul", "1"), ("maestro", "2"), ("seul", "2")]
    donnees = json.loads((rapports / "20261015-120000" / cmp.FICHIER_JSON).read_text("utf-8"))
    assert [(p["cote"], p["rang"]) for p in donnees["passages"]] == [
        ("maestro", 1),
        ("seul", 1),
        ("maestro", 2),
        ("seul", 2),
    ]
    # Chaque livrable a été rejoué sur un clone propre, des deux côtés.
    for passage in donnees["passages"]:
        assert passage["grille"]["tenues"] == len(gr.AXES)
        assert passage["grille"]["copie"].endswith(f"{passage['cote']}-{passage['rang']}-clone")
    assert donnees["cotes"]["seul"]["mesures"]["gestes"]["mediane"] == 1


@AVEC_GIT
def test_ce_qui_est_mesure_survit_a_un_banc_tue_en_chemin(tmp_path: Path) -> None:
    """L'agent seul n'a aucun garde-fou : s'il tue le banc, le passage de Maestro reste écrit."""

    class Tueur(FournisseurFactice):
        async def run_agent(self, prompt: str, **reglages: Any) -> str:
            raise SystemExit("taskkill //IM python.exe //F")

    with pytest.raises(SystemExit):
        _main(tmp_path, fournisseur=Tueur())
    donnees = json.loads(
        (tmp_path / "rapports" / "20261015-120000" / cmp.FICHIER_JSON).read_text("utf-8")
    )
    assert [(p["cote"], p["issue"]) for p in donnees["passages"]] == [("maestro", cmp.ABOUTI)]


def test_le_banc_refuse_le_cote_maestro_sans_api(tmp_path: Path) -> None:
    class Muette(FilFactice):
        def demander(self, methode: str, chemin: str, **reglages: Any) -> Reponse:
            if chemin == "/api/sante":
                raise ErreurAPI("injoignable", chemin=chemin)
            return super().demander(methode, chemin, **reglages)

    code, _, erreur, rapports = _main(
        tmp_path, fil=Muette(tmp_path / "x", horloge=Horloge())
    )
    assert code == cmp.CODE_API_MUETTE
    assert "start.sh" in erreur
    assert not rapports.exists()


@AVEC_GIT
def test_l_agent_seul_se_joue_sans_api(tmp_path: Path) -> None:
    class Muette(FilFactice):
        def demander(self, methode: str, chemin: str, **reglages: Any) -> Reponse:
            raise AssertionError(f"l'agent seul n'appelle aucune API : {chemin}")

    code, sortie, erreur, _ = _main(
        tmp_path, "--cote", "seul", fil=Muette(tmp_path / "x", horloge=Horloge())
    )
    assert code == cmp.CODE_ECRIT, erreur
    assert "~8 $ par passage côté seul, soit ~8 $" in sortie


def test_tous_les_passages_empeches_sont_une_abstention(tmp_path: Path) -> None:
    fournisseur = FournisseurFactice(leve=LimiteUsageAtteinte("limite atteinte"))
    code, _, _, rapports = _main(tmp_path, "--cote", "seul", fournisseur=fournisseur)
    assert code == cmp.CODE_AUCUN_PASSAGE
    donnees = json.loads((rapports / "20261015-120000" / cmp.FICHIER_JSON).read_text("utf-8"))
    (passage,) = donnees["passages"]
    assert passage["issue"] == cmp.EMPECHE and passage["grille"] is None


@AVEC_GIT
def test_rejouer_met_des_livrables_existants_cote_a_cote(tmp_path: Path) -> None:
    c2 = _depot(tmp_path / "p5-bilan", "Maestro : socle-projet", **{"package.json": "{}\n"})
    c3 = _depot(tmp_path / "p5-seule", "feat: socle", **{"package.json": "{}\n"})

    class Muette(FilFactice):
        def demander(self, methode: str, chemin: str, **reglages: Any) -> Reponse:
            raise AssertionError(f"le rejeu n'appelle aucune API : {chemin}")

    code, _, erreur, rapports = _main(
        tmp_path,
        "--rejouer",
        f"maestro={c2}",
        "--rejouer",
        f"seul={c3}",
        "--axes",
        "installation,commits",
        fil=Muette(tmp_path / "x", horloge=Horloge()),
    )
    assert code == cmp.CODE_ECRIT, erreur
    dossier = rapports / "20261015-120000"
    donnees = json.loads((dossier / cmp.FICHIER_JSON).read_text("utf-8"))
    assert donnees["rejeu"] is True
    assert [(p["cote"], p["livrable"]) for p in donnees["passages"]] == [
        ("maestro", str(c2)),
        ("seul", str(c3)),
    ]
    assert [ligne["axe"] for ligne in donnees["passages"][0]["grille"]["lignes"]] == [
        "installation",
        "commits",
    ]
    texte = (dossier / cmp.FICHIER_MARKDOWN).read_text("utf-8")
    assert "livrables rejoués seulement" in texte


@AVEC_GIT
def test_les_lignes_ecartees_se_lisent_une_fois_pour_les_deux_cotes(tmp_path: Path) -> None:
    """Les lignes que la personne écarte dépendent de ses choix, pas du livrable : une seule
    lecture vaut pour les deux côtés, et le rapport ne les compte pas dans ce qui se tient."""
    maestro = _depot(tmp_path / "maestro", "feat: socle", **{"package.json": "{}\n"})
    chaine = _depot(tmp_path / "chaine", "feat: socle", **{"package.json": "{}\n"})
    lecteur = LecteurFactice(
        ecartees="ECARTEES: FORMATAGE\nPOURQUOI: la personne n'applique aucun formateur."
    )
    code, sortie, erreur, rapports = _main(
        tmp_path,
        "--rejouer",
        f"maestro={maestro}",
        "--rejouer",
        f"chaine={chaine}",
        lecteur=lecteur,
    )
    assert code == cmp.CODE_ECRIT, erreur
    assert len(lecteur.ecartements) == 1
    assert "lignes écartées par la personne" in sortie
    dossier = rapports / "20261015-120000"
    donnees = json.loads((dossier / cmp.FICHIER_JSON).read_text("utf-8"))
    for passage in donnees["passages"]:
        etats = {ligne["axe"]: ligne["etat"] for ligne in passage["grille"]["lignes"]}
        assert etats[gr.AXE_FORMATAGE] == gr.SANS_OBJET
        assert passage["grille"]["jugees"] == passage["grille"]["tenues"] == len(gr.AXES) - 1
    texte = (dossier / cmp.FICHIER_MARKDOWN).read_text("utf-8")
    assert f"| Lignes tenues (sur {len(gr.AXES) - 1}) |" in texte
    assert "| Formatage | sans objet | sans objet |" in texte


@AVEC_GIT
def test_rejouer_juge_chaque_cote_depuis_sa_base(tmp_path: Path) -> None:
    """Les deux livrables d'un ticket (#1522) : chacun part de sa propre base (docs/51 § 3.1)."""
    chaine, base = _depot_au_long_historique(tmp_path / "chaine")
    maestro = _depot(tmp_path / "maestro", "Maestro : socle", **{"README.md": "# m\n"})

    code, sortie, erreur, rapports = _main(
        tmp_path,
        "--rejouer",
        f"maestro={maestro}",
        "--rejouer",
        f"chaine={chaine}",
        f"--base=chaine={base[:10]}",
        "--axes",
        "commits",
    )
    assert code == cmp.CODE_ECRIT, erreur
    assert f"chaine · livrable {chaine} depuis {base[:10]}" in sortie
    dossier = rapports / "20261015-120000"
    donnees = json.loads((dossier / cmp.FICHIER_JSON).read_text("utf-8"))
    bases = {p["cote"]: p["grille"]["base"] for p in donnees["passages"]}
    assert bases == {"maestro": "", "chaine": base}
    texte = (dossier / cmp.FICHIER_MARKDOWN).read_text("utf-8")
    assert f"depuis la base `{base[:12]}`" in texte


@pytest.mark.parametrize(
    "arguments, motif",
    [
        (["--passages", "0"], "≥ 1"),
        (["--passages"], "attend une valeur"),
        (["--cote", "les-deux"], "--cote attend"),
        (["--delai=-3"], "> 0"),
        (["--jour", "0"], "--jour doit être > 0"),
        (["--axes", "vitesse"], "--axes"),
        (["--rejouer", "c2"], "<côté>=<dépôt>"),
        (["--rejouer", "chaine=d", "--base", "de9564ce"], "<côté>=<commit>"),
        (["--rejouer", "chaine=d", "--base", "seul=de9564ce"], "aucun --rejouer pour seul"),
        (["--objectif", "inexistant"], "objectif inconnu"),
        (["--verbeux"], "argument inconnu"),
    ],
)
def test_un_usage_errone_est_refuse_avec_son_motif(
    tmp_path: Path, arguments: list[str], motif: str
) -> None:
    code, _, erreur, _ = _main(tmp_path, *arguments)
    assert code == cmp.CODE_USAGE
    assert motif in erreur


def test_liste_nomme_les_objectifs(tmp_path: Path) -> None:
    code, sortie, _, _ = _main(tmp_path, "--liste")
    assert code == cmp.CODE_ECRIT
    assert sortie.splitlines() == [
        "club  L'application du club de randonnée (projet long)",
        "club-season  L'application du club de randonnée, toute la saison (terrain du go/no-go)",
        "p5  Livre de recettes Next.js (p5)",
    ]


# =================================================================================================
# ⑦ Le projet long, repris le lendemain (#1513)
# =================================================================================================

#: Les choix d'outillage de p5 : une personne qui ne code pas n'en dit aucun.
_CHOIX_DE_P5 = ("TypeScript", "Next.js", "npm", "Vitest", "ESLint", "Prettier", "GitHub")


def test_le_projet_long_est_dit_sans_choix_technique_avec_ses_criteres_et_sa_feuille() -> None:
    objectif = cmp.charger_objectif("club")
    assert objectif.choix == ()
    assert not [mot for mot in _CHOIX_DE_P5 if mot in objectif.message]
    assert "Construisez l'application entière." in objectif.message
    assert len(objectif.criteres) == 20 and len(set(objectif.criteres)) == 20
    assert objectif.reponses and objectif.converse
    # Un jour de travail actif, puis la nuit ; le délai couvre le passage entier.
    assert objectif.jour_s == 1800 and objectif.delai_s == 21600
    # Personne n'a demandé de dépôt versionné, ni de choix confrontés au livrable.
    assert objectif.versionner is False
    assert not {gr.AXE_CI, gr.AXE_COMMITS, gr.AXE_OUTILLAGE} & set(objectif.axes)
    assert set(objectif.cout_estime_usd) == {cmp.COTE_MAESTRO, cmp.COTE_SEUL}


def test_le_terrain_du_go_no_go_est_club_agrandi_jamais_un_jour_raccourci() -> None:
    """Le terrain du go/no-go (#1736) : `club`, qu'une session a bouclé d'un trait, agrandi.

    Dit par la même trésorière, sans choix technique, avec ses critères d'acceptation et sa
    feuille. Le projet grandit, le jour ne raccourcit jamais : un jour plus court ferait
    dépasser une session à n'importe quel projet.
    """
    club = cmp.charger_objectif("club")
    terrain = cmp.charger_objectif("club-season")
    assert terrain.choix == ()
    assert not [mot for mot in _CHOIX_DE_P5 if mot in terrain.message]
    assert "Les Sentiers du Vexin" in terrain.message
    assert "Construisez l'application entière." in terrain.message
    assert len(terrain.criteres) == 54 and len(set(terrain.criteres)) == 54
    assert len(terrain.criteres) >= 2.5 * len(club.criteres)
    assert len(terrain.message) >= 2.5 * len(club.message)
    assert terrain.reponses and terrain.converse
    assert terrain.jour_s == club.jour_s and terrain.delai_s == club.delai_s
    assert terrain.versionner is False and terrain.axes == club.axes
    assert set(terrain.cout_estime_usd) == {cmp.COTE_MAESTRO, cmp.COTE_SEUL}


def test_la_personne_repond_par_sa_feuille_a_toute_question() -> None:
    """La feuille entière, des deux côtés — jamais la ligne qui semblerait répondre (#746)."""
    objectif = cmp.charger_objectif("club")
    feuille = objectif.reponse_de_la_personne
    for reponse in objectif.reponses:
        assert reponse in feuille
    assert objectif.repondre({"cle": "pile"}) == (feuille, True)
    assert objectif.repondre_a_l_agent({"question": "Quelle base de données ?"}) == feuille
    # p5 n'a pas de feuille : la personne n'y suit pas la conversation.
    assert cmp.charger_objectif("p5").converse is False


@pytest.mark.parametrize(
    "donnees, motif",
    [
        ({"message": "ok", "criteres": "tout"}, "criteres"),
        ({"message": "ok", "reponses": ["", "oui"]}, "reponses"),
        ({"message": "ok", "jour_s": 0}, "jour_s"),
        ({"message": "ok", "delai_s": True}, "delai_s"),
    ],
)
def test_un_projet_long_mal_forme_est_refuse(donnees: dict[str, Any], motif: str) -> None:
    with pytest.raises(ValueError, match=motif):
        cmp.objectif_depuis("x", donnees)


# --- Côté Maestro : la nuit, et les questions des agents ---------------------------------------


class FilLong(FilFactice):
    """Le fil, avec un run qui dure plus d'un jour : il tourne, s'éteint, reprend, aboutit.

    Un agent du run pose une question à la personne. `autres_runs` sont des runs d'autres
    projets en vol sur la stack, que l'extinction solderait aussi.
    """

    def __init__(
        self, ailleurs: Path, *, horloge: Horloge, autres_runs: tuple[str, ...] = ()
    ) -> None:
        super().__init__(ailleurs, horloge=horloge)
        self.pas_s = 600.0
        self.eteint = False
        self.repris = False
        self.autres_runs = autres_runs
        self.extinctions = 0
        self.reponses_aux_agents: list[str] = []

    def demander(
        self,
        methode: str,
        chemin: str,
        *,
        corps: Mapping[str, Any] | None = None,
        params: Mapping[str, str] | None = None,
        delai_s: float | None = None,
    ) -> Reponse:
        if chemin == "/api/executions":
            vol = [] if self.eteint and not self.repris else [{"run_id": "run-1"}]
            vol += [{"run_id": autre} for autre in self.autres_runs]
            return Reponse(200, [{**r, "statut": EXECUTION_EN_COURS} for r in vol])
        if chemin == "/api/extinction":
            self.extinctions += 1
            self.eteint = True
            return Reponse(200, {"runs": [{"run_id": "run-1"}]})
        if chemin == "/api/executions/run-1/reprendre":
            assert self.eteint, "« Reprendre » ne vient qu'après l'extinction"
            self.repris = True
            return Reponse(200, {"run_id": "run-1", "statut": EXECUTION_EN_COURS})
        if chemin == "/api/questions":
            repondue = bool(self.reponses_aux_agents)
            question = {
                "question_id": "q-1",
                "run_id": "run-1",
                "tache_id": "adherents",
                "agent": "dev",
                "question": "Le tarif famille couvre-t-il les enfants ?",
                "statut": "repondue" if repondue else "en_attente",
            }
            return Reponse(200, [question])
        if chemin == "/api/questions/q-1/reponse":
            self.reponses_aux_agents.append(str((corps or {}).get("reponse")))
            return Reponse(200, {})
        return super().demander(methode, chemin, corps=corps, params=params, delai_s=delai_s)

    def _run(self) -> dict[str, Any]:
        if self.repris:
            self._horloge.maintenant += 300.0
            if not self._fil:
                self._fil.append({"auteur": "orchestrateur", "run_id": "run-1", "contenu": "fini"})
            return {"statut": EXECUTION_TERMINEE, "cout_usd": 30.0, "nb_taches": 6}
        if self.eteint:
            return {"statut": EXECUTION_ECHEC, "cause": "extinction", "cout_usd": 8.0}
        self._horloge.maintenant += self.pas_s
        return {"statut": EXECUTION_EN_COURS, "cout_usd": 8.0}


def _maestro_long(
    tmp_path: Path,
    *,
    autres_runs: tuple[str, ...] = (),
    redemarrer: Callable[[], str] | None = None,
    jour_s: float = 1800.0,
    delai_s: float = 21600.0,
) -> tuple[cmp.Passage, FilLong, cmp.Objectif, list[str]]:
    horloge = Horloge()
    fil = FilLong(tmp_path / "Maestro" / "club", horloge=horloge, autres_runs=autres_runs)
    rallumages: list[str] = []

    def rallumer() -> str:
        rallumages.append("rallumée")
        horloge.maintenant += 45.0  # ce que la nuit prend au banc : couper, rallumer
        return "API coupée puis rallumée"

    objectif = replace(cmp.charger_objectif("club"), jour_s=jour_s)
    passage = cmp.jouer_maestro(
        objectif,
        rang=1,
        client=cmp.ClientCompteur(fil),
        atelier=Atelier(tmp_path / "atelier"),
        juge=JugeModele(),
        journal=Journal(),
        delai_s=delai_s,
        horloge=horloge,
        dormir=horloge.dormir,
        redemarrer=redemarrer or rallumer,
    )
    return passage, fil, objectif, rallumages


def test_maestro_passe_la_nuit_eteint_puis_reprend_son_run(tmp_path: Path) -> None:
    passage, fil, _, rallumages = _maestro_long(tmp_path)

    assert passage.issue == cmp.ABOUTI, passage.motif
    # Le soir : Maestro éteint comme quand on le ferme, une fois ; le lendemain, rallumé.
    assert fil.extinctions == 1 and rallumages == ["rallumée"]
    assert fil.repris
    assert "éteint" in passage.nuit and "le même run a continué" in passage.nuit
    # La reprise est un geste de la personne, le seul de la nuit ; éteindre n'en est pas un.
    assert passage.gestes is not None and passage.gestes.count(cmp.GESTE_REPRISE) == 1
    # La nuit n'entre pas dans la durée active.
    assert passage.nuit_s == pytest.approx(45.0)
    libelles = [etape.libelle for etape in passage.etapes]
    assert libelles.index("fin du jour") < libelles.index("le soir : Maestro éteint")
    assert libelles.index("le lendemain : run repris") < libelles.index("run soldé")


def test_maestro_fait_repondre_la_personne_aux_questions_des_agents(tmp_path: Path) -> None:
    passage, fil, objectif, _ = _maestro_long(tmp_path)
    # Une question, une réponse — sa feuille —, et une fois répondue, elle ne revient pas.
    assert fil.reponses_aux_agents == [objectif.reponse_de_la_personne]
    assert passage.gestes is not None and passage.gestes.count(cmp.GESTE_REPONSE) == 1
    assert any(e.libelle.startswith("question d'un agent") for e in passage.etapes)


def test_un_jour_plus_long_que_le_passage_ne_passe_pas_de_nuit(tmp_path: Path) -> None:
    passage, fil, _, rallumages = _maestro_long(tmp_path, jour_s=100_000.0, delai_s=3000.0)
    assert fil.extinctions == 0 and not rallumages
    assert passage.nuit == "" and passage.nuit_s is None
    assert passage.issue == cmp.NON_ABOUTI


def test_d_autres_runs_en_vol_font_sauter_la_nuit(tmp_path: Path) -> None:
    """L'extinction les solderait aussi : le banc n'éteint pas Maestro sous eux."""
    passage, fil, _, rallumages = _maestro_long(tmp_path, autres_runs=("run-autre",))
    assert fil.extinctions == 0 and not rallumages
    assert "run-autre" in passage.nuit and "n'a pas été éteint" in passage.nuit


def test_maestro_qui_ne_se_rallume_pas_rend_le_passage_empeche(tmp_path: Path) -> None:
    def refuse() -> str:
        raise OSError("le port :8013 ne se libère pas")

    passage, fil, _, _ = _maestro_long(tmp_path, redemarrer=refuse)
    assert fil.extinctions == 1
    assert passage.issue == cmp.EMPECHE and not passage.compte
    assert "rallumer Maestro" in passage.motif
    assert passage.nuit.startswith("manquée")


# --- Côté agent seul : la conversation, et la nuit -------------------------------------------


class SessionScriptee(FournisseurFactice):
    """L'adaptateur, qui joue une suite de sessions : chacune dort, puis rend un texte.

    Chaque fin porte un repère que le faux lecteur lit (`LecteurDeFin`) : c'est le double
    du jugement du modèle, pas un lexique du banc.
    """

    def __init__(
        self, script: list[tuple[float, str]], *, livrer: Callable[[Path], None] | None = None
    ) -> None:
        super().__init__(cout_usd=7.5, livrer=livrer)
        self._script = list(script)

    async def run_agent(self, prompt: str, **reglages: Any) -> str:
        self.appels.append({"prompt": prompt, **reglages})
        report_usage(StepUsage(calls=1, output_tokens=100, cost_usd=7.5))
        dort, texte = self._script.pop(0)
        if dort:
            await asyncio.sleep(dort)
        if self._livrer is not None:
            self._livrer(Path(reglages["workspace"]))
        return texte


class LecteurDeFin(LecteurFactice):
    """Le modèle qui lit une fin pour la personne — il lit le repère que la session a mis.

    Sa lecture coûte, et ce coût ne doit pas entrer dans celui de l'agent seul.
    """

    def __init__(self, *, lent_s: float = 0.0) -> None:
        super().__init__()
        self._lent_s = lent_s

    def juger(self, systeme: str, prompt: str) -> Avis:
        report_usage(StepUsage(calls=1, output_tokens=10, cost_usd=100.0))
        if self._lent_s:
            time.sleep(self._lent_s)
        if systeme == cmp.SYSTEME_ATTEND:
            return Avis(nomme="[QUESTION]" in prompt, pourquoi="lu")
        if systeme == cmp.SYSTEME_TERMINE:
            return Avis(nomme="[FINI]" in prompt, pourquoi="lu")
        return super().juger(systeme, prompt)


def _seul_long(
    tmp_path: Path, script: list[tuple[float, str]], *, jour_s: float, lecteur: LecteurDeFin
) -> tuple[cmp.Passage, SessionScriptee, cmp.Objectif]:
    fournisseur = SessionScriptee(script)
    objectif = replace(cmp.charger_objectif("club"), jour_s=jour_s)
    passage = cmp.jouer_seul(
        objectif,
        rang=1,
        atelier=Atelier(tmp_path / "atelier"),
        fournisseur=fournisseur,
        modele="claude-opus-5-5",
        journal=Journal(),
        delai_s=30.0,
        lecteur=lecteur,
    )
    return passage, fournisseur, objectif


def test_la_session_seule_est_fermee_le_soir_puis_rouverte_le_lendemain(tmp_path: Path) -> None:
    passage, fournisseur, objectif = _seul_long(
        tmp_path,
        [
            (10.0, "jamais rendu : la nuit tombe avant"),
            (0.0, "Les adhérents sont faits. [QUESTION] Le tarif famille couvre-t-il qui ?"),
            (0.0, "Il reste les comptes et le covoiturage."),
            (0.0, "[FINI] Tout est livré."),
        ],
        jour_s=0.3,
        lecteur=LecteurDeFin(),
    )

    assert passage.issue == cmp.ABOUTI, passage.motif
    # Une seule conversation, rouverte d'une session à l'autre — jamais une neuve.
    sessions = {id(appel["session"]) for appel in fournisseur.appels}
    assert len(sessions) == 1 and isinstance(fournisseur.appels[0]["session"], SessionAgent)
    # Ce que la personne a dit : son message, « Continue. » le lendemain, sa feuille à la
    # question, « Continue. » à une session arrêtée en chemin — jamais d'aide technique.
    assert [appel["prompt"] for appel in fournisseur.appels] == [
        objectif.message,
        cmp.RELANCE,
        objectif.reponse_de_la_personne,
        cmp.RELANCE,
    ]
    assert passage.gestes == (
        cmp.GESTE_MESSAGE,
        cmp.GESTE_REPRISE,
        cmp.GESTE_REPONSE,
        cmp.GESTE_RELANCE,
    )
    assert [(t.jour, t.fin, t.lecture) for t in passage.tours] == [
        (1, cmp.FIN_NUIT, ""),
        (2, cmp.FIN_RENDUE, cmp.LECTURE_QUESTION),
        (2, cmp.FIN_RENDUE, cmp.LECTURE_EN_CHEMIN),
        (2, cmp.FIN_RENDUE, cmp.LECTURE_TERMINE),
    ]
    assert "en plein travail" in passage.nuit
    # La première session a été coupée : le projet ne s'est pas bouclé d'un trait.
    assert passage.d_un_trait is False
    # Le coût de l'agent seul est celui de ses sessions ; la lecture est la dépense du banc.
    assert passage.cout_usd == pytest.approx(4 * 7.5)


class SessionTarifeeAuResultat(SessionScriptee):
    """L'adaptateur tel qu'il tarife : les tokens d'un tour sans prix, le prix au résultat.

    Un tour signale ses tokens dès qu'il passe, sans coût ; le résultat, s'il vient,
    porte le prix de la session et couvre ses tours (`claude._CompteurTours`). Une
    session coupée avant son résultat garde donc ses tokens sans prix (#1280).
    """

    TOKENS_PAR_SESSION = 1000

    async def run_agent(self, prompt: str, **reglages: Any) -> str:
        self.appels.append({"prompt": prompt, **reglages})
        report_usage(StepUsage(output_tokens=self.TOKENS_PAR_SESSION))
        dort, texte = self._script.pop(0)
        if dort:
            await asyncio.sleep(dort)
        report_usage(StepUsage(calls=1, cost_usd=2.0, unpriced_tokens=-self.TOKENS_PAR_SESSION))
        return texte


def test_une_session_fermee_le_soir_laisse_un_cout_plancher(tmp_path: Path) -> None:
    """Le banc n'invente pas le prix du premier jour : il dit que le coût est un plancher."""
    fournisseur = SessionTarifeeAuResultat(
        [(10.0, "jamais rendu : la nuit tombe avant"), (0.0, "[FINI] Tout est livré.")]
    )
    passage = cmp.jouer_seul(
        replace(cmp.charger_objectif("club-season"), jour_s=0.3),
        rang=1,
        atelier=Atelier(tmp_path / "atelier"),
        fournisseur=fournisseur,
        modele="claude-opus-5-5",
        journal=Journal(),
        delai_s=30.0,
        lecteur=LecteurDeFin(),
    )

    # Seul le lendemain a rendu son résultat, donc son prix ; la veille reste sans prix.
    assert passage.cout_usd == pytest.approx(2.0)
    assert passage.tokens_sans_cout == SessionTarifeeAuResultat.TOKENS_PAR_SESSION
    assert passage.cout_plancher
    assert passage.to_dict()["unpriced_tokens"] == SessionTarifeeAuResultat.TOKENS_PAR_SESSION
    texte = cmp.en_markdown(
        cmp.Comparatif("20261118-120000", cmp.charger_objectif("club"), (passage,))
    )
    assert "| Coût | ≥ 2.00 $ |" in texte
    assert "- **coût** : ≥ 2.00 $ (rapporté par l'adaptateur) — un plancher : 1000 tokens" in texte
    assert "Un coût précédé de « ≥ » est un plancher" in texte
    # Une session qui rend son résultat couvre ses tokens : son coût n'est pas un plancher.
    entier = _passage(cmp.COTE_SEUL, 1, cout_usd=2.0)
    assert not entier.cout_plancher
    assert "≥" not in cmp.en_markdown(
        cmp.Comparatif("20261118-120000", cmp.charger_objectif("club"), (entier,))
    )


def test_une_session_qui_rend_la_main_le_soir_recoit_sa_reponse_le_lendemain(
    tmp_path: Path,
) -> None:
    passage, fournisseur, objectif = _seul_long(
        tmp_path,
        [(0.0, "[QUESTION] Quelles couleurs ?"), (0.0, "[FINI] Livré.")],
        jour_s=0.1,
        lecteur=LecteurDeFin(lent_s=0.2),
    )
    assert passage.issue == cmp.ABOUTI
    assert [appel["prompt"] for appel in fournisseur.appels] == [
        objectif.message,
        objectif.reponse_de_la_personne,
    ]
    assert passage.gestes == (cmp.GESTE_MESSAGE, cmp.GESTE_REPRISE)
    assert "après avoir rendu la main" in passage.nuit


def test_une_session_qui_finit_dans_la_journee_ne_passe_pas_de_nuit(tmp_path: Path) -> None:
    passage, fournisseur, _ = _seul_long(
        tmp_path, [(0.0, "[FINI] Tout est livré.")], jour_s=600.0, lecteur=LecteurDeFin()
    )
    assert passage.issue == cmp.ABOUTI
    assert len(fournisseur.appels) == 1 and passage.gestes == (cmp.GESTE_MESSAGE,)
    assert passage.nuit == "" and passage.nuit_s is None


def _couverture(*etats: str) -> cv.Couverture:
    return cv.Couverture(
        verdicts=tuple(
            cv.Verdict(rang, f"critère {rang}", etat) for rang, etat in enumerate(etats, start=1)
        )
    )


def test_d_un_trait_se_juge_sur_la_premiere_session_et_les_criteres() -> None:
    rendue = cmp.Tour(1, cmp.GESTE_MESSAGE, cmp.FIN_RENDUE, cmp.LECTURE_TERMINE)
    coupee = cmp.Tour(1, cmp.GESTE_MESSAGE, cmp.FIN_NUIT)
    en_chemin = cmp.Tour(1, cmp.GESTE_MESSAGE, cmp.FIN_RENDUE, cmp.LECTURE_EN_CHEMIN)

    def passage(tour: cmp.Tour, couverture: cv.Couverture | None) -> cmp.Passage:
        return _passage(cmp.COTE_SEUL, 1, tours=(tour,), couverture=couverture)

    tenus = _couverture(cv.TENU, cv.TENU)
    assert passage(rendue, tenus).d_un_trait is True
    assert passage(rendue, _couverture(cv.TENU, cv.MANQUE)).d_un_trait is False
    assert passage(coupee, tenus).d_un_trait is False
    assert passage(en_chemin, tenus).d_un_trait is False
    # Un critère que le juge n'a pas tranché ne fait pas dire « oui ».
    assert passage(rendue, _couverture(cv.TENU, cv.NON_JUGE)).d_un_trait is None
    assert passage(rendue, None).d_un_trait is None
    assert _passage(cmp.COTE_MAESTRO, 1).d_un_trait is None


@pytest.mark.parametrize(
    "tour, couverture, cause",
    [
        (
            cmp.Tour(1, cmp.GESTE_MESSAGE, cmp.FIN_NUIT, duree_s=1800.0),
            (cv.TENU, cv.TENU),
            "non — la première session a été fermée le soir en plein travail, après 30 min 00 s"
            " : elle n'avait pas fini à la fin du premier jour : le projet dépasse une session",
        ),
        (
            cmp.Tour(1, cmp.GESTE_MESSAGE, cmp.FIN_RENDUE, cmp.LECTURE_EN_CHEMIN, duree_s=900.0),
            (cv.TENU, cv.TENU),
            "non — la première session a rendu la main avant la fin, après 15 min 00 s, sans "
            "dire le projet terminé : le projet dépasse une session",
        ),
        (
            cmp.Tour(1, cmp.GESTE_MESSAGE, cmp.FIN_RENDUE, cmp.LECTURE_QUESTION, duree_s=60.0),
            (cv.TENU, cv.TENU),
            "non — la première session a rendu la main sur une question, après 1 min 00 s",
        ),
        (
            cmp.Tour(1, cmp.GESTE_MESSAGE, cmp.FIN_RENDUE, cmp.LECTURE_TERMINE, duree_s=1200.0),
            (cv.TENU, cv.MANQUE, cv.TENU),
            "non — la première session l'a dit terminé après 20 min 00 s, mais le livrable ne "
            "tient que 2 critère(s) sur 3 : le projet dépasse une session",
        ),
        (
            cmp.Tour(1, cmp.GESTE_MESSAGE, cmp.FIN_ECHEC, duree_s=5.0),
            (cv.TENU,),
            "non — la première session a échoué (voir le motif) : ce passage ne dit pas si le "
            "projet dépasse une session",
        ),
    ],
)
def test_le_rapport_nomme_ce_qui_rompt_le_trait(
    tour: cmp.Tour, couverture: tuple[str, ...], cause: str
) -> None:
    """Un « non » dit pourquoi (#1736) : la nuit, une main rendue en chemin, un critère manqué."""
    passage = _passage(cmp.COTE_SEUL, 1, tours=(tour,), couverture=_couverture(*couverture))
    assert passage.d_un_trait is False
    texte = cmp.en_markdown(
        cmp.Comparatif("20261118-120000", cmp.charger_objectif("club-season"), (passage,))
    )
    assert f"- **d'un trait** : {cause}" in texte


def test_la_lecture_d_une_fin_demande_d_abord_si_l_agent_attend_une_reponse() -> None:
    lecteur = LecteurDeFin()
    assert cmp.lire_la_fin(lecteur, "[QUESTION] [FINI] Je continue ?") == cmp.LECTURE_QUESTION
    assert cmp.lire_la_fin(lecteur, "[FINI] Livré.") == cmp.LECTURE_TERMINE
    assert cmp.lire_la_fin(lecteur, "Il reste du travail.") == cmp.LECTURE_EN_CHEMIN


def test_une_fin_illisible_se_lit_en_chemin() -> None:
    """Un juge qui s'abstient ne vaut pas un « oui » : la personne relance."""

    class Muet(LecteurFactice):
        def juger(self, systeme: str, prompt: str) -> Avis:
            return Avis(nomme=True, pourquoi="hors contrat", lisible=False)

    assert cmp.lire_la_fin(Muet(), "[FINI]") == cmp.LECTURE_EN_CHEMIN


# --- La couverture des critères ----------------------------------------------------------------


def test_les_verdicts_se_lisent_ligne_par_ligne_et_la_derniere_l_emporte() -> None:
    brut = (
        "Je regarde d'abord C1.\n"
        "C1: manqué — pas vu au premier abord\n"
        "…\n"
        "C1: tenu — app/adherents.py crée et modifie la fiche\n"
        "- C2 : manque — aucune recherche par nom\n"
        "C4: tenu — tarifs.py\n"
    )
    couverture = cv.couverture_depuis(brut, ["fiche", "recherche", "liste", "tarifs"])
    assert [(v.cle, v.etat) for v in couverture.verdicts] == [
        ("C1", cv.TENU),
        ("C2", cv.MANQUE),
        ("C3", cv.NON_JUGE),
        ("C4", cv.TENU),
    ]
    assert couverture.verdicts[0].piece == "app/adherents.py crée et modifie la fiche"
    assert (couverture.tenus, couverture.juges) == (2, 3)
    assert not couverture.complete


def test_le_juge_des_criteres_lit_le_clone_en_lecture_seule(tmp_path: Path) -> None:
    class Juge(FournisseurFactice):
        async def run_agent(self, prompt: str, **reglages: Any) -> str:
            self.appels.append({"prompt": prompt, **reglages})
            report_usage(StepUsage(calls=1, output_tokens=100, cost_usd=1.25))
            return "C1: tenu — a.py\nC2: manqué — rien"

    fournisseur = Juge()
    juge = cv.JugeCouvertureModele(fournisseur, modele="claude-opus-5-5")
    couverture = juge.couvrir(tmp_path, ["fiche", "recherche"])

    (appel,) = fournisseur.appels
    assert Path(appel["workspace"]) == tmp_path
    assert tuple(appel["tools"]) == cv.OUTILS_DU_JUGE
    assert not {"Write", "Edit", "Bash"} & set(cv.OUTILS_DU_JUGE)
    assert appel["system_prompt"] == cv.SYSTEME_COUVERTURE
    assert "C1. fiche" in appel["prompt"] and "C2. recherche" in appel["prompt"]
    assert (couverture.tenus, couverture.juges) == (1, 2)
    assert couverture.cout_usd == pytest.approx(1.25)


def test_un_juge_des_criteres_injoignable_ne_juge_aucun_critere(tmp_path: Path) -> None:
    juge = cv.JugeCouvertureModele(
        FournisseurFactice(leve=RuntimeError("quota")),
        modele="claude-opus-5-5",
    )
    couverture = juge.couvrir(tmp_path, ["fiche", "recherche"])
    assert couverture.juges == 0 and couverture.tenus == 0
    assert "juge injoignable" in couverture.motif


class JugeDeCriteres:
    """Le juge des critères, réduit à ce qu'il a lu et à ce qu'il rend."""

    def __init__(self, *etats: str) -> None:
        self._etats = etats
        self.lus: list[Path] = []

    def couvrir(self, depot: Path, criteres: Any) -> cv.Couverture:
        self.lus.append(depot)
        etats = list(self._etats) + [cv.MANQUE] * (len(criteres) - len(self._etats))
        return cv.Couverture(
            verdicts=tuple(
                cv.Verdict(rang, critere, etat)
                for rang, (critere, etat) in enumerate(zip(criteres, etats, strict=True), start=1)
            )
        )


@AVEC_GIT
def test_un_dossier_non_versionne_se_rejoue_moins_ce_qu_il_ignore(tmp_path: Path) -> None:
    """Personne n'a demandé de dépôt versionné : le banc prend le dossier, sans y écrire."""
    livrable = tmp_path / "seul-1"
    (livrable / "node_modules" / "paquet").mkdir(parents=True)
    (livrable / "node_modules" / "paquet" / "index.js").write_text("x\n", encoding="utf-8")
    (livrable / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    (livrable / "app.py").write_text("print('club')\n", encoding="utf-8")
    copie = tmp_path / "seul-1-clone"

    grille = gr.rejouer(
        livrable,
        copie,
        choix=(),
        lecteur=LecteurFactice(),
        poste=_poste(Joueur()),
        axes=(gr.AXE_INSTALLATION,),
        versionne=False,
    )

    assert (copie / "app.py").is_file() and (copie / ".git").exists()
    assert not (copie / "node_modules").exists()
    assert not (livrable / ".git").exists()
    installation = grille.ligne(gr.AXE_INSTALLATION)
    assert installation is not None and installation.etat == gr.TENU
    # Demandé sous Git, le même dossier manque toutes ses lignes, comme avant.
    exige = gr.rejouer(
        livrable, tmp_path / "exige", choix=(), lecteur=LecteurFactice(), versionne=True
    )
    assert {ligne.etat for ligne in exige.lignes} == {gr.MANQUE}


@AVEC_GIT
def test_les_criteres_se_jugent_sur_le_clone_et_entrent_au_rapport(tmp_path: Path) -> None:
    objectif = cmp.charger_objectif("club")
    juge = JugeDeCriteres(cv.TENU, cv.TENU, cv.MANQUE)

    def livrer(etabli: Path) -> None:
        (etabli / "app.py").write_text("print('club')\n", encoding="utf-8")

    fournisseur = SessionScriptee(
        [(0.0, "Les adhérents sont faits."), (0.0, "[FINI] Livré.")], livrer=livrer
    )
    code, _, erreur, rapports = _main(
        tmp_path,
        "--objectif",
        "club",
        "--cote",
        "seul",
        "--jour",
        "600",
        fournisseur=fournisseur,
        lecteur=LecteurDeFin(),
        juge_couverture=juge,
    )

    assert code == cmp.CODE_ECRIT, erreur
    (lu,) = juge.lus
    assert lu.name == "seul-1-clone" and (lu / "app.py").is_file()
    dossier = rapports / "20261015-120000"
    donnees = json.loads((dossier / cmp.FICHIER_JSON).read_text("utf-8"))
    (passage,) = donnees["passages"]
    assert passage["couverture"]["tenus"] == 2
    assert passage["couverture"]["juges"] == len(objectif.criteres)
    assert donnees["cotes"]["seul"]["mesures"]["criteres_tenus"]["mediane"] == 2
    assert donnees["cotes"]["seul"]["criteres"]["C1"] == {"tenus": 1, "juges": 1}
    assert donnees["objectif"]["jour_s"] == 600
    texte = (dossier / cmp.FICHIER_MARKDOWN).read_text("utf-8")
    assert f"Critères tenus (sur {len(objectif.criteres)})" in texte
    assert "## Les critères d'acceptation, un par un" in texte
    # La session n'a rien dit de sa fin : « en chemin », relancée, et la première ne l'a
    # pas bouclé d'un trait.
    assert "**d'un trait** : non" in texte
    assert "Durée active" in texte
    assert passage["detail_gestes"] == [cmp.GESTE_MESSAGE, cmp.GESTE_RELANCE]


@AVEC_GIT
def test_le_banc_annonce_le_jour_et_le_cout_du_projet_long(tmp_path: Path) -> None:
    code, sortie, erreur, _ = _main(
        tmp_path,
        "--objectif",
        "club",
        "--cote",
        "seul",
        fournisseur=SessionScriptee([(0.0, "[FINI] Livré.")]),
        lecteur=LecteurDeFin(),
        juge_couverture=JugeDeCriteres(),
    )
    assert code == cmp.CODE_ECRIT, erreur
    premiere = sortie.splitlines()[0]
    assert "délai 21600 s par passage" in premiere
    assert "un jour de 1800 s, puis la nuit" in premiere
    assert "~28 $ par passage côté seul" in sortie.splitlines()[1]

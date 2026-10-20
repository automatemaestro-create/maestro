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
from collections.abc import Callable, Mapping
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
from maestro.providers.base import SessionAgent
from maestro.sandbox.verification import Execution
from maestro.scenarios import comparatif as cmp
from maestro.scenarios import couverture as cv
from maestro.scenarios import grille as gr
from maestro.scenarios.api import FIL, ErreurAPI, Reponse
from maestro.scenarios.juge import Avis, JugeModele
from maestro.scenarios.modele import Journal
from maestro.scenarios.projets import Atelier
from maestro.scenarios.scenarios import REPONSE_OUTILLAGE, port_de_service
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

    def demander(
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
        report_usage(StepUsage(appels=1, tokens_sortie=100, cout_usd=self._cout))
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
    passage, _ = _seul(tmp_path, FournisseurFactice(leve=RuntimeError("Usage limit reached")))
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
    """Le modèle de la grille : les commandes qu'il lit, le verdict de chaque ligne jugée."""

    def __init__(
        self,
        *,
        commandes: str = _COMMANDES,
        verdicts: Mapping[str, Avis] | None = None,
        injoignable: bool = False,
    ) -> None:
        self._commandes, self._injoignable = commandes, injoignable
        self._verdicts = dict(verdicts or {})
        self.lus: list[str] = []
        self.juges: list[str] = []

    def rediger(self, systeme: str, prompt: str) -> str:
        if self._injoignable:
            raise RuntimeError("fournisseur injoignable")
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
    """

    def __init__(self, codes: Mapping[str, int] | None = None, *, serveur_s: float = 0.0) -> None:
        self.codes = dict(codes or {})
        self.serveur_s = serveur_s
        self.joues: list[tuple[str, Path]] = []

    def __call__(self, commande: str, dossier: Path, delai_s: float) -> Execution:
        self.joues.append((commande, dossier))
        if commande.startswith("PORT=") and self.serveur_s:
            time.sleep(self.serveur_s)
        code = self.codes.get(re.sub(r"^(CI=1|PORT=\d+) ", "", commande), 0)
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
    grille = _rejouer(tmp_path, LecteurFactice(), Joueur(), choix=())
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
    fournisseur = FournisseurFactice(leve=RuntimeError("rate limit"))
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
        report_usage(StepUsage(appels=1, tokens_sortie=100, cout_usd=7.5))
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
        report_usage(StepUsage(appels=1, tokens_sortie=10, cout_usd=100.0))
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
            report_usage(StepUsage(appels=1, tokens_sortie=100, cout_usd=1.25))
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

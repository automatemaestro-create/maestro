"""L'exercice construit : ses verbes, son relevé, et l'oracle écrit avant de jouer (#1569).

`maestro.scenarios.exercice` joue sur la vraie stack, par la porte d'entrée réelle ; ces
tests le jouent contre un **double de l'API** (`ApiDouble`) qui ne rejoue que les
décisions dont les verbes dépendent — un projet proposé puis accepté dans le fil, une
question puis une pièce d'outillage, une équipe proposée, un run accordé qui se solde —,
sans réseau, sans modèle et sans attente réelle. Ce qui se vérifie sur la vraie stack,
un exercice joué la vérifie (docs/10 §6).
"""

from __future__ import annotations

import io
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from maestro.controltower.donnees import DEPOTS, ORIGINE_BANC, Donnees
from maestro.controltower.state import (
    EXECUTION_EN_ATTENTE_ARBITRAGE,
    EXECUTION_EN_COURS,
    EXECUTION_TERMINEE,
    QUESTION_EN_ATTENTE,
    QUESTION_REPONDUE,
    VALIDATION_APPROUVEE,
    VALIDATION_EN_ATTENTE,
    VALIDATION_REFUSEE,
)
from maestro.espace import Espace
from maestro.outillage.questionnaire import PROPOSITION_RETENUE
from maestro.scenarios import etat
from maestro.scenarios import exercice as ex
from maestro.scenarios.api import FIL, ClientAPI, ErreurAPI, Reponse
from maestro.scenarios.juge import JugeModele

ESPACE_DU_BANC = "copie.banc"


@dataclass
class RunDouble:
    """Un run du double : les statuts qu'il sert, lecture après lecture, puis le dernier."""

    run_id: str
    projet_id: str
    statuts: list[str] = field(default_factory=lambda: [EXECUTION_TERMINEE])
    cout_usd: float = 0.25
    lectures: int = 0

    def detail(self) -> dict[str, Any]:
        statut = self.statuts[min(self.lectures, len(self.statuts) - 1)]
        self.lectures += 1
        return {
            "run_id": self.run_id,
            "statut": statut,
            "cause": "",
            "cout_usd": self.cout_usd,
            "nb_taches": 1,
            "evenements": [{"statut": statut, "detail": "…"}],
        }


class ApiDouble:
    """Le `Transport` des verbes, au-dessus d'un modèle minimal du produit.

    `question` est la question d'outillage que l'accord du projet ouvre (aucune si
    `None`), suivie d'une pièce, `AGENTS.md`, que le geste « écrire » pose sur le disque.
    `statuts` est ce que chaque run accordé sert, lecture après lecture ; un run en
    `en_attente_arbitrage` porte une demande de validation, un run qui a `question_agent`
    une question libre à la personne.
    """

    def __init__(
        self,
        *,
        espace: str = ESPACE_DU_BANC,
        sante: bool = True,
        question: Mapping[str, Any] | None = None,
        statuts: Sequence[str] = (EXECUTION_TERMINEE,),
        question_agent: str = "",
        refuse: str = "",
    ) -> None:
        self._espace = espace
        self._sante = sante
        self._question = dict(question) if question is not None else None
        self._statuts = list(statuts)
        self._question_agent = question_agent
        self._refuse = refuse
        self.fil: list[dict[str, Any]] = []
        self.projets: dict[str, dict[str, Any]] = {}
        self.equipes: dict[str, int] = {}
        self.runs: list[RunDouble] = []
        self.validations: list[dict[str, Any]] = []
        self.questions: list[dict[str, Any]] = []
        self.reponses_outillage: list[dict[str, Any]] = []
        self.reponses_agents: list[str] = []
        self.decisions: list[tuple[str, bool]] = []
        self._proposee: dict[str, Any] | None = None
        self._piece_servie = False

    # --- Le transport -----------------------------------------------------

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
        params = dict(params or {})
        if self._refuse and chemin == self._refuse:
            return Reponse(statut=500, corps={}, texte="panne (test)")
        if chemin == "/api/sante":
            if not self._sante:
                raise ErreurAPI("API injoignable (test)", chemin=chemin)
            return Reponse(statut=200, corps={"statut": "ok", "espace": self._espace})
        routes = {
            ("POST", f"{FIL}/conversations"): lambda: Reponse(
                statut=201, corps={"conversation": {"id": "conv-1"}}
            ),
            ("GET", FIL): lambda: Reponse(statut=200, corps={"messages": list(self.fil)}),
            ("POST", f"{FIL}/messages"): lambda: self._message(corps),
            ("POST", f"{FIL}/projet"): lambda: self._accord(),
            ("POST", f"{FIL}/outillage"): lambda: self._repondre(corps),
            ("POST", f"{FIL}/outillage/piece"): lambda: self._trancher(corps),
            ("POST", f"{FIL}/recrutement"): lambda: self._recruter(corps),
            ("POST", f"{FIL}/cadrage"): lambda: self._cadrer(corps),
            ("POST", "/api/projets"): lambda: self._declarer(corps),
            ("GET", "/api/projets"): lambda: Reponse(statut=200, corps=list(self.projets.values())),
            ("GET", "/api/validations"): lambda: Reponse(statut=200, corps=self.validations),
            ("GET", "/api/questions"): lambda: Reponse(statut=200, corps=self.questions),
            ("GET", "/api/taches"): lambda: Reponse(
                statut=200, corps=[{"id": "t1", "titre": "Écrire", "statut": "terminee"}]
            ),
            ("GET", "/api/analytics/couts"): lambda: self._couts(params),
        }
        if (methode, chemin) in routes:
            return routes[(methode, chemin)]()
        morceaux = chemin.strip("/").split("/")
        if chemin.endswith("/equipe/proposition"):
            return Reponse(statut=200, corps={"id": "prop-1", "roles": [_role()]})
        if chemin.endswith("/equipe") and methode == "POST":
            self.equipes[morceaux[2]] = len(corps.get("roles") or [])
            return Reponse(statut=201, corps={"cree": True})
        if chemin.endswith("/versionner"):
            self.projets[morceaux[2]]["vcs"] = "git"
            return Reponse(statut=200, corps=self.projets[morceaux[2]])
        if chemin.startswith("/api/executions/"):
            return self._execution(morceaux[2])
        if chemin.startswith("/api/validations/"):
            return self._decider(morceaux[2], bool(corps.get("approuve")))
        if chemin.startswith("/api/questions/"):
            return self._repondre_a_l_agent(morceaux[2], str(corps.get("reponse") or ""))
        raise AssertionError(f"le double ne connaît pas {methode} {chemin}")

    def flux(self, chemin: str, **_: Any) -> Any:  # pragma: no cover - aucun verbe ne diffuse
        raise AssertionError(f"le double ne diffuse pas {chemin}")

    # --- Les décisions ----------------------------------------------------

    def _paire(self, demande: str, reponse: Mapping[str, Any]) -> Reponse:
        messages = [
            {"auteur": "utilisateur", "contenu": demande, "run_id": ""},
            {"auteur": "orchestrateur", "run_id": "", **dict(reponse)},
        ]
        self.fil.extend(messages)
        return Reponse(statut=201, corps={"messages": messages})

    def _message(self, corps: Mapping[str, Any]) -> Reponse:
        projet_id = str(corps.get("projet_id") or "")
        contenu = str(corps.get("contenu") or "")
        if not projet_id:
            marque = "dans le dossier "
            racine = (
                contenu.split(marque, 1)[1].split()[0].rstrip(".")
                if marque in contenu
                else "/ailleurs/projet"
            )
            self._proposee = {"nom": Path(racine).name, "racine": racine, "origine": "nouveau"}
            return self._paire(
                contenu, {"contenu": "Je vous le propose.", "projet_propose": self._proposee}
            )
        if not self.equipes.get(projet_id):
            return self._paire(
                contenu, {"contenu": "Il faut une équipe.", "recrutement": {"objectif": contenu}}
            )
        return self._paire(contenu, {"contenu": "Je lance ?", "proposition": contenu})

    def _accord(self) -> Reponse:
        assert self._proposee is not None
        racine = Path(str(self._proposee["racine"]))
        racine.mkdir(parents=True, exist_ok=True)
        fiche = {"id": "prj-1", "nom": racine.name, "racine": racine.as_posix()}
        self.projets["prj-1"] = fiche
        return self._paire(
            "Oui, crée ce projet.",
            {"contenu": "C'est fait.", "projet_cree": fiche, **self._suite()},
        )

    def _suite(self) -> dict[str, Any]:
        if self._question is not None:
            return {"question": self._question}
        if not self._piece_servie:
            self._piece_servie = True
            return {"piece": {"chemin": "AGENTS.md", "empreinte": "emp-1", "rang": 1, "total": 1}}
        return {}

    def _repondre(self, corps: Mapping[str, Any]) -> Reponse:
        self.reponses_outillage.append(dict(corps))
        self._question = None
        return self._paire(str(corps.get("valeur")), {"contenu": "Compris.", **self._suite()})

    def _trancher(self, corps: Mapping[str, Any]) -> Reponse:
        racine = Path(str(self.projets["prj-1"]["racine"]))
        (racine / "AGENTS.md").write_text("# Instructions\n", encoding="utf-8")
        fait = {"chemin": "AGENTS.md", "etat": "ecrit", "ecrite": True}
        return self._paire("Écris-la.", {"contenu": "C'est écrit.", "piece_ecrite": fait})

    def _recruter(self, corps: Mapping[str, Any]) -> Reponse:
        self.equipes["prj-1"] = len(corps.get("roles") or [])
        return self._paire(
            "équipe validée", {"contenu": "Je reprends.", "proposition": "la demande"}
        )

    def _cadrer(self, corps: Mapping[str, Any]) -> Reponse:
        run = RunDouble(f"run-{len(self.runs) + 1}", str(corps["projet_id"]), list(self._statuts))
        self.runs.append(run)
        if EXECUTION_EN_ATTENTE_ARBITRAGE in run.statuts:
            self.validations.append(
                {
                    "tache_id": "t1",
                    "run_id": run.run_id,
                    "statut": VALIDATION_EN_ATTENTE,
                    "titre": "Écrire dans le projet",
                    "outil": "Bash",
                    "arguments": {"command": "npm install"},
                    "horodatage": "h1",
                }
            )
        if self._question_agent:
            self.questions.append(
                {
                    "question_id": "q1",
                    "run_id": run.run_id,
                    "statut": QUESTION_EN_ATTENTE,
                    "agent": "dev",
                    "tache_id": "t1",
                    "question": self._question_agent,
                }
            )
        return self._paire("oui", {"contenu": f"Run {run.run_id} ouvert.", "run_id": run.run_id})

    def _declarer(self, corps: Mapping[str, Any]) -> Reponse:
        identifiant = f"prj-{len(self.projets) + 1}"
        fiche = {"id": identifiant, "nom": corps["nom"], "racine": corps["racine"]}
        self.projets[identifiant] = fiche
        return Reponse(statut=201, corps=fiche)

    def _execution(self, run_id: str) -> Reponse:
        run = next(r for r in self.runs if r.run_id == run_id)
        return Reponse(statut=200, corps=run.detail())

    def _decider(self, tache_id: str, approuve: bool) -> Reponse:
        self.decisions.append((tache_id, approuve))
        for demande in self.validations:
            if demande["tache_id"] == tache_id:
                demande["statut"] = VALIDATION_APPROUVEE if approuve else VALIDATION_REFUSEE
        return Reponse(statut=200, corps={})

    def _repondre_a_l_agent(self, question_id: str, reponse: str) -> Reponse:
        self.reponses_agents.append(reponse)
        for question in self.questions:
            if question["question_id"] == question_id:
                question["statut"] = QUESTION_REPONDUE
                question["reponse"] = reponse
        return Reponse(statut=200, corps={})

    def _couts(self, params: Mapping[str, str]) -> Reponse:
        total = sum(r.cout_usd for r in self.runs if r.projet_id == params.get("projet"))
        return Reponse(
            statut=200,
            corps={
                "total": {"cout_usd": total + 0.05},
                "fil": {"usage": {"cout_usd": 0.05}},
            },
        )


def _role() -> dict[str, Any]:
    return {
        "nom": "dev",
        "role": "Développeur",
        "competences": ["python"],
        "playbook": "# playbook\n",
        "instances": 1,
        "gabarit": "developpeur",
        "skills": [],
        "politique": {},
    }


class Horloge:
    """Une horloge que le sommeil fait avancer — aucune attente réelle."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def dormir(self, secondes: float) -> None:
        self.t += secondes


class RedisFactice:
    """Ce que `etat.sauver` lit de Redis : le journal de l'espace du banc."""

    def lrange(self, _nom: str, _debut: int, _fin: int) -> list[str]:
        return ['{"type": "execution.statut"}']


@dataclass
class Poste:
    """Un poste jetable : le dossier des exercices, l'atelier, la stack doublée."""

    racine: Path
    atelier: Path
    api: ApiDouble
    horloge: Horloge = field(default_factory=Horloge)
    sortie: io.StringIO = field(default_factory=io.StringIO)
    erreur: io.StringIO = field(default_factory=io.StringIO)

    def jouer(self, *argv: str, **reste: Any) -> int:
        return ex.main(
            list(argv),
            client=ClientAPI(self.api),
            juge=JugeModele(),
            racine=self.racine,
            atelier=self.atelier,
            horloge=self.horloge,
            dormir=self.horloge.dormir,
            sortie=self.sortie,
            erreur=self.erreur,
            **reste,
        )

    def ecrire(self, nom: str, texte: str) -> str:
        chemin = self.racine.parent / nom
        chemin.write_text(texte, encoding="utf-8")
        return str(chemin)

    @property
    def dossier(self) -> Path:
        (dossier,) = [d for d in self.racine.iterdir() if d.is_dir()]
        return dossier

    def etat(self) -> dict[str, Any]:
        donnees: dict[str, Any] = json.loads(
            (self.dossier / ex.FICHIER_ETAT).read_text(encoding="utf-8")
        )
        return donnees


def _poste(tmp_path: Path, **api: Any) -> Poste:
    return Poste(tmp_path / "exercices", tmp_path / "ateliers", ApiDouble(**api))


def _ouvrir(poste: Poste, *, personne: str = "") -> None:
    args = ["ouvrir", "--oracle", poste.ecrire("oracle.md", "Le run se solde « terminee ».")]
    if personne:
        args += ["--personne", poste.ecrire("personne.md", personne)]
    assert poste.jouer(*args) == ex.CODE_FAIT, poste.erreur.getvalue()


# --- C1 : un exercice composé de verbes, et son relevé ------------------------


def test_un_exercice_compose_de_verbes_laisse_un_releve_complet(tmp_path: Path) -> None:
    poste = _poste(tmp_path)
    _ouvrir(poste, personne="Je veux un carnet de chants.\nJe ne sais pas programmer.")
    assert poste.jouer("naitre", poste.ecrire("demande.md", "Un carnet de chants.")) == 0
    assert poste.jouer("dire", poste.ecrire("travail.md", "Ajoute trois chants.")) == 0
    assert poste.jouer("suivre") == 0
    assert (
        poste.jouer("conclure", "vert", poste.ecrire("motif.md", "Run soldé, AGENTS.md écrit."))
        == 0
    )

    dossier = poste.dossier
    for nom in (
        ex.FICHIER_ORACLE,
        ex.FICHIER_PERSONNE,
        ex.FICHIER_PROMPT,
        ex.FICHIER_DEROULE,
        ex.FICHIER_FAITS,
        ex.FICHIER_FAITS_LISIBLES,
        ex.FICHIER_VERDICT,
    ):
        assert (dossier / nom).is_file(), nom
    prompt = (dossier / ex.FICHIER_PROMPT).read_text(encoding="utf-8")
    assert "Un carnet de chants." in prompt and "Ajoute trois chants." in prompt
    faits = json.loads((dossier / ex.FICHIER_FAITS).read_text(encoding="utf-8"))
    assert faits["run_ids"] == ["run-1"]
    assert faits["runs"][0]["statut"] == EXECUTION_TERMINEE
    # Le coût est celui du grand livre du projet — fil compris —, pas celui du run seul.
    assert faits["cout"] == {
        "usd": pytest.approx(0.30),
        "source": "grand livre du projet (Coûts & analytics)",
        "postes": {"fil": 0.05},
    }
    assert "AGENTS.md" in [fichier["chemin"] for fichier in faits["fichiers"]]
    verdict = (dossier / ex.FICHIER_VERDICT).read_text(encoding="utf-8")
    assert "Le run se solde « terminee »." in verdict and "vert" in verdict
    etat_ex = poste.etat()
    assert etat_ex["runs"] == ["run-1"] and etat_ex["conclusion"]["verdict"] == "vert"
    assert [g["verbe"] for g in etat_ex["gestes"]] == [
        "ouvrir",
        "naitre",
        "dire",
        "suivre",
        "conclure",
    ]


def test_le_projet_nait_dans_l_atelier_de_l_exercice(tmp_path: Path) -> None:
    """Le fil propose ailleurs ; la personne le range dans l'atelier, et seulement là."""
    poste = _poste(tmp_path)
    _ouvrir(poste)
    assert poste.jouer("naitre", poste.ecrire("demande.md", "Un carnet."), "--nom", "carnet") == 0
    racine = Path(poste.etat()["racine"])
    assert racine.parent == Path(poste.etat()["atelier"]) and racine.name == "carnet"
    assert (racine / "AGENTS.md").is_file()


def test_semer_copie_le_projet_dans_l_atelier_et_le_declare(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "node_modules").mkdir(parents=True)
    (source / "app.py").write_text("print('ok')\n", encoding="utf-8")
    poste = _poste(tmp_path)
    _ouvrir(poste)
    assert poste.jouer("semer", str(source), "--equipe", "--versionner") == 0
    racine = Path(poste.etat()["racine"])
    assert (racine / "app.py").is_file() and not (racine / "node_modules").exists()
    assert poste.api.projets["prj-1"]["vcs"] == "git" and poste.api.equipes["prj-1"] == 1
    # Un second projet est un autre exercice.
    assert poste.jouer("semer", str(source)) == ex.CODE_MANQUE


# --- L'oracle s'écrit avant de jouer ------------------------------------------


def test_ouvrir_refuse_un_exercice_sans_oracle(tmp_path: Path) -> None:
    poste = _poste(tmp_path)
    assert poste.jouer("ouvrir", "--oracle", poste.ecrire("vide.md", "  \n")) == ex.CODE_USAGE
    assert poste.jouer("ouvrir") == ex.CODE_USAGE
    assert not poste.racine.exists()


def test_un_oracle_retouche_apres_l_ouverture_fait_refuser_chaque_verbe(tmp_path: Path) -> None:
    poste = _poste(tmp_path)
    _ouvrir(poste)
    (poste.dossier / ex.FICHIER_ORACLE).write_text("Ce que le produit a fait.\n", encoding="utf-8")
    assert poste.jouer("naitre", poste.ecrire("demande.md", "Un carnet.")) == ex.CODE_REFUS
    assert poste.jouer("conclure", "vert", poste.ecrire("motif.md", "…")) == ex.CODE_REFUS
    assert "oracle" in poste.erreur.getvalue()
    assert not (poste.dossier / ex.FICHIER_VERDICT).exists()


# --- La personne ----------------------------------------------------------------

QUESTION = {
    "cle": "pile",
    "intitule": "Avec quoi ?",
    "options": [{"valeur": "texte"}, {"valeur": "latex"}],
    "recommande": "texte",
}


def test_la_personne_repond_par_sa_feuille_entiere(tmp_path: Path) -> None:
    feuille = "Des fichiers texte, c'est très bien.\nJe veux pouvoir l'imprimer."
    poste = _poste(tmp_path, question=QUESTION, question_agent="Quel format de page ?")
    _ouvrir(poste, personne=feuille)
    assert poste.jouer("naitre", poste.ecrire("demande.md", "Un carnet.")) == 0
    # La feuille entière, avec ses mots — jamais la ligne qui semblerait convenir.
    assert poste.api.reponses_outillage == [
        {"valeur": feuille, "libre": True, "conversation": "conv-1"}
    ]
    assert poste.jouer("dire", poste.ecrire("travail.md", "Ajoute un chant.")) == 0
    assert poste.jouer("repondre") == 0
    assert poste.api.reponses_agents == [feuille]


def test_sans_feuille_la_personne_retient_la_recommandation_et_ne_repond_pas_aux_agents(
    tmp_path: Path,
) -> None:
    poste = _poste(tmp_path, question=QUESTION, question_agent="Quel format de page ?")
    _ouvrir(poste)
    assert poste.jouer("naitre", poste.ecrire("demande.md", "Un carnet.")) == 0
    assert poste.api.reponses_outillage[0]["valeur"] == "texte"
    assert poste.jouer("dire", poste.ecrire("travail.md", "Ajoute un chant.")) == 0
    assert poste.jouer("repondre") == 0
    assert poste.api.reponses_agents == []
    assert "laissée sans réponse" in (poste.dossier / ex.FICHIER_DEROULE).read_text(
        encoding="utf-8"
    )


def test_la_proposition_d_outillage_entiere_est_retenue_sans_feuille(tmp_path: Path) -> None:
    poste = _poste(tmp_path, question={"cle": "tout", "proposition": True})
    _ouvrir(poste)
    assert poste.jouer("naitre", poste.ecrire("demande.md", "Un carnet.")) == 0
    assert poste.api.reponses_outillage[0]["valeur"] == PROPOSITION_RETENUE


# --- Arbitrer et suivre -------------------------------------------------------

EN_ATTENTE = (EXECUTION_EN_ATTENTE_ARBITRAGE, EXECUTION_EN_ATTENTE_ARBITRAGE, EXECUTION_TERMINEE)


def _jusqu_au_run(poste: Poste, *, personne: str = "") -> None:
    _ouvrir(poste, personne=personne)
    assert poste.jouer("naitre", poste.ecrire("demande.md", "Un carnet.")) == 0
    assert poste.jouer("dire", poste.ecrire("travail.md", "Ajoute un chant.")) == 0


def test_par_defaut_la_personne_approuve_ce_que_son_run_demande(tmp_path: Path) -> None:
    poste = _poste(tmp_path, statuts=EN_ATTENTE)
    _jusqu_au_run(poste)
    assert poste.jouer("suivre") == 0
    assert poste.api.decisions == [("t1", True)]


def test_arbitrer_refuser_tranche_ce_qui_attend_dans_ce_sens(tmp_path: Path) -> None:
    poste = _poste(tmp_path, statuts=EN_ATTENTE)
    _jusqu_au_run(poste)
    assert poste.jouer("arbitrer", "refuser") == 0
    assert poste.api.decisions == [("t1", False)]
    assert poste.etat()["consigne"] == "refuser"


def test_laisser_attendre_ne_tranche_rien_et_rend_la_main_a_la_demande(tmp_path: Path) -> None:
    poste = _poste(tmp_path, statuts=EN_ATTENTE)
    _jusqu_au_run(poste)
    assert poste.jouer("arbitrer", "attendre") == 0
    assert poste.jouer("suivre", "--jusqu-a", "demande") == 0
    assert poste.api.decisions == []
    assert "en_attente_arbitrage" in poste.sortie.getvalue()


def test_suivre_rend_6_quand_le_delai_passe_avant_l_issue(tmp_path: Path) -> None:
    poste = _poste(tmp_path, statuts=(EXECUTION_EN_COURS,))
    _jusqu_au_run(poste)
    assert poste.jouer("suivre", "--delai", "30") == ex.CODE_EN_VOL
    assert poste.horloge.t >= 30
    assert "rejouer `suivre`" in poste.sortie.getvalue()


def test_suivre_rend_la_main_quand_un_agent_attend_la_personne(tmp_path: Path) -> None:
    poste = _poste(tmp_path, statuts=(EXECUTION_EN_COURS,), question_agent="Quel format de page ?")
    _jusqu_au_run(poste, personne="A4, s'il vous plaît.")
    assert poste.jouer("suivre", "--jusqu-a", "question") == 0
    # Au moment nommé, la question attend encore : c'est à la session de la voir.
    assert poste.api.reponses_agents == []


def test_suivre_jusqu_a_un_statut_nomme(tmp_path: Path) -> None:
    poste = _poste(tmp_path, statuts=(EXECUTION_EN_COURS, "suspendu", EXECUTION_TERMINEE))
    _jusqu_au_run(poste)
    assert poste.jouer("suivre", "--jusqu-a", "statut=suspendu") == 0
    assert "statut « suspendu »" in poste.sortie.getvalue()
    assert poste.jouer("suivre", "--jusqu-a", "plus-tard") == ex.CODE_USAGE


def test_le_delai_par_defaut_tient_sous_le_plafond_d_un_appel_bash() -> None:
    assert ex.DELAI_SUIVI_S < 600


# --- C2 : l'état sauvé, que start.sh --etat-banc rouvre --------------------------


def _banc(tmp_path: Path) -> Donnees:
    base = tmp_path / "banc"
    return Donnees(
        espace=Espace(ESPACE_DU_BANC, ORIGINE_BANC),
        racines={depot.nom: base / depot.nom for depot in DEPOTS},
    )


def test_sauver_range_l_etat_que_start_sh_rouvre(tmp_path: Path) -> None:
    poste = _poste(tmp_path)
    _jusqu_au_run(poste)
    assert poste.jouer("suivre") == 0
    code = poste.jouer("sauver", client_redis=RedisFactice(), donnees_banc=_banc(tmp_path))
    assert code == ex.CODE_FAIT, poste.erreur.getvalue()
    instantane = etat.dernier(poste.atelier)
    assert instantane is not None
    assert instantane.passage == poste.etat()["identifiant"]
    assert instantane.scenarios == (("exercice", "non conclu"),)
    assert etat.GESTE_ROUVRIR in poste.sortie.getvalue()


def test_sauver_refuse_une_api_qui_ne_sert_pas_le_banc_de_la_copie(tmp_path: Path) -> None:
    poste = _poste(tmp_path, espace="commun")
    _jusqu_au_run(poste)
    code = poste.jouer("sauver", client_redis=RedisFactice(), donnees_banc=_banc(tmp_path))
    assert code == ex.CODE_REFUS
    assert etat.dernier(poste.atelier) is None
    assert ex.GESTE_STACK in poste.erreur.getvalue()


# --- Les refus ----------------------------------------------------------------


def test_une_api_muette_rend_3(tmp_path: Path) -> None:
    poste = _poste(tmp_path, sante=False)
    assert poste.jouer("ouvrir", "--oracle", poste.ecrire("o.md", "…")) == ex.CODE_API_MUETTE


def test_sans_exercice_ouvert_un_verbe_rend_4(tmp_path: Path) -> None:
    assert _poste(tmp_path).jouer("relever") == ex.CODE_MANQUE


def test_dire_sans_projet_rend_4(tmp_path: Path) -> None:
    poste = _poste(tmp_path)
    _ouvrir(poste)
    assert poste.jouer("dire", poste.ecrire("m.md", "Bonjour.")) == ex.CODE_MANQUE


def test_un_refus_de_l_api_rend_1_et_se_consigne(tmp_path: Path) -> None:
    poste = _poste(tmp_path, refuse=f"{FIL}/cadrage")
    _ouvrir(poste)
    assert poste.jouer("naitre", poste.ecrire("demande.md", "Un carnet.")) == 0
    assert poste.jouer("dire", poste.ecrire("travail.md", "Ajoute un chant.")) == ex.CODE_GESTE
    deroule = (poste.dossier / ex.FICHIER_DEROULE).read_text(encoding="utf-8")
    assert "refusé par l'API" in deroule and "code 1" in deroule
    dernier = poste.etat()["gestes"][-1]
    assert (dernier["verbe"], dernier["code"]) == ("dire", ex.CODE_GESTE)


def test_l_exercice_vise_est_le_dernier_ouvert_ou_celui_qu_on_nomme(tmp_path: Path) -> None:
    poste = _poste(tmp_path)
    _ouvrir(poste)
    premier = poste.etat()["identifiant"]
    _ouvrir(poste)
    dossiers = sorted(d.name for d in poste.racine.iterdir())
    assert dossiers[0] == premier and len(dossiers) == 2
    assert poste.jouer("arbitrer", "refuser", "--exercice", premier) == 0
    lu = json.loads((poste.racine / premier / ex.FICHIER_ETAT).read_text(encoding="utf-8"))
    assert lu["consigne"] == "refuser"
    dernier = json.loads((poste.racine / dossiers[1] / ex.FICHIER_ETAT).read_text(encoding="utf-8"))
    assert dernier["consigne"] == "approuver"


_DOC_DU_WORKFLOW = Path(__file__).resolve().parents[1] / "docs" / "10-workflow-git.md"


def test_docs_10_donne_la_commande_de_chaque_verbe_et_d_aucun_autre() -> None:
    """docs/10 §6.2 suit le CLI : une ligne de commande par verbe, aucun verbe qui n'existe pas.

    `ouvrir` n'est pas dans `VERBES` : il crée l'exercice que les autres reprennent.
    """
    doc = _DOC_DU_WORKFLOW.read_text(encoding="utf-8")
    documentes = set(re.findall(r"-m maestro\.scenarios\.exercice (\w+)", doc))
    assert documentes == {"ouvrir", *ex.VERBES}

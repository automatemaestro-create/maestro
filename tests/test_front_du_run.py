"""Les tâches indépendantes d'un run tournent de front (#1299).

Le retour d'expérience du 2026-09-24 (projet `p3`) : *« je n'ai jamais remarqué un
parallélisme dans le traitement des tâches jusqu'ici »*. Le moteur savait lancer
tout ce qui n'attend personne, mais un agent ne prenait qu'**une** tâche à la fois
(`INSTANCES_DEFAUT = 1`, un défaut arbitraire au sens de docs/41) : quatre tâches
indépendantes du même rôle passaient en file.

Ce que ces tests gardent, sur le moteur entier et un modèle doublé :

① sur un projet **versionné**, des tâches indépendantes confiées au **même agent**
   tournent de front quand aucun réglage ne l'interdit. Le chevauchement est prouvé
   par une **barrière** — les trois tâches doivent être au travail en même temps
   pour qu'une seule rende —, jamais par un `sleep` ;
② le plafond se **dérive de la largeur du plan**, borné par un plafond global
   (`PLAFOND_INSTANCES_DERIVEES`) : un plan plus large que lui n'ouvre pas plus
   d'instances ;
③ un plan **linéaire** ne change rien, et un **réglage explicite** de la personne
   (`instances_max = 1`) l'emporte toujours ;
④ le garde-fou d'un projet **non versionné** (#839) ne bouge pas ;
⑤ le plafond dérivé et son **origine** s'annoncent dans le journal du run, avant la
   première tâche.

Vrai dépôt jetable (sautés sans `git`) : c'est ce qui fait d'un projet un projet
versionné, et chaque tâche y travaille dans son worktree.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from maestro.agents import DEVELOPER_PROFILE, AgentRuntime
from maestro.agents.capacity import (
    INSTANCES_DEFAUT,
    PLAFOND_INSTANCES_DERIVEES,
    STATUT_INSTANCES_DERIVEES,
    CapaciteAgent,
    CapacityStore,
    InstancesDerivees,
)
from maestro.engine import OrchestrationEngine
from maestro.orchestrator import Orchestrator
from maestro.plan_run import NoeudPlan, largeur_du_plan
from maestro.projets.modele import Projet
from maestro.projets.store import ProjetStore
from maestro.providers.base import ModelProvider
from maestro.telemetry import ETAPE_EQUIPE, RunJournal

GIT = shutil.which("git")

#: Ce qu'on laisse à la barrière avant de conclure qu'elle ne se lèvera pas. Ce n'est
#: **pas** une mesure : une barrière franchie prouve le chevauchement quel que soit ce
#: délai, et le délai ne sert qu'à muer une régression en échec net plutôt qu'en suite
#: suspendue.
FILET_S = 20.0

#: Ce qu'une tâche attend, seule, un éventuel compagnon avant de rendre — dans les
#: tests où l'on prouve au contraire qu'**aucune** autre ne la rejoint.
ATTENTE_SEULE_S = 0.5


@pytest.fixture(autouse=True)
def _maison_isolee(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """`Path.home()` sous `tmp_path` (#221, #224) : sous Windows, `tmp_path` vit sous `AppData`."""
    maison = tmp_path / "maison"
    maison.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: maison))
    return maison


@pytest.fixture(autouse=True)
def _sans_identite_git_ambiante(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Coupe l'identité Git du poste (#333) : le moteur passe par le `-c` du code."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig-absent"))


# --------------------------------------------------------------------------- #
# Fabriques
# --------------------------------------------------------------------------- #


def _tache(identifiant: str, dependances: list[str] | None = None) -> dict[str, Any]:
    """Une tâche du plan que le routage confie au développeur (`backend`)."""
    return {
        "id": identifiant,
        "titre": f"Maquetter la section {identifiant}",
        "description": f"Maquetter la section {identifiant} du site.",
        "competences_requises": ["backend"],
        "format_sortie": "Une page HTML",
        "dependances": dependances or [],
    }


def _independantes(n: int) -> list[dict[str, Any]]:
    """`n` sections sans dépendance entre elles — le plan que #1299 veut voir tourner de front."""
    return [_tache(f"section-{i}") for i in range(1, n + 1)]


def _chaine(n: int) -> list[dict[str, Any]]:
    """`n` étapes dont chacune attend la précédente — un plan linéaire."""
    return [
        _tache(f"etape-{i}", [f"etape-{i - 1}"] if i > 1 else None) for i in range(1, n + 1)
    ]


class _Planificateur(ModelProvider):
    """Le Chef de projet doublé : rend toujours le même plan."""

    name = "planificateur"

    def __init__(self, plan: list[dict[str, Any]]) -> None:
        self._plan = json.dumps(plan, ensure_ascii=False)

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        return self._plan


class _Barriere(ModelProvider):
    """Fournisseur outillé : une tâche ne rend qu'une fois `parties` tâches au travail ensemble.

    En exécution sérialisée, la première attendrait seule à la barrière : le filet
    (`FILET_S`) la fait échouer, et le test le dit, au lieu de pendre. `pic` relève le
    plus grand nombre de tâches au travail en même temps — c'est lui qui prouve qu'un
    plafond n'a jamais été dépassé.
    """

    name = "barriere"

    def __init__(self, parties: int) -> None:
        self._parties = parties
        self._en_vol = 0
        self._complet = asyncio.Event()
        self.pic = 0

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):  # pragma: no cover
        raise AssertionError("un rôle outillé passe par run_agent")

    async def run_agent(self, prompt, *, model, system_prompt=None, workspace, tools, **canaux):
        self._en_vol += 1
        self.pic = max(self.pic, self._en_vol)
        try:
            if self._en_vol >= self._parties:
                self._complet.set()
            try:
                await asyncio.wait_for(self._complet.wait(), timeout=FILET_S)
            except TimeoutError:
                raise RuntimeError(
                    f"barrière jamais levée : au plus {self.pic} tâche(s) au travail "
                    f"ensemble, {self._parties} attendues"
                ) from None
        finally:
            self._en_vol -= 1
        return "Section maquettée."


class _Seule(ModelProvider):
    """Fournisseur outillé : chaque tâche attend un compagnon, borné — et relève s'il en vient un.

    Sert à prouver l'inverse de `_Barriere` : que **rien** ne rejoint une tâche. Si le
    plafond laissait passer une seconde tâche du même agent, elle arriverait pendant
    l'attente de la première et `pic` monterait à 2.
    """

    name = "seule"

    def __init__(self) -> None:
        self._en_vol = 0
        self._compagnon = asyncio.Event()
        self.pic = 0

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):  # pragma: no cover
        raise AssertionError("un rôle outillé passe par run_agent")

    async def run_agent(self, prompt, *, model, system_prompt=None, workspace, tools, **canaux):
        self._en_vol += 1
        self.pic = max(self.pic, self._en_vol)
        try:
            if self._en_vol >= 2:
                self._compagnon.set()
            try:
                await asyncio.wait_for(self._compagnon.wait(), timeout=ATTENTE_SEULE_S)
            except TimeoutError:
                pass
        finally:
            self._en_vol -= 1
        return "Étape faite."


def _projet(tmp_path: Path, *, versionne: bool = True) -> tuple[ProjetStore, Projet]:
    """Un dépôt de projets et un projet déclaré — versionné par le verbe du produit (#704)."""
    depot = ProjetStore(tmp_path / "depot")
    racine = tmp_path / "projets" / "vitrine"
    projet = depot.creer("Vitrine", racine, origine="nouveau")
    (racine / "README.md").write_text("# Site vitrine\n", encoding="utf-8")
    if versionne:
        projet = depot.versionner(projet.id)
        assert projet.versionne, projet
    return depot, projet


def _moteur(
    fournisseur: ModelProvider,
    plan: list[dict[str, Any]],
    depot: ProjetStore,
    capacites: CapacityStore,
    *,
    max_parallele: int | None = None,
) -> OrchestrationEngine:
    """Le moteur entier : plan doublé, développeur outillé doublé, dépôts réels."""
    return OrchestrationEngine(
        fournisseur,
        Orchestrator(_Planificateur(plan), model="m"),
        runtimes={DEVELOPER_PROFILE.nom: AgentRuntime(fournisseur, DEVELOPER_PROFILE)},
        capacites=capacites,
        projets=depot,
        max_parallele=max_parallele,
    )


def _annonces(journal: RunJournal) -> list[Any]:
    """Les lignes du journal qui annoncent le plafond dérivé du run."""
    return [
        r
        for r in journal.records
        if r.etape == ETAPE_EQUIPE and r.statut == STATUT_INSTANCES_DERIVEES
    ]


# --------------------------------------------------------------------------- #
# ① Sur un projet versionné, le même agent prend plusieurs tâches de front
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(GIT is None, reason="git introuvable")
def test_des_taches_independantes_du_meme_agent_tournent_de_front(tmp_path: Path) -> None:
    """Le critère, sur le moteur entier : trois sections, un seul agent, aucun réglage.

    La barrière ne se lève que si les trois tâches sont au travail **en même temps** :
    avant #1299, la deuxième attendait que la première ait rendu, et la première
    attendait seule jusqu'au filet.
    """
    depot, projet = _projet(tmp_path)
    fournisseur = _Barriere(parties=3)
    journal = RunJournal()

    rapport = asyncio.run(
        _moteur(fournisseur, _independantes(3), depot, CapacityStore(tmp_path / "capacite")).run(
            "Maquetter les trois sections du site", journal=journal, projet_id=projet.id
        )
    )

    assert all(r.ok for r in rapport.resultats), [(r.task_id, r.erreur) for r in rapport.resultats]
    assert {r.agent for r in rapport.resultats} == {DEVELOPER_PROFILE.nom}
    assert fournisseur.pic == 3


# --------------------------------------------------------------------------- #
# ② Le plafond se dérive de la largeur, borné par le plafond global
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(GIT is None, reason="git introuvable")
def test_un_plan_plus_large_que_le_plafond_global_n_ouvre_pas_plus_d_instances(
    tmp_path: Path,
) -> None:
    """Cinq sections, un plafond global de trois : trois au travail ensemble, jamais quatre.

    ≥ : la barrière de trois se lève — le parallélisme est réel. ≤ : une fois levée,
    elle laisse passer les deux dernières sans attendre, donc seul le plafond peut
    les retenir ; `pic` le constate.
    """
    largeur = PLAFOND_INSTANCES_DERIVEES + 2
    depot, projet = _projet(tmp_path)
    fournisseur = _Barriere(parties=PLAFOND_INSTANCES_DERIVEES)

    rapport = asyncio.run(
        _moteur(
            fournisseur, _independantes(largeur), depot, CapacityStore(tmp_path / "capacite")
        ).run("Maquetter les sections du site", projet_id=projet.id)
    )

    assert all(r.ok for r in rapport.resultats), [(r.task_id, r.erreur) for r in rapport.resultats]
    assert fournisseur.pic == PLAFOND_INSTANCES_DERIVEES


# --------------------------------------------------------------------------- #
# ③ Un plan linéaire ne change rien ; un réglage explicite l'emporte
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(GIT is None, reason="git introuvable")
def test_un_plan_lineaire_ne_change_rien(tmp_path: Path) -> None:
    """Chaque étape attend la précédente : une seule au travail à la fois, comme avant."""
    depot, projet = _projet(tmp_path)
    fournisseur = _Seule()
    journal = RunJournal()

    rapport = asyncio.run(
        _moteur(fournisseur, _chaine(3), depot, CapacityStore(tmp_path / "capacite")).run(
            "Trois étapes enchaînées", journal=journal, projet_id=projet.id
        )
    )

    assert all(r.ok for r in rapport.resultats)
    assert fournisseur.pic == 1
    # Le régime s'annonce aussi quand il ne libère rien (#286 : dans les deux sens).
    (annonce,) = _annonces(journal)
    assert annonce.sortie.startswith("Une tâche à la fois par agent")
    assert "largeur du plan : 1" in annonce.entree


@pytest.mark.skipif(GIT is None, reason="git introuvable")
def test_un_reglage_explicite_a_une_instance_est_respecte(tmp_path: Path) -> None:
    """`instances_max = 1` posé par la personne : trois sections indépendantes, une à la fois.

    La preuve est double. `pic` reste à 1 alors que chaque tâche garde la main assez
    longtemps pour qu'une seconde la rejoigne si le plafond le permettait ; et les
    tâches suivantes ont réellement **attendu leur créneau** — elles étaient prêtes,
    c'est le réglage qui les a retenues.
    """
    depot, projet = _projet(tmp_path)
    capacites = CapacityStore(tmp_path / "capacite")
    capacites.pour_projet(projet.id).ecrire(
        CapaciteAgent(nom=DEVELOPER_PROFILE.nom, instances=1, instances_fixees=True)
    )
    fournisseur = _Seule()
    journal = RunJournal()

    rapport = asyncio.run(
        _moteur(fournisseur, _independantes(3), depot, capacites).run(
            "Maquetter les trois sections du site", journal=journal, projet_id=projet.id
        )
    )

    assert all(r.ok for r in rapport.resultats)
    assert fournisseur.pic == 1
    attentes = [r.usage.duree_attente_creneau_ms or 0 for r in rapport.resultats]
    assert sum(1 for attente in attentes if attente > 0) == 2, attentes
    # L'annonce dit que ce réglage-là est gardé : c'est lui qui explique la file.
    (annonce,) = _annonces(journal)
    assert DEVELOPER_PROFILE.nom in annonce.sortie


# --------------------------------------------------------------------------- #
# ④ Le garde-fou du projet non versionné ne bouge pas
# --------------------------------------------------------------------------- #


def test_un_projet_non_versionne_garde_une_tache_a_la_fois(tmp_path: Path) -> None:
    """#839 : sans versions, deux agents écriraient dans le même dossier — une à la fois.

    Rien n'est annoncé ici : le plafond dérivé ne s'applique pas à ce projet, et dire
    pourquoi ses tâches passent une à une est l'affaire de la cadence du run (#1298).
    """
    depot, projet = _projet(tmp_path, versionne=False)
    fournisseur = _Seule()
    journal = RunJournal()

    rapport = asyncio.run(
        _moteur(fournisseur, _independantes(3), depot, CapacityStore(tmp_path / "capacite")).run(
            "Maquetter les trois sections du site", journal=journal, projet_id=projet.id
        )
    )

    assert all(r.ok for r in rapport.resultats)
    assert fournisseur.pic == 1
    assert _annonces(journal) == []


# --------------------------------------------------------------------------- #
# ⑤ Le plafond dérivé et son origine s'annoncent avant la première tâche
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(GIT is None, reason="git introuvable")
def test_le_plafond_derive_et_son_origine_s_annoncent_avant_la_premiere_tache(
    tmp_path: Path,
) -> None:
    """Une ligne de run, au nom de l'orchestrateur, usage nul, avant tout `:debut`."""
    depot, projet = _projet(tmp_path)
    journal = RunJournal()

    asyncio.run(
        _moteur(_Barriere(parties=3), _independantes(3), depot, CapacityStore(tmp_path / "c")).run(
            "Maquetter les trois sections du site", journal=journal, projet_id=projet.id
        )
    )

    (annonce,) = _annonces(journal)
    assert annonce.agent == "orchestrateur"
    assert annonce.projet_id == projet.id
    assert annonce.usage.appels == 0
    # Le chiffre et son origine : la largeur du plan.
    assert "3" in annonce.sortie
    assert "largeur" in annonce.entree
    etapes = [r.etape for r in journal.records]
    premiere_tache = next(i for i, e in enumerate(etapes) if e.endswith(":debut"))
    assert etapes.index(ETAPE_EQUIPE) < premiere_tache


def test_le_run_borne_se_dit_dans_l_annonce() -> None:
    """Le plafond du run (`parallelisme`, #100) borne tous les agents : l'annonce le dit.

    Et seulement quand il est posé : un run sans borne n'a rien à en dire de plus.
    """
    derivees = InstancesDerivees(largeur=4)

    assert "2 tâches à la fois, tous agents confondus" in derivees.phrase(parallelisme=2)
    assert "tous agents confondus" not in derivees.phrase()


# --------------------------------------------------------------------------- #
# Le plafond dérivé, pièce par pièce
# --------------------------------------------------------------------------- #


def test_la_largeur_est_le_niveau_le_plus_peuple() -> None:
    """La même mesure que « jusqu'à N de front » à l'écran (`maestro.controltower.graphe`)."""
    losange = [
        NoeudPlan(id="a"),
        NoeudPlan(id="b", dependances=("a",)),
        NoeudPlan(id="c", dependances=("a",)),
        NoeudPlan(id="d", dependances=("b", "c")),
    ]

    assert largeur_du_plan(losange) == 2
    assert largeur_du_plan([NoeudPlan(id=f"s{i}") for i in range(4)]) == 4
    assert largeur_du_plan([]) == 0


def test_le_plafond_derive_suit_la_largeur_sous_le_plafond_global() -> None:
    """La largeur tant qu'elle tient sous le plafond global, lui au-delà — jamais moins d'un."""
    assert InstancesDerivees(largeur=2).instances == 2
    assert InstancesDerivees(largeur=2).origine == "largeur_du_plan"
    au_dela = InstancesDerivees(largeur=PLAFOND_INSTANCES_DERIVEES + 4)
    assert au_dela.instances == PLAFOND_INSTANCES_DERIVEES
    assert au_dela.origine == "plafond_global"
    assert InstancesDerivees(largeur=0).instances == INSTANCES_DEFAUT


def test_la_phrase_dit_le_chiffre_et_d_ou_il_vient() -> None:
    """Chaque origine a ses mots, et un réglage gardé est nommé."""
    large = InstancesDerivees(largeur=PLAFOND_INSTANCES_DERIVEES + 2).phrase()
    assert f"{PLAFOND_INSTANCES_DERIVEES} tâches de front" in large
    assert f"{PLAFOND_INSTANCES_DERIVEES + 2} de front" in large
    assert "plafond global" in large

    juste = InstancesDerivees(largeur=2).phrase()
    assert "2 tâches de front" in juste
    assert "largeur du plan" in juste

    garde = InstancesDerivees(
        largeur=3, reglees=(CapaciteAgent(nom="dev", instances=1, instances_fixees=True),)
    ).phrase()
    assert "« dev »" in garde and "1 instance" in garde


# --------------------------------------------------------------------------- #
# Ce qui compte comme un réglage explicite
# --------------------------------------------------------------------------- #


def test_une_capacite_par_defaut_ne_fixe_pas_ses_instances(tmp_path: Path) -> None:
    """Un agent jamais réglé n'a rien fixé : son plafond est celui que le run dérive."""
    assert CapacityStore(tmp_path).lire("dev").fixe_ses_instances is False


def test_des_instances_posees_par_la_personne_sont_fixees_et_le_restent(tmp_path: Path) -> None:
    """Le drapeau voyage dans le fichier : relu par un autre process, il tient."""
    depot = CapacityStore(tmp_path)
    depot.ecrire(CapaciteAgent(nom="dev", instances=1, instances_fixees=True))

    relu = CapacityStore(tmp_path).lire("dev")
    assert relu.fixe_ses_instances is True
    assert relu.instances == 1


def test_une_capacite_ecrite_sans_le_dire_se_juge_sur_sa_valeur(tmp_path: Path) -> None:
    """Les fichiers d'avant #1299, et la création d'équipe qui ne pose pas le drapeau.

    Une instance était le défaut partout : l'avoir écrite ne disait rien d'un choix, et
    c'est ce défaut-là que #1299 remplace. Toute autre valeur a été choisie — par la
    personne à l'écran de capacité, ou validée avec l'équipe — et reste fixée.
    """
    racine = tmp_path / "capacite"
    racine.mkdir()
    (racine / "dev.json").write_text(
        json.dumps({"nom": "dev", "actif": True, "instances": 1}), encoding="utf-8"
    )
    (racine / "qa.json").write_text(
        json.dumps({"nom": "qa", "actif": True, "instances": 2}), encoding="utf-8"
    )
    depot = CapacityStore(racine)

    assert depot.lire("dev").fixe_ses_instances is False
    assert depot.lire("qa").fixe_ses_instances is True
    # Désactiver seul ne fixe rien non plus : c'est une autre question.
    depot.ecrire(CapaciteAgent(nom="ux", actif=False))
    assert depot.lire("ux").fixe_ses_instances is False

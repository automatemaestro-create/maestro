"""Un run **dit ce qu'il fait, et pourquoi il s'est arrêté** (#479 ; couvert ici par #480).

Le lot 7 du chantier #472 a deux moitiés qui ne se ressemblent pas, et ce fichier
les garde toutes les deux.

**① Ce qu'il fait** — le silence d'une tâche longue était *à la source* : le
fournisseur consommait le flux du SDK sans rien publier entre `<tache>:debut` et
l'issue, quelle que soit la durée. `maestro.providers.activite` émet désormais
chaque geste, et `RegulateurActivite` les publie **à débit borné**, une salve qui
**annonce son regroupement**. Trois propriétés font tout le lot, et chacune a son
test : le **premier** geste part sans attendre la fenêtre (l'attendre recréerait au
démarrage le trou qu'on comble), la **séquence** n'est pas dédupliquée (c'est
l'information), et l'observateur **ne casse jamais l'observé**.

**② Pourquoi il s'est arrêté** — le moteur connaissait ses causes
(`TurnLimitReached`, `PlafondDepenseDepasse`, `DemarrageHoteRate`, l'annulation)
mais trois appelants recopiaient `f"{type} : {exc}"`, si bien que la liste disait
« Échec » à des pannes qui ne se réparent pas de la même façon. `causes.py` les
classe, et **l'ordre des tests de `cause_de` est le contenu du contrat** : les
types d'abord, le texte en dernier — ce que le moteur *sait* l'emporte sur ce
qu'un message *suggère*.

**Ni Redis, ni réseau, ni appel modèle** : le régulateur reçoit une horloge
injectée (une cadence éprouvée avec de vraies secondes rendrait la suite lente
**et** dépendante de la charge du poste), et les causes se lisent sur des
exceptions construites à la main.
"""

from __future__ import annotations

import asyncio

import pytest

from maestro.controltower import (
    CAUSE_ANNULATION,
    CAUSE_HOTE,
    CAUSE_LIMITE_USAGE,
    CAUSE_PLAFOND_COUT,
    CAUSE_PLAFOND_TOURS,
    CAUSES,
    ControlTowerState,
    Event,
    InMemoryEventBus,
    cause_de,
    detail_avec_cause,
)
from maestro.controltower.bridge import evenements_depuis_step
from maestro.controltower.events import EVENEMENT_AGENT_ACTIVITE
from maestro.controltower.executions import ServiceExecutions
from maestro.controltower.hote import DemarrageHoteRate, HoteRun, OrdreRun
from maestro.controltower.state import (
    EVENEMENT_EXECUTION_STATUT,
    EXECUTION_ECHEC,
    EXECUTION_EN_COURS,
    EXECUTION_TERMINEE,
)
from maestro.engine.executor import (
    STATUT_ACTIVITE,
    SUFFIXE_ETAPE_ACTIVITE,
    LocalExecutor,
)
from maestro.orchestrator.schema import Task
from maestro.providers.activite import (
    CIBLE_MAX,
    JALON_MAX,
    Geste,
    RegulateurActivite,
    cible_depuis,
    resume_salve,
)
from maestro.providers.base import TurnLimitReached
from maestro.telemetry import PlafondDepenseDepasse, RunJournal, StepUsage

RUN = "run-bavard"


# ------------------------------------------------- ①a La cible d'un appel d'outil


@pytest.mark.parametrize(
    ("entree", "attendu"),
    [
        ({"file_path": "maestro/engine/executor.py"}, "maestro/engine/executor.py"),
        ({"command": "pytest -q"}, "pytest -q"),
        ({"url": "https://example.test/spec"}, "https://example.test/spec"),
        ({"query": "porte d'exécution"}, "porte d'exécution"),
        ({"description": "chercher les appels"}, "chercher les appels"),
    ],
)
def test_la_cible_se_lit_dans_l_entree_de_l_outil(entree, attendu):
    assert cible_depuis(entree) == attendu


def test_le_motif_l_emporte_sur_le_chemin_quand_les_deux_sont_la():
    """L'ordre des clés **est** la décision : `Grep · .` n'apprendrait rien.

    Un `path` vaut « . » neuf fois sur dix ; c'est le motif qu'on veut voir.
    """
    assert cible_depuis({"pattern": "porte", "path": "."}) == "porte"


@pytest.mark.parametrize(
    "entree",
    [
        {},
        {"inconnu": "valeur"},  # un outil MCP : les clés de son serveur
        {"file_path": "   "},
        {"file_path": 42},
        "pas un objet",
        None,
    ],
)
def test_une_entree_sans_cible_connue_ne_fait_jamais_echouer_l_observation(entree):
    """Tracer une activité ne doit jamais casser l'activité tracée."""
    assert cible_depuis(entree) == ""


def test_une_cible_trop_longue_est_coupee_en_le_disant():
    cible = cible_depuis({"command": "x" * (CIBLE_MAX + 50)})

    assert len(cible) == CIBLE_MAX + 1
    assert cible.endswith("…")


def test_les_espaces_d_une_cible_sont_normalises():
    """Un fil d'activité est une ligne : un saut de ligne y casserait la mise en page."""
    assert cible_depuis({"command": "pytest\n  -q\t-x"}) == "pytest -q -x"


# ----------------------------------------------------------- ①b Un geste, en clair


@pytest.mark.parametrize(
    ("geste", "attendu"),
    [
        (Geste(outil="Read", cible="loop.py"), "Read · loop.py"),
        (Geste(outil="Bash"), "Bash"),
        (Geste.jalon("Je relis la boucle."), "« Je relis la boucle. »"),
        (Geste.jalon("   "), "réflexion"),
    ],
)
def test_un_geste_se_rend_en_une_ligne(geste, attendu):
    assert str(geste) == attendu


def test_un_jalon_de_texte_est_borne_plus_court_qu_une_cible():
    """Un bloc de prose peut faire des pages : on n'en garde que le début."""
    jalon = Geste.jalon("mot " * 200)

    assert jalon.cible.endswith("…")
    assert len(jalon.cible) <= JALON_MAX + 1
    assert JALON_MAX < CIBLE_MAX


# --------------------------------------------- ①c Une salve annonce son regroupement


def test_une_salve_vide_ne_dit_rien():
    assert resume_salve([]) == ""


def test_un_geste_seul_se_rend_tel_quel():
    """Le préfixer d'un « 1 geste » n'apprendrait rien — c'est le cas courant."""
    assert resume_salve([Geste(outil="Read", cible="loop.py")]) == "Read · loop.py"


def test_une_salve_dit_son_compte_sa_repartition_et_son_dernier_geste():
    """La moitié « en le disant » du débit borné.

    Une ligne qui tairait son regroupement se lirait comme un geste isolé, et un
    observateur en conclurait que l'agent est huit fois plus lent qu'il ne l'est.
    """
    salve = [
        Geste(outil="Read", cible="a.py"),
        Geste(outil="Read", cible="b.py"),
        Geste(outil="Read", cible="c.py"),
        Geste(outil="Bash", cible="pytest"),
    ]

    assert resume_salve(salve) == "4 gestes · Read×3, Bash — dernier : Bash · pytest"


def test_le_multiplicateur_est_tu_quand_l_outil_n_a_servi_qu_une_fois():
    salve = [Geste(outil="Read", cible="a.py"), Geste(outil="Bash")]

    assert "Read, Bash" in resume_salve(salve)
    assert "×1" not in resume_salve(salve)


def test_un_jalon_de_texte_se_compte_sous_le_nom_texte():
    salve = [Geste.jalon("je réfléchis"), Geste.jalon("encore"), Geste(outil="Read")]

    assert "texte×2" in resume_salve(salve)


def test_le_dernier_geste_est_rendu_et_non_le_premier():
    """C'est lui qui dit où l'agent en est **maintenant**, ce que la ligne répond."""
    salve = [Geste(outil="Read", cible="a.py"), Geste(outil="Write", cible="z.py")]

    assert resume_salve(salve).endswith("dernier : Write · z.py")


# ------------------------------------------------------------ ①d Le débit borné


class Horloge:
    """Une horloge qu'on avance à la main — la cadence est le sujet, pas l'attente."""

    def __init__(self) -> None:
        self.instant = 0.0

    def __call__(self) -> float:
        return self.instant

    def avance(self, secondes: float) -> None:
        self.instant += secondes


def _regulateur(periode_s: float = 5.0) -> tuple[RegulateurActivite, list[str], Horloge]:
    publies: list[str] = []
    horloge = Horloge()
    return (
        RegulateurActivite(publies.append, periode_s=periode_s, horloge=horloge),
        publies,
        horloge,
    )


def test_le_premier_geste_part_sans_attendre_la_fenetre():
    """Sinon on recrée au démarrage le trou même qu'on vient de combler.

    Un agent qui met trois secondes à appeler son premier outil ne doit pas en
    coûter cinq de plus avant que l'écran ne bouge.
    """
    regulateur, publies, _ = _regulateur()

    regulateur.note(Geste(outil="Read", cible="loop.py"))

    assert publies == ["Read · loop.py"]


def test_les_gestes_de_la_fenetre_s_accumulent_au_lieu_de_partir_un_par_un():
    regulateur, publies, horloge = _regulateur(periode_s=5.0)
    regulateur.note(Geste(outil="Read", cible="a.py"))

    horloge.avance(1.0)
    regulateur.note(Geste(outil="Read", cible="b.py"))
    horloge.avance(1.0)
    regulateur.note(Geste(outil="Bash", cible="pytest"))

    assert publies == ["Read · a.py"]  # rien de plus n'est parti


def test_la_fenetre_ecoulee_publie_la_salve_accumulee():
    regulateur, publies, horloge = _regulateur(periode_s=5.0)
    regulateur.note(Geste(outil="Read", cible="a.py"))
    regulateur.note(Geste(outil="Read", cible="b.py"))

    horloge.avance(6.0)
    regulateur.note(Geste(outil="Bash", cible="pytest"))

    # Le premier geste est parti seul (la fenêtre n'était pas encore ouverte) ;
    # la seconde salve regroupe ce qui a suivi.
    assert publies == [
        "Read · a.py",
        "2 gestes · Read, Bash — dernier : Bash · pytest",
    ]


def test_vider_publie_le_reliquat_a_la_fin_d_une_tache_courte():
    """Sans quoi les derniers gestes d'une tâche courte ne seraient jamais dits."""
    regulateur, publies, _ = _regulateur(periode_s=5.0)
    regulateur.note(Geste(outil="Read", cible="a.py"))
    regulateur.note(Geste(outil="Read", cible="b.py"))

    regulateur.vider()

    assert publies == ["Read · a.py", "Read · b.py"]


def test_vider_sans_reliquat_ne_publie_pas_une_ligne_vide():
    regulateur, publies, _ = _regulateur()

    regulateur.vider()
    regulateur.vider()

    assert publies == []


def test_la_sequence_n_est_pas_dedupliquee_c_est_l_information():
    """Deux `Read` successifs sont deux gestes — le point même du ticket."""
    regulateur, publies, _ = _regulateur(periode_s=0.0)

    for _ in range(3):
        regulateur.note(Geste(outil="Read", cible="a.py"))

    assert publies == ["Read · a.py"] * 3


def test_un_publieur_en_panne_ne_casse_pas_l_execution_observee():
    """La règle déjà posée pour `on_refus` : observer ne casse jamais l'observé."""

    def publieur_qui_leve(_texte: str) -> None:
        raise RuntimeError("le bus est tombé")

    regulateur = RegulateurActivite(publieur_qui_leve, periode_s=0.0)

    regulateur.note(Geste(outil="Read"))
    regulateur.vider()  # ne lève pas non plus


# --------------------------------- ①e Du geste au fil : l'étape, puis l'événement


def _tache() -> Task:
    return Task(
        id="t1",
        titre="Implémenter la boucle",
        description="…",
        competences_requises=("python",),
        format_sortie="Module",
        dependances=(),
    )


class _Agent:
    """Le strict nécessaire : `_consigne_activite` ne lit que le nom et le rôle."""

    nom = "developpeur"
    role = "Développeur"


def test_une_salve_devient_une_etape_activite_du_journal():
    journal = RunJournal(run_id=RUN)
    executeur = LocalExecutor.__new__(LocalExecutor)  # aucun fournisseur à résoudre

    LocalExecutor._consigne_activite(
        executeur, _tache(), _Agent(), "Read · loop.py", journal
    )

    (etape,) = [ligne.to_dict() for ligne in journal.records]
    assert etape["etape"] == f"t1{SUFFIXE_ETAPE_ACTIVITE}"
    assert etape["statut"] == STATUT_ACTIVITE
    assert etape["sortie"] == "Read · loop.py"
    assert etape["nom"] == "Implémenter la boucle"
    # Usage nul : une salve n'est pas un appel modèle de plus, le coût de la
    # tâche reste porté par son étape finale.
    assert etape["usage"] == StepUsage().to_dict()


@pytest.mark.parametrize("texte", ["", "   ", "\n"])
def test_une_salve_vide_n_est_pas_consignee(texte):
    """Une ligne vide au fil d'activité se lirait comme une panne d'affichage."""
    journal = RunJournal(run_id=RUN)
    executeur = LocalExecutor.__new__(LocalExecutor)

    LocalExecutor._consigne_activite(executeur, _tache(), _Agent(), texte, journal)

    assert journal.records == ()


def test_le_pont_range_une_etape_activite_en_activite_d_agent():
    """Même traitement que `:relance` et `:refus-outil` — elle ne déplace aucune carte."""
    (event,) = evenements_depuis_step(
        {
            "run_id": RUN,
            "etape": f"t1{SUFFIXE_ETAPE_ACTIVITE}",
            "nom": "Implémenter la boucle",
            "agent": "developpeur",
            "role": "Développeur",
            "statut": STATUT_ACTIVITE,
            "entree": "",
            "sortie": "4 gestes · Read×3, Bash — dernier : Bash · pytest",
            "erreur": None,
        }
    )

    assert event.type == EVENEMENT_AGENT_ACTIVITE
    assert event.tache_id == "t1"
    assert event.run_id == RUN
    assert "4 gestes" in event.detail


def test_une_activite_ne_change_le_statut_d_aucune_tache():
    """La projection ne s'en sert que pour rafraîchir la dernière activité de l'agent."""
    state = ControlTowerState()
    for event in evenements_depuis_step(
        {
            "run_id": RUN,
            "etape": f"t1{SUFFIXE_ETAPE_ACTIVITE}",
            "nom": "Implémenter",
            "agent": "developpeur",
            "role": "Développeur",
            "statut": STATUT_ACTIVITE,
            "sortie": "Read · loop.py",
        }
    ):
        state.appliquer(event)

    assert state.taches() == []


# ------------------------------------------------- ②a Les causes, et leur ordre


@pytest.mark.parametrize(
    ("erreur", "attendue"),
    [
        (PlafondDepenseDepasse("plafond de coût crevé : 5.02 $ / 5.00 $"), CAUSE_PLAFOND_COUT),
        (DemarrageHoteRate("le process n'est pas parti (code 1)"), CAUSE_HOTE),
        (asyncio.CancelledError(), CAUSE_ANNULATION),
        (RuntimeError("Claude AI usage limit reached"), CAUSE_LIMITE_USAGE),
        (RuntimeError("boum"), ""),
    ],
)
def test_chaque_cause_connue_se_nomme(erreur, attendue):
    assert cause_de(erreur) == attendue


def test_le_plafond_de_tours_est_reconnu_par_son_type():
    assert cause_de(TurnLimitReached("plafond de 40 tours atteint")) == CAUSE_PLAFOND_TOURS


def test_un_type_connu_l_emporte_sur_un_texte_qui_suggere_autre_chose():
    """L'ordre des tests est celui de la **précision** : les types d'abord.

    Un `TurnLimitReached` dont le message citerait « rate limit » reste un plafond
    de tours — c'est ce que le moteur *sait*, contre ce qu'un message *suggère*.
    """
    assert cause_de(TurnLimitReached("rate limit")) == CAUSE_PLAFOND_TOURS


@pytest.mark.parametrize(
    "message",
    [
        "Claude AI usage limit reached",
        "RATE LIMIT exceeded",
        "rate-limit",
        "429 Too Many Requests",
        '{"api_error_status": 429}',
        "Your credit balance is too low",
    ],
)
def test_la_limite_d_usage_est_la_seule_cause_reconnue_au_texte(message):
    """Faute de type : rien dans `maestro/` ne la détecte, et `est_transitoire`
    la range même parmi les aléas relançables. Ses marqueurs sont **repris** de
    `scripts/orchestrate/run.sh` (#171) plutôt que réinventés."""
    assert cause_de(RuntimeError(message)) == CAUSE_LIMITE_USAGE


def test_un_echec_inclassable_rend_une_chaine_vide_et_non_une_cause_fourre_tout():
    """Inventer une sixième cause ferait passer « je n'ai pas su ranger » pour un
    diagnostic — le `detail` porte déjà le type et le message."""
    assert cause_de(ValueError("chemin introuvable")) == ""
    assert "" not in CAUSES


def test_le_detail_garde_la_forme_que_les_trois_soldeurs_recopiaient():
    """La changer réécrirait l'historique de tous les runs passés à l'écran."""
    detail, cause = detail_avec_cause(RuntimeError("boum"))

    assert detail == "RuntimeError : boum"
    assert cause == ""


def test_la_cause_vient_en_plus_du_detail_jamais_a_sa_place():
    detail, cause = detail_avec_cause(PlafondDepenseDepasse("5.02 $ / 5.00 $"))

    assert "5.02 $" in detail
    assert cause == CAUSE_PLAFOND_COUT


# ---------------------------------------- ②b La cause voyage, et se pose


def test_la_cause_traverse_l_evenement_et_son_aller_retour_json():
    event = Event(
        type=EVENEMENT_EXECUTION_STATUT,
        run_id=RUN,
        statut=EXECUTION_ECHEC,
        cause=CAUSE_LIMITE_USAGE,
    )

    relu = Event.from_dict(event.to_dict())

    assert event.to_dict()["cause"] == CAUSE_LIMITE_USAGE
    assert relu.cause == CAUSE_LIMITE_USAGE


def test_une_cause_inconnue_est_relue_telle_quelle_sans_validation():
    """Le bus ne juge pas d'un vocabulaire : une trace d'un backend plus récent
    doit se relire, quitte à ce que l'écran ne sache pas la nommer."""
    relu = Event.from_dict({"type": EVENEMENT_EXECUTION_STATUT, "cause": "venue-du-futur"})

    assert relu.cause == "venue-du-futur"


def test_la_cause_se_pose_dans_la_projection_sur_un_run_solde():
    state = ControlTowerState()
    state.appliquer(
        Event(type=EVENEMENT_EXECUTION_STATUT, run_id=RUN, statut=EXECUTION_EN_COURS)
    )
    state.appliquer(
        Event(
            type=EVENEMENT_EXECUTION_STATUT,
            run_id=RUN,
            statut=EXECUTION_ECHEC,
            detail="RuntimeError : usage limit reached",
            cause=CAUSE_LIMITE_USAGE,
        )
    )

    execution = state.execution(RUN)
    assert execution.cause == CAUSE_LIMITE_USAGE
    assert execution.resume()["cause"] == CAUSE_LIMITE_USAGE


def test_un_run_qui_repart_n_affiche_plus_la_cause_de_sa_mort():
    """`cause` suit exactement le régime de `fin` : un run relancé qui aurait gardé
    la cause d'un plafond de coût continuerait d'afficher la mort dont il revient."""
    state = ControlTowerState()
    for statut, cause in (
        (EXECUTION_EN_COURS, ""),
        (EXECUTION_ECHEC, CAUSE_PLAFOND_COUT),
        (EXECUTION_EN_COURS, ""),
    ):
        state.appliquer(
            Event(type=EVENEMENT_EXECUTION_STATUT, run_id=RUN, statut=statut, cause=cause)
        )

    assert state.execution(RUN).cause == ""


def test_un_run_sans_cause_connue_n_en_porte_aucune():
    """Le cas courant : un échec applicatif que `causes.py` ne sait pas classer."""
    state = ControlTowerState()
    state.appliquer(
        Event(type=EVENEMENT_EXECUTION_STATUT, run_id=RUN, statut=EXECUTION_TERMINEE)
    )

    assert state.execution(RUN).resume()["cause"] == ""


# ------------------------- ②c Le service consigne la cause de ce qu'il solde


class HoteQuiNePartPas(HoteRun):
    """Un hôte dont le démarrage rate (#443) — la cause qui n'est pas un échec de run."""

    async def lancer(self, ordre_du_run: OrdreRun) -> None:
        raise DemarrageHoteRate("le process n'est pas parti (code 1)")

    async def annuler(self, run_id: str, *, delai_s: float) -> bool:
        return False

    def en_vol(self, run_id: str) -> bool:
        return False

    def runs_en_vol(self) -> tuple[str, ...]:
        return ()

    def ramasser(self):
        return ()

    async def fermer(self, *, delai_s: float) -> None:
        return None


def test_un_hote_qui_ne_demarre_pas_solde_le_run_avec_sa_cause():
    """« Ici rien n'a jamais tourné » : ni tâche, ni coût, ni journal à lire —
    seulement un process qui n'est pas parti. La liste doit pouvoir le dire."""
    projection = ControlTowerState()
    pilote = ServiceExecutions(InMemoryEventBus(), projection, hote=HoteQuiNePartPas())

    resume = asyncio.run(pilote.lancer("Prototyper"))

    execution = projection.execution(resume["run_id"])
    assert execution.statut == EXECUTION_ECHEC
    assert execution.cause == CAUSE_HOTE

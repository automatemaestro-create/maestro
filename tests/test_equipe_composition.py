"""L'équipe se propose pour le **besoin réel** du projet, et se corrige en langage naturel (#1159).

[docs/41](../docs/41-decision-maestro-juge-il-ne-bride-pas.md) renverse la dérivation par
règles de #1039 : cinq gabarits retenus par des règles fixes ne proposaient **jamais** un
rôle mobile, d'apprentissage automatique, de sécurité ou de documentation, et le rôle
données jamais sur un projet neuf. Le modèle compose désormais l'équipe ; les gabarits
restent une **matière**, et les règles le **repli** quand il ne répond pas.

Ce que cette suite tient, dans l'ordre du ticket :

① **le modèle nomme les rôles pertinents, avec leur raison** — un projet mobile, un
   pipeline d'apprentissage, et le rôle données sur un projet neuf qui en a besoin ;
② **l'exécution vérifie ce qu'il écrit** — un rôle sans raison, un gabarit inconnu, un
   skill que l'outillage ne recommande pas, une preuve que l'analyse n'a pas lue, un
   nombre d'instances hors bornes, l'orchestrateur : rien de cela ne passe tel quel ;
③ **le repli se dit** — une composition qui n'aboutit pas retombe sur les règles des
   gabarits, et la proposition nomme la cause ;
④ **« ajoute quelqu'un pour la sécurité » est compris** — le rôle est ajouté avec son
   playbook, par la route que l'étape d'équipe appelle ;
⑤ **le prompt porte le projet et les mots de la personne**, et le registre de langue.

Le fournisseur est **factice** (§5 de docs/41 : les tests unitaires gardent leurs
doubles) : il rend le texte qu'un modèle aurait rendu, et note ce qu'on lui a demandé.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from maestro.agents.capacity import CapacityStore
from maestro.agents.configuration import ConfigurationAgents
from maestro.agents.mcp import McpStore
from maestro.agents.permissions import PermissionStore
from maestro.agents.playbook_du_code import registre
from maestro.agents.playbooks import PlaybookStore
from maestro.agents.store import NOMS_RESERVES, AgentStore, SurchargeStore
from maestro.controltower import ControlTowerState, InMemoryEventBus, create_app
from maestro.controltower.equipe import CompositeurEquipe, ServiceEquipe
from maestro.controltower.generation_agent import (
    INTENTION_MAX,
    DefinitionProposee,
    GenerateurDefinitionAgent,
)
from maestro.controltower.projets import ServiceProjets
from maestro.equipe import (
    ECARTE_ORCHESTRATEUR,
    INSTANCES_MAX_CREEES,
    OUTIL_EXECUTION,
    proposer_equipe,
)
from maestro.equipe.composition import (
    CADRE_COMPOSITION,
    DEMANDE_MAX,
    CompositionIllisible,
    MembreActuel,
    corriger_equipe,
    lire_reponse,
    prompt_composition,
    prompt_correction,
    proposer_equipe_composee,
)
from maestro.equipe.modele import (
    ORIGINE_COMPOSITION_MODELE,
    ORIGINE_COMPOSITION_REGLES,
    ORIGINE_PLAYBOOK_ESQUISSE,
    ORIGINE_PLAYBOOK_GABARIT,
    ORIGINE_PLAYBOOK_GENERE,
)
from maestro.outillage.modele import Commande, Constats, Langage
from maestro.outillage.questionnaire import (
    Choix,
    constats_depuis_choix,
    recommandation_depuis_choix,
)
from maestro.outillage.recommandation import recommander
from maestro.projets import ProjetStore
from maestro.providers.base import ModelProvider

PROJET = "prj-mobile"


# --------------------------------------------------------------------------- #
# Les doubles : un fournisseur qui rend ce qu'un modèle aurait rendu
# --------------------------------------------------------------------------- #


class _FournisseurEcrit(ModelProvider):
    """Rend, dans l'ordre, les textes qu'on lui a donnés — et note chaque demande."""

    name = "ecrit"

    def __init__(self, *reponses: str) -> None:
        self._reponses = list(reponses)
        self.demandes: list[tuple[str, str | None]] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt: str, *, model: str, system_prompt: str | None = None) -> str:
        self.demandes.append((prompt, system_prompt))
        return self._reponses.pop(0)

    async def run_agent(self, *args: Any, **kwargs: Any) -> str:  # pragma: no cover
        raise AssertionError("la composition d'équipe ne fait qu'écrire")


class _FournisseurEnPanne(ModelProvider):
    """Un fournisseur qui ne répond jamais — quota épuisé, réseau coupé."""

    name = "en-panne"

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt: str, *, model: str, system_prompt: str | None = None) -> str:
        raise RuntimeError("quota épuisé (test)")

    async def run_agent(self, *args: Any, **kwargs: Any) -> str:  # pragma: no cover
        raise AssertionError("jamais appelé")


class _GenerateurEcrit(GenerateurDefinitionAgent):
    """Un générateur de playbooks qui écrit un playbook reconnaissable par rôle."""

    def __init__(self) -> None:
        super().__init__(provider=_FournisseurEnPanne())
        self.intentions: list[str] = []

    async def proposer(self, intention: str, **kwargs: Any) -> DefinitionProposee:
        self.intentions.append(intention)
        return DefinitionProposee(
            intention=intention,
            nom="x",
            role="x",
            competences=("x",),
            playbook=f"Playbook écrit pour : {intention}",
        )


class _GenerateurHorsLigne(GenerateurDefinitionAgent):
    """Un générateur de playbooks qui ne répond jamais — le rôle garde son repli."""

    async def proposer(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("hors ligne (test)")


def _json(**contenu: Any) -> str:
    """Ce qu'un modèle rend, noyé dans un peu de prose pour éprouver la lecture."""
    return "Voici l'équipe :\n```json\n" + json.dumps(contenu, ensure_ascii=False) + "\n```"


# --------------------------------------------------------------------------- #
# Des projets hors des cinq gabarits
# --------------------------------------------------------------------------- #


def _constats_mobile() -> Constats:
    """Une application Flutter : du Dart, et rien qui ressemble à un gabarit d'écran."""
    return Constats(
        langages=(Langage(nom="Dart", fichiers=24, part=1.0, exemple="lib/main.dart"),),
        commandes=(
            Commande(
                usage="tester",
                commande="flutter test",
                chemin="pubspec.yaml",
                origine="declaree",
            ),
        ),
    )


def _constats_ml() -> Constats:
    """Un pipeline d'entraînement : du Python et des carnets, aucune interface."""
    return Constats(
        langages=(
            Langage(nom="Python", fichiers=18, part=0.7, exemple="pipeline/entrainer.py"),
            Langage(nom="Jupyter Notebook", fichiers=6, part=0.3, exemple="carnets/eda.ipynb"),
        ),
    )


ROLE_MOBILE = {
    "nom": "mobile",
    "role": "Développeur mobile",
    "competences": ["flutter", "dart", "ios", "android"],
    "raison": "le projet est une application Flutter (lib/main.dart) : iOS et Android se "
    "construisent et se testent à part",
    "preuve": "lib/main.dart",
    "gabarit": "",
    "instances": 1,
    "skills": ["lancer-les-tests"],
}

ROLE_ML = {
    "nom": "ml",
    "role": "Ingénieur apprentissage automatique",
    "competences": ["machine learning", "entraînement", "évaluation", "données"],
    "raison": "le projet entraîne un modèle (pipeline/entrainer.py) et l'évalue dans des "
    "carnets : le jeu de données et les métriques sont un métier à part",
    "preuve": "pipeline/entrainer.py",
    "gabarit": "",
    "instances": 1,
    "skills": [],
}


@pytest.mark.parametrize(
    ("constats", "brut", "attendu"),
    [
        (_constats_mobile(), ROLE_MOBILE, "Développeur mobile"),
        (_constats_ml(), ROLE_ML, "Ingénieur apprentissage automatique"),
    ],
    ids=["application-mobile", "pipeline-ml"],
)
def test_hors_des_gabarits_la_proposition_nomme_les_roles_pertinents_avec_leur_raison(
    constats: Constats, brut: dict[str, Any], attendu: str
) -> None:
    """① Le critère du ticket : ce que les règles n'auraient **jamais** proposé."""
    regles = proposer_equipe(constats, recommander(constats), projet_id=PROJET)
    assert attendu not in {role.role for role in regles.roles}

    reponse = lire_reponse(_json(roles=[brut], ecartes=[]))
    proposition = proposer_equipe_composee(
        constats, recommander(constats), reponse, projet_id=PROJET
    )

    role = next(r for r in proposition.roles if r.role == attendu)
    assert role.raison == brut["raison"]
    assert role.gabarit == ""  # aucun gabarit n'en est la matière
    assert role.justification is not None
    assert role.justification.chemin == brut["preuve"]
    # Un rôle hors gabarit a un playbook dès la proposition — une esquisse, dite telle.
    assert role.playbook.strip()
    assert role.playbook_origine == ORIGINE_PLAYBOOK_ESQUISSE
    assert attendu in role.playbook
    assert registre() in role.playbook
    # Et il reçoit ses autorisations par la même règle qu'un rôle de gabarit.
    assert [a.outil for a in role.autorisations] == [OUTIL_EXECUTION]
    assert proposition.composition_origine == ORIGINE_COMPOSITION_MODELE


def test_les_competences_d_un_role_compose_sont_des_tags_que_le_routage_sait_lire() -> None:
    """Le routage confronte des tags exacts (`Agent.couverture`), et le catalogue les
    écrit en minuscules, sans accent, reliés par des tirets. Un « machine learning »
    tel quel ne serait jamais apparié."""
    proposition = proposer_equipe_composee(
        _constats_ml(),
        recommander(_constats_ml()),
        lire_reponse(_json(roles=[ROLE_ML])),
        projet_id=PROJET,
    )

    assert proposition.roles[0].competences == (
        "machine-learning",
        "entrainement",
        "evaluation",
        "donnees",
    )


def test_le_role_donnees_est_propose_sur_un_projet_neuf_qui_en_a_besoin() -> None:
    """① Le rôle données ne naissait **que** de fichiers SQL, donc jamais sur un projet
    neuf. Le modèle lit ce que la personne a répondu, et le propose avec sa raison ;
    il descend du gabarit `bdd`, dont il garde le playbook de repli."""
    choix = (
        Choix(cle="nature", valeur="service-api"),
        Choix(cle="langages", valeur="python"),
    )
    constats = constats_depuis_choix(choix)
    regles = proposer_equipe(constats, recommandation_depuis_choix(choix), choix=choix)
    assert "donnees" not in {role.nom for role in regles.roles}

    reponse = lire_reponse(
        _json(
            roles=[
                {
                    "nom": "donnees",
                    "role": "Base de données",
                    "competences": ["sql", "schema", "migration"],
                    "raison": "vous décrivez un service qui enregistre et restitue des "
                    "données : leur schéma et ses migrations sont à tenir dès le départ",
                    "gabarit": "bdd",
                }
            ]
        )
    )
    proposition = proposer_equipe_composee(
        constats, recommandation_depuis_choix(choix), reponse, projet_id=PROJET
    )

    donnees = next(r for r in proposition.roles if r.nom == "donnees")
    assert donnees.gabarit == "bdd"
    assert "enregistre et restitue des données" in donnees.raison
    assert donnees.playbook_origine == ORIGINE_PLAYBOOK_GABARIT
    assert donnees.justification is None  # aucun fichier ne le prouve encore : rien d'inventé


def test_un_role_qui_descend_d_un_gabarit_en_garde_la_matiere() -> None:
    """Les gabarits sont une matière : un rôle « qa » garde ses compétences s'il n'en
    donne pas, et branche les skills de son gabarit par usage."""
    constats = _constats_mobile()
    testeur = {"role": "Testeur", "raison": "les tests Flutter sont déclarés", "gabarit": "qa"}
    reponse = lire_reponse(_json(roles=[testeur]))

    tests = proposer_equipe_composee(constats, recommander(constats), reponse).roles[0]

    assert tests.gabarit == "qa"
    assert set(tests.competences) == {"tests", "e2e", "review", "qa"}
    assert [s.nom for s in tests.skills] == ["lancer-les-tests"]
    assert tests.nom not in NOMS_RESERVES


# --------------------------------------------------------------------------- #
# ② L'exécution vérifie ce que le modèle écrit
# --------------------------------------------------------------------------- #


def _compose(*roles: dict[str, Any], constats: Constats | None = None, **extra: Any):
    constats = constats or _constats_mobile()
    return proposer_equipe_composee(
        constats,
        recommander(constats),
        lire_reponse(_json(roles=list(roles), **extra)),
        projet_id=PROJET,
    )


def test_un_role_sans_raison_n_est_pas_propose_et_la_proposition_le_dit() -> None:
    """Chaque rôle avec sa raison — un rôle qui n'en porte pas est une affirmation."""
    proposition = _compose(ROLE_MOBILE, {**ROLE_ML, "raison": "  "})

    assert [r.role for r in proposition.roles] == ["Développeur mobile"]
    assert "Ingénieur apprentissage automatique" in proposition.composition_raison


def test_un_gabarit_inconnu_ne_devient_pas_une_filiation() -> None:
    role = _compose({**ROLE_MOBILE, "gabarit": "astronaute"}).roles[0]

    assert role.gabarit == ""
    assert role.playbook_origine == ORIGINE_PLAYBOOK_ESQUISSE


def test_un_skill_que_l_outillage_ne_recommande_pas_n_est_pas_branche() -> None:
    skills = ["lancer-les-tests", "deployer-sur-la-lune"]
    role = _compose({**ROLE_MOBILE, "skills": skills}).roles[0]

    assert [s.nom for s in role.skills] == ["lancer-les-tests"]


def test_une_preuve_que_l_analyse_n_a_pas_lue_n_est_pas_citee() -> None:
    """« Ce qui n'est pas vérifié n'est pas cité » : le chemin doit figurer dans les
    constats, sans quoi le rôle garde sa raison mais perd la pièce inventée."""
    role = _compose({**ROLE_MOBILE, "preuve": "ios/Runner/Info.plist"}).roles[0]

    assert role.justification is None
    assert role.raison == ROLE_MOBILE["raison"]


@pytest.mark.parametrize("instances", [0, -2, INSTANCES_MAX_CREEES + 1, "trois"])
def test_des_instances_hors_bornes_reviennent_a_une_et_le_disent(instances: Any) -> None:
    role = _compose({**ROLE_MOBILE, "instances": instances}).roles[0]

    assert role.instances == 1
    assert "hors des bornes" in role.raison_instances


def test_des_instances_dans_les_bornes_sont_gardees_avec_leur_raison() -> None:
    role = _compose(
        {**ROLE_MOBILE, "instances": 2, "raison_instances": "iOS et Android en parallèle"}
    ).roles[0]

    assert role.instances == 2
    assert role.raison_instances == "iOS et Android en parallèle"


def test_l_orchestrateur_n_est_jamais_recrute_meme_si_le_modele_le_propose() -> None:
    proposition = _compose(
        ROLE_MOBILE,
        {"nom": "orchestrateur", "role": "Orchestrateur", "raison": "il faut coordonner"},
    )

    assert all("orchestrateur" not in r.nom for r in proposition.roles)
    assert ECARTE_ORCHESTRATEUR in proposition.ecartes


def test_un_nom_pris_ou_reserve_est_rendu_libre() -> None:
    constats = _constats_mobile()
    proposition = proposer_equipe_composee(
        constats,
        recommander(constats),
        lire_reponse(
            _json(
                roles=[
                    ROLE_MOBILE,
                    {**ROLE_MOBILE, "role": "Développeur mobile (Android)"},
                    {**ROLE_MOBILE, "nom": "developpeur", "role": "Développeur"},
                ]
            )
        ),
        noms_pris=("mobile",),
    )

    noms = [r.nom for r in proposition.roles]
    assert noms == ["mobile-2", "mobile-3", "developpeur-2"]
    assert not set(noms) & NOMS_RESERVES


def test_les_gabarits_non_retenus_sont_ecartes_nommement_sans_promettre_de_geste() -> None:
    """Un gabarit que le modèle n'a pas retenu est nommé, avec la raison qu'il en donne
    — ou une raison qui dit que c'est son jugement. Aucune ne promet un geste (#1159)."""
    proposition = _compose(
        ROLE_MOBILE,
        ecartes=[{"gabarit": "bdd", "raison": "l'application ne stocke rien côté serveur"}],
    )

    ecartes = {e.nom: e.raison for e in proposition.ecartes}
    assert set(ecartes) == {"orchestrateur", "dev", "donnees", "infra", "interface", "tests"}
    assert ecartes["donnees"] == "l'application ne stocke rien côté serveur"
    assert "ne l'a pas retenu" in ecartes["infra"]
    for raison in ecartes.values():
        assert "validation" not in raison


@pytest.mark.parametrize(
    "texte",
    ["", "Je ne sais pas.", '{"roles": "mobile"}', _json(roles=[{"role": "Sans raison"}])],
    ids=["vide", "prose", "roles-pas-une-liste", "aucun-role-valide"],
)
def test_une_composition_inexploitable_leve(texte: str) -> None:
    """Une composition qui ne laisse **aucun** rôle vérifié n'est pas une équipe vide :
    c'est une réponse inexploitable, et le service retombe sur les règles."""
    with pytest.raises(CompositionIllisible):
        proposer_equipe_composee(
            _constats_mobile(), recommander(_constats_mobile()), lire_reponse(texte)
        )


def test_l_intention_d_un_role_compose_nomme_son_metier_et_tient_sous_la_borne() -> None:
    """#257 écrit le playbook depuis l'intention : un libellé seul (« Sécurité ») ne dit
    pas sur quoi porte le métier. Le régime d'exécution, qui la ferme, doit survivre à
    la coupe de `ServiceEquipe._playbook`."""
    role = _compose(ROLE_MOBILE).roles[0]

    assert "flutter" in role.intention
    assert role.intention[:INTENTION_MAX] == role.intention


# --------------------------------------------------------------------------- #
# ⑤ Le prompt porte le projet, les mots de la personne et le registre
# --------------------------------------------------------------------------- #


def test_le_cadre_porte_le_registre_et_ouvre_la_liste_des_roles() -> None:
    cadre = " ".join(CADRE_COMPOSITION.split())

    assert registre() in CADRE_COMPOSITION
    assert "pas une liste fermée" in cadre
    assert "Ne propose jamais l'orchestrateur" in cadre


def test_le_prompt_de_composition_porte_les_constats_les_reponses_et_la_matiere() -> None:
    choix = (Choix(cle="nature", valeur="service-api"),)
    constats = _constats_mobile()

    prompt = prompt_composition(constats, recommander(constats), choix, nom_projet="Pousse")

    assert "Pousse" in prompt
    assert "Dart" in prompt and "lib/main.dart" in prompt
    assert "nature : service-api" in prompt
    assert "lancer-les-tests" in prompt
    for gabarit in ("developpeur", "bdd", "devops", "designer", "qa"):
        assert gabarit in prompt


def test_le_prompt_de_correction_porte_la_demande_et_l_equipe_actuelle() -> None:
    constats = _constats_mobile()
    equipe = (MembreActuel(nom="mobile", role="Développeur mobile", retenu=True, instances=1),)

    prompt = prompt_correction(
        constats, recommander(constats), (), "ajoute quelqu'un pour la sécurité", equipe
    )

    assert "ajoute quelqu'un pour la sécurité" in prompt
    assert "mobile" in prompt and "Développeur mobile" in prompt


# --------------------------------------------------------------------------- #
# ④ La correction en langage naturel
# --------------------------------------------------------------------------- #

ROLE_SECURITE = {
    "nom": "securite",
    "role": "Sécurité applicative",
    "competences": ["securite", "audit", "dependances", "secrets"],
    "raison": "vous demandez quelqu'un pour la sécurité : l'application manipule des "
    "comptes, et ses dépendances comme ses secrets se relisent",
}

EQUIPE_MOBILE = (
    MembreActuel(nom="mobile", role="Développeur mobile", retenu=True, instances=1),
    MembreActuel(nom="tests", role="QA / Testeur", retenu=False, instances=1),
)


def test_ajoute_quelqu_un_pour_la_securite_est_compris_et_le_role_ajoute() -> None:
    constats = _constats_mobile()
    correction = corriger_equipe(
        constats,
        recommander(constats),
        lire_reponse(
            _json(roles=[ROLE_SECURITE], reponse="J'ajoute un rôle Sécurité applicative.")
        ),
        equipe=EQUIPE_MOBILE,
    )

    (securite,) = correction.ajouts
    assert securite.nom == "securite"
    assert securite.role == "Sécurité applicative"
    assert securite.playbook.strip()
    assert correction.reponse == "J'ajoute un rôle Sécurité applicative."
    assert correction.to_dict()["cree"] is False


def test_une_correction_ne_touche_qu_aux_roles_de_l_equipe_montree() -> None:
    """Retirer, remettre, changer les instances : seuls les noms de l'équipe montrée
    sont admis — un nom inventé ne retire rien et n'ajoute rien."""
    constats = _constats_mobile()
    correction = corriger_equipe(
        constats,
        recommander(constats),
        lire_reponse(
            _json(
                retraits=["mobile", "fantome"],
                remis=["tests", "fantome"],
                instances={"mobile": 2, "tests": INSTANCES_MAX_CREEES + 5, "fantome": 2},
                reponse="Fait.",
            )
        ),
        equipe=EQUIPE_MOBILE,
    )

    assert correction.retraits == ("mobile",)
    assert correction.remis == ("tests",)
    assert correction.instances == {"mobile": 2}


def test_un_ajout_ne_reprend_pas_le_nom_d_un_role_deja_montre() -> None:
    constats = _constats_mobile()
    correction = corriger_equipe(
        constats,
        recommander(constats),
        lire_reponse(_json(roles=[{**ROLE_SECURITE, "nom": "mobile"}], reponse="Ajouté.")),
        equipe=EQUIPE_MOBILE,
    )

    assert correction.ajouts[0].nom == "mobile-2"


def test_une_demande_qui_ne_porte_pas_sur_l_equipe_rend_la_reponse_sans_rien_changer() -> None:
    """Se laisser corriger, c'est aussi dire qu'on n'a pas compris — jamais deviner."""
    constats = _constats_mobile()
    correction = corriger_equipe(
        constats,
        recommander(constats),
        lire_reponse(_json(reponse="Je n'ai pas compris quel rôle vous voulez changer.")),
        equipe=EQUIPE_MOBILE,
    )

    assert correction.ajouts == () and correction.retraits == ()
    assert correction.reponse.startswith("Je n'ai pas compris")


def test_une_correction_muette_leve() -> None:
    constats = _constats_mobile()
    with pytest.raises(CompositionIllisible):
        corriger_equipe(
            constats, recommander(constats), lire_reponse(_json()), equipe=EQUIPE_MOBILE
        )


# --------------------------------------------------------------------------- #
# Le service : le modèle d'abord, les règles en repli, dites comme telles
# --------------------------------------------------------------------------- #


@pytest.fixture()
def atelier(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Sous un dossier utilisateur factice : `valider_racine` refuse `AppData` (#221)."""
    maison = tmp_path / "maison"
    maison.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: maison))
    atelier = tmp_path / "atelier"
    atelier.mkdir()
    return atelier


@pytest.fixture()
def gabarits(tmp_path: Path) -> ConfigurationAgents:
    racine = tmp_path / "core"
    return ConfigurationAgents(
        agents=AgentStore(racine / "agents"),
        surcharges=SurchargeStore(racine / "surcharges"),
        playbooks=PlaybookStore(racine / "playbooks"),
        permissions=PermissionStore(racine / "permissions"),
        mcp=McpStore(racine / "mcp"),
        capacites=CapacityStore(racine / "capacite"),
    )


def _projet_flutter(atelier: Path, projets: ServiceProjets) -> str:
    racine = atelier / "pousse"
    (racine / "lib").mkdir(parents=True)
    (racine / "lib" / "main.dart").write_text("void main() {}\n", encoding="utf-8")
    (racine / "pubspec.yaml").write_text("name: pousse\n", encoding="utf-8")
    return str(projets.creer("pousse", str(racine), origine="existant")["id"])


def _service(
    tmp_path: Path,
    atelier: Path,
    gabarits: ConfigurationAgents,
    fournisseur: ModelProvider,
    generateur: GenerateurDefinitionAgent | None = None,
) -> tuple[ServiceEquipe, str]:
    projets = ServiceProjets(ProjetStore(tmp_path / "depot"), racines_exploration=(atelier,))
    projet = _projet_flutter(atelier, projets)
    service = ServiceEquipe(
        projets,
        gabarits,
        generateur=generateur or _GenerateurHorsLigne(),
        compositeur=CompositeurEquipe(provider=fournisseur),
    )
    return service, projet


def test_le_service_compose_l_equipe_par_le_modele(
    tmp_path: Path, atelier: Path, gabarits: ConfigurationAgents
) -> None:
    fournisseur = _FournisseurEcrit(
        _json(roles=[{**ROLE_MOBILE, "preuve": "lib/main.dart"}])
    )
    generateur = _GenerateurEcrit()
    service, projet = _service(tmp_path, atelier, gabarits, fournisseur, generateur)

    servi = asyncio.run(service.proposer(projet))

    assert servi["composition"]["origine"] == ORIGINE_COMPOSITION_MODELE
    (mobile,) = servi["roles"]
    assert mobile["role"] == "Développeur mobile"
    assert mobile["justification"]["chemin"] == "lib/main.dart"
    # Le playbook est écrit pour ce projet, depuis l'intention du rôle composé.
    assert mobile["playbook_origine"] == ORIGINE_PLAYBOOK_GENERE
    assert "Développeur mobile" in generateur.intentions[0]
    # Le modèle a reçu le projet réellement analysé, sous le cadre qui porte le registre.
    prompt, systeme = fournisseur.demandes[0]
    assert "Dart" in prompt
    assert systeme == CADRE_COMPOSITION


def test_un_modele_en_panne_fait_retomber_sur_les_regles_et_le_dit(
    tmp_path: Path, atelier: Path, gabarits: ConfigurationAgents
) -> None:
    """③ Une équipe entière perdue parce qu'un quota est épuisé serait pire qu'une
    équipe par règles — pourvu qu'elle se dise telle, avec sa cause."""
    service, projet = _service(tmp_path, atelier, gabarits, _FournisseurEnPanne())

    servi = asyncio.run(service.proposer(projet))

    assert servi["composition"]["origine"] == ORIGINE_COMPOSITION_REGLES
    assert "quota épuisé" in servi["composition"]["raison"]
    assert [r["nom"] for r in servi["roles"]] == ["dev", "tests"]


def test_un_playbook_d_esquisse_qui_ne_s_ecrit_pas_reste_une_esquisse(
    tmp_path: Path, atelier: Path, gabarits: ConfigurationAgents
) -> None:
    """Le repli de #257 garde l'origine du repli : une esquisse ne devient pas un
    « playbook du gabarit » parce que la rédaction a échoué."""
    service, projet = _service(
        tmp_path, atelier, gabarits, _FournisseurEcrit(_json(roles=[ROLE_MOBILE]))
    )

    (mobile,) = asyncio.run(service.proposer(projet))["roles"]

    assert mobile["playbook_origine"] == ORIGINE_PLAYBOOK_ESQUISSE
    assert "hors ligne" in mobile["playbook_raison"]


def test_le_service_ajoute_le_role_demande_avec_son_playbook(
    tmp_path: Path, atelier: Path, gabarits: ConfigurationAgents
) -> None:
    """④ Le critère du ticket, par le service : compris, ajouté, playbook écrit."""
    fournisseur = _FournisseurEcrit(_json(roles=[ROLE_SECURITE], reponse="J'ajoute la sécurité."))
    generateur = _GenerateurEcrit()
    service, projet = _service(tmp_path, atelier, gabarits, fournisseur, generateur)

    servi = asyncio.run(
        service.corriger(projet, "ajoute quelqu'un pour la sécurité", EQUIPE_MOBILE)
    )

    (securite,) = servi["ajouts"]
    assert securite["role"] == "Sécurité applicative"
    assert securite["playbook_origine"] == ORIGINE_PLAYBOOK_GENERE
    assert "Sécurité applicative" in securite["playbook"]
    assert servi["reponse"] == "J'ajoute la sécurité."
    assert "ajoute quelqu'un pour la sécurité" in fournisseur.demandes[0][0]


@pytest.mark.parametrize("demande", ["", "   ", "x" * (DEMANDE_MAX + 1)])
def test_une_demande_vide_ou_trop_longue_est_refusee_avant_tout_appel(
    tmp_path: Path, atelier: Path, gabarits: ConfigurationAgents, demande: str
) -> None:
    fournisseur = _FournisseurEcrit()
    service, projet = _service(tmp_path, atelier, gabarits, fournisseur)

    with pytest.raises(ValueError):
        asyncio.run(service.corriger(projet, demande, EQUIPE_MOBILE))
    assert fournisseur.demandes == []


# --------------------------------------------------------------------------- #
# ④ Par HTTP — l'appel exact de l'étape d'équipe
# --------------------------------------------------------------------------- #


@pytest.fixture()
def client_http(
    tmp_path: Path, atelier: Path, gabarits: ConfigurationAgents
) -> Iterator[tuple[TestClient, _FournisseurEcrit]]:
    fournisseur = _FournisseurEcrit()
    app = create_app(
        bus=InMemoryEventBus(),
        state=ControlTowerState(),
        projets=ServiceProjets(ProjetStore(tmp_path / "depot"), racines_exploration=(atelier,)),
        agents_store=gabarits.agents,
        surcharges=gabarits.surcharges,
        playbooks=gabarits.playbooks,
        permissions=gabarits.permissions,
        mcp=gabarits.mcp,
        capacites=gabarits.capacites,
        generateur_agent=_GenerateurHorsLigne(),
        compositeur_equipe=CompositeurEquipe(provider=fournisseur),
    )
    with TestClient(app) as client:
        yield client, fournisseur


def _declarer_flutter(client: TestClient, atelier: Path) -> str:
    racine = atelier / "pousse"
    (racine / "lib").mkdir(parents=True)
    (racine / "lib" / "main.dart").write_text("void main() {}\n", encoding="utf-8")
    reponse = client.post(
        "/api/projets", json={"nom": "pousse", "racine": str(racine), "origine": "existant"}
    )
    assert reponse.status_code == 201, reponse.text
    return str(reponse.json()["id"])


def test_la_route_de_correction_ajoute_le_role_demande(
    client_http: tuple[TestClient, _FournisseurEcrit], atelier: Path
) -> None:
    client, fournisseur = client_http
    projet = _declarer_flutter(client, atelier)
    fournisseur._reponses.append(_json(roles=[ROLE_SECURITE], reponse="J'ajoute la sécurité."))

    reponse = client.post(
        f"/api/projets/{projet}/equipe/correction",
        json={
            "demande": "ajoute quelqu'un pour la sécurité",
            "equipe": [
                {"nom": "mobile", "role": "Développeur mobile", "retenu": True, "instances": 1}
            ],
        },
    )

    assert reponse.status_code == 200, reponse.text
    servi = reponse.json()
    assert [a["nom"] for a in servi["ajouts"]] == ["securite"]
    assert servi["ajouts"][0]["playbook"].strip()
    assert servi["cree"] is False


def test_la_route_de_correction_refuse_une_demande_vide(
    client_http: tuple[TestClient, _FournisseurEcrit], atelier: Path
) -> None:
    client, _ = client_http
    projet = _declarer_flutter(client, atelier)

    reponse = client.post(
        f"/api/projets/{projet}/equipe/correction", json={"demande": " ", "equipe": []}
    )

    assert reponse.status_code == 422


def test_la_route_de_correction_rend_502_quand_le_modele_ne_repond_pas(
    tmp_path: Path, atelier: Path, gabarits: ConfigurationAgents
) -> None:
    """Une correction ne se replie pas sur des règles : il n'y a pas de règle pour
    « ajoute quelqu'un pour la sécurité ». Elle échoue franchement, et l'écran le dit."""
    app = create_app(
        bus=InMemoryEventBus(),
        state=ControlTowerState(),
        projets=ServiceProjets(ProjetStore(tmp_path / "depot"), racines_exploration=(atelier,)),
        agents_store=gabarits.agents,
        generateur_agent=_GenerateurHorsLigne(),
        compositeur_equipe=CompositeurEquipe(provider=_FournisseurEnPanne()),
    )
    with TestClient(app) as client:
        projet = _declarer_flutter(client, atelier)
        reponse = client.post(
            f"/api/projets/{projet}/equipe/correction",
            json={"demande": "ajoute quelqu'un pour la sécurité", "equipe": []},
        )

    assert reponse.status_code == 502
    assert "quota épuisé" in reponse.text

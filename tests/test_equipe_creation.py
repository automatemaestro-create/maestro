"""L'équipe validée est créée dans le projet — ou rien ne l'est (#1040).

Lot final du chantier #1021 (#1043) — le lot 4 avait livré sans tests, par la
convention de découpage ([docs/10 §5.1](../docs/10-workflow-git.md)).

La proposition ne crée rien (#1039) ; celui-ci crée tout, et c'est **l'ordre**
qui en est la garantie. Trois promesses, et ces tests les prennent une par une :

① **ce qui est créé est ce qui a été montré.** `RoleValide` est délibérément la
   forme que l'API a *servie*, et non une re-dérivation : rejouer la rédaction à
   la validation rendrait un autre playbook, donc ferait créer un agent que
   personne n'a validé. La politique est celle que `RolePropose.politique()` a
   rendue, rapportée telle quelle — seul chemin de cette traduction, et c'est ce
   qui garantit qu'un cran `auto` lu avec sa raison est le cran qui sera écrit ;
② **tout est vérifié avant la première écriture.** Trois dépôts × N rôles
   n'offrent aucune transaction : un seul refus arrête tout, et rien n'est écrit
   — une demi-équipe serait pire qu'un refus. Et `refus_de` rend la liste
   **entière**, parce que corriger une équipe de cinq rôles un refus à la fois
   serait cinq allers-retours ;
③ **ce qui est écrit l'est dans le projet**, jamais au niveau des gabarits, qui
   n'appartiennent à personne.

Ni réseau ni appel modèle : `creer` est synchrone, et c'est justement ce qui la
distingue de `proposer`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from maestro.agents.capacity import CapacityStore
from maestro.agents.configuration import ConfigurationAgents
from maestro.agents.mcp import McpStore
from maestro.agents.permissions import EntreeArbitrage, PermissionStore, PolitiqueOutils
from maestro.agents.playbooks import PlaybookStore
from maestro.agents.rangement import SEGMENT_PROJETS
from maestro.agents.store import AgentStore, SurchargeStore
from maestro.controltower.equipe import EquipeRefusee, ServiceEquipe
from maestro.controltower.projets import ServiceProjets
from maestro.decideur import Decideur
from maestro.equipe import (
    AUCUN_SKILL,
    INSTANCES_MAX_CREEES,
    TITRE_SKILLS,
    EquipeCreee,
    RoleValide,
    SkillRetenu,
    capacite,
    definition,
    playbook_branche,
    refus_de,
)
from maestro.projets.store import ProjetStore


@pytest.fixture(autouse=True)
def _maison_isolee(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un `Path.home()` sous `tmp_path` (#221) — sans quoi `valider_racine`
    refuserait toute racine de projet sous `AppData` sur un poste Windows."""
    maison = tmp_path / "maison"
    maison.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: maison))
    return maison


def _role(nom: str = "dev", **extra) -> RoleValide:
    """Un rôle validé tel que l'écran le rapporte — playbook lu compris."""
    base = {
        "nom": nom,
        "role": "Développeur",
        "competences": ("backend",),
        "playbook": "Tu écris le code de Dépensio.",
        "instances": 1,
        "gabarit": "developpeur",
    }
    return RoleValide(**{**base, **extra})


def _gabarits(tmp_path: Path) -> ConfigurationAgents:
    """Les six dépôts montés côte à côte, au niveau des gabarits."""
    racine = tmp_path / "core"
    return ConfigurationAgents(
        agents=AgentStore(racine / "agents"),
        surcharges=SurchargeStore(racine / "surcharges"),
        playbooks=PlaybookStore(racine / "playbooks"),
        permissions=PermissionStore(racine / "permissions"),
        mcp=McpStore(racine / "mcp"),
        capacites=CapacityStore(racine / "capacite"),
    )


def _service(tmp_path: Path) -> tuple[ServiceEquipe, ConfigurationAgents, str]:
    """Le service, ses dépôts et l'identifiant d'un projet réellement déclaré."""
    depot = ProjetStore(tmp_path / "projets")
    racine = Path.home() / "depensio"
    racine.mkdir(parents=True, exist_ok=True)
    projet = depot.creer("Dépensio", racine)
    gabarits = _gabarits(tmp_path)
    service = ServiceEquipe(ServiceProjets(depot), gabarits, playbooks=False)
    return service, gabarits, projet.id


# --- ② Tout est vérifié avant la première écriture ---------------------------


def test_une_equipe_bien_formee_ne_rencontre_aucun_refus() -> None:
    assert refus_de([_role(), _role("tests", role="QA", competences=("tests",))]) == ()


def test_un_nom_de_gabarit_est_refuse_parce_qu_il_est_reserve() -> None:
    """Le paquet livre un playbook sous chacun des cinq noms du code, et il
    masquerait celui de la fiche : un rôle de projet nommé `developpeur` partirait
    en exécution avec le playbook du code, en silence."""
    (refus,) = refus_de([_role("developpeur")])

    assert refus.nom == "developpeur"
    assert "réservé par le dépôt" in refus.raison


def test_un_nom_deja_pris_dans_le_projet_est_refuse() -> None:
    (refus,) = refus_de([_role()], noms_pris=("dev",))

    assert "renommez le rôle" in refus.raison


def test_un_doublon_dans_l_equipe_validee_est_refuse() -> None:
    """Deux lignes qui écriraient le même fichier : la seconde effacerait la
    première sans que rien ne le dise."""
    (refus,) = refus_de([_role(), _role(role="Autre")])

    assert "figure deux fois" in refus.raison


@pytest.mark.parametrize("instances", [0, -1, INSTANCES_MAX_CREEES + 1])
def test_un_nombre_d_instances_hors_bornes_est_refuse(instances: int) -> None:
    """Le plafond existe pour qu'une faute de frappe ne règle pas une capacité à
    400 ; il se relève dans les écrans de capacité, qui ne sont pas bornés."""
    (refus,) = refus_de([_role(instances=instances)])

    assert f"attendu entre 1 et {INSTANCES_MAX_CREEES}" in refus.raison


def test_un_playbook_vide_est_refuse_avant_d_etre_augmente() -> None:
    """Le dépôt ne peut pas voir ce refus-là : `playbook_branche()` aurait déjà
    posé la section des skills par-dessus, et l'agent arriverait dans le projet
    avec l'inventaire de ses skills pour tout prompt système."""
    (refus,) = refus_de([_role(playbook="   ")])

    assert "playbook vide" in refus.raison


@pytest.mark.parametrize(
    ("champs", "attendu"),
    [
        ({"nom": "Dev Majuscule"}, "nom d'agent invalide"),
        ({"role": "  "}, "rôle vide"),
        ({"competences": ()}, "compétence"),
    ],
)
def test_ce_que_les_depots_refuseraient_est_demande_aux_depots(
    champs: dict, attendu: str
) -> None:
    """Délégué, jamais réécrit : une pré-vérification plus permissive que
    l'écriture ne sert à rien, une plus stricte refuse ce qui passerait."""
    (refus,) = refus_de([_role(**champs)])

    assert attendu in refus.raison


def test_les_refus_sont_rendus_tous_ensemble() -> None:
    blocages = refus_de([_role("developpeur"), _role("x", instances=0), _role(playbook="")])

    assert len(blocages) == 3


# --- ① Ce qui est créé est ce qui a été montré -------------------------------


def test_la_fiche_persistee_porte_le_playbook_augmente_de_ses_skills() -> None:
    role = _role(skills=(SkillRetenu(nom="lancer-les-tests", chemin=".claude/skills/t.md"),))

    fiche = definition(role)

    assert fiche.playbook.startswith("Tu écris le code de Dépensio.")
    assert TITRE_SKILLS in fiche.playbook
    assert ".claude/skills/t.md" in fiche.playbook


def test_la_fiche_ne_pose_ni_modele_ni_fournisseur_ni_effort() -> None:
    """L'analyse d'un projet n'en dit rien, et les poser serait inventer : la
    fiche prend les défauts des exécutants, et se règle ensuite depuis ses
    écrans."""
    fiche = definition(_role())

    assert (fiche.modele, fiche.fournisseur, fiche.effort) == (None, None, None)


def test_un_role_sans_skill_garde_sa_section_et_le_dit() -> None:
    """« Ce rôle n'en a aucun » et « personne n'a rangé ses skills » sont deux
    faits différents, et seul le premier est une décision."""
    assert AUCUN_SKILL in playbook_branche(_role())


def test_la_section_des_skills_se_repose_sans_s_empiler() -> None:
    """Idempotent : une section déjà présente est remplacée. Sans cela, un rôle
    revalidé accumulerait ses inventaires."""
    role = _role(skills=(SkillRetenu(nom="mettre-en-route"),))

    une_fois = playbook_branche(role)
    deux_fois = playbook_branche(_role(playbook=une_fois, skills=role.skills))

    assert deux_fois.count(TITRE_SKILLS) == 1


def test_les_sections_qui_suivent_les_skills_sont_gardees() -> None:
    """Un playbook est un prompt système, pas un fichier qu'on tronque."""
    avec_suite = f"Tu écris.\n\n{TITRE_SKILLS}\n\nvieux\n\n## Garde-fous\n\nrien de destructif.\n"

    repose = playbook_branche(_role(playbook=avec_suite))

    assert "## Garde-fous" in repose
    assert "vieux" not in repose


def test_la_capacite_creee_est_active_et_porte_les_instances_validees() -> None:
    """`actif=True` sans condition — on vient de le recruter ; le créer éteint
    n'aurait aucun sens à l'issue d'une validation d'équipe."""
    reglage = capacite(_role(instances=3))

    assert (reglage.nom, reglage.actif, reglage.instances) == ("dev", True, 3)


# --- ③ Créer, et dans le projet ----------------------------------------------


@pytest.mark.parametrize("origine", ["existant", "nouveau"])
def test_declarer_un_projet_n_instancie_aucun_agent(tmp_path: Path, origine: str) -> None:
    """*« À la création ou import d'un projet, aucun agent défini encore »* — la
    demande du 2026-09-19, prise à l'endroit où elle se vérifie : déclarer un
    projet n'écrit **rien** dans les dépôts de configuration d'agent. La validation
    d'une équipe (#1040) est le seul chemin par lequel un agent y naît.
    """
    depot = ProjetStore(tmp_path / "projets")
    racine = Path.home() / f"projet-{origine}"
    if origine == "existant":
        racine.mkdir(parents=True, exist_ok=True)
    gabarits = _gabarits(tmp_path)

    projet = depot.creer("Neuf", racine, origine=origine)

    cfg = gabarits.pour_projet(projet.id)
    assert cfg.agents.noms() == ()
    assert cfg.capacites.lister() == ()
    assert cfg.permissions.agents() == ()
    assert not (gabarits.agents.racine / SEGMENT_PROJETS).exists()


def test_l_equipe_validee_est_ecrite_dans_le_projet_et_pas_au_gabarit(
    tmp_path: Path,
) -> None:
    service, gabarits, projet_id = _service(tmp_path)
    politique = PolitiqueOutils(ask=(EntreeArbitrage("Bash", Decideur.AUTO),))

    rapport = service.creer(
        projet_id,
        [_role(politique=politique, instances=2)],
        proposition_id="equ-12345678",
    )

    cfg = gabarits.pour_projet(projet_id)
    assert cfg.agents.noms() == ("dev",)
    assert cfg.capacites.lire("dev").instances == 2
    assert cfg.permissions.lire("dev") == politique
    # Les gabarits n'appartiennent à personne : rien n'y est écrit.
    assert gabarits.agents.noms() == ()
    assert rapport["cree"] is True
    assert rapport["proposition_id"] == "equ-12345678"
    assert rapport["instances_total"] == 2


def test_le_cran_valide_est_le_cran_ecrit(tmp_path: Path) -> None:
    """Le seul chemin de la traduction « autorisations proposées → politique » est
    `RolePropose.politique()` : ce qui arrive ici est ce que l'utilisateur a lu."""
    service, gabarits, projet_id = _service(tmp_path)
    politique = PolitiqueOutils(ask=(EntreeArbitrage("Bash", Decideur.AUTO),))

    service.creer(projet_id, [_role(politique=politique)])

    ecrite = gabarits.pour_projet(projet_id).permissions.lire("dev")
    assert ecrite is not None
    assert ecrite.decideur("Bash") is Decideur.AUTO


def test_un_role_sans_politique_n_en_recoit_aucune(tmp_path: Path) -> None:
    """Un fichier de politique vide *serait* une politique (liste `allow` vide =
    ouverte) : poser ce qu'on n'a pas décidé est ce que ce chantier évite."""
    service, gabarits, projet_id = _service(tmp_path)

    service.creer(projet_id, [_role(politique=None)])

    cfg = gabarits.pour_projet(projet_id)
    assert cfg.agents.noms() == ("dev",)
    assert cfg.permissions.lire("dev") is None
    assert not (cfg.permissions.racine / "dev.json").exists()


def test_un_seul_refus_arrete_tout_et_rien_n_est_ecrit(tmp_path: Path) -> None:
    """Le cœur du lot : sans transaction de système de fichiers, une demi-équipe
    serait pire qu'un refus."""
    service, gabarits, projet_id = _service(tmp_path)

    with pytest.raises(EquipeRefusee) as refus:
        service.creer(projet_id, [_role(), _role("infra", instances=0)])

    assert refus.value.motif == "equipe-refusee"
    assert {r.nom for r in refus.value.refus} == {"infra"}
    assert gabarits.pour_projet(projet_id).agents.noms() == ()


def test_un_nom_deja_cree_dans_le_projet_est_refuse_a_la_seconde_validation(
    tmp_path: Path,
) -> None:
    service, _gabarits, projet_id = _service(tmp_path)
    service.creer(projet_id, [_role()])

    with pytest.raises(EquipeRefusee):
        service.creer(projet_id, [_role()])


def test_deux_projets_ne_se_partagent_pas_leur_equipe(tmp_path: Path) -> None:
    service, gabarits, projet_id = _service(tmp_path)
    autre = Path.home() / "autre"
    autre.mkdir(parents=True, exist_ok=True)
    voisin = ProjetStore(tmp_path / "projets").creer("Voisin", autre)

    service.creer(projet_id, [_role()])

    assert gabarits.pour_projet(projet_id).agents.noms() == ("dev",)
    assert gabarits.pour_projet(voisin.id).agents.noms() == ()


def test_le_rapport_de_creation_dit_ce_qui_existe_desormais() -> None:
    """Le pendant du `cree: False` que la proposition sert : la première dit
    qu'elle n'a rien fait, celui-ci dit ce qui a été fait."""
    rapport = EquipeCreee(projet_id="prj-1", proposition_id="equ-1").to_dict()

    assert rapport["cree"] is True
    assert rapport["agents"] == []
    assert rapport["instances_total"] == 0

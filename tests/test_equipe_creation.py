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

import re
from pathlib import Path

import pytest

from maestro.agents.capacity import CapacityStore
from maestro.agents.configuration import ConfigurationAgents
from maestro.agents.mcp import IntegrationMcp, McpStore, ServeurMcp
from maestro.agents.navigateur import ID_INTEGRATION as ID_NAVIGATEUR
from maestro.agents.navigateur import integration_navigateur
from maestro.agents.permissions import EntreeArbitrage, PermissionStore, PolitiqueOutils
from maestro.agents.playbook_du_code import roles_du_code
from maestro.agents.playbooks import PlaybookStore
from maestro.agents.rangement import SEGMENT_PROJETS
from maestro.agents.store import AgentStore, SurchargeStore
from maestro.controltower.equipe import EquipeRefusee, ServiceEquipe
from maestro.controltower.projets import ServiceProjets
from maestro.decideur import Decideur
from maestro.equipe import (
    AUCUN_SKILL,
    AUCUN_SKILL_ECRIT,
    INSTANCES_MAX_CREEES,
    INTRO_SKILLS,
    TITRE_NAVIGATEUR,
    TITRE_SKILLS,
    EquipeCreee,
    RoleValide,
    SkillRetenu,
    capacite,
    definition,
    playbook_branche,
    refus_de,
    sans_commandes_figees,
    skills_constates,
)
from maestro.equipe.creation import (
    TITRES_SKILLS_ANTERIEURS,
    scinder_section_skills,
    section_navigateur,
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


@pytest.mark.parametrize(
    "skills",
    [
        (SkillRetenu(nom="mettre-en-route", chemin=".agents/skills/m/SKILL.md"),),
        (),
    ],
    ids=["avec-skills", "sans-skill"],
)
def test_la_section_des_skills_tutoie_l_agent_comme_son_playbook(skills) -> None:
    """#1404 : « Servez-vous-en » sous un playbook écrit au tutoiement (`_CADRE_GENERATION`)."""
    section = scinder_section_skills(playbook_branche(_role(skills=skills)))[1]
    ecrits = playbook_branche(_role(skills_absents=(SkillRetenu(nom="x"),)))

    for texte in (section, ecrits, AUCUN_SKILL, AUCUN_SKILL_ECRIT):
        assert re.findall(r"\b(vous|votre|vos|vôtres?|servez)\b", texte, re.IGNORECASE) == []


def test_une_section_posee_sous_l_ancien_titre_est_remplacee_pas_empilee() -> None:
    """Une fiche écrite avant #1404 garde son texte jusqu'à son rebranchement — qui la
    remplace, parce que l'ancien titre reste une ancre."""
    (ancien,) = TITRES_SKILLS_ANTERIEURS
    avant = f"Tu écris.\n\n{ancien}\n\nServez-vous-en.\n\n## Garde-fous\n\nrien de destructif.\n"

    repose = playbook_branche(_role(playbook=avant))

    assert ancien not in repose
    assert repose.count(TITRE_SKILLS) == 1
    assert "Servez-vous-en" not in repose
    assert "## Garde-fous" in repose
    assert scinder_section_skills(avant)[1].startswith(ancien)


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
    # Le catalogue **effectif** du projet, et pas seulement ses dépôts : les cinq
    # fiches du code n'y entrent plus (#1042), ce sont des gabarits.
    assert cfg.catalogue() == ()
    assert not (gabarits.agents.racine / SEGMENT_PROJETS).exists()


def test_les_fiches_du_code_restent_consultables_en_gabarits(tmp_path: Path) -> None:
    """« Sans agent » ne veut pas dire « sans matière » : les cinq fiches sont là,
    au niveau gabarit, et c'est d'elles que l'analyse tire les rôles proposés."""
    gabarits = _gabarits(tmp_path)

    assert {fiche.nom for fiche in gabarits.gabarits_du_code()} == set(roles_du_code())
    assert gabarits.catalogue() == ()


def test_le_catalogue_du_projet_est_exactement_l_equipe_validee(tmp_path: Path) -> None:
    """L'autre moitié du critère : après la validation, le projet a une équipe —
    celle-là, et rien d'autre."""
    service, gabarits, projet_id = _service(tmp_path)

    service.creer(projet_id, [_role(), _role("tests", role="QA", competences=("tests",))])

    equipe = gabarits.pour_projet(projet_id).catalogue()
    assert {agent.nom for agent in equipe} == {"dev", "tests"}


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


# --- ④ Ne sont branchés que les skills que le projet porte (#1212) -----------
#
# Relevé du bouclage du 2026-09-22 (passage `20260922-164402`) : sur S3 et S4,
# l'équipe est créée sans que l'outillage soit écrit, et le playbook du `dev`
# nommait `.agents/skills/mettre-en-route/SKILL.md` dans un projet sans
# `.agents/`. Le fil et la route passent tous deux par `ServiceEquipe.creer`
# (`creer_equipe_du_projet`, app.py) : c'est là que ces tests le prennent.

#: Le skill exact du relevé, tel que la proposition le branche.
CHEMIN_METTRE_EN_ROUTE = ".agents/skills/mettre-en-route/SKILL.md"
METTRE_EN_ROUTE = SkillRetenu(
    nom="mettre-en-route", chemin=CHEMIN_METTRE_EN_ROUTE, commandes=("pip install -e .",)
)


def _ecrire_skill(racine: Path, chemin: str) -> None:
    fichier = racine / chemin
    fichier.parent.mkdir(parents=True, exist_ok=True)
    fichier.write_text("---\nname: skill\n---\n", encoding="utf-8")


def _playbook_ecrit(gabarits: ConfigurationAgents, projet_id: str, nom: str = "dev") -> str:
    fiche = gabarits.pour_projet(projet_id).agents.lire(nom)
    assert fiche is not None
    return fiche.playbook


def test_un_skill_recommande_mais_non_ecrit_n_est_pas_branche(tmp_path: Path) -> None:
    """Le relevé, rejoué : projet sans `.agents/`, équipe validée telle que proposée."""
    service, gabarits, projet_id = _service(tmp_path)
    role = _role(skills=(METTRE_EN_ROUTE,))
    # L'échantillon fautif : la section d'avant #1212, posée sans regarder le
    # disque, nomme bien le chemin — c'est ce qui rend l'absence ci-dessous
    # imputable au constat, et non à un texte qui ne l'aurait jamais porté.
    assert CHEMIN_METTRE_EN_ROUTE in playbook_branche(role)

    rapport = service.creer(projet_id, [role])

    playbook = _playbook_ecrit(gabarits, projet_id)
    assert CHEMIN_METTRE_EN_ROUTE not in playbook
    assert ".agents/" not in playbook
    assert AUCUN_SKILL_ECRIT in playbook
    assert rapport["agents"][0]["skills"] == []


def test_un_projet_outille_garde_sa_section_intacte_chemins_compris(tmp_path: Path) -> None:
    """L'autre moitié : l'outillage est écrit, la section est celle d'avant #1212 au
    caractère près — le constat ne retire rien à un projet qui porte ses skills."""
    service, gabarits, projet_id = _service(tmp_path)
    _ecrire_skill(Path.home() / "depensio", CHEMIN_METTRE_EN_ROUTE)
    role = _role(skills=(METTRE_EN_ROUTE,))

    rapport = service.creer(projet_id, [role])

    assert _playbook_ecrit(gabarits, projet_id) == playbook_branche(role)
    assert rapport["agents"][0]["skills"] == ["mettre-en-route"]


def test_seuls_les_skills_ecrits_sont_nommes_quand_l_outillage_est_partiel(
    tmp_path: Path,
) -> None:
    service, gabarits, projet_id = _service(tmp_path)
    ecrit = SkillRetenu(nom="lancer-les-tests", chemin=".agents/skills/lancer-les-tests/SKILL.md")
    _ecrire_skill(Path.home() / "depensio", ecrit.chemin)

    service.creer(projet_id, [_role(skills=(METTRE_EN_ROUTE, ecrit))])

    playbook = _playbook_ecrit(gabarits, projet_id)
    assert ecrit.chemin in playbook
    assert CHEMIN_METTRE_EN_ROUTE not in playbook
    assert AUCUN_SKILL_ECRIT not in playbook


@pytest.mark.parametrize(
    "chemin",
    ["../dehors/SKILL.md", "{dehors}/SKILL.md", ".agents/skills/dossier"],
)
def test_un_chemin_hors_du_projet_ou_qui_n_est_pas_un_fichier_n_est_pas_porte(
    tmp_path: Path, chemin: str
) -> None:
    """Le chemin arrive dans le corps de la requête : un fichier qui existe **hors**
    de la racine n'est pas un skill du projet, et un dossier n'est pas un
    `SKILL.md` que l'agent pourrait lire."""
    service, gabarits, projet_id = _service(tmp_path)
    dehors = Path.home() / "dehors"
    _ecrire_skill(dehors, "SKILL.md")
    (Path.home() / "depensio" / ".agents" / "skills" / "dossier").mkdir(parents=True)
    chemin = chemin.format(dehors=dehors.as_posix())

    service.creer(projet_id, [_role(skills=(SkillRetenu(nom="x", chemin=chemin),))])

    playbook = _playbook_ecrit(gabarits, projet_id)
    assert chemin not in playbook
    assert AUCUN_SKILL_ECRIT in playbook


def test_le_constat_garde_l_ordre_et_ne_branche_pas_un_skill_sans_chemin() -> None:
    """Pur : `skills_constates` ne touche aucun disque, il demande à `porte`."""
    a, b = SkillRetenu(nom="a", chemin="a.md"), SkillRetenu(nom="b", chemin="b.md")
    sans_chemin = SkillRetenu(nom="c")
    demandes: list[str] = []

    def porte(chemin: str) -> bool:
        demandes.append(chemin)
        return True

    constate = skills_constates(_role(skills=(b, sans_chemin, a)), porte)

    assert constate.skills == (b, a)
    assert constate.skills_absents == (sans_chemin,)
    assert demandes == ["b.md", "a.md"]


def test_un_role_a_qui_rien_n_etait_recommande_garde_sa_phrase() -> None:
    """« Rien n'a été recommandé » et « rien n'est écrit » sont deux raisons :
    la seconde ne remplace pas la première là où elle est vraie."""
    constate = skills_constates(_role(), lambda _chemin: False)

    assert AUCUN_SKILL in playbook_branche(constate)
    assert AUCUN_SKILL_ECRIT not in playbook_branche(constate)


# --- ④ Le navigateur de Maestro (#1532) --------------------------------------


def _service_equipe(tmp_path: Path) -> tuple[ServiceEquipe, ConfigurationAgents, str]:
    """Le service, son navigateur fabriqué sans regarder le poste."""
    _, gabarits, projet_id = _service(tmp_path)
    depot = ProjetStore(tmp_path / "projets")
    service = ServiceEquipe(
        ServiceProjets(depot),
        gabarits,
        playbooks=False,
        navigateur=lambda: integration_navigateur(canal="msedge", sorties=tmp_path / "sorties"),
    )
    return service, gabarits, projet_id


def test_un_role_equipe_du_navigateur_le_monte_a_sa_tache_et_son_playbook_le_nomme(
    tmp_path: Path,
) -> None:
    """Le critère du ticket, de la validation au montage : le pool du projet porte le
    navigateur, il s'active pour le rôle qui le reçoit, et l'exécuteur le monte à sa
    tâche (`McpStore.lire`, ce que `_serveurs_mcp` appelle) — sous le nom de Maestro."""
    service, gabarits, projet_id = _service_equipe(tmp_path)

    rapport = service.creer(
        projet_id,
        [_role(navigateur=True), _role("infra", role="Intégration continue", gabarit="devops")],
    )

    assert [a["navigateur"] for a in rapport["agents"]] == [True, False]
    mcp = gabarits.pour_projet(projet_id).mcp
    (integration,) = mcp.pool()
    assert integration.id == ID_NAVIGATEUR
    assert mcp.activations("dev") == (ID_NAVIGATEUR,)
    assert mcp.activations("infra") == ()
    (monte,) = gabarits.mcp.pour_projet(projet_id).lire("dev")
    assert monte.nom == "navigateur"
    assert "--browser=msedge" in monte.args
    assert gabarits.mcp.pour_projet(projet_id).lire("infra") == ()
    # Le playbook le nomme ; celui qui ne l'a pas n'en dit rien.
    playbook = _playbook_ecrit(gabarits, projet_id)
    assert TITRE_NAVIGATEUR in playbook
    assert "`mcp__navigateur__`" in playbook
    assert "N'écris pas ton propre banc" in playbook
    assert TITRE_NAVIGATEUR not in _playbook_ecrit(gabarits, projet_id, "infra")
    # Rien au niveau des gabarits, qui n'appartiennent à personne.
    assert gabarits.mcp.pool() == ()


def test_une_equipe_sans_navigateur_ne_touche_pas_au_pool(tmp_path: Path) -> None:
    fabrique: list[str] = []
    _, gabarits, projet_id = _service(tmp_path)
    service = ServiceEquipe(
        ServiceProjets(ProjetStore(tmp_path / "projets")),
        gabarits,
        playbooks=False,
        navigateur=lambda: fabrique.append("appelé") or integration_navigateur(),
    )

    service.creer(projet_id, [_role()])

    assert fabrique == []
    assert gabarits.pour_projet(projet_id).mcp.pool() == ()
    assert not (gabarits.mcp.racine / SEGMENT_PROJETS / projet_id / "pool.json").exists()


def test_un_navigateur_deja_au_pool_est_repris_tel_quel(tmp_path: Path) -> None:
    """Réglé par la personne depuis les intégrations — ou par une équipe précédente : ce
    qui y est a été décidé par quelqu'un, et la création ne le réécrit pas."""
    service, gabarits, projet_id = _service_equipe(tmp_path)
    mcp = gabarits.pour_projet(projet_id).mcp
    regle = IntegrationMcp(
        id=ID_NAVIGATEUR,
        serveur=ServeurMcp(nom="mon-navigateur", type="stdio", commande="npx", args=("x",)),
    )
    mcp.ecrire_pool([regle])
    mcp.ecrire_activations("dev", ["autre"])  # une activation que l'agent avait déjà

    service.creer(projet_id, [_role(navigateur=True)])

    assert mcp.pool() == (regle,)
    assert mcp.activations("dev") == ("autre", ID_NAVIGATEUR)


def test_un_pool_illisible_refuse_l_equipe_sans_rien_ecrire(tmp_path: Path) -> None:
    """Un rôle à qui l'on a promis le navigateur ne naît pas sans lui — ni la demi-équipe
    qu'un refus après la première fiche laisserait."""
    service, gabarits, projet_id = _service_equipe(tmp_path)
    racine = gabarits.mcp.racine / SEGMENT_PROJETS / projet_id
    racine.mkdir(parents=True)
    (racine / "pool.json").write_text("{pas du json", encoding="utf-8")

    with pytest.raises(EquipeRefusee) as refus:
        service.creer(projet_id, [_role("tests", role="QA"), _role(navigateur=True)])

    assert [r.nom for r in refus.value.refus] == ["dev"]
    assert "navigateur de Maestro" in refus.value.refus[0].raison
    assert gabarits.pour_projet(projet_id).agents.noms() == ()


def test_la_section_du_navigateur_se_repose_et_se_retire_sans_s_empiler() -> None:
    """Idempotente comme celle des skills : reposée, elle remplace ; le navigateur retiré
    en mots, elle part — et la régénération du corps la met de côté avec les skills."""
    une_fois = playbook_branche(_role(navigateur=True))
    deux_fois = playbook_branche(_role(playbook=une_fois, navigateur=True))
    assert deux_fois == une_fois
    assert deux_fois.count(TITRE_NAVIGATEUR) == 1

    retire = playbook_branche(_role(playbook=une_fois, navigateur=False))
    assert TITRE_NAVIGATEUR not in retire
    assert TITRE_SKILLS in retire

    corps, sections = scinder_section_skills(une_fois)
    assert corps == "Tu écris le code de Dépensio."
    assert sections.startswith(TITRE_SKILLS)
    assert TITRE_NAVIGATEUR in sections
    assert section_navigateur() in sections


# --- ⑤ Le skill fait foi : le playbook ne fige aucune commande (#1520) ---------
#
# Réserve R1 du bouclage du 2026-10-14 : chaque playbook listait la commande qu'un
# skill « enveloppe », figée au recrutement. Remplacée ensuite — dite par la personne,
# proposée par la revue d'après le run —, elle changeait dans le skill et restait dans
# le playbook : un agent du run suivant recevait deux commandes pour le même geste.

#: La section des skills telle que la création l'écrivait avant #1520 — l'état des
#: équipes déjà recrutées (S9, passage `20261014-171347`).
SECTION_D_AVANT = (
    f"{TITRE_SKILLS}\n\n"
    "L'outillage du projet t'est transmis en entier ; ceux-ci sont ceux que ton rôle "
    "branche. Sers-t'en plutôt que de réinventer la commande.\n\n"
    "- **construire-le-projet** — `.agents/skills/construire-le-projet/SKILL.md` "
    "(il enveloppe : `python assembler.py`)\n"
    "- **verifier-le-style** — `.agents/skills/verifier-le-style/SKILL.md` "
    "(il enveloppe : `python -m ruff check .`, `python -c \"print(1)\"`)\n"
)


def test_la_section_ne_fige_aucune_commande_le_skill_fait_foi(tmp_path: Path) -> None:
    """Le skill nommé, son chemin à ouvrir — la commande vit dans le skill, et nulle part
    ailleurs : une commande remplacée dans le projet n'a plus de copie à rattraper."""
    service, gabarits, projet_id = _service(tmp_path)
    _ecrire_skill(Path.home() / "depensio", CHEMIN_METTRE_EN_ROUTE)

    service.creer(projet_id, [_role(skills=(METTRE_EN_ROUTE,))])

    playbook = _playbook_ecrit(gabarits, projet_id)
    assert f"**mettre-en-route** — `{CHEMIN_METTRE_EN_ROUTE}`" in playbook
    assert "pip install -e ." not in playbook
    assert INTRO_SKILLS in playbook


def test_un_playbook_d_avant_cesse_de_figer_ses_commandes_et_garde_le_reste() -> None:
    """Le rebranchement d'une équipe déjà créée : la section de Maestro perd ses commandes
    figées — une commande qui porte des parenthèses comprise —, et rien d'autre ne bouge.
    Le corps du playbook, écrit pour le projet et relu par la personne, n'est pas à
    Maestro : ce qu'il cite y reste, et c'est le constat de l'écriture qui le dira."""
    corps = "Tu assembles le carnet. On t'a dit un jour : `python assembler.py`."
    avant = f"{corps}\n\n{SECTION_D_AVANT}\n## Garde-fous\n\nrien de destructif.\n"

    apres = sans_commandes_figees(avant)

    assert "(il enveloppe :" not in apres
    assert "print(1)" not in apres and "ruff check" not in apres
    assert "- **construire-le-projet** — `.agents/skills/construire-le-projet/SKILL.md`\n" in apres
    assert INTRO_SKILLS in apres
    assert apres.startswith(corps)
    assert apres.endswith("## Garde-fous\n\nrien de destructif.\n")
    assert sans_commandes_figees(apres) == apres


@pytest.mark.parametrize(
    "playbook",
    [
        "Tu écris le code.\n",
        playbook_branche(_role()),
        playbook_branche(_role(skills_absents=(METTRE_EN_ROUTE,))),
        playbook_branche(_role(skills=(METTRE_EN_ROUTE,))),
    ],
    ids=["sans-section", "aucun-skill", "aucun-skill-ecrit", "section-d-aujourd-hui"],
)
def test_un_playbook_qui_ne_fige_rien_revient_tel_quel(playbook: str) -> None:
    """Idempotent, et muet là où il n'a rien à faire : rebrancher n'écrit pas pour rien."""
    assert sans_commandes_figees(playbook) == playbook

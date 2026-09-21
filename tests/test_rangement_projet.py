"""Un agent appartient à un projet : rangement et reprise (#1038).

Lot final du chantier #1021 (#1043) — le lot 2 avait livré sans tests, par la
convention de découpage ([docs/10 §5.1](../docs/10-workflow-git.md)).

[docs/37 §2.1](../docs/37-decision-equipe-sur-mesure.md) renverse une décision :
un agent était une ressource du **poste** — définition, playbook, autorisations,
serveurs MCP, capacité et surcharges valaient pour tous les projets à la fois —,
il appartient désormais à **un projet**. Deux mécanismes portent ce renversement,
et ces tests les prennent l'un après l'autre :

① **le rangement** (`maestro.agents.rangement`), écrit une seule fois pour les
   six dépôts. Deux niveaux, un seul sens de lecture : la racine est celle des
   **gabarits**, `<racine>/_projets/<id>/` celle d'un projet, et le projet
   **recouvre** le gabarit fichier par fichier. Ce qu'un projet ne règle pas, il
   en hérite — sans ce repli, ranger les autorisations par projet ferait d'un
   projet neuf un projet « tout permis », *un garde-fou qui saute est pire qu'un
   garde-fou absent* ;
② **l'exception nommée** : l'**existence** d'un agent ne s'hérite pas
   (`AgentStore.herite_du_gabarit = False`). Un réglage a un défaut sensé, pas
   l'appartenance — et c'est par là qu'un projet **naît sans agent** (#1042) ;
③ **la reprise** (`maestro.agents.reprise`), qui emmène dans le projet ce qui
   existait déjà : sans elle, un poste installé avant ce lot verrait ses agents
   devenir des gabarits — l'équipe d'aucun projet — sans que rien ne le dise.
   Elle ne supprime jamais, n'écrase jamais, et **dit ce qu'elle a fait**.

Ni réseau ni API : les six dépôts sont montés sur des dossiers jetables, et la
reprise est appelée directement — ce que le conftest de la suite annonce
(`_neutralise_reprise_agents` : « le mécanisme lui-même s'éprouvera en
l'appelant, jamais en démarrant une app »).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from maestro.agents.capacity import CapaciteAgent, CapacityStore
from maestro.agents.configuration import ConfigurationAgents
from maestro.agents.mcp import McpStore
from maestro.agents.permissions import PermissionStore, PolitiqueOutils
from maestro.agents.playbooks import PlaybookStore
from maestro.agents.rangement import SEGMENT_PROJETS, RangeParProjet, racine_du_projet
from maestro.agents.reprise import projet_cible, reprendre
from maestro.agents.store import (
    AgentDefinition,
    AgentStore,
    SurchargeAgent,
    SurchargeStore,
)
from maestro.projets.store import ProjetStore

PROJET = "prj-depensio"
AUTRE = "prj-autre"

#: Un nom de gabarit du code — le seul genre de nom qu'une surcharge puisse
#: viser (#259). Écrit en toutes lettres : c'est une donnée du dépôt, pas un
#: symbole à importer.
GABARIT = "developpeur"


@pytest.fixture(autouse=True)
def _maison_isolee(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un `Path.home()` sous `tmp_path`, comme dans les tests du socle (#221).

    Indispensable sous Windows : le `tmp_path` de pytest vit sous `AppData`, que
    `valider_racine` interdit à juste titre — sans cette isolation, toute racine
    de projet déclarée ici serait refusée avant même le test.
    """
    maison = tmp_path / "maison"
    maison.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: maison))
    return maison


def _configuration(racine: Path) -> ConfigurationAgents:
    """Les six dépôts montés côte à côte sous `racine`, au niveau des gabarits."""
    return ConfigurationAgents(
        agents=AgentStore(racine / "agents"),
        surcharges=SurchargeStore(racine / "surcharges"),
        playbooks=PlaybookStore(racine / "playbooks"),
        permissions=PermissionStore(racine / "permissions"),
        mcp=McpStore(racine / "mcp"),
        capacites=CapacityStore(racine / "capacite"),
    )


def _fiche(nom: str = "analyste") -> AgentDefinition:
    """Une définition d'agent minimale mais valide pour le dépôt."""
    return AgentDefinition(
        nom=nom,
        role="Analyste",
        competences=("analyse",),
        playbook="Tu analyses.",
    )


def _projets_avec(racine: Path, *noms: str) -> ProjetStore:
    """Un dépôt de projets jetable, `noms` déclarés sur des dossiers réels."""
    depot = ProjetStore(racine / "projets")
    for nom in noms:
        dossier = Path.home() / nom
        dossier.mkdir(parents=True, exist_ok=True)
        depot.creer(nom, dossier)
    return depot


# --- ① Le rangement, écrit une seule fois ------------------------------------


def test_la_racine_d_un_projet_est_le_segment_reserve_puis_son_identifiant(
    tmp_path: Path,
) -> None:
    assert racine_du_projet(tmp_path, PROJET) == tmp_path / SEGMENT_PROJETS / PROJET


def test_le_segment_de_rangement_ne_peut_pas_etre_pris_pour_un_agent() -> None:
    """Le tiret bas n'est pas décoratif : un nom d'agent commence par `[a-z0-9]`
    dans les six dépôts, donc aucun agent ne peut porter ce nom — et il n'y a
    aucun mot à réserver dans six listes différentes."""
    assert SEGMENT_PROJETS.startswith("_")


@pytest.mark.parametrize(
    "douteux", ["../../ailleurs", "prj/../autre", "", "PRJ-MAJUSCULES", "a" * 65]
)
def test_un_identifiant_douteux_ne_sort_jamais_de_la_racine(
    tmp_path: Path, douteux: str
) -> None:
    """Même garde que le `_NOM_AGENT` des six dépôts sur un nom d'agent : un
    identifiant venu de l'extérieur sert de nom de dossier, donc rien de non
    conforme ne doit voyager jusque-là."""
    with pytest.raises(ValueError, match="identifiant de projet invalide"):
        racine_du_projet(tmp_path, douteux)


def test_sans_projet_le_depot_est_rendu_intact(tmp_path: Path) -> None:
    """`None` rend `self`, et non une copie : le niveau des gabarits est un
    niveau, pas un cas particulier — le code d'avant ce lot lit ce qu'il lisait."""
    depot = CapacityStore(tmp_path)

    assert depot.pour_projet(None) is depot
    assert depot.gabarits is None


def test_un_depot_cadre_sur_un_projet_range_sous_le_segment(tmp_path: Path) -> None:
    depot = CapacityStore(tmp_path).pour_projet(PROJET)

    assert depot.racine == tmp_path / SEGMENT_PROJETS / PROJET
    assert depot.gabarits is not None
    assert depot.gabarits.racine == tmp_path


def test_les_reglages_heritent_et_l_existence_n_herite_pas() -> None:
    """L'exception du rangement, prise pour elle-même : cinq dépôts de réglages
    héritent, `AgentStore` non — ce qu'il stocke décide de l'**existence** d'un
    agent, et l'appartenance n'a pas de défaut sensé."""
    assert RangeParProjet.herite_du_gabarit is True
    assert AgentStore.herite_du_gabarit is False
    for depot in (SurchargeStore, PlaybookStore, PermissionStore, McpStore, CapacityStore):
        assert depot.herite_du_gabarit is True, depot.__name__


# --- ① bis Le projet recouvre le gabarit, dépôt par dépôt --------------------


def test_une_capacite_du_gabarit_vaut_dans_le_projet_puis_se_recouvre(
    tmp_path: Path,
) -> None:
    """Le cas qui dit pourquoi le repli existe : un agent **désactivé** au gabarit
    le reste dans un projet qui n'a rien posé. Retomber sur « actif » serait ici
    un garde-fou qui saute, pas un défaut."""
    gabarits = CapacityStore(tmp_path)
    gabarits.ecrire(CapaciteAgent(nom="analyste", actif=False, instances=1))
    projet = gabarits.pour_projet(PROJET)

    assert projet.lire("analyste").actif is False
    assert "analyste" in projet.inactifs()

    projet.ecrire(CapaciteAgent(nom="analyste", actif=True, instances=3))

    assert projet.lire("analyste").instances == 3
    assert gabarits.lire("analyste").actif is False


def test_une_politique_du_gabarit_vaut_dans_le_projet_puis_se_recouvre(
    tmp_path: Path,
) -> None:
    """Sans ce repli, ranger les autorisations par projet ferait d'un projet neuf
    un projet « tout permis » : une politique absente vaut tout permis."""
    gabarits = PermissionStore(tmp_path)
    gabarits.ecrire("analyste", PolitiqueOutils(deny=("Bash",)))
    projet = gabarits.pour_projet(PROJET)

    heritee = projet.lire("analyste")
    assert heritee is not None
    assert heritee.autorise("Bash") is False

    projet.ecrire("analyste", PolitiqueOutils())

    assert projet.lire("analyste") == PolitiqueOutils()
    assert gabarits.lire("analyste") == PolitiqueOutils(deny=("Bash",))


def test_un_playbook_edite_au_gabarit_vaut_dans_le_projet_puis_se_recouvre(
    tmp_path: Path,
) -> None:
    gabarits = PlaybookStore(tmp_path)
    gabarits.ecrire("analyste", "Version du poste.")
    projet = gabarits.pour_projet(PROJET)

    assert projet.prompt_systeme("analyste", "défaut") == "Version du poste."

    projet.ecrire("analyste", "Version du projet.")

    assert projet.prompt_systeme("analyste", "défaut") == "Version du projet."
    assert gabarits.prompt_systeme("analyste", "défaut") == "Version du poste."


def test_une_surcharge_de_reglages_du_gabarit_vaut_dans_le_projet(tmp_path: Path) -> None:
    """La surcharge héritée atteint le catalogue du projet — l'invariant de #259
    dans le nouveau rangement : annuler une surcharge dans un projet le ramène au
    gabarit, pas à rien."""
    gabarits = SurchargeStore(tmp_path)
    gabarits.ecrire(SurchargeAgent(nom=GABARIT, modele="modele-du-poste"))
    projet = gabarits.pour_projet(PROJET)

    assert projet.lire(GABARIT).modele == "modele-du-poste"

    projet.ecrire(SurchargeAgent(nom=GABARIT, modele="modele-du-projet"))

    assert projet.lire(GABARIT).modele == "modele-du-projet"
    assert gabarits.lire(GABARIT).modele == "modele-du-poste"


def test_une_declaration_mcp_du_gabarit_vaut_dans_le_projet(tmp_path: Path) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "analyste.json").write_text(
        json.dumps({"serveurs": [{"nom": "outil", "type": "stdio", "commande": "echo"}]}),
        encoding="utf-8",
    )
    projet = McpStore(tmp_path).pour_projet(PROJET)

    heritees = projet.heritees("analyste")

    assert [serveur.nom for serveur in heritees] == ["outil"]


# --- ② L'existence ne s'hérite pas : un projet naît sans agent ---------------


def test_un_agent_range_au_gabarit_n_est_pas_un_agent_du_projet(tmp_path: Path) -> None:
    """Le cœur de « un projet naît sans agent » (#1042), au niveau du dépôt : une
    définition rangée à la racine est un **gabarit**, pas un membre de l'équipe
    d'un projet. Un projet qu'on vient de déclarer n'en a donc aucun."""
    gabarits = AgentStore(tmp_path)
    gabarits.ecrire(_fiche())
    projet = gabarits.pour_projet(PROJET)

    assert gabarits.noms() == ("analyste",)
    assert projet.noms() == ()
    assert projet.lister() == ()
    assert projet.lire("analyste") is None


def test_un_agent_ecrit_dans_un_projet_reste_dans_ce_projet(tmp_path: Path) -> None:
    gabarits = AgentStore(tmp_path)
    ici = gabarits.pour_projet(PROJET)
    ailleurs = gabarits.pour_projet(AUTRE)

    ici.ecrire(_fiche("dev"))

    assert ici.noms() == ("dev",)
    assert ailleurs.noms() == ()
    assert gabarits.noms() == ()


def test_la_configuration_cadre_les_six_depots_d_un_bloc(tmp_path: Path) -> None:
    """L'appartenance ne se règle pas dépôt par dépôt : la cadrer six fois
    finirait par un dépôt qu'on oublie et une politique venue d'un autre projet."""
    gabarits = _configuration(tmp_path)

    projet = gabarits.pour_projet(PROJET)

    assert projet.projet_id == PROJET
    assert gabarits.projet_id is None
    assert gabarits.pour_projet(None) is gabarits
    for depot in (
        projet.agents,
        projet.surcharges,
        projet.playbooks,
        projet.permissions,
        projet.mcp,
        projet.capacites,
    ):
        assert depot.racine.parent.name == SEGMENT_PROJETS
        assert depot.racine.name == PROJET


# --- ③ La reprise : sans perte, idempotente, et elle dit ce qu'elle a fait ---


def test_un_seul_projet_declare_est_par_construction_celui_qui_les_utilise(
    tmp_path: Path,
) -> None:
    projets = _projets_avec(tmp_path, PROJET)

    cible, motif = projet_cible(projets=projets)

    assert (cible, motif) == (projets.ids()[0], "seul projet déclaré")


def test_sans_projet_declare_il_n_y_a_personne_a_qui_rattacher(tmp_path: Path) -> None:
    cible, motif = projet_cible(projets=ProjetStore(tmp_path / "projets"))

    assert cible is None
    assert motif == "aucun projet déclaré"


def test_plusieurs_projets_ne_se_departagent_pas_tout_seuls(tmp_path: Path) -> None:
    """Choisir au hasard donnerait à un projet les autorisations d'un autre —
    ce que ce jalon existe précisément pour empêcher."""
    projets = _projets_avec(tmp_path, PROJET, AUTRE)

    cible, motif = projet_cible(projets=projets)

    assert cible is None
    assert "plusieurs projets déclarés" in motif


def test_un_projet_demande_l_emporte_et_un_inconnu_ne_range_rien(tmp_path: Path) -> None:
    """Un `projet` passé explicitement est un humain qui a répondu, et une réponse
    vaut mieux qu'une règle — mais on ne range pas sous un identifiant qui ne
    désigne rien."""
    projets = _projets_avec(tmp_path, PROJET, AUTRE)
    declare = projets.ids()[0]

    assert projet_cible(declare, projets=projets) == (declare, "projet demandé")

    cible, motif = projet_cible("prj-inexistant", projets=projets)

    assert cible is None
    assert "projet demandé inconnu" in motif


def test_la_reprise_rattache_les_six_depots_sans_rien_supprimer(tmp_path: Path) -> None:
    projets = _projets_avec(tmp_path, PROJET)
    cible = projets.ids()[0]
    cfg = _configuration(tmp_path)
    cfg.agents.ecrire(_fiche())
    cfg.playbooks.ecrire("analyste", "Playbook du poste.")
    cfg.permissions.ecrire("analyste", PolitiqueOutils(deny=("Bash",)))
    cfg.capacites.ecrire(CapaciteAgent(nom="analyste", actif=False))
    cfg.surcharges.ecrire(SurchargeAgent(nom=GABARIT, modele="modele-du-poste"))

    rapport = reprendre(projets=projets, configuration=cfg)

    assert rapport.projet_id == cible
    assert rapport.nb_reprises == 5
    assert cfg.agents.pour_projet(cible).noms() == ("analyste",)
    assert cfg.playbooks.pour_projet(cible).prompt_systeme("analyste", "x") == (
        "Playbook du poste."
    )
    # « Sans perte » au sens fort : la racine reste le catalogue de gabarits.
    assert cfg.agents.noms() == ("analyste",)


def test_la_reprise_ne_rejoue_pas_ce_qui_est_deja_range(tmp_path: Path) -> None:
    """Son idempotence : un fichier déjà présent côté projet est laissé tel quel
    et rapporté « déjà repris ». La rejouer ne rend pas un second résultat."""
    projets = _projets_avec(tmp_path, PROJET)
    cible = projets.ids()[0]
    cfg = _configuration(tmp_path)
    cfg.agents.ecrire(_fiche())

    premier = reprendre(projets=projets, configuration=cfg)
    cfg.agents.pour_projet(cible).ecrire(
        AgentDefinition(
            nom="analyste", role="Analyste (revu)", competences=("analyse",), playbook="Revu."
        )
    )
    second = reprendre(projets=projets, configuration=cfg)

    assert (premier.nb_reprises, premier.nb_deja) == (1, 0)
    assert (second.nb_reprises, second.nb_deja) == (0, 1)
    fiche = cfg.agents.pour_projet(cible).lire("analyste")
    assert fiche is not None
    assert fiche.role == "Analyste (revu)"


def test_la_reprise_en_controle_rend_le_meme_rapport_sans_rien_ecrire(
    tmp_path: Path,
) -> None:
    projets = _projets_avec(tmp_path, PROJET)
    cible = projets.ids()[0]
    cfg = _configuration(tmp_path)
    cfg.agents.ecrire(_fiche())

    rapport = reprendre(projets=projets, configuration=cfg, check=True)

    assert rapport.nb_reprises == 1
    assert rapport.ecrit is False
    assert "seraient repris" in str(rapport)
    assert cfg.agents.pour_projet(cible).noms() == ()


def test_sans_cible_la_reprise_n_ecrit_rien_et_nomme_le_geste_qui_tranche(
    tmp_path: Path,
) -> None:
    """Le rapport **dit ce qu'elle a fait** — ici : rien, pourquoi, et comment
    trancher. Sans cette phrase, un poste à plusieurs projets resterait sans
    explication."""
    projets = _projets_avec(tmp_path, PROJET, AUTRE)
    cfg = _configuration(tmp_path)
    cfg.agents.ecrire(_fiche())

    rapport = reprendre(projets=projets, configuration=cfg)

    assert rapport.projet_id is None
    assert rapport.nb_reprises == 0
    texte = str(rapport)
    assert "rien rattaché" in texte
    assert "--projet <id>" in texte
    assert not (tmp_path / "agents" / SEGMENT_PROJETS).exists()


def test_la_reprise_ignore_ce_qui_n_est_pas_de_la_configuration(tmp_path: Path) -> None:
    """Le segment de rangement, les fichiers cachés et les `README.md` livrés avec
    le dépôt n'ont rien à faire dans un projet."""
    projets = _projets_avec(tmp_path, PROJET)
    cible = projets.ids()[0]
    cfg = _configuration(tmp_path)
    cfg.agents.ecrire(_fiche())
    (cfg.agents.racine / "README.md").write_text("# Agents\n", encoding="utf-8")
    (cfg.agents.racine / ".gitignore").write_text("*\n", encoding="utf-8")

    reprendre(projets=projets, configuration=cfg)

    arrivee = racine_du_projet(cfg.agents.racine, cible)
    assert sorted(p.name for p in arrivee.iterdir()) == ["analyste.json"]

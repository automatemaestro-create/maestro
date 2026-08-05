"""Tests de l'entité Projet : modèle, racine validée, persistance (ticket #221, EF-35/EF-38).

Socle de la Phase 7 (parent #219) — le reste de la couverture de la phase revient
au lot final #220. Couvre les trois critères d'acceptation :

① le **modèle** (`Projet`, `Vcs`, `Perimetre`) : les champs de docs/24 §2.3, les
   exclusions par défaut (`.git`, `node_modules`, `.env`, `**/secrets/**`), un
   aller-retour `to_dict`/`from_dict` fidèle (`vcs` nul compris) et des
   identifiants engendrés à la forme `prj-<hex>`, distincts d'un tirage à l'autre ;
② la **validation de la racine** (EF-38) : canonicalisation (`..` et liens
   symboliques), refus **motivé** des racines interdites — racine de disque,
   dossier utilisateur nu ou ancêtre, `.ssh`/`AppData`, dépôt Maestro lui-même,
   chemin relatif, dossier absent, fichier — et interdiction d'écrire au-dessus
   de la racine déclarée (`chemin_dans_racine`) ;
③ la **détection du VCS** et le **dépôt** (`ProjetStore`) : un dossier avec `.git`
   rend sa branche et son remote, un dossier sans `.git` reste déclarable
   (`vcs=None`) ; écriture atomique et relecture, l'identifiant du fichier fait
   foi, refus sans rien écrire (id hors slug — pas de traversée de chemin —, nom
   vide, origine inconnue, racine déjà déclarée), `origine="nouveau"` crée le
   dossier, et résolution du dépôt configuré (`MAESTRO_PROJETS_DIR`).

Aucun appel réseau, aucun `git` appelé : les dépôts Git de test sont trois
fichiers écrits à la main, ce que la détection lit précisément.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from maestro.config import Settings
from maestro.projets import (
    EXCLUS_DEFAUT,
    Perimetre,
    Projet,
    ProjetStore,
    RacineRefusee,
    Vcs,
    chemin_dans_racine,
    detecter_vcs,
    nouvel_id,
    valider_racine,
)


@pytest.fixture(autouse=True)
def _maison_isolee(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un dossier utilisateur factice, hors duquel vivent les dossiers de test.

    Indispensable sous Windows : le `tmp_path` de pytest vit sous
    `C:/Users/<moi>/AppData/Local/Temp`, que la validation refuse à raison
    (`chemin-sensible`). Sans cette isolation, tous les tests qui déclarent un
    projet dans `tmp_path` seraient refusés — pour une bonne raison, mais pas
    celle qu'ils mesurent.
    """
    maison = tmp_path / "maison"
    maison.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: maison))
    return maison


@pytest.fixture()
def store(tmp_path: Path) -> ProjetStore:
    """Un dépôt de projets sur un dossier jetable."""
    return ProjetStore(tmp_path / "depot")


@pytest.fixture()
def projet_disque(tmp_path: Path) -> Path:
    """Un dossier de projet plausible sur le disque (non versionné)."""
    racine = tmp_path / "projets" / "depensio"
    racine.mkdir(parents=True)
    return racine


def _lien_dossier(lien: Path, cible: Path) -> None:
    """Pose un lien de dossier, ou saute le test si l'OS le refuse.

    Sous Windows, `symlink_to` exige des droits que n'a pas un poste ordinaire —
    mais la **jonction** (`mklink /J`), elle, ne demande rien et se comporte
    pareil du point de vue de `resolve()`. Sans ce repli, les deux seuls tests
    du vecteur « lien symbolique » (docs/24 §2.5) ne tourneraient jamais là où
    on développe.
    """
    try:
        lien.symlink_to(cible, target_is_directory=True)
        return
    except (OSError, NotImplementedError):
        pass
    if os.name != "nt":  # pragma: no cover - repli propre à Windows
        pytest.skip("liens de dossier indisponibles sur ce poste")
    fait = subprocess.run(  # noqa: S603 - arguments fixes, aucun contenu externe
        ["cmd", "/c", "mklink", "/J", str(lien), str(cible)],
        capture_output=True,
        check=False,
    )
    if fait.returncode != 0 or not lien.exists():  # pragma: no cover - poste verrouillé
        pytest.skip("ni lien symbolique ni jonction disponibles sur ce poste")


def _depot_git(racine: Path, *, branche: str = "main", distant: str | None = None) -> Path:
    """Fabrique un `.git` minimal — ce que `detecter_vcs` lit réellement."""
    git = racine / ".git"
    git.mkdir(parents=True, exist_ok=True)
    (git / "HEAD").write_text(f"ref: refs/heads/{branche}\n", encoding="utf-8")
    config = "[core]\n\trepositoryformatversion = 0\n"
    if distant is not None:
        config += f'[remote "origin"]\n\turl = {distant}\n\tfetch = +refs/heads/*\n'
    (git / "config").write_text(config, encoding="utf-8")
    return git


# --- ① Le modèle : champs, défauts et sérialisation -------------------------


def test_perimetre_exclut_par_defaut_les_gisements_de_secrets() -> None:
    perimetre = Perimetre()
    assert perimetre.inclus == (".",)
    assert perimetre.exclus == EXCLUS_DEFAUT
    assert set(perimetre.exclus) == {".git", "node_modules", ".env", "**/secrets/**"}


def test_projet_se_serialise_a_la_forme_documentee() -> None:
    projet = Projet(
        id="prj-7f3a1c04",
        nom="Dépensio",
        racine="D:/projets/depensio",
        origine="existant",
        vcs=Vcs(type="git", branche_base="main", distant="git@exemple:depensio.git"),
    )
    document = projet.to_dict()
    assert set(document) == {
        "id",
        "nom",
        "racine",
        "origine",
        "vcs",
        "perimetre",
        "cree_le",
        "modifie_le",
    }
    assert document["vcs"] == {
        "type": "git",
        "branche_base": "main",
        "distant": "git@exemple:depensio.git",
    }
    assert document["perimetre"]["exclus"] == list(EXCLUS_DEFAUT)
    assert Projet.from_dict(document) == projet


def test_projet_non_versionne_serialise_un_vcs_nul() -> None:
    projet = Projet(id="prj-1", nom="Notes", racine="D:/notes", vcs=None)
    assert projet.to_dict()["vcs"] is None
    assert projet.versionne is False
    assert Projet.from_dict(projet.to_dict()).vcs is None


def test_perimetre_vide_explicite_est_respecte() -> None:
    # Un `exclus` vide est un choix de l'utilisateur, pas une donnée manquante.
    perimetre = Perimetre.from_dict({"inclus": ["src"], "exclus": []})
    assert perimetre.inclus == ("src",)
    assert perimetre.exclus == ()


def test_identifiants_engendres_prefixes_et_distincts() -> None:
    ids = {nouvel_id() for _ in range(50)}
    assert len(ids) == 50
    assert all(id_.startswith("prj-") for id_ in ids)


# --- ② La racine : canonicalisation, interdits motivés, écriture bornée -----


def test_racine_canonicalisee_avant_stockage(projet_disque: Path) -> None:
    detourne = projet_disque / "sous" / ".." / "."
    (projet_disque / "sous").mkdir()
    assert valider_racine(detourne) == projet_disque.resolve()


def test_racine_relative_refusee() -> None:
    with pytest.raises(RacineRefusee) as refus:
        valider_racine("projets/depensio")
    assert refus.value.motif == "chemin-relatif"


def test_racine_de_disque_refusee(tmp_path: Path) -> None:
    with pytest.raises(RacineRefusee) as refus:
        valider_racine(Path(tmp_path.resolve().anchor))
    assert refus.value.motif == "racine-de-disque"


def test_dossier_utilisateur_nu_refuse(_maison_isolee: Path) -> None:
    maison = _maison_isolee
    with pytest.raises(RacineRefusee) as refus:
        valider_racine(maison)
    assert refus.value.motif == "dossier-utilisateur-nu"
    # Mais un sous-dossier de projet, lui, passe.
    (maison / "code").mkdir()
    assert valider_racine(maison / "code") == (maison / "code").resolve()


def test_ancetre_du_dossier_utilisateur_refuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Le cas « C:\\Users » : ni racine de disque, ni dossier utilisateur nu.
    utilisateurs = tmp_path / "Users"
    maison = utilisateurs / "sam"
    maison.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: maison))
    with pytest.raises(RacineRefusee) as refus:
        valider_racine(utilisateurs)
    assert refus.value.motif == "au-dessus-du-dossier-utilisateur"


@pytest.mark.parametrize("sensible", [".ssh", ".gnupg", ".aws", "AppData", "Library"])
def test_chemins_sensibles_du_profil_refuses(_maison_isolee: Path, sensible: str) -> None:
    cible = _maison_isolee / sensible / "quelque-part"
    cible.mkdir(parents=True)
    with pytest.raises(RacineRefusee) as refus:
        valider_racine(cible)
    assert refus.value.motif == "chemin-sensible"


def test_lien_symbolique_vers_un_interdit_refuse(tmp_path: Path, _maison_isolee: Path) -> None:
    (_maison_isolee / ".ssh").mkdir(parents=True)
    piege = tmp_path / "projet-innocent"
    _lien_dossier(piege, _maison_isolee / ".ssh")
    with pytest.raises(RacineRefusee) as refus:
        valider_racine(piege)
    assert refus.value.motif == "chemin-sensible"


@pytest.mark.skipif(os.name != "nt", reason="préfixes de chemin propres à Windows")
def test_prefixe_windows_ne_contourne_pas_les_interdits(_maison_isolee: Path) -> None:
    # `\\?\C:\...` désigne le même dossier que `C:\...` mais ne se compare pas à
    # lui : laisser passer cette écriture désactiverait toute la liste.
    (_maison_isolee / ".ssh").mkdir()
    for ecriture in [
        f"\\\\?\\{_maison_isolee}\\.ssh",
        f"//?/{_maison_isolee}/.ssh",
    ]:
        with pytest.raises(RacineRefusee) as refus:
            valider_racine(ecriture)
        assert refus.value.motif == "chemin-sensible"


@pytest.mark.skipif(os.name != "nt", reason="partages réseau propres à Windows")
def test_partage_reseau_refuse() -> None:
    with pytest.raises(RacineRefusee) as refus:
        valider_racine("\\\\localhost\\C$\\projets")
    assert refus.value.motif == "ancre-non-standard"


def test_depot_maestro_refuse() -> None:
    depot = Path(__file__).resolve().parents[1]
    with pytest.raises(RacineRefusee) as refus:
        valider_racine(depot)
    assert refus.value.motif == "depot-maestro"
    with pytest.raises(RacineRefusee) as refus:
        valider_racine(depot / "maestro")
    assert refus.value.motif == "depot-maestro"
    with pytest.raises(RacineRefusee) as refus:
        valider_racine(depot.parent)
    assert refus.value.motif == "au-dessus-du-depot-maestro"


def test_dossier_absent_refuse_sauf_creation_demandee(tmp_path: Path) -> None:
    neuf = tmp_path / "projets" / "tout-neuf"
    with pytest.raises(RacineRefusee) as refus:
        valider_racine(neuf)
    assert refus.value.motif == "dossier-absent"
    assert not neuf.exists()

    assert valider_racine(neuf, creer=True) == neuf.resolve()
    assert neuf.is_dir()


def test_fichier_refuse_comme_racine(tmp_path: Path) -> None:
    fichier = tmp_path / "notes.txt"
    fichier.write_text("x", encoding="utf-8")
    with pytest.raises(RacineRefusee) as refus:
        valider_racine(fichier)
    assert refus.value.motif == "pas-un-dossier"


def test_refus_porte_toujours_un_motif_et_le_chemin(tmp_path: Path) -> None:
    with pytest.raises(RacineRefusee) as refus:
        valider_racine(tmp_path / "absent")
    assert refus.value.motif
    assert str(tmp_path) in str(refus.value)
    assert isinstance(refus.value, ValueError)


def test_ecriture_au_dessus_de_la_racine_refusee(projet_disque: Path) -> None:
    assert chemin_dans_racine(projet_disque, "src/app.py") == (
        projet_disque / "src" / "app.py"
    ).resolve()
    for evasion in ["../voisin", "../../etc/passwd", str(projet_disque.parent)]:
        with pytest.raises(RacineRefusee) as refus:
            chemin_dans_racine(projet_disque, evasion)
        assert refus.value.motif == "hors-racine"


def test_lien_symbolique_sortant_du_projet_refuse(projet_disque: Path) -> None:
    dehors = projet_disque.parent / "dehors"
    dehors.mkdir()
    _lien_dossier(projet_disque / "fuite", dehors)
    with pytest.raises(RacineRefusee) as refus:
        chemin_dans_racine(projet_disque, "fuite")
    assert refus.value.motif == "hors-racine"


# --- ③ La détection du VCS et le dépôt --------------------------------------


def test_dossier_sans_git_reste_declarable(projet_disque: Path, store: ProjetStore) -> None:
    assert detecter_vcs(projet_disque) is None
    projet = store.creer("Notes", projet_disque)
    assert projet.vcs is None
    assert projet.versionne is False


def test_depot_git_detecte_avec_branche_et_distant(projet_disque: Path) -> None:
    _depot_git(projet_disque, branche="develop", distant="git@exemple:depensio.git")
    vcs = detecter_vcs(projet_disque)
    assert vcs == Vcs(type="git", branche_base="develop", distant="git@exemple:depensio.git")


def test_depot_git_sans_remote_reste_un_depot(projet_disque: Path) -> None:
    _depot_git(projet_disque, distant=None)
    vcs = detecter_vcs(projet_disque)
    assert vcs is not None
    assert vcs.branche_base == "main"
    assert vcs.distant is None


def test_head_detache_ne_rend_pas_de_branche(projet_disque: Path) -> None:
    git = _depot_git(projet_disque)
    (git / "HEAD").write_text("9f1c2b3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b\n", encoding="utf-8")
    vcs = detecter_vcs(projet_disque)
    assert vcs is not None
    assert vcs.branche_base == ""


def test_worktree_git_suit_son_fichier_gitdir(tmp_path: Path, projet_disque: Path) -> None:
    # Un worktree porte un `.git` FICHIER pointant le dépôt commun.
    principal = tmp_path / "principal"
    principal.mkdir()
    git = _depot_git(principal, distant="git@exemple:depensio.git")
    interne = git / "worktrees" / "wt"
    interne.mkdir(parents=True)
    (interne / "HEAD").write_text("ref: refs/heads/feat/221\n", encoding="utf-8")
    (interne / "commondir").write_text("../..\n", encoding="utf-8")
    (projet_disque / ".git").write_text(f"gitdir: {interne}\n", encoding="utf-8")

    vcs = detecter_vcs(projet_disque)
    assert vcs == Vcs(type="git", branche_base="feat/221", distant="git@exemple:depensio.git")


def test_git_incomplet_n_est_pas_un_depot(projet_disque: Path) -> None:
    # Un `.git` résiduel (clone avorté) enverrait #224 tenter un worktree Git.
    (projet_disque / ".git").mkdir()
    assert detecter_vcs(projet_disque) is None
    (projet_disque / ".git" / "notes.txt").write_text("x", encoding="utf-8")
    assert detecter_vcs(projet_disque) is None


def test_config_git_commente_et_quote_rend_l_url_propre(projet_disque: Path) -> None:
    git = _depot_git(projet_disque)
    (git / "config").write_text(
        '[remote "origin"] ; le remote de prod\n\turl = "git@exemple:depensio.git" # prod\n',
        encoding="utf-8",
    )
    vcs = detecter_vcs(projet_disque)
    assert vcs is not None
    assert vcs.distant == "git@exemple:depensio.git"


def test_perimetre_relu_depuis_une_chaine_reste_un_motif(store: ProjetStore) -> None:
    # Un `.env` édité à la main ne doit pas s'éclater en caractères, sous peine
    # de faire disparaître l'exclusion qu'il pose.
    assert Perimetre.from_dict({"exclus": ".env"}).exclus == (".env",)


def test_depot_illisible_n_empeche_pas_de_declarer(
    store: ProjetStore, projet_disque: Path
) -> None:
    store.racine.mkdir(parents=True, exist_ok=True)
    (store.racine / "prj-casse.json").write_text("{ pas du json", encoding="utf-8")
    projet = store.creer("Dépensio", projet_disque)
    assert store.par_racine(projet_disque) == projet


def test_creation_persiste_et_relit_le_projet(store: ProjetStore, projet_disque: Path) -> None:
    _depot_git(projet_disque, distant="git@exemple:depensio.git")
    projet = store.creer("Dépensio", projet_disque)

    assert projet.id.startswith("prj-")
    assert projet.racine == projet_disque.resolve().as_posix()
    assert projet.cree_le and projet.modifie_le
    assert projet.perimetre.exclus == EXCLUS_DEFAUT

    relu = store.lire(projet.id)
    assert relu == projet
    assert store.lister() == (projet,)
    assert store.par_racine(projet_disque) == projet

    # Le fichier stocké est de l'UTF-8 lisible, pas de l'échappement.
    brut = (store.racine / f"{projet.id}.json").read_text(encoding="utf-8")
    assert "Dépensio" in brut
    assert json.loads(brut)["vcs"]["type"] == "git"


def test_origine_nouveau_cree_le_dossier(store: ProjetStore, tmp_path: Path) -> None:
    cible = tmp_path / "projets" / "tout-neuf"
    projet = store.creer("Tout neuf", cible, origine="nouveau")
    assert cible.is_dir()
    assert projet.origine == "nouveau"
    assert projet.vcs is None


def test_origine_existant_exige_le_dossier(store: ProjetStore, tmp_path: Path) -> None:
    with pytest.raises(RacineRefusee) as refus:
        store.creer("Absent", tmp_path / "nulle-part")
    assert refus.value.motif == "dossier-absent"
    assert not store.ids()


def test_racine_interdite_refusee_sans_rien_ecrire(store: ProjetStore, tmp_path: Path) -> None:
    with pytest.raises(RacineRefusee):
        store.creer("Le disque entier", Path(tmp_path.resolve().anchor))
    assert not store.racine.exists() or not list(store.racine.iterdir())


def test_deux_projets_ne_partagent_pas_une_racine(
    store: ProjetStore, projet_disque: Path
) -> None:
    store.creer("Dépensio", projet_disque)
    with pytest.raises(ValueError, match="déjà déclarée"):
        store.creer("Dépensio (bis)", projet_disque)
    assert len(store.ids()) == 1


def test_reecriture_du_meme_projet_conserve_sa_date_de_creation(
    store: ProjetStore, projet_disque: Path
) -> None:
    projet = store.creer("Dépensio", projet_disque)
    renomme = store.ecrire(replace(projet, nom="Dépensio v2"))
    assert renomme.nom == "Dépensio v2"
    assert renomme.cree_le == projet.cree_le
    assert len(store.ids()) == 1


def test_identifiant_hors_slug_refuse_sans_traversee_de_chemin(store: ProjetStore) -> None:
    for vilain in ["../evasion", "prj/../../etc", "PRJ-MAJ", ""]:
        with pytest.raises(ValueError, match="identifiant de projet invalide"):
            store.lire(vilain)
    assert not store.racine.exists()


def test_projet_invalide_refuse(store: ProjetStore, projet_disque: Path) -> None:
    with pytest.raises(ValueError, match="nom vide"):
        store.ecrire(Projet(id="prj-1", nom="   ", racine=str(projet_disque)))
    with pytest.raises(ValueError, match="origine invalide"):
        store.ecrire(
            Projet(id="prj-1", nom="X", racine=str(projet_disque), origine="importe")
        )
    with pytest.raises(ValueError, match="origine invalide"):
        store.creer("X", projet_disque, origine="importe")
    assert not store.ids()


def test_le_nom_de_fichier_fait_foi_sur_l_identifiant(
    store: ProjetStore, projet_disque: Path
) -> None:
    projet = store.creer("Dépensio", projet_disque)
    chemin = store.racine / f"{projet.id}.json"
    document = json.loads(chemin.read_text(encoding="utf-8"))
    document["id"] = "prj-menteur"
    chemin.write_text(json.dumps(document), encoding="utf-8")
    relu = store.lire(projet.id)
    assert relu is not None
    assert relu.id == projet.id


def test_suppression_oublie_le_projet_sans_toucher_au_disque(
    store: ProjetStore, projet_disque: Path
) -> None:
    projet = store.creer("Dépensio", projet_disque)
    assert store.supprimer(projet.id) is True
    assert store.supprimer(projet.id) is False
    assert store.lire(projet.id) is None
    # Le dossier de l'utilisateur, lui, est intact.
    assert projet_disque.is_dir()


def test_depot_inexistant_liste_sans_broncher(tmp_path: Path) -> None:
    store = ProjetStore(tmp_path / "jamais-cree")
    assert store.ids() == ()
    assert store.lister() == ()
    assert store.lire("prj-inconnu") is None
    assert store.par_racine(tmp_path) is None


def test_le_depot_configure_vient_de_l_environnement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAESTRO_PROJETS_DIR", str(tmp_path / "ailleurs"))
    assert ProjetStore.default(Settings.from_env()).racine == tmp_path / "ailleurs"

    monkeypatch.delenv("MAESTRO_PROJETS_DIR", raising=False)
    defaut = ProjetStore.default(Settings.from_env()).racine
    assert defaut.name == "projets"
    assert defaut.parent.name == "core"

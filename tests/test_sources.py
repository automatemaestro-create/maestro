"""Sources d'un objectif : modèle, résolution et garde-fous d'ingestion (#315, EF-39).

Le lot 1 de la Phase 8 diffère ses tests au lot final (#323) **sauf ceux-ci** :
les **refus**. Racine interdite, évasion par `..`, schéma d'URL, nom de fichier
qui est un chemin, dépassement de plafond — ce sont des garde-fous de sécurité
et de coût, ils se testent avec leur code, jamais huit lots plus tard.

Trois choses sont vérifiées, dans cet ordre :

1. la **forme** se relit sans se rejuger — un journal durable rejoué après un
   durcissement des plafonds doit rester lisible ;
2. la **résolution** refuse **avec son motif**, type par type ;
3. les sources **rejoignent le run** — événement, projection, résumé —, et un
   lancement qui n'en déclare aucune rend exactement la vue d'avant ce lot.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from maestro.controltower.events import EVENEMENT_EXECUTION_STATUT, Event
from maestro.controltower.state import EXECUTION_TERMINEE, ControlTowerState
from maestro.engine.guardrails import GardeFousIngestion
from maestro.sources import (
    TYPE_DOSSIER,
    TYPE_FICHIER,
    TYPE_URL,
    Source,
    SourceRefusee,
    emplacement_ingestion,
    resoudre_sources,
    sources_depuis,
    sources_en_liste,
)
from maestro.sources.resolution import LONGUEUR_MAX_ID_RUN, racine_ingestion

RUN = "a1b2c3d4e5f6"


@pytest.fixture(autouse=True)
def _maison_isolee(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un dossier utilisateur factice, hors duquel vivent les dossiers de test.

    Reprise de `tests/test_projets.py`, et indispensable pour la même raison :
    sous Windows le `tmp_path` de pytest vit sous
    `C:/Users/<moi>/AppData/Local/Temp`, que la validation de racine refuse **à
    raison** (`chemin-sensible`). Sans cette isolation, les dossiers de
    référence seraient tous refusés — pour une bonne raison, mais pas celle que
    le test mesure.
    """
    maison = tmp_path / "maison"
    maison.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: maison))
    return maison


@pytest.fixture(autouse=True)
def _ingestion_jetable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """L'emplacement d'ingestion pointé sur un dossier jetable, jamais `core/`."""
    racine = tmp_path / "ingestion"
    monkeypatch.setenv("MAESTRO_INGESTION_DIR", str(racine))
    return racine


@pytest.fixture()
def dossier_refs(tmp_path: Path) -> Path:
    """Un dossier de références plausible : hors maison, hors zone interdite."""
    refs = tmp_path / "refs" / "maquettes"
    refs.mkdir(parents=True)
    return refs


def _refus(sources: object, **extra: object) -> SourceRefusee:
    """Résout `sources` en exigeant un refus, et rend l'exception pour l'inspecter."""
    with pytest.raises(SourceRefusee) as capture:
        resoudre_sources(sources, run_id=RUN, **extra)  # type: ignore[arg-type]
    return capture.value


# --- ① La forme : sérialisation, et relecture qui ne rejuge rien -------------


def test_aller_retour_json_d_une_source() -> None:
    """`to_dict`/`from_dict` conservent la source à l'identique."""
    source = Source(
        type=TYPE_FICHIER, nom="CDC-v2.docx", chemin="/ing/run/CDC-v2.docx", taille=184320
    )
    assert Source.from_dict(source.to_dict()) == source


def test_la_relecture_ne_rejuge_pas_une_source_deja_acceptee() -> None:
    """Une source relue est reconstruite telle quelle, même hors bornes du jour.

    C'est le pendant exact du refus : on refuse **une fois**, à la saisie, là où
    quelqu'un peut encore corriger. Rejuger au rejeu rendrait illisible un run
    passé le jour où l'on resserre un plafond — et un run passé ne se réécrit pas.
    """
    ancienne = {"type": TYPE_URL, "valeur": "ftp://ancien/spec", "taille": 10**12}
    relue = Source.from_dict(ancienne)
    assert relue.valeur == "ftp://ancien/spec"
    assert relue.taille == 10**12


def test_les_entrees_illisibles_sont_ecartees_une_a_une() -> None:
    """`sources_depuis` écarte ce qui n'apprend rien sans perdre le reste."""
    lues = sources_depuis(
        [{"type": TYPE_URL, "valeur": "https://a.test"}, "bruit", {}, {"type": "", "nom": "x"}]
    )
    assert [s.valeur for s in lues] == ["https://a.test"]


def test_une_chaine_n_est_pas_une_liste_de_sources() -> None:
    """Une chaîne est une `Sequence` : la lire caractère par caractère serait absurde."""
    assert sources_depuis("https://a.test") == []
    assert sources_en_liste(None) == []


# --- ② Résolution d'une URL : http(s), et rien d'autre -----------------------


def test_une_url_http_est_acceptee_telle_quelle() -> None:
    sources = resoudre_sources(
        [{"type": TYPE_URL, "nom": "Spec", "valeur": "https://exemple.test/spec"}], run_id=RUN
    )
    assert sources == (
        Source(type=TYPE_URL, nom="Spec", valeur="https://exemple.test/spec", lecture_seule=True),
    )


@pytest.mark.parametrize(
    "valeur",
    [
        "file:///etc/passwd",  # lirait le disque par une porte non contrôlée
        "javascript:alert(1)",  # n'est pas une adresse
        "../spec.md",  # ni schéma, ni hôte
        "https://exemple.test/a b",  # espace : jamais une URL telle quelle
    ],
)
def test_une_url_hors_http_est_refusee(valeur: str) -> None:
    """Le filtre est celui du contrat #183 (`SCHEMAS_URL`), pas une seconde liste."""
    refus = _refus([{"type": TYPE_URL, "valeur": valeur}])
    assert refus.motif == "url-non-suivable"
    assert refus.index == 0


def test_une_url_absente_ou_demesuree_est_refusee() -> None:
    assert _refus([{"type": TYPE_URL, "valeur": "  "}]).motif == "url-absente"
    longue = "https://exemple.test/" + "a" * 3000
    assert _refus([{"type": TYPE_URL, "valeur": longue}]).motif == "url-trop-longue"


# --- ③ Résolution d'un dossier : la validation de racine, et rien de neuf ----


def test_un_dossier_de_references_est_canonicalise_et_en_lecture_seule(
    dossier_refs: Path,
) -> None:
    """Le chemin stocké est le **résolu**, jamais la saisie — et l'écriture est barrée."""
    detourne = str(dossier_refs / ".." / dossier_refs.name)
    sources = resoudre_sources(
        [{"type": TYPE_DOSSIER, "chemin": detourne, "lecture_seule": False}], run_id=RUN
    )
    assert sources[0].chemin == str(dossier_refs.resolve())
    assert sources[0].nom == dossier_refs.name
    # Forcée : un dossier de références déclaré en écriture serait un projet
    # déguisé, qui n'aurait passé aucun des contrôles de la Phase 7.
    assert sources[0].lecture_seule is True


def test_un_dossier_sensible_est_refuse_avec_le_motif_de_la_validation_de_racine(
    _maison_isolee: Path,
) -> None:
    """Un dossier de références est un chemin du disque : mêmes interdits qu'une racine."""
    secrets = _maison_isolee / ".ssh"
    secrets.mkdir()
    refus = _refus([{"type": TYPE_DOSSIER, "chemin": str(secrets)}])
    # Le motif de `RacineRefusee` est conservé tel quel : c'est un code que l'UI
    # sait déjà traduire, le renommer ferait diverger deux vocabulaires.
    assert refus.motif == "chemin-sensible"


def test_un_detour_par_deux_points_ne_blanchit_pas_un_dossier_interdit(
    _maison_isolee: Path,
) -> None:
    """La canonicalisation a lieu **avant** la comparaison — sinon tout ceci est décoratif."""
    secrets = _maison_isolee / ".ssh"
    secrets.mkdir()
    evasion = str(_maison_isolee / ".ssh" / ".." / ".ssh" / "refs")
    assert _refus([{"type": TYPE_DOSSIER, "chemin": evasion}]).motif == "chemin-sensible"


def test_un_dossier_absent_relatif_ou_manquant_est_refuse(tmp_path: Path) -> None:
    assert _refus([{"type": TYPE_DOSSIER, "chemin": str(tmp_path / "nulle-part")}]).motif == (
        "dossier-absent"
    )
    assert _refus([{"type": TYPE_DOSSIER, "chemin": "refs/maquettes"}]).motif == "chemin-relatif"
    assert _refus([{"type": TYPE_DOSSIER, "chemin": ""}]).motif == "chemin-absent"


# --- ④ Résolution d'un fichier : hors du projet, et sans traversée -----------


def test_un_fichier_atterrit_dans_l_emplacement_du_run(_ingestion_jetable: Path) -> None:
    sources = resoudre_sources(
        [{"type": TYPE_FICHIER, "nom": "CDC-v2.docx", "taille": 184320}], run_id=RUN
    )
    attendu = (_ingestion_jetable / RUN / "CDC-v2.docx").resolve()
    assert Path(sources[0].chemin) == attendu
    assert sources[0].taille == 184320


def test_l_emplacement_d_ingestion_est_refuse_s_il_tombe_dans_le_projet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le défaut est hors projet ; le contrôle, lui, est **actif** — pas décoratif.

    Un `MAESTRO_INGESTION_DIR` posé à l'intérieur d'un projet est refusé, jamais
    obéi : une matière téléversée ne se mêle pas aux fichiers de l'utilisateur.
    """
    projet = tmp_path / "projets" / "depensio"
    projet.mkdir(parents=True)
    monkeypatch.setenv("MAESTRO_INGESTION_DIR", str(projet / "pieces-jointes"))
    refus = _refus(
        [{"type": TYPE_FICHIER, "nom": "note.md", "taille": 10}], racine_projet=projet
    )
    assert refus.motif == "ingestion-dans-le-projet"


def test_un_emplacement_hors_projet_reste_accepte(
    tmp_path: Path, _ingestion_jetable: Path
) -> None:
    """Le pendant du test précédent : c'est bien la **position** qui décide."""
    projet = tmp_path / "projets" / "depensio"
    projet.mkdir(parents=True)
    sources = resoudre_sources(
        [{"type": TYPE_FICHIER, "nom": "note.md", "taille": 10}],
        run_id=RUN,
        racine_projet=projet,
    )
    assert Path(sources[0].chemin) == (_ingestion_jetable / RUN / "note.md").resolve()


@pytest.mark.parametrize(
    "nom",
    [
        "../../evasion.md",  # traversée de répertoire, le classique
        "..",
        "/etc/passwd",
        "C:\\Windows\\system32\\cle.md",
        "note.md:cache",  # flux de données alterné NTFS : un contenu invisible
    ],
)
def test_un_nom_de_fichier_qui_est_un_chemin_est_refuse(nom: str) -> None:
    """Le nom vient du navigateur de l'utilisateur, donc de l'extérieur."""
    refus = _refus([{"type": TYPE_FICHIER, "nom": nom, "taille": 10}])
    assert refus.motif == "nom-invalide"


def test_un_fichier_sans_nom_ou_au_nom_demesure_est_refuse() -> None:
    assert _refus([{"type": TYPE_FICHIER, "nom": "  ", "taille": 10}]).motif == "nom-absent"
    assert _refus([{"type": TYPE_FICHIER, "nom": "n" * 300, "taille": 10}]).motif == (
        "nom-trop-long"
    )


def test_un_fichier_sans_taille_est_refuse() -> None:
    """Une taille inconnue est une taille non plafonnable — donc un plafond contournable."""
    assert _refus([{"type": TYPE_FICHIER, "nom": "a.md"}]).motif == "taille-absente"
    assert _refus([{"type": TYPE_FICHIER, "nom": "a.md", "taille": -1}]).motif == (
        "taille-invalide"
    )
    assert _refus([{"type": TYPE_FICHIER, "nom": "a.md", "taille": "gros"}]).motif == (
        "taille-invalide"
    )


def test_un_run_id_qui_est_un_chemin_est_refuse() -> None:
    """Le `run_id` sert de segment de chemin : rien de non conforme ne remonte d'un cran."""
    with pytest.raises(SourceRefusee) as capture:
        emplacement_ingestion("../autre-run")
    assert capture.value.motif == "run-invalide"


def test_l_emplacement_n_est_cree_que_si_on_le_demande(_ingestion_jetable: Path) -> None:
    """Ce lot ne lit ni n'écrit rien : la création revient au téléversement (#317)."""
    calcule = emplacement_ingestion(RUN)
    assert not calcule.exists()
    assert emplacement_ingestion(RUN, creer=True).is_dir()


# --- ⑤ Les plafonds d'ingestion : actifs par défaut, refusés avec leur motif -


def test_les_plafonds_sont_actifs_par_defaut() -> None:
    """La divergence assumée avec `Guardrails`, dont les plafonds sont inactifs à None.

    Un plafond de dépense absent laisse un run coûter ce qu'il coûte, ce qui se
    voit ; un plafond d'ingestion absent laisse un document entrer intégralement
    dans le contexte, et alors la barre de dépense ment (docs/24 §3.4).
    """
    defauts = GardeFousIngestion()
    assert defauts.taille_max_source_octets
    assert defauts.taille_max_totale_octets
    assert defauts.nb_max_sources


def test_une_source_trop_volumineuse_est_refusee() -> None:
    refus = _refus(
        [{"type": TYPE_FICHIER, "nom": "enorme.pdf", "taille": 20 * 1024 * 1024}],
        garde_fous=GardeFousIngestion(taille_max_source_octets=1024),
    )
    assert refus.motif == "source-trop-volumineuse"
    assert refus.index == 0


def test_le_cumul_des_sources_est_plafonne() -> None:
    """Sans plafond total, cinquante fichiers sous le plafond unitaire passeraient tous."""
    refus = _refus(
        [
            {"type": TYPE_FICHIER, "nom": "a.md", "taille": 600},
            {"type": TYPE_FICHIER, "nom": "b.md", "taille": 600},
        ],
        garde_fous=GardeFousIngestion(taille_max_source_octets=1000, taille_max_totale_octets=1000),
    )
    assert refus.motif == "ingestion-trop-volumineuse"
    # L'index situe la source qui fait déborder : « c'est trop » sans dire où
    # obligerait à tout relire pour savoir quoi retirer.
    assert refus.index == 1


def test_le_nombre_de_sources_est_plafonne() -> None:
    """Vérifié **avant** toute résolution : cinq cents sources ne touchent pas le disque."""
    refus = _refus(
        [{"type": TYPE_URL, "valeur": f"https://a.test/{i}"} for i in range(5)],
        garde_fous=GardeFousIngestion(nb_max_sources=3),
    )
    assert refus.motif == "trop-de-sources"


def test_un_plafond_a_none_est_un_choix_et_non_un_oubli() -> None:
    sources = resoudre_sources(
        [{"type": TYPE_FICHIER, "nom": "enorme.pdf", "taille": 10**9}],
        run_id=RUN,
        garde_fous=GardeFousIngestion(
            taille_max_source_octets=None, taille_max_totale_octets=None
        ),
    )
    assert sources[0].taille == 10**9


def test_un_plafond_nul_ou_negatif_est_refuse_a_la_construction() -> None:
    with pytest.raises(ValueError, match="nb_max_sources"):
        GardeFousIngestion(nb_max_sources=0)
    with pytest.raises(ValueError, match="taille_max_source_octets"):
        GardeFousIngestion(taille_max_source_octets=-1)


# --- ⑥ Le contour : rien à ingérer, ou une déclaration illisible -------------


def test_aucune_source_reste_le_comportement_d_avant_ce_lot() -> None:
    assert resoudre_sources(None, run_id=RUN) == ()
    assert resoudre_sources([], run_id=RUN) == ()


def test_un_type_inconnu_est_refuse_et_non_ignore() -> None:
    """Ignorer serait pire : l'utilisateur croirait avoir joint une matière absente."""
    assert _refus([{"type": "texte", "valeur": "..."}]).motif == "type-inconnu"
    assert _refus([{"nom": "sans-type.md"}]).motif == "type-inconnu"


def test_une_declaration_illisible_est_refusee() -> None:
    assert _refus(["https://a.test"]).motif == "source-illisible"
    assert _refus("https://a.test").motif == "sources-illisibles"


# --- ⑦ L'attachement au run : événement, projection, résumé ------------------


def test_les_sources_survivent_a_l_aller_retour_du_bus() -> None:
    """Elles voyagent en JSON comme le ticket (#187) et le projet (#222)."""
    source = Source(type=TYPE_URL, nom="Spec", valeur="https://exemple.test/spec")
    evenement = Event(type=EVENEMENT_EXECUTION_STATUT, run_id=RUN, sources=[source])
    assert Event.from_json(evenement.to_json()).sources == [source]


def test_un_evenement_sans_sources_n_efface_pas_celles_du_lancement() -> None:
    """`None` dit « je n'en sais rien », pas « il n'y en a plus » — comme `etapes` (#246)."""
    etat = ControlTowerState()
    source = Source(type=TYPE_DOSSIER, nom="maquettes", chemin="/refs/maquettes")
    etat.appliquer(
        Event(
            type=EVENEMENT_EXECUTION_STATUT,
            run_id=RUN,
            titre="Reprends le cahier des charges",
            statut="en_cours",
            sources=[source],
        )
    )
    etat.appliquer(
        Event(type=EVENEMENT_EXECUTION_STATUT, run_id=RUN, statut=EXECUTION_TERMINEE)
    )
    execution = etat.execution(RUN)
    assert execution is not None
    assert execution.resume()["sources"] == [source.to_dict()]


def test_un_run_sans_source_rend_la_vue_d_avant_ce_lot() -> None:
    etat = ControlTowerState()
    etat.appliquer(
        Event(type=EVENEMENT_EXECUTION_STATUT, run_id=RUN, titre="Objectif texte seul")
    )
    execution = etat.execution(RUN)
    assert execution is not None
    assert execution.resume()["sources"] == []


# --- ⑧ Le reste, différé au lot final (#323) ---------------------------------
#
# Ce que #315 avait laissé de côté à raison — ce ne sont pas des refus, donc
# rien qui doive se tester avec son code. Mais ce sont les points par lesquels
# l'ingestion se déplace d'un poste à l'autre (où les fichiers atterrissent) ou
# accepte une entrée qu'aucun écran ne produit (un `Source` déjà résolu).


def test_la_racine_d_ingestion_est_dans_le_depot_sauf_si_l_environnement_la_deplace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Un dossier du dépôt par défaut, déplaçable pour qui range ses données ailleurs."""
    monkeypatch.delenv("MAESTRO_INGESTION_DIR", raising=False)
    defaut = racine_ingestion()
    assert defaut.parts[-2:] == ("core", "ingestion")

    ailleurs = tmp_path / "hors-depot"
    monkeypatch.setenv("MAESTRO_INGESTION_DIR", str(ailleurs))
    assert racine_ingestion() == ailleurs


def test_la_racine_n_est_jamais_creee_par_sa_seule_lecture(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Rien n'est créé tant qu'aucun fichier n'a été téléversé."""
    ailleurs = tmp_path / "jamais-creee"
    monkeypatch.setenv("MAESTRO_INGESTION_DIR", str(ailleurs))
    assert racine_ingestion() == ailleurs
    assert not ailleurs.exists()


@pytest.mark.parametrize(
    "identifiant",
    ["", "a" * (LONGUEUR_MAX_ID_RUN + 1), "run 42", "run/42"],
)
def test_un_identifiant_de_run_hors_forme_est_refuse(identifiant: str) -> None:
    """L'identifiant devient un **nom de dossier** : tout ce qui n'est pas un mot est refusé."""
    with pytest.raises(SourceRefusee) as capture:
        emplacement_ingestion(identifiant)
    assert capture.value.motif == "run-invalide"


def test_une_source_deja_resolue_se_re_resout_a_l_identique(dossier_refs: Path) -> None:
    """Rejouer une résolution ne doit pas être un cas particulier — le journal en dépend."""
    (resolue,) = resoudre_sources(
        [{"type": TYPE_DOSSIER, "chemin": str(dossier_refs)}], run_id=RUN
    )
    assert resoudre_sources([resolue], run_id=RUN) == (resolue,)


@pytest.mark.parametrize("brut", [{"type": TYPE_URL}, 42, object()])
def test_une_liste_de_sources_qui_n_en_est_pas_une_est_refusee(brut: object) -> None:
    """Un mapping ou un entier ne sont pas des *listes* : le refus nomme le tout."""
    assert _refus(brut).motif == "sources-illisibles"


def test_une_source_vide_se_reconnait_comme_telle() -> None:
    """« Vide » = sans type, ou sans le moindre repère — un nom seul suffit à situer.

    Nuance à connaître avant de s'en servir comme d'un contrôle de saisie : ce
    n'en est pas un (`resoudre_sources` l'est), c'est le filtre de **relecture**
    qui écarte une ligne de journal qui n'apprend rien.
    """
    assert Source(type="", nom="Spec", valeur="https://a.test").vide is True
    assert Source(type=TYPE_URL).vide is True
    assert Source(type=TYPE_URL, nom="Rien").vide is False

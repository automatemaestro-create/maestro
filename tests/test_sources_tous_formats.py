"""Les sources d'un brief se lisent quel qu'en soit le format (#1163).

Avant ce ticket, `maestro.sources.extraction` ne lisait que six extensions (`.md`,
`.markdown`, `.txt`, `.text`, `.docx`, `.pdf`) : un cahier des charges en `.json`
ou en `.yaml`, un export `.csv` ou `.xlsx`, une page `.html`, un fichier de code ou
une maquette en image ressortaient « format non géré », et le brief s'écrivait sur
moins de matière que la personne n'en avait donné.

Deux critères, deux blocs :

1. **tout ce qui se lit nourrit le brief** — le texte structuré et le code comme du
   texte, le tableur par son contenu, l'image par un modèle qui la regarde. Chacun
   ressort *lu* au rapport et son contenu entre dans le contexte, encadré comme
   donnée (`contexte_markdown`) ;
2. **ce qui est réellement illisible se nomme avec sa raison** — binaire opaque,
   image trop grosse, image qu'aucun modèle ne peut regarder —, et les bornes déjà
   posées tiennent : octets lus, plafonds de tokens, périmètre d'un dossier, et
   jamais un fichier porteur de secrets dans le contexte d'un modèle.

Le modèle qui regarde une image est **injecté** (`lire_image=`) : aucun test ne
résout le fournisseur du poste (`tests/conftest.py`, #782). La moitié fournisseur —
comment Claude et un endpoint compatible OpenAI reçoivent une image — est éprouvée
dans `tests/test_providers.py` et `tests/test_openai_provider.py`.
"""

from __future__ import annotations

import asyncio
import struct
import zlib
from pathlib import Path

import pytest

from maestro.providers.base import ImageJointe, ModelProvider, UnsupportedCapability
from maestro.sources.extraction import (
    ETAT_IGNORE,
    ETAT_LU,
    ETAT_TRONQUE,
    GardeFousExtraction,
    Lecture,
    RapportLecture,
    contexte_markdown,
    extraire_sources,
)
from maestro.sources.images import (
    MOTIF_VISION_INDISPONIBLE,
    ImageNonVue,
    LecteurImagesModele,
    lire_image_en_apercu,
    type_image,
)
from maestro.sources.modele import TYPE_DOSSIER, TYPE_FICHIER, Source

# --------------------------------------------------------------------------- #
# Fabriques                                                                    #
# --------------------------------------------------------------------------- #


def png_minimal() -> bytes:
    """Un vrai PNG d'un pixel — assez pour qu'un décodeur l'accepte, écrit à la main.

    À la main plutôt que par Pillow : le dépôt n'en dépend pas, et c'est ce qui
    permet d'affirmer que la reconnaissance d'une image repose sur ses **octets**,
    pas sur une bibliothèque.
    """

    def morceau(genre: bytes, donnees: bytes) -> bytes:
        corps = genre + donnees
        return struct.pack(">I", len(donnees)) + corps + struct.pack(">I", zlib.crc32(corps))

    entete = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    pixels = zlib.compress(b"\x00\xff\x00\x00")
    return (
        b"\x89PNG\r\n\x1a\n"
        + morceau(b"IHDR", entete)
        + morceau(b"IDAT", pixels)
        + morceau(b"IEND", b"")
    )


def poser(base: Path, nom: str, contenu: str | bytes) -> Source:
    """Une source `fichier` posée sur le disque, texte ou octets."""
    chemin = base / nom
    chemin.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(contenu, bytes):
        chemin.write_bytes(contenu)
    else:
        chemin.write_text(contenu, encoding="utf-8")
    return Source(type=TYPE_FICHIER, nom=nom, chemin=str(chemin))


def classeur(chemin: Path) -> None:
    """Un `.xlsx` de deux feuilles, écrit par openpyxl — le convertisseur qu'on exerce."""
    openpyxl = pytest.importorskip("openpyxl", reason="convertisseur .xlsx non installé")
    classeur_ = openpyxl.Workbook()
    planning = classeur_.active
    planning.title = "Planning"
    planning.append(["Lot", "Délai"])
    planning.append(["Socle", "2 semaines"])
    budget = classeur_.create_sheet("Budget")
    budget.append(["Poste", "Montant"])
    budget.append(["Hébergement", 1200])
    classeur_.save(str(chemin))


class Regard:
    """Un modèle qui regarde une image — le double de `lire_image`, qui garde ce qu'il a vu."""

    def __init__(self, reponse: str = "Maquette : un bouton « Lancer » en haut à droite.") -> None:
        self.reponse = reponse
        self.vus: list[tuple[bytes, str, str]] = []

    def __call__(self, octets: bytes, type_media: str, nom: str) -> str:
        self.vus.append((octets, type_media, nom))
        return self.reponse


def seule(rapport: RapportLecture) -> Lecture:
    assert len(rapport.lectures) == 1, rapport.synthese()
    return rapport.lectures[0]


# --------------------------------------------------------------------------- #
# Critère 1 — tout ce qui se lit nourrit le brief                               #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("nom", "contenu", "attendu"),
    [
        ("cahier.json", '{"objectif": "livrer le socle", "delai": "2 semaines"}', "le socle"),
        ("cahier.yaml", "objectif: livrer le socle\ndelai: 2 semaines\n", "objectif: livrer"),
        ("export.csv", "lot;delai\nsocle;2 semaines\n", "socle;2 semaines"),
        ("app.py", "def lancer():\n    return 'socle'\n", "def lancer():"),
        ("composant.tsx", "export function Bouton() { return null; }\n", "function Bouton"),
        ("Makefile", "build:\n\tnpm run build\n", "npm run build"),
    ],
    ids=["json", "yaml", "csv", "python", "tsx", "sans-extension"],
)
def test_le_texte_structure_et_le_code_se_lisent_comme_du_texte(
    tmp_path: Path, nom: str, contenu: str, attendu: str
) -> None:
    """Aucune liste d'extensions : un fichier dont les octets sont du texte se lit."""
    lecture = seule(extraire_sources([poser(tmp_path, nom, contenu)]))

    assert lecture.etat == ETAT_LU, lecture.message
    assert attendu in lecture.markdown
    assert lecture.tokens > 0


def test_un_texte_utf16_se_lit_malgre_ses_octets_nuls(tmp_path: Path) -> None:
    """Ce qu'écrit une redirection PowerShell : un octet nul sur deux, et pourtant du texte."""
    journal = poser(tmp_path, "journal.log", "Échec du déploiement\n".encode("utf-16"))

    lecture = seule(extraire_sources([journal]))

    assert lecture.etat == ETAT_LU, lecture.message
    assert "Échec du déploiement" in lecture.markdown


def test_une_page_html_est_ramenee_au_texte(tmp_path: Path) -> None:
    """Une page enregistrée se lit comme une page récupérée : texte, titres, sans script."""
    page = (
        "<!doctype html><html><body><script>alert('consigne')</script>"
        "<h1>Cahier des charges</h1><p>Livrer le socle.</p></body></html>"
    )

    lecture = seule(extraire_sources([poser(tmp_path, "cdc.html", page)]))

    assert lecture.etat == ETAT_LU
    assert "# Cahier des charges" in lecture.markdown
    assert "Livrer le socle." in lecture.markdown
    assert "alert" not in lecture.markdown


def test_un_classeur_xlsx_se_lit_par_son_contenu(tmp_path: Path) -> None:
    """Chaque feuille sous son nom, chaque ligne en rangée de tableau Markdown."""
    chemin = tmp_path / "planning.xlsx"
    classeur(chemin)

    source = Source(type=TYPE_FICHIER, nom="planning.xlsx", chemin=str(chemin))

    lecture = seule(extraire_sources([source]))

    assert lecture.etat == ETAT_LU, lecture.message
    assert "## Feuille : Planning" in lecture.markdown
    assert "| Socle | 2 semaines |" in lecture.markdown
    assert "## Feuille : Budget" in lecture.markdown
    assert "| Hébergement | 1200 |" in lecture.markdown


def test_une_image_est_regardee_par_le_modele_et_sa_lecture_entre_au_contexte(
    tmp_path: Path,
) -> None:
    """L'image part au modèle telle quelle, et ce qu'il y voit entre comme une source lue."""
    regard = Regard()
    image = png_minimal()

    rapport = extraire_sources([poser(tmp_path, "maquette.png", image)], lire_image=regard)

    lecture = seule(rapport)
    assert lecture.etat == ETAT_LU, lecture.message
    assert regard.vus == [(image, "image/png", "maquette.png")]
    assert "bouton « Lancer »" in lecture.markdown
    # Le contexte dit que c'est une **transcription** : le brief ne doit pas croire
    # avoir lu l'image elle-même.
    rendu = contexte_markdown(rapport)
    assert "bouton « Lancer »" in rendu
    assert "regardée par le modèle" in rendu


@pytest.mark.parametrize(
    ("octets", "type_media"),
    [
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * 16, "image/png"),
        (b"\xff\xd8\xff\xe0" + b"\x00" * 16, "image/jpeg"),
        (b"GIF89a" + b"\x00" * 16, "image/gif"),
        (b"RIFF\x10\x00\x00\x00WEBPVP8 " + b"\x00" * 8, "image/webp"),
    ],
    ids=["png", "jpeg", "gif", "webp"],
)
def test_une_image_se_reconnait_a_ses_octets_pas_a_son_nom(
    tmp_path: Path, octets: bytes, type_media: str
) -> None:
    """Le nom vient de l'extérieur ; la signature du fichier, non."""
    assert type_image(octets) == type_media
    regard = Regard()

    capture = poser(tmp_path, "capture-sans-extension", octets)

    lecture = seule(extraire_sources([capture], lire_image=regard))

    assert lecture.etat == ETAT_LU
    assert regard.vus[0][1] == type_media


def test_tous_les_formats_du_ticket_nourrissent_le_contexte(tmp_path: Path) -> None:
    """Le critère entier, joint à un même objectif : chacun parmi les sources lues."""
    chemin_xlsx = tmp_path / "budget.xlsx"
    classeur(chemin_xlsx)
    sources = [
        poser(tmp_path, "cahier.json", '{"objectif": "JSON-LU"}'),
        poser(tmp_path, "cahier.yaml", "objectif: YAML-LU\n"),
        poser(tmp_path, "export.csv", "colonne\nCSV-LU\n"),
        Source(type=TYPE_FICHIER, nom="budget.xlsx", chemin=str(chemin_xlsx)),
        poser(tmp_path, "page.html", "<html><body><p>HTML-LU</p></body></html>"),
        poser(tmp_path, "main.go", 'package main // GO-LU\n'),
        poser(tmp_path, "maquette.png", png_minimal()),
    ]

    rapport = extraire_sources(sources, lire_image=Regard("IMAGE-LUE"))

    assert [lecture.etat for lecture in rapport.lectures] == [ETAT_LU] * len(sources), (
        rapport.synthese()
    )
    assert len(rapport.lues) == len(sources)
    rendu = contexte_markdown(rapport)
    for marque in ("JSON-LU", "YAML-LU", "CSV-LU", "Hébergement", "HTML-LU", "GO-LU", "IMAGE-LUE"):
        assert marque in rendu


# --------------------------------------------------------------------------- #
# Critère 2 — l'illisible se nomme avec sa raison, et les bornes tiennent       #
# --------------------------------------------------------------------------- #


def test_un_binaire_opaque_est_nomme_avec_sa_raison(tmp_path: Path) -> None:
    """Ni texte, ni image, ni document convertible : la ligne dit pourquoi."""
    outil = poser(tmp_path, "outil.exe", b"MZ\x90\x00\x03\x00\x00\x00binaire")

    lecture = seule(extraire_sources([outil]))

    assert lecture.etat == ETAT_IGNORE
    assert lecture.motif == "binaire-opaque"
    assert ".exe" in lecture.message
    assert lecture.markdown == "" and lecture.tokens == 0


def test_sans_modele_qui_voit_une_image_est_nommee_jamais_tue(tmp_path: Path) -> None:
    """Une lecture où aucun modèle n'est branché ne tait pas l'image : elle dit pourquoi."""
    rapport = extraire_sources([poser(tmp_path, "maquette.png", png_minimal())])

    lecture = seule(rapport)
    assert (lecture.etat, lecture.motif) == (ETAT_IGNORE, MOTIF_VISION_INDISPONIBLE)
    assert "maquette.png" in contexte_markdown(rapport)


def test_un_fournisseur_qui_ne_voit_pas_les_images_le_dit(tmp_path: Path) -> None:
    """Agnosticisme (O7) : un fournisseur sans la capacité est nommé, pas contourné."""

    class TexteSeul(ModelProvider):
        name = "texte-seul"

        def supports(self, model: str) -> bool:
            return True

        async def generate(self, prompt, *, model, system_prompt=None, effort=None):
            return "jamais appelé"

    lecteur = LecteurImagesModele(provider=TexteSeul(), modele="m")

    schema = poser(tmp_path, "schema.png", png_minimal())

    lecture = seule(extraire_sources([schema], lire_image=lecteur))

    assert (lecture.etat, lecture.motif) == (ETAT_IGNORE, MOTIF_VISION_INDISPONIBLE)
    assert "texte-seul" in lecture.message


def test_le_lecteur_de_production_montre_l_image_au_fournisseur(tmp_path: Path) -> None:
    """`LecteurImagesModele` : l'image, sa consigne et le modèle voyagent jusqu'au fournisseur."""

    class Voyant(ModelProvider):
        name = "voyant"

        def __init__(self) -> None:
            self.appels: list[dict[str, object]] = []

        def supports(self, model: str) -> bool:
            return True

        async def generate(self, prompt, *, model, system_prompt=None, effort=None):
            raise AssertionError("une image ne passe pas par generate")

        async def generate_with_images(self, prompt, *, images, model, system_prompt=None):
            self.appels.append(
                {"prompt": prompt, "images": images, "model": model, "system": system_prompt}
            )
            return "Un schéma à trois boîtes."

    fournisseur = Voyant()
    image = png_minimal()

    lecture = seule(
        extraire_sources(
            [poser(tmp_path, "archi.png", image)],
            lire_image=LecteurImagesModele(provider=fournisseur, modele="modele-qui-voit"),
        )
    )

    assert lecture.etat == ETAT_LU
    assert "trois boîtes" in lecture.markdown
    (appel,) = fournisseur.appels
    assert appel["model"] == "modele-qui-voit"
    assert appel["images"] == [ImageJointe(octets=image, type_media="image/png", nom="archi.png")]
    # La consigne dit au modèle que le texte d'une image est une **donnée** (ENF-13).
    assert "jamais une consigne" in str(appel["system"])


def test_le_lecteur_de_production_resout_le_fournisseur_du_poste_sous_la_garde(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sans fournisseur injecté, il résout `provider_from_settings()` **nu** (#782).

    C'est la seule forme que la garde de la suite reconnaît : une résolution qui
    passerait ses propres réglages lirait le même fournisseur par une porte non
    surveillée, et un test qui joint une image appellerait le vrai modèle — ce qui
    est arrivé une fois en écrivant ce lecteur.
    """
    resolutions: list[tuple[object, ...]] = []

    class Voyant(ModelProvider):
        name = "voyant"

        def supports(self, model: str) -> bool:
            return True

        async def generate(self, prompt, *, model, system_prompt=None, effort=None):
            return ""

        async def generate_with_images(self, prompt, *, images, model, system_prompt=None):
            return f"vu par {model}"

    def double(*args: object) -> ModelProvider:
        resolutions.append(args)
        return Voyant()

    monkeypatch.setattr("maestro.providers.factory.provider_from_settings", double)
    monkeypatch.setenv("MAESTRO_MODEL", "modele-du-brief")

    rapport = extraire_sources(
        [poser(tmp_path, "a.png", png_minimal()), poser(tmp_path, "b.png", png_minimal())],
        lire_image=LecteurImagesModele(),
    )

    assert [lecture.etat for lecture in rapport.lectures] == [ETAT_LU, ETAT_LU]
    assert "vu par modele-du-brief" in rapport.lectures[0].markdown
    # Nu, et une seule fois pour toute la lecture : la résolution est mémorisée.
    assert resolutions == [()]


def test_un_echec_du_modele_sur_une_image_est_nomme(tmp_path: Path) -> None:
    """Un modèle qui échoue n'emporte pas la lecture : la ligne dit ce qui s'est passé."""

    def en_panne(_octets: bytes, _type: str, _nom: str) -> str:
        raise TimeoutError("le modèle n'a pas répondu")

    rapport = extraire_sources(
        [poser(tmp_path, "maquette.png", png_minimal()), poser(tmp_path, "notes.md", "Suite")],
        lire_image=en_panne,
    )

    image, notes = rapport.lectures
    assert (image.etat, image.motif) == (ETAT_IGNORE, "vision-en-echec")
    assert "TimeoutError" in image.message
    assert notes.etat == ETAT_LU


def test_une_image_trop_grosse_est_nommee_sans_etre_montree(tmp_path: Path) -> None:
    """Une image ne se lit pas en partie : au-delà des octets lus, elle n'est pas envoyée."""
    regard = Regard()
    image = png_minimal() + b"\x00" * 4096

    lecture = seule(
        extraire_sources(
            [poser(tmp_path, "affiche.png", image)],
            garde_fous=GardeFousExtraction(octets_max_lus=1024),
            lire_image=regard,
        )
    )

    assert (lecture.etat, lecture.motif) == (ETAT_IGNORE, "trop-volumineux")
    assert "affiche.png" in lecture.message
    assert regard.vus == []


def test_le_nombre_d_images_montrees_au_modele_est_plafonne(tmp_path: Path) -> None:
    """Chaque image est un appel au modèle avant même le run : le plafond le dit par image."""
    for rang in range(3):
        (tmp_path / "maquettes").mkdir(exist_ok=True)
        (tmp_path / "maquettes" / f"ecran{rang}.png").write_bytes(png_minimal())
    regard = Regard()

    lecture = seule(
        extraire_sources(
            [Source(type=TYPE_DOSSIER, nom="maquettes", chemin=str(tmp_path / "maquettes"))],
            garde_fous=GardeFousExtraction(images_max=2),
            lire_image=regard,
        )
    )

    assert len(regard.vus) == 2
    assert [entree.motif for entree in lecture.entrees] == ["", "", "images-plafond"]
    assert "2 images" in lecture.entrees[2].message


def test_un_texte_trop_gros_reste_coupe_aux_octets_lus(tmp_path: Path) -> None:
    """La borne d'octets s'applique à tout texte, JSON et code compris — jamais chargé entier."""
    lecture = seule(
        extraire_sources(
            [poser(tmp_path, "donnees.json", "[" + "1," * 50_000 + "1]")],
            garde_fous=GardeFousExtraction(octets_max_lus=1_000, tokens_max_source=None),
        )
    )

    assert lecture.etat == ETAT_TRONQUE
    assert lecture.limite == "octets lus"


def test_un_classeur_enorme_est_coupe_en_le_disant(tmp_path: Path) -> None:
    """Un tableur se convertit sous la même borne : au-delà, coupé et annoncé."""
    openpyxl = pytest.importorskip("openpyxl", reason="convertisseur .xlsx non installé")
    classeur_ = openpyxl.Workbook()
    for rang in range(2_000):
        classeur_.active.append([f"ligne {rang}", "x" * 20])
    chemin = tmp_path / "gros.xlsx"
    classeur_.save(str(chemin))

    lecture = seule(
        extraire_sources(
            [Source(type=TYPE_FICHIER, nom="gros.xlsx", chemin=str(chemin))],
            garde_fous=GardeFousExtraction(octets_max_lus=2_000, tokens_max_source=None),
        )
    )

    assert lecture.etat == ETAT_TRONQUE
    assert "octets lus" in lecture.limite
    assert "ligne 1999" not in lecture.markdown


@pytest.mark.parametrize(
    "nom",
    [".env.local", ".npmrc", "cle.pem", "deploiement.key", "credentials"],
)
def test_un_fichier_porteur_de_secrets_n_entre_jamais_au_contexte(tmp_path: Path, nom: str) -> None:
    """Lire tous les formats n'ouvre pas la porte aux secrets : la règle est celle du projet."""
    rapport = extraire_sources([poser(tmp_path, nom, "JETON=valeur-tres-secrete-1163\n")])

    lecture = seule(rapport)
    assert (lecture.etat, lecture.motif) == (ETAT_IGNORE, "secret")
    assert "valeur-tres-secrete-1163" not in contexte_markdown(rapport)


def test_dans_un_dossier_perimetre_et_secrets_tiennent(tmp_path: Path) -> None:
    """Le périmètre écarte sans lister ; un porteur de secrets restant se nomme sans se lire."""
    refs = tmp_path / "refs"
    (refs / "node_modules").mkdir(parents=True)
    (refs / "node_modules" / "paquet.json").write_text('{"x": 1}', encoding="utf-8")
    (refs / ".env").write_text("CLE=exclue-par-le-perimetre", encoding="utf-8")
    (refs / "config.json").write_text('{"port": 8080}', encoding="utf-8")
    (refs / "serveur.pem").write_text("-----BEGIN KEY-----\nsecret-du-pem-1163\n", encoding="utf-8")

    rapport = extraire_sources([Source(type=TYPE_DOSSIER, nom="refs", chemin=str(refs))])

    lecture = seule(rapport)
    etats = {entree.nom: (entree.etat, entree.motif) for entree in lecture.entrees}
    assert etats == {"config.json": (ETAT_LU, ""), "serveur.pem": (ETAT_IGNORE, "secret")}
    rendu = contexte_markdown(rapport)
    assert "secret-du-pem-1163" not in rendu
    assert "exclue-par-le-perimetre" not in rendu


# --------------------------------------------------------------------------- #
# Le lecteur d'images                                                          #
# --------------------------------------------------------------------------- #


def test_l_apercu_ne_montre_pas_l_image_au_modele_et_le_dit(tmp_path: Path) -> None:
    """L'aperçu est gratuit (docs/05 §2.7.3) : il annonce la lecture au lancement sans la payer."""
    maquette = poser(tmp_path, "maquette.png", png_minimal())

    lecture = seule(extraire_sources([maquette], lire_image=lire_image_en_apercu))

    assert (lecture.etat, lecture.motif) == (ETAT_IGNORE, "vue-au-lancement")
    assert "lancement" in lecture.message


def test_une_image_non_vue_porte_son_motif() -> None:
    """Le contrat de l'exception : un code stable, une phrase lisible."""
    refus = ImageNonVue("vision-indisponible", "Aucun modèle ne voit ici.")

    assert (refus.motif, str(refus)) == ("vision-indisponible", "Aucun modèle ne voit ici.")


def test_la_frontiere_refuse_une_image_par_defaut() -> None:
    """Capacité optionnelle : un fournisseur qui ne la surcharge pas la refuse, nommément."""

    class TexteSeul(ModelProvider):
        name = "texte-seul"

        def supports(self, model: str) -> bool:
            return True

        async def generate(self, prompt, *, model, system_prompt=None, effort=None):
            return ""

    with pytest.raises(UnsupportedCapability, match="texte-seul"):
        asyncio.run(
            TexteSeul().generate_with_images(
                "Décris.", images=[ImageJointe(octets=b"x", type_media="image/png")], model="m"
            )
        )

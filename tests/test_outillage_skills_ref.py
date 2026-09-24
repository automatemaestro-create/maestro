"""Les skills générés sont valides au sens de la spécification Agent Skills (#1035).

Le second demi-critère du premier point de #1035 : *des skills générés sont
validés contre la spécification Agent Skills (`skills-ref validate`, ou son
équivalent)*. C'est « son équivalent » qui est retenu ici, et le choix se
justifie plutôt qu'il ne se subit :

- `skills-ref` est la bibliothèque de référence de la spécification
  ([agentskills/agentskills](https://github.com/agentskills/agentskills)), et
  l'appeler demanderait une **dépendance de plus**, installée depuis le réseau,
  dans un job de CI qui n'en a pas besoin. Le dépôt n'a même pas de dépendance
  YAML — `maestro.outillage.contexte` s'en explique — et en ajouter une pour
  deux chaînes de caractères serait le contraire de ce que fait ce dépôt ;
- la validation porte sur **une douzaine de règles écrites**, que
  `_valider_skill` applique et cite une à une. Elles viennent de la
  [spécification](https://agentskills.io/specification), relue le 2026-09-20, et
  sont exactement celles que docs/38 §2.1 avait retenues — *ce qui n'est pas
  vérifié n'est pas cité*.

**Le validateur est indépendant du code de production**, et c'est la condition
qui donne sa valeur à la suite : lire le frontmatter avec le lecteur de
`maestro.outillage.contexte` reviendrait à valider un texte avec l'outil qui l'a
écrit, et deux erreurs symétriques s'annuleraient sans que personne le voie.

**Et il est prouvé sur des échantillons fautifs** avant de servir de verdict
(`TestValidateurProuve`) : un nom en majuscules, un tiret en tête, deux tirets
d'affilée, un nom qui ne correspond pas au dossier, une description vide, un
frontmatter non fermé. Sans cette moitié, `assert erreurs == []` serait vert sur
un validateur qui ne regarde rien.

Ce qui est ensuite validé, ce sont des skills **réellement écrits sur le disque**
par `generer_outillage` (#1033) — sur quatre profils de projet analysés (Node,
Python, Rust, Make), parce que le texte d'un skill dépend des commandes
constatées et qu'un seul profil ne dirait rien des autres, **et** sur un projet
**neuf** questionné (#1031), qui est l'autre voie du chantier et qui n'était
éprouvée de bout en bout nulle part : le questionnaire s'arrête à la
recommandation, la génération part d'une analyse, et personne ne jouait le
chemin entier.

Le catalogue lui-même (`SKILL_PAR_USAGE`) est éprouvé à part : un nom de skill
inadmissible y serait une faute latente, invisible tant qu'aucun projet ne
justifie ce skill-là.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from maestro.outillage import (
    DOSSIER_SKILLS,
    SKILL_PAR_USAGE,
    Choix,
    analyser,
    constats_depuis_choix,
    generer_outillage,
    recommandation_depuis_choix,
    source_manifeste_des_choix,
)
from maestro.projets.modele import Projet

#: Les contraintes de la spécification (relue le 2026-09-20), en un seul endroit.
#: `name` : 1-64, minuscules/chiffres/tirets, ni en tête ni en fin, jamais
#: doublés, **égal au nom du dossier**. `description` : 1-1024, non vide.
#: `compatibility` : 1-500 si fourni.
NOM_MAX = 64
DESCRIPTION_MAX = 1024
COMPATIBILITE_MAX = 500
NOM_ADMIS = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

#: Les champs que la spécification définit. Un champ hors de cette liste n'est
#: pas une **violation** — la spécification ne l'interdit pas —, mais il est
#: rendu à l'appelant, qui décide : ici, un skill généré par Maestro n'en porte
#: aucun, et c'est une propriété qu'on garde.
CHAMPS_CONNUS = frozenset(
    {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
)


@dataclass(frozen=True)
class Verdict:
    """Ce que le validateur rend : les violations, et les champs qu'il n'a pas reconnus."""

    erreurs: tuple[str, ...] = ()
    champs_inconnus: tuple[str, ...] = ()

    @property
    def valide(self) -> bool:
        """Aucune règle de la spécification n'est enfreinte."""
        return not self.erreurs


def _frontmatter_brut(texte: str) -> tuple[dict[str, str], str | None]:
    """Les champs scalaires du frontmatter, et l'erreur de forme s'il y en a une.

    Lecteur **autonome**, écrit ici et pas emprunté au code de production (cf.
    l'en-tête). Il ne lit que des scalaires, ce que la spécification suffit à
    demander : `metadata` est une table, et sa seule présence est retenue.
    """
    lignes = texte.splitlines()
    if not lignes or lignes[0].strip() != "---":
        return {}, "SKILL.md sans frontmatter YAML en première ligne"
    champs: dict[str, str] = {}
    for index, ligne in enumerate(lignes[1:], start=1):
        if ligne.strip() == "---":
            return champs, None
        if not ligne.strip() or ligne[:1].isspace():
            continue  # continuation, ou entrée d'une table : pas un champ de tête
        cle, separateur, valeur = ligne.partition(":")
        if not separateur:
            return champs, f"ligne {index} du frontmatter sans « clé: valeur »"
        champs[cle.strip()] = valeur.strip().strip("\"'")
    return champs, "frontmatter non fermé par une seconde ligne « --- »"


def valider_skill(dossier: Path) -> Verdict:
    """Valide le skill posé dans `dossier` — l'équivalent de `skills-ref validate`.

    Chaque règle est celle de la spécification, citée dans le message : une
    violation doit dire *quoi corriger*, sinon le test rougit sans apprendre
    quoi que ce soit à qui le lit.
    """
    erreurs: list[str] = []
    fichier = dossier / "SKILL.md"
    if not fichier.is_file():
        return Verdict(erreurs=(f"{dossier.name} : SKILL.md est requis",))
    champs, defaut = _frontmatter_brut(fichier.read_text(encoding="utf-8"))
    if defaut is not None:
        erreurs.append(f"{dossier.name} : {defaut}")

    nom = champs.get("name", "")
    if not nom:
        erreurs.append(f"{dossier.name} : le champ « name » est requis")
    else:
        if len(nom) > NOM_MAX:
            erreurs.append(f"{nom} : « name » dépasse {NOM_MAX} caractères")
        if not NOM_ADMIS.match(nom):
            erreurs.append(
                f"{nom} : « name » n'admet que a-z, 0-9 et des tirets, "
                "jamais en tête ni en fin, jamais doublés"
            )
        if nom != dossier.name:
            erreurs.append(f"{nom} : « name » doit être égal au nom du dossier ({dossier.name})")

    description = champs.get("description", "")
    if not description:
        erreurs.append(f"{nom or dossier.name} : le champ « description » est requis et non vide")
    elif len(description) > DESCRIPTION_MAX:
        erreurs.append(f"{nom} : « description » dépasse {DESCRIPTION_MAX} caractères")

    compatibilite = champs.get("compatibility", "")
    if compatibilite and len(compatibilite) > COMPATIBILITE_MAX:
        erreurs.append(f"{nom} : « compatibility » dépasse {COMPATIBILITE_MAX} caractères")

    return Verdict(
        erreurs=tuple(erreurs),
        champs_inconnus=tuple(sorted(set(champs) - CHAMPS_CONNUS)),
    )


# --------------------------------------------------------------------------- #
# Le validateur, prouvé sur des échantillons fautifs                            #
# --------------------------------------------------------------------------- #


def poser_skill(racine: Path, nom_dossier: str, frontmatter: str, corps: str = "# Titre\n") -> Path:
    """Pose un skill fabriqué et rend son dossier."""
    dossier = racine / nom_dossier
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / "SKILL.md").write_text(f"---\n{frontmatter}---\n\n{corps}", encoding="utf-8")
    return dossier


class TestValidateurProuve:
    """Chaque règle est montrée fautive **avant** de servir de verdict sur du vrai."""

    def test_un_skill_conforme_passe(self, tmp_path: Path) -> None:
        dossier = poser_skill(
            tmp_path, "lancer-les-tests", "name: lancer-les-tests\ndescription: Jouer la suite.\n"
        )

        verdict = valider_skill(dossier)

        assert verdict.valide
        assert verdict.champs_inconnus == ()

    @pytest.mark.parametrize(
        ("nom_dossier", "frontmatter", "fragment"),
        [
            ("PDF-Processing", "name: PDF-Processing\ndescription: x\n", "n'admet que a-z"),
            ("pdf", "name: -pdf\ndescription: x\n", "n'admet que a-z"),
            ("pdf-processing", "name: pdf--processing\ndescription: x\n", "n'admet que a-z"),
            ("pdf", "name: pdf-\ndescription: x\n", "n'admet que a-z"),
            ("tests", "name: lancer-les-tests\ndescription: x\n", "égal au nom du dossier"),
            ("tests", "description: x\n", "« name » est requis"),
            ("tests", "name: tests\n", "« description » est requis"),
            ("tests", "name: tests\ndescription:\n", "« description » est requis"),
            ("tests", "name: " + "a" * 65 + "\ndescription: x\n", "dépasse 64"),
            ("tests", "name: tests\ndescription: " + "x" * 1025 + "\n", "dépasse 1024"),
            (
                "tests",
                "name: tests\ndescription: x\ncompatibility: " + "y" * 501 + "\n",
                "dépasse 500",
            ),
        ],
    )
    def test_chaque_regle_de_la_specification_fait_rougir(
        self, tmp_path: Path, nom_dossier: str, frontmatter: str, fragment: str
    ) -> None:
        dossier = poser_skill(tmp_path, nom_dossier, frontmatter)

        verdict = valider_skill(dossier)

        assert not verdict.valide
        assert any(fragment in erreur for erreur in verdict.erreurs), verdict.erreurs

    def test_un_skill_md_absent_est_la_seule_violation_rendue(self, tmp_path: Path) -> None:
        """Un dossier sans `SKILL.md` n'est pas un skill : inutile de juger le reste."""
        (tmp_path / "vide").mkdir()

        verdict = valider_skill(tmp_path / "vide")

        assert verdict.erreurs == ("vide : SKILL.md est requis",)

    def test_un_frontmatter_non_ferme_est_signale(self, tmp_path: Path) -> None:
        """Deux façons de l'oublier, et les deux font rougir — avec des mots différents."""
        dossier = tmp_path / "tests"
        dossier.mkdir()
        (dossier / "SKILL.md").write_text("---\nname: tests\ndescription: x\n", encoding="utf-8")
        second = tmp_path / "autre"
        second.mkdir()
        (second / "SKILL.md").write_text(
            "---\nname: autre\ndescription: x\n\n# Corps avalé par le frontmatter\n",
            encoding="utf-8",
        )

        sans_fermeture = valider_skill(dossier)
        corps_avale = valider_skill(second)

        assert any("non fermé" in erreur for erreur in sans_fermeture.erreurs)
        assert any("sans « clé: valeur »" in erreur for erreur in corps_avale.erreurs)

    def test_un_champ_hors_specification_est_rendu_sans_etre_une_violation(
        self, tmp_path: Path
    ) -> None:
        """La spécification ne les interdit pas — c'est à l'appelant de décider."""
        dossier = poser_skill(
            tmp_path, "tests", "name: tests\ndescription: x\nmaison: oui\nlicense: MIT\n"
        )

        verdict = valider_skill(dossier)

        assert verdict.valide
        assert verdict.champs_inconnus == ("maison",)


# --------------------------------------------------------------------------- #
# Les skills que Maestro génère, validés sur le disque                          #
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _maison_isolee(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un `Path.home()` sous `tmp_path` : sans lui, `valider_racine` refuse AppData (#221)."""
    maison = tmp_path / "maison"
    maison.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: maison))
    return maison


def ecrire(racine: Path, chemin: str, contenu: str) -> None:
    """Pose un fichier sous `racine`, dossiers parents compris."""
    cible = racine / chemin
    cible.parent.mkdir(parents=True, exist_ok=True)
    cible.write_text(contenu, encoding="utf-8")


#: Quatre projets plausibles, et quatre outillages différents. Le texte d'un
#: skill dépend des commandes constatées : valider un seul profil dirait que la
#: rédaction est correcte **sur ce profil-là**, ce qui n'est pas la question.
PROFILS: dict[str, dict[str, str]] = {
    "node": {
        "package.json": '{"name": "depensio", "scripts": {"test": "vitest run", '
        '"build": "next build", "lint": "eslint .", "dev": "next dev"}}',
        "package-lock.json": "{}",
        "src/app.ts": "export const x = 1;\n",
    },
    "python": {
        "pyproject.toml": '[project]\nname = "api"\n\n[tool.ruff]\n\n[tool.pytest.ini_options]\n',
        "uv.lock": "version = 1\n",
        "src/api.py": "def main() -> None: ...\n",
    },
    "rust": {
        "Cargo.toml": '[package]\nname = "moteur"\n',
        "Cargo.lock": "version = 3\n",
        "src/main.rs": "fn main() {}\n",
    },
    "make": {
        "Makefile": "test:\n\tgo test ./...\nbuild:\n\tgo build ./...\n",
        "main.go": "package main\n",
    },
}


def _projet_genere(tmp_path: Path, profil: str) -> Path:
    """Analyse un projet du profil demandé, écrit son outillage, et rend sa racine."""
    racine = tmp_path / "projets" / profil
    racine.mkdir(parents=True)
    for chemin, contenu in PROFILS[profil].items():
        ecrire(racine, chemin, contenu)
    analyse = analyser(racine, projet_id="prj-0000dead")
    projet = Projet(id="prj-0000dead", nom=profil, racine=racine.as_posix())
    generer_outillage(
        projet,
        analyse.constats,
        analyse.recommandation,
        source=analyse.source_manifeste(),
        horodatage="2026-09-20T09:12:00+00:00",
    )
    return racine


@pytest.mark.parametrize("profil", sorted(PROFILS))
def test_les_skills_generes_sont_valides_au_sens_de_la_specification(
    tmp_path: Path, profil: str
) -> None:
    """Le critère, sur des fichiers réellement posés : chaque skill écrit est conforme."""
    racine = _projet_genere(tmp_path, profil)

    dossiers = sorted((racine / DOSSIER_SKILLS).iterdir())

    assert dossiers, f"le profil {profil} n'a produit aucun skill"
    for dossier in dossiers:
        verdict = valider_skill(dossier)
        assert verdict.valide, f"{profil}/{dossier.name} : {verdict.erreurs}"


@pytest.mark.parametrize("profil", sorted(PROFILS))
def test_un_skill_genere_ne_declare_jamais_allowed_tools(tmp_path: Path, profil: str) -> None:
    """docs/38 §5.1 : l'écrire laisserait croire qu'un fichier du projet accorde une permission.

    Le champ **existe** dans la spécification et serait accepté par elle : ce
    test ne garde donc pas une contrainte du format, mais une décision du dépôt —
    ce qui autorise un outil n'est pas l'origine du fichier, c'est le geste d'une
    personne (docs/32).
    """
    racine = _projet_genere(tmp_path, profil)

    for dossier in sorted((racine / DOSSIER_SKILLS).iterdir()):
        champs, _ = _frontmatter_brut((dossier / "SKILL.md").read_text(encoding="utf-8"))
        assert "allowed-tools" not in champs
        assert valider_skill(dossier).champs_inconnus == ()


@pytest.mark.parametrize("profil", sorted(PROFILS))
def test_un_skill_genere_vit_dans_le_dossier_de_docs_38(tmp_path: Path, profil: str) -> None:
    """`.agents/skills/<nom>/SKILL.md` — le seul chemin lu par plus d'un client (§3.3).

    La forme du dossier fait partie de la spécification autant que le
    frontmatter : `name` doit être **égal au nom du dossier**, ce qui ne veut
    rien dire si le skill n'est pas rangé dans un dossier à lui.
    """
    racine = _projet_genere(tmp_path, profil)

    assert (racine / DOSSIER_SKILLS).is_dir()
    for dossier in sorted((racine / DOSSIER_SKILLS).iterdir()):
        assert dossier.is_dir()
        assert (dossier / "SKILL.md").is_file()
        assert not any(enfant.is_dir() for enfant in dossier.iterdir() if enfant.name == "SKILL.md")


def test_un_projet_neuf_questionne_rend_lui_aussi_des_skills_valides(tmp_path: Path) -> None:
    """L'autre voie du chantier : les **choix** d'un projet neuf, jusqu'aux fichiers écrits.

    La jonction #1031 → #1033 n'était éprouvée nulle part de bout en bout : le
    questionnaire s'arrête à la recommandation, et la génération part d'une
    analyse. Ce test joue le chemin entier sur un dossier **vide** — c'est le cas
    réel d'un projet neuf — et valide ce qui en sort.

    Il garde au passage une propriété du chantier : les deux voies passent par le
    même `recommander`, donc le même rédacteur, donc des skills conformes des
    deux côtés. Si un jour elles divergeaient, ce test le dirait ici.
    """
    racine = tmp_path / "projets" / "neuf"
    racine.mkdir(parents=True)
    # Un service Python, tel que le questionnaire l'a compris (#1147) : une phrase
    # tapée, les constats du modèle, puis les options cliquées.
    acquis = [
        Choix("nature", "Un service qui répond à des appels HTTP, en Python", libre=True),
        Choix("langages", "Python", deduit=True),
        Choix("manifeste", "pyproject.toml", deduit=True),
        Choix("gestionnaire", "uv", deduit=True),
        Choix("installer", "uv sync", deduit=True),
        Choix("tester", "pytest", deduit=True),
        Choix("lint", "ruff check .", deduit=True),
        Choix("demarrer", "uv run python -m app", deduit=True),
        Choix("forge", "github"),
        Choix("ci", ".github/workflows/ci.yml"),
        Choix("conventions", "conventional-commits"),
    ]
    projet = Projet(id="prj-0000dead", nom="Neuf", racine=racine.as_posix())

    generer_outillage(
        projet,
        constats_depuis_choix(acquis),
        recommandation_depuis_choix(acquis),
        source=source_manifeste_des_choix(projet.id, acquis),
        horodatage="2026-09-20T09:12:00+00:00",
    )

    dossiers = sorted((racine / DOSSIER_SKILLS).iterdir())
    assert dossiers, "un projet neuf outillé doit recevoir au moins un skill"
    for dossier in dossiers:
        verdict = valider_skill(dossier)
        assert verdict.valide, f"neuf/{dossier.name} : {verdict.erreurs}"
        assert verdict.champs_inconnus == ()


def test_le_catalogue_des_skills_ne_porte_que_des_noms_admissibles() -> None:
    """Un nom inadmissible y serait une faute latente : invisible tant qu'aucun projet ne le tire.

    Ce test ne passe par aucun projet, et c'est le but — il couvre les cinq noms
    du catalogue, y compris ceux qu'aucun des quatre profils ci-dessus ne
    justifie.
    """
    noms = [nom for nom, _ in SKILL_PAR_USAGE.values()]

    assert len(noms) == len(set(noms))
    for nom in noms:
        assert NOM_ADMIS.match(nom), f"{nom} n'est pas un « name » admissible"
        assert len(nom) <= NOM_MAX


def test_les_descriptions_generees_tiennent_sur_une_ligne_et_sous_le_plafond(
    tmp_path: Path,
) -> None:
    """Une description multi-ligne casserait le frontmatter — et le skill entier avec.

    C'est le défaut le plus facile à introduire en retouchant un gabarit : la
    description vient d'une phrase rédigée, et une phrase rédigée finit par
    contenir un retour à la ligne le jour où quelqu'un l'allonge.
    """
    racine = _projet_genere(tmp_path, "node")

    for dossier in sorted((racine / DOSSIER_SKILLS).iterdir()):
        champs, defaut = _frontmatter_brut((dossier / "SKILL.md").read_text(encoding="utf-8"))
        assert defaut is None
        assert "\n" not in champs["description"]
        assert 0 < len(champs["description"]) <= DESCRIPTION_MAX

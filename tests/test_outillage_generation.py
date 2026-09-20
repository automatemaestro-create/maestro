"""Tests de la génération de l'outillage dans le dossier du projet (#1033, docs/38 §4).

Les trois modules que le lot 5 de #1020 a posés, et rien d'autre :

- `maestro.outillage.redaction` — **le texte**, inerte et déterministe. Éprouvé
  sur des constats fabriqués, sans projet réel : c'est tout l'intérêt de la
  frontière avec le disque ;
- `maestro.outillage.generation` — **l'écriture**, et les quatre cas de docs/38
  §4.2. Chacun a son test, y compris celui qui ne fait rien ;
- `maestro.outillage.ecriture` — **où** cela s'écrit, c'est-à-dire le régime de
  docs/24 §2.4.

Ce que les deux critères d'acceptation demandent est nommé tel quel :
« la génération écrit `AGENTS.md`, les skills et les scripts retenus, ainsi que
le manifeste », et « elle n'écrase jamais un fichier existant sans le dire ;
régénérer ne duplique rien, parce que le manifeste dit ce qui vient de Maestro ».

Aucun réseau, aucun modèle. Le seul test qui a besoin d'un vrai dépôt en monte un
jetable et est sauté là où `git` manque ; tout le reste joue sur un arbre de
fichiers.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from maestro.outillage import (
    BALISE_DEBUT,
    BALISE_FIN,
    CHEMIN_MANIFESTE,
    DOSSIER_REFUSES,
    DOSSIER_SKILLS,
    GENERE_PAR,
    PORTEE_BLOC,
    PORTEE_FICHIER,
    REGIME_BRANCHE,
    REGIME_EN_PLACE,
    VERSION_MANIFESTE,
    Commande,
    Constats,
    DossierScripts,
    Ecarte,
    Entree,
    Fichier,
    Forge,
    Gestionnaire,
    Langage,
    Piece,
    Recommandation,
    analyser,
    generer,
    generer_outillage,
    nouvel_id_de_generation,
    portees_declarees,
    rediger,
)
from maestro.outillage.generation import REFUS_VERSION
from maestro.outillage.redaction import bloc, texte_script, texte_skill
from maestro.projets.modele import Perimetre, Projet, Vcs
from maestro.projets.racine import RacineRefusee, detecter_vcs
from maestro.sandbox import FrontiereEcriture, branche_de_tache
from maestro.sandbox.en_place import chemin_atelier

GIT = shutil.which("git")

besoin_de_git = pytest.mark.skipif(GIT is None, reason="git introuvable")

#: Le fragment `source` du manifeste, tel que `Analyse.source_manifeste()` le rend.
SOURCE = {
    "type": "analyse",
    "projet_id": "prj-0000dead",
    "reference": "ana-3c9f0011",
    "resume": "Python, TypeScript ; uv, npm ; tests : pytest",
}

#: Un horodatage figé : la date ne doit entrer dans **aucun** contenu de fichier,
#: seulement dans le manifeste (docs/38 §4.2 — sans quoi « empreinte identique »
#: serait inatteignable).
QUAND = "2026-09-20T12:00:00+00:00"


@pytest.fixture(autouse=True)
def _maison_isolee(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un `Path.home()` sous `tmp_path`, comme dans les tests du socle (#221).

    Indispensable sous Windows : le `tmp_path` de pytest vit sous `AppData`, que
    `valider_racine` interdit à juste titre — sans cette isolation, toutes les
    racines de projet de ce fichier seraient refusées.
    """
    maison = tmp_path / "maison"
    maison.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: maison))
    return maison


# --------------------------------------------------------------------------- #
# Fabriques : des constats et une recommandation, sans projet réel
# --------------------------------------------------------------------------- #


def _constats() -> Constats:
    """Un projet plausible : deux langages, deux gestionnaires, sept commandes."""
    return Constats(
        langages=(
            Langage(nom="Python", fichiers=42, part=0.7, exemple="src/app.py"),
            Langage(nom="TypeScript", fichiers=18, part=0.3, exemple="web/page.tsx"),
        ),
        gestionnaires=(
            Gestionnaire(nom="uv", chemin="pyproject.toml", verrou="uv.lock", installer="uv sync"),
            Gestionnaire(nom="npm", chemin="package.json"),
        ),
        commandes=(
            Commande(
                usage="installer", commande="uv sync", chemin="pyproject.toml", extrait="[tool.uv]"
            ),
            Commande(
                usage="construire",
                commande="npm run build",
                chemin="package.json",
                extrait="scripts.build",
            ),
            Commande(
                usage="tester", commande="pytest", chemin="pyproject.toml", origine="convention"
            ),
            Commande(usage="lint", commande="ruff check .", chemin="pyproject.toml"),
            Commande(usage="formater", commande="ruff format .", chemin="pyproject.toml"),
            Commande(usage="types", commande="mypy .", chemin="pyproject.toml"),
            Commande(
                usage="demarrer",
                commande="npm run dev",
                chemin="package.json",
                extrait="scripts.dev",
            ),
        ),
        ci=(Piece(nom="ci.yml", chemin=".github/workflows/ci.yml", role="GitHub Actions"),),
        forge=Forge(nom="GitHub", distant="git@github.com:moi/depensio.git", chemin=".git/config"),
        vcs=Vcs(type="git", branche_base="main"),
        conventions=(
            Piece(nom="CONTRIBUTING.md", chemin="CONTRIBUTING.md", role="contribution"),
            Piece(nom="AGENTS.md", chemin="AGENTS.md", role="instructions d'agent"),
        ),
        dossier_scripts=DossierScripts(
            chemin="bin", constate=True, scripts=(Piece(nom="build.sh", chemin="bin/build.sh"),)
        ),
    )


def _recommandation() -> Recommandation:
    """L'outillage de docs/38 §3.6 : les instructions, les deux ponts, les skills, un script."""
    return Recommandation(
        entrees=(
            Entree(
                type="instructions",
                nom="AGENTS.md",
                chemin="AGENTS.md",
                etat="a-generer",
                raison="le projet n'a pas d'instructions d'agent",
            ),
            Entree(
                type="pont",
                nom="CLAUDE.md",
                chemin="CLAUDE.md",
                etat="a-generer",
                raison="Claude Code ne lit pas AGENTS.md",
            ),
            Entree(
                type="pont",
                nom="GEMINI.md",
                chemin="GEMINI.md",
                etat="a-generer",
                raison="Gemini CLI ne lit pas AGENTS.md",
            ),
            Entree(
                type="skill",
                nom="lancer-les-tests",
                chemin=f"{DOSSIER_SKILLS}/lancer-les-tests/SKILL.md",
                etat="a-generer",
                raison="c'est la vérification que tout agent joue avant de rendre son travail",
                justification=Piece(
                    nom="pyproject.toml", chemin="pyproject.toml", role="manifeste Python"
                ),
                commandes=("pytest",),
            ),
            Entree(
                type="skill",
                nom="verifier-le-style",
                chemin=f"{DOSSIER_SKILLS}/verifier-le-style/SKILL.md",
                etat="a-generer",
                raison="le projet a ses vérifications de style",
                commandes=("ruff check .", "ruff format .", "mypy ."),
            ),
            Entree(
                type="script",
                nom="verifier.sh",
                chemin="bin/verifier.sh",
                etat="a-generer",
                raison="enchaîne les vérifications du projet",
                commandes=("ruff check .", "pytest"),
            ),
        ),
        ecartes=(
            Ecarte(type="commande", nom="—", raison="aucun format de commande n'est commun"),
        ),
    )


def _fichier(
    chemin: str = "AGENTS.md",
    contenu: str = "# AGENTS.md\n",
    *,
    role: str = "instructions",
    portee: str = PORTEE_FICHIER,
    executable: bool = False,
) -> Fichier:
    """Un `Fichier` à écrire, pour les tests qui visent un cas d'écrasement précis."""
    return Fichier(
        chemin=chemin, role=role, portee=portee, contenu=contenu, executable=executable
    )


def _manifeste(cible: Path) -> dict:
    """Le manifeste écrit dans `cible`, relu."""
    return json.loads((cible / CHEMIN_MANIFESTE).read_text(encoding="utf-8"))


def _chemins_declares(cible: Path) -> list[str]:
    """Les chemins que le manifeste de `cible` déclare, dans l'ordre."""
    return [entree["chemin"] for entree in _manifeste(cible)["entrees"]]


def _etats(rapport, chemin: str) -> list[str]:
    """Les états rapportés pour `chemin` — un seul, sauf bogue."""
    return [e.etat for e in rapport.ecritures if e.chemin == chemin]


# --------------------------------------------------------------------------- #
# Rédaction : le texte, et il ne dépend que des constats
# --------------------------------------------------------------------------- #


def test_rediger_rend_un_fichier_par_entree_dans_l_ordre_de_la_recommandation() -> None:
    fichiers = rediger(_constats(), _recommandation())

    assert [f.chemin for f in fichiers] == [
        "AGENTS.md",
        "CLAUDE.md",
        "GEMINI.md",
        f"{DOSSIER_SKILLS}/lancer-les-tests/SKILL.md",
        f"{DOSSIER_SKILLS}/verifier-le-style/SKILL.md",
        "bin/verifier.sh",
    ]
    assert [f.role for f in fichiers] == [
        "instructions",
        "pont",
        "pont",
        "skill",
        "skill",
        "script",
    ]
    # Seul le script est exécutable : un `SKILL.md` ne se lance pas.
    assert [f.executable for f in fichiers] == [False] * 5 + [True]
    # Le pont est **une ligne**, jamais une copie d'AGENTS.md (docs/38 §3.2).
    assert fichiers[1].contenu == "@AGENTS.md"


def test_rediger_est_deterministe() -> None:
    """Deux rédactions des mêmes constats rendent le même octet (docs/38 §4.2)."""
    constats, recommandation = _constats(), _recommandation()

    assert rediger(constats, recommandation) == rediger(constats, recommandation)


def test_une_entree_a_completer_rend_un_fichier_de_portee_bloc() -> None:
    recommandation = Recommandation(
        entrees=(
            Entree(
                type="instructions",
                nom="AGENTS.md",
                chemin="AGENTS.md",
                etat="a-completer",
                raison="le projet a déjà son AGENTS.md",
            ),
        )
    )

    (fichier,) = rediger(_constats(), recommandation)

    assert fichier.portee == PORTEE_BLOC


def test_une_entree_deja_presente_ne_rend_rien_tant_que_le_manifeste_ne_la_declare_pas() -> None:
    """Ce que le projet porte est reconnu, jamais dupliqué (docs/38 §3.3)."""
    recommandation = Recommandation(
        entrees=(
            Entree(
                type="skill",
                nom="lancer-les-tests",
                chemin=f"{DOSSIER_SKILLS}/lancer-les-tests/SKILL.md",
                etat="deja-present",
                raison="le projet porte déjà ce skill",
            ),
        )
    )

    assert rediger(_constats(), recommandation) == ()

    # Déclarée au manifeste, c'est un skill **de Maestro** relu : il se régénère.
    declarees = {f"{DOSSIER_SKILLS}/lancer-les-tests/SKILL.md": PORTEE_FICHIER}
    (fichier,) = rediger(_constats(), recommandation, portees=declarees)
    assert fichier.portee == PORTEE_FICHIER


def test_la_portee_du_manifeste_l_emporte_sur_celle_que_l_etat_suggererait() -> None:
    """La régénération du banc du 2026-09-20 : sans cela, AGENTS.md se dupliquait en lui-même."""
    recommandation = Recommandation(
        entrees=(
            Entree(
                type="instructions",
                nom="AGENTS.md",
                chemin="AGENTS.md",
                etat="a-completer",  # l'analyse rejouée voit le fichier que Maestro a écrit
                raison="le projet a déjà son AGENTS.md",
            ),
        )
    )

    (fichier,) = rediger(_constats(), recommandation, portees={"AGENTS.md": PORTEE_FICHIER})

    assert fichier.portee == PORTEE_FICHIER


def test_une_entree_d_un_type_inconnu_est_sautee_plutot_que_devinee() -> None:
    recommandation = Recommandation(
        entrees=(
            Entree(
                type="hologramme",
                nom="x",
                chemin="x.md",
                etat="a-generer",
                raison="type que ce module ne sait pas rédiger",
            ),
        )
    )

    assert rediger(_constats(), recommandation) == ()


def test_agents_md_porte_les_six_sections_et_les_constats_avec_leur_source() -> None:
    (instructions, *_) = rediger(_constats(), _recommandation())
    texte = instructions.contenu

    for titre in (
        "## Le projet",
        "## Monter et lancer",
        "## Vérifier",
        "## Conventions",
        "## L'outillage de ce projet",
        "## Ce qu'un agent ne touche pas",
    ):
        assert titre in texte

    assert "Python (70 %)" in texte
    assert "uv (`pyproject.toml`, verrou `uv.lock`)" in texte
    assert "GitHub Actions (`.github/workflows/ci.yml`)" in texte
    assert "git@github.com:moi/depensio.git" in texte
    assert "branche de base `main`" in texte
    # Chaque commande dit **d'où** elle sort, et une convention se déclare comme telle.
    assert "`uv sync` — déclarée dans `pyproject.toml` ([tool.uv])" in texte
    assert "`pytest` — convention de l'outil constaté dans `pyproject.toml` (non déclarée)" in texte
    # L'index des skills est dérivé, et le dossier de scripts est celui du projet.
    assert f"`{DOSSIER_SKILLS}/lancer-les-tests/SKILL.md`" in texte
    assert "`bin/`" in texte
    # Les instructions déjà écrites du projet ne sont pas listées comme convention à lire.
    assert "`CONTRIBUTING.md`" in texte
    assert "- `AGENTS.md` — instructions d'agent." not in texte
    assert GENERE_PAR in texte
    assert CHEMIN_MANIFESTE in texte
    # Aucun horodatage dans le contenu : il vit dans le manifeste.
    assert QUAND not in texte


def test_une_section_sans_matiere_dit_l_absence_plutot_que_de_disparaitre() -> None:
    """Un gabarit à sections variables serait illisible à la régénération."""
    (instructions,) = rediger(
        Constats(),
        Recommandation(
            entrees=(
                Entree(
                    type="instructions",
                    nom="AGENTS.md",
                    chemin="AGENTS.md",
                    etat="a-generer",
                    raison="projet nu",
                ),
            )
        ),
    )
    texte = instructions.contenu

    assert "## Le projet" in texte
    assert "n'a constaté ni langage dominant" in texte
    assert "ne déclare aucune commande d'installation" in texte
    assert "ne déclare aucune commande de test" in texte
    assert "n'écrit aucune convention" in texte
    assert "n'a pas encore de skill" in texte
    assert "n'a pas de dossier de scripts" in texte


def test_un_skill_porte_son_frontmatter_ses_commandes_et_ce_qui_le_justifie() -> None:
    entrees = {e.nom: e for e in _recommandation().entrees}

    tests = texte_skill(entrees["lancer-les-tests"])
    assert tests.startswith("---\nname: lancer-les-tests\ndescription: ")
    assert "allowed-tools" not in tests  # le champ serait inerte partout (docs/38 §5.1)
    assert "# Lancer les tests" in tests
    assert "Depuis la **racine du projet** :" in tests
    assert "```bash\npytest\n```" in tests
    assert "Constaté dans `pyproject.toml` (manifeste Python)." in tests

    style = texte_skill(entrees["verifier-le-style"])
    # `verifier-le-style` enveloppe trois commandes de la même question.
    assert "Ce skill couvre lint, formater, types" in style
    assert "Constaté dans" not in style  # cette entrée n'a pas de justification


def test_un_skill_inconnu_du_catalogue_retombe_sur_la_raison_de_son_entree() -> None:
    maison = Entree(
        type="skill",
        nom="deployer-la-preprod",
        chemin=f"{DOSSIER_SKILLS}/deployer-la-preprod/SKILL.md",
        etat="a-generer",
        raison="le projet déclare un script de déploiement",
    )

    texte = texte_skill(maison)

    assert "description: le projet déclare un script de déploiement" in texte
    # La phrase est capitalisée et ponctuée, même quand la raison ne l'était pas.
    assert "Le projet déclare un script de déploiement." in texte
    assert "aucune commande constatée pour ce skill" in texte


def test_un_script_genere_s_arrete_a_la_premiere_commande_en_echec() -> None:
    (script,) = [e for e in _recommandation().entrees if e.type == "script"]

    texte = texte_script(script)

    assert texte.startswith("#!/usr/bin/env bash\n")
    assert "set -euo pipefail" in texte
    assert "ruff check .\npytest\n" in texte
    assert GENERE_PAR in texte


def test_un_script_sans_commande_constatee_echoue_bruyamment() -> None:
    nu = Entree(
        type="script", nom="x.sh", chemin="bin/x.sh", etat="a-generer", raison="sans commande"
    )

    assert 'echo "aucune commande constatée" >&2; exit 1' in texte_script(nu)


def test_bloc_entoure_le_contenu_des_balises_relues_ailleurs() -> None:
    assert bloc("  Instructions.  ") == f"{BALISE_DEBUT}\nInstructions.\n{BALISE_FIN}"


# --------------------------------------------------------------------------- #
# Génération : les quatre cas de docs/38 §4.2, et le manifeste
# --------------------------------------------------------------------------- #


def test_un_arbre_vierge_recoit_tout_l_outillage_et_son_manifeste(tmp_path: Path) -> None:
    """Critère 1 : `AGENTS.md`, les skills et les scripts retenus, **ainsi que le manifeste**."""
    cible = tmp_path / "projet"
    cible.mkdir()
    fichiers = rediger(_constats(), _recommandation())

    rapport = generer(cible, fichiers, source=SOURCE, horodatage=QUAND)

    assert {e.etat for e in rapport.ecritures} == {"ecrit"}
    assert (cible / "AGENTS.md").is_file()
    assert (cible / "CLAUDE.md").read_text(encoding="utf-8") == "@AGENTS.md"
    assert (cible / DOSSIER_SKILLS / "lancer-les-tests" / "SKILL.md").is_file()
    assert (cible / "bin" / "verifier.sh").is_file()

    manifeste = _manifeste(cible)
    assert manifeste["manifeste"] == VERSION_MANIFESTE
    assert manifeste["genere_par"] == GENERE_PAR
    assert manifeste["genere_le"] == QUAND
    assert manifeste["source"] == SOURCE  # recopié tel quel, jamais reformulé
    assert _chemins_declares(cible) == [f.chemin for f in fichiers]
    assert all(e["empreinte"].startswith("sha256:") for e in manifeste["entrees"])
    assert rapport.genere_le == QUAND
    assert rapport.refus == ""


@pytest.mark.skipif(os.name == "nt", reason="bit d'exécution absent sous Windows")
def test_un_script_est_pose_executable(tmp_path: Path) -> None:
    cible = tmp_path / "projet"
    cible.mkdir()

    generer(cible, rediger(_constats(), _recommandation()), source=SOURCE)

    assert os.access(cible / "bin" / "verifier.sh", os.X_OK)


def test_regenerer_ne_duplique_rien_et_ne_redate_pas_ce_qui_n_a_pas_bouge(
    tmp_path: Path,
) -> None:
    """Critère 2, seconde moitié : le manifeste dit ce qui vient de Maestro."""
    cible = tmp_path / "projet"
    cible.mkdir()
    fichiers = rediger(_constats(), _recommandation())
    generer(cible, fichiers, source=SOURCE, horodatage=QUAND)
    avant = (cible / "AGENTS.md").read_bytes()

    rapport = generer(cible, fichiers, source=SOURCE, horodatage="2026-10-01T08:00:00+00:00")

    assert {e.etat for e in rapport.ecritures} == {"inchange"}
    assert (cible / "AGENTS.md").read_bytes() == avant
    # La date d'une entrée inchangée est **celle d'avant** : rien n'a été regénéré.
    entrees = {e["chemin"]: e for e in _manifeste(cible)["entrees"]}
    assert entrees["AGENTS.md"]["genere_le"] == QUAND
    assert _manifeste(cible)["genere_le"] == "2026-10-01T08:00:00+00:00"


def test_un_fichier_du_projet_absent_du_manifeste_n_est_jamais_touche(tmp_path: Path) -> None:
    """« Maestro ne possède que ce qu'il a déclaré » — le cas `ignore`."""
    cible = tmp_path / "projet"
    cible.mkdir()
    (cible / "AGENTS.md").write_text("# Le mien\n", encoding="utf-8")

    rapport = generer(cible, [_fichier()], source=SOURCE, horodatage=QUAND)

    assert _etats(rapport, "AGENTS.md") == ["ignore"]
    assert (cible / "AGENTS.md").read_text(encoding="utf-8") == "# Le mien\n"
    assert [e.chemin for e in rapport.ignores] == ["AGENTS.md"]
    assert "AGENTS.md" not in _chemins_declares(cible)


def test_un_fichier_modifie_depuis_n_est_jamais_ecrase_et_la_neuve_attend_nommee(
    tmp_path: Path,
) -> None:
    """Critère 2, première moitié : jamais écrasé **sans le dire**."""
    cible = tmp_path / "projet"
    cible.mkdir()
    generer(cible, [_fichier()], source=SOURCE, horodatage=QUAND)
    (cible / "AGENTS.md").write_text("# AGENTS.md\n\nMa ligne à moi.\n", encoding="utf-8")

    rapport = generer(
        cible, [_fichier(contenu="# AGENTS.md — v2\n")], source=SOURCE, horodatage=QUAND
    )

    (refuse,) = rapport.refuses
    assert refuse.chemin == "AGENTS.md"
    assert refuse.refuse_vers == f"{DOSSIER_REFUSES}/AGENTS.md"
    assert "jamais écrasé" in refuse.raison
    # Le fichier de la personne est intact ; la proposition attend à côté.
    assert "Ma ligne à moi." in (cible / "AGENTS.md").read_text(encoding="utf-8")
    depose = cible / DOSSIER_REFUSES / "AGENTS.md"
    assert depose.read_text(encoding="utf-8") == "# AGENTS.md — v2\n"
    # L'entrée d'avant est **gardée** : sans elle, la génération suivante lirait
    # « présent, absent du manifeste » et n'oserait plus jamais y toucher.
    assert "AGENTS.md" in _chemins_declares(cible)


def test_un_fichier_declare_mais_supprime_est_reecrit(tmp_path: Path) -> None:
    """Une suppression n'est pas une modification à préserver."""
    cible = tmp_path / "projet"
    cible.mkdir()
    generer(cible, [_fichier()], source=SOURCE, horodatage=QUAND)
    (cible / "AGENTS.md").unlink()

    rapport = generer(cible, [_fichier()], source=SOURCE, horodatage=QUAND)

    assert _etats(rapport, "AGENTS.md") == ["ecrit"]
    assert (cible / "AGENTS.md").is_file()


def test_une_piece_qui_n_est_plus_recommandee_quitte_le_manifeste_sans_quitter_le_disque(
    tmp_path: Path,
) -> None:
    """Maestro n'efface rien dans le projet de quelqu'un."""
    cible = tmp_path / "projet"
    cible.mkdir()
    generer(
        cible,
        [_fichier(), _fichier("CLAUDE.md", "@AGENTS.md", role="pont")],
        source=SOURCE,
        horodatage=QUAND,
    )

    rapport = generer(cible, [_fichier()], source=SOURCE, horodatage=QUAND)

    (retire,) = rapport.retires
    assert retire.chemin == "CLAUDE.md"
    assert retire.role == "pont"
    assert "le fichier reste sur le disque" in retire.raison
    assert (cible / "CLAUDE.md").is_file()
    assert _chemins_declares(cible) == ["AGENTS.md"]


def test_un_manifeste_d_une_version_inconnue_fait_tout_refuser(tmp_path: Path) -> None:
    """Écraser un manifeste qu'on ne comprend pas ferait perdre la mémoire de Maestro."""
    cible = tmp_path / "projet"
    (cible / CHEMIN_MANIFESTE).parent.mkdir(parents=True)
    (cible / CHEMIN_MANIFESTE).write_text(
        json.dumps({"manifeste": 99, "entrees": []}), encoding="utf-8"
    )

    rapport = generer(cible, [_fichier()], source=SOURCE, horodatage=QUAND)

    assert REFUS_VERSION in rapport.refus
    assert rapport.ecritures == ()
    assert not (cible / "AGENTS.md").exists()
    assert _manifeste(cible)["manifeste"] == 99  # pas réécrit


@pytest.mark.parametrize(
    "brut",
    [
        "",
        "   \n",
        "{ceci n'est pas du JSON",
        "[1, 2, 3]",
        json.dumps({"manifeste": VERSION_MANIFESTE, "entrees": "pas une liste", "source": 12}),
        json.dumps({"manifeste": VERSION_MANIFESTE, "entrees": [{"sans": "chemin"}, 7]}),
    ],
)
def test_un_manifeste_illisible_est_traite_comme_un_projet_non_outille(
    tmp_path: Path, brut: str
) -> None:
    cible = tmp_path / "projet"
    (cible / CHEMIN_MANIFESTE).parent.mkdir(parents=True)
    (cible / CHEMIN_MANIFESTE).write_text(brut, encoding="utf-8")

    rapport = generer(cible, [_fichier()], source=SOURCE, horodatage=QUAND)

    assert rapport.refus == ""
    assert _etats(rapport, "AGENTS.md") == ["ecrit"]


def test_portees_declarees_rend_la_memoire_du_manifeste(tmp_path: Path) -> None:
    cible = tmp_path / "projet"
    cible.mkdir()
    assert portees_declarees(cible) == {}  # première génération : le cas nominal

    generer(
        cible,
        [_fichier(), _fichier("CLAUDE.md", "bloc", role="pont", portee=PORTEE_BLOC)],
        source=SOURCE,
        horodatage=QUAND,
    )

    assert portees_declarees(cible) == {
        "AGENTS.md": PORTEE_FICHIER,
        "CLAUDE.md": PORTEE_BLOC,
    }


# --------------------------------------------------------------------------- #
# La portée `bloc` : Maestro ne possède qu'un bloc d'un fichier d'autrui
# --------------------------------------------------------------------------- #


def _bloc_fichier(contenu: str = "Instructions Maestro.") -> Fichier:
    """Un `AGENTS.md` de portée bloc — ce que Maestro s'autorise dans un fichier existant."""
    return _fichier(contenu=contenu, portee=PORTEE_BLOC)


def test_un_bloc_s_ajoute_a_la_fin_d_un_fichier_existant_sans_rien_remplacer(
    tmp_path: Path,
) -> None:
    cible = tmp_path / "projet"
    cible.mkdir()
    (cible / "AGENTS.md").write_text("# Mon projet\n\nLire le README.\n", encoding="utf-8")

    rapport = generer(cible, [_bloc_fichier()], source=SOURCE, horodatage=QUAND)

    texte = (cible / "AGENTS.md").read_text(encoding="utf-8")
    assert _etats(rapport, "AGENTS.md") == ["ecrit"]
    assert texte.startswith("# Mon projet\n\nLire le README.")
    assert texte.index("Lire le README.") < texte.index(BALISE_DEBUT)
    assert "Instructions Maestro." in texte
    assert texte.rstrip().endswith(BALISE_FIN)


def test_le_bloc_seul_est_reecrit_et_le_reste_du_fichier_est_intact(tmp_path: Path) -> None:
    cible = tmp_path / "projet"
    cible.mkdir()
    (cible / "AGENTS.md").write_text("# Mon projet\n\nLire le README.\n", encoding="utf-8")
    generer(cible, [_bloc_fichier()], source=SOURCE, horodatage=QUAND)

    rapport = generer(cible, [_bloc_fichier("Instructions v2.")], source=SOURCE, horodatage=QUAND)

    texte = (cible / "AGENTS.md").read_text(encoding="utf-8")
    assert _etats(rapport, "AGENTS.md") == ["ecrit"]
    assert "Lire le README." in texte
    assert "Instructions v2." in texte
    assert "Instructions Maestro." not in texte
    assert texte.count(BALISE_DEBUT) == 1  # jamais dupliqué


def test_un_bloc_deja_a_jour_n_est_pas_reecrit(tmp_path: Path) -> None:
    cible = tmp_path / "projet"
    cible.mkdir()
    (cible / "AGENTS.md").write_text("# Mon projet\n", encoding="utf-8")
    generer(cible, [_bloc_fichier()], source=SOURCE, horodatage=QUAND)
    avant = (cible / "AGENTS.md").read_bytes()

    rapport = generer(cible, [_bloc_fichier()], source=SOURCE, horodatage=QUAND)

    assert _etats(rapport, "AGENTS.md") == ["inchange"]
    assert (cible / "AGENTS.md").read_bytes() == avant


def test_un_bloc_modifie_a_la_main_n_est_jamais_ecrase(tmp_path: Path) -> None:
    cible = tmp_path / "projet"
    cible.mkdir()
    (cible / "AGENTS.md").write_text("# Mon projet\n", encoding="utf-8")
    generer(cible, [_bloc_fichier()], source=SOURCE, horodatage=QUAND)
    (cible / "AGENTS.md").write_text(
        f"# Mon projet\n\n{BALISE_DEBUT}\nJ'ai corrigé ça.\n{BALISE_FIN}\n", encoding="utf-8"
    )

    rapport = generer(cible, [_bloc_fichier("Instructions v2.")], source=SOURCE, horodatage=QUAND)

    (refuse,) = rapport.refuses
    assert refuse.refuse_vers == f"{DOSSIER_REFUSES}/AGENTS.md"
    assert "J'ai corrigé ça." in (cible / "AGENTS.md").read_text(encoding="utf-8")
    # Déposée **balises comprises** : c'est un bloc à recoller, pas un fragment.
    depose = (cible / DOSSIER_REFUSES / "AGENTS.md").read_text(encoding="utf-8")
    assert depose.startswith(BALISE_DEBUT)
    assert "Instructions v2." in depose
    assert depose.rstrip().endswith(BALISE_FIN)


def test_un_bloc_ecrit_par_quelqu_un_d_autre_est_ignore(tmp_path: Path) -> None:
    cible = tmp_path / "projet"
    cible.mkdir()
    (cible / "AGENTS.md").write_text(
        f"{BALISE_DEBUT}\nPas de Maestro ici.\n{BALISE_FIN}\n", encoding="utf-8"
    )

    rapport = generer(cible, [_bloc_fichier()], source=SOURCE, horodatage=QUAND)

    assert _etats(rapport, "AGENTS.md") == ["ignore"]
    assert "Pas de Maestro ici." in (cible / "AGENTS.md").read_text(encoding="utf-8")


def test_des_balises_depareillees_ne_sont_pas_prises_pour_un_bloc(tmp_path: Path) -> None:
    """On ne devine pas où un bloc s'arrête : le neuf s'ajoute à la fin."""
    cible = tmp_path / "projet"
    cible.mkdir()
    (cible / "AGENTS.md").write_text(
        f"# Mon projet\n\n{BALISE_DEBUT}\ncoupé...\n", encoding="utf-8"
    )

    rapport = generer(cible, [_bloc_fichier()], source=SOURCE, horodatage=QUAND)

    texte = (cible / "AGENTS.md").read_text(encoding="utf-8")
    assert _etats(rapport, "AGENTS.md") == ["ecrit"]
    assert "coupé..." in texte
    assert texte.rstrip().endswith(BALISE_FIN)


def test_un_bloc_sur_un_fichier_vide_ou_absent_est_ecrit_seul(tmp_path: Path) -> None:
    cible = tmp_path / "projet"
    cible.mkdir()
    (cible / "AGENTS.md").write_text("   \n", encoding="utf-8")

    generer(cible, [_bloc_fichier()], source=SOURCE, horodatage=QUAND)

    assert (cible / "AGENTS.md").read_text(encoding="utf-8") == (
        f"{BALISE_DEBUT}\nInstructions Maestro.\n{BALISE_FIN}\n"
    )


# --------------------------------------------------------------------------- #
# La frontière d'écriture : la même que celle des agents (#839), jamais une seconde
# --------------------------------------------------------------------------- #


def test_un_chemin_qui_sort_de_la_cible_est_refuse_avec_son_motif(tmp_path: Path) -> None:
    cible = tmp_path / "projet"
    cible.mkdir()

    rapport = generer(
        cible, [_fichier("../dehors.md")], source=SOURCE, horodatage=QUAND
    )

    (refuse,) = rapport.refuses
    assert "sort de la racine du projet" in refuse.raison
    assert not (tmp_path / "dehors.md").exists()


def test_un_chemin_exclu_du_perimetre_est_refuse(tmp_path: Path) -> None:
    cible = tmp_path / "projet"
    cible.mkdir()

    rapport = generer(
        cible,
        [_fichier(".env", "CLE=1\n")],
        source=SOURCE,
        frontiere=FrontiereEcriture.pour(cible, Perimetre()),
        horodatage=QUAND,
    )

    assert _etats(rapport, ".env") == ["refuse"]
    assert not (cible / ".env").exists()


def test_un_manifeste_qu_on_ne_peut_pas_ecrire_est_une_ligne_du_rapport(tmp_path: Path) -> None:
    cible = tmp_path / "projet"
    cible.mkdir()

    rapport = generer(
        cible,
        [_fichier()],
        source=SOURCE,
        frontiere=FrontiereEcriture.pour(cible, Perimetre(exclus=(".maestro",))),
        horodatage=QUAND,
    )

    (refuse,) = rapport.refuses
    assert refuse.chemin == CHEMIN_MANIFESTE
    assert refuse.role == "manifeste"
    assert not (cible / CHEMIN_MANIFESTE).exists()


def test_une_ecriture_impossible_est_une_ligne_du_rapport_jamais_une_exception(
    tmp_path: Path,
) -> None:
    """Il ne doit pas y avoir d'état où l'appelant ne sait pas ce qui a été posé."""
    cible = tmp_path / "projet"
    cible.mkdir()
    (cible / "bin").write_text("je suis un fichier, pas un dossier\n", encoding="utf-8")

    rapport = generer(cible, [_fichier("bin/verifier.sh", "#!/bin/sh\n")], source=SOURCE)

    (refuse,) = rapport.refuses
    assert refuse.chemin == "bin/verifier.sh"
    assert "écriture impossible" in refuse.raison


def test_le_rapport_se_montre_tel_quel(tmp_path: Path) -> None:
    cible = tmp_path / "projet"
    cible.mkdir()

    brut = generer(cible, [_fichier()], source=SOURCE, horodatage=QUAND).to_dict()

    assert brut["cible"] == str(cible)
    assert brut["manifeste"] == CHEMIN_MANIFESTE
    assert brut["genere_par"] == GENERE_PAR
    assert brut["genere_le"] == QUAND
    assert brut["ecrits"] == ["AGENTS.md"]
    assert brut["refuses"] == brut["ignores"] == brut["retires"] == []
    assert brut["ecritures"][0]["etat"] == "ecrit"
    assert brut["ecritures"][0]["refuse_vers"] == ""


# --------------------------------------------------------------------------- #
# Le régime d'écriture du projet (docs/24 §2.4)
# --------------------------------------------------------------------------- #


def _projet(racine: Path, *, vcs: Vcs | None = None) -> Projet:
    """Un projet déclaré pointant sur `racine`."""
    return Projet(id="prj-0000dead", nom="Dépensio", racine=racine.as_posix(), vcs=vcs)


def test_un_projet_non_versionne_recoit_son_outillage_en_place(tmp_path: Path) -> None:
    racine = tmp_path / "projets" / "depensio"
    racine.mkdir(parents=True)

    preparation = generer_outillage(
        _projet(racine), _constats(), _recommandation(), source=SOURCE, horodatage=QUAND
    )

    assert preparation.regime == REGIME_EN_PLACE
    assert preparation.branche == ""  # rien à fusionner : c'est fait
    assert (racine / "AGENTS.md").is_file()
    assert (racine / CHEMIN_MANIFESTE).is_file()
    assert preparation.to_dict()["regime"] == REGIME_EN_PLACE
    assert preparation.to_dict()["rapport"]["ecrits"]


def test_regenerer_en_place_ne_duplique_pas_agents_md_dans_lui_meme(tmp_path: Path) -> None:
    """Le défaut mesuré au banc du 2026-09-20 : la rédaction lit le manifeste de la cible."""
    racine = tmp_path / "projets" / "depensio"
    racine.mkdir(parents=True)
    generer_outillage(
        _projet(racine), _constats(), _recommandation(), source=SOURCE, horodatage=QUAND
    )

    seconde = generer_outillage(
        _projet(racine), _constats(), _recommandation(), source=SOURCE, horodatage=QUAND
    )

    assert {e.etat for e in seconde.rapport.ecritures} == {"inchange"}
    assert BALISE_DEBUT not in (racine / "AGENTS.md").read_text(encoding="utf-8")


def test_une_racine_qui_n_est_plus_admissible_est_refusee_avant_toute_ecriture(
    tmp_path: Path,
) -> None:
    """Dernière porte avant d'écrire chez quelqu'un : le dépôt des projets s'édite à la main."""
    with pytest.raises(RacineRefusee):
        generer_outillage(
            _projet(tmp_path / "projets" / "disparu"),
            _constats(),
            _recommandation(),
            source=SOURCE,
        )


def test_l_identifiant_d_une_generation_ne_se_reduit_jamais_a_outillage() -> None:
    identifiant = nouvel_id_de_generation()

    assert identifiant.startswith("outillage-")
    assert identifiant != "outillage"
    assert identifiant != nouvel_id_de_generation()


def test_un_atelier_de_tache_ne_prend_jamais_le_dossier_du_manifeste() -> None:
    """`.maestro/outillage/` porte la comptabilité de Maestro, pas un atelier (docs/38 §4.3)."""
    assert chemin_atelier("outillage") == ".maestro/outillage-tache"
    assert chemin_atelier("Outillage") == ".maestro/Outillage-tache"
    assert chemin_atelier("t1") == ".maestro/t1"
    assert CHEMIN_MANIFESTE.startswith(".maestro/outillage/")


@besoin_de_git
def test_un_projet_versionne_prepare_une_branche_et_laisse_la_racine_intacte(
    tmp_path: Path,
) -> None:
    racine = tmp_path / "projets" / "depensio-git"
    racine.mkdir(parents=True)
    (racine / "README.md").write_text("# Dépensio\n", encoding="utf-8")
    _git(racine, "init", "--quiet")
    _git(racine, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(racine, "add", "-A")
    _git(
        racine,
        "-c",
        "user.email=tests@maestro",
        "-c",
        "user.name=Tests",
        "commit",
        "--quiet",
        "-m",
        "socle",
    )
    projet = _projet(racine, vcs=detecter_vcs(racine))

    preparation = generer_outillage(
        projet, _constats(), _recommandation(), source=SOURCE, horodatage=QUAND
    )

    assert preparation.regime == REGIME_BRANCHE
    assert preparation.tache_id.startswith("outillage-")
    assert preparation.branche == branche_de_tache(preparation.tache_id)
    # Le rapport est le même que en place — et la racine n'a rien reçu : la
    # branche attend la fusion sous accord.
    assert [e.chemin for e in preparation.rapport.ecrits][:1] == ["AGENTS.md"]
    assert not (racine / "AGENTS.md").exists()
    # La branche existe, et elle porte l'outillage.
    assert preparation.branche in _git(racine, "branch", "--list", preparation.branche)
    assert "AGENTS.md" in _git(racine, "ls-tree", "-r", "--name-only", preparation.branche)


def _git(racine: Path, *arguments: str) -> str:
    """Lance `git` dans `racine` et rend sa sortie — échoue le test si Git échoue."""
    resultat = subprocess.run(
        [GIT or "git", *arguments],
        cwd=racine,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert resultat.returncode == 0, f"git {' '.join(arguments)} : {resultat.stderr}"
    return resultat.stdout


# --------------------------------------------------------------------------- #
# De bout en bout : analyser un vrai arbre, puis l'outiller
# --------------------------------------------------------------------------- #


def test_de_l_analyse_d_un_projet_reel_a_son_outillage_ecrit(tmp_path: Path) -> None:
    """Le chemin que l'API emprunte : analyser (lecture seule), rédiger, écrire."""
    racine = tmp_path / "projets" / "depensio"
    (racine / "src").mkdir(parents=True)
    (racine / "src" / "app.py").write_text("print('salut')\n", encoding="utf-8")
    (racine / "src" / "modele.py").write_text("X = 1\n", encoding="utf-8")
    (racine / "pyproject.toml").write_text(
        '[project]\nname = "depensio"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    (racine / "package.json").write_text(
        json.dumps({"name": "depensio", "scripts": {"test": "vitest", "build": "tsc"}}),
        encoding="utf-8",
    )
    (racine / "README.md").write_text("# Dépensio\n", encoding="utf-8")
    (racine / ".env").write_text("CLE=secrète\n", encoding="utf-8")

    analyse = analyser(racine, projet_id="prj-0000dead", perimetre=Perimetre())
    fichiers = rediger(analyse.constats, analyse.recommandation)
    rapport = generer(
        racine, fichiers, source=analyse.source_manifeste(), horodatage=QUAND
    )

    assert analyse.resume
    assert (racine / "AGENTS.md").is_file()
    assert rapport.ecrits
    # La `source` du manifeste dit **quelle analyse** a recommandé cet outillage.
    assert _manifeste(racine)["source"]["reference"] == analyse.id
    assert _manifeste(racine)["source"]["projet_id"] == "prj-0000dead"
    # Le secret du projet n'a été ni lu ni recopié dans les instructions.
    assert "secrète" not in (racine / "AGENTS.md").read_text(encoding="utf-8")

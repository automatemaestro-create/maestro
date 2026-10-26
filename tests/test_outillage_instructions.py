"""Un projet importé transmet à ses agents les instructions qu'il porte déjà (#1542).

Mesuré sur #1522 : un clone du dépôt de Maestro, importé par le fil, écrit ses règles
dans `CLAUDE.md`. L'`AGENTS.md` que Maestro y a écrit ne le nommait pas — ses
conventions sortaient d'une liste fermée, les fichiers d'instructions d'agent en étaient
même retirés —, et les agents de Maestro ne chargent jamais `CLAUDE.md`
(`sans_reglages_du_poste`). Les conventions du dépôt n'arrivaient donc à aucun agent :
la personne a dû écrire « lisez CLAUDE.md » dans sa demande.

Ce que la suite garde, du disque au message de la tâche :

① **un fichier d'instructions d'agent que le projet porte est nommé** dans l'`AGENTS.md`
   écrit, en tête des conventions, avec la règle de le lire — qu'il soit connu des tables
   (`CLAUDE.md`) ou **reconnu par le modèle** qui lit le projet, quel que soit son nom
   (docs/41 : aucune liste fermée n'en décide). Le modèle propose, le disque tranche :
   un fichier qu'il n'a pas lu n'entre pas ;
② **ce que Maestro possède n'est pas renvoyé à lui-même** — un `AGENTS.md` ou un pont
   qu'il a écrits en entier ne se nomment pas ; un `AGENTS.md` que le projet portait
   avant lui, oui, pour ce qui entoure le bloc de Maestro ;
③ **le message de la tâche le transmet** : l'outillage déclaré au manifeste part dans le
   message (`OutillageDuProjet.consigne`), et le fichier y est nommé avec sa règle de
   lecture — rien n'entre par le CLI (docs/44, `sans_reglages_du_poste` reste en place).

Ni réseau, ni modèle : le modèle qui lit est un faux fournisseur.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import ClassVar

import pytest

from maestro.outillage import analyser, lire_le_projet, outillage_du_projet
from maestro.outillage.detection import ROLE_INSTRUCTIONS
from maestro.outillage.exploration import CADRE_LECTURE
from maestro.outillage.generation import generer
from maestro.outillage.modele import Analyse
from maestro.outillage.redaction import PORTEE_BLOC, PORTEE_FICHIER, rediger
from maestro.projets.modele import Perimetre, Projet
from maestro.providers.base import ModelProvider

#: Une règle de `CLAUDE.md`, telle qu'un dépôt réel l'écrit — celle de Maestro (#1522).
REGLE = "Jamais de commit direct sur `main`."

CLAUDE_MD = f"# Règles du dépôt\n\n- {REGLE}\n- Commits en Conventional Commits.\n"

#: La phrase qui envoie l'agent lire les instructions du projet (`redaction`).
LIRE = "Lis-les en entier avant de commencer"


@pytest.fixture(autouse=True)
def _maison(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un `Path.home()` sous `tmp_path` — sous Windows, `tmp_path` vit dans `AppData` (#221)."""
    maison = tmp_path / "maison"
    maison.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: maison))
    return maison


class _Lecteur(ModelProvider):
    """Le modèle qui lit le projet : il rejoue ses réponses, une par tour."""

    name: ClassVar[str] = "lecteur-factice"

    def __init__(self, reponses: Sequence[str]) -> None:
        self._reponses = list(reponses)
        self.prompts: list[str] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt: str, *, model: str, system_prompt: str | None = None) -> str:
        self.prompts.append(prompt)
        return self._reponses.pop(0) if len(self._reponses) > 1 else self._reponses[0]


def ecrire(racine: Path, chemin: str, contenu: str) -> Path:
    """Pose un fichier sous `racine`, dossiers parents compris."""
    cible = racine / chemin
    cible.parent.mkdir(parents=True, exist_ok=True)
    cible.write_text(contenu, encoding="utf-8")
    return cible


def projet_python(racine: Path) -> Path:
    """Un projet Python ordinaire : un manifeste qui configure ses tests, un module."""
    ecrire(racine, "pyproject.toml", '[project]\nname = "depensio"\n\n[tool.pytest.ini_options]\n')
    ecrire(racine, "depensio/__init__.py", "")
    ecrire(racine, "README.md", "# Dépensio\n")
    return racine


def agents_md(analyse: Analyse, portees: dict[str, str] | None = None) -> str:
    """Le texte d'`AGENTS.md` que la rédaction tire de cette analyse."""
    (instructions,) = [
        f for f in rediger(analyse.constats, analyse.recommandation, portees=portees)
        if f.chemin == "AGENTS.md"
    ]
    return instructions.contenu


def conventions(texte: str) -> str:
    """La section « Conventions » d'un `AGENTS.md`, sans le reste."""
    return texte.split("## Conventions", 1)[1].split("\n## ", 1)[0]


def lire(analyse: Analyse, lecteur: _Lecteur) -> Analyse:
    """La lecture du modèle jouée jusqu'au bout, comme la Control Tower la joue."""
    return asyncio.run(
        lire_le_projet(analyse, perimetre=Perimetre(), provider=lecteur, modele="factice")
    )


# --------------------------------------------------------------------------- #
# ① Le fichier d'instructions du projet est nommé, en tête des conventions      #
# --------------------------------------------------------------------------- #


def test_agents_md_nomme_le_claude_md_du_projet_et_dit_de_le_lire(tmp_path: Path) -> None:
    """Le défaut de #1522 : `CLAUDE.md` était constaté, puis retiré de la liste à lire."""
    racine = projet_python(tmp_path / "depensio")
    ecrire(racine, "CLAUDE.md", CLAUDE_MD)

    section = conventions(agents_md(analyser(racine)))

    assert "`CLAUDE.md`" in section
    assert LIRE in section
    # En tête : avant les conventions ordinaires, qui ne disent pas comment travailler ici.
    assert section.index("`CLAUDE.md`") < section.index("`README.md`")


def test_un_fichier_d_instructions_qu_aucune_table_ne_connait_est_reconnu_par_le_modele(
    tmp_path: Path,
) -> None:
    """docs/41 : le modèle reconnaît le fichier, quel que soit son nom — les tables l'ignoraient."""
    racine = projet_python(tmp_path / "depensio")
    ecrire(racine, "docs/regles-des-agents.md", f"# Pour les agents\n\n- {REGLE}\n")
    indices = analyser(racine)
    assert "regles-des-agents" not in agents_md(indices), "les tables seules ne le voient pas"
    lecteur = _Lecteur(
        (
            "LISTER: docs\nLIRE: docs/regles-des-agents.md",
            "INSTRUCTIONS: docs/regles-des-agents.md | règles de travail des agents\nFIN",
        )
    )

    analyse = lire(indices, lecteur)

    (piece,) = [p for p in analyse.constats.conventions if p.chemin == "docs/regles-des-agents.md"]
    assert piece.role == ROLE_INSTRUCTIONS
    assert analyse.lecture is not None and analyse.lecture.retenus.conventions == (piece,)
    section = conventions(agents_md(analyse))
    assert "`docs/regles-des-agents.md`" in section and LIRE in section


def test_un_fichier_d_instructions_que_le_modele_n_a_pas_lu_est_ecarte(tmp_path: Path) -> None:
    """Le modèle propose, le disque tranche : sans lecture, le fichier n'entre pas."""
    racine = projet_python(tmp_path / "depensio")
    ecrire(racine, "REGLES.md", f"- {REGLE}\n")
    lecteur = _Lecteur(("INSTRUCTIONS: REGLES.md | règles\nFIN",))

    analyse = lire(analyser(racine), lecteur)

    assert all(p.chemin != "REGLES.md" for p in analyse.constats.conventions)
    assert analyse.lecture is not None
    (ecarte,) = analyse.lecture.ecartes
    assert "REGLES.md" in ecarte.raison and "pas lu" in ecarte.raison


def test_le_modele_apprend_quels_fichiers_d_instructions_les_tables_ont_vus(
    tmp_path: Path,
) -> None:
    """Les indices nomment ce que les tables ont vu ; le cadre dit ce qu'est un tel fichier."""
    racine = projet_python(tmp_path / "depensio")
    ecrire(racine, "CLAUDE.md", CLAUDE_MD)
    lecteur = _Lecteur(("FIN",))

    lire(analyser(racine), lecteur)

    (prompt,) = lecteur.prompts
    assert "CLAUDE.md (instructions d'agent)" in prompt
    assert "INSTRUCTIONS:" in CADRE_LECTURE
    cadre = " ".join(CADRE_LECTURE.split())
    assert "quel que soit son nom" in cadre
    # Vu sur le réel (#1542) : le prompt d'un sous-agent (`.claude/agents/…`) était retenu,
    # et `AGENTS.md` envoyait tout agent suivre les consignes d'un seul.
    assert "ni la définition d'un agent, d'un rôle ou d'une commande en particulier" in cadre


# --------------------------------------------------------------------------- #
# ② Ce que Maestro possède n'est pas renvoyé à lui-même                         #
# --------------------------------------------------------------------------- #


def test_un_fichier_que_maestro_a_ecrit_en_entier_ne_se_nomme_pas(tmp_path: Path) -> None:
    """Un `AGENTS.md` ou un pont de Maestro, relus à la régénération, ne sont pas au projet."""
    racine = projet_python(tmp_path / "depensio")
    ecrire(racine, "AGENTS.md", "# AGENTS.md\n")
    ecrire(racine, "CLAUDE.md", "@AGENTS.md\n")

    section = conventions(
        agents_md(analyser(racine), {"AGENTS.md": PORTEE_FICHIER, "CLAUDE.md": PORTEE_FICHIER})
    )

    assert "`CLAUDE.md`" not in section and "`AGENTS.md`" not in section
    assert LIRE not in section


def test_un_fichier_qui_ne_fait_qu_importer_agents_md_ne_se_nomme_pas(tmp_path: Path) -> None:
    """Un pont écrit par le projet (`@AGENTS.md`) n'a pas de règle à lui : le lire n'apprend rien.
    """
    racine = projet_python(tmp_path / "depensio")
    ecrire(racine, "AGENTS.md", f"# Nos règles\n\n- {REGLE}\n")
    ecrire(racine, "CLAUDE.md", "@AGENTS.md\n")

    analyse = analyser(racine)

    assert all(p.chemin != "CLAUDE.md" for p in analyse.constats.conventions)
    assert "`CLAUDE.md`" not in conventions(agents_md(analyse))


def test_un_agents_md_que_le_projet_portait_se_nomme_hors_du_bloc_de_maestro(
    tmp_path: Path,
) -> None:
    """Maestro n'y écrit qu'un bloc, et seul ce bloc part dans le message : le reste se lit."""
    racine = projet_python(tmp_path / "depensio")
    ecrire(racine, "AGENTS.md", f"# Nos règles\n\n- {REGLE}\n")

    analyse = analyser(racine)
    (entree,) = [e for e in analyse.recommandation.entrees if e.chemin == "AGENTS.md"]
    assert entree.etat == "a-completer"
    (fichier,) = [
        f for f in rediger(analyse.constats, analyse.recommandation) if f.chemin == "AGENTS.md"
    ]
    assert fichier.portee == PORTEE_BLOC
    section = conventions(fichier.contenu)

    assert "`AGENTS.md`, hors du bloc que Maestro y écrit" in section
    assert LIRE in section


# --------------------------------------------------------------------------- #
# ③ Le message de la tâche le transmet                                          #
# --------------------------------------------------------------------------- #


def test_le_message_de_la_tache_transmet_le_fichier_d_instructions_du_projet(
    tmp_path: Path,
) -> None:
    """Du disque au message : analysé, écrit, déclaré au manifeste — puis transmis à l'agent."""
    racine = projet_python(tmp_path / "depensio")
    ecrire(racine, "CLAUDE.md", CLAUDE_MD)
    analyse = analyser(racine, projet_id="prj-0000dead")
    rapport = generer(
        racine,
        rediger(analyse.constats, analyse.recommandation),
        source=analyse.source_manifeste(),
    )
    assert not rapport.refus
    projet = Projet(id="prj-0000dead", nom="Dépensio", racine=racine.as_posix())

    consigne = outillage_du_projet(projet).consigne()

    assert "`CLAUDE.md`" in consigne and LIRE in consigne
    # Le fichier du projet n'entre pas de lui-même : il est nommé, l'agent le lit.
    assert REGLE not in consigne
    # Et le fichier du projet n'a pas été touché.
    assert (racine / "CLAUDE.md").read_text(encoding="utf-8") == CLAUDE_MD

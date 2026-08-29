"""Un numéro de document, un document (#742).

Deux notes de décision ont porté le numéro `31` — `31-decision-surface-ecriture-agents.md`
et `31-decision-cran-orchestrateur.md` — pendant plusieurs jours sur `main`. Ce ticket
en a renommé une en `32` ; cette suite est ce qui empêche la troisième.

**Le défaut n'était pas une inattention, et c'est pourquoi il faut une machine.**
#354, #647 et #737 ont été instruits la même journée, trois auteurs lisant le même
`docs/` de la veille. Chacune des trois PR était **juste isolément**, git n'avait rien
à signaler puisque les noms de fichiers diffèrent, et aucune revue ne pouvait attraper
une collision qui n'existe qu'entre deux branches. La leçon écrite en
[docs/33 §11](../docs/33-decision-surveillance-run.md) — *le numéro d'un document se
réserve quand on ouvre le ticket, pas quand on écrit le fichier* — est une règle lue :
elle ne tient que si quelque chose la vérifie au moment du merge, c'est-à-dire ici.

**Trois invariants, et chacun prouve son motif avant de balayer.** Un test qui se
contente de constater que le dépôt d'aujourd'hui va bien rend un ✓ sur une question
jamais posée : chaque classe monte donc d'abord un échantillon **fautif** et vérifie
que la sonde le voit, puis seulement ensuite passe sur le corpus réel.

1. `TestUnNumeroUnDocument` — deux fichiers de `docs/` ne partagent pas un préfixe
   numérique. C'est la panne elle-même, dans sa forme la plus courte.
2. `TestTableauDuReadme` — le tableau des documents du `README.md` ne porte qu'une
   ligne par numéro, et chaque ligne pointe vers un fichier **qui existe** et **dont le
   préfixe est ce numéro**. Le doublon s'y voyait à l'œil nu (deux lignes « 31 ») sans
   que personne ne le voie : c'est le seul endroit du dépôt où les deux notes étaient
   côte à côte.
3. `TestTitreEtNumeroDaccord` — un document dont le titre de niveau 1 **commence par un
   nombre** porte le sien. L'invariant est conditionnel à dessein : les documents
   antérieurs à `25` n'ouvrent pas sur leur numéro (« # Cahier des charges — Maestro »),
   et l'exiger d'eux ferait rougir vingt-cinq fichiers qui ne sont pour rien dans la
   panne. Sous cette forme il attrape exactement la dérive qu'un renommage crée : le
   fichier suit, le titre reste.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent
DOCS = RACINE / "docs"

#: `31-decision-cran-orchestrateur.md` → `31`. Les documents sans préfixe numérique
#: (il n'y en a pas aujourd'hui) sont ignorés plutôt que refusés : cette suite garde
#: l'unicité d'un numéro, elle n'impose pas d'en porter un.
PREFIXE = re.compile(r"^(\d+)-")

#: Une ligne du tableau des documents du `README.md` : `| 31 | [Titre](./docs/31-….md) | … |`.
LIGNE_TABLEAU = re.compile(r"^\|\s*(\d+)\s*\|\s*\[[^\]]*\]\(\./(docs/[^)]+\.md)\)")

#: Un titre de niveau 1 qui s'ouvre sur son numéro : `# 32 — Le cran …`.
TITRE_NUMEROTE = re.compile(r"^#\s+(\d+)\s")


def numeros_par_fichier(repertoire: Path) -> dict[str, list[str]]:
    """Les documents d'un répertoire, groupés par préfixe numérique."""
    groupes: dict[str, list[str]] = defaultdict(list)
    for chemin in sorted(repertoire.glob("*.md")):
        capture = PREFIXE.match(chemin.name)
        if capture:
            groupes[capture.group(1)].append(chemin.name)
    return dict(groupes)


def doublons(groupes: dict[str, list[str]]) -> dict[str, list[str]]:
    return {numero: noms for numero, noms in groupes.items() if len(noms) > 1}


def lignes_du_tableau(readme: str) -> list[tuple[str, str]]:
    """Les couples (numéro, chemin) du tableau des documents."""
    releve: list[tuple[str, str]] = []
    for ligne in readme.splitlines():
        capture = LIGNE_TABLEAU.match(ligne)
        if capture:
            releve.append((capture.group(1), capture.group(2)))
    return releve


@pytest.fixture(scope="module")
def readme() -> str:
    return (RACINE / "README.md").read_text(encoding="utf-8")


class TestUnNumeroUnDocument:
    """Deux documents ne portent pas le même numéro."""

    def test_la_sonde_voit_le_doublon_quon_vient_de_defaire(self, tmp_path: Path) -> None:
        """L'échantillon fautif est la panne réelle, avec ses deux vrais noms."""
        faux = tmp_path / "docs"
        faux.mkdir()
        (faux / "31-decision-surface-ecriture-agents.md").write_text("# 31 — A\n", encoding="utf-8")
        (faux / "31-decision-cran-orchestrateur.md").write_text("# 31 — B\n", encoding="utf-8")
        (faux / "33-decision-surveillance-run.md").write_text("# 33 — C\n", encoding="utf-8")

        vus = doublons(numeros_par_fichier(faux))

        assert list(vus) == ["31"]
        assert vus["31"] == [
            "31-decision-cran-orchestrateur.md",
            "31-decision-surface-ecriture-agents.md",
        ]

    def test_le_corpus_reel_na_quun_document_par_numero(self) -> None:
        groupes = numeros_par_fichier(DOCS)

        # Le motif a de quoi parler : sans cette ligne, un `docs/` vide passerait.
        assert len(groupes) > 20

        assert doublons(groupes) == {}


class TestTableauDuReadme:
    """Le tableau des documents ne porte qu'une ligne par numéro, et elle mène quelque part."""

    def test_la_sonde_voit_les_deux_lignes_31(self) -> None:
        """L'échantillon fautif est le tableau tel qu'il était avant ce ticket."""
        fautif = "\n".join(
            [
                "| 29 | [Le run](./docs/29-decision-run-objet-de-premier-plan.md) | … |",
                "| 31 | [La surface](./docs/31-decision-surface-ecriture-agents.md) | … |",
                "| 31 | [Le cran](./docs/31-decision-cran-orchestrateur.md) | … |",
                "| 33 | [La surveillance](./docs/33-decision-surveillance-run.md) | … |",
            ]
        )

        numeros = [numero for numero, _ in lignes_du_tableau(fautif)]

        assert numeros == ["29", "31", "31", "33"]
        assert len(set(numeros)) < len(numeros)

    def test_un_numero_une_ligne(self, readme: str) -> None:
        numeros = [numero for numero, _ in lignes_du_tableau(readme)]

        assert len(numeros) > 20
        assert len(set(numeros)) == len(numeros), "un numéro apparaît sur deux lignes du tableau"

    def test_chaque_ligne_mene_au_document_de_son_numero(self, readme: str) -> None:
        for numero, chemin in lignes_du_tableau(readme):
            cible = RACINE / chemin
            assert cible.is_file(), f"le tableau renvoie à {chemin}, qui n'existe pas"
            assert cible.name.startswith(f"{numero}-"), (
                f"la ligne « {numero} » renvoie à {cible.name}, qui porte un autre numéro"
            )


class TestTitreEtNumeroDaccord:
    """Un document qui ouvre sur un numéro ouvre sur le sien."""

    def test_la_sonde_voit_le_titre_reste_en_arriere(self, tmp_path: Path) -> None:
        """C'est la dérive qu'un renommage crée : le fichier suit, le titre reste."""
        reste = tmp_path / "32-decision-cran-orchestrateur.md"
        reste.write_text("# 31 — Le cran « orchestrateur »\n", encoding="utf-8")

        capture = TITRE_NUMEROTE.match(reste.read_text(encoding="utf-8").splitlines()[0])

        assert capture is not None
        assert capture.group(1) != "32"

    def test_le_corpus_reel_est_daccord_avec_lui_meme(self) -> None:
        verifies = 0
        for chemin in sorted(DOCS.glob("*.md")):
            prefixe = PREFIXE.match(chemin.name)
            premiere = chemin.read_text(encoding="utf-8").splitlines()[0]
            titre = TITRE_NUMEROTE.match(premiere)
            if not prefixe or not titre:
                continue
            verifies += 1
            assert titre.group(1) == prefixe.group(1), (
                f"{chemin.name} s'ouvre sur « {premiere.strip()} »"
            )

        # Les notes de décision (25, 27→34) ouvrent toutes sur leur numéro : si ce
        # compte tombe à zéro, l'invariant ne porte plus sur rien.
        assert verifies >= 8

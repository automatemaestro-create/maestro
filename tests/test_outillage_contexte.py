"""L'outillage du projet transmis aux agents — et rien qui entre tout seul (#1032, docs/38 §5).

Le lot 4/7 de #1020 avait livré sans tests (convention de découpage,
[docs/10 §5.1](../docs/10-workflow-git.md)) ; cette suite les rend, et elle garde
le quatrième point du critère de #1035 : *la transmission de l'outillage aux
agents, **sans configuration ambiante***.

La frontière de docs/38 §5 a deux moitiés, et une seule des deux ne prouve rien :

① **ce qui est transmis** — `maestro.outillage.contexte`. Le **manifeste décide,
   pas le disque** : un fichier d'outillage présent dans la racine mais absent de
   `.maestro/outillage/manifeste.json` n'entre pas. C'est la même règle qu'à
   l'écriture (docs/38 §4.2), appliquée dans l'autre sens, et c'est elle qui
   distingue « Maestro injecte » de « le projet s'impose » — les deux donnent
   sinon le même contexte au même agent ;
② **ce qui est refusé** — `maestro.providers.claude`. `setting_sources=[]` et
   `skills=[]` ferment la porte que `strict_mcp_config` laissait ouverte sur les
   réglages, les fichiers d'instructions et les skills du `cwd`. Ici la garde
   porte sur **chaque** construction d'`ClaudeAgentOptions` du module, relevée sur
   l'AST : une seule qui les oublierait rouvrirait la porte pour le chemin qu'elle
   sert, et un test qui n'en vérifierait qu'une serait vert sans rien dire des
   autres. La sonde est **prouvée sur un source fautif** avant de balayer.

Entre les deux, le point de couture : `maestro.agents.runtime` lit l'outillage
dans la **racine du projet** — pas dans l'espace dérivé, qu'un worktree amputerait
de tout ce qui n'est pas commité — et le pose dans le **message de la tâche**.
Sans projet outillé, le message est celui d'avant ce lot, à la ligne près : c'est
vérifié ici par comparaison, parce que « ça n'ajoute rien » ne se lit pas dans un
diff de prompt.

Trois propriétés de docs/38 §5.1 sont éprouvées nommément, parce qu'elles sont le
genre de chose qu'un correctif bien intentionné défait : la **portée déclarée est
la portée transmise** (un `"bloc"` transmet le bloc, jamais le fichier qui
l'entoure), un **skill entre par son index** et jamais par son corps, et
**`allowed-tools:` est inerte** — signalé comme ignoré, pour que l'inertie se voie
au lieu de se supposer.

Ni réseau, ni modèle : le contexte est une lecture de fichiers, et le fournisseur
n'est lu qu'en tant que **texte**.
"""

from __future__ import annotations

import ast
import asyncio
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from maestro.agents import DEFAULT_TOOLS, AgentRuntime, RoleProfile
from maestro.outillage import (
    BALISE_DEBUT,
    BALISE_FIN,
    CHAMP_OUTILS,
    CHEMIN_MANIFESTE,
    ROLES_TRANSMIS,
    VERSION_MANIFESTE,
    outillage_du_projet,
)
from maestro.outillage.contexte import TITRE_RACINE_OUTILLEE, Bornes
from maestro.projets.modele import Perimetre, Projet
from maestro.projets.racine import detecter_vcs
from maestro.providers.base import ModelProvider
from maestro.providers.claude import sans_reglages_du_poste, sans_skills_du_poste

#: Le module du fournisseur, lu comme **texte** : c'est là que vit la moitié
#: « rien n'entre tout seul » de la frontière (docs/38 §5.3).
CLAUDE_PY = Path(__file__).resolve().parents[1] / "maestro" / "providers" / "claude.py"

#: Ce qu'un `SKILL.md` du projet porte dans son frontmatter. Le corps porte un
#: **mot-témoin** : il ne doit ressortir nulle part, la divulgation étant
#: progressive (docs/38 §5.1).
SKILL_TESTS = f"""---
name: lancer-les-tests
description: Jouer la suite de tests du projet et lire son verdict.
{CHAMP_OUTILS}: Bash Read Write
---

# Lancer les tests

corps-du-skill-jamais-transmis

```bash
pytest
```
"""


@pytest.fixture(autouse=True)
def _maison_isolee(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un `Path.home()` sous `tmp_path`, comme les tests du socle (#221).

    Indispensable sous Windows : le `tmp_path` de pytest vit sous `AppData`, que
    `valider_racine` interdit à juste titre — sans cette isolation, les racines
    de projet de ce fichier seraient refusées avant le test.
    """
    maison = tmp_path / "maison"
    maison.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: maison))
    return maison


# --------------------------------------------------------------------------- #
# Fabriques : un projet outillé à la main, manifeste compris                    #
# --------------------------------------------------------------------------- #


def ecrire(racine: Path, chemin: str, contenu: str) -> Path:
    """Pose un fichier sous `racine`, dossiers parents compris."""
    cible = racine / chemin
    cible.parent.mkdir(parents=True, exist_ok=True)
    cible.write_text(contenu, encoding="utf-8")
    return cible


def poser_manifeste(racine: Path, entrees: list[dict[str, Any]], *, version: Any = None) -> None:
    """Écrit le manifeste de `racine` avec `entrees` telles quelles."""
    ecrire(
        racine,
        CHEMIN_MANIFESTE,
        json.dumps(
            {
                "manifeste": VERSION_MANIFESTE if version is None else version,
                "genere_par": "maestro 0.0.0",
                "genere_le": "2026-09-20T09:12:00+00:00",
                "source": {"type": "analyse", "projet_id": "prj-0000dead"},
                "entrees": entrees,
            },
            ensure_ascii=False,
        ),
    )


def entree(chemin: str, role: str, *, portee: str = "fichier") -> dict[str, Any]:
    """Une entrée de manifeste, dans la forme de docs/38 §4.1."""
    return {
        "chemin": chemin,
        "role": role,
        "portee": portee,
        "empreinte": "sha256:00",
        "genere_le": "2026-09-20T09:12:00+00:00",
    }


def projet_outille(racine: Path, *, perimetre: Perimetre | None = None) -> Projet:
    """Un projet outillé à la main : instructions, un pont, un skill, et le manifeste."""
    racine.mkdir(parents=True, exist_ok=True)
    ecrire(racine, "AGENTS.md", "# Dépensio\n\nmot-temoin-des-instructions\n")
    ecrire(racine, "CLAUDE.md", "@AGENTS.md\n")
    ecrire(racine, ".agents/skills/lancer-les-tests/SKILL.md", SKILL_TESTS)
    poser_manifeste(
        racine,
        [
            entree("AGENTS.md", "instructions"),
            entree("CLAUDE.md", "pont"),
            entree(".agents/skills/lancer-les-tests/SKILL.md", "skill"),
        ],
    )
    return Projet(
        id="prj-0000dead",
        nom="Dépensio",
        racine=racine.as_posix(),
        perimetre=perimetre or Perimetre(),
    )


# --------------------------------------------------------------------------- #
# ① Le manifeste décide, pas le disque                                          #
# --------------------------------------------------------------------------- #


def test_l_outillage_declare_au_manifeste_est_transmis(tmp_path: Path) -> None:
    """Le cas nominal : les instructions en texte, les skills en index."""
    projet = projet_outille(tmp_path / "depensio")

    outillage = outillage_du_projet(projet)

    assert outillage.manifeste == CHEMIN_MANIFESTE
    assert outillage.chemin_instructions == "AGENTS.md"
    assert "mot-temoin-des-instructions" in outillage.instructions
    assert [skill.nom for skill in outillage.skills] == ["lancer-les-tests"]
    assert outillage.skills[0].chemin == ".agents/skills/lancer-les-tests/SKILL.md"
    assert not outillage.vide


def test_un_fichier_present_mais_absent_du_manifeste_n_entre_pas(tmp_path: Path) -> None:
    """La règle de docs/38 §4.2 dans l'autre sens : Maestro ne transmet que ce qu'il déclare.

    C'est **la** propriété qui sépare « Maestro injecte » de « le projet
    s'impose ». Le fichier posé ici est exactement celui qu'un projet importé
    pourrait porter depuis toujours ; il ne doit rien dire à l'agent.
    """
    racine = tmp_path / "depensio"
    projet = projet_outille(racine)
    ecrire(racine, ".agents/skills/intrus/SKILL.md", "---\nname: intrus\ndescription: x\n---\n")
    ecrire(racine, "AUTRE.md", "consignes-non-declarees\n")

    outillage = outillage_du_projet(projet)

    assert [skill.nom for skill in outillage.skills] == ["lancer-les-tests"]
    assert "consignes-non-declarees" not in outillage.consigne()


def test_un_projet_sans_manifeste_rend_un_outillage_vide_sans_lever(tmp_path: Path) -> None:
    """Le cas le plus courant — un projet non outillé n'est pas une panne."""
    racine = tmp_path / "depensio"
    racine.mkdir(parents=True)
    ecrire(racine, "AGENTS.md", "# écrit avant Maestro\n")
    projet = Projet(id="prj-0000dead", nom="Dépensio", racine=racine.as_posix())

    outillage = outillage_du_projet(projet)

    assert outillage.vide
    assert outillage.manifeste == ""
    assert outillage.consigne() == ""


def test_sans_projet_il_n_y_a_rien_a_transmettre() -> None:
    """Une tâche sans `projet_id` : le message de l'agent est celui d'avant ce lot."""
    outillage = outillage_du_projet(None)

    assert outillage.vide
    assert outillage.consigne() == ""


def test_un_manifeste_illisible_ne_se_distingue_pas_d_un_projet_non_outille(
    tmp_path: Path,
) -> None:
    """Trois cas qui ne donnent rien à transmettre, donc un seul comportement."""
    racine = tmp_path / "depensio"
    racine.mkdir(parents=True)
    ecrire(racine, CHEMIN_MANIFESTE, "{ ceci n'est pas du JSON")
    projet = Projet(id="prj-0000dead", nom="Dépensio", racine=racine.as_posix())

    assert outillage_du_projet(projet).vide


def test_un_manifeste_d_une_version_inconnue_refuse_et_le_dit(tmp_path: Path) -> None:
    """Un refus n'est pas un silence : il se corrige autrement, donc il se nomme."""
    racine = tmp_path / "depensio"
    projet = projet_outille(racine)
    poser_manifeste(racine, [entree("AGENTS.md", "instructions")], version=99)

    outillage = outillage_du_projet(projet)

    assert outillage.vide
    assert outillage.manifeste == CHEMIN_MANIFESTE
    assert [ecarte.role for ecarte in outillage.non_transmis] == ["manifeste"]
    assert "99" in outillage.non_transmis[0].raison


def test_les_roles_transmis_sont_les_deux_seuls_et_les_autres_sont_nommes(
    tmp_path: Path,
) -> None:
    """Un pont doublerait le texte qu'il désigne ; un script s'exécute, il ne se lit pas."""
    projet = projet_outille(tmp_path / "depensio")

    outillage = outillage_du_projet(projet)

    assert ROLES_TRANSMIS == frozenset({"instructions", "skill"})
    ponts = [ecarte for ecarte in outillage.non_transmis if ecarte.role == "pont"]
    assert [ecarte.chemin for ecarte in ponts] == ["CLAUDE.md"]
    assert "AGENTS.md" in ponts[0].raison


# --------------------------------------------------------------------------- #
# La portée déclarée est la portée transmise                                    #
# --------------------------------------------------------------------------- #


def test_une_portee_bloc_transmet_le_bloc_et_pas_ce_qui_l_entoure(tmp_path: Path) -> None:
    """docs/38 §4.2 dans l'autre sens : ce qui entoure le bloc n'a été déclaré par personne."""
    racine = tmp_path / "depensio"
    racine.mkdir(parents=True)
    ecrire(
        racine,
        "AGENTS.md",
        "# Écrit avant Maestro\n\nprose-du-projet\n\n"
        f"{BALISE_DEBUT}\ncontenu-ecrit-par-maestro\n{BALISE_FIN}\n\napres-le-bloc\n",
    )
    poser_manifeste(racine, [entree("AGENTS.md", "instructions", portee="bloc")])
    projet = Projet(id="prj-0000dead", nom="Dépensio", racine=racine.as_posix())

    outillage = outillage_du_projet(projet)

    assert outillage.instructions == "contenu-ecrit-par-maestro"
    assert "prose-du-projet" not in outillage.instructions
    assert "apres-le-bloc" not in outillage.instructions


def test_une_portee_bloc_sans_balises_ne_retombe_jamais_sur_le_fichier_entier(
    tmp_path: Path,
) -> None:
    """Un repli qui **élargit** la portée est exactement ce qu'un garde-fou ne doit pas faire."""
    racine = tmp_path / "depensio"
    racine.mkdir(parents=True)
    ecrire(racine, "AGENTS.md", "# Écrit avant Maestro\n\nprose-du-projet\n")
    poser_manifeste(racine, [entree("AGENTS.md", "instructions", portee="bloc")])
    projet = Projet(id="prj-0000dead", nom="Dépensio", racine=racine.as_posix())

    outillage = outillage_du_projet(projet)

    assert outillage.instructions == ""
    assert "prose-du-projet" not in outillage.consigne()
    assert [ecarte.raison for ecarte in outillage.non_transmis] == ["fichier vide ou illisible"]


def test_une_balise_de_fin_manquante_ne_transmet_rien(tmp_path: Path) -> None:
    """Des balises dépareillées ne font pas un bloc — la même règle qu'à l'écriture (#1033)."""
    racine = tmp_path / "depensio"
    racine.mkdir(parents=True)
    ecrire(racine, "AGENTS.md", f"avant\n{BALISE_DEBUT}\nsans-fin\n")
    poser_manifeste(racine, [entree("AGENTS.md", "instructions", portee="bloc")])
    projet = Projet(id="prj-0000dead", nom="Dépensio", racine=racine.as_posix())

    assert outillage_du_projet(projet).instructions == ""


def test_un_second_fichier_d_instructions_est_refuse_en_nommant_le_premier(
    tmp_path: Path,
) -> None:
    """Deux textes d'instructions se contrediraient, et rien ne dirait lequel fait foi."""
    racine = tmp_path / "depensio"
    projet = projet_outille(racine)
    ecrire(racine, "AUTRE.md", "# second\n")
    poser_manifeste(
        racine,
        [entree("AGENTS.md", "instructions"), entree("AUTRE.md", "instructions")],
    )

    outillage = outillage_du_projet(projet)

    assert outillage.chemin_instructions == "AGENTS.md"
    refus = [ecarte for ecarte in outillage.non_transmis if ecarte.chemin == "AUTRE.md"]
    assert "AGENTS.md" in refus[0].raison


# --------------------------------------------------------------------------- #
# Un skill entre par son index, jamais par son corps — et `allowed-tools` est inerte #
# --------------------------------------------------------------------------- #


def test_le_corps_d_un_skill_n_est_jamais_transmis(tmp_path: Path) -> None:
    """La divulgation progressive de la spécification, et le coût de l'index avec elle."""
    projet = projet_outille(tmp_path / "depensio")

    outillage = outillage_du_projet(projet)

    assert "corps-du-skill-jamais-transmis" not in outillage.consigne()
    assert outillage.skills[0].description.startswith("Jouer la suite de tests")
    assert "pytest" not in outillage.consigne()


def test_allowed_tools_est_signale_comme_ignore_jamais_honore(tmp_path: Path) -> None:
    """docs/38 §5.1 : l'inertie se **voit** au lieu de se supposer.

    Le champ n'est ni lu comme une permission ni silencieusement sauté : il sort
    dans `non_transmis`, et la consigne dit à l'agent qu'un `allowed-tools:` d'un
    `SKILL.md` est sans effet sur ses outils.
    """
    projet = projet_outille(tmp_path / "depensio")

    outillage = outillage_du_projet(projet)

    signale = [ecarte for ecarte in outillage.non_transmis if ecarte.role == CHAMP_OUTILS]
    assert len(signale) == 1
    assert "une permission se déclare par une personne" in signale[0].raison
    assert CHAMP_OUTILS in outillage.consigne()
    assert "sans effet" in outillage.consigne()


def test_un_skill_sans_frontmatter_est_ecarte_avec_sa_raison(tmp_path: Path) -> None:
    """Ce qui n'est pas lisible est nommé : un silence et un refus ne se corrigent pas pareil."""
    racine = tmp_path / "depensio"
    projet = projet_outille(racine)
    ecrire(racine, ".agents/skills/muet/SKILL.md", "# pas de frontmatter\n")
    poser_manifeste(racine, [entree(".agents/skills/muet/SKILL.md", "skill")])

    outillage = outillage_du_projet(projet)

    assert outillage.skills == ()
    assert outillage.non_transmis[0].raison == "SKILL.md sans frontmatter lisible"


def test_un_skill_sans_nom_declare_prend_celui_de_son_dossier(tmp_path: Path) -> None:
    """La spécification impose leur égalité, et le dossier est celui qu'on a ouvert."""
    racine = tmp_path / "depensio"
    projet = projet_outille(racine)
    ecrire(racine, ".agents/skills/construire/SKILL.md", "---\ndescription: bâtir\n---\n")
    poser_manifeste(racine, [entree(".agents/skills/construire/SKILL.md", "skill")])

    outillage = outillage_du_projet(projet)

    assert [skill.nom for skill in outillage.skills] == ["construire"]


@pytest.mark.parametrize(
    ("ecrite", "lue"),
    [
        ('"Assembler le carnet : sommaire compris"', "Assembler le carnet : sommaire compris"),
        ('"un \\"mot\\" cité, un chemin C:\\\\chants"', 'un "mot" cité, un chemin C:\\chants'),
        ("'l''index : cité'", "l'index : cité"),
        ("Jouer la suite", "Jouer la suite"),
    ],
)
def test_une_description_citee_se_lit_comme_yaml_la_lit(
    tmp_path: Path, ecrite: str, lue: str
) -> None:
    """#1350 : une description écrite par le modèle est citée, et l'index la rend sans guillemets.

    Maestro cite la description d'un skill (un « : » à la française la rendait illisible
    en YAML) : l'index que l'agent reçoit doit dire la phrase, pas son échappement.
    """
    racine = tmp_path / "depensio"
    projet = projet_outille(racine)
    ecrire(
        racine,
        ".agents/skills/assembler/SKILL.md",
        f"---\nname: assembler\ndescription: {ecrite}\n---\n",
    )
    poser_manifeste(racine, [entree(".agents/skills/assembler/SKILL.md", "skill")])

    (skill,) = outillage_du_projet(projet).skills

    assert skill.description == lue


def test_l_index_est_plafonne_et_le_dit(tmp_path: Path) -> None:
    """Au-delà, l'index coûterait plus cher que le travail qu'il sert."""
    racine = tmp_path / "depensio"
    projet = projet_outille(racine)
    entrees = []
    for index in range(5):
        chemin = f".agents/skills/skill{index}/SKILL.md"
        ecrire(racine, chemin, f"---\nname: skill{index}\ndescription: n° {index}\n---\n")
        entrees.append(entree(chemin, "skill"))
    poser_manifeste(racine, entrees)

    outillage = outillage_du_projet(projet, bornes=Bornes(skills_max=2))

    assert len(outillage.skills) == 2
    plafonnes = [e for e in outillage.non_transmis if "plafonné" in e.raison]
    assert len(plafonnes) == 3


def test_des_instructions_trop_longues_sont_tronquees_et_le_disent(tmp_path: Path) -> None:
    """Un contexte tronqué et un contexte complet ne disent pas la même chose."""
    racine = tmp_path / "depensio"
    projet = projet_outille(racine)
    ecrire(racine, "AGENTS.md", "x" * 500)
    poser_manifeste(racine, [entree("AGENTS.md", "instructions")])

    outillage = outillage_du_projet(projet, bornes=Bornes(instructions_max=100))

    assert outillage.instructions_tronquees is True
    assert len(outillage.instructions) == 100
    assert "tronqué à 100 caractères" in outillage.consigne()


# --------------------------------------------------------------------------- #
# Les chemins d'un manifeste : quatre refus, chacun avec sa raison               #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("chemin", "attendu"),
    [
        ("../dehors/AGENTS.md", "chemin hors de la racine du projet"),
        ("/etc/passwd", "chemin hors de la racine du projet"),
        ("C:/Windows/system.ini", "chemin hors de la racine du projet"),
        ("PAS-LA.md", "déclaré au manifeste, absent du disque"),
        (".agents/skills", "n'est pas un fichier"),
    ],
)
def test_un_chemin_qui_ne_designe_pas_une_piece_du_projet_est_refuse_motive(
    tmp_path: Path, chemin: str, attendu: str
) -> None:
    """Un manifeste est un fichier du disque comme un autre : il ne doit rien pouvoir atteindre.

    Le cas `..` n'est pas théorique — c'est ce qu'un manifeste recopié d'un autre
    projet, ou écrit à la main, produit en premier.
    """
    racine = tmp_path / "depensio"
    projet = projet_outille(racine)
    ecrire(tmp_path / "dehors", "AGENTS.md", "instructions-du-dehors\n")
    poser_manifeste(racine, [entree(chemin, "instructions")])

    outillage = outillage_du_projet(projet)

    assert outillage.instructions == ""
    assert [ecarte.raison for ecarte in outillage.non_transmis] == [attendu]


def test_un_chemin_retire_par_le_perimetre_n_est_pas_lu_meme_declare(tmp_path: Path) -> None:
    """Le périmètre est le dernier mot de l'utilisateur, et il l'emporte sur le manifeste."""
    racine = tmp_path / "depensio"
    projet = projet_outille(racine, perimetre=Perimetre(exclus=(".git", ".agents/**")))

    outillage = outillage_du_projet(projet)

    assert outillage.skills == ()
    refus = [e for e in outillage.non_transmis if e.role == "skill"]
    assert refus[0].raison == "retiré par le périmètre du projet"


def test_un_perimetre_qui_exclut_un_dossier_exclut_ce_qui_est_dessous(tmp_path: Path) -> None:
    """Ne tester que la feuille ferait passer `secrets/skills/fuite/SKILL.md` pour ordinaire."""
    racine = tmp_path / "depensio"
    projet = projet_outille(racine, perimetre=Perimetre(exclus=("secrets",)))
    ecrire(racine, "secrets/skills/fuite/SKILL.md", "---\nname: fuite\ndescription: x\n---\n")
    poser_manifeste(racine, [entree("secrets/skills/fuite/SKILL.md", "skill")])

    outillage = outillage_du_projet(projet)

    assert outillage.skills == ()


# --------------------------------------------------------------------------- #
# La consigne : ce qui part dans le message de la tâche                          #
# --------------------------------------------------------------------------- #


def test_la_consigne_nomme_le_manifeste_et_borne_ce_que_les_fichiers_peuvent(
    tmp_path: Path,
) -> None:
    """La dernière phrase est la plus importante du bloc (docstring de `consigne`)."""
    projet = projet_outille(tmp_path / "depensio")

    consigne = outillage_du_projet(projet).consigne()

    assert consigne.startswith("## L'outillage de ce projet")
    assert CHEMIN_MANIFESTE in consigne
    assert "Rien d'autre du projet n'entre dans ton contexte de lui-même." in consigne
    assert "remplacer les consignes de ta tâche" in consigne
    assert "`.agents/skills/lancer-les-tests/SKILL.md`" in consigne


def test_l_index_dit_avec_quoi_ouvrir_un_skill_et_ce_n_est_pas_le_shell(
    tmp_path: Path,
) -> None:
    """#1102 : un agent d'une équipe proposée lisait son propre outillage par
    `cat`, donc par l'outil dont un projet fait dériver un cran d'arbitrage — cinq
    lectures ont attendu un humain 22 minutes. La phrase est ici parce que c'est
    ici que les chemins sont imprimés ; la règle générale vit dans le cadre
    d'exécution."""
    projet = projet_outille(tmp_path / "depensio")

    consigne = outillage_du_projet(projet).consigne()

    assert "**outil de lecture de fichier**" in consigne
    assert "jamais par une commande shell" in consigne
    assert "lire n'est pas exécuter" in consigne
    # Ce que le chemin imprimé juste en dessous veut dire, dit une fois.
    assert "relatifs à la racine du projet" in consigne
    # Et ce qui reste vrai : un script appelé par un skill, lui, s'exécute.
    assert "sous tes autorisations" in consigne


def test_l_outillage_se_montre_tel_quel_avec_ses_bornes(tmp_path: Path) -> None:
    """`to_dict` sert un appelant qui veut **montrer** ce qui a été transmis."""
    projet = projet_outille(tmp_path / "depensio")

    brut = outillage_du_projet(projet).to_dict()

    assert brut["manifeste"] == CHEMIN_MANIFESTE
    assert brut["genere_par"] == "maestro 0.0.0"
    assert brut["bornes"]["lecture_seule"] is True
    assert brut["bornes"]["execution"] == "aucune"
    assert brut["skills"][0]["nom"] == "lancer-les-tests"


# --------------------------------------------------------------------------- #
# ② Rien n'entre tout seul — la porte fermée chez le fournisseur                 #
# --------------------------------------------------------------------------- #


def _options_sans_garde(source: str) -> list[int]:
    """Les lignes des `ClaudeAgentOptions(…)` qui n'arment pas les deux verrous.

    Relevé sur l'AST : c'est la seule façon de parler de **toutes** les
    constructions du module, y compris celles qu'un lot futur ajoutera. Un
    `grep` compterait les occurrences des deux mots sans dire s'ils sont dans la
    bonne construction.
    """
    manquants: list[int] = []
    for noeud in ast.walk(ast.parse(source)):
        if not isinstance(noeud, ast.Call):
            continue
        appele = noeud.func
        nom = appele.attr if isinstance(appele, ast.Attribute) else getattr(appele, "id", "")
        if nom != "ClaudeAgentOptions":
            continue
        passes = {mot.arg for mot in noeud.keywords if mot.arg}
        if not {"setting_sources", "skills"} <= passes:
            manquants.append(noeud.lineno)
    return manquants


def test_la_sonde_de_la_porte_voit_une_construction_qui_oublie_un_verrou() -> None:
    """L'échantillon fautif, avant le verdict : sans lui, le balayage serait vert par accident."""
    fautif = "options = ClaudeAgentOptions(cwd=chemin, skills=[])\n"
    correct = "options = ClaudeAgentOptions(cwd=chemin, setting_sources=[], skills=[])\n"

    assert _options_sans_garde(fautif) == [1]
    assert _options_sans_garde(correct) == []
    assert _options_sans_garde("options = AutreChose(cwd=chemin)\n") == []


def test_chaque_session_claude_ferme_les_reglages_et_les_skills_du_poste() -> None:
    """docs/38 §5.3 : la porte que `strict_mcp_config` laissait ouverte, fermée partout.

    « Partout » est le mot : une construction qui les oublierait rouvrirait la
    porte pour le seul chemin qu'elle sert — et c'est le genre d'écart qu'on ne
    voit pas, puisque tout continue de marcher.
    """
    source = CLAUDE_PY.read_text(encoding="utf-8")

    manquants = _options_sans_garde(source)

    assert manquants == [], f"ClaudeAgentOptions sans setting_sources/skills, lignes {manquants}"
    assert source.count("ClaudeAgentOptions(") >= 3


def test_les_deux_verrous_rendent_une_liste_vide_neuve_a_chaque_appel() -> None:
    """Une fonction, pas une constante : le SDK garde la liste qu'on lui donne."""
    premiere = sans_reglages_du_poste()
    seconde = sans_reglages_du_poste()

    assert premiere == [] and seconde == []
    assert premiere is not seconde
    assert sans_skills_du_poste() == []
    assert sans_skills_du_poste() is not sans_skills_du_poste()


def test_les_deux_verrous_ne_se_remplacent_pas_l_un_l_autre() -> None:
    """`strict_mcp_config` ferme les serveurs MCP ; les deux autres, tout le reste."""
    source = CLAUDE_PY.read_text(encoding="utf-8")

    assert "strict_mcp_config=True" in source
    assert "setting_sources=sans_reglages_du_poste()" in source
    assert "skills=sans_skills_du_poste()" in source


# --------------------------------------------------------------------------- #
# Le point de couture : le message de la tâche                                   #
# --------------------------------------------------------------------------- #


class _ProviderTemoin(ModelProvider):
    """Fournisseur factice : retient le message reçu, n'écrit rien."""

    name = "temoin"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):  # pragma: no cover
        raise AssertionError("un rôle outillé passe par run_agent")

    async def run_agent(self, prompt, *, model, workspace, tools, **kwargs: Any) -> str:
        self.prompts.append(prompt)
        return "Fait."


def _runtime(provider: ModelProvider) -> AgentRuntime:
    """Un runtime minimal, sur un profil fabriqué — aucun profil du dépôt n'est lu."""
    profil = RoleProfile(
        nom="developpeur",
        role="Développeur",
        modele="claude-sonnet-5",
        outils=DEFAULT_TOOLS,
        prompt_systeme="Tu développes.",
        intro_tache="Tâche :",
        consignes="Consignes du rôle.",
        consigne_finale="Rends un compte-rendu.",
        workspace_prefix="maestro-test-",
    )
    return AgentRuntime(provider, profil)


def test_le_message_de_la_tache_porte_l_outillage_du_projet(tmp_path: Path) -> None:
    """Le point de couture : lu dans la **racine**, posé dans le message."""
    projet = projet_outille(tmp_path / "depensio")
    provider = _ProviderTemoin()

    asyncio.run(_runtime(provider).execute("Ajouter un écran", projet=projet, tache_id="t-1"))

    prompt = provider.prompts[0]
    assert "## L'outillage de ce projet" in prompt
    assert "mot-temoin-des-instructions" in prompt
    assert "lancer-les-tests" in prompt
    assert "corps-du-skill-jamais-transmis" not in prompt


def test_un_projet_non_outille_rend_le_message_d_avant_a_la_ligne_pres(tmp_path: Path) -> None:
    """« Ça n'ajoute rien » ne se lit pas dans un diff de prompt : on compare.

    Les deux exécutions ne diffèrent que par le manifeste ; la seconde est donc
    la mesure de ce que ce lot a **ajouté** au message quand il n'a rien à dire.
    La racine de `projet_outille` ne porte que l'outillage : le fragment ajouté le
    dit (#1400), et c'est le fragment entier qui disparaît sans manifeste.
    """
    racine = tmp_path / "depensio"
    projet = projet_outille(racine)
    consigne = outillage_du_projet(projet).consigne(espace=ESPACE_NEUF)
    avec = _ProviderTemoin()
    asyncio.run(_runtime(avec).execute("Ajouter un écran", projet=projet, tache_id="t-1"))
    (racine / CHEMIN_MANIFESTE).unlink()
    sans = _ProviderTemoin()

    asyncio.run(_runtime(sans).execute("Ajouter un écran", projet=projet, tache_id="t-1"))

    assert "## L'outillage de ce projet" not in sans.prompts[0]
    assert avec.prompts[0].replace(f"\n\n{consigne}", "") == sans.prompts[0]


def test_l_outillage_est_lu_dans_la_racine_pas_dans_l_espace_de_la_tache(
    tmp_path: Path,
) -> None:
    """Un worktree n'en porte que ce qui est commité, et le manifeste vit sous `.maestro/`.

    La sonde est indirecte mais franche : le manifeste n'est **pas** recopié
    ailleurs, donc s'il était lu depuis l'espace de travail il n'y aurait rien à
    transmettre. Le contenu transmis prouve donc la racine.
    """
    racine = tmp_path / "depensio"
    projet = projet_outille(racine)
    provider = _ProviderTemoin()

    asyncio.run(_runtime(provider).execute("Ajouter un écran", projet=projet, tache_id="t-1"))

    assert "mot-temoin-des-instructions" in provider.prompts[0]
    assert (racine / CHEMIN_MANIFESTE).is_file()


# --------------------------------------------------------------------------- #
# Une racine qui ne porte que l'outillage (#1400)                               #
# --------------------------------------------------------------------------- #

#: Les fichiers de l'espace d'une tâche dans un projet **neuf** : l'outillage que le
#: manifeste de `projet_outille` déclare, le manifeste lui-même et l'atelier d'une
#: tâche — tout ce que Maestro a posé, rien du projet.
ESPACE_NEUF = (
    ".agents/skills/lancer-les-tests/SKILL.md",
    ".maestro/outillage/manifeste.json",
    ".maestro/t-1/brouillon.md",
    "AGENTS.md",
    "CLAUDE.md",
)

#: Les pièces déclarées de `projet_outille`, dans l'ordre de son manifeste.
PIECES_NEUVES = ("AGENTS.md", "CLAUDE.md", ".agents/skills/lancer-les-tests/SKILL.md")


def _section_racine(texte: str) -> str:
    """La section qui dit ce que porte la racine — `""` si le texte ne la porte pas."""
    _, titre, suite = texte.partition(TITRE_RACINE_OUTILLEE)
    return titre + suite.split("\n## ", 1)[0] if titre else ""


def test_une_racine_qui_ne_porte_que_l_outillage_le_dit_avec_ses_pieces(tmp_path: Path) -> None:
    """p5 (2026-10-01) : Maestro savait que le dossier ne portait que son outillage, et ne
    le disait à personne. L'agent du socle a tenté le générateur standard, l'a vu refuser
    un dossier non vide, et a écrit `package.json` de mémoire — six installations et
    neuf minutes plus tard, une installation passait.

    Le fait se dit avec sa liste, **dérivée du manifeste** et dans son ordre, et avec ce
    qu'il implique : un générateur qui exige un dossier vide se lance dans un dossier
    jetable, et son résultat se range à la racine sans toucher à l'outillage.
    """
    projet = projet_outille(tmp_path / "depensio")

    section = _section_racine(outillage_du_projet(projet).consigne(espace=ESPACE_NEUF))

    assert section
    positions = [section.find(f"`{piece}`") for piece in PIECES_NEUVES]
    assert -1 not in positions, section
    assert positions == sorted(positions)
    assert "`.maestro/`" in section
    assert "dossier jetable" in section
    assert "`mktemp -d`" in section
    assert "sans toucher à l'outillage" in section
    assert "à la main" in section


def test_la_section_vient_avant_ce_que_les_fichiers_ne_peuvent_pas_faire(tmp_path: Path) -> None:
    """La phrase qui borne l'outillage reste la dernière du bloc (docstring de `consigne`)."""
    projet = projet_outille(tmp_path / "depensio")

    consigne = outillage_du_projet(projet).consigne(espace=ESPACE_NEUF)

    assert consigne.index(TITRE_RACINE_OUTILLEE) < consigne.index("Deux choses que ces fichiers")
    assert consigne.rstrip().endswith("sous tes autorisations.")


@pytest.mark.parametrize(
    "fichier_du_projet",
    [
        "package.json",
        # Le manifeste décide, pas le disque : un skill que Maestro n'a pas déclaré
        # est un fichier du projet, et la racine n'est plus neuve.
        ".agents/skills/intrus/SKILL.md",
        # `.maestro` à la racine n'est que le nom d'un fichier : seul le dossier est à Maestro.
        ".maestrorc",
    ],
)
def test_un_seul_fichier_du_projet_et_la_racine_n_est_plus_neuve(
    tmp_path: Path, fichier_du_projet: str
) -> None:
    """Un projet qui a ses fichiers n'apprend rien de plus : la consigne est celle d'avant."""
    outillage = outillage_du_projet(projet_outille(tmp_path / "depensio"))

    assert outillage.consigne(espace=(*ESPACE_NEUF, fichier_du_projet)) == outillage.consigne()


def test_une_piece_declaree_absente_de_l_espace_n_est_pas_nommee(tmp_path: Path) -> None:
    """La liste est ce que la racine **porte** : une pièce disparue n'y est pas dite."""
    projet = projet_outille(tmp_path / "depensio")
    espace = tuple(chemin for chemin in ESPACE_NEUF if chemin != "CLAUDE.md")

    section = _section_racine(outillage_du_projet(projet).consigne(espace=espace))

    assert "`AGENTS.md`" in section
    assert "`CLAUDE.md`" not in section


def test_un_espace_sans_aucune_piece_de_l_outillage_ne_dit_rien_de_plus(tmp_path: Path) -> None:
    """Sans pièce dans l'espace, il n'y a rien à ne pas toucher : la consigne d'avant."""
    outillage = outillage_du_projet(projet_outille(tmp_path / "depensio"))

    assert outillage.consigne(espace=(".maestro/t-1/brouillon.md",)) == outillage.consigne()
    assert outillage.consigne(espace=()) == outillage.consigne()


def test_le_message_d_une_tache_dans_un_projet_neuf_dit_que_sa_racine_ne_porte_que_l_outillage(
    tmp_path: Path,
) -> None:
    """Le défaut de #1400, au point de couture : le message de la tâche du socle.

    Projet non versionné : l'espace **est** la racine, qui porte l'outillage et
    l'atelier de la tâche (`.maestro/t-1/`), et rien d'autre.
    """
    projet = projet_outille(tmp_path / "depensio")
    provider = _ProviderTemoin()

    asyncio.run(
        _runtime(provider).execute("Initialiser le socle Next.js", projet=projet, tache_id="t-1")
    )

    section = _section_racine(provider.prompts[0])
    assert section, "le message ne dit pas que la racine ne porte que l'outillage"
    assert "`AGENTS.md`" in section
    assert "`.agents/skills/lancer-les-tests/SKILL.md`" in section


def test_le_message_d_une_tache_dans_un_projet_qui_a_ses_fichiers_n_en_dit_rien(
    tmp_path: Path,
) -> None:
    """Un projet qui a déjà son code : l'outillage est transmis, la racine n'a rien à dire."""
    racine = tmp_path / "depensio"
    projet = projet_outille(racine)
    ecrire(racine, "package.json", "{}\n")
    provider = _ProviderTemoin()

    asyncio.run(_runtime(provider).execute("Ajouter un écran", projet=projet, tache_id="t-1"))

    assert "## L'outillage de ce projet" in provider.prompts[0]
    assert TITRE_RACINE_OUTILLEE not in provider.prompts[0]


@pytest.mark.skipif(shutil.which("git") is None, reason="git introuvable")
def test_dans_un_projet_versionne_c_est_le_worktree_de_la_tache_qui_est_juge(
    tmp_path: Path,
) -> None:
    """L'agent travaille dans le worktree : c'est lui qui est neuf ou non, pas la racine.

    L'outillage y arrive **commité**, manifeste compris (la fusion de sa branche) :
    `.maestro/` n'est pas au projet pour autant. Un brouillon que la racine porte sans
    l'avoir commité n'est pas dans le worktree, et ne rend pas la racine de l'agent
    « non neuve ».
    """
    racine = tmp_path / "depensio"
    projet_outille(racine)
    for arguments in (
        ("init", "--quiet"),
        ("symbolic-ref", "HEAD", "refs/heads/main"),
        ("add", "-A"),
        ("-c", "user.email=tests@maestro", "-c", "user.name=Tests", "commit", "-q", "-m", "o"),
    ):
        subprocess.run(["git", *arguments], cwd=racine, check=True, capture_output=True)
    ecrire(racine, "brouillon-non-commite.txt", "x\n")
    projet = Projet(
        id="prj-0000dead", nom="Dépensio", racine=racine.as_posix(), vcs=detecter_vcs(racine)
    )
    provider = _ProviderTemoin()

    asyncio.run(
        _runtime(provider).execute("Initialiser le socle Next.js", projet=projet, tache_id="t-1")
    )

    section = _section_racine(provider.prompts[0])
    assert section, "le worktree ne porte que l'outillage, le message doit le dire"
    assert "brouillon-non-commite.txt" not in section

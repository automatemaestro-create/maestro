"""Tests de la source unique des modèles Claude du produit (ticket #1270).

`maestro.familles_claude` lit `maestro/providers/familles-claude.tsv` — le fichier que
les runs `/orchestrate` lisent déjà (#1269) — et le produit y prend tous ses défauts :
le Chef de projet, chaque agent qu'aucun réglage ne fixe, le classifieur, et la gamme
que le choix du modèle d'un agent propose.

① **la lecture** : commentaires et lignes incomplètes écartés, fin de ligne Windows
   tolérée, famille sans casse, libellé facultatif ; un fichier absent ou vide est une
   erreur qui se nomme, jamais un modèle deviné ;
② **la résolution** : une famille donne sa dernière version, un identifiant complet
   passe tel quel ;
③ **tout suit la ligne** : un fichier dont la ligne `opus` a changé fait passer, dans
   un processus neuf, le Chef de projet et tous les agents à la version qu'elle nomme —
   la preuve que plus rien ne l'écrit à sa place ;
④ **aucun identifiant en dur** : aucune chaîne du paquet `maestro` n'est un modèle
   Claude, preuve faite d'abord sur un échantillon fautif.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from maestro import familles_claude
from maestro.familles_claude import (
    FamilleClaude,
    FamillesIllisibles,
    derniere_version,
    resoudre_famille,
)

RACINE = Path(__file__).resolve().parent.parent


@pytest.fixture
def fichier(tmp_path, monkeypatch):
    """Un fichier de familles à soi, relu depuis zéro (la lecture est mise en cache)."""
    chemin = tmp_path / "familles-claude.tsv"
    monkeypatch.setattr(familles_claude, "FICHIER_FAMILLES", chemin)
    familles_claude.familles_claude.cache_clear()
    yield chemin
    familles_claude.familles_claude.cache_clear()


# --- ① La lecture ------------------------------------------------------------------------


def test_la_lecture_est_celle_de_run_sh(fichier):
    fichier.write_bytes(
        "# en-tête\r\n"
        "#\r\n"
        "# famille\tidentifiant\tlibellé\r\n"
        "Opus\tclaude-opus-9\tOpus 9\r\n"
        "incomplete\r\n"
        "\r\n"
        "haiku\tclaude-haiku-9\r\n".encode()
    )

    assert familles_claude.familles_claude() == (
        FamilleClaude("opus", "claude-opus-9", "Opus 9"),
        # Sans libellé, la ligne se nomme par son identifiant.
        FamilleClaude("haiku", "claude-haiku-9", "claude-haiku-9"),
    )


def test_un_fichier_absent_se_nomme(fichier):
    with pytest.raises(FamillesIllisibles, match="familles-claude.tsv introuvable"):
        familles_claude.familles_claude()


def test_un_fichier_sans_famille_se_nomme(fichier):
    fichier.write_text("# rien que des commentaires\n", encoding="utf-8")
    with pytest.raises(FamillesIllisibles, match="aucune famille"):
        familles_claude.familles_claude()


# --- ② La résolution ---------------------------------------------------------------------


def test_une_famille_donne_sa_derniere_version(fichier):
    fichier.write_text("opus\tclaude-opus-9\tOpus 9\n", encoding="utf-8")

    assert derniere_version("opus") == "claude-opus-9"
    assert derniere_version(" OPUS ") == "claude-opus-9"


def test_une_famille_inconnue_eclate_en_nommant_les_connues(fichier):
    # Le code nomme ses familles en dur : un nom inconnu est une faute de frappe.
    fichier.write_text("opus\tclaude-opus-9\tOpus 9\n", encoding="utf-8")
    with pytest.raises(KeyError, match="connues : opus"):
        derniere_version("opuss")


def test_seule_une_famille_se_resout(fichier):
    fichier.write_text("opus\tclaude-opus-9\tOpus 9\n", encoding="utf-8")

    assert resoudre_famille("Opus") == "claude-opus-9"
    # Un identifiant complet est un choix épinglé : il passe tel quel.
    assert resoudre_famille("claude-opus-5") == "claude-opus-5"
    # Un nom que le fichier ignore n'est pas deviné non plus.
    assert resoudre_famille("gpt-4o") == "gpt-4o"


def test_le_fichier_du_depot_nomme_les_familles_du_produit():
    # Les familles que le code nomme (`opus` pour les agents, `haiku` pour le
    # classifieur) doivent exister dans le vrai fichier : sinon l'import éclate.
    for famille in ("opus", "haiku"):
        assert derniere_version(famille).startswith(f"claude-{famille}-")


# --- ③ Tout suit la ligne `opus` ---------------------------------------------------------

#: Relevé des défauts du produit dans un processus neuf, le fichier des familles
#: remplacé AVANT tout import : les défauts se calculent à l'import, et c'est
#: justement ce qu'on veut prouver — qu'ils se calculent depuis ce fichier.
RELEVE = """
import json, sys
from pathlib import Path

import maestro.familles_claude as familles
familles.FICHIER_FAMILLES = Path(sys.argv[1])

from maestro.agents.catalog import GABARITS_DU_CODE, MODELE_EXECUTANT_DEFAUT
from maestro.agents.fiche_outillee import CADRE_GENERIQUE, TOOLED_PROFILES
from maestro.agents.store import AgentDefinition
from maestro.config import Settings
from maestro.providers.claude import ClaudeProvider
from maestro.providers.factory import default_model
from maestro.router.classifier import MODELE_CLASSIFIEUR

agent_d_equipe = AgentDefinition(nom="recrue", role="Rôle", competences=(), playbook="p")
print(json.dumps({
    "chef_de_projet": default_model(Settings.from_env()),
    "defaut_des_agents": MODELE_EXECUTANT_DEFAUT,
    "gabarits": sorted({agent.modele for agent in GABARITS_DU_CODE}),
    "profils_outilles": sorted({profil.modele for profil in TOOLED_PROFILES}),
    "cadre_generique": CADRE_GENERIQUE.modele,
    "agent_sans_modele": agent_d_equipe.to_agent().modele,
    "classifieur": MODELE_CLASSIFIEUR,
    "gamme": [[m.nom, m.libelle] for m in ClaudeProvider.MODELES],
}))
"""


def _releve(tmp_path: Path, **variables: str) -> dict:
    fichier = tmp_path / "familles-claude.tsv"
    fichier.write_text(
        "# une version qui n'existe pas encore\n"
        "opus\tclaude-opus-99\tOpus 99\n"
        "haiku\tclaude-haiku-99\tHaiku 99\n",
        encoding="utf-8",
    )
    environnement = {
        **os.environ,
        # Posées vides plutôt qu'ôtées : `load_dotenv` ne remplace pas une variable
        # présente, et le `.env` d'un poste ne doit pas décider du verdict.
        "ANTHROPIC_MODEL": "",
        "MAESTRO_MODEL": "",
        "MAESTRO_PROVIDER": "",
        **variables,
        "PYTHONPATH": str(RACINE),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    sortie = subprocess.run(
        [sys.executable, "-c", RELEVE, str(fichier)],
        cwd=RACINE,
        env=environnement,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
        check=False,
    )
    assert sortie.returncode == 0, sortie.stderr
    return json.loads(sortie.stdout.strip().splitlines()[-1])


def test_changer_la_ligne_opus_fait_passer_tout_le_produit(tmp_path):
    releve = _releve(tmp_path)

    assert releve == {
        "chef_de_projet": "claude-opus-99",
        "defaut_des_agents": "claude-opus-99",
        "gabarits": ["claude-opus-99"],
        "profils_outilles": ["claude-opus-99"],
        "cadre_generique": "claude-opus-99",
        # Un agent écrit par une validation d'équipe ne porte aucun modèle
        # (`maestro.equipe.creation`) : il suit le défaut à l'exécution.
        "agent_sans_modele": "claude-opus-99",
        # Le classifieur route, il ne fait pas le travail : il reste sur Haiku,
        # mais sur sa dernière version, par le même fichier.
        "classifieur": "claude-haiku-99",
        "gamme": [["claude-opus-99", "Opus 99"], ["claude-haiku-99", "Haiku 99"]],
    }


def test_un_reglage_l_emporte_toujours_sur_la_ligne(tmp_path):
    # `ANTHROPIC_MODEL` épinglé pour le Chef de projet, `MAESTRO_MODEL` pour tous :
    # la source unique donne le défaut, elle ne retire aucun réglage.
    assert _releve(tmp_path, ANTHROPIC_MODEL="claude-opus-4-8")["chef_de_projet"] == (
        "claude-opus-4-8"
    )
    assert _releve(tmp_path, MAESTRO_MODEL="claude-sonnet-5")["chef_de_projet"] == (
        "claude-sonnet-5"
    )


# --- ④ Aucun identifiant en dur ----------------------------------------------------------

#: Un identifiant de modèle Claude, en entier — pas une phrase qui en cite un.
IDENTIFIANT_CLAUDE = re.compile(r"claude-(opus|sonnet|fable|haiku)-\d[\w.-]*")


def _identifiants_en_dur(source: str) -> list[str]:
    """Les chaînes littérales du code qui SONT un identifiant Claude.

    Lit l'arbre syntaxique et non le texte : un commentaire ou une docstring qui
    raconte un identifiant passé n'est pas un défaut, une valeur l'est.
    """
    return [
        noeud.value
        for noeud in ast.walk(ast.parse(source))
        if isinstance(noeud, ast.Constant)
        and isinstance(noeud.value, str)
        and IDENTIFIANT_CLAUDE.fullmatch(noeud.value)
    ]


def test_la_sonde_voit_une_valeur_et_ignore_un_recit():
    fautif = 'MODELE = "claude-sonnet-5"\nprofil = dict(modele="claude-opus-5-5")\n'
    recit = (
        '"""Le défaut était `claude-sonnet-5`."""\n'
        "# claude-opus-5 restait figé\n"
        'MODELE = derniere_version("opus")\n'
    )

    assert _identifiants_en_dur(fautif) == ["claude-sonnet-5", "claude-opus-5-5"]
    assert _identifiants_en_dur(recit) == []


def test_aucun_modele_claude_n_est_ecrit_dans_le_code_du_produit():
    trouves = {
        str(chemin.relative_to(RACINE)): valeurs
        for chemin in sorted((RACINE / "maestro").rglob("*.py"))
        if (valeurs := _identifiants_en_dur(chemin.read_text(encoding="utf-8")))
    }
    assert trouves == {}, (
        "un modèle Claude écrit en dur vieillira sans le dire : le lire dans "
        f"familles-claude.tsv (maestro.familles_claude) — {trouves}"
    )

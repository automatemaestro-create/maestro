"""Tests des secrets partagés — `scripts/env-pull.sh` (ticket #156, lot final du parent #155).

Le script complète un `.env` local avec les clés **partagées** publiées dans les variables
du projet. Quatre promesses le rendent utilisable sans relire son code à chaque fois, et ce sont
elles que ces tests épinglent :

1. **le gabarit fait foi** — la liste des clés partagées est lue dans `.env.example`
   (marqueurs `# [partagé]` / `# [perso]`), jamais recopiée dans le script ;
2. **non destructif** — une clé déjà renseignée n'est jamais écrasée, même si la variable CI/CD
   dit autre chose ; les clés `[perso]` ne sont pas même regardées ;
3. **aucune valeur imprimée** — la sortie ne porte que des NOMS de clés et des comptes ;
4. **franc sur ce qu'il ne peut pas** — une clé partagée absente des variables du dépôt est dite
   comme telle, avec la commande qui la publie.

**Ni réseau ni compte de forge** : le script expose la couture `MAESTRO_ENV_PULL_SOURCE`, un fichier
JSON qui remplace l'appel à l'API des variables. Tout se joue donc dans `tmp_path`, sur un
`.env.example` et un `.env` synthétiques — jamais sur ceux du dépôt.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash introuvable")

# Gabarit synthétique. `CLE_ORPHELINE` précède tout marqueur : c'est la seule position où une clé
# est vraiment « sans marqueur », un marqueur valant jusqu'au suivant.
GABARIT = """\
# Gabarit de test — aucune valeur réelle.

# Clé volontairement laissée hors convention : le script doit la signaler et l'ignorer.
CLE_ORPHELINE=

# [perso] Jeton nominatif — personne ne peut vous le donner.
GITLAB_TOKEN=
# [perso] Chemin de machine.
MAESTRO_CHROME_PROFILE=

# [partagé] Observabilité — publiées dans les variables du dépôt.
LANGFUSE_SECRET_KEY=
LANGFUSE_PUBLIC_KEY=
LANGFUSE_HOST=
# [partagé] Messagerie et certificat.
SLACK_BOT_TOKEN=
MAESTRO_CERTIF=
"""

# `.env` local d'un poste déjà à moitié équipé.
ENV_LOCAL = """\
# Mon .env — commentaire à préserver.
CLE_ORPHELINE=
GITLAB_TOKEN=jeton-nominatif-a-preserver
MAESTRO_CHROME_PROFILE=
LANGFUSE_SECRET_KEY=
LANGFUSE_PUBLIC_KEY=valeur-deja-posee
"""

# Valeurs « secrètes » : aucune ne doit jamais apparaître dans la sortie du script.
SECRET = "sk-valeur-partagee-secrete"
HOTE = "https://langfuse.exemple.invalid"
AUTRE = "pk-valeur-du-projet-differente"

# Forme de « GET /repos/:dépôt/actions/variables », aplatie par `--jq '.variables[]'` : un objet
# « name / value » par variable. Les notions de type et de masquage n'existent pas côté GitHub — un
# vrai secret n'est de toute façon pas relisible par API (docs/27 §5).
VARIABLES_CI = [
    {"name": "LANGFUSE_SECRET_KEY", "value": SECRET},
    {"name": "LANGFUSE_PUBLIC_KEY", "value": AUTRE},
    {"name": "LANGFUSE_HOST", "value": HOTE},
    # Publiée alors qu'elle est [perso] : le script ne doit pas la poser pour autant.
    {"name": "GITLAB_TOKEN", "value": "jeton-du-projet"},
    # Publiée SANS VALEUR : inexploitable dans un .env — dit, pas posé.
    {"name": "MAESTRO_CERTIF", "value": ""},
    # Publiée sans clé correspondante au gabarit : souvent une coquille côté dépôt.
    {"name": "VARIABLE_INCONNUE", "value": "peu importe"},
    # SLACK_BOT_TOKEN, elle, n'est PAS publiée : le script doit le dire franchement.
]


@dataclass
class Clone:
    """Clone jetable : le vrai `env-pull.sh`, un gabarit et un `.env` synthétiques."""

    racine: Path
    variables: Path

    @property
    def env(self) -> Path:
        return self.racine / ".env"

    def lance(self, *args: str, variables: object = None) -> subprocess.CompletedProcess[str]:
        environnement = os.environ.copy()
        environnement["MAESTRO_ENV_PULL_SOURCE"] = str(self.variables)
        if variables is not None:
            self.variables.write_text(
                json.dumps(variables, ensure_ascii=False), encoding="utf-8", newline="\n"
            )
        assert BASH is not None
        return subprocess.run(  # noqa: S603
            [BASH, str(self.racine / "scripts" / "env-pull.sh"), *args],
            cwd=str(self.racine),
            env=environnement,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )

    def valeurs(self) -> dict[str, str]:
        """Le `.env` relu en dictionnaire (lignes de commentaire écartées)."""
        lues: dict[str, str] = {}
        for ligne in self.env.read_text(encoding="utf-8").splitlines():
            nue = ligne.strip()
            if not nue or nue.startswith("#") or "=" not in nue:
                continue
            cle, _, valeur = nue.partition("=")
            lues[cle] = valeur
        return lues


@pytest.fixture
def clone(tmp_path: Path) -> Clone:
    racine = tmp_path / "clone"
    (racine / "scripts").mkdir(parents=True)
    shutil.copy2(RACINE / "scripts" / "env-pull.sh", racine / "scripts" / "env-pull.sh")
    (racine / ".env.example").write_text(GABARIT, encoding="utf-8", newline="\n")
    (racine / ".env").write_text(ENV_LOCAL, encoding="utf-8", newline="\n")

    variables = tmp_path / "variables-ci.json"
    variables.write_text(
        json.dumps(VARIABLES_CI, ensure_ascii=False), encoding="utf-8", newline="\n"
    )
    return Clone(racine=racine, variables=variables)


# --- Le gabarit fait foi -------------------------------------------------------------------------


def test_manquantes_ne_liste_que_les_cles_partagees_a_completer(clone: Clone) -> None:
    """Sortie destinée à être consommée par `setup.sh` : des noms, rien d'autre."""
    acheve = clone.lance("--manquantes")
    assert acheve.returncode == 0, acheve.stderr
    manquantes = acheve.stdout.split()
    assert set(manquantes) == {
        "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST", "SLACK_BOT_TOKEN", "MAESTRO_CERTIF"
    }
    # Ni les clés [perso], ni celles déjà renseignées, ni la clé sans marqueur.
    assert "GITLAB_TOKEN" not in manquantes
    assert "LANGFUSE_PUBLIC_KEY" not in manquantes
    assert "CLE_ORPHELINE" not in manquantes


def test_manquantes_tolere_un_env_absent(clone: Clone) -> None:
    """C'est l'état d'un clone frais : `setup.sh` veut la liste, pas une erreur."""
    clone.env.unlink()
    acheve = clone.lance("--manquantes")
    assert acheve.returncode == 0, acheve.stderr
    assert "LANGFUSE_PUBLIC_KEY" in acheve.stdout.split()   # tout est à compléter


def test_signale_les_cles_du_gabarit_sans_marqueur(clone: Clone) -> None:
    """La convention a dérivé : on le dit plutôt que de deviner si publier serait une fuite."""
    acheve = clone.lance("--check")
    assert "sans marqueur" in acheve.stdout
    assert "CLE_ORPHELINE" in acheve.stdout


def test_refuse_de_travailler_sans_env(clone: Clone) -> None:
    clone.env.unlink()
    acheve = clone.lance()
    assert acheve.returncode == 1
    assert "scripts/setup.sh --only env" in acheve.stderr
    assert not clone.env.exists()          # rien n'a été créé dans le dos


# --- Non destructif ------------------------------------------------------------------------------


def test_complete_les_cles_vides_sans_ecraser_les_valeurs_posees(clone: Clone) -> None:
    acheve = clone.lance()
    assert acheve.returncode == 0, acheve.stderr
    valeurs = clone.valeurs()

    assert valeurs["LANGFUSE_SECRET_KEY"] == SECRET        # vide → complétée sur place
    assert valeurs["LANGFUSE_HOST"] == HOTE               # absente → ajoutée
    # Déjà renseignée : préservée, MÊME si la variable CI/CD dit autre chose.
    assert valeurs["LANGFUSE_PUBLIC_KEY"] == "valeur-deja-posee"
    assert AUTRE not in clone.env.read_text(encoding="utf-8")


def test_ne_touche_jamais_aux_cles_perso(clone: Clone) -> None:
    """`GITLAB_TOKEN` est publié côté projet : le marqueur [perso] doit primer."""
    clone.lance()
    valeurs = clone.valeurs()
    assert valeurs["GITLAB_TOKEN"] == "jeton-nominatif-a-preserver"
    assert valeurs["MAESTRO_CHROME_PROFILE"] == ""        # vide, et le reste
    assert "jeton-du-projet" not in clone.env.read_text(encoding="utf-8")


def test_preserve_commentaires_ordre_et_cles_inconnues(clone: Clone) -> None:
    clone.lance()
    contenu = clone.env.read_text(encoding="utf-8")
    assert contenu.startswith("# Mon .env — commentaire à préserver.\n")
    assert "CLE_ORPHELINE=\n" in contenu
    # Les clés ajoutées le sont en fin de fichier, sous un en-tête qui dit d'où elles viennent.
    entete = "# --- Clés partagées récupérées des variables du dépôt (bash scripts/env-pull.sh) ---"
    assert entete in contenu
    assert contenu.index(entete) > contenu.index("LANGFUSE_PUBLIC_KEY")


def test_preserve_les_fins_de_ligne_crlf(clone: Clone) -> None:
    """L'awk de Git Bash convertirait les CRLF en LF : le script réécrit donc en bash pur."""
    clone.env.write_bytes(ENV_LOCAL.replace("\n", "\r\n").encode("utf-8"))
    clone.lance()
    octets = clone.env.read_bytes()
    assert b"\r\n" in octets
    assert octets.count(b"\n") == octets.count(b"\r\n"), "des lignes ont perdu leur CR"
    assert clone.valeurs()["LANGFUSE_SECRET_KEY"] == SECRET


def test_second_passage_ne_change_plus_rien(clone: Clone) -> None:
    """Idempotent : ce qu'il vient de poser est désormais « renseigné », donc intouchable."""
    clone.lance()
    apres_premier = clone.env.read_bytes()
    acheve = clone.lance()
    assert acheve.returncode == 0, acheve.stderr
    assert clone.env.read_bytes() == apres_premier
    assert "Rien à compléter" in acheve.stdout or "Aucune valeur à poser" in acheve.stdout


# --- Aucune valeur imprimée ----------------------------------------------------------------------


@pytest.mark.parametrize("options", [(), ("--check",)])
def test_aucune_valeur_ne_traverse_la_sortie(clone: Clone, options: tuple[str, ...]) -> None:
    """La sortie ne porte que des NOMS de clés et des comptes — jamais un secret."""
    acheve = clone.lance(*options)
    sortie = acheve.stdout + acheve.stderr
    for valeur in (SECRET, HOTE, AUTRE, "jeton-du-projet", "jeton-nominatif-a-preserver"):
        assert valeur not in sortie, f"valeur divulguée : {valeur}"
    # …mais les noms, eux, sont bien là : c'est ce qui rend le diagnostic exploitable.
    assert "LANGFUSE_SECRET_KEY" in sortie


def test_check_n_ecrit_rien(clone: Clone) -> None:
    avant = clone.env.read_bytes()
    acheve = clone.lance("--check")
    assert acheve.returncode == 0, acheve.stderr
    assert clone.env.read_bytes() == avant
    assert "rien écrit, aucune valeur lue" in acheve.stdout


# --- Franc sur ce qu'il ne peut pas ---------------------------------------------------------------


def test_dit_ce_qui_manque_cote_variables_ci(clone: Clone) -> None:
    acheve = clone.lance()
    assert "absentes des variables du dépôt" in acheve.stdout
    assert "SLACK_BOT_TOKEN" in acheve.stdout
    assert "gh variable set <CLÉ> --body <valeur>" in acheve.stdout
    assert "SLACK_BOT_TOKEN" not in clone.valeurs()      # rien deviné, rien posé


def test_dit_ce_qui_est_publie_mais_inexploitable(clone: Clone) -> None:
    """Une variable de type `file` n'a pas sa place dans un `.env` : dite, jamais posée."""
    acheve = clone.lance()
    assert "publiées mais inexploitables" in acheve.stdout
    assert "MAESTRO_CERTIF (vide)" in acheve.stdout
    assert "MAESTRO_CERTIF" not in clone.valeurs()


def test_dit_les_variables_du_projet_hors_gabarit(clone: Clone) -> None:
    acheve = clone.lance()
    assert "hors gabarit (ignorées)" in acheve.stdout
    assert "VARIABLE_INCONNUE" in acheve.stdout
    assert "VARIABLE_INCONNUE" not in clone.valeurs()


def test_une_valeur_multiligne_est_refusee(clone: Clone) -> None:
    """Un `\\n` dans une valeur casserait le `.env` en silence : elle est écartée, et dite."""
    variables = [
        {"name": "LANGFUSE_SECRET_KEY", "value": "debut\nfin"},
    ]
    acheve = clone.lance(variables=variables)
    assert "LANGFUSE_SECRET_KEY (multiligne)" in acheve.stdout
    assert clone.valeurs()["LANGFUSE_SECRET_KEY"] == ""


def test_valeur_accentuee_posee_sans_mojibake(clone: Clone) -> None:
    """Le décodage JSON passe par `\\uXXXX` : c'est exactement le piège de #141."""
    variables = [
        {
            "name": "LANGFUSE_HOST",
            "value": "https://héberg.exemple.invalid/é",
        },
    ]
    clone.lance(variables=variables)
    # Vérification par OCTETS : un terminal cp1252 réafficherait du mojibake de façon plausible.
    assert "LANGFUSE_HOST=https://héberg.exemple.invalid/é".encode() in clone.env.read_bytes()


def test_source_de_variables_introuvable(clone: Clone) -> None:
    clone.variables.unlink()
    acheve = clone.lance()
    assert acheve.returncode == 1
    assert "fichier introuvable" in acheve.stderr


def test_option_inconnue_refusee(clone: Clone) -> None:
    acheve = clone.lance("--tout-ecraser")
    assert acheve.returncode == 2
    assert "Option inconnue" in acheve.stderr

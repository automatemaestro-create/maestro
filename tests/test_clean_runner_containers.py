"""Tests du ménage des conteneurs de jobs CI — `scripts/gitlab/clean-runner-containers.sh` (#166).

**Ni réseau ni Docker.** Le script ne parle qu'au CLI `docker` : on lui en substitue un **faux**,
placé en tête du `PATH`, qui lit son inventaire dans des fichiers et **journalise** les `docker rm`
au lieu de les exécuter. Ce qui est vérifié ici, ce sont donc les **filtres et les garde-fous** —
exactement ce qu'on ne veut pas découvrir en production, la production étant ici le poste d'un
collègue et les conteneurs de ses autres projets.

Le vrai script est lancé depuis le dépôt (il n'écrit rien dans l'arborescence, contrairement à
[`test_worktree.py`](test_worktree.py) qui doit monter un dépôt jetable).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent
SCRIPT = RACINE / "scripts" / "gitlab" / "clean-runner-containers.sh"
BASH = shutil.which("bash")
# Dossier des utilitaires POSIX (grep, sed, sort, date) : sert à fabriquer un PATH sans docker.
_GREP = shutil.which("grep")
OUTILS = str(Path(_GREP).parent) if _GREP else ""

pytestmark = pytest.mark.skipif(BASH is None, reason="bash introuvable")

# Un faux `docker` : chaque sous-commande est servie depuis $FAUX_DOCKER_ETAT. Les séparateurs sont
# des `#` (absents des noms de conteneurs comme des horodatages RFC3339), ce qui évite d'avoir à
# échapper une tabulation dans un script shell généré.
FAUX_DOCKER = r"""#!/usr/bin/env bash
etat="$FAUX_DOCKER_ETAT"
sous="$1"; shift
valeur() { # <fichier> <clé>
  local ligne
  ligne="$(grep -F "$2#" "$etat/$1" 2>/dev/null | head -1)"
  [ -n "$ligne" ] || return 1
  printf '%s\n' "${ligne#*#}"
}
case "$sous" in
  info)
    [ -f "$etat/injoignable" ] && exit 1
    exit 0 ;;
  ps)
    cat "$etat/ps" 2>/dev/null
    exit 0 ;;
  inspect)
    for a in "$@"; do nom="$a"; done
    valeur inspect "$nom" ;;
  rm)
    for a in "$@"; do
      case "$a" in -*) continue ;; esac
      printf '%s\n' "$a" >> "$etat/rm.log"
    done ;;
  volume)
    sous2="$1"; shift
    case "$sous2" in
      ls) cat "$etat/volumes" 2>/dev/null ;;
      inspect)
        for a in "$@"; do nom="$a"; done
        valeur volume-inspect "$nom" ;;
      rm)
        for a in "$@"; do
          case "$a" in -*) continue ;; esac
          grep -qxF "$a" "$etat/volumes-en-service" 2>/dev/null && exit 1
          printf '%s\n' "$a" >> "$etat/volume-rm.log"
        done ;;
    esac ;;
esac
exit 0
"""


def horodatage(minutes: int) -> str:
    """`FinishedAt` d'un conteneur terminé il y a `minutes` minutes, au format de docker inspect."""
    instant = datetime.now(UTC) - timedelta(minutes=minutes)
    return instant.strftime("%Y-%m-%dT%H:%M:%S.000000000Z")


@dataclass
class Docker:
    """Le faux démon : on lui décrit un inventaire, il retient ce qu'on a voulu supprimer."""

    etat: Path
    fauxbin: Path

    # --- mise en scène ---
    def conteneurs(self, *lignes: tuple[str, str, str, int]) -> None:
        """Pose l'inventaire : (nom, état, FinishedAt, taille en octets)."""
        ps = [f"{nom}|{etat}" for nom, etat, _, _ in lignes]
        inspect = [f"{nom}#{fin}|{taille}" for nom, _, fin, taille in lignes]
        (self.etat / "ps").write_text("\n".join(ps) + "\n", encoding="utf-8", newline="\n")
        (self.etat / "inspect").write_text(
            "\n".join(inspect) + "\n", encoding="utf-8", newline="\n"
        )

    def volumes(self, *noms: str, creations: dict[str, str] | None = None) -> None:
        (self.etat / "volumes").write_text("\n".join(noms) + "\n", encoding="utf-8", newline="\n")
        creations = creations or {}
        lignes = [f"{nom}#{creations.get(nom, '2026-01-01T00:00:00Z')}" for nom in noms]
        (self.etat / "volume-inspect").write_text(
            "\n".join(lignes) + "\n", encoding="utf-8", newline="\n"
        )

    def volume_en_service(self, nom: str) -> None:
        """Un volume monté : `docker volume rm` le refuse, comme le vrai."""
        with (self.etat / "volumes-en-service").open("a", encoding="utf-8", newline="\n") as f:
            f.write(nom + "\n")

    def injoignable(self) -> None:
        (self.etat / "injoignable").touch()

    # --- constats ---
    @property
    def supprimes(self) -> list[str]:
        return self._lignes("rm.log")

    @property
    def volumes_supprimes(self) -> list[str]:
        return self._lignes("volume-rm.log")

    def _lignes(self, fichier: str) -> list[str]:
        chemin = self.etat / fichier
        if not chemin.exists():
            return []
        return [ligne for ligne in chemin.read_text(encoding="utf-8").splitlines() if ligne]

    # --- exécution ---
    def lance(self, *args: str, sans_docker: bool = False) -> subprocess.CompletedProcess[str]:
        environnement = os.environ.copy()
        # Le poste qui joue les tests héberge peut-être un vrai runner : ses réglages ne doivent
        # pas déteindre sur le scénario.
        for cle in ("MAESTRO_RUNNER_CONTAINER", "MAESTRO_CLEAN_AGE_MIN"):
            environnement.pop(cle, None)
        environnement["FAUX_DOCKER_ETAT"] = str(self.etat)
        chemin = environnement.get("PATH", "")
        if sans_docker:
            # PATH réduit aux outils dont le script a besoin (grep, sed, sort, date vivent dans le
            # même dossier), ce qui écarte le docker de la machine sans avoir à le désinstaller.
            environnement["PATH"] = OUTILS
        else:
            environnement["PATH"] = os.pathsep.join([str(self.fauxbin), chemin])
        assert BASH is not None
        return subprocess.run(  # noqa: S603
            [BASH, str(SCRIPT), *args],
            cwd=str(RACINE),
            env=environnement,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )


@pytest.fixture
def docker(tmp_path: Path) -> Docker:
    etat = tmp_path / "etat"
    fauxbin = tmp_path / "fauxbin"
    etat.mkdir()
    fauxbin.mkdir()
    shim = fauxbin / "docker"
    shim.write_text(FAUX_DOCKER, encoding="utf-8", newline="\n")
    shim.chmod(0o755)
    faux = Docker(etat=etat, fauxbin=fauxbin)
    faux.conteneurs()
    faux.volumes()
    return faux


# Un job du runner = deux conteneurs (au moins) partageant le préfixe jusqu'au hash.
JOB_A = "runner--6urv5jg-project-84049571-concurrent-0-0540dd831a1b09f2"
JOB_B = "runner--6urv5jg-project-84049571-concurrent-1-90c5261ec766603b"
MO = 1048576


def test_supprime_les_conteneurs_de_jobs_termines(docker: Docker) -> None:
    """Le cas nominal : les restes d'un pipeline interrompu, et le compte rendu qui va avec."""
    docker.conteneurs(
        (f"{JOB_A}-build", "exited", horodatage(60), 400 * MO),
        (f"{JOB_A}-predefined", "exited", horodatage(65), 20 * 1024),
    )
    acheve = docker.lance()
    assert acheve.returncode == 0
    assert sorted(docker.supprimes) == sorted([f"{JOB_A}-build", f"{JOB_A}-predefined"])
    # Nombre ET espace récupéré : c'est ce que le critère d'acceptation demande d'annoncer.
    assert "2 conteneur(s) de job supprimé(s)" in acheve.stderr
    assert "Mo" in acheve.stderr


def test_epargne_tout_le_groupe_dun_job_encore_vivant(docker: Docker) -> None:
    """Le garde-fou qui compte : `-predefined` SORT pendant que le job tourne dans `-build`.

    Le supprimer casserait l'envoi des artefacts du job en cours. Un conteneur `exited` ne suffit
    donc pas — c'est l'état de TOUT le groupe qui décide.
    """
    docker.conteneurs(
        (f"{JOB_A}-predefined", "exited", horodatage(90), 20 * 1024),
        (f"{JOB_A}-build", "running", horodatage(0), 100 * MO),
        (f"{JOB_B}-predefined", "exited", horodatage(90), 20 * 1024),
        (f"{JOB_B}-build", "exited", horodatage(90), 100 * MO),
    )
    docker.lance()
    # Le job A est vivant : ses deux conteneurs sont épargnés. Le job B, lui, est bien un résidu.
    assert sorted(docker.supprimes) == sorted([f"{JOB_B}-predefined", f"{JOB_B}-build"])


def test_epargne_un_conteneur_termine_trop_recemment(docker: Docker) -> None:
    """Garde d'ancienneté : le second conteneur du job peut naître quelques secondes plus tard."""
    docker.conteneurs((f"{JOB_A}-predefined", "exited", horodatage(1), 20 * 1024))
    acheve = docker.lance()
    assert docker.supprimes == []
    assert acheve.returncode == 0


def test_epargne_un_conteneur_dont_la_date_est_illisible(docker: Docker) -> None:
    """Date incompréhensible ⇒ on s'abstient.

    Un résidu de plus vaut mieux qu'un effacement au jugé.
    """
    docker.conteneurs((f"{JOB_A}-build", "exited", "pas-une-date", 400 * MO))
    docker.lance()
    assert docker.supprimes == []


def test_ne_touche_jamais_aux_conteneurs_des_autres_projets(docker: Docker) -> None:
    """La raison d'être du filtrage par nom : `docker container prune` détruirait tout ceci."""
    docker.conteneurs(
        (f"{JOB_A}-build", "exited", horodatage(60), 400 * MO),
        ("infra-postgres-1", "exited", horodatage(6000), 200 * MO),
        ("n8n", "exited", horodatage(60000), 300 * MO),
        ("gestion-locative-db", "running", horodatage(0), 50 * MO),
    )
    docker.lance()
    assert docker.supprimes == [f"{JOB_A}-build"]


def test_ne_touche_jamais_au_conteneur_du_runner(docker: Docker) -> None:
    """Le démon `gitlab-runner` arrêté est un conteneur à redémarrer, pas un déchet à supprimer."""
    docker.conteneurs(
        ("gitlab-runner", "exited", horodatage(6000), 500 * MO),
        (f"{JOB_A}-build", "exited", horodatage(60), 400 * MO),
    )
    docker.lance()
    assert "gitlab-runner" not in docker.supprimes


def test_check_ne_supprime_rien_mais_annonce(docker: Docker) -> None:
    docker.conteneurs((f"{JOB_A}-build", "exited", horodatage(60), 400 * MO))
    acheve = docker.lance("--check")
    assert acheve.returncode == 0
    assert docker.supprimes == []
    assert f"{JOB_A}-build" in acheve.stderr
    assert "à supprimer" in acheve.stderr


def test_silencieux_quand_il_ny_a_rien_a_faire(docker: Docker) -> None:
    """Appelé à chaque clôture : il ne doit pas polluer la sortie de `/ticket-finish` pour rien."""
    docker.conteneurs(("gestion-locative-db", "running", horodatage(0), 50 * MO))
    acheve = docker.lance()
    assert acheve.returncode == 0
    assert acheve.stderr.strip() == ""
    assert acheve.stdout.strip() == ""


def test_docker_injoignable_code_non_nul_sans_exception(docker: Docker) -> None:
    """Best-effort : l'appelant enchaîne en `|| …`, il lui faut un code, pas une pile d'erreurs."""
    docker.injoignable()
    acheve = docker.lance()
    assert acheve.returncode == 1
    assert "démon Docker injoignable" in acheve.stderr


def test_docker_absent_reste_silencieux(docker: Docker) -> None:
    """Une machine sans Docker n'héberge aucun job : rien à nettoyer, et rien d'anormal."""
    if not OUTILS:
        pytest.skip("grep introuvable — impossible de fabriquer un PATH sans docker")
    if shutil.which("docker", path=OUTILS) is not None:
        pytest.skip("docker vit dans le même dossier que les outils POSIX sur cette machine")
    acheve = docker.lance(sans_docker=True)
    assert acheve.returncode == 0
    assert acheve.stderr.strip() == ""


def test_signale_les_volumes_de_cache_sans_les_supprimer(docker: Docker) -> None:
    """Plusieurs enregistrements du runner ⇒ des jeux de cache orphelins.

    On informe, sans jamais effacer.
    """
    docker.volumes(
        "runner-aaaa1111-cache-3c3f060a",
        "runner-aaaa1111-cache-3c3f060a-protected",
        "runner-bbbb2222-cache-3c3f060a",
    )
    acheve = docker.lance()
    assert "3 volumes de cache sur 2 enregistrements" in acheve.stderr
    assert docker.volumes_supprimes == []


def test_un_seul_enregistrement_ne_dit_rien(docker: Docker) -> None:
    docker.volumes("runner-aaaa1111-cache-3c3f060a", "runner-aaaa1111-cache-c33bcaa1")
    acheve = docker.lance()
    assert "volumes de cache" not in acheve.stderr


def test_volumes_purge_les_enregistrements_perimes_et_garde_le_courant(docker: Docker) -> None:
    """`--volumes` : geste explicite, jamais appelé par les skills.

    Le jeu le plus récent est celui de l'enregistrement courant : il est conservé.
    """
    docker.volumes(
        "runner-aaaa1111-cache-3c3f060a",
        "runner-bbbb2222-cache-3c3f060a",
        "runner-bbbb2222-cache-c33bcaa1",
        creations={
            "runner-aaaa1111-cache-3c3f060a": "2026-01-01T00:00:00Z",
            "runner-bbbb2222-cache-3c3f060a": "2026-07-01T00:00:00Z",
            "runner-bbbb2222-cache-c33bcaa1": "2026-07-02T00:00:00Z",
        },
    )
    acheve = docker.lance("--volumes")
    assert docker.volumes_supprimes == ["runner-aaaa1111-cache-3c3f060a"]
    assert "runner-bbbb2222" in acheve.stderr


def test_volumes_conserve_un_volume_en_service(docker: Docker) -> None:
    """Filet de sécurité final : docker refuse de retirer un volume monté, le script l'accepte."""
    docker.volumes(
        "runner-aaaa1111-cache-3c3f060a",
        "runner-bbbb2222-cache-3c3f060a",
        creations={
            "runner-aaaa1111-cache-3c3f060a": "2026-01-01T00:00:00Z",
            "runner-bbbb2222-cache-3c3f060a": "2026-07-01T00:00:00Z",
        },
    )
    docker.volume_en_service("runner-aaaa1111-cache-3c3f060a")
    acheve = docker.lance("--volumes")
    assert docker.volumes_supprimes == []
    assert "conservé" in acheve.stderr


def test_option_inconnue_refusee(docker: Docker) -> None:
    acheve = docker.lance("--tout-effacer")
    assert acheve.returncode == 2
    assert "option inconnue" in acheve.stderr

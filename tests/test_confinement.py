"""Ce qu'un agent lance pendant sa tâche ne survit pas à sa tâche (#1279).

Le run `3fe501fc0878` a laissé derrière lui un Edge headless et son port de
débogage ouvert, rattaché à un `bash.exe` déjà mort. Les trois critères du ticket,
et ce qui les tient :

① **à la clôture d'une tâche — succès, échec, relance ou annulation —, les
   processus nés de sa session sont arrêtés.** Éprouvé sur la **chaîne réelle** :
   le fournisseur Claude, le transport du SDK, le vrai lanceur du confinement
   (`python -m maestro.sandbox.confinement`) et un **faux CLI** qui lance en
   arrière-plan un processus dont le parent meurt aussitôt — la forme exacte de
   l'incident. Une relance est une nouvelle session : la tentative en échec a
   rangé derrière elle avant que la suivante ne parte (le cas « échec »). Le
   lanceur seul est éprouvé aussi, avec un vrai `bash` et son `sleep &` — ce que
   MSYS détache de l'arbre des parents Windows —, et arrêté en pleine session ;
② **un processus qui ne peut pas être arrêté est dit au journal du run, avec son
   nom et son pid** : le relevé du lanceur (`_arreter`, sur un arbre qui résiste),
   sa phrase, et l'étape `:processus` que l'exécuteur écrit — rangée par le pont
   avec sa tâche ;
③ **le test demandé** : `test_un_processus_lance_en_arriere_plan_est_arrete_a_la_fin_de_la_tache`
   et `test_y_compris_quand_la_tache_echoue`.

Deux coutures dans le transport du SDK, posées sur ses méthodes privées à dessein
(comme `tests/test_plafond_flux.py`) : la commande lancée — le lanceur joué par
`python -m`, parce que son exécutable n'est installé qu'avec le paquet — et la
recherche du binaire. Tout le reste est le vrai chemin.
"""

import asyncio
import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest
from claude_agent_sdk import AssistantMessage, TextBlock
from claude_agent_sdk._internal.transport import subprocess_cli
from double_cli_claude import DoubleCli

from maestro.agents.permissions import PolitiqueOutils
from maestro.controltower.bridge import evenements_depuis_step
from maestro.controltower.events import EVENEMENT_AGENT_ACTIVITE
from maestro.engine import OrchestrationEngine
from maestro.engine.executor import (
    STATUT_PROCESSUS_ARRETES,
    STATUT_PROCESSUS_SURVIVANTS,
    STATUT_SESSION_NON_CONFINEE,
    SUFFIXE_ETAPE_PROCESSUS,
)
from maestro.orchestrator import Orchestrator
from maestro.providers import ClaudeProvider, Credentials, GardeFouInoperant
from maestro.providers import claude as claude_mod
from maestro.providers.base import ModelProvider
from maestro.sandbox import confinement
from maestro.sandbox import verification as execution
from maestro.sandbox.arbre import ProcessusNomme
from maestro.sandbox.confinement import (
    ENV_COMMANDE,
    ENV_RELEVE,
    ReleveConfinement,
    SessionConfinee,
)
from maestro.telemetry import RunJournal

#: La racine du dépôt : le lanceur, joué par `python -m` depuis un répertoire de
#: test, doit importer **ce** `maestro` — pas celui qu'un venv partagé désigne.
RACINE = Path(__file__).resolve().parents[1]

#: L'interpréteur shell des agents (Git Bash sous Windows), relevé **avant** que la
#: garde du conftest ne le retire à la vérification des commandes (#1160).
BASH = execution.interprete()

#: Le lanceur, tel que le SDK le lancerait s'il était installé.
LANCEUR = [sys.executable, "-m", "maestro.sandbox.confinement"]

#: L'interpréteur des faux CLI et des sessions : celui **de base**, pas le
#: redirecteur du venv. Sous Windows avec le Python du Store, le redirecteur fait
#: naître son interpréteur par un alias d'application, donc hors du job (mesuré,
#: cf. `maestro.sandbox.arbre`) ; le veilleur l'adopte, mais une chaîne de trois
#: redirecteurs dont l'intermédiaire meurt en cent millisecondes est précisément la
#: limite qu'il nomme — un test qui en dépendrait serait un tirage. L'évasion par
#: alias a son test à elle, qui la provoque exprès.
PYTHON = getattr(sys, "_base_executable", sys.executable)

#: Ce qui lance, depuis n'importe quel processus Python, un dormeur **détaché** par
#: un intermédiaire qui meurt aussitôt — le `bash` de l'incident, mort, et son
#: `msedge` qui tourne encore. Détaché pour de bon : groupe et session à lui sous
#: POSIX, hors console sous Windows. Le pid du dormeur est écrit dans `$PID_FICHIER`.
ARRIERE_PLAN = textwrap.dedent(
    '''
    import os, subprocess, sys
    detache = (
        {"creationflags": 0x00000008 | 0x00000200}
        if sys.platform == "win32"
        else {"start_new_session": True}
    )
    code = (
        "import subprocess, sys\\n"
        "kw = " + repr(detache) + "\\n"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)'],"
        " stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)\\n"
        "print(p.pid)\\n"
    )
    sortie = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    with open(os.environ["PID_FICHIER"], "w", encoding="utf-8") as releve:
        releve.write(sortie.stdout.strip())
    '''
)

#: Faux CLI Claude Code : le strict nécessaire du protocole `stream-json` — réponse
#: à `initialize`, un message en entrée, un résultat en sortie —, qui lance en
#: arrière-plan avant de répondre. `MAESTRO_FAUX_CLI_ISSUE` choisit la fin :
#: `succes`, `echec` (le CLI meurt en code 1), `attente` (il ne répond jamais — la
#: tâche sera annulée).
FAUX_CLI = textwrap.dedent(
    '''
    """Faux CLI : lance en arrière-plan un processus dont le parent meurt aussitôt (#1279)."""
    import json
    import os
    import runpy
    import sys

    session = "faux-cli"
    issue = os.environ.get("MAESTRO_FAUX_CLI_ISSUE", "succes")


    def ecrire(message):
        sys.stdout.buffer.write(json.dumps(message).encode("utf-8") + b"\\n")
        sys.stdout.buffer.flush()


    for brute in sys.stdin:
        message = json.loads(brute)
        if message.get("type") == "control_request":
            ecrire({
                "type": "control_response",
                "response": {
                    "subtype": "success",
                    "request_id": message["request_id"],
                    "response": {},
                },
            })
        elif message.get("type") == "user":
            runpy.run_path(os.environ["MAESTRO_FAUX_CLI_ARRIERE_PLAN"])
            if issue == "echec":
                sys.exit(1)
            if issue == "attente":
                continue
            ecrire({
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "model": "claude-opus-5",
                    "content": [{"type": "text", "text": "Maquette vérifiée."}],
                },
                "parent_tool_use_id": None,
                "session_id": session,
            })
            ecrire({
                "type": "result",
                "subtype": "success",
                "duration_ms": 1,
                "duration_api_ms": 1,
                "is_error": False,
                "num_turns": 1,
                "session_id": session,
                "result": "Maquette vérifiée.",
            })
    '''
)


# --- Harnais ----------------------------------------------------------------------------


def _vivant(pid: int) -> bool:
    """Ce pid vit-il encore ? Un zombie compte pour mort — il ne tient plus rien.

    Témoin **indépendant** de ce qu'on éprouve : `tasklist` sous Windows, `/proc`
    ailleurs — et non les sondes de `maestro.sandbox.arbre`.
    """
    if sys.platform == "win32":
        sortie = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout
        return f'"{pid}"' in sortie
    stat = Path(f"/proc/{pid}/stat")
    if stat.parent.is_dir():
        try:
            texte = stat.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        return texte[texte.rfind(")") + 2 :].split()[0] != "Z"
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _mort_sous(pid: int, delai_s: float = 5.0) -> bool:
    echeance = time.monotonic() + delai_s
    while _vivant(pid):
        if time.monotonic() >= echeance:
            return False
        time.sleep(0.1)
    return True


def _achever(pid: int) -> None:
    """Filet du test : un dormeur qui aurait survécu ne survit pas au test."""
    if not _vivant(pid):
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, check=False)
    else:
        try:
            os.kill(pid, 9)
        except OSError:
            pass


@pytest.fixture
def arriere_plan(tmp_path, monkeypatch) -> Path:
    """Le script qui lance le dormeur détaché ; rend le fichier où son pid sera écrit."""
    script = tmp_path / "arriere_plan.py"
    script.write_text(ARRIERE_PLAN, encoding="utf-8")
    pid = tmp_path / "pid.txt"
    monkeypatch.setenv("MAESTRO_FAUX_CLI_ARRIERE_PLAN", str(script))
    monkeypatch.setenv("PID_FICHIER", str(pid))
    yield pid
    if pid.exists() and pid.read_text(encoding="utf-8").strip():
        _achever(int(pid.read_text(encoding="utf-8")))


@pytest.fixture
def faux_cli(monkeypatch, tmp_path, arriere_plan) -> Path:
    """Branche le vrai transport du SDK sur le lanceur, et le lanceur sur le faux CLI."""
    script = tmp_path / "faux_claude.py"
    script.write_text(FAUX_CLI, encoding="utf-8")
    construire = subprocess_cli.SubprocessCLITransport._build_command

    def commande(self):
        # Les arguments restent ceux que le SDK a construits à partir de nos
        # options ; seul l'exécutable change : le lanceur, joué par `python -m`.
        return [*LANCEUR, *construire(self)[1:]]

    monkeypatch.setattr(subprocess_cli.SubprocessCLITransport, "_build_command", commande)
    monkeypatch.setattr(claude_mod, "_commande_du_cli", lambda: (PYTHON, str(script)))
    monkeypatch.setattr(confinement, "chemin_lanceur", lambda: Path(sys.executable))
    monkeypatch.setenv("CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK", "1")
    monkeypatch.setenv("PYTHONPATH", str(RACINE))
    return arriere_plan


def _run_agent(provider: ClaudeProvider, workspace: Path, releves: list[ReleveConfinement]):
    return provider.run_agent(
        "Vérifie la maquette.",
        model="claude-opus-5",
        workspace=workspace,
        tools=("Bash",),
        on_processus=releves.append,
    )


# --- ①③ La chaîne réelle : fournisseur, SDK, lanceur, faux CLI ---------------------------


def test_un_processus_lance_en_arriere_plan_est_arrete_a_la_fin_de_la_tache(
    faux_cli, tmp_path
):
    releves: list[ReleveConfinement] = []

    reponse = asyncio.run(_run_agent(ClaudeProvider(Credentials()), tmp_path, releves))

    assert reponse == "Maquette vérifiée."
    pid = int(faux_cli.read_text(encoding="utf-8"))
    assert _mort_sous(pid), f"le processus {pid} lancé pendant la tâche lui survit"
    # Et c'est dit : le dormeur est nommé parmi ce que la session laissait tourner.
    (releve,) = releves
    assert pid in {p.pid for p in releve.arretes}
    assert all(p.nom for p in releve.arretes)
    assert releve.survivants == ()
    assert f"(PID {pid})" in releve.phrase()


def test_y_compris_quand_la_tache_echoue(faux_cli, tmp_path, monkeypatch):
    # Le CLI meurt en code 1 juste après avoir lancé son arrière-plan : la tâche
    # échoue, et c'est de cette tentative-là qu'une relance repartirait.
    monkeypatch.setenv("MAESTRO_FAUX_CLI_ISSUE", "echec")
    releves: list[ReleveConfinement] = []

    with pytest.raises(Exception):  # noqa: B017 — l'échec du SDK, quel que soit son type
        asyncio.run(_run_agent(ClaudeProvider(Credentials()), tmp_path, releves))

    pid = int(faux_cli.read_text(encoding="utf-8"))
    assert _mort_sous(pid), f"le processus {pid} survit à la tâche en échec"
    (releve,) = releves
    assert pid in {p.pid for p in releve.arretes}


def test_y_compris_quand_la_tache_est_annulee(faux_cli, tmp_path, monkeypatch):
    # Le CLI ne répond jamais : c'est l'annulation de la tâche qui ferme la session.
    monkeypatch.setenv("MAESTRO_FAUX_CLI_ISSUE", "attente")
    releves: list[ReleveConfinement] = []

    async def scenario() -> None:
        tache = asyncio.create_task(
            _run_agent(ClaudeProvider(Credentials()), tmp_path, releves)
        )
        echeance = time.monotonic() + 30
        while not (faux_cli.exists() and faux_cli.read_text(encoding="utf-8").strip()):
            assert time.monotonic() < echeance, "le faux CLI n'a jamais lancé son arrière-plan"
            await asyncio.sleep(0.05)
        tache.cancel()
        with pytest.raises(asyncio.CancelledError):
            await tache

    asyncio.run(scenario())

    pid = int(faux_cli.read_text(encoding="utf-8"))
    assert _mort_sous(pid), f"le processus {pid} survit à la tâche annulée"


def test_un_callback_qui_leve_ne_casse_pas_la_tache(faux_cli, tmp_path):
    def casse(_releve: ReleveConfinement) -> None:
        raise RuntimeError("journal indisponible")

    reponse = asyncio.run(
        ClaudeProvider(Credentials()).run_agent(
            "Vérifie la maquette.",
            model="claude-opus-5",
            workspace=tmp_path,
            tools=("Bash",),
            on_processus=casse,
        )
    )

    assert reponse == "Maquette vérifiée."
    assert _mort_sous(int(faux_cli.read_text(encoding="utf-8")))


def test_sans_lanceur_installe_la_session_tourne_et_le_dit(monkeypatch, tmp_path):
    # Un clone qui n'a pas réinstallé le paquet : le SDK lance son CLI comme avant
    # — `cli_path` vide —, et le relevé dit que la session n'était pas confinée.
    monkeypatch.setattr(confinement, "chemin_lanceur", lambda: None)
    vu: dict[str, object] = {}

    class _Texte:
        def __init__(self, text):
            self.text = text

    class _Message:
        def __init__(self, content):
            self.content = content

    async def fake_query(*, prompt, options):
        vu["cli_path"] = options.cli_path
        vu["env"] = dict(options.env)
        yield _Message([_Texte("Livré.")])

    monkeypatch.setattr(claude_mod, "query", fake_query)
    monkeypatch.setattr(claude_mod, "AssistantMessage", _Message)
    monkeypatch.setattr(claude_mod, "TextBlock", _Texte)
    releves: list[ReleveConfinement] = []

    asyncio.run(_run_agent(ClaudeProvider(Credentials()), tmp_path, releves))

    assert vu["cli_path"] is None
    assert ENV_COMMANDE not in vu["env"]
    (releve,) = releves
    assert "maestro-confinement" in releve.non_confinee
    assert releve.phrase().startswith("Session de l'agent non confinée")


async def _livre(*, prompt, options):
    """La session d'un agent qui livre, telle que le double du CLI la rend (#1304)."""
    yield AssistantMessage(content=[TextBlock(text="Livré.")], model="claude-double")


def _sous_politique(provider: ClaudeProvider, workspace: Path) -> str:
    return asyncio.run(
        provider.run_agent(
            "Fais",
            model="claude-double",
            workspace=workspace,
            tools=("Read",),
            politique=PolitiqueOutils(deny=("Bash",)),
        )
    )


def test_la_sonde_du_point_de_controle_passe_par_le_meme_lanceur(monkeypatch, tmp_path):
    # La sonde de démarrage (#1304) est une session du CLI comme une autre : elle
    # est confinée comme celle de l'agent — même lanceur, même protocole.
    monkeypatch.setattr(confinement, "chemin_lanceur", lambda: Path("maestro-confinement"))
    cli = DoubleCli(monkeypatch, session=_livre)

    assert _sous_politique(ClaudeProvider(Credentials()), tmp_path) == "Livré."

    (sonde,) = cli.sondes
    (session,) = cli.sessions
    assert sonde.cli_path == session.cli_path == Path("maestro-confinement")
    assert ENV_COMMANDE in sonde.env
    # Le dossier du relevé est parti avec la session soldée.
    assert not Path(session.env[ENV_RELEVE]).parent.exists()


def test_une_sonde_qui_leve_ne_laisse_pas_le_dossier_du_releve(monkeypatch, tmp_path):
    # Le garde-fou ne tient pas : aucune session d'agent ne suivra, et le dossier
    # préparé pour son relevé ne doit pas attendre le ramassage.
    monkeypatch.setattr(confinement, "chemin_lanceur", lambda: Path("maestro-confinement"))
    cli = DoubleCli(monkeypatch, applique_les_refus=False, session=_livre)

    with pytest.raises(GardeFouInoperant):
        _sous_politique(ClaudeProvider(Credentials()), tmp_path)

    (sonde,) = cli.sondes
    assert cli.sessions == []
    assert not Path(sonde.env[ENV_RELEVE]).parent.exists()


# --- ① Le lanceur seul ------------------------------------------------------------------


def _confiner(commande, tmp_path, *, releve=True, **popen):
    """Lance `commande` sous le lanceur, comme le SDK le ferait ; rend le processus et le relevé."""
    fichier = tmp_path / "releve.json"
    env = {**os.environ, ENV_COMMANDE: json.dumps(list(commande)), "PYTHONPATH": str(RACINE)}
    if releve:
        env[ENV_RELEVE] = str(fichier)
    process = subprocess.Popen([*LANCEUR, *popen.pop("arguments", ())], env=env, **popen)
    return process, fichier


def _releve(fichier: Path) -> ReleveConfinement:
    return ReleveConfinement.from_dict(json.loads(fichier.read_text(encoding="utf-8")))


SESSION = textwrap.dedent(
    '''
    """Une session : lance son arrière-plan, écrit son environnement et ses arguments, sort."""
    import json, os, runpy, sys
    runpy.run_path(os.environ["MAESTRO_FAUX_CLI_ARRIERE_PLAN"])
    with open(os.environ["SESSION_VUE"], "w", encoding="utf-8") as vue:
        json.dump({"env": sorted(os.environ), "argv": sys.argv[1:]}, vue)
    sys.exit(int(sys.argv[1]))
    '''
)


def test_le_lanceur_relaie_arguments_et_code_et_tait_son_protocole(
    arriere_plan, tmp_path, monkeypatch
):
    script = tmp_path / "session.py"
    script.write_text(SESSION, encoding="utf-8")
    vue = tmp_path / "vue.json"
    monkeypatch.setenv("SESSION_VUE", str(vue))

    process, fichier = _confiner(
        [PYTHON, str(script)], tmp_path, arguments=("3", "--output-format", "stream-json")
    )

    assert process.wait(timeout=60) == 3
    session = json.loads(vue.read_text(encoding="utf-8"))
    assert session["argv"] == ["3", "--output-format", "stream-json"]
    # Le protocole du lanceur n'entre pas dans l'environnement de la session : ce
    # que l'agent lance n'a pas à le voir.
    assert ENV_COMMANDE not in session["env"] and ENV_RELEVE not in session["env"]
    pid = int(arriere_plan.read_text(encoding="utf-8"))
    assert _mort_sous(pid)
    assert pid in {p.pid for p in _releve(fichier).arretes}


def test_le_lanceur_sans_commande_ne_lance_rien(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != ENV_COMMANDE}
    env["PYTHONPATH"] = str(RACINE)

    rendu = subprocess.run(
        [*LANCEUR, "-v"], env=env, capture_output=True, text=True, encoding="utf-8", check=False
    )

    assert rendu.returncode == 2
    assert ENV_COMMANDE in rendu.stderr


@pytest.mark.skipif(BASH is None, reason="bash introuvable")
def test_le_sleep_detache_par_bash_ne_survit_pas(tmp_path):
    """L'incident, avec le vrai interpréteur des agents : `… &` depuis `bash`, puis `bash` sort.

    Sous Windows, c'est ce que MSYS détache de l'arbre des parents (mesure de
    `maestro.sandbox.verification`) ; le pid Windows du `sleep` se lit dans
    `/proc/<pid>/winpid`, que MSYS expose.
    """
    assert BASH is not None
    fichier_pid = tmp_path / "winpid.txt"
    script = (
        "sleep 300 & p=$!; "
        'if [ -r "/proc/$p/winpid" ]; then cat "/proc/$p/winpid"; else echo "$p"; fi '
        f'> "{fichier_pid.as_posix()}"'
    )

    process, fichier = _confiner([*BASH, script], tmp_path)

    try:
        assert process.wait(timeout=60) == 0
        pid = int(fichier_pid.read_text(encoding="utf-8").strip())
        assert _mort_sous(pid), f"le sleep {pid} lancé par bash survit à la session"
        assert pid in {p.pid for p in _releve(fichier).arretes}
    finally:
        if fichier_pid.exists() and fichier_pid.read_text(encoding="utf-8").strip():
            _achever(int(fichier_pid.read_text(encoding="utf-8")))


@pytest.mark.skipif(BASH is None, reason="bash introuvable")
def test_un_interprete_lance_en_arriere_plan_par_bash_ne_survit_pas(tmp_path):
    """Le `python -m http.server &` d'un agent, lancé par l'interpréteur **de base**.

    Sous Windows avec le Python du Store, c'est un **alias d'application** : le
    processus naît hors du job, et seul le veilleur l'y rattrape (mesuré — sans lui,
    il survivait à l'arrêt). Ailleurs, c'est un processus ordinaire du groupe. Le
    pid est celui que l'interpréteur imprime lui-même : celui que `bash` croit avoir
    lancé n'est pas toujours lui.
    """
    assert BASH is not None
    fichier_pid = tmp_path / "pid.txt"
    dormeur = "import os, time; print(os.getpid(), flush=True); time.sleep(300)"
    script = (
        f"'{Path(PYTHON).as_posix()}' -c '{dormeur}' > '{fichier_pid.as_posix()}' & "
        "sleep 1"
    )

    process, fichier = _confiner([*BASH, script], tmp_path)

    try:
        assert process.wait(timeout=60) == 0
        pid = int(fichier_pid.read_text(encoding="utf-8").strip())
        assert _mort_sous(pid), f"l'interpréteur {pid} lancé par bash survit à la session"
        assert pid in {p.pid for p in _releve(fichier).arretes}
    finally:
        if fichier_pid.exists() and fichier_pid.read_text(encoding="utf-8").strip():
            _achever(int(fichier_pid.read_text(encoding="utf-8")))


def test_le_lanceur_arrete_en_pleine_session_emporte_l_arbre(arriere_plan, tmp_path):
    """Le SDK termine le lanceur quand le CLI ne sort pas dans son délai de grâce.

    Sous POSIX le signal est intercepté et mène au même arrêt ; sous Windows il n'y
    a pas de signal, mais le job « tué à la fermeture » emporte l'arbre quand le
    système ferme la poignée du lanceur mort.
    """
    attente = tmp_path / "attente.py"
    attente.write_text(
        "import os, runpy, time\n"
        "runpy.run_path(os.environ['MAESTRO_FAUX_CLI_ARRIERE_PLAN'])\n"
        "time.sleep(300)\n",
        encoding="utf-8",
    )
    process, _ = _confiner([PYTHON, str(attente)], tmp_path)
    try:
        echeance = time.monotonic() + 30
        while not (arriere_plan.exists() and arriere_plan.read_text(encoding="utf-8").strip()):
            assert time.monotonic() < echeance, "la session n'a jamais lancé son arrière-plan"
            time.sleep(0.05)

        process.terminate()
        process.wait(timeout=30)

        assert _mort_sous(int(arriere_plan.read_text(encoding="utf-8")))
    finally:
        if process.poll() is None:
            process.kill()


# --- ② Ce qui résiste est nommé ----------------------------------------------------------


class _ArbreQuiResiste:
    """Un arbre dont un membre survit à l'arrêt — ce qu'aucun processus de test ne sait faire."""

    def __init__(self) -> None:
        self.process = subprocess.Popen([sys.executable, "-c", "pass"])
        self.process.wait()
        self.confine = True
        self.arrete = False
        self.ferme = False

    def membres(self):
        return (
            ProcessusNomme("node.exe", 4100),
            ProcessusNomme("msedge.exe", 37484),
            ProcessusNomme("claude.exe", self.process.pid),
        )

    def arreter(self) -> None:
        self.arrete = True

    def survivants(self, parmi, *, delai_s):
        return tuple(p for p in parmi if p.pid == 37484)

    def fermer(self) -> None:
        self.ferme = True


def test_un_processus_qui_resiste_a_l_arret_est_nomme_avec_son_pid():
    arbre = _ArbreQuiResiste()

    releve = confinement._arreter(arbre)  # type: ignore[arg-type]

    assert arbre.arrete and arbre.ferme
    assert releve.survivants == (ProcessusNomme("msedge.exe", 37484),)
    # La session elle-même n'est pas un reste : elle n'est pas comptée parmi les arrêtés.
    assert releve.arretes == (ProcessusNomme("node.exe", 4100),)
    assert releve.phrase() == (
        "1 processus lancé pendant la tâche n’a pas pu être arrêté à sa clôture : "
        "msedge.exe (PID 37484). Un autre l’a été : node.exe (PID 4100)."
    )


def test_la_phrase_nomme_tous_les_survivants_et_borne_les_arretes():
    arretes = tuple(ProcessusNomme("msedge.exe", 100 + i) for i in range(13))
    survivants = tuple(ProcessusNomme("svc.exe", 900 + i) for i in range(12))

    seuls_arretes = ReleveConfinement(arretes=arretes).phrase()
    avec_survivants = ReleveConfinement(arretes=arretes, survivants=survivants).phrase()

    assert seuls_arretes.startswith(
        "13 processus lancés pendant la tâche lui survivaient — arrêtés à sa clôture : "
    )
    assert seuls_arretes.endswith("msedge.exe (PID 109) et 3 autres.")
    # Les survivants, eux, sont **tous** nommés — c'est le critère.
    assert all(f"svc.exe (PID {p.pid})" in avec_survivants for p in survivants)
    assert ReleveConfinement().vide and ReleveConfinement().phrase() == ""


def test_le_releve_voyage_et_se_relit(tmp_path):
    releve = ReleveConfinement(
        arretes=(ProcessusNomme("node", 12),), survivants=(ProcessusNomme("msedge.exe", 37484),)
    )

    assert ReleveConfinement.from_dict(json.loads(json.dumps(releve.to_dict()))) == releve


def test_la_session_preparee_se_solde_sur_son_releve(monkeypatch, tmp_path):
    monkeypatch.setattr(confinement, "chemin_lanceur", lambda: Path("maestro-confinement"))
    session = SessionConfinee.preparer(("claude",))
    releve = ReleveConfinement(survivants=(ProcessusNomme("msedge.exe", 37484),))
    Path(session.env[ENV_RELEVE]).write_text(json.dumps(releve.to_dict()), encoding="utf-8")
    dossier = Path(session.env[ENV_RELEVE]).parent

    assert session.lanceur == Path("maestro-confinement")
    assert json.loads(session.env[ENV_COMMANDE]) == ["claude"]
    assert session.solder() == releve
    assert not dossier.exists()


def test_une_session_sans_releve_ecrit_ne_dit_rien(monkeypatch):
    # Le lanceur terminé par le SDK sous Windows : l'arbre est parti avec le job,
    # mais rien n'a été écrit — et on ne dit rien qu'on ne sait pas.
    monkeypatch.setattr(confinement, "chemin_lanceur", lambda: Path("maestro-confinement"))
    session = SessionConfinee.preparer(("claude",))

    assert session.solder().vide


def test_sans_cli_la_session_n_est_pas_confinee():
    releve = SessionConfinee.preparer(None).solder()

    assert releve.non_confinee == "le CLI de la session est introuvable"


def test_le_lanceur_est_un_point_d_entree_du_paquet():
    """Garde d'installation, comme celle du shim : ce que `cli_path` pointera existe.

    Le point d'entrée est déclaré dans `pyproject.toml` ; et dès que le paquet
    installé le déclare aussi, son exécutable est à côté de l'interpréteur. Un venv
    installé avant #1279 ne le déclare pas encore : c'est le cas « lanceur absent »,
    que la session dit au journal, et que `setup.sh` répare (dérive de `pyproject.toml`).
    """
    from importlib.metadata import entry_points

    declare = f'{confinement.NOM_LANCEUR} = "maestro.sandbox.confinement:main"'
    assert declare in (RACINE / "pyproject.toml").read_text(encoding="utf-8")
    installes = {e.name for e in entry_points(group="console_scripts")}
    if confinement.NOM_LANCEUR in installes:
        assert confinement.chemin_lanceur() is not None


# --- ② Le journal du run le dit ---------------------------------------------------------


class _Planificateur(ModelProvider):
    name = "planificateur"

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        return json.dumps(
            [
                {
                    "id": "maquette",
                    "titre": "Vérifier la maquette",
                    "description": "Rendre la maquette et la vérifier.",
                    "competences_requises": ["frontend"],
                    "format_sortie": "Texte",
                    "dependances": [],
                }
            ],
            ensure_ascii=False,
        )


class _SessionQuiLaisse(ModelProvider):
    """Exécutant outillé dont la session a laissé `releve` derrière elle."""

    name = "session-qui-laisse"

    def __init__(self, releve: ReleveConfinement) -> None:
        self.releve = releve

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):  # pragma: no cover
        return "TEXTE"

    async def run_agent(self, prompt, *, workspace, on_processus=None, **canaux):
        (Path(workspace) / "maquette.html").write_text("<main></main>", encoding="utf-8")
        if on_processus is not None:
            on_processus(self.releve)
        return "Maquette vérifiée."


def _lignes_processus(releve: ReleveConfinement) -> list:
    journal = RunJournal(run_id="run-1279")
    moteur = OrchestrationEngine(
        _SessionQuiLaisse(releve), Orchestrator(_Planificateur(), model="claude-opus-5")
    )
    asyncio.run(moteur.run("Livrer la maquette", journal=journal))
    return [r for r in journal.records if r.etape.endswith(SUFFIXE_ETAPE_PROCESSUS)]


def test_un_processus_qui_n_a_pas_pu_etre_arrete_est_dit_au_journal_du_run():
    releve = ReleveConfinement(
        arretes=(ProcessusNomme("node.exe", 4100),),
        survivants=(ProcessusNomme("msedge.exe", 37484),),
    )

    (ligne,) = _lignes_processus(releve)

    assert ligne.etape == f"maquette{SUFFIXE_ETAPE_PROCESSUS}"
    assert ligne.statut == STATUT_PROCESSUS_SURVIVANTS
    assert "msedge.exe (PID 37484)" in ligne.sortie
    assert ligne.usage.appels == 0
    # Le pont la range avec sa tâche, en activité d'agent — jamais en tâche fantôme.
    (evenement,) = evenements_depuis_step(ligne.to_dict())
    assert evenement.type == EVENEMENT_AGENT_ACTIVITE
    assert evenement.tache_id == "maquette"
    assert "msedge.exe (PID 37484)" in evenement.detail


def test_le_journal_dit_aussi_ce_qui_a_ete_arrete_et_une_session_non_confinee():
    (arretes,) = _lignes_processus(ReleveConfinement(arretes=(ProcessusNomme("node", 12),)))
    (non_confinee,) = _lignes_processus(ReleveConfinement(non_confinee="lanceur introuvable"))

    assert arretes.statut == STATUT_PROCESSUS_ARRETES
    assert "node (PID 12)" in arretes.sortie
    assert non_confinee.statut == STATUT_SESSION_NON_CONFINEE
    assert "lanceur introuvable" in non_confinee.sortie


def test_une_session_qui_n_a_rien_laisse_n_ecrit_rien():
    assert _lignes_processus(ReleveConfinement()) == []

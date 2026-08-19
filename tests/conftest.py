"""Garde-fous communs à toute la suite pytest (tickets #195 et #236).

**Le verdict de la suite ne dépend pas du poste qui la joue** : ce qui traîne
dans l'environnement — clés d'un `.env` renseigné, variables du bloc `env` d'un
`.claude/settings.local.json` — est neutralisé ici, une fois pour toute la
suite. Sans quoi le même code rend deux verdicts selon la machine, et c'est en
local que ça se voit (la CI, elle, part d'un environnement nu).

**Aucun test n'a besoin d'un backend** : la suite ne doit ni publier vers
Langfuse, ni ouvrir de connexion vers `LANGFUSE_HOST`, quel que soit le `.env`
du poste qui la joue — au même titre que le réseau débranché d'office côté UI
(`apps/web/tests/setup.ts`). Sans ce garde-fou, un poste dont l'intégration
Langfuse est *opérationnelle* joue la même suite pour le même verdict en
**17 min 51 s** au lieu de 7 min 08 s (#195) : `activer_export_langfuse()` est
appelée par chaque point d'entrée (`engine_cli.main`, `maestro.demo.main`), le
handler posé sur le logger **global** `maestro.trace` survit au test qui l'a
déclenché, et chaque ligne journalisée ensuite part en POST synchrone vers le
vrai hôte — au passage, les évènements des tests polluent le vrai projet
Langfuse.

Deux garde-fous, ici parce qu'ils valent pour la suite entière :

- **les clés Langfuse sont neutralisées dans l'environnement du processus**, ce
  qui rend `activer_export_langfuse()` no-op (sa bascule est purement
  configurative) : aucun handler, donc aucun envoi ;
- **aucun `LangfuseExportHandler` ne survit à un test** : le contrôle échoue le
  test fautif plutôt que de laisser la facture aux suivants.

Les tests qui exercent réellement l'export (`tests/test_langfuse.py`) ne
dépendent pas de l'environnement : ils passent un `Settings` explicite pointant
un serveur d'ingestion factice, en local.

Troisième garde-fou, même motif (#236) : **`MAESTRO_ORCHESTRATE_COULEUR` est
neutralisée**. Elle force les couleurs de `scripts/orchestrate/run.sh` hors
console (`[ -t 1 ] || [ "$MAESTRO_ORCHESTRATE_COULEUR" = 1 ]`) et se pose dans
le bloc `env` d'un `.claude/settings.local.json`, d'où elle fuit dans
l'environnement de toute session lancée depuis ce poste : la sortie capturée par
un test arrive alors truffée de codes ANSI et
`test_sans_le_marqueur_la_sortie_reste_sans_couleur` échoue **en local
seulement**. Quatre sessions ont rouvert la même enquête pour cette fausse
alerte ; c'est le dépôt, pas chaque run, qui doit la tarir.

Quatrième garde-fou, même famille que le troisième : **`MAESTRO_GITHUB_REPO`
est vidée**. Elle choisit le dépôt que vise `scripts/gitlab/lib.sh`, et se pose
exactement au même endroit que la précédente — le bloc `env` d'un
`.claude/settings.local.json` —, d'où elle fuit dans les sous-processus de tous
les tests d'outillage. Un dépôt cible hérité du poste n'a rien à faire dans un
test : les suites qui en visent un le posent explicitement dans l'environnement
du sous-processus qu'elles lancent.

Sa voisine `MAESTRO_FORGE` était épinglée ici du temps où `lib.sh` portait deux
backends (#339, révisé par #343). Le commutateur a été retiré avec la branche
GitLab (#344) : il n'y a plus de valeur à épingler, et la variable n'est plus
lue par rien. Elle reste **vidée** parce que les postes qui l'ont connue la
posent encore — neutraliser une variable morte ne coûte rien, et c'est ce qui
évite qu'elle ne ressuscite en silence si quelqu'un rebranchait un jour une
lecture dessus.

Cinquième garde-fou (#364), **retiré par #365** : `MAESTRO_CYCLE` était épinglée
ici à `labels` le temps que la bascule vive à côté de son retour arrière. Le
commutateur est parti avec les six labels `workflow::*`, donc il n'y a plus de
valeur à épingler ni de backend à choisir — et les suites d'outillage ont vu
leurs doubles portés sur le champ Status (des lignes déjà aplaties, cf. l'en-tête
de `tests/test_collaboration.py`). Ne pas la réintroduire « au cas où » : une
variable épinglée qui ne commande plus rien est ce qui fait croire à un backend
qu'on aurait encore le choix de servir.

Sixième garde-fou (#333), et le seul qui REFUSE de jouer au lieu de
neutraliser : **git absent EN CI est une erreur, pas un saut**. Tous les
précédents protègent le verdict de ce que le poste apporte en trop — ou choisit
à sa place ; celui-ci le protège de ce que l'image du job n'apporte pas. Le
`python:3.11-slim` dans lequel tournait le job `pytest` de la CI GitLab n'avait
pas git,
et ~285 tests d'outillage sont gardés par `skipif(shutil.which("git") is
None)` : le pipeline les sautait tous en silence, vert, depuis toujours — si
bien qu'ils n'ont jamais tourné que sur des postes de développement, tous sous
Windows. Un runner Linux muni de git en a trouvé 16 rouges du premier coup
(#332). Le contrôle ne remet rien au vert : il fait qu'un futur retrait de git
du job — un `container:` posé dans `.github/workflows/ci.yml`, par exemple — se
voie tout de suite.
"""

import logging
import os
import shutil

import pytest

#: Les deux clés dont la présence suffit à poser l'exporteur (`activer_export_langfuse`).
_CLES_LANGFUSE = ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")

#: Hôte imposé aux tests : port *discard* sur la boucle locale, où rien n'écoute.
#: Rien ne devrait construire de publieur depuis l'environnement — si quelque
#: chose le fait, il échoue immédiatement en local au lieu de sortir sur Internet.
HOTE_LANGFUSE_NEUTRE = "http://127.0.0.1:9"

#: La variable qui force les couleurs de `scripts/orchestrate/run.sh` hors console (#236).
CLE_COULEUR_ORCHESTRATE = "MAESTRO_ORCHESTRATE_COULEUR"

#: Le dépôt que vise `scripts/gitlab/lib.sh`, et le commutateur de forge que #344 a retiré.
#: Neutralisés ensemble : ni l'un ni l'autre ne doit être hérité du poste.
CLES_FORGE = ("MAESTRO_FORGE", "MAESTRO_GITHUB_REPO")

#: Variables posées d'office par les intégrations continues. `GITLAB_CI` reste de la liste bien
#: après le retrait de la CI GitLab (#344) : ce qui est testé est « quelqu'un lira-t-il ce compte
#: rendu ? », et un jeton de reconnaissance de plus ne coûte rien là où en manquer un coûte 285
#: tests invisibles.
#: Leur présence distingue « personne n'est là pour lire un `s` dans le compte rendu » d'un
#: lancement sur un poste, où un saut reste une réponse acceptable.
_CLES_CI = ("CI", "GITLAB_CI", "GITHUB_ACTIONS")


def git_manquant_en_ci(environnement: dict[str, str], git: str | None) -> bool:
    """Faut-il refuser de jouer la suite ? — la décision seule, sans rien lire du monde.

    Séparée du hook pour être jouable dans les deux sens sans sous-processus ni git à cacher :
    c'est une règle à deux entrées, et c'est la combinaison « en CI **et** sans git » qui est
    fautive, pas chacune prise à part.
    """
    return git is None and any(environnement.get(cle) for cle in _CLES_CI)


def pytest_configure(config: pytest.Config) -> None:
    """Refuse de jouer la suite EN CI sans git — un saut n'y est pas une réponse (#333).

    Sept modules d'outillage montent un vrai dépôt jetable, et sont gardés par
    `skipif(shutil.which("git") is None)`. Le garde-fou est juste sur un poste : il dit « cette
    machine ne peut pas répondre ». En CI il **transforme une dépendance non satisfaite en silence
    vert** — l'image `python:3.11-slim` du pipeline n'ayant pas git, 285 tests y étaient sautés
    depuis toujours, sans que rien ne distingue ce pipeline d'un pipeline complet. Ils n'ont donc
    tourné que sur des postes de développement, tous sous Windows, jusqu'à ce qu'un runner Linux
    muni de git en trouve 16 rouges d'un coup (#332).

    Ce que ce contrôle achète n'est pas la remise au vert — c'est qu'un futur retrait de git du
    job se voie **immédiatement**, au lieu de rendre 285 tests invisibles en gardant la pipeline
    verte. Sur GitHub Actions, git vient avec le runner hébergé ; le seul geste qui le reperdrait
    est un `container:`, c'est-à-dire exactement l'image slim d'où venait le défaut. L'échec est
    levé à la configuration, avant la collecte : il nomme la cause plutôt que de laisser lire un
    compte rendu criblé de `s`.
    """
    # Poste sans git : le `skipif` de chaque module reste la bonne réponse.
    if not git_manquant_en_ci(dict(os.environ), shutil.which("git")):
        return
    raise pytest.UsageError(
        "git est introuvable alors que la suite tourne en intégration continue : ~285 tests "
        "d'outillage seraient SAUTÉS en silence et la pipeline resterait verte (#333). "
        "Rendre git disponible dans le job (cf. le job `pytest` de .github/workflows/ci.yml)."
    )


def _neutralise_langfuse() -> None:
    """Vide les clés Langfuse de l'environnement du processus de test.

    Les clés sont mises à **vide** et non supprimées : `maestro.config` charge le
    `.env` du dépôt par `load_dotenv(override=False)`, qui ne complète que les
    clés *absentes* de l'environnement. Une clé retirée serait donc recomplétée
    depuis le fichier au premier import du module — une clé vide, jamais.
    """
    for cle in _CLES_LANGFUSE:
        os.environ[cle] = ""
    os.environ["LANGFUSE_HOST"] = HOTE_LANGFUSE_NEUTRE


def _neutralise_couleur_orchestrate() -> None:
    """Vide `MAESTRO_ORCHESTRATE_COULEUR` de l'environnement du processus de test.

    `scripts/orchestrate/run.sh` colore sa sortie dès que la variable vaut `1`,
    même quand `stdout` n'est pas un terminal — c'est ce qui permet à `--detach`
    de garder ses couleurs dans la console qu'il ouvre (#176). Posée dans le bloc
    `env` d'un `.claude/settings.local.json`, elle fuit dans l'environnement de
    toutes les sessions de ce poste, donc dans les sous-processus lancés par
    `tests/test_orchestrate.py`, dont la sortie ressort colorée : le contre-test
    `test_sans_le_marqueur_la_sortie_reste_sans_couleur` échoue sur ce poste et
    nulle part ailleurs (#236).

    Mise à **vide** plutôt que supprimée, comme les clés Langfuse : `run.sh` lit
    `${MAESTRO_ORCHESTRATE_COULEUR:-0}`, pour qui vide et absente valent 0, et une
    valeur vide traverse sans surprise les `env={**os.environ, …}` des tests.
    """
    os.environ[CLE_COULEUR_ORCHESTRATE] = ""


def _neutralise_forge() -> None:
    """Vide `MAESTRO_GITHUB_REPO` et `MAESTRO_FORGE` (#339, révisé par #343 puis #344).

    `MAESTRO_GITHUB_REPO` choisit le dépôt que vise `scripts/gitlab/lib.sh`. Elle
    se pose dans le bloc `env` d'un `.claude/settings.local.json` — exactement où
    se pose la couleur de `run.sh` (#236) —, d'où elle fuit dans les
    sous-processus des suites d'outillage. Un dépôt hérité du poste n'a pas sa
    place dans un test : celles qui en visent un le posent explicitement dans
    l'environnement du sous-processus qu'elles lancent.

    `MAESTRO_FORGE` n'est plus lue par rien depuis #344, qui a retiré le
    commutateur avec la branche GitLab de `lib.sh`. On la vide quand même : elle
    survit dans les `settings.local.json` des postes qui l'ont connue, et une
    variable morte laissée en place est ce qui rend une résurrection silencieuse.
    """
    for cle in CLES_FORGE:
        os.environ[cle] = ""


# Posés à l'import du conftest, donc avant l'import du premier module de test :
# un test qui appelle `load_settings()` dès son import voit déjà l'environnement
# neutralisé (la config relit `os.environ` à chaque appel, rien n'est mis en cache).
_neutralise_langfuse()
_neutralise_couleur_orchestrate()
_neutralise_forge()


@pytest.fixture(autouse=True)
def _pas_de_fuite_d_export_langfuse():
    """Échoue le test qui laisse un `LangfuseExportHandler` sur `maestro.trace`.

    Le logger du journal est global et rien ne retire ses handlers : un handler
    oublié fait payer à **tous les tests suivants** un POST synchrone par ligne
    consignée (#195). La fuite est retirée avant de rendre le verdict, pour que
    l'échec reste celui du test fautif et ne se propage pas à la suite.
    """
    # Imports locaux : le conftest est chargé avant tout module de test, et
    # neutraliser l'environnement (ci-dessus) doit précéder l'import de `maestro`.
    from maestro.telemetry.journal import LOGGER_NAME
    from maestro.telemetry.langfuse import LangfuseExportHandler

    yield

    logger = logging.getLogger(LOGGER_NAME)
    fuites = [h for h in logger.handlers if isinstance(h, LangfuseExportHandler)]
    for handler in fuites:
        logger.removeHandler(handler)
    assert not fuites, (
        f"{len(fuites)} LangfuseExportHandler laissé(s) sur le logger « {LOGGER_NAME} » : "
        "le journal de tous les tests suivants partirait vers Langfuse (#195). "
        "Retirer le handler posé (`logger.removeHandler`) en fin de test."
    )

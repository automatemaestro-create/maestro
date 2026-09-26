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
appelée par chaque point d'entrée (`engine_cli.main`), le
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

Septième garde-fou (#782), et la fuite la plus chère de toutes : **le
fournisseur de modèle du poste ne peut pas être résolu par un test**. Les trois
répondeurs de chat que `create_app()` construit sans fournisseur —
`RepondeurModele`, `RepondeurOrchestration`, `RepondeurAssistanceDocumentee` —
résolvent `provider_from_settings()` **au premier message**, ce qui est voulu
(l'app se construit sans configuration) ; mais un test d'endpoint qui poste dans
un fil **sans injecter de répondeur** appelle alors le vrai modèle sur toute
machine où l'accès est configuré — un poste de développement, par l'abonnement.
Mesuré sur le premier test de câblage de #764 : **43 s** et une vraie réponse
documentée, contre **1,4 s** une fois le fournisseur neutralisé — l'écart *est*
l'appel réseau. En CI le défaut est invisible (sans clés, la résolution échoue et
le canal replie) : il ne se voit que là où il coûte. Les trois neutralisations
précédentes faussent un verdict ; celle-ci part sur le réseau et consomme du
quota. La garde vit dans une fixture `autouse` et fait deux choses, parce que les
répondeurs **avalent** un échec de résolution en réponse de repli (201, « réglage
absent ») — une exception seule serait donc muette sur le chemin même qu'on
protège : elle **lève** `FournisseurDuPosteRefuse` à la place de la lecture des
réglages du poste par la fabrique, en nommant sa cause plutôt que l'erreur
d'authentification que le fournisseur aurait fini par lever ; et elle **échoue le
test à sa sortie** dès qu'une résolution a été tentée, comme la garde Langfuse —
sauf si le test a déjà échoué de cette exception-là, la cause étant alors déjà
nommée. Ce qui distingue « le poste » d'un fournisseur voulu : la garde ne vise
que la résolution **sans `Settings` explicites** — `provider_from_settings()`
nu, c'est-à-dire ce que le poste a configuré ; un test qui construit ses propres
`Settings` et les passe n'est pas concerné, et un test qui veut **vraiment** lire
le poste le dit avec `@pytest.mark.fournisseur_du_poste`. Limite nommée :
`provider_from_settings(load_settings())`, la fabrique par défaut des workers
(`maestro.durable.activities`, `maestro.queue.worker`), lit le poste par un geste
explicite du code appelant — aucun test n'y passe, tous injectent leur fabrique.
La garde est éprouvée sur un cas fautif avant de balayer
(`tests/test_garde_fournisseur.py`) : un test qui poste sans injecter y **passe**
sa phase d'appel, et c'est la garde qui le fait rougir.

Huitième garde-fou (#1164) : **l'espace de données est figé à `commun`**. Depuis
que chaque copie de travail range ses clés et canaux Redis dans son propre espace
(`maestro.espace`), le nom se **déduit** de la copie qui joue le code — celui de
son worktree. Sans épinglage, la même suite nommerait ses clés autrement selon
qu'on la joue dans le clone principal, dans un worktree ou dans le conteneur du
filet local (qui monte un worktree tel quel), et un test qui compare un nom Redis
rendrait deux verdicts. `commun` est l'espace sans préfixe, celui de la CI ; les
tests qui exercent la séparation posent eux-mêmes `MAESTRO_ESPACE`.

Neuvième garde-fou (#1160) : **aucun test ne joue les commandes d'un projet sans
le dire**. Depuis que l'outillage est vérifié en l'exécutant, toute génération —
une quarantaine de tests l'appellent, par l'API ou directement — joue `npm ci`,
`pytest`, `npm run dev` dans une copie du projet. Le verdict dépendrait alors des
outils installés sur le poste (npm ici, pas là), et un test d'écriture paierait une
installation réseau. La garde retire **l'interpréteur** (`maestro.sandbox.
verification.interprete` rend `None`) : chaque commande sort « à vérifier » avec la
raison du poste sans bash — un vrai chemin du produit, instantané, le même partout.
Un test qui veut un verdict passe son propre `joueur` au `Verificateur` ; un test qui
veut **vraiment** jouer une commande le dit avec `@pytest.mark.commandes_jouees`.

Dixième garde-fou (#1304) : **aucun test ne lance le vrai CLI du fournisseur sans qu'on le
demande**. Ce que les doubles ne prouvent pas — que le CLI applique un refus de Maestro — se
prouve sur le CLI réel, avec un vrai modèle : un tel test porte `@pytest.mark.cli_reel` et
reste **sauté**, raison dite, sauf `MAESTRO_TESTS_CLI_REEL=1`. Même verdict sur tous les
postes, et ce qu'il coûte n'est payé que par qui le demande.

Onzième garde-fou (#1295) : **aucun test ne lit les clients d'agents du poste sans le dire**.
Les ponts de l'outillage (`CLAUDE.md`, `GEMINI.md`) suivent les clients installés et leur
version : une analyse jouée par l'API recommanderait autre chose sur un poste qui a Claude Code
que sur la CI, et lancerait `claude --version` à chaque test. La garde ferme la porte du `PATH`
(`maestro.clients_du_poste.resoudre` ne trouve rien) : un poste nu, celui de la CI. Un test qui
veut des clients les passe en double ; un test qui veut **vraiment** ceux du poste le dit avec
`@pytest.mark.clients_du_poste`.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

    from maestro.config import Settings

#: Les deux clés dont la présence suffit à poser l'exporteur (`activer_export_langfuse`).
_CLES_LANGFUSE = ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")

#: Hôte imposé aux tests : port *discard* sur la boucle locale, où rien n'écoute.
#: Rien ne devrait construire de publieur depuis l'environnement — si quelque
#: chose le fait, il échoue immédiatement en local au lieu de sortir sur Internet.
HOTE_LANGFUSE_NEUTRE = "http://127.0.0.1:9"

#: La variable qui force les couleurs de `scripts/orchestrate/run.sh` hors console (#236).
CLE_COULEUR_ORCHESTRATE = "MAESTRO_ORCHESTRATE_COULEUR"

#: La variable qui fixe l'espace de données d'une stack (#1164), et sa valeur de test :
#: l'espace sans préfixe, celui de la CI. Écrites ici et non importées de
#: `maestro.espace` : importer le paquet depuis le conftest le chargerait avant que
#: les autres neutralisations soient posées. `tests/test_espace.py` les confronte.
CLE_ESPACE = "MAESTRO_ESPACE"
ESPACE_DES_TESTS = "commun"

#: Le dépôt que vise `scripts/gitlab/lib.sh`, et le commutateur de forge que #344 a retiré.
#: Neutralisés ensemble : ni l'un ni l'autre ne doit être hérité du poste.
CLES_FORGE = ("MAESTRO_FORGE", "MAESTRO_GITHUB_REPO")

#: Le dépôt des **réglages du poste** (#1022). Pointé sur un dossier jetable par
#: `_reglages_du_poste_isoles` : un répertoire des projets réglé sur le poste
#: ajouterait un point d'entrée à l'explorateur, et le verdict de la suite ne
#: dépend pas du poste (docs/10 §8.7).
CLE_REGLAGES_DIR = "MAESTRO_REGLAGES_DIR"

#: La reprise des agents dans leur projet, jouée au démarrage de l'API (#1038).
CLE_REPRISE_AGENTS = "MAESTRO_REPRISE_AGENTS"

#: Le **régime d'accès** de l'API et ses réglages (#638), plus le port du front
#: dont le défaut d'origines se dérive. Vidés par `_neutralise_acces_api` : les
#: trois premiers se posent dans un `.env`, et `MAESTRO_PORT_UI` dans le bloc
#: `env` du `.claude/settings.local.json` que `worktree.sh` écrit par copie de
#: travail — d'où elle fuit dans l'environnement de toute session du poste. Un
#: port hérité ferait attendre `http://localhost:3017` là où la CI attend 3000 :
#: le verdict dépendrait du worktree qui joue la suite (docs/10 §8.7).
CLES_ACCES_API = (
    "MAESTRO_API_AUTH",
    "MAESTRO_API_JETON",
    "MAESTRO_API_ORIGINES",
    "MAESTRO_PORT_UI",
)

#: Où le jeton de l'API est persisté (#638). Pointé sur un dossier jetable par
#: `_jeton_api_isole` : la suite n'écrit pas dans le `~/.maestro/` du poste, et
#: n'y lit pas non plus un jeton qui donnerait raison à un test pour la mauvaise
#: raison.
CLE_JETON_FICHIER = "MAESTRO_API_JETON_FICHIER"

#: Variables posées d'office par les intégrations continues. `GITLAB_CI` reste de la liste bien
#: après le retrait de la CI GitLab (#344) : ce qui est testé est « quelqu'un lira-t-il ce compte
#: rendu ? », et un jeton de reconnaissance de plus ne coûte rien là où en manquer un coûte 285
#: tests invisibles.
#: Leur présence distingue « personne n'est là pour lire un `s` dans le compte rendu » d'un
#: lancement sur un poste, où un saut reste une réponse acceptable.
_CLES_CI = ("CI", "GITLAB_CI", "GITHUB_ACTIONS")

#: Le marqueur par lequel un test dit qu'il veut VRAIMENT résoudre le fournisseur configuré sur
#: le poste (#782) — la garde s'efface alors. À réserver à un test qui n'appelle aucun modèle
#: réel : lire le poste n'est pas l'appeler, et c'est l'appel qui coûte.
MARQUEUR_FOURNISSEUR_DU_POSTE = "fournisseur_du_poste"

#: La cause que la garde nomme — à la place de l'erreur d'authentification que le fournisseur
#: aurait levée bien plus tard, ou de la vraie réponse qu'il aurait rendue sur un poste configuré.
CAUSE_FOURNISSEUR_DU_POSTE = (
    "ce test allait appeler un vrai modèle : `provider_from_settings()` a résolu le fournisseur "
    "configuré sur CE POSTE (sans `Settings` explicites), ce qui appelle le modèle par "
    "l'abonnement sur la machine d'un développeur (#782, tests/conftest.py). Injecte un "
    "répondeur (`RepondeurScripte`, `JugeScripte`, un point d'injection de `create_app`) ou un "
    "fournisseur factice (`provider=…`), ou remplace "
    "`maestro.providers.factory.provider_from_settings` par un double — et si ce test doit "
    "vraiment lire le poste, marque-le `@pytest.mark.fournisseur_du_poste`."
)


#: Le marqueur par lequel un test dit qu'il veut VRAIMENT jouer des commandes du projet dans
#: une copie de vérification (#1160) — la garde qui retire l'interpréteur s'efface alors.
MARQUEUR_COMMANDES_JOUEES = "commandes_jouees"

#: Le marqueur d'un test qui lance le **vrai** CLI du fournisseur, donc un vrai modèle (#1304).
#: Ce que les doubles ne peuvent pas prouver — que le CLI applique un refus de Maestro — ne se
#: prouve que là. Un tel test coûte un appel de modèle et dépend de l'accès du poste : il est
#: **sauté** sauf demande explicite (`CLE_CLI_REEL`), en le disant, pour que le verdict de la
#: suite reste celui de la CI sur tous les postes (docs/10 §8.7).
MARQUEUR_CLI_REEL = "cli_reel"

#: La variable qui demande ces tests : `1` les joue, toute autre valeur les saute.
CLE_CLI_REEL = "MAESTRO_TESTS_CLI_REEL"

#: Le marqueur d'un test qui lit VRAIMENT les clients d'agents du poste (#1295) — la garde qui
#: ferme la résolution sur le `PATH` s'efface alors.
MARQUEUR_CLIENTS_DU_POSTE = "clients_du_poste"


class FournisseurDuPosteRefuse(RuntimeError):
    """Levée par la garde à la place de la lecture des réglages du poste par la fabrique (#782).

    Son texte est `CAUSE_FOURNISSEUR_DU_POSTE` : ce que le test allait faire et comment y
    remédier — jamais une panne de fournisseur, que ce test n'a pas atteint.
    """


#: Ce dont le test a échoué en phase d'appel (le type de l'exception), ou `None` s'il a passé —
#: posé par `pytest_runtest_makereport`, lu par la garde à la sortie du test pour ne pas nommer
#: deux fois la même cause.
_ECHEC_APPEL: pytest.StashKey[type[BaseException] | None] = pytest.StashKey()


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
    # Le marqueur d'exemption de la garde du fournisseur (#782), déclaré pour que
    # `--strict-markers` n'y voie jamais une faute de frappe.
    config.addinivalue_line(
        "markers",
        f"{MARQUEUR_FOURNISSEUR_DU_POSTE}: ce test résout VOLONTAIREMENT le fournisseur de "
        "modèle configuré sur le poste (#782) — la garde de tests/conftest.py s'efface ; à "
        "réserver à un test qui n'appelle aucun modèle réel.",
    )
    config.addinivalue_line(
        "markers",
        f"{MARQUEUR_COMMANDES_JOUEES}: ce test joue VOLONTAIREMENT des commandes dans une copie "
        "de vérification de l'outillage (#1160) — la garde de tests/conftest.py qui retire "
        "l'interpréteur s'efface.",
    )
    config.addinivalue_line(
        "markers",
        f"{MARQUEUR_CLI_REEL}: ce test lance le VRAI CLI du fournisseur et appelle un vrai modèle "
        f"(#1304) — sauté sauf `{CLE_CLI_REEL}=1`.",
    )
    config.addinivalue_line(
        "markers",
        f"{MARQUEUR_CLIENTS_DU_POSTE}: ce test lit VOLONTAIREMENT les clients d'agents installés "
        "sur le poste (#1295) — la garde de tests/conftest.py qui ferme le `PATH` s'efface.",
    )
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


def _neutralise_reprise_agents() -> None:
    """Coupe la reprise des agents dans leur projet pendant la suite (#1038).

    Au démarrage, l'API rattache au projet qui les utilise les agents et réglages
    globaux qu'un poste porte encore — best-effort, idempotente, et elle n'écrit
    que si **un seul** projet est déclaré. Or beaucoup de tests construisent
    `create_app()` en n'injectant qu'une partie des dépôts : les autres
    retombent alors sur ceux de la config, c'est-à-dire `core/` du dépôt, et le
    service des projets sur `core/projets/`. Sur un poste qui y a déclaré un
    projet — et un seul —, ouvrir un `TestClient` **écrirait** dans
    `core/*/_projets/<id>/`.

    C'est la fuite du poste dans le verdict que docs/10 §8 interdit : ce que la
    suite mesure ne doit pas dépendre des projets déclarés sur la machine qui la
    joue. Le mécanisme lui-même s'éprouvera en l'appelant, jamais en démarrant
    une app (lot #1043).

    Mise à `0` plutôt que vidée : `0` est ce que la variable *signifie*, et le
    code lit une valeur, pas une absence.
    """
    os.environ[CLE_REPRISE_AGENTS] = "0"


def _neutralise_acces_api() -> None:
    """Vide le régime d'accès de l'API et le port du front (#638) — voir `CLES_ACCES_API`.

    Vidées et non supprimées, comme les clés Langfuse : `maestro.config` charge
    le `.env` du dépôt par `load_dotenv(override=False)`, qui recomplèterait une
    clé *absente* au premier appel de `load_settings()`.
    """
    for cle in CLES_ACCES_API:
        os.environ[cle] = ""


def _fige_espace() -> None:
    """Fige l'espace de données à `commun` (#1164) — voir l'en-tête du module.

    À l'import et non dans une fixture : la file Celery (`maestro.queue.celery_app`)
    nomme sa file à l'import de son module, donc avant la première fixture.
    """
    os.environ[CLE_ESPACE] = ESPACE_DES_TESTS


# Posés à l'import du conftest, donc avant l'import du premier module de test :
# un test qui appelle `load_settings()` dès son import voit déjà l'environnement
# neutralisé (la config relit `os.environ` à chaque appel, rien n'est mis en cache).
_neutralise_langfuse()
_neutralise_couleur_orchestrate()
_neutralise_forge()
_neutralise_reprise_agents()
_neutralise_acces_api()
_fige_espace()


@pytest.fixture(autouse=True)
def _reglages_du_poste_isoles(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Coupe le dépôt des **réglages du poste** de celui du dépôt Git (#1022).

    `ReglagesProjetsStore.default()` retombe sur `core/reglages/` du dépôt, que
    la Control Tower du poste remplit dès qu'on ouvre l'écran Projets. Un
    répertoire des projets réglé à la main y ferait apparaître un **point
    d'entrée de plus** dans l'explorateur : le verdict de la suite dépendrait
    alors du poste, ce que docs/10 §8.7 interdit. Le dépôt est donc pointé sur
    un dossier jetable — vide, donc « aucun réglage posé », donc le défaut.

    Posée sur la variable d'environnement et non par injection : elle protège
    aussi les suites qui construisent un `ServiceProjets` sans passer de dépôt
    de réglages, c'est-à-dire toutes celles qui existaient avant ce réglage.
    """
    ancienne = os.environ.get(CLE_REGLAGES_DIR)
    os.environ[CLE_REGLAGES_DIR] = str(tmp_path_factory.mktemp("reglages"))
    try:
        yield
    finally:
        # `pop` et non `del` : un test a le droit de retirer la variable pour
        # mesurer le repli sur `core/reglages/`, et la remise en état ne doit
        # pas se casser sur ce qu'elle protège.
        if ancienne is None:
            os.environ.pop(CLE_REGLAGES_DIR, None)
        else:
            os.environ[CLE_REGLAGES_DIR] = ancienne


@pytest.fixture(autouse=True)
def _jeton_api_isole(
    tmp_path_factory: pytest.TempPathFactory, request: pytest.FixtureRequest
) -> Iterator[None]:
    """Coupe le jeton de l'API du `~/.maestro/` du poste (#638).

    `jeton_local()` engendre un secret au premier appel et le persiste : sans
    cette isolation, la suite écrirait dans le dossier personnel de qui la joue
    — et, pire, un test pourrait passer parce que le poste a déjà un jeton là où
    la CI n'en a aucun. Le fichier pointé n'existe pas au départ : chaque test
    part donc du cas « premier démarrage », celui du critère, et aucun jeton
    n'est hérité du test précédent.

    **Un dossier pour la session, un nom de fichier par test** (l'empreinte du
    nodeid), plutôt qu'un `mktemp` par test : la quasi-totalité des tests ne
    résout jamais de jeton et n'écrit donc rien du tout — leur créer à chacun un
    dossier serait trois mille répertoires vides pour une variable
    d'environnement.
    """
    ancienne = os.environ.get(CLE_JETON_FICHIER)
    racine = tmp_path_factory.getbasetemp() / "jetons-api"
    racine.mkdir(exist_ok=True)
    empreinte = hashlib.sha1(request.node.nodeid.encode("utf-8")).hexdigest()[:16]
    os.environ[CLE_JETON_FICHIER] = str(racine / empreinte)
    try:
        yield
    finally:
        if ancienne is None:
            os.environ.pop(CLE_JETON_FICHIER, None)
        else:
            os.environ[CLE_JETON_FICHIER] = ancienne


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


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]):
    """Retient de quoi la phase d'appel a échoué — pour la garde du fournisseur (#782).

    La garde échoue un test à sa sortie quand une résolution a été tentée ; si le test a
    déjà échoué **de l'exception même qu'elle a levée**, la cause est nommée et la redire
    en ferait un second échec pour le même fait. Elle a donc besoin de savoir de quoi la
    phase d'appel s'est soldée, ce qu'une fixture ne voit pas : c'est ce que ce hook lui
    dépose, et rien d'autre.
    """
    rapport = yield
    if call.when == "call":
        item.stash[_ECHEC_APPEL] = call.excinfo.type if call.excinfo is not None else None
    return rapport


@pytest.fixture(autouse=True)
def _pas_de_fournisseur_du_poste(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    """Refuse au test la résolution du fournisseur configuré sur le poste, et le dit (#782).

    Ce qui est remplacé est la lecture des réglages du poste **par la fabrique**
    (`maestro.providers.factory.load_settings`), c'est-à-dire exactement ce que
    `provider_from_settings()` fait quand on ne lui passe rien — et rien d'autre :
    `provider_from_settings(settings)` avec des réglages construits par le test passe, un
    `provider_from_settings` remplacé par un double n'arrive jamais ici, et le marqueur
    `fournisseur_du_poste` efface la garde pour le test qui veut vraiment lire le poste.

    Deux gestes, parce que les trois répondeurs de `create_app` **avalent** un échec de
    résolution en réponse de repli (201, « réglage absent ») : lever ne suffit pas sur le
    chemin même qu'on protège. La résolution tentée est donc **retenue**, et le test qui
    l'a faite est échoué à sa sortie — même forme que la garde Langfuse — sauf s'il a déjà
    échoué de `FournisseurDuPosteRefuse` elle-même, la cause étant alors sous les yeux.

    Le `monkeypatch` du test est celui utilisé ici, à dessein : un test qui remplace lui
    aussi la fabrique empile sa substitution sur celle-ci dans **une seule** pile de
    restauration, donc dans le bon ordre quel que soit celui des fixtures — deux piles
    laisseraient la garde en place après le test, liée à un test fini. Portée nommée :
    celle d'un test — ce qu'une fixture de module ou de session fait à son **montage**
    précède la garde ; y résoudre un fournisseur serait y appeler un modèle, ce qui ne se
    fait pas, et rien ici ne le refuserait.
    """
    if request.node.get_closest_marker(MARQUEUR_FOURNISSEUR_DU_POSTE) is not None:
        yield
        return

    tentatives: list[str] = []

    def refuse_le_poste() -> Settings:
        tentatives.append(request.node.nodeid)
        raise FournisseurDuPosteRefuse(CAUSE_FOURNISSEUR_DU_POSTE)

    # Import différé par le chemin en chaîne : la fabrique tire le SDK, payé une fois par
    # processus — et jamais avant la neutralisation de l'environnement (en tête de module).
    monkeypatch.setattr("maestro.providers.factory.load_settings", refuse_le_poste)

    yield

    if not tentatives:
        return
    if request.node.stash.get(_ECHEC_APPEL, None) is FournisseurDuPosteRefuse:
        return
    pytest.fail(
        f"{len(tentatives)} résolution(s) du fournisseur du poste pendant ce test — "
        f"{CAUSE_FOURNISSEUR_DU_POSTE}",
        pytrace=False,
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Saute les tests `cli_reel` sauf demande explicite (#1304) — et dit comment les demander.

    Un saut, pas une sélection : le test reste dans le compte rendu, avec sa raison, là où un
    `-m "not cli_reel"` le ferait disparaître. C'est la même exigence que la garde de git en CI
    (#333) dans l'autre sens : ce qui ne joue pas doit se voir.
    """
    if os.environ.get(CLE_CLI_REEL) == "1":
        return
    saut = pytest.mark.skip(
        reason=f"lance le vrai CLI et appelle un vrai modèle (#1304) : `{CLE_CLI_REEL}=1` le joue"
    )
    for item in items:
        if item.get_closest_marker(MARQUEUR_CLI_REEL) is not None:
            item.add_marker(saut)


@pytest.fixture(autouse=True)
def _pas_de_commande_du_projet_jouee(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retire l'interpréteur des commandes du projet, sauf au test qui le demande (#1160).

    Ce qui est remplacé est `maestro.sandbox.verification.interprete`, que le
    `Verificateur` relit **à chaque appel** : c'est la seule porte par laquelle une
    génération joue une commande sans qu'on lui ait passé de `joueur`. Sans bash, la
    vérification rend « à vérifier » pour chaque commande, sans copier ni jouer quoi
    que ce soit — un chemin réel du produit, et le même sur tous les postes. Un test
    qui passe au `Verificateur` son propre `interprete` n'est pas concerné.
    """
    if request.node.get_closest_marker(MARQUEUR_COMMANDES_JOUEES) is not None:
        return
    monkeypatch.setattr("maestro.sandbox.verification.interprete", lambda: None)


@pytest.fixture(autouse=True)
def _clients_du_poste_fermes(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un poste nu pour les clients d'agents, sauf au test qui les demande (#1295).

    Ce qui est remplacé est `maestro.clients_du_poste.resoudre`, que `detecter` relit **à
    chaque appel** : aucun client trouvé, donc aucune version lue, aucun processus lancé — un
    vrai chemin du produit, celui d'un poste sans client, et le même partout. Un test qui passe
    à `detecter` son propre `resolveur`, ou au service son propre détecteur, n'est pas concerné.
    """
    if request.node.get_closest_marker(MARQUEUR_CLIENTS_DU_POSTE) is not None:
        return
    monkeypatch.setattr("maestro.clients_du_poste.resoudre", lambda commande: None)

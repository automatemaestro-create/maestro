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
"""

import logging
import os

import pytest

#: Les deux clés dont la présence suffit à poser l'exporteur (`activer_export_langfuse`).
_CLES_LANGFUSE = ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")

#: Hôte imposé aux tests : port *discard* sur la boucle locale, où rien n'écoute.
#: Rien ne devrait construire de publieur depuis l'environnement — si quelque
#: chose le fait, il échoue immédiatement en local au lieu de sortir sur Internet.
HOTE_LANGFUSE_NEUTRE = "http://127.0.0.1:9"

#: La variable qui force les couleurs de `scripts/orchestrate/run.sh` hors console (#236).
CLE_COULEUR_ORCHESTRATE = "MAESTRO_ORCHESTRATE_COULEUR"


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


# Posés à l'import du conftest, donc avant l'import du premier module de test :
# un test qui appelle `load_settings()` dès son import voit déjà l'environnement
# neutralisé (la config relit `os.environ` à chaque appel, rien n'est mis en cache).
_neutralise_langfuse()
_neutralise_couleur_orchestrate()


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

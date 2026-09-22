"""Le mode local durci de l'API : jeton et origines (ticket #638).

Ce que le lot livre sans tests différés (#645), parce que sa logique est
critique : le **refus `401`** et le **filtrage d'origine**. Le reste du parent
#637 garde sa couverture différée.

Aucun réseau, aucun Redis : l'app tourne sur le bus mémoire via le `TestClient`
de Starlette, et le WebSocket est celui de la production. Le jeton du poste est
isolé dans un fichier jetable par `tests/conftest.py`, et les quatre variables
d'environnement du régime y sont vidées : le verdict ne dépend donc ni du
`~/.maestro/` de la machine ni des ports du worktree qui joue la suite.

Trois familles :

① **le jeton** — engendré au premier démarrage, persisté hors du dépôt,
   idempotent, imposable par réglage ; l'API refuse en `401` toute requête qui
   ne le porte pas, WebSocket compris, et l'accepte quand il est là ;
② **les origines** — plus de `*` par défaut : la liste est un réglage, dont le
   défaut local est l'origine du front (donc le port du front), et le mode
   serveur passe par la même variable ;
③ **le régime** — durci sans rien dire, ouvert quand on le nomme, erreur franche
   sur une valeur inconnue, et annoncé au démarrage de `maestro-api`, qui rend
   aussi le jeton au lanceur (`--jeton`).
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from maestro.config import ConfigError, Settings, load_settings
from maestro.controltower.acces import (
    PORT_UI_DEFAUT,
    REGIME_JETON,
    REGIME_OUVERT,
    PolitiqueAcces,
    chemin_du_jeton,
    entetes_client,
    jeton_local,
    origines_locales,
    origines_reglees,
    politique_depuis,
)
from maestro.controltower.app import create_app
from maestro.controltower.cli import main as cli

#: Une origine qui n'est pas celle du front — une page tierce ouverte dans le
#: même navigateur, c'est-à-dire la menace que la liste d'origines traite.
ORIGINE_TIERCE = "https://page-tierce.example"


def _reglages(**surcharges: str | None) -> Settings:
    """Les réglages du processus de test, avec les seules clés qu'on veut bouger."""
    return replace(load_settings(), **surcharges)


@pytest.fixture()
def politique() -> PolitiqueAcces:
    """La politique durcie d'un poste : le jeton engendré, l'origine du front."""
    return politique_depuis(_reglages(api_auth=REGIME_JETON))


@pytest.fixture()
def client_durci(politique: PolitiqueAcces):
    """L'app servie comme en production : portier armé, origines limitées."""
    with TestClient(create_app(acces=politique)) as client:
        yield client


def _entete(politique: PolitiqueAcces) -> dict[str, str]:
    """L'en-tête que porte un appelant en règle."""
    return {"Authorization": f"Bearer {politique.jeton}"}


# --- ① Le jeton : engendré, persisté hors du dépôt, idempotent ----------------


def test_le_jeton_est_engendre_au_premier_appel_et_persiste():
    chemin = chemin_du_jeton()
    assert not chemin.exists(), "la suite doit partir d'un poste sans jeton"

    jeton = jeton_local()

    assert jeton, "un jeton vide n'authentifierait rien"
    assert chemin.read_text(encoding="utf-8").strip() == jeton


def test_le_jeton_ne_change_pas_au_demarrage_suivant():
    # Idempotence : c'est elle qui permet à l'API et aux outils du poste de
    # s'accorder sans se parler — ils lisent le même fichier.
    premier = jeton_local()
    assert jeton_local() == premier


def test_le_jeton_est_persiste_hors_du_depot(tmp_path: Path):
    # Le défaut est `~/.maestro/jeton-api`, jamais un dossier du dépôt : un
    # secret n'a rien à faire dans un arbre Git. On le mesure sur les réglages
    # NUS (fichier non nommé), pas sur ceux que la suite isole.
    depot = Path(__file__).resolve().parents[1]
    defaut = chemin_du_jeton(_reglages(api_jeton_fichier=None))

    assert depot not in defaut.parents
    assert defaut.parent.name == ".maestro"
    assert defaut.name == "jeton-api"


def test_un_jeton_impose_court_circuite_le_fichier():
    # La porte du mode serveur : un secret injecté par le déploiement, et aucun
    # fichier écrit sur le poste.
    reglages = _reglages(api_jeton="jeton-du-deploiement")

    assert jeton_local(reglages) == "jeton-du-deploiement"
    assert not chemin_du_jeton(reglages).exists()


def test_deux_engendrements_concurrents_rendent_le_meme_jeton(tmp_path: Path):
    # La course de deux process qui démarrent ensemble, jouée là où elle se
    # décide : `_engendrer` appelé deux fois sur le même fichier. Le perdant
    # relit ce que le gagnant a écrit — écraser reviendrait à invalider un jeton
    # peut-être déjà distribué au front.
    from maestro.controltower.acces import _engendrer

    chemin = tmp_path / "course" / "jeton-api"
    gagnant = _engendrer(chemin)

    assert _engendrer(chemin) == gagnant
    assert chemin.read_text(encoding="utf-8").strip() == gagnant


# --- ① Le refus 401, sur toutes les portes -----------------------------------


def test_sans_jeton_l_api_refuse_en_401(client_durci):
    reponse = client_durci.get("/api/sante")

    assert reponse.status_code == 401
    assert "jeton" in reponse.json()["detail"]
    assert reponse.headers["www-authenticate"] == "Bearer"


def test_avec_le_jeton_l_api_repond(client_durci, politique):
    reponse = client_durci.get("/api/sante", headers=_entete(politique))

    assert reponse.status_code == 200
    assert reponse.json()["statut"] == "ok"


def test_un_mauvais_jeton_est_refuse(client_durci):
    reponse = client_durci.get(
        "/api/sante", headers={"Authorization": "Bearer pas-le-bon"}
    )

    assert reponse.status_code == 401


def test_un_jeton_sans_le_schema_bearer_est_refuse(client_durci, politique):
    reponse = client_durci.get(
        "/api/sante", headers={"Authorization": str(politique.jeton)}
    )

    assert reponse.status_code == 401


def test_le_refus_couvre_les_ecritures_pas_seulement_les_lectures(client_durci):
    # C'est l'écriture qui touche au disque de l'utilisateur : une garde qui ne
    # couvrirait que les lectures ne fermerait rien de ce que #638 vise.
    reponse = client_durci.post(
        "/api/executions", json={"objectif": "écrire chez l'utilisateur"}
    )

    assert reponse.status_code == 401


def test_le_jeton_ne_passe_pas_en_parametre_d_url_sur_le_rest(client_durci, politique):
    # Réservé au WebSocket, qui n'admet aucun en-tête : l'admettre en REST ferait
    # voyager le secret dans les journaux d'accès et les historiques.
    reponse = client_durci.get(f"/api/sante?jeton={politique.jeton}")

    assert reponse.status_code == 401


def test_le_websocket_sans_jeton_est_refuse(client_durci):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client_durci.websocket_connect("/ws/evenements?projet=tous") as socket:
            socket.receive_json()


def test_le_websocket_avec_le_jeton_en_parametre_est_accepte(client_durci, politique):
    # Un navigateur ne pose aucun en-tête sur une poignée de main WebSocket :
    # sans ce chemin, le flux temps réel serait injoignable depuis le front.
    with client_durci.websocket_connect(
        f"/ws/evenements?projet=tous&jeton={politique.jeton}"
    ):
        pass


def test_le_websocket_avec_un_mauvais_jeton_est_refuse(client_durci):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client_durci.websocket_connect(
            "/ws/evenements?projet=tous&jeton=pas-le-bon"
        ) as socket:
            socket.receive_json()


def test_le_websocket_d_une_origine_tierce_est_refuse(client_durci, politique):
    # Le jeton fermerait déjà la porte, mais une page qui l'aurait appris n'a
    # toujours rien à faire sur le flux : CORS ne couvre pas le WebSocket, donc
    # l'origine se juge ici.
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client_durci.websocket_connect(
            f"/ws/evenements?projet=tous&jeton={politique.jeton}",
            headers={"origin": ORIGINE_TIERCE},
        ) as socket:
            socket.receive_json()


def test_le_websocket_de_l_origine_du_front_passe(politique):
    origine = politique.origines[0]
    with TestClient(create_app(acces=politique)) as client:
        with client.websocket_connect(
            f"/ws/evenements?projet=tous&jeton={politique.jeton}",
            headers={"origin": origine},
        ):
            pass


# --- ② Les origines : un réglage, jamais `*` par défaut ----------------------


def test_le_defaut_des_origines_est_l_origine_du_front():
    origines = origines_reglees(_reglages(api_origines=None, port_ui=None))

    assert origines == origines_locales(PORT_UI_DEFAUT)
    assert "*" not in origines


def test_le_defaut_suit_le_port_du_front_de_la_copie():
    # Chaque copie de travail sert son front sur ses propres ports (#847) : sans
    # cette dérivation, une copie s'autoriserait la stack d'une autre.
    origines = origines_reglees(_reglages(api_origines=None, port_ui="3017"))

    assert origines == ("http://localhost:3017", "http://127.0.0.1:3017")


def test_les_origines_se_reglent_et_le_mode_serveur_passe_par_la_meme_cle():
    origines = origines_reglees(
        _reglages(api_origines="https://maestro.example, https://maestro.example/ ")
    )

    # Rognées, dédoublonnées, et la barre finale n'en fait pas deux origines.
    assert origines == ("https://maestro.example",)


def test_une_etoile_reglee_rouvre_tout_et_reste_une_decision_ecrite():
    assert origines_reglees(_reglages(api_origines="*")) == ("*",)


def test_un_reglage_qui_ne_porte_aucune_origine_vaut_absent():
    assert origines_reglees(_reglages(api_origines=" , ")) == origines_reglees(
        _reglages(api_origines=None)
    )


def test_une_origine_tierce_ne_recoit_aucun_en_tete_cors(client_durci, politique):
    reponse = client_durci.get(
        "/api/sante",
        headers={**_entete(politique), "Origin": ORIGINE_TIERCE},
    )

    # Le serveur répond (le jeton est bon), mais le navigateur ne laissera pas
    # la page tierce lire la réponse : aucun `Access-Control-Allow-Origin`.
    assert reponse.status_code == 200
    assert "access-control-allow-origin" not in reponse.headers


def test_l_origine_du_front_recoit_son_en_tete_cors(client_durci, politique):
    origine = politique.origines[0]
    reponse = client_durci.get(
        "/api/sante", headers={**_entete(politique), "Origin": origine}
    )

    assert reponse.headers["access-control-allow-origin"] == origine


def test_le_preflight_du_front_passe_sans_jeton(client_durci, politique):
    # Un navigateur ne porte pas d'`Authorization` sur un préflight : le refuser
    # rendrait toute requête du front impossible. CORS est monté au-dessus du
    # portier précisément pour cela.
    reponse = client_durci.options(
        "/api/sante",
        headers={
            "Origin": politique.origines[0],
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )

    assert reponse.status_code == 200
    assert reponse.headers["access-control-allow-origin"] == politique.origines[0]


def test_la_politique_ouverte_garde_la_suite_de_tests_en_marche():
    # `create_app()` sans politique est la configuration des 170 suites qui
    # montent l'app : aucune ne connaît de jeton, et aucune n'a à en connaître.
    with TestClient(create_app()) as client:
        assert client.get("/api/sante").status_code == 200


# --- ③ Le régime : durci sans rien dire, ouvert quand on le nomme -------------


def test_le_regime_par_defaut_est_durci():
    politique = politique_depuis(_reglages(api_auth=None))

    assert politique.durci
    assert politique.regime == REGIME_JETON


def test_le_regime_ouvert_se_nomme_et_n_exige_plus_rien():
    politique = politique_depuis(_reglages(api_auth=REGIME_OUVERT))

    assert not politique.durci
    with TestClient(create_app(acces=politique)) as client:
        assert client.get("/api/sante").status_code == 200


def test_le_regime_ouvert_garde_des_origines_nommees():
    # Deux gardes distinctes : rouvrir l'authentification ne rouvre pas les
    # origines — `MAESTRO_API_ORIGINES` reste la seule clé qui les décide.
    politique = politique_depuis(_reglages(api_auth=REGIME_OUVERT))

    assert politique.origines == origines_locales(PORT_UI_DEFAUT)


def test_un_regime_inconnu_est_une_erreur_franche():
    with pytest.raises(ConfigError) as refus:
        politique_depuis(_reglages(api_auth="jetton"))

    assert "MAESTRO_API_AUTH" in str(refus.value)


def test_l_annonce_dit_le_regime_et_ne_dit_pas_le_secret(politique):
    annonce = politique.annonce()

    assert REGIME_JETON in annonce
    assert str(politique.jeton) not in annonce


def test_l_annonce_du_regime_ouvert_le_dit_en_toutes_lettres():
    assert REGIME_OUVERT in PolitiqueAcces().annonce()


def test_maestro_api_jeton_rend_le_jeton_et_annonce_le_regime(monkeypatch, capsys):
    # Ce que `scripts/controltower/start.sh` lit : le jeton sur la sortie
    # standard (pour le front), le régime sur l'erreur (pour la personne).
    # Porté par le CLI et non par `acces` : ce module-là est déjà importé par le
    # paquet, et l'exécuter en `-m` ferait précéder le jeton d'un RuntimeWarning.
    monkeypatch.setenv("MAESTRO_API_AUTH", REGIME_JETON)

    code = cli(["--jeton"])

    rendu = capsys.readouterr()
    assert code == 0
    assert rendu.out.strip() == jeton_local()
    assert REGIME_JETON in rendu.err


def test_maestro_api_jeton_ne_rend_rien_en_regime_ouvert(monkeypatch, capsys):
    monkeypatch.setenv("MAESTRO_API_AUTH", REGIME_OUVERT)

    code = cli(["--jeton"])

    # `3` : « il n'y en a pas » n'est pas une panne, et le lanceur le sait.
    assert code == 3
    assert capsys.readouterr().out == ""


# --- Les outils du poste portent le même jeton -------------------------------


def test_les_outils_locaux_posent_l_en_tete_du_meme_jeton(monkeypatch):
    # Le banc, la purge, les captures et la relecture visuelle frappent la vraie
    # API : sans en-tête commun, chacun réécrirait le sien — et un seul oubli
    # rendrait « l'API ne répond pas » là où elle refuse.
    monkeypatch.setenv("MAESTRO_API_AUTH", REGIME_JETON)

    assert entetes_client() == {"Authorization": f"Bearer {jeton_local()}"}


def test_les_outils_locaux_ne_posent_rien_en_regime_ouvert(monkeypatch):
    monkeypatch.setenv("MAESTRO_API_AUTH", REGIME_OUVERT)

    assert entetes_client() == {}


def test_l_en_tete_des_outils_ouvre_bien_l_api_durcie(politique):
    entetes = entetes_client(_reglages(api_auth=REGIME_JETON))
    with TestClient(create_app(acces=politique)) as client:
        assert client.get("/api/sante", headers=entetes).status_code == 200


def test_le_401_reste_lisible_par_le_front(client_durci):
    # Le front lit le `detail` d'un refus (`motifDe`) : un corps non JSON ferait
    # tomber la bannière sur un message générique.
    reponse = client_durci.get("/api/sante")

    assert json.loads(reponse.text)["detail"]
    assert reponse.headers["content-type"].startswith("application/json")

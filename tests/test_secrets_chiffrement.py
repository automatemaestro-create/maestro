"""Chiffrement au repos du coffre + 3 parcours d'auth MCP (ticket #132, parent #129).

Aucun appel réseau : coffres sur répertoires temporaires, clés de test. Les tests
**exhaustifs** de la config MCP depuis la Control Tower sont différés au lot 5/5
(#134) ; ce fichier couvre la **logique critique** de #132, qui ne doit pas
partir sans filet :

① **chiffrement au repos** : `enregistrer` chiffre (le fichier ne contient
   aucune valeur en clair), `lire` déchiffre — round-trip via clé injectée,
   clé locale `.cle` auto-générée, et `MAESTRO_SECRETS_KEY` ;
② **trois modes d'auth** (docs/21 §3.2) : token statique chiffré & masqué,
   appairage éphémère non secret (ni chiffré ni masqué), token OAuth importé
   expirable (expiré → omis de la résolution → serveur refusé au montage) ;
③ **état & renouvellement** : `etat` expose la validité sans déchiffrer,
   `renouveler` ré-importe un token expiré et le serveur remonte ;
④ **rétro-compat #109** : un coffre hérité `{"VAR": "clair"}` reste lu et masqué ;
⑤ **validation à la lecture/écriture** : formes douteuses refusées avec leur cause.
"""

import json
from datetime import UTC, datetime, timedelta

import pytest

from maestro.agents.chiffrement import Chiffreur
from maestro.agents.mcp import ServeurMcp, resolus
from maestro.agents.secrets import SecretStore
from maestro.config import Settings
from maestro.providers.base import McpServerUnavailable
from maestro.telemetry import MARQUEUR_SECRET, redact_secrets
from maestro.telemetry import redact as redact_mod

SECRET = "glpat-tok-chiffre-1234567890"


@pytest.fixture(autouse=True)
def registre_vierge(monkeypatch):
    """Isole le registre des secrets servis : il vit sinon pour tout le process."""
    monkeypatch.setattr(redact_mod, "_SECRETS_SERVIS", set())


@pytest.fixture()
def cle():
    """Une clé Fernet de test, stable pour un test donné."""
    return Chiffreur.generer_cle()


@pytest.fixture()
def store(tmp_path, cle):
    """Coffre vierge avec chiffreur injecté (clé de `MAESTRO_SECRETS_KEY` en vrai)."""
    return SecretStore(tmp_path / "secrets", chiffreur=Chiffreur(cle))


def _lu_brut(store: SecretStore, agent: str) -> str:
    """Le contenu texte du fichier de coffre de `agent` (pour vérifier le chiffré)."""
    return (store.racine / f"{agent}.json").read_text(encoding="utf-8")


# --- ① Chiffrement au repos -------------------------------------------------------------


def test_enregistrer_chiffre_et_lire_dechiffre(store):
    etat = store.enregistrer("qa", "GITLAB_TOKEN", SECRET, mode_auth="token_statique")

    brut = _lu_brut(store, "qa")
    assert SECRET not in brut  # jamais en clair sur disque
    assert '"chiffre"' in brut
    assert store.lire("qa") == {"GITLAB_TOKEN": SECRET}
    assert etat.cle == "GITLAB_TOKEN" and etat.secret and etat.valide


def test_cle_locale_auto_generee_et_relue(tmp_path):
    # Sans chiffreur injecté ni MAESTRO_SECRETS_KEY : le coffre gère `.cle`.
    ecrivain = SecretStore(tmp_path / "s")
    ecrivain.enregistrer("qa", "TOK", SECRET, mode_auth="token_statique")

    assert (tmp_path / "s" / ".cle").is_file()
    # Une instance neuve relit la même clé locale et déchiffre.
    assert SecretStore(tmp_path / "s").lire("qa") == {"TOK": SECRET}
    # `.cle` n'est pas un coffre : il ne provisionne pas à lui seul (mais qa.json oui).
    assert SecretStore(tmp_path / "s").provisionne


def test_mauvaise_cle_refuse_le_dechiffrement(tmp_path, cle):
    SecretStore(tmp_path / "s", chiffreur=Chiffreur(cle)).enregistrer(
        "qa", "TOK", SECRET, mode_auth="token_statique"
    )
    autre = SecretStore(tmp_path / "s", chiffreur=Chiffreur(Chiffreur.generer_cle()))

    with pytest.raises(ValueError, match="déchiffrement impossible"):
        autre.lire("qa")


def test_coffre_chiffre_sans_cle_refuse(tmp_path):
    ecrivain = SecretStore(tmp_path / "s")
    ecrivain.enregistrer("qa", "TOK", SECRET, mode_auth="token_statique")
    (tmp_path / "s" / ".cle").unlink()  # la clé locale disparaît

    with pytest.raises(ValueError, match="clé de chiffrement absente"):
        SecretStore(tmp_path / "s").lire("qa")


def test_cle_maitresse_via_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("MAESTRO_SECRETS_DIR", str(tmp_path / "coffre"))
    monkeypatch.setenv("MAESTRO_SECRETS_KEY", Chiffreur.generer_cle().decode())
    store = SecretStore.default(Settings.from_env())

    store.enregistrer("qa", "TOK", SECRET, mode_auth="token_statique")

    assert store.lire("qa") == {"TOK": SECRET}
    # Clé fournie par l'environnement : pas de fichier de clé local.
    assert not (tmp_path / "coffre" / ".cle").exists()


# --- ② Trois modes d'auth ---------------------------------------------------------------


def test_token_statique_masque_en_sortie(store):
    store.enregistrer("qa", "GITLAB_TOKEN", SECRET, mode_auth="token_statique")
    store.lire("qa")  # sert la valeur → registre de rédaction

    assert redact_secrets(f"fuite {SECRET}") == f"fuite {MARQUEUR_SECRET}"


def test_appairage_non_secret_ni_chiffre_ni_masque(store):
    canal = "figma-canal-ephemere-42"
    etat = store.enregistrer(
        "designer", "FIGMA_CHANNEL", canal, mode_auth="appairage", secret=False
    )

    # Non secret : stocké en clair (c'est une valeur jetable, pas un secret)…
    assert canal in _lu_brut(store, "designer")
    assert store.lire("designer") == {"FIGMA_CHANNEL": canal}
    assert etat.ephemere and not etat.secret
    # …et jamais enregistré au registre de rédaction : il reste lisible en sortie.
    store.lire("designer")
    assert redact_secrets(f"canal {canal}") == f"canal {canal}"


def test_oauth_expire_est_omis_de_la_resolution(store):
    hier = datetime.now(UTC) - timedelta(hours=1)
    store.enregistrer(
        "designer", "FIGMA_OAUTH_TOKEN", "oauth-token-secret-1", mode_auth="oauth_importe",
        expire_le=hier,
    )

    # Expiré : absent de lire → la résolution du serveur échoue proprement.
    assert store.lire("designer") == {}
    serveur = ServeurMcp(
        nom="figma", type="http", url="https://mcp.figma.com/mcp",
        headers={"Authorization": "Bearer ${FIGMA_OAUTH_TOKEN}"},
    )
    with pytest.raises(McpServerUnavailable, match="figma.*FIGMA_OAUTH_TOKEN"):
        resolus([serveur], store.environ("designer"))


def test_oauth_expire_sur_serveur_optionnel_est_omis_sans_echec(store):
    hier = datetime.now(UTC) - timedelta(hours=1)
    store.enregistrer(
        "designer", "FIGMA_OAUTH_TOKEN", "oauth-token-secret-2", mode_auth="oauth_importe",
        expire_le=hier,
    )
    serveur = ServeurMcp(
        nom="figma", type="http", url="https://mcp.figma.com/mcp",
        headers={"Authorization": "Bearer ${FIGMA_OAUTH_TOKEN}"}, optionnel=True,
    )

    assert resolus([serveur], store.environ("designer")) == ()  # omis, pas d'échec


def test_oauth_valide_se_resout(store):
    demain = datetime.now(UTC) + timedelta(hours=1)
    store.enregistrer(
        "designer", "FIGMA_OAUTH_TOKEN", "oauth-token-secret-3", mode_auth="oauth_importe",
        expire_le=demain,
    )

    assert store.lire("designer") == {"FIGMA_OAUTH_TOKEN": "oauth-token-secret-3"}


# --- ③ État & renouvellement ------------------------------------------------------------


def test_etat_expose_la_validite_sans_dechiffrer(store):
    hier = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    store.enregistrer(
        "designer", "FIGMA_OAUTH_TOKEN", "tok", mode_auth="oauth_importe", expire_le=hier
    )

    (etat,) = store.etat("designer")
    assert etat.mode_auth == "oauth_importe"
    assert etat.valide is False
    assert etat.expire_le == hier


def test_renouveler_remonte_le_serveur(store):
    hier = datetime.now(UTC) - timedelta(hours=1)
    store.enregistrer(
        "designer", "FIGMA_OAUTH_TOKEN", "vieux-tok-secret", mode_auth="oauth_importe",
        expire_le=hier,
    )
    assert store.lire("designer") == {}  # expiré

    demain = datetime.now(UTC) + timedelta(days=1)
    etat = store.renouveler("designer", "FIGMA_OAUTH_TOKEN", "neuf-tok-secret", demain)

    assert etat.valide is True
    assert store.lire("designer") == {"FIGMA_OAUTH_TOKEN": "neuf-tok-secret"}


def test_supprimer(store):
    store.enregistrer("qa", "TOK", SECRET, mode_auth="token_statique")

    assert store.supprimer("qa", "TOK") is True
    assert store.lire("qa") == {}
    assert store.supprimer("qa", "ABSENT") is False


def test_enregistrer_preserve_les_autres_entrees(store):
    store.enregistrer("qa", "A", "valeur-a-secrete", mode_auth="token_statique")
    store.enregistrer("qa", "B", "valeur-b-secrete", mode_auth="token_statique")

    assert store.lire("qa") == {"A": "valeur-a-secrete", "B": "valeur-b-secrete"}


# --- ④ Rétro-compat #109 (format hérité en clair) ---------------------------------------


def test_coffre_herite_en_clair_reste_lu_et_masque(store):
    store.racine.mkdir(parents=True)
    (store.racine / "qa.json").write_text(
        json.dumps({"secrets": {"GITLAB_TOKEN": SECRET}}), encoding="utf-8"
    )

    assert store.lire("qa") == {"GITLAB_TOKEN": SECRET}
    assert redact_secrets(f"fuite {SECRET}") == f"fuite {MARQUEUR_SECRET}"


def test_herite_et_structure_cohabitent(store):
    # Un ancien secret en clair posé à la main, plus un nouveau chiffré via l'API.
    store.enregistrer("qa", "NOUVEAU", "nouveau-secret-chiffre", mode_auth="token_statique")
    brut = json.loads(_lu_brut(store, "qa"))
    brut["secrets"]["ANCIEN"] = "ancien-secret-clair"
    (store.racine / "qa.json").write_text(json.dumps(brut), encoding="utf-8")

    assert store.lire("qa") == {
        "NOUVEAU": "nouveau-secret-chiffre",
        "ANCIEN": "ancien-secret-clair",
    }


# --- ⑤ Validation à la lecture / écriture -----------------------------------------------


def test_mode_auth_invalide_refuse(store):
    with pytest.raises(ValueError, match="mode d'auth invalide"):
        store.enregistrer("qa", "TOK", SECRET, mode_auth="magique")


def test_nom_de_variable_invalide_refuse(store):
    with pytest.raises(ValueError, match="nom de variable invalide"):
        store.enregistrer("qa", "pas une variable", SECRET, mode_auth="token_statique")


def test_echeance_invalide_refusee(store):
    with pytest.raises(ValueError, match="échéance invalide"):
        store.enregistrer(
            "designer", "TOK", "x", mode_auth="oauth_importe", expire_le="pas une date"
        )


def test_entree_secrete_sans_chiffre_refusee(store):
    store.racine.mkdir(parents=True)
    (store.racine / "qa.json").write_text(
        json.dumps({"secrets": {"TOK": {"mode_auth": "token_statique", "secret": True}}}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="sans jeton 'chiffre'"):
        store.lire("qa")


def test_entree_de_forme_inattendue_refusee(store):
    store.racine.mkdir(parents=True)
    (store.racine / "qa.json").write_text(
        json.dumps({"secrets": {"TOK": 42}}), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="doit être une chaîne"):
        store.lire("qa")


def test_cle_maitresse_malformee_refusee():
    with pytest.raises(ValueError, match="clé de chiffrement invalide"):
        Chiffreur("pas-une-cle-fernet")

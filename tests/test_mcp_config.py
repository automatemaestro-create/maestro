"""Config MCP depuis la Control Tower — le parcours de bout en bout (lot 5/5, #134, parent #129).

Les lots 1-3 sont couverts **chacun en isolation** :
[`test_mcp_pool.py`](./test_mcp_pool.py) (pool ∩ activation, #130),
[`test_mcp_registry.py`](./test_mcp_registry.py) (registre + recherche + garde-fou, #131) et
[`test_secrets_chiffrement.py`](./test_secrets_chiffrement.py) (secrets chiffrés + 3 parcours,
#132). Ce fichier certifie leur **couture** — l'histoire que le parent #129 promet et
qu'aucun test par lot n'exerce en entier :

    bibliothèque (registre curé) → pool projet → activation par agent
        → coffre chiffré (secret saisi une fois) → composition `lire(agent)`
        → montage `resolus` avec le secret **déchiffré**, en mémoire seulement.

C'est le geste que la Control Tower fera en écriture (lot #133) : instancier un template curé,
le poser au pool, l'activer pour un agent, poser son secret. Ici, le **contrat backend** de ce
geste, sans réseau ni UI ni clé réelle (coffres sur répertoires temporaires, clé Fernet de test).
Le volet UI proprement dit (`ParametresMcp`, endpoints d'écriture) est porté par #133 et testé
avec lui ; la lecture de la composition **par l'API** est dans
[`test_controltower.py`](./test_controltower.py).
"""

from datetime import UTC, datetime, timedelta

import pytest

from maestro.agents.chiffrement import Chiffreur
from maestro.agents.mcp import IntegrationMcp, McpStore, ServeurMcp, resolus
from maestro.agents.mcp_registry import RegistreMcp
from maestro.agents.secrets import SecretStore
from maestro.providers.base import McpServerUnavailable
from maestro.telemetry import MARQUEUR_SECRET, redact_secrets
from maestro.telemetry import redact as redact_mod


@pytest.fixture(autouse=True)
def registre_vierge(monkeypatch):
    """Isole le registre de rédaction : `resolus` y enregistre tout secret servi (process-wide)."""
    monkeypatch.setattr(redact_mod, "_SECRETS_SERVIS", set())


@pytest.fixture()
def registre():
    """La bibliothèque curée du POC (GitLab, Slack, Figma officiel)."""
    return RegistreMcp.curee()


@pytest.fixture()
def mcp_store(tmp_path):
    """Dépôt MCP vierge (ni pool, ni activation, ni fichier hérité)."""
    return McpStore(tmp_path / "mcp")


@pytest.fixture()
def secret_store(tmp_path):
    """Coffre vierge, chiffreur injecté (la clé de `MAESTRO_SECRETS_KEY` en production)."""
    return SecretStore(tmp_path / "secrets", chiffreur=Chiffreur(Chiffreur.generer_cle()))


# --- ① De la bibliothèque au pool -------------------------------------------------------


def test_de_la_bibliotheque_au_pool_via_instanciation(registre, mcp_store):
    # On configure une intégration **depuis la bibliothèque** : recherche puis
    # instanciation du template curé, posé au pool — sans éditer de fichier à la
    # main (l'écriture que fera la Control Tower, #133). Le gabarit `${VAR}`
    # traverse intact : le secret se pose au coffre, pas dans la déclaration.
    # `merge-request` est le tag qui distingue GitLab de GitHub depuis que les
    # deux forges cohabitent au registre (#412) — `issues`, lui, les rend toutes
    # les deux. Le parcours reste joué sur GitLab à dessein : il éprouve une
    # entrée de la **bibliothèque**, pas le défaut du produit (`core/mcp/qa.json`).
    (trouvee,) = registre.rechercher("merge-request")
    assert trouvee.id == "gitlab"

    mcp_store.ecrire_pool([IntegrationMcp(id="gitlab", serveur=registre.instancier("gitlab"))])

    (integ,) = mcp_store.pool()
    assert integ.id == "gitlab"
    assert integ.serveur.env["GITLAB_PERSONAL_ACCESS_TOKEN"] == "${GITLAB_TOKEN}"


def test_un_serveur_hors_registre_ne_peut_pas_entrer_au_pool(registre):
    # Le garde-fou supply-chain (#131, docs/19) barre le parcours de config en
    # amont du pool : instancier est l'unique voie template → liaison, et elle
    # refuse tout id hors allowlist.
    #
    # ⚠ Le motif a perdu le mot « curée » avec #678 : l'allowlist n'est plus
    # seulement le seed, elle contient aussi ce qu'une **admission** y a fait
    # entrer. Ce qui est épinglé ici reste ce qui compte — le refus, et le fait
    # qu'il nomme l'allowlist —, pas l'adjectif devenu faux.
    with pytest.raises(ValueError, match="hors allowlist"):
        registre.instancier("kubernetes-prod")


# --- ② Le parcours complet : configure, compose, monte ----------------------------------


def test_parcours_complet_configure_puis_monte(registre, mcp_store, secret_store):
    # Le bout-en-bout du parent #129 : pool + activation + secret chiffré (saisi
    # une fois), puis `lire` compose pour l'agent et `resolus` monte avec le
    # secret DÉCHIFFRÉ tiré du coffre.
    mcp_store.ecrire_pool([IntegrationMcp(id="gitlab", serveur=registre.instancier("gitlab"))])
    mcp_store.ecrire_activations("qa", ["gitlab"])
    secret_store.enregistrer("qa", "GITLAB_TOKEN", "glpat-secret-1234", mode_auth="token_statique")

    # Forme versionnable : la référence reste en place tant qu'on ne monte pas.
    (compose,) = mcp_store.lire("qa")
    assert compose.env["GITLAB_PERSONAL_ACCESS_TOKEN"] == "${GITLAB_TOKEN}"

    # Forme montable : la valeur effective, résolue depuis le coffre de l'agent.
    (monte,) = resolus(mcp_store.lire("qa"), secret_store.environ("qa"))
    assert monte.env["GITLAB_PERSONAL_ACCESS_TOKEN"] == "glpat-secret-1234"


def test_un_secret_configure_une_fois_sert_deux_agents(registre, mcp_store, secret_store):
    # L'objectif du pool : une intégration déclarée **une fois**, activée pour
    # deux agents. Chacun résout le secret **de son coffre** — le pool ne partage
    # que la déclaration, jamais la valeur (scoping #109).
    mcp_store.ecrire_pool([IntegrationMcp(id="gitlab", serveur=registre.instancier("gitlab"))])
    mcp_store.ecrire_activations("qa", ["gitlab"])
    mcp_store.ecrire_activations("devops", ["gitlab"])
    secret_store.enregistrer("qa", "GITLAB_TOKEN", "glpat-de-qa", mode_auth="token_statique")
    secret_store.enregistrer(
        "devops", "GITLAB_TOKEN", "glpat-de-devops", mode_auth="token_statique"
    )

    (cote_qa,) = resolus(mcp_store.lire("qa"), secret_store.environ("qa"))
    (cote_devops,) = resolus(mcp_store.lire("devops"), secret_store.environ("devops"))

    assert cote_qa.env["GITLAB_PERSONAL_ACCESS_TOKEN"] == "glpat-de-qa"
    assert cote_devops.env["GITLAB_PERSONAL_ACCESS_TOKEN"] == "glpat-de-devops"


def test_activation_sans_secret_refuse_le_montage_de_facon_propre(
    registre, mcp_store, secret_store
):
    # Un agent active l'intégration mais n'a pas (encore) posé son secret : le
    # serveur requis est refusé au montage, cause nommée, avant tout appel modèle
    # — pas un montage à moitié. Le coffre est provisionné (qa a un fichier),
    # donc le scoping est strict : la variable ne « fuit » pas de l'environnement.
    mcp_store.ecrire_pool([IntegrationMcp(id="gitlab", serveur=registre.instancier("gitlab"))])
    mcp_store.ecrire_activations("qa", ["gitlab"])
    mcp_store.ecrire_activations("devops", ["gitlab"])
    secret_store.enregistrer("qa", "GITLAB_TOKEN", "glpat-de-qa", mode_auth="token_statique")

    with pytest.raises(McpServerUnavailable, match="GITLAB_TOKEN"):
        resolus(mcp_store.lire("devops"), secret_store.environ("devops"))


def test_le_secret_monte_est_masque_en_sortie(registre, mcp_store, secret_store):
    # Servir un secret au montage l'enregistre au registre de rédaction : s'il
    # réapparaît dans un journal, une trace ou un livrable, il est masqué.
    mcp_store.ecrire_pool([IntegrationMcp(id="gitlab", serveur=registre.instancier("gitlab"))])
    mcp_store.ecrire_activations("qa", ["gitlab"])
    secret_store.enregistrer("qa", "GITLAB_TOKEN", "glpat-a-masquer-42", mode_auth="token_statique")

    resolus(mcp_store.lire("qa"), secret_store.environ("qa"))
    assert redact_secrets("fuite glpat-a-masquer-42 !") == f"fuite {MARQUEUR_SECRET} !"


# --- ③ Les trois modes d'auth, montés depuis le pool ------------------------------------


def test_les_trois_modes_d_auth_montes_depuis_le_pool(registre, mcp_store, secret_store):
    # Un pool couvrant les trois parcours de docs/21 §3.2, tous activés pour le
    # designer, montés ensemble depuis le coffre : token statique (résolu),
    # appairage éphémère (résolu), OAuth importé **expiré** sur un serveur
    # **optionnel** (omis du montage, sans faire échouer la tâche).
    pont = ServeurMcp(
        nom="figma-pont",
        type="stdio",
        commande="npx",
        args=("-y", "cursor-talk-to-figma-mcp"),
        env={"FIGMA_CHANNEL": "${FIGMA_CHANNEL}"},
    )
    mcp_store.ecrire_pool(
        [
            IntegrationMcp(id="gitlab", serveur=registre.instancier("gitlab")),
            IntegrationMcp(id="figma-pont", serveur=pont),
            IntegrationMcp(id="figma-officiel", serveur=registre.instancier("figma-officiel")),
        ]
    )
    mcp_store.ecrire_activations("designer", ["gitlab", "figma-pont", "figma-officiel"])
    secret_store.enregistrer(
        "designer", "GITLAB_TOKEN", "glpat-designer-1", mode_auth="token_statique"
    )
    secret_store.enregistrer(
        "designer", "FIGMA_CHANNEL", "figma-canal-jetable-77", mode_auth="appairage", secret=False
    )
    hier = datetime.now(UTC) - timedelta(hours=1)
    secret_store.enregistrer(
        "designer", "FIGMA_OAUTH_TOKEN", "oauth-expire", mode_auth="oauth_importe", expire_le=hier
    )

    # `lire` compose les trois intégrations activées…
    assert tuple(s.nom for s in mcp_store.lire("designer")) == (
        "gitlab",
        "figma-pont",
        "figma-officiel",
    )
    # …`resolus` en monte deux et **omet** le serveur officiel (OAuth expiré + optionnel).
    montes = resolus(mcp_store.lire("designer"), secret_store.environ("designer"))
    assert tuple(s.nom for s in montes) == ("gitlab", "figma-pont")
    (gitlab, pont_monte) = montes
    assert gitlab.env["GITLAB_PERSONAL_ACCESS_TOKEN"] == "glpat-designer-1"
    assert pont_monte.env["FIGMA_CHANNEL"] == "figma-canal-jetable-77"


def test_le_renouvellement_du_token_oauth_remonte_le_serveur(registre, mcp_store, secret_store):
    # Le parcours OAuth importé (#132) bout-en-bout depuis le pool : un token
    # expiré fait omettre le serveur officiel (optionnel) ; renouvelé, il remonte.
    mcp_store.ecrire_pool(
        [IntegrationMcp(id="figma-officiel", serveur=registre.instancier("figma-officiel"))]
    )
    mcp_store.ecrire_activations("designer", ["figma-officiel"])
    hier = datetime.now(UTC) - timedelta(hours=1)
    secret_store.enregistrer(
        "designer", "FIGMA_OAUTH_TOKEN", "vieux-oauth", mode_auth="oauth_importe", expire_le=hier
    )
    assert resolus(mcp_store.lire("designer"), secret_store.environ("designer")) == ()  # omis

    demain = datetime.now(UTC) + timedelta(days=1)
    secret_store.renouveler("designer", "FIGMA_OAUTH_TOKEN", "neuf-oauth", demain)

    (monte,) = resolus(mcp_store.lire("designer"), secret_store.environ("designer"))
    assert monte.headers["Authorization"] == "Bearer neuf-oauth"


# --- ④ Rétro-compat : l'héritée seule, inchangée ----------------------------------------


def test_retro_compat_sans_pool_le_montage_reste_celui_du_socle(mcp_store, secret_store):
    # Sans pool ni activation, un agent qui garde son fichier hérité `<agent>.json`
    # (#104) monte exactement comme avant : le nouveau modèle n'impose rien aux
    # runs existants tant qu'aucune activation n'est posée.
    mcp_store.racine.mkdir(parents=True)
    (mcp_store.racine / "qa.json").write_text(
        '{"serveurs": [{"nom": "tickets", "type": "stdio", "commande": "npx",'
        ' "env": {"GITLAB_PERSONAL_ACCESS_TOKEN": "${GITLAB_TOKEN}"}}]}',
        encoding="utf-8",
    )
    secret_store.enregistrer("qa", "GITLAB_TOKEN", "glpat-herite-9", mode_auth="token_statique")

    (monte,) = resolus(mcp_store.lire("qa"), secret_store.environ("qa"))
    assert monte.nom == "tickets"
    assert monte.env["GITLAB_PERSONAL_ACCESS_TOKEN"] == "glpat-herite-9"

"""Tests du registre curé de serveurs MCP + recherche (ticket #131, parent #129).

Aucun réseau ni secret : le registre est un seed en code, l'API tourne sur le
TestClient de Starlette (bus mémoire). Ce lot intermédiaire ne porte que le
**cœur critique** — le garde-fou supply-chain (`instancier`) et la recherche —,
la logique d'allowlist étant sensible ; la couverture intégrale (UI, migration,
liaison par agent) est **différée au lot 5/5 → #134** du parent #129.

Couvre :
① le seed curé : GitLab, Slack, Figma officiel présents, chaque entrée portant
   transport, gabarit `${VAR}`, mode d'auth (docs/21), variables à fournir et
   lien de procédure côté outil ; forme publique `to_dict` (gabarit tel quel) ;
② la recherche par nom/tag : insensible à la casse et aux accents, requête vide
   → tout le registre, requête sans correspondance → rien ;
③ le garde-fou supply-chain (docs/19) : `instancier` ne rend une liaison
   `ServeurMcp` **montable** que pour une entrée curée ; un id hors allowlist est
   refusé (`ValueError`), jamais monté — et la liaison produite se résout bien
   via `maestro.agents.mcp.resolus` ;
④ les invariants du registre : id en double refusé, mode d'auth inconnu refusé,
   entrée au gabarit invalide refusée à la construction ;
⑤ l'API : `GET /api/mcp/registre` (liste + `?q=` recherche) et
   `GET /api/mcp/registre/{id}` (404 hors allowlist).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from maestro.agents.mcp import McpServerUnavailable, resolus
from maestro.agents.mcp_registry import (
    SEED,
    EntreeRegistre,
    RegistreMcp,
    VariableSecret,
)
from maestro.controltower.app import create_app
from maestro.controltower.events import InMemoryEventBus

# ── ① seed curé ───────────────────────────────────────────────────────────────


def test_seed_expose_les_pilotes_et_les_deux_forges() -> None:
    registre = RegistreMcp.curee()
    assert [e.id for e in registre.lister()] == ["github", "gitlab", "slack", "figma-officiel"]


def test_les_deux_forges_cohabitent_dans_la_bibliotheque() -> None:
    """Le registre est une bibliothèque, pas la config d'un agent (#412).

    Il répond à « quelles intégrations existe-t-il ? », jamais à « laquelle ce
    projet utilise-t-il ? » — ce dernier point se lit dans `core/mcp/qa.json`
    seul, et c'est `tests/test_controltower.py` qui l'épingle. Garder les deux
    n'est donc pas une hésitation : l'allowlist *est* le registre, donc en
    sortir `gitlab` interdirait de monter un serveur GitLab, alors qu'un projet
    outillé par Maestro n'est pas forcément sur la forge du nôtre.
    """
    registre = RegistreMcp.curee()
    forges = [e.id for e in registre.rechercher("forge")]
    assert forges == ["github", "gitlab"]
    # Les deux restent instanciables — c'est ce que « curé » veut dire.
    assert registre.instancier("github").type == "http"
    assert registre.instancier("gitlab").type == "stdio"


def test_chaque_entree_porte_ses_metadonnees() -> None:
    for entree in RegistreMcp.curee().lister():
        assert entree.transport in ("stdio", "sse", "http")
        assert entree.mode_auth  # classé docs/21
        assert entree.procedure_url  # lien de procédure côté outil
        assert entree.secrets  # au moins une variable à fournir
        # Le gabarit d'exécution porte au moins une référence ${VAR}.
        gabarit = " ".join((*entree.env.values(), *entree.headers.values()))
        assert "${" in gabarit


def test_figma_est_optionnel_et_oauth_importe() -> None:
    figma = RegistreMcp.curee().get("figma-officiel")
    assert figma is not None
    assert figma.optionnel is True
    assert figma.mode_auth == "oauth_importe"
    assert figma.transport == "http"


def test_to_dict_reemet_le_gabarit_tel_quel() -> None:
    """La forme publique montre les `${VAR}` (gabarit à remplir), pas des secrets masqués."""
    fiche = RegistreMcp.curee().get("gitlab")
    assert fiche is not None
    dico = fiche.to_dict()
    assert dico["curee"] is True
    assert dico["env"]["GITLAB_PERSONAL_ACCESS_TOKEN"] == "${GITLAB_TOKEN}"
    assert dico["secrets"][0]["cle"] == "GITLAB_TOKEN"
    assert dico["secrets"][0]["secret"] is True


# ── ② recherche ───────────────────────────────────────────────────────────────


def test_recherche_par_nom() -> None:
    resultats = RegistreMcp.curee().rechercher("figma")
    assert [e.id for e in resultats] == ["figma-officiel"]


def test_recherche_par_tag() -> None:
    # « tickets » est porté par les deux forges (#412) : la recherche rend les
    # deux, dans l'ordre du seed — elle filtre, elle ne départage pas.
    resultats = RegistreMcp.curee().rechercher("tickets")
    assert [e.id for e in resultats] == ["github", "gitlab"]


def test_recherche_insensible_casse_et_accents() -> None:
    registre = RegistreMcp.curee()
    # « messagerie » est un tag de Slack ; « MESSAGERIE » le retrouve.
    assert [e.id for e in registre.rechercher("MESSAGERIE")] == ["slack"]
    # « maquettes » (tag Figma, sans accent) et sa variante accentuée coïncident.
    assert registre.rechercher("maquëttes") == registre.rechercher("maquettes")
    assert [e.id for e in registre.rechercher("maquëttes")] == ["figma-officiel"]


def test_recherche_vide_rend_tout() -> None:
    registre = RegistreMcp.curee()
    assert registre.rechercher("") == registre.lister()
    assert registre.rechercher("   ") == registre.lister()


def test_recherche_sans_correspondance() -> None:
    assert RegistreMcp.curee().rechercher("kubernetes") == ()


# ── ③ garde-fou supply-chain ─────────────────────────────────────────────────


def test_instancier_entree_curee_rend_une_liaison_montable() -> None:
    registre = RegistreMcp.curee()
    serveur = registre.instancier("gitlab")
    # Le gabarit ${VAR} est intact (forme versionnable) …
    assert serveur.env["GITLAB_PERSONAL_ACCESS_TOKEN"] == "${GITLAB_TOKEN}"
    # … et se monte proprement une fois le secret fourni.
    montes = resolus([serveur], {"GITLAB_TOKEN": "glpat-xxx"})
    assert montes[0].env["GITLAB_PERSONAL_ACCESS_TOKEN"] == "glpat-xxx"


def test_instancier_nomme_la_liaison() -> None:
    serveur = RegistreMcp.curee().instancier("slack", nom="slack-devops")
    assert serveur.nom == "slack-devops"
    assert serveur.type == "stdio"


def test_instancier_hors_allowlist_refuse() -> None:
    """Découverte ≠ installation : un id inconnu du registre n'est jamais monté."""
    with pytest.raises(ValueError, match="hors allowlist"):
        RegistreMcp.curee().instancier("@evil/pkg")


def test_instancier_figma_optionnel_omis_sans_token() -> None:
    """La liaison hérite du caractère optionnel : sans secret, omise au montage (#125)."""
    serveur = RegistreMcp.curee().instancier("figma-officiel")
    assert serveur.optionnel is True
    assert resolus([serveur], {}) == ()
    # Un serveur non optionnel, lui, échouerait.
    with pytest.raises(McpServerUnavailable):
        resolus([RegistreMcp.curee().instancier("gitlab")], {})


# ── ④ invariants du registre ─────────────────────────────────────────────────


def _entree(**kwargs: object) -> EntreeRegistre:
    base: dict[str, object] = {
        "id": "x",
        "nom": "X",
        "description": "",
        "mode_auth": "token_statique",
        "transport": "stdio",
        "commande": "npx",
    }
    base.update(kwargs)
    return EntreeRegistre(**base)  # type: ignore[arg-type]


def test_id_en_double_refuse() -> None:
    with pytest.raises(ValueError, match="double"):
        RegistreMcp([_entree(id="dup"), _entree(id="dup")])


def test_mode_auth_inconnu_refuse() -> None:
    with pytest.raises(ValueError, match="mode d'auth"):
        RegistreMcp([_entree(mode_auth="magique")])


def test_gabarit_invalide_refuse_a_la_construction() -> None:
    # stdio sans commande : la validation de `maestro.agents.mcp` refuse.
    with pytest.raises(ValueError):
        RegistreMcp([_entree(commande="")])


def test_variable_secret_to_dict() -> None:
    var = VariableSecret("K", "clé", secret=False)
    assert var.to_dict() == {"cle": "K", "description": "clé", "secret": False}


def test_seed_est_un_registre_valide() -> None:
    # Le seed en clair du module se construit sans erreur (allowlist saine).
    assert RegistreMcp(SEED).lister() == SEED


# ── ⑤ API ─────────────────────────────────────────────────────────────────────


@pytest.fixture()
def client() -> TestClient:
    with TestClient(create_app(bus=InMemoryEventBus())) as client:
        return client


def test_api_liste_le_registre(client: TestClient) -> None:
    reponse = client.get("/api/mcp/registre")
    assert reponse.status_code == 200
    corps = reponse.json()
    assert [e["id"] for e in corps] == ["github", "gitlab", "slack", "figma-officiel"]
    assert all(e["curee"] is True for e in corps)


def test_api_recherche_par_q(client: TestClient) -> None:
    reponse = client.get("/api/mcp/registre", params={"q": "design"})
    assert reponse.status_code == 200
    assert [e["id"] for e in reponse.json()] == ["figma-officiel"]


def test_api_fiche_entree(client: TestClient) -> None:
    reponse = client.get("/api/mcp/registre/slack")
    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["nom"] == "Slack"
    assert corps["mode_auth"] == "token_statique"
    assert corps["env"]["SLACK_BOT_TOKEN"] == "${SLACK_BOT_TOKEN}"


def test_api_fiche_inconnue_404(client: TestClient) -> None:
    reponse = client.get("/api/mcp/registre/inconnu")
    assert reponse.status_code == 404

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
    """Les quatre pilotes d'origine sont toujours là — **inclusion**, plus égalité (#271).

    La liste exacte était le contrat tant que le registre tenait en quatre
    entrées ; depuis l'élargissement elle rendrait ce test faux à chaque
    intégration ajoutée, c'est-à-dire à chaque fois qu'il a le plus de raisons
    d'être joué. Ce qui doit rester vrai est qu'aucun ajout ne fait *disparaître*
    un pilote historique.
    """
    ids = {e.id for e in RegistreMcp.curee().lister()}
    assert {"github", "gitlab", "slack", "figma-officiel"} <= ids


def test_lister_met_les_plus_courants_en_tete() -> None:
    """Le tri du critère 2 : palier d'usage décroissant, puis nom (jamais l'ordre du seed)."""
    entrees = RegistreMcp.curee().lister()
    paliers = [e.popularite for e in entrees]
    assert paliers == sorted(paliers, reverse=True)
    # À palier égal, l'ordre est alphabétique — stable, sans faux gagnant.
    tetes = [e.nom for e in entrees if e.popularite == paliers[0]]
    assert tetes == sorted(tetes, key=str.casefold)


def test_recherche_par_editeur() -> None:
    """« nom, éditeur et tags » (critère 2) : un éditeur qui n'est dans aucun nom se trouve."""
    resultats = RegistreMcp.curee().rechercher("microsoft")
    assert [e.id for e in resultats] == ["playwright"]


def test_provenance_dit_ses_sources_et_sa_date() -> None:
    """Critère 1 : la liste dit d'où elle vient et quand elle a été revue."""
    provenance = RegistreMcp.curee().provenance
    assert provenance.revue_le
    assert provenance.sources
    assert all(s.url.startswith("https://") for s in provenance.sources)
    assert "resume" in provenance.to_dict()


def test_tags_rend_les_pistes_de_recherche() -> None:
    """La sortie du cul-de-sac (critère 2) : par quoi chercher, dédoublonné et trié."""
    tags = RegistreMcp.curee().tags()
    assert "forge" in tags and "recherche" in tags
    assert list(tags) == sorted(set(tags))


def test_les_deux_forges_cohabitent_dans_la_bibliotheque() -> None:
    """Le registre est une bibliothèque, pas la config d'un agent (#412).

    Il répond à « quelles intégrations existe-t-il ? », jamais à « laquelle ce
    projet utilise-t-il ? » — ce dernier point se lit dans `core/mcp/qa.json`
    seul, et c'est `tests/test_controltower.py` qui l'épingle. Garder les deux
    n'est donc pas une hésitation : le seed **est** l'allowlist du dépôt, donc en
    sortir `gitlab` interdirait de monter un serveur GitLab, alors qu'un projet
    outillé par Maestro n'est pas forcément sur la forge du nôtre. ⚠ Depuis #678
    l'allowlist ne se réduit plus au seed — elle est le seed **plus** ce qu'un
    geste humain y a admis —, mais l'argument tient au mot près : une admission
    est un geste, pas un filet, et rien ne rattraperait une entrée qu'on aurait
    sortie d'ici.
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
        assert entree.editeur  # qui publie ce serveur (#271, critère 1)
        assert entree.description  # ce que l'intégration apporte (#271, critère 1)
        if entree.mode_auth == "sans_secret":
            # Le cas dégénéré (#271) : rien à saisir, donc rien à guider. Exiger
            # une variable ici reviendrait à en inventer une pour satisfaire le
            # test — et à fermer la bibliothèque aux utilitaires locaux, qui sont
            # parmi les serveurs les plus utilisés.
            assert entree.secrets == ()
            continue
        assert entree.secrets  # au moins une variable à fournir
        # Le gabarit d'exécution porte au moins une référence ${VAR}.
        gabarit = " ".join((*entree.env.values(), *entree.headers.values()))
        assert "${" in gabarit


def test_chaque_variable_declaree_est_referencee_par_le_gabarit() -> None:
    """Aucune variable orpheline : ce qu'on fait saisir doit servir au montage (#271).

    Le piège d'une liste qui grandit : déclarer un secret dans `secrets` sans le
    référencer dans `env`/`headers`. L'UI le ferait saisir, `resolus` ne le
    lirait jamais, et l'intégration échouerait au montage en accusant autre
    chose. `resolus` ne résout **que** `env` et `headers` — jamais `args` —,
    d'où un gabarit qui ne porte aucune variable en ligne de commande.
    """
    for entree in RegistreMcp.curee().lister():
        gabarit = " ".join((*entree.env.values(), *entree.headers.values()))
        for variable in entree.secrets:
            assert f"${{{variable.cle}}}" in gabarit, f"{entree.id} : {variable.cle} orpheline"
        assert not any("${" in arg for arg in entree.args), f"{entree.id} : ${{VAR}} en args"


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
    # Les deux voies Figma (serveur officiel et pont communautaire, #271) — et
    # la plus courante d'abord, ce que le tri garantit.
    resultats = RegistreMcp.curee().rechercher("figma")
    assert [e.id for e in resultats] == ["figma-officiel", "figma-pont"]


def test_recherche_par_tag() -> None:
    # « tickets » est porté par les deux forges (#412) et par les gestionnaires
    # de projet (#271) : la recherche filtre, et le tri met le plus courant en
    # tête. L'inclusion plutôt que l'égalité — la liste grandit à chaque revue.
    resultats = RegistreMcp.curee().rechercher("tickets")
    ids = [e.id for e in resultats]
    assert ids[0] == "github"
    assert {"github", "gitlab", "linear", "atlassian"} <= set(ids)


def test_recherche_insensible_casse_et_accents() -> None:
    registre = RegistreMcp.curee()
    # « messagerie » est un tag de Slack ; « MESSAGERIE » le retrouve.
    assert [e.id for e in registre.rechercher("MESSAGERIE")] == ["slack"]
    # « maquettes » (tag Figma, sans accent) et sa variante accentuée coïncident.
    assert registre.rechercher("maquëttes") == registre.rechercher("maquettes")
    assert [e.id for e in registre.rechercher("maquëttes")] == ["figma-officiel", "figma-pont"]


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
    # `lister()` trie par palier d'usage (#271), donc l'égalité porte sur le
    # contenu et non sur l'ordre de déclaration (une entrée n'est pas hachable :
    # elle porte des dicts, d'où la comparaison sur les ids).
    listees = RegistreMcp(SEED).lister()
    assert sorted(e.id for e in listees) == sorted(e.id for e in SEED)


def test_id_reserve_par_une_route_refuse() -> None:
    """Un id que l'API prend pour une de ses routes rendrait l'entrée injoignable (#271)."""
    with pytest.raises(ValueError, match="réservé"):
        RegistreMcp([_entree(id="provenance")])


# ── ⑤ API ─────────────────────────────────────────────────────────────────────


@pytest.fixture()
def client() -> TestClient:
    with TestClient(create_app(bus=InMemoryEventBus())) as client:
        return client


def test_api_liste_le_registre(client: TestClient) -> None:
    reponse = client.get("/api/mcp/registre")
    assert reponse.status_code == 200
    corps = reponse.json()
    assert {"github", "gitlab", "slack", "figma-officiel"} <= {e["id"] for e in corps}
    assert all(e["curee"] is True for e in corps)
    # L'éditeur et le palier d'usage voyagent jusqu'à l'UI (#271, critères 1 et 2).
    assert all(e["editeur"] for e in corps)
    paliers = [e["popularite"] for e in corps]
    assert paliers == sorted(paliers, reverse=True)


def test_api_recherche_par_q(client: TestClient) -> None:
    reponse = client.get("/api/mcp/registre", params={"q": "design"})
    assert reponse.status_code == 200
    assert [e["id"] for e in reponse.json()] == ["figma-officiel", "figma-pont"]


def test_api_provenance(client: TestClient) -> None:
    """La provenance a sa route (#271) : l'écran ne peut pas la dire s'il ne l'a pas."""
    reponse = client.get("/api/mcp/registre/provenance")
    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["revue_le"]
    assert corps["sources"]
    assert corps["tags"]  # les pistes de recherche, pour un résultat vide


def test_api_provenance_ne_masque_aucune_entree(client: TestClient) -> None:
    """L'id `provenance` est réservé, donc la route ne peut voler la place de personne."""
    ids = {e["id"] for e in client.get("/api/mcp/registre").json()}
    assert "provenance" not in ids


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

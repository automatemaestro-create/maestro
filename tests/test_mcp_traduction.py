"""Traduction d'un `server.json` amont en entrée de bibliothèque (#676, lot 2/6 de #673).

Le contrat du module tient en une phrase : **une entrée complète, ou un refus
nommé** — jamais une entrée à moitié déclarée. Ces tests l'éprouvent des deux
côtés, et surtout sur les refus, qui sont la moitié utile : un refus se juge sur
son `motif` (code stable, sur lequel une UI groupe) *et* sur sa `cause` (la
phrase qui nomme la forme fautive), et les deux sont vérifiés ensemble.

Deux familles de refus méritent d'être lues avant d'y toucher, parce qu'elles ne
sont pas des précautions de style mais des faits sur `maestro.agents.mcp.resolus`
— qui ne substitue les `${VAR}` que dans `env` et `headers` : une valeur
nécessaire en **argv** et un gabarit dans l'**URL** d'un `remotes[]` ne sont
résolubles par personne, donc refusés plutôt que servis troués.
"""

from __future__ import annotations

from typing import Any

import pytest

from maestro.agents.mcp_amont import STATUT_ACTIF, STATUT_SUPPRIME, EntreeAmont
from maestro.agents.mcp_registry import MODES_AUTH, VariableSecret
from maestro.agents.mcp_traduction import (
    MODES_CURATION,
    MODES_DERIVES,
    MOTIF_ARGV,
    MOTIF_IDENTITE,
    MOTIF_REGISTRE,
    MOTIF_SANS_FORME,
    MOTIF_SUPPRIMEE,
    MOTIF_TRANSPORT,
    MOTIF_URL,
    MOTIF_VALIDATION,
    MOTIF_VERSION,
    MOTIFS,
    Refus,
    Traduction,
    deriver_mode_auth,
    traduire,
    traduire_entree,
)

SCHEMA_CONNU = "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json"


def document(**champs: Any) -> dict[str, Any]:
    """Un `server.json` minimal et valide, que chaque test spécialise."""
    base: dict[str, Any] = {
        "$schema": SCHEMA_CONNU,
        "name": "io.github.alice/mon-serveur",
        "description": "Un serveur de démonstration.",
        "version": "1.2.3",
    }
    base.update(champs)
    return base


def remote(**champs: Any) -> dict[str, Any]:
    """Un `remotes[]` valide, que chaque test spécialise."""
    return {"type": "streamable-http", "url": "https://mcp.example.com/mcp", **champs}


def paquet(**champs: Any) -> dict[str, Any]:
    """Un `packages[]` valide (npm → npx), que chaque test spécialise."""
    return {
        "registryType": "npm",
        "identifier": "@alice/mon-serveur-mcp",
        "version": "0.4.0",
        **champs,
    }


# ── Le mode d'auth est dérivé, et jamais deviné ──────────────────────────────


def test_sans_variable_le_mode_est_sans_secret() -> None:
    assert deriver_mode_auth(()) == "sans_secret"


def test_une_variable_secrete_donne_token_statique() -> None:
    secrets = (VariableSecret(cle="MCP_X_TOKEN", secret=True),)
    assert deriver_mode_auth(secrets) == "token_statique"


def test_des_variables_dont_aucune_secrete_tombent_sur_sans_secret() -> None:
    """Le cas intermédiaire : les modes classent *comment un secret s'obtient*."""
    secrets = (
        VariableSecret(cle="MCP_X_TEAM_ID", secret=False),
        VariableSecret(cle="MCP_X_CANAL", secret=False),
    )
    assert deriver_mode_auth(secrets) == "sans_secret"


def test_la_derivation_ne_produit_jamais_un_mode_de_curation() -> None:
    """`appairage` et `oauth_importe` décrivent une procédure humaine — jamais dérivée."""
    assert set(MODES_CURATION) == {"appairage", "oauth_importe"}
    # Défini comme le *complément* des dérivables : un cinquième mode ajouté à
    # `MODES_AUTH` tombe du côté sûr tout seul.
    assert set(MODES_DERIVES) | set(MODES_CURATION) == set(MODES_AUTH)
    assert not set(MODES_DERIVES) & set(MODES_CURATION)


# ── Un `remotes[]` se traduit en endpoint ────────────────────────────────────


def test_un_remote_streamable_http_devient_une_entree_http() -> None:
    traduction = traduire(document(remotes=[remote()], websiteUrl="https://example.com"))

    assert traduction.ok
    assert traduction.refus is None
    assert traduction.avertissements == ()
    assert traduction.schema == SCHEMA_CONNU

    entree = traduction.entree
    assert entree is not None
    assert entree.transport == "http"
    assert entree.url == "https://mcp.example.com/mcp"
    assert entree.mode_auth == "sans_secret"
    assert entree.procedure_url == "https://example.com"
    # Rien n'est inventé : ni tag, ni popularité.
    assert entree.tags == ()
    assert entree.popularite == 0
    assert entree.optionnel is False


def test_le_nom_amont_se_recompose_au_caractere_pres() -> None:
    """`nom` + `editeur` redonnent le nom amont : rien n'est perdu, rien n'est embelli."""
    traduction = traduire(document(remotes=[remote()]))

    entree = traduction.entree
    assert entree is not None
    assert entree.editeur == "io.github.alice"
    assert entree.nom == "mon-serveur"
    assert f"{entree.editeur}/{entree.nom}" == "io.github.alice/mon-serveur"
    # Le namespace est conservé dans l'id : c'est lui qui le rend injectif.
    assert entree.id == "io-github-alice-mon-serveur"


def test_un_nom_sans_namespace_reste_entier() -> None:
    traduction = traduire(document(name="mon-serveur", remotes=[remote()]))

    entree = traduction.entree
    assert entree is not None
    assert entree.editeur == ""
    assert entree.nom == "mon-serveur"


def test_le_transport_sse_est_supporte() -> None:
    traduction = traduire(document(remotes=[remote(type="sse")]))

    assert traduction.ok
    assert traduction.entree is not None
    assert traduction.entree.transport == "sse"


def test_un_transport_distant_inconnu_est_refuse_en_le_nommant() -> None:
    traduction = traduire(document(remotes=[remote(type="websocket")]))

    assert not traduction.ok
    assert traduction.refus is not None
    assert traduction.refus.motif == MOTIF_TRANSPORT
    assert "websocket" in traduction.refus.cause
    assert "remotes[0]" in traduction.refus.cause


def test_un_remote_sans_url_est_refuse() -> None:
    document_sans_url = document(remotes=[{"type": "streamable-http"}])
    traduction = traduire(document_sans_url)

    assert traduction.refus is not None
    assert traduction.refus.motif == MOTIF_VALIDATION
    assert "url" in traduction.refus.cause


def test_un_gabarit_dans_l_url_est_refuse_car_personne_ne_le_resoudrait() -> None:
    """`resolus` ne traverse jamais `url` : substituer ou laisser tel quel sont pires."""
    traduction = traduire(
        document(remotes=[remote(url="https://{region}.mcp.example.com/mcp")])
    )

    assert traduction.refus is not None
    assert traduction.refus.motif == MOTIF_URL
    assert "{region}" in traduction.refus.cause


def test_une_url_non_http_est_refusee_par_la_validation_du_serveur() -> None:
    """L'entrée produite passe `valide_serveur` : ce qui ne se monterait pas est un refus."""
    traduction = traduire(document(remotes=[remote(url="ftp://example.com/mcp")]))

    assert not traduction.ok
    assert traduction.refus is not None
    assert traduction.refus.motif == MOTIF_VALIDATION
    assert "url" in traduction.refus.cause


# ── Les en-têtes, et les variables qu'ils déclarent ──────────────────────────


def test_un_entete_gabarite_devient_une_variable_namespacee() -> None:
    entete = {
        "name": "Authorization",
        "value": "Bearer {token}",
        "isSecret": True,
        "variables": {"token": {"description": "Jeton d'API", "isSecret": True}},
    }
    traduction = traduire(document(remotes=[remote(headers=[entete])]))

    entree = traduction.entree
    assert entree is not None
    attendu = "MCP_IO_GITHUB_ALICE_MON_SERVEUR_TOKEN"
    assert entree.headers == {"Authorization": "Bearer ${" + attendu + "}"}
    assert entree.mode_auth == "token_statique"
    assert entree.optionnel is True
    assert [s.cle for s in entree.secrets] == [attendu]
    assert entree.secrets[0].description == "Jeton d'API"
    assert entree.secrets[0].secret is True


def test_un_gabarit_non_declare_est_traite_comme_secret_requis_et_signale() -> None:
    entete = {"name": "Authorization", "value": "Bearer {token}"}
    traduction = traduire(document(remotes=[remote(headers=[entete])]))

    entree = traduction.entree
    assert entree is not None
    assert entree.secrets[0].secret is True
    assert any("non déclaré" in note for note in traduction.avertissements)


def test_une_valeur_litterale_declaree_secrete_est_laissee_telle_quelle_en_le_disant() -> None:
    entete = {"name": "X-Api-Key", "value": "clef-en-clair", "isSecret": True}
    traduction = traduire(document(remotes=[remote(headers=[entete])]))

    entree = traduction.entree
    assert entree is not None
    assert entree.headers == {"X-Api-Key": "clef-en-clair"}
    assert entree.secrets == ()
    assert any("littérale déclarée secrète" in note for note in traduction.avertissements)


def test_une_variable_requise_mais_non_secrete_ne_bascule_pas_le_mode() -> None:
    entete = {"name": "X-Team-Id", "isRequired": True}
    traduction = traduire(document(remotes=[remote(headers=[entete])]))

    entree = traduction.entree
    assert entree is not None
    cle = "MCP_IO_GITHUB_ALICE_MON_SERVEUR_X_TEAM_ID"
    assert entree.headers == {"X-Team-Id": "${" + cle + "}"}
    assert entree.secrets == (VariableSecret(cle=cle, description="", secret=False),)
    assert entree.mode_auth == "sans_secret"


def test_une_variable_facultative_sans_valeur_est_omise_du_gabarit_en_le_disant() -> None:
    """Une référence `${VAR}` *signifie* « requise » pour `resolus` : y placer une
    variable facultative changerait sa nature."""
    entete = {"name": "X-Trace", "description": "en-tête de trace"}
    traduction = traduire(document(remotes=[remote(headers=[entete])]))

    entree = traduction.entree
    assert entree is not None
    assert entree.headers == {}
    assert entree.secrets == ()
    assert any("facultative sans valeur" in note for note in traduction.avertissements)


def test_une_variable_secrete_est_declaree_meme_sans_is_required() -> None:
    """Le défaut de schéma est `false`, mais un serveur qui lit un secret en a besoin."""
    entete = {"name": "X-Api-Key", "isSecret": True}
    traduction = traduire(document(remotes=[remote(headers=[entete])]))

    entree = traduction.entree
    assert entree is not None
    assert entree.secrets[0].secret is True
    assert entree.mode_auth == "token_statique"


def test_deux_entetes_sur_la_meme_variable_ne_la_declarent_qu_une_fois() -> None:
    entetes = [
        {"name": "Authorization", "value": "Bearer {token}"},
        {"name": "X-Token", "value": "{token}"},
    ]
    traduction = traduire(document(remotes=[remote(headers=entetes)]))

    entree = traduction.entree
    assert entree is not None
    assert len(entree.secrets) == 1
    assert len(entree.headers) == 2


def test_un_entete_qui_n_est_pas_un_objet_est_refuse() -> None:
    traduction = traduire(document(remotes=[remote(headers=["Authorization: x"])]))

    assert traduction.refus is not None
    assert traduction.refus.motif == MOTIF_VALIDATION
    assert "objet attendu" in traduction.refus.cause


def test_un_entete_sans_name_est_refuse() -> None:
    traduction = traduire(document(remotes=[remote(headers=[{"value": "x"}])]))

    assert traduction.refus is not None
    assert traduction.refus.motif == MOTIF_VALIDATION
    assert "name" in traduction.refus.cause


# ── Un `packages[]` se traduit en commande stdio ─────────────────────────────


def test_un_paquet_npm_devient_une_commande_npx_a_version_epinglee() -> None:
    traduction = traduire(document(packages=[paquet()]))

    entree = traduction.entree
    assert entree is not None
    assert entree.transport == "stdio"
    assert entree.commande == "npx"
    assert entree.args == ("-y", "@alice/mon-serveur-mcp@0.4.0")
    assert entree.url == ""


def test_un_paquet_pypi_devient_une_commande_uvx_sans_drapeau() -> None:
    traduction = traduire(
        document(packages=[paquet(registryType="pypi", identifier="mcp-server-alice")])
    )

    entree = traduction.entree
    assert entree is not None
    assert entree.commande == "uvx"
    assert entree.args == ("mcp-server-alice@0.4.0",)


def test_la_version_du_serveur_sert_de_repli_a_celle_du_paquet() -> None:
    sans_version = {"registryType": "npm", "identifier": "@alice/mon-serveur-mcp"}
    traduction = traduire(document(version="2.5.0", packages=[sans_version]))

    entree = traduction.entree
    assert entree is not None
    assert entree.args == ("-y", "@alice/mon-serveur-mcp@2.5.0")


@pytest.mark.parametrize("flottante", ["latest", "next", "stable", "canary", ""])
def test_une_version_flottante_est_refusee(flottante: str) -> None:
    """Une étiquette flottante retire à la fédération le seul argument qui la rend sûre."""
    traduction = traduire(document(version=flottante, packages=[paquet(version=flottante)]))

    assert not traduction.ok
    assert traduction.refus is not None
    assert traduction.refus.motif == MOTIF_VERSION


@pytest.mark.parametrize("epinglee", ["1.2.3", "v0.4.0", "2026.1.1"])
def test_une_version_qui_commence_par_un_chiffre_est_epinglee(epinglee: str) -> None:
    traduction = traduire(document(packages=[paquet(version=epinglee)]))

    assert traduction.ok
    assert traduction.entree is not None
    assert traduction.entree.args[-1].endswith(f"@{epinglee}")


def test_un_registre_non_supporte_est_refuse_en_le_nommant() -> None:
    """`oci` attendrait ses variables en `-e` sur la ligne de commande — donc en argv."""
    traduction = traduire(document(packages=[paquet(registryType="oci")]))

    assert traduction.refus is not None
    assert traduction.refus.motif == MOTIF_REGISTRE
    assert "oci" in traduction.refus.cause


def test_un_paquet_sans_identifier_est_refuse() -> None:
    traduction = traduire(document(packages=[{"registryType": "npm", "version": "1.0.0"}]))

    assert traduction.refus is not None
    assert traduction.refus.motif == MOTIF_VALIDATION
    assert "identifier" in traduction.refus.cause


def test_un_paquet_a_transport_non_stdio_est_refuse() -> None:
    """Un paquet qu'il faut lancer *puis* joindre par une URL n'est pas exprimable ici."""
    traduction = traduire(
        document(packages=[paquet(transport={"type": "streamable-http"})])
    )

    assert traduction.refus is not None
    assert traduction.refus.motif == MOTIF_TRANSPORT


def test_un_transport_stdio_explicite_passe() -> None:
    traduction = traduire(document(packages=[paquet(transport={"type": "stdio"})]))

    assert traduction.ok


# ── Les arguments : littéraux ou refus ───────────────────────────────────────


def test_les_arguments_litteraux_encadrent_le_paquet() -> None:
    forme = paquet(
        runtimeArguments=[{"type": "named", "name": "--node-option", "value": "--trace"}],
        packageArguments=[{"type": "positional", "value": "serve"}],
    )
    traduction = traduire(document(packages=[forme]))

    entree = traduction.entree
    assert entree is not None
    assert entree.args == (
        "-y",
        "--node-option",
        "--trace",
        "@alice/mon-serveur-mcp@0.4.0",
        "serve",
    )


def test_une_valeur_par_defaut_sert_de_litteral() -> None:
    forme = paquet(packageArguments=[{"type": "positional", "default": "stdio"}])
    traduction = traduire(document(packages=[forme]))

    entree = traduction.entree
    assert entree is not None
    assert entree.args[-1] == "stdio"


def test_un_gabarit_en_argv_est_refuse_car_resolus_ne_traverse_pas_args() -> None:
    forme = paquet(packageArguments=[{"type": "named", "name": "--key", "value": "{api_key}"}])
    traduction = traduire(document(packages=[forme]))

    assert traduction.refus is not None
    assert traduction.refus.motif == MOTIF_ARGV
    assert "packageArguments[0]" in traduction.refus.cause


def test_un_argument_porteur_de_variables_est_refuse_meme_sans_accolades() -> None:
    forme = paquet(
        packageArguments=[
            {"type": "positional", "value": "serve", "variables": {"mode": {}}}
        ]
    )
    traduction = traduire(document(packages=[forme]))

    assert traduction.refus is not None
    assert traduction.refus.motif == MOTIF_ARGV


def test_une_valeur_necessaire_en_argv_est_refusee() -> None:
    """Rien ne distingue à coup sûr `--verbose` (complet) de `--api-key` (amputé)."""
    forme = paquet(
        packageArguments=[{"type": "named", "name": "--api-key", "isRequired": True}]
    )
    traduction = traduire(document(packages=[forme]))

    assert traduction.refus is not None
    assert traduction.refus.motif == MOTIF_ARGV
    assert "--api-key" in traduction.refus.cause


def test_un_argument_facultatif_sans_valeur_est_omis_en_le_disant() -> None:
    forme = paquet(packageArguments=[{"type": "named", "name": "--verbose"}])
    traduction = traduire(document(packages=[forme]))

    entree = traduction.entree
    assert entree is not None
    assert "--verbose" not in entree.args
    assert any("facultatif sans valeur" in note for note in traduction.avertissements)


def test_un_argument_nomme_sans_name_est_refuse() -> None:
    forme = paquet(packageArguments=[{"type": "named", "value": "x"}])
    traduction = traduire(document(packages=[forme]))

    assert traduction.refus is not None
    assert traduction.refus.motif == MOTIF_ARGV
    assert "name" in traduction.refus.cause


def test_un_argument_qui_n_est_pas_un_objet_est_refuse() -> None:
    traduction = traduire(document(packages=[paquet(packageArguments=["--stdio"])]))

    assert traduction.refus is not None
    assert traduction.refus.motif == MOTIF_ARGV
    assert "objet attendu" in traduction.refus.cause


def test_les_variables_d_environnement_deviennent_le_gabarit_env() -> None:
    forme = paquet(
        environmentVariables=[
            {"name": "API_KEY", "isSecret": True, "isRequired": True},
            {"name": "LOG_LEVEL", "value": "info"},
        ]
    )
    traduction = traduire(document(packages=[forme]))

    entree = traduction.entree
    assert entree is not None
    cle = "MCP_IO_GITHUB_ALICE_MON_SERVEUR_API_KEY"
    assert entree.env == {"API_KEY": "${" + cle + "}", "LOG_LEVEL": "info"}
    assert entree.mode_auth == "token_statique"
    assert [s.cle for s in entree.secrets] == [cle]


def test_une_variable_d_environnement_facultative_est_omise_du_gabarit() -> None:
    forme = paquet(environmentVariables=[{"name": "LOG_LEVEL", "description": "verbosité"}])
    traduction = traduire(document(packages=[forme]))

    entree = traduction.entree
    assert entree is not None
    assert entree.env == {}
    assert entree.mode_auth == "sans_secret"
    assert any("facultative sans valeur" in note for note in traduction.avertissements)


# ── L'identité : dérivée, ou refusée ─────────────────────────────────────────


def test_un_document_sans_name_est_refuse() -> None:
    traduction = traduire({"$schema": SCHEMA_CONNU, "remotes": [remote()]})

    assert traduction.refus is not None
    assert traduction.refus.motif == MOTIF_IDENTITE
    assert traduction.nom == ""


def test_un_nom_qui_ne_donne_aucun_slug_est_refuse() -> None:
    traduction = traduire(document(name="???", remotes=[remote()]))

    assert traduction.refus is not None
    assert traduction.refus.motif == MOTIF_IDENTITE
    assert "???" in traduction.refus.cause


def test_un_id_reserve_par_une_route_de_l_api_est_refuse() -> None:
    """`provenance` est pris par une route de l'API : l'entrée serait injoignable."""
    traduction = traduire(document(name="provenance", remotes=[remote()]))

    assert traduction.refus is not None
    assert traduction.refus.motif == MOTIF_IDENTITE
    assert "provenance" in traduction.refus.cause


def test_le_slug_replie_espaces_et_ponctuation() -> None:
    traduction = traduire(document(name="Mon Serveur !", remotes=[remote()]))

    entree = traduction.entree
    assert entree is not None
    assert entree.id == "mon-serveur"


def test_un_document_sans_remotes_ni_packages_n_a_rien_a_monter() -> None:
    traduction = traduire(document())

    assert traduction.refus is not None
    assert traduction.refus.motif == MOTIF_SANS_FORME


def test_une_forme_qui_n_est_pas_un_objet_ne_compte_pas_comme_candidate() -> None:
    traduction = traduire(document(remotes=["https://mcp.example.com"], packages=["npm"]))

    assert traduction.refus is not None
    assert traduction.refus.motif == MOTIF_SANS_FORME


# ── L'ordre des candidats, et ce que les écartés laissent derrière eux ───────


def test_les_remotes_passent_avant_les_packages() -> None:
    """docs/21 §3.4 : rien à exécuter, l'URL est vérifiable."""
    traduction = traduire(document(remotes=[remote()], packages=[paquet()]))

    entree = traduction.entree
    assert entree is not None
    assert entree.transport == "http"


def test_une_forme_ecartee_laisse_sa_cause_dans_les_avertissements() -> None:
    """Une entrée servie par son secours dit pourquoi la forme préférée ne convenait pas."""
    traduction = traduire(
        document(remotes=[remote(type="websocket")], packages=[paquet()])
    )

    assert traduction.ok
    assert traduction.entree is not None
    assert traduction.entree.transport == "stdio"
    ecartee = [n for n in traduction.avertissements if "écartée au profit de" in n]
    assert len(ecartee) == 1
    assert "remotes[0]" in ecartee[0]
    assert "packages[0]" in ecartee[0]


def test_le_second_remote_prend_la_releve_du_premier() -> None:
    traduction = traduire(
        document(remotes=[remote(type="websocket"), remote(type="sse")])
    )

    assert traduction.ok
    assert traduction.entree is not None
    assert traduction.entree.transport == "sse"


def test_un_refus_a_forme_unique_porte_sa_cause_telle_quelle() -> None:
    """En dénombrer une seule ferait passer un diagnostic précis pour un bilan."""
    traduction = traduire(document(remotes=[remote(type="websocket")]))

    assert traduction.refus is not None
    assert "aucune des" not in traduction.refus.cause


def test_un_refus_a_formes_multiples_recite_toutes_les_causes() -> None:
    traduction = traduire(
        document(remotes=[remote(type="websocket")], packages=[paquet(registryType="oci")])
    )

    assert traduction.refus is not None
    # Le motif est celui de la forme *préférée*, les causes sont toutes là.
    assert traduction.refus.motif == MOTIF_TRANSPORT
    assert "aucune des 2 formes" in traduction.refus.cause
    assert "remotes[0]" in traduction.refus.cause
    assert "packages[0]" in traduction.refus.cause


def test_une_cause_deja_situee_n_est_pas_prefixee_deux_fois() -> None:
    forme = paquet(packageArguments=[{"type": "named", "value": "x"}])
    traduction = traduire(document(packages=[forme]))

    assert traduction.refus is not None
    assert traduction.refus.cause.startswith("packages[0].packageArguments[0]")
    assert "packages[0] — packages[0]" not in traduction.refus.cause


# ── Le `$schema` est lu, aucune version n'est pariée ─────────────────────────


def test_un_schema_connu_ne_produit_aucun_avertissement() -> None:
    traduction = traduire(document(remotes=[remote()]))

    assert traduction.avertissements == ()


def test_un_schema_inconnu_est_signale_sans_refuser() -> None:
    """Refuser à chaque montée de version ferait tomber la fédération le jour où l'amont bouge."""
    inconnu = "https://static.modelcontextprotocol.io/schemas/2099-01-01/server.schema.json"
    traduction = traduire(document(**{"$schema": inconnu}, remotes=[remote()]))

    assert traduction.ok
    assert any("schéma amont inconnu" in note for note in traduction.avertissements)
    assert traduction.schema == inconnu


def test_un_schema_sans_millesime_est_signale() -> None:
    sans_millesime = {"$schema": "https://example.com/server.json"}
    traduction = traduire(document(**sans_millesime, remotes=[remote()]))

    assert traduction.ok
    assert any("schéma amont inconnu" in note for note in traduction.avertissements)


def test_un_schema_absent_ne_signale_rien() -> None:
    sans_schema = document(remotes=[remote()])
    del sans_schema["$schema"]
    traduction = traduire(sans_schema)

    assert traduction.ok
    assert traduction.avertissements == ()
    assert traduction.schema == ""


def test_un_avertissement_de_schema_survit_au_refus() -> None:
    inconnu = "https://static.modelcontextprotocol.io/schemas/2099-01-01/server.schema.json"
    traduction = traduire(document(**{"$schema": inconnu}))

    assert traduction.refus is not None
    assert any("schéma amont inconnu" in note for note in traduction.avertissements)


def test_un_champ_cherche_sous_son_alias_snake_case() -> None:
    """Chaque champ est cherché sous ses alias connus : camelCase d'aujourd'hui,
    snake_case d'hier."""
    forme = {
        "registry_type": "npm",
        "identifier": "@alice/mon-serveur-mcp",
        "version": "0.4.0",
        "runtime_hint": "npx",
        "package_arguments": [{"type": "positional", "value": "serve"}],
        "environment_variables": [{"name": "API_KEY", "is_secret": True, "is_required": True}],
    }
    traduction = traduire(document(packages=[forme], website_url="https://example.com"))

    entree = traduction.entree
    assert entree is not None
    assert entree.commande == "npx"
    assert entree.args[-1] == "serve"
    assert entree.mode_auth == "token_statique"
    assert entree.procedure_url == "https://example.com"


def test_un_remote_declare_sous_transport_type_est_reconnu() -> None:
    traduction = traduire(
        document(remotes=[{"transport_type": "SSE", "url": "https://mcp.example.com/sse"}])
    )

    assert traduction.ok
    assert traduction.entree is not None
    assert traduction.entree.transport == "sse"


def test_un_drapeau_non_booleen_retombe_sur_le_defaut() -> None:
    entete = {"name": "X-Api-Key", "isSecret": "oui", "isRequired": True}
    traduction = traduire(document(remotes=[remote(headers=[entete])]))

    entree = traduction.entree
    assert entree is not None
    assert entree.secrets[0].secret is False


def test_un_champ_qui_n_est_pas_une_chaine_ne_devient_pas_un_str_de_dict() -> None:
    traduction = traduire(
        document(description={"fr": "bonjour"}, repository="pas-un-objet", remotes=[remote()])
    )

    entree = traduction.entree
    assert entree is not None
    assert entree.description == ""
    assert entree.procedure_url == ""


def test_le_depot_sert_de_procedure_a_defaut_de_site() -> None:
    traduction = traduire(
        document(repository={"url": "https://github.com/alice/mon-serveur"}, remotes=[remote()])
    )

    entree = traduction.entree
    assert entree is not None
    assert entree.procedure_url == "https://github.com/alice/mon-serveur"


# ── L'enveloppe de listing est acceptée telle quelle ─────────────────────────


def test_une_enveloppe_de_listing_est_deballee() -> None:
    enveloppe = {"server": document(remotes=[remote()]), "_meta": {"status": STATUT_ACTIF}}
    traduction = traduire(enveloppe)

    assert traduction.ok
    assert traduction.entree is not None
    assert traduction.entree.id == "io-github-alice-mon-serveur"


def test_un_document_qui_porte_deja_un_name_n_est_pas_deballe() -> None:
    """Un `server` imbriqué ne prend pas le pas sur un document déjà déballé."""
    direct = document(remotes=[remote()], server={"name": "autre/chose"})
    traduction = traduire(direct)

    assert traduction.ok
    assert traduction.entree is not None
    assert traduction.entree.id == "io-github-alice-mon-serveur"


# ── Le verbe que la fédération appelle ───────────────────────────────────────


def test_une_entree_du_miroir_se_traduit_par_son_document() -> None:
    entree = EntreeAmont(
        nom="io.github.alice/mon-serveur",
        statut=STATUT_ACTIF,
        document=document(remotes=[remote()]),
    )
    traduction = traduire_entree(entree)

    assert traduction.ok
    assert traduction.entree is not None
    assert traduction.entree.id == "io-github-alice-mon-serveur"


def test_une_entree_supprimee_chez_l_amont_ne_se_traduit_pas() -> None:
    """Une entrée retirée pour spam ou malware n'a rien à faire dans une bibliothèque."""
    entree = EntreeAmont(
        nom="io.github.mallory/spam",
        statut=STATUT_SUPPRIME,
        document=document(name="io.github.mallory/spam", remotes=[remote()]),
    )
    traduction = traduire_entree(entree)

    assert not traduction.ok
    assert traduction.refus is not None
    assert traduction.refus.motif == MOTIF_SUPPRIMEE
    assert traduction.nom == "io.github.mallory/spam"


# ── Les formes publiques : ce qu'une API et un écran lisent ──────────────────


def test_les_motifs_sont_tous_declares_dans_la_table() -> None:
    """L'UI et les tests s'adossent à `MOTIFS` plutôt qu'à des chaînes recopiées."""
    attendus = {
        MOTIF_SANS_FORME,
        MOTIF_TRANSPORT,
        MOTIF_REGISTRE,
        MOTIF_ARGV,
        MOTIF_URL,
        MOTIF_VERSION,
        MOTIF_IDENTITE,
        MOTIF_SUPPRIMEE,
        MOTIF_VALIDATION,
    }
    assert set(MOTIFS) == attendus
    assert len(MOTIFS) == len(attendus)


def test_un_refus_se_reemet_en_dict() -> None:
    refus = Refus(MOTIF_URL, "variable en URL")
    assert refus.to_dict() == {"motif": MOTIF_URL, "cause": "variable en URL"}


def test_une_traduction_reussie_se_reemet_en_dict() -> None:
    traduction = traduire(document(remotes=[remote()]))
    rendu = traduction.to_dict()

    assert rendu["ok"] is True
    assert rendu["refus"] is None
    assert rendu["entree"] is not None
    assert rendu["entree"]["id"] == "io-github-alice-mon-serveur"
    assert rendu["nom"] == "io.github.alice/mon-serveur"
    assert rendu["schema"] == SCHEMA_CONNU
    assert rendu["avertissements"] == []


def test_un_refus_se_reemet_en_dict_sans_entree() -> None:
    traduction = traduire(document(remotes=[remote(type="websocket")]))
    rendu = traduction.to_dict()

    assert rendu["ok"] is False
    assert rendu["entree"] is None
    assert rendu["refus"]["motif"] == MOTIF_TRANSPORT


def test_le_resume_d_une_traduction_reussie_tient_en_une_ligne() -> None:
    traduction = traduire(document(remotes=[remote()]))

    assert traduction.resume() == (
        "io.github.alice/mon-serveur → io-github-alice-mon-serveur (http, sans_secret)"
    )


def test_le_resume_compte_les_avertissements_quand_il_y_en_a() -> None:
    entete = {"name": "X-Trace"}
    traduction = traduire(document(remotes=[remote(headers=[entete])]))

    assert "1 avertissement(s)" in traduction.resume()


def test_le_resume_d_un_refus_nomme_sa_cause() -> None:
    traduction = traduire(document(remotes=[remote(type="websocket")]))

    resume = traduction.resume()
    assert "refusée" in resume
    assert "websocket" in resume


def test_une_traduction_vide_ne_pretend_ni_reussir_ni_nommer_une_cause() -> None:
    vide = Traduction()

    assert vide.ok is False
    assert vide.resume() == "? refusée — cause inconnue"

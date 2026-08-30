"""Tests de la surcharge d'un agent du code (ticket #259).

Lot final « tests + doc » du parent #243 — couvre les tests différés du lot #259,
que `core/surcharges/README.md` annonçait renvoyés ici.

Le sujet est le **troisième état** du catalogue. « Du code » et « personnalisé » ne
suffisaient pas : changer le modèle d'un agent du code obligeait à le *dupliquer*,
c'est-à-dire à recopier son playbook pour ne toucher qu'un réglage, après quoi les
deux exemplaires divergent en silence. Trois choses se vérifient donc ici, et la
troisième est celle qui donne son sens aux deux autres :

① **le dépôt** (`SurchargeStore`) : un fichier par agent surchargé, la surcharge
   **vide qui ne se stocke pas** (annuler et n'avoir rien posé sont le *même* état),
   `herite()` qui nomme ce qui reste au code, le refus d'un nom hors `NOMS_DU_CODE`
   et le verrou de traversée de chemin ;
② **le catalogue effectif** (`catalogue(surcharges=…)`) : le seul endroit par lequel
   une surcharge atteint l'exécution — un dépôt vide rend exactement le catalogue
   d'avant, `MAESTRO_MODEL` prime sur le modèle et **pas** sur l'effort, et le
   `fournisseur` reste déclaratif ;
③ **les routes** (`PUT`/`DELETE /api/catalogue/{nom}/reglages`) : la fiche à trois
   états, `herite`/`reglages_du_code` qui disent d'où vient chaque valeur, et les
   **deux gestes voisins que rien ne doit confondre** — annuler une surcharge rend
   l'agent au code, supprimer un agent le fait disparaître.

Aucun appel réseau : dépôts sur répertoires temporaires, `TestClient` sur bus
mémoire. ⚠ Les quatre dépôts que l'app touche sont injectés — `create_app` retombe
sinon sur les vrais dossiers `core/` du dépôt, et une écriture de test y atterrirait
dans le versionné.
"""

import json

import pytest
from fastapi.testclient import TestClient

from maestro.agents.catalog import DEFAULT_AGENTS, MODELE_EXECUTANT_DEFAUT
from maestro.agents.mcp import McpStore
from maestro.agents.permissions import PermissionStore
from maestro.agents.store import (
    AGENT_SOURCE_DEFAUT,
    AGENT_SOURCE_PERSONNALISE,
    AGENT_SOURCE_SURCHARGE,
    NOMS_DU_CODE,
    REGLAGES_SURCHARGEABLES,
    AgentDefinition,
    AgentStore,
    SurchargeAgent,
    SurchargeStore,
    catalogue,
)
from maestro.config import load_settings
from maestro.controltower import InMemoryEventBus, create_app

#: Un agent du code, choisi une fois : les tests parlent de « developpeur » plutôt
#: que d'un nom au hasard, et il est vérifié présent avant qu'on s'appuie dessus.
AGENT_DU_CODE = "developpeur"


@pytest.fixture()
def depot(tmp_path):
    """Dépôt de surcharges vierge, sur répertoire temporaire."""
    return SurchargeStore(tmp_path / "surcharges")


@pytest.fixture()
def agents(tmp_path):
    """Dépôt d'agents personnalisés vierge (le catalogue part des seuls agents du code)."""
    return AgentStore(tmp_path / "agents")


@pytest.fixture()
def client(tmp_path, agents, depot):
    """App montée sur des dépôts temporaires — jamais sur les `core/` du dépôt."""
    app = create_app(
        bus=InMemoryEventBus(),
        agents_store=agents,
        surcharges=depot,
        permissions=PermissionStore(tmp_path / "permissions"),
        mcp=McpStore(tmp_path / "mcp"),
    )
    with TestClient(app) as client:
        yield client


def _agent_du_code(nom=AGENT_DU_CODE):
    """L'agent `nom` tel que le code le définit."""
    agent = next((a for a in DEFAULT_AGENTS if a.nom == nom), None)
    assert agent is not None, f"{nom!r} n'est plus un agent du code"
    return agent


def _definition(nom="redacteur", **overrides):
    """Définition d'agent personnalisé valide."""
    champs = {
        "nom": nom,
        "role": "Rédacteur technique",
        "competences": ("redaction",),
        "playbook": "Tu rédiges la documentation demandée.",
    }
    champs.update(overrides)
    return AgentDefinition(**champs)


def _du_catalogue(agents_effectifs, nom):
    """L'agent `nom` dans un catalogue effectif."""
    return next(a for a in agents_effectifs if a.nom == nom)


# --- ① Le dépôt : poser, hériter, annuler ----------------------------------------------


def test_un_depot_vide_rend_une_surcharge_vide_et_non_none(depot):
    # `lire` ne rend jamais None : « pas de fichier » et « rien de surchargé » sont
    # deux façons de dire la même chose, et chaque appelant n'a pas à les distinguer.
    surcharge = depot.lire(AGENT_DU_CODE)

    assert surcharge.nom == AGENT_DU_CODE
    assert surcharge.vide is True
    assert surcharge.modifie_le == ""
    assert depot.lister() == ()


def test_une_surcharge_posee_se_relit_et_porte_sa_date(depot):
    depot.ecrire(SurchargeAgent(nom=AGENT_DU_CODE, modele="claude-opus-5"))

    relue = depot.lire(AGENT_DU_CODE)

    assert relue.modele == "claude-opus-5"
    assert relue.vide is False
    assert relue.modifie_le  # posée par le dépôt à l'écriture (ISO 8601, UTC)
    assert [s.nom for s in depot.lister()] == [AGENT_DU_CODE]


def test_ce_qui_n_est_pas_surcharge_reste_herite_du_code(depot):
    ecrite = depot.ecrire(SurchargeAgent(nom=AGENT_DU_CODE, modele="claude-opus-5"))

    # `herite()` nomme ce qui reste au code, pour que l'UI le marque plutôt que de
    # le faire deviner — et n'offre « revenir au défaut » que sur le reste.
    assert ecrite.herite() == ("fournisseur", "effort")
    assert SurchargeAgent(nom=AGENT_DU_CODE).herite() == REGLAGES_SURCHARGEABLES
    complete = SurchargeAgent(
        nom=AGENT_DU_CODE, fournisseur="claude", modele="claude-opus-5", effort="xhigh"
    )
    assert complete.herite() == ()


def test_une_surcharge_vide_ne_se_stocke_pas(depot):
    depot.ecrire(SurchargeAgent(nom=AGENT_DU_CODE, modele="claude-opus-5"))

    rendue = depot.ecrire(SurchargeAgent(nom=AGENT_DU_CODE))

    # Sans cette règle, « du code, surchargé avec rien » existerait sur le disque à
    # côté de « du code » : deux états indiscernables à l'usage dont l'un
    # afficherait pourtant l'agent comme modifié.
    assert rendue.vide is True
    assert depot.lister() == ()
    assert not (depot.racine / f"{AGENT_DU_CODE}.json").exists()


def test_une_chaine_vide_vaut_herite_et_non_reglage_vide(depot):
    rendue = depot.ecrire(
        SurchargeAgent(nom=AGENT_DU_CODE, modele="  claude-opus-5  ", effort="   ")
    )

    assert rendue.modele == "claude-opus-5"
    assert rendue.effort is None
    assert "effort" in rendue.herite()


def test_annuler_une_surcharge_rend_l_agent_au_code(depot):
    depot.ecrire(SurchargeAgent(nom=AGENT_DU_CODE, modele="claude-opus-5"))

    assert depot.supprimer(AGENT_DU_CODE) is True
    assert depot.lire(AGENT_DU_CODE).vide is True
    # Idempotent : rien à annuler n'est pas une erreur.
    assert depot.supprimer(AGENT_DU_CODE) is False


def test_seuls_les_agents_du_code_se_surchargent(depot):
    # Sur un agent personnalisé, la surcharge ferait un second chemin d'écriture
    # vers les trois mêmes réglages, que sa définition porte déjà.
    with pytest.raises(ValueError, match="seuls les agents du code"):
        depot.ecrire(SurchargeAgent(nom="redacteur", modele="claude-opus-5"))
    # `orchestrateur` et `assistance` sont réservés mais ne sont pas au catalogue :
    # ils n'ont aucun réglage de modèle à surcharger.
    assert "orchestrateur" not in NOMS_DU_CODE
    with pytest.raises(ValueError, match="seuls les agents du code"):
        depot.ecrire(SurchargeAgent(nom="orchestrateur", modele="claude-opus-5"))


def test_le_nom_est_verrouille_pas_de_traversee_de_chemin(depot):
    with pytest.raises(ValueError, match="nom d'agent invalide"):
        depot.lire("../evasion")


def test_le_nom_du_fichier_fait_foi_sur_son_contenu(depot, tmp_path):
    depot.racine.mkdir(parents=True, exist_ok=True)
    (depot.racine / f"{AGENT_DU_CODE}.json").write_text(
        json.dumps({"nom": "usurpateur", "modele": "claude-opus-5"}),
        encoding="utf-8",
    )

    assert depot.lire(AGENT_DU_CODE).nom == AGENT_DU_CODE


def test_la_racine_est_configurable(monkeypatch, tmp_path):
    monkeypatch.setenv("MAESTRO_SURCHARGES_DIR", str(tmp_path / "ailleurs"))

    assert SurchargeStore.default(load_settings()).racine == tmp_path / "ailleurs"


def test_l_aller_retour_dict_preserve_la_surcharge():
    surcharge = SurchargeAgent(
        nom=AGENT_DU_CODE,
        fournisseur="claude",
        modele="claude-opus-5",
        effort="xhigh",
        modifie_le="2026-08-30T10:00:00+00:00",
    )

    assert SurchargeAgent.from_dict(surcharge.to_dict()) == surcharge


# --- ② Le catalogue effectif : le seul chemin vers l'exécution -------------------------


def test_un_depot_de_surcharges_vide_rend_le_catalogue_d_avant(agents, depot):
    assert catalogue(agents, surcharges=depot) == DEFAULT_AGENTS


def test_la_surcharge_recouvre_le_modele_de_l_agent_du_code(agents, depot):
    depot.ecrire(SurchargeAgent(nom=AGENT_DU_CODE, modele="claude-opus-5"))

    effectif = catalogue(agents, surcharges=depot)

    surcharge = _du_catalogue(effectif, AGENT_DU_CODE)
    assert surcharge.modele == "claude-opus-5"
    # Le reste continue de venir du code, et d'en suivre les évolutions : un
    # playbook amélioré dans `maestro.agents.catalog` vaut toujours pour lui.
    du_code = _agent_du_code()
    assert surcharge.role == du_code.role
    assert surcharge.prompt_systeme == du_code.prompt_systeme
    assert surcharge.competences == du_code.competences
    # Les autres agents du code ne bougent pas d'un caractère.
    assert _du_catalogue(effectif, "qa") == _agent_du_code("qa")


def test_l_effort_surcharge_atteint_l_execution(agents, depot):
    depot.ecrire(SurchargeAgent(nom=AGENT_DU_CODE, effort="xhigh"))

    effectif = catalogue(agents, surcharges=depot)

    surcharge = _du_catalogue(effectif, AGENT_DU_CODE)
    assert surcharge.effort == "xhigh"
    assert surcharge.modele == MODELE_EXECUTANT_DEFAUT  # non surchargé : celui du code


def test_le_fournisseur_reste_declaratif_et_n_entre_pas_dans_l_agent(agents, depot):
    depot.ecrire(SurchargeAgent(nom=AGENT_DU_CODE, fournisseur="openai"))

    effectif = catalogue(agents, surcharges=depot)

    # Le moteur exécute sur `MAESTRO_PROVIDER` : le champ est stocké et affiché,
    # il n'entre pas dans l'`Agent`, qui ne le porte pas.
    assert not hasattr(_du_catalogue(effectif, AGENT_DU_CODE), "fournisseur")
    assert _du_catalogue(effectif, AGENT_DU_CODE) == _agent_du_code()


def test_la_bascule_globale_prime_sur_le_modele_surcharge(agents, depot):
    # #69 : `MAESTRO_MODEL` est une bascule globale ; lui faire céder devant un
    # réglage par agent la viderait de son sens.
    depot.ecrire(SurchargeAgent(nom=AGENT_DU_CODE, modele="claude-opus-5", effort="xhigh"))

    effectif = catalogue(agents, modele="modele-unique", surcharges=depot)

    assert {a.modele for a in effectif} == {"modele-unique"}
    # …mais elle ne touche pas à l'effort : un effort n'est pas un modèle.
    assert _du_catalogue(effectif, AGENT_DU_CODE).effort == "xhigh"


def test_les_personnalises_suivent_les_agents_du_code_surcharges(agents, depot):
    agents.ecrire(_definition())
    depot.ecrire(SurchargeAgent(nom=AGENT_DU_CODE, modele="claude-opus-5"))

    effectif = catalogue(agents, surcharges=depot)

    # L'ordre du routage est préservé : les agents du code d'abord, surchargés ou non.
    assert [a.nom for a in effectif[: len(DEFAULT_AGENTS)]] == [
        a.nom for a in DEFAULT_AGENTS
    ]
    assert effectif[-1].nom == "redacteur"


# --- ③ Les routes : la fiche à trois états ---------------------------------------------


def test_un_agent_du_code_nait_a_l_etat_defaut(client):
    fiche = client.get(f"/api/catalogue/{AGENT_DU_CODE}").json()

    assert fiche["source"] == AGENT_SOURCE_DEFAUT
    assert sorted(fiche["herite"]) == sorted(REGLAGES_SURCHARGEABLES)
    assert fiche["modifie_le"] is None
    assert fiche["cree_le"] is None  # un agent du code n'est pas créé, il est là
    assert fiche["reglages_du_code"] == {
        "fournisseur": None,
        "modele": MODELE_EXECUTANT_DEFAUT,
        "effort": None,
    }


def test_surcharger_pose_le_troisieme_etat_sans_dupliquer_l_agent(client):
    reponse = client.put(
        f"/api/catalogue/{AGENT_DU_CODE}/reglages",
        json={"modele": "claude-opus-5", "effort": "xhigh"},
    )

    assert reponse.status_code == 200
    fiche = reponse.json()
    assert fiche["source"] == AGENT_SOURCE_SURCHARGE
    # Les valeurs servies sont les **effectives**…
    assert fiche["modele"] == "claude-opus-5" and fiche["effort"] == "xhigh"
    # …et trois clés disent d'où elles viennent.
    assert fiche["herite"] == ["fournisseur"]
    assert fiche["reglages_du_code"]["modele"] == MODELE_EXECUTANT_DEFAUT
    assert fiche["modifie_le"]
    # Le reste n'a pas bougé : c'est tout l'objet du ticket.
    du_code = _agent_du_code()
    assert fiche["role"] == du_code.role
    assert fiche["playbook"] == du_code.prompt_systeme


def test_ce_qui_n_est_pas_envoye_retourne_au_code(client):
    client.put(
        f"/api/catalogue/{AGENT_DU_CODE}/reglages",
        json={"modele": "claude-opus-5", "effort": "xhigh"},
    )

    # Remplacement intégral, jamais un diff : le second appel ne parle que du modèle.
    fiche = client.put(
        f"/api/catalogue/{AGENT_DU_CODE}/reglages", json={"modele": "claude-opus-5"}
    ).json()

    assert fiche["effort"] is None
    assert sorted(fiche["herite"]) == ["effort", "fournisseur"]


def test_poser_les_trois_a_null_annule_comme_le_delete(client):
    client.put(f"/api/catalogue/{AGENT_DU_CODE}/reglages", json={"modele": "claude-opus-5"})

    fiche = client.put(
        f"/api/catalogue/{AGENT_DU_CODE}/reglages",
        json={"fournisseur": None, "modele": None, "effort": None},
    ).json()

    assert fiche["source"] == AGENT_SOURCE_DEFAUT
    assert fiche["modele"] == MODELE_EXECUTANT_DEFAUT
    assert fiche["modifie_le"] is None


def test_annuler_est_idempotent(client):
    client.put(f"/api/catalogue/{AGENT_DU_CODE}/reglages", json={"effort": "xhigh"})

    premier = client.delete(f"/api/catalogue/{AGENT_DU_CODE}/reglages")
    second = client.delete(f"/api/catalogue/{AGENT_DU_CODE}/reglages")

    # Il n'y a rien à signaler à qui demande un état déjà atteint.
    assert premier.status_code == second.status_code == 200
    assert premier.json()["source"] == second.json()["source"] == AGENT_SOURCE_DEFAUT


def test_annuler_une_surcharge_n_est_pas_supprimer_l_agent(client):
    client.put(f"/api/catalogue/{AGENT_DU_CODE}/reglages", json={"effort": "xhigh"})

    client.delete(f"/api/catalogue/{AGENT_DU_CODE}/reglages")

    # Deux verbes voisins pour deux gestes que rien ne doit confondre : l'agent
    # reste au catalogue, il redevient simplement celui du code.
    noms = [fiche["nom"] for fiche in client.get("/api/catalogue").json()]
    assert AGENT_DU_CODE in noms
    # …et la suppression, elle, reste refusée sur un agent du code.
    assert client.delete(f"/api/catalogue/{AGENT_DU_CODE}").status_code == 403


def test_un_agent_personnalise_ne_se_surcharge_pas(client):
    client.post(
        "/api/catalogue",
        json={
            "nom": "redacteur",
            "role": "Rédacteur technique",
            "competences": ["redaction"],
            "playbook": "Tu rédiges.",
        },
    )

    reponse = client.put("/api/catalogue/redacteur/reglages", json={"effort": "xhigh"})

    assert reponse.status_code == 403
    # Le refus **nomme le chemin** : sa définition est son réglage.
    assert "PUT /api/catalogue/{nom}" in reponse.json()["detail"]
    assert client.get("/api/catalogue/redacteur").json()["source"] == AGENT_SOURCE_PERSONNALISE


def test_un_acteur_systeme_n_a_aucun_reglage_a_surcharger(client):
    reponse = client.put("/api/catalogue/orchestrateur/reglages", json={"effort": "xhigh"})

    assert reponse.status_code == 403
    assert "hors catalogue" in reponse.json()["detail"]


def test_un_agent_inconnu_rend_404(client):
    assert client.put("/api/catalogue/fantome/reglages", json={}).status_code == 404
    assert client.delete("/api/catalogue/fantome/reglages").status_code == 404


def test_supprimer_un_agent_personnalise_emporte_sa_surcharge(client, depot, agents):
    # Le nettoyage est celui de #72 : un homonyme recréé plus tard repart des
    # défauts. La surcharge d'un agent du code, elle, ne peut pas être concernée —
    # ce test garde que le geste vise bien le nom supprimé et rien d'autre.
    depot.ecrire(SurchargeAgent(nom=AGENT_DU_CODE, effort="xhigh"))
    client.post(
        "/api/catalogue",
        json={
            "nom": "redacteur",
            "role": "Rédacteur technique",
            "competences": ["redaction"],
            "playbook": "Tu rédiges.",
        },
    )

    assert client.delete("/api/catalogue/redacteur").status_code == 200

    assert depot.lire(AGENT_DU_CODE).effort == "xhigh"
    assert agents.lister() == ()


def test_la_surcharge_se_relit_sur_la_liste_comme_sur_la_fiche(client):
    client.put(f"/api/catalogue/{AGENT_DU_CODE}/reglages", json={"modele": "claude-opus-5"})

    liste = client.get("/api/catalogue").json()

    fiche = next(f for f in liste if f["nom"] == AGENT_DU_CODE)
    assert fiche["source"] == AGENT_SOURCE_SURCHARGE
    assert fiche["modele"] == "claude-opus-5"
    # Les deux formes de fiche portent les **mêmes champs** : un client n'a jamais
    # à deviner ses clés d'après la `source`.
    personnalise_ou_non = {"source", "herite", "reglages_du_code", "modele", "effort"}
    assert personnalise_ou_non <= set(fiche)

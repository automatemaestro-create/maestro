"""Tests de l'écriture des permissions d'un agent (ticket #262).

Lot final « tests + doc » du parent #243 — couvre les tests différés du lot #262,
que `apps/web/tests/permissions.test.ts` annonçait renvoyés ici pour tout ce qui
dépasse `lib/permissions`.

Le **versant lecture** de ce dépôt est couvert de bout en bout par
`tests/test_permissions.py` (sémantique, arbitrage, montage runtime, moteur, refus
au vol, journal) : ce fichier ne le rejoue pas. Il juge ce que #262 a ajouté —
**écrire** la politique depuis la fiche agent, là où il fallait éditer
`core/permissions/<agent>.json` à la main puis relancer :

① **le dépôt en écriture** (`PermissionStore.ecrire`) : aller-retour avec `lire`,
   la forme de sortie (`ask` **toujours** en objet), la validation **avant** que
   rien ne touche le disque, et la **réparation** — écrire par-dessus un fichier
   que `lire` refuse, sans quoi le geste manquerait précisément là où il est
   nécessaire ;
② **le point de passage unique** (`politique_validee`, `entree_valide`) : une seule
   définition de « politique admissible », donc pas de fichier qu'on écrirait et
   qu'on ne saurait plus relire — ni l'inverse ;
③ **la route** (`PUT /api/permissions/{agent}`) : 200 et politique relue avec la
   fiche, 404 hors catalogue **sans laisser de fichier orphelin**, 422 **motivé**
   avec le fichier d'avant intact, et les deux formes de `ask` acceptées ;
④ **les suggestions** (`permissions_outils`) : les trois origines servies *avec* la
   fiche, et ce qui serait refusé à l'écriture jamais proposé.

Aucun appel réseau : dépôts sur répertoires temporaires, `TestClient` sur bus
mémoire. ⚠ Les dépôts que l'app touche sont injectés — `create_app` retombe sinon
sur les vrais dossiers `core/` du dépôt, et un `PUT` de test y écrirait pour de bon.
"""

import json

import pytest
from fastapi.testclient import TestClient

from maestro.agents.mcp import McpStore
from maestro.agents.permissions import (
    PermissionStore,
    PolitiqueOutils,
    entree_valide,
    politique_validee,
)
from maestro.agents.store import AgentStore, SurchargeStore
from maestro.controltower import InMemoryEventBus, create_app
from maestro.decideur import Decideur

#: Un agent du code : la route refuse une politique orpheline, il faut donc un nom
#: que le catalogue connaît.
AGENT = "developpeur"


@pytest.fixture()
def store(tmp_path):
    """Dépôt de permissions vierge, sur répertoire temporaire."""
    return PermissionStore(tmp_path / "permissions")


@pytest.fixture()
def client(tmp_path, store):
    """App montée sur des dépôts temporaires — jamais sur les `core/` du dépôt."""
    app = create_app(
        bus=InMemoryEventBus(),
        agents_store=AgentStore(tmp_path / "agents"),
        surcharges=SurchargeStore(tmp_path / "surcharges"),
        permissions=store,
        mcp=McpStore(tmp_path / "mcp"),
    )
    with TestClient(app) as client:
        yield client


def _pose_fichier(store, agent, contenu):
    """Écrit un fichier de politique **à la main**, valide ou non.

    Volontairement hors de `ecrire` : c'est le seul moyen de fabriquer l'état que
    la réparation doit savoir remplacer.
    """
    store.racine.mkdir(parents=True, exist_ok=True)
    chemin = store.racine / f"{agent}.json"
    chemin.write_text(
        contenu if isinstance(contenu, str) else json.dumps(contenu, ensure_ascii=False),
        encoding="utf-8",
    )
    return chemin


# --- ① Le dépôt en écriture ------------------------------------------------------------


def test_l_aller_retour_ecrire_lire_preserve_la_politique(store):
    ecrite = store.ecrire(
        AGENT, {"allow": ["Read", "Write"], "ask": {"Bash": "humain"}, "deny": ["WebFetch"]}
    )

    relue = store.lire(AGENT)

    assert relue == ecrite
    assert relue.allow == ("Read", "Write")
    assert relue.deny == ("WebFetch",)
    (arbitree,) = relue.ask
    assert arbitree == "Bash" and arbitree.decideur is Decideur.HUMAIN


def test_ecrire_accepte_une_politique_construite_en_python(store):
    politique = PolitiqueOutils(allow=("Read",), ask=("Bash",), deny=())

    ecrite = store.ecrire(AGENT, politique)

    # Une chaîne nue vaut le cran par défaut : un appelant Python dit exactement
    # ce qu'il disait avant #586.
    (arbitree,) = ecrite.ask
    assert arbitree.decideur is Decideur.HUMAIN
    assert store.lire(AGENT) == politique


def test_le_fichier_ecrit_porte_ask_en_objet_et_les_trois_listes(store):
    store.ecrire(AGENT, {"allow": ["Read"], "ask": ["Bash"]})

    charge = json.loads((store.racine / f"{AGENT}.json").read_text(encoding="utf-8"))

    # Deux formes en sortie obligeraient chaque consommateur à savoir les
    # distinguer, pour n'économiser que quelques caractères sur le cas par défaut.
    assert charge == {"allow": ["Read"], "ask": {"Bash": "humain"}, "deny": []}


def test_le_fichier_ecrit_est_relisible_par_un_humain(store):
    store.ecrire(AGENT, {"deny": ["Bash"]})

    texte = (store.racine / f"{AGENT}.json").read_text(encoding="utf-8")

    # De la configuration versionnée avec le dépôt Git : indentée, terminée par
    # une fin de ligne, comme le dépôt MCP voisin.
    assert "\n  " in texte and texte.endswith("\n")


def test_une_ecriture_fautive_laisse_le_fichier_d_avant(store):
    store.ecrire(AGENT, {"allow": ["Read"]})

    with pytest.raises(ValueError, match="entrée deny"):
        store.ecrire(AGENT, {"deny": ["un outil avec des espaces"]})

    # Un dépôt qui n'écrirait qu'à moitié une politique de garde-fou serait pire
    # que pas d'écriture du tout.
    assert store.lire(AGENT).allow == ("Read",)


def test_un_decideur_inconnu_est_refuse_a_l_ecriture_aussi(store):
    with pytest.raises(ValueError, match="décideur"):
        store.ecrire(AGENT, {"ask": {"Bash": "orchestrateur"}})

    assert store.agents() == ()


def test_ecrire_ne_relit_pas_ce_qu_il_remplace(store):
    # La réparation depuis l'écran : un aller-retour lecture → écriture échouerait
    # précisément sur le fichier qu'on vient corriger.
    _pose_fichier(store, AGENT, "{ ceci n'est pas du JSON")
    with pytest.raises(ValueError, match="illisible"):
        store.lire(AGENT)

    store.ecrire(AGENT, {"allow": ["Read"], "ask": {}, "deny": []})

    assert store.lire(AGENT).allow == ("Read",)


def test_une_politique_invalide_mais_lisible_se_repare_aussi(store):
    _pose_fichier(store, AGENT, {"allow": "Read"})  # une chaîne, pas une liste
    with pytest.raises(ValueError, match="allow"):
        store.lire(AGENT)

    store.ecrire(AGENT, {"allow": ["Read"]})

    assert store.lire(AGENT).allow == ("Read",)


def test_l_ecriture_ne_laisse_aucun_fichier_temporaire(store):
    store.ecrire(AGENT, {"allow": ["Read"]})

    assert sorted(p.name for p in store.racine.iterdir()) == [f"{AGENT}.json"]


def test_le_nom_est_verrouille_a_l_ecriture_comme_a_la_lecture(store):
    with pytest.raises(ValueError, match="nom d'agent invalide"):
        store.ecrire("../evasion", {"allow": ["Read"]})


def test_une_politique_ecrite_entre_dans_la_liste_des_agents(store):
    assert store.agents() == ()

    store.ecrire(AGENT, {"deny": ["Bash"]})

    assert store.agents() == (AGENT,)


# --- ② Le point de passage unique ------------------------------------------------------


def test_les_deux_sens_refusent_la_meme_chose(store):
    # Une seule définition de « politique admissible » : pas de fichier qu'on
    # écrirait et qu'on ne saurait plus relire, ni d'écran qui refuserait ce que
    # le moteur accepte déjà.
    fautive = {"allow": ["mauvais outil"]}
    _pose_fichier(store, "qa", fautive)

    with pytest.raises(ValueError) as a_la_lecture:
        store.lire("qa")
    with pytest.raises(ValueError) as a_l_ecriture:
        store.ecrire("qa", fautive)

    assert "entrée allow" in str(a_la_lecture.value)
    assert "entrée allow" in str(a_l_ecriture.value)


def test_le_motif_nomme_le_fichier_a_la_lecture_et_pas_a_l_ecriture():
    # À la lecture `source` aide à trouver quoi corriger ; à l'écriture il n'existe
    # pas encore — le corps refusé vient de l'appel, pas du disque.
    with pytest.raises(ValueError, match=r"qa\.json"):
        politique_validee(["Read"], agent="qa", source="qa.json")
    with pytest.raises(ValueError) as sans_source:
        politique_validee(["Read"], agent="qa")
    assert ".json" not in str(sans_source.value)


def test_une_politique_qui_n_est_pas_un_objet_est_refusee():
    with pytest.raises(ValueError, match="objet"):
        politique_validee(["Read"], agent="qa")


def test_le_refus_d_une_entree_nomme_l_agent_et_la_liste_des_deux_cotes():
    # ⚠ `source` ne porte que sur le refus de **forme globale** ci-dessus : le refus
    # d'une entrée nomme l'agent et sa liste, jamais le fichier. Les deux sens
    # rendent donc ici le même message au caractère près — et c'est le but.
    with pytest.raises(ValueError) as depuis_un_fichier:
        politique_validee({"allow": [1]}, agent="qa", source="qa.json")
    with pytest.raises(ValueError) as depuis_un_appel:
        politique_validee({"allow": [1]}, agent="qa")

    assert str(depuis_un_fichier.value) == str(depuis_un_appel.value)
    assert "entrée allow" in str(depuis_un_appel.value)


def test_entree_valide_pose_la_meme_question_que_le_refus():
    assert entree_valide("Bash") is True
    assert entree_valide("mcp__slack") is True
    assert entree_valide("mcp__slack__send_message") is True
    assert entree_valide("") is False
    assert entree_valide("mon outil") is False
    # Le pendant en question de ce que l'écriture refuse : ce qui suggère des
    # entrées doit pouvoir écarter ce que l'écriture refuserait.
    for entree in ("", "mon outil", "mcp__mauvais serveur"):
        assert entree_valide(entree) is False
        with pytest.raises(ValueError):
            politique_validee({"allow": [entree]}, agent="qa")


# --- ③ La route : régler les permissions depuis la fiche -------------------------------


def test_la_route_ecrit_la_politique_et_la_rend(client, store):
    reponse = client.put(
        f"/api/permissions/{AGENT}",
        json={"allow": ["Read"], "ask": {"Bash": "auto"}, "deny": ["WebFetch"]},
    )

    assert reponse.status_code == 200
    assert reponse.json() == {
        "agent": AGENT,
        "permissions": {"allow": ["Read"], "ask": {"Bash": "auto"}, "deny": ["WebFetch"]},
    }
    assert store.lire(AGENT).deny == ("WebFetch",)


def test_la_politique_ecrite_se_relit_sur_la_fiche(client):
    client.put(f"/api/permissions/{AGENT}", json={"deny": ["Bash"]})

    fiche = client.get(f"/api/catalogue/{AGENT}").json()

    assert fiche["permissions"] == {"allow": [], "ask": {}, "deny": ["Bash"]}
    assert fiche["permissions_erreur"] is None


def test_la_route_remplace_integralement(client):
    client.put(f"/api/permissions/{AGENT}", json={"allow": ["Read"], "deny": ["Bash"]})

    charge = client.put(f"/api/permissions/{AGENT}", json={"allow": ["Write"]}).json()

    assert charge["permissions"] == {"allow": ["Write"], "ask": {}, "deny": []}


def test_ask_est_acceptee_sous_ses_deux_formes(client):
    en_liste = client.put(f"/api/permissions/{AGENT}", json={"ask": ["Bash"]}).json()
    en_objet = client.put(f"/api/permissions/{AGENT}", json={"ask": {"Bash": "auto"}}).json()

    # En liste, l'entrée retombe sur le cran par défaut — le régime d'avant #586.
    assert en_liste["permissions"]["ask"] == {"Bash": "humain"}
    assert en_objet["permissions"]["ask"] == {"Bash": "auto"}


def test_un_agent_hors_catalogue_rend_404_sans_fichier_orphelin(client, store):
    reponse = client.put("/api/permissions/fantome", json={"allow": ["Read"]})

    assert reponse.status_code == 404
    # Une politique orpheline ne serait jamais appliquée : on n'en écrit pas.
    assert store.agents() == ()


def test_une_entree_mal_formee_rend_422_motive_et_laisse_le_fichier(client, store):
    client.put(f"/api/permissions/{AGENT}", json={"allow": ["Read"]})

    reponse = client.put(f"/api/permissions/{AGENT}", json={"deny": ["mon outil"]})

    assert reponse.status_code == 422
    # C'est ce motif que l'écran affiche : il nomme la liste et l'entrée en faute.
    detail = reponse.json()["detail"]
    assert "deny" in detail and "mon outil" in detail
    assert store.lire(AGENT).allow == ("Read",)


def test_un_decideur_inconnu_rend_422_en_nommant_les_crans_admis(client):
    reponse = client.put(f"/api/permissions/{AGENT}", json={"ask": {"Bash": "personne"}})

    assert reponse.status_code == 422
    detail = reponse.json()["detail"]
    assert "personne" in detail
    # Le message se compose de `tuple(Decideur)` : il ne peut pas nommer un cran
    # qui n'existe plus.
    for cran in Decideur:
        assert f"« {cran} »" in detail


def test_une_politique_illisible_se_repare_depuis_l_ecran(client, store):
    _pose_fichier(store, AGENT, "{ pas du JSON")
    fiche = client.get(f"/api/catalogue/{AGENT}").json()
    assert fiche["permissions"] is None and "illisible" in fiche["permissions_erreur"]

    reponse = client.put(f"/api/permissions/{AGENT}", json={"allow": ["Read"]})

    assert reponse.status_code == 200
    assert client.get(f"/api/catalogue/{AGENT}").json()["permissions_erreur"] is None


def test_un_agent_personnalise_regle_aussi_ses_permissions(client):
    client.post(
        "/api/catalogue",
        json={
            "nom": "redacteur",
            "role": "Rédacteur technique",
            "competences": ["redaction"],
            "playbook": "Tu rédiges.",
        },
    )

    reponse = client.put("/api/permissions/redacteur", json={"deny": ["Bash"]})

    assert reponse.status_code == 200
    assert client.get("/api/catalogue/redacteur").json()["permissions"]["deny"] == ["Bash"]


# --- ④ Les suggestions servies avec la fiche -------------------------------------------


def test_la_fiche_suggere_ce_que_l_agent_peut_appeler(client):
    fiche = client.get(f"/api/catalogue/{AGENT}").json()

    outils = fiche["permissions_outils"]
    assert outils, "la fiche doit suggérer des entrées, pas les faire retrouver ailleurs"
    origines = {outil["origine"] for outil in outils}
    # Deux origines au moins sans aucun serveur MCP monté : les outils intégrés du
    # profil, et les verbes du serveur in-process « maestro ».
    assert {"integre", "maestro"} <= origines
    assert all(outil["nom"] and outil["libelle"] for outil in outils)


def test_aucune_suggestion_ne_serait_refusee_a_l_ecriture(client):
    fiche = client.get(f"/api/catalogue/{AGENT}").json()

    noms = [outil["nom"] for outil in fiche["permissions_outils"]]

    # Proposer une entrée impossible à écrire serait pire que de n'en proposer
    # aucune : chaque suggestion doit passer l'écriture telle quelle.
    assert all(entree_valide(nom) for nom in noms)
    assert client.put(f"/api/permissions/{AGENT}", json={"allow": noms}).status_code == 200

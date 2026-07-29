"""Modèle de données MCP : pool projet + activation par agent (ticket #130, parent #129).

Ces tests couvrent le **modèle de données** introduit par le lot 1/5 — le socle
que les lots suivants (registre #131, secrets chiffrés #132, UI #133) réutilisent :

① **pool projet** (`pool.json`) : lisible et **écrivable** via le store, chaque
   entrée = un `id` d'intégration + une déclaration `ServeurMcp` (secrets par
   `${VAR}`). Validé à la lecture comme le socle (#104) : id ou déclaration
   invalide, ids en double, JSON illisible, forme inattendue — refusés avec leur
   cause. La forme **stockée** garde les références `${VAR}` intactes (là où la
   forme publique `to_dict` masque les littéraux) ;
② **activation par agent** (`activations.json`) : liste d'ids par agent,
   écrivable (fusion non destructive des autres agents, liste vide = retrait),
   ordre préservé, doublons écartés, agent/id verrouillés en slug ;
③ **composition `lire(agent)` = pool ∩ activations** : sans activation, la
   déclaration héritée seule (rétro-compat #104, le pool n'est pas lu) ; avec,
   l'union héritée + intégrations activées, l'héritée **autoritaire** en cas de
   collision de `serveur.nom` ; une activation vers une intégration absente du
   pool est un échec propre à la lecture ;
④ **migration** héritée → pool : `composer_migration` mutualise les serveurs
   identiques partagés par plusieurs agents en une seule intégration, `migrer`
   la persiste (option : retrait des fichiers hérités) — la rétro-compat du
   critère #2, réellement couverte.

Le volet écriture/activation depuis la Control Tower (UI) et sa doc relèvent des
lots #133/#134 : ici, le contrat backend seul, sans réseau ni UI.
"""

import json

import pytest

from maestro.agents.mcp import IntegrationMcp, McpStore, ServeurMcp


@pytest.fixture()
def store(tmp_path):
    """Dépôt MCP vierge sur répertoire temporaire (ni pool, ni activation, ni fichier)."""
    return McpStore(tmp_path / "mcp")


def _integ_stdio(id_: str = "gitlab", nom: str = "tickets", **surcharges) -> IntegrationMcp:
    """Une intégration stdio du pool : commande locale + secret par référence."""
    champs = {
        "nom": nom,
        "type": "stdio",
        "commande": "npx",
        "args": ("-y", "@zereight/mcp-gitlab"),
        "env": {"GITLAB_TOKEN": "${GITLAB_TOKEN}"},
    }
    champs.update(surcharges)
    return IntegrationMcp(id=id_, serveur=ServeurMcp(**champs))


def _integ_http(id_: str = "figma", nom: str = "figma-officiel") -> IntegrationMcp:
    """Une intégration http distante et optionnelle (le patron Figma #128)."""
    return IntegrationMcp(
        id=id_,
        serveur=ServeurMcp(
            nom=nom,
            type="http",
            url="https://mcp.figma.com/mcp",
            headers={"Authorization": "Bearer ${FIGMA_OAUTH_TOKEN}"},
            optionnel=True,
        ),
    )


def _ecrire_fichier_agent(racine, agent, serveurs):
    """Écrit une déclaration MCP **héritée** (`<agent>.json`, modèle #104)."""
    racine.mkdir(parents=True, exist_ok=True)
    (racine / f"{agent}.json").write_text(
        json.dumps({"serveurs": serveurs}, ensure_ascii=False), encoding="utf-8"
    )


# --- ① Pool projet : écriture, lecture, validation --------------------------------------


def test_pool_ecrit_puis_relu_a_l_identique(store):
    ecrites = store.ecrire_pool([_integ_http(), _integ_stdio()])
    relues = store.pool()

    assert tuple(i.id for i in ecrites) == ("figma", "gitlab")
    assert tuple(i.id for i in relues) == ("figma", "gitlab")
    (figma, gitlab) = relues
    assert figma.serveur.nom == "figma-officiel"
    assert gitlab.serveur.env == {"GITLAB_TOKEN": "${GITLAB_TOKEN}"}


def test_pool_absent_rend_vide(store):
    assert store.pool() == ()


def test_la_forme_stockee_garde_les_references_intactes(store):
    # Écriture : les ${VAR} doivent rester résolvables (jamais masquées comme dans
    # la forme publique to_dict), sans quoi le secret deviendrait irrécupérable.
    store.ecrire_pool([_integ_http()])
    brut = (store.racine / "pool.json").read_text(encoding="utf-8")

    assert "${FIGMA_OAUTH_TOKEN}" in brut
    assert "•••" not in brut


def test_pool_id_invalide_refuse(store):
    with pytest.raises(ValueError, match="id d'intégration MCP invalide"):
        store.ecrire_pool([_integ_http(id_="Figma Officiel !")])


def test_pool_declaration_invalide_refuse_avec_sa_cause(store):
    # La validation à la lecture du socle (#104) s'applique au serveur d'une
    # intégration : une commande locale ne porte pas d'URL.
    mauvaise = IntegrationMcp(
        id="x", serveur=ServeurMcp(nom="x", type="stdio", commande="npx", url="https://y.test")
    )
    with pytest.raises(ValueError, match="url/headers interdits"):
        store.ecrire_pool([mauvaise])


def test_pool_ids_en_double_refuses(store):
    with pytest.raises(ValueError, match="intégrations en double : gitlab"):
        store.ecrire_pool([_integ_stdio(), _integ_stdio(nom="autre")])


def test_ecrire_pool_invalide_n_ecrit_rien(store):
    store.ecrire_pool([_integ_stdio()])
    with pytest.raises(ValueError):
        store.ecrire_pool([_integ_http(id_="mauvais id")])
    # Le pool précédent est intact : l'écriture invalide n'a rien touché.
    assert tuple(i.id for i in store.pool()) == ("gitlab",)


def test_pool_json_illisible_refuse(store):
    store.racine.mkdir(parents=True)
    (store.racine / "pool.json").write_text("{pas du json", encoding="utf-8")
    with pytest.raises(ValueError, match="pool MCP illisible"):
        store.pool()


def test_pool_forme_inattendue_refusee(store):
    store.racine.mkdir(parents=True)
    (store.racine / "pool.json").write_text('["pas", "un", "objet"]', encoding="utf-8")
    with pytest.raises(ValueError, match="integrations"):
        store.pool()


def test_pool_relu_refuse_les_ids_en_double(store):
    # Validation à la lecture d'un `pool.json` édité à la main : deux intégrations
    # de même id sont refusées (le chemin lecture, distinct de `ecrire_pool`).
    store.racine.mkdir(parents=True)
    (store.racine / "pool.json").write_text(
        json.dumps(
            {
                "integrations": [
                    {"id": "gitlab", "serveur": {"nom": "a", "type": "stdio", "commande": "x"}},
                    {"id": "gitlab", "serveur": {"nom": "b", "type": "stdio", "commande": "y"}},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="intégrations en double : gitlab"):
        store.pool()


def test_integration_to_dict_masque_les_litteraux(store):
    # Forme publique (API/UI, lot #133) : les littéraux d'`env`/`headers` sont
    # masqués, les références ${VAR} restent visibles — comme pour un serveur.
    publie = _integ_stdio(env={"GITLAB_TOKEN": "${GITLAB_TOKEN}", "EN_CLAIR": "s3cret"}).to_dict()
    assert publie["id"] == "gitlab"
    assert publie["serveur"]["env"]["GITLAB_TOKEN"] == "${GITLAB_TOKEN}"
    assert publie["serveur"]["env"]["EN_CLAIR"] == "•••"


# --- ② Activation par agent : écriture, lecture, validation -----------------------------


def test_activations_ecrites_puis_relues(store):
    store.ecrire_activations("qa", ["gitlab", "figma"])
    assert store.activations("qa") == ("gitlab", "figma")
    assert store.activations("devops") == ()


def test_ecrire_activations_preserve_les_autres_agents(store):
    store.ecrire_activations("qa", ["gitlab"])
    store.ecrire_activations("designer", ["figma"])
    # La seconde écriture ne doit pas effacer la première (fusion non destructive).
    assert store.activations("qa") == ("gitlab",)
    assert store.activations("designer") == ("figma",)


def test_activation_vide_retire_l_agent(store):
    store.ecrire_activations("qa", ["gitlab"])
    store.ecrire_activations("qa", [])
    assert store.activations("qa") == ()
    table = json.loads((store.racine / "activations.json").read_text(encoding="utf-8"))
    assert "qa" not in table


def test_activations_dedoublonnees_ordre_preserve(store):
    store.ecrire_activations("qa", ["figma", "gitlab", "figma"])
    assert store.activations("qa") == ("figma", "gitlab")


def test_ecrire_activations_nom_agent_hors_slug_refuse(store):
    with pytest.raises(ValueError, match="nom d'agent invalide"):
        store.ecrire_activations("../evasion", ["gitlab"])


def test_ecrire_activations_id_hors_slug_refuse(store):
    with pytest.raises(ValueError, match="id d'intégration invalide"):
        store.ecrire_activations("qa", ["pas un id"])


def test_activations_json_illisible_refuse(store):
    store.racine.mkdir(parents=True)
    (store.racine / "activations.json").write_text("{pas du json", encoding="utf-8")
    with pytest.raises(ValueError, match="activations MCP illisibles"):
        store.activations("qa")


def test_activations_forme_inattendue_refusee(store):
    store.racine.mkdir(parents=True)
    (store.racine / "activations.json").write_text('["pas", "un", "objet"]', encoding="utf-8")
    with pytest.raises(ValueError, match="activations MCP invalides"):
        store.activations("qa")


def _ecrire_activations_brutes(racine, table):
    """Écrit une `activations.json` **brute** (contourne la validation d'écriture)."""
    racine.mkdir(parents=True, exist_ok=True)
    (racine / "activations.json").write_text(
        json.dumps(table, ensure_ascii=False), encoding="utf-8"
    )


def test_activations_relues_refusent_un_nom_d_agent_hors_slug(store):
    # Validation à la lecture d'une table éditée à la main : agent hors slug.
    _ecrire_activations_brutes(store.racine, {"Pas Un Agent": ["gitlab"]})
    with pytest.raises(ValueError, match="nom d'agent"):
        store.activations("qa")


def test_activations_relues_refusent_une_valeur_non_liste(store):
    _ecrire_activations_brutes(store.racine, {"qa": "gitlab"})
    with pytest.raises(ValueError, match="liste d'ids"):
        store.activations("qa")


def test_activations_relues_refusent_un_id_hors_slug(store):
    _ecrire_activations_brutes(store.racine, {"qa": ["pas un id"]})
    with pytest.raises(ValueError, match="id d'intégration"):
        store.activations("qa")


def test_depot_absent_ni_agents_ni_migration(store):
    # Racine jamais créée : les lectures restent vides, sans erreur.
    assert store.agents() == ()
    assert store.composer_migration() == ((), {})


# --- ③ Composition lire(agent) = pool ∩ activations -------------------------------------


def test_sans_pool_ni_activation_lire_rend_la_declaration_heritee(store):
    # Rétro-compat #104 : le comportement d'origine est strictement préservé.
    _ecrire_fichier_agent(
        store.racine, "qa", [{"nom": "tickets", "type": "stdio", "commande": "npx"}]
    )
    (serveur,) = store.lire("qa")
    assert serveur.nom == "tickets"


def test_lire_compose_les_integrations_activees(store):
    # Agent sans fichier hérité : lire = pool ∩ activations, pur.
    store.ecrire_pool([_integ_http(), _integ_stdio()])
    store.ecrire_activations("qa", ["gitlab", "figma"])
    assert tuple(s.nom for s in store.lire("qa")) == ("tickets", "figma-officiel")


def test_lire_n_active_que_les_integrations_de_l_agent(store):
    store.ecrire_pool([_integ_http(), _integ_stdio()])
    store.ecrire_activations("designer", ["figma"])
    assert tuple(s.nom for s in store.lire("designer")) == ("figma-officiel",)
    # Un agent sans activation ne reçoit rien du pool.
    assert store.lire("qa") == ()


def test_lire_union_heritee_puis_pool(store):
    # L'héritée d'abord (ordre historique), puis les intégrations activées.
    _ecrire_fichier_agent(
        store.racine, "qa", [{"nom": "local", "type": "stdio", "commande": "python"}]
    )
    store.ecrire_pool([_integ_http()])
    store.ecrire_activations("qa", ["figma"])
    assert tuple(s.nom for s in store.lire("qa")) == ("local", "figma-officiel")


def test_collision_de_nom_l_heritee_l_emporte(store):
    # Une intégration activée dont le serveur porte le même nom qu'un serveur
    # hérité est écartée : la source qu'un run existant utilise reste autoritaire.
    _ecrire_fichier_agent(
        store.racine, "qa", [{"nom": "tickets", "type": "stdio", "commande": "local-legacy"}]
    )
    store.ecrire_pool([_integ_stdio()])  # serveur nommé "tickets", commande "npx"
    store.ecrire_activations("qa", ["gitlab"])
    (serveur,) = store.lire("qa")
    assert (serveur.nom, serveur.commande) == ("tickets", "local-legacy")


def test_activation_vers_integration_absente_du_pool_echoue(store):
    store.ecrire_pool([_integ_stdio()])
    store.ecrire_activations("qa", ["inexistante"])
    with pytest.raises(ValueError, match="absente.*du pool : inexistante"):
        store.lire("qa")


def test_lire_nom_reserve_refuse(store):
    # `pool`/`activations` ne sont pas des agents : refus, jamais une lecture du
    # fichier réservé comme s'il déclarait des serveurs.
    with pytest.raises(ValueError, match="nom d'agent invalide"):
        store.lire("pool")


# --- agents() : union fichiers hérités + activations, fichiers réservés exclus -----------


def test_agents_union_fichiers_et_activations_sans_fichiers_reserves(store):
    _ecrire_fichier_agent(store.racine, "devops", [{"nom": "s", "type": "stdio", "commande": "x"}])
    store.ecrire_pool([_integ_stdio()])
    store.ecrire_activations("qa", ["gitlab"])
    # `pool.json` / `activations.json` ne comptent pas comme des agents.
    assert store.agents() == ("devops", "qa")


# --- ④ Migration héritée → pool ---------------------------------------------------------


def test_composer_migration_mutualise_les_serveurs_partages(store):
    # Deux agents déclarent le MÊME serveur (déclaration identique) : la migration
    # n'en fait qu'une intégration, activée des deux côtés — l'objectif du pool.
    partage = {"nom": "slack", "type": "stdio", "commande": "npx", "env": {"T": "${SLACK}"}}
    _ecrire_fichier_agent(store.racine, "devops", [partage])
    _ecrire_fichier_agent(store.racine, "qa", [partage])

    pool, activations = store.composer_migration()

    assert tuple(i.id for i in pool) == ("slack",)
    assert activations == {"devops": ("slack",), "qa": ("slack",)}


def test_composer_migration_suffixe_les_noms_en_collision(store):
    # Deux serveurs de même nom mais déclarations DIFFÉRENTES gardent chacun leur
    # intégration : id dérivé du nom, suffixé pour rester unique.
    _ecrire_fichier_agent(
        store.racine, "devops", [{"nom": "slack", "type": "stdio", "commande": "a"}]
    )
    _ecrire_fichier_agent(
        store.racine, "qa", [{"nom": "slack", "type": "stdio", "commande": "b"}]
    )
    # Un troisième « slack » distinct force le suffixe à s'incrémenter (slack-3).
    _ecrire_fichier_agent(
        store.racine, "bdd", [{"nom": "slack", "type": "stdio", "commande": "c"}]
    )

    pool, activations = store.composer_migration()

    assert sorted(i.id for i in pool) == ["slack", "slack-2", "slack-3"]
    assert len({activations["devops"], activations["qa"], activations["bdd"]}) == 3


def test_migrer_persiste_et_conserve_la_lecture(store):
    _ecrire_fichier_agent(
        store.racine, "qa", [{"nom": "slack", "type": "stdio", "commande": "npx"}]
    )
    avant = tuple(s.nom for s in store.lire("qa"))

    store.migrer()

    # Le pool et les activations sont persistés, la lecture est inchangée
    # (l'héritée reste, autoritaire, tant que son fichier n'est pas retiré).
    assert store.pool()
    assert store.activations("qa") == ("slack",)
    assert tuple(s.nom for s in store.lire("qa")) == avant


def test_migrer_retirer_fichiers_bascule_sur_le_pool(store):
    _ecrire_fichier_agent(
        store.racine, "qa", [{"nom": "slack", "type": "stdio", "commande": "npx"}]
    )
    avant = tuple(s.nom for s in store.lire("qa"))

    store.migrer(retirer_fichiers=True)

    # Le fichier hérité est retiré : la lecture passe désormais par le pool seul,
    # à l'identique — la bascule est transparente.
    assert not (store.racine / "qa.json").exists()
    assert tuple(s.nom for s in store.lire("qa")) == avant

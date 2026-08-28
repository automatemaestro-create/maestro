"""Tests du canal de chat utilisateur ↔ agent — fil persisté et flux d'un envoi (ticket #84).

Aucun réseau ni fournisseur : le fil vit dans un répertoire temporaire, la
messagerie (#44) et le bus (#46) tournent en mémoire, le répondeur est scripté
ou factice. Couvre le critère « canal backend » du ticket #83 (tests différés
du parent #82) :

① persistance du fil (`ChatStore`) : append-only relu dans l'ordre, un fichier
   JSONL par agent, fil vide tant que l'agent n'a jamais été contacté, agents
   listés triés — et le garde-fou : un nom hors slug est refusé sans toucher
   au disque (jamais de traversée de chemin depuis un nom venu de l'API) ;
② production de la réponse : `RepondeurScripte` (déterministe, zéro modèle) et
   `RepondeurModele` — transcription du fil en prompt, playbook **courant**
   (#76 : la version éditée du stockage, sinon le prompt du code) complété du
   cadre de conversation en prompt système, modèle de l'agent transmis au
   fournisseur ;
③ flux d'un envoi (`ServiceChat`) : persistance + messagerie (requête vers la
   boîte de l'agent, réponse en retour) + diffusion `chat.message` pour le
   message utilisateur comme pour la réponse — l'état persisté **avant** la
   diffusion — et les refus : message vide (rien n'est écrit), répondeur en
   échec ou réponse vide (`ReponseIndisponible`, le message utilisateur reste
   acquis : relancer ne perd pas le fil) ;
④ **les sources qu'un message embarque** (#482, lot 1 de #481 — couverture due au
   lot final #485) : le dépôt (un fichier voyage par son identifiant de
   téléversement, ses octets sont rattachés à l'emplacement d'ingestion **du
   message**), les **plafonds refusés** (le refus tombe avant toute écriture —
   ni fil, ni lettre, ni événement) et le **rapport de lecture** (ce qui a été lu,
   ce qui a été ignoré, ce que le REST rend et ce que seul le stockage garde) ;
⑤ **l'arrêt d'une génération en vol** (#695) — la logique critique du lot qui
   consomme le flux ; le reste de la couverture du chantier « chat global pleine
   page » a été soldé par le lot 8 et vit dans `tests/test_chat_pleine_page.py`
   (le flux qui porte ses sources, les incréments d'un répondeur modèle, les
   conversations).
   Trois choses, et elles se cassent en silence : la trame `interrompu` clôt le
   flux, **ce qui a été reçu est persisté** comme réponse (« ce qui a déjà été
   reçu reste au fil » n'est pas un état d'écran), et « rien à arrêter » se
   distingue d'un arrêt — un identifiant inconnu comme un échange qui vient de
   se terminer rendent `False` au lieu d'annuler autre chose.

L'exposition HTTP du canal (REST `/api/chat` + WebSocket `chat.message`) est
couverte dans `tests/test_controltower.py` (section ⑧), sources comprises.
"""

import asyncio
import io
import json
from pathlib import Path

import pytest

from maestro.agents.catalog import Agent
from maestro.agents.playbooks import PlaybookStore
from maestro.config import Settings
from maestro.controltower.chat import (
    AUTEUR_AGENT,
    AUTEUR_UTILISATEUR,
    FRAGMENT_CHAT_DELTA,
    FRAGMENT_CHAT_FIN,
    FRAGMENT_CHAT_INTERROMPU,
    UTILISATEUR,
    ChatStore,
    MessageChat,
    Redaction,
    RepondeurChat,
    RepondeurModele,
    RepondeurScripte,
    ReponseChat,
    ReponseIndisponible,
    ServiceChat,
)
from maestro.controltower.events import EVENEMENT_CHAT_MESSAGE, InMemoryEventBus
from maestro.engine.guardrails import GardeFousIngestion
from maestro.messaging import MESSAGE_REPONSE, MESSAGE_REQUETE, InMemoryMailbox
from maestro.providers.base import ModelProvider
from maestro.sources import (
    DepotTeleversements,
    SourceRefusee,
    racine_ingestion,
)


def _agent(nom="qa", role="QA / Testeur", competences=("qualite", "tests"),
           modele="claude-opus-4-8", prompt="Tu vérifies la qualité des livrables."):
    """Fiche catalogue factice : de quoi répondre sans charger le vrai catalogue."""
    return Agent(nom=nom, role=role, competences=frozenset(competences),
                 modele=modele, prompt_systeme=prompt)


def _message(agent="qa", auteur=UTILISATEUR, contenu="Bonjour"):
    return MessageChat(agent=agent, auteur=auteur, contenu=contenu)


# ------------------------------------------------- ① Persistance du fil


def test_le_fil_d_un_agent_jamais_contacte_est_vide(tmp_path):
    store = ChatStore(tmp_path / "chat")
    assert store.fil("qa") == ()
    assert store.agents() == ()


def test_ajouter_puis_relire_le_fil_dans_l_ordre(tmp_path):
    """Append-only : le fil se relit exactement comme il a été écrit."""
    store = ChatStore(tmp_path / "chat")
    echanges = (
        _message(contenu="Peux-tu vérifier la CI ?"),
        _message(auteur="qa", contenu="Oui — pipeline vert."),
        _message(contenu="Merci !"),
    )
    for message in echanges:
        store.ajouter(message)

    assert store.fil("qa") == echanges


def test_un_fichier_jsonl_par_agent_lisible_tel_quel(tmp_path):
    """Chaque agent a son fichier ; une ligne = un message JSON, accents lisibles."""
    store = ChatStore(tmp_path / "chat")
    store.ajouter(_message(agent="qa", contenu="Vérifie le déploiement"))
    store.ajouter(_message(agent="bdd", contenu="Le schéma est prêt ?"))

    fichier = tmp_path / "chat" / "qa.jsonl"
    assert fichier.is_file()
    (ligne,) = fichier.read_text(encoding="utf-8").splitlines()
    assert json.loads(ligne)["contenu"] == "Vérifie le déploiement"
    assert "déploiement" in ligne  # ensure_ascii=False : le fichier reste lisible

    # Les fils ne se mélangent pas : chaque agent ne relit que le sien.
    (message_bdd,) = store.fil("bdd")
    assert message_bdd.contenu == "Le schéma est prêt ?"


def test_agents_liste_les_fils_tries_et_ignore_les_intrus(tmp_path):
    racine = tmp_path / "chat"
    store = ChatStore(racine)
    store.ajouter(_message(agent="qa"))
    store.ajouter(_message(agent="bdd"))
    # Un fichier hors slug déposé à la main n'entre pas dans la liste.
    (racine / "Intrus.jsonl").write_text("{}", encoding="utf-8")

    assert store.agents() == ("bdd", "qa")


@pytest.mark.parametrize("nom", ["../evasion", "a.b", "QA", "un/chemin", ""])
def test_un_nom_hors_slug_est_refuse_sans_toucher_le_disque(tmp_path, nom):
    """Un nom venu de l'API n'est jamais un chemin disque (même garde que #72)."""
    store = ChatStore(tmp_path / "chat")

    with pytest.raises(ValueError):
        store.ajouter(_message(agent=nom))
    with pytest.raises(ValueError):
        store.fil(nom)

    assert not (tmp_path / "chat").exists()  # le refus n'a rien créé


def test_le_depot_par_defaut_respecte_la_racine_configuree(tmp_path):
    """`MAESTRO_CHAT_DIR` fait foi ; sinon le dossier `core/chat/` du dépôt."""
    configure = Settings(
        anthropic_api_key=None, anthropic_model="claude-opus-4-8",
        claude_auth_mode=None, claude_oauth_token=None,
        database_url=None, redis_url=None, chat_dir=str(tmp_path / "ailleurs"),
    )
    assert ChatStore.default(configure).racine == tmp_path / "ailleurs"

    defaut = Settings(
        anthropic_api_key=None, anthropic_model="claude-opus-4-8",
        claude_auth_mode=None, claude_oauth_token=None,
        database_url=None, redis_url=None,
    )
    assert ChatStore.default(defaut).racine.parts[-2:] == ("core", "chat")


def test_message_aller_retour_dict():
    message = _message(contenu="Où en est la revue ?")
    assert MessageChat.from_dict(message.to_dict()) == message
    assert message.horodatage  # l'horodatage se pose tout seul
    # Une ligne minimaliste (vieux stockage) reste lisible.
    minimal = MessageChat.from_dict({"agent": "qa"})
    assert minimal.auteur == UTILISATEUR and minimal.contenu == ""


# ------------------------------------------------- ② Production de la réponse


class FournisseurEnregistreur(ModelProvider):
    """Fournisseur factice : enregistre chaque appel et rend une réponse fixe."""

    name = "enregistreur"

    def __init__(self, reponse="Réponse du modèle."):
        self.appels = []
        self._reponse = reponse

    def supports(self, model):
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        self.appels.append({"prompt": prompt, "model": model, "system_prompt": system_prompt})
        return self._reponse


def test_repondeur_scripte_reflete_le_dernier_message():
    """Déterministe et sans modèle : le levier de la démo (#65) et des tests d'API."""
    agent = _agent()
    fil = (_message(contenu="Bonjour"), _message(contenu="La CI est-elle verte ?"))

    reponse = asyncio.run(RepondeurScripte().repondre(agent, fil))

    assert "La CI est-elle verte ?" in reponse
    assert agent.role in reponse
    assert "qualite, tests" in reponse  # compétences triées
    assert "aucun modèle" in reponse


def test_repondeur_modele_transcrit_le_fil_et_cadre_le_fournisseur():
    """Le fil part en prompt, le playbook + cadre de conversation en prompt système."""
    agent = _agent()
    fournisseur = FournisseurEnregistreur()
    fil = (
        _message(contenu="Bonjour"),
        _message(auteur="qa", contenu="Bonjour, que puis-je vérifier ?"),
        _message(contenu="Le déploiement de l'API"),
    )

    reponse = asyncio.run(RepondeurModele(provider=fournisseur).repondre(agent, fil))

    assert reponse == "Réponse du modèle."
    (appel,) = fournisseur.appels
    assert appel["model"] == agent.modele
    # La transcription garde l'ordre et distingue les deux voix.
    assert appel["prompt"].index("Utilisateur : Bonjour") < appel["prompt"].index(
        "Toi : Bonjour, que puis-je vérifier ?"
    )
    assert "Réponds au dernier message" in appel["prompt"]
    # Le prompt système : le playbook de l'agent, puis le cadre de conversation.
    assert appel["system_prompt"].startswith(agent.prompt_systeme)
    assert "CONVERSATION DIRECTE" in appel["system_prompt"]


def test_repondeur_modele_prend_le_playbook_courant(tmp_path):
    """La version éditée (#76) fait foi ; un agent jamais édité garde son prompt du code."""
    agent = _agent()
    depot = PlaybookStore(tmp_path / "playbooks")
    depot.ecrire("qa", "Consignes éditées depuis la Control Tower.")
    fournisseur = FournisseurEnregistreur()

    asyncio.run(
        RepondeurModele(provider=fournisseur, playbooks=depot).repondre(
            agent, (_message(contenu="Bonjour"),)
        )
    )
    asyncio.run(
        RepondeurModele(provider=fournisseur, playbooks=depot).repondre(
            _agent(nom="bdd", role="Base de données", prompt="Tu conçois les schémas."),
            (_message(agent="bdd", contenu="Bonjour"),),
        )
    )

    edite, jamais_edite = fournisseur.appels
    assert edite["system_prompt"].startswith("Consignes éditées depuis la Control Tower.")
    assert jamais_edite["system_prompt"].startswith("Tu conçois les schémas.")


# ------------------------------------------------- ③ Flux d'un envoi


class BusEspion(InMemoryEventBus):
    """Bus mémoire qui garde trace des événements publiés, dans l'ordre."""

    def __init__(self):
        super().__init__()
        self.publies = []

    async def publish(self, event):
        self.publies.append(event)
        await super().publish(event)


class RepondeurConstant(RepondeurChat):
    """Rend toujours la même réponse — ou lève, selon le scénario."""

    def __init__(self, reponse="", erreur=None):
        self._reponse = reponse
        self._erreur = erreur

    async def repondre(self, agent, fil):
        if self._erreur is not None:
            raise self._erreur
        return self._reponse


def _service(tmp_path, repondeur=None):
    """Le service câblé comme dans l'app : store fichier, messagerie et bus mémoire."""
    store = ChatStore(tmp_path / "chat")
    mailbox = InMemoryMailbox()
    bus = BusEspion()
    service = ServiceChat(
        store=store,
        repondeur=repondeur if repondeur is not None else RepondeurScripte(),
        mailbox=mailbox,
        bus=bus,
    )
    return service, store, mailbox, bus


def test_envoyer_persiste_achemine_et_diffuse(tmp_path):
    """Le chemin complet d'un envoi : fil, boîtes aux lettres (#44), bus (#46)."""

    async def scenario():
        service, store, mailbox, bus = _service(tmp_path)
        agent = _agent()
        boite_agent = await mailbox.subscribe("qa")
        boite_utilisateur = await mailbox.subscribe(UTILISATEUR)

        message, reponse = await service.envoyer(agent, "  Peux-tu vérifier la CI ?  ")

        # La paire rendue : contenu épuré, auteurs posés.
        assert message.auteur == UTILISATEUR
        assert message.contenu == "Peux-tu vérifier la CI ?"
        assert reponse.auteur == "qa"
        assert "Peux-tu vérifier la CI ?" in reponse.contenu

        # Persistance du fil, dans l'ordre de l'échange.
        assert store.fil("qa") == (message, reponse)
        assert service.fil("qa") == (message, reponse)

        # Messagerie : la requête part vers la boîte de l'agent, la réponse
        # revient vers celle de l'utilisateur — un agent-processus abonné la verrait.
        requete = await anext(boite_agent)
        assert requete.type == MESSAGE_REQUETE
        assert requete.de_agent == UTILISATEUR and requete.a_agent == "qa"
        assert requete.payload == {"contenu": message.contenu}
        retour = await anext(boite_utilisateur)
        assert retour.type == MESSAGE_REPONSE
        assert retour.de_agent == "qa" and retour.a_agent == UTILISATEUR
        assert retour.payload == {"contenu": reponse.contenu}

        # Diffusion temps réel : un `chat.message` par message, réponse comprise.
        assert [e.type for e in bus.publies] == [EVENEMENT_CHAT_MESSAGE] * 2
        aller, retour_bus = bus.publies
        assert aller.agent == "qa" and aller.role == agent.role
        assert aller.statut == AUTEUR_UTILISATEUR and aller.detail == message.contenu
        assert aller.horodatage == message.horodatage
        assert retour_bus.statut == AUTEUR_AGENT and retour_bus.detail == reponse.contenu

    asyncio.run(scenario())


def test_l_objet_du_message_inter_agents_est_un_extrait(tmp_path):
    """La ligne « sujet » de la lettre est bornée ; le contenu intégral vit en payload."""

    async def scenario():
        service, _, mailbox, _ = _service(tmp_path)
        boite = await mailbox.subscribe("qa")
        contenu = "Détaille-moi " + "très " * 40 + "précisément le plan."

        await service.envoyer(_agent(), contenu)

        requete = await anext(boite)
        assert requete.objet == contenu[:80]
        assert requete.payload == {"contenu": contenu}

    asyncio.run(scenario())


def test_le_fil_est_persiste_avant_d_etre_diffuse(tmp_path):
    """L'ordre du service : un client notifié par WebSocket relit un REST à jour."""

    class BusTemoin(BusEspion):
        def __init__(self, store):
            super().__init__()
            self._store = store
            self.longueurs_du_fil = []

        async def publish(self, event):
            self.longueurs_du_fil.append(len(self._store.fil("qa")))
            await super().publish(event)

    async def scenario():
        store = ChatStore(tmp_path / "chat")
        bus = BusTemoin(store)
        service = ServiceChat(store=store, repondeur=RepondeurScripte(),
                              mailbox=InMemoryMailbox(), bus=bus)

        await service.envoyer(_agent(), "Bonjour")

        # À chaque diffusion, le message diffusé était déjà dans le fil.
        assert bus.longueurs_du_fil == [1, 2]

    asyncio.run(scenario())


def test_deux_envois_successifs_gardent_l_historique(tmp_path):
    """Le fil s'allonge dans l'ordre ; le répondeur voit tout l'historique."""

    async def scenario():
        service, store, _, _ = _service(tmp_path)
        agent = _agent()

        await service.envoyer(agent, "Premier message")
        await service.envoyer(agent, "Second message")

        fil = store.fil("qa")
        assert [m.auteur for m in fil] == [UTILISATEUR, "qa", UTILISATEUR, "qa"]
        assert fil[0].contenu == "Premier message"
        assert fil[2].contenu == "Second message"
        assert "Second message" in fil[3].contenu  # la réponse reflète le dernier

    asyncio.run(scenario())


def test_un_message_vide_est_refuse_sans_rien_persister(tmp_path):
    async def scenario():
        service, store, _, bus = _service(tmp_path)

        with pytest.raises(ValueError):
            await service.envoyer(_agent(), "   \n ")

        assert store.fil("qa") == ()
        assert bus.publies == []

    asyncio.run(scenario())


def test_repondeur_en_echec_le_message_utilisateur_reste_acquis(tmp_path):
    """L'échec ne concerne que la réponse : relancer ne perd pas le fil."""

    async def scenario():
        panne = RepondeurConstant(erreur=RuntimeError("fournisseur indisponible"))
        service, store, _, bus = _service(tmp_path, repondeur=panne)

        with pytest.raises(ReponseIndisponible, match="fournisseur indisponible"):
            await service.envoyer(_agent(), "Bonjour")

        # Le message utilisateur est persisté et déjà diffusé — la réponse, non.
        (message,) = store.fil("qa")
        assert message.auteur == UTILISATEUR
        (evenement,) = bus.publies
        assert evenement.statut == AUTEUR_UTILISATEUR

    asyncio.run(scenario())


def test_une_reponse_vide_vaut_reponse_indisponible(tmp_path):
    async def scenario():
        service, store, _, _ = _service(tmp_path, repondeur=RepondeurConstant("  \n"))

        with pytest.raises(ReponseIndisponible, match="réponse vide"):
            await service.envoyer(_agent(), "Bonjour")

        (message,) = store.fil("qa")  # le message utilisateur, seul, reste acquis
        assert message.auteur == UTILISATEUR

    asyncio.run(scenario())


def test_la_reponse_est_epuree_avant_persistance(tmp_path):
    async def scenario():
        service, store, _, _ = _service(
            tmp_path, repondeur=RepondeurConstant("  Voilà mon analyse.  \n")
        )

        _, reponse = await service.envoyer(_agent(), "Analyse le plan")

        assert reponse.contenu == "Voilà mon analyse."
        assert store.fil("qa")[1].contenu == "Voilà mon analyse."

    asyncio.run(scenario())


# ------------------------------------------------- ④ Les sources d'un message


@pytest.fixture(autouse=True)
def _ingestion_jetable(tmp_path, monkeypatch):
    """L'emplacement d'ingestion pointé sur un dossier jetable, jamais `core/`.

    Autouse et non demandée au cas par cas : le service résout son dépôt
    **paresseusement** (`DepotTeleversements.default()`), donc un test qui
    oublierait de la poser écrirait dans le dépôt du dépôt — et
    `test_un_message_sans_source_ne_cree_aucun_dossier` ne pourrait rien
    prouver, la racine réelle existant déjà.
    """
    racine = tmp_path / "ingestion"
    monkeypatch.setenv("MAESTRO_INGESTION_DIR", str(racine))
    return racine


def _service_a_sources(tmp_path, *, garde_fous=None, repondeur=None):
    """Le service câblé pour recevoir des sources — dépôt de téléversement injecté.

    Deux jeux de plafonds, à dessein : le **dépôt** reste permissif (il plafonne
    à la réception des octets, ce que `tests/test_televersement.py` couvre déjà)
    et le **service** porte ceux que la composition applique. C'est ce qui permet
    de faire refuser une source par la chaîne d'ingestion du fil sans que le
    refus vienne d'ailleurs.
    """
    store = ChatStore(tmp_path / "chat")
    mailbox = InMemoryMailbox()
    bus = BusEspion()
    depot = DepotTeleversements(tmp_path / "depot")
    service = ServiceChat(
        store=store,
        repondeur=repondeur if repondeur is not None else RepondeurScripte(),
        mailbox=mailbox,
        bus=bus,
        televersements=depot,
        garde_fous_ingestion=garde_fous,
    )
    return service, store, mailbox, bus, depot


def _televerser(depot, nom, contenu):
    """Le renvoi `{"type": "fichier", "id": …}` que l'écran envoie après dépôt."""
    recu = depot.accueillir(nom, io.BytesIO(contenu))
    return {"type": "fichier", "id": recu.id}


def test_un_message_porte_ses_sources_lues_et_leurs_octets_rattaches(tmp_path):
    """Le chemin complet d'un dépôt : identifiant → résolution → octets → rapport.

    Le critère 1 de #482 pris au mot — « la chaîne d'ingestion existante, jamais
    une seconde » : la source ressort **résolue** (chemin calculé par le backend,
    jamais celui du navigateur), ses octets sont **dans l'emplacement
    d'ingestion**, et ce qui a été lu se lit dans le rapport.
    """

    async def scenario():
        service, _, _, _, depot = _service_a_sources(tmp_path)
        contenu = "# Cahier des charges\n\nRefondre l'écran de lancement.".encode()

        message, _ = await service.envoyer(
            _agent(), "Voici le cahier.", [_televerser(depot, "cdc.md", contenu)]
        )

        (source,) = message.sources
        assert source.type == "fichier" and source.nom == "cdc.md"
        # Le chemin est **calculé** : sous la racine d'ingestion, dans un dossier
        # propre au message, et les octets y sont pour de bon.
        chemin = Path(source.chemin)
        assert chemin.is_file() and chemin.read_bytes() == contenu
        assert racine_ingestion() in chemin.parents

        # Le rapport dit ce qui a été lu, et le contexte porte le contenu encadré.
        assert message.rapport is not None
        (lecture,) = message.rapport.lectures
        assert lecture.etat == "lu" and lecture.tokens > 0
        assert "Refondre l'écran de lancement." in message.contexte
        assert message.contexte.startswith("## Sources fournies")

    asyncio.run(scenario())


def test_chaque_message_a_son_propre_emplacement_d_ingestion(tmp_path):
    """Un dossier par **acte**, comme `core/ingestion/<run_id>/` en est un par run.

    Deux messages qui partageraient leur dossier rendraient impossible de dire de
    quel tour de conversation un document relève — et de le ramasser sans toucher
    à celui du voisin.
    """

    async def scenario():
        service, _, _, _, depot = _service_a_sources(tmp_path)

        premier, _ = await service.envoyer(
            _agent(), "Un", [_televerser(depot, "a.md", b"alpha")]
        )
        second, _ = await service.envoyer(
            _agent(), "Deux", [_televerser(depot, "b.md", b"beta")]
        )

        dossiers = {Path(m.sources[0].chemin).parent for m in (premier, second)}
        assert len(dossiers) == 2
        assert all(dossier.name.startswith("chat-") for dossier in dossiers)

    asyncio.run(scenario())


def test_un_message_de_sources_seules_est_accepte_et_se_resume_par_elles(tmp_path):
    """Déposer un cahier des charges *est* le message (#482).

    Sans texte, le fil d'activité écrirait une ligne vide et la lettre
    inter-agents partirait sans objet : nommer les sources est la seule chose
    vraie à dire.
    """

    async def scenario():
        service, store, mailbox, bus, depot = _service_a_sources(tmp_path)
        boite = await mailbox.subscribe("qa")

        message, _ = await service.envoyer(
            _agent(), "   ", [_televerser(depot, "cdc.md", b"# Cahier\n")]
        )

        assert message.contenu == ""
        assert message.resume == "1 source(s) jointe(s) : cdc.md"
        (persiste, _) = store.fil("qa")
        assert persiste.contenu == "" and persiste.resume == message.resume
        # Ce que le résumé sert : l'objet de la lettre et le détail de l'événement.
        assert (await anext(boite)).objet == message.resume
        assert bus.publies[0].detail == message.resume

    asyncio.run(scenario())


def test_une_source_hors_bornes_ne_laisse_ni_message_ni_lettre_ni_evenement(tmp_path):
    """Le refus tombe **avant toute écriture** — c'est le point du critère.

    Un plafond franchi est une saisie que l'utilisateur peut corriger : elle ne
    doit laisser derrière elle ni ligne au fil, ni lettre dans une boîte, ni
    événement sur le bus, faute de quoi le fil montrerait un message qui n'a
    jamais été envoyé.
    """

    async def scenario():
        service, store, mailbox, bus, depot = _service_a_sources(
            tmp_path, garde_fous=GardeFousIngestion(taille_max_source_octets=16)
        )
        boite = await mailbox.subscribe("qa")

        with pytest.raises(SourceRefusee) as refus:
            await service.envoyer(
                _agent(),
                "Voici le cahier.",
                [_televerser(depot, "cdc.md", b"x" * 4096)],
            )

        # Le motif et l'**index** : « une source est trop grosse » sans dire
        # laquelle obligerait à tout relire pour savoir quoi retirer.
        assert refus.value.motif == "source-trop-volumineuse"
        assert refus.value.index == 0
        assert store.fil("qa") == ()
        assert bus.publies == []
        assert boite.file.empty()  # aucune lettre n'est partie non plus

    asyncio.run(scenario())


def test_un_refus_est_un_value_error_et_reste_distinct_du_message_vide(tmp_path):
    """Trois façons d'échouer qui ne se confondent pas (docstring d'`envoyer`).

    `SourceRefusee` hérite de `ValueError` — donc la route qui traduit l'un
    traduit l'autre —, mais elle porte un motif : les aplatir en chaîne perdrait
    l'endroit où l'écran doit afficher le refus.
    """

    async def scenario():
        service, _, _, _, depot = _service_a_sources(
            tmp_path, garde_fous=GardeFousIngestion(nb_max_sources=1)
        )

        with pytest.raises(ValueError) as refus:
            await service.envoyer(
                _agent(),
                "Deux documents",
                [
                    _televerser(depot, "a.md", b"alpha"),
                    _televerser(depot, "b.md", b"beta"),
                ],
            )
        assert isinstance(refus.value, SourceRefusee)
        assert refus.value.motif == "trop-de-sources"

        # Le message vide, lui, reste un `ValueError` nu : rien à envoyer.
        with pytest.raises(ValueError) as vide:
            await service.envoyer(_agent(), "   ")
        assert not isinstance(vide.value, SourceRefusee)

    asyncio.run(scenario())


def test_un_format_non_gere_est_ignore_au_rapport_et_ne_refuse_rien(tmp_path):
    """« Rien à lire ici » et « je refuse de lire ça » ne se disent jamais pareil.

    Une image se joint comme n'importe quel fichier — la chaîne est unique — mais
    l'extraction ne lit que le texte, le Markdown, le `.docx` et le `.pdf` : elle
    ressort **ligne du rapport**, avec son motif, et le message part quand même.
    """

    async def scenario():
        service, store, _, _, depot = _service_a_sources(tmp_path)

        message, _ = await service.envoyer(
            _agent(),
            "La maquette :",
            [_televerser(depot, "maquette.png", b"\x89PNG\r\n\x1a\n binaire")],
        )

        (lecture,) = message.rapport.lectures
        assert lecture.etat == "ignore" and lecture.motif == "format-non-gere"
        assert lecture.tokens == 0
        # Rien de lu, donc pas de contexte : un en-tête sans contenu coûterait des
        # tokens pour ne rien dire.
        assert message.contexte == ""
        assert len(store.fil("qa")) == 2  # le message et sa réponse

    asyncio.run(scenario())


def test_le_rest_rend_le_rapport_et_le_stockage_seul_garde_le_contexte(tmp_path):
    """Deux formes plutôt qu'une : l'écran veut savoir, le répondeur veut lire.

    Le `contexte` est fait pour un prompt, pas pour un écran : le rapatrier au
    navigateur enverrait le contenu intégral des documents à chaque relecture du
    fil. Il est en revanche **persisté** — sans lui, un fil relu du disque aurait
    un rapport complet et un contenu perdu, et l'agent cesserait de voir le
    document dès le tour suivant.
    """

    async def scenario():
        service, store, _, _, depot = _service_a_sources(tmp_path)

        message, _ = await service.envoyer(
            _agent(), "Voici.", [_televerser(depot, "cdc.md", b"# Cahier\n\nDeux mots.")]
        )

        vers_ecran = message.to_dict()
        assert "contexte" not in vers_ecran
        assert vers_ecran["rapport"]["lectures"][0]["etat"] == "lu"
        # Le rapport lui-même ne rapatrie pas le contenu (`Lecture.to_dict`).
        assert "markdown" not in vers_ecran["rapport"]["lectures"][0]
        # Le stockage, lui, le garde — c'est la seule différence entre les deux formes.
        assert message.to_ligne()["contexte"] == message.contexte
        assert message.to_ligne().keys() - vers_ecran.keys() == {"contexte"}

        # Relu du disque par une autre instance : sources, rapport et contexte
        # intacts — **sauf** le `markdown` de la lecture, que `Lecture.to_dict`
        # n'emporte pas (un rapport dit ce qu'une source coûte, pas ce qu'elle
        # raconte). C'est exactement ce qui rend le champ `contexte` nécessaire :
        # sans lui, le fil relu aurait un rapport complet et un contenu perdu, et
        # l'agent cesserait de voir le document dès le tour suivant.
        (relu, _) = ChatStore(tmp_path / "chat").fil("qa")
        assert relu.sources == message.sources
        assert relu.contexte == message.contexte
        assert relu.rapport.lectures[0].markdown == ""
        assert message.rapport.lectures[0].markdown != ""
        assert relu.rapport.to_dict() == message.rapport.to_dict()

    asyncio.run(scenario())


def test_le_contenu_lu_entre_sous_le_message_qui_l_a_porte(tmp_path):
    """Le contexte suit son tour de conversation, il n'est jamais rassemblé en fin de fil.

    C'est ce qui dit de quel tour un document relève ; et il entre **encadré
    comme donnée** (ENF-13), par `contexte_markdown` et par lui seul.
    """

    async def scenario():
        fournisseur = FournisseurEnregistreur()
        service, _, _, _, depot = _service_a_sources(
            tmp_path, repondeur=RepondeurModele(provider=fournisseur)
        )

        await service.envoyer(
            _agent(),
            "Le cahier :",
            [_televerser(depot, "cdc.md", b"Refondre l'ecran de lancement.")],
        )
        await service.envoyer(_agent(), "Et maintenant ?")

        second = fournisseur.appels[-1]["prompt"]
        # Le contenu est sous la ligne du message qui l'a porté, avant le tour suivant.
        assert second.index("Utilisateur : Le cahier :") < second.index(
            "Refondre l'ecran de lancement."
        )
        assert second.index("Refondre l'ecran de lancement.") < second.index(
            "Utilisateur : Et maintenant ?"
        )
        # Encadré comme donnée : le préambule et le bloc viennent de l'extraction.
        assert "## Sources fournies" in second

    asyncio.run(scenario())


def test_la_lettre_porte_les_sources_declarees_et_jamais_leur_contenu(tmp_path):
    """La messagerie (#44) dit ce que le message **embarque**, pas ce qu'il raconte.

    Le contenu extrait a son seul chemin (`contexte_markdown`) et n'a rien à
    faire dans une boîte aux lettres ; et sans source, la clé est **absente** —
    non posée à `[]` —, sans quoi un abonné verrait passer un champ que rien dans
    le message ne justifie.
    """

    async def scenario():
        service, _, mailbox, _, depot = _service_a_sources(tmp_path)
        boite = await mailbox.subscribe("qa")

        await service.envoyer(
            _agent(), "Avec source", [_televerser(depot, "cdc.md", b"# Cahier\n")]
        )
        avec = await anext(boite)
        assert [s["nom"] for s in avec.payload["sources"]] == ["cdc.md"]
        assert "# Cahier" not in json.dumps(avec.payload, ensure_ascii=False)

        await service.envoyer(_agent(), "Sans source")
        sans = await anext(boite)
        assert sans.payload == {"contenu": "Sans source"}

    asyncio.run(scenario())


def test_un_message_sans_source_ne_cree_aucun_dossier(tmp_path):
    """Un message de texte garde exactement son coût d'avant #482.

    C'est ce qui rend le changement invisible pour qui ne joint rien : ni dépôt
    de téléversement résolu, ni emplacement d'ingestion créé.
    """

    async def scenario():
        # Sans dépôt injecté : celui du défaut serait résolu au premier besoin.
        service, store, _, _ = _service(tmp_path)

        await service.envoyer(_agent(), "Bonjour")

        assert len(store.fil("qa")) == 2
        assert not racine_ingestion().exists()

    asyncio.run(scenario())


# ------------------------------------------------- ⑤ l'arrêt d'une génération (#695)


class RepondeurLent(RepondeurChat):
    """Écrit cinq morceaux en cédant la main entre chacun — de quoi arrêter au milieu.

    Il surcharge `produire` comme le font les deux répondeurs réels : c'est la
    seule façon d'obtenir plusieurs incréments, et donc la seule façon d'observer
    un arrêt qui tombe **entre** deux.
    """

    MORCEAUX = ("un ", "deux ", "trois ", "quatre ", "cinq")

    async def repondre(self, agent, fil):
        return "".join(self.MORCEAUX).strip()

    async def produire(self, agent, fil, *, incrementer=None, projet_id=None):
        redaction = Redaction(incrementer)
        for morceau in self.MORCEAUX:
            await asyncio.sleep(0)
            await redaction.ecrire(morceau)
        return ReponseChat(contenu=redaction.texte)


async def _diffuser_en_arretant(service, agent, apres):
    """Draine un flux et demande l'arrêt après le `apres`-ième fragment."""
    trames = []
    fragments = 0
    async for trame in service.diffuser(agent, "Vérifie la CI"):
        trames.append(trame)
        if trame.type != FRAGMENT_CHAT_DELTA:
            continue
        fragments += 1
        if fragments == apres:
            assert service.interrompre(trame.echange) is True
    return trames


def test_un_arret_clot_le_flux_et_persiste_ce_qui_a_ete_recu(tmp_path):
    """« Ce qui a déjà été reçu reste au fil » — donc au fil persisté, pas à l'écran."""

    async def scenario():
        service, store, _, _ = _service(tmp_path, RepondeurLent())

        trames = await _diffuser_en_arretant(service, _agent(), apres=2)

        # Le flux se clôt sur `interrompu` et non sur `fin` : ce qu'il porte est
        # un texte arrêté, pas une réponse complète — les confondre ferait lire
        # l'un pour l'autre.
        assert trames[-1].type == FRAGMENT_CHAT_INTERROMPU
        assert FRAGMENT_CHAT_FIN not in [t.type for t in trames]

        recu = "".join(t.delta for t in trames if t.type == FRAGMENT_CHAT_DELTA)
        assert recu.strip() == "un deux"
        # La trame porte le message **persisté**, et le fil le porte aussi : un
        # rechargement ne fait donc pas disparaître ce qu'on venait de lire.
        assert trames[-1].message is not None
        assert trames[-1].message.contenu == recu.strip()
        fil = store.fil("qa")
        assert [m.auteur for m in fil] == [UTILISATEUR, "qa"]
        assert fil[-1].contenu == recu.strip()

    asyncio.run(scenario())


def test_rien_a_arreter_se_distingue_d_un_arret(tmp_path):
    """Un identifiant inconnu et un échange soldé ne sont pas des pannes.

    Cliquer au moment où la réponse tombe est une course normale : le service
    rend `False` et n'annule rien d'autre. Et le registre ne fuit pas — un
    échange qui survivrait à son flux rendrait `True` pour toujours.
    """

    async def scenario():
        service, _, _, _ = _service(tmp_path, RepondeurLent())

        trames = [t async for t in service.diffuser(_agent(), "Vérifie la CI")]

        assert trames[-1].type == FRAGMENT_CHAT_FIN
        assert service.interrompre("jamais-vu") is False
        assert service.interrompre(trames[-1].echange) is False

    asyncio.run(scenario())

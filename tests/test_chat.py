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
   acquis : relancer ne perd pas le fil).

L'exposition HTTP du canal (REST `/api/chat` + WebSocket `chat.message`) est
couverte dans `tests/test_controltower.py` (section ⑧).
"""

import asyncio
import json

import pytest

from maestro.agents.catalog import Agent
from maestro.agents.playbooks import PlaybookStore
from maestro.config import Settings
from maestro.controltower.chat import (
    AUTEUR_AGENT,
    AUTEUR_UTILISATEUR,
    UTILISATEUR,
    ChatStore,
    MessageChat,
    RepondeurChat,
    RepondeurModele,
    RepondeurScripte,
    ReponseIndisponible,
    ServiceChat,
)
from maestro.controltower.events import EVENEMENT_CHAT_MESSAGE, InMemoryEventBus
from maestro.messaging import MESSAGE_REPONSE, MESSAGE_REQUETE, InMemoryMailbox
from maestro.providers.base import ModelProvider


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

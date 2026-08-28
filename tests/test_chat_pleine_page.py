"""Ce que le chantier « chat global pleine page » a établi côté canal — lot 8/8 de #690.

Les lots 2, 3 et 4 ont livré sans tests, comme la règle de découpage le prévoit
(docs/10 §5.1) : ils sont ici. Aucun réseau, aucun fournisseur, aucun modèle —
le fil vit dans un répertoire temporaire, le bus et la messagerie en mémoire,
les fournisseurs sont des doubles (`tests/conftest.py`, #195).

Ce fichier ne recouvre pas ce que `tests/test_chat.py` garde déjà (persistance,
envoi, sources sur `POST …/messages`, arrêt d'une génération) ni ce que
`tests/test_chat_global.py` garde du fil de l'orchestration. Il couvre les
**quatre** invariants que les trois lots ont introduits et que rien ne gardait :

① **le flux porte ce qu'un message porte** (#692) — `POST …/flux` accepte le
   corps de `POST …/messages`, sources comprises, et la trame `debut` rend le
   message de l'utilisateur *avec* ses sources et son rapport de lecture. C'est
   la moitié du lot qui se défait sans bruit : un flux qui perdrait les pièces
   jointes échangerait un rendu incrémental contre une fonctionnalité, et rien
   à l'écran ne le dirait ;
② **les incréments d'un répondeur modèle** (#693) — la concaténation des
   morceaux **est** le message final, `generate_stream` reste honoré par un
   fournisseur qui ne sait pas streamer, et un flux coupé en route le dit ;
③ **un fil d'avant les conversations en devient une** (#694) — un `<agent>.jsonl`
   écrit avant le lot se relit sous `origine` sans être déplacé, réécrit, ni relu
   autrement. C'est le critère qui interdit la migration, donc celui qu'une
   migration bien intentionnée casserait ;
④ **ouvrir une conversation neuve** (#694) — le `201`, l'idempotence tant que
   rien n'a été dit, l'ordre qui met la neuve devant, et les trois réponses à un
   identifiant (422 / 404 / le cas nominal).

⚠ **Chaque sonde prouve son motif sur un échantillon fautif avant de conclure**
(méthode de #534/#537/#539). Les invariants de ce chantier sont pour moitié des
**absences** — rien n'est déplacé, rien n'est réécrit, rien n'est perdu —, et une
absence est vraie pour deux raisons, dont l'une est que le test regarde à côté.
"""

import asyncio
import io
import json
from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient

from maestro.agents.catalog import Agent
from maestro.controltower.app import create_app
from maestro.controltower.chat import (
    CONVERSATION_ORIGINE,
    FRAGMENT_CHAT_DEBUT,
    FRAGMENT_CHAT_FIN,
    UTILISATEUR,
    ChatStore,
    FluxInterrompu,
    MessageChat,
    Redaction,
    RepondeurModele,
    RepondeurScripte,
    titre_conversation,
)
from maestro.controltower.events import InMemoryEventBus
from maestro.providers.base import ModelProvider
from maestro.sources import DepotTeleversements


def _agent(nom="qa", role="QA / Testeur", modele="claude-opus-4-8"):
    """Fiche catalogue factice — de quoi répondre sans charger le vrai catalogue."""
    return Agent(
        nom=nom,
        role=role,
        competences=frozenset({"qualite", "tests"}),
        modele=modele,
        prompt_systeme="Tu vérifies la qualité des livrables.",
    )


@pytest.fixture()
def bus():
    return InMemoryEventBus()


@pytest.fixture()
def depot_chat(tmp_path):
    """Fil de chat sur répertoire temporaire — jamais le `core/chat/` réel."""
    return ChatStore(tmp_path / "chat")


@pytest.fixture()
def client_chat(bus, depot_chat):
    """L'app sur fil temporaire, répondeur scripté (zéro modèle)."""
    with TestClient(
        create_app(bus=bus, chat_store=depot_chat, chat_repondeur=RepondeurScripte())
    ) as client:
        yield client


@pytest.fixture()
def client_chat_sources(bus, depot_chat, tmp_path, monkeypatch):
    """Le même client, mais capable de recevoir des sources (#482).

    Deux réglages, et les deux sont nécessaires : le **dépôt de téléversement**
    est injecté (une seule instance pour la route qui reçoit les octets et pour
    le fil qui les rattache) et l'**emplacement d'ingestion** est déplacé sur un
    dossier jetable — sans quoi le rattachement écrirait dans le `core/ingestion/`
    du dépôt.
    """
    monkeypatch.setenv("MAESTRO_INGESTION_DIR", str(tmp_path / "ingestion"))
    app = create_app(
        bus=bus,
        chat_store=depot_chat,
        chat_repondeur=RepondeurScripte(),
        televersements=DepotTeleversements(tmp_path / "depot"),
    )
    with TestClient(app) as client:
        yield client


def _trames(reponse):
    """Les trames d'un flux SSE, décodées dans leur ordre d'arrivée."""
    return [
        json.loads(ligne[len("data: ") :])
        for ligne in reponse.text.splitlines()
        if ligne.startswith("data: ")
    ]


def _deposer_un_fichier(client, nom="cdc.md", octets=b"# Cahier\n\nDeux mots."):
    """Un fichier téléversé — rend l'identifiant par lequel un message le porte."""
    depose = client.post(
        "/api/sources",
        files=[("fichier", (nom, io.BytesIO(octets), "text/markdown"))],
    ).json()
    return depose["sources"][0]["id"]


# ─────────── ① Le flux porte ce qu'un message porte (#692) ───────────


def test_le_flux_accepte_les_memes_sources_que_l_envoi(client_chat_sources):
    """Le cœur du lot : `POST …/flux` prend le corps de `POST …/messages`.

    Le `contenu` du `GET` voyage en paramètre d'URL, où l'on ne peut
    raisonnablement déclarer ni identifiants de sources ni corps — c'est le
    transport, et lui seul, qui barrait le consommateur du canal.
    """
    identifiant = _deposer_un_fichier(client_chat_sources)

    reponse = client_chat_sources.post(
        "/api/chat/qa/flux",
        json={
            "contenu": "Voici le cahier.",
            "sources": [{"type": "fichier", "id": identifiant}],
        },
    )

    assert reponse.status_code == 200
    assert reponse.headers["content-type"].startswith("text/event-stream")
    trames = _trames(reponse)
    assert trames[0]["type"] == FRAGMENT_CHAT_DEBUT
    assert trames[-1]["type"] == FRAGMENT_CHAT_FIN


def test_la_trame_d_ouverture_porte_les_sources_et_leur_rapport(client_chat_sources):
    """Sans la trame `debut`, un client du flux enverrait des sources sans jamais
    savoir ce qui en a été lu, tronqué ou ignoré (le rapport de #316).

    C'est ce qui distingue ce canal d'un simple `POST` suivi d'un rechargement :
    la paire que `POST …/messages` rend d'un coup est ici rendue en deux temps,
    et la première moitié ne doit rien perdre en route.
    """
    identifiant = _deposer_un_fichier(client_chat_sources)

    reponse = client_chat_sources.post(
        "/api/chat/qa/flux",
        json={
            "contenu": "Voici le cahier.",
            "sources": [{"type": "fichier", "id": identifiant}],
        },
    )

    ouverture = _trames(reponse)[0]["message"]
    assert ouverture["auteur"] == UTILISATEUR
    assert [source["nom"] for source in ouverture["sources"]] == ["cdc.md"]
    (lecture,) = ouverture["rapport"]["lectures"]
    assert lecture["etat"] == "lu" and lecture["tokens"] > 0
    # Le contenu extrait ne revient pas : le rapatrier enverrait les documents
    # entiers à chaque trame d'ouverture (contrat de §6.12).
    assert "contexte" not in ouverture and "markdown" not in lecture


def test_les_deux_voies_d_envoi_deposent_le_meme_message(client_chat_sources):
    """« Ce ne sont pas deux chemins d'envoi » : les deux verbes appellent le même
    `diffuser`, qui dépose comme `envoyer`.

    Comparé **champ par champ**, hors horodatage : deux mécaniques d'envoi qui
    divergeraient d'un champ donneraient deux formes du même message dans un
    seul fil, et c'est le rechargement qui l'apprendrait.
    """
    def corps():
        """Le même corps des deux côtés — seul l'identifiant du dépôt diffère."""
        identifiant = _deposer_un_fichier(client_chat_sources)
        return {
            "contenu": "Bonjour",
            "sources": [{"type": "fichier", "id": identifiant}],
        }

    par_le_post = client_chat_sources.post(
        "/api/chat/qa/messages", json=corps()
    ).json()["messages"][0]

    par_le_flux = _trames(
        client_chat_sources.post("/api/chat/qa/flux", json=corps())
    )[0]["message"]

    def comparables(message):
        return {cle: valeur for cle, valeur in message.items() if cle != "horodatage"}

    # Les identifiants d'ingestion diffèrent d'un dépôt à l'autre : c'est la
    # **forme** qui se compare, pas la valeur d'un identifiant tiré au sort.
    assert comparables(par_le_post).keys() == comparables(par_le_flux).keys()
    assert par_le_post["auteur"] == par_le_flux["auteur"]
    assert par_le_post["contenu"] == par_le_flux["contenu"]
    assert [s["nom"] for s in par_le_post["sources"]] == [
        s["nom"] for s in par_le_flux["sources"]
    ]


def test_une_source_refusee_sort_en_422_avant_la_premiere_trame(
    client_chat_sources, depot_chat
):
    """Le refus garde la forme qu'il a sur l'autre voie — `{motif, message, index}`.

    Il est tranché **avant** le premier `yield` : une fois les en-têtes partis, il
    n'y a plus de statut HTTP à rendre, et le refus devrait se dire en trame,
    c'est-à-dire trop tard pour que le client sache que rien n'est parti.
    """
    reponse = client_chat_sources.post(
        "/api/chat/qa/flux",
        json={
            "contenu": "Et cette adresse ?",
            "sources": [{"type": "url", "valeur": "ftp://exemple.test/spec"}],
        },
    )

    assert reponse.status_code == 422
    detail = reponse.json()["detail"]
    assert detail["motif"] == "url-non-suivable"
    assert detail["index"] == 0
    # Rien n'a été écrit : ni fil, ni fichier de fil.
    assert client_chat_sources.get("/api/chat/qa").json()["messages"] == []
    assert not depot_chat.racine.exists()


def test_toutes_les_trames_nomment_leur_conversation(client_chat):
    """#694 vu du flux : la conversation est sur **toutes** les trames, `debut`
    comprise — un client qui n'apprendrait où la réponse s'écrit qu'à la clôture
    ne saurait pas dans quel fil poser ce qu'il affiche déjà.
    """
    reponse = client_chat.post("/api/chat/qa/flux", json={"contenu": "Bonjour"})

    trames = _trames(reponse)
    assert len(trames) >= 3
    assert {trame["conversation"] for trame in trames} == {CONVERSATION_ORIGINE}


# ─────────── ② Les incréments d'un répondeur modèle (#693) ───────────


class FournisseurQuiStreame(ModelProvider):
    """Un fournisseur qui **découpe** — le cas que #693 a ouvert."""

    name = "streameur"

    def __init__(self, morceaux):
        self.morceaux = list(morceaux)
        self.appels_generate = 0

    def supports(self, model):
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        self.appels_generate += 1
        return "".join(self.morceaux)

    async def generate_stream(
        self, prompt, *, model, system_prompt=None
    ) -> AsyncIterator[str]:
        for morceau in self.morceaux:
            yield morceau


class FournisseurSansFlux(ModelProvider):
    """Un fournisseur qui **ne sait pas** streamer : il hérite du défaut.

    C'est le cas du compatible OpenAI, et c'est le point du contrat — la
    capacité est optionnelle mais **honorée par tous**, à la différence de
    `run_agent` qui se refuse : l'appelant n'a aucune capacité à tester avant
    d'appeler.
    """

    name = "sans-flux"

    def __init__(self, reponse="Réponse entière."):
        self.reponse = reponse

    def supports(self, model):
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        return self.reponse


class FournisseurQuiCasse(ModelProvider):
    """Un fournisseur dont le flux s'arrête en chemin, après quelques morceaux."""

    name = "casse"

    def supports(self, model):
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        raise AssertionError("generate ne doit pas être appelé ici")

    async def generate_stream(
        self, prompt, *, model, system_prompt=None
    ) -> AsyncIterator[str]:
        yield "Je regarde le "
        raise RuntimeError("connexion perdue")


def _morceaux_de(fournisseur, fil=None):
    """Ce que `RepondeurModele` publie, et ce qu'il rend — la paire à comparer."""
    publies: list[str] = []

    async def incrementer(morceau):
        publies.append(morceau)

    fil = fil or (MessageChat(agent="qa", auteur=UTILISATEUR, contenu="Bonjour"),)
    reponse = asyncio.run(
        RepondeurModele(provider=fournisseur).produire(
            _agent(), fil, incrementer=incrementer
        )
    )
    return publies, reponse.contenu


def test_la_concatenation_des_increments_est_le_message_final():
    """La propriété dont dépend tout le contrat SSE — un client affiche pendant
    que ça arrive et n'a **rien** à réconcilier ensuite.

    Elle se casse d'un `strip()` de trop, et le seul endroit d'où elle se voit
    est celui-ci : ni la trame `fin`, ni le fil persisté ne disent comment le
    texte a été découpé.
    """
    fournisseur = FournisseurQuiStreame(["Je regarde ", "le pipeline", " du run."])

    publies, final = _morceaux_de(fournisseur)

    assert len(publies) == 3
    assert "".join(publies) == final
    assert final == "Je regarde le pipeline du run."


def test_un_fournisseur_qui_ne_streame_pas_produit_ce_qu_il_produisait():
    """La capacité est optionnelle **et honorée par tous** : le défaut de la
    frontière appelle `generate` et rend le texte entier en un morceau.

    C'est ce qui fait qu'un fournisseur qui ne sait pas streamer traverse les
    deux étages sans être modifié — le comportement ne se dégrade jamais, il
    s'affine quand le fournisseur sait le faire.
    """
    publies, final = _morceaux_de(FournisseurSansFlux("Réponse entière."))

    assert publies == ["Réponse entière."]
    assert final == "Réponse entière."


def test_un_texte_vide_ne_publie_aucun_morceau():
    """« Texte vide ⇒ aucun morceau », et pas un morceau vide : une trame
    `fragment` sans `delta` ferait afficher une bulle qui a commencé à s'écrire
    alors que rien n'est venu.
    """
    publies, final = _morceaux_de(FournisseurSansFlux(""))

    assert publies == []
    assert final == ""


def test_les_blancs_de_bord_ne_font_pas_mentir_l_invariant():
    """Le geste que `Redaction` a et que personne d'autre n'a.

    `ServiceChat` **rase** le texte final : publier tel quel un flux qui commence
    ou finit par des blancs ferait diverger la concaténation des `delta` de la
    trame `fin` — d'un retour à la ligne, assez pour qu'un client qui recolle ne
    retrouve pas le message. Les blancs intérieurs, eux, sont du texte et
    restent.
    """
    fournisseur = FournisseurQuiStreame(["\n  Deux", " mots", "  \n\n"])

    publies, final = _morceaux_de(fournisseur)

    assert "".join(publies) == final
    assert final == "Deux mots"


def test_les_blancs_retenus_repartent_quand_du_texte_les_suit():
    """La contre-épreuve du précédent, sans laquelle « écarter les blancs »
    voudrait dire « écarter les espaces », c'est-à-dire recoller les mots.

    Un blanc n'est de queue que tant que rien ne le suit ; dès qu'un morceau non
    blanc arrive, il est **intérieur** au texte et part avec lui.
    """
    publies, final = _morceaux_de(FournisseurQuiStreame(["Deux", "  ", "mots"]))

    assert "".join(publies) == final
    assert final == "Deux  mots"


def test_un_flux_coupe_en_route_le_dit_au_lieu_de_se_taire():
    """Un texte arrêté ne se distingue pas d'une réponse courte — c'est la seule
    information que le client ne peut pas déduire : il voit du texte, et rien ne
    lui apprendrait qu'il est incomplet.
    """
    with pytest.raises(FluxInterrompu) as leve:
        _morceaux_de(FournisseurQuiCasse())

    assert "incomplet" in str(leve.value)
    assert "connexion perdue" in str(leve.value)


def test_un_echec_avant_le_premier_morceau_reste_l_echec_qu_il_est():
    """La moitié symétrique, et elle compte autant : tant que rien n'est parti,
    il ne s'est rien affiché — l'échec est celui de n'importe quel répondeur, et
    le nommer « interrompu » ferait chercher un texte partiel qui n'existe pas.
    """
    redaction = Redaction(None)
    cause = RuntimeError("fournisseur indisponible")

    # L'échantillon fautif de la sonde : la même classe, après un morceau publié.
    assert redaction.interruption(cause) is cause
    asyncio.run(redaction.ecrire("Je regarde"))
    assert isinstance(redaction.interruption(cause), FluxInterrompu)


# ─────────── ③ Un fil d'avant les conversations en devient une (#694) ───────────


def _ecrire_un_fil_d_avant(racine, agent="qa", contenus=("Bonjour", "Salut")):
    """Un `<agent>.jsonl` tel qu'il était écrit **avant** #694 : sans le champ
    `conversation`, à l'emplacement historique.

    Écrit à la main et non par `ChatStore.ajouter` : c'est tout l'objet du
    critère — ce fichier vient d'une installation d'avant le lot, et le code
    d'aujourd'hui ne doit rien exiger de plus que ce qu'il porte.
    """
    racine.mkdir(parents=True, exist_ok=True)
    chemin = racine / f"{agent}.jsonl"
    lignes = [
        {
            "agent": agent,
            "auteur": UTILISATEUR if index % 2 == 0 else "agent",
            "contenu": contenu,
            "horodatage": f"2026-08-0{index + 1}T10:00:00+00:00",
        }
        for index, contenu in enumerate(contenus)
    ]
    chemin.write_text(
        "".join(json.dumps(ligne, ensure_ascii=False) + "\n" for ligne in lignes),
        encoding="utf-8",
    )
    return chemin


def test_un_fil_d_avant_le_lot_ne_porte_pas_de_conversation(tmp_path):
    """L'échantillon fautif de cette section : sans lui, les trois tests suivants
    diraient « la rétro-compatibilité marche » d'un fichier qui porterait déjà le
    champ, c'est-à-dire d'une question jamais posée.
    """
    chemin = _ecrire_un_fil_d_avant(tmp_path / "chat")

    lignes = [json.loads(ligne) for ligne in chemin.read_text(encoding="utf-8").splitlines()]
    assert lignes != []
    assert all("conversation" not in ligne for ligne in lignes)


def test_le_fichier_historique_se_relit_sous_origine(tmp_path):
    """« Un fichier écrit avant ce lot **devient** une conversation sans être ni
    déplacé, ni réécrit, ni relu autrement. »

    C'est le chemin qui fait foi : `origine` est stockée là où le fil l'a
    toujours été, et la ligne qui ne porte pas de conversation vient forcément
    de là.
    """
    racine = tmp_path / "chat"
    _ecrire_un_fil_d_avant(racine)
    store = ChatStore(racine)

    fil = store.fil("qa")

    assert [message.contenu for message in fil] == ["Bonjour", "Salut"]
    assert {message.conversation for message in fil} == {CONVERSATION_ORIGINE}


def test_relire_un_fil_d_avant_ne_touche_pas_a_ses_octets(tmp_path):
    """Le critère interdit la migration, donc il interdit l'écriture.

    Comparé **sur les octets** et non sur ce qui se relit : un aller-retour qui
    ré-encoderait rendrait le même objet Python en ayant réécrit le fichier —
    c'est le piège du mojibake de #141, sur un autre objet.
    """
    racine = tmp_path / "chat"
    chemin = _ecrire_un_fil_d_avant(racine)
    avant = chemin.read_bytes()
    store = ChatStore(racine)

    store.fil("qa")
    store.conversations("qa")
    store.courante("qa")

    assert chemin.read_bytes() == avant
    # Et rien n'a été créé à côté : ni dossier par agent, ni fichier annexe de
    # métadonnées — celles-ci sont **dérivées**, jamais tenues à part.
    assert sorted(p.name for p in racine.iterdir()) == ["qa.jsonl"]


def test_un_fil_d_avant_est_liste_avec_son_titre_et_ses_dates(tmp_path):
    """Les métadonnées sont dérivées des messages, donc un JSONL ancien les a
    toutes — c'est ce qui fait qu'un fichier annexe aurait rendu ces
    conversations-là sans titre ni date.
    """
    racine = tmp_path / "chat"
    _ecrire_un_fil_d_avant(racine, contenus=("Ajoute la pagination", "C'est fait"))
    store = ChatStore(racine)

    (carte,) = store.conversations("qa")

    assert carte.id == CONVERSATION_ORIGINE
    assert carte.titre == "Ajoute la pagination"
    assert carte.messages == 2
    assert carte.debut == "2026-08-01T10:00:00+00:00"
    assert carte.derniere == "2026-08-02T10:00:00+00:00"


def test_un_agent_jamais_contacte_a_quand_meme_son_origine(tmp_path):
    """« Un agent sans aucune conversation » n'existe pas, donc « la plus
    récente » a toujours une réponse — sans quoi le premier message d'un fil
    neuf n'aurait nulle part où aller.
    """
    store = ChatStore(tmp_path / "chat")

    cartes = store.conversations("qa")

    assert [carte.id for carte in cartes] == [CONVERSATION_ORIGINE]
    assert store.courante("qa") == CONVERSATION_ORIGINE
    assert cartes[0].messages == 0


def test_le_titre_vient_du_premier_message_de_l_utilisateur():
    """Le titre est **dérivé**, et de l'utilisateur : celui de l'agent dirait ce
    que Maestro a répondu, pas ce dont on a parlé.
    """
    fil = (
        MessageChat(agent="qa", auteur="agent", contenu="Bonjour, que puis-je ?"),
        MessageChat(agent="qa", auteur=UTILISATEUR, contenu="Ajoute la pagination"),
    )

    assert titre_conversation(fil) == "Ajoute la pagination"
    assert titre_conversation(()) == ""


# ─────────── ④ Ouvrir une conversation neuve (#694) ───────────


def test_ouvrir_rend_201_et_la_carte_de_la_neuve(client_chat):
    """La moitié écran de « démarrer un nouveau chat » tient à ce `201` : c'est
    la carte rendue qui dit à quel fil le prochain message ira.
    """
    client_chat.post("/api/chat/qa/messages", json={"contenu": "Bonjour"})

    reponse = client_chat.post("/api/chat/qa/conversations")

    assert reponse.status_code == 201
    carte = reponse.json()["conversation"]
    assert carte["id"] != CONVERSATION_ORIGINE
    assert carte["messages"] == 0
    assert carte["titre"] == ""


def test_ouvrir_est_idempotent_tant_que_rien_n_a_ete_dit(client_chat):
    """Sans cette règle, deux clics sur « nouvelle conversation » laisseraient un
    historique de fils vides derrière eux, et le premier clic sur un agent jamais
    contacté doublerait son `origine` avant qu'elle ait servi.
    """
    premiere = client_chat.post("/api/chat/qa/conversations").json()["conversation"]
    seconde = client_chat.post("/api/chat/qa/conversations").json()["conversation"]

    assert premiere["id"] == seconde["id"]
    assert len(client_chat.get("/api/chat/qa/conversations").json()["conversations"]) == 1


def test_la_neuve_passe_devant_celle_qu_on_quitte(client_chat):
    """« L'ordre est celui de la dernière activité, et **ouvrir en est une** » —
    c'est ce qui départage les deux lectures de « la plus récente ». Sans cela,
    le premier message d'un nouveau fil retomberait dans l'ancien.
    """
    client_chat.post("/api/chat/qa/messages", json={"contenu": "Bonjour"})
    neuve = client_chat.post("/api/chat/qa/conversations").json()["conversation"]

    listees = client_chat.get("/api/chat/qa/conversations").json()["conversations"]

    assert [carte["id"] for carte in listees] == [neuve["id"], CONVERSATION_ORIGINE]
    # Et « la conversation courante » est bien celle-là : le fil servi sans
    # paramètre est le sien, encore vide.
    servi = client_chat.get("/api/chat/qa").json()
    assert servi["conversation"] == neuve["id"] and servi["messages"] == []


def test_ecrire_dans_une_ancienne_la_ramene_en_tete(client_chat):
    """L'autre moitié de la même règle, sans laquelle « la conversation
    courante » serait figée sur la dernière **ouverte** plutôt que sur la
    dernière **active**.
    """
    client_chat.post("/api/chat/qa/messages", json={"contenu": "Bonjour"})
    neuve = client_chat.post("/api/chat/qa/conversations").json()["conversation"]

    client_chat.post(
        "/api/chat/qa/messages",
        json={"contenu": "Encore une chose", "conversation": CONVERSATION_ORIGINE},
    )

    listees = client_chat.get("/api/chat/qa/conversations").json()["conversations"]
    assert [carte["id"] for carte in listees] == [CONVERSATION_ORIGINE, neuve["id"]]


def test_chaque_conversation_garde_son_fil(client_chat):
    """« Repartir de zéro avec le même agent » n'a de contenu que si les deux
    fils ne se mélangent pas — c'est ce que l'écran promet en listant les deux.
    """
    client_chat.post("/api/chat/qa/messages", json={"contenu": "Le fil d'avant"})
    neuve = client_chat.post("/api/chat/qa/conversations").json()["conversation"]["id"]
    client_chat.post(
        "/api/chat/qa/messages", json={"contenu": "Tout autre chose", "conversation": neuve}
    )

    ancien = client_chat.get(
        "/api/chat/qa", params={"conversation": CONVERSATION_ORIGINE}
    ).json()
    recent = client_chat.get("/api/chat/qa", params={"conversation": neuve}).json()

    assert [m["contenu"] for m in ancien["messages"]][0] == "Le fil d'avant"
    assert [m["contenu"] for m in recent["messages"]][0] == "Tout autre chose"
    assert ancien["conversation"] == CONVERSATION_ORIGINE
    assert recent["conversation"] == neuve


@pytest.mark.parametrize(
    ("identifiant", "attendu"),
    [
        ("../../secrets", 422),
        ("Pas Un Slug", 422),
        ("20260828t143012-inconnue", 404),
    ],
)
def test_trois_reponses_a_un_identifiant_et_elles_ne_se_confondent_pas(
    client_chat, identifiant, attendu
):
    """Mal formé → `422` : c'est la garde de traversée de chemin, la **même** que
    pour un nom d'agent, parce qu'un identifiant venu de l'API désigne un fichier
    tout autant. Bien formé mais inconnu → `404` : on n'adresse pas un fil qui
    n'existe pas.
    """
    reponse = client_chat.get("/api/chat/qa", params={"conversation": identifiant})

    assert reponse.status_code == attendu


def test_sans_parametre_ce_n_est_pas_une_erreur_mais_le_cas_nominal(client_chat):
    """La troisième réponse, et celle d'un appelant d'avant le lot : l'absence de
    `conversation` sert la plus récente.
    """
    client_chat.post("/api/chat/qa/messages", json={"contenu": "Bonjour"})

    servi = client_chat.get("/api/chat/qa").json()

    assert servi["conversation"] == CONVERSATION_ORIGINE
    assert [m["contenu"] for m in servi["messages"]][0] == "Bonjour"

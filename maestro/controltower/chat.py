"""Canal de chat utilisateur ↔ agent — fil persisté, répondeur et flux (#84, #268).

Premier lot du chat de la Control Tower (#82) : un utilisateur envoie un message
à un agent du catalogue et reçoit sa réponse, le fil étant **persisté** et
consultable par agent. Quatre briques, assemblées par `ServiceChat` et servies
par les endpoints `/api/chat` de l'app (`maestro.controltower.app`) :

- `MessageChat` : un message du fil (auteur « utilisateur » ou l'agent), prêt à
  voyager en JSON — la forme du REST et du stockage ;
- `ChatStore` : la persistance du fil, un fichier JSONL par agent
  (`core/chat/<agent>.jsonl` au POC, racine remplaçable par `MAESTRO_CHAT_DIR`) —
  en V1 elle passera en base (entité AGENT_MESSAGE, docs/03) sans changer ce
  contrat ;
- `RepondeurChat` : la production de la réponse — `RepondeurModele` confie le
  fil au fournisseur configuré (`ModelProvider.generate`), cadré par le playbook
  **courant** de l'agent (#76, rechargé à chaque message comme l'exécuteur) ;
  `RepondeurScripte` répond sans modèle (démo #65, tests #83) ;
- `ServiceChat` : le flux d'un envoi — persiste le message, le fait transiter
  par la **messagerie existante** (#44, `Mailbox` : requête vers la boîte de
  l'agent, réponse en retour) et publie chaque message en `chat.message` sur le
  **bus d'événements** (#46) — le WebSocket `/ws/evenements` diffuse donc le
  fil en temps réel, réponse comprise.

La réponse est générée **dans la requête** (POC mono-process) : le POST rend la
paire message/réponse, les clients temps réel voient le message utilisateur dès
sa publication puis la réponse quand elle tombe. Un agent-processus autonome
abonné à sa boîte pourra plus tard prendre le relais sans changer le contrat.

## Le streaming est un canal, pas une particularité d'un fil (#268)

`ServiceChat.diffuser` rend la réponse **au fur et à mesure**, en trames
`FragmentChat` (`debut` · `fragment` · `fin` · `erreur`, docs/05 §6.5) que
`GET /api/chat/{agent}/flux` sérialise en `text/event-stream`. Il ne connaît ni
l'orchestration, ni l'assistance, ni le catalogue : **tout fil** s'y diffuse, ce
qui fait de ce module le lieu du streaming et de #268 son premier appelant, non
son propriétaire.

Le point d'extension est `RepondeurChat.produire`, qui reçoit un `Incrementeur`
— « voici un morceau de plus » — et rend une `ReponseChat` complète. Son
implémentation par défaut appelle `repondre` et publie le texte **en un seul
incrément** : tout répondeur existant se diffuse donc sans changer une ligne, et
celui qui sait produire par morceaux (l'orchestration, #268 ; un fournisseur
capable de streamer le jour où la frontière `ModelProvider` l'exposera) n'a que
`produire` à surcharger. Le canal ne devient jamais un second chemin : `envoyer`
et `diffuser` passent tous deux par lui, donc persistent, acheminent et
diffusent exactement de la même façon.

## Ce qui découle d'un message est rattaché au fil (#268)

Un `MessageChat` peut porter un `run_id` et un `tache_id` : la réponse de
l'orchestration au message « ajoute la pagination » nomme ainsi le run qu'elle a
ouvert. Vides partout ailleurs (une conversation ordinaire ne rattache rien), ils
voyagent avec le message — stockage, REST, et `Event.run_id`/`Event.tache_id` sur
le bus, où ils existaient déjà.
"""

from __future__ import annotations

import asyncio
import json
import re
import unicodedata
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from maestro.agents.catalog import Agent
from maestro.agents.playbooks import PlaybookStore
from maestro.config import Settings, load_settings
from maestro.controltower.events import EVENEMENT_CHAT_MESSAGE, Event, EventBus
from maestro.messaging import (
    MESSAGE_REPONSE,
    MESSAGE_REQUETE,
    AgentMessage,
    Mailbox,
)
from maestro.providers.base import ModelProvider

#: L'acteur humain du chat : l'expéditeur des requêtes, le destinataire des
#: réponses — le pendant « utilisateur » d'un nom d'agent, côté messagerie (#44)
#: comme dans l'`auteur` des messages du fil.
UTILISATEUR = "utilisateur"

#: Auteurs d'un message du fil, tels que portés par `Event.statut` (le champ
#: libre du type `chat.message`) : de quoi distinguer les deux bulles côté UI.
AUTEUR_UTILISATEUR = "utilisateur"
AUTEUR_AGENT = "agent"

#: Les quatre types de trame d'un flux de réponse (docs/05 §6.5) : `debut` ouvre,
#: `fragment` incrémente, `fin` clôt en portant le message complet, `erreur` dit
#: qu'aucune réponse ne viendra. Ils vivent **ici**, avec le canal qui les émet,
#: et non dans les fixtures qui les imitaient avant #268 : deux vocabulaires pour
#: le même contrat, c'est la démo qui finit par diverger de ce que l'API sert.
FRAGMENT_CHAT_DEBUT = "debut"
FRAGMENT_CHAT_DELTA = "fragment"
FRAGMENT_CHAT_FIN = "fin"
FRAGMENT_CHAT_ERREUR = "erreur"

#: La publication d'un incrément de réponse — le seul geste que le canal demande
#: à un répondeur qui sait produire par morceaux. Attendable, parce que publier
#: peut céder la main (file, socket) ; sans valeur de retour, parce que le
#: répondeur n'a rien à apprendre de la diffusion.
Incrementeur = Callable[[str], Awaitable[None]]

#: Nom d'agent admissible comme fichier de stockage : slug sûr, sans séparateur
#: ni point — verrouille toute traversée de chemin depuis un nom venu de l'API
#: (même garde que `maestro.agents.store`).
_NOM_AGENT = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

#: Longueur de l'`objet` des messages inter-agents dérivés du chat : la ligne
#: « sujet » de la lettre (#44), un extrait — le contenu intégral vit en payload.
_LONGUEUR_OBJET = 80

#: Cadre de conversation ajouté au playbook de l'agent : le chat n'est pas une
#: tâche à livrer (le playbook exige « strictement le livrable ») mais un
#: échange direct avec un humain — on le dit explicitement au modèle.
_CADRE_CONVERSATION = """\
Contexte particulier : tu es en CONVERSATION DIRECTE avec un utilisateur humain
depuis la Control Tower de Maestro — ce n'est pas une tâche à livrer. Réponds au
dernier message de l'utilisateur, en français, de façon concise et utile, dans
les limites de ton rôle et de tes garde-fous. Si la demande sort de ton domaine,
dis-le et oriente vers l'agent compétent."""


def _horodatage() -> str:
    """Horodatage UTC ISO-8601, même précision que le journal (#8)."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def normaliser(texte: str) -> str:
    """Le texte réduit pour la comparaison : minuscules, sans accents ni ponctuation.

    « Où sont les COÛTS ? » et « ou est le cout » doivent tomber sur le même
    sujet : l'utilisateur tape vite, souvent sans accents. Deux canaux lisent du
    texte humain de cette façon — l'assistance (#123) pour trouver le sujet,
    l'orchestration (#268) pour reconnaître une demande de travail —, d'où une
    seule définition, ici, dans le socle qu'ils partagent déjà.
    """
    sans_accents = "".join(
        c
        for c in unicodedata.normalize("NFD", texte.lower())
        if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"[^a-z0-9]+", " ", sans_accents).strip()


class ReponseIndisponible(RuntimeError):
    """L'agent n'a pas pu produire de réponse (fournisseur en échec, réponse vide).

    Le message utilisateur, lui, est déjà persisté et diffusé : l'échec ne
    concerne que la réponse — l'API le traduit en 502, l'utilisateur peut
    relancer sans perdre le fil.
    """


@dataclass(frozen=True)
class MessageChat:
    """Un message du fil utilisateur ↔ agent, prêt à voyager en JSON.

    `agent` est le fil d'appartenance (le nom d'agent du catalogue) ; `auteur`
    l'émetteur : `UTILISATEUR` ou ce même nom d'agent. C'est la forme du REST
    (`GET /api/chat/{agent}`) et du stockage (`ChatStore`).

    `run_id` et `tache_id` (#268) rattachent au message **ce qui en découle** :
    le run que l'orchestration a ouvert en réponse à une demande, la tâche dont
    il est question. Chaînes vides partout ailleurs — un message ordinaire ne
    rattache rien, et une ligne écrite avant ce lot se relit à l'identique.
    """

    agent: str
    auteur: str
    contenu: str
    horodatage: str = field(default_factory=_horodatage)
    run_id: str = ""
    tache_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Réémet le message en dict JSON-sérialisable (la forme du REST)."""
        return {
            "agent": self.agent,
            "auteur": self.auteur,
            "contenu": self.contenu,
            "horodatage": self.horodatage,
            "run_id": self.run_id,
            "tache_id": self.tache_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MessageChat:
        """Reconstruit un message depuis sa forme `to_dict` (la ligne stockée)."""
        return cls(
            agent=data["agent"],
            auteur=data.get("auteur", UTILISATEUR),
            contenu=data.get("contenu", ""),
            horodatage=data.get("horodatage", ""),
            run_id=data.get("run_id", ""),
            tache_id=data.get("tache_id", ""),
        )


@dataclass(frozen=True)
class ReponseChat:
    """Ce qu'un répondeur rend : le texte, et ce qu'il a **ouvert** en le rendant.

    Le texte seul ne suffisait plus dès lors qu'un répondeur peut agir (#268) :
    l'orchestration qui lance un run doit pouvoir en nommer l'identifiant, sans
    quoi le fil dirait « c'est parti » sans dire vers quoi. `run_id`/`tache_id`
    sont vides pour tout répondeur qui se contente de parler — c'est-à-dire pour
    tous ceux d'avant ce lot, que l'implémentation par défaut de
    `RepondeurChat.produire` enveloppe sans qu'ils aient à la connaître.
    """

    contenu: str
    run_id: str = ""
    tache_id: str = ""


@dataclass(frozen=True)
class FragmentChat:
    """Une trame du flux d'une réponse (docs/05 §6.5) — la forme du SSE.

    `type` est l'un des `FRAGMENT_CHAT_*` ; `delta` porte l'incrément de texte
    (vide hors `fragment`), `message` le `MessageChat` complet sur la seule trame
    `fin` (`None` ailleurs). Sur `erreur`, `delta` porte la cause : une trame
    plutôt qu'une socket coupée, pour que le client sache **pourquoi** rien ne
    vient — le message utilisateur, lui, reste acquis.
    """

    type: str
    agent: str
    auteur: str = AUTEUR_AGENT
    delta: str = ""
    message: MessageChat | None = None

    def to_dict(self) -> dict[str, Any]:
        """Réémet la trame en dict JSON-sérialisable (le `data:` du SSE)."""
        return {
            "type": self.type,
            "agent": self.agent,
            "auteur": self.auteur,
            "delta": self.delta,
            "message": self.message.to_dict() if self.message is not None else None,
        }


class ChatStore:
    """Persistance du fil de conversation, un fichier JSONL par agent.

    Append-only : chaque message s'ajoute en fin de fichier
    (`<racine>/<agent>.jsonl`, une ligne JSON par message) — le fil se relit
    dans l'ordre d'écriture. Un seul écrivain à la fois au POC (l'API Control
    Tower) : le dépôt ne porte pas de verrou de concurrence.
    """

    def __init__(self, racine: Path) -> None:
        self._racine = racine

    @property
    def racine(self) -> Path:
        """La racine du dépôt (un fichier JSONL par agent)."""
        return self._racine

    @classmethod
    def default(cls, settings: Settings | None = None) -> ChatStore:
        """Le dépôt configuré : `MAESTRO_CHAT_DIR`, sinon `core/chat/` du dépôt."""
        settings = settings or load_settings()
        if settings.chat_dir:
            return cls(Path(settings.chat_dir))
        return cls(Path(__file__).resolve().parents[2] / "core" / "chat")

    def ajouter(self, message: MessageChat) -> MessageChat:
        """Ajoute `message` en fin de fil de son agent et le renvoie tel quel."""
        chemin = self._chemin(message.agent)
        self._racine.mkdir(parents=True, exist_ok=True)
        with chemin.open("a", encoding="utf-8") as fichier:
            fichier.write(json.dumps(message.to_dict(), ensure_ascii=False) + "\n")
        return message

    def fil(self, agent: str) -> tuple[MessageChat, ...]:
        """Le fil de `agent`, dans l'ordre d'écriture (vide si jamais contacté)."""
        chemin = self._chemin(agent)
        if not chemin.is_file():
            return ()
        return tuple(
            MessageChat.from_dict(json.loads(ligne))
            for ligne in chemin.read_text(encoding="utf-8").splitlines()
            if ligne.strip()
        )

    def agents(self) -> tuple[str, ...]:
        """Les noms d'agents ayant un fil persisté, triés (vide si aucun)."""
        if not self._racine.is_dir():
            return ()
        return tuple(
            sorted(
                chemin.stem
                for chemin in self._racine.glob("*.jsonl")
                if _NOM_AGENT.match(chemin.stem)
            )
        )

    def _chemin(self, agent: str) -> Path:
        """Le fichier du fil de `agent`, nom validé (jamais un chemin arbitraire)."""
        if not _NOM_AGENT.match(agent):
            raise ValueError(f"nom d'agent invalide : {agent!r} (slug [a-z0-9_-] attendu).")
        return self._racine / f"{agent}.jsonl"


class RepondeurChat(ABC):
    """Production de la réponse d'un agent au fil — le point d'injection du chat.

    `repondre` reçoit la fiche catalogue de l'agent (identité, modèle, playbook
    « du code ») et le fil complet, dernier message utilisateur inclus, et rend
    le texte de la réponse. Toute erreur levée est traduite par le service en
    `ReponseIndisponible` — un répondeur n'a pas à s'en soucier.
    """

    @abstractmethod
    async def repondre(self, agent: Agent, fil: Sequence[MessageChat]) -> str:
        """La réponse de `agent` au dernier message utilisateur du fil."""
        raise NotImplementedError

    async def produire(
        self,
        agent: Agent,
        fil: Sequence[MessageChat],
        *,
        incrementer: Incrementeur | None = None,
    ) -> ReponseChat:
        """La réponse complète, diffusée au passage si un `incrementer` est fourni.

        Le point d'extension du canal (#268), et le **seul** que `ServiceChat`
        appelle : `envoyer` le fait sans incrémenteur, `diffuser` avec. Par
        défaut il délègue à `repondre` et publie le texte en **un seul**
        incrément — un répondeur qui ne sait pas produire par morceaux se
        diffuse donc quand même, en une trame plutôt qu'en dix, sans rien
        connaître du flux.

        Le surcharger sert à deux choses, indépendantes : publier de vrais
        incréments au fil de la production, et rattacher au message ce que la
        réponse a **ouvert** (`ReponseChat.run_id`).
        """
        texte = await self.repondre(agent, fil)
        if incrementer is not None and texte:
            await incrementer(texte)
        return ReponseChat(contenu=texte)


class RepondeurModele(RepondeurChat):
    """Le répondeur réel : confie le fil au fournisseur configuré (#32/#69).

    Le prompt système est le playbook **courant** de l'agent (#76 : la version
    éditée du stockage si elle existe, sinon le prompt du code — relu à chaque
    message, comme l'exécuteur #78), complété du cadre de conversation. Le
    fournisseur est résolu paresseusement (`MAESTRO_PROVIDER`) : construire le
    répondeur ne coûte rien et ne lève pas d'erreur de config.
    """

    def __init__(
        self,
        provider: ModelProvider | None = None,
        playbooks: PlaybookStore | None = None,
    ) -> None:
        self._provider = provider
        self._playbooks = playbooks

    async def repondre(self, agent: Agent, fil: Sequence[MessageChat]) -> str:
        if self._provider is None:
            # Import local : ne tire la couche fournisseur (SDK…) qu'au premier
            # message — l'app se construit et se teste sans elle.
            from maestro.providers.factory import provider_from_settings

            self._provider = provider_from_settings()
        return await self._provider.generate(
            _transcription(fil),
            model=agent.modele,
            system_prompt=f"{self._playbook_courant(agent)}\n\n{_CADRE_CONVERSATION}",
        )

    def _playbook_courant(self, agent: Agent) -> str:
        """Le playbook effectif de `agent` : la version éditée (#76), sinon le code."""
        if self._playbooks is not None:
            courant = self._playbooks.lire(agent.nom)
            if courant is not None:
                return courant.contenu
        return agent.prompt_systeme


class RepondeurScripte(RepondeurChat):
    """Répondeur sans modèle : une réponse déterministe qui reflète le fil.

    Le levier de la démo locale (#65 : regarder l'UI vivre sans fournisseur ni
    authentification) et des tests d'API (#83) — même contrat que le répondeur
    réel, zéro réseau.
    """

    async def repondre(self, agent: Agent, fil: Sequence[MessageChat]) -> str:
        dernier = fil[-1].contenu if fil else ""
        return (
            f"Bien reçu : « {dernier} ». Je suis l'agent {agent.role} "
            f"(compétences : {', '.join(sorted(agent.competences))}) — réponse "
            "scriptée de démonstration, aucun modèle n'a été appelé."
        )


class ServiceChat:
    """Le flux d'un échange utilisateur ↔ agent, de l'envoi à la réponse (#84).

    `envoyer` persiste le message utilisateur, le fait transiter par la
    messagerie inter-agents (#44 : requête déposée dans la boîte de l'agent —
    un processus-agent abonné la verrait), publie l'événement `chat.message`
    sur le bus (#46 : le WebSocket diffuse le fil en temps réel), obtient la
    réponse du répondeur puis refait le même chemin en sens inverse. Si le
    répondeur échoue, le message utilisateur reste acquis (persisté, diffusé)
    et `ReponseIndisponible` est levée — relancer ne perd pas le fil.
    """

    def __init__(
        self,
        *,
        store: ChatStore,
        repondeur: RepondeurChat,
        mailbox: Mailbox,
        bus: EventBus,
    ) -> None:
        self._store = store
        self._repondeur = repondeur
        self._mailbox = mailbox
        self._bus = bus

    def fil(self, agent: str) -> tuple[MessageChat, ...]:
        """Le fil persisté de `agent`, dans l'ordre d'écriture."""
        return self._store.fil(agent)

    async def envoyer(self, agent: Agent, contenu: str) -> tuple[MessageChat, MessageChat]:
        """Envoie `contenu` à `agent` et rend la paire (message, réponse).

        Lève `ValueError` sur un contenu vide (rien n'est persisté) et
        `ReponseIndisponible` si la réponse ne peut pas être produite (le
        message utilisateur, lui, est déjà persisté et diffusé).
        """
        message = await self._ouvrir(agent, contenu)
        return message, await self._repondre(agent)

    async def diffuser(self, agent: Agent, contenu: str) -> AsyncIterator[FragmentChat]:
        """Envoie `contenu` à `agent` et rend la réponse **au fur et à mesure** (#268).

        Le même échange que `envoyer` — mêmes persistance, messagerie et
        diffusion `chat.message` —, rendu en trames : `debut`, autant de
        `fragment` que le répondeur produit d'incréments, puis `fin` portant le
        message complet. Une réponse impossible sort en trame `erreur` plutôt
        qu'en exception : la socket est déjà ouverte et le message utilisateur
        déjà acquis, dire pourquoi vaut mieux que couper. `ValueError` sur un
        contenu vide reste levée **avant** la première trame, là où l'appelant
        peut encore répondre 422.

        La production tourne dans une tâche à part et publie ses incréments dans
        une file que ce générateur draine : c'est ce qui fait qu'un fragment part
        vers le client dès qu'il existe, sans attendre le suivant. Un client qui
        se déconnecte en cours de route **ne l'annule pas** — la réponse a coûté
        ce qu'elle a coûté, elle finit d'être persistée et diffusée, et le fil la
        rendra à la reconnexion.
        """
        await self._ouvrir(agent, contenu)
        yield FragmentChat(type=FRAGMENT_CHAT_DEBUT, agent=agent.nom)

        file: asyncio.Queue[str | None] = asyncio.Queue()

        async def incrementer(delta: str) -> None:
            await file.put(delta)

        async def produire() -> MessageChat:
            try:
                return await self._repondre(agent, incrementer=incrementer)
            finally:
                # La sentinelle passe par le même canal que les incréments : elle
                # arrive donc **après** eux, et le drainage ne peut pas s'arrêter
                # sur une file qui n'a pas encore reçu son dernier fragment.
                await file.put(None)

        tache = asyncio.create_task(produire())
        try:
            while True:
                delta = await file.get()
                if delta is None:
                    break
                yield FragmentChat(type=FRAGMENT_CHAT_DELTA, agent=agent.nom, delta=delta)
            try:
                reponse = await tache
            except ReponseIndisponible as exc:
                yield FragmentChat(type=FRAGMENT_CHAT_ERREUR, agent=agent.nom, delta=str(exc))
                return
            yield FragmentChat(type=FRAGMENT_CHAT_FIN, agent=agent.nom, message=reponse)
        finally:
            if not tache.done():
                # Fermeture prématurée (client parti) : on laisse la réponse
                # s'achever, mais plus personne n'attend son résultat — sans ce
                # rattrapage, une `ReponseIndisponible` finirait en « exception
                # never retrieved » dans les journaux de l'API.
                tache.add_done_callback(lambda achevee: achevee.exception())

    async def _ouvrir(self, agent: Agent, contenu: str) -> MessageChat:
        """Persiste, achemine et diffuse le message utilisateur — le début des deux voies."""
        contenu = contenu.strip()
        if not contenu:
            raise ValueError("message vide : rien à envoyer à l'agent.")
        message = MessageChat(agent=agent.nom, auteur=UTILISATEUR, contenu=contenu)
        await self._acheminer(message, agent, type_message=MESSAGE_REQUETE)
        return message

    async def _repondre(
        self, agent: Agent, *, incrementer: Incrementeur | None = None
    ) -> MessageChat:
        """Produit la réponse, la persiste, l'achemine et la diffuse.

        Le seul endroit où le répondeur est appelé — `envoyer` et `diffuser` s'y
        rejoignent, à l'incrémenteur près. Toute erreur du répondeur, comme une
        réponse vide, devient `ReponseIndisponible` : le message utilisateur est
        déjà acquis, relancer ne perd pas le fil.
        """
        try:
            reponse = await self._repondeur.produire(
                agent, self._store.fil(agent.nom), incrementer=incrementer
            )
        except Exception as exc:
            raise ReponseIndisponible(
                f"l'agent {agent.nom} n'a pas pu répondre : {exc}"
            ) from exc
        texte = reponse.contenu.strip()
        if not texte:
            raise ReponseIndisponible(
                f"l'agent {agent.nom} a rendu une réponse vide."
            )

        message = MessageChat(
            agent=agent.nom,
            auteur=agent.nom,
            contenu=texte,
            run_id=reponse.run_id,
            tache_id=reponse.tache_id,
        )
        await self._acheminer(message, agent, type_message=MESSAGE_REPONSE)
        return message

    async def _acheminer(self, message: MessageChat, agent: Agent, *, type_message: str) -> None:
        """Persiste `message`, le poste dans la messagerie (#44) et le diffuse (#46).

        L'ordre — stockage, boîte aux lettres, bus — garantit qu'un client
        notifié par le WebSocket relit un fil REST déjà à jour (même principe
        que la pompe de l'app : l'état d'abord, la diffusion ensuite).
        """
        self._store.ajouter(message)
        utilisateur_emet = message.auteur == UTILISATEUR
        await self._mailbox.publish(
            AgentMessage(
                type=type_message,
                de_agent=UTILISATEUR if utilisateur_emet else agent.nom,
                a_agent=agent.nom if utilisateur_emet else UTILISATEUR,
                objet=message.contenu[:_LONGUEUR_OBJET],
                payload={"contenu": message.contenu},
            )
        )
        await self._bus.publish(
            Event(
                type=EVENEMENT_CHAT_MESSAGE,
                agent=agent.nom,
                role=agent.role,
                statut=AUTEUR_UTILISATEUR if utilisateur_emet else AUTEUR_AGENT,
                detail=message.contenu,
                # Ce que le message a ouvert (#268) voyage sur les champs que
                # l'événement portait déjà : un client temps réel apprend le run
                # en même temps que la réponse, sans relire le fil.
                run_id=message.run_id,
                tache_id=message.tache_id,
                horodatage=message.horodatage,
            )
        )


def _transcription(fil: Sequence[MessageChat]) -> str:
    """Le fil rendu en prompt : la conversation puis la consigne de réponse."""
    lignes = [
        f"{'Utilisateur' if m.auteur == UTILISATEUR else 'Toi'} : {m.contenu}" for m in fil
    ]
    return (
        "Fil de conversation avec l'utilisateur :\n\n"
        + "\n".join(lignes)
        + "\n\nRéponds au dernier message de l'utilisateur."
    )

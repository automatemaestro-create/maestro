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
celui qui sait produire par morceaux n'a que `produire` à surcharger. Le canal ne
devient jamais un second chemin : `envoyer` et `diffuser` passent tous deux par
lui, donc persistent, acheminent et diffusent exactement de la même façon.

Deux répondeurs le surchargent : l'orchestration (#268), qui écrit son verdict
puis ce qu'elle a ouvert, et **le répondeur modèle** (#693), qui consomme les
incréments du fournisseur. Ce second-là était le trou du dispositif — le point
d'extension existait, personne ne le remplissait côté modèle, si bien qu'un fil
servi par le vrai modèle se diffusait d'un bloc quoi qu'on branche en face. Il a
fallu l'ouvrir un cran plus bas : `ModelProvider.generate_stream` (#693) est la
génération par incréments de la frontière, dont l'implémentation par défaut rend
le texte entier en un morceau — un fournisseur qui ne sait pas streamer traverse
donc les deux étages sans être modifié, et rien ne se dégrade.

Le contrat que les deux tiennent est celui de la trame `fin` : le message complet
est **exactement** la concaténation des `delta`. `Redaction` en répond pour les
deux — c'est sa seule raison d'être — et un flux coupé en route se signale
(`FluxInterrompu`) au lieu de laisser lire un début de réponse comme une réponse.

## Le flux porte ce qu'un message porte — et pourquoi par un POST (#692)

Un message peut embarquer des **sources** (#482) et nommer le **projet** de la
fenêtre (#683). Le POST les portait, le flux non : `GET …/flux?contenu=…` prend
son contenu en paramètre d'URL, où l'on ne peut raisonnablement déclarer ni
identifiants de sources ni corps. Y basculer un fil aurait donc échangé un rendu
incrémental contre une fonctionnalité — c'est le transport, et lui seul, qui
barrait le consommateur.

`diffuser` accepte donc les mêmes `sources` qu'`envoyer`, et le canal a **deux**
entrées HTTP : `POST …/flux`, dont le corps est exactement celui de
`POST …/messages`, et le `GET` d'origine, conservé pour le cas sans source — seul
verbe qu'un `EventSource` sait ouvrir, et contrat déjà publié (#183/#268).

L'autre option — un `GET` référençant une **composition déjà déclarée** — a été
écartée, et c'est le genre de choix qu'on redécouvre : elle demandait un second
endpoint pour déclarer, un état composé à garder entre les deux appels puis à
ramasser, et elle éloignait le refus du moment de l'envoi. Un corps de POST fait
la même chose sans rien garder, et laisse au refus la forme qu'il a déjà sur
l'autre voie — un 422 `{motif, message, index}` (#315), levé **avant** la
première trame parce que `_ouvrir` précède le premier `yield`.

Deux verbes ne font pas deux chemins d'envoi : ils appellent tous deux
`diffuser`, qui passe par `_ouvrir` puis `_repondre` comme `envoyer`. La règle du
module vaut aussi pour ses entrées.

## S'arrêter à la demande n'est pas se déconnecter (#695)

Une génération en vol peut être **arrêtée** : `diffuser` nomme chaque échange
(`FragmentChat.echange`), `ServiceChat.interrompre(echange)` annule la production
et `POST /api/chat/{agent}/flux/{echange}/arret` en est le verbe HTTP.

Ce n'est **pas** un retour sur l'arbitrage de #268 — « un client qui se
déconnecte ne l'annule pas » — mais son pendant : une déconnexion est un
accident, dont on ne peut pas déduire une intention, et la réponse déjà payée
finit d'être produite ; un arrêt est un **acte**, et il est le seul à annuler.
Les deux régimes cohabitent sans se contredire parce qu'ils ne se ressemblent
qu'à l'écran.

Le principe de #268 — « la réponse a coûté ce qu'elle a coûté » — est **tenu**
jusque dans l'arrêt : ce qui a été produit avant lui est persisté comme réponse
(trame `interrompu`, qui la porte), au lieu d'être jeté. C'est ce qui donne son
sens à « ce qui a déjà été reçu reste au fil » : la portion reçue n'est pas un
état d'écran que le premier rechargement effacerait, c'est le message du fil.
Rien reçu, rien persisté — une trame `interrompu` sans message, et le fil ne
garde que la demande.

Une annulation arrivée **pendant** l'acheminement de la réponse complète ne
double rien : `_conclure_arret` regarde le fil avant d'écrire, et rend ce qui s'y
trouve déjà plutôt que d'y ajouter un second message.

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
import uuid
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from maestro.agents.catalog import Agent
from maestro.agents.playbooks import PlaybookStore
from maestro.config import Settings, load_settings
from maestro.controltower.events import EVENEMENT_CHAT_MESSAGE, Event, EventBus
from maestro.engine.guardrails import GardeFousIngestion
from maestro.messaging import (
    MESSAGE_REPONSE,
    MESSAGE_REQUETE,
    AgentMessage,
    Mailbox,
)
from maestro.providers.base import ModelProvider
from maestro.sources import (
    DepotTeleversements,
    RapportLecture,
    Source,
    composer_sources,
    contexte_markdown,
    extraire_sources,
    sources_depuis,
    sources_en_liste,
)

#: Relecture d'un rapport de lecture persisté — l'aller-retour JSON de #316,
#: aliasé ici pour que `MessageChat.from_dict` se lise comme `Source.from_dict`.
rapport_depuis = RapportLecture.from_dict

#: Lecture de la matière d'un message : des sources **résolues** au rapport de
#: lecture (#316). Même alias et même raison d'être que côté lancement
#: (`maestro.controltower.executions.LecteurSources`) — une source `url` part sur
#: le réseau, et `tests/conftest.py` (#195) exige qu'aucun test n'en ait besoin.
LecteurSources = Callable[[Sequence[Source]], RapportLecture]

#: L'acteur humain du chat : l'expéditeur des requêtes, le destinataire des
#: réponses — le pendant « utilisateur » d'un nom d'agent, côté messagerie (#44)
#: comme dans l'`auteur` des messages du fil.
UTILISATEUR = "utilisateur"

#: Auteurs d'un message du fil, tels que portés par `Event.statut` (le champ
#: libre du type `chat.message`) : de quoi distinguer les deux bulles côté UI.
AUTEUR_UTILISATEUR = "utilisateur"
AUTEUR_AGENT = "agent"

#: Les cinq types de trame d'un flux de réponse (docs/05 §6.5) : `debut` ouvre,
#: `fragment` incrémente, `fin` clôt en portant le message complet, `interrompu`
#: clôt un échange **arrêté à la demande** (#695) en portant ce qui en a été
#: persisté, `erreur` dit qu'aucune réponse ne viendra. Ils vivent **ici**, avec
#: le canal qui les émet, et non dans les fixtures qui les imitaient avant #268 :
#: deux vocabulaires pour le même contrat, c'est la démo qui finit par diverger
#: de ce que l'API sert.
#:
#: `interrompu` est distinct de `fin` parce que les deux ne disent pas la même
#: chose du texte qu'ils portent : `fin` annonce la réponse **entière**, celle
#: dont la concaténation des `delta` répond ; `interrompu` annonce ce qui a été
#: écrit avant l'arrêt. Les confondre ferait lire un texte tronqué comme une
#: réponse complète — la faute même que `FluxInterrompu` évite d'un autre côté.
FRAGMENT_CHAT_DEBUT = "debut"
FRAGMENT_CHAT_DELTA = "fragment"
FRAGMENT_CHAT_FIN = "fin"
FRAGMENT_CHAT_INTERROMPU = "interrompu"
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


class FluxInterrompu(RuntimeError):
    """Le fournisseur a lâché **après** avoir publié des incréments (#693).

    Un échec avant le premier incrément et un échec au milieu de la réponse se
    ressemblent de l'intérieur — même exception, même 502 — et ne se ressemblent
    pas du tout à l'écran : dans le premier cas il ne s'est rien affiché, dans le
    second l'utilisateur a **sous les yeux un texte qui s'est arrêté**, que rien
    ne distingue d'une réponse courte. Les confondre, c'est laisser lire comme
    une réponse ce qui est un début de réponse.

    Ce type ne change donc rien au traitement — `ServiceChat` l'enveloppe en
    `ReponseIndisponible` comme n'importe quel échec de répondeur, et le fil ne
    garde rien (voir `ServiceChat._repondre` : le message n'est persisté qu'une
    fois la réponse **entière**, donc un flux coupé ne laisse jamais de moitié de
    message dans le fil). Il change ce qui est **dit** : la cause nomme
    l'interruption, elle voyage jusqu'à la trame `erreur` du flux, et le client
    sait que ce qu'il affiche est à jeter.
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

    Trois autres champs sont venus avec les **sources** (#482, lot 1 de #481) —
    ce que le message **embarque**, là où les deux précédents disent ce qu'il
    **ouvre** —, et ils
    n'ont de valeur que sur un message d'utilisateur :

    - `sources` — la matière **résolue** que le message embarque (fichiers
      déposés, dossier de références, adresses), telle que la chaîne d'ingestion
      l'a rendue. Une liste vide dit « aucune source », et le fil est alors
      exactement celui d'avant ce lot ;
    - `rapport` — le **rapport de lecture** (#316) de cette matière : ce qui a
      été lu, tronqué ou ignoré, et ce que ça coûte. C'est lui que le critère 3
      demande de pouvoir consulter depuis le message qui a porté les sources ;
    - `contexte` — le Markdown extrait, **encadré comme donnée** par
      `contexte_markdown` (ENF-13) et par lui seul. Il est persisté et non
      recalculé, pour la raison qui rend le champ nécessaire : `Lecture.to_dict`
      **n'emporte pas** le `markdown` (à dessein — un rapport dit ce qu'une source
      coûte, pas ce qu'elle raconte), donc un fil relu du disque aurait un rapport
      complet et un contenu perdu, et l'agent cesserait de voir le document dès le
      tour suivant.

    Ils sont **absents des lignes JSONL écrites avant #482**, que `from_dict`
    relit sans broncher : un fil persisté ne se réécrit pas.
    """

    agent: str
    auteur: str
    contenu: str
    horodatage: str = field(default_factory=_horodatage)
    run_id: str = ""
    tache_id: str = ""
    sources: tuple[Source, ...] = ()
    rapport: RapportLecture | None = None
    contexte: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Réémet le message en dict JSON-sérialisable (la forme du REST).

        Le `contexte` n'y est **pas** : il est fait pour un prompt, pas pour un
        écran, et le rapatrier au navigateur enverrait le contenu intégral des
        documents à chaque relecture du fil — ce que `Lecture.to_dict` refuse déjà
        de faire, pour la même raison. Le stockage, lui, le garde (`to_ligne`).
        """
        return {
            "agent": self.agent,
            "auteur": self.auteur,
            "contenu": self.contenu,
            "horodatage": self.horodatage,
            "run_id": self.run_id,
            "tache_id": self.tache_id,
            "sources": sources_en_liste(self.sources),
            "rapport": self.rapport.to_dict() if self.rapport is not None else None,
        }

    @property
    def resume(self) -> str:
        """Ce que le message dit en une ligne — son texte, ou ce qu'il embarque.

        Un message peut n'être fait que de sources (#482 : déposer un cahier des
        charges *est* le message). Son texte est alors vide, et le rendre tel quel
        écrirait « Vous avez écrit à dev » sur une ligne vide du fil d'activité et
        laisserait une lettre inter-agents sans objet. Nommer les sources est la
        seule chose vraie à dire — jamais une phrase inventée, jamais un silence.
        """
        if self.contenu:
            return self.contenu
        if not self.sources:
            return ""
        noms = ", ".join(source.nom for source in self.sources if source.nom)
        return f"{len(self.sources)} source(s) jointe(s){f' : {noms}' if noms else ''}"

    def to_ligne(self) -> dict[str, Any]:
        """La forme **stockée** : celle du REST, plus le contexte extrait.

        Deux formes plutôt qu'une parce que les deux lecteurs n'ont pas le même
        besoin : l'écran veut savoir ce qui a été lu, le répondeur veut le lire.
        """
        ligne = self.to_dict()
        if self.contexte:
            ligne["contexte"] = self.contexte
        return ligne

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MessageChat:
        """Reconstruit un message depuis sa forme stockée (la ligne du JSONL).

        **Ne rejuge rien**, exactement comme `Source.from_dict` (#315) : c'est la
        relecture d'un message déjà accepté, et un fil écrit avant que les sources
        n'existent doit rester lisible après un durcissement des garde-fous. Les
        clés absentes retombent sur les défauts.
        """
        rapport = data.get("rapport")
        return cls(
            agent=data["agent"],
            auteur=data.get("auteur", UTILISATEUR),
            contenu=data.get("contenu", ""),
            horodatage=data.get("horodatage", ""),
            run_id=data.get("run_id", ""),
            tache_id=data.get("tache_id", ""),
            sources=tuple(sources_depuis(data.get("sources"))),
            rapport=rapport_depuis(rapport) if isinstance(rapport, Mapping) else None,
            contexte=str(data.get("contexte") or ""),
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
    (vide hors `fragment`). Sur `erreur`, `delta` porte la cause : une trame
    plutôt qu'une socket coupée, pour que le client sache **pourquoi** rien ne
    vient — le message utilisateur, lui, reste acquis.

    `message` porte un `MessageChat` complet sur les trames qui **bornent** un
    échange : le message **utilisateur** sur `debut`, la **réponse** sur `fin`,
    et sur `interrompu` (#695) ce qui a été persisté de la réponse arrêtée —
    `None` quand l'arrêt précède le premier incrément, comme sur `fragment` et
    `erreur`. Le premier est venu avec les sources (#692) : sans lui, un client
    du flux aurait envoyé de la matière sans jamais savoir ce qui en a été lu,
    tronqué ou ignoré (le `rapport` de #316), là où `POST …/messages` rend la
    paire d'un seul coup. Le flux rend donc la même paire, en deux trames.

    `echange` nomme le flux lui-même (#695) et voyage sur **toutes** les trames :
    c'est ce que le client rend à `POST …/flux/{echange}/arret` pour arrêter la
    génération. Le poser sur `debut` seul aurait suffi au client d'aujourd'hui et
    obligé chacun à le retenir ; il est une propriété du flux, comme `agent` et
    `auteur`, et se lit donc sur la trame qu'on a sous la main.

    `auteur` reste celui du **flux** — l'agent qui répond —, sur toutes les
    trames : c'est une propriété de la réponse en cours, pas du message
    transporté, lequel porte son propre `auteur`.
    """

    type: str
    agent: str
    auteur: str = AUTEUR_AGENT
    delta: str = ""
    message: MessageChat | None = None
    echange: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Réémet la trame en dict JSON-sérialisable (le `data:` du SSE)."""
        return {
            "type": self.type,
            "agent": self.agent,
            "auteur": self.auteur,
            "delta": self.delta,
            "message": self.message.to_dict() if self.message is not None else None,
            "echange": self.echange,
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
            # `to_ligne` et non `to_dict` : le stockage garde en plus le contexte
            # extrait des sources (#482), que le REST n'emporte pas — sans lui, un
            # fil relu du disque perdrait le contenu des documents joints et
            # l'agent cesserait de les voir dès le tour suivant.
            fichier.write(json.dumps(message.to_ligne(), ensure_ascii=False) + "\n")
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


class Redaction:
    """Une réponse qui s'écrit par morceaux, et se diffuse au fur et à mesure (#268).

    Chaque morceau part vers le flux dès qu'il est écrit (quand un incrémenteur
    est là) **et** s'accumule : le texte final est **exactement** la concaténation
    des incréments publiés, ce dont le contrat SSE dépend — la trame `fin` porte
    le message complet, et un client doit pouvoir le reconstituer des `delta`
    seuls.

    Elle vit **ici** depuis #693, avec l'`Incrementeur` qu'elle sert, et non plus
    chez l'orchestration qui l'avait écrite la première : deux répondeurs
    produisent désormais par morceaux, et deux accumulateurs écrits côte à côte
    finiraient par ne plus tenir le même invariant — celui-là est trop facile à
    casser d'un `strip()` de plus pour être défini deux fois.

    ⚠ « Exactement » demande un geste, et c'est le seul que cette classe ait.
    `ServiceChat._repondre` **rase** le texte final ; publier tel quel un flux qui
    commence ou finit par des blancs ferait donc mentir l'invariant d'un retour à
    la ligne — assez pour qu'un client qui recolle ses `delta` n'obtienne pas la
    trame `fin`. Les blancs de tête sont écartés, ceux de queue **retenus**
    jusqu'à ce qu'un morceau non blanc les suive (ils sont alors intérieurs au
    texte, et publiés avec lui) ou jusqu'à la fin (ils ne partent jamais). Rien
    n'est ajouté, rien n'est réordonné : ce qui sort est le `strip()` du flux,
    découpé là où le fournisseur l'a découpé.
    """

    def __init__(self, incrementer: Incrementeur | None) -> None:
        self._incrementer = incrementer
        self._morceaux: list[str] = []
        # Les blancs de queue vus au dernier morceau : on ne sait pas encore
        # s'ils sont intérieurs au texte ou à la fin de la réponse.
        self._retenus = ""

    async def ecrire(self, morceau: str) -> None:
        """Ajoute `morceau` à la réponse et publie ce qui est acquis."""
        candidat = self._retenus + morceau
        if not self._morceaux:
            candidat = candidat.lstrip()
        corps = candidat.rstrip()
        self._retenus = candidat[len(corps) :]
        if not corps:
            return
        self._morceaux.append(corps)
        if self._incrementer is not None:
            await self._incrementer(corps)

    @property
    def texte(self) -> str:
        """La réponse écrite jusqu'ici — la concaténation exacte des incréments publiés."""
        return "".join(self._morceaux)

    @property
    def diffusee(self) -> bool:
        """Un incrément est-il déjà parti ? — donc : y a-t-il du texte à l'écran ?"""
        return bool(self._morceaux)

    def interruption(self, cause: BaseException) -> BaseException:
        """L'échec à relayer, **nommé** quand des incréments sont déjà partis (#693).

        Rend `cause` telle quelle tant que rien n'a été publié : il ne s'est rien
        affiché, l'échec est celui de n'importe quel répondeur et il n'y a rien à
        ajouter. Une fois le premier incrément parti, l'échec change de nature —
        pas de gravité — et devient un `FluxInterrompu` qui le dit, parce que
        c'est la seule information que le client ne peut pas déduire : il voit du
        texte, et rien ne lui apprendrait qu'il est incomplet.
        """
        if not self._morceaux:
            return cause
        return FluxInterrompu(
            f"réponse interrompue après {len(self.texte)} caractère(s) déjà diffusé(s) "
            f"— ce qui s'affiche est incomplet : {cause}"
        )


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
        projet_id: str | None = None,
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

        `projet_id` (#683) est le **projet de la fenêtre** d'où le message part —
        ni une portée de lecture, ni une propriété du fil, qui reste transverse
        (#281) : la valeur ne touche ni le message persisté ni l'événement
        diffusé. Elle n'intéresse que le répondeur qui **agit** — l'orchestration
        y rattache le run qu'elle ouvre —, d'où la valeur par défaut ici : un
        répondeur qui n'ouvre rien n'a rien à en faire et n'a pas à la connaître.
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
        """La réponse en un aller — `produire` est la voie qui diffuse (#693)."""
        return await self._resolu().generate(
            transcription(fil),
            model=agent.modele,
            system_prompt=self._systeme(agent),
        )

    async def produire(
        self,
        agent: Agent,
        fil: Sequence[MessageChat],
        *,
        incrementer: Incrementeur | None = None,
        projet_id: str | None = None,
    ) -> ReponseChat:
        """La réponse du modèle, publiée **au fil de son arrivée** (#693).

        Ce répondeur n'avait pas surchargé `produire`, donc l'implémentation par
        défaut publiait la réponse en **un seul** incrément : un fil servi par le
        vrai modèle se « diffusait » d'un bloc, et brancher le front sur le flux
        n'y aurait rien changé. Il consomme désormais `generate_stream`, la
        génération par incréments de la frontière — et comme celle-ci a une
        implémentation par défaut, **un fournisseur qui ne sait pas streamer
        produit exactement ce qu'il produisait avant** : un incrément, celui de
        `generate`. Le comportement ne se dégrade donc jamais, il s'affine quand
        le fournisseur sait le faire.

        `projet_id` n'est pas lu : un répondeur qui parle sans rien ouvrir n'a
        rien à rattacher (contrat de `RepondeurChat.produire`).

        Le texte rendu est la concaténation **exacte** des incréments publiés —
        `Redaction` en répond —, et un flux qui casse en cours de route lève un
        échec qui le **nomme** : le fil, lui, ne garde rien (il n'est écrit qu'au
        retour de cette méthode), mais l'écran, si.
        """
        redaction = Redaction(incrementer)
        try:
            async for morceau in self._resolu().generate_stream(
                transcription(fil),
                model=agent.modele,
                system_prompt=self._systeme(agent),
            ):
                await redaction.ecrire(morceau)
        except Exception as exc:
            interrompu = redaction.interruption(exc)
            if interrompu is exc:
                raise
            raise interrompu from exc
        return ReponseChat(contenu=redaction.texte)

    def _resolu(self) -> ModelProvider:
        """Le fournisseur configuré, résolu au premier usage (`MAESTRO_PROVIDER`)."""
        if self._provider is None:
            # Import local : ne tire la couche fournisseur (SDK…) qu'au premier
            # message — l'app se construit et se teste sans elle.
            from maestro.providers.factory import provider_from_settings

            self._provider = provider_from_settings()
        return self._provider

    def _systeme(self, agent: Agent) -> str:
        """Le prompt système des deux voies : playbook courant + cadre de conversation."""
        return f"{self._playbook_courant(agent)}\n\n{_CADRE_CONVERSATION}"

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
        televersements: DepotTeleversements | None = None,
        garde_fous_ingestion: GardeFousIngestion | None = None,
        lecteur_sources: LecteurSources | None = None,
    ) -> None:
        self._store = store
        self._repondeur = repondeur
        self._mailbox = mailbox
        self._bus = bus
        # Résolus paresseusement : un service qui ne reçoit jamais de source ne
        # touche pas au dépôt de téléversement et ne crée aucun dossier.
        self._televersements = televersements
        self._ingestion = (
            garde_fous_ingestion if garde_fous_ingestion is not None else GardeFousIngestion()
        )
        # Injectable pour la même raison qu'au lancement (#317) : une source `url`
        # part sur le réseau, et `tests/conftest.py` (#195) exige qu'aucun test
        # n'en ait besoin.
        self._lecteur = lecteur_sources if lecteur_sources is not None else extraire_sources
        # Les générations en vol, par identifiant d'échange (#695) : c'est le seul
        # état que le service garde entre deux requêtes, et il ne dure que le temps
        # d'un flux — `diffuser` l'inscrit à l'ouverture et le retire dans son
        # `finally`, quelle que soit la façon dont l'échange se termine.
        self._en_vol: dict[str, asyncio.Task[MessageChat]] = {}

    def fil(self, agent: str) -> tuple[MessageChat, ...]:
        """Le fil persisté de `agent`, dans l'ordre d'écriture."""
        return self._store.fil(agent)

    async def envoyer(
        self,
        agent: Agent,
        contenu: str,
        sources: Sequence[Mapping[str, Any] | Source] | None = None,
        *,
        projet_id: str | None = None,
    ) -> tuple[MessageChat, MessageChat]:
        """Envoie `contenu` et ses `sources` à `agent` ; rend la paire (message, réponse).

        `sources` est la matière que le message embarque (#482), déclarée dans
        **l'ordre où l'écran l'a composée** — celui qui décide de ce qui entre
        quand le budget de tokens s'épuise (#316). Un fichier y voyage par
        l'`id` que `POST /api/sources` lui a rendu, comme au lancement : le fil
        emprunte la chaîne d'ingestion existante, il n'en ouvre pas une seconde.

        Un message **sans texte mais avec des sources** est accepté : déposer un
        cahier des charges *est* le message. Sans texte **ni** sources, il n'y a
        rien à envoyer et c'est toujours un `ValueError`.

        Trois façons d'échouer, et elles ne se confondent pas :

        - `SourceRefusee` (donc `ValueError`) — une saisie que l'utilisateur peut
          corriger : plafond dépassé, racine interdite, type inconnu. Elle est
          levée **avant toute écriture**, comme au lancement : un refus ne doit
          laisser ni message au fil, ni événement sur le bus ;
        - `ValueError` nu — message vide ;
        - `ReponseIndisponible` — le message utilisateur, lui, est déjà acquis
          (persisté et diffusé) : relancer ne perd pas le fil.

        Ce qui est simplement **illisible** n'échoue pas : une source au format
        non géré (une image, aujourd'hui) ressort en ligne « ignoré » du rapport,
        avec son motif. C'est la distinction de #316 — « rien à lire ici » et
        « je refuse de lire ça » ne se disent jamais pareil.

        `projet_id` (#683) accompagne la **réponse** et non le message : voir
        `RepondeurChat.produire`. Le fil ne devient pas cadré pour autant — il
        n'a pas de périmètre à respecter (#281) —, c'est ce qu'une réponse
        **ouvre** qui en a un.
        """
        message = await self._ouvrir(agent, contenu, sources)
        return message, await self._repondre(agent, projet_id=projet_id)

    async def diffuser(
        self,
        agent: Agent,
        contenu: str,
        sources: Sequence[Mapping[str, Any] | Source] | None = None,
        *,
        projet_id: str | None = None,
    ) -> AsyncIterator[FragmentChat]:
        """Envoie `contenu` et ses `sources` à `agent`, réponse **au fur et à mesure** (#268).

        Le même échange que `envoyer` — mêmes persistance, messagerie et
        diffusion `chat.message` —, rendu en trames : `debut` portant le message
        utilisateur, autant de `fragment` que le répondeur produit d'incréments,
        puis `fin` portant la réponse complète. Une réponse impossible sort en
        trame `erreur` plutôt qu'en exception : la socket est déjà ouverte et le
        message utilisateur déjà acquis, dire pourquoi vaut mieux que couper.

        `sources` (#692) est la **même** matière, déclarée de la même façon et
        dans le même ordre, que sur `envoyer` : les deux voies mènent au même
        `_ouvrir`, donc à la même chaîne d'ingestion, aux mêmes identifiants et
        aux mêmes garde-fous. Ce qui la déclare est un corps de requête — voir
        l'arbitrage en tête de module.

        Deux refus restent levés **avant** la première trame, là où l'appelant
        peut encore répondre 422 plutôt que d'ouvrir un flux sur une erreur :
        `ValueError` nu sur un message vide, et `SourceRefusee` sur une source
        hors bornes — cette dernière portant son motif et son index, sans quoi
        « une source a été refusée » n'apprendrait pas laquelle.

        La production tourne dans une tâche à part et publie ses incréments dans
        une file que ce générateur draine : c'est ce qui fait qu'un fragment part
        vers le client dès qu'il existe, sans attendre le suivant. Un client qui
        se déconnecte en cours de route **ne l'annule pas** — la réponse a coûté
        ce qu'elle a coûté, elle finit d'être persistée et diffusée, et le fil la
        rendra à la reconnexion. Seul un **arrêt demandé** l'annule
        (`interrompre`, #695) : voir l'en-tête du module, une déconnexion est un
        accident et un arrêt est un acte.

        `projet_id` (#683) suit le même chemin que dans `envoyer`, et pour la
        même raison : les deux voies mènent au **même** `_repondre`, donc un run
        ouvert depuis le flux se rattache comme un run ouvert depuis le POST. Le
        canal ne devient jamais un second chemin — c'est la règle du module.
        """
        message = await self._ouvrir(agent, contenu, sources)
        # L'échange se nomme **après** `_ouvrir` : un message refusé n'a jamais
        # de flux, donc jamais d'identifiant à arrêter.
        echange = uuid.uuid4().hex[:12]
        # La trame d'ouverture porte le message utilisateur **résolu** — ses
        # sources et leur rapport de lecture (#316) —, que seul `_ouvrir` connaît
        # et qu'aucune trame suivante ne redira. C'est le pendant, sur cette
        # voie, de la paire que `POST …/messages` rend d'un coup (#692).
        yield FragmentChat(
            type=FRAGMENT_CHAT_DEBUT, agent=agent.nom, echange=echange, message=message
        )

        file: asyncio.Queue[str | None] = asyncio.Queue()

        async def incrementer(delta: str) -> None:
            await file.put(delta)

        async def produire() -> MessageChat:
            try:
                return await self._repondre(
                    agent, incrementer=incrementer, projet_id=projet_id
                )
            finally:
                # La sentinelle passe par le même canal que les incréments : elle
                # arrive donc **après** eux, et le drainage ne peut pas s'arrêter
                # sur une file qui n'a pas encore reçu son dernier fragment.
                await file.put(None)

        tache = asyncio.create_task(produire())
        self._en_vol[echange] = tache
        # Ce que le client a **vu** — la seule mesure fiable de « ce qui a déjà
        # été reçu », prise là où les trames partent pour de bon. `Redaction` en
        # garantit l'autre moitié : la concaténation des incréments est
        # exactement le texte de la réponse.
        recu: list[str] = []
        try:
            while True:
                delta = await file.get()
                if delta is None:
                    break
                recu.append(delta)
                yield FragmentChat(
                    type=FRAGMENT_CHAT_DELTA, agent=agent.nom, echange=echange, delta=delta
                )
            try:
                reponse = await tache
            except asyncio.CancelledError:
                # Un arrêt demandé — et lui seul : si c'est **ce** générateur
                # qu'on annule (client parti pendant l'attente), la tâche n'est
                # pas annulée et l'annulation nous traverse comme avant.
                if not tache.cancelled():
                    raise
                yield FragmentChat(
                    type=FRAGMENT_CHAT_INTERROMPU,
                    agent=agent.nom,
                    echange=echange,
                    message=await self._conclure_arret(agent, "".join(recu)),
                )
                return
            except ReponseIndisponible as exc:
                yield FragmentChat(
                    type=FRAGMENT_CHAT_ERREUR, agent=agent.nom, echange=echange, delta=str(exc)
                )
                return
            yield FragmentChat(
                type=FRAGMENT_CHAT_FIN, agent=agent.nom, echange=echange, message=reponse
            )
        finally:
            self._en_vol.pop(echange, None)
            if not tache.done():
                # Fermeture prématurée (client parti) : on laisse la réponse
                # s'achever, mais plus personne n'attend son résultat — sans ce
                # rattrapage, une `ReponseIndisponible` finirait en « exception
                # never retrieved » dans les journaux de l'API.
                tache.add_done_callback(lambda achevee: achevee.exception())

    def interrompre(self, echange: str) -> bool:
        """Arrête la génération en vol nommée par `echange` — rend `False` s'il n'y en a pas (#695).

        Le **seul** geste qui annule une production : une déconnexion ne le fait
        pas (voir `diffuser`). Synchrone à dessein — annuler est immédiat, ce qui
        suit (persister ce qui a été reçu, clore le flux) appartient au générateur
        qui tient l'échange, pas à celui qui demande l'arrêt.

        `False` couvre les deux « rien à arrêter » qui ne se distinguent pas d'ici
        et n'appellent pas deux conduites : un identifiant inconnu, et un échange
        qui vient de se terminer. C'est une course normale — l'utilisateur clique
        au moment où la réponse tombe —, et l'appelant HTTP la traite comme telle.
        """
        tache = self._en_vol.get(echange)
        if tache is None or tache.done():
            return False
        tache.cancel()
        return True

    async def _conclure_arret(self, agent: Agent, recu: str) -> MessageChat | None:
        """Persiste ce qui a été reçu avant l'arrêt — la moitié « reste au fil » (#695).

        Ce qui a été produit a été payé (principe de #268) : l'arrêt ne le jette
        pas, il l'arrête. La portion reçue devient donc un message du fil, au même
        titre qu'une réponse courte — persistée, acheminée, diffusée en
        `chat.message` —, et non un état d'écran que le premier rechargement
        effacerait.

        Deux abstentions, et aucune n'est un cas de bord :

        - **la réponse est déjà passée** — l'annulation a atteint la tâche pendant
          `_acheminer`, après l'écriture au fil. On rend ce qui s'y trouve plutôt
          que d'y ajouter un second message, ce qui donnerait deux réponses à une
          question ;
        - **rien n'a été reçu** — arrêt avant le premier incrément : il n'y a pas
          de réponse tronquée, il n'y en a pas du tout, et le fil ne garde que la
          demande.
        """
        fil = self._store.fil(agent.nom)
        if fil and fil[-1].auteur != UTILISATEUR:
            return fil[-1]
        texte = recu.strip()
        if not texte:
            return None
        message = MessageChat(agent=agent.nom, auteur=agent.nom, contenu=texte)
        await self._acheminer(message, agent, type_message=MESSAGE_REPONSE)
        return message

    async def _ouvrir(
        self,
        agent: Agent,
        contenu: str,
        sources: Sequence[Mapping[str, Any] | Source] | None = None,
    ) -> MessageChat:
        """Persiste, achemine et diffuse le message utilisateur — le début des deux voies.

        C'est ici que la matière du message est résolue et lue (#482), et donc ici
        que le refus tombe : **avant** toute écriture, sans laisser ni message au
        fil ni événement sur le bus. Le placer sur la voie commune plutôt que dans
        `envoyer` est ce qui fait qu'un fil diffusé (#268) applique les mêmes
        plafonds qu'un fil posté — deux entrées, un seul jeu de garde-fous.

        `sources` arrive désormais des **deux** voies (#692) : le corps de
        `POST …/messages` comme celui de `POST …/flux`. Seul `GET …/flux` n'en
        porte jamais — rien ne déclare de matière sur une requête sans corps —,
        et c'est pour cette raison, et non par oubli, qu'il reste le verbe du cas
        sans source.
        """
        contenu = contenu.strip()
        declarees = list(sources or ())
        if not contenu and not declarees:
            raise ValueError("message vide : rien à envoyer à l'agent.")

        matiere, rapport = await self._lire(declarees)
        message = MessageChat(
            agent=agent.nom,
            auteur=UTILISATEUR,
            contenu=contenu,
            sources=matiere,
            rapport=rapport,
            contexte=contexte_markdown(rapport) if rapport is not None else "",
        )
        await self._acheminer(message, agent, type_message=MESSAGE_REQUETE)
        return message

    async def _repondre(
        self,
        agent: Agent,
        *,
        incrementer: Incrementeur | None = None,
        projet_id: str | None = None,
    ) -> MessageChat:
        """Produit la réponse, la persiste, l'achemine et la diffuse.

        Le seul endroit où le répondeur est appelé — `envoyer` et `diffuser` s'y
        rejoignent, à l'incrémenteur près, et depuis #683 au projet près. Toute
        erreur du répondeur, comme une réponse vide, devient
        `ReponseIndisponible` : le message utilisateur est déjà acquis, relancer
        ne perd pas le fil.
        """
        try:
            reponse = await self._repondeur.produire(
                agent,
                self._store.fil(agent.nom),
                incrementer=incrementer,
                projet_id=projet_id,
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

    async def _lire(
        self, declarees: Sequence[Mapping[str, Any] | Source]
    ) -> tuple[tuple[Source, ...], RapportLecture | None]:
        """Résout puis lit la matière d'un message — `((), None)` s'il n'y en a pas.

        Le pendant, pour un message, de ce que `ServiceExecutions` fait pour un
        run : la **même** chaîne (`composer_sources`, #482) et le **même** lecteur
        (#316). Aucune source, aucun fil et aucun dossier : un message de texte
        garde exactement son coût d'avant ce lot, et c'est ce qui rend le
        changement invisible pour qui ne joint rien.

        La `cle` d'ingestion est propre au **message** — un dossier par acte, comme
        `core/ingestion/<run_id>/` en est un par run. Elle suit le régime de
        rétention des runs, c'est-à-dire aucun ramassage aujourd'hui : ce lot
        hérite d'une question ouverte, il n'en ouvre pas une nouvelle.
        """
        if not declarees:
            return (), None
        if self._televersements is None:
            self._televersements = DepotTeleversements.default()
        # Le refus a lieu ici, **avant** toute écriture au fil : une source hors
        # bornes ne doit laisser ni message persisté, ni événement sur le bus.
        matiere = composer_sources(
            declarees,
            cle=f"chat-{uuid.uuid4().hex[:12]}",
            depot=self._televersements,
            garde_fous=self._ingestion,
        )
        # Dans un fil : la lecture ouvre des fichiers et peut récupérer une page
        # (#316), ce que la boucle de l'API ne doit pas porter.
        return matiere, await asyncio.to_thread(self._lecteur, matiere)

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
                objet=message.resume[:_LONGUEUR_OBJET],
                # Les sources voyagent **déclarées** et non lues : la lettre dit ce
                # que le message embarque, le contenu extrait a son seul chemin
                # (`contexte_markdown`, ENF-13) et n'a rien à faire dans une
                # boîte aux lettres. La clé est **absente** quand il n'y en a pas,
                # et non posée à `[]` : un message de texte doit produire la lettre
                # exacte d'avant #482, sans quoi un abonné de #44 verrait passer un
                # champ que rien dans le message ne justifie.
                payload={
                    "contenu": message.contenu,
                    **(
                        {"sources": sources_en_liste(message.sources)}
                        if message.sources
                        else {}
                    ),
                },
            )
        )
        await self._bus.publish(
            Event(
                type=EVENEMENT_CHAT_MESSAGE,
                agent=agent.nom,
                role=agent.role,
                statut=AUTEUR_UTILISATEUR if utilisateur_emet else AUTEUR_AGENT,
                detail=message.resume,
                # Ce que le message a ouvert (#268) voyage sur les champs que
                # l'événement portait déjà : un client temps réel apprend le run
                # en même temps que la réponse, sans relire le fil.
                run_id=message.run_id,
                tache_id=message.tache_id,
                horodatage=message.horodatage,
            )
        )


def transcription(fil: Sequence[MessageChat]) -> str:
    """Le fil rendu en prompt : la conversation puis la consigne de réponse.

    Publique pour la même raison que `normaliser` : deux canaux la partagent
    depuis #685 — le chat d'un agent (`RepondeurModele`) et le fil global, dont
    le répondeur confie désormais au modèle le soin de juger l'intention. Une
    seconde mise en forme du fil, écrite à côté, finirait par ne plus dire la
    même conversation que celle-ci — à commencer par le contenu des sources, que
    la boucle ci-dessous range **sous le message qui les a portées**.
    """
    lignes: list[str] = []
    for message in fil:
        lignes.append(
            f"{'Utilisateur' if message.auteur == UTILISATEUR else 'Toi'} : {message.resume}"
        )
        # Le contenu des sources **sous le message qui les a portées**, et jamais
        # rassemblé en fin de fil : c'est ce qui dit de quel tour de conversation
        # un document relève. Il entre déjà encadré comme donnée — `contexte` est
        # la sortie de `contexte_markdown` (ENF-13) et de rien d'autre, si bien
        # qu'il n'y a pas ici de second endroit où l'encadrement pourrait être
        # oublié.
        if message.contexte:
            lignes.append(message.contexte)
    return (
        "Fil de conversation avec l'utilisateur :\n\n"
        + "\n".join(lignes)
        + "\n\nRéponds au dernier message de l'utilisateur."
    )

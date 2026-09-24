"""Canal de chat utilisateur ↔ agent — fil persisté, répondeur et flux (#84, #268).

Premier lot du chat de la Control Tower (#82) : un utilisateur envoie un message
à un agent du catalogue et reçoit sa réponse, le fil étant **persisté** et
consultable par agent. Quatre briques, assemblées par `ServiceChat` et servies
par les endpoints `/api/chat` de l'app (`maestro.controltower.app`) :

- `MessageChat` : un message du fil (auteur « utilisateur » ou l'agent), prêt à
  voyager en JSON — la forme du REST et du stockage ;
- `Conversation` : un fil parmi ceux d'un agent (#694) — identifiant, titre
  **dérivé** de son premier message, instants d'ouverture et de dernière
  activité ;
- `ChatStore` : la persistance des fils, un fichier JSONL par **conversation**
  (`core/chat/` au POC, racine remplaçable par `MAESTRO_CHAT_DIR`) — en V1 elle
  passera en base (entité AGENT_MESSAGE, docs/03) sans changer ce contrat ;
- `RepondeurChat` : la production de la réponse — `RepondeurModele` confie le
  fil au fournisseur configuré (`ModelProvider.generate`), cadré par le playbook
  **courant** de l'agent (#76, rechargé à chaque message comme l'exécuteur) ;
  `RepondeurScripte` répond sans modèle (tests #83) ;
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
première trame parce que `_deposer` précède le premier `yield`.

Deux verbes ne font pas deux chemins d'envoi : ils appellent tous deux
`diffuser`, qui passe par `_deposer` puis `_repondre` comme `envoyer`. La règle
du module vaut aussi pour ses entrées.

## Un fil d'agent est une suite de conversations (#694)

Le fil d'un agent n'est plus un JSONL éternel : c'est une **suite de
conversations**, qu'on liste, qu'on rouvre, et dont on ouvre une neuve. Trois
décisions tiennent tout le dispositif, et la troisième est la plus importante.

**La conversation `origine` est celle qu'un agent a par défaut**, et elle est
stockée là où le fil l'a toujours été — `<racine>/<agent>.jsonl`. Les
conversations suivantes vivent sous `<racine>/<agent>/<id>.jsonl`. Un fichier
écrit avant ce lot **devient** donc une conversation sans être ni déplacé, ni
réécrit, ni relu autrement : c'est le critère 3 du ticket, et c'est ce qui fait
qu'un poste qui met à jour continue sa conversation au lieu d'en commencer une.

**Les métadonnées sont dérivées, jamais tenues à part.** Le titre vient du
premier message, les horodatages du premier et du dernier — un fichier annexe
qu'un JSONL antérieur n'aurait pas rendrait ces conversations-là sans titre ni
date. Le seul fait qu'aucun message ne porte est l'**instant d'ouverture** d'une
conversation encore vide : il voyage donc dans l'identifiant
(`20260828t143012-9f3a2b`), d'où il se relit sans rien ouvrir. Le jour où le
stockage passera en base, ces trois valeurs deviendront des colonnes sans que le
contrat REST bouge.

**Ouvrir est idempotent tant que rien n'a été dit** : si la conversation la plus
récente est vide, elle *est* la conversation neuve — rouvrir n'en fabrique pas
une seconde. Sans cette règle, cliquer deux fois sur « nouvelle conversation »
laisserait derrière lui un historique de fils vides, et un agent jamais contacté
verrait son `origine` doublée avant d'avoir servi.

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

## Ce qu'un message **demande** y est rattaché aussi (#943)

Troisième question portée par le même objet, après ce qu'il embarque et ce qu'il
ouvre : `proposition` est l'objectif qu'une réponse de l'orchestration soumet à
l'accord de l'utilisateur. Sans lui, une demande de cadrage n'existait **que
dans une phrase** — « Je lance ? » —, c'est-à-dire nulle part pour une surface :
le fil ne pouvait pas offrir de geste pour y répondre, et le panneau
« Cadrage en attente » affirmait « aucun » au moment même où la question était
posée (retex du 2026-09-11, constat G10).

Deux pièces vont avec, et aucune ne juge un texte :

- `proposition_en_attente` — la demande qu'un fil porte **encore**, énoncée une
  seule fois pour les deux côtés (le geste qui tranche, et les écrans qui la
  montrent) ;
- `ServiceChat.trancher_cadrage` — le **geste** : il écrit l'acte au fil, puis
  fait exécuter la décision par le répondeur sans repasser par le juge. Un
  accord au bouton n'est pas un texte à reconnaître, c'est un acte ; et un
  objectif amendé ne survivrait pas à un tour de jugement de plus.

## …et ce qu'il demande peut être une équipe (#1146)

Un projet sans agent ne peut rien faire d'un run : chaque tâche part en repli
« à assigner » après que le cadrage et le plan ont été payés. L'orchestration ne
lui propose donc pas de run, elle lui propose son **équipe** — et cette demande
vit sur le message comme les deux autres : `recrutement` (`DemandeRecrutement`),
l'objectif qui attend une équipe et le projet qui en manque. Mêmes pièces, même
règle : `recrutement_en_attente` dit si elle tient encore, et
`ServiceChat.recruter` est le geste qui y répond — il écrit l'acte au fil, puis
le répondeur crée l'équipe validée et reprend la demande d'origine.
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
from typing import TYPE_CHECKING, Any

from maestro.agents.catalog import Agent
from maestro.agents.playbook_du_code import registre
from maestro.agents.playbooks import PlaybookStore
from maestro.config import Settings, load_settings
from maestro.controltower.bornes import AUCUNE_BORNE, BornesRun
from maestro.controltower.events import EVENEMENT_CHAT_MESSAGE, Event, EventBus
from maestro.engine.guardrails import GardeFousIngestion
from maestro.equipe import RoleValide
from maestro.messaging import (
    MESSAGE_REPONSE,
    MESSAGE_REQUETE,
    AgentMessage,
    Mailbox,
)
from maestro.outillage.questionnaire import REPONSE_LIBRE_MAX, Choix, QuestionOutillage
from maestro.providers.base import ModelProvider
from maestro.sources import (
    DepotTeleversements,
    RapportLecture,
    Source,
    composer_sources,
    contexte_markdown,
    lecteur_par_defaut,
    sources_depuis,
    sources_en_liste,
)

if TYPE_CHECKING:  # le conducteur importe ce module : cycle à l'exécution seulement
    from maestro.controltower.outillage import ConducteurOutillage

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
#: deux vocabulaires pour le même contrat finissent par diverger de ce que l'API
#: sert.
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

#: La sixième (#1223) : une **étape**, ce que l'interlocuteur vient de faire pour
#: pouvoir répondre. Elle n'incrémente pas la réponse et ne la clôt pas — c'est
#: exactement pourquoi elle n'est pas un `fragment` : la concaténation des
#: `delta` **est** le texte final (`Redaction`), et y glisser « A lu README.md »
#: ferait mentir le contrat SSE sur lequel un client recolle son message.
FRAGMENT_CHAT_ETAPE = "etape"

#: La publication d'un incrément de réponse — le seul geste que le canal demande
#: à un répondeur qui sait produire par morceaux. Attendable, parce que publier
#: peut céder la main (file, socket) ; sans valeur de retour, parce que le
#: répondeur n'a rien à apprendre de la diffusion.
Incrementeur = Callable[[str], Awaitable[None]]

#: La publication d'une **étape** (#1223) — le second canal, et le seul autre que
#: le canal demande à un répondeur. Même forme que l'`Incrementeur` et même
#: raison : publier peut céder la main, et le répondeur n'a rien à apprendre de
#: la diffusion. Deux canaux plutôt qu'un parce que les deux ne portent pas la
#: même chose (voir `FRAGMENT_CHAT_ETAPE`), et un répondeur qui n'en a qu'un ne
#: connaît jamais l'autre.
Etapeur = Callable[["EtapeFil"], Awaitable[None]]

#: Nom d'agent admissible comme fichier de stockage : slug sûr, sans séparateur
#: ni point — verrouille toute traversée de chemin depuis un nom venu de l'API
#: (même garde que `maestro.agents.store`).
_NOM_AGENT = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

#: Identifiant de conversation admissible comme fichier — la **même** garde que
#: pour un nom d'agent (#694) : une traversée de chemin ne devient pas acceptable
#: parce que la valeur porte un autre nom, et celle-ci vient de l'API tout autant.
_ID_CONVERSATION = _NOM_AGENT

#: La conversation qu'un agent a **par défaut**, stockée là où le fil l'a
#: toujours été (`<racine>/<agent>.jsonl`) : c'est par elle qu'un JSONL antérieur
#: à #694 devient une conversation sans être ni déplacé ni réécrit. Elle est
#: toujours adressable et toujours listée — vide tant que rien n'y a été dit —,
#: si bien qu'« un agent sans aucune conversation » n'existe pas et que
#: « la plus récente » a toujours une réponse.
CONVERSATION_ORIGINE = "origine"

#: L'instant d'ouverture porté par l'identifiant d'une conversation neuve
#: (`<AAAAMMJJ>t<HHMMSS>-<hex>`) — le seul fait qu'aucun message ne peut dire
#: d'une conversation encore vide, et donc la seule chose que l'identifiant a à
#: porter. Absent d'`origine`, dont l'ouverture est celle de son premier message.
_ID_OUVERTURE = re.compile(r"^(\d{4})(\d{2})(\d{2})t(\d{2})(\d{2})(\d{2})-")

#: Longueur du titre dérivé d'une conversation : de quoi reconnaître un fil dans
#: une liste, pas de quoi le résumer — le fil lui-même est à un clic.
_LONGUEUR_TITRE = 60

#: Longueur de l'`objet` des messages inter-agents dérivés du chat : la ligne
#: « sujet » de la lettre (#44), un extrait — le contenu intégral vit en payload.
_LONGUEUR_OBJET = 80

#: Cadre de conversation ajouté au playbook de l'agent : le chat n'est pas une
#: tâche à livrer (le playbook exige « strictement le livrable ») mais un
#: échange direct avec un humain — on le dit explicitement au modèle.
#:
#: ⚠ Il **redit le registre** (#945), alors que le playbook de l'agent le porte déjà
#: par son socle. Ce n'est pas une recopie : un agent **personnalisé** (#72) a le
#: playbook que son auteur lui a écrit, lequel ne passe par aucun socle — et c'est
#: précisément en conversation directe, ici, qu'un registre à lui se verrait. La
#: source reste unique (`playbook_du_code.registre()`), seul le nombre de fois où on
#: la sert change.
_CADRE_CONVERSATION = f"""\
Contexte particulier : tu es en CONVERSATION DIRECTE avec un utilisateur humain
depuis la Control Tower de Maestro — ce n'est pas une tâche à livrer. Réponds au
dernier message de l'utilisateur, en français, de façon concise et utile, dans
les limites de ton rôle et de tes garde-fous. Si la demande sort de ton domaine,
dis-le et oriente vers l'agent compétent.

{registre()}"""


def _horodatage() -> str:
    """Horodatage UTC ISO-8601, même précision que le journal (#8)."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def _nouvel_id_conversation() -> str:
    """Un identifiant de conversation neuf, portant son instant d'ouverture (#694).

    Deux propriétés, et la seconde est la raison d'être de la forme : il est un
    slug (`_ID_CONVERSATION`), donc un nom de fichier sûr ; et il **porte sa
    date**, donc une conversation encore vide a une place dans l'ordre et une
    ligne de temps sans qu'aucun fichier annexe n'ait à la porter — un fichier
    qu'un JSONL antérieur à ce lot n'aurait de toute façon pas. Le suffixe
    aléatoire départage deux ouvertures dans la même seconde.
    """
    return f"{datetime.now(UTC):%Y%m%dt%H%M%S}-{uuid.uuid4().hex[:6]}"


def _ouverture_de(identifiant: str) -> str:
    """L'instant d'ouverture relu de `identifiant`, ou `""` s'il n'en porte pas.

    `""` est la réponse pour `origine`, qui n'a jamais été « ouverte » : son
    ouverture est celle de son premier message, et c'est `ChatStore` qui fait ce
    repli — ici on ne rend que ce que l'identifiant dit.
    """
    trouve = _ID_OUVERTURE.match(identifiant)
    if trouve is None:
        return ""
    annee, mois, jour, heure, minute, seconde = trouve.groups()
    return f"{annee}-{mois}-{jour}T{heure}:{minute}:{seconde}+00:00"


def titre_conversation(fil: Sequence[MessageChat]) -> str:
    """Le titre **dérivé** d'une conversation : ce que son premier message dit.

    Dérivé et non stocké, pour la raison qui commande tout le lot : un JSONL
    écrit avant #694 n'a pas de titre à relire, et lui en fabriquer un
    demanderait de le réécrire. Le premier message **de l'utilisateur** fait foi
    — c'est lui qui dit de quoi on a voulu parler, là où une réponse d'agent
    dirait ce qu'on lui a répondu ; à défaut, le premier message tout court.

    Un message fait de seules sources (#482) rend son `resume` (« 2 source(s)
    jointe(s) : … ») : nommer ce qui a été déposé est la seule chose vraie à
    dire, jamais une phrase inventée. Une conversation vide rend `""` — l'écran
    est le seul à savoir comment appeler un fil dont personne n'a rien dit.
    """
    premier = next((m for m in fil if m.auteur == UTILISATEUR), fil[0] if fil else None)
    if premier is None:
        return ""
    texte = " ".join(premier.resume.split())
    if len(texte) <= _LONGUEUR_TITRE:
        return texte
    coupe = texte[:_LONGUEUR_TITRE].rsplit(" ", 1)[0] or texte[:_LONGUEUR_TITRE]
    return f"{coupe}…"


def proposition_en_attente(fil: Sequence[MessageChat]) -> MessageChat | None:
    """La **demande de cadrage** que ce fil porte encore, `None` s'il n'y en a pas (#943).

    C'est le dernier message, et lui seul, quand il porte une `proposition` :
    une demande est en attente tant que **rien n'a suivi**. La règle est la
    lecture littérale de la propriété que `orchestration` tient déjà — « le fil
    est la seule mémoire », « le silence n'est pas un accord » : ce qui rend une
    proposition caduque n'est pas le temps, c'est qu'on ait répondu, quoi qu'on
    ait répondu.

    Elle est **énoncée une fois** et appelée par les deux côtés — le geste qui
    tranche (`ServiceChat.trancher_cadrage`) et les surfaces qui la montrent
    (`apps/web/lib/brief`) —, parce que c'est exactement la divergence que ce
    lot corrige : deux formulations de « y a-t-il un cadrage en attente ? »
    finissent par ne plus désigner la même chose, et l'écran affirme « aucun »
    pendant que la question est posée.
    """
    dernier = fil[-1] if fil else None
    if dernier is None or not dernier.proposition:
        return None
    return dernier


def question_en_attente(fil: Sequence[MessageChat]) -> MessageChat | None:
    """La **question d'outillage** que ce fil porte encore, `None` sinon (#1031).

    Le pendant exact de `proposition_en_attente`, sur l'autre demande que le canal
    sait porter, et **écrit deux fois exprès plutôt que factorisé** : les deux
    lisent le même dernier message, mais ce qui rend une demande caduque n'a pas à
    devenir un réglage d'une fonction commune. Une proposition et une question
    sont deux choses ; les fondre donnerait une fonction dont chaque appelant
    devrait dire laquelle il veut, c'est-à-dire la même question posée deux fois.

    La règle, elle, est la même — et c'est celle du module : **le fil est la seule
    mémoire**, donc une question attend tant que rien ne l'a suivie. Ce qui la
    solde n'est pas le temps, c'est qu'on y ait répondu.
    """
    dernier = fil[-1] if fil else None
    if dernier is None or dernier.question is None:
        return None
    return dernier


def recrutement_en_attente(fil: Sequence[MessageChat]) -> MessageChat | None:
    """La **demande de recrutement** que ce fil porte encore, `None` sinon (#1146).

    La troisième demande du canal, et la troisième écriture de la même règle —
    le dernier message, et lui seul —, pour la raison que `question_en_attente`
    donne déjà : ce qui solde une demande n'a pas à devenir le réglage d'une
    fonction commune. Une équipe proposée attend tant que rien n'a suivi ; ce qui
    la solde est qu'on y ait répondu, d'un geste ou d'une phrase.
    """
    dernier = fil[-1] if fil else None
    if dernier is None or dernier.recrutement is None:
        return None
    return dernier


def choix_du_fil(fil: Sequence[MessageChat]) -> tuple[Choix, ...]:
    """Les réponses d'outillage acquises sur ce fil, dans l'ordre où elles sont venues.

    Lues **structurellement**, sur le champ `choix` des messages, jamais dans leur
    texte : reconnaître « oui, Vitest » dans une phrase serait le lexique que ce
    canal a retiré (#685), et un questionnaire qui se relit par mots-clés répondrait
    autre chose que ce qui a été cliqué.

    C'est ce qui permet au conducteur de n'avoir aucune mémoire à lui : l'état du
    questionnaire **est** le fil, donc rouvrir la Control Tower, changer de poste ou
    recharger la page ne perd rien et ne reprend rien deux fois.
    """
    return tuple(m.choix for m in fil if m.choix is not None)


def _question_repondue(
    fil: Sequence[MessageChat],
) -> tuple[QuestionOutillage, str] | None:
    """La question d'outillage à laquelle le dernier message **répond par une frappe** (#1147).

    Le dernier message est de l'utilisateur et porte une réponse libre, et celui
    d'avant posait la question de ce sujet : c'est la forme exacte que `_deposer`
    écrit quand on tape pendant qu'une question attend. Lue **structurellement**,
    comme `choix_du_fil` — jamais en reconnaissant une réponse dans le texte.
    """
    if len(fil) < 2:
        return None
    dernier, avant = fil[-1], fil[-2]
    if dernier.auteur != UTILISATEUR or dernier.choix is None or not dernier.choix.libre:
        return None
    if avant.question is None or avant.question.cle != dernier.choix.cle:
        return None
    return avant.question, dernier.choix.valeur


def _geste_de_reponse(question: QuestionOutillage, valeur: str) -> str:
    """Ce que le geste écrit dans le fil — le message que le clic vaut (#1031).

    Même raison qu'en #1014 (`_geste_de_cadrage`) : le canal n'a pas d'autre mémoire
    que sa conversation, donc une réponse donnée au bouton doit s'y lire, et s'y lire
    comme une personne l'aurait écrite. Le **libellé** y va, pas la valeur : c'est ce
    qu'on a vu à l'écran, et `vitest` en toutes lettres dirait moins que « Vitest ».

    La valeur, elle, voyage sur `MessageChat.choix`, qui est ce que le conducteur
    relit. Le texte est pour l'œil, le champ pour la machine — et aucun des deux
    n'est dérivé de l'autre à la relecture.
    """
    return f"{question.intitule} → {question.libelle_de(valeur)}"


def _geste_de_cadrage(
    approuve: bool,
    retenu: str,
    demande: MessageChat,
    bornes: BornesRun = AUCUNE_BORNE,
) -> str:
    """Ce que le geste écrit dans le fil — le message que le clic vaut (#943).

    Le canal n'a pas d'autre mémoire que sa conversation, donc un accord donné
    au bouton doit s'y lire ; et il s'y lit comme une personne l'aurait écrit,
    parce que c'est le tour suivant qui le relira — les formulations sont celles
    que le contrat du juge donne lui-même en exemple d'accord et de refus
    (`orchestration._PROMPT_ORCHESTRATION`). Une trace que le juge ne
    reconnaîtrait pas serait une trace qui ment sur ce qui s'est passé.

    L'objectif **amendé** est recopié en toutes lettres : c'est la seule chose
    que la proposition ne dit pas déjà, et le fil doit porter ce qui part —
    sinon la relecture d'un run corrigé ne retrouverait nulle part ce qu'on a
    corrigé.

    Les **bornes** (#990) suivent exactement cette règle, et c'est pourquoi
    elles ne s'écrivent que lorsqu'il y en a : elles sont ce que le formulaire a
    ajouté, et que ni la proposition ni la réponse à venir ne portent.

    ⚠ Depuis #1222, **c'est la seule trace des bornes dans le fil** : la réponse
    qui ouvrait le run n'en récapitule plus le régime (`orchestration._ouvrir_un_run`
    dit pourquoi). Ce qui n'a pas été posé n'a donc rien à dire ici non plus — le
    régime par défaut s'annonce au moment de lancer, sur la carte de cadrage qui
    le porte dans les deux sens, et non après coup dans une phrase récitée.
    """
    if not approuve:
        return "Non, ne lance pas."
    amende = retenu != demande.proposition
    if not amende and bornes.aucune:
        return "Oui, lance."
    if amende and bornes.aucune:
        return f"Oui, lance — avec cet objectif : {retenu}"
    if not amende:
        return f"Oui, lance — bornes : {bornes.en_phrase()}"
    return f"Oui, lance — avec cet objectif : {retenu} — bornes : {bornes.en_phrase()}"


def _geste_de_recrutement(approuve: bool, roles: Sequence[RoleValide]) -> str:
    """Ce que le geste écrit dans le fil — le message que le clic vaut (#1146).

    Même règle que `_geste_de_cadrage` : le fil est la seule mémoire du canal,
    donc ce qui a été validé doit s'y lire comme une personne l'aurait écrit. Ce
    qui s'écrit est l'équipe **retenue**, instances ajustées comprises — c'est la
    seule chose que la proposition ne dit pas déjà, puisqu'on a pu y retirer un
    rôle ou y changer un nombre.
    """
    if not approuve:
        return "Pas d'équipe pour l'instant."
    composition = " · ".join(
        f"{role.role} ×{role.instances}" if role.instances > 1 else role.role
        for role in roles
    )
    return f"Je valide cette équipe : {composition}."


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


class CadrageIntrouvable(RuntimeError):
    """Ce fil n'a **aucune demande de cadrage en attente** à trancher (#943).

    Le pendant, pour le geste du fil, du `409` que les routes de brief rendent
    sur un run qui n'attend plus (§6.10) : un cadrage tranché deux fois, ou un
    geste arrivé après que la conversation a repris, ne doit pas ouvrir un run
    de plus. L'API la traduit en `409`.

    Elle couvre les trois façons de n'avoir rien à trancher, qui appellent la
    même conduite : aucune proposition n'a jamais été faite, la dernière est
    déjà tranchée (un message a suivi), ou le répondeur de ce fil n'en fait
    pas — un agent du catalogue ne propose pas de run.
    """


class QuestionIntrouvable(RuntimeError):
    """Ce fil n'a **aucune question d'outillage en attente** à répondre (#1031).

    Le pendant exact de `CadrageIntrouvable` sur l'autre demande, et il couvre les
    mêmes trois façons de n'avoir rien à répondre : aucune question n'a été posée,
    la dernière a déjà reçu sa réponse (un message a suivi), ou le répondeur de ce
    fil n'en pose pas. L'API la traduit en `409`.

    Elle est **distincte** de `CadrageIntrouvable` bien que leur traitement soit le
    même : les confondre ferait rendre « aucune proposition à trancher » à quelqu'un
    qui répondait à une question, sur un canal qui porte désormais les deux.
    """


class RecrutementIntrouvable(RuntimeError):
    """Ce fil n'a **aucune équipe proposée en attente** à valider (#1146).

    Le troisième pendant de `CadrageIntrouvable`, pour les mêmes trois façons de
    n'avoir rien à valider : aucune équipe n'a été proposée, la dernière a déjà
    reçu sa réponse (un message a suivi), ou le répondeur de ce fil n'en propose
    pas. L'API la traduit en `409` — c'est ce qui empêche un double clic de créer
    deux fois la même équipe.
    """


@dataclass(frozen=True)
class DemandeRecrutement:
    """Ce qu'une demande de recrutement porte : le travail qui attend, et où (#1146).

    `objectif` est la reformulation que l'orchestration aurait proposée au run —
    celle qu'elle **reprend** une fois l'équipe créée, pour que la demande
    d'origine ne soit pas à retaper. `projet_id` est le projet qui n'a personne :
    il est écrit **sur le message**, et non relu de la fenêtre au moment du geste,
    parce que c'est de lui que la phrase parle — une équipe validée depuis une
    fenêtre passée sur un autre projet naîtrait sinon dans ce dernier.

    ## Deux moments, une seule demande (#1227)

    La même forme sert deux situations, et les quatre derniers champs disent
    laquelle :

    - **avant le run** (#1146) — le projet n'a *aucun* agent, l'équipe entière est
      proposée, et `run_id` est vide. Le geste crée l'équipe puis le fil
      **repropose** le travail : c'est la demande de cadrage qui prend le relais ;
    - **pendant le run** (#1227) — le plan appelle un rôle que l'équipe n'a pas.
      `run_id` nomme le run qui attend, `role` le poste proposé, `gabarit` celui
      dont il sort, `raison` pourquoi (le plan, pas le projet) et `taches` ce qu'il
      prendrait. Le geste complète l'équipe et le run **reprend de lui-même** — il
      n'y a rien à reproposer.

    `gabarit` et `raison` sont ce que la carte **rapporte** à
    `POST …/equipe/proposition` pour obtenir ce rôle-là et pas l'équipe entière :
    un designer n'est justifié par aucun constat d'un projet en Python, donc une
    proposition ordinaire l'écarterait — c'est le plan qui le demande, et le plan
    n'est connu que d'ici. Même régime que `RoleEquipeRequete` (#1040) : ce qui
    repart est ce qui a été servi, et le serveur revalide le gabarit contre son
    catalogue.

    Une seule forme plutôt que deux, parce que le geste est le même et que la
    carte du fil est la même (`EquipeDansLeFil`, réutilisée et non doublée) : ce
    qui change est ce qu'elle a à montrer, et c'est exactement ce que ces champs
    portent. `run_id` est le témoin qui les sépare — *ce recrutement suspend-il un
    run ?* —, jamais une phrase reconnue dans le contenu.
    """

    objectif: str
    projet_id: str
    run_id: str = ""
    role: str = ""
    gabarit: str = ""
    raison: str = ""
    taches: tuple[str, ...] = ()

    @property
    def pendant_un_run(self) -> bool:
        """Ce recrutement suspend-il un run déjà ouvert ? (#1227)"""
        return bool(self.run_id)

    def to_dict(self) -> dict[str, Any]:
        """La demande en JSON — la forme du REST et du stockage."""
        return {
            "objectif": self.objectif,
            "projet_id": self.projet_id,
            "run_id": self.run_id,
            "role": self.role,
            "gabarit": self.gabarit,
            "raison": self.raison,
            "taches": list(self.taches),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DemandeRecrutement:
        """Relit une demande persistée, sans rien rejuger (même règle que `MessageChat`).

        Les quatre champs de #1227 sont **absents** des demandes écrites avant ce
        lot : elles se relisent telles quelles, et un fil ancien garde exactement
        la carte qu'il avait.
        """
        taches = data.get("taches")
        return cls(
            objectif=str(data.get("objectif") or ""),
            projet_id=str(data.get("projet_id") or ""),
            run_id=str(data.get("run_id") or ""),
            role=str(data.get("role") or ""),
            gabarit=str(data.get("gabarit") or ""),
            raison=str(data.get("raison") or ""),
            taches=tuple(str(t) for t in taches) if isinstance(taches, list) else (),
        )


@dataclass(frozen=True)
class EquipeRecrutee:
    """Ce qu'un recrutement a **créé** — le fait qu'une réponse porte sous sa bulle (#1262).

    Le pendant de `DemandeRecrutement` après le geste : la demande dit ce qu'on
    propose, celle-ci ce qui existe désormais dans le projet. Le fil l'écrivait
    en toutes lettres (« Équipe créée : Développeur ×2 · QA — 3 agents. »), dans
    une phrase du code accolée à la réponse ; c'est un **fait**, et il a
    retrouvé la place des faits — un champ du message, que l'écran rend sous la
    bulle comme il y rend le run ouvert (`run_id`, #268). Les mots, eux, sont
    ceux du modèle, qui reçoit la composition pour en parler s'il y a lieu.

    `roles` est lu dans le **rapport de création** (`EquipeCreee.to_dict`) et non
    dans ce qui a été demandé : on dit ce qui existe, comme l'étape d'équipe du
    parcours de création (« la liste, jamais un ok »). Chaque rôle y est une paire
    `(libellé, instances)` — tout ce que la bulle affiche, et rien de ce que la
    fiche d'un agent dit déjà mieux (skills, politique).
    """

    projet_id: str
    roles: tuple[tuple[str, int], ...] = ()

    @property
    def instances_total(self) -> int:
        """Combien d'agents sont nés — instances comprises."""
        return sum(instances for _, instances in self.roles)

    def composition(self) -> str:
        """L'équipe en une ligne — « Développeur ×2 · QA — 3 agents »."""
        roles = " · ".join(
            f"{role} ×{instances}" if instances > 1 else role
            for role, instances in self.roles
        )
        total = self.instances_total
        return f"{roles} — {total} {'agent' if total <= 1 else 'agents'}"

    @classmethod
    def du_rapport(cls, rapport: Mapping[str, Any], projet_id: str) -> EquipeRecrutee:
        """Le fait tiré du rapport de création — ce qui existe, pas ce qui a été demandé."""
        agents = [a for a in rapport.get("agents") or () if isinstance(a, Mapping)]
        return cls(
            projet_id=str(rapport.get("projet_id") or projet_id),
            roles=tuple(
                (str(a.get("role") or a.get("nom") or ""), int(a.get("instances") or 1))
                for a in agents
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        """Le fait en JSON — la forme du REST et du stockage."""
        return {
            "projet_id": self.projet_id,
            "roles": [{"role": role, "instances": n} for role, n in self.roles],
            "instances_total": self.instances_total,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EquipeRecrutee:
        """Relit un fait persisté, sans rien rejuger (même règle que `MessageChat`)."""
        roles = data.get("roles")
        return cls(
            projet_id=str(data.get("projet_id") or ""),
            roles=tuple(
                (str(r.get("role") or ""), int(r.get("instances") or 1))
                for r in (roles if isinstance(roles, list) else [])
                if isinstance(r, Mapping)
            ),
        )


@dataclass(frozen=True)
class EtapeFil:
    """Une chose que l'interlocuteur a **faite** en répondant — une lecture (#1223).

    `libelle` est la ligne qui s'affiche — « A lu « README.md » » —, écrite du
    point de vue de qui le regarde travailler ; `detail` est ce que cette lecture
    a rendu, déjà borné par celui qui l'a faite
    (`maestro.controltower.consultation.Lecture`), et qui ne se lit qu'au dépli.

    Elle voyage **deux fois** et c'est voulu : en direct sur le flux
    (`FRAGMENT_CHAT_ETAPE`), pour se voir pendant que la réponse s'écrit, puis
    sur le message persisté (`MessageChat.etapes`), pour être encore là au
    rechargement. Un seul des deux chemins laisserait, au choix, une trace qui
    disparaît d'elle-même ou une trace qui arrive après coup — or ce qui est
    demandé est *voir ce qu'il consulte pendant qu'il répond*, puis pouvoir y
    revenir.
    """

    libelle: str
    detail: str = ""

    def to_dict(self) -> dict[str, str]:
        """L'étape en JSON — la forme du REST, du flux et du stockage."""
        return {"libelle": self.libelle, "detail": self.detail}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EtapeFil:
        """Relit une étape persistée, sans rien rejuger (même règle que `MessageChat`)."""
        return cls(
            libelle=str(data.get("libelle") or ""), detail=str(data.get("detail") or "")
        )


def etapes_depuis(brut: Any) -> tuple[EtapeFil, ...]:
    """Les étapes d'une ligne relue — `()` sur un message écrit avant #1223.

    Une entrée qui n'est pas un objet, ou dont le libellé est vide, est **sautée** :
    une étape sans libellé n'aurait rien à afficher, et la rendre ferait une puce
    vide dans le repli.
    """
    if not isinstance(brut, Sequence) or isinstance(brut, str | bytes):
        return ()
    lues = [EtapeFil.from_dict(item) for item in brut if isinstance(item, Mapping)]
    return tuple(etape for etape in lues if etape.libelle)


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

    `conversation` (#694) est le fil d'appartenance **à l'intérieur** de l'agent,
    comme `agent` l'est à l'intérieur du dépôt — la même redondance avec le
    chemin, et pour la même raison : une ligne se lit sans savoir d'où elle
    vient, ce dont le passage en base (docs/03) aura besoin. À la relecture,
    c'est le **chemin qui fait foi** (`ChatStore.fil`) : de deux traces d'un même
    fait, une seule peut décider, sinon elles divergent.

    `proposition` (#943) est la troisième question que le même objet porte :
    après ce que le message **embarque** (`sources`) et ce qu'il **ouvre**
    (`run_id`), ce qu'il **demande**. C'est l'objectif qu'une proposition de
    l'orchestration soumet à l'accord de l'utilisateur — la reformulation
    qu'elle enverrait au run —, et c'est ce champ qui fait exister une *demande
    de cadrage* ailleurs que dans une phrase du fil.

    Il est vide partout ailleurs, et un fil écrit avant ce lot se relit à
    l'identique. **Rien n'en dérive l'attente** : savoir si la demande tient
    encore est une propriété de la *suite* des messages, pas de l'un d'eux, et
    elle s'énonce une fois (`proposition_en_attente`).

    `question` et `choix` (#1031) sont la **quatrième** question que le même objet
    porte, et elle est double parce qu'un questionnaire a deux moitiés : ce qu'un
    message **demande** (`question`, sur un message d'agent) et ce qu'un message
    **répond** (`choix`, sur un message d'utilisateur). Elles suivent exactement le
    patron de `proposition` — vides partout ailleurs, `None` sur une ligne écrite
    avant ce lot, et l'attente énoncée une seule fois (`question_en_attente`).

    Deux champs plutôt qu'un, et c'est ce qui fait tenir le reste : l'état du
    questionnaire n'est tenu **nulle part** ailleurs que dans la suite des messages
    (`choix_du_fil`), donc aucune session, aucun cache et aucune table ne peuvent se
    désaccorder du fil. Les fondre en un seul champ obligerait chaque lecteur à
    deviner, sur un même objet, s'il lit une demande ou une réponse.

    `recrutement` (#1146) est la troisième chose qu'un message d'agent peut
    demander : une **équipe**, pour un projet qui n'en a pas et dont on vient de
    demander un travail. Même patron encore — `None` partout ailleurs et sur une
    ligne écrite avant ce lot, l'attente énoncée une fois
    (`recrutement_en_attente`) — et jamais sur le même message qu'une proposition
    ou une question : on ne propose pas un run qu'on sait ne pas pouvoir aboutir.

    `etapes` (#1223) est la cinquième, et la seule qui ne **demande** rien : ce
    que l'interlocuteur a **fait** pour écrire ce message — les fichiers qu'il a
    lus, les recherches qu'il a passées. Vide partout ailleurs et sur une ligne
    écrite avant ce lot ; elle est persistée pour la raison qui fait persister
    `sources` — ce qui a nourri un message se relit avec lui, sans quoi le fil
    rouvert demain ne dirait plus sur quoi la réponse s'appuyait.

    `equipe` (#1262) est ce qu'un geste de recrutement a **créé** — le pendant de
    `run_id` pour une équipe : un fait que la bulle porte, et que le fil récitait
    auparavant dans une phrase du code. `None` partout ailleurs et sur une ligne
    écrite avant ce lot.

    `comprehension` (#1147) accompagne une question d'outillage ou la conclusion
    du questionnaire : **ce que Maestro a compris** du projet à ce tour, en constats
    (`Choix` déduits, et les réponses cliquées qui font foi). Persistée parce que
    c'est elle que la conclusion relit pour écrire l'outillage : rappeler le modèle
    à ce moment-là pourrait comprendre autre chose que ce que l'écran a montré.
    Vide partout ailleurs et sur une ligne écrite avant ce lot.
    """

    agent: str
    auteur: str
    contenu: str
    horodatage: str = field(default_factory=_horodatage)
    run_id: str = ""
    tache_id: str = ""
    proposition: str = ""
    question: QuestionOutillage | None = None
    recrutement: DemandeRecrutement | None = None
    equipe: EquipeRecrutee | None = None
    choix: Choix | None = None
    sources: tuple[Source, ...] = ()
    rapport: RapportLecture | None = None
    contexte: str = ""
    conversation: str = CONVERSATION_ORIGINE
    etapes: tuple[EtapeFil, ...] = ()
    comprehension: tuple[Choix, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Réémet le message en dict JSON-sérialisable (la forme du REST).

        Le `contexte` n'y est **pas** : il est fait pour un prompt, pas pour un
        écran, et le rapatrier au navigateur enverrait le contenu intégral des
        documents à chaque relecture du fil — ce que `Lecture.to_dict` refuse déjà
        de faire, pour la même raison. Le stockage, lui, le garde (`to_ligne`).
        """
        return {
            "agent": self.agent,
            "conversation": self.conversation,
            "auteur": self.auteur,
            "contenu": self.contenu,
            "horodatage": self.horodatage,
            "run_id": self.run_id,
            "tache_id": self.tache_id,
            "proposition": self.proposition,
            "question": self.question.to_dict() if self.question is not None else None,
            "recrutement": (
                self.recrutement.to_dict() if self.recrutement is not None else None
            ),
            "equipe": self.equipe.to_dict() if self.equipe is not None else None,
            "choix": self.choix.to_dict() if self.choix is not None else None,
            "sources": sources_en_liste(self.sources),
            "rapport": self.rapport.to_dict() if self.rapport is not None else None,
            "etapes": [etape.to_dict() for etape in self.etapes],
            "comprehension": [c.to_dict() for c in self.comprehension],
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
        question = data.get("question")
        recrutement = data.get("recrutement")
        equipe = data.get("equipe")
        choix = data.get("choix")
        return cls(
            agent=data["agent"],
            # Une ligne d'avant #694 n'en porte pas : elle vient forcément du
            # fichier historique, c'est-à-dire d'`origine`.
            conversation=str(data.get("conversation") or CONVERSATION_ORIGINE),
            auteur=data.get("auteur", UTILISATEUR),
            contenu=data.get("contenu", ""),
            horodatage=data.get("horodatage", ""),
            run_id=data.get("run_id", ""),
            tache_id=data.get("tache_id", ""),
            proposition=str(data.get("proposition") or ""),
            question=(
                QuestionOutillage.from_dict(question)
                if isinstance(question, Mapping)
                else None
            ),
            recrutement=(
                DemandeRecrutement.from_dict(recrutement)
                if isinstance(recrutement, Mapping)
                else None
            ),
            equipe=EquipeRecrutee.from_dict(equipe) if isinstance(equipe, Mapping) else None,
            choix=Choix.from_dict(choix) if isinstance(choix, Mapping) else None,
            sources=tuple(sources_depuis(data.get("sources"))),
            rapport=rapport_depuis(rapport) if isinstance(rapport, Mapping) else None,
            contexte=str(data.get("contexte") or ""),
            etapes=etapes_depuis(data.get("etapes")),
            comprehension=tuple(
                Choix.from_dict(c)
                for c in data.get("comprehension") or ()
                if isinstance(c, Mapping)
            ),
        )


@dataclass(frozen=True)
class Conversation:
    """Un fil parmi ceux d'un agent (#694) — sa carte, pas son contenu.

    C'est ce que rend `GET /api/chat/{agent}/conversations` : de quoi peupler une
    liste d'historique sans charger un seul message.

    - `id` — l'identifiant du fil, `origine` pour celui que l'agent a par défaut,
      sinon un slug portant son instant d'ouverture ;
    - `titre` — **dérivé** du premier message (`titre_conversation`), vide tant
      que rien n'a été dit ;
    - `debut` — l'ouverture : celle que porte l'identifiant, à défaut
      l'horodatage du premier message, à défaut rien ;
    - `derniere` — la dernière activité, c'est-à-dire le plus récent de
      l'ouverture et du dernier message. C'est **lui** qui ordonne, donc qui
      désigne « la plus récente » : ouvrir une conversation compte comme une
      activité (sans quoi une conversation neuve serait immédiatement moins
      récente que celle qu'on vient de quitter), et écrire dans une ancienne la
      ramène en tête (sans quoi « la conversation courante » serait figée à la
      dernière ouverte) ;
    - `messages` — combien de messages elle porte ; `0` dit « vierge », l'état
      qui rend `ChatStore.ouvrir` idempotent.
    """

    agent: str
    id: str
    titre: str = ""
    debut: str = ""
    derniere: str = ""
    messages: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Réémet la carte en dict JSON-sérialisable (la forme du REST)."""
        return {
            "agent": self.agent,
            "id": self.id,
            "titre": self.titre,
            "debut": self.debut,
            "derniere": self.derniere,
            "messages": self.messages,
        }


@dataclass(frozen=True)
class ReponseChat:
    """Ce qu'un répondeur rend : le texte, et ce qu'il a **ouvert** en le rendant.

    Le texte seul ne suffisait plus dès lors qu'un répondeur peut agir (#268) :
    l'orchestration qui lance un run doit pouvoir en nommer l'identifiant, sans
    quoi le fil dirait « c'est parti » sans dire vers quoi. `run_id`/`tache_id`
    sont vides pour tout répondeur qui se contente de parler — c'est-à-dire pour
    tous ceux d'avant ce lot, que l'implémentation par défaut de
    `RepondeurChat.produire` enveloppe sans qu'ils aient à la connaître.

    `proposition` (#943) est ce qu'il **demande** : l'objectif soumis à l'accord
    de l'utilisateur quand la réponse est une proposition de run. Il voyage
    jusqu'au `MessageChat` persisté et diffusé, exactement comme `run_id` — et
    c'est ce qui donne à la demande une existence ailleurs que dans la phrase
    qui la formule, donc un geste pour y répondre.

    `question` (#1031) est l'autre chose qu'il peut demander : la question
    d'outillage à laquelle un geste répond. Même patron, et pour la même raison —
    une question qui n'existerait que dans le texte d'une réponse ne pourrait pas
    porter de bouton. Les deux ne cohabitent jamais sur un même message : on
    demande un accord, ou on pose une question, jamais les deux à la fois.

    `recrutement` (#1146) est la troisième : l'équipe qu'un projet sans agent
    doit valider avant qu'un run puisse y aboutir. Elle ne cohabite avec aucune
    des deux autres.

    `etapes` (#1223) ne demande rien : ce sont les lectures que le répondeur a
    faites pour écrire cette réponse. Elles ont déjà été **diffusées** au fil de
    l'eau quand un `Etapeur` était branché ; les porter ici est ce qui les fait
    **persister** sur le message, et les deux chemins partent du même répondeur.

    `equipe` (#1262) ne demande rien non plus : c'est ce qu'un geste de
    recrutement a créé, porté comme `run_id` porte ce qu'un accord a ouvert.

    `comprehension` (#1147) ne demande rien non plus : ce que le questionnaire a
    compris du projet à ce tour, qui voyage jusqu'au message (`MessageChat`).
    """

    contenu: str
    run_id: str = ""
    tache_id: str = ""
    proposition: str = ""
    question: QuestionOutillage | None = None
    recrutement: DemandeRecrutement | None = None
    equipe: EquipeRecrutee | None = None
    etapes: tuple[EtapeFil, ...] = ()
    comprehension: tuple[Choix, ...] = ()


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

    `conversation` (#694) dit **où** la réponse s'écrit : un client qui affiche
    un fil sait ainsi si les incréments qui arrivent sont les siens, dès la trame
    `debut` et sans attendre le `MessageChat` de la trame `fin`.

    `etape` (#1223) porte ce que l'interlocuteur vient de **faire** — sur la seule
    trame `etape`, `None` partout ailleurs. Elle arrive **avant** les `fragment`
    de la réponse, parce que c'est l'ordre réel des choses : il lit, puis il
    rédige ; et elle ne touche pas au texte, que `delta` continue de porter seul.
    """

    type: str
    agent: str
    auteur: str = AUTEUR_AGENT
    delta: str = ""
    message: MessageChat | None = None
    echange: str = ""
    conversation: str = CONVERSATION_ORIGINE
    etape: EtapeFil | None = None

    def to_dict(self) -> dict[str, Any]:
        """Réémet la trame en dict JSON-sérialisable (le `data:` du SSE)."""
        return {
            "type": self.type,
            "agent": self.agent,
            "conversation": self.conversation,
            "auteur": self.auteur,
            "delta": self.delta,
            "message": self.message.to_dict() if self.message is not None else None,
            "echange": self.echange,
            "etape": self.etape.to_dict() if self.etape is not None else None,
        }


class ChatStore:
    """Persistance des fils, un fichier JSONL par **conversation** (#84, #694).

    Append-only : chaque message s'ajoute en fin de fichier — une ligne JSON par
    message, le fil se relit dans l'ordre d'écriture. Un seul écrivain à la fois
    au POC (l'API Control Tower) : le dépôt ne porte pas de verrou de
    concurrence.

    Deux emplacements, et c'est le cœur du lot : la conversation `origine` est
    le fichier historique `<racine>/<agent>.jsonl`, les suivantes vivent sous
    `<racine>/<agent>/<id>.jsonl`. Un fil écrit avant #694 est donc **déjà** une
    conversation — il n'est ni déplacé, ni réécrit, ni relu autrement —, et une
    installation qui n'ouvre jamais de seconde conversation écrit exactement les
    mêmes octets qu'avant ce lot.

    Les cartes rendues par `conversations` sont **dérivées** des messages
    (titre, horodatages) : rien n'est tenu à part, donc rien ne manque à un
    fichier antérieur.
    """

    def __init__(self, racine: Path) -> None:
        self._racine = racine

    @property
    def racine(self) -> Path:
        """La racine du dépôt (fichiers et dossiers de conversations)."""
        return self._racine

    @classmethod
    def default(cls, settings: Settings | None = None) -> ChatStore:
        """Le dépôt configuré : `MAESTRO_CHAT_DIR`, sinon `core/chat/` du dépôt."""
        settings = settings or load_settings()
        if settings.chat_dir:
            return cls(Path(settings.chat_dir))
        return cls(Path(__file__).resolve().parents[2] / "core" / "chat")

    def ajouter(self, message: MessageChat) -> MessageChat:
        """Ajoute `message` en fin de sa conversation et le renvoie tel quel."""
        chemin = self._chemin(message.agent, message.conversation)
        chemin.parent.mkdir(parents=True, exist_ok=True)
        with chemin.open("a", encoding="utf-8") as fichier:
            # `to_ligne` et non `to_dict` : le stockage garde en plus le contexte
            # extrait des sources (#482), que le REST n'emporte pas — sans lui, un
            # fil relu du disque perdrait le contenu des documents joints et
            # l'agent cesserait de les voir dès le tour suivant.
            fichier.write(json.dumps(message.to_ligne(), ensure_ascii=False) + "\n")
        return message

    def fil(self, agent: str, conversation: str | None = None) -> tuple[MessageChat, ...]:
        """Le fil de `conversation`, dans l'ordre d'écriture (vide si rien n'y est dit).

        Sans `conversation`, celui de la **plus récente** (`courante`) : c'est ce
        qui fait qu'un appelant d'avant #694 lit toujours le même fil, un agent
        n'en ayant alors qu'une.
        """
        identifiant = conversation or self.courante(agent)
        chemin = self._chemin(agent, identifiant)
        if not chemin.is_file():
            return ()
        return tuple(
            # Le **chemin fait foi** : la clé stockée rend la ligne
            # auto-descriptive (docs/03), elle ne décide pas où la ligne est.
            MessageChat.from_dict({**json.loads(ligne), "conversation": identifiant})
            for ligne in chemin.read_text(encoding="utf-8").splitlines()
            if ligne.strip()
        )

    def conversations(self, agent: str) -> tuple[Conversation, ...]:
        """Les conversations de `agent`, **la plus récente d'abord**.

        Jamais vide : `origine` en fait toujours partie, fût-elle vierge — un
        agent a toujours au moins la conversation qu'on lui ouvre en lui parlant.

        L'ordre est celui de la **dernière activité**, et il se départage à la
        milliseconde par la date du fichier. Ce second terme n'est pas un luxe :
        les horodatages sont à la **seconde** (celle du journal, #8), or les deux
        gestes que le lot doit distinguer se suivent en bien moins d'une seconde
        — on ouvre une conversation *puis* on y écrit, on quitte une conversation
        *puis* on retourne dans l'ancienne. Sans départage, l'ordre dépendrait
        alors de l'ordre alphabétique des identifiants, c'est-à-dire de rien. Le
        fait de domaine reste en tête de clé et décide seul dès qu'une seconde
        s'est écoulée ; la date du fichier ne tranche que ce qu'il ne peut pas
        voir.
        """
        cartes = [self._carte(agent, identifiant) for identifiant in self._identifiants(agent)]
        return tuple(
            sorted(
                cartes,
                key=lambda carte: (carte.derniere, self._touchee(agent, carte.id), carte.id),
                reverse=True,
            )
        )

    def conversation_du_run(self, agent: str, run_id: str) -> str | None:
        """La conversation de `agent` **où ce run a été demandé** — `None` si aucune (#1224).

        Le rattachement est celui de #268 : la réponse qui ouvre un run porte son
        `run_id`, persisté. Le chercher ici plutôt que de le faire porter au run
        évite un second registre à tenir d'accord avec le fil — le fil *est* la
        mémoire du canal, et c'est déjà lui que l'annonce de fin (#928) croise
        côté écran.

        Les conversations sont parcourues **de la plus récente à la plus
        ancienne** : un run vient d'être demandé, donc il est presque toujours
        dans la première. Un run lancé depuis l'écran des exécutions n'est dans
        aucune, et c'est un `None` — pas une anomalie.
        """
        if not run_id:
            return None
        for carte in self.conversations(agent):
            if any(message.run_id == run_id for message in self.fil(agent, carte.id)):
                return carte.id
        return None

    def _touchee(self, agent: str, conversation: str) -> float:
        """Quand le fichier d'une conversation a été écrit pour la dernière fois.

        `0.0` quand il n'existe pas — le cas d'une `origine` jamais servie, qui
        n'a de toute façon aucune activité à faire valoir.
        """
        chemin = self._chemin(agent, conversation)
        return chemin.stat().st_mtime if chemin.is_file() else 0.0

    def courante(self, agent: str) -> str:
        """L'identifiant de la conversation la plus récente de `agent`.

        « Sans précision, un envoi rejoint la plus récente » — c'est cette
        réponse-là, et elle existe toujours (`origine` au pire).
        """
        return self.conversations(agent)[0].id

    def existe(self, agent: str, conversation: str) -> bool:
        """`conversation` est-elle adressable chez `agent` ? (`origine` l'est toujours.)

        Lève `ValueError` sur un identifiant qui n'est pas un slug : la garde de
        traversée de chemin passe **avant** la question de l'existence, sans quoi
        un `../../etc` obtiendrait un « non » au lieu d'un refus.
        """
        chemin = self._chemin(agent, conversation)
        return conversation == CONVERSATION_ORIGINE or chemin.is_file()

    def ouvrir(self, agent: str) -> Conversation:
        """Ouvre une conversation neuve chez `agent` et rend sa carte.

        **Idempotent tant que rien n'a été dit** : si la plus récente est vierge,
        elle *est* la conversation neuve et c'est elle qui revient. Sans cette
        règle, deux clics sur « nouvelle conversation » laisseraient un
        historique de fils vides, et le premier clic sur un agent jamais contacté
        doublerait son `origine` avant qu'elle ait servi.
        """
        cartes = self.conversations(agent)
        if cartes[0].messages == 0:
            return cartes[0]
        identifiant = _nouvel_id_conversation()
        chemin = self._chemin(agent, identifiant)
        while chemin.exists():  # deux ouvertures dans la même seconde
            identifiant = _nouvel_id_conversation()
            chemin = self._chemin(agent, identifiant)
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.touch()
        return self._carte(agent, identifiant)

    def agents(self) -> tuple[str, ...]:
        """Les noms d'agents ayant au moins une conversation persistée, triés."""
        if not self._racine.is_dir():
            return ()
        noms = {
            chemin.stem
            for chemin in self._racine.glob("*.jsonl")
            if _NOM_AGENT.match(chemin.stem)
        }
        # Un agent dont on a ouvert une seconde conversation sans jamais écrire
        # dans la première n'a **que** son dossier : le taire le ferait
        # disparaître de l'inventaire au moment précis où il gagne un fil.
        noms |= {
            chemin.name
            for chemin in self._racine.iterdir()
            if chemin.is_dir() and _NOM_AGENT.match(chemin.name)
        }
        return tuple(sorted(noms))

    def _carte(self, agent: str, identifiant: str) -> Conversation:
        """La carte de `identifiant` — titre et horodatages **dérivés** de ses messages.

        L'ouverture vient de l'identifiant quand il la porte (une conversation
        vide en a donc une), sinon du premier message : c'est le cas d'`origine`,
        qui n'a jamais été « ouverte » puisqu'elle a toujours été là.
        """
        fil = self.fil(agent, identifiant)
        debut = _ouverture_de(identifiant) or (fil[0].horodatage if fil else "")
        return Conversation(
            agent=agent,
            id=identifiant,
            titre=titre_conversation(fil),
            debut=debut,
            derniere=max(debut, fil[-1].horodatage) if fil else debut,
            messages=len(fil),
        )

    def _identifiants(self, agent: str) -> tuple[str, ...]:
        """Les identifiants des conversations de `agent`, `origine` en tête."""
        self._chemin(agent)  # valide le nom d'agent avant d'en faire un dossier
        dossier = self._racine / agent
        if not dossier.is_dir():
            return (CONVERSATION_ORIGINE,)
        return (CONVERSATION_ORIGINE, *sorted(
            chemin.stem
            for chemin in dossier.glob("*.jsonl")
            # Un `origine.jsonl` égaré dans le dossier ne peut pas être une
            # seconde `origine` : celle-ci vit au chemin historique, et elle seule.
            if _ID_CONVERSATION.match(chemin.stem) and chemin.stem != CONVERSATION_ORIGINE
        ))

    def _chemin(self, agent: str, conversation: str = CONVERSATION_ORIGINE) -> Path:
        """Le fichier d'une conversation — noms validés, jamais un chemin arbitraire."""
        if not _NOM_AGENT.match(agent):
            raise ValueError(f"nom d'agent invalide : {agent!r} (slug [a-z0-9_-] attendu).")
        if not _ID_CONVERSATION.match(conversation):
            raise ValueError(
                f"identifiant de conversation invalide : {conversation!r} "
                "(slug [a-z0-9_-] attendu)."
            )
        if conversation == CONVERSATION_ORIGINE:
            return self._racine / f"{agent}.jsonl"
        return self._racine / agent / f"{conversation}.jsonl"


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
        etapeur: Etapeur | None = None,
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

        `etapeur` (#1223) est le second canal : ce que le répondeur **fait** avant
        de parler. Même régime que l'incrémenteur — `None` quand personne ne
        regarde, et l'implémentation par défaut ci-dessous ne le touche pas : un
        répondeur qui ne lit rien n'a aucune étape à publier, et son fil est
        exactement celui d'avant ce lot.
        """
        texte = await self.repondre(agent, fil)
        if incrementer is not None and texte:
            await incrementer(texte)
        return ReponseChat(contenu=texte)

    async def trancher_cadrage(
        self,
        agent: Agent,
        fil: Sequence[MessageChat],
        *,
        approuve: bool,
        objectif: str,
        projet_id: str | None = None,
        bornes: BornesRun = AUCUNE_BORNE,
    ) -> ReponseChat:
        """La réponse au **geste** qui tranche une demande de cadrage (#943).

        Le pendant de `produire` pour un acte plutôt qu'un message : l'accord
        n'a pas à repasser devant un juge, il *est* le verdict. C'est ce qui
        distingue ce chemin d'un « oui » retapé dans la zone de saisie, et les
        deux raisons sont mécaniques : un jugement de plus peut se tromper sur
        une décision déjà prise, et un objectif **amendé** ne survivrait pas au
        tour — le contrat du juge lui demande de recopier mot pour mot la
        proposition qu'il a faite, donc l'originale (`orchestration`).

        La propriété que #685 tient n'en bouge pas : *aucun run sans accord
        explicite*. Un bouton est l'accord le plus explicite qu'on puisse
        recevoir ; ce qui ouvre reste un acte de l'utilisateur, jamais un texte
        reconnu ni un silence.

        `bornes` (#990) est ce que l'écran a posé au moment de lancer, et il
        arrive par ce chemin pour la raison qui y fait passer l'objectif
        amendé : un tour de jugement ne le rendrait pas.

        Par défaut, un répondeur **ne propose rien**, donc n'a rien à trancher :
        il le dit plutôt que de le laisser deviner. Seul celui qui pose une
        `ReponseChat.proposition` a cette méthode à écrire.
        """
        raise CadrageIntrouvable(
            f"le fil {agent.nom} ne propose pas de cadrage : rien à trancher."
        )

    async def repondre_question(
        self,
        agent: Agent,
        fil: Sequence[MessageChat],
        *,
        question: QuestionOutillage,
        valeur: str,
    ) -> ReponseChat:
        """La réponse à une question d'outillage — un geste, ou une frappe (#1031, #1147).

        Le second point d'extension « acte » du canal, jumeau de
        `trancher_cadrage` : une réponse n'est pas une demande de travail à
        reconnaître, et le juge de l'orchestration n'est pas consulté. La suite
        est celle du **questionnaire** : depuis #1147 elle se comprend (le
        conducteur confie ce qui a été dit au modèle), elle ne se calcule plus
        sur un catalogue.

        `question` est celle que le fil portait, **relue du fil** : c'est celle
        qu'on a eue sous les yeux. `valeur` est la réponse — une option déjà
        vérifiée par l'appelant, ou la phrase de la personne.

        Par défaut, un répondeur **ne pose aucune question**, donc n'a rien à
        recevoir : il le dit plutôt que de le laisser deviner. Seul celui qui
        pose une `ReponseChat.question` a cette méthode à écrire.
        """
        raise QuestionIntrouvable(
            f"le fil {agent.nom} ne pose pas de question d'outillage : rien à répondre."
        )

    async def recruter(
        self,
        agent: Agent,
        fil: Sequence[MessageChat],
        *,
        demande: DemandeRecrutement,
        approuve: bool,
        roles: Sequence[RoleValide],
        proposition_id: str = "",
    ) -> ReponseChat:
        """La réponse au **geste** qui valide — ou décline — l'équipe proposée (#1146).

        Le troisième point d'extension « acte » du canal. Aucun appel modèle : la
        décision est un clic, et ce qu'il y a à faire se déduit — créer l'équipe
        retenue, puis reprendre la demande d'origine (`demande.objectif`).

        `demande` est celle que le fil portait, **relue du fil** ; `roles` est
        l'équipe telle que l'écran l'a montrée et ajustée, ignorée sur un refus.

        Par défaut, un répondeur **ne propose aucune équipe** : il le dit plutôt
        que de le laisser deviner. Seul celui qui pose un
        `ReponseChat.recrutement` a cette méthode à écrire.
        """
        raise RecrutementIntrouvable(
            f"le fil {agent.nom} ne propose pas d'équipe : rien à valider."
        )

    async def rediger(
        self, agent: Agent, fil: Sequence[MessageChat], *, faits: str
    ) -> str:
        """Le message que ce fil adresse à la personne **sur des faits** (#1262).

        Le quatrième point d'extension, et le seul qui ne réponde à rien de tapé :
        un geste vient d'avoir ses suites, ou le travail en cours fait prendre la
        parole au fil (le renfort d'un run, #1227). Ce qui s'est passé arrive en
        `faits` — du réel, déjà accompli —, et ce qui revient est **la parole** de
        l'interlocuteur : jamais une phrase que le code aurait composée à sa place,
        ce qui faisait lire deux voix dans une même conversation.

        `fil` est la conversation telle qu'elle est, geste compris ; vide quand
        personne n'a parlé (le renfort), où seuls les faits comptent.

        Par défaut, un répondeur **ne rédige rien sur des faits** : il le dit
        plutôt que de le laisser deviner. Seul celui qui écrit de lui-même dans
        son fil a cette méthode à écrire.
        """
        raise NotImplementedError(
            f"le fil {agent.nom} ne rédige pas de message sur des faits."
        )

    async def ouvrir_questionnaire(
        self, agent: Agent, fil: Sequence[MessageChat]
    ) -> ReponseChat:
        """Ouvre — ou reprend — le questionnaire d'outillage sur ce fil (#1031).

        L'entrée du dispositif, rendue comme n'importe quelle réponse d'agent : le
        canal ne gagne pas un second chemin d'écriture parce qu'il gagne une
        question. Elle reçoit le fil et en dérive où l'on en est, ce qui la rend
        idempotente sans qu'aucune garde n'ait à le tenir.

        Par défaut, un répondeur **ne pose aucune question** : il le dit.
        """
        raise QuestionIntrouvable(
            f"le fil {agent.nom} ne conduit pas de questionnaire d'outillage."
        )


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
        # `MAESTRO_MODEL` (#69), relu avec le fournisseur (#1173) : il prime sur le
        # modèle de l'agent, exactement comme dans un run (`catalogue_*`). Sans lui,
        # parler à un agent envoyait son modèle — `claude-sonnet-5` pour un
        # gabarit — à un fournisseur qui n'est peut-être pas Claude.
        self._modele_impose: str | None = None

    async def repondre(self, agent: Agent, fil: Sequence[MessageChat]) -> str:
        """La réponse en un aller — `produire` est la voie qui diffuse (#693)."""
        fournisseur = self._resolu()
        return await fournisseur.generate(
            transcription(fil),
            model=self._modele_impose or agent.modele,
            system_prompt=self._systeme(agent),
        )

    async def produire(
        self,
        agent: Agent,
        fil: Sequence[MessageChat],
        *,
        incrementer: Incrementeur | None = None,
        etapeur: Etapeur | None = None,
        projet_id: str | None = None,
    ) -> ReponseChat:
        """La réponse du modèle, publiée **au fil de son arrivée** (#693).

        `etapeur` (#1223) est accepté et **jamais utilisé** : ce répondeur parle
        à un agent du catalogue, il ne lit rien du projet. Le nommer ici plutôt
        que de l'avaler dans un `**kwargs` est ce qui fait que la signature
        **dit** ce que le canal offre, et qu'un répondeur qui y branchera des
        étapes un jour n'aura pas à découvrir le canal dans un dictionnaire.

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
            fournisseur = self._resolu()
            async for morceau in fournisseur.generate_stream(
                transcription(fil),
                model=self._modele_impose or agent.modele,
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
            self._modele_impose = getattr(self._provider, "modele_configure", None)
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

    Le levier des tests d'API (#83) — même contrat que le répondeur réel, zéro
    réseau. `conducteur` (#1147) est le questionnaire qu'il conduit : depuis que
    le questionnaire **comprend** par le modèle, un test qui le joue passe un
    conducteur sur un faux fournisseur ; sans lui, le conducteur par défaut
    résout le fournisseur du poste au premier usage (et la suite de tests le
    refuse, `FournisseurDuPosteRefuse`).
    """

    def __init__(self, conducteur: ConducteurOutillage | None = None) -> None:
        self._conducteur_injecte = conducteur

    async def repondre(self, agent: Agent, fil: Sequence[MessageChat]) -> str:
        dernier = fil[-1].contenu if fil else ""
        return (
            f"Bien reçu : « {dernier} ». Je suis l'agent {agent.role} "
            f"(compétences : {', '.join(sorted(agent.competences))}) — réponse "
            "scriptée, aucun modèle n'a été appelé."
        )

    async def rediger(
        self, agent: Agent, fil: Sequence[MessageChat], *, faits: str
    ) -> str:
        """Le même reflet que `repondre`, sur les faits reçus (#1262) — aucun modèle."""
        return f"Faits reçus : « {faits} » — message scripté, aucun modèle n'a été appelé."

    async def ouvrir_questionnaire(
        self, agent: Agent, fil: Sequence[MessageChat]
    ) -> ReponseChat:
        """Conduit le questionnaire d'outillage, **pour de vrai** (#1031, #1147).

        Le seul verbe de ce répondeur qui ne soit pas scripté, et c'est voulu : il
        joue le conducteur réel — question ouverte, compréhension, conclusion.
        Une version scriptée aurait éprouvé un comportement qui ressemble au produit
        sans être le sien, et c'est exactement ce que le dépôt refuse ailleurs
        (« deux vocabulaires pour le même contrat finissent par diverger de ce que
        l'API sert »). Le **modèle**, lui, est celui du conducteur injecté.
        """
        return await self._conducteur().ouvrir(fil)

    async def repondre_question(
        self,
        agent: Agent,
        fil: Sequence[MessageChat],
        *,
        question: QuestionOutillage,
        valeur: str,
    ) -> ReponseChat:
        """Enchaîne sur la réponse : la suite, comprise (#1031, #1147)."""
        return await self._conducteur().repondre(fil, question, valeur)

    def _conducteur(self) -> ConducteurOutillage:
        """Le conducteur du questionnaire — l'injecté, sinon un conducteur par défaut.

        Import différé, et c'est une nécessité de structure plutôt qu'une
        optimisation : `controltower.outillage` importe ce module-ci (il a besoin
        de `MessageChat` et de `choix_du_fil`), donc l'importer en tête créerait un
        cycle. Le conducteur ne garde aucun état du questionnaire, en construire un
        par appel ne coûte rien.
        """
        if self._conducteur_injecte is not None:
            return self._conducteur_injecte
        from maestro.controltower.outillage import ConducteurOutillage

        return ConducteurOutillage()


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
        # n'en ait besoin. Le défaut montre les images au modèle du poste
        # (#1163) — c'est par le fil qu'une maquette rejoint un brief.
        self._lecteur = lecteur_sources if lecteur_sources is not None else lecteur_par_defaut()
        # Les générations en vol, par identifiant d'échange (#695) : c'est le seul
        # état que le service garde entre deux requêtes, et il ne dure que le temps
        # d'un flux — `diffuser` l'inscrit à l'ouverture et le retire dans son
        # `finally`, quelle que soit la façon dont l'échange se termine.
        self._en_vol: dict[str, asyncio.Task[MessageChat]] = {}

    def fil(self, agent: str, conversation: str | None = None) -> tuple[MessageChat, ...]:
        """Le fil persisté d'une conversation de `agent`, dans l'ordre d'écriture.

        Sans `conversation`, celui de la plus récente (#694).
        """
        return self._store.fil(agent, conversation)

    def conversations(self, agent: str) -> tuple[Conversation, ...]:
        """Les conversations de `agent`, la plus récente d'abord (#694)."""
        return self._store.conversations(agent)

    def courante(self, agent: str) -> str:
        """L'identifiant de la conversation la plus récente de `agent` (#694)."""
        return self._store.courante(agent)

    def existe(self, agent: str, conversation: str) -> bool:
        """`conversation` est-elle adressable chez `agent` ? (`ValueError` si mal formée.)"""
        return self._store.existe(agent, conversation)

    def ouvrir_conversation(self, agent: str) -> Conversation:
        """Ouvre une conversation neuve chez `agent` — idempotente si la courante est vierge."""
        return self._store.ouvrir(agent)

    async def envoyer(
        self,
        agent: Agent,
        contenu: str,
        sources: Sequence[Mapping[str, Any] | Source] | None = None,
        *,
        projet_id: str | None = None,
        conversation: str | None = None,
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

        `conversation` (#694) est le fil où l'échange se range ; sans précision,
        **la plus récente**. Message et réponse y vont ensemble : ce sont les
        deux moitiés d'un même tour, les séparer n'aurait aucun sens.
        """
        fil = self._resoudre(agent, conversation)
        message = await self._deposer(agent, contenu, sources, conversation=fil)
        return message, await self._repondre(agent, conversation=fil, projet_id=projet_id)

    async def trancher_cadrage(
        self,
        agent: Agent,
        *,
        approuve: bool,
        objectif: str | None = None,
        projet_id: str | None = None,
        bornes: BornesRun = AUCUNE_BORNE,
        conversation: str | None = None,
    ) -> tuple[MessageChat, MessageChat]:
        """Tranche la demande de cadrage en attente ; rend la paire (geste, réponse).

        La **même forme** qu'`envoyer` — un message d'utilisateur, puis la
        réponse — parce que c'est la même chose : quelqu'un s'est adressé au
        fil. Ce qui change est que le contenu vient d'un **geste** et non d'une
        frappe, et que la réponse n'est pas jugée mais exécutée
        (`RepondeurChat.trancher_cadrage`).

        L'acte est écrit dans le fil, et ce n'est pas une politesse : le fil est
        la seule mémoire du canal (`orchestration`). Un accord donné au bouton
        sans trace laisserait le tour suivant devant une proposition sans
        réponse, que le juge reproposerait.

        `objectif` est la version **amendée**, `None` la proposition telle
        quelle — exactement le `brief: null` de `POST …/brief/decision` (§6.10),
        et pour la même raison : le corps ne recopie jamais ce qu'on n'a pas
        touché. Il est ignoré sur un refus, où il n'y a rien à lancer.

        `bornes` (#990) est ce jusqu'où le run pourra aller — coût, tokens,
        délai par tâche, parallélisme. Elles sont **écrites dans le fil** avec
        le geste quand il y en a, parce que le fil est la seule mémoire du canal
        et qu'un run borné dont la trace ne dirait pas à quoi il s'est arrêté
        serait un run qu'on ne peut plus relire. Ignorées sur un refus, comme
        l'objectif.

        `CadrageIntrouvable` quand rien n'attend — c'est le `409` de l'API, et
        il couvre le double geste comme le geste tardif.
        """
        fil = self._resoudre(agent, conversation)
        demande = proposition_en_attente(self._store.fil(agent.nom, fil))
        if demande is None:
            raise CadrageIntrouvable(
                f"aucune demande de cadrage en attente sur le fil {agent.nom}."
            )
        retenu = (objectif or "").strip() or demande.proposition
        geste = await self._deposer(
            agent,
            _geste_de_cadrage(approuve, retenu, demande, bornes),
            conversation=fil,
        )
        try:
            reponse = await self._repondeur.trancher_cadrage(
                agent,
                self._store.fil(agent.nom, fil),
                approuve=approuve,
                objectif=retenu,
                projet_id=projet_id,
                bornes=bornes,
            )
        except CadrageIntrouvable:
            # Le geste est déjà au fil : il a bien eu lieu, c'est la suite qui
            # manque. Remonter tel quel plutôt que d'envelopper — l'API en fait
            # un 409, pas un 502, et la conversation garde la trace du clic.
            raise
        except Exception as exc:
            raise ReponseIndisponible(
                f"l'agent {agent.nom} n'a pas pu trancher le cadrage : {exc}"
            ) from exc
        return geste, await self._persister_reponse(
            agent, conversation=fil, reponse=reponse
        )

    async def repondre_question(
        self,
        agent: Agent,
        *,
        valeur: str,
        libre: bool = False,
        conversation: str | None = None,
    ) -> tuple[MessageChat, MessageChat]:
        """Répond à la question d'outillage en attente ; rend la paire (geste, réponse).

        Jumeau de `trancher_cadrage` sur l'autre demande du canal, et **la même
        forme qu'`envoyer`** : un message d'utilisateur, puis la réponse. Ce qui
        change est que le contenu vient d'un geste, et que la suite se comprend au
        lieu de se juger.

        La question à laquelle on répond est **lue du fil**, jamais passée par
        l'appelant : c'est la seule façon qu'un geste tardif ou un double clic
        n'aille pas répondre à une question qui n'est plus posée. La `cle` n'est
        donc pas un paramètre — elle est celle de la question en attente, et un
        écran ne peut pas se tromper de question parce qu'il n'en désigne aucune.

        Deux sortes de réponses (#1147) :

        - **une option** (`libre=False`) : `valeur` est refusée (`ValueError`) si elle
          n'en est pas une de cette question-là — le fil est la seule mémoire du
          canal, une valeur fantaisiste y resterait comme un constat ;
        - **avec ses mots** (`libre=True`) : toute phrase non vide est acceptée,
          bornée (`REPONSE_LIBRE_MAX`), et enregistrée telle quelle. C'est le choix
          « Autre chose » de la carte (variante retenue de #1147, d'après le contrat
          `AskUserQuestion` : « use the user's custom text as the answer value »).
          Ce n'est pas un constat : le tour suivant la comprend.

        `QuestionIntrouvable` quand rien n'attend — c'est le `409` de l'API, et il
        couvre le double geste comme le geste tardif.
        """
        fil = self._resoudre(agent, conversation)
        demande = question_en_attente(self._store.fil(agent.nom, fil))
        if demande is None or demande.question is None:
            raise QuestionIntrouvable(
                f"aucune question d'outillage en attente sur le fil {agent.nom}."
            )
        question = demande.question
        if libre:
            valeur = " ".join(valeur.split())[:REPONSE_LIBRE_MAX]
            if not valeur:
                raise ValueError("réponse vide : écrivez ce que vous voulez répondre.")
        elif not question.options:
            raise ValueError(
                "cette question se répond avec vos mots : elle n'a pas d'options."
            )
        elif not any(o.valeur == valeur for o in question.options):
            offertes = ", ".join(o.valeur for o in question.options)
            raise ValueError(
                f"réponse hors des options posées : {valeur!r} "
                f"(attendu l'une de : {offertes}), ou répondez avec vos mots."
            )
        # Une réponse **avec ses mots** s'écrit telle quelle, comme une phrase tapée
        # dans la zone de saisie : ce sont les mots de la personne, et la relecture
        # de #1147 a montré ce que coûtait « Question → … » — la première réponse
        # donnant son titre au fil, celui-ci prenait la question de Maestro pour nom
        # et la description du projet disparaissait sous les points de suspension.
        # Un clic, lui, s'écrit toujours avec sa question (#1031).
        geste = await self._deposer(
            agent,
            valeur if libre else _geste_de_reponse(question, valeur),
            conversation=fil,
            choix=Choix(cle=question.cle, valeur=valeur, libre=libre),
        )
        try:
            reponse = await self._repondeur.repondre_question(
                agent,
                self._store.fil(agent.nom, fil),
                question=question,
                valeur=valeur,
            )
        except QuestionIntrouvable:
            # Le geste est déjà au fil : il a bien eu lieu, c'est la suite qui
            # manque. Remonter tel quel plutôt que d'envelopper — l'API en fait un
            # 409, pas un 502, comme pour le cadrage.
            raise
        except Exception as exc:
            raise ReponseIndisponible(
                f"l'agent {agent.nom} n'a pas pu poursuivre le questionnaire : {exc}"
            ) from exc
        return geste, await self._persister_reponse(
            agent, conversation=fil, reponse=reponse
        )

    async def recruter(
        self,
        agent: Agent,
        *,
        approuve: bool,
        roles: Sequence[RoleValide] = (),
        proposition_id: str = "",
        conversation: str | None = None,
    ) -> tuple[MessageChat, MessageChat]:
        """Valide — ou décline — l'équipe proposée ; rend la paire (geste, réponse) (#1146).

        Le troisième geste du canal, et **la même forme qu'`envoyer`** : un
        message d'utilisateur, puis la réponse. Ce qui change est que le contenu
        vient d'un clic, et que la suite s'exécute au lieu de se juger
        (`RepondeurChat.recruter`).

        La demande à laquelle on répond est **lue du fil**, jamais passée par
        l'appelant — objectif et projet compris. C'est ce qui fait qu'un geste
        tardif ou un double clic tombe sur `RecrutementIntrouvable` (le `409` de
        l'API) au lieu de créer l'équipe une seconde fois, et qu'une fenêtre
        passée sur un autre projet ne recrute pas dans celui-là.

        `roles` est l'équipe **retenue**, telle que l'écran l'a montrée : un rôle
        retiré n'y est pas, des instances ajustées y sont. Ignorée sur un refus.
        """
        fil = self._resoudre(agent, conversation)
        attente = recrutement_en_attente(self._store.fil(agent.nom, fil))
        if attente is None or attente.recrutement is None:
            raise RecrutementIntrouvable(
                f"aucune équipe proposée en attente sur le fil {agent.nom}."
            )
        geste = await self._deposer(
            agent, _geste_de_recrutement(approuve, roles), conversation=fil
        )
        try:
            reponse = await self._repondeur.recruter(
                agent,
                self._store.fil(agent.nom, fil),
                demande=attente.recrutement,
                approuve=approuve,
                roles=roles,
                proposition_id=proposition_id,
            )
        except RecrutementIntrouvable:
            # Le geste est déjà au fil : il a bien eu lieu, c'est la suite qui
            # manque — un 409, comme pour le cadrage, jamais un 502.
            raise
        except Exception as exc:
            raise ReponseIndisponible(
                f"l'agent {agent.nom} n'a pas pu donner suite à l'équipe : {exc}"
            ) from exc
        return geste, await self._persister_reponse(
            agent, conversation=fil, reponse=reponse
        )

    async def poser_question(
        self,
        agent: Agent,
        *,
        conversation: str | None = None,
    ) -> MessageChat:
        """Ouvre le questionnaire d'outillage sur ce fil ; rend le message posé.

        L'entrée du dispositif : c'est elle que le parcours de création d'un projet
        (#1034) appellera après le choix de la racine. Elle n'écrit **aucun message
        d'utilisateur** — personne n'a rien demandé —, seulement la question, par la
        même voie que n'importe quelle réponse d'agent.

        Idempotente vis-à-vis d'elle-même : le répondeur relit le fil, donc rouvrir
        un questionnaire déjà en cours repose la question **là où il en est**, et
        n'en recommence pas un second. C'est la propriété qui vient de ce que le fil
        est la seule mémoire ; aucune garde n'a à la tenir.

        `QuestionIntrouvable` sur un fil dont le répondeur n'en pose pas — `409`,
        comme partout ailleurs sur ce canal.
        """
        fil = self._resoudre(agent, conversation)
        try:
            reponse = await self._repondeur.ouvrir_questionnaire(
                agent, self._store.fil(agent.nom, fil)
            )
        except QuestionIntrouvable:
            raise
        except Exception as exc:
            raise ReponseIndisponible(
                f"l'agent {agent.nom} n'a pas pu ouvrir le questionnaire : {exc}"
            ) from exc
        return await self._persister_reponse(
            agent, conversation=fil, reponse=reponse
        )

    async def proposer_recrutement(
        self,
        agent: Agent,
        *,
        faits: str,
        demande: DemandeRecrutement | None = None,
        conversation: str | None = None,
    ) -> MessageChat:
        """Pose dans le fil la demande de renfort d'un run — ou son issue (#1227).

        Le pendant de `poser_question` sur l'autre demande qu'un fil peut recevoir
        **sans que personne n'ait parlé** : la décomposition vient de constater
        qu'un rôle manque au plan, et le run attend. Aucun message d'utilisateur
        n'est écrit — personne n'a rien demandé. `faits` et `demande` sont
        composés par l'appelant, qui est le seul à connaître le manque
        (`maestro.controltower.renfort`).

        **Les mots sont ceux du répondeur du fil** (#1262, `RepondeurChat.rediger`),
        et non une phrase de l'appelant : c'est la voix qui parle partout ailleurs
        dans cette conversation, et la demande de renfort était la dernière à y
        faire entendre celle du code. Ce que l'écran doit pouvoir lire sans phrase
        — le rôle, sa raison, les tâches — voyage sur la demande, donc sur la carte
        (`EquipeDansLeFil`). Les faits ne partent qu'au répondeur : le fil n'en
        garde que la parole, pas une seconde fois la même chose.

        `demande=None` dit l'issue sans offrir de geste, et c'est le second usage :
        à l'échéance, le run est reparti sans renfort et il faut le dire là où la
        demande avait été posée. Reposer la demande au lieu de la clore laisserait
        au pied du fil un bouton « Créer l'équipe » qui promettrait de faire
        reprendre un run déjà parti.

        Le message part par le chemin unique des réponses d'agent
        (`_persister_reponse`) : persisté, posté dans la messagerie, diffusé sur
        le bus. C'est ce qui fait que la carte du fil (`EquipeDansLeFil`) et le
        geste de validation (`recruter`) marchent sans une ligne de plus — la
        demande est au même endroit, sur le dernier message, qu'elle vienne du
        juge de l'orchestration ou d'un run.

        Le fil visé est celui de l'**orchestration** : c'est l'appelant qui le
        choisit en passant sa fiche, comme pour tout ce module.

        La conversation n'est **pas** passée au répondeur : personne n'y a parlé
        en dernier, et une transcription qui finit sur « réponds au dernier
        message » ferait répondre une seconde fois à l'accord qui a lancé le run.
        Les faits suffisent, comme pour le récit de fin (#1224).
        """
        fil = self._resoudre(agent, conversation)
        try:
            contenu = await self._repondeur.rediger(agent, (), faits=faits)
        except Exception as exc:
            raise ReponseIndisponible(
                f"l'agent {agent.nom} n'a pas pu rédiger la demande de renfort : {exc}"
            ) from exc
        return await self._persister_reponse(
            agent,
            conversation=fil,
            reponse=ReponseChat(contenu=contenu, recrutement=demande),
        )

    async def raconter_la_fin(
        self,
        agent: Agent,
        *,
        contenu: str,
        run_id: str,
        conversation: str | None = None,
    ) -> MessageChat:
        """Pose dans le fil le **récit de fin** d'un run (#1224).

        Le troisième verbe qui écrit au fil **sans que personne n'ait parlé** —
        après `poser_question` (#1023) et `proposer_recrutement` (#1227) —, et
        c'est le même patron : aucun message d'utilisateur, aucun appel modèle
        ici, le texte étant composé par l'appelant (`controltower.recit`), qui
        est le seul à avoir lu le livrable.

        Le message **porte le `run_id`** du run qu'il raconte, comme la réponse
        qui l'avait ouvert : c'est le rattachement de #268, donc ce qui fait que
        le récit se relit dans la bonne conversation et que la carte sous la
        bulle mène au run. Il n'en résulte aucun doublon d'annonce — `issuesDuFil`
        ne retient qu'une fin par run, jamais une par message.

        Le fil visé est celui de l'**orchestration** : comme partout ici, c'est
        l'appelant qui le choisit en passant sa fiche.
        """
        fil = self._resoudre(agent, conversation)
        return await self._persister_reponse(
            agent,
            conversation=fil,
            reponse=ReponseChat(contenu=contenu, run_id=run_id),
        )

    def conversation_du_run(self, agent: str, run_id: str) -> str | None:
        """La conversation de `agent` où `run_id` a été demandé — `None` si aucune (#1224)."""
        return self._store.conversation_du_run(agent, run_id)

    async def diffuser(
        self,
        agent: Agent,
        contenu: str,
        sources: Sequence[Mapping[str, Any] | Source] | None = None,
        *,
        projet_id: str | None = None,
        conversation: str | None = None,
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
        `_deposer`, donc à la même chaîne d'ingestion, aux mêmes identifiants et
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
        `conversation` (#694) de même : elle est résolue une fois, ici, et
        voyage sur **toutes** les trames, `debut` comprise.
        """
        fil = self._resoudre(agent, conversation)
        message = await self._deposer(agent, contenu, sources, conversation=fil)
        # L'échange se nomme **après** le dépôt : un message refusé — conversation
        # mal formée, message vide, source hors bornes — n'a jamais de flux, donc
        # jamais d'identifiant à arrêter.
        echange = uuid.uuid4().hex[:12]
        # La trame d'ouverture porte le message utilisateur **résolu** — ses
        # sources et leur rapport de lecture (#316) —, que seul `_deposer`
        # connaît et qu'aucune trame suivante ne redira. C'est le pendant, sur
        # cette voie, de la paire que `POST …/messages` rend d'un coup (#692).
        yield FragmentChat(
            type=FRAGMENT_CHAT_DEBUT,
            agent=agent.nom,
            conversation=fil,
            echange=echange,
            message=message,
        )

        # Une seule file pour les deux canaux (#1223) : un incrément est une
        # chaîne, une étape est une `EtapeFil`, et c'est **l'ordre de la file**
        # qui garantit qu'une étape arrive au client avant le texte qu'elle a
        # rendu possible. Deux files auraient rendu cet ordre indéterminé.
        file: asyncio.Queue[str | EtapeFil | None] = asyncio.Queue()

        async def incrementer(delta: str) -> None:
            await file.put(delta)

        async def etapeur(etape: EtapeFil) -> None:
            await file.put(etape)

        async def produire() -> MessageChat:
            try:
                return await self._repondre(
                    agent,
                    conversation=fil,
                    incrementer=incrementer,
                    etapeur=etapeur,
                    projet_id=projet_id,
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
                publie = await file.get()
                if publie is None:
                    break
                if isinstance(publie, EtapeFil):
                    # Une étape ne rejoint **pas** `recu` : ce qui y est accumulé
                    # est la réponse, et c'est elle que `_conclure_arret`
                    # persisterait si l'échange était coupé ici.
                    yield FragmentChat(
                        type=FRAGMENT_CHAT_ETAPE,
                        agent=agent.nom,
                        conversation=fil,
                        echange=echange,
                        etape=publie,
                    )
                    continue
                recu.append(publie)
                yield FragmentChat(
                    type=FRAGMENT_CHAT_DELTA,
                    agent=agent.nom,
                    conversation=fil,
                    echange=echange,
                    delta=publie,
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
                    conversation=fil,
                    echange=echange,
                    message=await self._conclure_arret(agent, "".join(recu), conversation=fil),
                )
                return
            except ReponseIndisponible as exc:
                yield FragmentChat(
                    type=FRAGMENT_CHAT_ERREUR,
                    agent=agent.nom,
                    conversation=fil,
                    echange=echange,
                    delta=str(exc),
                )
                return
            yield FragmentChat(
                type=FRAGMENT_CHAT_FIN,
                agent=agent.nom,
                conversation=fil,
                echange=echange,
                message=reponse,
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

    async def _conclure_arret(
        self, agent: Agent, recu: str, *, conversation: str
    ) -> MessageChat | None:
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

        `conversation` (#694) est celle de l'échange arrêté, résolue une fois par
        `diffuser` : la portion reçue se range là où la question a été posée, et
        nulle part ailleurs — la lire dans la conversation « courante » suffirait
        presque toujours et écrirait dans la mauvaise dès qu'une autre a été
        ouverte pendant la génération.
        """
        fil = self._store.fil(agent.nom, conversation)
        if fil and fil[-1].auteur != UTILISATEUR:
            return fil[-1]
        texte = recu.strip()
        if not texte:
            return None
        message = MessageChat(
            agent=agent.nom, conversation=conversation, auteur=agent.nom, contenu=texte
        )
        await self._acheminer(message, agent, type_message=MESSAGE_REPONSE)
        return message

    def _resoudre(self, agent: Agent, conversation: str | None) -> str:
        """La conversation d'un échange : celle demandée, sinon la plus récente (#694).

        Résolue **une fois par échange**, sur la voie commune, et jamais deux
        fois : un envoi qui tomberait dans une conversation et une réponse dans
        une autre couperait un tour en deux. Un identifiant mal formé lève
        `ValueError` — c'est la garde de traversée de chemin, et l'appelant la
        traduit en 422 comme un message vide.

        La **forme** est donc vérifiée ici, avant toute écriture et avant même
        la lecture des sources ; l'**existence** ne l'est pas : le dépôt est
        append-only, une conversation bien formée mais jamais écrite naît à son
        premier message. C'est l'API qui refuse d'en adresser une inconnue (404),
        parce que c'est elle qui a des identifiants venus du dehors.
        """
        if not conversation:
            return self._store.courante(agent.nom)
        self._store.existe(agent.nom, conversation)  # lève si l'identifiant n'est pas un slug
        return conversation

    async def _deposer(
        self,
        agent: Agent,
        contenu: str,
        sources: Sequence[Mapping[str, Any] | Source] | None = None,
        *,
        conversation: str,
        choix: Choix | None = None,
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

        `choix` (#1031) est la réponse d'outillage que ce message **porte**, quand
        il vient d'un geste sur une question. Il passe par ici et non par un
        second chemin d'écriture parce que c'est la règle du module : un message
        d'utilisateur se persiste, s'achemine et se diffuse d'une seule façon,
        qu'il vienne d'une frappe ou d'un clic.

        ⚠ **Une frappe répond aussi** (#1147). Un message tapé pendant qu'une
        question d'outillage attend **est** la réponse à cette question, avec les
        mots de la personne : il porte alors un `Choix` libre sur le sujet de la
        question, et `_repondre` le confie au questionnaire plutôt qu'au juge. Avant
        #1147, la même phrase faisait disparaître la carte — la question n'était
        plus le dernier message — et n'était enregistrée nulle part : corriger une
        réponse en l'écrivant la perdait. D'après *Intercom* (veille de #1147) : la
        zone de saisie reste une réponse valable quand aucune option ne convient.
        """
        contenu = contenu.strip()
        declarees = list(sources or ())
        if not contenu and not declarees:
            raise ValueError("message vide : rien à envoyer à l'agent.")
        if choix is None and contenu:
            attente = question_en_attente(self._store.fil(agent.nom, conversation))
            if attente is not None and attente.question is not None:
                choix = Choix(
                    cle=attente.question.cle,
                    valeur=" ".join(contenu.split())[:REPONSE_LIBRE_MAX],
                    libre=True,
                )

        matiere, rapport = await self._lire(declarees)
        message = MessageChat(
            agent=agent.nom,
            conversation=conversation,
            auteur=UTILISATEUR,
            contenu=contenu,
            choix=choix,
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
        conversation: str,
        incrementer: Incrementeur | None = None,
        etapeur: Etapeur | None = None,
        projet_id: str | None = None,
    ) -> MessageChat:
        """Produit la réponse, la persiste, l'achemine et la diffuse.

        Le seul endroit où le répondeur est appelé — `envoyer` et `diffuser` s'y
        rejoignent, à l'incrémenteur près, et depuis #683 au projet près. Toute
        erreur du répondeur, comme une réponse vide, devient
        `ReponseIndisponible` : le message utilisateur est déjà acquis, relancer
        ne perd pas le fil.

        Le fil confié au répondeur est celui de **la conversation en cours**
        (#694), et non tout ce que l'agent a jamais entendu : c'est ce qui donne
        son sens à « ouvrir une conversation neuve » — repartir de zéro avec le
        même agent, ce qu'un fil éternel rendait impossible.

        Un message qui **répond** à une question d'outillage (#1147, voir
        `_deposer`) n'est pas jugé : il est confié au questionnaire, comme un geste
        sur la carte. La réponse n'est alors pas diffusée par incréments — elle
        vient d'un tour de compréhension, pas d'une rédaction.
        """
        fil = self._store.fil(agent.nom, conversation)
        repondue = _question_repondue(fil)
        try:
            if repondue is not None:
                question, valeur = repondue
                reponse = await self._repondeur.repondre_question(
                    agent, fil, question=question, valeur=valeur
                )
            else:
                reponse = await self._repondeur.produire(
                    agent,
                    fil,
                    incrementer=incrementer,
                    etapeur=etapeur,
                    projet_id=projet_id,
                )
        except Exception as exc:
            raise ReponseIndisponible(
                f"l'agent {agent.nom} n'a pas pu répondre : {exc}"
            ) from exc
        return await self._persister_reponse(
            agent, conversation=conversation, reponse=reponse
        )

    async def _persister_reponse(
        self, agent: Agent, *, conversation: str, reponse: ReponseChat
    ) -> MessageChat:
        """Écrit une `ReponseChat` au fil — la moitié commune des deux voies.

        Partagée par `_repondre` (une réponse jugée), `trancher_cadrage` (une
        réponse exécutée, #943), `repondre_question` (#1031) et `recruter`
        (#1146) : ce qu'un répondeur rend se persiste, s'achemine et se diffuse
        toujours de la même façon, et c'est ici que les huit champs du contrat
        (`run_id`, `tache_id`, `proposition`, `question`, `recrutement`,
        `equipe`, `etapes`, `comprehension`) passent du répondeur au message.
        """
        texte = reponse.contenu.strip()
        if not texte:
            raise ReponseIndisponible(
                f"l'agent {agent.nom} a rendu une réponse vide."
            )

        message = MessageChat(
            agent=agent.nom,
            conversation=conversation,
            auteur=agent.nom,
            contenu=texte,
            run_id=reponse.run_id,
            tache_id=reponse.tache_id,
            proposition=reponse.proposition,
            question=reponse.question,
            recrutement=reponse.recrutement,
            equipe=reponse.equipe,
            etapes=reponse.etapes,
            comprehension=reponse.comprehension,
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

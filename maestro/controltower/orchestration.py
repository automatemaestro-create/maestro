"""Canal de chat avec l'**orchestration** — le fil global (ticket #268, lot 1 de #244).

Le chat de la Control Tower avait deux canaux, et il leur manquait le principal.
`maestro.controltower.chat` (#84) porte le dialogue avec un **agent exécutant** :
on s'adresse au Développeur, au QA, à propos du travail qu'ils font. `assistance`
(#123) porte les questions sur **l'outil** : où est un réglage, comment trancher
une validation. Aucun des deux ne permettait de dire « ajoute la pagination à la
liste des projets » — c'est-à-dire de s'adresser à **l'orchestration** plutôt qu'à
un exécutant, ce que la revue résume par « communiquer avec les agents sans passer
par les onglets chat de chacun ».

C'est ce canal : un fil `orchestrateur`, non lié à un agent du catalogue, qui
réutilise **toute** l'infrastructure du chat — `ChatStore` pour la persistance,
`ServiceChat` pour l'acheminement et la diffusion `chat.message` (#46), les mêmes
endpoints `/api/chat/{agent}` — avec deux pièces qui lui sont propres :

- `AGENT_ORCHESTRATION` : la fiche de l'orchestration. Comme l'assistant, ce
  n'est **pas** un agent du catalogue : elle n'exécute aucune tâche, n'apparaît
  ni au routage ni au Kanban, et n'a de l'`Agent` que ce dont le chat a besoin.
  Son nom était déjà **réservé** (`maestro.agents.store.NOMS_RESERVES`) avant ce
  lot, et c'est le même mot que l'acteur du cycle de vie d'un run
  (`events.ACTEUR_RUN`) : le fil et le journal parlent du même orchestrateur ;
- `RepondeurOrchestration` : la production de la réponse — et, ce qui le
  distingue de tous les répondeurs d'avant, la possibilité d'**agir**.

## Ouvrir des tâches, c'est ouvrir un run

La Control Tower n'a pas de `POST /api/taches`, et ce n'est pas un manque : une
tâche naît de la **décomposition** d'un objectif par l'orchestrateur, jamais
d'une écriture directe dans la projection (`maestro.controltower.state`, où seul
un événement `tache.statut` crée une carte). Une demande de travail formulée dans
le fil se traite donc en **lançant un run** — `ServiceExecutions.lancer` —, après
quoi les tâches apparaissent d'elles-mêmes au Kanban, avec leur graphe et leur
coût, exactement comme un run lancé depuis l'écran des exécutions.

D'où le `LanceurRun` injecté plutôt qu'un `ServiceExecutions` : le répondeur n'a
besoin que d'« ouvre un run sur cet objectif et dis-moi lequel », ce qui le rend
testable sans moteur et empêche ce module de tirer la couche d'exécution.
**Sans lanceur**, le canal reste conversationnel et le dit — il ne fait jamais
semblant d'avoir lancé quelque chose.

## Ce qui est ouvert est rattaché au fil

La réponse porte le `run_id` du run ouvert (`ReponseChat.run_id`), que le service
recopie sur le `MessageChat` persisté **et** sur l'événement `chat.message`
diffusé. Le fil garde donc le lien vers ce qu'il a déclenché, et un client temps
réel l'apprend sans relire quoi que ce soit.

## …et appartient au projet de la fenêtre (#683)

Le fil est **transverse** (#281) : il parle de l'outil, pas d'un projet, et ni le
message ni sa socket ne portent de périmètre. Mais ce qu'il **ouvre** en a un —
un run appartient à un projet (#222), et toutes les vues de travail sont cadrées
sur le projet actif (#277). Tant que le lanceur ne recevait pas de projet, un run
dicté au fil naissait orphelin : absent de la liste des runs de tout projet,
refusé par la vue de détail, invisible au Kanban et au journal. Le défaut était un
cas de bord tant que « Composer un objectif » existait ; depuis #666, où le chat
est **la seule porte d'entrée**, il valait pour **tous** les runs.

D'où le `projet_id` qui accompagne la demande : il vient de la fenêtre, il n'est
pas deviné, et il ne rend le fil ni cadré ni filtré — il ne touche ni le fil
persisté, ni l'événement diffusé, ni la socket. Deux usages seulement, dans le
répondeur : **rattacher** le run ouvert, et **cadrer** l'aperçu, pour que la
phrase « où en est-on ? » compte ce que l'écran d'à côté peut montrer.

## Le modèle juge, l'utilisateur tranche (#685)

Ce canal a reconnu les demandes de travail par un **lexique** jusqu'à #685 : une
demande commençait, politesses retirées, par un verbe d'une liste. La liste était
l'arbitraire même — « Crée-moi une app » lançait un run, « Génère-moi une app »
n'en lançait aucun —, et #682 a mesuré **quatre** causes de silence dans une seule
phrase réellement envoyée. Le lexique est parti en entier : ni juge, ni voie
rapide, ni repli.

### L'arbitrage de #268, et pourquoi il a été renversé

Il est écrit ici plutôt que laissé au ticket, parce qu'un arbitrage dont on ne
garde que la conclusion se refait dans six mois. #268 avait tranché pour le
lexique sur **quatre** appuis. Trois sont tombés, le quatrième a changé de sens :

- **coût et latence** — un appel modèle par message paraissait cher. Il ne l'est
  plus relativement à rien : les deux autres canaux du **même écran** le paient
  déjà, et celui d'un agent exécutant passait par `RepondeurModele` pendant que
  l'orchestrateur passait par une expression régulière ;
- **reproductibilité** — elle est acquise autrement, par le point d'injection
  `orchestration_repondeur` de `create_app`, sur lequel toute la suite du canal
  s'appuie sans réseau ni authentification. Et l'argument se retournait : un
  lexique n'est reproductible que dans un sens inutile — il se trompe **de façon
  reproductible** ;
- **« le vrai raisonnement a lieu dans le run »** — la décomposition est déjà un
  appel modèle *à l'intérieur* du run. Le lexique était donc exactement ce qui
  empêchait une demande légitime d'**atteindre** la partie intelligente ;
- **l'asymétrie des erreurs** — ne pas reconnaître coûte une reformulation,
  reconnaître à tort lance un run. Celui-là était juste, et c'est le seul. Mais
  le lexique n'achetait pas de la *prudence*, il achetait de l'*arbitraire* :
  même intention, verdict opposé selon le verbe employé.

Ce qui a réglé le quatrième n'est donc pas une meilleure liste, c'est la seconde
décision du 2026-08-28 — **tout run passe par un accord explicite**. La
validation systématique **dissout** l'asymétrie : un faux positif ne coûte plus
un run mais un « non ». C'est elle, et rien d'autre, qui autorise le juge à être
*large* là où le lexique devait être timide ; les deux moitiés du chantier ne
sont pas séparables, et un juge libéral sans la validation serait le pire des
trois régimes.

### Le précédent du moteur : le déclencheur passe du texte à l'acte

Le moteur a fait ce chemin le premier, et c'est la même leçon. `engine.guardrails`
classait une tâche « sensible » par **radicaux** (`deploi`, `supprim`,
`destructi`) trouvés dans son titre et sa description ; **#585** l'a désarmé
(`mots_sensibles` vide par défaut) sur un motif **mesuré** en **#568** — le mot
venait du *brief* et se propageait à toutes les descriptions issues de la
décomposition, si bien qu'un objectif demandant « une sous-commande **supprimer**
une note » rendait **3 tâches sur 3** sensibles, « Rédiger le README » comprise.
La docstring qui en est restée vaut ici mot pour mot :

> Développer une fonction de suppression n'est pas exécuter une suppression.

Ce qui l'a remplacé n'est pas une liste mieux tenue mais **deux canaux de
jugement** (`maestro.providers.arbitrage`, chantier **#573**) : l'agent lève la
main — l'outil MCP `demander_arbitrage(raison)`, **#582** — et l'acte est
suspendu au moment où il a lieu — hook `PreToolUse`, **#583**. Le déclencheur a
été déplacé **du texte vers l'acte**.

Le chat global en est la **seconde application**, et le parallèle est exact : ici
aussi le texte cesse d'être ce qui déclenche. Ce qui ouvre le run est l'**accord**
de l'utilisateur — un acte —, et le texte n'est plus qu'une entrée soumise au
jugement. La différence tient à qui arbitre : là-bas c'est l'humain qui suspend
l'acte d'un agent, ici c'est l'humain qui autorise celui du canal.

### Ce que le canal fait à la place

Ce qui remplace le lexique tient en **un appel modèle par message**, qui rend d'un
coup le texte de la réponse **et** le verdict (`_Verdict`) :

- **proposition** — le dernier message est une demande de travail. Le canal
  reformule l'objectif qu'il enverrait et demande l'accord. **Rien n'est ouvert à
  cet instant** ;
- **accord** — le dernier message approuve une proposition faite juste avant dans
  ce fil. Le run part alors, sur l'objectif **tel qu'il a été montré** ;
- **échange** — tout le reste : question, demande d'état, salutation, refus,
  message obscur. Rien ne s'ouvre.

Deux propriétés portent tout le reste :

**Aucun run sans accord explicite.** Un refus n'ouvre rien, et le **silence n'est
pas un accord** : une proposition sans réponse n'ouvre rien, parce que le run
n'est ouvert que sur le verdict `accord` d'un **message qui arrive**. La
propriété est structurelle et non gardée par un `if` — il n'existe qu'un chemin
vers le lanceur, et il part d'un verdict, qu'aucun silence ne produit. Rien
n'est mis « en attente » entre deux tours, ce qui ferait du message suivant, quel
qu'il soit, un accord par ricochet.

**Le fil est la seule mémoire.** Le répondeur reçoit le fil complet, donc sa
propre proposition et la réponse de l'utilisateur : rien à stocker à côté, aucun
état de session, aucun second lexique pour lire « oui » / « vas-y » / « plutôt
pas » — juger l'accord est du même ordre que juger la demande, et c'est le même
appel qui le fait.

Et l'objectif lancé est **celui que le modèle a recopié de sa proposition**, pas
le dernier message : `_ouvrir_un_run` ne connaît que `_Verdict.objectif`, si bien
qu'un « oui » ne peut structurellement pas partir comme objectif de run. On a
écarté de le **vérifier** contre le fil (chercher la reformulation dans les
messages précédents) : ce serait un second juge, en expression régulière, juste
après en avoir retiré un.

Un verdict **illisible vaut un échange** : le texte du modèle est rendu tel quel
et rien ne s'ouvre. Une réponse hors contrat coûte ainsi une reformulation, jamais
un run — et jamais non plus un 502 sur une conversation que le modèle a pourtant
tenue.

## Et quand le juge est injoignable, on le dit (#686)

Le lot précédent a fait du modèle le seul juge de ce canal ; il laissait ouverte
la question que cette décision pose : **que fait la porte d'entrée quand ce juge
ne répond pas ?** La réponse est celle de #268, étendue d'un cran — un empêchement
**ne lève pas, il se raconte dans le fil**. Une exception deviendrait ici une
`ReponseIndisponible`, donc un 502 sans trace, sur la seule porte d'entrée du
produit depuis #666 : l'auteur verrait sa demande partir et rien revenir.

Le canal annonce donc qu'il ne peut pas juger, **en n'ouvrant ni ne proposant
rien**. Trois choses tiennent ensemble.

**La cause est nommée, et sa famille avec elle.** Un fournisseur muet et un
fournisseur absent ne se réparent pas de la même façon, et l'utilisateur d'une
Control Tower locale est aussi celui qui répare : l'un se réessaie, l'autre se
configure, et les confondre fait renvoyer dix fois un message que rien n'attend.
La famille se lit à **l'endroit** de l'échec, jamais à son texte : ce qui casse en
*résolvant* le fournisseur (`provider_from_settings`) est un réglage — rien n'est
encore parti sur le réseau —, ce qui casse en *appelant* `generate` est une
indisponibilité. C'est la règle de `controltower.causes` (« la classification est
un `isinstance`, pas une lecture de texte ») tenue d'un cran plus haut : ici c'est
la **structure** qui classe, et aucune chaîne n'est examinée.

**Aucun lexique ne prend le relais.** Le lexique retiré au lot 1 ne revient pas
par la porte de service, et c'est pourquoi la phrase est **la même quel que soit
le dernier message** : reconnaître qu'un « oui » était un accord demanderait
précisément le juge qui manque. Un juge de secours moins bon que le titulaire,
activé quand personne ne regarde, est la pire des combinaisons — il proposerait
des runs sur les seules formulations qu'il sait reconnaître, et tairait les
autres.

**La demande, elle, est acquise.** `ServiceChat` persiste et diffuse le message
d'utilisateur **avant** d'appeler le répondeur : ce qui est indisponible est la
réponse, jamais la demande. Le fil garde donc le texte écrit *et* la phrase qui
dit pourquoi rien n'a suivi — y compris quand le fournisseur tombe *entre* la
proposition et l'accord, cas où le « oui » reste au fil sans rien ouvrir ni se
perdre en silence.

## Ce qui est gardé, et par quoi (#688)

`tests/test_chat_global.py` tient le tout, sans réseau, sans modèle et sans
moteur. Trois choses y méritent d'être connues avant d'y toucher :

- le **banc de #682 est joué cause par cause**, chaque formulation portant en
  identifiant de cas la raison exacte pour laquelle le lexique la faisait taire
  (`verbe-hors-liste`, `amorce-sans-s`, `subordonnee-que-tu`,
  `pronom-objet-intercale`, `subordonnee-et-conjugaison`). Les quatre dernières
  causes tenaient **ensemble** dans la phrase réellement envoyée : les séparer est
  ce qui empêche qu'un correctif n'en traite qu'une et que le banc passe quand
  même. Ses témoins négatifs (`comment ajouter une page ?`, `où en sont les
  runs ?`, `merci`) sont la moitié qui interdit de le rendre vert en proposant un
  run sur tout ;
- le **protocole d'accord est joué en deux tours** sur un seul répondeur, seule
  forme où la décision du 2026-08-28 est visible : proposition → rien, puis
  accord → run. Un test à verdict unique ne voit jamais l'intervalle entre les
  deux, qui est précisément l'endroit où une régression se logerait ;
- l'absence du lexique est gardée **structurellement**, sur l'arbre syntaxique et
  jamais par un `grep` — ce module *doit* citer `_AMORCES` et `_VERBES_TRAVAIL`
  pour raconter leur retrait, et une garde textuelle se déclencherait sur la
  docstring même qui les documente. Elle porte sur les identifiants **Python**,
  ce qui écarte du même geste les `AMORCES_ORCHESTRATION` de `apps/web`, qui sont
  les amorces de conversation d'un fil vide et n'ont jamais été ce lexique.

La moitié que ces tests **n'atteignent pas** est nommée plutôt que masquée : la
qualité du jugement. Le juge y est un double, donc « cette phrase est-elle une
demande de travail ? » n'y est pas posée — ce qui est tenu est qu'elle *atteint*
le juge, que le verdict décide seul, et qu'aucun run ne part sans accord. Le
reste relève du prompt, et se mesure en usage.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from maestro.agents.catalog import MODELE_EXECUTANT_DEFAUT, Agent
from maestro.controltower.causes import cause_lisible
from maestro.controltower.chat import (
    Incrementeur,
    MessageChat,
    Redaction,
    RepondeurChat,
    ReponseChat,
    transcription,
)
from maestro.controltower.events import ACTEUR_RUN, ROLE_RUN
from maestro.controltower.portee import PorteeProjet
from maestro.controltower.state import (
    EXECUTION_EN_ATTENTE_ARBITRAGE,
    EXECUTION_EN_ATTENTE_BRIEF,
    EXECUTION_EN_ATTENTE_REPONSES,
    EXECUTION_EN_COURS,
    ControlTowerState,
)
from maestro.providers.base import ModelProvider

#: Le nom du fil global — la clé de stockage (`core/chat/orchestrateur.jsonl`), le
#: segment d'URL des endpoints `/api/chat/{agent}` et le `agent` des événements
#: `chat.message` que l'UI filtre. Il vaut `events.ACTEUR_RUN` à dessein : c'est
#: déjà sous ce nom que le cycle de vie d'un run est consigné, et deux
#: orchestrateurs sur le même écran seraient un de trop.
NOM_ORCHESTRATION = ACTEUR_RUN

#: Son rôle affiché, le même que celui du journal (`events.ROLE_RUN`).
ROLE_ORCHESTRATION = ROLE_RUN

#: Les trois verdicts que le modèle rend **avec** sa réponse (voir le module).
#: `VERDICT_ECHANGE` garde le mot de l'`INTENTION_ECHANGE` d'avant #685 : c'est la
#: même situation — rien ne s'ouvre — et le vocabulaire du fil n'a pas de raison
#: de changer parce que le juge a changé.
VERDICT_PROPOSITION = "proposition"
VERDICT_ACCORD = "accord"
VERDICT_ECHANGE = "echange"

#: Les seuls verdicts admis. Tout autre mot — comme toute réponse hors contrat —
#: retombe sur `VERDICT_ECHANGE` : la liste est **blanche**, jamais noire, parce
#: qu'on ne maîtrise pas ce qu'un modèle peut écrire dans ce champ et qu'un mot
#: inattendu ne doit jamais pouvoir valoir un accord.
VERDICTS = frozenset({VERDICT_PROPOSITION, VERDICT_ACCORD, VERDICT_ECHANGE})

#: Le cadre de l'orchestration : ce qu'elle est, et le contrat de sa réponse.
#: Il existait depuis #268 « si un jour elle passe par un modèle » et n'avait
#: jamais été branché ; #685 le branche et lui ajoute le verdict, puisque c'est le
#: **même** appel qui rend la réponse et la décision.
_PROMPT_ORCHESTRATION = """\
Tu es l'orchestrateur de Maestro : tu reçois les demandes de l'utilisateur, tu les
cadres et tu les confies à l'équipe d'agents (Développeur, QA, DevOps, BDD,
Design). Tu n'exécutes pas le travail toi-même et tu ne parles pas à la place des
agents — tu ouvres le travail, tu en rends compte et tu dis où il en est.

Ouvrir un run coûte du quota et écrit dans le projet de l'utilisateur : tu n'en
ouvres JAMAIS un de ta propre initiative. Tu proposes, et c'est l'utilisateur qui
accepte.

Réponds toujours par un seul objet JSON, sans rien autour :

{"verdict": "proposition|accord|echange", "objectif": "...", "reponse": "..."}

Le verdict :
- "proposition" — le dernier message de l'utilisateur est une demande de travail,
  sous n'importe quelle forme : impératif, question, souhait, subordonnée
  ("génère-moi une application d'agenda", "j'aimerai que tu ajoutes la
  pagination", "il faudrait que tu corriges le tri", "peux-tu me créer une
  application"). Sois large : un run proposé de trop coûte un "non", une demande
  légitime non reconnue coûte à l'utilisateur de se reformuler sans savoir
  pourquoi.
- "accord" — le dernier message approuve une proposition que TU viens de faire
  dans ce fil ("oui", "vas-y", "ok lance"). Sans proposition juste avant, ce
  n'est jamais un accord.
- "echange" — tout le reste : question sur l'outil ou sur le travail, demande
  d'état, salutation, refus ("non", "plutôt pas"), message que tu ne comprends
  pas. Dans le doute, c'est "echange".

L'objectif :
- sur "proposition", l'objectif que tu enverrais au run — une phrase complète et
  autonome, qui reformule la demande sans rien inventer ;
- sur "accord", recopie MOT POUR MOT l'objectif de la proposition que
  l'utilisateur vient d'approuver ;
- vide sur "echange".

La réponse : le texte affiché à l'utilisateur, en français, bref. Sur
"proposition", il énonce l'objectif et demande explicitement l'accord. Sur
"accord", il confirme que le run part. Sur "echange", il répond — en s'appuyant
sur l'état de l'orchestration quand la question porte dessus."""

#: La fiche de l'orchestration, hors catalogue (voir le module) : le chat n'a
#: besoin que du nom, du rôle et du prompt système. Les compétences restent vides
#: — rien ne doit pouvoir lui router une tâche.
AGENT_ORCHESTRATION = Agent(
    nom=NOM_ORCHESTRATION,
    role=ROLE_ORCHESTRATION,
    competences=frozenset(),
    modele=MODELE_EXECUTANT_DEFAUT,
    prompt_systeme=_PROMPT_ORCHESTRATION,
)

#: Ouvrir un run sur un objectif, et rendre son résumé (dont `run_id`) — le seul
#: geste que le canal demande à la couche d'exécution. `ServiceExecutions.lancer`
#: le satisfait tel quel, une fois ses réglages liés par l'appelant. Le second
#: argument est le **projet de la fenêtre** d'où part la demande (#683), `None`
#: quand il n'y en a pas : le run part alors sans projet, comme avant ce lot.
LanceurRun = Callable[[str, str | None], Awaitable[Mapping[str, Any]]]

#: L'état de l'orchestration en une phrase, pour répondre « où en est-on ? » sans
#: donner à ce module la connaissance de la projection. Il prend le projet de la
#: fenêtre (#683) — un `str | None`, jamais un objet de portée : ce module ne
#: connaît ni la projection ni le contrat de lecture, il **transmet** ce que le
#: fil lui a donné, l'appelant en fait une portée.
ApercuOrchestration = Callable[[str | None], str]

#: Les statuts sous lesquels un run **n'est pas soldé** : il tourne, ou il attend
#: quelqu'un. Les quatre comptent pour « en cours » dans l'aperçu — de la place
#: où l'on pose la question, un run qui attend un arbitrage est un run en cours,
#: et l'écran des exécutions dira lequel attend quoi.
_STATUTS_ACTIFS = frozenset(
    {
        EXECUTION_EN_COURS,
        EXECUTION_EN_ATTENTE_BRIEF,
        EXECUTION_EN_ATTENTE_REPONSES,
        EXECUTION_EN_ATTENTE_ARBITRAGE,
    }
)

#: Un bloc de code Markdown, que les modèles posent volontiers autour d'un JSON
#: qu'on leur a demandé nu.
_FENCE = re.compile(r"```(?:json)?\s*(?P<corps>.+?)```", re.DOTALL)

#: Ce que le canal répond quand il n'a pas pu juger (#686). L'ordre des trois
#: morceaux **est** le contenu du message : la cause, ce qui n'a pas eu lieu, le
#: geste qui répare. Le deuxième porte le critère — dire « je ne peux pas » sans
#: dire « je n'ai rien ouvert » laisse chercher au tableau de bord un run qui
#: n'existe pas. Et il parle du **message**, jamais de « votre demande » : savoir
#: que c'en était une est précisément ce qui manque.
_PHRASE_INJOIGNABLE = (
    "Je ne peux pas juger votre message pour l'instant : {cause}. "
    "Aucun run n'a été ouvert, et je ne vous en ai proposé aucun. {reparation}"
)

#: Le fournisseur est configuré mais n'a rien rendu — panne passagère, on
#: réessaie. La phrase dit **où est la demande**, parce que c'est la question qui
#: vient juste après « ça n'a pas marché » : le message est déjà au fil, il n'y a
#: rien à retaper.
_REPARATION_PASSAGERE = (
    "Votre message reste dans ce fil : renvoyez-le tel quel quand le fournisseur "
    "répondra à nouveau, vous n'avez rien à retaper."
)

#: Rien ne répondra tant que le réglage n'aura pas été posé — le dire évite dix
#: renvois inutiles, et c'est toute la raison de séparer les deux familles. Les
#: réglages sont **nommés** parce qu'ici celui qui lit est celui qui répare : une
#: Control Tower locale n'a pas d'exploitant à qui transmettre.
_REPARATION_CONFIGURATION = (
    "Ce n'est pas une panne passagère mais un réglage absent : renseignez le "
    "fournisseur de modèle (MAESTRO_PROVIDER et ses identifiants) dans la "
    "configuration, puis renvoyez votre message — il reste dans ce fil."
)


class _JugeInjoignable(RuntimeError):
    """Le juge n'a rendu **aucun** verdict, et ce que le fil doit en dire (#686).

    Une exception plutôt qu'un quatrième `VERDICT_*` : les trois autres disent ce
    que le modèle a *jugé*, celle-ci dit qu'il n'a rien jugé du tout. Les ranger
    ensemble ferait passer une panne pour une décision, et il suffirait d'un `if`
    oubliant le quatrième cas pour qu'un run s'ouvre sans juge.

    Elle ne sort jamais du module : `produire` la rattrape et rend son texte au
    fil, là où la laisser remonter la ferait traduire en `ReponseIndisponible`,
    donc en 502 muet.
    """

    def __init__(self, cause: str, reparation: str) -> None:
        super().__init__(_PHRASE_INJOIGNABLE.format(cause=cause, reparation=reparation))


def _accord(nombre: int, singulier: str, pluriel: str) -> str:
    """« 1 run » / « 3 runs » — l'accord, écrit une fois."""
    return f"{nombre} {singulier if nombre <= 1 else pluriel}"


def apercu_de(state: ControlTowerState) -> ApercuOrchestration:
    """L'aperçu de l'orchestration, lu **à chaque question** dans `state`.

    Une fabrique et non une méthode du répondeur : celui-ci ne connaît qu'un
    `ApercuOrchestration` (« l'état, en une phrase »), ce qui le rend jouable
    sans projection, tandis que la formule vit ici, avec le canal qui la dit. La
    lecture est refaite à chaque appel — un aperçu figé à la construction de
    l'app annoncerait l'état d'hier.

    Elle est **cadrée sur le projet de la fenêtre** depuis #683, et c'est la
    seconde moitié du défaut que ce ticket corrige : la phrase comptait *tous*
    les runs du poste quand chaque écran ne montre que ceux du projet actif, si
    bien que le fil annonçait « 1 run en cours » à propos d'un run que la liste
    ne portait pas et que la vue de détail refusait d'ouvrir. Ce qu'elle compte
    est désormais ce que l'écran peut montrer.

    La portée est celle du contrat de lecture (#277) — `PorteeProjet.retient`,
    la règle écrite une fois — et non un filtre de plus : les trois compteurs de
    la phrase (runs actifs, tâches suivies, validations en attente) passent par
    la **même**, faute de quoi une seule phrase mélangerait deux périmètres.
    Sans projet — un client qui n'en envoie pas, un poste qui n'en a pas —, elle
    reste **transverse**, c'est-à-dire exactement la phrase d'avant ce lot.

    Depuis #685 elle n'est plus rendue telle quelle à l'utilisateur : elle entre
    dans le **prompt**, en tête du fil, et c'est le modèle qui la reprend quand la
    question porte dessus. Elle reste lue à chaque message, y compris sur une
    demande de travail — la construire coûte une lecture en mémoire, et savoir ce
    qui tourne déjà est ce qui permet de répondre « un run est déjà en cours
    là-dessus » plutôt que d'en proposer un second.
    """

    def apercu(projet_id: str | None = None) -> str:
        portee = PorteeProjet.projet(projet_id) if projet_id else PorteeProjet.tous()
        actifs = [run for run in state.executions(portee) if run.statut in _STATUTS_ACTIFS]
        attentes = sum(
            1 for validation in state.validations(portee) if validation.en_attente
        )
        if not actifs:
            phrase = "Aucun run en cours."
        else:
            phrase = (
                f"{_accord(len(actifs), 'run en cours', 'runs en cours')}, "
                f"{_accord(len(state.taches(portee)), 'tâche suivie', 'tâches suivies')}."
            )
        if attentes:
            phrase += f" {_accord(attentes, 'validation attend', 'validations attendent')} "
            phrase += "votre arbitrage."
        return phrase

    return apercu


@dataclass(frozen=True)
class _Verdict:
    """Ce qu'un appel modèle rend : ce que le canal dit, et ce qu'il en conclut.

    `nom` est l'un des `VERDICT_*`, `reponse` le texte affiché, `objectif` la
    reformulation — celle qui est **proposée**, puis celle qui est **lancée**
    quand l'utilisateur l'a approuvée. Les trois viennent du même appel : séparer
    « juger » de « répondre » en ferait deux, dont le second devrait redire au
    modèle ce que le premier vient de décider.
    """

    nom: str
    reponse: str
    objectif: str = ""


def _objet_json(texte: str) -> Any:
    """Le premier objet JSON de `texte` — nu, en bloc de code, ou noyé dans la prose.

    La borne de la sous-chaîne est la **dernière** accolade fermante et non un
    comptage de profondeur : les deux champs de texte du contrat (`reponse`,
    `objectif`) portent de la prose écrite d'après un message humain, donc des
    accolades sont possibles *dans les chaînes*, où un compteur les prendrait pour
    de la structure et couperait l'objet en plein milieu. Le décodage tranche
    ensuite — un candidat mal formé lève et le suivant est essayé.
    """
    candidats = [texte.strip()]
    fence = _FENCE.search(texte)
    if fence is not None:
        candidats.append(fence.group("corps").strip())
    debut, fin = texte.find("{"), texte.rfind("}")
    if debut != -1 and fin > debut:
        candidats.append(texte[debut : fin + 1])
    for candidat in candidats:
        try:
            return json.loads(candidat)
        except json.JSONDecodeError:
            continue
    return None


def _verdict_depuis(texte: str) -> _Verdict:
    """Le verdict lu dans la réponse du modèle — **un verdict illisible est un échange**.

    Rendre une erreur ferait d'une réponse hors contrat un 502, sur un canal qui
    est depuis #666 la seule porte d'entrée : le modèle a parlé, on affiche ce
    qu'il a dit, et on n'ouvre rien. C'est l'asymétrie du module appliquée à
    l'analyse plutôt qu'au jugement — ce qu'on ne comprend pas ne peut jamais
    valoir un accord.
    """
    charge = _objet_json(texte)
    if not isinstance(charge, Mapping):
        return _Verdict(nom=VERDICT_ECHANGE, reponse=texte.strip())
    nom = str(charge.get("verdict") or "").strip().lower()
    return _Verdict(
        nom=nom if nom in VERDICTS else VERDICT_ECHANGE,
        # Le texte brut en repli : un objet bien formé mais sans phrase à
        # afficher laisserait le fil muet, et `ServiceChat` refuse une réponse
        # vide (502). Mieux vaut montrer ce que le modèle a écrit.
        reponse=str(charge.get("reponse") or "").strip() or texte.strip(),
        objectif=str(charge.get("objectif") or "").strip(),
    )


def _prompt(fil: Sequence[MessageChat], etat: str) -> str:
    """Le fil rendu en prompt, précédé de l'état de l'orchestration quand on l'a.

    La conversation passe par `chat.transcription` — la **même** mise en forme
    que le chat d'un agent, sources comprises — et l'état vient en tête plutôt
    qu'en queue : la consigne de réponse ferme la transcription, et glisser un
    fait après elle le ferait lire comme une instruction de plus.
    """
    conversation = transcription(fil)
    if not etat:
        return conversation
    return f"État de l'orchestration : {etat}\n\n{conversation}"


class RepondeurOrchestration(RepondeurChat):
    """Le répondeur du fil global : il répond, et il peut ouvrir un run (#268, #685).

    `lanceur` ouvre le run approuvé (`LanceurRun`) ; sans lui, le canal reste
    conversationnel et le dit. `apercu` rend l'état de l'orchestration en une
    phrase, qui entre dans le prompt ; sans lui, le modèle juge sur le seul fil.
    `provider` est le fournisseur du jugement — résolu **paresseusement** comme
    dans `RepondeurModele` : construire le répondeur ne coûte rien et ne lève
    aucune erreur de configuration, ce dont dépend `create_app`.

    Un lancement qui échoue **ne lève pas** : il se raconte dans le fil. Une
    exception se traduirait en `ReponseIndisponible`, donc en 502 sans trace — or
    la demande, elle, est déjà persistée, et son auteur a besoin de lire pourquoi
    rien ne s'est ouvert pour pouvoir reformuler. **Un juge injoignable non plus**
    (#686) : c'est le même invariant un cran plus tôt — l'empêchement porte alors
    sur le verdict lui-même, et le canal dit qu'il ne peut pas juger au lieu de
    laisser passer un 502.
    """

    def __init__(
        self,
        *,
        lanceur: LanceurRun | None = None,
        apercu: ApercuOrchestration | None = None,
        provider: ModelProvider | None = None,
    ) -> None:
        self._lanceur = lanceur
        self._apercu = apercu
        self._provider = provider

    async def repondre(self, agent: Agent, fil: Sequence[MessageChat]) -> str:
        """La réponse seule — `produire` est la voie complète (rattachement compris)."""
        return (await self.produire(agent, fil)).contenu

    async def produire(
        self,
        agent: Agent,
        fil: Sequence[MessageChat],
        *,
        incrementer: Incrementeur | None = None,
        projet_id: str | None = None,
    ) -> ReponseChat:
        """Répond au dernier message, et ouvre le run que l'utilisateur vient d'approuver.

        Un seul appel modèle (`_juger`), dont le verdict décide de la suite :
        seul `VERDICT_ACCORD` ouvre quelque chose. Une **proposition** n'ouvre
        rien — c'est tout le sujet de #685 —, et un message qui ne vient pas
        n'ouvre rien non plus, faute de verdict à rendre : le silence n'est pas un
        accord parce qu'il n'est pas un message.

        `projet_id` est le **projet de la fenêtre** d'où part la demande (#683).
        Il sert deux fois, et les deux le doivent pour la même raison : il
        **cadre** l'aperçu que le modèle reçoit, et il **rattache** le run que
        l'accord ouvre. Les dissocier ferait juger sur l'état d'un périmètre et
        travailler dans un autre.

        Sans verdict du tout — juge injoignable (#686) —, le canal dit la cause
        et s'arrête là. Le `LanceurRun` n'est alors pas atteint, et pas par une
        garde qu'il faudrait tenir : il n'existe qu'**un** chemin vers lui, et il
        part d'un verdict qui n'a pas été rendu.
        """
        redaction = Redaction(incrementer)
        try:
            verdict = await self._juger(agent, fil, projet_id)
        except _JugeInjoignable as injoignable:
            await redaction.ecrire(str(injoignable))
            return ReponseChat(contenu=redaction.texte)
        await redaction.ecrire(verdict.reponse)
        if verdict.nom == VERDICT_ACCORD:
            return await self._ouvrir_un_run(redaction, verdict.objectif, projet_id)
        if verdict.nom == VERDICT_PROPOSITION and self._lanceur is None:
            # Prévenir **avant** le « oui » : proposer un run qu'on ne pourra pas
            # ouvrir ferait attendre l'utilisateur pour un refus au tour suivant.
            await redaction.ecrire(
                " ⚠ Aucune exécution n'est branchée sur ce fil pour l'instant : je "
                "peux en parler, pas encore l'ouvrir."
            )
        return ReponseChat(contenu=redaction.texte)

    async def _juger(
        self, agent: Agent, fil: Sequence[MessageChat], projet_id: str | None
    ) -> _Verdict:
        """L'appel modèle — fournisseur résolu au premier usage (import local, comme #84).

        Le prompt système est celui de la fiche (`_PROMPT_ORCHESTRATION`), qui
        porte le contrat de la réponse ; le prompt d'utilisateur est le fil, précédé
        de l'état. Aucun `PlaybookStore` ici, contrairement à `RepondeurModele` :
        l'orchestration n'est pas au catalogue, donc n'a pas de playbook éditable
        — et le contrat de sortie n'est pas un texte que l'UI doit pouvoir
        réécrire.

        Les trois façons de n'avoir **aucun** verdict lèvent `_JugeInjoignable`
        plutôt que de remonter (#686), et la **famille** de la cause se lit à
        l'endroit de l'échec : résoudre le fournisseur ne touche à aucun réseau,
        donc ce qui casse là est un réglage ; `generate`, lui, part dehors, donc
        ce qui casse là est une indisponibilité. Aucune chaîne n'est examinée pour
        trancher — c'est la règle de `controltower.causes`, tenue ici par la
        structure plutôt que par un `isinstance`.

        Une **réponse vide** est rangée avec les indisponibilités et non avec les
        verdicts illisibles, et la frontière est nette : un texte hors contrat est
        un modèle qui a *parlé* (on l'affiche, on n'ouvre rien — `_verdict_depuis`),
        un texte vide est un modèle qui n'a rien dit, donc rien à afficher, donc
        le 502 « réponse vide » de `ServiceChat` que ce lot supprime.

        Un échec de résolution **ne se mémorise pas** : `self._provider` reste
        `None`, si bien que le message suivant retente. C'est ce qui rend la
        phrase de réparation vraie — corriger la configuration suffit, sans
        redémarrer la Control Tower.
        """
        if self._provider is None:
            from maestro.providers.factory import provider_from_settings

            try:
                self._provider = provider_from_settings()
            except Exception as echec:  # noqa: BLE001 — la position classe, cf. docstring
                raise _JugeInjoignable(
                    f"aucun fournisseur de modèle n'est utilisable "
                    f"({cause_lisible(echec)})",
                    _REPARATION_CONFIGURATION,
                ) from echec
        etat = self._apercu(projet_id) if self._apercu is not None else ""
        try:
            texte = await self._provider.generate(
                _prompt(fil, etat),
                model=agent.modele,
                system_prompt=agent.prompt_systeme,
            )
        except Exception as echec:  # noqa: BLE001 — la position classe, cf. docstring
            raise _JugeInjoignable(
                f"le fournisseur de modèle n'a pas répondu ({cause_lisible(echec)})",
                _REPARATION_PASSAGERE,
            ) from echec
        if not (texte or "").strip():
            raise _JugeInjoignable(
                "le fournisseur de modèle a rendu une réponse vide",
                _REPARATION_PASSAGERE,
            )
        return _verdict_depuis(texte)

    async def _ouvrir_un_run(
        self, redaction: Redaction, objectif: str, projet_id: str | None
    ) -> ReponseChat:
        """Ouvre le run de `objectif`, dans son projet, et le rattache à la réponse.

        `objectif` est **la reformulation approuvée** et jamais le dernier message
        (#685) : la méthode ne reçoit pas le fil, donc un « oui » ne peut pas
        partir comme objectif de run même par accident. Un objectif vide est un
        verdict qui se contredit — accord sans rien à lancer : on le dit et on
        n'ouvre rien, plutôt que de retomber sur le message brut.

        Le run **hérite du projet de la fenêtre** (#683) : il apparaît donc dans
        la liste des runs de ce projet et s'ouvre en détail, là où un run sans
        projet n'entrait dans la vue d'aucun (`PorteeProjet.retient`) — c'est-à-dire
        nulle part, le chat étant depuis #666 la seule porte d'entrée. Rien n'est
        deviné : `projet_id` est ce que la fenêtre a envoyé, `None` quand elle n'a
        pas de projet, et le run part alors sans projet comme avant ce lot.
        """
        if not objectif:
            await redaction.ecrire(
                " Je n'ai pas retrouvé l'objectif que vous venez d'approuver : "
                "redites-moi le travail à faire et je vous le proposerai à nouveau."
            )
            return ReponseChat(contenu=redaction.texte)
        if self._lanceur is None:
            await redaction.ecrire(
                " Je ne peux pas ouvrir de run depuis ce fil : aucune exécution n'y "
                "est branchée. La demande est bien enregistrée ici."
            )
            return ReponseChat(contenu=redaction.texte)

        try:
            resume = await self._lanceur(objectif, projet_id)
        except Exception as echec:
            # Nommé dans le fil plutôt que levé : voir la classe. Un objectif
            # refusé (vide, plafond hors bornes) et un moteur qui ne démarre pas
            # se lisent tous deux ici, avec leur cause.
            await redaction.ecrire(f" Le lancement a échoué : {echec}")
            return ReponseChat(contenu=redaction.texte)

        run_id = str(resume.get("run_id", ""))
        statut = str(resume.get("statut", ""))
        await redaction.ecrire(f" Run {run_id} ouvert" if run_id else " Run ouvert")
        if statut:
            await redaction.ecrire(f", statut « {statut} »")
        await redaction.ecrire(
            ". Les tâches apparaîtront au tableau de bord à mesure que la "
            "décomposition les produit."
        )
        return ReponseChat(contenu=redaction.texte, run_id=run_id)

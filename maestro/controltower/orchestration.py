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

## Reconnaître une demande de travail — et le prix de se tromper

`intention` tranche sur une règle courte : **la demande commence, une fois les
formules de politesse retirées, par un verbe d'action**. « Ajoute la pagination »,
« peux-tu corriger le tri », « je voudrais migrer la base » ouvrent un run ;
« comment ajouter une page ? », « où en sont les runs ? », « merci » n'en ouvrent
pas.

Le choix vient de l'**asymétrie des deux erreurs**, pas d'une conviction sur le
langage : ne pas reconnaître une demande coûte une reformulation, la reconnaître à
tort lance un run — du quota, des écritures dans un projet, un arrêt à faire à la
main. En cas de doute on **répond** donc, en proposant d'ouvrir le run : c'est une
phrase de plus, jamais un run de trop. Un modèle jugerait mieux ; il jugerait aussi
plus lentement, plus cher, et sans reproductibilité — et le raisonnement qui compte
vraiment, celui qui découpe l'objectif en tâches, a déjà lieu **dans** le run.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from maestro.agents.catalog import MODELE_EXECUTANT_DEFAUT, Agent
from maestro.controltower.chat import (
    Incrementeur,
    MessageChat,
    RepondeurChat,
    ReponseChat,
    normaliser,
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

#: Le nom du fil global — la clé de stockage (`core/chat/orchestrateur.jsonl`), le
#: segment d'URL des endpoints `/api/chat/{agent}` et le `agent` des événements
#: `chat.message` que l'UI filtre. Il vaut `events.ACTEUR_RUN` à dessein : c'est
#: déjà sous ce nom que le cycle de vie d'un run est consigné, et deux
#: orchestrateurs sur le même écran seraient un de trop.
NOM_ORCHESTRATION = ACTEUR_RUN

#: Son rôle affiché, le même que celui du journal (`events.ROLE_RUN`).
ROLE_ORCHESTRATION = ROLE_RUN

#: Le cadre de l'orchestration si un jour elle passe par un modèle
#: (`RepondeurModele`) : elle coordonne, elle n'exécute pas.
_PROMPT_ORCHESTRATION = """\
Tu es l'orchestrateur de Maestro : tu reçois les demandes de l'utilisateur, tu les
cadres et tu les confies à l'équipe d'agents (Développeur, QA, DevOps, BDD,
Design). Tu n'exécutes pas le travail toi-même et tu ne parles pas à la place des
agents — tu ouvres le travail, tu en rends compte et tu dis où il en est.

Réponds en français, brièvement. Quand la demande est un travail à faire, dis ce
que tu ouvres ; quand c'est une question sur l'état, réponds avec ce que tu sais."""

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

#: Les deux intentions que le canal distingue (voir le module).
INTENTION_TRAVAIL = "travail"
INTENTION_ECHANGE = "echange"

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

#: Les amorces retirées avant de chercher le verbe : politesses et formulations
#: de volonté. Sans elles, « peux-tu corriger le tri » ne commencerait pas par un
#: verbe d'action et passerait pour une question — alors que c'en est une demande.
#: Elles sont **normalisées** (`chat.normaliser`), donc sans accents ni apostrophes.
_AMORCES: tuple[str, ...] = (
    "bonjour",
    "salut",
    "merci de",
    "merci",
    "s il te plait",
    "stp",
    "peux tu",
    "pourrais tu",
    "est ce que tu peux",
    "est ce que tu pourrais",
    "tu peux",
    "tu pourrais",
    "j aimerais",
    "je voudrais",
    "je veux",
    "il faut",
    "il faudrait",
    "on doit",
    "on devrait",
    "on aimerait",
    "j ai besoin de",
    "besoin de",
    "pour moi",
    "maintenant",
    "aussi",
    "ensuite",
    "puis",
    "et",
)

#: Les verbes qui ouvrent une demande de travail, à l'impératif comme à
#: l'infinitif — les deux façons dont on dicte une tâche. Normalisés, donc sans
#: accents ; les formes en « -e » couvrent l'impératif (« ajoute ») et les formes
#: en « -er » l'infinitif (« ajouter »), qui suit les amorces de volonté.
_VERBES_TRAVAIL: tuple[str, ...] = (
    "ajoute",
    "ajouter",
    "cree",
    "creer",
    "construis",
    "construire",
    "corrige",
    "corriger",
    "deploie",
    "deployer",
    "developpe",
    "developper",
    "documente",
    "documenter",
    "ecris",
    "ecrire",
    "enleve",
    "enlever",
    "fais",
    "faire",
    "implemente",
    "implementer",
    "integre",
    "integrer",
    "lance",
    "lancer",
    "migre",
    "migrer",
    "mets",
    "mettre",
    "optimise",
    "optimiser",
    "prepare",
    "preparer",
    "refactorise",
    "refactoriser",
    "regle",
    "regler",
    "remplace",
    "remplacer",
    "renomme",
    "renommer",
    "repare",
    "reparer",
    "redige",
    "rediger",
    "supprime",
    "supprimer",
    "teste",
    "tester",
    "traduis",
    "traduire",
)


def _sans_amorce(demande: str) -> str:
    """La demande normalisée, débarrassée de ses amorces de politesse en tête.

    Répété tant qu'une amorce tombe : « bonjour, peux-tu ajouter… » en porte
    deux. Le nombre de tours est borné par la longueur du texte, chaque passe en
    retirant au moins un mot.
    """
    texte = demande
    encore = True
    while encore and texte:
        encore = False
        for amorce in _AMORCES:
            if texte == amorce:
                return ""
            if texte.startswith(f"{amorce} "):
                texte = texte[len(amorce) + 1 :]
                encore = True
                break
    return texte


def intention(demande: str) -> str:
    """`INTENTION_TRAVAIL` si `demande` est un travail à ouvrir, sinon `INTENTION_ECHANGE`.

    La règle et sa justification sont dans le module : commence-t-elle, une fois
    les politesses retirées, par un verbe d'action ? Tout le reste — questions,
    salutations, demandes d'état — est une conversation, et **le doute compte
    comme une conversation**.
    """
    texte = _sans_amorce(normaliser(demande))
    if not texte:
        return INTENTION_ECHANGE
    premier = texte.split(" ", 1)[0]
    return INTENTION_TRAVAIL if premier in _VERBES_TRAVAIL else INTENTION_ECHANGE


class _Redaction:
    """Une réponse qui s'écrit par morceaux, et se diffuse au fur et à mesure.

    Chaque morceau part vers le flux dès qu'il est écrit (quand un incrémenteur
    est là) **et** s'accumule : le texte final est exactement la concaténation
    des incréments, ce dont le contrat SSE dépend — la trame `fin` porte le
    message complet, et un client doit pouvoir le reconstituer des `delta` seuls.
    """

    def __init__(self, incrementer: Incrementeur | None) -> None:
        self._incrementer = incrementer
        self._morceaux: list[str] = []

    async def ecrire(self, morceau: str) -> None:
        """Ajoute `morceau` à la réponse et le publie."""
        self._morceaux.append(morceau)
        if self._incrementer is not None:
            await self._incrementer(morceau)

    @property
    def texte(self) -> str:
        """La réponse écrite jusqu'ici — sans espace aux extrémités (cf. `ServiceChat`)."""
        return "".join(self._morceaux).strip()


class RepondeurOrchestration(RepondeurChat):
    """Le répondeur du fil global : il répond, et il peut ouvrir un run (#268).

    `lanceur` ouvre le run d'une demande de travail (`LanceurRun`) ; sans lui, le
    canal reste conversationnel et le dit. `apercu` rend l'état de l'orchestration
    en une phrase pour les questions du type « où en est-on ? » ; sans lui, la
    réponse se borne à orienter.

    Un lancement qui échoue **ne lève pas** : il se raconte dans le fil. Une
    exception se traduirait en `ReponseIndisponible`, donc en 502 sans trace — or
    la demande, elle, est déjà persistée, et son auteur a besoin de lire pourquoi
    rien ne s'est ouvert pour pouvoir reformuler.
    """

    def __init__(
        self,
        *,
        lanceur: LanceurRun | None = None,
        apercu: ApercuOrchestration | None = None,
    ) -> None:
        self._lanceur = lanceur
        self._apercu = apercu

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
        """Répond au dernier message, en ouvrant un run si c'en est une demande.

        `projet_id` est le **projet de la fenêtre** d'où part la demande (#683).
        Il traverse les deux voies, parce que les deux le doivent pour la même
        raison : la voie active y **rattache** le run qu'elle ouvre, la voie
        conversationnelle y **cadre** l'aperçu qu'elle rend. Les dissocier ferait
        annoncer l'état d'un périmètre et travailler dans un autre.
        """
        demande = fil[-1].contenu if fil else ""
        redaction = _Redaction(incrementer)
        if intention(demande) != INTENTION_TRAVAIL:
            await self._echanger(redaction, demande, projet_id)
            return ReponseChat(contenu=redaction.texte)
        return await self._ouvrir_un_run(redaction, demande, projet_id)

    async def _echanger(
        self, redaction: _Redaction, demande: str, projet_id: str | None = None
    ) -> None:
        """La voie conversationnelle : ce qu'on sait de l'état, puis ce qu'on sait faire."""
        if self._apercu is not None:
            await redaction.ecrire(self._apercu(projet_id))
            await redaction.ecrire(" ")
        await redaction.ecrire(
            "Dites-moi le travail à faire — « ajoute la pagination à la liste des "
            "projets », « corrige le tri des tâches » — et j'ouvre un run : je "
            "découpe l'objectif en tâches et je les confie aux agents compétents. "
        )
        if self._lanceur is None:
            await redaction.ecrire(
                "⚠ Aucune exécution n'est branchée sur ce fil pour l'instant : je "
                "peux en parler, pas encore l'ouvrir."
            )
        else:
            await redaction.ecrire("Le suivi reste dans ce fil.")

    async def _ouvrir_un_run(
        self, redaction: _Redaction, demande: str, projet_id: str | None = None
    ) -> ReponseChat:
        """La voie active : ouvrir le run de `demande`, dans son projet, et le rattacher.

        Le run **hérite du projet de la fenêtre** (#683) : il apparaît donc dans
        la liste des runs de ce projet et s'ouvre en détail, là où un run sans
        projet n'entrait dans la vue d'aucun (`PorteeProjet.retient`) — c'est-à-dire
        nulle part, le chat étant depuis #666 la seule porte d'entrée. Rien n'est
        deviné : `projet_id` est ce que la fenêtre a envoyé, `None` quand elle n'a
        pas de projet, et le run part alors sans projet comme avant ce lot.
        """
        if self._lanceur is None:
            await redaction.ecrire(
                "Je ne peux pas ouvrir de run depuis ce fil : aucune exécution n'y "
                "est branchée. La demande est bien enregistrée ici."
            )
            return ReponseChat(contenu=redaction.texte)

        await redaction.ecrire("J'ouvre un run sur cette demande.")
        try:
            resume = await self._lanceur(demande, projet_id)
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

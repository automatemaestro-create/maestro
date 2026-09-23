"""Le récit de fin d'un run — ce qui a été produit, et comment l'essayer (#1224).

## Le constat

La fin d'un run n'écrivait **rien** dans le fil. Le dernier message de
l'orchestration restait celui du lancement, et tout ce qu'on apprenait d'une fin
venait de l'annonce dérivée côté front (`AnnonceIssueRun`, #928) : le verdict, le
coût, la racine du projet, « Ouvrir le dossier », « Copier le chemin ». Elle dit
**où** est le livrable ; elle ne dit ni **ce qu'il contient**, ni **comment
l'essayer**. Le 2026-09-22, la personne avait bien le lien du dossier et écrivait
pourtant : *« on ne me dit pas comment tester, pourtant on a généré une
documentation »*.

Ce module écrit ce message-là : à la fin d'un run, dans **le fil qui l'a
demandé**, l'orchestration raconte ce que le run a produit, comment l'essayer
(avec la commande **lue dans le livrable**, jamais devinée), ce qui reste
éventuellement à faire, et nomme les fichiers qui comptent en **liens qui
s'ouvrent d'un geste** (`apps/web/lib/markdown.ts`).

## Trois décisions portent ce module

**1. Le récit s'ajoute, il ne remplace rien.** L'annonce de #928 ne bouge pas —
ni dans le fil, ni dans la cloche, ni ses deux gestes. Elle est **dérivée du
persisté** et ne manque donc jamais ; le récit, lui, est un message, donc il est
écrit une fois et au moment où la fin passe. Les deux ne se recouvrent pas : la
première est la ligne d'événement qui dit *c'est fini, voilà où*, le second est
la bulle qui dit *voilà ce que c'est, et voilà comment l'essayer*.

**2. Le livrable est LU, pas deviné.** La commande à taper ne s'invente pas et ne
se déduit pas d'un catalogue de gabarits de projet (#1169) : elle se lit dans ce
que le run a écrit. La lecture passe par la **chaîne d'ingestion existante**
(`maestro.sources.extraction`, #316) avec une source `dossier` sur la racine du
projet — donc par les mêmes plafonds, le même parcours qui ne suit aucun lien
symbolique, le même respect du **périmètre** du projet (les gisements de secrets
de docs/24 §2.5 en sont exclus d'office) et le même encadrement de données
(`contexte_markdown`, ENF-13). Aucune seconde chaîne de lecture n'est ouverte
ici, et aucun nom de fichier n'est privilégié : c'est le modèle qui reconnaît un
mode d'emploi dans ce qu'il lit.

**3. Rien n'est fabriqué.** Un modèle injoignable n'écrit pas une phrase gabarit
à sa place : le récit n'est simplement pas écrit, et l'annonce de #928 reste (elle
porte le verdict et le chemin). C'est l'asymétrie de tout le dépôt — ce qu'on ne
sait pas dire ne se dit pas —, et c'est aussi ce qui empêche le fil de se remplir
de « je n'ai pas pu vous dire ce que le run a fait ».

## Ce que ce module ne fait pas

Il n'écoute pas le bus (c'est la pompe de `app.py` qui lui passe la main, une
fois la projection à jour), ne rend aucun écran, et n'ouvre aucun run. Il répond à
une seule question : *ce run vient de finir — qu'est-ce qu'on en raconte, et dans
quel fil ?*

## La fenêtre où le récit s'écrit, et pourquoi elle est bornée

Le récit est écrit quand la **fin passe** sur le bus. Un run soldé pendant que
l'API était arrêtée revient par le **rejeu du journal durable** (#97), qui ne
passe pas par la pompe : ce run-là n'aura pas de récit, et c'est assumé — le
rattrapage de ce cas est l'annonce de #928, dérivée du persisté, qui existe
précisément pour ça. Réécrire un récit au rejeu demanderait un appel modèle par
run à chaque démarrage de l'API, pour des runs dont la conversation est close.

L'idempotence, elle, est tenue **sur le fil** (`deja_raconte`) et non en mémoire :
un même `execution.statut` terminal reçu deux fois ne fait pas deux récits, et le
marqueur est le fil lui-même — le seul endroit qui survit à un redémarrage.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from maestro.agents.catalog import Agent
from maestro.controltower.chat import UTILISATEUR, MessageChat, ServiceChat
from maestro.controltower.state import (
    STATUTS_EXECUTION_TERMINAUX,
    ControlTowerState,
    EtatExecution,
)
from maestro.projets.modele import Perimetre, Projet
from maestro.providers.base import ModelProvider
from maestro.sources.extraction import (
    GardeFousExtraction,
    RapportLecture,
    contexte_markdown,
    extraire_sources,
)
from maestro.sources.modele import Source

_LOGGER = logging.getLogger(__name__)

#: Combien de fichiers du livrable on **offre en lien**. Une douzaine : le récit
#: nomme ce qui compte, il ne rend pas l'arborescence — un fil n'est pas un
#: explorateur, et l'explorateur, lui, s'ouvre d'un geste (#928).
FICHIERS_OFFERTS_MAX = 12

#: Ce qu'on accepte de lire du livrable pour en tirer le mode d'emploi. Plus
#: serré que le défaut d'ingestion (#316) parce que ce n'est pas la même
#: question : un objectif embarque des documents à analyser, ici on cherche
#: comment lancer ce qui vient d'être écrit. Trente fichiers et ~12 000 tokens
#: couvrent largement un livrable de run ; au-delà, le rapport le **dit**
#: (`limite`), et le modèle le lit.
GARDE_FOUS_LIVRABLE = GardeFousExtraction(
    tokens_max_source=12_000,
    tokens_max_total=12_000,
    nb_max_fichiers_dossier=30,
)

#: Le titre du bloc de données du livrable, dans le prompt. Il dit **d'où**
#: viennent ces octets : c'est ce que le run a écrit, pas ce que quelqu'un a
#: demandé.
TITRE_LIVRABLE = "Le livrable du run, tel qu'il est sur le disque"

#: La consigne de rédaction. Elle dit **quoi dire** et **comment écrire les
#: liens** — c'est tout ce que le canal impose, le reste étant le jugement du
#: modèle (#1169). La forme `[libellé](<chemin>)` est celle de CommonMark pour
#: une destination qui peut contenir des espaces, et `apps/web/lib/markdown.ts`
#: la reconnaît : un chemin de Windows (`C:\\Mes projets\\app.py`) ne se met pas
#: entre parenthèses nues sans se couper au premier blanc.
SYSTEME = (
    "Tu es l'orchestrateur de Maestro. Un run que cette personne t'a demandé "
    "vient de se terminer, et tu lui écris — dans le fil où elle l'a demandé — "
    "ce qu'elle a maintenant entre les mains.\n"
    "\n"
    "Écris un message court, en français, à la deuxième personne. Quatre choses, "
    "dans cet ordre, et tu sautes celles qui n'ont rien à dire :\n"
    "1. **ce que le run a produit** — une ou deux phrases, en nommant la chose "
    "par ce qu'elle est pour la personne, pas par le nombre de tâches ;\n"
    "2. **comment l'essayer tout de suite** — la commande exacte, dans un bloc "
    "de code, telle que tu l'as LUE dans le livrable (un README, un "
    "`package.json`, un point d'entrée). N'invente jamais une commande : si le "
    "livrable n'en donne aucune, dis-le en une ligne et propose ce que tu vois "
    "de plus proche ;\n"
    "3. **ce qui reste à faire**, s'il reste quelque chose — une tâche en échec, "
    "un fichier annoncé et absent, une dépendance à installer ;\n"
    "4. **les fichiers qui comptent**, en liens. Un lien s'écrit "
    "`[nom lisible](<chemin absolu>)`, avec les chevrons, et le chemin doit être "
    "l'un de ceux de la liste « Fichiers du livrable » ci-dessous, recopié "
    "exactement. Ne fabrique aucun chemin.\n"
    "\n"
    "Pas de salutation, pas de formule de politesse finale, pas de titre de "
    "niveau 1. Ne récite ni le statut, ni le coût, ni l'identifiant du run : "
    "ils sont déjà affichés juste à côté de ton message."
)


@dataclass(frozen=True)
class Livrable:
    """Ce que le run a laissé sur le disque, lu et prêt à entrer dans un prompt.

    `racine` est le chemin du projet du run — celui-là même que l'annonce de
    #928 affiche. `chemins` sont les fichiers qu'on **offre en lien**, en chemins
    absolus tels que le poste les écrit : c'est ce que la coque ouvrira, et c'est
    donc la seule forme qui ait une chance de marcher d'un geste.

    `contexte` est le contenu lu, **déjà encadré comme donnée** (ENF-13) : il ne
    se recompose pas ailleurs, sans quoi il y aurait deux endroits où
    l'encadrement pourrait être oublié.
    """

    racine: str
    chemins: tuple[str, ...]
    contexte: str
    rapport: RapportLecture

    @property
    def vide(self) -> bool:
        """Rien n'a pu être lu — ni contenu, ni fichier à nommer."""
        return not self.chemins and self.rapport.vide


def lire_le_livrable(
    racine: Path,
    perimetre: Perimetre | None = None,
    *,
    garde_fous: GardeFousExtraction | None = None,
    fichiers_max: int = FICHIERS_OFFERTS_MAX,
) -> Livrable:
    """Lit la racine d'un projet comme une source `dossier` — jamais autrement.

    Passer par `extraire_sources` plutôt que par un parcours à nous est la
    décision 2 du module : les plafonds, le refus des liens symboliques, le
    périmètre du projet et l'encadrement du contenu sont écrits **une fois**, là
    où tout le produit les applique déjà. Ce qui reste propre à cet appel tient
    en deux réglages : des plafonds plus serrés (`GARDE_FOUS_LIVRABLE`) et le
    nombre de fichiers qu'on offrira en lien.

    Ne lève pas : une racine disparue ou illisible rend un `Livrable` vide, et
    l'appelant dit alors ce qu'il peut dire — un run qui n'a rien laissé est une
    information, pas une panne.
    """
    source = Source(type="dossier", nom=racine.name or str(racine), chemin=str(racine))
    try:
        rapport = extraire_sources(
            [source],
            garde_fous=garde_fous or GARDE_FOUS_LIVRABLE,
            perimetre=perimetre or Perimetre(),
        )
    except Exception:  # noqa: BLE001 — une racine illisible n'est pas une panne
        _LOGGER.exception("Livrable illisible : %s", racine)
        return Livrable(racine=str(racine), chemins=(), contexte="", rapport=RapportLecture())
    # Les entrées d'une lecture `dossier` portent le chemin **relatif** de chaque
    # fichier parcouru ; le lien, lui, a besoin de l'absolu. On le recompose ici
    # et nulle part ailleurs : la racine et les relatifs sont les deux moitiés du
    # même fait, et les séparer laisserait le front deviner.
    chemins: list[str] = []
    for lecture in rapport.lectures:
        for entree in lecture.entrees:
            if not entree.nom:
                continue
            chemins.append(str(racine / entree.nom))
            if len(chemins) >= fichiers_max:
                break
        if len(chemins) >= fichiers_max:
            break
    return Livrable(
        racine=str(racine),
        chemins=tuple(chemins),
        contexte=contexte_markdown(rapport, titre=TITRE_LIVRABLE),
        rapport=rapport,
    )


def deja_raconte(fil: Sequence[MessageChat], run_id: str) -> bool:
    """Ce run a-t-il **déjà** son récit dans ce fil ?

    Le marqueur est le fil lui-même, et il tient en une observation : un run
    ouvert depuis le fil y laisse **un** message d'agent portant son `run_id` —
    la réponse qui l'a lancé (#268). Le récit en est un second. Au-delà d'un, il
    y a donc déjà eu récit.

    Pas de registre en mémoire : il ne survivrait pas au redémarrage, et c'est
    précisément après un redémarrage qu'un doublon se verrait. Pas de champ neuf
    sur le message non plus — une ligne de JSONL écrite avant ce lot doit se
    relire sans rien apprendre de nouveau.
    """
    if not run_id:
        return False
    porteurs = sum(
        1
        for message in fil
        if message.run_id == run_id and message.auteur != UTILISATEUR
    )
    return porteurs > 1


class RedacteurRecit(Protocol):
    """Ce que le conteur demande à un rédacteur — et rien de plus."""

    async def rediger(self, *, agent: Agent, contexte: str) -> str: ...


class RedacteurModele:
    """Le rédacteur réel : un appel au fournisseur configuré, un message en retour.

    Fournisseur résolu **paresseusement**, comme `RepondeurOrchestration` et
    `JugeModele` : construire le conteur ne coûte rien et ne lève aucune erreur
    de configuration, ce dont dépend `create_app`. Un échec de résolution ne se
    mémorise pas — la fin de run suivante retentera, et corriger la
    configuration suffit.

    **Pas de flux** ici, à la différence du fil qui répond (#1222) : personne
    n'attend devant l'écran qu'un récit s'écrive, et un message publié par
    morceaux depuis la pompe demanderait un échange ouvert qu'aucune requête n'a
    ouvert.
    """

    def __init__(
        self,
        provider: ModelProvider | None = None,
        *,
        modele: str | None = None,
    ) -> None:
        self._provider = provider
        self._modele = modele

    async def rediger(self, *, agent: Agent, contexte: str) -> str:
        """Le texte du récit — lève si le fournisseur n'a rien rendu."""
        provider, modele = self._resolu(agent)
        texte = await provider.generate(contexte, model=modele, system_prompt=SYSTEME)
        return (texte or "").strip()

    def _resolu(self, agent: Agent) -> tuple[ModelProvider, str]:
        """Le fournisseur et le modèle — ceux du poste, le modèle suivant le fournisseur."""
        from maestro.providers.factory import modele_du_canal, provider_from_settings

        if self._provider is None:
            fournisseur = provider_from_settings()
            self._modele = modele_du_canal(agent.modele, fournisseur)
            self._provider = fournisseur
        return self._provider, self._modele or agent.modele


class ProjetDuRun(Protocol):
    """Où travaille un run — la seule chose que le conteur demande aux projets."""

    def __call__(self, projet_id: str | None) -> Projet | None: ...


class ConteurDeFin:
    """Écrit dans le fil qui a demandé un run ce que ce run a laissé (#1224).

    Une dépendance par question, toutes injectables : `chat` pour trouver le fil
    et y écrire, `state` pour ce que le run a fait, `projet` pour savoir où est
    le livrable, `redacteur` pour les mots. C'est ce qui rend le conteur jouable
    sans modèle, sans disque et sans API.

    `projet` est une fonction `projet_id -> Projet | None` plutôt qu'un
    `ServiceProjets` : le conteur n'a besoin que d'une racine et d'un périmètre,
    et lui passer le service entier le ferait dépendre d'un store qu'il ne lit
    pas.
    """

    def __init__(
        self,
        *,
        chat: ServiceChat,
        state: ControlTowerState,
        agent: Agent,
        projet: ProjetDuRun,
        redacteur: RedacteurRecit | None = None,
    ) -> None:
        self._chat = chat
        self._state = state
        self._agent = agent
        self._projet = projet
        self._redacteur = redacteur if redacteur is not None else RedacteurModele()
        # Les récits en cours d'écriture, par `run_id` : `deja_raconte` lit le
        # fil **persisté**, donc il ne voit pas un récit encore en vol. Deux
        # événements terminaux qui se suivent de près écriraient sinon deux fois.
        self._en_vol: set[str] = set()

    async def raconter(self, run_id: str) -> MessageChat | None:
        """Écrit le récit de `run_id`, ou rend `None` en disant pourquoi au journal.

        Cinq raisons de ne rien écrire, et aucune n'est une panne : le run n'est
        pas soldé, il n'a été demandé dans aucun fil, son récit y est déjà, le
        modèle n'a rien rendu, ou l'écriture au fil a échoué. Dans les cinq cas
        l'annonce de #928 reste — elle est dérivée du persisté et ne dépend de
        rien d'ici.
        """
        if run_id in self._en_vol:
            return None
        execution = self._state.execution(run_id)
        if execution is None or execution.statut not in STATUTS_EXECUTION_TERMINAUX:
            return None
        conversation = self._chat.conversation_du_run(self._agent.nom, run_id)
        if conversation is None:
            return None  # run lancé ailleurs que dans un fil : la cloche est là pour lui
        if deja_raconte(self._chat.fil(self._agent.nom, conversation), run_id):
            return None
        self._en_vol.add(run_id)
        try:
            return await self._ecrire(execution, conversation)
        finally:
            self._en_vol.discard(run_id)

    async def _ecrire(
        self, execution: EtatExecution, conversation: str
    ) -> MessageChat | None:
        """Lit le livrable, fait rédiger, et pose le message au fil."""
        projet = self._projet(execution.projet_id)
        livrable = await self._lire(projet)
        try:
            texte = await self._redacteur.rediger(
                agent=self._agent, contexte=contexte_du_recit(self._state, execution, livrable)
            )
        except Exception:  # noqa: BLE001 — un modèle muet ne fabrique pas de prose
            _LOGGER.exception(
                "Récit de fin non écrit pour le run %s : le rédacteur n'a pas répondu. "
                "L'annonce de fin (#928) reste dans le fil et dans la cloche.",
                execution.run_id,
            )
            return None
        if not texte:
            _LOGGER.warning(
                "Récit de fin non écrit pour le run %s : le rédacteur a rendu un texte vide.",
                execution.run_id,
            )
            return None
        try:
            return await self._chat.raconter_la_fin(
                self._agent,
                contenu=texte,
                run_id=execution.run_id,
                conversation=conversation,
            )
        except Exception:  # noqa: BLE001 — le fil est ailleurs, le run est fini
            _LOGGER.exception(
                "Récit de fin non posé au fil pour le run %s.", execution.run_id
            )
            return None

    async def _lire(self, projet: Projet | None) -> Livrable | None:
        """Le livrable du run — `None` quand le run ne relève d'aucun projet lisible.

        Dans un fil d'exécution à part : lire un dossier ouvre des fichiers, et
        la boucle de l'API ne doit pas le porter — même règle que la lecture des
        sources d'un message (`ServiceChat._lire`).
        """
        if projet is None or not projet.racine:
            return None
        return await asyncio.to_thread(
            lire_le_livrable, projet.racine_chemin, projet.perimetre
        )


def contexte_du_recit(
    state: ControlTowerState, execution: EtatExecution, livrable: Livrable | None
) -> str:
    """Ce que le rédacteur lit : ce que le run a fait, puis ce qu'il a laissé.

    Trois blocs, et l'ordre est le raisonnement : *ce qui s'est passé* (la fiche
    du run, telle que le fil la donne déjà au juge — `orchestration.fiche_du_run`,
    donc une seule formule pour les deux canaux), *les fichiers qu'on peut
    offrir en lien* (des chemins du poste, à recopier tels quels), puis *le
    contenu lu*, **encadré comme donnée**.

    L'encadrement n'est pas refait ici : `Livrable.contexte` est la sortie de
    `contexte_markdown` et de rien d'autre. Les chemins, eux, sont hors du bloc
    encadré à dessein — le modèle doit pouvoir les recopier dans un lien, ce
    qu'on ne demande d'aucun contenu.
    """
    from maestro.controltower.orchestration import fiche_du_run

    blocs = ["## Le run qui vient de finir", "", *fiche_du_run(state, execution)]
    if livrable is None or livrable.vide:
        blocs.extend(
            [
                "",
                "## Le livrable",
                "",
                "Aucun fichier lisible : ce run n'a pas de racine de projet, ou elle "
                "est vide. Dis-le simplement, et ne propose aucune commande.",
            ]
        )
        return "\n".join(blocs)
    blocs.extend(["", "## Fichiers du livrable", "", f"Racine : {livrable.racine}"])
    blocs.extend(f"- {chemin}" for chemin in livrable.chemins)
    if not livrable.chemins:
        blocs.append("- (aucun fichier à offrir en lien)")
    blocs.extend(["", livrable.contexte])
    return "\n".join(blocs)

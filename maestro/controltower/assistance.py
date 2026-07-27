"""Canal d'assistance utilisateur de la Control Tower (ticket #123, lot 7 de #116).

Le chat existant (#84, `maestro.controltower.chat`) sert au dialogue utilisateur ↔
**agent exécutant** : on s'y adresse au Développeur, au DevOps… à propos du travail
en cours. Il manquait un canal pour les questions sur **l'outil lui-même** — « à
quoi sert cette page ? », « comment j'approuve une validation ? » —, accessible
depuis n'importe quelle page sans quitter ce qu'on est en train de faire.

C'est ce canal : un fil `assistance` qui réutilise **toute** l'infrastructure du
chat (`ChatStore` pour la persistance, `ServiceChat` pour l'acheminement, le bus
d'événements #46 pour le temps réel) avec deux pièces qui lui sont propres :

- `AGENT_ASSISTANCE` : la fiche de l'assistant. Ce n'est **pas** un agent du
  catalogue (`DEFAULT_AGENTS`) — il n'exécute aucune tâche, ne consomme pas de
  budget et n'apparaît ni au routage ni au Kanban. Il n'a de l'`Agent` que ce
  dont le chat a besoin : un nom (la clé du fil), un rôle et un prompt système.
- `RepondeurAssistance` : la production de la réponse, **déterministe et sans
  modèle**. Un choix assumé au POC : les questions portent sur un produit dont
  aucun modèle n'a la documentation, donc des réponses écrites ici sont plus
  justes qu'une génération — et le canal marche à l'identique dans la démo (#65),
  sans fournisseur ni authentification. Le contrat étant celui de `RepondeurChat`,
  passer à `RepondeurModele` le jour où l'assistant devra raisonner sur l'état
  courant reste un changement d'une ligne, côté `create_app`.

Le fil est persisté comme les autres (`core/chat/assistance.jsonl`) : l'historique
survit au rechargement de la page, et les endpoints `/api/chat/assistance` sont
ceux du chat — aucun contrat REST supplémentaire à apprendre côté UI.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from maestro.agents.catalog import MODELE_EXECUTANT_DEFAUT, Agent
from maestro.controltower.chat import MessageChat, RepondeurChat

#: Le nom du fil d'assistance — la clé de stockage (`core/chat/assistance.jsonl`),
#: le segment d'URL des endpoints `/api/chat/{agent}` et le `agent` des événements
#: `chat.message` que le panneau flottant filtre côté UI. Ce nom est **réservé**
#: (`maestro.agents.store.NOMS_RESERVES`) : un agent personnalisé ne peut pas le
#: prendre, sans quoi il partagerait ce fil et serait masqué par l'assistant.
NOM_ASSISTANCE = "assistance"

#: Le cadre de l'assistant, si un jour il passe par un modèle (`RepondeurModele`) :
#: il aide à se servir de la Control Tower, il ne réalise pas de tâche.
_PROMPT_ASSISTANCE = """\
Tu es l'assistant de la Control Tower de Maestro, le poste de pilotage depuis
lequel un humain supervise une équipe d'agents IA (tableau de bord des tâches,
état des agents, playbooks, chat, coûts, validations humaines, paramètres).

Tu réponds aux questions de l'utilisateur SUR L'OUTIL : à quoi sert une page, où
trouver un réglage, comment trancher une validation. Tu n'exécutes pas de tâche et
tu ne parles pas à la place des agents — pour travailler avec un agent, l'utilisateur
passe par la page Chat. Réponds en français, brièvement, et dis-le franchement
quand tu ne sais pas."""

#: La fiche de l'assistant, hors catalogue (voir le module) : le chat n'a besoin
#: que du nom, du rôle et du prompt système. Les compétences restent vides — rien
#: ne doit pouvoir lui router une tâche.
AGENT_ASSISTANCE = Agent(
    nom=NOM_ASSISTANCE,
    role="Assistant Control Tower",
    competences=frozenset(),
    modele=MODELE_EXECUTANT_DEFAUT,
    prompt_systeme=_PROMPT_ASSISTANCE,
)


def _normaliser(texte: str) -> str:
    """Le texte réduit pour la comparaison : minuscules, sans accents ni ponctuation.

    « Où sont les COÛTS ? » et « ou est le cout » doivent tomber sur le même sujet :
    l'utilisateur tape vite, souvent sans accents.
    """
    sans_accents = "".join(
        c
        for c in unicodedata.normalize("NFD", texte.lower())
        if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"[^a-z0-9]+", " ", sans_accents).strip()


@dataclass(frozen=True)
class SujetAssistance:
    """Un sujet d'aide : les mots qui le déclenchent et la réponse à rendre.

    `mots` sont cherchés dans la question **normalisée** (`_normaliser`) : ils
    s'écrivent donc en minuscules sans accents, et peuvent tenir en plusieurs
    mots (« prise en main »). Le nombre de mots trouvés fait le score — le sujet
    le mieux couvert répond.
    """

    identifiant: str
    mots: tuple[str, ...]
    reponse: str

    def score(self, question: str) -> int:
        """Nombre de mots-clés du sujet présents dans la question normalisée."""
        return sum(1 for mot in self.mots if mot in question)


#: Les sujets couverts, dans l'ordre où ils départagent les ex æquo (le premier
#: gagne). Chaque réponse dit **où aller** et **ce qu'on y fait** : c'est ce qu'on
#: attend d'une aide contextuelle, pas un cours sur le produit.
SUJETS_ASSISTANCE: tuple[SujetAssistance, ...] = (
    SujetAssistance(
        "validations",
        ("validation", "valider", "approuver", "refuser", "arbitrage", "sensible"),
        "Une action sensible met la tâche en pause et attend votre arbitrage. Les "
        "demandes en attente apparaissent en tête du tableau de bord et dans le "
        "panneau de la cloche (barre supérieure) : « Approuver » fait reprendre la "
        "tâche, « Refuser » l'annule proprement. La page Validations en garde "
        "l'historique, décision et contexte compris.",
    ),
    SujetAssistance(
        "couts",
        ("cout", "couts", "budget", "depense", "prix", "token", "tokens", "analytics"),
        "Coûts & analytics agrège la dépense sur la période choisie : total, "
        "évolution dans le temps, répartition par agent, détail par tâche et par "
        "exécution. Le coût cumulé reste affiché en permanence dans la barre "
        "supérieure — un coût « — » signifie que le fournisseur n'a rien rapporté, "
        "pas que la tâche était gratuite.",
    ),
    SujetAssistance(
        "playbooks",
        ("playbook", "playbooks", "prompt", "consigne", "instruction", "version"),
        "La page Playbooks porte les consignes de chaque agent. On y édite le "
        "playbook courant, on relit l'historique des versions et on restaure une "
        "version passée — l'historique est append-only, rien ne se perd. La version "
        "publiée est reprise à chaud par les agents dès la tâche suivante.",
    ),
    SujetAssistance(
        "agents",
        ("agent", "agents", "capacite", "instance", "instances", "catalogue", "desactiver"),
        "La page Agents montre l'état de chacun (libre/occupé, tâches en cours, "
        "compteurs, coût cumulé) et son réglage de capacité : le désactiver pour "
        "qu'il ne reçoive plus de tâches, ou plafonner ses instances simultanées. "
        "Le Catalogue, lui, sert à consulter les agents fournis et à créer vos "
        "propres agents.",
    ),
    SujetAssistance(
        "chat",
        ("chat", "parler", "discuter", "message", "conversation", "demander a un agent"),
        "La page Chat ouvre un fil avec un agent du catalogue : on lui écrit "
        "directement et sa réponse arrive dans le fil, qui est conservé. À ne pas "
        "confondre avec moi — je réponds sur l'outil, lui travaille sur vos tâches.",
    ),
    SujetAssistance(
        "taches",
        ("tache", "taches", "kanban", "colonne", "statut", "bloquee", "echec", "reassigner"),
        "Le tableau de bord répartit les tâches par statut — assignées, en cours, "
        "bloquées, terminées, échecs — et se met à jour en direct. Une carte donne "
        "l'agent, le coût et l'usage de la tâche ; on peut la réassigner à un autre "
        "agent depuis sa fiche.",
    ),
    SujetAssistance(
        "parametres",
        ("parametre", "parametres", "reglage", "api", "url", "connexion", "backend"),
        "Les Paramètres portent l'URL du backend visé et le bouton « Tester la "
        "connexion » (qui interroge le REST, indépendamment du temps réel), ainsi "
        "que la section Apparence : thème clair/sombre/système et repli de la barre "
        "latérale.",
    ),
    SujetAssistance(
        "theme",
        ("theme", "sombre", "clair", "apparence", "couleur", "nuit"),
        "Le thème se bascule depuis l'icône de la barre supérieure, ou depuis "
        "Paramètres › Apparence : clair, sombre, ou « système » pour suivre le "
        "réglage de votre machine. Le choix est retenu d'une visite à l'autre.",
    ),
    SujetAssistance(
        "temps-reel",
        ("temps reel", "websocket", "reconnexion", "deconnecte", "fige", "actualiser"),
        "La Control Tower suit l'orchestration par WebSocket : rien à recharger. Le "
        "badge « Temps réel connecté » indique l'état du flux ; s'il passe à "
        "« Reconnexion… », la connexion se rétablit seule et l'affichage rattrape "
        "ce qui s'est passé pendant la coupure.",
    ),
    SujetAssistance(
        "guide",
        ("visite", "guide", "guidee", "decouvrir", "prise en main", "tour"),
        "La visite guidée fait le tour du poste de pilotage en quelques étapes. "
        "Elle se lance à la première visite, et se relance quand vous voulez depuis "
        "le menu d'aide (l'icône « ? » de la barre supérieure) › Visite guidée.",
    ),
    SujetAssistance(
        "notifications",
        ("notification", "notifications", "cloche", "badge", "alerte"),
        "La cloche de la barre supérieure vous suit de page en page : son badge "
        "compte les validations en attente, et son panneau permet de les trancher "
        "sans quitter la page courante, puis rappelle l'activité récente notable.",
    ),
    SujetAssistance(
        "maestro",
        ("maestro", "control tower", "c est quoi", "sert a quoi", "presentation"),
        "Maestro orchestre une équipe d'agents IA : l'orchestrateur découpe une "
        "demande en tâches, les route vers l'agent compétent et vous rend la main "
        "sur les décisions sensibles. La Control Tower est le poste de pilotage de "
        "tout ça — l'état en direct, les coûts, les arbitrages.",
    ),
)

#: La réponse quand aucun sujet ne ressort : on oriente au lieu d'inventer.
_ORIENTATION = (
    "Je ne suis pas sûr de savoir répondre à ça. Je peux vous aider sur le tableau "
    "de bord et les tâches, les agents et leur capacité, les playbooks, le chat avec "
    "un agent, les coûts, les validations humaines, les notifications, les paramètres "
    "et le thème. Pour une question sur votre projet lui-même, la page Chat vous met "
    "en relation avec l'agent compétent."
)

def repondre_assistance(question: str) -> str:
    """La réponse d'aide à `question` : le sujet le mieux couvert, sinon l'orientation."""
    normalisee = _normaliser(question)
    meilleur: SujetAssistance | None = None
    meilleur_score = 0
    for sujet in SUJETS_ASSISTANCE:
        score = sujet.score(normalisee)
        if score > meilleur_score:
            meilleur, meilleur_score = sujet, score
    return meilleur.reponse if meilleur is not None else _ORIENTATION


class RepondeurAssistance(RepondeurChat):
    """Le répondeur du canal d'assistance : déterministe, sans modèle ni réseau.

    Ne lit que le **dernier** message utilisateur du fil : chaque question d'aide
    se suffit à elle-même, et l'absence de contexte accumulé rend la réponse
    reproductible — la même question donne la même réponse, en démo comme en
    production.
    """

    async def repondre(self, agent: Agent, fil: Sequence[MessageChat]) -> str:
        derniere = fil[-1].contenu if fil else ""
        return repondre_assistance(derniere)

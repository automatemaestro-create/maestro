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

⚠ **Le lexique juge des MOTS, plus des sous-chaînes** (#684). Le score se
calculait par `mot in question`, si bien qu'un mot-clé court trouvé **au milieu**
d'un autre mot faisait répondre son sujet avec aplomb : `api` dans « r**api**de »
(le cas du ticket : « comment rendre la page plus rapide ? » répondait sur l'URL
du backend), `cout` dans « é**cout**er », `tour` dans « re**tour** », `version`
dans « con**version** », `connexion` dans « **re**connexion » — celui-là faisant
répondre « Paramètres » à une question sur le temps réel. La comparaison porte
désormais sur des **mots entiers** (`_mot_present`), à la flexion près.

Le ticket laissait trois issues ; **c'est (a) qui a été retenue** — resserrer la
comparaison et compléter la table —, et les deux autres écartées **pour la raison
que #123 donnait déjà**, laquelle n'a pas bougé : un modèle qui n'a pas la
documentation du produit **invente**, et une aide qui invente est pire qu'une aide
qui oriente. Passer à `RepondeurModele` (option b) ne se répond donc pas en
changeant de juge mais en **donnant la doc au modèle** (contexte/RAG sur `docs/`,
`apps/web/README.md`) : c'est **#748**, ouvert par ce ticket-ci pour que la
question survive au merge qui le ferme. L'hybride (option c) le suppose fait —
sans doc en contexte, il ne fait qu'échanger l'orientation franche contre une
réponse large et fausse, c'est-à-dire qu'il perd la seule propriété que ce ticket
n'avait pas le droit de perdre ; il se décidera donc dans #748.

⚠ **Ce qui reste après ce ticket est nommé, et c'est le second défaut de #684** :
un sujet absent de la table tombe sur l'orientation, quelle que soit la clarté de
la question. Le resserrement le rend même *plus* fréquent — d'où la tolérance de
flexion ci-dessous, qui en amortit la moitié mécanique (les pluriels), et d'où
#748, qui seul le traite. Compléter la table repousse la limite, il ne la lève
pas : elle est celle d'une table.

**Ce qui a été fait en plus du resserrement compte autant** : la table datait de
#123 et l'interface avait bougé sous elle — elle envoyait encore vers une page
« Playbooks » et un « Catalogue » qui sont devenus des onglets de la fiche d'agent
(#190), décrivait le tableau de bord comme un Kanban de tâches alors que le Kanban
est passé dans la vue d'un run (#476), et citait un badge « Temps réel connecté »
retiré depuis (#691). Une réponse qui envoie vers un écran disparu est du même
tonneau que la sous-chaîne : elle répond faux, sans le dire. Les sujets manquants
au menu d'aujourd'hui — runs, journal, intégrations, projets, cadrage — ont été
ajoutés dans le même geste : c'est la moitié « compléter la table » de l'option
(a), et c'est elle qui répond au second exemple du ticket (« où est le bouton pour
relancer un run bloqué ? »), que le resserrement seul laissait sur l'orientation.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from maestro.agents.catalog import MODELE_EXECUTANT_DEFAUT, Agent
from maestro.controltower.chat import MessageChat, RepondeurChat, normaliser

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
lequel un humain supervise une équipe d'agents IA (tableau de bord, runs, fil avec
l'orchestration, agents et playbooks, intégrations MCP, coûts, validations
humaines, journal, paramètres).

Tu réponds aux questions de l'utilisateur SUR L'OUTIL : à quoi sert une page, où
trouver un réglage, comment trancher une validation. Tu n'exécutes pas de tâche et
tu ne parles pas à la place des agents — pour lancer du travail, l'utilisateur
passe par le fil de la page Chat. Réponds en français, brièvement, et dis-le
franchement quand tu ne sais pas."""

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

#: Les terminaisons tolérées sur le **dernier mot** d'un mot-clé — la flexion
#: ordinaire du français : pluriel, féminin, féminin pluriel. « cout » couvre
#: ainsi couts/coute/coutes, « bloque » couvre bloques/bloquee/bloquees, sans
#: qu'il faille écrire les quatre formes de chaque entrée.
#:
#: ⚠ Cette tolérance n'est **pas** le retour de la sous-chaîne, et la différence
#: est ce que ce module garde : un mot-clé s'aligne toujours sur une **frontière
#: de mot des deux côtés**, la flexion ne relâchant que la fin. « api » couvre
#: {api, apis, apie, apies} et ne rencontrera jamais « rapide ». Elle est là parce
#: que l'égalité stricte aurait aggravé le vrai défaut du mécanisme — sa
#: couverture : chaque pluriel oublié dans la table devient une question sans
#: réponse, et la table dérive en silence vers l'orientation.
_FLEXIONS: tuple[str, ...] = ("", "s", "e", "es")


def _mot_present(mots: list[str], cle: str) -> bool:
    """`cle` apparaît-il comme **mots entiers** dans `mots` (à la flexion près) ?

    `mots` est la question déjà normalisée et découpée ; `cle` un mot-clé de la
    table, lui aussi normalisé — il peut en compter plusieurs (« prise en main »),
    auquel cas ils doivent se suivre. Seul le **dernier** accepte une flexion : la
    porter sur les précédents ferait matcher « prises en mains » sans rien
    apporter, et ouvrirait la règle sans qu'on sache dire jusqu'où.
    """
    attendus = cle.split()
    if not attendus:
        return False
    tete, dernier = attendus[:-1], attendus[-1]
    formes = {dernier + flexion for flexion in _FLEXIONS}
    largeur = len(attendus)
    for depart in range(len(mots) - largeur + 1):
        if mots[depart : depart + largeur - 1] != tete:
            continue
        if mots[depart + largeur - 1] in formes:
            return True
    return False


@dataclass(frozen=True)
class SujetAssistance:
    """Un sujet d'aide : les mots qui le déclenchent et la réponse à rendre.

    `mots` sont cherchés dans la question **normalisée** (`chat.normaliser`) : ils
    s'écrivent donc en minuscules sans accents, et peuvent tenir en plusieurs
    mots (« prise en main »). Le nombre de mots trouvés fait le score — le sujet
    le mieux couvert répond.

    Ils sont cherchés en **mots entiers** (#684) : écrire ici le singulier suffit,
    la flexion étant tolérée (`_FLEXIONS`), mais une autre forme — un verbe
    conjugué, un synonyme — se déclare en toutes lettres. C'est le prix assumé de
    la règle : une forme manquante fait retomber la question sur l'orientation,
    ce qui est une réponse honnête, là où la sous-chaîne fabriquait une réponse
    fausse et assurée.
    """

    identifiant: str
    mots: tuple[str, ...]
    reponse: str

    def score(self, question: str) -> int:
        """Nombre de mots-clés du sujet présents dans la question normalisée."""
        mots = question.split()
        return sum(1 for cle in self.mots if _mot_present(mots, cle))


#: Les sujets couverts, **du plus précis au plus général** — c'est cet ordre qui
#: départage les ex æquo (le premier gagne), d'où les sujets aux mots-clés larges
#: (paramètres, et `maestro` qui répond au « c'est quoi ? ») rangés en dernier.
SUJETS_ASSISTANCE: tuple[SujetAssistance, ...] = (
    # ⚠ « cadrage » passe **avant** « validations », et c'est une décision : les
    # deux se tranchent par « Approuver » / « Refuser », mais **pas au même
    # endroit** — un brief se décide dans le fil, une action sensible sur la page
    # Validations. « Comment approuver le brief ? » fait donc ex æquo, et rangé
    # derrière, `cadrage` envoyait vers le mauvais écran.
    SujetAssistance(
        "cadrage",
        ("brief", "cadrage", "cadrer", "reformulation", "reformuler", "suspendu"),
        "Avant de lancer le travail, l'orchestration reformule votre demande en un "
        "brief : le run reste suspendu tant que vous ne l'avez pas tranché. Le "
        "cadrage se lit et se décide dans le fil (page Chat) — « Approuver », "
        "« Approuver la version corrigée » ou « Refuser ». Les briefs en attente "
        "sont rappelés en tête du tableau de bord et dans la cloche.",
    ),
    SujetAssistance(
        "validations",
        (
            "validation",
            "valider",
            "valide",
            "approuver",
            "approuve",
            "refuser",
            "refuse",
            "arbitrage",
            "arbitrer",
            "trancher",
            "tranche",
            "sensible",
        ),
        "Une action sensible met la tâche en pause et attend votre arbitrage. Les "
        "demandes en attente apparaissent en tête du tableau de bord et dans le "
        "panneau de la cloche (barre supérieure) : « Approuver » fait reprendre la "
        "tâche, « Refuser » l'annule proprement. La page Validations porte la file "
        "en plein format — la plus ancienne d'abord — et garde en dessous les "
        "décisions déjà tranchées.",
    ),
    SujetAssistance(
        "playbooks",
        (
            "playbook",
            "prompt",
            "consigne",
            "instruction",
            "version",
            "publier",
            "restaurer",
            "amelioration",
        ),
        "Le playbook d'un agent — ses consignes — s'édite dans sa fiche : Agents › "
        "l'agent › onglet Playbook. On y publie une nouvelle version, on relit "
        "l'historique et on restaure une version passée : le dépôt est "
        "append-only, restaurer republie, rien ne se perd. Les propositions "
        "d'auto-amélioration en attente s'y approuvent ou s'y rejettent. Il n'y a "
        "plus de page « Playbooks » — l'ancienne adresse redirige ici.",
    ),
    SujetAssistance(
        "integrations",
        (
            "integration",
            "mcp",
            "serveur",
            "pool",
            "bibliotheque",
            "permission",
            "secret",
        ),
        "La page Intégrations porte les serveurs MCP : le « Pool projet », ce qui "
        "est disponible sur ce projet, et la « Bibliothèque » de serveurs curés "
        "qu'on y ajoute — avec en tête le nombre au pool, les agents équipés et "
        "les secrets à revoir. Ce qu'un agent a le droit d'en faire se lit dans sa "
        "fiche, onglet « MCP & permissions ».",
    ),
    SujetAssistance(
        "projets",
        ("projet", "racine", "perimetre", "selecteur", "depot"),
        "Un projet est le cadre de tout le reste : sa racine sur le disque, son "
        "périmètre inclus/exclu, son origine VCS. On en change au sélecteur de "
        "projet de la barre supérieure, dont l'entrée « Gérer les projets » ouvre "
        "l'écran où on les crée, modifie et supprime — la suppression s'y arme en "
        "deux temps.",
    ),
    SujetAssistance(
        "journal",
        ("journal", "activite", "evenement", "notable", "filtre", "filtrer"),
        "La page Journal déroule l'activité persistée du projet en plein format : "
        "recherche plein texte, filtres par type d'événement, par agent et par "
        "tâche, et une case « Notable seulement » qui ne garde que ce que remonte "
        "la cloche. Le tableau de bord n'en montre qu'un aperçu, avec un renvoi "
        "vers cette page.",
    ),
    SujetAssistance(
        "couts",
        (
            "cout",
            "budget",
            "depense",
            "prix",
            "token",
            "analytics",
            "plafond",
            "facture",
            "consommation",
        ),
        "Coûts & analytics agrège la dépense sur la période choisie : coût total, "
        "tokens, appels modèle et exécutions en tête, puis l'évolution dans le "
        "temps, la répartition par agent et le détail de la période — par tâche ou "
        "par exécution. Le coût cumulé du projet reste affiché dans la barre "
        "supérieure ; un coût « — » signifie que le fournisseur n'a rien rapporté, "
        "pas que la tâche était gratuite. Les plafonds, eux, se posent dans "
        "Paramètres › La dépense › Coûts & plafonds.",
    ),
    SujetAssistance(
        "runs",
        (
            "run",
            "pipeline",
            "frise",
            "kanban",
            "interrompre",
            "interrompu",
            "arreter",
            "arrete",
            "pause",
            "reprendre",
            "relancer",
            "relance",
            "bloque",
            "avancement",
        ),
        "La page Runs liste les runs du projet, du plus récent au plus ancien ; en "
        "ouvrir un donne quatre lectures du même run — Pipeline, Kanban, Frise et "
        "Journal. Les gestes sont sur la carte du run, dans la liste comme dans son "
        "détail : « Mettre en pause » / « Reprendre », et « Interrompre », qui "
        "s'arme en deux temps et tue les tâches en vol. Un run que l'hôte a perdu "
        "se relance par « Reprendre » dans le panneau « Runs interrompus » du "
        "tableau de bord ; un run seulement suspendu, lui, attend une décision de "
        "votre part — son cadrage dans le fil, ou une validation.",
    ),
    SujetAssistance(
        "taches",
        (
            "tache",
            "tableau de bord",
            "accueil",
            "colonne",
            "statut",
            "echec",
            "reassigner",
            "indicateur",
        ),
        "Le tableau de bord donne l'état du poste d'un coup d'œil : ce qui attend "
        "un arbitrage (briefs, validations, runs interrompus), quatre indicateurs "
        "— run en cours, tâches, agents, dépense —, l'état des runs groupés par "
        "régime (en cours, suspendus, en pause, interrompus, soldés du jour) et un "
        "aperçu du fil d'activité. Le détail tâche par tâche, lui, est dans le "
        "run : c'est sa lecture « Kanban ».",
    ),
    # ⚠ « chat » passe **avant** « agents », et l'ordre porte ici une décision :
    # « parler à un agent », « demander à un agent » nomment tous deux le mot
    # `agent`, donc font ex æquo, donc reviennent au premier des deux. Rangé
    # derrière, `chat` ne pouvait **jamais** gagner un tel ex æquo — son mot-clé
    # « demander a un agent » était mort-né, et c'est un invariant de la table
    # (`test_chaque_mot_cle_atteint_un_sujet_qui_le_declare`) qui l'a montré.
    # Une intention conversationnelle va au fil ; nommer l'agent comme objet
    # (le désactiver, le créer, sa capacité) ajoute un mot-clé à `agents`, qui
    # reprend alors la main par le score.
    SujetAssistance(
        "chat",
        (
            "chat",
            "fil",
            "parler",
            "discuter",
            "message",
            "conversation",
            "orchestrateur",
            "orchestration",
            "lancer",
            "lance",
            "objectif",
            "piece jointe",
            "fichier",
            "dossier",
            "source",
            "deposer",
            "depose",
            "demander a un agent",
        ),
        "La page Chat porte le fil avec l'orchestration, et c'est la porte d'entrée "
        "du travail : on y écrit sa demande, et un run s'ouvre. On peut y joindre "
        "des fichiers, un dossier ou une adresse à lire — chaque réponse dit ce "
        "qui a été lu —, et c'est là que se tranche le cadrage d'un run. Pour "
        "parler à un agent en particulier, passez par sa fiche : Agents › l'agent "
        "› onglet Chat.",
    ),
    SujetAssistance(
        "agents",
        (
            "agent",
            "capacite",
            "instance",
            "catalogue",
            "desactiver",
            "desactive",
            "activer",
            "fiche",
        ),
        "La page Agents réunit les agents fournis avec le produit et les vôtres ; "
        "« Nouvel agent » en crée un. Chaque fiche ouvre quatre onglets — Profil, "
        "Playbook, MCP & permissions, Chat. Activer ou désactiver un agent et "
        "borner ses exécutions simultanées se règle dans Paramètres › "
        "L'exécution › Agents & capacité ; son fournisseur et son modèle, juste à "
        "côté, dans Fournisseurs & modèles.",
    ),
    SujetAssistance(
        "notifications",
        ("notification", "cloche", "badge", "alerte", "pastille"),
        "La cloche de la barre supérieure vous suit de page en page : son badge "
        "compte les validations et les briefs en attente. Son panneau les range en "
        "« Briefs à trancher » (qui renvoient au fil), « À valider » (décidable "
        "sur place) et « Activité récente ».",
    ),
    SujetAssistance(
        "theme",
        ("theme", "sombre", "clair", "apparence", "couleur", "nuit"),
        "Le thème se bascule depuis l'icône soleil/lune de la barre supérieure, ou "
        "depuis Paramètres › Le poste › Apparence : clair, sombre, ou « système » "
        "pour suivre le réglage de votre machine. Le choix est retenu d'une visite "
        "à l'autre.",
    ),
    SujetAssistance(
        "temps-reel",
        (
            "temps reel",
            "websocket",
            "reconnexion",
            "deconnecte",
            "fige",
            "actualiser",
            "rafraichir",
            "recharger",
        ),
        "La Control Tower suit l'orchestration par WebSocket : il n'y a rien à "
        "recharger. Quand tout va bien, rien ne s'affiche ; si le flux se coupe, "
        "« Reconnexion… » apparaît dans la barre supérieure, la connexion se "
        "rétablit seule et l'affichage rattrape ce qui s'est passé. Paramètres › "
        "Le poste › Général donne l'état du service et un bouton « Tester la "
        "connexion ».",
    ),
    SujetAssistance(
        "guide",
        ("visite", "guide", "decouvrir", "prise en main", "tour"),
        "La visite guidée fait le tour du poste de pilotage en quelques étapes. "
        "Elle se lance à la première visite, et se relance quand vous voulez depuis "
        "le menu d'aide (l'icône « ? » de la barre supérieure) › Visite guidée. Le "
        "même menu porte « Poser une question », qui m'ouvre.",
    ),
    SujetAssistance(
        "parametres",
        (
            "parametre",
            "reglage",
            "regler",
            "regle",
            "configuration",
            "configurer",
            "api",
            "url",
            "connexion",
            "backend",
            "fournisseur",
            "modele",
        ),
        "Les Paramètres rassemblent les réglages en trois familles : « Le poste » "
        "(Général, Apparence, Notifications), « L'exécution » (Agents & capacité, "
        "Fournisseurs & modèles) et « La dépense » (Coûts & plafonds). Général "
        "affiche l'URL du backend visé — fixée au build — et le bouton « Tester la "
        "connexion », qui interroge le REST indépendamment du temps réel.",
    ),
    SujetAssistance(
        "maestro",
        ("maestro", "control tower", "c est quoi", "sert a quoi", "presentation"),
        "Maestro orchestre une équipe d'agents IA : vous écrivez ce que vous voulez "
        "dans le fil, l'orchestration le reformule en un brief que vous tranchez, "
        "puis découpe le travail en tâches et les route vers l'agent compétent, en "
        "vous rendant la main sur les décisions sensibles. La Control Tower est le "
        "poste de pilotage de tout ça — l'état en direct, les runs, les coûts, les "
        "arbitrages.",
    ),
)

#: La réponse quand aucun sujet ne ressort : on oriente au lieu d'inventer.
#: C'est la propriété que #684 avait interdiction de perdre — resserrer la
#: comparaison rend cette réponse **plus fréquente**, et c'est voulu : « je ne
#: sais pas » est un aveu, la réponse d'à côté était un mensonge.
_ORIENTATION = (
    "Je ne suis pas sûr de savoir répondre à ça. Je peux vous aider sur le tableau "
    "de bord, les runs et leurs tâches, le fil et le cadrage d'un run, les agents "
    "et leurs playbooks, les intégrations MCP, les coûts, les validations "
    "humaines, le journal, les notifications, les projets, les paramètres et le "
    "thème. Pour une question sur le contenu de votre projet, c'est la page Chat "
    "qui vous met en relation avec l'orchestration."
)


def sujet_assistance(question: str) -> SujetAssistance | None:
    """Le sujet le mieux couvert par `question`, ou `None` si aucun ne ressort.

    Séparé de `repondre_assistance` parce que « quel sujet ? » et « quel texte ? »
    sont deux questions : la seconde se réécrit quand l'interface bouge — elle
    l'a fait en #684 —, la première est le contrat, et c'est elle que les tests
    visent.
    """
    normalisee = normaliser(question)
    meilleur: SujetAssistance | None = None
    meilleur_score = 0
    for sujet in SUJETS_ASSISTANCE:
        score = sujet.score(normalisee)
        if score > meilleur_score:
            meilleur, meilleur_score = sujet, score
    return meilleur


def repondre_assistance(question: str) -> str:
    """La réponse d'aide à `question` : le sujet le mieux couvert, sinon l'orientation."""
    sujet = sujet_assistance(question)
    return sujet.reponse if sujet is not None else _ORIENTATION


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

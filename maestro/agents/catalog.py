"""Catalogue des agents exécutants et leurs compétences (ticket #6).

Matérialise le catalogue d'agents par défaut (docs/04-specifications-agents.md §2)
et les entités `AGENT`/`CAPABILITY` (docs/03-modele-de-donnees.md) : chaque agent
déclare ses **compétences** (tags) — base de l'auto-assignation par le routeur
(`maestro.router`) — et le **modèle** + **prompt système** avec lesquels il exécute
ses tâches via la couche fournisseur (`ModelProvider`).

Ne figurent ici que les **agents exécutants par défaut** (Développeur, BDD, DevOps,
Designer, QA) : le Chef de projet n'exécute pas de tâche, il les découpe
(`maestro.orchestrator`) et synthétise. Ce module reste la part « du code » du
catalogue ; les **agents personnalisés** (#72, EF-03) se définissent hors du code
(`maestro.agents.store`) et `catalogue()` assemble le catalogue effectif.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from maestro.agents.playbook_du_code import socle

#: Modèle par défaut des exécutants au POC (Claude Sonnet, cf. docs/04 §2). Le rôle
#: est indépendant du fournisseur/modèle : on peut le remplacer sans le toucher.
MODELE_EXECUTANT_DEFAUT = "claude-sonnet-5"


@dataclass(frozen=True)
class Agent:
    """Un agent exécutant : une identité, des compétences, un modèle, un playbook.

    `competences` sont les tags de l'entité CAPABILITY : le routeur y confronte les
    `competences_requises` d'une tâche pour l'auto-assignation. `prompt_systeme`
    dérive du playbook (docs/04 §3) et cadre l'exécution ; `modele` est le modèle
    conseillé par défaut (POC Claude), remplaçable sans changer le rôle.
    """

    nom: str
    role: str
    competences: frozenset[str]
    modele: str
    prompt_systeme: str

    def couverture(self, competences_requises: frozenset[str]) -> int:
        """Nombre de compétences requises que cet agent possède (score de routage)."""
        return len(competences_requises & self.competences)


def _prompt_systeme(role: str, mission: str, garde_fous: str, methode: str = "") -> str:
    """Compose le prompt système d'exécution d'un agent depuis sa fiche (docs/04 §3).

    Forme commune : identité + mission, contrat d'entrée/sortie (une tâche → son
    `format_sortie`), **méthode** propre au métier, **régime sénior** commun (#293,
    `playbook_du_code.socle()`) et garde-fous propres au rôle.

    C'est la moitié « exécution texte » du régime — l'autre étant les playbooks des
    profils outillés. Elle prend le socle **sans** le cadre outillé : ici l'agent n'a ni
    outils ni espace de travail, sa réponse *est* le livrable. D'où la clause finale, qui
    remplace l'ancien « rends STRICTEMENT le livrable » : le livrable d'abord, puis les
    deux sections que le socle exige — sans elles, les arbitrages d'un agent texte se
    perdraient, alors que ce sont eux qui font la différence avec un exécutant.

    `methode` est la **part métier** du playbook (#296), condensée en une phrase : ce que
    le document Markdown du rôle déroule en étapes, l'agent texte doit l'avoir aussi, sans
    quoi les deux chemins d'exécution n'exécutent pas le même rôle. Optionnelle et vide par
    défaut : un rôle dont la part métier n'a pas encore été écrite garde exactement son
    prompt d'avant — c'est ce qui rend ce lot mergeable seul, à côté des lots frères.
    """
    bloc_methode = f"\n\nMéthode : {methode}" if methode else ""
    return f"""\
Tu es l'agent {role} de Maestro. {mission}

On te confie UNE tâche précise : titre, description, format de sortie attendu, et \
le cas échéant les résultats des tâches dont elle dépend. Réalise-la dans ton \
domaine de compétence et rends le livrable décrit par son « format de sortie ».\
{bloc_methode}

{socle()}

Garde-fous : {garde_fous}

Réponds directement par le livrable demandé, sans préambule ni méta-commentaire — \
puis, après lui, les deux sections « Décisions & arbitrages » et « Recommandations », \
brèves et sans remplissage."""


#: Les cinq agents exécutants par défaut (docs/04 §2). L'ordre fait foi pour départager
#: les ex æquo de routage (cf. `maestro.router.assign`) : les compétences étant deux à
#: deux disjointes ici, ce départage ne joue qu'en cas de tâche multi-domaine.
DEFAULT_AGENTS: tuple[Agent, ...] = (
    Agent(
        nom="developpeur",
        role="Développeur",
        competences=frozenset({"backend", "frontend", "api", "refactor"}),
        modele=MODELE_EXECUTANT_DEFAUT,
        prompt_systeme=_prompt_systeme(
            "Développeur",
            "Tu implémentes et modifies le code applicatif — backend, frontend, API, "
            "refactorisation.",
            "tu travailles sur une branche dédiée et ouvres une PR ; tu ne fusionnes "
            "pas sans validation ni QA.",
            "lis l'existant et ses conventions avant d'écrire, pose les options "
            "d'implémentation qui se présentent, tranche la plus simple qui tienne le "
            "besoin, avance par incréments cohérents, et livre avec les tests qui "
            "protègent ton choix et le traitement des cas d'erreur. L'architecture, les "
            "patrons et les bibliothèques sont à toi — choisis-les et dis pourquoi. Les "
            "dettes et les risques que tu constates sans pouvoir les traiter se "
            "signalent, chiffrés si tu le peux, plutôt que de se taire.",
        ),
    ),
    Agent(
        nom="bdd",
        role="Base de données",
        competences=frozenset({"sql", "schema", "migration", "data"}),
        modele=MODELE_EXECUTANT_DEFAUT,
        prompt_systeme=_prompt_systeme(
            "Base de données",
            "Tu conçois le schéma, écris les migrations et optimises les accès.",
            "toute opération destructive ou irréversible (DROP, TRUNCATE, suppression "
            "de colonne, perte de données) se décrit et se remonte pour validation "
            "humaine, elle ne se joue pas ; jamais de base réelle ni de production.",
            "modélise avant d'écrire du SQL — entités, relations, cardinalités, types et "
            "nullabilité —, vérifie d'abord l'intégrité (clés, unicité, contraintes de "
            "domaine, cascades) puis les accès (un index par requête réelle, et pas un de "
            "plus), et fais que chaque migration soit réversible, son retour arrière écrit "
            "à côté. Le modèle, l'indexation et les arbitrages de performance sont à toi — "
            "tranche-les et dis pourquoi.",
        ),
    ),
    Agent(
        nom="devops",
        role="DevOps",
        competences=frozenset({"ci-cd", "infra", "deploy", "docker"}),
        modele=MODELE_EXECUTANT_DEFAUT,
        prompt_systeme=_prompt_systeme(
            "DevOps",
            "Tu construis les pipelines CI/CD et l'infrastructure, et tu prépares les "
            "déploiements.",
            "aucun déploiement réel ni modification d'une infrastructure existante — le "
            "runbook et le plan de retour arrière sont le livrable, un humain les "
            "exécute ; respecte les plafonds de ressources.",
            "cadre d'abord l'environnement cible (plateforme, ressources, secrets, "
            "services voisins, existant), en écrivant tes hypothèses quand rien ne les "
            "donne ; écris l'infrastructure comme du code, avec des versions épinglées, "
            "rien qui dépende de l'état d'une machine et aucun secret en clair ; dis ce "
            "que tu validerais à blanc et comment ; puis prépare l'exécution, runbook "
            "étape par étape avec sa vérification et plan de retour arrière. L'outillage "
            "et la topologie sont à toi — tranche-les et dis pourquoi.",
        ),
    ),
    Agent(
        nom="designer",
        role="Designer",
        competences=frozenset({"ui", "ux", "design-system", "figma"}),
        modele=MODELE_EXECUTANT_DEFAUT,
        prompt_systeme=_prompt_systeme(
            "Designer",
            "Tu proposes écrans, maquettes et composants conformes à la charte.",
            "respecte le design system existant ; tu proposes, tu ne remplaces pas la "
            "charte sans accord.",
        ),
    ),
    Agent(
        nom="qa",
        role="QA / Testeur",
        competences=frozenset({"tests", "e2e", "review", "qa"}),
        modele=MODELE_EXECUTANT_DEFAUT,
        prompt_systeme=_prompt_systeme(
            "QA / Testeur",
            "Tu écris et exécutes les tests, valides les livrables et fais la revue.",
            "tu peux bloquer une tâche jugée non conforme et la renvoyer au "
            "Développeur.",
        ),
    ),
)


def agents_pour(modele: str | None) -> tuple[Agent, ...]:
    """Le catalogue par défaut, chaque agent basculé sur `modele` s'il est renseigné.

    C'est la moitié « exécutants » de la bascule par configuration (#69) :
    `MAESTRO_MODEL` impose un modèle unique à tous les rôles sans toucher au
    catalogue ni à la logique d'agent. `None` rend le catalogue tel quel.
    """
    if not modele:
        return DEFAULT_AGENTS
    return tuple(replace(agent, modele=modele) for agent in DEFAULT_AGENTS)

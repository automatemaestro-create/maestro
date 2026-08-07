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
            "Tu implémentes et modifies le code applicatif.",
            "tu travailles sur une branche dédiée et ouvres une PR ; tu ne fusionnes "
            "pas sans validation ni QA.",
        ),
    ),
    Agent(
        nom="bdd",
        role="Base de données",
        competences=frozenset({"sql", "schema", "migration", "data"}),
        modele=MODELE_EXECUTANT_DEFAUT,
        prompt_systeme=_prompt_systeme(
            "Base de données",
            "Tu conçois le schéma, écris les migrations et optimises les requêtes.",
            "toute migration destructive (drop, perte de données) requiert une "
            "validation humaine ; jamais directement en production.",
        ),
    ),
    Agent(
        nom="devops",
        role="DevOps",
        competences=frozenset({"ci-cd", "infra", "deploy", "docker"}),
        modele=MODELE_EXECUTANT_DEFAUT,
        prompt_systeme=_prompt_systeme(
            "DevOps",
            "Tu construis les pipelines CI/CD, l'infrastructure et les déploiements.",
            "tout déploiement (surtout en production) passe par une validation "
            "humaine ; respecte les plafonds de ressources.",
        ),
    ),
    Agent(
        nom="designer",
        role="Designer",
        competences=frozenset({"ui", "ux", "design-system", "figma"}),
        modele=MODELE_EXECUTANT_DEFAUT,
        prompt_systeme=_prompt_systeme(
            "Designer",
            "Tu conçois écrans, parcours, composants et design tokens conformes à la "
            "charte.",
            "respecte le design system existant ; tu proposes, tu ne remplaces pas la "
            "charte sans accord.",
            "cadre le besoin et les parcours avant de dessiner ; pose les états et les "
            "cas limites de chaque écran — vide, chargement, erreur, droits "
            "insuffisants, données qui débordent — avant le cas nominal ; produis "
            "écrans, tokens et composants, toute valeur récurrente devenant un token "
            "nommé et tout motif récurrent un composant ; puis vérifie l'accessibilité "
            "en la chiffrant (contrastes 4,5:1 et 3:1, parcours clavier, focus visible, "
            "libellés et alternatives textuelles) et la cohérence d'un écran à l'autre. "
            "La structure, les patrons d'interaction et la nomenclature des tokens sont "
            "à toi — tranche-les et dis pourquoi. Charte absente ou livrable amont "
            "incomplet : pose l'hypothèse la plus raisonnable, énonce-la comme une "
            "proposition, et continue plutôt que de rendre vide.",
        ),
    ),
    Agent(
        nom="qa",
        role="QA / Testeur",
        competences=frozenset({"tests", "e2e", "review", "qa"}),
        modele=MODELE_EXECUTANT_DEFAUT,
        prompt_systeme=_prompt_systeme(
            "QA / Testeur",
            "Tu analyses le risque, écris et exécutes les tests, fais la revue des "
            "livrables et rends un verdict priorisé.",
            "tu évalues, tu ne réécris pas le livrable d'un autre rôle — un défaut se "
            "signale avec la correction que tu proposes, l'appliquer revient au rôle "
            "producteur ; ton verdict éclaire une décision humaine, sans rétro-boucle "
            "automatique, donc ce que tu n'écris pas est perdu.",
            "pars du risque — ce qui casse le plus probablement, et ce qui coûte le "
            "plus cher si ça casse ; retiens pour chacun le niveau de test le moins "
            "cher qui l'attrape vraiment (unitaire, intégration, bout en bout) et écris "
            "aussi ce que tu laisses délibérément de côté ; exécute pour de vrai et "
            "consigne les résultats réels, jamais supposés ; puis hiérarchise chaque "
            "défaut — **bloquant** (le livrable ne remplit pas son objet : format de "
            "sortie non rendu, chemin nominal en échec, perte de données, faille, "
            "régression), **majeur** (un cas réel casse ou un attendu explicite "
            "manque), **mineur** (rien ne casse) — avec sa preuve et la correction que "
            "tu proposes. Le verdict en découle et n'est pas binaire : non conforme "
            "s'il reste un bloquant, conforme sous réserve s'il reste un majeur, "
            "conforme sinon. Livrable amont incomplet ou critères absents : traite le "
            "manque comme un constat à part entière avec sa sévérité, écris le "
            "référentiel que tu retiens, et rends un verdict plutôt que d'attendre.",
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

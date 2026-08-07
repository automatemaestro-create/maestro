"""Profil du rôle DevOps — le paramétrage de son runtime outillé (ticket #67).

Quatrième rôle outillé après le Développeur, la Base de données et le QA (#45) : là où
le catalogue (`maestro.agents.catalog`) décrit le DevOps comme une *identité*
(compétences `ci-cd`/`infra`/`deploy`/`docker`), ce module déclare le **profil** de son
runtime outillé : le prompt système, les outils et les consignes avec lesquels le
runtime générique (`maestro.agents.runtime.AgentRuntime`) traite une tâche
d'infrastructure *de bout en bout* (pipeline CI/CD, conteneurisation, préparation de
déploiement) dans un espace de travail isolé.

Particularité du DevOps (docs/04 §3.4, playbook `agents/devops/README.md`) : **tout
déploiement passe par une validation humaine**. Au POC, l'agent ne touche à aucun
environnement réel : un « déploiement » se matérialise en fichiers (configuration,
scripts, runbook) et son compte-rendu liste explicitement ce qui reste soumis à
validation humaine avant toute application réelle.
"""

from __future__ import annotations

from maestro.agents.playbook_du_code import CONSIGNE_RENDU_COMPTE, playbook_du_code
from maestro.agents.runtime import DEFAULT_TOOLS, RoleProfile
from maestro.providers.base import PLAFOND_TOURS_DEFAUT

#: Prompt système du *runtime* DevOps : son playbook « du code » (#295), le document
#: `playbooks_defaut/devops.md` — régime sénior commun compris, et la particularité du
#: rôle : jamais de déploiement réel, la validation humaine est explicite. Depuis #296
#: il porte la part métier du spécialiste : méthode (cadrer l'environnement cible →
#: écrire l'infrastructure comme du code → valider à blanc → préparer l'exécution et son
#: retour arrière), latitude nommée sur l'outillage et la topologie — le garde-fou
#: restant entier, runbook et plan de retour arrière *étant* le livrable.
_SYSTEM_PROMPT = playbook_du_code("devops")

#: Profil du DevOps : modèle par défaut du POC (Claude Sonnet, cf. docs/04 §2), outils
#: fichiers + shell (docs/02 §7 : permissions scopées), consignes de validation locale
#: et de déploiement soumis à validation humaine. `nom` correspond à l'agent `devops`
#: du catalogue.
DEVOPS_PROFILE = RoleProfile(
    nom="devops",
    role="DevOps",
    modele="claude-sonnet-5",
    outils=DEFAULT_TOOLS,
    prompt_systeme=_SYSTEM_PROMPT,
    intro_tache="Tâche d'infrastructure (CI/CD, déploiement) à réaliser de bout en bout :",
    consignes=(
        "Tu travailles dans un répertoire vide et isolé (le répertoire courant). "
        "Écris-y les fichiers du livrable avec tes outils (configuration de pipeline, "
        "Dockerfile, scripts, runbook) — ne te contente pas d'afficher des "
        "recommandations. Cadre d'abord l'environnement cible, en écrivant tes "
        "hypothèses quand rien ne les donne. Valide localement ce qui peut l'être "
        "(syntaxe, construction, exécution à blanc) et consigne les résultats réels ; "
        "ne déploie rien vers un environnement réel. Tranche seul l'outillage et la "
        "topologie ; l'exécution réelle se prépare — runbook étape par étape et plan de "
        "retour arrière sont le livrable — et se remonte, elle ne se fait pas."
    ),
    consigne_finale=(
        "Quand c'est fait, résume ce que tu as produit et liste explicitement ce qui "
        "requiert une validation humaine avant toute application réelle (déploiement, "
        f"modification d'infrastructure), {CONSIGNE_RENDU_COMPTE}"
    ),
    workspace_prefix="maestro-devops-",
    # Borne conservatrice (#239) : pipelines et runbooks se valident à blanc, sans
    # la boucle rendre/regarder/reprendre du design. Un plafond atteint ici a
    # signalé un emballement réel (run D de la démo v1, docs/13) — pas une borne trop courte.
    # Revérifié à #296 : la méthode de spécialiste n'ajoute pas d'étape, elle **nomme**
    # ce qui était déjà attendu (cadrage, validation à blanc, runbook, retour arrière).
    # Relever le plafond affaiblirait le seul garde-fou anti-emballement qui ait déjà
    # servi, pour un besoin que rien ne mesure — conservé.
    plafond_tours=PLAFOND_TOURS_DEFAUT,
)

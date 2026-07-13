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

from maestro.agents.runtime import DEFAULT_TOOLS, RoleProfile

#: Prompt système du *runtime* DevOps : il doit matérialiser un livrable en fichiers
#: (configuration de pipeline, Dockerfile, scripts, runbook) dans son répertoire de
#: travail, valider localement ce qui peut l'être (shell), et porter la particularité
#: du rôle : jamais de déploiement réel, la validation humaine est explicite.
_SYSTEM_PROMPT = """\
Tu es l'agent DevOps de Maestro. Tu traites une tâche d'infrastructure de bout en \
bout : pipelines CI/CD, conteneurisation, provisionnement, préparation de \
déploiement — et tu produis un livrable réellement exploitable.

Tu disposes d'outils (lecture, écriture et édition de fichiers, exploration, shell) et \
d'un répertoire de travail vide et isolé (ton répertoire courant). Matérialise TON \
livrable en fichiers dans ce répertoire (configuration de pipeline, Dockerfile, \
scripts, runbook de déploiement) — n'affiche pas seulement des recommandations. Quand \
la tâche s'y prête, valide localement avec le shell (syntaxe, exécution à blanc) et \
consigne les résultats réels.

Garde-fous : reste dans ton répertoire de travail. Tu ne déploies JAMAIS vers un \
environnement réel et ne modifies aucune infrastructure existante : tout déploiement \
passe par une validation humaine — prépare-le en fichiers (configuration, scripts, \
runbook) et signale explicitement ce qui requiert cette validation. Respecte les \
plafonds de ressources : aucun processus persistant ni service à l'écoute ne doit \
survivre à la tâche."""

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
        "recommandations. Valide localement ce qui peut l'être (syntaxe, exécution à "
        "blanc) et consigne les résultats réels ; ne déploie rien vers un "
        "environnement réel."
    ),
    consigne_finale=(
        "Quand c'est fait, résume ce que tu as produit et liste explicitement ce qui "
        "requiert une validation humaine avant toute application réelle (déploiement, "
        "modification d'infrastructure)."
    ),
    workspace_prefix="maestro-devops-",
)

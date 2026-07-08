"""Profil du rôle Développeur — le paramétrage de son runtime outillé (tickets #4, #35).

Là où le catalogue (`maestro.agents.catalog`) décrit le Développeur comme une *identité*
(compétences, modèle, prompt système d'exécution texte), ce module déclare le **profil**
de son runtime outillé : le prompt système, les outils et les consignes avec lesquels le
runtime générique (`maestro.agents.runtime.AgentRuntime`) exécute une tâche de
développement *de bout en bout* — comprendre, écrire du code, produire des fichiers —
dans un espace de travail isolé.

Historique : le ticket #4 avait donné au Développeur un runtime dédié (`DeveloperAgent`) ;
le ticket #35 l'a factorisé avec celui du BDD (#5) en un runtime générique — ne reste ici
que ce qui est propre au rôle.
"""

from __future__ import annotations

from maestro.agents.runtime import DEFAULT_TOOLS, RoleProfile

#: Prompt système du *runtime* Développeur : il doit matérialiser un livrable en
#: fichiers dans son répertoire de travail, pas se contenter d'afficher du code.
_SYSTEM_PROMPT = """\
Tu es l'agent Développeur de Maestro. Tu implémentes du code applicatif de bout en \
bout : tu comprends la tâche, tu écris les fichiers nécessaires et tu produis un \
livrable réellement exploitable.

Tu disposes d'outils (lecture, écriture et édition de fichiers, exploration, shell) et \
d'un répertoire de travail vide et isolé (ton répertoire courant). Matérialise TON \
livrable en fichiers dans ce répertoire — n'affiche pas seulement du code. Garde le \
résultat minimal, cohérent et exécutable.

Garde-fous : reste dans ton répertoire de travail ; n'entreprends aucune action \
destructrice hors de cet espace et ne fusionne rien. Termine par un bref compte-rendu \
de ce que tu as produit et de la manière de l'utiliser."""

#: Profil du Développeur : modèle par défaut du POC (Claude Sonnet, cf. docs/04 §2),
#: outils fichiers + shell (docs/02 §7 : permissions scopées), consignes de
#: matérialisation du livrable. `nom` correspond à l'agent `developpeur` du catalogue.
DEVELOPER_PROFILE = RoleProfile(
    nom="developpeur",
    role="Développeur",
    modele="claude-sonnet-5",
    outils=DEFAULT_TOOLS,
    prompt_systeme=_SYSTEM_PROMPT,
    intro_tache="Tâche de développement à réaliser de bout en bout :",
    consignes=(
        "Tu travailles dans un répertoire vide et isolé (le répertoire courant). "
        "Écris-y les fichiers du livrable avec tes outils — ne te contente pas "
        "d'afficher du code. Vise un résultat minimal mais réellement exploitable."
    ),
    consigne_finale=(
        "Quand c'est fait, résume en quelques lignes ce que tu as produit et comment "
        "l'utiliser."
    ),
    workspace_prefix="maestro-dev-",
)

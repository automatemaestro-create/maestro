"""Profil du rôle QA / Testeur — le paramétrage de son runtime outillé (ticket #45).

Troisième rôle outillé après le Développeur (`maestro.agents.developer`) et la Base de
données (`maestro.agents.database`) : là où le catalogue (`maestro.agents.catalog`)
décrit le QA comme une *identité* (compétences `tests`/`e2e`/`review`/`qa`), ce module
déclare le **profil** de son runtime outillé : le prompt système, les outils et les
consignes avec lesquels le runtime générique (`maestro.agents.runtime.AgentRuntime`)
traite une tâche de qualité *de bout en bout* (écrire et exécuter des tests, valider un
livrable, faire la revue) dans un espace de travail isolé.

Particularité du QA (docs/04 §3.6, playbook `agents/qa/README.md`) : il **évalue** les
livrables des tâches dont il dépend (le tableau noir), il ne les réécrit pas — et son
compte-rendu rend un **verdict explicite** (« conforme » / « non conforme »). Au POC,
« bloquer et renvoyer au Développeur » se matérialise par ce verdict étayé : la boucle
n'a pas encore de rétro-boucle automatique, le verdict éclaire la décision humaine.
"""

from __future__ import annotations

from maestro.agents.runtime import DEFAULT_TOOLS, RoleProfile

#: Prompt système du *runtime* QA : il doit matérialiser un livrable en fichiers
#: (suite de tests, rapport de revue) dans son répertoire de travail, exécuter ce qui
#: peut l'être (shell), et porter la particularité du rôle : un verdict explicite,
#: sans corriger lui-même le livrable évalué.
_SYSTEM_PROMPT = """\
Tu es l'agent QA / Testeur de Maestro. Tu traites une tâche de qualité de bout en \
bout : tu écris et exécutes les tests, tu valides les livrables qu'on te confie et tu \
en fais la revue, et tu produis un livrable réellement exploitable.

Tu disposes d'outils (lecture, écriture et édition de fichiers, exploration, shell) et \
d'un répertoire de travail vide et isolé (ton répertoire courant). Matérialise TON \
livrable en fichiers dans ce répertoire (suite de tests, rapport de revue) — n'affiche \
pas seulement des remarques. Quand la tâche s'y prête, exécute les tests avec le shell \
et consigne leurs résultats réels.

Garde-fous : reste dans ton répertoire de travail. Tu évalues les livrables qu'on te \
transmet, tu ne les réécris pas : un défaut se signale, sa correction revient au rôle \
qui l'a produit (ex. le Développeur). Termine par un compte-rendu qui rend un VERDICT \
EXPLICITE — « conforme » ou « non conforme » — étayé par tes constats ; en cas de \
non-conformité, liste précisément ce qui bloque et doit être renvoyé au rôle \
producteur."""

#: Profil du QA : modèle par défaut du POC (Claude Sonnet, cf. docs/04 §2), outils
#: fichiers + shell (docs/02 §7 : permissions scopées), consignes de revue sur tableau
#: noir et de verdict explicite. `nom` correspond à l'agent `qa` du catalogue.
QA_PROFILE = RoleProfile(
    nom="qa",
    role="QA / Testeur",
    modele="claude-sonnet-5",
    outils=DEFAULT_TOOLS,
    prompt_systeme=_SYSTEM_PROMPT,
    intro_tache="Tâche de qualité (tests, validation, revue) à réaliser de bout en bout :",
    consignes=(
        "Tu travailles dans un répertoire vide et isolé (le répertoire courant). "
        "Écris-y les fichiers du livrable avec tes outils (tests, rapport de revue) — "
        "ne te contente pas d'afficher des remarques. Les livrables à valider sont "
        "dans la description ci-dessus (résultats des tâches dont celle-ci dépend) : "
        "appuie ta revue dessus, et exécute les tests quand c'est possible."
    ),
    consigne_finale=(
        "Quand c'est fait, résume ce que tu as produit et rends un verdict explicite "
        "(conforme / non conforme) ; en cas de non-conformité, liste ce qui bloque et "
        "doit être renvoyé au rôle producteur."
    ),
    workspace_prefix="maestro-qa-",
)

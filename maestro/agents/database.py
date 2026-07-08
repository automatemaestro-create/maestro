"""Profil du rôle Base de données — le paramétrage de son runtime outillé (tickets #5, #35).

Pendant du Développeur (`maestro.agents.developer`) pour le domaine **base de données** :
là où le catalogue (`maestro.agents.catalog`) décrit le BDD comme une *identité*
(compétences `sql`/`schema`/`migration`/`data`), ce module déclare le **profil** de son
runtime outillé : le prompt système, les outils et les consignes avec lesquels le runtime
générique (`maestro.agents.runtime.AgentRuntime`) traite une tâche BDD *de bout en bout*
(concevoir un schéma, écrire des migrations, optimiser des requêtes) dans un espace de
travail isolé.

Garde-fou propre au BDD (docs/04 §2, playbook du catalogue) : une migration **destructive**
(DROP, perte de données) doit être clairement signalée et laissée à une **validation
humaine** ; l'agent ne cible **jamais** une base réelle ou de production — toute validation
se fait contre une base jetable créée dans l'espace de travail.

Historique : le ticket #5 avait donné au BDD un runtime dédié (`DatabaseAgent`) ; le
ticket #35 l'a factorisé avec celui du Développeur (#4) en un runtime générique — ne
reste ici que ce qui est propre au rôle.
"""

from __future__ import annotations

from maestro.agents.runtime import DEFAULT_TOOLS, RoleProfile

#: Prompt système du *runtime* BDD : il doit matérialiser un livrable en fichiers
#: (schéma, migrations, requêtes) dans son répertoire de travail, et porter le
#: garde-fou anti-migration-destructive du rôle. Le shell sert à *valider* le livrable
#: contre une base jetable (ex. SQLite dans l'espace de travail), jamais une base réelle.
_SYSTEM_PROMPT = """\
Tu es l'agent Base de données de Maestro. Tu traites une tâche de base de données de \
bout en bout : tu conçois le schéma, tu écris les migrations et tu optimises les \
requêtes, et tu produis un livrable réellement exploitable.

Tu disposes d'outils (lecture, écriture et édition de fichiers, exploration, shell) et \
d'un répertoire de travail vide et isolé (ton répertoire courant). Matérialise TON \
livrable en fichiers dans ce répertoire (schéma SQL, fichiers de migration, requêtes) — \
n'affiche pas seulement du SQL. Garde le résultat minimal, cohérent et applicable.

Garde-fous : reste dans ton répertoire de travail. Ne te connecte JAMAIS à une base \
réelle ou de production ; si tu veux vérifier ton schéma ou tes migrations, fais-le \
uniquement contre une base jetable que tu crées dans ce répertoire (ex. un fichier \
SQLite local). Toute opération destructive (DROP, TRUNCATE, suppression de colonne, \
perte de données) doit être clairement signalée dans ton compte-rendu comme nécessitant \
une validation humaine — tu la proposes, tu ne la réputes jamais appliquée en \
production. Termine par un bref compte-rendu de ce que tu as produit, de la manière de \
l'appliquer et des points qui requièrent une validation."""

#: Profil du BDD : modèle par défaut du POC (Claude Sonnet, cf. docs/04 §2), outils
#: fichiers + shell (docs/02 §7 : permissions scopées), consignes anti-base-réelle.
#: `nom` correspond à l'agent `bdd` du catalogue.
DATABASE_PROFILE = RoleProfile(
    nom="bdd",
    role="Base de données",
    modele="claude-sonnet-5",
    outils=DEFAULT_TOOLS,
    prompt_systeme=_SYSTEM_PROMPT,
    intro_tache="Tâche de base de données à réaliser de bout en bout :",
    consignes=(
        "Tu travailles dans un répertoire vide et isolé (le répertoire courant). "
        "Écris-y les fichiers du livrable avec tes outils (schéma, migrations, "
        "requêtes) — ne te contente pas d'afficher du SQL. Vise un résultat minimal "
        "mais réellement applicable. Ne cible aucune base réelle : toute vérification "
        "se fait contre une base jetable créée dans ce répertoire."
    ),
    consigne_finale=(
        "Quand c'est fait, résume en quelques lignes ce que tu as produit, comment "
        "l'appliquer, et signale toute opération destructive à valider par un humain."
    ),
    workspace_prefix="maestro-bdd-",
)

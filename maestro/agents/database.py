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

from maestro.agents.playbook_du_code import CONSIGNE_RENDU_COMPTE, playbook_du_code
from maestro.agents.runtime import DEFAULT_TOOLS, RoleProfile
from maestro.providers.base import PLAFOND_TOURS_DEFAUT

#: Prompt système du *runtime* BDD : son playbook « du code » (#295), le document
#: `playbooks_defaut/bdd.md` — régime sénior commun compris, et le garde-fou
#: anti-migration-destructive du rôle. Le shell y sert à *valider* le livrable contre une
#: base jetable (ex. SQLite dans l'espace de travail), jamais une base réelle. Depuis #296
#: il porte la part métier du spécialiste : méthode (modéliser → vérifier l'intégrité puis
#: les accès → migrer de façon réversible), latitude nommée sur le modèle, l'indexation et
#: les arbitrages de performance — le garde-fou restant entier, l'autonomie ne portant que
#: sur ce qui est réversible.
_SYSTEM_PROMPT = playbook_du_code("bdd")

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
        "se fait contre une base jetable créée dans ce répertoire — applique le schéma, "
        "joue les migrations puis leur retour arrière, éprouve les contraintes. Tranche "
        "seul le modèle, l'indexation et les arbitrages de performance ; ce qui est "
        "destructif ou irréversible se décrit et se remonte, jamais ne s'applique."
    ),
    consigne_finale=(
        "Quand c'est fait, résume en quelques lignes ce que tu as produit, comment "
        "l'appliquer et toute opération destructive à valider par un humain, "
        f"{CONSIGNE_RENDU_COMPTE}"
    ),
    workspace_prefix="maestro-bdd-",
    # Borne conservatrice (#239) : schémas et migrations tiennent en une douzaine
    # de tours à ~13 000 tokens (mesuré sur `schema-depenses`) — le défaut suffit.
    # Revérifié à #296 : l'épreuve sur base jetable est plus fournie (migrations *puis*
    # retour arrière, insertions qui doivent être refusées, plan d'exécution), mais
    # chaque vérification est un tour court. On reste autour d'une vingtaine, soit la
    # moitié du plafond — conservé.
    plafond_tours=PLAFOND_TOURS_DEFAUT,
)

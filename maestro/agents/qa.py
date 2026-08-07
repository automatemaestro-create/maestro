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

from maestro.agents.playbook_du_code import CONSIGNE_RENDU_COMPTE, playbook_du_code
from maestro.agents.runtime import DEFAULT_TOOLS, RoleProfile
from maestro.providers.base import PLAFOND_TOURS_DEFAUT

#: Prompt système du *runtime* QA : son playbook « du code » (#295), le document
#: `playbooks_defaut/qa.md` — régime sénior commun compris, et la particularité du rôle :
#: un verdict explicite, sans corriger lui-même le livrable évalué. Depuis #297 il porte
#: la part métier du spécialiste : méthode (analyser le risque → choisir la stratégie et
#: le niveau de test → écrire et exécuter → hiérarchiser les défauts), échelle de
#: sévérité **bloquant / majeur / mineur** et verdict qui en découle — le binaire
#: « conforme / non conforme » d'avant mettait trois virgules et une perte de données
#: dans la même case, alors qu'ils n'appellent pas la même décision.
_SYSTEM_PROMPT = playbook_du_code("qa")

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
        "appuie ta revue dessus, et exécute les tests quand c'est possible en "
        "consignant les résultats réels, jamais supposés. Choisis seul ta stratégie et "
        "ton niveau de test, en partant de ce qui risque le plus de casser et de ce "
        "qui coûte le plus cher si ça casse ; écris aussi ce que tu laisses de côté. "
        "Chaque défaut sort avec sa sévérité — bloquant, majeur ou mineur —, sa preuve "
        "et la correction que tu proposes sans l'appliquer. Si le livrable amont est "
        "incomplet, teste ce qui est là et traite le manque comme un constat à part "
        "entière, plutôt que d'attendre."
    ),
    consigne_finale=(
        "Quand c'est fait, résume ce que tu as produit et rends un verdict explicite "
        "qui découle de ta sévérité la plus haute (non conforme s'il reste un "
        "bloquant, conforme sous réserve s'il reste un majeur, conforme sinon), suivi "
        "de tes défauts par sévérité décroissante ; liste ce qui bloque et doit être "
        f"renvoyé au rôle producteur, {CONSIGNE_RENDU_COMPTE}"
    ),
    workspace_prefix="maestro-qa-",
    # Borne conservatrice (#239) : rôle de lecture et d'analyse (relire les
    # livrables amont, exécuter des tests, rendre un verdict) — c'est lui que le
    # relevé global du plafond protégeait le moins bien, il garde le défaut.
    # Revérifié à #297 : ce que la méthode de spécialiste ajoute — analyse de risque
    # en amont, hiérarchisation des défauts en aval — se joue dans le raisonnement,
    # pas en appels d'outil ; le cycle coûteux (écrire les tests, les lancer, lire les
    # résultats) était déjà là. Plafond conservé, sans relèvement à l'aveugle.
    plafond_tours=PLAFOND_TOURS_DEFAUT,
)

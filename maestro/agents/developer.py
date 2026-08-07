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

from maestro.agents.playbook_du_code import CONSIGNE_RENDU_COMPTE, playbook_du_code
from maestro.agents.runtime import DEFAULT_TOOLS, RoleProfile
from maestro.providers.base import PLAFOND_TOURS_DEFAUT

#: Prompt système du *runtime* Développeur : son playbook « du code » (#295), le
#: document `playbooks_defaut/developpeur.md` — régime sénior commun compris. C'est
#: le même texte que `PLAYBOOK_DEFAUTS["developpeur"]`, par construction. Depuis #296
#: il porte la part métier du spécialiste : méthode (lire l'existant → poser les
#: options → trancher → incrémenter → tester), latitude nommée sur l'architecture, les
#: patrons et les bibliothèques, exigences de tests et de gestion d'erreur, remontée
#: des dettes et des risques constatés.
_SYSTEM_PROMPT = playbook_du_code("developpeur")

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
        "d'afficher du code. Vise un résultat minimal mais réellement exploitable. "
        "Tranche seul l'architecture, les patrons et les bibliothèques ; si une entrée "
        "manque, pose l'hypothèse la plus raisonnable et signale-la plutôt que de "
        "t'arrêter. Écris les tests qui protègent ton choix, lance-les pour de vrai et "
        "consigne leurs résultats ; traite les cas d'erreur au lieu de les supposer "
        "absents."
    ),
    consigne_finale=(
        "Quand c'est fait, résume en quelques lignes ce que tu as produit et comment "
        f"l'utiliser, {CONSIGNE_RENDU_COMPTE}"
    ),
    workspace_prefix="maestro-dev-",
    # Borne conservatrice (#239) : ses tours sont parmi les plus légers (~10 000
    # tokens le tour, mesuré sur `validation-depenses`, 5 tours) — 40 laisse une
    # marge large sans qu'un emballement coûte cher.
    # Revérifié à #296 : la méthode de spécialiste ajoute un cycle « écrire les tests →
    # les lancer → corriger », soit une poignée de tours sur une tâche de cette taille
    # (ordre de grandeur : 5 mesurés → une quinzaine). La marge reste d'un facteur 2 et
    # plus — plafond conservé, sans relèvement à l'aveugle.
    plafond_tours=PLAFOND_TOURS_DEFAUT,
)

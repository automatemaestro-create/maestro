"""Le runtime outillé d'un agent, dérivé de sa **fiche** (ticket #1037).

Jusqu'ici l'outillage était le privilège de cinq noms : `default_runtimes` construisait
un `AgentRuntime` par profil déclaré dans le code (`TOOLED_PROFILES`), l'exécuteur
cherchait le sien dans cette table, et un agent **défini par sa fiche** — créé depuis la
Control Tower (#72), ou demain proposé par l'analyse d'un projet (#1021) — n'y figurait
pas. Il ne travaillait donc qu'en **texte** : ni fichiers, ni écriture dans le projet,
ni commandes. Une équipe dérivée d'un projet ne pouvait pas exister sous ce verrou.

Ce module renverse le sens de la construction : le profil outillé se **dérive de la
fiche** de l'agent (`maestro.agents.catalog.Agent` — celle que `catalogue()` assemble,
agents du code, surcharges de réglages (#259) et définitions persistées comprises), et
les cinq rôles du code passent par **ce même chemin**. Il n'y a plus deux runtimes.

Ce que la dérivation prend, et où :

- le **métier** (nom, rôle, modèle, effort) vient de la fiche, sans détour — d'où un
  effet de bord voulu : une surcharge de modèle posée sur un agent du code (#259)
  atteint désormais aussi son exécution outillée, là où elle ne recouvrait que le
  chemin texte ;
- le **playbook** vient de la même source que pour le chemin texte — le document du
  paquet quand le rôle en a un (`maestro.agents.playbook_du_code`), le champ
  `playbook` de la fiche sinon. L'édition à chaud du dépôt versionné (#78) le recouvre
  ensuite, exécution par exécution, exactement comme avant ;
- le **cadre outillé** — ce que le message de tâche dit du travail à rendre — est celui
  que le rôle du code déclare quand il en déclare un (`CADRES_DU_CODE`), et sinon le
  cadre **générique** : les mêmes outils, le même espace de travail, la même exigence
  de matérialiser le livrable en fichiers, dits sans métier.

⚠ **Le playbook d'une fiche reçoit le cadre d'exécution en plus** (`cadre_outille()`, le
fragment que les documents des rôles du code appellent par `{{cadre}}`). Sans lui, un
agent outillé pour la première fois garderait un playbook qui ne lui parle ni de son
répertoire de travail ni de son livrable en fichiers : il répondrait en texte avec des
outils dans les mains, et sa tâche rendrait un livrable **vide** — moins que le chemin
texte qu'on vient de lui retirer. Un playbook **édité** (#78) remplace le prompt entier,
cadre compris, et c'est le contrat de #78 : l'API n'ouvre l'édition qu'aux rôles du
code (`PLAYBOOK_DEFAUTS`), dont le document porte déjà ce cadre développé.

Les **autorisations** (#110) et les **serveurs MCP** (#104) ne demandent rien ici : les
deux dépôts sont indexés par nom d'agent et relus à chaud par l'exécuteur, qui les passe
à `AgentRuntime.execute`. Ils s'appliquaient déjà à n'importe quel nom — ils n'avaient
simplement jamais de runtime à équiper.
"""

from __future__ import annotations

from dataclasses import replace

from maestro.agents.catalog import MODELE_EXECUTANT_DEFAUT, Agent, gabarits_pour
from maestro.agents.database import DATABASE_PROFILE
from maestro.agents.designer import DESIGNER_PROFILE
from maestro.agents.developer import DEVELOPER_PROFILE
from maestro.agents.devops import DEVOPS_PROFILE
from maestro.agents.playbook_du_code import (
    CONSIGNE_RENDU_COMPTE,
    cadre_outille,
    playbook_du_code,
    roles_du_code,
)
from maestro.agents.playbooks import PlaybookStore
from maestro.agents.qa import QA_PROFILE
from maestro.agents.runtime import DEFAULT_TOOLS, AgentRuntime, RoleProfile
from maestro.providers.base import ModelProvider

#: Les profils outillés **déclarés par le code**, dans l'ordre du catalogue. Depuis #1037
#: ce ne sont plus des runtimes à part : ce sont les **cadres** que le paquet livre pour
#: ces cinq rôles — le pendant, côté message de tâche, du document que
#: `playbook_du_code` livre côté prompt système. Y inscrire un rôle lui donne un cadre
#: propre ; ne pas y figurer ne retire plus rien, le cadre générique outille tout autant.
TOOLED_PROFILES: tuple[RoleProfile, ...] = (
    DEVELOPER_PROFILE,
    DATABASE_PROFILE,
    DEVOPS_PROFILE,
    DESIGNER_PROFILE,
    QA_PROFILE,
)

#: Les mêmes, indexés par nom d'agent du catalogue — la table que la dérivation consulte
#: pour savoir si le paquet déclare un cadre propre au rôle de cette fiche.
CADRES_DU_CODE: dict[str, RoleProfile] = {profil.nom: profil for profil in TOOLED_PROFILES}

#: Le cadre outillé **générique** : ce qu'on dit à un agent dont le code ne déclare pas
#: de cadre propre. Il ne dit rien du métier — c'est le playbook de la fiche qui le porte
#: — et tout de l'exécution : le répertoire courant, le livrable matérialisé en fichiers,
#: les arbitrages tranchés plutôt que remontés, les deux sections de rendu de compte.
#: C'est le dénominateur commun des cinq cadres du code, leurs consignes de métier
#: retirées.
#:
#: Les quatre champs d'identité (`nom`, `role`, `prompt_systeme`, `modele`) n'y sont que
#: des valeurs de remplissage : `profil_outille` les prend tous sur la fiche. De même
#: `workspace_prefix`, remplacé par un préfixe dérivé du nom de l'agent — un gabarit ne
#: s'exécute pas, il se recouvre.
CADRE_GENERIQUE = RoleProfile(
    nom="",
    role="",
    modele=MODELE_EXECUTANT_DEFAUT,
    outils=DEFAULT_TOOLS,
    prompt_systeme="",
    intro_tache="Tâche à réaliser de bout en bout :",
    consignes=(
        "Tu travailles dans le répertoire courant, avec tes outils. Écris-y les fichiers "
        "du livrable — ne te contente pas de les décrire : ce qui n'existe que dans ta "
        "réponse n'existe pas. Vise un résultat minimal mais réellement exploitable. "
        "Tranche seul ce qui relève de ton domaine ; si une entrée manque, pose "
        "l'hypothèse la plus raisonnable et signale-la plutôt que de t'arrêter. Les "
        "livrables dont ta tâche dépend sont dans la description ci-dessus : appuie-toi "
        "dessus au lieu de les redemander."
    ),
    consigne_finale=(
        "Quand c'est fait, résume en quelques lignes ce que tu as produit et comment "
        f"l'utiliser, {CONSIGNE_RENDU_COMPTE}"
    ),
    workspace_prefix="maestro-agent-",
)


def profil_outille(agent: Agent) -> RoleProfile:
    """Le profil du runtime outillé de `agent`, dérivé de sa fiche (#1037).

    Le cadre vient du code quand il en déclare un pour ce nom, du gabarit générique
    sinon ; tout le reste vient de la fiche. Pour les cinq rôles du code, le profil
    rendu est celui qu'ils déclarent — leur comportement ne bouge pas, c'est le chemin
    qui a changé.
    """
    cadre = CADRES_DU_CODE.get(agent.nom)
    if cadre is None:
        # Un préfixe par agent, pas un préfixe commun : c'est lui qui rend un espace de
        # travail attribuable à l'œil nu, et le ramassage (#197) reconnaît de toute
        # façon n'importe quel préfixe par son marqueur de pid.
        cadre = replace(CADRE_GENERIQUE, workspace_prefix=f"maestro-{agent.nom}-")
    return replace(
        cadre,
        nom=agent.nom,
        role=agent.role,
        modele=agent.modele,
        effort=agent.effort,
        prompt_systeme=playbook_outille(agent),
    )


def playbook_outille(agent: Agent) -> str:
    """Le playbook servi en prompt système au runtime outillé de `agent`.

    Le document du paquet quand le rôle en a un — il porte déjà son cadre d'exécution
    par son `{{cadre}}` —, sinon le `playbook` de la fiche **suivi de ce cadre**. Cf. la
    mise en garde du module : un playbook de fiche ne parle pas d'outils, et un agent
    outillé qui l'ignore rend un livrable vide.
    """
    if agent.nom in roles_du_code():
        return playbook_du_code(agent.nom)
    return f"{agent.prompt_systeme.rstrip()}\n\n{cadre_outille()}\n"


def runtime_outille(
    provider: ModelProvider,
    agent: Agent,
    *,
    playbooks: PlaybookStore | None = None,
) -> AgentRuntime:
    """Le runtime outillé de `agent` sur `provider`, construit depuis sa fiche (#1037).

    Le point de construction unique : l'exécuteur l'appelle à chaque tâche (la fiche est
    relue à chaud, comme les playbooks, les serveurs MCP et les politiques), et
    `default_runtimes` s'en sert pour figer un instantané des rôles du code.

    `playbooks` (#76) **fige** le prompt système sur la version courante du dépôt au
    moment du câblage — un instantané, pour les usages une-fois. L'application à chaud
    (#78) passe, elle, par `LocalExecutor(playbooks=...)`, qui surcharge ce prompt
    exécution par exécution. None : le playbook dérivé de la fiche.
    """
    profil = profil_outille(agent)
    return AgentRuntime(
        provider,
        profil,
        system_prompt=(
            playbooks.prompt_systeme(profil.nom, profil.prompt_systeme)
            if playbooks is not None
            else None
        ),
    )


def default_runtimes(
    provider: ModelProvider,
    *,
    model: str | None = None,
    playbooks: PlaybookStore | None = None,
) -> dict[str, AgentRuntime]:
    """Les runtimes outillés des cinq rôles **du code**, indexés par nom d'agent.

    Un instantané, et plus le câblage de production : depuis #1037 l'exécuteur résout le
    runtime de chaque tâche depuis la fiche de l'agent routé, agents personnalisés
    compris — lui passer cette table le **restreindrait** à ces cinq noms. Elle reste ce
    qu'on injecte pour figer un régime (tests, câblages qui veulent exactement ces cinq
    rôles et rien d'autre).

    `model` (#69) bascule les cinq fiches sur un modèle unique (`gabarits_pour`), d'où les
    runtimes suivent : le modèle vient de la fiche, comme le reste. `playbooks` (#76)
    fige le prompt système sur la version courante du dépôt (cf. `runtime_outille`).
    """
    return {
        agent.nom: runtime_outille(provider, agent, playbooks=playbooks)
        for agent in gabarits_pour(model)
    }

"""Le régime d'exécution d'un agent — écrit par Maestro depuis sa politique (#1405).

## Le fait qui l'a rendu nécessaire

Run de p5, le 2026-10-01 : l'agent `dev-nextjs` a suspendu son travail par
`demander_arbitrage` et attendu **4 min 19 s** une personne, avant un `npm install`
que sa politique laissait passer (`Bash: auto`, portée `projet` — `maestro.portee`
range un `npm install` sans `-g` dans le projet, et le décideur `auto` le trace).
Son playbook disait : « Deux cas seulement attendent l'accord d'une personne : ce qui
sort du dossier du projet … **ou joindre un service extérieur** ». Cette exception
n'était dans aucune entrée du générateur : c'est le modèle qui rédigeait le playbook
(#257) qui l'avait ajoutée, en reformulant le régime que l'intention du rôle lui
donnait (#1102, #1226). Un texte écrit par un modèle avait créé une règle que la
politique n'a pas, et l'agent l'a crue.

## Ce que ce module change

Le régime n'est plus rédigé : il est **écrit par Maestro**, de façon déterministe,
depuis la politique que l'exécution applique — `PolitiqueOutils.decide`, l'appel
même que le hook du fournisseur lit avant chaque outil —, et ajouté au prompt
système de **chaque** tâche outillée (`maestro.agents.runtime.AgentRuntime.execute`),
comme le cadre d'exécution l'est au playbook d'une fiche (`_cadre_outille.md`). Il
est recomposé à chaque tâche, comme la politique est relue à chaque tâche (#110) :
une politique réglée depuis l'écran d'un agent (#262) change le texte de la tâche
suivante, et le texte ne peut pas dire autre chose que la règle.

Le modèle, lui, rédige le **métier** — ce que l'agent fait, comment il procède, ce
qu'il rend (`maestro.controltower.generation_agent._CADRE_GENERATION`) — et ne
reçoit plus le régime dans l'intention d'un rôle
(`maestro.equipe.proposition.intention_du_role`) : ce qu'on ne lui donne pas, il ne
le reformule pas. Le bloc **prime** sur ce que le playbook dirait d'autre des
commandes, et il le dit : c'est ce qui couvre un playbook écrit avant ce lot, ou
retouché à la main.

⚠ **Ce n'est pas un lexique** (#1169). Rien ici ne lit un playbook pour y chercher
une règle : le bloc est *ajouté*, et il dit le vrai parce qu'il est *composé* depuis
la règle, jamais parce qu'on aurait vérifié le reste du texte. Ce qui retire des
playbooks déjà écrits le régime qu'un modèle y avait mis est une réécriture par le
modèle (`maestro.controltower.regeneration_playbooks`) : le modèle juge, le code
compose.

## Deux lecteurs, deux formes, un seul endroit

- `regime_de_l_agent` s'adresse **à l'agent**, au tutoiement des playbooks
  (docs/04), dans son prompt système ;
- `REGIME_EXECUTION` et `REGIME_PORTEE` disent le même régime **à l'orchestrateur**
  (`maestro.controltower.regime`), qui parle de l'agent à la personne, à la
  troisième personne.

Les deux formes vivent ici, côte à côte et indexées par les **mêmes clés** (le
décideur, la portée) : un régime qu'on corrige se corrige pour ses deux lecteurs
dans le même fichier, et un décideur ou une portée qui n'aurait qu'une des deux
formes fait rougir `tests/test_regime_d_execution.py`. Elles disent la portée
**précisément** : ce qui reste dans le projet comprend l'installation de ses
dépendances et le réseau — `maestro.portee` : « le réseau n'est ni l'une ni l'autre
des deux familles ». C'est l'imprécision de « ce qui en sort » qui a laissé le
modèle y ranger un service extérieur.

## Ce qu'il ne décide pas

Rien de ce que l'exécution applique : ni le cran (`maestro.agents.permissions`), ni
la portée (`maestro.portee`), ni qui tranche (`maestro.decideur`). Il les **dit**,
et c'est tout. Il n'importe que ces modules-là et `maestro.lecture`, pour être lu
par le runtime comme par la Control Tower sans boucle d'import.
"""

from __future__ import annotations

from maestro.agents.permissions import PolitiqueOutils, Verdict
from maestro.decideur import Decideur
from maestro.lecture import OUTIL_SHELL
from maestro.portee import PORTEE_PROJET

#: Le titre du bloc dans le prompt système de l'agent — même niveau que le cadre
#: d'exécution (`### Cadre d'exécution`), dont il est le pendant : le cadre dit où
#: travailler et quoi rendre, le régime dit ce qui passe et ce qui revient à une
#: personne.
TITRE_REGIME = "### Ton régime d'exécution"

#: Ce que le bloc dit de lui-même avant de rien dire du régime : **qui** l'écrit, et
#: qu'il **prime**. C'est la phrase qui tranche un playbook écrit avant #1405, ou
#: retouché à la main, qui dirait autre chose — sans elle l'agent aurait deux règles
#: et choisirait la plus prudente, c'est-à-dire celle qui fait attendre quelqu'un.
INTRO_REGIME = (
    "Ce qui suit est écrit par Maestro, depuis la politique qu'il applique à tes "
    "appels d'outils pendant cette tâche. C'est la règle : elle prime sur tout ce que "
    "les consignes ci-dessus diraient d'autre de tes commandes ou des accords à "
    "demander."
)

#: Le shell, quand la politique ne le soumet à rien (aucune entrée `ask` ne le
#: couvre) : le verdict `PASSE`.
SHELL_LIBRE = "Tes commandes shell passent sans attendre personne."

#: Le shell, quand la politique le retire (`deny`, ou une liste `allow` fermée qui
#: ne le nomme pas) : le verdict `REFUS`. L'outil n'est alors pas même monté
#: (`PolitiqueOutils.filtre_outils`) — le dire évite que l'agent le cherche.
SHELL_REFUSE = (
    "Tu n'as pas de shell : ta politique te le refuse. Travaille avec tes autres outils."
)

#: Le shell soumis à un décideur, **dit à l'agent**, décideur par décideur.
#:
#: `HUMAIN` porte la règle de #1102 — un playbook ne fait pas d'une commande le
#: premier geste obligatoire —, qui vivait dans le cadre de génération et que le
#: modèle devait recopier dans le playbook. Elle est ici désormais, là où le cran est
#: connu pour de bon : un agent sous ce régime l'apprend de Maestro, à chaque tâche.
REGIME_DE_L_AGENT: dict[Decideur, str] = {
    Decideur.AUTO: (
        "Tes commandes shell passent sans attendre personne, et chacune est tracée."
    ),
    Decideur.HUMAIN: (
        "Tes commandes shell attendent l'accord d'une personne, qui peut ne pas venir "
        "pendant ta tâche — sauf celles qui ne font que lire, qui passent sans attendre "
        "personne. Sans réponse, la commande est refusée : ne fais donc pas d'une "
        "commande le préalable de tout le reste. Lis ce dont tu as besoin avec tes "
        "outils de lecture, commence ton travail, et ne lance une commande que "
        "lorsqu'elle sert ton livrable."
    ),
}

#: Ce que la **portée** ajoute au cran `auto`, dit à l'agent (#1226). C'est la
#: phrase que p5 n'avait pas : elle **énumère** ce qui reste dans le projet, réseau
#: et dépendances compris, et dit en toutes lettres qu'aucun de ces actes ne se fait
#: arbitrer — puis les deux familles de `maestro.portee`, et elles seules.
#:
#: ⚠ L'énumération ne range plus le **ménage** dans le travail ordinaire (#1401). Elle
#: disait « nettoyer ce que tes exécutions ont produit » : une invitation à effacer
#: `node_modules/` et `.next/` en fin de tâche, là où le cadre d'exécution
#: (`playbooks_defaut/_cadre_outille.md`) dit de les laisser, ignorés par le
#: `.gitignore`. Le geste reste permis — la règle de `maestro.portee` n'a pas bougé,
#: et ce bloc n'en dit que des exemples —, mais ce qu'il nomme pour finir une tâche
#: est ce que le cadre en dit : arrêter ce que l'agent a lancé.
PORTEE_DE_L_AGENT: dict[str, str] = {
    PORTEE_PROJET: (
        "Cela vaut dans le dossier du projet, pour tout le travail qui s'y fait : "
        "lancer, construire et tester ce que tu écris, installer les dépendances du "
        "projet dans le projet, joindre le réseau pour cela, arrêter ce que tu as "
        "lancé. Ne demande d'arbitrage pour aucune de ces commandes. "
        "Deux familles d'actes, et elles seules, attendent l'accord d'une personne : "
        "ce qui sort du dossier du projet — écrire ou installer ailleurs, une "
        "installation globale, l'élévation de privilèges, une machine distante —, et "
        "ce qui efface, écrase ou déplace ce qui s'y trouvait avant ta tâche."
    ),
}

#: Les autres outils de la politique, par sort — dits à l'agent.
AUTRES_A_UNE_PERSONNE = "Attendent l'accord d'une personne"
AUTRES_D_OFFICE = "Passent sans attendre personne, en étant tracés"
AUTRES_REFUSES = "Te sont refusés"

#: Le même régime, **dit à l'orchestrateur** (#1102, #1323) : un fait sur l'agent, à
#: la troisième personne, que `maestro.controltower.regime` colle derrière son nom.
#: Chaque phrase commence par une espace pour cette raison.
REGIME_EXECUTION: dict[Decideur, str] = {
    Decideur.HUMAIN: (
        " Ses commandes shell attendent l'accord d'une personne, qui peut ne pas "
        "venir pendant sa tâche — sauf celles qui ne font que lire, qui passent "
        "sans attendre personne."
    ),
    Decideur.AUTO: (
        " Ses commandes shell passent sans attendre personne, en étant tracées."
    ),
}

#: Ce que la portée ajoute au cran `auto`, dit à l'orchestrateur (#1226) — avec la
#: même précision que `PORTEE_DE_L_AGENT` : l'orchestrateur qui lirait « ce qui en
#: sort » annoncerait à la personne l'accord qu'un `npm install` ne demandera pas.
REGIME_PORTEE: dict[str, str] = {
    PORTEE_PROJET: (
        " Cela vaut dans le dossier du projet, l'installation de ses dépendances et le "
        "réseau compris : ce qui sort du dossier (écrire ou installer ailleurs, une "
        "installation globale, une machine distante), ou ce qui efface ce qui s'y "
        "trouvait avant lui, attend l'accord d'une personne."
    ),
}


def regime_de_l_agent(politique: PolitiqueOutils | None) -> str:
    """Le bloc de régime que l'agent reçoit, composé depuis `politique` — vide sans elle.

    `None` est un agent sans politique déclarée : tout lui est permis et le hook n'a
    rien à appliquer (`AgentRuntime.execute`, « comportement d'origine »). Il n'y a
    alors aucun régime à écrire, et le prompt reste celui d'avant ce lot, au
    caractère près.

    Le shell d'abord — c'est l'outil dont le cran se dérive du projet, et le seul qui
    ait fait attendre quelqu'un —, puis les autres outils que la politique nomme,
    par sort. Une liste `allow` fermée n'est pas énumérée : ce qu'elle exclut n'est
    pas monté sur la session, l'agent ne l'a pas dans les mains.
    """
    if politique is None:
        return ""
    lignes = [TITRE_REGIME, "", INTRO_REGIME, "", f"- {_shell(politique)}"]
    for libelle, outils in (
        (AUTRES_A_UNE_PERSONNE, outils_soumis(politique, Decideur.HUMAIN)),
        (AUTRES_D_OFFICE, outils_soumis(politique, Decideur.AUTO)),
        (AUTRES_REFUSES, [o for o in politique.deny if o != OUTIL_SHELL]),
    ):
        if outils:
            lignes.append(f"- {libelle} : {', '.join(outils)}.")
    return "\n".join(lignes)


def avec_regime(prompt_systeme: str, politique: PolitiqueOutils | None) -> str:
    """`prompt_systeme` suivi du bloc de régime — inchangé quand il n'y en a pas.

    En **fin** de prompt, après le playbook et son cadre : le bloc se réclame d'eux
    (« les consignes ci-dessus ») et c'est ce qui le fait primer à la lecture.
    """
    regime = regime_de_l_agent(politique)
    if not regime:
        return prompt_systeme
    corps = prompt_systeme.rstrip()
    return f"{corps}\n\n{regime}\n" if corps else f"{regime}\n"


def outils_soumis(politique: PolitiqueOutils, decideur: Decideur) -> list[str]:
    """Les entrées `ask` **hors shell** que tranche `decideur`, dans l'ordre du fichier.

    Le shell est dit à part, avec sa portée ; les autres outils soumis le sont à
    part de lui, parce que rien d'autre ne les borne — et, côté orchestrateur, parce
    que l'accord de l'objectif ne les couvre pas (#1198).
    """
    return [
        str(entree)
        for entree in politique.ask
        if entree.decideur is decideur and str(entree) != OUTIL_SHELL
    ]


def _shell(politique: PolitiqueOutils) -> str:
    """Le régime du shell, lu dans le verdict que la politique rend pour lui.

    C'est le verdict que le hook lira (`decide`) : `PASSE` quand rien ne le soumet,
    le cran et sa portée quand une entrée `ask` le couvre, `REFUS` quand la
    politique le retire. La portée ne se dit que sur `auto`, le seul cran qu'elle
    borne : sur `humain`, dedans comme dehors, une personne tranche. Un décideur que
    ce module ne sait pas dire est nommé tel quel plutôt que tu.
    """
    decision = politique.decide(OUTIL_SHELL)
    if decision.verdict is Verdict.REFUS:
        return SHELL_REFUSE
    if decision.verdict is Verdict.PASSE:
        return SHELL_LIBRE
    decideur = decision.decideur
    regime = REGIME_DE_L_AGENT.get(decideur) if decideur is not None else None
    if regime is None:
        return f"Tes commandes shell sont soumises au décideur « {decideur} »."
    if decideur is not Decideur.AUTO:
        return regime
    portee = PORTEE_DE_L_AGENT.get(decision.portee, "")
    return f"{regime} {portee}" if portee else regime

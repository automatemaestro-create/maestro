"""Les gabarits de rôle : ce que Maestro sait proposer, et ce qui le justifie (#1039).

Les cinq agents figés du code ne sont plus des agents qu'un projet reçoit
d'office : ils deviennent des **gabarits de rôle** que l'analyse consulte
([docs/37 §2.1, §4.1](../../docs/37-decision-equipe-sur-mesure.md)). Ce module
est ce catalogue de gabarits, et **rien d'autre** : il ne lit pas le disque, ne
touche à aucun dépôt et ne fabrique pas de proposition — c'est
`maestro.equipe.proposition` qui s'en sert.

Ce qu'un gabarit apporte, et d'où :

- son **rôle** et ses **compétences** viennent de `DEFAULT_AGENTS`
  (`maestro.agents.catalog`), **dérivés et jamais recopiés** : un rôle dont on
  élargirait les compétences dans le catalogue les élargirait ici sans une
  ligne, et deux listes finiraient par ne plus dire la même chose ;
- son **playbook de repli** vient du document du paquet
  (`maestro.agents.playbook_du_code`) — c'est « la matière n'est pas perdue » de
  docs/37 §2.1 ;
- ses **usages d'outillage** disent quels skills du projet il branche, par
  l'usage et non par le nom : `SKILL_PAR_USAGE` (#1030) tient les noms, et les
  redire ici en ferait une seconde table à tenir d'accord ;
- son **besoin** est la fonction qui répond à *ce projet appelle-t-il ce
  rôle ?*, et qui rend **l'endroit du projet** qui le prouve. Un rôle sans sa
  pièce serait une affirmation, exactement ce que l'analyse d'outillage refuse
  déjà (`Entree.justification`).

## Le nom proposé n'est pas celui du gabarit

`Gabarit.nom` (`dev`, `tests`, `infra`…) diffère de `Gabarit.gabarit`
(`developpeur`, `qa`, `devops`…), et ce n'est pas une coquetterie :
`playbook_outille` (#1037) rend **le document du paquet** pour tout nom de
`roles_du_code()`. Une fiche nommée `developpeur` verrait donc son playbook
*écrit pour ce projet* masqué par celui du code — la génération de #257
n'atteindrait jamais l'exécution. Le gabarit reste nommé sur le rôle proposé, la
filiation se lit, et le playbook proposé est celui qui s'appliquera.

Ces noms sont par ailleurs libres des `NOMS_RESERVES` du dépôt d'agents, ce que
`test_equipe` vérifiera : un rôle proposé doit pouvoir être créé (#1040) sans se
heurter à un nom que `AgentStore` refuse.

## L'orchestrateur n'est pas ici, et c'est la décision

Il n'a pas de gabarit parce qu'il n'est pas un rôle de l'équipe : c'est Maestro,
et c'est lui qui recrute (docs/37 §4.2). Il n'est pas pour autant passé sous
silence — `maestro.equipe.proposition` l'écarte **nommément**, avec sa raison.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from maestro.agents.catalog import DEFAULT_AGENTS, Agent
from maestro.agents.playbook_du_code import playbook_du_code
from maestro.outillage.detection import LANGAGE_PAR_EXTENSION
from maestro.outillage.modele import Constats, Piece

#: Les agents figés, indexés par nom — la matière des gabarits. Un gabarit qui
#: nommerait un agent absent lève à l'import de ce module, ce qui est préférable
#: à un rôle proposé sans compétences.
_AGENT_PAR_NOM: dict[str, Agent] = {agent.nom: agent for agent in DEFAULT_AGENTS}

#: Les extensions qui n'existent que pour dessiner un écran. C'est le signal
#: d'interface le plus honnête que l'analyse porte : `.ts` ne dit pas si le
#: projet a une interface (un service en TypeScript n'en a pas), une feuille de
#: style ou un composant d'écran, si.
#:
#: Les **langages** s'en dérivent (jamais une seconde liste de noms) : renommer
#: un langage dans la table de détection suit ici, et en retirer une extension
#: lève franchement à l'import.
EXTENSIONS_INTERFACE: tuple[str, ...] = (".css", ".scss", ".vue", ".svelte")
LANGAGES_INTERFACE: frozenset[str] = frozenset(
    LANGAGE_PAR_EXTENSION[extension] for extension in EXTENSIONS_INTERFACE
)

#: Le langage qui trahit une base de données dans un projet analysé — le même
#: raisonnement, dérivé de la même table.
LANGAGE_SQL: str = LANGAGE_PAR_EXTENSION[".sql"]

#: Les usages de vérification qu'un rôle QA couvre, dans l'ordre de préférence :
#: c'est le premier constaté qui justifie le rôle.
USAGES_VERIFICATION: tuple[str, ...] = ("tester", "lint", "formater", "types")


@dataclass(frozen=True)
class Justification:
    """Pourquoi ce rôle, et **l'endroit du projet** qui le prouve.

    `piece` peut être `None` — sur un projet qui n'a aucun fichier de code, le
    rôle de développeur reste justifié sans qu'aucun fichier ne le désigne, et
    inventer une pièce serait pire que de n'en pas avoir.
    """

    raison: str
    piece: Piece | None = None


#: La signature d'une règle de besoin : les constats du projet et les réponses
#: du questionnaire (vides sur un projet analysé), et rien d'autre. Pas de
#: `Recommandation` : un rôle est justifié par ce que le projet **est**, pas par
#: ce que Maestro propose d'y écrire — sans quoi un rôle justifierait un skill
#: qui justifierait ce rôle.
ReglesDeBesoin = Callable[[Constats, Mapping[str, str]], Justification | None]


def _besoin_developpeur(constats: Constats, reponses: Mapping[str, str]) -> Justification:
    """Toujours — un projet existe pour qu'on y écrive du code.

    C'est le seul rôle inconditionnel, comme `AGENTS.md` est la seule entrée
    d'outillage qui ne dépend d'aucun constat. Sa **justification**, elle, est
    bien constatée : le langage dominant et le fichier qui l'illustre.
    """
    if constats.langages:
        dominant = constats.langages[0]
        return Justification(
            raison=(
                f"le projet est écrit en {dominant.nom} "
                f"({_part(dominant.part)} des fichiers de code vus) : "
                "quelqu'un doit l'écrire et le modifier"
            ),
            piece=Piece(
                nom=dominant.nom,
                chemin=dominant.exemple,
                role=f"{dominant.fichiers} fichier(s) {dominant.nom}",
            )
            if dominant.exemple
            else None,
        )
    return Justification(
        raison=(
            "aucun langage de code n'a été constaté dans les bornes de l'analyse, "
            "mais un projet existe pour qu'on y écrive : c'est le seul rôle qu'un "
            "projet appelle sans rien avoir à prouver"
        )
    )


def _besoin_qa(constats: Constats, reponses: Mapping[str, str]) -> Justification | None:
    """Une vérification constatée — tests, style, format ou types."""
    for usage in USAGES_VERIFICATION:
        commande = constats.commande_de(usage)
        if commande is None:
            continue
        return Justification(
            raison=(
                f"le projet a sa commande de « {usage} » (`{commande.commande}`, "
                f"{commande.origine}) : quelqu'un doit la jouer et en rendre le verdict"
            ),
            piece=Piece(
                nom=commande.chemin,
                chemin=commande.chemin,
                role=commande.extrait or f"commande de {usage}",
            ),
        )
    return None


def _besoin_devops(constats: Constats, reponses: Mapping[str, str]) -> Justification | None:
    """Une CI, une construction ou une forge constatée.

    Les trois dans cet ordre, du plus parlant au plus faible : une CI dit que
    quelque chose vérifie le code à chaque changement, une construction qu'il y
    a une chaîne à tenir, une forge seulement qu'il y a un endroit où tout cela
    vivra.
    """
    if constats.ci:
        piece = constats.ci[0]
        return Justification(
            raison=(
                f"le projet a une intégration continue ({piece.chemin}) : "
                "elle se tient, et ce n'est le métier d'aucun autre rôle"
            ),
            piece=piece,
        )
    construire = constats.commande_de("construire")
    if construire is not None:
        return Justification(
            raison=(
                f"le projet se construit (`{construire.commande}`) : la chaîne de "
                "construction et son automatisation sont un métier à part"
            ),
            piece=Piece(
                nom=construire.chemin,
                chemin=construire.chemin,
                role=construire.extrait or "commande de construction",
            ),
        )
    if constats.forge is not None:
        return Justification(
            raison=(
                f"le code vit sur {constats.forge.nom} : il y a un endroit où poser "
                "une vérification automatique, même s'il n'y en a pas encore"
            ),
            piece=Piece(
                nom=constats.forge.nom,
                chemin=constats.forge.chemin,
                role="forge constatée",
            )
            if constats.forge.chemin
            else None,
        )
    return None


def _besoin_designer(constats: Constats, reponses: Mapping[str, str]) -> Justification | None:
    """Une interface : un langage d'écran constaté, ou une nature qui le dit."""
    for langage in constats.langages:
        if langage.nom in LANGAGES_INTERFACE:
            return Justification(
                raison=(
                    f"le projet porte du {langage.nom} : il a des écrans, et un écran "
                    "se conçoit avant de s'écrire"
                ),
                piece=Piece(
                    nom=langage.nom,
                    chemin=langage.exemple,
                    role=f"{langage.fichiers} fichier(s) {langage.nom}",
                )
                if langage.exemple
                else None,
            )
    if reponses.get("nature") == "application-web":
        return Justification(
            raison=(
                "vous avez répondu qu'il s'agit d'une application web : elle aura des "
                "écrans, et un écran se conçoit avant de s'écrire"
            )
        )
    return None


def _besoin_bdd(constats: Constats, reponses: Mapping[str, str]) -> Justification | None:
    """Du SQL constaté — schéma, migration ou requête posée dans le dépôt."""
    for langage in constats.langages:
        if langage.nom == LANGAGE_SQL:
            return Justification(
                raison=(
                    f"le projet porte du {LANGAGE_SQL} "
                    f"({langage.fichiers} fichier(s)) : un schéma et ses migrations "
                    "ne se traitent pas comme du code applicatif"
                ),
                piece=Piece(
                    nom=langage.nom,
                    chemin=langage.exemple,
                    role=f"{langage.fichiers} fichier(s) {LANGAGE_SQL}",
                )
                if langage.exemple
                else None,
            )
    return None


@dataclass(frozen=True)
class Gabarit:
    """Un rôle que Maestro sait proposer, et tout ce qui le décide.

    `nom` est le slug proposé pour la fiche, `gabarit` le nom de l'agent figé
    dont il sort (cf. la docstring du module : les deux diffèrent à dessein).

    `usages` liste, pour chaque usage d'outillage que ce rôle branche, la raison
    pour laquelle **ce rôle-là** le branche. L'usage, et jamais le nom du
    skill : `SKILL_PAR_USAGE` (#1030) tient les noms.

    ⚠ Un gabarit ne porte **aucune** politique d'autorisations propre, et c'est
    une décision : le cran d'un outil se dérive de ce que le **projet** déclare
    (`maestro.equipe.proposition`), jamais du rôle. Ce que le rôle ne doit pas
    faire — « une opération destructive se remonte, elle ne se joue pas »,
    « aucun déploiement réel » — est dans son **playbook**, qui peut distinguer
    un `SELECT` d'un `DROP` ; une politique de permissions ne le peut pas. Y
    poser un cran plus fermé bloquerait `ls` pour empêcher `DROP TABLE`, et
    laisserait le rôle incapable de travailler sans rendre le garde-fou plus
    vrai.

    `instances_selon` dit si ce rôle se démultiplie. `None` — le cas de tous les
    rôles sauf un — veut dire *une instance*, ce qui est le défaut de
    `CapaciteAgent` (docs/09 : on augmente les instances pour absorber la
    charge, on ne les pose pas par principe).
    """

    nom: str
    gabarit: str
    besoin: ReglesDeBesoin
    usages: tuple[tuple[str, str], ...] = ()
    instances_selon: Callable[[Constats], tuple[int, str]] | None = None

    @property
    def agent(self) -> Agent:
        """L'agent figé dont ce gabarit tient son rôle et ses compétences."""
        return _AGENT_PAR_NOM[self.gabarit]

    @property
    def role(self) -> str:
        """Le libellé humain du rôle — celui du catalogue, jamais réécrit."""
        return self.agent.role

    @property
    def competences(self) -> tuple[str, ...]:
        """Les compétences du rôle, triées — celles du catalogue, jamais réécrites.

        Triées parce que `Agent.competences` est un `frozenset` : sans tri, deux
        propositions du même projet rendraient deux ordres, et une forme servie
        par l'API qui bouge sans raison est une forme qu'on ne peut pas comparer.
        """
        return tuple(sorted(self.agent.competences))

    def playbook_de_repli(self) -> str:
        """Le playbook du gabarit — ce qui sert quand la génération n'a rien produit.

        Le document du paquet, entier : c'est un vrai playbook de rôle, pas une
        version dégradée, et c'est ce que docs/37 §2.1 appelle « la matière n'est
        pas perdue ».
        """
        return playbook_du_code(self.gabarit)


#: La part des fichiers de code au-delà de laquelle un langage compte comme une
#: **surface** du projet et non comme un résidu (un script de build en Shell, un
#: fichier de configuration en Python). Un seuil, donc un arbitrage : au-dessous,
#: le langage reste dans les constats, il ne justifie simplement pas une instance
#: de plus.
PART_SUBSTANTIELLE = 0.15

#: Le plafond d'instances qu'une proposition pose. Décision, pas mesure — cf.
#: `_instances_developpeur`.
INSTANCES_MAX_PROPOSEES = 3

#: Ce que dit un rôle qui n'a qu'une instance. Écrit une fois : c'est la réponse
#: à « pourquoi une seule ? », et elle est la même pour tous les rôles qui ne se
#: démultiplient pas.
RAISON_UNE_INSTANCE = (
    "une instance : un seul travail à la fois sur ce rôle, c'est le défaut "
    "(docs/09 — on augmente les instances pour absorber la charge, on ne les "
    "pose pas par principe)"
)


#: Les langages qui justifient **un autre rôle** — une feuille de style appelle
#: le designer, un fichier SQL le rôle base de données. Ils ne comptent donc pas
#: comme des surfaces du développeur : la même matière serait comptée deux fois,
#: une fois pour recruter un rôle et une fois pour lui retirer son travail.
LANGAGES_CONFIES_AILLEURS: frozenset[str] = LANGAGES_INTERFACE | {LANGAGE_SQL}


def _instances_developpeur(constats: Constats) -> tuple[int, str]:
    """Une instance par langage substantiel, plafonnée — et la raison qui va avec.

    Le seul rôle qui se démultiplie, parce que c'est le seul dont la charge se
    lit dans les constats : deux langages substantiels sont deux surfaces, et
    deux instances y travaillent sans se disputer les mêmes fichiers.

    Les langages **confiés à un autre rôle** en sont retirés
    (`LANGAGES_CONFIES_AILLEURS`) : le CSS d'un projet a déjà fait recruter un
    designer, et le compter ici ferait recruter un développeur de plus pour un
    travail qui n'est pas le sien.

    ⚠ Le plafond est **une décision, pas une mesure** : trois agents qui écrivent
    en même temps dans un projet est déjà beaucoup pour une équipe qu'on vient de
    proposer et que personne n'a encore vue travailler. Ne pas le déplacer en
    invoquant une mesure qui n'existe pas — c'est l'utilisateur qui l'ajuste à la
    validation (#1040), et la capacité se règle ensuite à tout moment (#86).
    """
    substantiels = [
        langage
        for langage in constats.langages
        if langage.part >= PART_SUBSTANTIELLE
        and langage.nom not in LANGAGES_CONFIES_AILLEURS
    ]
    if len(substantiels) < 2:
        return 1, RAISON_UNE_INSTANCE
    instances = min(len(substantiels), INSTANCES_MAX_PROPOSEES)
    nommes = ", ".join(f"{lang.nom} {_part(lang.part)}" for lang in substantiels)
    return instances, (
        f"{len(substantiels)} langages substantiels ({nommes}) : {instances} instances "
        "travaillent sur des surfaces différentes sans se disputer les mêmes fichiers. "
        f"Le plafond de {INSTANCES_MAX_PROPOSEES} est une décision, pas une mesure — "
        "c'est vous qui l'ajustez"
    )


#: Les cinq gabarits, **dans l'ordre du catalogue** (`DEFAULT_AGENTS`). L'ordre
#: fait foi pour le rendu : deux propositions du même projet rendent la même
#: liste, sans tri à l'affichage.
GABARITS: tuple[Gabarit, ...] = (
    Gabarit(
        nom="dev",
        gabarit="developpeur",
        besoin=_besoin_developpeur,
        usages=(
            ("installer", "il installe les dépendances avant d'écrire la première ligne"),
            (
                "construire",
                "il construit le projet pour vérifier que ce qu'il écrit tient debout",
            ),
            ("demarrer", "il lance le projet pour voir tourner ce qu'il vient de changer"),
        ),
        instances_selon=_instances_developpeur,
    ),
    Gabarit(
        nom="donnees",
        gabarit="bdd",
        besoin=_besoin_bdd,
        usages=(
            ("installer", "il lui faut les dépendances du projet pour jouer ses migrations"),
        ),
    ),
    Gabarit(
        nom="infra",
        gabarit="devops",
        besoin=_besoin_devops,
        usages=(
            (
                "construire",
                "la construction est ce que la vérification automatique doit rejouer à "
                "chaque changement",
            ),
            (
                "installer",
                "installer les dépendances est la première étape de toute vérification "
                "automatique",
            ),
        ),
    ),
    Gabarit(
        nom="interface",
        gabarit="designer",
        besoin=_besoin_designer,
        usages=(("demarrer", "un écran ne se juge qu'en le regardant tourner"),),
    ),
    Gabarit(
        nom="tests",
        gabarit="qa",
        besoin=_besoin_qa,
        usages=(
            ("tester", "c'est sa vérification centrale : le verdict qu'il rend sort de là"),
            (
                "lint",
                "le style, le format et les types font partie de ce qu'il vérifie avant "
                "de conclure",
            ),
        ),
    ),
)


def _part(part: float) -> str:
    """Une part de fichiers en pourcentage lisible (`0.6234` → « 62 % »)."""
    return f"{round(part * 100)} %"

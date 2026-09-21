"""Prompts système de l'orchestrateur — playbooks « Chef de projet » (#3, #298, #318).

⚠ **Les rôles et les compétences ne sont plus écrits dans le playbook** (#1041,
[docs/37 §3.5](../../docs/37-decision-equipe-sur-mesure.md)) : ils sont **dérivés de
l'équipe** qu'on passe à `prompt_orchestrateur`, c'est-à-dire des agents du projet
(`maestro.agents.store.catalogue_du_projet`). Une liste figée dans le document ne
pouvait décrire qu'un seul projet — celui aux cinq rôles du code —, si bien que les
compétences d'un agent recruté pour *ce* projet n'étaient jamais proposées au
découpage, et que ses tâches ne l'atteignaient que par le classifieur de repli ou
par une réassignation à la main.


Deux documents, un seul agent : `playbook.md` le fait **décomposer** (#3), et
`playbook_brief.md` le fait **cadrer** avant de décomposer (#318). Le brief est un
geste du Chef de projet et non un septième agent — d'où son prompt ici, avec les
playbooks, plutôt que dans une famille de plus. Ils restent **deux fichiers** parce
que leurs contrats de sortie s'excluent — un tableau de tâches, un objet de brief — et
qu'un prompt système décrivant les deux laisserait le modèle choisir lequel rendre.

Matérialise la fiche `docs/04-specifications-agents.md §3.1` en instructions exécutables,
et fixe le **contrat de sortie** : un tableau JSON de tâches conformes à
`packages/shared/schemas/task.schema.json` (et, pour le brief, un objet conforme à
`brief.schema.json`). Le prompt est volontairement strict sur la forme (JSON pur, champs
exacts) parce que la sortie est ensuite parsée et validée par
`maestro.orchestrator.schema`.

Le playbook lui-même est un **document Markdown** (`playbook.md`, à côté de ce module)
depuis #298 : structuré, relisable et diffable, comme ceux des cinq rôles exécutants
(#295). Ce module ne fait plus que le charger et y substituer la fourchette de tâches.

⚠ Il vit **ici** et non dans `maestro/agents/playbooks_defaut/`, à dessein — c'était la
décision ouverte du ticket :

- ce dossier-là est le repli du **catalogue** (`PLAYBOOK_DEFAUTS` est construit sur
  `GABARITS_DU_CODE`) et la liste des agents que la Control Tower édite et versionne
  (`core/playbooks/<agent>/`). Le Chef de projet n'est ni dans le catalogue — il n'exécute
  pas de tâche — ni éditable : y déposer son document ferait mentir `roles_du_code()` et
  laisserait croire à un repli versionné qui n'existe pas ;
- il ne pourrait pas prendre le tronc commun `{{socle}}` de toute façon : la section « Ce
  que tu rends » du socle impose deux sections de prose au compte-rendu, ce qui contredit
  frontalement le « réponds UNIQUEMENT par un tableau JSON » d'ici. Sans fragment partagé
  à reprendre, la raison principale de cohabiter disparaît. Son régime sénior est donc
  écrit dans son propre document, adapté à sa seule voie de sortie : ses arbitrages ne se
  rendent pas en prose, ils se lisent dans les tâches qu'il émet.

D'où un chargeur local plutôt qu'un import de `maestro.agents.playbook_du_code`, dont la
racine est celle des rôles. Il en garde le principe : substitution de marqueurs `{{…}}` sur
une table **fermée**, et échec franc sur un marqueur inconnu ou une accolade laissée dans le
texte — mieux vaut un import qui échoue qu'un prompt système servi avec un trou dedans.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

from maestro.agents.catalog import GABARITS_DU_CODE, Agent
from maestro.orchestrator.schema import Clarification

#: Fourchette visée. Guidage, pas une règle de schéma, et depuis #298 le playbook la
#: présente comme une **conséquence** du découpage plutôt que comme un quota à remplir.
#: MIN reste un vrai plancher : le critère d'acceptation du ticket #6 demande que la
#: boucle assigne et exécute **au moins 3** tâches. MAX borne le découpage inutilement fin.
MIN_TASKS = 3
MAX_TASKS = 5

#: Le playbook « du code » du Chef de projet, livré avec le paquet (cf. `package-data`
#: dans `pyproject.toml` — sans quoi une roue s'installerait sans lui).
CHEMIN_PLAYBOOK = Path(__file__).resolve().parent / "playbook.md"

#: Le playbook du **brief** (#318) : le même Chef de projet, l'étape d'avant. Un
#: document à part et non une section de plus dans `playbook.md`, parce que les deux
#: contrats de sortie s'excluent — l'un impose un tableau de tâches, l'autre un objet
#: de brief, et un prompt système qui décrirait les deux laisserait le modèle choisir.
CHEMIN_PLAYBOOK_BRIEF = Path(__file__).resolve().parent / "playbook_brief.md"

#: Un marqueur dans le document : `{{min_taches}}`, `{{max_taches}}`, `{{roles}}`,
#: `{{equipe}}`, `{{competences}}`.
_MARQUEUR = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")


def _valeurs(equipe: Sequence[Agent]) -> dict[str, str]:
    """Les substitutions admises dans le document, pour cette équipe-ci.

    Volontairement **fermée** : un marqueur hors de cette table lève, plutôt que
    de partir tel quel dans le prompt système. Elle est calculée par appel depuis
    #1041, et non plus figée au module : trois de ses cinq entrées décrivent
    l'équipe du projet, qui change d'un projet à l'autre.

    Les deux entrées de découpage (`min_taches`, `max_taches`) restent des
    constantes de guidage : elles ne dépendent pas de qui exécute.
    """
    return {
        "min_taches": str(MIN_TASKS),
        "max_taches": str(MAX_TASKS),
        "roles": _roles(equipe),
        "equipe": _bloc_equipe(equipe),
        "competences": _liste_competences(equipe),
    }


def _roles(equipe: Sequence[Agent]) -> str:
    """Les rôles de l'équipe en une énumération — « Développeur, QA / Testeur »."""
    return ", ".join(agent.role for agent in equipe)


def _bloc_equipe(equipe: Sequence[Agent]) -> str:
    """L'équipe en liste Markdown : un rôle par ligne, avec ses compétences.

    Le **nom** de la fiche voyage à côté du rôle parce que c'est lui qui identifie
    l'agent partout ailleurs (routage, journal, Kanban), et que deux fiches d'un
    même projet peuvent porter un rôle homonyme — deux instances d'un même
    gabarit, par exemple. Les compétences sont triées : `Agent.competences` est un
    `frozenset`, et un prompt système dont l'ordre bouge à chaque construction
    n'est pas comparable d'un run à l'autre (même règle que `Gabarit.competences`).
    """
    return "\n".join(
        f"- **{agent.role}** (`{agent.nom}`) — {', '.join(sorted(agent.competences))}"
        for agent in equipe
    )


def _liste_competences(equipe: Sequence[Agent]) -> str:
    """Les tags admis pour `competences_requises` — ceux de l'équipe, et eux seuls.

    Groupés par rôle dans l'ordre du catalogue puis dédupliqués : un tag partagé
    par deux rôles n'apparaît qu'une fois, à sa première occurrence. C'est la même
    liste que celle du bloc ci-dessus, aplatie — jamais une seconde table à tenir
    d'accord avec elle.
    """
    tags = dict.fromkeys(
        tag for agent in equipe for tag in sorted(agent.competences)
    )
    return ", ".join(tags) + "."


def _lire_playbook(chemin: Path, equipe: Sequence[Agent]) -> str:
    """Le playbook de `chemin`, marqueurs substitués — un prompt système effectif."""
    if not chemin.is_file():
        raise FileNotFoundError(f"playbook du Chef de projet introuvable : {chemin}")
    valeurs = _valeurs(equipe)

    def substitue(m: re.Match[str]) -> str:
        cle = m.group(1)
        if cle not in valeurs:
            raise ValueError(
                f"marqueur de playbook inconnu : {{{{{cle}}}}} (attendus : "
                f"{', '.join(sorted(valeurs))})."
            )
        return valeurs[cle]

    texte = _MARQUEUR.sub(substitue, chemin.read_text(encoding="utf-8").strip())
    if "{{" in texte:
        raise ValueError(f"marqueur mal formé dans le playbook {chemin.name}.")
    return texte


def prompt_orchestrateur(equipe: Sequence[Agent] | None = None) -> str:
    """Le prompt système de décomposition, cadré sur `equipe` (#1041).

    C'est **le seul** endroit où les rôles et les compétences entrent dans le
    playbook du Chef de projet : ils n'y sont plus écrits, ils en sont dérivés
    (cf. la docstring du module). Le routage (`maestro.router`) lit les mêmes
    fiches, si bien qu'un tag proposé au découpage est par construction un tag que
    quelqu'un sait prendre.

    `None` — et une séquence **vide**, qui vaut omission — retombe sur les agents
    du code (`GABARITS_DU_CODE`), c'est-à-dire sur les gabarits de rôle : un plan
    hors projet (la CLI `maestro-plan`, une activité durable, un test) garde
    exactement le prompt d'avant ce lot, et un projet né sans agent (#1042)
    continue de se faire découper au lieu de recevoir un playbook sans équipe.
    C'est une **dérivation**, jamais une liste recopiée : élargir les compétences
    d'un rôle du catalogue les élargit ici sans une ligne.

    Le document est relu à chaque appel, comme les playbooks des rôles le sont à
    chaque tâche (#78) : l'équipe change entre deux runs, et retenir un prompt
    construit une fois ferait découper le second sur l'équipe du premier.
    """
    return _lire_playbook(
        CHEMIN_PLAYBOOK, tuple(equipe) if equipe else GABARITS_DU_CODE
    )


#: Le prompt système de décomposition **sans projet** : celui des gabarits du code.
#: Conservé comme constante pour les appelants qui ne cadrent rien (la CLI, les
#: doubles de fournisseur des tests) ; tout ce qui connaît un projet passe par
#: `prompt_orchestrateur`.
ORCHESTRATOR_SYSTEM_PROMPT = prompt_orchestrateur()

#: Prompt système de l'étape **brief** (#318). Le playbook du brief ne porte aucun
#: marqueur aujourd'hui — il passe par le même chargeur pour hériter du même échec
#: franc si l'un y était ajouté sans être déclaré dans `_valeurs`. Il ne prend pas
#: d'équipe : cadrer un objectif ne demande pas de savoir qui l'exécutera, et le
#: brief ne nomme aucune compétence.
BRIEF_SYSTEM_PROMPT = _lire_playbook(CHEMIN_PLAYBOOK_BRIEF, GABARITS_DU_CODE)

#: Ce qu'on dit au modèle quand aucune source n'accompagne l'objectif. Le dire
#: explicitement plutôt que se taire : un silence laisse le modèle supposer qu'un
#: document lui a été fourni et qu'il l'a mal lu, et inventer ce qu'il croit y
#: manquer — le brief doit travailler sur le texte seul **en le sachant**.
_SANS_SOURCE = (
    "Aucune source n'a été fournie : travaille sur le texte de l'objectif seul, "
    "et n'invente aucun document."
)


def build_user_prompt(objective: str) -> str:
    """Compose le message utilisateur transmis au modèle pour un objectif donné."""
    cleaned = objective.strip()
    return (
        "Découpe l'objectif suivant en un plan de tâches raisonné, en respectant "
        "strictement le format JSON imposé par tes instructions. Chaque description "
        "porte ses quatre sections : objectif, périmètre et limites, latitude de "
        "décision, critères de réussite.\n\n"
        f"Objectif :\n{cleaned}"
    )


def build_brief_user_prompt(
    objectif: str,
    contexte_sources: str = "",
    clarifications: Sequence[Clarification] = (),
    *,
    dernier_tour: bool = False,
) -> str:
    """Compose le message utilisateur de l'étape **brief** (#318, régénéré en #321).

    `contexte_sources` est le contenu extrait **déjà encadré** par
    `maestro.sources.extraction.contexte_markdown` — jamais du Markdown brut : c'est
    cet encadrement (préambule, noms assainis, clôture calculée) qui tient la
    frontière donnée / consigne (ENF-13). Vide quand aucune source n'a été fournie.

    Les sources viennent **après** la consigne et après l'objectif, à dessein : ce
    qui est en dernier est ce qui pèse le plus, et on ne veut pas que ce soit le
    contenu non fiable qui donne le ton de la réponse.

    `clarifications` (#321) porte les allers-retours **déjà joués**, cumulés depuis
    le premier tour : le brief est régénéré **en entier** à chaque fois, jamais
    rapiécé, donc le modèle a besoin de tout l'historique pour ne pas reperdre ce
    qu'un tour précédent avait levé. Elles passent **après** les sources — donc en
    dernier, au rang le plus fort : ce sont des réponses de l'utilisateur, la seule
    entrée de ce prompt qui fasse autorité sur l'objectif lui-même.

    `dernier_tour` annonce que le plafond est atteint et qu'il n'y aura plus de
    question posée. Le modèle est alors invité à **trancher en hypothèses** ce qu'il
    reste. C'est une invitation et non la garantie : celle-ci est tenue en Python
    par `Brief.questions_en_hypotheses`, qui rattrape le cas où il repose ses
    questions quand même — un plafond qui dépendrait de la docilité du modèle n'est
    pas un plafond.
    """
    cleaned = objectif.strip()
    morceaux = [
        "Rédige le brief structuré de l'objectif suivant, en respectant strictement "
        "le format JSON imposé par tes instructions.",
        "",
        f"Objectif :\n{cleaned}",
        "",
        contexte_sources.strip() if contexte_sources.strip() else _SANS_SOURCE,
    ]
    if clarifications:
        morceaux.extend(["", _bloc_clarifications(clarifications, dernier_tour)])
    return "\n".join(morceaux)


def _bloc_clarifications(
    clarifications: Sequence[Clarification], dernier_tour: bool
) -> str:
    """Rend les allers-retours déjà joués, et ce qu'on attend du tour qui vient (#321).

    Les questions restées **sans réponse** sont annoncées comme telles plutôt
    qu'omises : les taire ferait croire au modèle qu'il ne les a jamais posées, et
    il les reposerait à l'identique — un tour d'aller-retour dépensé pour rien. Les
    dire, c'est lui demander d'en faire une hypothèse et d'avancer.
    """
    lignes = [
        "Réponses de l'utilisateur à tes questions précédentes. Elles font autorité : "
        "intègre-les au brief que tu réécris — dans le périmètre, les contraintes, les "
        "critères ou les hypothèses, selon ce qu'elles tranchent — et ne repose aucune "
        "question qu'elles ont déjà levée.",
        "",
    ]
    for clarification in clarifications:
        lignes.append(f"- Question : {clarification.question}")
        if clarification.sans_reponse:
            lignes.append(
                "  Réponse : aucune. Ne la repose pas : tranche-la en hypothèse explicite."
            )
        else:
            lignes.append(f"  Réponse : {clarification.reponse}")
    lignes.append("")
    if dernier_tour:
        lignes.append(
            "C'est le DERNIER tour : plus aucune question ne sera posée. Rends "
            "`questions` vide et inscris en hypothèses explicites tout ce que tu n'as "
            "pas pu lever, en disant ce que tu retiens faute de réponse."
        )
    else:
        lignes.append(
            "Ne conserve dans `questions` que ce qui reste réellement indécidable et "
            "qui change le plan selon la réponse. Le reste devient une hypothèse."
        )
    return "\n".join(lignes)

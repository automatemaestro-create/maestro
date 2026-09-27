"""Ce qu'un run fera — lu dans ce que Maestro applique, jamais deviné (ticket #1323).

L'orchestrateur lisait le projet, ses runs et leurs livrables (#1223, #1263) ; il
ne savait pas ce que **Maestro lui-même** ferait. Au bouclage du 2026-09-25
(`main` à `aa390ca`, vraie stack, vrai modèle), il a donc affirmé à la personne,
deux fois sur deux échantillons, une chose fausse sur la suite :

- **un accord que le run ne demandera pas** (S3) — après la validation de
  l'équipe : « Comme le rangement va déplacer des fichiers, le run vous demandera
  votre accord avant de commencer. » Le run `6ac2e9c2faef` n'a posé aucune
  demande de validation : depuis #1226/#1237 un acte dans le projet passe sans
  personne, et l'acte qu'un objectif nomme est imputé à l'accord du cadrage
  (#1198) ;
- **une borne de run tenue pour un réglage durable** (S4 puis P8) — la borne
  « s'interrompt à 1 tokens », donnée à l'accord d'un run passé, devenue « si ce
  réglage n'a pas changé, ce nouveau run a toutes les chances de s'arrêter
  pareil. Je ne sais pas où ce plafond a été défini ». La carte de la même
  proposition disait « Aucune borne », et le run est allé au bout.

Ce n'était pas une affaire de ton : le modèle comblait un vide, parce qu'aucun
de ces faits n'était dans ce qu'il recevait. Ce module les écrit, **comme des
faits** : il ne dit pas au modèle quoi répondre, il lui dit ce que Maestro fait.
Aucun lexique, aucune phrase interdite (#1169) — la règle qui en découle,
s'appuyer sur ces faits et avouer ce qu'ils ne disent pas, vit dans le cadre de
l'orchestrateur (`orchestration._PROMPT_ORCHESTRATION`), et nulle part ailleurs.

## Ce que le bloc dit, et d'où il le tient

`regime_d_un_run` compose un bloc de contexte, lu **à chaque message** comme
l'équipe ou les faits des runs — une politique se règle depuis l'écran d'un
agent (#262) et l'exécution la relit à chaque tâche, donc un bloc figé
annoncerait le régime d'hier :

- **le cadrage** — ce que fait un run au départ, selon le régime de brief que le
  lanceur du fil lui pose (`PHRASES_DU_BRIEF`, indexées par `MODE_BRIEF_*`) ;
- **les actes de l'équipe, agent par agent** — lus dans la politique que
  l'exécution appliquera (`PermissionStore.pour_projet(…).lire`, l'appel exact
  de `Executor._politique_permissions`) et dits avec les phrases de
  `maestro.equipe.proposition` (`REGIME_EXECUTION`, `REGIME_PORTEE`). Ce sont
  celles que l'intention d'un rôle porte déjà : une seconde formulation du même
  régime finirait par en décrire un autre ;
- **l'acte que l'objectif nomme** — accordé avec le run (`REGLE_DE_L_ACTE_ACCORDE`,
  #1198) ;
- **ce qui revient à la personne, et ce qu'on ne sait pas d'avance** — une
  demande de validation quand la politique en fait une, une question ou un
  arbitrage qu'un agent peut poser de lui-même, un renfort que le plan peut
  appeler (`CE_QUI_NE_SE_PREVOIT_PAS`) ;
- **les bornes** — celles que la carte posera à l'accord, aucune par défaut, et
  jamais héritées d'un run passé (`REGLE_DES_BORNES`). Celles d'un run passé se
  lisent sur sa fiche (`bornes_du_run`), depuis l'événement de lancement qui les
  porte (`Event.bornes`).

## Ce qu'il ne sait pas, il le dit

Une politique illisible n'est pas passée sous silence : l'exécution en ferait un
échec de tâche, et c'est ce que le bloc en dit. Un projet sans agent n'a pas de
politique à lire — l'équipe proposée porte la sienne, que la personne valide avec
elle. Sans projet, le bloc ne parle que de ce qui ne dépend d'aucune équipe.

Le module est **pur** : il ne lit aucun dépôt. C'est l'app qui les lit
(`create_app`, `regime_du_projet`) et lui passe ce qu'elle a trouvé, ce qui le
rend jouable sans disque et garde une seule lecture des politiques — celle de
l'exécution.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from maestro.agents.permissions import PolitiqueOutils, Verdict
from maestro.controltower.bornes import AUCUNE_BORNE, BornesRun
from maestro.decideur import Decideur
from maestro.engine.brief import MODE_BRIEF_AUTO, MODE_BRIEF_HUMAIN, MODE_BRIEF_SANS
from maestro.equipe.proposition import OUTIL_EXECUTION, REGIME_EXECUTION, REGIME_PORTEE

#: L'en-tête du bloc — ce qui le distingue, dans le prompt, des faits du projet
#: et des runs : ceux-là disent ce qui **est**, celui-ci ce qu'un run **fera**.
ENTETE = "Ce qu'un run fera — lu dans ce que Maestro applique, pas deviné :"

#: Ce que fait un run au départ, par régime de brief (`maestro.engine.brief`).
#: Le fil lance en `auto` (`create_app`, `ouvrir_un_run`), et c'est la phrase qui
#: manquait à S3 : l'accord donné sur la carte est le **seul** qu'un run du fil
#: attend pour démarrer. Les deux autres régimes sont là pour qu'un lanceur qui
#: en changerait ne fasse pas dire au fil le régime d'un autre.
PHRASES_DU_BRIEF: dict[str, str] = {
    MODE_BRIEF_AUTO: (
        "Cadrage : un run ouvert depuis ce fil rédige son brief et découpe ses "
        "tâches sans rien soumettre à personne. L'accord donné sur la carte de la "
        "proposition est le seul qu'il attend pour démarrer."
    ),
    MODE_BRIEF_HUMAIN: (
        "Cadrage : le brief d'un run attend la validation de l'utilisateur, sur la "
        "vue du run, avant que la moindre tâche ne soit créée."
    ),
    MODE_BRIEF_SANS: (
        "Cadrage : un run découpe son objectif tel quel en tâches, sans brief à "
        "valider."
    ),
}

#: L'accord de l'objectif (#1198), dit comme l'exécution l'applique : l'outil
#: d'exécution de la tâche qui porte l'acte passe sans nouvelle demande, et rien
#: d'autre ne bouge. C'est la moitié de S3 que la politique seule ne dit pas — le
#: rangement « déplace des fichiers » que la personne a posés là, et c'est
#: pourtant sans nouvelle demande, parce que l'objectif accepté le nommait.
REGLE_DE_L_ACTE_ACCORDE = (
    "Un acte que l'objectif accepté nomme lui-même — vider un dossier, supprimer, "
    "déplacer ou renommer des fichiers — est accordé avec le run : la tâche qui le "
    "commet le porte, et ses commandes shell passent sans nouvelle demande, même là "
    "où la politique ci-dessus en ferait une. Ce qui reste refusé l'est toujours "
    "(refus de la politique, fichiers exclus du projet). Un acte que l'objectif ne "
    "nomme pas suit la politique."
)

#: Où arrive ce qui revient à une personne, et ce qu'il advient sans elle —
#: le fail-safe de l'arbitrage (EF-08) : l'acte est refusé, l'agent poursuit.
CE_QUI_REVIENT = (
    "Ce qui revient à l'utilisateur arrive comme une demande de validation, à "
    "trancher depuis l'écran Validations ou la vue du run ; sans réponse à temps, "
    "l'acte est refusé et l'agent poursuit sans lui."
)

#: Ce qu'aucune politique ne dit d'avance, et qu'un fait honnête doit nommer : le
#: bloc ne peut pas promettre qu'un run ne dérangera personne. Chaque chose dit
#: **où** elle arrive, parce que c'est ce que le modèle comblait sinon — le rejeu
#: de P8 sur la vraie stack a vu une question d'agent annoncée « dans l'écran
#: Validations », quand elle se répond au pied du fil (`QuestionDansLeFil`).
CE_QUI_NE_SE_PREVOIT_PAS = (
    "Ce qui ne se prévoit pas : pendant sa tâche, un agent peut de lui-même poser "
    "une question à l'utilisateur — elle arrive au pied du fil de conversation, "
    "avec ce qu'il fera sans réponse — ou lui demander d'arbitrer un acte — une "
    "demande de validation, comme ci-dessus —, et un run dont le plan appelle un "
    "métier que l'équipe n'a pas propose un renfort dans le fil qui l'a lancé. "
    "Cela revient à l'utilisateur si cela arrive, et rien ne permet de le savoir "
    "avant."
)

#: Les bornes d'un run — le fait que P8 ignorait. Elles se posent à l'accord, sur
#: la carte, et n'appartiennent qu'au run qui les reçoit ; le défaut est dit avec
#: les mots de la carte (`BornesRun.en_phrase`), pour que le fil et l'écran ne
#: présentent pas le même run sous deux régimes.
REGLE_DES_BORNES = (
    "Bornes : celles d'un run — plafond de coût, de tokens, délai par tâche, tâches "
    "en parallèle — se posent au moment de l'accepter, sur la carte de la "
    "proposition ou dans les mots d'un accord tapé (« vas-y, 5 $ max »), et ne "
    "valent que pour lui. Un run n'hérite jamais des bornes d'un run précédent, et "
    "l'estimation de coût d'une proposition n'en est pas une. Sans borne posée, "
    f"c'est « {AUCUNE_BORNE.en_phrase()} ». Les bornes d'un run passé, dans sa "
    "fiche, sont les siennes : un run arrêté sur sa borne n'annonce rien du suivant."
)

#: Un projet sans agent n'a aucune politique à lire — ce n'est pas un vide, c'est
#: un fait, et il dit où est la politique qui viendra.
SANS_AGENT = (
    "Actes de l'équipe : ce projet n'a encore aucun agent, donc aucune politique à "
    "lire. L'équipe proposée porte la sienne, que l'utilisateur valide avec elle."
)


@dataclass(frozen=True)
class MembreDeLEquipe:
    """Un agent de l'équipe, et la politique que l'exécution appliquera à ses tâches.

    `politique` est ce que `PermissionStore.lire` rend : `None` quand personne ne
    déclare de politique pour cet agent — tout permis, comportement d'origine.
    `illisible` porte la cause quand la lecture a refusé la politique : c'est un
    fait distinct d'une absence, et l'exécution n'en fait pas la même chose (un
    échec de tâche, avant tout acte).
    """

    role: str
    nom: str
    politique: PolitiqueOutils | None = None
    illisible: str = ""


def regime_d_un_run(
    membres: Sequence[MembreDeLEquipe] | None, *, mode_brief: str = MODE_BRIEF_AUTO
) -> str:
    """Le bloc de faits : ce qu'un run fera, pour l'équipe `membres`.

    `membres` à `None` dit « aucun projet, donc aucune équipe à lire » : le bloc
    ne parle alors que de ce qui ne dépend pas d'elle (cadrage, bornes). Une
    séquence **vide** dit « un projet sans agent », qui est un fait à dire.
    `mode_brief` est celui que le lanceur du fil pose — l'appelant le tient du
    même endroit que lui, faute de quoi le fil décrirait le régime d'un autre.
    """
    lignes = [ENTETE]
    brief = PHRASES_DU_BRIEF.get(mode_brief)
    if brief:
        lignes.append(f"- {brief}")
    if membres is not None:
        lignes.append(f"- {regime_des_actes(membres)}")
        if membres:
            lignes.append(f"- {REGLE_DE_L_ACTE_ACCORDE}")
            lignes.append(f"- {CE_QUI_REVIENT}")
        lignes.append(f"- {CE_QUI_NE_SE_PREVOIT_PAS}")
    lignes.append(f"- {REGLE_DES_BORNES}")
    return "\n".join(lignes)


def regime_des_actes(membres: Sequence[MembreDeLEquipe]) -> str:
    """Ce que la politique de chaque agent fait de ses actes — ou qu'il n'y a personne.

    Une ligne par agent, dans l'ordre du catalogue : c'est vers eux que les
    tâches seront routées, et ce que chacun fera sans personne dépend de **sa**
    politique — deux agents d'une même équipe peuvent ne pas avoir le même cran.
    """
    if not membres:
        return SANS_AGENT
    lignes = ["Actes de l'équipe, selon la politique de chaque agent :"]
    lignes.extend(f"  · {_actes_du_membre(membre)}" for membre in membres)
    return "\n".join(lignes)


def bornes_du_run(bornes: BornesRun | None) -> str:
    """La ligne de fiche d'un run : ses bornes à lui, ou l'aveu qu'on ne les connaît pas.

    `None` est un run dont le lancement ne les a pas consignées — antérieur à
    #1323, ou publié hors de l'API. On le dit plutôt que de taire la ligne : un
    run arrêté sur un plafond, dont la fiche ne dirait rien de ses bornes, est
    exactement ce qui a fait inventer au fil un « réglage » qui dure.
    """
    if bornes is None:
        return "bornes : non consignées pour ce run"
    return f"bornes de ce run, posées à son lancement : {bornes.en_phrase()}"


def _actes_du_membre(membre: MembreDeLEquipe) -> str:
    """Un agent, et ce que sa politique fait de ses commandes shell et de ses autres outils.

    Chaque phrase de régime commence par une espace, comme `REGIME_EXECUTION` :
    elles se collent derrière le nom de l'agent.
    """
    qui = f"{membre.role} « {membre.nom} »." if membre.role else f"« {membre.nom} »."
    if membre.illisible:
        return (
            f"{qui} Sa politique est illisible ({membre.illisible}) : ses tâches "
            "échoueront avant d'agir tant qu'elle n'est pas corrigée."
        )
    politique = membre.politique
    if politique is None:
        return (
            f"{qui} Aucune politique déclarée : ses commandes shell et ses autres "
            "outils passent sans attendre personne."
        )
    morceaux = [qui + _shell(politique)]
    soumis = _autres_soumis(politique, Decideur.HUMAIN)
    if soumis:
        morceaux.append(f"Attendent l'accord d'une personne : {', '.join(soumis)}.")
    d_office = _autres_soumis(politique, Decideur.AUTO)
    if d_office:
        morceaux.append(f"Passent d'office, en étant tracés : {', '.join(d_office)}.")
    if politique.deny:
        morceaux.append(f"Refusés d'office : {', '.join(politique.deny)}.")
    return " ".join(morceaux)


def _shell(politique: PolitiqueOutils) -> str:
    """Le régime des commandes shell, dit avec les phrases de l'intention d'un rôle (#1102, #1226).

    Le verdict est celui que la politique rend pour l'outil d'exécution
    (`decide`), c'est-à-dire celui que le hook lira : `PASSE` quand rien ne le
    soumet, le cran et sa portée quand une entrée `ask` le couvre, `REFUS` quand
    la politique le retire. La portée ne se dit que sur le cran `auto`, le seul
    qu'elle borne : sur `humain`, dedans comme dehors, une personne tranche.
    """
    decision = politique.decide(OUTIL_EXECUTION)
    if decision.verdict is Verdict.REFUS:
        return " Il n'a pas de shell : sa politique le lui refuse."
    if decision.verdict is Verdict.PASSE:
        return " Ses commandes shell passent sans attendre personne."
    decideur = decision.decideur
    regime = REGIME_EXECUTION.get(decideur) if decideur is not None else None
    if regime is None:
        return f" Ses commandes shell sont soumises au décideur « {decideur} »."
    portee = REGIME_PORTEE.get(decision.portee, "") if decideur is Decideur.AUTO else ""
    return regime + portee


def _autres_soumis(politique: PolitiqueOutils, decideur: Decideur) -> list[str]:
    """Les entrées `ask` **hors shell** que tranche `decideur`.

    Le shell est déjà dit par `_shell` ; les autres outils soumis le sont à part,
    parce que l'accord de l'objectif ne les couvre pas (#1198 : « vide le
    dossier » n'a jamais accordé un message dans Slack).
    """
    return [
        str(entree)
        for entree in politique.ask
        if entree.decideur is decideur and str(entree) != OUTIL_EXECUTION
    ]

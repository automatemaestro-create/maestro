"""Des constats et de l'outillage à l'équipe proposée — rôle par rôle (#1039).

La seconde moitié du module : `maestro.equipe.gabarits` dit ce que Maestro sait
proposer, celui-ci en déduit ce que **ce projet-là** appelle. Il ne touche pas
au disque — il ne reçoit que des `Constats`, une `Recommandation` et les
réponses éventuelles du questionnaire —, et c'est ce qui le rend éprouvable sur
des constats fabriqués, sans projet réel ni fournisseur de modèle.

## Les quatre règles qui décident

1. **Rien sans son endroit.** Chaque rôle porte la `Piece` du projet qui le fait
   exister — le fichier lu, pas une phrase. C'est la règle de la recommandation
   d'outillage (#1030), et elle vaut ici pour la même raison : une équipe se
   conteste en ouvrant les fichiers qui l'ont désignée.
2. **Ce qui n'est pas proposé est nommé, avec sa raison.** `ecartes` porte les
   rôles qu'aucun constat ne justifie *et* l'orchestrateur, qui n'est jamais un
   membre de l'équipe (docs/37 §4.2). Sans cette liste, « pas de rôle base de
   données » se lirait comme une défaillance de Maestro plutôt que comme un fait
   du projet.
3. **Chaque autorisation porte sa raison, et le cran `auto` se dérive du
   projet.** C'est le second critère du ticket (#716, docs/37 §4.3) : Maestro
   *rédige* la politique, l'utilisateur la *décide* (#1040). Un cran `auto`
   proposé sans sa raison serait une décision prise sans personne.
4. **Rien n'est créé.** Aucun dépôt n'est ouvert en écriture ici, et
   `PropositionEquipe.to_dict()` le dit en toutes lettres.

## Ce que ce module ne décide pas

**Le texte des playbooks.** Il pose le playbook du **gabarit** et l'`intention`
qui servira à en écrire un pour ce projet ; l'écriture est la mécanique de #257
(`GenerateurDefinitionAgent`), appelée par `maestro.controltower.equipe` — la
seule couche qui connaisse un fournisseur de modèle. Un rôle dont la génération
échoue garde donc le playbook de son gabarit, **et le dit**
(`playbook_origine`) : une équipe entière perdue parce qu'un quota est épuisé
serait une réponse bien pire qu'un playbook générique annoncé comme tel.

⚠ Ce que ce module doit en revanche à #257, c'est de lui dire **sous quel régime
le rôle exécute** : l'`intention` porte le cran proposé pour l'outil d'exécution
(#1102). Un playbook écrit sans lui faisait d'une commande à approuver le premier
geste de l'agent, si bien qu'une équipe validée telle quelle attendait un humain
dès sa première tâche. Le fait est ici, la règle qu'on en tire est dans le cadre
de #257 — jamais les deux.

**Le modèle de chaque rôle.** L'analyse d'un projet ne dit rien du modèle avec
lequel un rôle doit travailler. Une fiche sans réglage prend le modèle par
défaut des exécutants, et c'est exactement ce qu'on veut dire.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime

from maestro.agents.fiche_outillee import profil_outille
from maestro.decideur import Decideur
from maestro.equipe.gabarits import (
    GABARITS,
    RAISON_UNE_INSTANCE,
    Gabarit,
    Justification,
)
from maestro.equipe.manque import RoleManquant
from maestro.equipe.modele import (
    ORIGINE_PLAYBOOK_GABARIT,
    ORIGINES_PLAYBOOK,
    AutorisationProposee,
    PropositionEquipe,
    RoleEcarte,
    RolePropose,
    SkillBranche,
    nouvel_id,
)
from maestro.outillage.modele import Constats, Entree, Recommandation
from maestro.outillage.questionnaire import Choix
from maestro.outillage.recommandation import SKILL_PAR_USAGE

#: L'orchestrateur, écarté **nommément** de toute équipe. Il n'a pas de gabarit
#: (`maestro.equipe.gabarits`) et il n'en aura pas : c'est Maestro, et c'est lui
#: qui recrute (docs/37 §4.2). L'écarter en silence laisserait croire qu'aucun
#: projet n'en a — alors que tout projet en a un, et qu'il n'est simplement pas
#: recruté.
ECARTE_ORCHESTRATEUR = RoleEcarte(
    nom="orchestrateur",
    role="Orchestrateur",
    raison=(
        "l'orchestrateur n'est pas un membre de l'équipe : c'est Maestro, présent "
        "dans tout projet, et c'est lui qui recrute. Il n'y a donc rien à créer "
        "pour lui, ni playbook ni autorisations de projet"
    ),
)

#: L'outil dont le cran se **dérive du projet** plutôt que d'être posé. C'est le
#: seul de `DEFAULT_TOOLS` qui exécute quelque chose, donc le seul dont la
#: question « faut-il arbitrer chaque appel ? » se pose vraiment — et c'est aussi
#: le seul que les cinq politiques livrées avec le dépôt (`core/permissions/`)
#: citent toutes.
OUTIL_EXECUTION = "Bash"

#: L'origine d'une commande que le **projet déclare** (par opposition à la
#: convention de son gestionnaire). Importée sous ce nom pour qu'on lise, dans
#: `_crans_execution`, ce qui la rend décidable d'avance : quelqu'un l'a écrite
#: dans le projet, et l'analyse l'y a lue.
ORIGINE_DECLAREE = "declaree"


def proposer_equipe(
    constats: Constats,
    recommandation: Recommandation,
    *,
    projet_id: str = "",
    choix: Sequence[Choix] = (),
    noms_pris: Sequence[str] = (),
    source: Mapping[str, object] | None = None,
) -> PropositionEquipe:
    """L'équipe que ce projet appelle — proposée, jamais créée.

    `constats` et `recommandation` viennent de la **même** paire que l'outillage
    (#1030 sur un projet existant, #1031 sur un projet neuf) : il n'y a donc pas
    deux chemins de « ce qu'il faut à ce projet » à tenir d'accord, et une équipe
    dérivée de réponses branche les mêmes skills qu'une équipe dérivée d'une
    analyse.

    `choix` n'est utilisé que pour ce que les constats ne portent pas — la
    **nature** du projet, qui dit qu'une application web aura des écrans avant
    qu'aucun fichier ne l'ait prouvé. Vide sur un projet analysé, et c'est le cas
    nominal.

    `noms_pris` sont les noms qu'une fiche ne peut pas porter dans ce projet : le
    nom proposé est rendu libre avant de sortir, pour qu'une validation (#1040)
    ne tombe pas sur une collision dont l'utilisateur n'est pas l'auteur.

    `source` est le fragment de provenance du manifeste d'outillage (docs/38
    §4.1), repris tel quel — jamais réécrit.
    """
    reponses = {c.cle: c.valeur for c in choix}
    skills = _skills_recommandes(recommandation)
    pris = [*noms_pris]
    roles: list[RolePropose] = []
    ecartes: list[RoleEcarte] = [ECARTE_ORCHESTRATEUR]
    for gabarit in GABARITS:
        justification = gabarit.besoin(constats, reponses)
        if justification is None:
            ecartes.append(_ecarte(gabarit, constats))
            continue
        nom = _nom_libre(gabarit.nom, pris)
        pris.append(nom)
        roles.append(_role(gabarit, nom, justification, constats, skills))
    return PropositionEquipe(
        id=nouvel_id(),
        projet_id=projet_id,
        faite_le=_maintenant(),
        resume=_resume(roles, ecartes),
        roles=tuple(roles),
        ecartes=tuple(ecartes),
        source=dict(source) if source is not None else None,
    )


def proposer_renfort(
    constats: Constats,
    recommandation: Recommandation,
    manque: RoleManquant,
    raison: str,
    *,
    projet_id: str = "",
    noms_pris: Sequence[str] = (),
    source: Mapping[str, object] | None = None,
) -> PropositionEquipe:
    """Le **seul rôle** qui manque à une équipe pour exécuter un plan (#1227).

    Le pendant de `proposer_equipe` sur l'autre question. Là-bas : *quelle équipe
    ce projet appelle-t-il ?*, et la réponse se lit dans les constats. Ici : *qui
    manque à l'équipe qu'il a déjà ?*, et la réponse a été calculée ailleurs
    (`maestro.equipe.manque.manque_au_plan`) — sur le **plan**, pas sur le projet.

    D'où le seul écart de fond entre les deux : la `raison` est **donnée** au lieu
    d'être dérivée d'un constat. C'est la règle 1 du module tenue, pas contournée
    — « rien sans son endroit » demande que le rôle porte ce qui le fait exister,
    et ce qui le fait exister est ici le plan qu'on vient de payer, que l'appelant
    est seul à connaître. La `Piece` reste `None` pour la même raison : aucun
    fichier du projet ne désigne ce rôle, et en inventer un serait pire que de
    n'en pas avoir (cf. `Justification`).

    Tout le reste est **identique** : mêmes skills branchés par usage, mêmes
    autorisations dérivées du projet avec leur raison, mêmes instances, même
    intention pour #257, même nom rendu libre. C'est ce qui fait qu'un rôle
    recruté en cours de run est un rôle de plein droit, et pas une fiche au
    rabais.

    `ecartes` porte l'orchestrateur **et** les rôles que l'équipe n'appelle pas
    ici : ce ne serait pas la même liste que celle d'une proposition d'équipe
    entière (elle ne dirait rien des rôles déjà en place), et la servir
    laisserait croire qu'ils ont été examinés. Seul l'orchestrateur y reste, parce
    que sa raison — *ce n'est pas un membre de l'équipe* — est vraie quel que soit
    le moment (docs/37 §4.2).

    Lève `ValueError` sur un manque sans gabarit : il n'y a alors aucun poste à
    proposer, et l'appelant le sait avant d'appeler (`ManqueAuPlan.recrutable`).
    Proposer quelque chose ici reviendrait à inventer un rôle.
    """
    gabarit = _gabarit_de(manque)
    skills = _skills_recommandes(recommandation)
    nom = _nom_libre(gabarit.nom, noms_pris)
    role = _role(
        gabarit,
        nom,
        Justification(raison=raison),
        constats,
        skills,
    )
    return PropositionEquipe(
        id=nouvel_id(),
        projet_id=projet_id,
        faite_le=_maintenant(),
        resume=_resume((role,), (ECARTE_ORCHESTRATEUR,)),
        roles=(role,),
        ecartes=(ECARTE_ORCHESTRATEUR,),
        source=dict(source) if source is not None else None,
    )


def _gabarit_de(manque: RoleManquant) -> Gabarit:
    """Le gabarit que `manque` désigne — lève si le manque n'en nomme aucun.

    Résolu par son **nom de gabarit** (`Gabarit.nom`, le slug proposé), qui est
    exactement ce que `role_manquant` y a mis : une seconde table d'équivalence
    serait une table de plus à tenir d'accord avec l'ordre du catalogue.
    """
    if manque.gabarit is None:
        raise ValueError(
            f"aucun gabarit ne couvre {', '.join(manque.competences)} : il n'y a "
            "pas de rôle à proposer, et en inventer un le ferait lire comme un "
            "rôle du catalogue."
        )
    for gabarit in GABARITS:
        if gabarit.nom == manque.gabarit:
            return gabarit
    raise ValueError(
        f"gabarit inconnu : {manque.gabarit!r} (attendus : "
        f"{', '.join(g.nom for g in GABARITS)})."
    )


def _role(
    gabarit: Gabarit,
    nom: str,
    justification: Justification,
    constats: Constats,
    skills: Mapping[str, Entree],
) -> RolePropose:
    """Le rôle proposé pour ce gabarit : ses skills, ses instances, ses autorisations."""
    branches = _skills_du_role(gabarit, skills)
    autorisations = _autorisations(gabarit, constats)
    instances, raison_instances = (
        gabarit.instances_selon(constats)
        if gabarit.instances_selon is not None
        else (1, RAISON_UNE_INSTANCE)
    )
    return RolePropose(
        nom=nom,
        role=gabarit.role,
        gabarit=gabarit.gabarit,
        competences=gabarit.competences,
        raison=justification.raison,
        justification=justification.piece,
        instances=instances,
        raison_instances=raison_instances,
        playbook=gabarit.playbook_de_repli(),
        playbook_origine=ORIGINE_PLAYBOOK_GABARIT,
        playbook_raison=(
            f"playbook du gabarit « {gabarit.gabarit} » : sa rédaction pour ce projet "
            "n'a pas encore eu lieu"
        ),
        intention=_intention(gabarit, constats, branches, autorisations),
        outils=profil_outille(gabarit.agent).outils,
        skills=branches,
        autorisations=autorisations,
    )


def _skills_recommandes(recommandation: Recommandation) -> dict[str, Entree]:
    """Les entrées de type `skill` de l'outillage, indexées par nom.

    Les autres types (`instructions`, `pont`, `script`) n'en sont pas : un rôle
    ne « branche » pas un `AGENTS.md`, il le lit comme tout le monde
    (`outillage_du_projet`, #1032), et un script est déjà appelé par le skill
    qui l'enveloppe.
    """
    return {
        entree.nom: entree for entree in recommandation.entrees if entree.type == "skill"
    }


def _skills_du_role(
    gabarit: Gabarit, skills: Mapping[str, Entree]
) -> tuple[SkillBranche, ...]:
    """Les skills du projet que ce rôle branche — par usage, jamais par nom.

    Un usage que la recommandation a écarté (aucune commande constatée) ne
    produit **rien** : le rôle n'est pas branché sur un skill qui n'existera
    pas, et c'est la recommandation qui porte déjà la raison de son absence.
    """
    branches: list[SkillBranche] = []
    for usage, raison in gabarit.usages:
        nom_skill = SKILL_PAR_USAGE.get(usage, ("", ""))[0]
        entree = skills.get(nom_skill)
        if entree is None:
            continue
        branches.append(
            SkillBranche(
                nom=entree.nom,
                chemin=entree.chemin,
                etat=entree.etat,
                raison=raison,
                commandes=entree.commandes,
            )
        )
    return tuple(branches)


def _autorisations(
    gabarit: Gabarit, constats: Constats
) -> tuple[AutorisationProposee, ...]:
    """Les autorisations proposées pour ce rôle — chacune avec sa raison.

    **Aucune entrée `allow`, et c'est une décision.** Une liste `allow` non vide
    est *fermée* (`PolitiqueOutils`) : la remplir avec les outils du profil
    refuserait tout le reste, à commencer par les canaux in-process de Maestro
    (`mcp__maestro__…` : poser une question (#1023), consigner une décision
    (#1024), signaler un blocage, écrire à un pair) — que l'analyse d'un projet
    ne peut pas énumérer, et dont le refus rendrait l'agent muet là où le jalon
    vient précisément de lui donner la parole. Les cinq politiques livrées avec
    le dépôt (`core/permissions/`) ouvrent toutes leur `allow` pour cette
    raison ; ce que le rôle tient se lit dans `RolePropose.outils`, qui n'est
    pas une politique.

    Reste donc la seule question qu'un projet permette de trancher : l'outil
    d'**exécution**. C'est aussi la seule qui se pose — les autres outils du
    profil lisent et écrivent dans un espace de travail déjà borné par la
    frontière d'écriture (#839).
    """
    outils = profil_outille(gabarit.agent).outils
    if OUTIL_EXECUTION not in outils:
        return ()
    return (_cran_execution(gabarit, constats),)


def _cran_execution(gabarit: Gabarit, constats: Constats) -> AutorisationProposee:
    """Le cran proposé pour l'exécution de commandes — et **d'où il sort**.

    Deux cas, et c'est le **projet** qui les sépare, jamais le rôle :

    - le projet **déclare** les commandes que ce rôle joue : `auto`, et la raison
      nomme les fichiers où elles ont été lues. `auto` n'est pas « la machine
      approuve » : l'appel passe **en étant tracé**, et la décision est la vôtre,
      prise à froid (`maestro.decideur`, #716) ;
    - aucune commande de ce rôle n'a été lue : `humain`. Ce qu'il lancerait, il
      le composerait lui-même, et personne n'a rien décidé d'avance là-dessus.
      La raison dit alors que **lire** n'attend personne (#1197) — le renvoi au
      ticket reste ici, jamais dans la raison servie, qui s'adresse à quelqu'un
      qui n'a pas ce dépôt sous les yeux (`tests/test_registre_de_langue.py`).

    ⚠ La raison le dit, parce que c'est vrai : `auto` **ne borne pas** les
    commandes à celles qui ont été lues — un cran porte sur un outil, pas sur
    ses arguments. C'est une autorisation d'exécuter, donnée d'avance et
    révocable, et la taire ferait lire la proposition pour plus prudente qu'elle
    n'est. C'est aussi pourquoi aucun rôle ne reçoit ici un cran plus fermé au
    nom de son métier : ce qu'un rôle ne doit pas *faire* vit dans son playbook,
    qui sait distinguer un `SELECT` d'un `DROP` — un cran ne le sait pas, et le
    poser bloquerait `ls` sans empêcher quoi que ce soit.
    """
    declarees = _commandes_declarees(gabarit, constats)
    if not declarees:
        return AutorisationProposee(
            outil=OUTIL_EXECUTION,
            cran="ask",
            decideur=Decideur.HUMAIN,
            raison=(
                "aucune commande de ce rôle n'a été lue dans le projet : ce qu'il "
                "lancerait, il le composerait lui-même. Personne n'a rien décidé "
                "d'avance là-dessus, donc une personne tranche chaque appel. Cela "
                "ne l'empêche pas de commencer : **lire** son projet et son "
                "outillage — lister, chercher, ouvrir un fichier — n'attend "
                "personne, au shell comme avec ses outils de lecture, parce que "
                "lire n'est pas exécuter"
            ),
        )
    return AutorisationProposee(
        outil=OUTIL_EXECUTION,
        cran="ask",
        decideur=Decideur.AUTO,
        raison=(
            "les commandes de ce rôle sont écrites dans le projet et l'analyse les y "
            f"a lues ({', '.join(declarees)}). Le cran « auto » les laisse passer "
            "**en les traçant**, au lieu de vous faire arbitrer chaque exécution — "
            "c'est vous qui le décidez ici, à froid, et cela se révoque. ⚠ Il ne les "
            "borne pas à celles-là : un cran porte sur un outil, pas sur ses arguments"
        ),
    )


def _commandes_declarees(gabarit: Gabarit, constats: Constats) -> tuple[str, ...]:
    """Les endroits du projet où les commandes de ce rôle ont été **lues**.

    Deux gisements, et les deux comptent : une commande que le projet *déclare*
    (`origine == "declaree"` — un script de `package.json`, une cible de
    `Makefile`) et un **script** du projet pour cet usage (docs/38 §3.4). Une
    commande de `convention`, elle, n'en est pas : c'est la commande de l'outil
    détecté, que le projet n'écrit nulle part — la retenir ferait passer une
    supposition pour une lecture, l'exact travers qu'`ORIGINES_COMMANDE` existe
    pour empêcher.

    ⚠ **Une commande que Maestro a lui-même écrite dans un skill du projet n'en
    est pas une non plus**, et #1102 l'a pesé plutôt que supposé. Elle est bien
    *écrite dans le projet*, mais elle n'y a été décidée par personne : elle
    vient de la même convention, recopiée un cran plus loin. La compter ferait
    de Maestro l'auteur de sa propre autorisation, quand docs/38 §5.1 dit que
    *ce qui autorise un outil n'est pas l'origine du fichier, c'est le geste
    d'une personne* — et le cran obtenu ne serait pas borné à cette commande, un
    cran portant sur un outil et non sur ses arguments. Ce que #1102 corrige est
    ailleurs, et sans toucher à ce cran : un agent n'a pas besoin d'exécuter pour
    **lire** son outillage.

    ⚠ #1102 avait confié cette correction au **texte** du playbook, et #1197 a
    mesuré qu'elle n'avait pas suivi sur un vrai run : l'agent lisait au shell
    quand même. Elle vit désormais dans l'exécution (`maestro.lecture`), où une
    commande qui ne fait que lire ne suspend plus rien. Ce cran-ci n'a toujours
    pas bougé, et c'est le même motif : *ce qui autorise un outil est le geste
    d'une personne*.
    """
    usages = {usage for usage, _ in gabarit.usages}
    endroits: list[str] = []
    for commande in constats.commandes:
        if commande.usage in usages and commande.origine == ORIGINE_DECLAREE:
            endroits.append(
                f"{commande.chemin} ({commande.extrait})"
                if commande.extrait
                else commande.chemin
            )
    endroits.extend(
        piece.chemin for piece in constats.dossier_scripts.scripts if piece.role in usages
    )
    return tuple(dict.fromkeys(endroits))


#: Ce que l'intention dit du **régime d'exécution** du rôle, décideur par
#: décideur (#1102). Un **fait**, jamais une consigne : ce que le playbook doit
#: en faire est écrit dans le cadre de #257
#: (`maestro.controltower.generation_agent._CADRE_GENERATION`), et le redire ici
#: ferait deux consignes à tenir d'accord — la frontière que `_intention` pose
#: déjà pour tout le reste.
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


def _intention(
    gabarit: Gabarit,
    constats: Constats,
    skills: Sequence[SkillBranche],
    autorisations: Sequence[AutorisationProposee],
) -> str:
    """La phrase d'où #257 écrira le playbook de ce rôle — *pour ce projet*.

    Une phrase, comme l'entrée de #257 : le rôle, ce que le projet est, les
    skills qu'il branche, et **sous quel régime il exécute**. Rien de ce que le
    playbook doit dire n'y est écrit — c'est le cadre de #257 qui le demande, et
    le redire ici en ferait une seconde consigne à tenir d'accord.

    Le régime d'exécution y est entré par #1102, et c'est le premier fait qui ne
    vienne pas du projet mais de la **proposition elle-même** : un playbook écrit
    dans l'ignorance du cran de son agent ordonnait, en premier geste, une
    commande qu'une personne devait approuver — la tâche commençait donc par
    attendre quelqu'un. L'intention le dit, le cadre de #257 en tire la règle.

    Elle vit **dans la proposition** et pas seulement dans l'appel : c'est elle
    qu'on relit pour juger un playbook qu'on trouve à côté de la plaque, et un
    playbook sans la phrase dont il est né ne se juge pas (`DefinitionProposee`
    garde la sienne pour la même raison).
    """
    langages = ", ".join(langage.nom for langage in constats.langages[:3])
    noms = ", ".join(skill.nom for skill in skills)
    morceaux = [
        f"Un agent « {gabarit.role} » pour un projet",
        f" écrit en {langages}" if langages else "",
        ". Il travaille dans le dossier du projet",
        f" et appelle les skills du projet : {noms}" if noms else "",
        ".",
        _regime_execution(autorisations),
    ]
    return "".join(morceaux)


def _regime_execution(autorisations: Sequence[AutorisationProposee]) -> str:
    """Ce que l'intention dit du cran proposé pour l'outil d'exécution.

    Vide quand le rôle n'a pas cet outil dans les mains, ou quand son cran ne
    désigne aucun décideur (`allow`, `deny` : personne ne tranche) : une phrase
    sur un régime qui ne s'applique pas serait pire que son absence.
    """
    decideur = next(
        (
            autorisation.decideur_effectif
            for autorisation in autorisations
            if autorisation.outil == OUTIL_EXECUTION
        ),
        None,
    )
    if decideur is None:
        return ""
    return REGIME_EXECUTION.get(decideur, "")


def _ecarte(gabarit: Gabarit, constats: Constats) -> RoleEcarte:
    """Le rôle non proposé, avec ce que le projet n'a pas montré.

    La raison nomme **ce qui manque**, pas ce que Maestro n'a pas fait : sans
    cela, « pas de rôle base de données » se lirait comme un oubli de l'analyse
    plutôt que comme un fait du projet. Elle rappelle aussi que l'analyse a des
    bornes — un projet tronqué peut porter ce que l'analyse n'a pas vu.
    """
    return RoleEcarte(
        nom=gabarit.nom,
        role=gabarit.role,
        raison=(
            f"rien dans les bornes de l'analyse ne justifie un rôle « {gabarit.role} » : "
            f"{_manque(gabarit)}. Vous pouvez l'ajouter à la validation si le projet en "
            "a besoin"
        ),
    )


def _manque(gabarit: Gabarit) -> str:
    """Ce qui, concrètement, n'a pas été trouvé pour ce gabarit.

    Dérivé du gabarit plutôt que redit par rôle : la règle de besoin est la
    source, et une phrase écrite à côté d'elle finirait par décrire une règle
    qu'on aurait changée.
    """
    return MANQUE_PAR_GABARIT.get(
        gabarit.gabarit, f"aucun constat ne désigne « {gabarit.role} »"
    )


#: Ce que chaque règle de besoin cherchait, dit en clair. Indexé par le nom du
#: **gabarit** (et non du rôle proposé) parce que c'est la règle qui est en
#: cause, pas le nom de la fiche qu'on aurait créée.
MANQUE_PAR_GABARIT: dict[str, str] = {
    "bdd": (
        "aucun fichier SQL n'a été vu, donc ni schéma ni migration à tenir"
    ),
    "devops": (
        "ni intégration continue, ni commande de construction, ni forge constatée"
    ),
    "designer": (
        "aucune feuille de style ni composant d'écran constaté, et le projet n'a pas "
        "été déclaré comme une application web"
    ),
    "qa": (
        "aucune commande de test, de style, de format ni de types constatée"
    ),
}

def _nom_libre(base: str, pris: Sequence[str]) -> str:
    """Le nom de fiche `base`, suffixé tant qu'il est déjà pris (`dev`, `dev-2`…).

    La collision n'est **pas** une validation : le dépôt reste seul juge à la
    création (#1040), et un agent créé entre cette réponse et la validation la
    rendrait de toute façon caduque. Ce qu'on évite est le cas fréquent et
    évitable — proposer « dev » à un projet qui en a déjà un.
    """
    occupes = {nom.casefold() for nom in pris}
    if base.casefold() not in occupes:
        return base
    for suite in range(2, 100):
        candidat = f"{base}-{suite}"
        if candidat.casefold() not in occupes:
            return candidat
    return base


def _resume(roles: Sequence[RolePropose], ecartes: Sequence[RoleEcarte]) -> str:
    """La phrase qu'on relira à côté d'une équipe dont on se demande d'où elle sort.

    Une ligne, et seulement ce qui a été proposé — le pendant de
    `maestro.outillage.analyse.resume`. Les écartés y sont **comptés** et non
    nommés : leur liste est juste en dessous, avec leurs raisons, et la recopier
    ferait du résumé un second inventaire.
    """
    if not roles:
        return "aucun rôle proposé"
    nommes = ", ".join(
        f"{role.role} ×{role.instances}" if role.instances > 1 else role.role
        for role in roles
    )
    instances = sum(role.instances for role in roles)
    return (
        f"{nommes} — {len(roles)} rôle(s), {instances} instance(s) ; "
        f"{len(ecartes)} rôle(s) écarté(s)"
    )


def avec_playbook(
    role: RolePropose, playbook: str, *, origine: str, raison: str
) -> RolePropose:
    """Le même rôle, son playbook remplacé — le seul chemin de ce remplacement.

    Pur, parce que la rédaction ne l'est pas : c'est
    `maestro.controltower.equipe` qui appelle la mécanique de #257, et cette
    couche-là est la seule à connaître un fournisseur de modèle. Ce verbe est ce
    qu'elle rapporte ici, et il **valide l'origine** — une origine hors de
    `ORIGINES_PLAYBOOK` ferait servir une proposition dont on ne saurait pas dire
    d'où vient le playbook, c'est-à-dire précisément l'information que ce champ
    existe pour porter.
    """
    if origine not in ORIGINES_PLAYBOOK:
        raise ValueError(
            f"origine de playbook inconnue : {origine!r} "
            f"(attendues : {', '.join(sorted(ORIGINES_PLAYBOOK))})."
        )
    if not playbook.strip():
        raise ValueError(
            f"playbook vide proposé pour le rôle {role.nom!r} : un prompt système "
            "vide n'a aucun sens."
        )
    return replace(
        role,
        playbook=playbook,
        playbook_origine=origine,
        playbook_raison=raison,
    )


def _maintenant() -> str:
    """L'horodatage d'une proposition (ISO 8601, UTC, à la seconde)."""
    return datetime.now(tz=UTC).isoformat(timespec="seconds")

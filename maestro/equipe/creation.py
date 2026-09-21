"""De l'équipe **validée** aux trois artefacts d'un agent de projet (#1040).

Le pendant de [`maestro.equipe.proposition`](./proposition.py), et sa symétrie
est le sujet : la proposition part des constats et rend une équipe **que
personne n'a encore acceptée** ; ce module part de l'équipe **qu'on a acceptée**
et rend ce qu'il faut écrire pour qu'elle existe. Comme son voisin, il est pur —
il ne touche ni au disque, ni à la forge, ni à aucun dépôt d'agents. Ce qui
écrit est `maestro.controltower.equipe`, seule couche qui connaisse un projet
déclaré et ses six dépôts.

## Ce que « créer un rôle » veut dire, et où chaque morceau va

Un rôle validé se range dans **trois** des six dépôts de `ConfigurationAgents`
(#1038), et pas ailleurs :

| Ce que l'utilisateur a validé | Ce qu'on écrit | Dépôt |
| --- | --- | --- |
| nom, rôle, compétences, **playbook** | `AgentDefinition` | `agents` |
| les **autorisations** relues une à une | `PolitiqueOutils` | `permissions` |
| le nombre d'**instances** | `CapaciteAgent` | `capacites` |

Les trois autres — surcharges de réglages (#259), serveurs MCP (#104) — ne
reçoivent rien : l'analyse d'un projet ne dit rien du modèle avec lequel un rôle
doit travailler (`maestro.equipe.modele`), et aucun serveur MCP ne se déduit
d'un projet. Une fiche sans réglage prend le modèle par défaut des exécutants, et
c'est exactement ce qu'on veut dire.

## Les skills branchés vivent dans le playbook, et c'est une décision

Le critère du ticket demande des agents créés « avec **leurs skills branchés** ».
Il n'existe pas de dépôt « skills par agent », et il n'en est pas créé un ici :
l'outillage d'un projet est transmis **au projet entier** par le message de
tâche (#1032, `maestro.outillage.contexte`), bornes du manifeste comprises. Ce
qui manque à un agent n'est donc pas l'accès aux skills — il les a tous — mais de
savoir **lesquels sont les siens**. C'est une consigne, donc cela vit dans son
playbook : `playbook_branche()` y ajoute une section qui les nomme, avec leur
chemin et les commandes qu'ils enveloppent.

Trois raisons de préférer cela à un sixième dépôt :

1. **c'est relisible et modifiable là où l'équipe se revoit** — le playbook d'un
   agent de projet s'édite depuis ses écrans (#72, #1038), donc rebrancher un
   skill est un geste qui existe déjà ;
2. **rien ne se périme en silence** : un skill retiré du projet laisse une ligne
   dans un playbook qu'on relit, là où une table d'association pointerait un
   chemin mort que personne n'ouvrirait jamais ;
3. **aucune frontière nouvelle.** Ajouter un dépôt obligerait l'exécuteur à le
   lire, donc à filtrer l'outillage par agent — c'est-à-dire à défaire
   `outillage_du_projet`, qui borne déjà ce qui entre (docs/38 §5).

La section est **idempotente** : la reposer remplace la précédente au lieu de
l'empiler, pour qu'une équipe re-validée ne laisse pas deux inventaires
contradictoires dans le même prompt.

## Ce qui se vérifie **avant** d'écrire quoi que ce soit

Une équipe s'écrit dans trois dépôts × N rôles : il n'y a pas de transaction, et
un système de fichiers n'en offrira pas. Le recours est donc de **tout vérifier
avant de rien écrire** (`refus_de`), pour que le cas courant — un nom déjà pris,
une politique mal formée — se solde par un refus motivé et **aucune écriture**,
plutôt que par une demi-équipe. C'est la règle qu'applique déjà
`PermissionStore.ecrire` à l'échelle d'un fichier (« un dépôt qui n'écrirait
qu'à moitié une politique de garde-fou serait pire que pas d'écriture du tout »),
portée ici à l'échelle de l'équipe.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from maestro.agents.capacity import CapaciteAgent
from maestro.agents.permissions import PolitiqueOutils
from maestro.agents.store import (
    NOMS_RESERVES,
    AgentDefinition,
    definition_validee,
)

#: Le titre de la section que `playbook_branche()` pose dans le playbook. Il sert
#: aussi d'**ancre** : c'est par lui qu'une section déjà posée se retrouve pour
#: être remplacée, et c'est pourquoi il ne se reformule pas à la légère — deux
#: orthographes feraient deux sections.
TITRE_SKILLS = "## Les skills du projet qui sont les vôtres"

#: Ce que la section dit avant de lister. Une phrase, parce que le playbook d'un
#: agent n'est pas une documentation : elle rappelle que les skills sont ceux du
#: **projet** (l'agent les reçoit tous, #1032) et que cette liste dit lesquels
#: relèvent de son rôle.
INTRO_SKILLS = (
    "L'outillage du projet vous est transmis en entier ; ceux-ci sont ceux que "
    "votre rôle branche. Servez-vous-en plutôt que de réinventer la commande."
)

#: Ce qu'on écrit pour un rôle qui ne branche **aucun** skill. La section reste
#: posée, et c'est voulu : « ce rôle n'en a aucun » et « personne n'a rangé ses
#: skills » sont deux faits différents, et seul le premier est une décision.
AUCUN_SKILL = (
    "Aucun skill du projet n'est rattaché à votre rôle : l'outillage du projet "
    "n'en a recommandé aucun pour ce que vous faites."
)

#: Le plafond d'instances qu'une **création** accepte. Décision, pas mesure — au
#: même titre que le plafond de trois que la *proposition* pose
#: (`INSTANCES_MAX_PROPOSEES`), et plus haut que lui parce que ce n'est plus
#: Maestro qui propose mais une personne qui décide. Il existe pour qu'une faute
#: de frappe ne règle pas une capacité à 400 ; il se relève dans les écrans de
#: capacité (#86), qui ne sont pas bornés.
INSTANCES_MAX_CREEES = 20


@dataclass(frozen=True)
class SkillRetenu:
    """Un skill de l'outillage que ce rôle branche — repris de la proposition.

    `chemin` et `commandes` viennent de l'`Entree` d'outillage qui l'a
    recommandé (`maestro.outillage.modele`), au caractère près : le playbook
    nomme un chemin qui existe dans le projet, jamais une reformulation.
    """

    nom: str
    chemin: str = ""
    commandes: tuple[str, ...] = ()


@dataclass(frozen=True)
class RoleValide:
    """Un rôle que l'utilisateur a **gardé**, tel qu'il l'a relu et ajusté.

    C'est délibérément la forme que l'API a **servie** (`RolePropose.to_dict()`)
    et non une re-dérivation : ce qui est créé doit être exactement ce qui a été
    montré. Deux champs le disent mieux que tout le reste :

    - `playbook` est celui qu'on a lu à l'écran — écrit pour ce projet par la
      mécanique de #257, ou celui du gabarit quand la rédaction n'a pas abouti.
      Le rejouer à la validation rendrait un **autre** texte, donc ferait créer
      un agent que personne n'a validé ;
    - `politique` est la `PolitiqueOutils` que `RolePropose.politique()` a
      rendue, rapportée telle quelle. C'est le **seul** chemin de la traduction
      « autorisations proposées → politique » (`maestro.equipe.modele`), et le
      garder unique est ce qui garantit qu'un cran `auto` lu avec sa raison est
      le cran qui sera écrit. `None` : le rôle n'a aucune autorisation à poser,
      et **aucun fichier de politique n'est écrit** — ce qui n'est pas la même
      chose qu'une politique vide, qui serait une politique.

    `instances` est le seul champ que l'écran **ajuste** librement. `gabarit` ne
    sert qu'à la trace (de quel rôle figé celui-ci descend, docs/37 §2.1).
    """

    nom: str
    role: str
    competences: tuple[str, ...]
    playbook: str
    instances: int = 1
    gabarit: str = ""
    skills: tuple[SkillRetenu, ...] = ()
    politique: PolitiqueOutils | None = None


@dataclass(frozen=True)
class AgentCree:
    """Ce qu'une validation a écrit pour un rôle — de quoi le relire, pas de quoi le refaire.

    Sert la réponse de l'API : l'écran annonce ce qui existe désormais dans le
    projet, et les écrans d'agents prennent la suite (#1038).
    """

    nom: str
    role: str
    instances: int
    gabarit: str = ""
    skills: tuple[str, ...] = ()
    politique: PolitiqueOutils | None = None

    def to_dict(self) -> dict[str, Any]:
        """L'agent créé en JSON."""
        return {
            "nom": self.nom,
            "role": self.role,
            "instances": self.instances,
            "gabarit": self.gabarit,
            "skills": list(self.skills),
            "politique": None if self.politique is None else self.politique.to_dict(),
        }


@dataclass(frozen=True)
class Refus:
    """Un rôle qu'on refuse de créer, **avant** toute écriture, et pourquoi.

    `nom` peut être vide : un rôle sans nom est refusé lui aussi, et le dire
    « pour le rôle '' » vaut mieux que de taire la ligne fautive.
    """

    nom: str
    raison: str

    def to_dict(self) -> dict[str, Any]:
        """Le refus en JSON."""
        return {"nom": self.nom, "raison": self.raison}


@dataclass(frozen=True)
class EquipeCreee:
    """Le rapport d'une validation — ce qui a été créé, et sous quelle proposition.

    `cree` est **vrai** dès que l'équipe est passée : c'est le pendant du `cree:
    False` que la proposition sert (`PropositionEquipe.to_dict`), et les deux se
    lisent ensemble — la première dit qu'elle n'a rien fait, celui-ci dit ce qui
    a été fait.
    """

    projet_id: str
    proposition_id: str = ""
    agents: tuple[AgentCree, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        """Le rapport entier en JSON — la forme servie par l'API."""
        return {
            "projet_id": self.projet_id,
            "proposition_id": self.proposition_id,
            "cree": True,
            "agents": [agent.to_dict() for agent in self.agents],
            "instances_total": sum(agent.instances for agent in self.agents),
        }


def refus_de(roles: Sequence[RoleValide], noms_pris: Iterable[str] = ()) -> tuple[Refus, ...]:
    """Ce qui empêche d'écrire cette équipe — **tout**, avant d'en écrire un bout.

    Cinq causes, et aucune n'est une surprise à la lecture d'un refus : un nom
    déjà pris dans ce projet (ou réservé partout), un doublon **dans la
    proposition elle-même** (deux lignes qui écriraient le même fichier), un
    nombre d'instances hors bornes, un playbook vide, et ce que les dépôts
    refuseraient de toute façon (nom hors slug, rôle vide, aucune compétence).

    Les deux dernières familles sont **déléguées** : `AgentDefinition` est passée
    au même validateur que celui du dépôt (`AgentStore.ecrire`) et la politique à
    celui de `PermissionStore`. Recopier leurs règles ici les ferait diverger, et
    c'est précisément la divergence qu'une pré-vérification doit éviter — une
    vérification plus permissive que l'écriture ne sert à rien, une plus stricte
    refuse ce qui passerait.

    Rend la liste **entière** plutôt que le premier refus : corriger une équipe
    de cinq rôles un refus à la fois serait cinq allers-retours.
    """
    pris = {nom for nom in noms_pris} | set(NOMS_RESERVES)
    vus: set[str] = set()
    refus: list[Refus] = []
    for role in roles:
        nom = role.nom.strip()
        if nom in vus:
            refus.append(
                Refus(nom, f"« {nom} » figure deux fois dans l'équipe validée.")
            )
            continue
        vus.add(nom)
        if nom in pris:
            refus.append(
                Refus(
                    nom,
                    f"« {nom} » est déjà pris dans ce projet (ou réservé par le "
                    "dépôt) : renommez le rôle, ou retirez-le de l'équipe.",
                )
            )
            continue
        if not 1 <= role.instances <= INSTANCES_MAX_CREEES:
            refus.append(
                Refus(
                    nom,
                    f"{role.instances} instance(s) pour « {nom} » : attendu entre 1 "
                    f"et {INSTANCES_MAX_CREEES}.",
                )
            )
            continue
        erreur = _erreur_de_depot(role)
        if erreur is not None:
            refus.append(Refus(nom, erreur))
    return tuple(refus)


def _erreur_de_depot(role: RoleValide) -> str | None:
    """Ce que les dépôts refuseraient — demandé **aux dépôts**, jamais réécrit.

    Une exception, et elle est ici parce que le dépôt ne peut pas la voir : un
    playbook **vide** passerait sa validation, `playbook_branche()` ayant déjà
    posé la section des skills par-dessus. On juge donc le playbook **validé**,
    celui qu'on a lu à l'écran, et non la fiche augmentée — sans quoi un rôle
    arriverait dans le projet avec, pour tout prompt système, l'inventaire de ses
    skills.
    """
    if not role.playbook.strip():
        return (
            f"playbook vide pour « {role.nom} » : un agent dont le prompt système "
            "se réduit à la liste de ses skills ne saurait pas quoi en faire."
        )
    try:
        definition(role)
    except ValueError as exc:
        return str(exc)
    if role.politique is not None:
        try:
            PolitiqueOutils.from_dict(role.politique.to_dict())
        except ValueError as exc:  # pragma: no cover - une politique déjà construite
            return str(exc)
    return None


def definition(role: RoleValide) -> AgentDefinition:
    """La fiche à persister — playbook **augmenté de ses skills branchés**.

    Ni modèle, ni fournisseur, ni effort : l'analyse d'un projet n'en dit rien,
    et les poser serait inventer (`maestro.equipe.modele`). La fiche prend donc
    les défauts des exécutants, et se règle ensuite depuis ses écrans.

    Lève `ValueError` — celle du dépôt, au mot près — si la fiche est invalide.
    """
    return definition_validee(
        AgentDefinition(
            nom=role.nom.strip(),
            role=role.role,
            competences=tuple(role.competences),
            playbook=playbook_branche(role),
        )
    )


def capacite(role: RoleValide) -> CapaciteAgent:
    """Le réglage de capacité : actif, et le nombre d'instances validé.

    `actif=True` sans condition — on vient de le recruter. Un agent qu'on ne veut
    pas voir travailler tout de suite se désactive depuis ses écrans (#86) ; le
    créer éteint n'aurait aucun sens à l'issue d'une validation d'équipe.
    """
    return CapaciteAgent(nom=role.nom.strip(), actif=True, instances=role.instances)


def playbook_branche(role: RoleValide) -> str:
    """Le playbook validé, sa section « skills » posée (ou reposée) en fin de document.

    Idempotent : une section déjà présente est **remplacée**, jamais empilée. La
    coupe se fait au titre (`TITRE_SKILLS`) et court jusqu'au prochain titre de
    même niveau, pour qu'un playbook qui aurait des sections après elle les garde
    — un playbook est un prompt système, pas un fichier qu'on tronque.
    """
    corps = _sans_section_skills(role.playbook).rstrip()
    return f"{corps}\n\n{_section_skills(role.skills)}\n"


def _section_skills(skills: Sequence[SkillRetenu]) -> str:
    """La section, telle qu'elle apparaît dans le prompt système de l'agent."""
    if not skills:
        return f"{TITRE_SKILLS}\n\n{AUCUN_SKILL}"
    lignes = [TITRE_SKILLS, "", INTRO_SKILLS, ""]
    for skill in skills:
        chemin = f" — `{skill.chemin}`" if skill.chemin else ""
        commandes = (
            f" (il enveloppe : {', '.join(f'`{c}`' for c in skill.commandes)})"
            if skill.commandes
            else ""
        )
        lignes.append(f"- **{skill.nom}**{chemin}{commandes}")
    return "\n".join(lignes)


def _sans_section_skills(playbook: str) -> str:
    """Le playbook privé de sa section « skills », s'il en portait une."""
    motif = re.compile(
        rf"^{re.escape(TITRE_SKILLS)}\s*$.*?(?=^## |\Z)",
        re.MULTILINE | re.DOTALL,
    )
    return motif.sub("", playbook)

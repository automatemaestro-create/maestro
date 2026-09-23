"""Les formes d'une équipe **proposée** pour un projet (#1039).

Inerte, au patron de [`maestro.outillage.modele`](../outillage/modele.py) : ce
module **décrit et sérialise**, il ne touche ni au disque ni à la forge ni à
aucun dépôt d'agents. Ce qui en déduit une équipe est ailleurs —
`maestro.equipe.gabarits` (les rôles que Maestro sait proposer et ce qui les
justifie) et `maestro.equipe.proposition` (la dérivation elle-même). C'est ce
qui rend les deux éprouvables sur des constats fabriqués, sans projet réel.

Quatre objets, et la frontière entre les deux premiers est celle à ne pas
confondre :

- **le rôle proposé** (`RolePropose`) — ce que Maestro propose de créer : sa
  raison, ses instances, son playbook, les skills du projet qu'il branche et ses
  autorisations. Chacun porte la **pièce** du projet qui le justifie, au même
  titre qu'une `Entree` d'outillage : une proposition sans son endroit serait
  invérifiable ;
- **le rôle écarté** (`RoleEcarte`) — ce que Maestro a choisi de **ne pas**
  proposer, avec sa raison. Sans cette liste, « pas de rôle base de données » se
  lirait comme un oubli de Maestro plutôt que comme un fait du projet. C'est
  aussi là que vit l'orchestrateur, qui n'est **jamais** un membre de l'équipe
  (docs/37 §4.2) ;
- **l'autorisation proposée** (`AutorisationProposee`) — un outil, son cran, et
  **sa raison**. Le cran `ask` porte en plus son décideur (#586), et c'est là que
  le critère du ticket se tient : *chaque permission `auto` proposée est nommée
  avec sa raison* (#716, docs/37 §4.3) ;
- **le skill branché** (`SkillBranche`) — ce que ce rôle prend de l'outillage du
  projet, repris tel quel de l'`Entree` qui l'a recommandé.

`RolePropose.politique()` rend la `PolitiqueOutils` (#110) que #1040 persistera :
c'est le **seul** endroit où les autorisations proposées deviennent une
politique, et une seconde traduction finirait par ne plus dire la même chose que
celle qu'on a montrée à l'utilisateur.

⚠ **Rien n'est créé.** `PropositionEquipe.to_dict()` le dit en toutes lettres
(`cree`, `validation`), au patron de `Bornes.to_dict()` qui rend `lecture_seule`
et `execution` : ce ne sont pas des réglages, ce sont les promesses du ticket
rendues lisibles par l'appelant. Aucun champ de ce module n'est négociable de
l'extérieur, et rien ici n'ouvre un fichier en écriture.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from maestro.agents.permissions import EntreeArbitrage, PolitiqueOutils
from maestro.decideur import DECIDEUR_DEFAUT, Decideur
from maestro.outillage.modele import Piece

#: Version de la forme servie. Elle voyage dans la réponse pour la raison qui a
#: fait naître `VERSION_ANALYSE` : un consommateur — #1040, l'écran qui montrera
#: l'équipe — doit pouvoir dire « je ne sais pas lire cette forme-là » plutôt que
#: de lire de travers une forme qui a changé.
VERSION_PROPOSITION = 1

#: Préfixe des identifiants de proposition d'équipe. Même entropie que les
#: identifiants de projet et d'analyse — 8 hex d'un `uuid4` tronqué — et pour la
#: même raison : c'est la **référence** qu'une équipe créée gardera de ce qui l'a
#: proposée.
PREFIXE_ID = "equ-"

#: Les trois crans qu'une autorisation proposée peut porter — ceux de
#: `PolitiqueOutils`, et rien d'autre. La liste est ici parce qu'elle borne ce
#: que `AutorisationProposee` admet ; la **sémantique** (deny > ask > allow)
#: vit dans `maestro.agents.permissions` et n'est pas redite.
CRANS: tuple[str, ...] = ("allow", "ask", "deny")

#: D'où vient le playbook d'un rôle proposé. `genere` : la mécanique de #257
#: l'a écrit pour ce projet ; `gabarit` : le fournisseur n'a rien pu produire et
#: le document du rôle figé sert de repli. La distinction est **servie**, jamais
#: tue : un playbook générique et un playbook écrit pour le projet ne valent pas
#: la même chose, et rien ne permettrait de les distinguer à la lecture.
ORIGINE_PLAYBOOK_GENERE = "genere"
ORIGINE_PLAYBOOK_GABARIT = "gabarit"
ORIGINES_PLAYBOOK: frozenset[str] = frozenset(
    {ORIGINE_PLAYBOOK_GENERE, ORIGINE_PLAYBOOK_GABARIT}
)


def nouvel_id() -> str:
    """Un identifiant de proposition d'équipe neuf, de la forme `equ-<8 hex>`."""
    return f"{PREFIXE_ID}{uuid.uuid4().hex[:8]}"


@dataclass(frozen=True)
class AutorisationProposee:
    """Un outil, le cran proposé pour lui, et **la raison** de ce cran.

    La raison n'est pas un ornement : c'est le critère du ticket. Une politique
    de permissions se relit des mois plus tard, devant un agent qui vient de
    faire quelque chose d'inattendu, et un `ask: {"Bash": "auto"}` sans sa raison
    ne dit pas *qui* a décidé ça ni *sur quoi*. Elle nomme donc, quand il y en a
    une, la pièce du projet qui l'a désignée.

    `decideur` n'a de sens que sur le cran `ask` (#586) et vaut `None` ailleurs :
    un `allow` passe sans que personne tranche, un `deny` refuse sans que
    personne tranche. Sur un `ask`, l'absence vaut `humain` — *un cran non
    précisé escalade, il ne s'auto-approuve pas* (`DECIDEUR_DEFAUT`).
    """

    outil: str
    cran: str
    raison: str
    decideur: Decideur | None = None

    def __post_init__(self) -> None:
        # Frozen : mêmes précautions que `PolitiqueOutils.__post_init__`. On
        # normalise plutôt que de refuser, sauf sur le cran — un cran inconnu
        # produirait une politique que `PermissionStore` refuserait de relire,
        # c'est-à-dire une proposition invalidable seulement à la création.
        if self.cran not in CRANS:
            raise ValueError(
                f"cran d'autorisation inconnu : {self.cran!r} "
                f"(attendus : {', '.join(CRANS)})."
            )
        if self.cran != "ask" and self.decideur is not None:
            raise ValueError(
                f"un décideur ne se pose que sur le cran « ask » (reçu sur "
                f"« {self.cran} » pour {self.outil!r})."
            )

    @property
    def decideur_effectif(self) -> Decideur | None:
        """Le décideur qui s'appliquera — `humain` par défaut sur un `ask`."""
        if self.cran != "ask":
            return None
        return self.decideur if self.decideur is not None else DECIDEUR_DEFAUT

    def to_dict(self) -> dict[str, Any]:
        """L'autorisation en JSON — le décideur **effectif**, jamais l'absence.

        Servir `None` sur un `ask` obligerait chaque écran à connaître le défaut
        pour l'afficher, c'est-à-dire à tenir une seconde fois une règle que
        `maestro.decideur` porte déjà.
        """
        decideur = self.decideur_effectif
        return {
            "outil": self.outil,
            "cran": self.cran,
            "decideur": str(decideur) if decideur is not None else None,
            "raison": self.raison,
        }


@dataclass(frozen=True)
class SkillBranche:
    """Un skill de l'outillage du projet que ce rôle branche.

    Repris de l'`Entree` qui l'a recommandé (`maestro.outillage.modele`) plutôt
    que redécrit : `chemin` et `etat` sont les siens, au caractère près. `etat`
    compte — un skill `deja-present` est déjà dans le projet, un `a-generer`
    n'existera qu'une fois l'outillage écrit (#1033), et un rôle branché sur un
    skill qui n'existe pas encore n'est pas une erreur mais un ordre de marche.
    Ce n'est vrai que **dans la proposition** : à la création, seul ce que le
    projet porte sur son disque est branché (`skills_constates`, #1212).

    `raison` dit pourquoi **ce rôle-là** le branche ; la raison du skill
    lui-même reste celle de la recommandation, et ne se recopie pas ici.
    """

    nom: str
    chemin: str
    etat: str
    raison: str
    commandes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Le skill branché en JSON."""
        return {
            "nom": self.nom,
            "chemin": self.chemin,
            "etat": self.etat,
            "raison": self.raison,
            "commandes": list(self.commandes),
        }


@dataclass(frozen=True)
class RolePropose:
    """Un rôle que l'analyse propose de recruter — jamais un agent créé.

    `nom` est le slug de la fiche que #1040 créera, `gabarit` le rôle figé dont
    il sort (docs/37 §4.1). Les deux **diffèrent**, et ce n'est pas une
    coquetterie : `playbook_outille` (#1037) rend le document du paquet pour
    tout nom de `roles_du_code()`, si bien qu'une fiche nommée `developpeur`
    verrait son playbook **écrit pour ce projet** masqué par celui du code. Le
    gabarit reste nommé, la filiation se lit, et le playbook proposé est celui
    qui s'appliquera.

    `justification` est **l'endroit du projet** qui fait exister ce rôle — le
    fichier lu, pas une phrase (même discipline qu'`Entree.justification`).
    `raison_instances` répond à l'autre question que personne ne pose à voix
    haute : *pourquoi deux, et pas un ?*

    `outils` dit ce que ce rôle aura **dans les mains** : les outils de son
    runtime outillé (#1037). Il n'est pas une autorisation — c'est ce sur quoi
    les autorisations portent —, et les deux sont séparés à dessein : une
    politique qui dirait « Bash : auto » sans qu'on sache ce que l'agent tient
    par ailleurs ne se juge pas.

    `modele` et `effort` n'y figurent pas : l'analyse d'un projet ne dit rien du
    modèle avec lequel un rôle doit travailler, et le proposer serait inventer.
    Une fiche sans réglage prend le modèle par défaut des exécutants, et c'est
    exactement ce qu'on veut dire.
    """

    nom: str
    role: str
    gabarit: str
    competences: tuple[str, ...]
    raison: str
    instances: int
    raison_instances: str
    playbook: str
    playbook_origine: str
    playbook_raison: str
    intention: str
    justification: Piece | None = None
    outils: tuple[str, ...] = ()
    skills: tuple[SkillBranche, ...] = ()
    autorisations: tuple[AutorisationProposee, ...] = ()

    def politique(self) -> PolitiqueOutils:
        """Les autorisations proposées muées en `PolitiqueOutils` (#110).

        Le **seul** chemin de cette traduction, et c'est ce qui garantit que ce
        que #1040 persistera est exactement ce que l'utilisateur a validé : une
        seconde traduction, écrite dans l'écran ou dans la route de création,
        finirait par ne plus dire la même chose que celle-ci.

        L'ordre des entrées est celui de la proposition. Il compte sur `ask`, où
        la **première** entrée qui couvre un outil donne son décideur
        (`PolitiqueOutils.decide`).

        ⚠ Une proposition sans entrée `allow` rend une liste `allow` **vide**,
        c'est-à-dire **ouverte** — et c'est voulu (cf. `maestro.equipe`). Ne pas
        « compléter » cette liste avec les `outils` du rôle : une liste `allow`
        non vide est *fermée*, et fermer celle-ci refuserait les canaux
        in-process de Maestro (`mcp__maestro__…` : poser une question, consigner
        une décision, signaler un blocage) que l'analyse d'un projet ne peut pas
        énumérer.
        """
        return PolitiqueOutils(
            allow=tuple(a.outil for a in self.autorisations if a.cran == "allow"),
            ask=tuple(
                EntreeArbitrage(a.outil, a.decideur_effectif or DECIDEUR_DEFAUT)
                for a in self.autorisations
                if a.cran == "ask"
            ),
            deny=tuple(a.outil for a in self.autorisations if a.cran == "deny"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Le rôle proposé en JSON — la forme servie par l'API.

        `politique` voyage **à côté** des autorisations détaillées, et non à leur
        place : l'écran montre les raisons une par une (#1040), la création
        n'a besoin que de la politique. Les deux sortent de la même source, donc
        elles ne peuvent pas se contredire.
        """
        return {
            "nom": self.nom,
            "role": self.role,
            "gabarit": self.gabarit,
            "competences": list(self.competences),
            "raison": self.raison,
            "justification": (
                self.justification.to_dict() if self.justification is not None else None
            ),
            "instances": self.instances,
            "raison_instances": self.raison_instances,
            "outils": list(self.outils),
            "playbook": self.playbook,
            "playbook_origine": self.playbook_origine,
            "playbook_raison": self.playbook_raison,
            "intention": self.intention,
            "skills": [skill.to_dict() for skill in self.skills],
            "autorisations": [a.to_dict() for a in self.autorisations],
            "politique": self.politique().to_dict(),
        }


@dataclass(frozen=True)
class RoleEcarte:
    """Un rôle que l'analyse a **choisi de ne pas** proposer, avec sa raison.

    Deux familles, et les deux sont utiles : ce que le projet ne justifie pas
    (aucun fichier SQL constaté) et ce qui n'est **jamais** un membre de
    l'équipe par décision — l'orchestrateur, qui est Maestro et qui recrute
    (docs/37 §4.2). Les mettre sous la même forme est voulu : la question de
    qui n'est pas dans l'équipe se pose une fois, la réponse se lit au même
    endroit.
    """

    nom: str
    role: str
    raison: str

    def to_dict(self) -> dict[str, Any]:
        """Le rôle écarté en JSON."""
        return {"nom": self.nom, "role": self.role, "raison": self.raison}


@dataclass(frozen=True)
class PropositionEquipe:
    """L'équipe qu'un projet appelle — proposée, jamais créée (#1039).

    `source` est le fragment de provenance du manifeste d'outillage (docs/38
    §4.1), tel qu'`Analyse.source_manifeste()` ou `source_manifeste_des_choix()`
    le rend : c'est lui qui dira, six mois plus tard, si cette équipe vient de
    l'analyse d'un projet existant ou des réponses données sur un projet neuf.
    Il est **repris**, jamais réécrit — une seconde orthographe de ce fragment
    finirait par diverger.

    `resume` tient en une ligne, pour la raison qui vaut déjà pour celui d'une
    analyse : c'est la phrase qu'on relit à côté d'une équipe dont on se demande
    d'où elle sort.
    """

    id: str
    projet_id: str
    faite_le: str
    resume: str = ""
    roles: tuple[RolePropose, ...] = ()
    ecartes: tuple[RoleEcarte, ...] = ()
    source: dict[str, Any] | None = None

    @property
    def instances_total(self) -> int:
        """Le nombre d'agents que cette équipe ferait travailler en parallèle."""
        return sum(role.instances for role in self.roles)

    def to_dict(self) -> dict[str, Any]:
        """La proposition entière en JSON — la forme servie par l'API.

        `cree` et `validation` ne sont pas des réglages : ce sont les deux
        promesses du ticket rendues **lisibles par l'appelant**, au patron de
        `lecture_seule`/`execution` dans `Bornes.to_dict()`. Elles sont
        constantes parce qu'elles ne sont pas négociables — aucun appel ne peut
        les changer, et une proposition qui créerait quoi que ce soit ne serait
        pas une proposition réglée autrement, ce serait autre chose (#1040).
        """
        return {
            "proposition": VERSION_PROPOSITION,
            "id": self.id,
            "projet_id": self.projet_id,
            "faite_le": self.faite_le,
            "resume": self.resume,
            "source": self.source,
            "roles": [role.to_dict() for role in self.roles],
            "ecartes": [ecarte.to_dict() for ecarte in self.ecartes],
            "instances_total": self.instances_total,
            "cree": False,
            "validation": "requise",
        }

"""Ce qui manque à une tâche pour avancer, dit en données (#1181).

Trois situations faisaient **échouer** une tâche là où Maestro savait proposer ce
qui lui manquait — relevé le 2026-09-21 par le balayage « rien de figé » :

- un **serveur MCP** à authentifier ou injoignable (`McpServerUnavailable`),
  jamais rejoué, alors que la bibliothèque MCP connaît le mode d'accès et la
  procédure de chaque serveur admis ;
- un **agent qui dit être bloqué** (`signaler_blocage`, #719), dont la raison ne
  faisait que s'écrire au journal ;
- un **rôle absent** de l'équipe pour une tâche du plan, laissée « à assigner ».

La règle est celle que #1146 tient avant le run, tenue **pendant** : un prérequis
que Maestro sait combler se propose au moment où il manque, jamais par un échec.
Ce module en porte le **fait** — `PrerequisManquant` —, et les deux façons de le
constater sans rien deviner :

- `prerequis_du_mcp` le lit sur l'exception, **en données** (`ServeurInjoignable`,
  que chaque adaptateur produit dans les mots de Maestro), et y joint la procédure
  que la bibliothèque connaît pour chaque serveur (`RegistreMcp.get`) ;
- `prerequis_du_role` le lit sur le rôle manquant du routage (`RoleManquant`,
  #1041).

Le troisième — le blocage signalé — n'a pas de constat mécanique, et c'est voulu :
« il me manque le jeton Stripe » ne se reconnaît à aucun motif. C'est le **Chef de
projet** qui le lit et le nomme (`maestro.orchestrator.rattrapage`, geste
`proposer`), dans la même forme.

Le module vit à la racine du paquet, comme `maestro.references` : le moteur
(`maestro.engine`) et le Chef de projet (`maestro.orchestrator`) le partagent, et
ni l'un ni l'autre n'a à importer l'autre pour ça.

## Quatre genres, parce que Maestro sait proposer quatre remèdes

Le `genre` n'est pas une taxonomie des pannes — elles varient à l'infini, et c'est
le modèle qui les lit. Il dit **par quel geste** la personne peut donner ce qui
manque, et donc quelle carte le fil lui montre : un **rôle** se recrute (la carte
d'équipe de #1146, en un geste), un **serveur**, un **secret** ou un **outil** se
fournit hors du fil puis se confirme d'un geste (« c'est fait »). Un cinquième
remède qu'on ne saurait pas proposer n'aurait rien à faire ici : ce qui ne se
propose pas se **demande** (`demander`, #1178).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from maestro.agents.mcp_registry import EntreeRegistre, RegistreMcp
from maestro.equipe.manque import RoleManquant
from maestro.providers.base import (
    MCP_A_AUTHENTIFIER,
    MCP_DESACTIVE,
    MCP_EN_ECHEC,
    MCP_NON_MONTABLE,
    MCP_SANS_REPONSE,
    McpServerUnavailable,
    ServeurInjoignable,
)

#: Un serveur MCP à rendre joignable — authentifier, réactiver, démarrer.
GENRE_SERVEUR = "serveur"
#: Un secret à fournir — un jeton, une clé, un identifiant.
GENRE_SECRET = "secret"
#: Un outil à installer ou à rendre accessible sur le poste.
GENRE_OUTIL = "outil"
#: Un rôle à recruter dans l'équipe du projet.
GENRE_ROLE = "role"
GENRES = (GENRE_SERVEUR, GENRE_SECRET, GENRE_OUTIL, GENRE_ROLE)

#: Comment chaque genre se dit dans une phrase — « il manque un serveur MCP… ».
_LIBELLES = {
    GENRE_SERVEUR: "le serveur MCP",
    GENRE_SECRET: "le secret",
    GENRE_OUTIL: "l'outil",
    GENRE_ROLE: "le rôle",
}

#: L'endroit de la Control Tower où un secret d'intégration se renseigne — la page
#: « Intégrations » (#270, docs/05 §2.8). Nommé une fois : la procédure le cite
#: pour chaque mode d'accès.
ECRAN_INTEGRATIONS = "l'écran Intégrations du projet"


@dataclass(frozen=True)
class PrerequisManquant:
    """Ce qui manque à une tâche pour avancer, et comment le lui donner (#1181).

    `objet` nomme la chose — « github », « STRIPE_API_KEY », « Designer » —,
    `raison` dit le constat (pourquoi la tâche s'arrête là), `procedure` comment
    le donner : celle que la bibliothèque MCP connaît pour un serveur, celle que
    le Chef de projet a écrite pour ce qu'un agent a signalé. Vide quand personne
    ne la connaît — la carte le dit alors sans en inventer une.

    Les trois derniers champs n'ont de sens que pour un **rôle** : les
    compétences que personne ne couvre, le gabarit qui y répond et ce qu'il en
    couvre — la forme exacte de `RoleManquant`, pour que la proposition de
    recrutement soit *la* raison calculée et pas une seconde formulation.
    """

    genre: str
    objet: str
    raison: str
    procedure: str = ""
    competences: tuple[str, ...] = ()
    gabarit: str = ""
    couvre: tuple[str, ...] = ()

    @property
    def recrutable(self) -> bool:
        """Un rôle qu'un gabarit sait pourvoir — donc à proposer en un geste de recrutement."""
        return self.genre == GENRE_ROLE and bool(self.gabarit)

    def role_manquant(self) -> RoleManquant:
        """Le rôle manquant tel que le canal de renfort le transporte (#1227)."""
        return RoleManquant(
            competences=self.competences,
            role=self.objet,
            gabarit=self.gabarit or None,
            couvre=self.couvre,
        )

    def phrase(self) -> str:
        """Le prérequis en une ligne — ce qui manque, et pourquoi."""
        libelle = _LIBELLES.get(self.genre, "le prérequis")
        raison = self.raison.strip().rstrip(".")
        return f"il manque {libelle} « {self.objet} » — {raison}."

    def to_dict(self) -> dict[str, Any]:
        """Le prérequis en JSON — la forme qui voyage avec un résultat de tâche (#41)."""
        return {
            "genre": self.genre,
            "objet": self.objet,
            "raison": self.raison,
            "procedure": self.procedure,
            "competences": list(self.competences),
            "gabarit": self.gabarit,
            "couvre": list(self.couvre),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PrerequisManquant:
        """Relit un prérequis depuis sa forme `to_dict`."""
        return cls(
            genre=str(data.get("genre") or ""),
            objet=str(data.get("objet") or ""),
            raison=str(data.get("raison") or ""),
            procedure=str(data.get("procedure") or ""),
            competences=tuple(str(c) for c in data.get("competences") or ()),
            gabarit=str(data.get("gabarit") or ""),
            couvre=tuple(str(c) for c in data.get("couvre") or ()),
        )


def prerequis_du_mcp(
    exc: McpServerUnavailable, registre: RegistreMcp | None = None
) -> PrerequisManquant | None:
    """Le prérequis qu'une indisponibilité MCP constate — `None` si elle ne dit rien.

    Lu sur `exc.serveurs`, **jamais sur le message** : un producteur qui n'a pas
    donné ses faits (un appelant tiers, un test) rend `None`, et la tâche échoue
    comme avant plutôt que de proposer un remède deviné.

    La procédure de chaque serveur vient de la **bibliothèque** (`registre.get`,
    donc l'allowlist : le seed et les entrées admises), trouvée par le nom de la
    liaison — celui qu'`instancier` lui donne par défaut. Un serveur déclaré hors
    de la bibliothèque est dit tel quel : sa déclaration est la seule chose qui
    sache comment il se lance.
    """
    if not exc.serveurs:
        return None
    return PrerequisManquant(
        genre=GENRE_SERVEUR,
        objet=", ".join(serveur.nom for serveur in exc.serveurs),
        raison=" ; ".join(_constat(serveur) for serveur in exc.serveurs),
        procedure="\n".join(
            _procedure(serveur, registre.get(serveur.nom) if registre is not None else None)
            for serveur in exc.serveurs
        ),
    )


def prerequis_du_role(manque: RoleManquant, titre: str = "") -> PrerequisManquant:
    """Le prérequis qu'un rôle manquant du routage constate (#1041).

    `titre` est la tâche que personne ne sait prendre : c'est elle que la raison
    nomme, puisque c'est elle qui attend.
    """
    competences = ", ".join(manque.couvre or manque.competences)
    sujet = f"la tâche « {titre} »" if titre else "cette tâche"
    return PrerequisManquant(
        genre=GENRE_ROLE,
        objet=manque.role,
        raison=f"{sujet} demande {competences}, et aucun rôle de l'équipe ne le couvre",
        procedure=(
            f"recruter le rôle « {manque.role} » (gabarit `{manque.gabarit}`)"
            if manque.gabarit is not None
            else f"recruter un rôle qui couvre {competences}, depuis les écrans d'agents du projet"
        ),
        competences=manque.competences,
        gabarit=manque.gabarit or "",
        couvre=manque.couvre,
    )


def _constat(serveur: ServeurInjoignable) -> str:
    """Ce qui est arrivé à un serveur, en une proposition."""
    cause = f" ({serveur.cause.strip()})" if serveur.cause.strip() else ""
    if serveur.etat == MCP_A_AUTHENTIFIER:
        return f"« {serveur.nom} » demande une authentification{cause}"
    if serveur.etat == MCP_NON_MONTABLE:
        manquants = ", ".join(serveur.references) or "un secret"
        return f"« {serveur.nom} » n'a pas pu être lancé : il lui manque {manquants}"
    if serveur.etat == MCP_DESACTIVE:
        return f"« {serveur.nom} » est désactivé{cause}"
    if serveur.etat == MCP_SANS_REPONSE:
        return f"« {serveur.nom} » n'a pas répondu à temps{cause}"
    if serveur.etat == MCP_EN_ECHEC:
        return f"« {serveur.nom} » n'a pas pu démarrer ou se connecter{cause}"
    return f"« {serveur.nom} » est injoignable{cause}"


def _procedure(serveur: ServeurInjoignable, entree: EntreeRegistre | None) -> str:
    """Comment rendre `serveur` joignable — ce que la bibliothèque en sait, sinon ce qu'on sait.

    Le mode d'accès de l'entrée décide de la phrase (docs/21 §2) : un jeton
    statique se **fournit**, un jeton OAuth importé se **réimporte** (il expire),
    un appairage se **renouvelle**, un serveur sans secret doit pouvoir
    **démarrer** sur le poste. Les variables à renseigner sont celles de l'entrée,
    avec leur description — ou, pour un serveur non montable, celles qui manquaient.
    """
    if entree is None:
        manquants = (
            f" : renseignez {', '.join(serveur.references)} dans {ECRAN_INTEGRATIONS}"
            if serveur.references
            else ""
        )
        return (
            f"« {serveur.nom} » n'est pas dans la bibliothèque MCP de Maestro — sa "
            f"déclaration dans {ECRAN_INTEGRATIONS} dit comment il se lance{manquants}."
        )
    variables = _variables(entree, serveur.references)
    if serveur.etat == MCP_DESACTIVE:
        geste = f"réactivez-le dans {ECRAN_INTEGRATIONS}"
    elif entree.mode_auth == "oauth_importe":
        geste = (
            f"importez un jeton OAuth neuf émis par l'outil ({variables}) "
            f"dans {ECRAN_INTEGRATIONS}"
        )
    elif entree.mode_auth == "appairage":
        geste = f"renouvelez l'appairage ({variables}) dans {ECRAN_INTEGRATIONS}"
    elif entree.mode_auth == "sans_secret" or not entree.secrets:
        lancement = entree.url or " ".join((entree.commande, *entree.args)).strip()
        geste = f"il n'a aucun secret : il doit pouvoir démarrer sur ce poste ({lancement})"
    else:
        geste = f"fournissez {variables} dans {ECRAN_INTEGRATIONS}"
    lien = f" Procédure de l'outil : {entree.procedure_url}." if entree.procedure_url else ""
    return f"{entree.nom} (`{entree.id}`) : {geste}.{lien}"


def _variables(entree: EntreeRegistre, manquantes: Sequence[str]) -> str:
    """Les variables à renseigner, décrites — celles qui manquaient d'abord, sinon toutes."""
    visees = [v for v in entree.secrets if v.cle in manquantes] or list(entree.secrets)
    if not visees:
        return ", ".join(manquantes) or "ses secrets"
    return " ; ".join(
        f"{v.cle} — {v.description}" if v.description else v.cle for v in visees
    )

"""L'équipe composée par le modèle pour le **besoin réel** du projet (#1159, docs/41).

[docs/41 §6](../../docs/41-decision-maestro-juge-il-ne-bride-pas.md) renverse la
dérivation de #1039 : cinq gabarits retenus par des règles fixes ne proposaient
**jamais** un rôle mobile, d'apprentissage automatique, de sécurité ou de
documentation, et le rôle données jamais sur un projet neuf. Ce module est la moitié
**pure** de ce qui les remplace : il écrit ce qu'on demande au modèle, lit ce qu'il
répond, et **vérifie** chaque élément avant qu'il devienne un rôle proposé. L'appel
lui-même vit dans `maestro.controltower.equipe` (`CompositeurEquipe`), seule couche
qui connaisse un fournisseur — la frontière que `maestro.equipe.proposition` tient
déjà pour les playbooks.

## Comprendre, proposer, se laisser corriger, vérifier

Le patron de docs/41 §3, en quatre verbes et deux demandes :

- **composer** (`prompt_composition` → `proposer_equipe_composee`) : le modèle reçoit
  ce que l'analyse a constaté (ou ce que la personne a répondu, sur un projet neuf),
  les skills que l'outillage recommande et les gabarits **comme matière** ; il nomme
  les rôles dont *ce* projet a besoin, chacun avec sa raison ;
- **corriger** (`prompt_correction` → `corriger_equipe`) : la personne dit ce qui
  manque avec ses mots — « ajoute quelqu'un pour la sécurité » — et le modèle rend les
  rôles à ajouter, ceux à retirer ou à remettre, et une phrase qui lui répond.

Les deux demandes partagent **un seul cadre** (`CADRE_COMPOSITION`) et un seul
contrat de réponse : ce qu'est un rôle ne change pas selon qu'on compose ou qu'on
corrige, et deux contrats finiraient par ne plus dire la même chose.

## Ce que l'exécution vérifie, et ce qu'elle fait d'un écart

Le modèle propose, le code tranche ce qui se tranche sans jugement (docs/41 §3 :
*« le modèle propose, l'exécution tranche »*). Aucun écart ne fait échouer la
composition entière ; chacun a sa conduite, et elle **se dit** :

| Ce que le modèle écrit | Ce qu'on en fait |
| --- | --- |
| un rôle sans libellé ou sans raison | il n'est pas proposé, et `composition_raison` le nomme |
| un gabarit qui n'existe pas | aucune filiation : le rôle garde une esquisse de playbook |
| un skill que l'outillage ne recommande pas | il n'est pas branché |
| une preuve que l'analyse n'a pas lue | le rôle garde sa raison, pas la pièce inventée |
| des instances hors de 1 à `INSTANCES_MAX_CREEES` | une instance, et la raison le dit |
| l'orchestrateur | jamais recruté : c'est Maestro (docs/37 §4.2) |
| un nom pris, réservé ou déjà montré | rendu libre par un suffixe |
| des compétences en prose | ramenées aux tags que le routage sait apparier |

Une réponse qui ne laisse **aucun** rôle vérifié lève `CompositionIllisible` : ce
n'est pas une équipe vide, c'est une réponse inexploitable — et le service retombe
alors sur les règles des gabarits, en le disant.

## Pourquoi du JSON, ici

Le générateur de #257 a écarté le JSON pour un contrat à quatre champs plats et un
long document Markdown (`maestro.controltower.generation_agent`). Ici c'est l'inverse :
une **liste** d'objets courts, sans document multi-ligne. C'est la forme que le JSON
sert le mieux, et celle qu'un format à en-têtes servirait le plus mal.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from maestro.agents.catalog import MODELE_EXECUTANT_DEFAUT, Agent
from maestro.agents.fiche_outillee import profil_outille
from maestro.agents.playbook_du_code import registre, socle
from maestro.agents.store import NOMS_RESERVES
from maestro.equipe.creation import INSTANCES_MAX_CREEES
from maestro.equipe.gabarits import GABARITS, RAISON_UNE_INSTANCE, Gabarit
from maestro.equipe.modele import (
    ORIGINE_COMPOSITION_MODELE,
    ORIGINE_PLAYBOOK_ESQUISSE,
    ORIGINE_PLAYBOOK_GABARIT,
    PropositionEquipe,
    RoleEcarte,
    RolePropose,
    nouvel_id,
)
from maestro.equipe.proposition import (
    ECARTE_ORCHESTRATEUR,
    _maintenant,
    _nom_libre,
    _resume,
    _skills_recommandes,
    autorisations_du_role,
    intention_du_role,
    raison_playbook_gabarit,
    skills_par_usages,
)
from maestro.outillage.modele import Constats, Entree, Piece, Recommandation
from maestro.outillage.questionnaire import Choix
from maestro.outillage.recommandation import SKILL_PAR_USAGE

#: Ce qu'une demande de correction peut peser. Une phrase, comme l'intention de #257
#: (`INTENTION_MAX`) et pour la même raison : un champ sans borne devient un canal
#: d'injection de prompt à volume libre. Le refus est franc plutôt que tronqué.
DEMANDE_MAX = 500

#: Le nombre de rôles qu'une réponse peut proposer d'un coup. Au-delà, ce n'est plus
#: une équipe qu'on relit ligne à ligne avant de la valider : la borne tient la
#: proposition **relisable**, elle ne décide pas de la taille d'une équipe — la
#: personne ajoute ce qu'elle veut ensuite, un rôle à la fois.
ROLES_MAX = 12

#: Le nom qu'aucun rôle proposé ne porte, quoi qu'écrive le modèle : l'orchestrateur
#: est Maestro, et c'est lui qui recrute (docs/37 §4.2).
_NOM_ORCHESTRATEUR = ECARTE_ORCHESTRATEUR.nom

#: Ce qu'on dit d'un gabarit que le modèle n'a pas retenu sans en donner la raison.
#: Un fait — c'est son jugement sur ce projet —, et aucun geste : l'écran qui offre
#: d'ajouter un rôle le dit à côté de son contrôle (#1159).
RAISON_NON_RETENU = (
    "l'analyse d'équipe ne l'a pas retenu pour ce projet : rien de ce qu'elle a lu, ni "
    "de ce que vous avez dit, ne l'appelle"
)

_CADRE = """\
Tu composes l'équipe d'agents IA qui travaillera sur un projet, pour Maestro, un
orchestrateur qui répartit du travail entre des agents autonomes. Chaque rôle que tu
proposes deviendra un agent — mais rien n'est créé sans que la personne l'ait relu et
validé.

On te donne ce que l'analyse du projet a constaté (ou, pour un projet neuf, ce que la
personne a répondu), les skills que l'outillage du projet recommande, et des rôles
types que Maestro connaît. Ces rôles types sont une matière, pas une liste fermée :
reprends-en un quand il convient (nomme-le dans "gabarit"), écarte-le quand le projet
ne l'appelle pas, et propose tout autre rôle que le besoin réel demande — une
application mobile, un pipeline d'apprentissage automatique, la sécurité, la
documentation, les données d'un projet neuf qui en stockera…

Chaque rôle porte sa raison : ce que le projet contient, ou ce que la personne a dit,
qui le rend nécessaire. Un rôle sans raison tirée du projet ou de ses mots n'est pas
proposé. Si un fichier cité dans ce qu'on te donne le prouve, recopie son chemin exact
dans "preuve" ; sinon laisse "preuve" vide — n'invente jamais un chemin. Une instance
par rôle par défaut ; davantage seulement si le projet a des surfaces indépendantes, et
dis pourquoi. Ne propose jamais l'orchestrateur : c'est Maestro lui-même, présent dans
tout projet, et c'est lui qui recrute.

Réponds par UN SEUL objet JSON, sans rien autour :

{
  "roles": [
    {
      "nom": "identifiant court en minuscules, chiffres et tirets, autre qu'un rôle type",
      "role": "le libellé du rôle, en quelques mots",
      "competences": ["trois à six tags courts, en minuscules"],
      "raison": "une phrase : pourquoi ce projet appelle ce rôle",
      "preuve": "le chemin exact d'un fichier cité qui le prouve, ou une chaîne vide",
      "gabarit": "le nom du rôle type dont il descend, ou une chaîne vide",
      "instances": 1,
      "raison_instances": "pourquoi ce nombre, s'il dépasse un",
      "skills": ["les skills du projet dont ce rôle se sert, parmi ceux listés"]
    }
  ],
  "ecartes": [{"gabarit": "un rôle type non retenu", "raison": "une phrase : pourquoi"}],
  "retraits": ["noms de rôles de l'équipe montrée à retirer"],
  "remis": ["noms de rôles retirés de l'équipe montrée à remettre"],
  "instances": {"nom d'un rôle de l'équipe montrée": 2},
  "reponse": "une ou deux phrases adressées à la personne"
}

Quand on te demande de COMPOSER l'équipe, remplis "roles" et "ecartes" et laisse le
reste vide. Quand on te transmet une DEMANDE de la personne sur l'équipe qu'elle a sous
les yeux, ne mets dans "roles" que les rôles à ajouter ; remplis "retraits", "remis"
et "instances" selon ce qu'elle demande, et dis-lui dans "reponse" ce que tu as fait —
ou, si sa demande ne porte pas sur l'équipe ou reste ambiguë, ce qu'il te faudrait
savoir, sans rien changer. Ne devine jamais : ne change que ce qu'elle a demandé.

Les raisons — celles des rôles écartés comprises — et la réponse sont lues telles
quelles par la personne : adresse-toi à elle, « vous avez répondu », « vos réponses »,
jamais « la personne » ni « l'utilisateur » à la troisième personne.

"""

#: Le cadre de l'appel de composition et de correction — sa consigne, son contrat de
#: réponse, et le **registre de langue** (#945) : les raisons et la réponse sont lues
#: telles quelles par la personne, à l'étape d'équipe.
CADRE_COMPOSITION = _CADRE + registre()


class CompositionIllisible(ValueError):
    """La réponse du modèle ne laisse rien d'exploitable — et rien n'a été proposé.

    Le service en fait deux choses selon la demande : une **composition** retombe sur
    les règles des gabarits, dites comme telles ; une **correction** échoue
    franchement (502), parce qu'il n'y a pas de règle pour « ajoute quelqu'un pour la
    sécurité » et qu'inventer une réponse serait pire qu'en rendre aucune.
    """


@dataclass(frozen=True)
class MembreActuel:
    """Un rôle de l'équipe **telle que la personne la voit** au moment de sa demande.

    `retenu` distingue une ligne cochée d'une ligne retirée : « remets les tests » ne
    veut rien dire si le modèle ignore que les tests ont été retirés. Seuls ces noms
    peuvent être retirés, remis ou changés d'instances par une correction.
    """

    nom: str
    role: str
    retenu: bool = True
    instances: int = 1


@dataclass(frozen=True)
class ReponseModele:
    """Ce que le modèle a répondu, **lu mais pas encore vérifié**.

    La lecture ne juge que la forme (un objet, des listes, des chaînes) ; la
    vérification — ce qui devient un rôle, ce qui est écarté — est celle de
    `proposer_equipe_composee` et `corriger_equipe`, qui connaissent le projet.
    """

    roles: tuple[Mapping[str, Any], ...] = ()
    ecartes: tuple[Mapping[str, Any], ...] = ()
    retraits: tuple[str, ...] = ()
    remis: tuple[str, ...] = ()
    instances: Mapping[str, Any] = field(default_factory=dict)
    reponse: str = ""


@dataclass(frozen=True)
class CorrectionEquipe:
    """Ce qu'une demande en langage naturel change à l'équipe montrée — rien de créé.

    `ajouts` sont des rôles proposés **de plein droit** : même forme, mêmes
    autorisations, même playbook écrit pour le projet qu'un rôle de la composition.
    `retraits`, `remis` et `instances` ne nomment que des rôles de l'équipe montrée.
    `reponse` est la phrase que l'écran affiche à la personne.
    """

    reponse: str
    ajouts: tuple[RolePropose, ...] = ()
    retraits: tuple[str, ...] = ()
    remis: tuple[str, ...] = ()
    instances: Mapping[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """La correction en JSON — `cree: False`, comme toute proposition (#1040)."""
        return {
            "reponse": self.reponse,
            "ajouts": [role.to_dict() for role in self.ajouts],
            "retraits": list(self.retraits),
            "remis": list(self.remis),
            "instances": dict(self.instances),
            "cree": False,
        }


# --------------------------------------------------------------------------- #
# Ce qu'on demande au modèle
# --------------------------------------------------------------------------- #


def prompt_composition(
    constats: Constats,
    recommandation: Recommandation,
    choix: Sequence[Choix] = (),
    *,
    nom_projet: str = "",
) -> str:
    """La demande de composition : le projet, puis la consigne qui ferme le prompt."""
    return "\n".join(
        (
            *_contexte(constats, recommandation, choix, nom_projet),
            "Compose l'équipe de ce projet selon le format demandé.",
        )
    )


def prompt_correction(
    constats: Constats,
    recommandation: Recommandation,
    choix: Sequence[Choix],
    demande: str,
    equipe: Sequence[MembreActuel],
    *,
    nom_projet: str = "",
) -> str:
    """La demande de correction : le projet, l'équipe montrée, puis les mots de la personne.

    La demande vient **en dernier**, juste avant la consigne : c'est la matière
    qu'on traite, et la règle du fil global vaut ici — ce qui ferme le prompt se lit
    comme une instruction, donc la consigne est la nôtre, pas la sienne.
    """
    lignes = [f"- {m.nom} — {m.role}{_etat_du_membre(m)}" for m in equipe]
    return "\n".join(
        (
            *_contexte(constats, recommandation, choix, nom_projet),
            "Équipe que la personne a sous les yeux :",
            "",
            "\n".join(lignes) if lignes else "- (aucun rôle)",
            "",
            "Demande de la personne, avec ses mots :",
            "",
            demande_valide(demande),
            "",
            "Applique sa demande à l'équipe selon le format demandé.",
        )
    )


def demande_valide(demande: str) -> str:
    """La demande remise à plat — ou `ValueError` si elle est vide ou trop longue.

    Refusée **avant** tout appel : une saisie n'est pas une panne, et l'appelant en
    tire un 422, jamais un 502.
    """
    phrase = " ".join(demande.split())
    if not phrase:
        raise ValueError("demande vide : dites ce que vous voulez changer à l'équipe.")
    if len(phrase) > DEMANDE_MAX:
        raise ValueError(
            f"demande trop longue ({len(phrase)} caractères, {DEMANDE_MAX} au plus) : "
            "dites en une phrase ce que vous voulez changer à l'équipe."
        )
    return phrase


def _etat_du_membre(membre: MembreActuel) -> str:
    etat = [] if membre.retenu else ["retiré par la personne"]
    if membre.instances > 1:
        etat.append(f"{membre.instances} instances")
    return f" ({', '.join(etat)})" if etat else ""


def _contexte(
    constats: Constats,
    recommandation: Recommandation,
    choix: Sequence[Choix],
    nom_projet: str,
) -> tuple[str, ...]:
    """Ce que le modèle doit savoir du projet — tout ce qui est lu, rien d'autre."""
    provenance = (
        "un projet neuf, décrit par les réponses de la personne"
        if choix
        else "un projet existant, lu par l'analyse"
    )
    nomme = f" « {nom_projet} »" if nom_projet else ""
    return (
        f"Le projet{nomme} est {provenance}.",
        "",
        "Ce que l'analyse a constaté :",
        "",
        _constats_en_clair(constats),
        "",
        *(
            (
                "Ce que la personne a répondu :",
                "",
                "\n".join(f"- {c.cle} : {c.valeur}" for c in choix),
                "",
            )
            if choix
            else ()
        ),
        "Skills que l'outillage du projet recommande :",
        "",
        _skills_en_clair(recommandation),
        "",
        "Rôles types que Maestro connaît (une matière, pas une liste fermée) :",
        "",
        "\n".join(
            f"- {g.gabarit} — {g.role} : {', '.join(g.competences)}" for g in GABARITS
        ),
        "",
    )


def _constats_en_clair(constats: Constats) -> str:
    lignes = []
    for lang in constats.langages:
        exemple = f", par exemple {lang.exemple}" if lang.exemple else ""
        lignes.append(
            f"- langage {lang.nom} : {round(lang.part * 100)} % des fichiers de code "
            f"({lang.fichiers} fichier(s){exemple})"
        )
    lignes += [
        f"- commande de « {c.usage} » : `{c.commande}` ({c.chemin}, {c.origine})"
        for c in constats.commandes
    ]
    lignes += [f"- intégration continue : {piece.chemin}" for piece in constats.ci]
    if constats.forge is not None:
        lignes.append(f"- forge : {constats.forge.nom}")
    lignes += [f"- convention : {piece.chemin}" for piece in constats.conventions]
    lignes += [f"- script : {piece.chemin}" for piece in constats.dossier_scripts.scripts]
    lignes += [
        f"- outillage d'agents déjà présent : {piece.chemin}"
        for piece in constats.outillage_present
    ]
    return "\n".join(lignes) if lignes else "- rien : le projet est vide, ou neuf"


def _skills_en_clair(recommandation: Recommandation) -> str:
    skills = _skills_recommandes(recommandation)
    if not skills:
        return "- aucun : l'outillage n'en recommande pas"
    lignes = []
    for nom, entree in skills.items():
        commande = f" : `{entree.commandes[0]}`" if entree.commandes else ""
        lignes.append(f"- {nom}{commande}")
    return "\n".join(lignes)


# --------------------------------------------------------------------------- #
# Ce que le modèle répond — lu, puis vérifié
# --------------------------------------------------------------------------- #

_FENCE = re.compile(r"```(?:json)?\s*(?P<corps>.*?)```", re.DOTALL)


def lire_reponse(texte: str) -> ReponseModele:
    """La réponse du modèle, lue dans sa forme — `CompositionIllisible` si elle n'en a pas.

    L'objet se cherche nu, en bloc de code, ou noyé dans la prose (un modèle en
    ajoute) — le patron de `orchestration._objet_json`, sans son import : ce module
    est pur, et l'orchestration tire le moteur entier. Les clés inconnues sont
    ignorées ; une clé attendue de la mauvaise forme lève, parce qu'une liste de rôles
    qui n'est pas une liste ne se répare pas en devinant.
    """
    objet = _objet_json(texte)
    if not isinstance(objet, dict):
        raise CompositionIllisible("la réponse du modèle ne contient aucun objet JSON lisible")
    roles, ecartes = objet.get("roles") or [], objet.get("ecartes") or []
    retraits, remis = objet.get("retraits") or [], objet.get("remis") or []
    instances = objet.get("instances") or {}
    if not all(isinstance(v, list) for v in (roles, ecartes, retraits, remis)) or not (
        isinstance(instances, dict)
    ):
        raise CompositionIllisible(
            "la réponse du modèle n'a pas la forme attendue (des listes de rôles et de noms)"
        )
    return ReponseModele(
        roles=tuple(r for r in roles if isinstance(r, dict)),
        ecartes=tuple(e for e in ecartes if isinstance(e, dict)),
        retraits=tuple(str(n) for n in retraits if isinstance(n, str)),
        remis=tuple(str(n) for n in remis if isinstance(n, str)),
        instances=instances,
        reponse=_texte(objet.get("reponse")),
    )


def _objet_json(texte: str) -> Any:
    candidats = [texte.strip()]
    fence = _FENCE.search(texte)
    if fence is not None:
        candidats.append(fence.group("corps").strip())
    debut, fin = texte.find("{"), texte.rfind("}")
    if debut != -1 and fin > debut:
        candidats.append(texte[debut : fin + 1])
    for candidat in candidats:
        try:
            return json.loads(candidat)
        except json.JSONDecodeError:
            continue
    return None


def proposer_equipe_composee(
    constats: Constats,
    recommandation: Recommandation,
    reponse: ReponseModele,
    *,
    projet_id: str = "",
    noms_pris: Sequence[str] = (),
    source: Mapping[str, object] | None = None,
) -> PropositionEquipe:
    """L'équipe que le modèle a composée, **vérifiée** — jamais créée.

    Même forme que `proposer_equipe` : l'écran, la création (#1040) et la carte du fil
    lisent une proposition sans savoir qui l'a composée ; seul `composition_origine`
    le dit. Les gabarits que le modèle n'a pas retenus sont écartés **nommément**,
    avec sa raison quand il en donne une — règle 2 de `maestro.equipe.proposition`.

    Lève `CompositionIllisible` si aucun rôle ne survit à la vérification.
    """
    skills = _skills_recommandes(recommandation)
    pris = [*noms_pris, *sorted(NOMS_RESERVES)]
    roles, rejets = _roles_verifies(reponse.roles, constats, skills, pris)
    if not roles:
        raise CompositionIllisible(
            "le modèle n'a proposé aucun rôle vérifiable"
            + (f" ({'; '.join(rejets)})" if rejets else "")
        )
    ecartes = [ECARTE_ORCHESTRATEUR, *_ecartes(roles, reponse.ecartes)]
    return PropositionEquipe(
        id=nouvel_id(),
        projet_id=projet_id,
        faite_le=_maintenant(),
        resume=_resume(roles, ecartes),
        roles=tuple(roles),
        ecartes=tuple(ecartes),
        source=dict(source) if source is not None else None,
        composition_origine=ORIGINE_COMPOSITION_MODELE,
        composition_raison=(
            f"{len(rejets)} rôle(s) proposé(s) par le modèle écarté(s) à la vérification : "
            + " ; ".join(rejets)
            if rejets
            else ""
        ),
    )


def corriger_equipe(
    constats: Constats,
    recommandation: Recommandation,
    reponse: ReponseModele,
    *,
    equipe: Sequence[MembreActuel],
    noms_pris: Sequence[str] = (),
) -> CorrectionEquipe:
    """Ce que la demande change à l'équipe montrée, **vérifié** — jamais appliqué ici.

    Les ajouts suivent la vérification de la composition ; leurs noms sont rendus
    libres **aussi** contre l'équipe montrée, sans quoi un ajout écraserait une ligne
    à l'écran. Retraits, remises et instances ne nomment que des rôles montrés.

    Une réponse sans aucun changement **et** sans phrase lève `CompositionIllisible` :
    le modèle n'a rien dit, et une correction muette laisserait la personne sans
    savoir si sa demande a été lue.
    """
    montres = {membre.nom for membre in equipe}
    pris = [*noms_pris, *montres, *sorted(NOMS_RESERVES)]
    ajouts, rejets = _roles_verifies(
        reponse.roles, constats, _skills_recommandes(recommandation), pris
    )
    retraits = tuple(dict.fromkeys(n for n in reponse.retraits if n in montres))
    remis = tuple(dict.fromkeys(n for n in reponse.remis if n in montres))
    instances = {
        nom: nombre
        for nom, brut in reponse.instances.items()
        if nom in montres and (nombre := _instances_admises(brut)) is not None
    }
    phrase = reponse.reponse
    if rejets:
        phrase = f"{phrase} ({'; '.join(rejets)})".strip()
    if not (ajouts or retraits or remis or instances or phrase):
        raise CompositionIllisible("le modèle n'a rien changé et n'a rien répondu")
    return CorrectionEquipe(
        reponse=phrase or _reponse_par_defaut(ajouts, retraits, remis, instances),
        ajouts=tuple(ajouts),
        retraits=retraits,
        remis=remis,
        instances=instances,
    )


def _reponse_par_defaut(
    ajouts: Sequence[RolePropose],
    retraits: Sequence[str],
    remis: Sequence[str],
    instances: Mapping[str, int],
) -> str:
    """Ce qu'on dit quand le modèle a changé l'équipe sans rien en dire."""
    morceaux = []
    if ajouts:
        morceaux.append("ajouté : " + ", ".join(r.role for r in ajouts))
    if retraits:
        morceaux.append("retiré : " + ", ".join(retraits))
    if remis:
        morceaux.append("remis : " + ", ".join(remis))
    if instances:
        morceaux.append(
            "instances : " + ", ".join(f"{nom} ×{n}" for nom, n in instances.items())
        )
    return "Équipe modifiée — " + " ; ".join(morceaux) + "."


def _roles_verifies(
    bruts: Sequence[Mapping[str, Any]],
    constats: Constats,
    skills: Mapping[str, Entree],
    pris: list[str],
) -> tuple[list[RolePropose], list[str]]:
    """Les rôles que la vérification laisse passer, et ce qu'elle a écarté (en clair)."""
    roles: list[RolePropose] = []
    rejets: list[str] = []
    for brut in bruts[:ROLES_MAX]:
        libelle = _une_ligne(_texte(brut.get("role")))
        raison = _une_ligne(_texte(brut.get("raison")))
        slug = _slug(_texte(brut.get("nom"))) or _slug(libelle)
        if _NOM_ORCHESTRATEUR in (slug, _slug(libelle)):
            rejets.append("l'orchestrateur, qui est Maestro et n'est jamais recruté")
            continue
        if not libelle or not raison:
            rejets.append(f"« {libelle or slug or '?'} », proposé sans raison")
            continue
        gabarit = _gabarit(_texte(brut.get("gabarit")))
        competences = _competences(brut.get("competences")) or (
            gabarit.competences if gabarit is not None else ()
        )
        if not competences:
            rejets.append(f"« {libelle} », proposé sans aucune compétence")
            continue
        nom = _nom_libre(_nom_de_base(slug, libelle, gabarit), pris)
        pris.append(nom)
        roles.append(
            _role_compose(
                brut, nom, libelle, raison, competences, gabarit, constats, skills
            )
        )
    return roles, rejets


def _nom_de_base(slug: str, libelle: str, gabarit: Gabarit | None) -> str:
    """Le nom à rendre libre : celui du modèle, sauf s'il est **réservé**.

    Relevé sur la vraie stack : le modèle nomme volontiers un rôle comme l'agent du
    code dont il descend (`developpeur`, `qa`, `devops`), noms que le dépôt réserve
    (`NOMS_RESERVES`). Les suffixer donnait `developpeur-2` — libre, mais illisible
    à l'étape d'équipe. On retombe donc, dans l'ordre, sur le nom du **gabarit**
    (`dev`, `tests`, `infra` — c'est la raison d'être de `Gabarit.nom`), puis sur le
    libellé ; le suffixe ne sert qu'en dernier recours.
    """
    candidats = (
        slug,
        gabarit.nom if gabarit is not None else "",
        _slug(libelle),
    )
    libres = [c for c in candidats if c and c not in NOMS_RESERVES]
    return libres[0] if libres else (slug or "role")


def _role_compose(
    brut: Mapping[str, Any],
    nom: str,
    libelle: str,
    raison: str,
    competences: tuple[str, ...],
    gabarit: Gabarit | None,
    constats: Constats,
    skills: Mapping[str, Entree],
) -> RolePropose:
    """Le rôle vérifié, dans la forme exacte d'un rôle de gabarit.

    Un rôle qui **descend d'un gabarit** en garde la matière : ses usages (donc ses
    skills), ses outils, son playbook de repli. Un rôle **hors gabarit** branche les
    skills que le modèle a nommés parmi ceux recommandés, tient les outils du cadre
    générique — ceux qu'aura réellement l'agent créé (`profil_outille`) — et porte une
    esquisse de playbook jusqu'à ce que #257 en écrive un pour ce projet.
    """
    usages = _usages(gabarit, brut.get("skills"), skills)
    outils = profil_outille(
        gabarit.agent
        if gabarit is not None
        else Agent(
            nom=nom,
            role=libelle,
            competences=frozenset(competences),
            modele=MODELE_EXECUTANT_DEFAUT,
            prompt_systeme="",
        )
    ).outils
    branches = skills_par_usages(usages, skills)
    autorisations = autorisations_du_role(outils, usages, constats)
    instances, raison_instances = _instances(brut)
    return RolePropose(
        nom=nom,
        role=libelle,
        gabarit=gabarit.gabarit if gabarit is not None else "",
        competences=competences,
        raison=raison,
        justification=_preuve(_texte(brut.get("preuve")), constats),
        instances=instances,
        raison_instances=raison_instances,
        playbook=(
            gabarit.playbook_de_repli()
            if gabarit is not None
            else playbook_esquisse(libelle, raison, competences)
        ),
        playbook_origine=(
            ORIGINE_PLAYBOOK_GABARIT if gabarit is not None else ORIGINE_PLAYBOOK_ESQUISSE
        ),
        playbook_raison=(
            raison_playbook_gabarit(gabarit.gabarit)
            if gabarit is not None
            else RAISON_ESQUISSE
        ),
        intention=intention_du_role(
            libelle,
            constats,
            branches,
            autorisations,
            competences=() if gabarit is not None else competences,
        ),
        outils=outils,
        skills=branches,
        autorisations=autorisations,
    )


#: Ce qu'on dit d'une esquisse **avant** que sa rédaction ait été tentée.
RAISON_ESQUISSE = (
    "esquisse écrite à partir du rôle, de sa raison et de ses compétences : ce rôle ne "
    "descend d'aucun gabarit, et sa rédaction pour ce projet n'a pas encore eu lieu"
)


def playbook_esquisse(role: str, raison: str, competences: Sequence[str]) -> str:
    """Le playbook d'un rôle **hors gabarit**, tant que #257 n'en a pas écrit un.

    Un vrai prompt système, pas un gabarit vide : le rôle, pourquoi on l'a recruté,
    ses compétences, puis le **socle** des rôles (`socle()`) — le régime sénior et le
    registre de langue que tout rôle du code reçoit déjà. Il se dit comme tel
    (`ORIGINE_PLAYBOOK_ESQUISSE`) et se relit à la validation, comme tout playbook.
    """
    return (
        f"# {role}\n\n"
        f"Tu es l'agent « {role} » de ce projet. On t'a recruté parce que {raison}.\n\n"
        f"Ton métier : {', '.join(competences)}. Tu t'en tiens à lui, et tu signales ce "
        "qui en sort plutôt que de le prendre.\n\n"
        f"{socle()}"
    )


def _ecartes(
    roles: Sequence[RolePropose], bruts: Sequence[Mapping[str, Any]]
) -> list[RoleEcarte]:
    """Chaque gabarit dont aucun rôle ne descend, avec la raison du modèle ou la sienne."""
    retenus = {role.gabarit for role in roles}
    raisons: dict[str, str] = {}
    for brut in bruts:
        gabarit = _gabarit(_texte(brut.get("gabarit")) or _texte(brut.get("nom")))
        raison = _une_ligne(_texte(brut.get("raison")))
        if gabarit is not None and raison:
            raisons.setdefault(gabarit.gabarit, raison)
    return [
        RoleEcarte(
            nom=gabarit.nom,
            role=gabarit.role,
            raison=raisons.get(gabarit.gabarit, RAISON_NON_RETENU),
        )
        for gabarit in GABARITS
        if gabarit.gabarit not in retenus
    ]


def _gabarit(brut: str) -> Gabarit | None:
    """Le gabarit que le modèle nomme — par son nom d'agent ou son slug proposé."""
    cle = _slug(brut)
    if not cle:
        return None
    return next((g for g in GABARITS if cle in (g.gabarit, g.nom)), None)


def _usages(
    gabarit: Gabarit | None, noms: Any, skills: Mapping[str, Entree]
) -> tuple[tuple[str, str], ...]:
    """Les usages que ce rôle branche : ceux de son gabarit, puis ceux qu'il a nommés.

    Un skill nommé n'est retenu que s'il est **recommandé** pour ce projet : le
    modèle choisit dans la liste qu'on lui a donnée, il n'en ajoute pas.
    """
    usages: dict[str, str] = dict(gabarit.usages) if gabarit is not None else {}
    usage_du_skill = {nom: usage for usage, (nom, _) in SKILL_PAR_USAGE.items()}
    for nom in noms if isinstance(noms, list) else ():
        usage = usage_du_skill.get(_texte(nom))
        if usage is not None and usage not in usages and _texte(nom) in skills:
            usages[usage] = "l'analyse d'équipe l'a rattaché à ce rôle"
    return tuple(usages.items())


def _instances(brut: Mapping[str, Any]) -> tuple[int, str]:
    """Le nombre d'instances et sa raison — une, dite telle, quand il est hors bornes."""
    propose = brut.get("instances", 1)
    nombre = _instances_admises(propose)
    if nombre is None:
        return 1, (
            f"le modèle en proposait « {propose} », hors des bornes de 1 à "
            f"{INSTANCES_MAX_CREEES} : une instance, que vous ajustez si besoin"
        )
    if nombre == 1:
        return 1, RAISON_UNE_INSTANCE
    raison = _une_ligne(_texte(brut.get("raison_instances")))
    return nombre, raison or f"{nombre} instances proposées par l'analyse d'équipe"


def _instances_admises(brut: Any) -> int | None:
    if isinstance(brut, bool) or not isinstance(brut, int):
        return None
    return brut if 1 <= brut <= INSTANCES_MAX_CREEES else None


def _preuve(chemin: str, constats: Constats) -> Piece | None:
    """La pièce que le modèle cite, **si l'analyse l'a lue** — sinon aucune.

    Les chemins admis sont ceux que les constats portent : exemples de langage,
    commandes, CI, forge, conventions, scripts, outillage présent. Un chemin
    plausible mais jamais lu serait une pièce inventée — pire que pas de pièce.
    """
    if not chemin:
        return None
    lus: dict[str, str] = {}
    for langage in constats.langages:
        if langage.exemple:
            lus.setdefault(langage.exemple, f"{langage.fichiers} fichier(s) {langage.nom}")
    for commande in constats.commandes:
        lus.setdefault(commande.chemin, f"commande de {commande.usage}")
    for piece in (
        *constats.ci,
        *constats.conventions,
        *constats.dossier_scripts.scripts,
        *constats.outillage_present,
    ):
        lus.setdefault(piece.chemin, piece.role or piece.nom)
    if constats.forge is not None and constats.forge.chemin:
        lus.setdefault(constats.forge.chemin, "forge constatée")
    role = lus.get(chemin.strip())
    return Piece(nom=chemin.strip(), chemin=chemin.strip(), role=role) if role else None


def _competences(brut: Any) -> tuple[str, ...]:
    """Les compétences ramenées aux tags du routage — minuscules, sans accent, tirets."""
    if not isinstance(brut, list):
        return ()
    return tuple(dict.fromkeys(tag for tag in (_slug(_texte(c)) for c in brut) if tag))


def _texte(brut: Any) -> str:
    return brut.strip() if isinstance(brut, str) else ""


def _une_ligne(texte: str) -> str:
    return " ".join(texte.split())


def _slug(brut: str) -> str:
    """`brut` au format d'un nom d'agent (`[a-z0-9][a-z0-9_-]*`), ou vide."""
    decompose = unicodedata.normalize("NFKD", brut)
    sans_accents = "".join(c for c in decompose if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", "-", sans_accents).strip("-")[:40].strip("-")

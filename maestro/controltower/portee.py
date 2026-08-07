"""La **portée projet** d'une lecture de la Control Tower (ticket #277, EF-35).

Le `projet_id` est porté par la tâche et le run depuis #222, mais chaque vue en
faisait ce qu'elle voulait : un paramètre `?projet=` optionnel, une absence qui
valait « tout », un identifiant inconnu qui rendait une liste vide. Trois
lectures d'une même question, donc trois façons de se tromper. Ce module en fait
**un seul contrat**, que toutes les lectures qui agrègent partagent — Kanban,
exécutions, coûts, validations, journal — et que le flux temps réel applique à
l'identique (`Diffusion`, `WS /ws/evenements`).

Le contrat tient en trois valeurs et un refus :

- ``?projet=<id>`` — la vue ne rend que ce qui appartient à ce projet. Un
  identifiant **inconnu du dépôt** (ou mal formé) est **refusé** avec son motif
  (`projet-inconnu`, 404), comme les refus de `ServiceProjets` (#223) : une
  faute de frappe qui rend une liste vide se lit « ce projet n'a rien fait »,
  ce qui est un mensonge coûteux ;
- ``?projet=tous`` — la vue transverse, **explicitement demandée** : tous les
  projets, travaux sans projet compris. C'est ce que rendait l'absence de
  paramètre avant ce lot ; la différence est qu'on le dit ;
- ``?projet=aucun`` — les seuls travaux qui ne relèvent d'aucun projet. Une vue
  jusqu'ici inatteignable, et pourtant la seule qui montre ce qui échappe au
  cadre.

Et l'absence : ``?projet`` **omis** est **refusé** (`projet-requis`, 422). C'est
le cœur du lot — « rien plutôt qu'un mélange » (critère #277) : l'API ne devine
plus quel périmètre on voulait. Un refus motivé vaut mieux qu'une liste vide,
qui ne se distingue pas d'un projet sans activité, et mieux qu'un mélange
silencieux de tous les projets, qui était le défaut d'avant.

`tous` et `aucun` sont des **mots réservés** : ils ne peuvent pas désigner un
projet réel, dont l'identifiant est engendré par le dépôt sous la forme
``prj-<empreinte>`` (#221). Un projet ne peut donc pas les masquer.

Ce module est volontairement une **feuille de la couche lecture** : il ne connaît
ni FastAPI ni HTTP (la traduction du motif en code est le rôle d'`app.py`, via
`maestro.controltower.projets.statut_http`), et il n'importe du dépôt de projets
que de quoi répondre « cet identifiant existe-t-il ? ». C'est ce qui permet à la
projection (`ControlTowerState`), aux analytics et à la diffusion temps réel de
partager le même objet de portée sans dépendre de l'API.
"""

from __future__ import annotations

from dataclasses import dataclass

from maestro.appartenance import projet_id_valide

#: La vue transverse, explicitement demandée : tous les projets, travaux sans
#: projet compris. Mot réservé — aucun projet ne peut porter cet identifiant.
PORTEE_TOUS = "tous"

#: Les seuls travaux qui ne relèvent d'aucun projet. Mot réservé, comme `tous`.
PORTEE_AUCUN = "aucun"

#: Les portées qui ne désignent pas un projet — celles qu'on n'a pas à chercher
#: dans le dépôt avant de les accepter.
PORTEES_RESERVEES = frozenset({PORTEE_TOUS, PORTEE_AUCUN})


class PorteeRefusee(ValueError):
    """Une portée projet inexploitable — motif compris, comme `RacineRefusee` (#221).

    Deux motifs, et deux seulement : `projet-requis` (le paramètre manque) et
    `projet-inconnu` (l'identifiant ne désigne aucun projet déclaré). Le `motif`
    est un code court et stable — c'est lui que l'UI teste, pas la phrase —, le
    message la raison lisible. `app.py` en tire le code HTTP par la même table
    que les refus de `ServiceProjets` (`statut_http`), pour que deux refus de
    même nature ne sortent pas par deux portes différentes.
    """

    def __init__(self, motif: str, message: str) -> None:
        super().__init__(message)
        self.motif = motif


@dataclass(frozen=True)
class PorteeProjet:
    """Le périmètre d'une lecture : un projet, tous, ou aucun.

    Un seul prédicat en sort — `retient` — et c'est lui que consultent la
    projection, les analytics, le journal et la diffusion temps réel. Aucun de
    ces quatre ne réimplémente donc « appartient au projet demandé », ce qui est
    tout l'objet du contrat unique du critère #277.

    `projet_id` est l'identifiant demandé (`None` pour `tous` et `aucun`) et
    `transverse` dit qu'aucun filtre ne s'applique. Les deux ensemble décrivent
    les trois portées sans état impossible : `tous` est le seul cas transverse,
    `aucun` est `projet_id=None` non transverse, un projet est son identifiant.
    """

    projet_id: str | None = None
    transverse: bool = False

    @classmethod
    def tous(cls) -> PorteeProjet:
        """La vue transverse : tous les projets, travaux sans projet compris."""
        return cls(transverse=True)

    @classmethod
    def aucun(cls) -> PorteeProjet:
        """Les seuls travaux qui ne relèvent d'aucun projet."""
        return cls()

    @classmethod
    def projet(cls, projet_id: str) -> PorteeProjet:
        """La vue d'un projet : ce qui porte cet identifiant, et rien d'autre."""
        return cls(projet_id=projet_id)

    @property
    def libelle(self) -> str:
        """La portée telle qu'elle a été demandée — ce que les réponses rappellent.

        `tous`, `aucun` ou l'identifiant du projet : la même chaîne que celle
        reçue en paramètre, pour qu'une réponse dise sur quoi elle porte sans
        que le client ait à le déduire de ce qu'elle contient.
        """
        if self.transverse:
            return PORTEE_TOUS
        return self.projet_id if self.projet_id is not None else PORTEE_AUCUN

    def retient(self, projet_id: str | None) -> bool:
        """Ce travail entre-t-il dans la portée ?

        La règle unique du lot : `tous` retient tout, une portée de projet ne
        retient que l'égalité stricte (un travail **sans** projet n'est deviné
        rattaché à aucun — il n'apparaît donc dans aucune vue filtrée), et
        `aucun` ne retient que ce qui n'a pas de projet.
        """
        if self.transverse:
            return True
        return projet_id == self.projet_id


def resoudre_portee(brut: str | None, *, projet_connu: object = None) -> PorteeProjet:
    """La portée d'une requête, ou `PorteeRefusee` motivée — jamais une devinette.

    `brut` est le paramètre `projet` reçu tel quel. Absent ou vide, il est
    **refusé** (`projet-requis`) : c'est le « rien plutôt qu'un mélange » du
    critère #277. `tous` et `aucun` sont rendus sans consulter le dépôt (ce sont
    des mots réservés) ; tout le reste doit **exister**.

    `projet_connu` est ce qui sait répondre « cet identifiant est-il déclaré ? » —
    en pratique le `ServiceProjets` de l'app, dont seul `existe` est appelé.
    Passé à `None` (une projection isolée, un test de la seule règle de
    filtrage), l'existence n'est pas vérifiée : la portée se résout alors sur la
    seule forme de l'identifiant. Le refus d'un identifiant **mal formé** reste,
    lui, systématique — il ne peut désigner aucun projet du dépôt (#221).
    """
    valeur = (brut or "").strip()
    if not valeur:
        raise PorteeRefusee(
            "projet-requis",
            "Portée projet manquante : précisez ?projet=<id> pour un projet, "
            f"?projet={PORTEE_TOUS} pour la vue transverse, ou ?projet={PORTEE_AUCUN} "
            "pour les travaux sans projet.",
        )
    if valeur == PORTEE_TOUS:
        return PorteeProjet.tous()
    if valeur == PORTEE_AUCUN:
        return PorteeProjet.aucun()
    identifiant = projet_id_valide(valeur)
    if identifiant is None or not _existe(identifiant, projet_connu):
        raise PorteeRefusee(
            "projet-inconnu",
            f"Projet inconnu : {valeur} (voir GET /api/projets) — ou "
            f"?projet={PORTEE_TOUS} pour la vue transverse.",
        )
    return PorteeProjet.projet(identifiant)


def _existe(identifiant: str, projet_connu: object) -> bool:
    """Le projet est-il déclaré ? Vrai d'office quand personne ne peut le dire.

    Un dépôt injoignable ne doit pas transformer une lecture en panne : la
    portée est alors accordée sur la seule forme de l'identifiant, et la vue
    ressortira vide si rien ne lui correspond. Refuser ici ferait d'un incident
    de stockage un « projet inconnu », c'est-à-dire un diagnostic faux.
    """
    if projet_connu is None:
        return True
    try:
        return bool(projet_connu.existe(identifiant))  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover - dépôt illisible : on n'invente pas un refus
        return True

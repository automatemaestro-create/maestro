"""Le lexique d'écriture d'un playbook — structures et tournures du dépôt (#261).

L'éditeur de playbook de la Control Tower propose des **complétions en cours de frappe**
(ticket #261). Ce qu'il propose n'est pas inventé : c'est ce que les playbooks livrés avec
le paquet (`maestro/agents/playbooks_defaut/`, #295) ont **en commun**.

Deux familles, dérivées des mêmes documents :

- les **structures** — les titres de section (`## Mission`, `## Méthode`, `## Garde-fous`…).
  Cinq rôles sur cinq partagent le même squelette : c'est *la* forme d'un playbook ici, et
  c'est la première chose qui manque à qui en écrit un ;
- les **tournures** — les phrases courtes qu'on retrouve d'un rôle à l'autre (« Quand deux
  options se valent, choisis-en une, dis pourquoi, et avance. »). Elles viennent pour
  l'essentiel des fragments partagés `_socle.md` / `_cadre_outille.md`, c'est-à-dire du
  régime de travail commun à tous les agents.

⚠ **Dérivé, jamais recopié.** Le premier réflexe serait une constante côté front, écrite à
la main ; elle mentirait au premier playbook modifié, sans que rien ne le signale. Le
lexique se recalcule donc à partir des documents eux-mêmes et est servi par l'API
(`GET /api/playbooks/lexique`) : renommer une section dans `_socle.md` change ce que
l'éditeur propose, sans toucher une ligne de TypeScript.

Le **seuil de récurrence** (`SEUIL_ROLES`, 2) est le contenu de la décision : ce module
répond à « qu'est-ce qui est récurrent ? », pas à « qu'est-ce qui existe ? ». Un titre
présent chez un seul rôle est une particularité de ce rôle (`## Le verdict` chez QA,
`## Dettes et risques` chez le Développeur) — le proposer à tout le monde diffuserait une
singularité au lieu d'une convention.

Comme `maestro.agents.playbook_du_code`, dont il est le seul client, ce module **n'importe
rien d'autre du paquet** : il n'a besoin ni du catalogue, ni du stockage versionné.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache

from maestro.agents.playbook_du_code import playbook_du_code, roles_du_code

#: Nombre minimal de playbooks du dépôt où une entrée doit figurer pour être proposée.
#: À 1, le lexique rendrait tout ce qui a été écrit une fois ; à 2, ce qui s'est répété.
SEUIL_ROLES = 2

#: Bornes d'une tournure, en caractères. En dessous, ce n'est pas une tournure mais un
#: mot — une complétion qui n'épargne rien ; au-dessus, c'est un paragraphe, qu'on ne fait
#: pas apparaître d'un coup sous le curseur de quelqu'un qui écrit.
TOURNURE_MIN = 20
TOURNURE_MAX = 110

#: Un titre de section Markdown de niveau 2 ou 3 — le grain de structure d'un playbook.
#: Le niveau 1 est le titre du document (« # Playbook — Développeur ») : il porte le nom
#: du rôle, donc ne se partage pas.
_TITRE = re.compile(r"^#{2,3} \S")

#: Fin de phrase forte. Le point-virgule en fait partie : les playbooks énumèrent
#: beaucoup en puces terminées par `;`, et chacune est une tournure à part entière.
_FIN_DE_PHRASE = re.compile(r"(?<=[.!?;])\s+")

#: L'amorce d'une puce ou d'une énumération, retirée avant de peser une phrase : c'est le
#: texte qui se répète d'un playbook à l'autre, pas le tiret ou le numéro qui le porte.
_AMORCE = re.compile(r"^(?:[-*+]|\d+\.)\s+")


@dataclass(frozen=True)
class EntreeLexique:
    """Une entrée proposable, et de quoi la justifier à l'écran.

    `roles` est le nombre de playbooks du dépôt où l'entrée figure : l'éditeur s'en sert
    pour ordonner ses propositions et pour dire d'où elles viennent — une suggestion dont
    on voit la provenance se refuse en connaissance de cause.
    """

    texte: str
    roles: int

    def to_dict(self) -> dict[str, object]:
        return {"texte": self.texte, "roles": self.roles}


@cache
def lexique() -> tuple[tuple[EntreeLexique, ...], tuple[EntreeLexique, ...]]:
    """Les (structures, tournures) récurrentes des playbooks du code.

    Chaque famille est triée par récurrence décroissante, puis par **position moyenne**
    dans les documents : `## Mission` précède `## Format de sortie` parce qu'elle vient
    plus tôt partout, et non parce qu'un ordre a été figé à la main quelque part.

    Résultat mis en cache : les documents sont livrés avec le paquet, ils ne changent pas
    en cours d'exécution.
    """
    titres: dict[str, list[float]] = {}
    phrases: dict[str, list[float]] = {}
    for role in roles_du_code():
        _releve(playbook_du_code(role), titres, phrases)
    return _retenues(titres), _retenues(phrases)


def lexique_dict() -> dict[str, list[dict[str, object]]]:
    """Le lexique dans la forme servie par l'API (#261)."""
    structures, tournures = lexique()
    return {
        "structures": [e.to_dict() for e in structures],
        "tournures": [e.to_dict() for e in tournures],
    }


def _releve(
    texte: str, titres: dict[str, list[float]], phrases: dict[str, list[float]]
) -> None:
    """Relève titres et phrases d'un document, chacun avec sa position relative.

    Une entrée vue **plusieurs fois dans le même document** ne compte qu'une fois : le
    seuil pèse un nombre de rôles, pas un nombre d'occurrences — sans quoi un playbook
    bavard suffirait à faire passer sa propre tournure pour une convention partagée.
    """
    lignes = texte.splitlines()
    total = max(len(lignes), 1)
    vus_titres: dict[str, float] = {}
    vus_phrases: dict[str, float] = {}
    for rang, ligne in enumerate(lignes):
        nu = ligne.strip()
        if _TITRE.match(nu):
            vus_titres.setdefault(nu, rang / total)
    for rang, bloc in _blocs(lignes):
        for phrase in _phrases(bloc):
            vus_phrases.setdefault(phrase, rang / total)
    for table, vus in ((titres, vus_titres), (phrases, vus_phrases)):
        for entree, position in vus.items():
            table.setdefault(entree, []).append(position)


def _blocs(lignes: list[str]) -> list[tuple[int, str]]:
    """Les paragraphes du corps, recollés, avec le rang de leur première ligne.

    Les documents sont **enveloppés à ~95 colonnes** : découper en phrases ligne par ligne
    couperait la moitié d'entre elles au milieu, et le lexique ne retiendrait que celles
    qui tiennent par chance sur une ligne. Un bloc court d'une ligne non vide à la
    suivante ; une puce, une énumération ou un titre en ouvre un nouveau.
    """
    blocs: list[tuple[int, str]] = []
    courant: list[str] = []
    depart = 0
    for rang, ligne in enumerate(lignes):
        nu = ligne.strip()
        rupture = not nu or nu.startswith("#") or bool(_AMORCE.match(nu))
        if rupture and courant:
            blocs.append((depart, " ".join(courant)))
            courant = []
        if not nu or nu.startswith("#"):
            continue
        if not courant:
            depart = rang
        courant.append(nu)
    if courant:
        blocs.append((depart, " ".join(courant)))
    return blocs


def _phrases(ligne: str) -> list[str]:
    """Les phrases retenables d'un bloc de corps, amorce de puce retirée.

    Le découpage est volontairement grossier — une phrase de playbook n'a pas à être
    reconnue exactement, seulement à être reconnaissable par qui la retape.
    """
    retenues = []
    for brut in _FIN_DE_PHRASE.split(ligne):
        phrase = _AMORCE.sub("", brut).strip()
        if TOURNURE_MIN <= len(phrase) <= TOURNURE_MAX:
            retenues.append(phrase)
    return retenues


def _retenues(table: dict[str, list[float]]) -> tuple[EntreeLexique, ...]:
    """Les entrées au-dessus du seuil, ordonnées (récurrence ↓, position moyenne ↑)."""
    return tuple(
        EntreeLexique(texte=texte, roles=len(positions))
        for texte, positions in sorted(
            (t for t in table.items() if len(t[1]) >= SEUIL_ROLES),
            key=lambda t: (-len(t[1]), sum(t[1]) / len(t[1]), t[0]),
        )
    )

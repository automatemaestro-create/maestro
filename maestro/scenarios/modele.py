"""Ce qu'un passage du banc produit : un verdict par scénario, et de quoi le relire (#1148).

Objets inertes — ils décrivent et sérialisent, ils ne touchent ni au disque ni à
l'API. C'est ce qui permet de juger la forme du rapport sans jouer un scénario.

**Deux verdicts, et pas trois.** Le code de sortie du banc répond à une seule
question — *les quatre sont-ils verts ?* —, et un troisième état ne changerait pas
la réponse : un scénario que le banc n'a pas pu jouer n'est pas vert, donc il
bloque un bouclage (#1152) comme un scénario qui a échoué. Ce qui les distingue
n'a pourtant pas à se perdre, parce qu'on ne les répare pas de la même façon :
`Resultat.empechement` le porte à côté du motif, et le rapport le dit en mots. Une
zone grise dans le **verdict** aurait fait le contraire — elle aurait laissé
passer un « ni vert ni rouge » pour un vert.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

#: Le scénario a fait ce qu'on lui demandait, et son oracle l'a constaté.
VERDICT_VERT = "vert"
#: Il ne l'a pas fait, ou le banc n'a pas pu le mener jusqu'à son oracle.
VERDICT_ROUGE = "rouge"


def horodatage() -> str:
    """L'horodatage d'un passage — le format d'un identifiant de run d'outillage.

    `20260922-143012` : triable en ordre lexical, sans séparateur qu'un nom de
    dossier refuserait sous Windows. C'est la même convention que `RUN_ID` de
    `scripts/orchestrate/run.sh`, parce que les deux nomment la même chose — un
    passage daté qu'on retrouve à l'œil nu.
    """
    return datetime.now().strftime("%Y%m%d-%H%M%S")


@dataclass(frozen=True)
class Etape:
    """Une étape du déroulé : ce que le banc a fait, et ce qu'il a vu.

    C'est la pièce du verdict. Un rouge sans déroulé obligerait à rejouer le banc
    pour savoir où il s'est arrêté — c'est-à-dire à repayer le modèle.
    """

    libelle: str
    detail: str = ""

    def to_dict(self) -> dict[str, str]:
        """L'étape en JSON — la forme du rapport."""
        return {"libelle": self.libelle, "detail": self.detail}


@dataclass
class Journal:
    """Le déroulé d'un scénario, en train de s'écrire."""

    etapes: list[Etape] = field(default_factory=list)

    def note(self, libelle: str, detail: str = "") -> None:
        """Consigne une étape."""
        self.etapes.append(Etape(libelle=libelle, detail=detail))


@dataclass(frozen=True)
class Issue:
    """Ce qu'un scénario rend à la fin de son déroulé.

    `motif` dit **pourquoi** — pourquoi c'est vert, pourquoi c'est rouge — et il
    est obligatoire dans les deux sens : un vert sans raison ne se conteste pas,
    et c'est exactement ce qu'on reproche à un bilan coché sans pièce.
    """

    verdict: str
    motif: str
    run_id: str = ""
    cout_usd: float | None = None
    empechement: bool = False

    @property
    def vert(self) -> bool:
        """L'oracle est-il satisfait ?"""
        return self.verdict == VERDICT_VERT


def vert(motif: str, **reste: Any) -> Issue:
    """Un scénario dont l'oracle est satisfait."""
    return Issue(verdict=VERDICT_VERT, motif=motif, **reste)


def rouge(motif: str, **reste: Any) -> Issue:
    """Un scénario dont l'oracle n'est pas satisfait."""
    return Issue(verdict=VERDICT_ROUGE, motif=motif, **reste)


def empeche(motif: str, **reste: Any) -> Issue:
    """Un scénario que le banc n'a pas pu mener jusqu'à son oracle.

    Rouge, parce qu'il n'est pas vert — et marqué, parce qu'une API injoignable ne
    se répare pas comme un produit qui se trompe.
    """
    return Issue(verdict=VERDICT_ROUGE, motif=motif, empechement=True, **reste)


@dataclass(frozen=True)
class Resultat:
    """Le rapport d'un scénario : verdict, coût, durée, `run_id` — et son déroulé."""

    identifiant: str
    titre: str
    verdict: str
    motif: str
    duree_s: float
    run_id: str = ""
    projet_id: str = ""
    racine: str = ""
    cout_usd: float | None = None
    rejoue: bool = False
    empechement: bool = False
    etapes: tuple[Etape, ...] = ()

    @property
    def vert(self) -> bool:
        """Ce scénario est-il vert ?"""
        return self.verdict == VERDICT_VERT

    def to_dict(self) -> dict[str, Any]:
        """Le résultat en JSON — la forme que `/milestone-bilan` relira (#1152)."""
        return {
            "id": self.identifiant,
            "titre": self.titre,
            "verdict": self.verdict,
            "motif": self.motif,
            "duree_s": round(self.duree_s, 3),
            "run_id": self.run_id,
            "projet_id": self.projet_id,
            "racine": self.racine,
            "cout_usd": self.cout_usd,
            "rejoue": self.rejoue,
            "empechement": self.empechement,
            "etapes": [e.to_dict() for e in self.etapes],
        }


@dataclass(frozen=True)
class Rapport:
    """Un passage du banc : son horodatage, et ses résultats dans l'ordre joué."""

    horodatage: str
    resultats: tuple[Resultat, ...]

    @property
    def vert(self) -> bool:
        """Tous les scénarios joués sont-ils verts ?

        Un passage **sans aucun scénario** n'est pas vert : rien n'a été vérifié,
        et rendre « tout va bien » sur une liste vide est la façon la plus sûre de
        faire boucler un jalon sur du vide.
        """
        return bool(self.resultats) and all(r.vert for r in self.resultats)

    @property
    def cout_usd(self) -> float | None:
        """Ce que le passage a coûté — `None` si aucun run n'a rapporté de montant."""
        connus = [r.cout_usd for r in self.resultats if r.cout_usd is not None]
        return sum(connus) if connus else None

    @property
    def duree_s(self) -> float:
        """La somme des durées des scénarios — le passage étant séquentiel."""
        return sum(r.duree_s for r in self.resultats)

    def to_dict(self) -> dict[str, Any]:
        """Le passage en JSON."""
        return {
            "horodatage": self.horodatage,
            "vert": self.vert,
            "cout_usd": self.cout_usd,
            "duree_s": round(self.duree_s, 3),
            "scenarios": [r.to_dict() for r in self.resultats],
        }

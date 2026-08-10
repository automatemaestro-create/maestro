"""Les sources d'un objectif : le modèle (ticket #315, EF-39, entité SOURCE de docs/03).

Un lancement ne portait qu'un `objectif` **texte**
(`LancementExecutionRequete`, `maestro/controltower/app.py`) : il n'existait
aucun endroit où dire ce qu'un objectif embarque — le cahier des charges à
reprendre, le dossier de maquettes à consulter, la page de spécification à
lire —, donc aucune frontière à ce qui entre dans le contexte. Une **source**
est cet endroit : une matière d'entrée **déclarée**, typée et bornée, attachée
au run à côté de son objectif.

Trois types, ceux du contrat déjà figé
([docs/24 §3.2](../../docs/24-projets-locaux-et-poste-de-travail.md)) :
`fichier` (téléversé), `dossier` (références en lecture seule) et `url`. Le
`texte` que mentionne l'entité SOURCE de [docs/03](../../docs/03-modele-de-donnees.md)
n'en est pas un ici : c'est l'objectif lui-même, qui a déjà son champ.

**Ce module ne lit aucun contenu et ne refuse rien** — il ne fait que porter la
forme. La résolution (canonicalisation d'un dossier, schéma d'une URL,
emplacement d'ingestion d'un fichier) et les refus motivés vivent dans
`maestro.sources.resolution` ; l'extraction du texte, elle, est le lot suivant
(#316) et ajoutera son champ sans changer celui-ci.

C'est une **feuille**, comme `maestro.references` (#187) et
`maestro.detail_tache` (#246), et pour la même raison : une source voyage du
lancement (`maestro.controltower.executions`) jusqu'à la projection en passant
par les événements, des couches qui ne peuvent pas dépendre les unes des
autres. `maestro.controltower.events` le ré-exporte.

Une règle gouverne la relecture, et elle est l'inverse de celle de la
résolution : **rien ne se refuse au rejeu**. `from_dict` et `sources_depuis`
reconstruisent ce qui a été accepté un jour sans le rejuger — un journal
durable (#97) relu après un durcissement des garde-fous doit rester lisible, et
un run passé ne se réécrit pas. Le refus a lieu **une fois**, au lancement, là
où quelqu'un peut encore corriger sa saisie.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

#: Un fichier téléversé : sa matière est copiée dans l'emplacement d'ingestion
#: du run (jamais dans le projet de l'utilisateur — cf. `resolution`).
TYPE_FICHIER = "fichier"
#: Un dossier de **références** sur le disque : consulté, jamais écrit
#: (`lecture_seule`). C'est une référence, pas un projet (docs/03, SOURCE).
TYPE_DOSSIER = "dossier"
#: Une page à lire, en `http(s)` seulement (cf. `resolution`).
TYPE_URL = "url"

#: Les seuls types admis. Un type inconnu est **refusé** à la résolution plutôt
#: qu'ignoré : une source qu'on ne sait pas résoudre est une matière que
#: l'utilisateur croit avoir jointe et qui n'arriverait jamais.
TYPES_SOURCE: tuple[str, ...] = (TYPE_FICHIER, TYPE_DOSSIER, TYPE_URL)

#: Longueur maximale d'un nom de source — la borne d'un nom de fichier sur les
#: systèmes usuels (NTFS, ext4, APFS). Au-delà, ce n'est plus un nom : c'est un
#: collage, et l'écriture échouerait de toute façon au moment de l'ingestion.
LONGUEUR_MAX_NOM = 255


class SourceRefusee(ValueError):
    """Une source refusée, **avec son motif** — jamais un rejet muet (#315).

    Même contrat que `RacineRefusee` (#221) dont elle reprend les motifs quand
    le refus vient de la validation de racine : `motif` est un code stable et
    court (`type-inconnu`, `url-non-suivable`, `source-trop-volumineuse`,
    `chemin-sensible`…), le message restant la phrase lisible. `index` situe la
    source fautive dans la liste envoyée — sans lui, « une source est trop
    grosse » n'aide personne à savoir laquelle retirer.

    Hérite de `ValueError` à dessein : la route de lancement
    (`POST /api/executions`) la transforme déjà en **422** par la branche qui
    existe pour les garde-fous hors bornes — une source refusée est une requête
    invalide, pas une panne.
    """

    def __init__(self, motif: str, message: str, *, index: int | None = None) -> None:
        super().__init__(message)
        self.motif = motif
        self.index = index


@dataclass(frozen=True)
class Source:
    """Une matière d'entrée déclarée par un objectif (EF-39, entité SOURCE).

    `type` est l'un des `TYPES_SOURCE`. Les autres champs sont renseignés selon
    le type, et c'est la résolution qui les remplit :

    - `nom` — le libellé montrable (nom du fichier, du dossier, de la page) ;
    - `chemin` — le chemin **résolu** d'un `fichier` (dans l'emplacement
      d'ingestion du run) ou d'un `dossier` (canonicalisé, sur le disque de
      l'utilisateur) ; vide pour une `url` ;
    - `valeur` — l'URL d'une source `url` ; vide pour les deux autres. Deux
      champs plutôt qu'un seul « où », parce que ce ne sont pas les mêmes
      espaces de noms : un chemin se compare, se contient et se canonicalise,
      une URL non ;
    - `taille` — la taille en **octets** quand elle est connue (un fichier),
      `None` sinon (un dossier n'est pas mesuré, une URL pas encore récupérée).
      C'est elle que plafonnent les garde-fous d'ingestion ;
    - `lecture_seule` — Maestro n'écrit **jamais** dans une source. Vrai par
      défaut, et **forcé** à vrai sur un `dossier` : c'est une référence, pas un
      projet (docs/03), et la déclarer en écriture serait un projet déguisé qui
      n'aurait passé aucun des contrôles de la Phase 7.

    Immuable : ce qui est résolu ne se retouche pas, on résout à nouveau.
    """

    type: str
    nom: str = ""
    chemin: str = ""
    valeur: str = ""
    taille: int | None = None
    lecture_seule: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Réémet la source en dict JSON-sérialisable (la forme du contrat #183)."""
        return {
            "type": self.type,
            "nom": self.nom,
            "chemin": self.chemin,
            "valeur": self.valeur,
            "taille": self.taille,
            "lecture_seule": self.lecture_seule,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Source:
        """Reconstruit une source depuis sa forme `to_dict` (aller-retour JSON).

        **Ne rejuge rien** (cf. docstring du module) : c'est la relecture d'une
        source déjà acceptée — événement du bus, journal durable rejoué — et non
        une saisie. Les clés absentes retombent sur les défauts.
        """
        taille = data.get("taille")
        return cls(
            type=str(data.get("type") or ""),
            nom=str(data.get("nom") or ""),
            chemin=str(data.get("chemin") or ""),
            valeur=str(data.get("valeur") or ""),
            taille=taille if isinstance(taille, int) and not isinstance(taille, bool) else None,
            lecture_seule=bool(data.get("lecture_seule", True)),
        )

    @property
    def vide(self) -> bool:
        """La source n'apprend-elle rien (ni type, ni emplacement) ?"""
        return not self.type or not (self.nom or self.chemin or self.valeur)


def sources_depuis(data: Any) -> list[Source]:
    """Les sources lisibles d'une valeur brute — **liste vide** s'il n'y en a pas.

    Le pendant de `etapes_depuis` (#246) : l'ordre est celui du flux (une liste
    de pièces jointes se lit dans l'ordre où elle a été composée), et ce qui
    n'apprend rien est écarté entrée par entrée — une ligne illisible ne fait
    pas perdre les autres. Comme `from_dict`, c'est de la **relecture** : rien
    n'y est refusé.
    """
    lues = (Source.from_dict(brut) for brut in _sequence(data) if isinstance(brut, Mapping))
    return [source for source in lues if not source.vide]


def sources_en_liste(sources: Sequence[Source] | None) -> list[dict[str, Any]]:
    """La forme JSON d'une liste de sources — `[]` quand il n'y en a aucune.

    Le pendant de `ticket_en_dict` (#187) et d'`etapes_en_liste` (#246) : côté
    client, une liste vide dit « aucune source » et la vue reste strictement
    celle d'avant ce lot.
    """
    return [source.to_dict() for source in sources or ()]


def _sequence(data: Any) -> tuple[Any, ...]:
    """Les entrées d'une valeur brute censée être une liste — `()` sinon.

    Une chaîne est une `Sequence` : l'exclure explicitement évite de lire
    « https://… » comme une liste de caractères, chacun donnant une source vide.
    """
    if isinstance(data, Sequence) and not isinstance(data, str | bytes):
        return tuple(data)
    return ()

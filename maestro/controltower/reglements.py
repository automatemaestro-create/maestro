"""Ce qui **attend quelqu'un** pendant un run, réglé depuis le fil (#1183).

Un run qui attend une personne l'attendait sur un autre écran : la **question** d'un
agent (#1023, `POST /api/questions/{id}/reponse`) et la **validation** d'une action
sensible (#48, `POST /api/validations/{tache}/decision`). Dire au fil « réponds-lui :
prends Postgres », « oui, valide » ou « refuse et archive plutôt » n'avait aucun
effet : il ne pouvait que renvoyer vers l'écran concerné. Il règle désormais ces
attentes, par les **mêmes** services que les écrans (`maestro.controltower.attentes`).

Ce module ne porte que le **vocabulaire** que quatre couches partagent — le service
qui règle, le fil qui propose puis règle sur accord, la route qui sert la
confirmation, le message persisté qui porte la carte — et les deux objets du fil : ce
qu'une carte **propose** (`ReglementPropose`) et ce que la confirmation a **donné**
(`ReglementFait`). Il ne tire rien, pour la raison de `maestro.controltower.gestes` :
le fil (`chat`) et l'orchestration restent jouables sans la projection, qui tire la
couche d'exécution entière.

## Trois règlements, deux attentes

- `reponse` — répondre à la question d'un agent, **avec un texte** : l'agent suspendu
  le lit et reprend (#1023). Répondre n'approuve **aucun** acte (EF-08, docs/32 §5) ;
- `approbation` — approuver l'acte qu'une validation retient : la tâche reprend ;
- `refus` — le refuser, **avec sa raison** quand la personne en donne une. La raison
  voyage par le même champ que le motif de l'écran des validations (#272), que #1185
  porte jusqu'à l'agent comme consigne ; le fil n'en ouvre pas un second.

Le **genre** de l'attente se déduit de l'action (`GENRE_DU_REGLEMENT`) : une question
ne s'approuve pas, une validation ne se répond pas. Le contrat du juge n'a donc pas à
le redire, et un identifiant ne peut pas viser la mauvaise file.

## Le refus est un fait, avec son motif

`ReglementRefuse` est ce que le service rend quand l'état de l'attente interdit le
règlement — une question déjà répondue, une validation déjà tranchée, une réponse
vide. `motif` est un code stable que les routes traduisent en statut HTTP, le message
la phrase que l'écran et le fil montrent telle quelle : la règle de `GesteRefuse`
(#1179), et pour la même raison — les règles vivent une fois, dans le service.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

#: Répondre à la question d'un agent, avec un texte (#1023).
REGLEMENT_REPONSE = "reponse"

#: Approuver l'acte qu'une validation retient (#48).
REGLEMENT_APPROBATION = "approbation"

#: Refuser l'acte qu'une validation retient — avec sa raison, facultative (#272).
REGLEMENT_REFUS = "refus"

#: Les trois, dans l'ordre où le prompt et l'écran les nomment. Une liste **blanche** :
#: un règlement hors de ces trois n'atteint jamais le service.
REGLEMENTS = (REGLEMENT_REPONSE, REGLEMENT_APPROBATION, REGLEMENT_REFUS)

#: Les deux files d'attente humaine d'un run.
GENRE_QUESTION = "question"
GENRE_VALIDATION = "validation"

#: La file que chaque règlement vise — déduite, jamais écrite par le modèle.
GENRE_DU_REGLEMENT = {
    REGLEMENT_REPONSE: GENRE_QUESTION,
    REGLEMENT_APPROBATION: GENRE_VALIDATION,
    REGLEMENT_REFUS: GENRE_VALIDATION,
}

#: Le verbe de chaque règlement, à l'infinitif — ce que la transcription dit d'une
#: carte et d'un fait au tour suivant, et ce que l'orchestration écrit d'un
#: empêchement. Une table, pour que le fil et le modèle le nomment des mêmes mots.
VERBES_DE_REGLEMENT = {
    REGLEMENT_REPONSE: "répondre à",
    REGLEMENT_APPROBATION: "approuver",
    REGLEMENT_REFUS: "refuser",
}

#: Aucune attente ne porte cet identifiant — `404` sur les routes.
MOTIF_ATTENTE_INCONNUE = "attente-inconnue"

#: L'attente est déjà réglée : question répondue, validation tranchée — `409`.
MOTIF_ATTENTE_REGLEE = "attente-reglee"

#: Une réponse vide n'apprend rien à l'agent — `422` (#1023).
MOTIF_REPONSE_VIDE = "reponse-vide"

#: Une approbation étendue que la demande ne permet pas — étendue inconnue, demande
#: sans acte, run ou projet introuvables — `422` (#1185).
MOTIF_ETENDUE_REFUSEE = "etendue-refusee"


class ReglementRefuse(ValueError):
    """Un règlement refusé par l'état de l'attente, **avec son motif** — jamais un rejet muet.

    Hérite de `ValueError` pour la raison de `GesteRefuse` (#1179) : c'est une requête
    que l'état rend invalide, pas une panne. `motif` est l'un des `MOTIF_*`, que les
    routes traduisent en statut HTTP ; le message est la phrase que l'écran et le fil
    montrent telle quelle.
    """

    def __init__(self, motif: str, message: str) -> None:
        super().__init__(message)
        self.motif = motif


@dataclass(frozen=True)
class AttenteVisee:
    """Une attente **telle que le fil l'a montrée** — ce qu'une carte vise (#1183).

    Recopiée de la projection au moment où le fil en parle, comme `RunVise` (#1179) :
    une carte montre la question ou l'acte sur lequel la personne dit oui, et le fait
    d'après la confirmation dit ce qui en est sorti. Ce sont deux instants.

    - `genre` : `question` ou `validation` ;
    - `identifiant` : la question (`question_id`) ou la tâche que la validation
      retient (`tache_id`) — la clé de chacune des deux files ;
    - `objet` : ce que la personne tranche, en une ligne — la question posée, ou
      l'acte (l'outil et ses arguments, déjà expurgés), à défaut ce qu'il ferait ;
    - `outil` : l'outil d'un acte, vide pour une validation de tâche ou d'écriture
      dans le projet — c'est ce qui dit ce qu'un refus laisse faire à l'agent ;
    - `hypothese` et `echeance` : pour une question, ce que l'agent fera sans réponse
      et jusqu'à quand il l'attend — c'est ce qui dit si la réponse le fait reprendre
      ou le rattrape plus tard (#1025).
    """

    genre: str
    identifiant: str
    agent: str = ""
    role: str = ""
    titre: str = ""
    objet: str = ""
    run_id: str = ""
    outil: str = ""
    hypothese: str = ""
    echeance: str = ""

    def to_dict(self) -> dict[str, Any]:
        """L'attente en JSON — la forme du REST et du stockage."""
        return {
            "genre": self.genre,
            "identifiant": self.identifiant,
            "agent": self.agent,
            "role": self.role,
            "titre": self.titre,
            "objet": self.objet,
            "run_id": self.run_id,
            "outil": self.outil,
            "hypothese": self.hypothese,
            "echeance": self.echeance,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> AttenteVisee:
        """Relit une attente persistée, sans rien rejuger (même règle que `MessageChat`)."""
        return cls(
            genre=str(data.get("genre") or ""),
            identifiant=str(data.get("identifiant") or ""),
            agent=str(data.get("agent") or ""),
            role=str(data.get("role") or ""),
            titre=str(data.get("titre") or ""),
            objet=str(data.get("objet") or ""),
            run_id=str(data.get("run_id") or ""),
            outil=str(data.get("outil") or ""),
            hypothese=str(data.get("hypothese") or ""),
            echeance=str(data.get("echeance") or ""),
        )

    def en_phrase(self) -> str:
        """L'attente en une ligne — ce que le modèle relit au tour suivant."""
        porteur = self.role or self.agent
        de = f" de l'agent {self.agent}" if self.agent else ""
        if self.genre == GENRE_QUESTION:
            quoi = f"la question{de} « {self.objet} »" if self.objet else f"la question{de}"
        else:
            quoi = f"la validation{de} — {self.objet}" if self.objet else f"la validation{de}"
        details = [f"identifiant {self.identifiant}"]
        if porteur and porteur != self.agent:
            details.append(porteur)
        if self.titre:
            details.append(f"tâche « {self.titre} »")
        if self.run_id:
            details.append(f"run {self.run_id}")
        return f"{quoi} ({', '.join(details)})"


def attentes_visees_depuis(brut: Any) -> tuple[AttenteVisee, ...]:
    """Les attentes d'une ligne relue — `()` sur un message écrit avant #1183."""
    if not isinstance(brut, Sequence) or isinstance(brut, str | bytes):
        return ()
    lues = [AttenteVisee.from_dict(item) for item in brut if isinstance(item, Mapping)]
    return tuple(attente for attente in lues if attente.identifiant)


@dataclass(frozen=True)
class ReglementPropose:
    """Un règlement **proposé** à la confirmation de la personne (#1183).

    `action` est l'un des `REGLEMENTS` ; `attente`, ce qu'il vise tel que la carte le
    montre ; `texte`, la réponse que l'agent recevra (`reponse`) ou la raison d'un
    refus (`refus`, vide pour un refus sans motif) — ignoré sur une approbation ;
    `suite`, ce que le règlement fera, dit **avant** qu'il parte, par le service qui le
    fera (`ServiceAttentes.suite`) — la carte le montre, et le fait d'après le redit
    des mêmes mots.

    Rien n'est réglé tant que la personne n'a pas dit oui, d'un geste ou d'un mot :
    c'est la règle des six autres demandes du fil, et celle de docs/33 ② —
    l'orchestrateur ne décide rien de lui-même. Ce qui part est **ce que la carte
    montre**, relu du fil : ni une reformulation du modèle au tour suivant, ni un
    texte recopié par l'appelant.
    """

    action: str
    attente: AttenteVisee
    texte: str = ""
    suite: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Le règlement en JSON — la forme du REST et du stockage."""
        return {
            "action": self.action,
            "attente": self.attente.to_dict(),
            "texte": self.texte,
            "suite": self.suite,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ReglementPropose:
        """Relit un règlement persisté, sans rien rejuger (même règle que `MessageChat`)."""
        attente = data.get("attente")
        return cls(
            action=str(data.get("action") or ""),
            attente=_attente_depuis(attente),
            texte=str(data.get("texte") or ""),
            suite=str(data.get("suite") or ""),
        )

    def en_phrase(self) -> str:
        """Le règlement en une ligne — ce que le modèle relit de la carte au tour suivant."""
        verbe = VERBES_DE_REGLEMENT.get(self.action, self.action)
        phrase = f"{verbe} {self.attente.en_phrase()}"
        if self.action == REGLEMENT_REPONSE:
            phrase += f", réponse transmise : « {self.texte} »"
        elif self.action == REGLEMENT_REFUS:
            phrase += f", raison : « {self.texte} »" if self.texte else ", sans raison donnée"
        return phrase


@dataclass(frozen=True)
class ReglementFait:
    """Ce qu'un règlement confirmé a **donné** — ce qui a repris, ou le refus (#1183).

    Le pendant de `ReglementPropose` après la confirmation, comme `GesteRunFait` l'est
    de `GesteRunPropose` (#1179) : `texte` est ce qui est parti (la réponse, la
    raison du refus), `suite` ce que le service dit qu'il en sort — « la tâche
    reprend », « l'agent poursuit sa tâche sans cet appel » —, et `refus` la phrase du
    service quand l'état de l'attente a refusé le règlement : alors rien n'est parti.
    """

    action: str
    attente: AttenteVisee
    texte: str = ""
    suite: str = ""
    refus: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Le fait en JSON — la forme du REST et du stockage."""
        return {
            "action": self.action,
            "attente": self.attente.to_dict(),
            "texte": self.texte,
            "suite": self.suite,
            "refus": self.refus,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ReglementFait:
        """Relit un fait persisté, sans rien rejuger (même règle que `MessageChat`)."""
        return cls(
            action=str(data.get("action") or ""),
            attente=_attente_depuis(data.get("attente")),
            texte=str(data.get("texte") or ""),
            suite=str(data.get("suite") or ""),
            refus=str(data.get("refus") or ""),
        )

    def en_phrase(self) -> str:
        """Le fait en une ligne — ce que le modèle relit au tour suivant."""
        verbe = VERBES_DE_REGLEMENT.get(self.action, self.action)
        vise = self.attente.en_phrase()
        if self.refus:
            return f"{verbe} {vise} : refusé, rien n'est parti — {self.refus}"
        suite = f" — {self.suite}" if self.suite else ""
        return f"{verbe} {vise} : fait{suite}"


def _attente_depuis(brut: Any) -> AttenteVisee:
    """L'attente d'une carte ou d'un fait relu — vide si la ligne n'en porte pas."""
    if isinstance(brut, Mapping):
        return AttenteVisee.from_dict(brut)
    return AttenteVisee(genre="", identifiant="")

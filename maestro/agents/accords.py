"""Les accords étendus d'un agent — « oui, et ne me redemande plus pour cet outil » (#1185).

Une approbation valait pour **un** appel : la même carte revenait à chaque `Bash`
d'une tâche qui en lance vingt. La personne peut désormais étendre son oui à
l'**outil**, pour la suite du **run** ou pour le **projet** (`maestro.decision_humaine`).
Ce module garde ce qu'elle a accordé, là où vivent les permissions de l'agent :

- **écrit par l'API**, au moment où la décision est prise et **avant** qu'elle parte
  vers le moteur (`maestro.controltower.attentes.ServiceAttentes.trancher`) : l'appel
  suivant de l'agent le trouve déjà ;
- **lu par le moteur** à chaque appel arbitré (`LocalExecutor._arbitre_acte`), relu à
  chaud comme la politique — un accord retiré depuis l'écran vaut **dès l'appel
  suivant**, pas à la tâche suivante ;
- **montré et retiré** dans les permissions de l'agent (fiche agent, onglet « MCP &
  permissions », `DELETE /api/permissions/{agent}/accords/{id}`).

## Ce qu'un accord couvre, et ce qu'il ne couvre pas

Il couvre **cet outil, par cet agent** — le nom exact que la demande portait, jamais
un préfixe : accorder `mcp__slack__send_message` n'accorde pas le serveur entier. Il
ne s'applique qu'**après** la politique : la liste `deny` refuse toujours, la
frontière d'écriture juge toujours avant, et un outil laissé passer (`allow`, `auto`)
n'avait rien à accorder. Ce qu'il retire est l'**attente d'une personne**, rien
d'autre — et la trace reste : l'appel passé sur accord laisse sa ligne au journal,
qui nomme l'accord (`Accord.detail`).

Un accord **de run** ne vaut que pour ce run : un run suivant redemande. Un accord
**de projet** vaut jusqu'à ce que quelqu'un le retire.

## Où il vit

`<racine des permissions du niveau>/_accords/<agent>.json` — le niveau d'un projet
(`core/permissions/_projets/<id>/`), ou celui des gabarits pour un run hors projet.
Le tiret bas le met hors d'atteinte d'un nom d'agent, comme `_projets`
(`maestro.agents.rangement`). **Rien n'est hérité** : un accord donné dans un projet
ne vaut pas dans un autre, et c'est pourquoi ce dépôt ne se replie jamais sur le
gabarit — contrairement à la politique, dont l'absence vaudrait « tout permis ». Ce
n'est pas de la configuration versionnée mais une décision d'usage : les deux
emplacements sont ignorés de git.
"""

from __future__ import annotations

import json
import os
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from maestro.decision_humaine import (
    ETENDUE_PROJET,
    ETENDUE_RUN,
    ETENDUES_DURABLES,
    portee_de_l_etendue,
)

#: Le dossier des accords, sous la racine des permissions d'un niveau. Le tiret bas
#: le met hors d'atteinte d'un nom d'agent (`[a-z0-9]…`), comme `_projets`.
DOSSIER_ACCORDS = "_accords"

#: Nom d'agent admissible comme fichier — même verrou que les dépôts voisins
#: (`maestro.agents.permissions`, `mcp`, `store`) : un slug, jamais un chemin.
_NOM_AGENT = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

#: Un nom d'outil tel qu'une politique l'écrit (`Bash`, `mcp__slack__send_message`).
_OUTIL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


@dataclass(frozen=True)
class Accord:
    """Un oui étendu : cet outil, par cet agent, pour la suite d'un run ou d'un projet.

    `tache_id` est la demande sur laquelle la personne l'a donné — ce qu'on relit pour
    savoir d'où vient l'accord. `accorde_le` est l'instant, en ISO UTC.
    """

    id: str
    agent: str
    outil: str
    etendue: str
    run_id: str = ""
    tache_id: str = ""
    accorde_le: str = ""

    def couvre(self, outil: str, run_id: str) -> bool:
        """L'appel de `outil` sur le run `run_id` passe-t-il sur cet accord ?

        Un accord de run ne couvre **que** son run, et jamais un appel hors de tout
        run (`run_id` vide) : on ne sait pas dire qu'il en fait partie.
        """
        if outil != self.outil:
            return False
        if self.etendue == ETENDUE_PROJET:
            return True
        return self.etendue == ETENDUE_RUN and bool(run_id) and run_id == self.run_id

    def detail(self) -> str:
        """Le détail traçable d'un appel passé sur cet accord — ce que le journal garde.

        Le pendant de `maestro.engine.guardrails.detail_accorde` pour l'accord donné
        **en cours de route** : personne n'est sollicité maintenant, mais quelqu'un
        l'a été, et la ligne dit quand et pour quoi.
        """
        quand = f", le {self.accorde_le}" if self.accorde_le else ""
        portee = portee_de_l_etendue(self.etendue, self.outil)
        return (
            f"accord donné pour la suite par la personne ({portee}{quand}) — aucune "
            "nouvelle demande n'a été composée"
        )

    def to_dict(self) -> dict[str, Any]:
        """La forme stockée et servie par l'API."""
        return {
            "id": self.id,
            "agent": self.agent,
            "outil": self.outil,
            "etendue": self.etendue,
            "run_id": self.run_id,
            "tache_id": self.tache_id,
            "accorde_le": self.accorde_le,
        }

    @classmethod
    def depuis(cls, data: Mapping[str, Any]) -> Accord | None:
        """Relit un accord stocké — `None` s'il ne se lit pas.

        Tolérant à dessein : un accord illisible est **ignoré**, pas appliqué à
        moitié — et l'ignorer ramène l'outil à l'arbitrage, c'est-à-dire au sens sûr.
        """
        try:
            accord = cls(
                id=str(data["id"]),
                agent=str(data["agent"]),
                outil=str(data["outil"]),
                etendue=str(data["etendue"]),
                run_id=str(data.get("run_id") or ""),
                tache_id=str(data.get("tache_id") or ""),
                accorde_le=str(data.get("accorde_le") or ""),
            )
        except (KeyError, TypeError):
            return None
        if accord.etendue not in ETENDUES_DURABLES or not _OUTIL.match(accord.outil):
            return None
        return accord


def horodatage() -> str:
    """L'instant présent en ISO UTC à la seconde — celui qu'un accord retient."""
    return datetime.now(UTC).replace(microsecond=0).isoformat()


class AccordStore:
    """Les accords étendus d'un niveau — un fichier JSON par agent (#1185).

    Construit par `PermissionStore.accords()`, qui lui donne sa racine : les accords
    d'un projet vivent sous le rangement de ce projet, ceux d'un run hors projet
    sous les gabarits. Écriture atomique, comme la politique voisine.
    """

    def __init__(self, racine: Path) -> None:
        self._racine = racine

    @property
    def racine(self) -> Path:
        """Le dossier des accords de ce niveau."""
        return self._racine

    def lire(self, agent: str) -> tuple[Accord, ...]:
        """Les accords de `agent` — vide sans fichier.

        Lève `ValueError` (agent nommé) si le fichier ne se lit pas : l'appelant qui
        juge un appel le mue en arbitrage ordinaire, celui qui affiche en erreur
        dite — jamais un accord deviné.
        """
        chemin = self._chemin(agent)
        if not chemin.is_file():
            return ()
        try:
            data = json.loads(chemin.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"accords étendus illisibles pour l'agent {agent!r} ({chemin.name}) : {exc}"
            ) from exc
        brut = data.get("accords") if isinstance(data, Mapping) else None
        if not isinstance(brut, list):
            return ()
        lus = (Accord.depuis(entree) for entree in brut if isinstance(entree, Mapping))
        return tuple(accord for accord in lus if accord is not None and accord.agent == agent)

    def couvre(self, agent: str, outil: str, run_id: str) -> Accord | None:
        """Le premier accord de `agent` qui couvre cet appel, ou `None`."""
        return next((a for a in self.lire(agent) if a.couvre(outil, run_id)), None)

    def accorder(
        self,
        *,
        agent: str,
        outil: str,
        etendue: str,
        run_id: str = "",
        tache_id: str = "",
    ) -> Accord:
        """Enregistre un accord et le rend — **idempotent** sur ce qu'il couvre.

        Un second oui étendu pour le même outil, la même étendue et le même run rend
        l'accord déjà posé au lieu d'en empiler un double que l'écran montrerait deux
        fois. Lève `ValueError` sur une étendue qui ne dure pas, un outil mal formé,
        ou un accord de run sans run : aucun ne s'écrit à moitié.
        """
        if etendue not in ETENDUES_DURABLES:
            raise ValueError(
                f"étendue {etendue!r} : un accord ne se pose que pour "
                f"{' ou '.join(ETENDUES_DURABLES)}."
            )
        if not _OUTIL.match(outil):
            raise ValueError(f"outil {outil!r} : nom d'outil mal formé.")
        if etendue == ETENDUE_RUN and not run_id:
            raise ValueError("un accord pour la suite du run demande de savoir quel run.")
        existants = list(self.lire(agent))
        for accord in existants:
            if (accord.outil, accord.etendue, accord.run_id) == (
                outil,
                etendue,
                run_id if etendue == ETENDUE_RUN else "",
            ):
                return accord
        nouveau = Accord(
            id=secrets.token_hex(6),
            agent=agent,
            outil=outil,
            etendue=etendue,
            run_id=run_id if etendue == ETENDUE_RUN else "",
            tache_id=tache_id,
            accorde_le=horodatage(),
        )
        self._ecrire(agent, [*existants, nouveau])
        return nouveau

    def retirer(self, agent: str, accord_id: str) -> Accord | None:
        """Retire l'accord `accord_id` de `agent` et le rend — `None` s'il n'existe pas."""
        existants = list(self.lire(agent))
        retire = next((a for a in existants if a.id == accord_id), None)
        if retire is None:
            return None
        self._ecrire(agent, [a for a in existants if a.id != accord_id])
        return retire

    def _ecrire(self, agent: str, accords: list[Accord]) -> None:
        chemin = self._chemin(agent)
        self._racine.mkdir(parents=True, exist_ok=True)
        temporaire = chemin.with_suffix(chemin.suffix + ".tmp")
        temporaire.write_text(
            json.dumps(
                {"accords": [a.to_dict() for a in accords]}, ensure_ascii=False, indent=2
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporaire, chemin)

    def _chemin(self, agent: str) -> Path:
        if not _NOM_AGENT.match(agent):
            raise ValueError(f"nom d'agent invalide : {agent!r} (slug [a-z0-9_-] attendu).")
        return self._racine / f"{agent}.json"

"""Reprise des agents et réglages globaux dans le projet qui les utilise (ticket #1038).

Le pendant du rangement (`maestro.agents.rangement`) : la règle dit *où* la
configuration d'un agent vit désormais, la reprise emmène là-bas ce qui existait
déjà. Sans elle, un poste installé avant ce lot verrait ses agents personnalisés
devenir des **gabarits** — donc l'équipe d'aucun projet — sans que rien ne le
dise. C'est le critère 2 du ticket : *repris sans perte, rattachés au projet qui
les utilise ou gardés comme gabarits, de façon idempotente, et la reprise dit ce
qu'elle a fait*.

## Ce qu'elle fait, et ce qu'elle ne fait pas

- Elle **recopie** vers `<racine>/_projets/<projet_id>/` ce que la racine porte :
  définitions d'agents, surcharges, playbooks (l'historique entier), politiques
  de permissions, configurations MCP, capacités.
- Elle ne **supprime jamais** rien. « Sans perte » se prend au sens fort : la
  racine reste le catalogue de **gabarits** que #1039 consultera, et un poste
  qui n'aimerait pas le résultat n'a rien perdu pour revenir en arrière.
- Elle n'**écrase jamais** ce que le projet a déjà : un fichier présent côté
  projet est laissé tel quel et rapporté « déjà repris ». C'est là son
  idempotence — la rejouer deux fois ne rend pas un second résultat.

## À quel projet rattacher ?

Le critère dit « le projet qui les utilise ». Rien sur le disque ne le nomme :
aucune configuration d'agent ne portait de `projet_id` avant ce lot, par
construction. Le seul fait dont on dispose est la **liste des projets déclarés**,
et on n'en devine pas plus :

- **un seul projet déclaré** — c'est *par construction* celui qui les utilise :
  tout ce qui s'est exécuté sur ce poste s'y est exécuté. On rattache ;
- **aucun projet** — il n'y a personne à qui rattacher : tout reste gabarit ;
- **plusieurs projets** — deux candidats et aucun départage : tout reste
  gabarit, et le rapport **le dit**, avec le geste qui tranche
  (`--projet <id>`). Choisir au hasard donnerait à un projet les autorisations
  d'un autre, ce que ce jalon existe précisément pour empêcher.

Un `projet` passé explicitement l'emporte toujours : c'est un humain qui a
répondu à la question, et une réponse vaut mieux qu'une règle.

## Où elle est jouée

- **Au démarrage de l'API** Control Tower (best-effort, jamais bloquant,
  `MAESTRO_REPRISE_AGENTS=0` pour s'en passer) : c'est le seul moment où une
  installation existante croise le nouveau code sans que personne ait rien à
  taper.
- **À la main**, pour la rejouer, la vérifier ou la cadrer :
  `python -m maestro.agents.reprise [--check] [--projet <id>]`. `--check` rend
  le même rapport **sans rien écrire**, comme `maestro.controltower.purge`.
"""

from __future__ import annotations

import shutil
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from maestro.agents.configuration import ConfigurationAgents
from maestro.agents.rangement import SEGMENT_PROJETS, racine_du_projet
from maestro.config import Settings, load_settings
from maestro.projets.store import ProjetStore

#: Code de sortie : la reprise a rendu son rapport (faite, ou rien à faire).
CODE_FAIT = 0
#: Code de sortie : argument inconnu ou projet demandé introuvable.
CODE_USAGE = 2

_USAGE = (
    "usage : python -m maestro.agents.reprise [--check] [--projet <id>]\n"
    "  --check          dit ce qui serait repris, sans rien écrire\n"
    "  --projet <id>    rattache à ce projet-là (sinon : le seul projet déclaré)"
)

#: Les six dépôts, dans l'ordre où le rapport les nomme. Le libellé est celui de
#: l'énumération du ticket — définition, playbook, autorisations, serveurs MCP,
#: capacité — plus les surcharges, qui sont un réglage de modèle à part (#259).
_DOMAINES: tuple[tuple[str, str], ...] = (
    ("agents", "définitions d'agents"),
    ("playbooks", "playbooks"),
    ("permissions", "autorisations"),
    ("mcp", "serveurs MCP"),
    ("capacite", "capacités"),
    ("surcharges", "surcharges de réglages"),
)


@dataclass(frozen=True)
class RepriseDomaine:
    """Ce qu'un dépôt a donné : ce qui est parti, et ce qui était déjà là."""

    domaine: str
    libelle: str
    reprises: tuple[str, ...] = ()
    deja: tuple[str, ...] = ()

    @property
    def vide(self) -> bool:
        """Rien à reprendre et rien de déjà repris — le dépôt n'a rien à dire."""
        return not self.reprises and not self.deja


@dataclass(frozen=True)
class RapportReprise:
    """Ce que la reprise a fait — ou ferait, en `--check`. Se rend en texte.

    `projet_id` est le projet visé, ou None quand il n'y avait personne à qui
    rattacher : `motif` dit alors lequel des deux cas (« aucun projet déclaré »,
    « plusieurs projets déclarés ») et le rapport reste lisible dans les deux.
    """

    projet_id: str | None
    motif: str = ""
    ecrit: bool = True
    domaines: tuple[RepriseDomaine, ...] = field(default_factory=tuple)

    @property
    def nb_reprises(self) -> int:
        """Combien d'éléments ont changé de niveau (0 quand il n'y avait rien à faire)."""
        return sum(len(d.reprises) for d in self.domaines)

    @property
    def nb_deja(self) -> int:
        """Combien étaient déjà rangés dans le projet — la mesure de l'idempotence."""
        return sum(len(d.deja) for d in self.domaines)

    def lignes(self) -> tuple[str, ...]:
        """Le rapport, une ligne par fait — c'est le « dit ce qu'elle a fait » du critère."""
        if self.projet_id is None:
            return (
                f"Reprise des agents : rien rattaché — {self.motif}.",
                "  Les agents et réglages globaux restent des gabarits de rôle "
                f"(à la racine de chaque dépôt, hors de `{SEGMENT_PROJETS}/`).",
                "  Pour trancher : python -m maestro.agents.reprise --projet <id>",
            )
        verbe = "seraient repris" if not self.ecrit else "repris"
        entete = f"Reprise des agents vers le projet {self.projet_id} :"
        if not self.domaines:
            return (entete, "  rien à reprendre — aucun réglage global à ce jour.")
        corps = [
            f"  {d.libelle} : {len(d.reprises)} {verbe}"
            + (f", {len(d.deja)} déjà rangé(s) dans le projet" if d.deja else "")
            + (f" — {', '.join(d.reprises)}" if d.reprises else "")
            for d in self.domaines
        ]
        return (entete, *corps)

    def __str__(self) -> str:
        return "\n".join(self.lignes())


def projet_cible(
    projet_id: str | None = None, *, projets: ProjetStore | None = None
) -> tuple[str | None, str]:
    """À quel projet rattacher, et pourquoi — voir l'en-tête du module.

    Rend le couple `(projet_id, motif)`. `projet_id` à None signifie « personne
    à qui rattacher » et `motif` le dit en une phrase lisible. Un `projet_id`
    **demandé** mais inconnu du dépôt est rendu à None avec son motif : on ne
    range pas une configuration sous un identifiant qui ne désigne rien.
    """
    projets = projets if projets is not None else ProjetStore.default()
    try:
        connus = projets.ids()
    except Exception:  # pragma: no cover - dépôt illisible : on n'invente pas un rattachement
        return None, "dépôt de projets illisible"
    if projet_id is not None:
        if projet_id in connus:
            return projet_id, "projet demandé"
        return None, f"projet demandé inconnu : {projet_id}"
    if not connus:
        return None, "aucun projet déclaré"
    if len(connus) > 1:
        return None, f"plusieurs projets déclarés ({', '.join(connus)}) — lequel les utilise ?"
    return connus[0], "seul projet déclaré"


def reprendre(
    projet_id: str | None = None,
    *,
    settings: Settings | None = None,
    projets: ProjetStore | None = None,
    configuration: ConfigurationAgents | None = None,
    check: bool = False,
) -> RapportReprise:
    """Rattache au projet ce que la racine de chaque dépôt porte encore. Idempotent.

    `check=True` calcule exactement le même rapport **sans rien écrire** — le
    `--check` de la CLI, et ce qui permet de regarder avant de toucher.

    `configuration` fixe **quels** dépôts sont repris. C'est ce que l'API passe
    au démarrage : ses six dépôts sont ceux qu'on lui a injectés, et une reprise
    qui les redemanderait à la config toucherait le `core/` du dépôt pendant que
    l'app travaille ailleurs. None : les dépôts configurés du poste.
    """
    settings = settings or load_settings()
    cible, motif = projet_cible(projet_id, projets=projets)
    if cible is None:
        return RapportReprise(projet_id=None, motif=motif, ecrit=not check)
    racines = _racines(configuration or ConfigurationAgents.default(settings))
    domaines = tuple(
        rapport
        for domaine, libelle in _DOMAINES
        if not (
            rapport := _reprendre_domaine(domaine, libelle, racines[domaine], cible, check)
        ).vide
    )
    return RapportReprise(projet_id=cible, motif=motif, ecrit=not check, domaines=domaines)


def _racines(configuration: ConfigurationAgents) -> dict[str, Path]:
    """La racine de chaque dépôt, **demandée aux dépôts eux-mêmes**.

    Jamais recopiée ici : c'est la règle que `maestro.controltower.purge` s'est
    déjà donnée (#830). Un `MAESTRO_*_DIR` posé sur un poste vaut donc pour la
    reprise sans qu'elle ait à connaître un seul nom de variable, et une
    configuration injectée (l'API, les tests) est reprise là où elle vit.
    """
    return {
        "agents": configuration.agents.racine,
        "playbooks": configuration.playbooks.racine,
        "permissions": configuration.permissions.racine,
        "mcp": configuration.mcp.racine,
        "capacite": configuration.capacites.racine,
        "surcharges": configuration.surcharges.racine,
    }


def _reprendre_domaine(
    domaine: str, libelle: str, racine: Path, projet_id: str, check: bool
) -> RepriseDomaine:
    """Recopie vers le projet les entrées de premier niveau de `racine`.

    Une « entrée » est ce que le dépôt range à son premier niveau : un fichier
    JSON (définitions, surcharges, capacités, permissions, MCP) ou un dossier
    d'agent (playbooks, dont l'historique entier voyage d'un bloc). Le segment
    de rangement lui-même (`_projets/`) et les fichiers temporaires d'écriture
    sont écartés — ce ne sont pas de la configuration.
    """
    if not racine.is_dir():
        return RepriseDomaine(domaine=domaine, libelle=libelle)
    destination = racine_du_projet(racine, projet_id)
    reprises: list[str] = []
    deja: list[str] = []
    for source in sorted(racine.iterdir()):
        if not _a_reprendre(source):
            continue
        arrivee = destination / source.name
        if arrivee.exists():
            deja.append(source.name)
            continue
        reprises.append(source.name)
        if check:
            continue
        destination.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, arrivee)
        else:
            shutil.copy2(source, arrivee)
    return RepriseDomaine(
        domaine=domaine, libelle=libelle, reprises=tuple(reprises), deja=tuple(deja)
    )


def _a_reprendre(chemin: Path) -> bool:
    """Cette entrée est-elle de la configuration à rattacher ?

    Non pour le segment de rangement (il porte déjà des projets), pour tout ce
    qui commence par un point ou un tiret bas (`.gitignore`, et par construction
    aucun agent ne s'appelle ainsi), pour les `README.md` livrés avec le dépôt,
    et pour les fichiers temporaires d'écriture.
    """
    if chemin.name.startswith((".", "_")):
        return False
    if chemin.is_dir():
        return True
    return chemin.suffix == ".json" or (chemin.suffix == ".md" and chemin.name != "README.md")


def main(
    argv: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
    projets: ProjetStore | None = None,
    sortie: TextIO | None = None,
    erreur: TextIO | None = None,
) -> int:
    """Point d'entrée CLI : `--check` regarde, sans lui la reprise est faite.

    `settings` et `projets` sont injectables **pour les tests** — des dossiers
    jetables, un dépôt de projets factice.
    """
    sortie = sortie or sys.stdout
    erreur = erreur or sys.stderr
    args = list(sys.argv[1:] if argv is None else argv)
    check = False
    demande: str | None = None
    reste = list(args)
    while reste:
        arg = reste.pop(0)
        if arg == "--check":
            check = True
        elif arg == "--projet":
            if not reste:
                print(f"{_USAGE}\n  --projet attend un identifiant.", file=erreur)
                return CODE_USAGE
            demande = reste.pop(0)
        else:
            print(f"{_USAGE}\n  argument inconnu : {arg}", file=erreur)
            return CODE_USAGE
    rapport = reprendre(demande, settings=settings, projets=projets, check=check)
    if rapport.projet_id is None and demande is not None:
        print("\n".join(rapport.lignes()), file=erreur)
        return CODE_USAGE
    print("\n".join(rapport.lignes()), file=sortie)
    return CODE_FAIT


if __name__ == "__main__":  # pragma: no cover - point d'entrée
    raise SystemExit(main())

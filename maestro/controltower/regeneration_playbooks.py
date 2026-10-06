"""Les playbooks déjà écrits, réécrits sans le régime qu'un modèle y avait mis (#1405).

Jusqu'à #1405, le cadre de génération (#257) faisait écrire au modèle le **régime
d'exécution** de l'agent — ce qui passe sans personne, ce qui attend l'accord d'une
personne —, tiré du cran que l'intention du rôle lui donnait (#1102, #1226). Sur p5
le modèle y a ajouté une exception que la politique n'a pas (« joindre un service
extérieur »), et l'agent a fait attendre quelqu'un avant un `npm install` qui passait
seul. Le régime est désormais écrit par Maestro, depuis la politique, au prompt de
chaque tâche (`maestro.agents.regime_d_execution`), et le cadre de génération
interdit de l'écrire. Restent les playbooks **déjà enregistrés** : ce module les
reprend.

## Une réécriture, pas une génération de zéro

Le playbook d'un agent de projet est celui qu'une personne a lu et validé avec son
équipe (`maestro.equipe.creation.RoleValide` : *ce qui est créé doit être exactement
ce qui a été montré*), et elle a pu le retoucher depuis les écrans d'agents depuis.
Le régénérer de zéro remplacerait ce métier validé par un autre texte, pour corriger
un seul passage. Le modèle reçoit donc le playbook et en **retire le régime**, sans
toucher au reste (`GenerateurDefinitionAgent.sans_regime`) : c'est lui qui juge ce
qui en relève, parce qu'un passage d'autorisation ne se reconnaît pas à un mot
(#1169).

Ce que le code tient, lui, ne passe pas par le modèle :

- la **section des skills**, que Maestro pose et que rien ne rédige
  (`maestro.equipe.creation.scinder_section_skills`), est mise de côté puis remise
  à l'identique ;
- l'**ancien playbook** est gardé, entier, sous
  `.maestro/regeneration-playbooks/<horodatage>/<projet>/<agent>.md` (chemin
  relatif au répertoire courant, ou sous le dossier que `--archive` nomme) — rien
  n'est perdu, et la comparaison se fait à l'œil nu ;
- un **échec** (modèle injoignable, réponse hors contrat, fiche refusée par le
  dépôt) laisse la fiche intacte et se dit ; les autres agents continuent.

Ce n'est pas ce qui rend la règle vraie : ce qui reste du texte, le bloc composé
depuis la politique **prime** dessus à chaque tâche. La réécriture retire la seconde
version d'une règle, pour qu'il n'y en ait plus qu'une à lire.

## Où elle est jouée

À la main, une fois par poste dont les équipes sont nées avant #1405 :

    python -m maestro.controltower.regeneration_playbooks [--check] [--projet <id>]…
        [--archive <dossier>]

Sans `--projet`, tous les projets qui ont une équipe (`<agents>/_projets/<id>/`).
`--check` liste les fiches qu'elle confierait au modèle, **sans appel ni
écriture**. Rejouée, elle repasse chaque playbook au modèle : un playbook déjà sans
régime revient tel quel (« inchangé »), à la manière de toute réécriture qui n'a
rien à retirer.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from maestro.agents.rangement import SEGMENT_PROJETS, racine_du_projet
from maestro.agents.store import AgentDefinition, AgentStore
from maestro.config import load_settings
from maestro.controltower.generation_agent import (
    GenerateurDefinitionAgent,
    GenerationIndisponible,
)
from maestro.equipe.creation import scinder_section_skills

#: Où l'ancien playbook est gardé — relatif au répertoire courant, comme ce que les
#: scripts du dépôt invitent à relire (`.maestro/<domaine>/`).
DOSSIER_ARCHIVE = Path(".maestro") / "regeneration-playbooks"

#: Les issues d'une fiche, telles que le rapport les écrit.
ISSUE_REECRIT = "réécrit"
ISSUE_INCHANGE = "inchangé"
ISSUE_ECHEC = "en échec"
ISSUE_A_REECRIRE = "à réécrire"

CODE_FAIT = 0
CODE_ECHEC = 1
CODE_USAGE = 2

_USAGE = (
    "usage : python -m maestro.controltower.regeneration_playbooks "
    "[--check] [--projet <id>]… [--archive <dossier>]"
)


@dataclass(frozen=True)
class Reecriture:
    """Ce qu'il est advenu du playbook d'un agent — une ligne du rapport."""

    projet_id: str
    agent: str
    issue: str
    detail: str = ""

    def ligne(self) -> str:
        """La ligne du rapport, sans caractère que la console Windows ne sache écrire."""
        detail = f" ({self.detail})" if self.detail else ""
        return f"  {self.projet_id} · {self.agent} — {self.issue}{detail}"


def projets_avec_equipe(store: AgentStore) -> tuple[str, ...]:
    """Les projets dont le dépôt d'agents porte une équipe, triés.

    Lus sur le disque, sous `_projets/` : c'est là que les fiches vivent, qu'un
    projet soit encore déclaré ou non. Un dossier sans fiche n'a rien à réécrire.
    """
    racine = store.racine / SEGMENT_PROJETS
    if not racine.is_dir():
        return ()
    projets = []
    for dossier in sorted(racine.iterdir()):
        if not dossier.is_dir():
            continue
        try:
            if store.pour_projet(dossier.name).noms():
                projets.append(dossier.name)
        except ValueError:  # un dossier au nom hors slug n'est pas un projet
            continue
    return tuple(projets)


async def reecrire_les_playbooks(
    store: AgentStore,
    generateur: GenerateurDefinitionAgent,
    projets: Sequence[str],
    archive: Path,
) -> tuple[Reecriture, ...]:
    """Réécrit le playbook de chaque agent de `projets` — l'ancien gardé sous `archive`.

    Les agents d'un projet sont confiés au modèle **en même temps** : ce sont des
    appels indépendants, comme les playbooks d'une équipe proposée
    (`ServiceEquipe._roles_avec_playbooks`). Aucun ne peut faire tomber les autres.
    """
    rapport: list[Reecriture] = []
    for projet_id in projets:
        depot = store.pour_projet(projet_id)
        rapport.extend(
            await asyncio.gather(
                *(
                    _reecrire(depot, projet_id, definition, generateur, archive / projet_id)
                    for definition in depot.lister()
                )
            )
        )
    return tuple(rapport)


def a_reecrire(store: AgentStore, projets: Sequence[str]) -> tuple[Reecriture, ...]:
    """Ce que `reecrire_les_playbooks` confierait au modèle — sans appel ni écriture."""
    return tuple(
        Reecriture(projet_id, definition.nom, ISSUE_A_REECRIRE)
        for projet_id in projets
        for definition in store.pour_projet(projet_id).lister()
    )


async def _reecrire(
    depot: AgentStore,
    projet_id: str,
    definition: AgentDefinition,
    generateur: GenerateurDefinitionAgent,
    archive: Path,
) -> Reecriture:
    """Une fiche : son corps réécrit, sa section des skills remise telle quelle."""
    corps, section = scinder_section_skills(definition.playbook)
    if not corps:
        return Reecriture(
            projet_id, definition.nom, ISSUE_INCHANGE, "aucun texte hors des skills"
        )
    try:
        reecrit = await generateur.sans_regime(corps)
    except (GenerationIndisponible, ValueError) as exc:
        return Reecriture(projet_id, definition.nom, ISSUE_ECHEC, str(exc))
    playbook = f"{reecrit}\n\n{section}\n" if section else f"{reecrit}\n"
    if playbook.strip() == definition.playbook.strip():
        return Reecriture(projet_id, definition.nom, ISSUE_INCHANGE)
    archive.mkdir(parents=True, exist_ok=True)
    gardee = archive / f"{definition.nom}.md"
    gardee.write_text(definition.playbook, encoding="utf-8")
    try:
        depot.ecrire(replace(definition, playbook=playbook))
    except ValueError as exc:
        return Reecriture(projet_id, definition.nom, ISSUE_ECHEC, str(exc))
    return Reecriture(
        projet_id,
        definition.nom,
        ISSUE_REECRIT,
        f"{len(corps)} puis {len(reecrit)} caractères hors des skills ; "
        f"l'ancien est gardé sous {gardee.as_posix()}",
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    store: AgentStore | None = None,
    generateur: GenerateurDefinitionAgent | None = None,
    archive: Path | None = None,
    sortie: TextIO | None = None,
    erreur: TextIO | None = None,
) -> int:
    """Point d'entrée — voir l'en-tête du module.

    `store`, `generateur` et `archive` sont injectables **pour les tests** : un
    dépôt dans un dossier jetable, un générateur sur un fournisseur factice.
    """
    sortie = sortie or sys.stdout
    erreur = erreur or sys.stderr
    args = list(sys.argv[1:] if argv is None else argv)
    check = False
    voulus: list[str] = []
    dossier: Path | None = None
    while args:
        arg = args.pop(0)
        if arg == "--check":
            check = True
        elif arg == "--projet" and args:
            voulus.append(args.pop(0))
        elif arg == "--archive" and args:
            dossier = Path(args.pop(0))
        else:
            print(f"{_USAGE}\n  argument inconnu : {arg}", file=erreur)
            return CODE_USAGE
    store = store or AgentStore.default(load_settings())
    for projet_id in voulus:
        try:
            racine_du_projet(store.racine, projet_id)
        except ValueError as exc:
            print(f"{_USAGE}\n  {exc}", file=erreur)
            return CODE_USAGE
    projets = tuple(voulus) or projets_avec_equipe(store)

    if check:
        print(
            "Réécriture des playbooks sans leur régime d'exécution — vérification, "
            "rien n'est appelé ni écrit :",
            file=sortie,
        )
        print(_rapport(a_reecrire(store, projets)), file=sortie)
        return CODE_FAIT

    horodatage = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    archive = archive or (dossier or DOSSIER_ARCHIVE) / horodatage
    rapport = asyncio.run(
        reecrire_les_playbooks(
            store, generateur or GenerateurDefinitionAgent(), projets, archive
        )
    )
    print("Réécriture des playbooks sans leur régime d'exécution :", file=sortie)
    print(_rapport(rapport), file=sortie)
    return CODE_ECHEC if any(r.issue == ISSUE_ECHEC for r in rapport) else CODE_FAIT


def _rapport(lignes: Sequence[Reecriture]) -> str:
    """Le rapport, une ligne par fiche — ou ce qu'il n'y avait pas."""
    return "\n".join(r.ligne() for r in lignes) or "  aucune fiche d'agent de projet."


if __name__ == "__main__":
    raise SystemExit(main())

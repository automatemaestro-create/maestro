"""Démo de bout en bout du POC et validation du critère de sortie Phase 0 (ticket #10).

`maestro-demo` boucle la Phase 0 : elle déroule la boucle d'orchestration complète
(objectif → tâches → agents → agrégat) sur un objectif conçu pour mobiliser au
moins les agents Développeur et Base de données, **écrit les artefacts** de
l'exécution dans un dossier de sortie (synthèse Markdown, rapport structuré,
journal JSON Lines, livrables par tâche — « résultats dans des fichiers »,
docs/06 Phase 0), puis **vérifie le critère de sortie** de la Phase 0 :

    un objectif → des tâches → 2 agents produisent un résultat exploitable.

Chaque volet du critère est confronté au `RunReport` (nombre de tâches, agents
distincts ayant produit, absence d'échec) et le verdict est imprimé et consigné
(`verdict.md`). Le volet architectural du critère (« fournisseur accédé via
l'interface d'abstraction ») ne se mesure pas sur une exécution : il est garanti
par construction — le moteur ne dépend que de `ModelProvider` — et prouvé par la
suite de tests, qui déroule cette même démo sur des fournisseurs factices
(tests/test_demo.py). Le parcours complet est documenté dans docs/11-demo-poc.md.

Garde-fous (#9) : le plafond de dépense (budget de l'exécution entière, #56) et
le time-out (par tâche) sont **armés par défaut** (bonne pratique docs/07 §4),
ajustables par options ; une tâche sensible déclenche une validation console
(refusée si l'entrée n'est pas interactive).

L'export Langfuse (#81) et l'évaluation en fin de run (#80 : scores de
réussite sur la trace) s'appliquent comme pour `maestro-run` : purement
configuratifs (clés `LANGFUSE_*` dans l'environnement), sans option dédiée.

Code de sortie : 0 si la démo tourne de bout en bout **et** que le critère de
sortie est validé ; 1 sinon (tâche en échec, critère non rempli, erreur de
configuration ou de planification) ; 2 si l'appel est mal formé.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from maestro.config import ConfigError
from maestro.engine.cli import activer_trace, console_tolerante, validation_console
from maestro.engine.guardrails import Guardrails
from maestro.engine.loop import OrchestrationEngine, RunReport
from maestro.engine.runner import run_borne
from maestro.orchestrator.errors import OrchestratorError
from maestro.telemetry import RunJournal, activer_export_langfuse, evaluer_run_langfuse

#: Objectif par défaut de la démo : deux domaines de compétences (schéma/SQL puis
#: backend/API) pour que le plan mobilise les agents BDD **et** Développeur. Le
#: périmètre est explicitement borné (« minimal », « un seul module ») pour que
#: chaque tâche tienne dans le budget de tours du runtime outillé (garde-fou
#: anti-emballement du fournisseur) — c'est une démo de POC, pas un produit.
OBJECTIF_DEFAUT = (
    "Prototyper la gestion des contacts d'un mini-CRM, volontairement minimal "
    "(démo de POC) : concevoir le schéma SQL de la table des contacts avec une "
    "migration simple, puis implémenter en Python une API REST minimale — un seul "
    "module, deux endpoints (créer un contact, lister les contacts) — qui s'appuie "
    "sur ce schéma. Rester au plus simple : bibliothèque standard ou micro-framework, "
    "pas d'authentification, pas d'outillage au-delà du strict nécessaire."
)

#: Garde-fous par défaut de la démo (#9) : plafonnée et bornée d'office — la démo
#: illustre les bonnes pratiques du POC (docs/07 §4), elle ne tourne jamais « sans filet ».
PLAFOND_COUT_DEFAUT_USD = 5.0
TIMEOUT_DEFAUT_S = 600.0


@dataclass(frozen=True)
class Verification:
    """Un volet du critère de sortie Phase 0 confronté à l'exécution : verdict + preuve."""

    volet: str
    ok: bool
    preuve: str


def verifier_critere_sortie(report: RunReport) -> tuple[Verification, ...]:
    """Confronte le `RunReport` aux volets mesurables du critère de sortie Phase 0.

    Trois volets (docs/06-roadmap.md) : « des tâches » (l'objectif a été décomposé
    en au moins 2 tâches), « 2 agents produisent » (au moins 2 agents distincts ont
    livré avec succès), « un résultat exploitable » (aucune tâche en échec — le
    moteur garantit déjà qu'une tâche réussie a un livrable non vide). Le volet
    architectural (abstraction fournisseur) est couvert par les tests, pas ici.
    """
    nb_taches = len(report.resultats)
    agents = sorted({r.agent for r in report.reussies})
    nb_echecs = len(report.echouees)
    return (
        Verification(
            volet="des tâches",
            ok=nb_taches >= 2,
            preuve=f"{nb_taches} tâche(s) planifiée(s) depuis l'objectif (attendu : ≥ 2)",
        ),
        Verification(
            volet="2 agents produisent",
            ok=len(agents) >= 2,
            preuve=(
                f"{len(agents)} agent(s) distinct(s) ont produit un livrable "
                f"({', '.join(agents) if agents else 'aucun'}) — attendu : ≥ 2"
            ),
        ),
        Verification(
            volet="un résultat exploitable",
            ok=nb_taches > 0 and nb_echecs == 0,
            preuve=(
                f"{len(report.reussies)}/{nb_taches} tâche(s) réussie(s), chacune avec un "
                "livrable non vide (texte et/ou fichiers)"
            ),
        ),
    )


def rendu_verdict(report: RunReport, verifications: Sequence[Verification]) -> str:
    """Rend le verdict du critère de sortie en Markdown : un volet par ligne, puis la note
    sur le volet architectural (garanti par construction, prouvé par les tests)."""
    valide = all(v.ok for v in verifications)
    lignes = [
        f"# Critère de sortie Phase 0 — {'VALIDÉ' if valide else 'NON VALIDÉ'}",
        "",
        f"Objectif : {report.objectif}",
        f"Run : `{report.run_id or 'n/d'}` — usage total : {report.usage_totale.resume_court()}",
        "",
    ]
    for v in verifications:
        marque = "[OK]" if v.ok else "[KO]"
        lignes.append(f"- {marque} {v.volet} — {v.preuve}.")
    lignes += [
        "",
        "Volet architectural (« fournisseur accédé via l'interface d'abstraction ») : "
        "garanti par construction — le moteur ne dépend que de `ModelProvider` — et "
        "prouvé par la suite de tests, qui déroule cette même boucle sur des "
        "fournisseurs factices (tests/test_demo.py, tests/test_engine.py).",
    ]
    return "\n".join(lignes) + "\n"


def ecrire_artefacts(
    report: RunReport,
    journal: RunJournal,
    verifications: Sequence[Verification],
    dossier: Path,
) -> Path:
    """Écrit les artefacts de l'exécution sous `dossier/run-<run_id>/` et renvoie ce chemin.

    Un sous-dossier par exécution (relié au journal par le `run_id`) : rien n'est
    écrasé d'un run à l'autre. Contenu : `synthese.md` (agrégat Markdown),
    `rapport.json` (rapport structuré), `journal.jsonl` (une ligne JSON par étape,
    #8), `verdict.md` (critère de sortie), et `livrables/<tâche>/` — le livrable
    texte (`livrable.md`) et les fichiers produits (`fichiers/…`, #35) de chaque
    tâche réussie : les « résultats dans des fichiers » attendus de la Phase 0.
    """
    racine = dossier / f"run-{report.run_id or 'sans-id'}"
    racine.mkdir(parents=True, exist_ok=True)

    (racine / "synthese.md").write_text(report.synthese(), encoding="utf-8")
    rapport = json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n"
    (racine / "rapport.json").write_text(rapport, encoding="utf-8")
    traces = "".join(
        json.dumps(record.to_dict(), ensure_ascii=False) + "\n" for record in journal.records
    )
    (racine / "journal.jsonl").write_text(traces, encoding="utf-8")
    (racine / "verdict.md").write_text(rendu_verdict(report, verifications), encoding="utf-8")

    for resultat in report.reussies:
        coin = racine / "livrables" / _nom_sur(resultat.task_id)
        coin.mkdir(parents=True, exist_ok=True)
        if resultat.sortie:
            (coin / "livrable.md").write_text(resultat.sortie + "\n", encoding="utf-8")
        for fichier in resultat.fichiers:
            if fichier.contenu is None:
                continue  # binaire/volumineux : le chemin reste visible dans le rapport
            cible = coin / "fichiers" / fichier.chemin
            cible.parent.mkdir(parents=True, exist_ok=True)
            cible.write_text(fichier.contenu, encoding="utf-8")
    return racine


def _nom_sur(task_id: str) -> str:
    """Mue un id de tâche en nom de dossier sûr (pas de séparateur ni de caractère exotique)."""
    return re.sub(r"[^A-Za-z0-9._-]", "-", task_id) or "tache"


def run_demo(
    engine: OrchestrationEngine,
    *,
    objectif: str,
    dossier: Path,
    journal: RunJournal | None = None,
) -> int:
    """Déroule la démo sur `engine` : exécution, artefacts, verdict ; renvoie le code de sortie.

    Séparée de `main()` pour être exerçable telle quelle sur un moteur factice
    (tests/test_demo.py) — la vraie démo et les tests partagent ce même parcours.
    Un `journal` injecté permet à l'appelant de relire l'exécution consignée
    (c'est ainsi que `main()` l'évalue dans Langfuse, #80).
    """
    journal = journal if journal is not None else RunJournal()
    try:
        # Arrêt borné (#64) : une réalisation détachée par le time-out ne peut pas
        # suspendre la fermeture de la boucle — artefacts et verdict sont toujours rendus.
        report = run_borne(engine.run(objectif, journal=journal))
    except OrchestratorError as exc:
        print(f"Orchestration : {exc}", file=sys.stderr)
        return 1
    verifications = verifier_critere_sortie(report)
    racine = ecrire_artefacts(report, journal, verifications, dossier)
    print(report.synthese())
    print(rendu_verdict(report, verifications))
    print(f"Artefacts écrits dans : {racine}")
    return 0 if all(v.ok for v in verifications) else 1


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    """Analyse les arguments de `maestro-demo` (sortie 2 si l'appel est mal formé)."""
    parser = argparse.ArgumentParser(
        prog="maestro-demo",
        description=(
            "Démo de bout en bout du POC : objectif → tâches → agents → résultats dans "
            "des fichiers, puis vérification du critère de sortie Phase 0."
        ),
    )
    parser.add_argument(
        "objectif",
        nargs="*",
        help=f"objectif en langage naturel (défaut : « {OBJECTIF_DEFAUT} »)",
    )
    parser.add_argument(
        "--sortie",
        default="sortie-demo",
        help="dossier où écrire les artefacts (défaut : sortie-demo/)",
    )
    parser.add_argument(
        "--plafond-cout",
        type=float,
        default=PLAFOND_COUT_DEFAUT_USD,
        metavar="USD",
        help=f"plafond de dépense de l'exécution en USD (défaut : {PLAFOND_COUT_DEFAUT_USD:g})",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=TIMEOUT_DEFAUT_S,
        metavar="S",
        help=f"time-out par tâche en secondes (défaut : {TIMEOUT_DEFAUT_S:g})",
    )
    parser.add_argument(
        "--trace",
        action="store_true",
        help="émet le journal d'exécution (JSON Lines, #8) sur stderr au fil de l'eau",
    )
    return parser.parse_args(list(argv))


def main(argv: Sequence[str] | None = None) -> int:
    console_tolerante()
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    if args.trace:
        activer_trace()
    # Export Langfuse (#81) : purement configuratif — no-op sans clés dans l'env.
    activer_export_langfuse()
    objectif = " ".join(args.objectif).strip() or OBJECTIF_DEFAUT

    try:
        guardrails = Guardrails(
            plafond_cout_usd=args.plafond_cout,
            timeout_s=args.timeout,
            validateur=validation_console,
        )
    except ValueError as exc:
        print(f"Garde-fous : {exc}", file=sys.stderr)
        return 2

    try:
        engine = OrchestrationEngine.default(guardrails=guardrails)
    except ConfigError as exc:
        print(f"Configuration : {exc}", file=sys.stderr)
        return 1

    journal = RunJournal()
    code = run_demo(engine, objectif=objectif, dossier=Path(args.sortie), journal=journal)
    # Évaluation (#80) : les scores de l'exécution partent sur sa trace Langfuse —
    # même bascule configurative que l'export, no-op sans clés.
    evaluer_run_langfuse(journal)
    return code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

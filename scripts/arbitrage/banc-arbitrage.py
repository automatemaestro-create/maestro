#!/usr/bin/env python
"""Banc d'arbitrage sur l'acte : le parcours entier, sur les politiques versionnées (#716).

Le chantier #573 a livré la chaîne de bout en bout — la politique classe un outil
`ask` (#580), le hook `PreToolUse` suspend l'appel (#583), la demande part avec
l'outil et ses arguments (#581), le délai de la tâche ne court pas pendant la
délibération (#584), le cran dit qui tranche (#586) — et **rien ne l'exerçait** :
`core/permissions/` ne portait aucune entrée `ask`, donc aucun outil n'était
suspendu et la file `/api/validations` ne recevait jamais d'acte. #716 pose les
entrées ; ce banc-ci prouve qu'elles font ce qu'elles disent, et se **rejoue**.

## Les cinq stations, et ce qui est réel à chacune

1. **la politique** : lue par `PermissionStore` dans `core/permissions/` — le
   dossier **versionné**, jamais une politique écrite pour l'occasion. C'est le
   point du ticket : ce qui est exercé est ce qui partira en production ;
2. **le hook** : `maestro.providers.claude._hook_permissions`, la fonction que le
   SDK appelle en production, jouée sur un vrai payload `{"tool_name", "tool_input"}` ;
3. **la demande** : composée par `LocalExecutor` (via un vrai
   `OrchestrationEngine.run`), soumise au vrai `Guardrails`, portée par le vrai
   `ValidateurControlTower` sur le bus ;
4. **la file** : la vraie application FastAPI de la Control Tower —
   `GET /api/validations` puis `POST /api/validations/{tache_id}/decision` ;
5. **le journal** : les `StepRecord` du vrai `RunJournal`, où l'issue s'écrit
   sous l'étape `:refus-outil` et le statut `arbitrage_outil`.

## Ce que le banc ne prouve PAS, et il vaut mieux le dire que le laisser croire

**Le modèle n'est pas là.** Ce qui manque à la chaîne est l'agent qui *décide*
d'appeler l'outil : le fournisseur du banc joue le hook sur des actes écrits ici,
au lieu d'attendre qu'un LLM les commette. C'est le seul maillon substitué, et
c'est celui qu'on ne peut pas jouer sans quota ni aléa — le pilote #105
(docs/15) et le pilote #128 (docs/20) sont les relevés où un vrai agent a
réellement appelé ses outils MCP. Tout le reste, ci-dessus, est le code de
production.

**L'attente est raccourcie.** Les bornes du banc (30 s / 60 s) ne sont pas celles
de la production (240 s / 300 s) : elles ne changent pas ce qui est vérifié — les
issues du hook et leur invariant sont éprouvés dans `tests/test_permissions.py`
⑥a — mais elles font qu'une station en panne se voit comme une panne, et non
comme un banc qui semble réfléchir pendant quatre minutes.

Ni réseau, ni Redis, ni quota : bus mémoire, plan constant, fournisseur local.

Usage :

    .venv/Scripts/python.exe scripts/arbitrage/banc-arbitrage.py
    .venv/Scripts/python.exe scripts/arbitrage/banc-arbitrage.py --json

Code de retour : 0 si les cinq stations répondent, 1 sinon.
"""

from __future__ import annotations

import argparse
import functools
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: La racine du dépôt, en tête du chemin d'import : lancé depuis un worktree, un
#: script se ferait sinon servir le `maestro` du clone principal (installation
#: éditable), c'est-à-dire mesurer un autre code que celui qu'on modifie.
RACINE = Path(__file__).resolve().parents[2]
if str(RACINE) not in sys.path:
    sys.path.insert(0, str(RACINE))

from fastapi.testclient import TestClient  # noqa: E402

from maestro.agents.permissions import PermissionStore, PolitiqueOutils  # noqa: E402
from maestro.controltower import (  # noqa: E402
    VALIDATION_APPROUVEE,
    VALIDATION_EN_ATTENTE,
    ControlTowerState,
    InMemoryEventBus,
    ValidateurControlTower,
    create_app,
)
from maestro.decideur import Decideur  # noqa: E402
from maestro.engine import OrchestrationEngine  # noqa: E402
from maestro.engine.executor import STATUT_ARBITRAGE_OUTIL, SUFFIXE_ETAPE_REFUS  # noqa: E402
from maestro.engine.guardrails import ORIGINE_POLITIQUE, Guardrails  # noqa: E402
from maestro.orchestrator import Orchestrator  # noqa: E402
from maestro.providers import claude as claude_mod  # noqa: E402
from maestro.providers.arbitrage import BornesArbitrage  # noqa: E402
from maestro.providers.base import ModelProvider  # noqa: E402
from maestro.telemetry import RunJournal  # noqa: E402

#: L'agent dont on exerce la politique. Le designer parce que sa politique est la
#: seule à porter les **trois** régimes à la fois (`humain`, `auto`, `allow`) :
#: un banc qui ne jouerait que le premier ne montrerait pas ce qui le distingue
#: des deux autres, or c'est exactement la question du ticket.
AGENT = "designer"

#: Le cran de chaque acte joué, tel que la politique versionnée doit le rendre.
#: `None` = aucun arbitrage (`allow`) : l'appel passe **en silence**.
ACTES: tuple[tuple[str, dict[str, str], Decideur | None], ...] = (
    (
        "mcp__figma-officiel__use_figma",
        {"prompt": "Renommer le calque « Header » en « Bandeau » dans le fichier d'équipe"},
        Decideur.HUMAIN,
    ),
    ("mcp__figma-officiel__get_metadata", {"nodeId": "12:34"}, Decideur.AUTO),
    ("Read", {"file_path": "maquette.md"}, None),
)

#: L'acte que quelqu'un doit trancher — le premier, seul classé `humain`.
ACTE_ARBITRE = ACTES[0][0]

#: Bornes du banc : voir le docstring. Raccourcies pour qu'une panne se voie.
BORNES = BornesArbitrage(attente_s=30.0, borne_hook_s=60.0)

#: Le plan, constant : aucun appel modèle n'est fait pour le produire. Les
#: compétences routent la tâche vers le designer (`ui` — `maestro.agents.catalog`).
PLAN = json.dumps(
    [
        {
            "id": "banc-arbitrage",
            "titre": "Retoucher le bandeau de la maquette",
            "description": "Ajuster le bandeau dans le fichier Figma de l'équipe.",
            "competences_requises": ["ui"],
            "format_sortie": "Texte",
            "dependances": [],
        }
    ],
    ensure_ascii=False,
)

OBJECTIF = "Retoucher le bandeau de la maquette Control Tower"

#: Le temps que le banc s'accorde pour voir la carte arriver dans la file, puis
#: pour voir le run se solder après la décision. Généreux au regard de ce qui s'y
#: joue (un aller de bus mémoire), assez court pour qu'une panne rende la main.
DELAI_S = 20.0


class ConstantProvider(ModelProvider):
    """Planificateur constant : rend le plan sans jamais appeler de modèle."""

    name = "constant"

    def __init__(self, reponse: str) -> None:
        self._reponse = reponse

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        return self._reponse


class ProviderQuiJoueLeHook(ModelProvider):
    """L'exécutant du banc : il joue le **vrai** hook sur les actes de `ACTES`.

    C'est le seul maillon substitué au réel, et la substitution est étroite : on
    remplace le modèle qui *choisit* l'outil, jamais le point de contrôle qui le
    juge. Le hook construit ici est celui de la production
    (`_hook_permissions`), armé de la politique que l'exécuteur vient de lire
    dans `core/permissions/` et du canal d'arbitrage qu'il vient de composer.

    ⚠ Ne pas y réécrire la décision du hook : le banc doit pouvoir **échouer**.
    Un double qui déciderait lui-même du sort d'un appel rendrait un verdict vert
    sur une question jamais posée.
    """

    name = "banc-arbitrage"

    def __init__(self) -> None:
        self.sorties: list[tuple[str, dict[str, Any]]] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):  # pragma: no cover
        return "TEXTE"

    async def run_agent(
        self, prompt, *, model, system_prompt=None, workspace, tools,
        mcp_serveurs=(), politique=None, on_refus=None, on_arbitrage_acte=None,
        on_activite=None, on_etapes=None, on_arbitrage=None, credit_arbitrage=None,
        plafond_tours=None, projet=None,
    ):
        if politique is None:
            raise RuntimeError(
                f"aucune politique lue pour l'agent {AGENT!r} — "
                "core/permissions/ est-il bien le dépôt visé ?"
            )
        hook = claude_mod._hook_permissions(
            politique, on_refus, on_arbitrage_acte, BORNES, credit_arbitrage
        )
        for index, (outil, arguments, _) in enumerate(ACTES):
            sortie = await hook(
                {"tool_name": outil, "tool_input": arguments}, f"tu-{index}", None
            )
            self.sorties.append((outil, sortie))
        (Path(workspace) / "livrable.md").write_text("banc", encoding="utf-8")
        return "banc joué"


#: L'ordre du parcours — celui du relevé, qui n'est pas celui où les stations se
#: mesurent (le hook et le journal ne se relisent qu'une fois le run soldé).
ORDRE = ("politique", "hook", "file", "decision", "journal")


@dataclass
class Station:
    """Une station du parcours : ce qu'on attendait, ce qu'on a vu."""

    nom: str
    ok: bool
    constat: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class Releve:
    """Le relevé du banc — ce qu'on colle dans le ticket."""

    stations: list[Station] = field(default_factory=list)
    erreur: str = ""

    @property
    def ok(self) -> bool:
        return not self.erreur and bool(self.stations) and all(s.ok for s in self.stations)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "erreur": self.erreur,
            "stations": [
                {"nom": s.nom, "ok": s.ok, "constat": s.constat, "detail": s.detail}
                for s in self.stations
            ],
        }


def _attend(lecture, predicat, delai: float = DELAI_S):
    """Rejoue `lecture` jusqu'à ce que `predicat` accepte — None si le délai tombe."""
    limite = time.monotonic() + delai
    while time.monotonic() < limite:
        valeur = lecture()
        if predicat(valeur):
            return valeur
        time.sleep(0.02)
    return None


def _politique() -> PolitiqueOutils | None:
    """La politique **versionnée** de l'agent du banc (jamais une politique de test)."""
    return PermissionStore(RACINE / "core" / "permissions").lire(AGENT)


def _station_politique(releve: Releve, politique: PolitiqueOutils | None) -> None:
    """① La politique versionnée classe-t-elle les trois actes comme annoncé ?"""
    if politique is None:
        releve.stations.append(
            Station("politique", False, f"aucune politique versionnée pour « {AGENT} »")
        )
        return
    lus = {outil: politique.decide(outil) for outil, _, _ in ACTES}
    attendus = {outil: cran for outil, _, cran in ACTES}
    ecarts = [
        f"{outil} → {decision.decideur or 'allow'} (attendu {attendus[outil] or 'allow'})"
        for outil, decision in lus.items()
        if decision.decideur != attendus[outil]
    ]
    releve.stations.append(
        Station(
            "politique",
            not ecarts,
            "écarts : " + ", ".join(ecarts)
            if ecarts
            else f"{len(ACTES)} actes classés par core/permissions/{AGENT}.json",
            {
                outil: f"{decision.verdict.value}"
                + (f" ({decision.decideur})" if decision.decideur else "")
                for outil, decision in lus.items()
            },
        )
    )


def _station_hook(releve: Releve, sorties: list[tuple[str, dict[str, Any]]]) -> None:
    """② Le hook a-t-il suspendu l'acte `humain`, laissé passer les deux autres ?"""
    par_outil = dict(sorties)
    manquants = [outil for outil, _, _ in ACTES if outil not in par_outil]
    if manquants:
        releve.stations.append(
            Station("hook", False, f"actes jamais soumis au hook : {', '.join(manquants)}")
        )
        return
    # L'acte arbitré et approuvé rend une sortie **vide** : le hook ne force pas
    # l'appel, il cesse de le suspendre (cf. `_hook_permissions`).
    passes = [outil for outil, sortie in sorties if not sortie]
    releve.stations.append(
        Station(
            "hook",
            len(passes) == len(ACTES),
            f"{len(passes)}/{len(ACTES)} actes laissés partir après jugement du hook",
            {outil: (sortie or "laissé partir") for outil, sortie in sorties},
        )
    )


def _station_file(releve: Releve, carte: dict[str, Any] | None) -> None:
    """③ La demande a-t-elle atteint `GET /api/validations`, avec l'acte ?"""
    if carte is None:
        releve.stations.append(
            Station("file", False, "aucune demande servie par GET /api/validations")
        )
        return
    attendu = {
        "outil": ACTE_ARBITRE,
        "decideur": str(Decideur.HUMAIN),
        "statut": VALIDATION_EN_ATTENTE,
    }
    ecarts = [
        f"{cle} = {carte.get(cle)!r}" for cle, val in attendu.items() if carte.get(cle) != val
    ]
    releve.stations.append(
        Station(
            "file",
            not ecarts,
            "écarts : " + ", ".join(ecarts) if ecarts else f"carte « {ACTE_ARBITRE} » en attente",
            {
                "tache_id": carte.get("tache_id"),
                "outil": carte.get("outil"),
                "arguments": carte.get("arguments"),
                "decideur": carte.get("decideur"),
                "raison": carte.get("raison") or carte.get("detail"),
            },
        )
    )


def _station_decision(releve: Releve, code: int, apres: list[dict[str, Any]]) -> None:
    """④ La décision rendue par l'API a-t-elle été enregistrée ?"""
    tranchees = [v for v in apres if v.get("statut") == VALIDATION_APPROUVEE]
    releve.stations.append(
        Station(
            "decision",
            code == 200 and len(tranchees) == 1,
            f"POST /api/validations/…/decision → {code}, "
            f"{len(tranchees)} demande(s) approuvée(s)",
            {"http": code, "statuts": [v.get("statut") for v in apres]},
        )
    )


def _station_journal(releve: Releve, journal: RunJournal, report: Any) -> None:
    """⑤ Le journal garde-t-il la trace, sous son statut à lui, avec le décideur ?

    Et la tâche s'est-elle soldée ? Les deux tiennent dans la même station parce
    qu'ils se lisent au même instant et disent une seule chose : un arbitrage
    laisse une trace **et** ne condamne pas le run. Séparer le second en ferait
    une propriété à part, alors que c'est le contrat du canal depuis #110.
    """
    etapes = [
        r
        for r in journal.records
        if r.etape.endswith(SUFFIXE_ETAPE_REFUS) and r.statut == STATUT_ARBITRAGE_OUTIL
    ]
    par_outil = {r.entree: r for r in etapes}
    attendus = {outil for outil, _, cran in ACTES if cran is not None}
    silencieux = {outil for outil, _, cran in ACTES if cran is None}
    resultats = list(getattr(report, "resultats", ()))
    reussies = [r for r in resultats if getattr(r, "ok", False)]
    ok = (
        attendus <= set(par_outil)
        and not (silencieux & set(par_outil))
        and len(reussies) == len(resultats) == 1
    )
    releve.stations.append(
        Station(
            "journal",
            ok,
            f"{len(etapes)} étape(s) « {STATUT_ARBITRAGE_OUTIL} » "
            f"pour {len(attendus)} acte(s) arbitré(s), "
            f"{len(silencieux)} acte(s) passé(s) en silence — "
            f"{len(reussies)}/{len(resultats)} tâche(s) réussie(s)",
            {r.entree: r.nom for r in etapes},
        )
    )


def joue() -> Releve:
    """Joue le parcours entier et rend son relevé — ne lève jamais."""
    releve = Releve()
    politique = _politique()
    _station_politique(releve, politique)

    bus = InMemoryEventBus()
    state = ControlTowerState()
    journal = RunJournal(run_id="banc-arbitrage-716")
    executant = ProviderQuiJoueLeHook()
    moteur = OrchestrationEngine(
        executant,
        Orchestrator(ConstantProvider(PLAN), model="claude-opus-4-8"),
        permissions=PermissionStore(RACINE / "core" / "permissions"),
        guardrails=Guardrails(validateur=ValidateurControlTower(bus)),
    )

    try:
        with TestClient(create_app(bus=bus, state=state)) as client:
            run = client.portal.start_task_soon(
                functools.partial(moteur.run, OBJECTIF, journal=journal)
            )

            def file() -> list[dict[str, Any]]:
                return client.get("/api/validations?projet=tous").json()

            attente = _attend(file, lambda v: bool(v))
            carte = attente[0] if attente else None
            _station_file(releve, carte)

            if carte is None:
                run.cancel()
                releve.erreur = "aucune demande n'a atteint la file — parcours interrompu"
                return releve

            code = client.post(
                f"/api/validations/{carte['tache_id']}/decision", json={"approuve": True}
            ).status_code
            _station_decision(releve, code, file())

            try:
                report = run.result(timeout=DELAI_S)
            except Exception as exc:  # noqa: BLE001 — le relevé porte la cause
                releve.erreur = f"le run ne s'est pas soldé : {exc}"
                report = None
    except Exception as exc:  # noqa: BLE001 — un banc ne lève pas, il rapporte
        releve.erreur = f"{type(exc).__name__} : {exc}"
        return releve

    _station_hook(releve, executant.sorties)
    _station_journal(releve, journal, report)
    # Les stations naissent dans l'ordre où le parcours les rend lisibles (le
    # hook et le journal ne se relisent qu'une fois le run soldé) ; on les rend
    # dans l'ordre où on les **parcourt**, seul ordre qui raconte la chaîne.
    releve.stations.sort(key=lambda s: ORDRE.index(s.nom))
    return releve


def rend(releve: Releve) -> str:
    """Le relevé en clair — la forme qu'on colle dans le ticket."""
    lignes = [
        "Banc d'arbitrage sur l'acte (#716)",
        f"  agent      : {AGENT} (politique versionnée core/permissions/{AGENT}.json)",
        f"  acte jugé  : {ACTE_ARBITRE} — cran « {Decideur.HUMAIN} »",
        f"  provenance : {ORIGINE_POLITIQUE}",
        "",
    ]
    for index, station in enumerate(releve.stations, start=1):
        verdict = "OK" if station.ok else "KO"
        lignes.append(f"  {verdict}  {index}. {station.nom} — {station.constat}")
        for cle, valeur in station.detail.items():
            lignes.append(f"          {cle} : {valeur}")
    lignes.append("")
    if releve.erreur:
        lignes.append(f"  erreur : {releve.erreur}")
    lignes.append(
        "  VERDICT : chaîne exercée de bout en bout"
        if releve.ok
        else "  VERDICT : chaîne NON exercée"
    )
    lignes.append("  (le modèle qui choisit l'outil est le seul maillon substitué — cf. docstring)")
    return "\n".join(lignes)


def main(argv: list[str] | None = None) -> int:
    # La console Windows décode en cp1252, où « → » n'existe pas : sans ce
    # réglage le banc mourrait sur son propre relevé, après l'avoir mesuré. On
    # force donc la sortie de **notre** processus, jamais l'environnement d'un
    # autre (cf. le piège `PYTHONIOENCODING` devant un pipe, #141).
    for flux in (sys.stdout, sys.stderr):
        reconfigure = getattr(flux, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")

    parseur = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parseur.add_argument("--json", action="store_true", help="rendre le relevé en JSON")
    options = parseur.parse_args(argv)

    releve = joue()
    if options.json:
        print(json.dumps(releve.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(rend(releve))
    return 0 if releve.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

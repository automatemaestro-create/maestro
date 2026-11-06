"""L'exercice qu'une session construit pour ce qu'elle vient d'implémenter (#1569).

    .venv/Scripts/python.exe -m maestro.scenarios.exercice <verbe> [options]

    ouvrir --oracle <fichier> [--personne <fichier>] [--titre <titre>]
    semer <dossier> [--nom <nom>] [--versionner] [--equipe]
    naitre <fichier-de-la-demande> [--nom <nom>] [--versionner]
    dire <fichier-du-message>
    repondre
    arbitrer approuver|refuser|attendre
    suivre [--jusqu-a issue|demande|question|statut=<statut>] [--delai <s>] [--run <id>]
    relever
    sauver
    conclure vert|rouge|empeche <fichier-du-motif>

Chaque verbe prend `--exercice <id>` ; sans lui, c'est le dernier ouvert.

Il n'y a **pas de scénario figé** (#1568) : quand un changement demande à être vu sur la
vraie stack, la session qui l'a écrit **construit** l'exercice qui l'exerce — le message
à donner à Maestro, ce que la personne répondra, ce qu'on doit constater — puis le joue
geste par geste, une commande par geste. Aucune liste de parcours ne vit ici : ce module
rend des **verbes**, et la session décide de leur ordre.

Tout passe par **la porte d'entrée réelle** — l'API de la Control Tower, par le fil de
l'orchestrateur — et par les gestes partagés avec le comparatif
(`maestro.scenarios.gestes`), tirés tels quels.

## L'oracle s'écrit avant de jouer

`ouvrir` refuse un exercice sans oracle, et en retient l'empreinte : ce qu'on doit
constater, écrit **avant** le premier geste. Chaque verbe suivant la confronte au fichier,
et refuse (`5`) un oracle retouché — un oracle écrit après coup se taille sur ce que le
produit a fait, et ne prouve plus rien. Qui veut un autre oracle ouvre un autre exercice.

## La personne

`--personne` est sa **feuille** : ce qu'elle répond quand on lui demande quelque chose,
écrit d'avance, dans ses mots. Elle répond toujours par la feuille **entière**, jamais par
la ligne qui semblerait convenir — choisir une ligne par ses mots serait juger une phrase
par un lexique (#746), et c'est au produit d'y lire sa réponse. Sans feuille, la personne
n'a pas d'avis : elle retient ce que Maestro recommande, laisse le fil choisir, et ne
répond pas aux agents. Elle accepte ce qu'on lui montre — le projet proposé, l'équipe
proposée, le run proposé — et tranche les arbitrages selon sa **consigne** (`arbitrer`,
`approuver` par défaut ; `attendre` les laisse attendre).

## Le relevé

`.maestro/exercices/<id>/` : `oracle.md` et `personne.md` (écrits avant), `prompt.md` (ce
qui a été dit à Maestro), `deroule.md` (chaque geste et ce que la stack a rendu),
`faits.json` et `faits.md` (`relever` : cartes, statuts, journal, validations, questions,
fil, coût du grand livre, fichiers et commits du projet, durée, `run_id`) et `verdict.md`
(`conclure`). L'identifiant de l'exercice est celui de son atelier — ses projets vivent
sous l'atelier des passages (`maestro.scenarios.projets`), qui garde les derniers
(`retenir_le_poste`, à l'ouverture), et son état sauvé y va aussi : `start.sh --etat-banc`
le rouvre (`sauver`, sur une API servie par le jeu du banc de la copie).

## Le temps

`suivre` est borné (`--delai`, 480 s par défaut) **sous le plafond d'un appel Bash**
(10 min) : un run plus long se suit en plusieurs appels, et le `6` dit qu'il est encore en
vol. Jamais une attente qui tient la session au-delà de ce qu'un appel lui laisse.

Codes : `0` fait · `1` un geste n'a pas abouti (l'API a refusé, ou le fil n'a pas fait ce
qu'on lui demandait — la cause est dite) · `2` usage · `3` API muette · `4` aucun exercice,
ou ce qui manque pour jouer ce geste (projet, run) · `5` refusé (oracle retouché, API hors
du banc, état non sauvé) · `6` le délai de `suivre` a passé, le run est encore en vol.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn, TextIO

from maestro.controltower.donnees import Donnees, donnees_du_banc
from maestro.controltower.state import (
    EXECUTION_EN_ATTENTE_ARBITRAGE,
    QUESTION_EN_ATTENTE,
    STATUTS_EXECUTION_TERMINAUX,
)
from maestro.scenarios.api import (
    ClientAPI,
    ErreurAPI,
    TransportHTTP,
    attendre_le_run,
    base_locale,
    repondre_aux_agents,
)
from maestro.scenarios.gestes import (
    REPONSE_SANS_PRECISION,
    Contexte,
    Repondeur,
    accepter_le_projet,
    accorder,
    cout_du_grand_livre,
    declarer,
    demander,
    doter_d_une_equipe,
    extrait,
    montant,
    naitre_dans_l_atelier,
    outiller_dans_le_fil,
    reponse_a,
    run_de,
    valider_l_equipe,
)
from maestro.scenarios.juge import Juge, JugeModele
from maestro.scenarios.modele import Issue, Journal, Rapport, Resultat, horodatage
from maestro.scenarios.projets import VARIABLE_ATELIER, Atelier, retenir_le_poste

#: Où vivent les exercices — sous `.maestro/`, en chemin relatif (#234).
RACINE_EXERCICES = Path(".maestro") / "exercices"

FICHIER_ETAT = "exercice.json"
FICHIER_ORACLE = "oracle.md"
FICHIER_PERSONNE = "personne.md"
FICHIER_PROMPT = "prompt.md"
FICHIER_DEROULE = "deroule.md"
FICHIER_FAITS = "faits.json"
FICHIER_FAITS_LISIBLES = "faits.md"
FICHIER_VERDICT = "verdict.md"

VERSION_FORMAT = 1

#: Ce que `suivre` attend au plus en un appel : 8 min, sous le plafond de 10 min d'un
#: appel Bash, avec la marge de ce que le verbe fait avant et après le suivi.
DELAI_SUIVI_S = 480.0

#: Ce que la personne fait d'une demande d'arbitrage : l'accorder, la refuser, ou la
#: laisser attendre (le run reste suspendu jusqu'à un geste ou sa borne d'arbitrage).
CONSIGNES = {"approuver": True, "refuser": False, "attendre": None}
CONSIGNE_DEFAUT = "approuver"

#: Les moments où `suivre` rend la main avant l'issue — en plus de `statut=<statut>`,
#: n'importe quel statut de run tel que l'API le nomme.
MOMENT_ISSUE = "issue"
MOMENT_DEMANDE = "demande"
MOMENT_QUESTION = "question"
PREFIXE_STATUT = "statut="

VERDICTS = ("vert", "rouge", "empeche")

#: Ce que le relevé des fichiers du projet n'entre pas : des dépendances, des caches,
#: le dépôt git (ses commits sont relevés à part). Le reste est le livrable.
DOSSIERS_HORS_RELEVE = frozenset(
    {
        ".git",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".next",
    }
)
#: Ce qu'une copie semée n'emporte pas : des dépendances qui se réinstallent.
COPIE_SANS = ("node_modules", ".venv", "__pycache__")
FICHIERS_RELEVES_MAX = 500
COMMITS_RELEVES_MAX = 30
EVENEMENTS_RELEVES_MAX = 50
#: Ce que `faits.md` montre d'un message du fil — le JSON le garde entier.
EXTRAIT_FIL = 1200

GESTE_STACK = "bash scripts/controltower/start.sh --etat-neuf --no-browser"
GESTE_ROUVRIR = "bash scripts/controltower/start.sh --etat-banc"

CODE_FAIT = 0
CODE_GESTE = 1
CODE_USAGE = 2
CODE_API_MUETTE = 3
CODE_MANQUE = 4
CODE_REFUS = 5
CODE_EN_VOL = 6


class _Refus(Exception):
    """Un verbe qui s'arrête avant d'agir, avec son code et sa raison."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class Exercice:
    """L'état d'un exercice, relu et réécrit à chaque verbe (`exercice.json`)."""

    identifiant: str
    dossier: Path
    ouvert_le: str
    api: str
    espace: str
    atelier: str
    oracle_sha256: str
    titre: str = ""
    conversation: str = ""
    projet_id: str = ""
    racine: str = ""
    runs: list[str] = field(default_factory=list)
    consigne: str = CONSIGNE_DEFAUT
    gestes: list[dict[str, Any]] = field(default_factory=list)
    conclusion: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def lire(cls, dossier: Path) -> Exercice | None:
        """L'exercice rangé dans `dossier`, ou `None` s'il n'y en a pas de lisible."""
        try:
            donnees = json.loads((dossier / FICHIER_ETAT).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(donnees, dict) or donnees.get("version") != VERSION_FORMAT:
            return None
        donnees.pop("version")
        try:
            return cls(dossier=dossier, **donnees)
        except TypeError:
            return None

    def ecrire(self) -> None:
        """Réécrit `exercice.json` — à côté puis renommé, jamais à moitié."""
        donnees = {"version": VERSION_FORMAT, **asdict(self)}
        donnees.pop("dossier")
        partiel = self.dossier / f"{FICHIER_ETAT}.partiel"
        partiel.write_text(
            json.dumps(donnees, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        partiel.replace(self.dossier / FICHIER_ETAT)

    @property
    def feuille(self) -> str:
        """La feuille de la personne, entière — `""` si elle n'en a pas."""
        try:
            return (self.dossier / FICHIER_PERSONNE).read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    @property
    def duree_s(self) -> float:
        """Le temps passé dans les gestes de l'exercice, attentes comprises."""
        return sum(float(geste.get("duree_s") or 0.0) for geste in self.gestes)


@dataclass
class _Monde:
    """Ce dont les verbes ont besoin du monde — injectable pour les tests."""

    client: ClientAPI
    racine: Path
    sortie: TextIO
    erreur: TextIO
    juge: Juge | None = None
    atelier: Path | None = None
    horloge: Callable[[], float] = time.monotonic
    dormir: Callable[[float], None] = time.sleep
    client_redis: Any = None
    donnees_banc: Donnees | None = None

    def dire(self, ligne: str) -> None:
        print(ligne, file=self.sortie)

    def reserver(self) -> Atelier:
        """L'atelier de l'exercice, à lui seul — son nom est l'identifiant de l'exercice."""
        environnement = None if self.atelier is None else {VARIABLE_ATELIER: str(self.atelier)}
        return Atelier.reserver(horodatage(), environnement=environnement)


# ── Les verbes ────────────────────────────────────────────────────────────────


def _ouvrir(monde: _Monde, args: argparse.Namespace) -> int:
    """Ouvre un exercice : l'oracle d'abord, la feuille de la personne, une conversation neuve."""
    oracle = _texte_du_fichier(args.oracle, "l'oracle")
    personne = (
        _texte_du_fichier(args.personne, "la feuille de la personne") if args.personne else ""
    )
    _exiger_l_api(monde)
    atelier = monde.reserver()
    if monde.atelier is None:
        # Après la réservation : l'exercice qui commence compte parmi les derniers, et
        # s'est déjà nommé dans son atelier. Un atelier injecté ne fait pas le ménage.
        retenir_le_poste(monde.dire)
    dossier = monde.racine / atelier.passage
    dossier.mkdir(parents=True)
    (dossier / FICHIER_ORACLE).write_text(oracle + "\n", encoding="utf-8")
    if personne:
        (dossier / FICHIER_PERSONNE).write_text(personne + "\n", encoding="utf-8")
    (dossier / FICHIER_PROMPT).write_text(
        f"# Ce qui a été dit à Maestro — exercice {atelier.passage}\n", encoding="utf-8"
    )
    (dossier / FICHIER_DEROULE).write_text(
        f"# Déroulé — exercice {atelier.passage}\n", encoding="utf-8"
    )
    debut = monde.horloge()
    exercice = Exercice(
        identifiant=atelier.passage,
        dossier=dossier,
        ouvert_le=datetime.now(UTC).isoformat(timespec="seconds"),
        api=base_locale(),
        espace=monde.client.espace(),
        atelier=str(atelier.racine),
        oracle_sha256=_empreinte(dossier / FICHIER_ORACLE),
        titre=args.titre or "",
        conversation=monde.client.ouvrir_conversation(),
    )
    exercice.gestes.append(_geste("ouvrir", debut, monde.horloge(), CODE_FAIT))
    exercice.ecrire()
    monde.dire(
        f"Exercice {exercice.identifiant} ouvert — oracle écrit avant de jouer "
        f"({exercice.oracle_sha256[:12]}), "
        f"{'feuille de la personne' if personne else 'personne sans feuille (sans avis)'}, "
        f"conversation {exercice.conversation}, API {exercice.api} "
        f"(espace « {exercice.espace or '?'} »)."
    )
    monde.dire(f"  Relevé : {dossier}")
    monde.dire(f"  Projets : {atelier.racine}")
    monde.dire(
        "  Suite : `semer <dossier>` ou `naitre <fichier>`, puis `dire`, `suivre`, "
        "`relever`, `conclure`."
    )
    return CODE_FAIT


def _semer(monde: _Monde, ex: Exercice, ctx: Contexte, args: argparse.Namespace) -> int:
    """Sème une copie d'un projet existant dans l'atelier et la déclare."""
    _sans_projet(ex)
    source = Path(args.dossier)
    if not source.is_dir():
        raise _Refus(CODE_USAGE, f"aucun dossier à semer : {source}")
    nom = args.nom or source.resolve().name
    racine = _dossier_libre(Path(ex.atelier), nom)
    shutil.copytree(source, racine, ignore=shutil.ignore_patterns(*COPIE_SANS))
    ctx.note("projet semé", f"copie de {source} dans {racine}")
    projet_id = declarer(ctx, nom, racine, origine="existant")
    if args.versionner:
        fiche = monde.client.versionner_projet(projet_id)
        if not fiche.get("vcs"):
            ctx.note("versionnement refusé", str(fiche.get("versionnement_refuse") or "—"))
            monde.dire("Le projet semé n'a pas pu être versionné par le geste de l'écran Projets.")
            return CODE_GESTE
        ctx.note("projet versionné", "par `POST /api/projets/{id}/versionner`")
    if args.equipe:
        doter_d_une_equipe(ctx, projet_id)
    monde.dire(f"Projet {projet_id} semé et déclaré — {racine}")
    return CODE_FAIT


def _naitre(monde: _Monde, ex: Exercice, ctx: Contexte, args: argparse.Namespace) -> int:
    """Fait naître un projet dans le fil, rangé dans l'atelier, puis l'accepte et l'outille."""
    _sans_projet(ex)
    demande = _texte_du_fichier(args.demande, "la demande")
    racine = _dossier_libre(Path(ex.atelier), args.nom or "projet")
    racine.mkdir(parents=True)
    _consigner_le_prompt(ex, "naitre", demande)
    issue = naitre_dans_l_atelier(
        ctx,
        ex.conversation,
        demande,
        racine,
        "l'exercice",
        precision=ex.feuille or REPONSE_SANS_PRECISION,
    )
    if issue is not None:
        return _pas_abouti(monde, issue)
    naissance = accepter_le_projet(
        ctx,
        ex.conversation,
        racine,
        versionner=args.versionner,
        repondre=_repondeur(ex),
    )
    if isinstance(naissance, Issue):
        return _pas_abouti(monde, naissance)
    projet_id, ecrites = naissance
    monde.dire(
        f"Projet {projet_id} né dans le fil — {racine} ; outillage écrit : {', '.join(ecrites)}"
    )
    return CODE_FAIT


def _dire(monde: _Monde, ex: Exercice, ctx: Contexte, args: argparse.Namespace) -> int:
    """Dit un message au fil, et fait ce qu'une personne qui accepte ce qu'on lui montre ferait."""
    _avec_projet(ex)
    texte = _texte_du_fichier(args.message, "le message")
    _consigner_le_prompt(ex, "dire", texte)
    reponse = demander(ctx, ex.conversation, ex.projet_id, texte)
    if reponse.get("recrutement"):
        recrutement = valider_l_equipe(ctx, ex.conversation, ex.projet_id)
        if isinstance(recrutement, Issue):
            return _pas_abouti(monde, recrutement)
        reponse = recrutement[2]
    if _demande_d_outillage(reponse):
        monde.dire("Le fil pose une question ou propose une pièce d'outillage : `repondre`.")
        return CODE_FAIT
    if not reponse.get("proposition"):
        monde.dire(f"Le fil a répondu sans rien proposer : {extrait(reponse)}")
        return CODE_FAIT
    run_id = run_de(accorder(ctx, ex.conversation, ex.projet_id))
    if not run_id:
        monde.dire("L'accord n'a ouvert aucun run.")
        return CODE_GESTE
    ex.runs.append(run_id)
    monde.dire(f"Run {run_id} ouvert sur accord — suite : `suivre`.")
    return CODE_FAIT


def _repondre(monde: _Monde, ex: Exercice, ctx: Contexte, _args: argparse.Namespace) -> int:
    """Répond, par la feuille, à ce qui attend une réponse : l'outillage du fil, puis les agents."""
    repondu = False
    fil = monde.client.fil(ex.conversation) if ex.conversation else []
    if fil and _demande_d_outillage(fil[-1]):
        ecrites, inacheve = outiller_dans_le_fil(
            ctx, ex.conversation, fil[-1], repondre=_repondeur(ex)
        )
        repondu = True
        if ecrites:
            monde.dire(f"Outillage écrit : {', '.join(ecrites)}")
        if inacheve:
            monde.dire(inacheve)
            return CODE_GESTE
    feuille = ex.feuille
    for run_id in ex.runs:
        en_attente = _questions_en_attente(monde.client, ex.projet_id, run_id)
        if en_attente and not feuille:
            for question in en_attente:
                ctx.note("question d'un agent, laissée sans réponse", _question_en_mots(question))
            monde.dire(
                f"{len(en_attente)} question(s) d'agent sur {run_id} : la personne n'a pas de "
                "feuille, elle ne répond pas."
            )
            continue
        if en_attente:
            repondre_aux_agents(monde.client, run_id, ex.projet_id, lambda _q: feuille, ctx.note)
            repondu = True
            monde.dire(
                f"{len(en_attente)} question(s) d'agent sur {run_id}, répondue(s) par la feuille."
            )
    if not repondu:
        monde.dire("Rien n'attend de réponse de la personne.")
    return CODE_FAIT


def _arbitrer(monde: _Monde, ex: Exercice, ctx: Contexte, args: argparse.Namespace) -> int:
    """Pose la consigne de la personne, et tranche selon elle ce qui attend déjà."""
    ex.consigne = args.consigne
    ctx.note("consigne d'arbitrage", ex.consigne)
    approuve = CONSIGNES[ex.consigne]
    tranches = 0
    if approuve is not None and ex.projet_id:
        for run_id in ex.runs:
            avant = len(ctx.arbitrages)
            # Un seul relevé (délai nul) : la mécanique du suivi tranche ce qui attend,
            # demande par demande et acte par acte, puis rend la main.
            attendre_le_run(
                monde.client,
                run_id,
                projet_id=ex.projet_id,
                delai_s=0.0,
                note=ctx.note,
                horloge=monde.horloge,
                dormir=monde.dormir,
                arbitrages=ctx.arbitrages,
                approuve=approuve,
            )
            tranches += len(ctx.arbitrages) - avant
    monde.dire(f"Consigne « {ex.consigne} » posée ; {tranches} demande(s) en attente tranchée(s).")
    return CODE_FAIT


def _suivre(monde: _Monde, ex: Exercice, ctx: Contexte, args: argparse.Namespace) -> int:
    """Suit un run jusqu'à son issue ou au moment nommé — borné sous le plafond d'un appel."""
    run_id = args.run or (ex.runs[-1] if ex.runs else "")
    if not run_id:
        raise _Refus(CODE_MANQUE, "aucun run à suivre : `dire` en ouvre un sur accord")
    moment = args.jusqu_a
    cible = moment[len(PREFIXE_STATUT) :] if moment.startswith(PREFIXE_STATUT) else ""
    if moment == MOMENT_DEMANDE:
        cible = EXECUTION_EN_ATTENTE_ARBITRAGE
    atteint: list[str] = []

    def arret(detail: Mapping[str, Any]) -> bool:
        statut = str(detail.get("statut") or "")
        if cible and statut == cible:
            atteint.append(f"statut « {statut} »")
        elif moment == MOMENT_QUESTION and _questions_en_attente(
            monde.client, ex.projet_id, run_id
        ):
            atteint.append("une question d'agent attend la personne")
        return bool(atteint)

    feuille = ex.feuille
    repondre = (lambda _q: feuille) if feuille and moment != MOMENT_QUESTION else None
    ctx.note(
        "suivi",
        f"{run_id} jusqu'à « {moment} », {args.delai:g} s au plus, consigne « {ex.consigne} »",
    )
    detail = attendre_le_run(
        monde.client,
        run_id,
        projet_id=ex.projet_id,
        delai_s=args.delai,
        note=ctx.note,
        horloge=monde.horloge,
        dormir=monde.dormir,
        arbitrages=ctx.arbitrages,
        approuve=CONSIGNES[ex.consigne],
        arret=None if moment == MOMENT_ISSUE else arret,
        repondre=repondre,
    )
    statut = str(detail.get("statut") or "")
    if statut in STATUTS_EXECUTION_TERMINAUX:
        ctx.note(
            "run soldé",
            f"statut « {statut} », cause « {detail.get('cause') or '—'} », "
            f"{detail.get('nb_taches')} tâche(s), {montant(detail)}",
        )
        monde.dire(f"Run {run_id} soldé : « {statut} » ({montant(detail)}).")
        return CODE_FAIT
    if atteint:
        ctx.note("moment atteint", atteint[0])
        monde.dire(f"Run {run_id} : {atteint[0]} — la main est rendue.")
        return CODE_FAIT
    ctx.note("délai passé", f"run encore en vol (statut « {statut} »)")
    monde.dire(
        f"Run {run_id} encore en vol après {args.delai:g} s (statut « {statut} ») — "
        "rejouer `suivre`."
    )
    return CODE_EN_VOL


def _relever(monde: _Monde, ex: Exercice, ctx: Contexte, _args: argparse.Namespace) -> int:
    """Relève les faits de l'exercice dans la stack et le projet — `faits.json`, `faits.md`."""
    faits = _faits(monde.client, ex)
    (ex.dossier / FICHIER_FAITS).write_text(
        json.dumps(faits, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (ex.dossier / FICHIER_FAITS_LISIBLES).write_text(_faits_en_mots(faits), encoding="utf-8")
    cout = faits["cout"]
    ctx.note("faits relevés", f"{len(faits['runs'])} run(s), coût {_dollars(cout['usd'])}")
    monde.dire(
        f"Faits relevés : {ex.dossier / FICHIER_FAITS_LISIBLES} — {len(faits['runs'])} run(s), "
        f"coût {_dollars(cout['usd'])} ({cout['source']}), {len(faits['fichiers'])} fichier(s)."
    )
    return CODE_FAIT


def _sauver(monde: _Monde, ex: Exercice, ctx: Contexte, _args: argparse.Namespace) -> int:
    """Sauve l'état de la stack que `start.sh --etat-banc` rouvre — celui du banc de la copie."""
    # Paresseux, comme dans le banc : `python -m maestro.scenarios.etat` charge ce paquet.
    from maestro.scenarios import etat

    banc = monde.donnees_banc or donnees_du_banc()
    espace = monde.client.espace()
    if espace != banc.espace.nom:
        raise _Refus(
            CODE_REFUS,
            f"l'API sert l'espace « {espace or '?'} », pas le banc de cette copie "
            f"(« {banc.espace.nom} ») : l'état sauvé ne serait pas celui de l'exercice. "
            f"Jouer l'exercice sur le banc : {GESTE_STACK}",
        )
    conclusion = ex.conclusion
    resultat = Resultat(
        identifiant="exercice",
        titre=ex.titre or f"exercice {ex.identifiant}",
        verdict=str(conclusion.get("verdict") or "non conclu"),
        motif=str(conclusion.get("motif") or ""),
        duree_s=ex.duree_s,
        run_id=ex.runs[-1] if ex.runs else "",
        projet_id=ex.projet_id,
        racine=ex.racine,
        cout_usd=_cout_releve(ex),
    )
    try:
        instantane = etat.sauver(
            Rapport(horodatage=ex.identifiant, resultats=(resultat,)),
            Path(ex.atelier),
            banc,
            monde.client_redis or etat.client_redis(),
        )
    except Exception as exc:  # Redis ou disque : l'exercice, lui, est intact
        raise _Refus(CODE_REFUS, f"état NON sauvé : {exc}") from exc
    ctx.note("état sauvé", str(instantane.dossier))
    monde.dire(
        f"État sauvé : {instantane.dossier} ({instantane.evenements} événement(s), "
        f"{instantane.projets} projet(s)) — rouvrable sans rien rejouer : {GESTE_ROUVRIR}"
    )
    return CODE_FAIT


def _conclure(monde: _Monde, ex: Exercice, ctx: Contexte, args: argparse.Namespace) -> int:
    """Relève les faits, puis rend le verdict contre l'oracle écrit avant de jouer."""
    motif = _texte_du_fichier(args.motif, "le motif du verdict")
    _relever(monde, ex, ctx, args)
    faits = json.loads((ex.dossier / FICHIER_FAITS).read_text(encoding="utf-8"))
    ex.conclusion = {
        "verdict": args.verdict,
        "motif": motif,
        "le": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    oracle = (ex.dossier / FICHIER_ORACLE).read_text(encoding="utf-8").strip()
    runs = ", ".join(ex.runs) or "aucun"
    lignes = [
        f"# Exercice {ex.identifiant} — {args.verdict}",
        "",
        f"## Oracle (écrit avant de jouer, {ex.oracle_sha256[:12]})",
        "",
        oracle,
        "",
        "## Verdict",
        "",
        motif,
        "",
        "## Pièces",
        "",
        f"- run(s) : {runs}",
        f"- projet : {ex.projet_id or '—'} — {ex.racine or '—'}",
        f"- coût : {_dollars(faits['cout']['usd'])} ({faits['cout']['source']})",
        f"- durée des gestes : {ex.duree_s:.0f} s",
        f"- relevé : {FICHIER_FAITS_LISIBLES}, {FICHIER_DEROULE}, {FICHIER_PROMPT}",
        "",
    ]
    (ex.dossier / FICHIER_VERDICT).write_text("\n".join(lignes), encoding="utf-8")
    ctx.note("verdict", f"{args.verdict} — {motif.splitlines()[0] if motif else ''}")
    monde.dire(
        f"Exercice {ex.identifiant} : {args.verdict} — run(s) {runs}, coût "
        f"{_dollars(faits['cout']['usd'])}, gestes {ex.duree_s:.0f} s, relevé {ex.dossier}"
    )
    return CODE_FAIT


#: Les verbes qui jouent sur un exercice ouvert, dans l'ordre où l'aide les nomme.
VERBES: dict[str, Callable[[_Monde, Exercice, Contexte, argparse.Namespace], int]] = {
    "semer": _semer,
    "naitre": _naitre,
    "dire": _dire,
    "repondre": _repondre,
    "arbitrer": _arbitrer,
    "suivre": _suivre,
    "relever": _relever,
    "sauver": _sauver,
    "conclure": _conclure,
}


# ── Les faits ─────────────────────────────────────────────────────────────────


def _faits(client: ClientAPI, ex: Exercice) -> dict[str, Any]:
    """Ce que la stack et le projet disent de l'exercice, lu et jamais reconstruit."""
    projet: dict[str, Any] = {}
    runs: list[dict[str, Any]] = []
    validations: list[dict[str, Any]] = []
    questions: list[dict[str, Any]] = []
    dernier: Mapping[str, Any] = {}
    if ex.projet_id:
        projet = next((p for p in client.projets() if str(p.get("id")) == ex.projet_id), {})
        for run_id in ex.runs:
            detail = dict(client.execution(run_id, projet_id=ex.projet_id))
            evenements = list(detail.get("evenements") or [])
            detail["evenements"] = evenements[-EVENEMENTS_RELEVES_MAX:]
            detail["evenements_total"] = len(evenements)
            detail["cartes"] = client.taches(run_id, projet_id=ex.projet_id)
            runs.append(detail)
            dernier = detail
        validations = [
            v
            for v in client.validations(projet_id=ex.projet_id)
            if str(v.get("run_id") or "") in ex.runs
        ]
        questions = [
            q
            for q in client.questions(projet_id=ex.projet_id)
            if str(q.get("run_id") or "") in ex.runs
        ]
    total, source, postes = cout_du_grand_livre(client, ex.projet_id, dernier)
    racine = Path(ex.racine) if ex.racine else None
    return {
        "exercice": ex.identifiant,
        "releve_le": datetime.now(UTC).isoformat(timespec="seconds"),
        "projet": projet or {"id": ex.projet_id, "racine": ex.racine},
        "run_ids": list(ex.runs),
        "runs": runs,
        "validations": validations,
        "questions": questions,
        "fil": client.fil(ex.conversation) if ex.conversation else [],
        "cout": {"usd": total, "source": source, "postes": postes},
        "fichiers": list(_fichiers(racine)) if racine is not None else [],
        "commits": _commits(racine) if racine is not None else [],
        "duree": {"ouvert_le": ex.ouvert_le, "gestes_s": round(ex.duree_s, 1)},
        "consigne": ex.consigne,
    }


def _fichiers(racine: Path) -> Iterator[dict[str, Any]]:
    """Les fichiers du livrable, en chemins relatifs — dépendances et caches écartés."""
    if not racine.is_dir():
        return
    vus = 0
    for chemin in sorted(racine.rglob("*")):
        relatif = chemin.relative_to(racine)
        if any(partie in DOSSIERS_HORS_RELEVE for partie in relatif.parts) or not chemin.is_file():
            continue
        if vus >= FICHIERS_RELEVES_MAX:
            yield {"chemin": "…", "octets": 0, "tronque": True}
            return
        vus += 1
        yield {"chemin": relatif.as_posix(), "octets": chemin.stat().st_size}


def _commits(racine: Path) -> list[str]:
    """Les derniers commits du projet, s'il est sous Git — `hash sujet`, du plus récent."""
    if not (racine / ".git").exists():
        return []
    git = shutil.which("git")
    if git is None:
        return []
    try:
        fini = subprocess.run(  # noqa: S603 - git du poste, sur le projet de l'exercice
            [git, "-C", str(racine), "log", f"-n{COMMITS_RELEVES_MAX}", "--format=%h %s"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [ligne for ligne in (fini.stdout or "").splitlines() if ligne.strip()]


def _faits_en_mots(faits: Mapping[str, Any]) -> str:
    """Les faits en Markdown, à lire à l'œil nu — `faits.json` les garde entiers."""
    projet = faits["projet"]
    cout = faits["cout"]
    lignes = [
        f"# Faits — exercice {faits['exercice']}",
        "",
        f"Relevés le {faits['releve_le']} · consigne d'arbitrage « {faits['consigne']} »",
        "",
        "## Projet",
        "",
        f"- {projet.get('nom') or '—'} ({projet.get('id') or '—'}) — {projet.get('racine') or '—'}",
        "",
        "## Runs",
        "",
    ]
    if not faits["runs"]:
        lignes.append("- aucun")
    for run in faits["runs"]:
        lignes.append(
            f"- **{run.get('run_id')}** — statut « {run.get('statut')} », cause "
            f"« {run.get('cause') or '—'} », {run.get('nb_taches')} tâche(s), {montant(run)}"
        )
        for carte in run.get("cartes") or []:
            lignes.append(
                f"  - {carte.get('statut') or '—'} · {carte.get('titre') or carte.get('id')}"
                f" ({carte.get('agent') or '—'})"
            )
    lignes += ["", "## Arbitrages", ""]
    lignes += [
        f"- {v.get('statut')} · {v.get('titre') or v.get('outil') or v.get('tache_id')}"
        for v in faits["validations"]
    ] or ["- aucun"]
    lignes += ["", "## Questions des agents", ""]
    lignes += [
        f"- {q.get('statut')} · {_question_en_mots(q)}"
        + (f" → {' '.join(str(q.get('reponse')).split())[:300]}" if q.get("reponse") else "")
        for q in faits["questions"]
    ] or ["- aucune"]
    lignes += ["", "## Fil", ""]
    for message in faits["fil"]:
        cartes = [cle for cle in _CARTES_DU_FIL if message.get(cle)]
        contenu = " ".join(str(message.get("contenu") or "").split())
        if len(contenu) > EXTRAIT_FIL:
            contenu = f"{contenu[: EXTRAIT_FIL - 1]}…"
        lignes.append(
            f"- **{message.get('auteur') or '—'}**"
            f"{' · run ' + str(message['run_id']) if message.get('run_id') else ''}"
            f"{' · ' + ', '.join(cartes) if cartes else ''} — {contenu}"
        )
    postes = ", ".join(f"{nom} {_dollars(usd)}" for nom, usd in cout["postes"].items())
    lignes += [
        "",
        "## Coût",
        "",
        f"- {_dollars(cout['usd'])} — {cout['source']}{' (' + postes + ')' if postes else ''}",
        "",
        "## Fichiers du projet",
        "",
    ]
    lignes += [f"- {f['chemin']} ({f['octets']} o)" for f in faits["fichiers"]] or ["- aucun"]
    lignes += ["", "## Commits", ""]
    lignes += [f"- {commit}" for commit in faits["commits"]] or ["- aucun"]
    duree = faits["duree"]
    lignes += [
        "",
        "## Durée",
        "",
        f"- ouvert le {duree['ouvert_le']}, {duree['gestes_s']:.0f} s dans les gestes",
        "",
    ]
    return "\n".join(lignes)


#: Ce qu'un message du fil peut porter, que `faits.md` nomme à côté de son texte.
_CARTES_DU_FIL = (
    "projet_propose",
    "projet_cree",
    "question",
    "piece",
    "piece_ecrite",
    "recrutement",
    "proposition",
)


# ── La plomberie ──────────────────────────────────────────────────────────────


def _jouer(monde: _Monde, verbe: str, args: argparse.Namespace) -> int:
    """Joue un verbe sur l'exercice visé : oracle confronté, gestes consignés, état réécrit."""
    ex = _exercice(monde.racine, args.exercice)
    _oracle_intact(ex)
    _exiger_l_api(monde)
    journal = Journal(echo=lambda etape: monde.dire(_en_direct(etape)))
    ctx = Contexte(
        client=monde.client,
        atelier=Atelier(Path(ex.atelier)),
        juge=monde.juge or JugeModele(),
        journal=journal,
        horloge=monde.horloge,
        dormir=monde.dormir,
        projet_id=ex.projet_id,
        racine=Path(ex.racine) if ex.racine else None,
    )
    debut = monde.horloge()
    try:
        code = VERBES[verbe](monde, ex, ctx, args)
    except ErreurAPI as refus:
        ctx.note("refusé par l'API", str(refus))
        monde.dire(f"La stack a refusé le geste : {refus}")
        code = CODE_GESTE
    finally:
        ex.projet_id = ctx.projet_id
        ex.racine = str(ctx.racine) if ctx.racine is not None else ex.racine
    ex.gestes.append(_geste(verbe, debut, monde.horloge(), code))
    _consigner_le_deroule(ex, verbe, journal, code)
    ex.ecrire()
    return code


def _exercice(racine: Path, identifiant: str | None) -> Exercice:
    """L'exercice nommé, ou le dernier ouvert — `_Refus(4)` s'il n'y en a pas."""
    if identifiant:
        ex = Exercice.lire(racine / identifiant)
        if ex is None:
            raise _Refus(CODE_MANQUE, f"aucun exercice lisible sous {racine / identifiant}")
        return ex
    dossiers = (
        sorted((d for d in racine.iterdir() if (d / FICHIER_ETAT).is_file()), key=lambda d: d.name)
        if racine.is_dir()
        else []
    )
    for dossier in reversed(dossiers):
        ex = Exercice.lire(dossier)
        if ex is not None:
            return ex
    raise _Refus(CODE_MANQUE, f"aucun exercice sous {racine} : `ouvrir --oracle <fichier>`")


def _oracle_intact(ex: Exercice) -> None:
    """Refuse (`5`) de jouer si l'oracle a bougé depuis l'ouverture."""
    chemin = ex.dossier / FICHIER_ORACLE
    if not chemin.is_file() or _empreinte(chemin) != ex.oracle_sha256:
        raise _Refus(
            CODE_REFUS,
            f"l'oracle de l'exercice {ex.identifiant} a changé depuis son ouverture : un "
            "oracle s'écrit avant de jouer. Pour un autre oracle, ouvrir un autre exercice.",
        )


def _exiger_l_api(monde: _Monde) -> None:
    if not monde.client.sante():
        raise _Refus(
            CODE_API_MUETTE,
            f"l'API de la Control Tower ne répond pas sur {base_locale()} — l'exercice passe "
            f"par la porte d'entrée réelle. Démarrer la stack sur le banc : {GESTE_STACK}",
        )


def _sans_projet(ex: Exercice) -> None:
    if ex.projet_id:
        raise _Refus(
            CODE_MANQUE,
            f"l'exercice a déjà son projet ({ex.projet_id}) : un autre projet, un autre exercice",
        )


def _avec_projet(ex: Exercice) -> None:
    if not ex.projet_id:
        raise _Refus(CODE_MANQUE, "l'exercice n'a pas de projet : `semer` ou `naitre` d'abord")


def _repondeur(ex: Exercice) -> Repondeur:
    """Ce que la personne répond à une question d'outillage : sa feuille entière, ou sans avis."""
    feuille = ex.feuille
    if not feuille:
        return reponse_a
    return lambda _question: (feuille, True)


def _demande_d_outillage(message: Mapping[str, Any]) -> bool:
    """Le message porte-t-il une question ou une pièce d'outillage à trancher ?"""
    question, piece = message.get("question"), message.get("piece")
    return (isinstance(question, Mapping) and bool(question.get("cle"))) or (
        isinstance(piece, Mapping) and bool(piece.get("chemin"))
    )


def _questions_en_attente(client: ClientAPI, projet_id: str, run_id: str) -> list[dict[str, Any]]:
    if not projet_id:
        return []
    return [
        q
        for q in client.questions(projet_id=projet_id)
        if str(q.get("run_id") or "") == run_id
        and str(q.get("statut") or "") == QUESTION_EN_ATTENTE
    ]


def _question_en_mots(question: Mapping[str, Any]) -> str:
    texte = " ".join(str(question.get("question") or "").split())[:300]
    return f"{question.get('agent') or '—'} ({question.get('tache_id') or '—'}) : {texte}"


def _pas_abouti(monde: _Monde, issue: Issue) -> int:
    nature = "empêché" if issue.empechement else "pas abouti"
    monde.dire(f"Geste {nature} : {issue.motif}")
    return CODE_GESTE


def _cout_releve(ex: Exercice) -> float | None:
    try:
        faits = json.loads((ex.dossier / FICHIER_FAITS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    usd = faits.get("cout", {}).get("usd")
    return None if usd is None else float(usd)


def _dossier_libre(atelier: Path, nom: str) -> Path:
    """Un dossier de l'atelier qui n'existe pas encore — `<nom>`, puis `<nom>-2`…"""
    rang = 1
    while True:
        chemin = atelier / (nom if rang == 1 else f"{nom}-{rang}")
        if not chemin.exists():
            return chemin
        rang += 1


def _texte_du_fichier(chemin: str, quoi: str) -> str:
    """Le texte d'un fichier passé en argument — un texte long voyage par un fichier (#233)."""
    try:
        texte = Path(chemin).read_text(encoding="utf-8").strip()
    except OSError as echec:
        raise _Refus(CODE_USAGE, f"{quoi} illisible ({chemin}) : {echec}") from echec
    if not texte:
        raise _Refus(CODE_USAGE, f"{quoi} est vide ({chemin})")
    return texte


def _empreinte(chemin: Path) -> str:
    return hashlib.sha256(chemin.read_bytes()).hexdigest()


def _geste(verbe: str, debut: float, fin: float, code: int) -> dict[str, Any]:
    return {
        "verbe": verbe,
        "le": datetime.now(UTC).isoformat(timespec="seconds"),
        "duree_s": round(max(fin - debut, 0.0), 1),
        "code": code,
    }


def _consigner_le_prompt(ex: Exercice, verbe: str, texte: str) -> None:
    with (ex.dossier / FICHIER_PROMPT).open("a", encoding="utf-8") as fichier:
        fichier.write(f"\n## {verbe} — {datetime.now():%H:%M:%S}\n\n{texte}\n")


def _consigner_le_deroule(ex: Exercice, verbe: str, journal: Journal, code: int) -> None:
    with (ex.dossier / FICHIER_DEROULE).open("a", encoding="utf-8") as fichier:
        fichier.write(f"\n## {verbe} — {datetime.now():%H:%M:%S} — code {code}\n\n")
        for etape in journal.etapes:
            fichier.write(f"- **{etape.libelle}**{' — ' + etape.detail if etape.detail else ''}\n")


def _en_direct(etape: Any) -> str:
    return f"  · {etape.libelle}{' — ' + etape.detail if etape.detail else ''}"


def _dollars(usd: float | None) -> str:
    return "non rapporté" if usd is None else f"{usd:.4f} $"


class _Analyseur(argparse.ArgumentParser):
    """Un analyseur qui rend la main au lieu de quitter le process."""

    def error(self, message: str) -> NoReturn:
        raise _Refus(CODE_USAGE, message)


def _analyseur() -> _Analyseur:
    analyseur = _Analyseur(
        prog="python -m maestro.scenarios.exercice",
        description="L'exercice qu'une session construit pour ce qu'elle vient d'implémenter.",
    )
    verbes = analyseur.add_subparsers(dest="verbe", required=True)

    def verbe(nom: str, aide: str) -> argparse.ArgumentParser:
        sous = verbes.add_parser(nom, help=aide)
        if nom != "ouvrir":
            sous.add_argument("--exercice", help="l'exercice visé (le dernier ouvert sinon)")
        return sous

    ouvrir = verbe("ouvrir", "ouvre un exercice, oracle écrit d'abord")
    ouvrir.add_argument("--oracle", required=True, help="ce qu'on doit constater (fichier)")
    ouvrir.add_argument("--personne", help="la feuille de la personne (fichier)")
    ouvrir.add_argument("--titre", help="ce que l'exercice exerce, en une ligne")
    semer = verbe("semer", "sème une copie d'un projet existant et la déclare")
    semer.add_argument("dossier")
    semer.add_argument("--nom")
    semer.add_argument("--versionner", action="store_true")
    semer.add_argument("--equipe", action="store_true", help="dote le projet de l'équipe proposée")
    naitre = verbe("naitre", "fait naître un projet dans le fil, puis l'outille")
    naitre.add_argument("demande", help="la phrase de la personne (fichier)")
    naitre.add_argument("--nom")
    naitre.add_argument("--versionner", action="store_true")
    dire = verbe("dire", "dit un message au fil et accepte ce qu'il propose")
    dire.add_argument("message", help="le message (fichier)")
    verbe("repondre", "répond par la feuille à ce qui attend la personne")
    arbitrer = verbe("arbitrer", "pose la consigne d'arbitrage et tranche ce qui attend")
    arbitrer.add_argument("consigne", choices=tuple(CONSIGNES))
    suivre = verbe("suivre", "suit un run jusqu'à son issue ou un moment nommé")
    suivre.add_argument("--jusqu-a", dest="jusqu_a", default=MOMENT_ISSUE, type=_moment)
    suivre.add_argument("--delai", type=float, default=DELAI_SUIVI_S)
    suivre.add_argument("--run")
    verbe("relever", "relève les faits de l'exercice")
    verbe("sauver", "sauve l'état de la stack, que start.sh --etat-banc rouvre")
    conclure = verbe("conclure", "relève les faits et rend le verdict contre l'oracle")
    conclure.add_argument("verdict", choices=VERDICTS)
    conclure.add_argument("motif", help="pourquoi, pièces à l'appui (fichier)")
    return analyseur


def _moment(brut: str) -> str:
    moment = brut.strip()
    if moment in (MOMENT_ISSUE, MOMENT_DEMANDE, MOMENT_QUESTION):
        return moment
    if moment.startswith(PREFIXE_STATUT) and moment[len(PREFIXE_STATUT) :]:
        return moment
    raise argparse.ArgumentTypeError(
        f"moment inconnu « {brut} » : issue, demande, question ou statut=<statut>"
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    client: ClientAPI | None = None,
    juge: Juge | None = None,
    racine: Path | None = None,
    atelier: Path | None = None,
    horloge: Callable[[], float] = time.monotonic,
    dormir: Callable[[float], None] = time.sleep,
    client_redis: Any = None,
    donnees_banc: Donnees | None = None,
    sortie: TextIO | None = None,
    erreur: TextIO | None = None,
) -> int:
    """Point d'entrée : voir l'en-tête du module pour les verbes et les codes.

    Tout ce qui touche au monde est injectable **pour les tests** — une fausse API, un
    atelier et un dossier d'exercices jetables, aucune attente réelle. Un atelier injecté
    ne fait pas la rétention du poste (`projets.retenir_le_poste`).
    """
    sortie = sortie or sys.stdout
    erreur = erreur or sys.stderr
    try:
        args = _analyseur().parse_args(list(sys.argv[1:] if argv is None else argv))
    except _Refus as refus:
        print(f"Usage : {refus}", file=erreur)
        return refus.code
    except SystemExit as sortie_aide:  # `--help` : l'aide est déjà écrite
        return int(sortie_aide.code or 0)
    monde = _Monde(
        client=client or ClientAPI(TransportHTTP(base_locale())),
        racine=racine or RACINE_EXERCICES,
        sortie=sortie,
        erreur=erreur,
        juge=juge,
        atelier=atelier,
        horloge=horloge,
        dormir=dormir,
        client_redis=client_redis,
        donnees_banc=donnees_banc,
    )
    try:
        if args.verbe == "ouvrir":
            return _ouvrir(monde, args)
        return _jouer(monde, args.verbe, args)
    except _Refus as refus:
        print(f"Exercice refusé : {refus}", file=erreur)
        return refus.code


if __name__ == "__main__":
    for flux in (sys.stdout, sys.stderr):
        if hasattr(flux, "reconfigure"):
            flux.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())

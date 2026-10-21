"""La recette du run : les critères du brief, vérifiés en les exécutant sur le livrable (#1530).

## Le constat

Sur p5 (run `fea601a5c629`, critère C2 du bilan du 2026-10-14), la dernière tâche du
plan, `recette-finale`, rejouait toute l'application pour confronter les 8 critères
du brief : 12 min 50 s du chemin critique, 1,633 $, 34 réponses du modèle et 2
arbitrages — un agent qui installait puppeteer pour écrire son banc navigateur à la
main. Chaque tâche avait pourtant déjà été vérifiée en l'exécutant (#1177), en 29 à
53 s et pour 0,12 à 0,24 $ par juge. Ce que la tâche rendait est à garder : une
recette dans le projet, chaque critère avec sa mesure. Ce qu'elle coûtait ne l'est
pas. La chaîne qui développe ce dépôt fait ce travail **une fois, à la clôture**, en
confrontant les critères au diff (`criteres-note`), sans ticket dédié
(docs/50 § 4.1, lot A1).

## Ce que ce module décide

**La recette n'est plus une tâche du plan, c'est un geste de Maestro.** À la fin du
run, une fois toutes les tâches soldées et fusionnées, chaque critère d'acceptation
du brief est confronté au **livrable intégré** — la racine du projet, telle que la
personne la reçoit. Le playbook du Chef de projet ne demande plus de tâche qui ne
ferait que vérifier (`maestro/orchestrator/playbook.md`, Méthode § 4 et § 7).

**Le même mécanisme que la vérification d'une tâche, pas un second.** Les critères
du brief deviennent les critères de réussite d'une tâche que personne n'exécute
(`tache_de_recette`), et c'est `VerificateurTaches.verifier` qui les traduit en
contrôles, joue les commandes de l'outillage du projet, démarre et sonde le service
quand un critère porte sur ce qu'il sert, et contre-expertise une commande qui n'a
pas tenu avant de la croire. Ce module ne connaît ni miroir ni projet : c'est
l'exécuteur qui lui donne la `Livraison` — un **miroir** de la racine (#1496), jamais
la racine elle-même. La recette d'un jalon contre son cahier (#1424) passera par le
même `recetter`, avec d'autres critères.

**Un critère, un verdict.** Chaque critère du brief rend `tenu`, `non tenu` ou `non
vérifiable` — avec sa preuve, ou la raison pour laquelle rien n'a pu être constaté
(`Constat`, dont l'état « non joué » se dit ici « non vérifiable »). Les critères sont
numérotés dans la tâche (`C1`, `C2`…) et le vérificateur les recopie numéro compris :
un format que Maestro impose et relit, jamais un texte qu'il interprète. Un critère
que le vérificateur n'a traduit en aucun contrôle est **non vérifiable** — jamais
tenu par défaut.

**Rien n'est coché qui ne tient pas.** La recette ne corrige rien et ne relance
aucune tâche : elle dit. Dans le projet, seuls les critères tenus sont cochés
(`en_markdown`) ; un critère non tenu porte sa preuve. Le récit de fin la résume
(`maestro.controltower.recit`), et le run ne se dit pas tenu sur ce qu'elle n'a pas
vu tenir.

## Où elle s'écrit

Dans le projet, sous `.maestro/recette/<run>.md` (`chemin_de_la_recette`) — le dossier
de Maestro dans un projet (docs/38 § 4.3), où l'outillage range déjà son manifeste :
un nom qui ne prend la place d'aucun fichier de la personne, et que l'atelier d'une
tâche ne peut pas prendre (`ATELIERS_RESERVES`). Au régime du projet, comme tout ce que
Maestro écrit chez quelqu'un (docs/24 § 2.4) :

- **non versionné** : en place, sous la frontière d'écriture du projet ;
- **versionné** : commitée sur la branche de travail (`deposer_sur_la_base`), et
  **seulement sous l'accord d'écriture du run** (#706) — un run dont aucune fusion n'a
  été accordée n'écrit pas sa recette non plus. Elle reste alors au journal et dans
  le récit, et l'étape le dit.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from maestro.engine.verification import (
    CONSTAT_NON_JOUE,
    CONSTAT_NON_TENU,
    CONSTAT_TENU,
    Constat,
    Livraison,
    VerificateurEnPanne,
    VerificateurTaches,
)
from maestro.orchestrator.schema import Brief, Task
from maestro.projets.application import ApplicationRefusee, deposer_sur_la_base
from maestro.projets.modele import Projet
from maestro.projets.racine import RacineRefusee, valider_racine
from maestro.sandbox.en_place import DOSSIER_ATELIER, FrontiereEcriture
from maestro.telemetry import PlafondDepenseDepasse

if TYPE_CHECKING:
    from maestro.engine.retry import PolitiqueRelance

#: L'identifiant de la tâche que la recette fait vérifier — une tâche que personne
#: n'exécute et qui n'entre dans aucun plan : elle ne porte que les critères.
ID_TACHE_RECETTE = "recette-du-run"

#: Le dossier des recettes dans un projet : sous `.maestro/`, le dossier de Maestro.
DOSSIER_RECETTE = f"{DOSSIER_ATELIER}/recette"

#: Les trois issues d'une recette, au vocabulaire du journal — celles d'une
#: vérification (#1177), au niveau du run : tout tient, au moins un critère est
#: constaté faux, ou rien de faux mais tout n'a pas pu être constaté.
STATUT_RECETTE_TENUE = "recette_tenue"
STATUT_RECETTE_NON_TENUE = "recette_non_tenue"
STATUT_RECETTE_IMPOSSIBLE = "recette_impossible"

#: Ce qu'un critère est devenu, tel que la personne le lit.
_MOTS = {CONSTAT_TENU: "tenu", CONSTAT_NON_TENU: "non tenu", CONSTAT_NON_JOUE: "non vérifiable"}

#: Le numéro qu'un critère porte en tête — le format que `tache_de_recette` impose
#: au vérificateur (« C3 — … »), relu ici et nulle part ailleurs.
_NUMERO = re.compile(r"^\W*C(\d+)\b", re.IGNORECASE)

#: La part d'une preuve que le récit lit, par critère : de quoi citer une assertion,
#: pas de quoi recopier une sortie de compilation dans un prompt.
_PREUVE_RECIT_MAX = 400

_SANS_CONTROLE = "le vérificateur n'en a établi aucun contrôle — rien ne l'a constaté"


def marque(rang: int) -> str:
    """Le numéro du critère de rang `rang` (base 0) : « C1 », « C2 »…"""
    return f"C{rang + 1}"


def tache_de_recette(criteres: Sequence[str]) -> Task:
    """La tâche que la recette fait vérifier : les critères du brief, numérotés, et rien d'autre.

    C'est le **contrat** que le vérificateur lit (`_bloc_tache`) : l'objet de la
    vérification — le livrable intégré, tel que la personne le reçoit —, chaque
    critère avec son numéro, et où chercher ce qui le prouve. Le brief, lui, vient
    à côté comme pour toute tâche (`_bloc_brief`), avec son objectif et son
    hors-périmètre.
    """
    lignes = [
        "Objectif : confronter le livrable intégré du run — la racine du projet, toutes "
        "ses tâches réunies — à chacun des critères d'acceptation du brief approuvé. "
        "Chaque tâche a déjà été vérifiée sur ses propres critères ; ici, c'est le "
        "produit assemblé, tel que la personne le reçoit.",
        "",
        "Critères de réussite — UN contrôle par critère, et le champ \"critere\" de "
        "chacun commence par son numéro, tel qu'écrit ici (« C1 — … ») :",
        *(f"{marque(rang)} — {critere}" for rang, critere in enumerate(criteres)),
        "",
        "Ce qui se constate en exécutant se joue avec l'outillage du projet : ses "
        "commandes d'installation, de test, de construction et de démarrage, lues dans "
        "le projet lui-même (son AGENTS.md, son README, son manifeste). Un critère qui "
        "porte sur ce que l'application sert se constate en la démarrant et en la "
        "sondant ; un parcours dans le navigateur se joue par l'outillage de bout en bout "
        "du projet quand il en porte un. Ce qui ne se constate qu'en lisant se lit dans "
        "le livrable.",
    ]
    return Task(
        id=ID_TACHE_RECETTE,
        titre="Recette du run — les critères d'acceptation du brief",
        description="\n".join(lignes),
        competences_requises=("qa",),
        format_sortie="Le livrable intégré du run, dans la racine du projet.",
    )


@dataclass(frozen=True)
class RecetteDuRun:
    """Ce que la recette a rendu : un constat par critère du brief, dans son ordre.

    `empechement` non vide : la recette n'a pas pu se jouer du tout (aucune tâche
    aboutie, vérificateur en panne, plafond de dépense atteint) — chaque critère
    est alors non vérifiable, avec ce motif. `fichier` est où elle s'est écrite dans
    le projet (chemin absolu), `ecriture` pourquoi elle ne s'y est pas écrite.
    """

    constats: tuple[Constat, ...]
    empechement: str = ""
    fichier: str = ""
    ecriture: str = ""

    @property
    def tenus(self) -> tuple[Constat, ...]:
        """Les critères constatés tenus — les seuls cochés."""
        return tuple(c for c in self.constats if c.etat == CONSTAT_TENU)

    @property
    def non_tenus(self) -> tuple[Constat, ...]:
        """Les critères constatés faux, preuve à l'appui."""
        return tuple(c for c in self.constats if c.etat == CONSTAT_NON_TENU)

    @property
    def non_verifiables(self) -> tuple[Constat, ...]:
        """Les critères que rien n'a pu constater — ni tenus, ni non tenus."""
        return tuple(c for c in self.constats if c.etat == CONSTAT_NON_JOUE)

    @property
    def statut(self) -> str:
        """L'issue de la recette, au vocabulaire du journal."""
        if self.non_tenus:
            return STATUT_RECETTE_NON_TENUE
        if self.constats and len(self.tenus) == len(self.constats):
            return STATUT_RECETTE_TENUE
        return STATUT_RECETTE_IMPOSSIBLE

    def resume(self) -> str:
        """La recette en une ligne — « 6/8 critère(s) du brief tenu(s), 1 non tenu… »."""
        phrase = f"{len(self.tenus)}/{len(self.constats)} critère(s) du brief tenu(s)"
        if self.non_tenus:
            phrase += f", {len(self.non_tenus)} non tenu(s)"
        if self.non_verifiables:
            phrase += f", {len(self.non_verifiables)} non vérifiable(s)"
        if self.empechement:
            phrase += f" — recette non jouée : {self.empechement}"
        return phrase

    def preuves(self) -> str:
        """Chaque critère, une ligne chacun — la preuve lisible de la recette."""
        return "\n".join(
            _ligne(rang, constat) for rang, constat in enumerate(self.constats)
        )

    def en_markdown(self, *, objectif: str, run_id: str, horodatage: str) -> str:
        """La recette telle qu'elle s'écrit dans le projet : la liste cochée, puis les preuves.

        Seuls les critères **tenus** sont cochés — un critère non tenu ou non
        vérifiable reste une case vide, sa preuve ou sa raison en regard.
        """
        lignes = [
            f"# Recette du run {run_id}",
            "",
            objectif.strip(),
            "",
            f"**{self.resume()}.**",
            "",
            f"Vérifiée par Maestro le {horodatage}, en exécutant chaque critère "
            "d'acceptation du brief sur le livrable intégré du run.",
            "",
            "## Critères",
            "",
        ]
        for rang, constat in enumerate(self.constats):
            case = "x" if constat.etat == CONSTAT_TENU else " "
            mot = _MOTS.get(constat.etat, "non vérifiable")
            verdict = mot if constat.etat == CONSTAT_TENU else f"**{mot}**"
            lignes.append(f"- [{case}] **{marque(rang)}** — {constat.critere} — {verdict}")
        lignes += ["", "## Preuves"]
        for rang, constat in enumerate(self.constats):
            lignes += ["", f"### {marque(rang)} — {constat.critere}", ""]
            mot = _MOTS.get(constat.etat, "non vérifiable")
            if constat.commande:
                code = f" → code {constat.code}" if constat.code is not None else ""
                lignes.append(f"{mot.capitalize()} — `{constat.commande}`{code}")
            else:
                lignes.append(f"{mot.capitalize()} — à la lecture du livrable")
            if constat.preuve:
                lignes += ["", "```text", constat.preuve, "```"]
        return "\n".join(lignes).rstrip() + "\n"

    def to_dict(self) -> dict[str, Any]:
        """La forme JSON de la recette — portée par l'étape `recette` du journal."""
        return {
            "statut": self.statut,
            "resume": self.resume(),
            "empechement": self.empechement,
            "constats": [c.to_dict() for c in self.constats],
            "fichier": self.fichier,
            "ecriture": self.ecriture,
        }

    @classmethod
    def depuis(cls, data: Mapping[str, Any]) -> RecetteDuRun:
        """Relit une recette depuis sa forme JSON — tolérante, comme toute relecture."""
        brut = data.get("constats")
        return cls(
            constats=tuple(
                Constat.depuis(c) for c in (brut if isinstance(brut, list) else ())
                if isinstance(c, Mapping)
            ),
            empechement=str(data.get("empechement") or ""),
            fichier=str(data.get("fichier") or ""),
            ecriture=str(data.get("ecriture") or ""),
        )


def _ligne(rang: int, constat: Constat) -> str:
    """Un critère en une ligne : son numéro, son verdict, sa commande, sa preuve."""
    tete = f"{marque(rang)} [{_MOTS.get(constat.etat, 'non vérifiable')}] « {constat.critere} »"
    if constat.commande:
        code = f" → code {constat.code}" if constat.code is not None else ""
        tete += f" — `{constat.commande}`{code}"
    return f"{tete} : {constat.preuve}" if constat.preuve else tete


def recette_non_jouee(criteres: Sequence[str], motif: str) -> RecetteDuRun:
    """La recette qui n'a pas pu se jouer : chaque critère non vérifiable, avec ce motif."""
    return RecetteDuRun(
        constats=tuple(
            Constat(critere=critere, etat=CONSTAT_NON_JOUE, preuve=motif) for critere in criteres
        ),
        empechement=motif,
    )


async def recetter(
    verificateur: VerificateurTaches,
    criteres: Sequence[str],
    livraison: Livraison,
    *,
    modele: str,
    brief: Brief | None = None,
    relance: PolitiqueRelance | None = None,
) -> RecetteDuRun:
    """Confronte `livraison` à chacun des `criteres` — un constat par critère, dans leur ordre.

    Un seul appel au mécanisme de la vérification d'une tâche
    (`VerificateurTaches.verifier`), sur la tâche que `tache_de_recette` compose. Un
    vérificateur **en panne** se relance seul, selon `relance` — la politique des
    aléas de l'exécuteur, comme pour la vérification d'une tâche (#1388) ; resté en
    panne, ou arrêté par le plafond de dépense, il rend une recette non jouée qui le
    dit. Ne lève pas.
    """
    if not criteres:
        return RecetteDuRun(constats=())
    tache = tache_de_recette(criteres)
    essais = relance.max_tentatives if relance is not None else 1
    essai = 1
    while True:
        try:
            _, verdict = await verificateur.verifier(
                tache, livraison, modele=modele, brief=brief
            )
            break
        except PlafondDepenseDepasse as exc:
            return recette_non_jouee(criteres, f"le plafond de dépense du run est atteint ({exc})")
        except VerificateurEnPanne as panne:
            if relance is None or essai >= essais:
                return recette_non_jouee(
                    criteres,
                    f"le vérificateur n'a pas pu juger après {essai} essai(s) : {panne}",
                )
            await asyncio.sleep(relance.attente_s(essai))
            essai += 1
    if verdict.empechement:
        return recette_non_jouee(criteres, verdict.empechement)
    return RecetteDuRun(constats=par_critere(criteres, verdict.constats))


def par_critere(criteres: Sequence[str], constats: Sequence[Constat]) -> tuple[Constat, ...]:
    """Un constat par critère, dans l'ordre des critères — et le texte du brief pour chacun.

    Le constat d'un critère se reconnaît à son **numéro** (« C3 — … »), que la tâche
    impose ; à défaut, à son texte recopié tel quel. Le premier constat d'un critère
    l'emporte ; un constat qui ne se rattache à aucun n'est pas un critère du brief, et
    un critère qui n'a reçu aucun constat est **non vérifiable** — jamais tenu.
    """
    textes = {_normalise(critere): rang for rang, critere in enumerate(criteres)}
    retenus: dict[int, Constat] = {}
    for constat in constats:
        rang = _rang(constat.critere, len(criteres), textes)
        if rang is None or rang in retenus:
            continue
        retenus[rang] = replace(constat, critere=criteres[rang])
    return tuple(
        retenus.get(rang)
        or Constat(critere=critere, etat=CONSTAT_NON_JOUE, preuve=_SANS_CONTROLE)
        for rang, critere in enumerate(criteres)
    )


def _rang(texte: str, nombre: int, textes: Mapping[str, int]) -> int | None:
    """Le rang du critère que `texte` désigne — par son numéro, sinon par son texte exact."""
    numero = _NUMERO.match(texte)
    if numero is not None:
        rang = int(numero.group(1)) - 1
        if 0 <= rang < nombre:
            return rang
    return textes.get(_normalise(texte))


def _normalise(texte: str) -> str:
    """Le texte d'un critère, espaces réduits et casse pliée — pour comparer deux recopies."""
    return " ".join(texte.split()).casefold()


def chemin_de_la_recette(run_id: str) -> str:
    """Où la recette du run `run_id` s'écrit dans le projet, relatif à sa racine."""
    return f"{DOSSIER_RECETTE}/{run_id}.md"


def ecrire_dans_le_projet(
    projet: Projet, run_id: str, texte: str, *, accorde: bool
) -> tuple[str, str]:
    """Écrit la recette dans le projet, au régime du projet — rend `(fichier, raison)`.

    `fichier` est le chemin absolu écrit, `raison` pourquoi rien ne l'a été : un seul
    des deux est non vide. `accorde` dit si le run a reçu l'accord d'écrire dans ce
    projet (#706) — sans lui, un projet versionné ne reçoit rien. **Bloquant** (disque,
    sous-processus Git) : l'appelant le joue hors de sa boucle. Ne lève pas.
    """
    relatif = chemin_de_la_recette(run_id)
    try:
        racine = valider_racine(projet.racine)
    except RacineRefusee as refus:
        return "", f"la racine du projet n'est plus admissible : {refus}"
    refus_frontiere = FrontiereEcriture.pour(racine, projet.perimetre).refus_chemin(
        relatif, ecriture=True
    )
    if refus_frontiere:
        return "", refus_frontiere
    if projet.versionne:
        if not accorde:
            return "", (
                "ce run n'a reçu aucun accord d'écriture dans ce projet versionné — la "
                "recette n'y est pas commitée, elle se lit au journal du run et dans son récit"
            )
        try:
            cible = deposer_sur_la_base(
                projet, relatif, texte, message=f"Maestro : recette du run {run_id}"
            )
        except (ApplicationRefusee, RacineRefusee) as exc:
            return "", f"recette non commitée dans le projet : {exc}"
        return str(cible), ""
    cible = racine / Path(relatif)
    try:
        cible.parent.mkdir(parents=True, exist_ok=True)
        cible.write_text(texte, encoding="utf-8")
    except OSError as exc:
        return "", f"écriture impossible dans le projet : {exc}"
    return str(cible), ""


def recette_en_texte(donnees: Mapping[str, Any]) -> str:
    """La recette telle que le récit de fin la lit — depuis sa forme JSON (`to_dict`).

    Le verdict, puis chaque critère avec le sien et une preuve bornée, puis où elle est
    écrite. Rendue ici, pour que la recette n'ait qu'une seule orthographe.
    """
    recette = RecetteDuRun.depuis(donnees)
    lignes = [f"Verdict : {recette.resume()}."]
    for rang, constat in enumerate(recette.constats):
        preuve = constat.preuve.strip()
        if len(preuve) > _PREUVE_RECIT_MAX:
            preuve = "…" + preuve[-(_PREUVE_RECIT_MAX - 1) :]
        commande = f" — `{constat.commande}`" if constat.commande else ""
        tete = (
            f"- {marque(rang)} [{_MOTS.get(constat.etat, 'non vérifiable')}] "
            f"« {constat.critere} »{commande}"
        )
        lignes.append(f"{tete} : {preuve}" if preuve else tete)
    if recette.fichier:
        lignes.append(f"Écrite dans le projet : {recette.fichier}")
    elif recette.ecriture:
        lignes.append(f"Pas écrite dans le projet : {recette.ecriture}")
    return "\n".join(lignes)

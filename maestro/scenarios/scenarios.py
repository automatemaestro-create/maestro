"""Les sept scénarios de référence, et ce qui les rend verts (#1148, docs/40 §5).

| | Scénario | Ce qui le rend vert |
|---|---|---|
| S1 | Vider un dossier | Le dossier est vide hors périmètre exclu |
| S2 | Créer une petite application | Elle s'exécute, sans commande soumise à la personne |
| | (et depuis #1291) | Une tâche terminée finit à N/N, cochée par le verbe de checklist |
| S3 | Reprendre un projet sans équipe | L'équipe est proposée avant de dépenser, puis ça part |
| S4 | « Pourquoi le run a échoué ? » | La réponse nomme la cause de l'API, jugée par un modèle |
| S5 | « Comment j'essaie le livrable ? » | La fin se raconte, lie un fichier réel, dit quoi taper |
| | (et depuis #1265) | La réponse s'écrit en direct ; ce qu'il lit se voit dans le fil |
| S6 | Le plan appelle un métier absent | Le rôle se propose dans le fil ; accepté, il travaille |
| S7 | Un projet naît dans la conversation | Proposé, corrigé en mots, déclaré sur accord |

## Trois règles que ces scénarios suivent

**La porte d'entrée est le fil, toujours.** Une demande passe par
`POST /api/chat/orchestrateur/messages`, l'accord par le geste de cadrage. C'est la
seule porte qu'un écran offre depuis #666, donc la seule dont l'état vaut quelque
chose : un banc qui appellerait `POST /api/executions` vérifierait un chemin que
personne n'emprunte.

**Ce que le scénario mesure n'est pas ce qu'il prépare.** S1, S2, S4, S5 et S6
dotent leur projet d'une équipe **avant** de demander quoi que ce soit, par la route
d'équipe du projet (#1039/#1040). C'est du montage, et le faire passer par le fil
ferait de chacun une copie de S3 — quatre scénarios qui échouent ensemble au
premier défaut de recrutement, et plus aucun qui parle de vider un dossier. S3,
lui, **part** d'un projet sans équipe : c'est son sujet.

**L'oracle regarde le monde, pas la prose.** Le disque pour S1, l'application
lancée pour S2, l'équipe écrite et le run soldé pour S3. Les deux oracles qui
portent sur une phrase — S4 et S5 — passent par un modèle
(`maestro.scenarios.juge`, #746) : un lexique se tromperait dans les deux sens.
Et même là, ce qui peut se constater se constate : S5 vérifie **sur le disque**
que le fichier mis en lien par le récit existe, et **sur le transport** que la
réponse arrive en direct (#1265), avant de demander à qui que ce soit ce qu'il
pense du texte.

## Ce que ces scénarios coûtent, et pourquoi S2, S4, S5 et S6 se rejouent

Un passage coûte du vrai modèle (le run du retex du 2026-09-11 a coûté ~10 $),
d'où le banc hors CI. S2, S4, S5 et S6 ne sont pas déterministes — écrire du code
qui s'exécute, reconnaître une cause dans une phrase, dire comment essayer un
livrable, nommer dans le plan le métier qui manque — donc un rouge se rejoue
**une** fois avant d'être cru, et le rapport dit s'il l'a été (`Scenario.rejouable`,
appliqué par `maestro.scenarios.banc`).
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from maestro.controltower.events import EVENEMENT_TACHE_STATUT
from maestro.controltower.state import (
    EXECUTION_ECHEC,
    EXECUTION_TERMINEE,
    STATUTS_EXECUTION_TERMINAUX,
)
from maestro.detail_tache import ETAPE_FAITE
from maestro.engine.executor import STATUT_ROLE_MANQUANT, STATUT_TERMINEE
from maestro.lecture import OUTIL_SHELL
from maestro.scenarios.api import (
    DELAI_RUN_S,
    ClientAPI,
    Echange,
    ErreurAPI,
    attendre_le_run,
    equipe_validee,
)
from maestro.scenarios.juge import Juge
from maestro.scenarios.modele import Issue, Journal, empeche, rouge, vert
from maestro.scenarios.projets import (
    Atelier,
    manquants,
    restes,
    semer_a_vider,
    semer_projet_existant,
)

#: Le nom du fichier que S2 demande. L'oracle de S2 est « elle s'exécute », et une
#: application dont personne ne sait comment la lancer n'est pas exécutable :
#: nommer le point d'entrée dans la demande est ce qui rend l'oracle vérifiable
#: sans deviner. Ce n'est pas un bridage — c'est ce que dit n'importe quel
#: utilisateur qui veut pouvoir lancer ce qu'il a demandé.
POINT_D_ENTREE = "app.py"

#: Ce qu'on laisse à une application de S2 pour démarrer, s'afficher et sortir.
DELAI_APPLICATION_S = 60.0

#: Ce qu'on laisse au récit de fin pour paraître dans le fil, et l'intervalle
#: entre deux lectures (#1224). Le récit part **après** que le run est soldé — sa
#: rédaction est un appel modèle —, donc le lire une seule fois rendrait S5 rouge
#: sur un produit qui marche. Deux minutes : c'est la marge d'un appel modèle
#: unique sur un contexte borné, pas celle d'un run.
ATTENTE_RECIT_S = 120.0
INTERVALLE_RECIT_S = 2.0

#: La note que la personne dépose dans le projet de S5 une fois le run raconté,
#: et la question qu'elle pose dessus (#1265). C'est la question **dont la
#: réponse ne peut venir que du disque** : la note est écrite après tout le reste,
#: donc son contenu n'est ni dans la conversation, ni dans le récit, ni dans les
#: faits du run — l'orchestrateur ne peut en parler qu'en la lisant.
#:
#: Pourquoi pas la question « comment j'essaie ? » qui précède : le passage du
#: 2026-09-24 y a répondu **sans rien lire** (fil `20260924t043254-d72e4e`,
#: `etapes: []`), le récit de fin lui donnant déjà la commande. C'était une bonne
#: réponse, et un oracle de lecture posé là l'aurait rendue rouge. Seule la
#: demande de S3 avait lu ce jour-là — par hasard du jugement, pas par construction.
NOTE_S5 = "notes-de-la-personne.md"
CONTENU_NOTE_S5 = (
    "# Mes notes\n"
    "\n"
    "- Ajouter une option `--nom` pour saluer quelqu'un par son prénom.\n"
    "- Écrire le message en couleur quand le terminal le permet.\n"
)
QUESTION_NOTE_S5 = (
    f"J'ai déposé mes notes dans `{NOTE_S5}`, à la racine du projet. "
    "Qu'est-ce que j'y ai noté ?"
)

#: Le plafond qui provoque l'échec de S4. En **tokens** et non en dollars : les
#: tokens sont toujours rapportés, quel que soit le fournisseur (#113), là où un
#: plafond en dollars n'a aucune prise sur un endpoint qui ne tarife pas. Le run
#: s'arrête donc à sa première mesure — l'échec est provoqué pour presque rien,
#: ce qui compte sur un banc qui paie du vrai modèle.
PLAFOND_TOKENS_S4 = 1

#: La demande de S6 : l'animation du logo du bouclage du 2026-09-24 (#1260), sur un
#: projet qui n'a qu'un développeur — et **dite comme un travail de design**.
#:
#: La phrase du bouclage (« un M stylisé qui s'anime en boucle, soigné
#: visuellement ») laisse au plan le soin de juger si un développeur suffit, et il
#: l'a jugé une fois sur deux le 2026-09-24 (trois essais sur six). Ce jugement est
#: l'autre moitié de #1227 — planifier pour le besoin —, et il se mesure au bouclage.
#: S6, lui, mesure la chaîne **après** que le plan a nommé le besoin : proposée dans
#: le fil, acceptée, au travail. La demande dit donc le besoin, comme le dit quelqu'un
#: qui le veut. Un plan qui ne le nomme toujours pas reste un rouge, et son motif le
#: dit.
DEMANDE_S6 = (
    "Crée dans ce projet une petite animation du logo Maestro, dans un fichier "
    "logo-anime.svg : un M stylisé qui s'anime en boucle. C'est d'abord un travail "
    "de design — le tracé du M, sa palette, le rythme de l'animation — et le rendu "
    "doit être soigné visuellement."
)

#: Le seul rôle que S6 garde de ce que l'analyse propose : celui du développeur.
#: C'est le champ `gabarit` d'un rôle proposé — l'agent du code dont il dérive
#: (`maestro.equipe.gabarits`, `Gabarit.gabarit`) —, et non son slug `dev` : le
#: premier passage réel de S6 l'a appris, son montage cherchait le slug et ne
#: trouvait rien. Une équipe à qui manque l'interface.
GABARIT_SEUL_S6 = "developpeur"

#: Ce qu'on laisse à la proposition de renfort pour paraître dans le fil, et
#: l'intervalle entre deux lectures (#1260). Elle vient **après** le cadrage et la
#: décomposition — deux appels modèle sur un contexte borné — et avant toute
#: tâche : c'est la marge de deux appels, pas celle d'un run.
ATTENTE_RENFORT_S = 300.0
INTERVALLE_RENFORT_S = 3.0


@dataclass
class Contexte:
    """Tout ce qu'un scénario a besoin de connaître du monde — et rien d'autre.

    Chaque dépendance est **injectable**, et c'est ce qui permet aux tests de
    jouer les quatre déroulés contre une fausse API et un faux fournisseur, sans
    réseau, sans horloge réelle et sans lancer de sous-process.

    `projet_id` et `racine` sont **écrits par le scénario** et relus par le banc :
    ils nomment le projet jetable qu'il a déclaré, et le rapport en a besoin même
    quand le scénario s'arrête avant son oracle — c'est là que les pièces d'un
    rouge sont restées.

    `arbitrages` (#1226) est ce que le banc a **tranché à la place de la
    personne** : un outil par demande approuvée. Il se remplit pendant le suivi du
    run et se relit deux fois — par l'oracle de S2, dont c'est une moitié, et par
    le rapport, qui le compte.
    """

    client: ClientAPI
    atelier: Atelier
    juge: Juge
    journal: Journal = field(default_factory=Journal)
    delai_run_s: float = DELAI_RUN_S
    horloge: Callable[[], float] = time.monotonic
    dormir: Callable[[float], None] = time.sleep
    lancer_application: Callable[[Path, str], tuple[int, str]] | None = None
    projet_id: str = ""
    racine: Path | None = None
    arbitrages: list[str] = field(default_factory=list)

    def note(self, libelle: str, detail: str = "") -> None:
        """Consigne une étape du déroulé."""
        self.journal.note(libelle, detail)

    @property
    def validations_de_commande(self) -> tuple[str, ...]:
        """Les demandes d'arbitrage portant sur l'**outil d'exécution**.

        C'est ce que #1226 compte : une équipe validée exécute son travail dans
        son projet sans réveiller personne, donc ce tuple doit rester vide sur un
        projet neuf. Les autres arbitrages — une tâche, un accord d'écriture — ne
        sont pas des validations de commande et n'y entrent pas.
        """
        return tuple(outil for outil in self.arbitrages if outil == OUTIL_SHELL)

    def executer(self, racine: Path, point_d_entree: str) -> tuple[int, str]:
        """Lance l'application produite — le vrai `subprocess`, sauf injection."""
        lanceur = self.lancer_application or lancer_application
        return lanceur(racine, point_d_entree)


def lancer_application(racine: Path, point_d_entree: str) -> tuple[int, str]:
    """Exécute `point_d_entree` dans `racine` et rend `(code, sortie)`.

    Avec `sys.executable` : c'est le Python du venv qui joue le banc, donc celui
    dont on sait qu'il existe. Un délai dépassé rend un code non nul et le dit —
    une application qui ne rend jamais la main ne s'exécute pas au sens de
    l'oracle, et bloquer le banc dessus serait pire que la rendre rouge.
    """
    try:
        fini = subprocess.run(  # noqa: S603 - l'interpréteur du banc, sur un projet jetable
            [sys.executable, point_d_entree],
            cwd=str(racine),
            capture_output=True,
            text=True,
            timeout=DELAI_APPLICATION_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return 1, f"aucune sortie au bout de {DELAI_APPLICATION_S:.0f} s"
    except OSError as echec:  # interpréteur introuvable, dossier disparu
        return 1, f"lancement impossible : {echec}"
    sortie = (fini.stdout or "") + (fini.stderr or "")
    return fini.returncode, sortie.strip()


# --- Les gestes que les scénarios partagent --------------------------------


def _declarer(ctx: Contexte, nom: str, racine: Path, *, origine: str) -> str:
    """Déclare le projet jetable du scénario et le retient dans le contexte."""
    projet_id = ctx.client.declarer_projet(nom, str(racine), origine=origine)
    ctx.projet_id, ctx.racine = projet_id, racine
    ctx.note("projet déclaré", f"{projet_id} — {racine}")
    return projet_id


def _doter_d_une_equipe(ctx: Contexte, projet_id: str) -> int:
    """Crée l'équipe que l'analyse du projet recommande — du **montage**, pas l'oracle.

    Par la route d'équipe du projet (#1039 puis #1040), et non par le fil : ce que
    S1, S2 et S4 mesurent commence après. L'équipe proposée est reprise **telle
    quelle** (`equipe_validee`), parce qu'un utilisateur qui découvre le produit
    accepte ce qu'on lui montre.
    """
    proposition = ctx.client.proposition_equipe(projet_id)
    validee = equipe_validee(proposition)
    ctx.client.creer_equipe(projet_id, validee)
    noms = ", ".join(str(role["nom"]) for role in validee["roles"])
    ctx.note("équipe créée", f"{len(validee['roles'])} rôle(s) : {noms}")
    return len(validee["roles"])


def _demander(ctx: Contexte, conversation: str, projet_id: str, texte: str) -> dict[str, Any]:
    """Envoie la demande de travail au fil et rend la réponse de l'orchestrateur."""
    reponse = ctx.client.envoyer(texte, projet_id=projet_id, conversation=conversation)
    ctx.note("demande envoyée", texte)
    ctx.note("réponse du fil", _extrait(reponse))
    return reponse


def _demander_en_direct(
    ctx: Contexte, conversation: str, projet_id: str, texte: str
) -> Echange:
    """Pose une question au fil **par le flux de l'écran**, et note comment la réponse est venue.

    Le pendant de `_demander` pour les questions dont l'oracle regarde l'arrivée
    (#1265) : la réponse est la même, le banc en garde en plus chaque trame
    datée. La mesure est notée au déroulé dans tous les cas — un vert qui dit
    « seize incréments en trois secondes » se relit, un vert sans chiffre non.
    """
    echange = ctx.client.envoyer_en_direct(texte, projet_id=projet_id, conversation=conversation)
    ctx.note("demande envoyée", texte)
    ctx.note("réponse du fil", _extrait(echange.reponse))
    ctx.note("réponse reçue en direct", _mesure_du_direct(echange))
    return echange


def _accorder(
    ctx: Contexte,
    conversation: str,
    projet_id: str,
    *,
    bornes: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Donne l'accord au cadrage proposé et rend la réponse qui ouvre le run."""
    reponse = ctx.client.trancher_cadrage(
        conversation=conversation, projet_id=projet_id, bornes=bornes
    )
    ctx.note("accord donné", _extrait(reponse))
    return reponse


def _suivre(ctx: Contexte, run_id: str, projet_id: str) -> dict[str, Any]:
    """Suit le run jusqu'à son issue et note ce qu'elle a été."""
    detail = attendre_le_run(
        ctx.client,
        run_id,
        projet_id=projet_id,
        delai_s=ctx.delai_run_s,
        note=ctx.note,
        horloge=ctx.horloge,
        dormir=ctx.dormir,
        arbitrages=ctx.arbitrages,
    )
    ctx.note(
        "run soldé",
        f"statut « {detail.get('statut')} », cause « {detail.get('cause') or '—'} », "
        f"{detail.get('nb_taches')} tâche(s), {_montant(detail)}",
    )
    return detail


def _extrait(message: Mapping[str, Any], longueur: int = 300) -> str:
    """Le contenu d'un message du fil, borné — le rapport se lit à l'œil nu."""
    contenu = str(message.get("contenu") or "").strip().replace("\n", " ")
    return contenu if len(contenu) <= longueur else f"{contenu[: longueur - 1]}…"


def _montant(detail: Mapping[str, Any]) -> str:
    """Le coût du run en mots, ou le fait qu'aucun n'a été rapporté."""
    cout = detail.get("cout_usd")
    return "coût non rapporté" if cout is None else f"{float(cout):.4f} $"


def _cout(detail: Mapping[str, Any] | None) -> float | None:
    """Le coût du run, tel que l'API l'agrège — `None` quand rien n'est connu."""
    if not detail:
        return None
    cout = detail.get("cout_usd")
    return None if cout is None else float(cout)


def _releve_de_l_echec(detail: Mapping[str, Any]) -> str:
    """Ce que l'API dit de l'échec : sa cause, et les détails qu'elle a consignés.

    C'est la matière que le juge de S4 confronte à la réponse du fil — **relue
    dans l'API**, jamais reconstruite : si le banc racontait l'échec à sa façon, il
    jugerait la réponse contre son propre récit.
    """
    morceaux: list[str] = []
    cause = str(detail.get("cause") or "")
    if cause:
        morceaux.append(f"cause : {cause}")
    for evenement in detail.get("evenements") or []:
        if str(evenement.get("statut") or "") == EXECUTION_ECHEC:
            texte = str(evenement.get("detail") or "").strip()
            if texte and texte not in morceaux:
                morceaux.append(texte)
    return " | ".join(morceaux)


def _run_de(reponse: Mapping[str, Any]) -> str:
    """Le run que la réponse du fil a ouvert — chaîne vide si elle n'en a ouvert aucun."""
    return str(reponse.get("run_id") or "")


# --- S1 — vider un dossier -------------------------------------------------


def s1_vider_un_dossier(ctx: Contexte) -> Issue:
    """Le dossier finit vide, et le périmètre exclu est épargné.

    Deux moitiés dans l'oracle, et la seconde compte autant que la première : un
    run qui efface **tout**, `.env` compris, ne vide pas un dossier — il perd des
    secrets. Le périmètre se lit avec la règle du produit
    (`maestro.projets.perimetre`), jamais avec une liste recopiée ici.
    """
    racine = ctx.atelier.dossier("s1-vider")
    temoins = semer_a_vider(racine)
    avant = restes(racine)
    ctx.note(
        "projet semé",
        f"{len(avant)} entrée(s) à effacer ; hors périmètre : {', '.join(temoins) or '—'}",
    )
    projet_id = _declarer(ctx, "banc-s1-vider", racine, origine="existant")
    _doter_d_une_equipe(ctx, projet_id)

    conversation = ctx.client.ouvrir_conversation()
    reponse = _demander(
        ctx,
        conversation,
        projet_id,
        "Vide le dossier de ce projet : supprime tout son contenu.",
    )
    if not reponse.get("proposition"):
        return rouge(
            "le fil n'a proposé aucun run pour cette demande — rien n'a été lancé",
            cout_usd=None,
        )
    accord = _accorder(ctx, conversation, projet_id)
    run_id = _run_de(accord)
    if not run_id:
        return rouge("l'accord n'a ouvert aucun run", cout_usd=None)
    detail = _suivre(ctx, run_id, projet_id)
    cout = _cout(detail)

    if str(detail.get("statut")) != EXECUTION_TERMINEE:
        return rouge(
            f"le run s'est soldé « {detail.get('statut')} » "
            f"(cause « {detail.get('cause') or '—'} ») au lieu d'aboutir",
            run_id=run_id,
            cout_usd=cout,
        )
    perdus = manquants(racine, temoins)
    if perdus:
        return rouge(
            f"le périmètre exclu a été touché : {', '.join(perdus)}",
            run_id=run_id,
            cout_usd=cout,
        )
    restants = restes(racine)
    if restants:
        return rouge(
            f"{len(restants)} entrée(s) restent dans le dossier : "
            f"{', '.join(restants[:10])}",
            run_id=run_id,
            cout_usd=cout,
        )
    return vert(
        f"le dossier est vide ; le périmètre exclu est intact ({', '.join(temoins) or '—'})",
        run_id=run_id,
        cout_usd=cout,
    )


# --- S2 — créer une petite application -------------------------------------


def s2_creer_une_application(ctx: Contexte) -> Issue:
    """Une petite application naît dans un dossier neuf, elle s'exécute, **et personne
    n'a eu à trancher une commande**.

    L'oracle **lance** ce qui a été produit : lire le fichier dirait seulement
    qu'il existe, et un fichier qui ne tourne pas n'est pas une application. Le
    code de sortie fait foi, la sortie est recopiée au rapport.

    La seconde moitié vient de #1226, et c'est ce scénario-là qui la porte parce
    que c'est le sien : écrire du code et le lancer. Le projet est **neuf et
    vide**, donc aucun acte de l'agent ne peut légitimement remonter — il n'y a
    rien à détruire qu'il n'ait produit, et rien à chercher hors du dossier. Une
    seule validation de commande signifie donc que l'agent attend une personne
    pour lancer son propre travail, ce que le run mesuré le 2026-09-22 faisait
    quatorze fois. Elle est jugée **avant** l'exécution du livrable : un vert
    rendu sur une application qui tourne masquerait exactement la régression qu'on
    vient de corriger.

    La troisième vient de #1291 : **une tâche terminée finit à N/N**, cochée par
    le verbe de checklist de Maestro. Depuis le 2026-09-22, toutes les tâches se
    soldaient à « 0/N · relevé incomplet » parce que la checklist se lisait dans
    un outil du CLI que le CLI avait remplacé, et aucun scénario ne l'a vu : les
    oracles regardaient le livrable, jamais la carte. S2 est le scénario d'un
    agent outillé qui écrit et lance du code, donc celui où la checklist doit se
    tenir ; elle est jugée **après** le livrable, qui reste le sujet du scénario.
    """
    racine = ctx.atelier.dossier("s2-application")
    projet_id = _declarer(ctx, "banc-s2-application", racine, origine="nouveau")
    _doter_d_une_equipe(ctx, projet_id)

    conversation = ctx.client.ouvrir_conversation()
    reponse = _demander(
        ctx,
        conversation,
        projet_id,
        "Crée dans ce projet une petite application Python exécutable : un fichier "
        f"`{POINT_D_ENTREE}` à la racine qui, lancé par `python {POINT_D_ENTREE}`, "
        "affiche une ligne de texte puis se termine sans erreur.",
    )
    if not reponse.get("proposition"):
        return rouge("le fil n'a proposé aucun run pour cette demande", cout_usd=None)
    accord = _accorder(ctx, conversation, projet_id)
    run_id = _run_de(accord)
    if not run_id:
        return rouge("l'accord n'a ouvert aucun run", cout_usd=None)
    detail = _suivre(ctx, run_id, projet_id)
    cout = _cout(detail)

    if str(detail.get("statut")) != EXECUTION_TERMINEE:
        return rouge(
            f"le run s'est soldé « {detail.get('statut')} » "
            f"(cause « {detail.get('cause') or '—'} ») au lieu d'aboutir",
            run_id=run_id,
            cout_usd=cout,
        )
    commandes = ctx.validations_de_commande
    if commandes:
        return rouge(
            f"{len(commandes)} validation(s) de commande demandée(s) à la personne "
            "sur un projet neuf : l'équipe validée doit exécuter son travail dans "
            "son projet sans attendre personne",
            run_id=run_id,
            cout_usd=cout,
        )
    if not (racine / POINT_D_ENTREE).is_file():
        presents = ", ".join(restes(racine)[:10]) or "rien"
        return rouge(
            f"aucun `{POINT_D_ENTREE}` à la racine du projet — présents : {presents}",
            run_id=run_id,
            cout_usd=cout,
        )
    code, sortie = ctx.executer(racine, POINT_D_ENTREE)
    ctx.note("application lancée", f"code {code} — {sortie[:300] or 'aucune sortie'}")
    if code != 0:
        return rouge(
            f"`python {POINT_D_ENTREE}` sort en {code} : {sortie[:300] or 'aucune sortie'}",
            run_id=run_id,
            cout_usd=cout,
        )
    tenue, releve = _checklists_du_run(ctx, run_id, projet_id)
    if not tenue:
        return rouge(
            "aucune tâche terminée ne finit à N/N : la checklist n'a pas été tenue "
            f"jusqu'au bout ({releve})",
            run_id=run_id,
            cout_usd=cout,
        )
    return vert(
        f"`python {POINT_D_ENTREE}` s'exécute et sort en 0 "
        f"({sortie[:120] or 'aucune sortie'}) ; aucune validation de commande "
        f"demandée à la personne ; checklist tenue ({releve})",
        run_id=run_id,
        cout_usd=cout,
    )


def _checklists_du_run(ctx: Contexte, run_id: str, projet_id: str) -> tuple[bool, str]:
    """Une tâche terminée du run finit-elle à N/N ? — et le relevé de chaque carte (#1291).

    Lu sur ce que la carte montre (`GET /api/taches?run=`), jamais dans la trace :
    c'est à l'écran que « 0/N · relevé incomplet » se lisait. Une tâche compte si
    elle est **terminée** et que sa checklist, non vide, est entièrement faite ;
    une seule suffit, et les autres se lisent au relevé — un écart réel sur une
    tâche voisine n'est pas le défaut que cet oracle garde.
    """
    cartes = ctx.client.taches(run_id, projet_id=projet_id)
    tenue = False
    releves: list[str] = []
    for carte in cartes:
        etapes = [e for e in carte.get("etapes") or [] if isinstance(e, Mapping)]
        faites = sum(1 for etape in etapes if str(etape.get("etat") or "") == ETAPE_FAITE)
        statut = str(carte.get("statut") or "")
        releves.append(f"« {carte.get('titre') or carte.get('id')} » {faites}/{len(etapes)}")
        if statut == STATUT_TERMINEE and etapes and faites == len(etapes):
            tenue = True
    releve = " ; ".join(releves) or "aucune tâche servie"
    ctx.note("checklists du run", releve)
    return tenue, releve


# --- S3 — reprendre un projet existant sans équipe -------------------------


def s3_reprendre_sans_equipe(ctx: Contexte) -> Issue:
    """Le fil propose l'équipe **avant** de dépenser, puis le run demandé aboutit.

    Trois choses à constater dans cet ordre, et l'ordre est l'oracle (#1146) :
    la demande sur un projet sans agent reçoit une équipe et **aucun run** — c'est
    la dépense qu'on évite —, l'équipe validée est bien créée, et le fil repropose
    alors la demande d'origine pour qu'un accord la lance.
    """
    racine = ctx.atelier.dossier("s3-sans-equipe")
    semer_projet_existant(racine)
    projet_id = _declarer(ctx, "banc-s3-sans-equipe", racine, origine="existant")
    ctx.note("aucune équipe créée", "le projet est repris tel quel, sans agent")

    conversation = ctx.client.ouvrir_conversation()
    demande = (
        "Range les sources de ce projet : ajoute un fichier NOTES.md "
        "qui décrit ce qu'il contient."
    )
    reponse = _demander(ctx, conversation, projet_id, demande)

    recrutement = reponse.get("recrutement")
    if not recrutement:
        return rouge(
            "le fil n'a pas proposé d'équipe sur un projet qui n'en a pas "
            f"(proposition de run : {str(reponse.get('proposition') or '—')!r})",
            run_id=_run_de(reponse),
            cout_usd=None,
        )
    if _run_de(reponse):
        return rouge(
            f"un run a été ouvert avant tout recrutement : {_run_de(reponse)}",
            run_id=_run_de(reponse),
            cout_usd=None,
        )
    ctx.note(
        "équipe proposée dans le fil",
        f"travail en attente : {str(recrutement.get('objectif') or '')[:200]}",
    )

    proposition = ctx.client.proposition_equipe(projet_id)
    validee = equipe_validee(proposition)
    if not validee["roles"]:
        return rouge("l'analyse du projet n'a proposé aucun rôle", cout_usd=None)
    suite = ctx.client.recruter(conversation=conversation, validee=validee)
    ctx.note("équipe validée dans le fil", _extrait(suite))

    if not suite.get("proposition"):
        return rouge(
            "l'équipe validée, le fil n'a pas reproposé la demande d'origine",
            cout_usd=None,
        )
    accord = _accorder(ctx, conversation, projet_id)
    run_id = _run_de(accord)
    if not run_id:
        return rouge("l'accord n'a ouvert aucun run", cout_usd=None)
    detail = _suivre(ctx, run_id, projet_id)
    cout = _cout(detail)
    if str(detail.get("statut")) != EXECUTION_TERMINEE:
        return rouge(
            f"le run repris s'est soldé « {detail.get('statut')} » "
            f"(cause « {detail.get('cause') or '—'} ») au lieu d'aboutir",
            run_id=run_id,
            cout_usd=cout,
        )
    noms = ", ".join(str(role["nom"]) for role in validee["roles"])
    return vert(
        f"équipe proposée avant tout run, validée ({noms}), puis le run demandé aboutit",
        run_id=run_id,
        cout_usd=cout,
    )


# --- S4 — « pourquoi le run a échoué ? » -----------------------------------


def s4_pourquoi_l_echec(ctx: Contexte) -> Issue:
    """Après un échec **provoqué**, le fil nomme la cause que l'API a relevée.

    L'échec est provoqué par une borne et non par un sabotage : l'accord part avec
    un plafond de tokens de 1, si bien que le run s'arrête à sa première mesure.
    C'est un échec que le produit **connaît** — il en pose la cause (#479) —, donc
    exactement celui dont on veut savoir s'il se raconte.

    Le jugement est rendu par un modèle (`ctx.juge`), et une abstention du juge est
    un **empêchement** et non un rouge du produit : on ne met pas une panne de
    quota sur le compte de ce qu'on mesure.
    """
    racine = ctx.atelier.dossier("s4-pourquoi")
    semer_projet_existant(racine)
    projet_id = _declarer(ctx, "banc-s4-pourquoi", racine, origine="existant")
    _doter_d_une_equipe(ctx, projet_id)

    conversation = ctx.client.ouvrir_conversation()
    reponse = _demander(
        ctx,
        conversation,
        projet_id,
        "Ajoute à ce projet un fichier NOTES.md qui décrit en trois lignes ce qu'il contient.",
    )
    if not reponse.get("proposition"):
        return rouge("le fil n'a proposé aucun run pour cette demande", cout_usd=None)
    accord = _accorder(
        ctx, conversation, projet_id, bornes={"plafond_tokens": PLAFOND_TOKENS_S4}
    )
    run_id = _run_de(accord)
    if not run_id:
        return rouge(
            f"l'accord borné à {PLAFOND_TOKENS_S4} token(s) n'a ouvert aucun run — "
            f"réponse du fil : {_extrait(accord)}",
            cout_usd=None,
        )
    detail = _suivre(ctx, run_id, projet_id)
    cout = _cout(detail)
    statut = str(detail.get("statut") or "")

    if statut != EXECUTION_ECHEC:
        mot = "encore en vol" if statut not in STATUTS_EXECUTION_TERMINAUX else "soldé"
        return rouge(
            f"aucun échec à expliquer : le run est {mot} en « {statut} » "
            f"malgré le plafond de {PLAFOND_TOKENS_S4} token(s)",
            run_id=run_id,
            cout_usd=cout,
        )
    releve = _releve_de_l_echec(detail)
    if not releve:
        return rouge(
            "l'API ne relève aucune cause pour cet échec : il n'y a rien à nommer",
            run_id=run_id,
            cout_usd=cout,
        )
    ctx.note("échec relevé par l'API", releve[:400])

    explication = _demander(
        ctx, conversation, projet_id, "Pourquoi le run a-t-il échoué ?"
    )
    avis = ctx.juge.nomme_la_cause(
        cause=str(detail.get("cause") or ""),
        releve=releve,
        reponse=str(explication.get("contenu") or ""),
    )
    ctx.note(
        "jugement du modèle",
        f"{'nomme' if avis.nomme else 'ne nomme pas'} — {avis.pourquoi}",
    )
    if not avis.lisible:
        return empeche(
            f"le jugement n'a pas pu être rendu : {avis.pourquoi}",
            run_id=run_id,
            cout_usd=cout,
        )
    if not avis.nomme:
        return rouge(
            f"la réponse du fil ne nomme pas la cause relevée ({releve[:160]}) : "
            f"{avis.pourquoi}",
            run_id=run_id,
            cout_usd=cout,
        )
    return vert(
        f"la réponse nomme la cause relevée par l'API — {avis.pourquoi}",
        run_id=run_id,
        cout_usd=cout,
    )


# --- S5 — comment j'essaie ce qui vient d'être livré ? ---------------------


def s5_comment_essayer_le_livrable(ctx: Contexte) -> Issue:
    """À la fin du run, le fil dit **comment essayer** ce qui a été produit (#1224).

    Le constat du 2026-09-22, mot pour mot : *« on ne me dit pas comment tester,
    pourtant on a généré une documentation »*. La personne avait le lien du
    dossier — l'annonce de #928 le donne — et rien pour s'en servir.

    Trois choses à constater, dans cet ordre, et l'ordre est l'oracle :

    1. **le fil porte un récit**, écrit de lui-même à la fin du run. C'est un
       fait structurel, pas une phrase : un message de l'orchestrateur, postérieur
       à celui qui a ouvert le run, portant le même `run_id` ;
    2. **il nomme un fichier qui existe**, en lien. Vérifié **sur le disque** et
       non dans le texte : un chemin cité qui ne mène à rien serait un geste mort,
       ce qui est exactement ce que le critère interdit ;
    3. **on sait comment l'essayer**, jugé par un modèle (`ctx.juge`) sur le
       récit **et** sur la réponse à la question qu'on lui pose. Une abstention
       du juge est un **empêchement**, jamais un rouge du produit : on ne met pas
       une panne de quota sur le compte de ce qu'on mesure.

    Deux constats s'y greffent depuis #1265, sans payer de run de plus — ils
    gardent au bouclage ce que C8 demandait et qu'aucun scénario ne rejouait :

    - **la réponse s'écrit en direct** (C1). La question part par le flux que
      l'écran emprunte (`POST …/flux`), et son arrivée est constatée **avant**
      que le juge soit saisi (`_le_direct`) : plusieurs incréments, reçus dans
      plusieurs images d'écran ;
    - **ce qu'il lit se voit dans le fil** (C2). Une note déposée après le récit,
      et une question dessus : la réponse ne peut venir que du disque, donc elle
      doit porter ses lectures, en direct et dans le fil relu (`_lectures_du_fil`).

    Le run demandé est celui de S2 — une petite application exécutable — parce
    qu'il faut *quelque chose à essayer* pour que la question ait un sens, et que
    c'est le livrable dont on sait qu'un run sait le produire. Le projet est
    déclaré à part : chaque scénario a le sien, et `--scenario S5` doit jouer
    exactement ce que le passage complet joue en cinquième.
    """
    racine = ctx.atelier.dossier("s5-essayer")
    projet_id = _declarer(ctx, "banc-s5-essayer", racine, origine="nouveau")
    _doter_d_une_equipe(ctx, projet_id)

    conversation = ctx.client.ouvrir_conversation()
    reponse = _demander(
        ctx,
        conversation,
        projet_id,
        "Crée dans ce projet une petite application Python exécutable : un fichier "
        f"`{POINT_D_ENTREE}` à la racine qui, lancé par `python {POINT_D_ENTREE}`, "
        "affiche une ligne de texte puis se termine sans erreur. Ajoute un "
        "README.md qui dit comment la lancer.",
    )
    if not reponse.get("proposition"):
        return rouge("le fil n'a proposé aucun run pour cette demande", cout_usd=None)
    accord = _accorder(ctx, conversation, projet_id)
    run_id = _run_de(accord)
    if not run_id:
        return rouge("l'accord n'a ouvert aucun run", cout_usd=None)
    detail = _suivre(ctx, run_id, projet_id)
    cout = _cout(detail)

    if str(detail.get("statut")) != EXECUTION_TERMINEE:
        return rouge(
            f"le run s'est soldé « {detail.get('statut')} » "
            f"(cause « {detail.get('cause') or '—'} ») : il n'y a rien à essayer",
            run_id=run_id,
            cout_usd=cout,
        )

    recit = _recit_de_fin(ctx, conversation, run_id)
    if not recit:
        return rouge(
            f"la fin du run n'a rien écrit dans le fil en {ATTENTE_RECIT_S:.0f} s : "
            "le dernier message reste celui du lancement",
            run_id=run_id,
            cout_usd=cout,
        )
    ctx.note("récit de fin", _extrait({"contenu": recit}, 400))

    lies = _fichiers_lies(recit, racine)
    if not lies:
        presents = ", ".join(restes(racine)[:10]) or "rien"
        return rouge(
            "le récit ne met en lien aucun fichier existant du livrable — "
            f"présents sur le disque : {presents}",
            run_id=run_id,
            cout_usd=cout,
        )
    ctx.note("fichiers liés par le récit", ", ".join(lies[:5]))

    explication = _demander_en_direct(
        ctx, conversation, projet_id, "Comment j'essaie ce que tu viens de livrer ?"
    )
    hors_direct = _le_direct(explication)
    if hors_direct:
        return rouge(hors_direct, run_id=run_id, cout_usd=cout)
    avis = ctx.juge.dit_comment_essayer(
        livrable=", ".join(restes(racine)[:30]) or "aucun fichier",
        recit=recit,
        reponse=str(explication.reponse.get("contenu") or ""),
    )
    ctx.note(
        "jugement du modèle",
        f"{'dit' if avis.nomme else 'ne dit pas'} comment essayer — {avis.pourquoi}",
    )
    if not avis.lisible:
        return empeche(
            f"le jugement n'a pas pu être rendu : {avis.pourquoi}",
            run_id=run_id,
            cout_usd=cout,
        )
    if not avis.nomme:
        return rouge(
            f"le fil ne dit pas comment essayer le livrable : {avis.pourquoi}",
            run_id=run_id,
            cout_usd=cout,
        )

    lectures, sans_lecture = _lectures_du_fil(ctx, conversation, projet_id, racine)
    if sans_lecture:
        return rouge(sans_lecture, run_id=run_id, cout_usd=cout)
    return vert(
        f"la fin du run se raconte dans le fil, met {len(lies)} fichier(s) du "
        f"livrable en lien, dit comment l'essayer, répond en direct "
        f"({len(explication.increments)} incréments sur {explication.images} images) "
        f"et montre ses {lectures} lecture(s) dans le fil — {avis.pourquoi}",
        run_id=run_id,
        cout_usd=cout,
    )


def _recit_de_fin(ctx: Contexte, conversation: str, run_id: str) -> str:
    """Le message que la **fin** du run a écrit dans le fil — vide s'il n'y vient pas.

    Le récit se reconnaît à sa place et à son rattachement, jamais à ses mots :
    un message de l'orchestrateur, portant ce `run_id`, et qui n'est pas le
    premier — le premier étant la réponse qui a ouvert le run (#268). Juger sur
    le contenu reviendrait à chercher un lexique (#746) dans ce que le modèle a
    écrit, ce que ce banc existe précisément pour ne pas faire.

    ⚠ **Il faut l'attendre**, et c'est une propriété du produit, pas une
    commodité du banc : le récit s'écrit quand la fin **passe**, et sa rédaction
    est un appel modèle qui part *après* que le run est soldé. Mesuré le
    2026-09-23 sur le passage `20260923-185330` — le run était terminé, le fil
    relu dans la foulée ne portait encore que le lancement, et le récit y est
    arrivé quelques secondes plus tard. Lire une seule fois rendait donc S5 rouge
    sur un produit qui marche, ce qui est le pire des verdicts.

    L'attente est **bornée et dite** : passé `ATTENTE_RECIT_S`, on rend la chaîne
    vide et l'oracle tranche. Elle passe par l'horloge et le sommeil du contexte,
    comme le suivi d'un run — les tests jouent donc ce chemin sans attendre.
    """
    limite = ctx.horloge() + ATTENTE_RECIT_S
    while True:
        porteurs = [
            str(message.get("contenu") or "")
            for message in ctx.client.fil(conversation)
            if str(message.get("run_id") or "") == run_id
            and str(message.get("auteur") or "") != "utilisateur"
        ]
        if len(porteurs) > 1:
            return porteurs[-1]
        if ctx.horloge() >= limite:
            return ""
        ctx.dormir(INTERVALLE_RECIT_S)


def _lectures_du_fil(
    ctx: Contexte, conversation: str, projet_id: str, racine: Path
) -> tuple[int, str]:
    """Une réponse fondée sur une lecture **montre ses lectures dans le fil** (#1265, C2).

    Rend le nombre de lectures vues, et pourquoi l'oracle n'est pas satisfait
    (`""` quand il l'est). Trois constats, tous structurels — des étapes et leur
    place, jamais leur libellé (#746) :

    1. **la réponse a lu** : au moins une trame `etape` est venue par le flux.
       La question porte sur une note déposée après tout le reste
       (`NOTE_S5`) : son contenu n'est dans aucun contexte, donc une réponse
       sans lecture ne s'est pas fondée sur le projet réel ;
    2. **le fil relu garde la réponse** — ce qu'un rechargement montrerait ;
    3. **il garde ses lectures**, les mêmes et dans le même ordre : vues pendant
       qu'il répond, puis encore là au retour (#1223 les fait voyager deux fois,
       et c'est ce que l'oracle vérifie).
    """
    (racine / NOTE_S5).write_text(CONTENU_NOTE_S5, encoding="utf-8")
    ctx.note("note déposée", f"{NOTE_S5} — écrite après le récit : aucun contexte ne la porte")
    echange = _demander_en_direct(ctx, conversation, projet_id, QUESTION_NOTE_S5)
    vues = [etape["libelle"] for etape in echange.etapes]
    gardees = _etapes_gardees(ctx.client.fil(conversation), echange.reponse)
    ctx.note(
        "lectures du fil",
        f"en direct : {' · '.join(vues) or 'aucune'} ; dans le fil relu : "
        f"{'réponse absente' if gardees is None else ' · '.join(gardees) or 'aucune'}",
    )
    if not vues:
        return 0, (
            f"l'orchestrateur a répondu sur `{NOTE_S5}` sans rien lire : aucune étape "
            "n'a paru dans le fil, alors que le contenu de la note n'est dans aucun de "
            "ses contextes"
        )
    if gardees is None:
        return len(vues), (
            f"la réponse sur `{NOTE_S5}` n'est pas dans le fil relu : ses lectures ne "
            "survivent pas à un rechargement"
        )
    if gardees != vues:
        return len(vues), (
            f"les lectures vues en direct ne restent pas dans le fil — en direct : "
            f"{' · '.join(vues)} ; au rechargement : {' · '.join(gardees) or 'aucune'}"
        )
    return len(vues), ""


def _etapes_gardees(
    messages: Sequence[Mapping[str, Any]], reponse: Mapping[str, Any]
) -> list[str] | None:
    """Les libellés des étapes que le fil relu garde sur `reponse` — `None` s'il ne la porte pas.

    Le message se retrouve par ce que la trame `fin` en a dit — son auteur, son
    horodatage, son contenu —, jamais par sa place : un récit ou une autre
    réponse peuvent s'être écrits entre-temps.
    """
    for message in reversed(messages):
        if (
            str(message.get("auteur") or "") == str(reponse.get("auteur") or "")
            and str(message.get("horodatage") or "") == str(reponse.get("horodatage") or "")
            and str(message.get("contenu") or "") == str(reponse.get("contenu") or "")
        ):
            return [
                str(etape.get("libelle") or "")
                for etape in message.get("etapes") or []
                if isinstance(etape, Mapping) and str(etape.get("libelle") or "")
            ]
    return None


def _le_direct(echange: Echange) -> str:
    """Pourquoi la réponse **n'est pas** arrivée en direct — `""` quand elle l'est (#1265).

    C1 : *l'indicateur d'attente ne couvre plus que le temps avant le premier
    mot*. Vu du transport, c'est une propriété de l'**arrivée**, jamais du
    texte : après le premier incrément, il en vient d'autres, plus tard. Deux
    façons d'y manquer, et le motif dit laquelle :

    - **une seule trame** porte tout le texte — le fil d'avant #1222, ou un
      modèle qui a répondu en JSON (`_LectureDuFlux`, régime machine) ;
    - **plusieurs trames, toutes dans la même image d'écran** (`IMAGE_S`) — un
      transport qui tamponne, ou une réponse écrite entière puis découpée : on
      les compte, mais personne ne les voit arriver.

    Aucun seuil de durée n'est posé sur l'attente elle-même : treize secondes de
    lecture avant le premier mot (le bouclage du 2026-09-24) sont le produit qui
    **lit**, pas un produit lent. La mesure est écrite au déroulé
    (`_mesure_du_direct`), le verdict ne tranche que sur la forme de l'arrivée.
    """
    increments = echange.increments
    if len(increments) < 2:
        return (
            f"la réponse est arrivée d'un bloc : {len(increments)} incrément(s) pour "
            f"{len(str(echange.reponse.get('contenu') or ''))} caractère(s) — "
            "l'attente a couvert la réponse entière"
        )
    if echange.images < 2:
        return (
            f"la réponse est arrivée d'un bloc : ses {len(increments)} incréments sont "
            f"tous reçus dans la même image d'écran ({echange.ecriture_s * 1000:.1f} ms) — "
            "l'attente a couvert la réponse entière"
        )
    return ""


def _mesure_du_direct(echange: Echange) -> str:
    """Ce que le banc a vu arriver, en mots — la pièce du verdict, qu'il soit vert ou rouge."""
    attente = echange.attente_s
    return (
        f"{len(echange.increments)} incrément(s) sur {echange.images} image(s) d'écran ; "
        f"premier au bout de {'—' if attente is None else f'{attente:.1f} s'}, "
        f"texte écrit pendant {echange.ecriture_s:.1f} s ; "
        f"{len(echange.etapes)} étape(s) publiée(s) en direct"
    )


def _fichiers_lies(recit: str, racine: Path) -> list[str]:
    """Les fichiers du livrable que `recit` met en lien **et qui existent**.

    La forme lue est celle que l'écran sait rendre en geste
    (`apps/web/lib/markdown.ts`) : `[libellé](<chemin>)`, chevrons compris,
    parce qu'un chemin contient des espaces. L'existence est vérifiée sur le
    **disque** — un chemin cité qui ne mène à rien est un geste mort, et le
    critère demande un lien qui s'ouvre.

    Le chemin est confronté à la racine du projet : un lien vers un fichier
    d'ailleurs n'est pas un fichier du livrable, et le compter rendrait
    l'oracle vert sur un récit qui parle d'autre chose.
    """
    trouves: list[str] = []
    for brut in re.findall(r"\[[^\]\n]*\]\(<([^<>\n]+)>\)", recit):
        chemin = Path(brut.strip())
        if not chemin.is_absolute() or not chemin.is_file():
            continue
        try:
            relatif = chemin.resolve().relative_to(racine.resolve())
        except ValueError:
            continue
        trouves.append(relatif.as_posix())
    return trouves


# --- S6 — le plan appelle un métier que l'équipe n'a pas --------------------


def s6_completer_l_equipe(ctx: Contexte) -> Issue:
    """Le rôle qui manque au plan **se propose dans le fil**, avant d'exécuter (#1260).

    Le cas du retex et de #1227, mot pour mot : une animation du logo, demandée sur
    un projet dont l'équipe n'a qu'un développeur. Le bouclage du 2026-09-24 l'a
    rejoué sur la vraie stack et n'a rien vu paraître — l'hôte détaché n'avait pas
    d'arbitre de renfort, le manque était consigné au journal et nulle part
    ailleurs, et le run finissait en échec 0/3. Les tests de #1227 étaient verts :
    ils exerçaient le seul chemin qui marchait. C'est pour cela que ce scénario
    existe, sur la vraie stack et par le fil.

    Trois choses à constater, dans cet ordre, et l'ordre est l'oracle :

    1. **la proposition paraît dans le fil qui a lancé le run** — un message qui
       porte une demande de recrutement rattachée à **ce** run. C'est un fait
       structurel, jamais une phrase reconnue ;
    2. **l'accepter recrute** le rôle proposé, par les deux gestes de la carte du
       fil : la proposition de ce seul rôle, puis sa validation ;
    3. **le run s'exécute avec l'équipe complétée** : il aboutit, et au moins une
       tâche est allée au rôle recruté — sans quoi on aurait recruté pour rien.

    Le montage réduit l'équipe au seul `dev` que l'analyse propose : c'est la
    situation qu'on veut mesurer, pas une retouche de ce que l'analyse recommande.

    ⚠ Que le plan **nomme** le métier absent est un jugement du modèle (#1227, le
    playbook planifie pour le besoin) : le bouclage l'a vu une fois sur deux. Un
    rouge se rejoue donc une fois, et son motif dit lequel des deux défauts il a
    vu — un plan qui ne nomme rien, ou un manque constaté que personne n'a
    proposé.
    """
    racine = ctx.atelier.dossier("s6-renfort")
    semer_projet_existant(racine)
    projet_id = _declarer(ctx, "banc-s6-renfort", racine, origine="existant")
    if not _doter_d_un_seul_developpeur(ctx, projet_id):
        return empeche(
            f"l'analyse du projet ne propose aucun rôle de gabarit `{GABARIT_SEUL_S6}` : l'équipe "
            "d'un seul développeur que S6 mesure ne peut pas être montée",
            cout_usd=None,
        )

    conversation = ctx.client.ouvrir_conversation()
    reponse = _demander(ctx, conversation, projet_id, DEMANDE_S6)
    if not reponse.get("proposition"):
        return rouge("le fil n'a proposé aucun run pour cette demande", cout_usd=None)
    accord = _accorder(ctx, conversation, projet_id)
    run_id = _run_de(accord)
    if not run_id:
        return rouge("l'accord n'a ouvert aucun run", cout_usd=None)

    demande = _renfort_propose(ctx, conversation, run_id, projet_id)
    if demande is None:
        detail = ctx.client.execution(run_id, projet_id=projet_id)
        return rouge(_sans_proposition(detail), run_id=run_id, cout_usd=_cout(detail))
    ctx.note(
        "renfort proposé dans le fil",
        f"rôle « {demande.get('role')} » (gabarit `{demande.get('gabarit')}`) — "
        f"tâches : {', '.join(str(t) for t in demande.get('taches') or []) or '—'}",
    )

    proposition = ctx.client.proposition_equipe(
        projet_id,
        renfort={"gabarit": demande.get("gabarit"), "raison": demande.get("raison")},
    )
    validee = equipe_validee(proposition)
    if not validee["roles"]:
        return rouge(
            "la proposition du rôle de renfort est vide : il n'y a rien à recruter",
            run_id=run_id,
            cout_usd=None,
        )
    suite = ctx.client.recruter(conversation=conversation, validee=validee)
    recrues = {str(role["nom"]) for role in validee["roles"]}
    ctx.note("renfort accepté dans le fil", f"{', '.join(sorted(recrues))} — {_extrait(suite)}")

    detail = _suivre(ctx, run_id, projet_id)
    cout = _cout(detail)
    if str(detail.get("statut")) != EXECUTION_TERMINEE:
        return rouge(
            f"le renfort accepté, le run s'est soldé « {detail.get('statut')} » "
            f"(cause « {detail.get('cause') or '—'} ») au lieu d'aboutir",
            run_id=run_id,
            cout_usd=cout,
        )
    faites = _taches_terminees_par(detail, recrues)
    if not faites:
        return rouge(
            f"le run a abouti, mais aucune tâche n'est allée au rôle recruté "
            f"({', '.join(sorted(recrues))}) : l'équipe complétée n'a pas servi",
            run_id=run_id,
            cout_usd=cout,
        )
    return vert(
        f"le rôle « {demande.get('role')} » s'est proposé dans le fil avant la "
        f"première tâche ; accepté, il a pris {len(faites)} tâche(s) "
        f"({', '.join(faites[:3])}) et le run a abouti",
        run_id=run_id,
        cout_usd=cout,
    )


def _doter_d_un_seul_developpeur(ctx: Contexte, projet_id: str) -> bool:
    """Crée l'équipe d'un seul développeur — le rôle `dev` que l'analyse propose.

    Du **montage**, comme `_doter_d_une_equipe` : le rôle est repris tel que
    l'analyse l'a écrit, playbook compris. Ce qui est retiré, ce sont les autres
    rôles — c'est la situation que S6 mesure, celle d'un projet qui n'a recruté
    qu'un développeur. `False` quand l'analyse n'en propose aucun.
    """
    validee = equipe_validee(ctx.client.proposition_equipe(projet_id))
    seuls = [role for role in validee["roles"] if role.get("gabarit") == GABARIT_SEUL_S6]
    if not seuls:
        return False
    ctx.client.creer_equipe(projet_id, {**validee, "roles": seuls[:1]})
    ctx.note("équipe créée", f"1 rôle : {seuls[0]['nom']} — le seul développeur")
    return True


def _renfort_propose(
    ctx: Contexte, conversation: str, run_id: str, projet_id: str
) -> dict[str, Any] | None:
    """La demande de renfort que le fil porte pour `run_id` — `None` si elle n'y vient pas.

    Reconnue à sa **structure** : un message du fil dont la demande de recrutement
    est rattachée à ce run. Il faut l'attendre — le cadrage et la décomposition
    sont deux appels modèle —, mais pas au-delà d'`ATTENTE_RENFORT_S`, et pas
    au-delà de la fin du run : un run soldé n'attend plus personne.
    """
    limite = ctx.horloge() + ATTENTE_RENFORT_S
    while True:
        for message in ctx.client.fil(conversation):
            demande = message.get("recrutement")
            if isinstance(demande, Mapping) and str(demande.get("run_id") or "") == run_id:
                return dict(demande)
        statut = str(ctx.client.execution(run_id, projet_id=projet_id).get("statut") or "")
        if statut in STATUTS_EXECUTION_TERMINAUX or ctx.horloge() >= limite:
            return None
        ctx.dormir(INTERVALLE_RENFORT_S)


def _sans_proposition(detail: Mapping[str, Any]) -> str:
    """Pourquoi rien ne s'est proposé — et lequel des deux défauts on a vu.

    Deux causes ne se corrigent pas du tout pareil, et le rapport doit les
    distinguer : le moteur a **constaté** le manque (sa ligne « L'équipe confrontée
    au plan » est dans la trace) sans que rien n'atteigne le fil — la panne de
    #1260 —, ou le plan n'a **nommé** aucun métier absent, et il n'y avait rien à
    proposer. La ligne se reconnaît à son statut, jamais à son texte.
    """
    statut = str(detail.get("statut") or "")
    constats = [
        str(evenement.get("detail") or "").strip()
        for evenement in detail.get("evenements") or []
        if str(evenement.get("statut") or "") == STATUT_ROLE_MANQUANT
        and not str(evenement.get("tache_id") or "")
    ]
    fin = f" ; le run s'est soldé « {statut} »" if statut in STATUTS_EXECUTION_TERMINAUX else ""
    if constats:
        return (
            f"le moteur a constaté le manque ({constats[0][:200]}) mais rien ne s'est "
            f"proposé dans le fil qui a lancé le run{fin}"
        )
    return (
        "aucun renfort proposé : le plan n'a nommé aucun métier que l'équipe d'un "
        f"seul développeur n'a pas{fin}"
    )


def _taches_terminees_par(detail: Mapping[str, Any], agents: set[str]) -> list[str]:
    """Les tâches du run terminées par l'un des `agents` — lues dans la trace de l'API."""
    faites: list[str] = []
    for evenement in detail.get("evenements") or []:
        if (
            str(evenement.get("type") or "") == EVENEMENT_TACHE_STATUT
            and str(evenement.get("statut") or "") == STATUT_TERMINEE
            and str(evenement.get("agent") or "") in agents
        ):
            titre = str(evenement.get("titre") or evenement.get("tache_id") or "")
            if titre and titre not in faites:
                faites.append(titre)
    return faites


# --- S7 — un projet naît dans la conversation -------------------------------

#: Ce que la personne dit en arrivant, mot pour mot : c'est la phrase du critère
#: de #1294, et celle du retour d'expérience du 2026-09-24 (docs/43 §1).
DEMANDE_S7 = "Je veux un site vitrine pour mon kombucha."

#: La réponse de la personne à une question du fil : elle ne sait rien de plus, et
#: laisse Maestro choisir. Un scénario ne peut pas deviner ce qu'on lui demandera ;
#: il peut dire ce que dirait quelqu'un qui n'a pas d'avis.
REPONSE_S7 = (
    "Je n'ai pas d'autre précision : choisissez ce qui vous paraît le mieux et "
    "proposez-moi le projet."
)

#: Le nom que la correction demande — l'exemple du critère de #1294.
NOM_S7 = "racines"

#: Combien de tours le fil a pour arriver à une proposition — la première, puis la
#: corrigée. « Au plus les questions qui manquent » (#1294) : trois tours, c'est
#: déjà deux questions pour un site vitrine ; au-delà, le fil ne propose pas, il
#: interroge.
TOURS_S7 = 3


def _proposition_du_fil(
    ctx: Contexte, conversation: str, reponse: dict[str, Any]
) -> dict[str, Any] | None:
    """La carte de projet que le fil pose, en répondant à ses questions — `None` sinon."""
    for tour in range(TOURS_S7):
        proposee = reponse.get("projet_propose")
        if isinstance(proposee, Mapping) and proposee.get("racine"):
            return dict(proposee)
        if tour + 1 >= TOURS_S7:
            break
        reponse = _demander(ctx, conversation, "", REPONSE_S7)
    return None


def _meme_dossier(a: str, b: Path) -> bool:
    """Deux écritures du même dossier se reconnaissent — l'API rend du POSIX canonique."""
    try:
        return Path(a).resolve() == b.resolve()
    except OSError:  # pragma: no cover - chemin illisible pour l'OS
        return False


def s7_un_projet_nait_dans_la_conversation(ctx: Contexte) -> Issue:
    """Un projet naît dans la conversation : compris, proposé, corrigé, déclaré sur accord (#1294).

    Le parcours de la personne, par la seule porte qu'elle a — le fil, **sans
    projet** : elle dit ce qu'elle veut construire ; le fil pose au plus les
    questions qui manquent, puis propose un nom, un dossier et le versionnement ;
    elle **corrige en langage naturel** (le nom, et le dossier, rangé dans
    l'atelier du passage) ; elle accepte d'un geste.

    L'oracle regarde le monde, pas la prose :

    - **rien n'est déclaré avant l'accord** — ni à la proposition, ni à la
      correction : la liste des projets de l'API ne connaît pas le dossier ;
    - la correction est **prise** : la proposition suivante porte le nom demandé
      et le dossier demandé, vérifiés par l'API ;
    - après l'accord, le projet **existe** : servi par `GET /api/projets` sur ce
      dossier, dossier présent sur le disque, sous Git si la mise sous Git était
      proposée.

    Rejouable : que le modèle propose au premier tour ou pose une question de plus
    dépend de lui, et une correction mal comprise une fois ne dit pas encore que
    le produit ne la prend pas.
    """
    racine = ctx.atelier.dossier("s7-kombucha")
    conversation = ctx.client.ouvrir_conversation()

    premiere = _proposition_du_fil(
        ctx, conversation, _demander(ctx, conversation, "", DEMANDE_S7)
    )
    if premiere is None:
        return rouge(
            f"le fil n'a proposé aucun projet en {TOURS_S7} tours pour « {DEMANDE_S7} »",
            cout_usd=None,
        )
    ctx.note(
        "projet proposé",
        f"« {premiere.get('nom')} » — {premiere.get('racine')} — "
        f"versionner : {premiere.get('versionner')}",
    )

    correction = (
        f"Appelle-le « {NOM_S7} », et mets-le plutôt dans le dossier "
        f"{racine.as_posix()}."
    )
    corrigee = _proposition_du_fil(
        ctx, conversation, _demander(ctx, conversation, "", correction)
    )
    if corrigee is None:
        return rouge("la correction n'a amené aucune proposition nouvelle", cout_usd=None)
    ctx.note(
        "proposition corrigée",
        f"« {corrigee.get('nom')} » — {corrigee.get('racine')} — "
        f"versionner : {corrigee.get('versionner')}",
    )
    if any(_meme_dossier(str(f.get("racine") or ""), racine) for f in ctx.client.projets()):
        return rouge("un projet a été déclaré avant l'accord", cout_usd=None)
    if not _meme_dossier(str(corrigee.get("racine") or ""), racine):
        return rouge(
            f"la correction du dossier n'a pas été prise : proposé "
            f"{corrigee.get('racine')} au lieu de {racine.as_posix()}",
            cout_usd=None,
        )
    if NOM_S7 not in str(corrigee.get("nom") or "").casefold():
        return rouge(
            f"la correction du nom n'a pas été prise : proposé « {corrigee.get('nom')} »",
            cout_usd=None,
        )

    accord = ctx.client.declarer_par_le_fil(conversation=conversation)
    ctx.note("accord donné", _extrait(accord))
    cree = accord.get("projet_cree")
    if not isinstance(cree, Mapping) or not cree.get("id"):
        return rouge(
            f"l'accord n'a déclaré aucun projet : {_extrait(accord)}", cout_usd=None
        )
    ctx.projet_id, ctx.racine = str(cree["id"]), racine
    fiche = next((f for f in ctx.client.projets() if f.get("id") == cree["id"]), None)
    if fiche is None:
        return rouge(f"le projet {cree['id']} n'est pas servi par l'API", cout_usd=None)
    if not _meme_dossier(str(fiche.get("racine") or ""), racine) or not racine.is_dir():
        return rouge(
            f"le projet {cree['id']} est déclaré sur {fiche.get('racine')}, "
            f"pas sur {racine.as_posix()}",
            cout_usd=None,
        )
    if corrigee.get("versionner") and not (racine / ".git").exists():
        return rouge(
            "la mise sous Git était proposée et acceptée, mais le dossier n'est pas "
            f"versionné (refus : {cree.get('versionnement_refuse') or '—'})",
            cout_usd=None,
        )
    git = "sous Git" if (racine / ".git").exists() else "sans versionnement"
    return vert(
        f"« {fiche.get('nom')} » est né dans la conversation : proposé, corrigé en "
        f"langage naturel, déclaré sur accord seulement ({racine.as_posix()}, {git})",
        cout_usd=None,
    )


# --- Le catalogue ----------------------------------------------------------


@dataclass(frozen=True)
class Scenario:
    """Un scénario de référence : son identifiant, ce qu'il vérifie, comment il se joue.

    `rejouable` dit si un rouge doit être **rejoué une fois** avant d'être cru
    (docs/40 §5) : S2 demande au modèle d'écrire du code qui s'exécute et S4 de
    reconnaître une cause dans une phrase, deux choses qui échouent parfois sans
    que le produit ait changé. S1 et S3, eux, portent sur des mécaniques
    déterministes — les rejouer masquerait un défaut intermittent au lieu de le
    montrer, et paierait un second run pour cela.
    """

    identifiant: str
    titre: str
    jouer: Callable[[Contexte], Issue]
    rejouable: bool = False


#: Les scénarios, dans l'ordre où le banc les joue. Cet ordre est celui de
#: docs/40 §5 et il ne porte aucune dépendance : chacun déclare son propre projet
#: jetable, si bien que `--scenario S3` joue exactement ce que le passage complet
#: joue en troisième.
SCENARIOS: tuple[Scenario, ...] = (
    Scenario("S1", "Vider un dossier", s1_vider_un_dossier),
    Scenario("S2", "Créer une petite application exécutable", s2_creer_une_application, True),
    Scenario("S3", "Reprendre un projet existant sans équipe", s3_reprendre_sans_equipe),
    Scenario("S4", "Pourquoi le run a-t-il échoué ?", s4_pourquoi_l_echec, True),
    Scenario(
        "S5",
        "Comment j'essaie ce que le run a livré ?",
        s5_comment_essayer_le_livrable,
        True,
    ),
    Scenario(
        "S6",
        "Le plan appelle un métier que l'équipe n'a pas",
        s6_completer_l_equipe,
        True,
    ),
    Scenario(
        "S7",
        "Un projet naît dans la conversation",
        s7_un_projet_nait_dans_la_conversation,
        True,
    ),
)


def par_identifiant(identifiants: Sequence[str]) -> tuple[Scenario, ...]:
    """Les scénarios nommés, dans l'ordre du catalogue — lève sur un nom inconnu.

    Dans l'ordre du **catalogue** et non celui de la ligne de commande : le
    rapport d'un passage partiel se relit à côté de celui d'un passage complet, et
    deux ordres pour la même liste rendraient la comparaison illisible.
    """
    connus = {s.identifiant.upper(): s for s in SCENARIOS}
    demandes = [i.strip().upper() for i in identifiants if i.strip()]
    inconnus = [i for i in demandes if i not in connus]
    if inconnus:
        raise ValueError(
            f"scénario(s) inconnu(s) : {', '.join(inconnus)} — connus : "
            f"{', '.join(connus)}"
        )
    return tuple(s for s in SCENARIOS if s.identifiant.upper() in set(demandes))


def nettoyer(ctx: Contexte) -> None:
    """Retire la déclaration du projet jetable du scénario — best-effort.

    Le **dossier** ne part pas ici (`Atelier.retirer` s'en charge sur
    `--nettoyer`) : `DELETE /api/projets` ne touche jamais au disque (#221), et
    c'est la règle qu'on veut garder — oublier un projet n'est pas supprimer du
    travail.
    """
    if not ctx.projet_id:
        return
    try:
        ctx.client.retirer_projet(ctx.projet_id)
    except ErreurAPI:  # le poste garde la déclaration : le rapport dit où elle est
        return

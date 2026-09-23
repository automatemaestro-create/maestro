"""Les cinq scénarios de référence, et ce qui les rend verts (#1148, docs/40 §5).

| | Scénario | Ce qui le rend vert |
|---|---|---|
| S1 | Vider un dossier | Le dossier est vide hors périmètre exclu |
| S2 | Créer une petite application | Elle s'exécute, sans commande soumise à la personne |
| S3 | Reprendre un projet sans équipe | L'équipe est proposée avant de dépenser, puis ça part |
| S4 | « Pourquoi le run a échoué ? » | La réponse nomme la cause de l'API, jugée par un modèle |
| S5 | « Comment j'essaie le livrable ? » | La fin se raconte, lie un fichier réel, dit quoi taper |

## Trois règles que ces cinq scénarios suivent

**La porte d'entrée est le fil, toujours.** Une demande passe par
`POST /api/chat/orchestrateur/messages`, l'accord par le geste de cadrage. C'est la
seule porte qu'un écran offre depuis #666, donc la seule dont l'état vaut quelque
chose : un banc qui appellerait `POST /api/executions` vérifierait un chemin que
personne n'emprunte.

**Ce que le scénario mesure n'est pas ce qu'il prépare.** S1, S2, S4 et S5 dotent
leur projet d'une équipe **avant** de demander quoi que ce soit, par la route
d'équipe du projet (#1039/#1040). C'est du montage, et le faire passer par le fil
ferait de chacun une copie de S3 — quatre scénarios qui échouent ensemble au
premier défaut de recrutement, et plus aucun qui parle de vider un dossier. S3,
lui, **part** d'un projet sans équipe : c'est son sujet.

**L'oracle regarde le monde, pas la prose.** Le disque pour S1, l'application
lancée pour S2, l'équipe écrite et le run soldé pour S3. Les deux oracles qui
portent sur une phrase — S4 et S5 — passent par un modèle
(`maestro.scenarios.juge`, #746) : un lexique se tromperait dans les deux sens.
Et même là, ce qui peut se constater se constate : S5 vérifie **sur le disque**
que le fichier mis en lien par le récit existe, avant de demander à qui que ce
soit ce qu'il pense du texte.

## Ce que ces scénarios coûtent, et pourquoi S2, S4 et S5 se rejouent

Un passage coûte du vrai modèle (le run du retex du 2026-09-11 a coûté ~10 $),
d'où le banc hors CI. S2, S4 et S5 ne sont pas déterministes — écrire du code qui
s'exécute, reconnaître une cause dans une phrase, dire comment essayer un
livrable — donc un rouge se rejoue **une** fois avant d'être cru, et le rapport
dit s'il l'a été (`Scenario.rejouable`, appliqué par `maestro.scenarios.banc`).
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

from maestro.controltower.state import (
    EXECUTION_ECHEC,
    EXECUTION_TERMINEE,
    STATUTS_EXECUTION_TERMINAUX,
)
from maestro.lecture import OUTIL_SHELL
from maestro.scenarios.api import (
    DELAI_RUN_S,
    ClientAPI,
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

#: Le plafond qui provoque l'échec de S4. En **tokens** et non en dollars : les
#: tokens sont toujours rapportés, quel que soit le fournisseur (#113), là où un
#: plafond en dollars n'a aucune prise sur un endpoint qui ne tarife pas. Le run
#: s'arrête donc à sa première mesure — l'échec est provoqué pour presque rien,
#: ce qui compte sur un banc qui paie du vrai modèle.
PLAFOND_TOKENS_S4 = 1


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


# --- Les gestes que les quatre scénarios partagent --------------------------


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
    return vert(
        f"`python {POINT_D_ENTREE}` s'exécute et sort en 0 "
        f"({sortie[:120] or 'aucune sortie'}) ; aucune validation de commande "
        "demandée à la personne",
        run_id=run_id,
        cout_usd=cout,
    )


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

    explication = _demander(
        ctx, conversation, projet_id, "Comment j'essaie ce que tu viens de livrer ?"
    )
    avis = ctx.juge.dit_comment_essayer(
        livrable=", ".join(restes(racine)[:30]) or "aucun fichier",
        recit=recit,
        reponse=str(explication.get("contenu") or ""),
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
    return vert(
        f"la fin du run se raconte dans le fil, met {len(lies)} fichier(s) du "
        f"livrable en lien, et dit comment l'essayer — {avis.pourquoi}",
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


#: Les quatre scénarios, dans l'ordre où le banc les joue. Cet ordre est celui de
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

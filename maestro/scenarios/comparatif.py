"""Le banc comparatif : un même objectif sur Maestro et sur un agent seul, plusieurs fois (#1512).

    .venv/Scripts/python.exe -m maestro.scenarios.comparatif [--objectif p5] [--passages <n>]
                                                             [--cote maestro|seul] [--delai <s>]
                                                             [--axes <a,b>]
    .venv/Scripts/python.exe -m maestro.scenarios.comparatif --rejouer <côté>=<dépôt> [...]
                                                             [--objectif p5] [--axes <a,b>]
    .venv/Scripts/python.exe -m maestro.scenarios.comparatif --liste

Le go/no-go du jalon « Maestro fait mieux qu'un agent seul » (#1510, docs/48) se
tranche sur plusieurs passages. Le bilan du 2026-10-14 n'en avait qu'un de chaque
côté, joué à la main (C2 et C3, docs/50) : la durée et le coût d'un run varient
d'un passage à l'autre, et un passage ne permet pas de conclure. Ce module joue la
même mesure en une commande, autant de fois qu'on la demande.

## Les deux côtés

**Maestro** : la vraie stack et le vrai modèle, l'objectif confié **par le fil**
comme une personne le ferait — le parcours de S9 et S12 (`_naitre_dans_l_atelier`,
`_lancer_dans_le_fil`). Le projet naît du message, s'outille dans la conversation,
se dote d'une équipe, puis le run part et se suit jusqu'à son issue, arbitrages
approuvés au passage. La personne répond aux questions d'outillage par **ses choix,
avec ses mots** (`Objectif.repondre`), jamais par une aide technique.

**L'agent seul** : même modèle, même message, mêmes outils que les agents de Maestro
(`DEFAULT_TOOLS`), et rien d'autre — ni prompt système, ni plan, ni juge. Il passe
par l'**adaptateur** du fournisseur (`ModelProvider.run_agent`), le contrat que
Maestro possède : aucun outil interne d'un CLI n'est lu (docs/44), et son coût est
celui que l'adaptateur rapporte (`collect_usage`). Il n'a **aucun garde-fou** :
il travaille dans un établi de l'atelier du banc, sous les racines déclarées
(`maestro.emplacements`, #1454), et ce qu'il lance est confiné à sa session
(#1279) — rien d'autre ne le retient.

## Ce qu'un passage range

Sa **durée** (du premier geste au run soldé, ou à la fin de la session), son
**coût** et les **gestes de la personne** : chaque requête que le banc fait à sa
place — un message, une réponse d'outillage, une pièce tranchée, l'accord d'un
projet, d'une équipe, d'un run, un arbitrage (`ClientCompteur`). Ce que le banc fait
pour lui-même, ranger le projet dans son atelier, ne compte pas. L'agent seul en a
un : son message.

Le **coût de Maestro** est celui du grand livre du projet, la vue Coûts & analytics
(`ClientAPI.couts`) : le compte du produit, ce qu'il voit et rien de plus. Ce que
le grand livre ne voit pas, le banc ne l'invente pas : c'est le grand livre qui se
corrige. Depuis #1534, il compte le fil et les tentatives coupées ; le rapport en
détaille les postes hors des agents (`POSTES_DU_GRAND_LIVRE`).

Puis **chaque livrable se rejoue sur un clone propre**, selon la grille du bilan
(`maestro.scenarios.grille`) : dix lignes, de l'installation à l'outillage suivi.

## Trois issues, et la troisième ne compte pas

`abouti` : le run s'est terminé, ou la session a rendu la main. `non abouti` : le run
a échoué, la session aussi, ou l'un d'eux n'a pas fini dans le délai — c'est une
perte, comptée avec ce qu'elle a coûté, et son livrable se rejoue tel qu'il est.
`empêché` : le passage n'a pas eu lieu — l'API a refusé, le fournisseur a opposé sa
limite d'usage, lue dans un champ typé (`cause` du run) ou par le classement du
produit (`maestro.controltower.causes.cause_de`), jamais dans un texte relu ici
(docs/44). Un empêchement est une **abstention** : il n'entre dans aucune médiane,
comme un banc injouable n'est jamais compté vert (docs/47 § 5).

## Ce qui se compare

Pour chaque côté, sur les passages comptés : la **médiane** de la durée, du coût,
des gestes et des lignes tenues, et leur **dispersion** — l'étendue, du plus petit
au plus grand, et son rapport à la médiane. Puis le rapport des médianes, Maestro
sur l'agent seul. Le rapport s'écrit sous `.maestro/comparatif/<passage>/`
(`rapport.md`, `rapport.json`, #234). Le banc ne rend **aucun verdict** : il mesure,
et le go/no-go se tranche sur ses chiffres (docs/48).

`--rejouer <côté>=<dépôt>` rejoue seulement la grille sur des livrables qui existent
déjà — ceux de C2 et C3, ou les deux livrables d'un ticket (#1522) —, côte à côte,
sans modèle pour les construire.

## Codes de sortie

`0` le rapport est écrit · `2` usage · `3` l'API ne répond pas (rien n'a été joué)
· `4` aucun passage n'a pu se jouer : tous empêchés, une abstention.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import sys
import time
import tomllib
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO

from maestro.agents.runtime import DEFAULT_TOOLS
from maestro.controltower.causes import CAUSE_LIMITE_USAGE, cause_de, cause_lisible
from maestro.controltower.state import EXECUTION_TERMINEE
from maestro.scenarios.api import ClientAPI, ErreurAPI, Transport, TransportHTTP, base_locale
from maestro.scenarios.banc import GESTE_PREALABLE
from maestro.scenarios.grille import (
    AXES,
    LIBELLES,
    Grille,
    Lecteur,
    PosteDuBanc,
    rejouer,
)
from maestro.scenarios.juge import Juge, JugeModele
from maestro.scenarios.modele import Etape, Issue, Journal
from maestro.scenarios.modele import horodatage as horodatage_courant
from maestro.scenarios.projets import Atelier
from maestro.scenarios.scenarios import (
    REPONSE_OUTILLAGE,
    Contexte,
    _lancer_dans_le_fil,
    _naitre_dans_l_atelier,
    _revue_d_apres_le_run,
    _suivre,
)
from maestro.telemetry import collect_usage

if TYPE_CHECKING:
    from maestro.providers.base import ModelProvider

MODULE = __spec__.name if __spec__ else "maestro.scenarios.comparatif"

#: Les objectifs du banc : un fichier TOML chacun, nommé par son objectif.
DOSSIER_OBJECTIFS = Path(__file__).parent / "objectifs"
OBJECTIF_DEFAUT = "p5"

#: Où les rapports s'écrivent, **relatif au répertoire courant** (#234).
RACINE_RAPPORTS = Path(".maestro") / "comparatif"
FICHIER_MARKDOWN = "rapport.md"
FICHIER_JSON = "rapport.json"

#: Les deux côtés du banc, dans l'ordre où un passage les joue.
COTE_MAESTRO = "maestro"
COTE_SEUL = "seul"
COTES: tuple[str, ...] = (COTE_MAESTRO, COTE_SEUL)

#: Les trois issues d'un passage (voir l'en-tête).
ABOUTI = "abouti"
NON_ABOUTI = "non abouti"
EMPECHE = "empêché"

#: Ce que le banc laisse à un passage, de chaque côté, avant de cesser d'attendre et de
#: le dire. Deux heures : C2 a mis 64 min hors arrêt sur p5, et C3 26 min (docs/50 §
#: 1.3). Ce n'est pas un plafond de dépense, et `--delai` la règle.
DELAI_PASSAGE_S = 7200.0

#: Ce que chaque verbe du banc fait à la place de la personne, nommé (`ClientCompteur`).
GESTE_MESSAGE = "message"
GESTE_PROJET = "accord du projet"
GESTE_QUESTION = "réponse d'outillage"
GESTE_PIECE = "pièce d'outillage"
GESTE_EQUIPE = "équipe validée"
GESTE_RUN = "accord du run"
GESTE_ARBITRAGE = "arbitrage"
GESTE_VERSIONNER = "versionnement"
GESTE_PLAFOND = "geste au plafond"

CODE_ECRIT = 0
CODE_USAGE = 2
CODE_API_MUETTE = 3
CODE_AUCUN_PASSAGE = 4

_USAGE = (
    f"Usage : python -m {MODULE} [--objectif <nom|fichier.toml>] [--passages <n>] "
    "[--cote maestro|seul] [--delai <secondes>] [--axes <a,b>]\n"
    f"        python -m {MODULE} --rejouer <côté>=<dépôt> [...] [--objectif …] [--axes …]\n"
    f"        python -m {MODULE} --liste"
)


# --- L'objectif -------------------------------------------------------------


@dataclass(frozen=True)
class Objectif:
    """Ce que la personne demande, et rien de plus : le message, ses choix, sa forme.

    `message` est envoyé **tel quel** des deux côtés. `choix` sont ses choix
    d'outillage, un par ligne : ce qu'elle répond quand Maestro lui pose une question
    d'outillage, et ce que la grille confronte au livrable. `travail` est ce qu'elle
    demande une fois le projet né et outillé — le message lui-même s'il n'est pas dit.
    `versionner` demande un projet sous Git. `axes` restreint la grille (toutes ses
    lignes par défaut). `cout_estime_usd` est ce qu'un passage a coûté de chaque côté,
    annoncé avant de jouer — jamais une borne.
    """

    nom: str
    titre: str
    message: str
    choix: tuple[str, ...] = ()
    travail: str = ""
    versionner: bool = False
    axes: tuple[str, ...] = AXES
    cout_estime_usd: Mapping[str, float] = field(default_factory=dict)

    @property
    def demande_de_travail(self) -> str:
        """Ce que la personne demande au fil une fois le projet outillé."""
        return self.travail or self.message

    @property
    def reponse_outillage(self) -> str:
        """Ce que la personne répond à une question d'outillage : ses choix, avec ses mots.

        Pas d'option de la carte, et pas de recherche de l'option qui ressemblerait à
        un choix : ce serait juger une phrase par ses mots (#746). La personne redit
        ce qu'elle a choisi, et c'est le fil qui comprend (docs/41). Sans choix, elle
        n'a pas d'avis, comme dans S9.
        """
        if not self.choix:
            return REPONSE_OUTILLAGE
        return (
            f"Mes choix sont faits : {' ; '.join(self.choix)}. "
            "Pour le reste, choisissez ce qui convient le mieux à ce projet."
        )

    def repondre(self, _question: Mapping[str, Any]) -> tuple[str, bool]:
        """La réponse à une question d'outillage — toujours avec ses mots (`libre`)."""
        return self.reponse_outillage, True


def objectifs_connus() -> tuple[str, ...]:
    """Les objectifs du banc, par leur nom."""
    return tuple(sorted(chemin.stem for chemin in DOSSIER_OBJECTIFS.glob("*.toml")))


def charger_objectif(reference: str) -> Objectif:
    """L'objectif nommé — ou lu dans le fichier TOML désigné. Lève `ValueError` avec son motif."""
    chemin = Path(reference)
    if chemin.suffix != ".toml":
        chemin = DOSSIER_OBJECTIFS / f"{reference}.toml"
    if not chemin.is_file():
        connus = ", ".join(objectifs_connus()) or "aucun"
        raise ValueError(f"objectif inconnu : {reference} (connus : {connus})")
    try:
        donnees = tomllib.loads(chemin.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"objectif illisible ({chemin}) : {exc}") from exc
    return objectif_depuis(chemin.stem, donnees)


def objectif_depuis(nom: str, donnees: Mapping[str, Any]) -> Objectif:
    """L'objectif que décrit `donnees` — lève `ValueError` sur toute forme inattendue."""
    message = donnees.get("message")
    if not isinstance(message, str) or not message.strip():
        raise ValueError(f"objectif {nom} : `message` doit être un texte non vide")
    choix = donnees.get("choix", [])
    if not isinstance(choix, list) or not all(isinstance(c, str) and c.strip() for c in choix):
        raise ValueError(f"objectif {nom} : `choix` doit être une liste de textes")
    axes = donnees.get("axes", list(AXES))
    if not isinstance(axes, list) or not axes or any(axe not in AXES for axe in axes):
        raise ValueError(
            f"objectif {nom} : `axes` doit nommer des lignes de la grille ({', '.join(AXES)})"
        )
    couts = donnees.get("cout_estime_usd", {})
    if not isinstance(couts, Mapping) or not all(
        isinstance(v, int | float) and not isinstance(v, bool) for v in couts.values()
    ):
        raise ValueError(f"objectif {nom} : `cout_estime_usd` doit donner un montant par côté")
    return Objectif(
        nom=nom,
        titre=str(donnees.get("titre") or nom),
        message=message.strip(),
        choix=tuple(c.strip() for c in choix),
        travail=str(donnees.get("travail") or "").strip(),
        versionner=bool(donnees.get("versionner", False)),
        axes=tuple(axe for axe in AXES if axe in axes),
        cout_estime_usd={str(cote): float(montant) for cote, montant in couts.items()},
    )


# --- Les gestes de la personne ----------------------------------------------


class ClientCompteur(ClientAPI):
    """Le client du banc, qui compte chaque geste qu'il fait à la place de la personne.

    Compter les verbes, et non les requêtes : un verbe du banc **est** un geste
    d'écran (`maestro.scenarios.api`), et c'est ainsi que le bilan les a comptés à la
    main (« 20 avant le run, 7 arbitrages »). Ouvrir une conversation n'en est pas un ;
    ce que le banc fait pour lui-même non plus, et il l'encadre de `montage`.
    """

    def __init__(self, transport: Transport, *, delai_modele_s: float = DELAI_PASSAGE_S) -> None:
        super().__init__(transport, delai_modele_s=delai_modele_s)
        self.gestes: list[str] = []
        self._montage = 0

    @contextmanager
    def montage(self) -> Iterator[None]:
        """Ce que le banc fait pour lui-même : rien de ce qui s'y joue n'est compté."""
        self._montage += 1
        try:
            yield
        finally:
            self._montage -= 1

    def _geste(self, nature: str) -> None:
        if not self._montage:
            self.gestes.append(nature)

    def envoyer(self, contenu: str, *, projet_id: str, conversation: str) -> dict[str, Any]:
        self._geste(GESTE_MESSAGE)
        return super().envoyer(contenu, projet_id=projet_id, conversation=conversation)

    def envoyer_en_direct(self, contenu: str, *, projet_id: str, conversation: str) -> Any:
        self._geste(GESTE_MESSAGE)
        return super().envoyer_en_direct(contenu, projet_id=projet_id, conversation=conversation)

    def declarer_par_le_fil(self, *, conversation: str, approuve: bool = True) -> dict[str, Any]:
        self._geste(GESTE_PROJET)
        return super().declarer_par_le_fil(conversation=conversation, approuve=approuve)

    def repondre_question(
        self, *, conversation: str, valeur: str, libre: bool = False
    ) -> dict[str, Any]:
        self._geste(GESTE_QUESTION)
        return super().repondre_question(conversation=conversation, valeur=valeur, libre=libre)

    def trancher_piece(
        self, *, conversation: str, decision: str, piece: str = ""
    ) -> dict[str, Any]:
        self._geste(GESTE_PIECE)
        return super().trancher_piece(conversation=conversation, decision=decision, piece=piece)

    def recruter(
        self, *, conversation: str, validee: Mapping[str, Any], approuve: bool = True
    ) -> dict[str, Any]:
        self._geste(GESTE_EQUIPE)
        return super().recruter(conversation=conversation, validee=validee, approuve=approuve)

    def trancher_cadrage(
        self,
        *,
        conversation: str,
        projet_id: str,
        approuve: bool = True,
        bornes: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._geste(GESTE_RUN)
        return super().trancher_cadrage(
            conversation=conversation, projet_id=projet_id, approuve=approuve, bornes=bornes
        )

    def decider(self, tache_id: str, *, approuve: bool = True) -> None:
        self._geste(GESTE_ARBITRAGE)
        super().decider(tache_id, approuve=approuve)

    def versionner_projet(self, projet_id: str) -> dict[str, Any]:
        self._geste(GESTE_VERSIONNER)
        return super().versionner_projet(projet_id)

    def arreter_au_plafond(self, run_id: str) -> None:
        self._geste(GESTE_PLAFOND)
        super().arreter_au_plafond(run_id)


# --- Un passage -------------------------------------------------------------


@dataclass(frozen=True)
class Passage:
    """Un passage d'un côté : son issue, sa durée, son coût, les gestes, et son livrable rejoué.

    `duree_s`, `cout_usd` et `gestes` valent `None` quand rien ne les a mesurés — un
    livrable seulement rejoué (`--rejouer`), un coût que personne n'a rapporté :
    inconnu, jamais zéro. `cout_source` dit d'où vient le coût.
    """

    cote: str
    rang: int
    issue: str
    motif: str
    duree_s: float | None = None
    cout_usd: float | None = None
    cout_source: str = ""
    cout_postes: Mapping[str, float] = field(default_factory=dict)
    gestes: tuple[str, ...] | None = None
    livrable: str = ""
    run_id: str = ""
    projet_id: str = ""
    modele: str = ""
    grille: Grille | None = None
    etapes: tuple[Etape, ...] = ()

    @property
    def compte(self) -> bool:
        """Entre-t-il dans les médianes ? Un empêchement est une abstention."""
        return self.issue != EMPECHE

    def avec_grille(self, grille: Grille, etapes: Sequence[Etape]) -> Passage:
        """Le même passage, son livrable rejoué."""
        return replace(self, grille=grille, etapes=tuple(etapes))

    def to_dict(self) -> dict[str, Any]:
        """Le passage en JSON — la forme du rapport."""
        return {
            "cote": self.cote,
            "rang": self.rang,
            "issue": self.issue,
            "motif": self.motif,
            "duree_s": None if self.duree_s is None else round(self.duree_s, 3),
            "cout_usd": self.cout_usd,
            "cout_source": self.cout_source,
            "cout_postes": dict(self.cout_postes),
            "gestes": None if self.gestes is None else len(self.gestes),
            "detail_gestes": None if self.gestes is None else list(self.gestes),
            "livrable": self.livrable,
            "run_id": self.run_id,
            "projet_id": self.projet_id,
            "modele": self.modele,
            "grille": None if self.grille is None else self.grille.to_dict(),
            "etapes": [etape.to_dict() for etape in self.etapes],
        }


def jouer_maestro(
    objectif: Objectif,
    *,
    rang: int,
    client: ClientCompteur,
    atelier: Atelier,
    juge: Juge,
    journal: Journal,
    delai_s: float = DELAI_PASSAGE_S,
    horloge: Callable[[], float] = time.monotonic,
    dormir: Callable[[float], None] = time.sleep,
) -> Passage:
    """Le côté Maestro d'un passage : l'objectif confié par le fil, le run suivi jusqu'à son issue.

    Le parcours de S9 et S12, à deux différences près : la personne répond aux
    questions d'outillage par ses choix (`Objectif.repondre`), et elle ne demande
    rien de plus que l'objectif. La durée court du premier message au run soldé ;
    la revue de l'outillage que la fin du run propose est tranchée ensuite, comme
    la personne la verrait arriver.
    """
    racine = atelier.dossier(f"{COTE_MAESTRO}-{rang}")
    ctx = Contexte(
        client=client,
        atelier=atelier,
        juge=juge,
        journal=journal,
        delai_run_s=delai_s,
        horloge=horloge,
        dormir=dormir,
    )
    debut = horloge()
    run_id, duree = "", None
    detail: dict[str, Any] = {}

    def passage(issue: str, motif: str) -> Passage:
        cout, source, postes = _cout_de_maestro(client, ctx.projet_id, detail)
        return Passage(
            cote=COTE_MAESTRO,
            rang=rang,
            issue=issue,
            motif=motif,
            duree_s=duree if duree is not None else horloge() - debut,
            cout_usd=cout,
            cout_source=source,
            cout_postes=postes,
            gestes=tuple(client.gestes),
            livrable=str(racine),
            run_id=run_id,
            projet_id=ctx.projet_id,
            etapes=tuple(journal.etapes),
        )

    try:
        conversation = client.ouvrir_conversation()
        non_ne = _naitre_dans_l_atelier(
            ctx,
            conversation,
            objectif.message,
            racine,
            "le banc comparatif",
            montage=client.montage,
        )
        if non_ne is not None:
            return passage(*_issue_du_lancement(non_ne))
        lance = _lancer_dans_le_fil(
            ctx,
            conversation,
            racine,
            objectif.demande_de_travail,
            versionner=objectif.versionner,
            repondre=objectif.repondre,
        )
        if isinstance(lance, Issue):
            return passage(*_issue_du_lancement(lance))
        run_id = lance.run_id
        detail = _suivre(ctx, run_id, lance.projet_id)
        duree = horloge() - debut
        statut, cause = str(detail.get("statut") or ""), str(detail.get("cause") or "")
        if statut == EXECUTION_TERMINEE:
            _revue_d_apres_le_run(ctx, conversation, run_id, repondre=objectif.repondre)
            return passage(ABOUTI, f"le run s'est terminé ({lance.equipe})")
        if cause == CAUSE_LIMITE_USAGE:
            return passage(EMPECHE, "le fournisseur a opposé sa limite d'usage au run")
        return passage(
            NON_ABOUTI,
            f"le run s'est soldé « {statut or 'sans statut'} » (cause « {cause or '—'} »)",
        )
    except ErreurAPI as echec:
        return passage(EMPECHE, f"l'API a refusé : {echec}")
    except OSError as echec:  # disque, projet devenu illisible
        return passage(EMPECHE, f"le banc n'a pas pu jouer : {echec}")


def _issue_du_lancement(issue: Issue) -> tuple[str, str]:
    """Un lancement qui n'a pas eu lieu : empêché s'il l'était, une perte sinon."""
    return (EMPECHE if issue.empechement else NON_ABOUTI), issue.motif


#: Les postes du grand livre d'un projet que le rapport détaille : ce que Maestro
#: dépense hors des agents — la conduite des runs (#1028) et le fil (#1534). Ce sont
#: les noms des champs de la vue Coûts & analytics, et le rapport les reprend tels quels.
POSTES_DU_GRAND_LIVRE: tuple[str, ...] = ("orchestration", "fil")


def _cout_de_maestro(
    client: ClientAPI, projet_id: str, detail: Mapping[str, Any]
) -> tuple[float | None, str, dict[str, float]]:
    """Ce que le projet a coûté, et ses postes — lu au grand livre du projet, du run à défaut.

    Le total de la vue Coûts & analytics compte, depuis #1534, ce que le fil dépense
    pour le projet et ce que coûtent les tentatives coupées en vol.
    """
    if projet_id:
        try:
            vue = client.couts(projet_id)
        except ErreurAPI:
            vue = {}
        total = vue.get("total")
        if isinstance(total, Mapping) and total.get("cout_usd") is not None:
            return (
                float(total["cout_usd"]),
                "grand livre du projet (Coûts & analytics)",
                _postes(vue),
            )
    if detail.get("cout_usd") is not None:
        return float(detail["cout_usd"]), "grand livre du run", {}
    return None, "non rapporté", {}


def _postes(vue: Mapping[str, Any]) -> dict[str, float]:
    """Le coût de chaque poste que la vue rapporte — un poste sans coût mesuré n'y est pas."""
    postes: dict[str, float] = {}
    for nom in POSTES_DU_GRAND_LIVRE:
        poste = vue.get(nom)
        usage = poste.get("usage") if isinstance(poste, Mapping) else None
        if isinstance(usage, Mapping) and usage.get("cout_usd") is not None:
            postes[nom] = float(usage["cout_usd"])
    return postes


def jouer_seul(
    objectif: Objectif,
    *,
    rang: int,
    atelier: Atelier,
    fournisseur: ModelProvider,
    modele: str,
    journal: Journal,
    delai_s: float = DELAI_PASSAGE_S,
    horloge: Callable[[], float] = time.monotonic,
) -> Passage:
    """Le côté de l'agent seul : le même message, dans un établi vide, par l'adaptateur.

    Aucun prompt système, aucune politique, aucun canal : l'agent reçoit le message et
    les outils des agents de Maestro, et travaille jusqu'à rendre la main. Le délai
    passé, la session est annulée — l'adaptateur arrête ce qu'elle a lancé (#1279).
    """
    etabli = atelier.dossier(f"{COTE_SEUL}-{rang}")
    journal.note("établi de l'agent seul", f"{etabli} — modèle {modele}")
    journal.note("message envoyé", objectif.message)
    debut = horloge()
    with collect_usage() as depense:
        try:
            compte_rendu = asyncio.run(
                asyncio.wait_for(
                    fournisseur.run_agent(
                        objectif.message,
                        model=modele,
                        workspace=etabli,
                        tools=DEFAULT_TOOLS,
                        plafond_tours=None,
                    ),
                    timeout=delai_s,
                )
            )
            issue, motif = ABOUTI, "la session a rendu la main"
            journal.note("compte rendu de l'agent", " ".join(compte_rendu.split())[:600])
        except TimeoutError:
            issue, motif = NON_ABOUTI, f"la session n'a pas fini en {delai_s:g} s"
        except Exception as echec:  # le fournisseur, l'adaptateur : la session a échoué
            if cause_de(echec) == CAUSE_LIMITE_USAGE:
                issue, motif = EMPECHE, f"le fournisseur a opposé sa limite d'usage : {echec}"
            else:
                issue = NON_ABOUTI
                motif = f"la session a échoué ({type(echec).__name__}) : {cause_lisible(echec)}"
    duree = horloge() - debut
    journal.note("session soldée", f"{issue} — {motif}")
    cout = depense.total.cout_usd
    return Passage(
        cote=COTE_SEUL,
        rang=rang,
        issue=issue,
        motif=motif,
        duree_s=duree,
        cout_usd=cout,
        cout_source="rapporté par l'adaptateur" if cout is not None else "non rapporté",
        gestes=(GESTE_MESSAGE,),
        livrable=str(etabli),
        modele=modele,
        etapes=tuple(journal.etapes),
    )


# --- Ce qui se compare ------------------------------------------------------


@dataclass(frozen=True)
class Mesure:
    """Une grandeur sur les passages comptés d'un côté : sa médiane et son étendue."""

    valeurs: tuple[float, ...]

    @property
    def mediane(self) -> float:
        return float(statistics.median(self.valeurs))

    @property
    def minimum(self) -> float:
        return min(self.valeurs)

    @property
    def maximum(self) -> float:
        return max(self.valeurs)

    @property
    def dispersion(self) -> float | None:
        """L'étendue rapportée à la médiane — `None` sur un seul passage, ou une médiane nulle."""
        if len(self.valeurs) < 2 or self.mediane == 0:
            return None
        return (self.maximum - self.minimum) / self.mediane

    def to_dict(self) -> dict[str, Any]:
        return {
            "n": len(self.valeurs),
            "mediane": round(self.mediane, 4),
            "min": round(self.minimum, 4),
            "max": round(self.maximum, 4),
            "dispersion": None if self.dispersion is None else round(self.dispersion, 4),
            "valeurs": [round(v, 4) for v in self.valeurs],
        }


def mesurer(valeurs: Iterable[float | None]) -> Mesure | None:
    """La mesure des valeurs connues — `None` quand aucune ne l'est."""
    connues = tuple(float(v) for v in valeurs if v is not None)
    return Mesure(connues) if connues else None


#: Les grandeurs comparées, dans l'ordre du rapport.
GRANDEUR_DUREE = "duree_s"
GRANDEUR_COUT = "cout_usd"
GRANDEUR_GESTES = "gestes"
GRANDEUR_TENUES = "lignes_tenues"
GRANDEURS: tuple[str, ...] = (GRANDEUR_DUREE, GRANDEUR_COUT, GRANDEUR_GESTES, GRANDEUR_TENUES)


@dataclass(frozen=True)
class Comparatif:
    """Un passage du banc comparatif : l'objectif, et chaque passage de chaque côté."""

    horodatage: str
    objectif: Objectif
    passages: tuple[Passage, ...]
    rejeu: bool = False

    def cotes(self) -> tuple[str, ...]:
        """Les côtés, dans l'ordre où ils ont été joués."""
        vus: list[str] = []
        for passage in self.passages:
            if passage.cote not in vus:
                vus.append(passage.cote)
        return tuple(vus)

    def du_cote(self, cote: str) -> tuple[Passage, ...]:
        return tuple(p for p in self.passages if p.cote == cote)

    def comptes(self, cote: str) -> tuple[Passage, ...]:
        return tuple(p for p in self.du_cote(cote) if p.compte)

    def mesures(self, cote: str) -> dict[str, Mesure | None]:
        """Les grandeurs d'un côté, sur ses passages comptés."""
        comptes = self.comptes(cote)
        return {
            GRANDEUR_DUREE: mesurer(p.duree_s for p in comptes),
            GRANDEUR_COUT: mesurer(p.cout_usd for p in comptes),
            GRANDEUR_GESTES: mesurer(None if p.gestes is None else len(p.gestes) for p in comptes),
            GRANDEUR_TENUES: mesurer(
                None if p.grille is None else p.grille.tenues for p in comptes
            ),
        }

    @property
    def joue(self) -> bool:
        """Au moins un passage a-t-il été compté ?"""
        return any(p.compte for p in self.passages)

    def to_dict(self) -> dict[str, Any]:
        return {
            "horodatage": self.horodatage,
            "objectif": {
                "nom": self.objectif.nom,
                "titre": self.objectif.titre,
                "message": self.objectif.message,
                "choix": list(self.objectif.choix),
                "axes": list(self.objectif.axes),
            },
            "rejeu": self.rejeu,
            "cotes": {
                cote: {
                    "passages": len(self.du_cote(cote)),
                    "comptes": len(self.comptes(cote)),
                    "aboutis": sum(1 for p in self.du_cote(cote) if p.issue == ABOUTI),
                    "mesures": {
                        nom: None if mesure is None else mesure.to_dict()
                        for nom, mesure in self.mesures(cote).items()
                    },
                    "grille": _grille_du_cote(self.comptes(cote), self.objectif.axes),
                }
                for cote in self.cotes()
            },
            "rapports": _rapports(self),
            "passages": [p.to_dict() for p in self.passages],
        }


def _grille_du_cote(passages: Sequence[Passage], axes: Sequence[str]) -> dict[str, dict[str, int]]:
    """Ligne par ligne : combien de passages la tiennent, sur combien qui l'ont jugée."""
    compte: dict[str, dict[str, int]] = {}
    for axe in axes:
        lignes = [p.grille.ligne(axe) for p in passages if p.grille is not None]
        jugees = [ligne for ligne in lignes if ligne is not None and ligne.jugee]
        compte[axe] = {"tenues": sum(1 for ligne in jugees if ligne.tenue), "jugees": len(jugees)}
    return compte


def _rapports(comparatif: Comparatif) -> dict[str, float | None]:
    """Le rapport des médianes, Maestro sur l'agent seul — quand les deux côtés en ont."""
    if COTE_MAESTRO not in comparatif.cotes() or COTE_SEUL not in comparatif.cotes():
        return {}
    maestro, seul = comparatif.mesures(COTE_MAESTRO), comparatif.mesures(COTE_SEUL)
    rapports: dict[str, float | None] = {}
    for nom in GRANDEURS:
        un, autre = maestro[nom], seul[nom]
        rapports[nom] = (
            None
            if un is None or autre is None or autre.mediane == 0
            else round(un.mediane / autre.mediane, 4)
        )
    return rapports


# --- Le rapport -------------------------------------------------------------


def ecrire(comparatif: Comparatif, *, racine: Path | None = None) -> Path:
    """Écrit `rapport.md` et `rapport.json` et rend le dossier du passage."""
    dossier = (racine or RACINE_RAPPORTS) / comparatif.horodatage
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / FICHIER_JSON).write_text(
        json.dumps(comparatif.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (dossier / FICHIER_MARKDOWN).write_text(en_markdown(comparatif), encoding="utf-8")
    return dossier


def en_markdown(comparatif: Comparatif) -> str:
    """Le rapport lisible : les côtés côte à côte, la grille, puis chaque passage."""
    objectif, cotes = comparatif.objectif, comparatif.cotes()
    rapports = _rapports(comparatif)
    entete, separation = (" | maestro / seul |", " | --- |") if rapports else (" |", " |")
    lignes = [
        f"# Banc comparatif — {objectif.titre} — {comparatif.horodatage}",
        "",
        f"Objectif `{objectif.nom}` · "
        + " · ".join(f"{cote} : {len(comparatif.du_cote(cote))} passage(s)" for cote in cotes)
        + (" · livrables rejoués seulement" if comparatif.rejeu else ""),
        "",
        "## Les côtés, côte à côte",
        "",
        "| | " + " | ".join(cotes) + entete,
        "| --- | " + " | ".join("---" for _ in cotes) + separation,
    ]
    lignes.append(
        "| Passages comptés | "
        + " | ".join(
            f"{len(comparatif.comptes(c))}/{len(comparatif.du_cote(c))}, "
            f"{sum(1 for p in comparatif.du_cote(c) if p.issue == ABOUTI)} abouti(s)"
            for c in cotes
        )
        + (" | |" if rapports else " |")
    )
    noms = {
        GRANDEUR_DUREE: ("Durée", _duree),
        GRANDEUR_COUT: ("Coût", _dollars),
        GRANDEUR_GESTES: ("Gestes de la personne", _nombre),
        GRANDEUR_TENUES: (f"Lignes tenues (sur {len(objectif.axes)})", _nombre),
    }
    for grandeur, (libelle, forme) in noms.items():
        cellules = [_mesure_en_mots(comparatif.mesures(c)[grandeur], forme) for c in cotes]
        fin = ""
        if rapports:
            rapport = rapports.get(grandeur)
            fin = f" | {'—' if rapport is None else f'{rapport:.2f}'}"
        lignes.append(f"| {libelle} | " + " | ".join(cellules) + fin + " |")
    lignes += [
        "",
        "Médiane sur les passages comptés, puis l'étendue (du plus petit au plus grand) et "
        "son rapport à la médiane. Un passage empêché n'entre dans aucune médiane.",
        "",
        "## La grille, ligne par ligne",
        "",
        "| Ligne | " + " | ".join(cotes) + " |",
        "| --- | " + " | ".join("---" for _ in cotes) + " |",
    ]
    par_cote = {c: _grille_du_cote(comparatif.comptes(c), objectif.axes) for c in cotes}
    for axe in objectif.axes:
        cellules = [
            f"{par_cote[c][axe]['tenues']}/{par_cote[c][axe]['jugees']}"
            if par_cote[c][axe]["jugees"]
            else "non jugée"
            for c in cotes
        ]
        lignes.append(f"| {LIBELLES[axe]} | " + " | ".join(cellules) + " |")
    lignes += ["", "## Passage par passage", ""]
    for passage in comparatif.passages:
        lignes += _passage_en_markdown(passage)
    return "\n".join(lignes).rstrip() + "\n"


def _passage_en_markdown(passage: Passage) -> list[str]:
    lignes = [
        f"### {passage.cote} · passage {passage.rang} — {passage.issue}",
        "",
        f"- **motif** : {passage.motif}",
        f"- **durée** : {_duree(passage.duree_s)}",
        f"- **coût** : {_dollars(passage.cout_usd)} ({passage.cout_source or '—'})"
        + _postes_en_mots(passage.cout_postes),
    ]
    if passage.gestes is not None:
        detail = _gestes_en_mots(passage.gestes)
        lignes.append(f"- **gestes de la personne** : {len(passage.gestes)}{detail}")
    lignes.append(f"- **livrable** : `{passage.livrable}`")
    if passage.run_id:
        lignes.append(f"- **run** : `{passage.run_id}` · projet `{passage.projet_id}`")
    if passage.modele:
        lignes.append(f"- **modèle** : `{passage.modele}`")
    if passage.grille is not None:
        grille = passage.grille
        lignes += [
            "",
            f"**Grille** — {grille.tenues}/{grille.jugees} ligne(s) tenue(s), "
            f"clone `{grille.copie}`",
            "",
            "| Ligne | État | Commande | Ce qui le dit |",
            "| --- | --- | --- | --- |",
        ]
        for ligne in grille.lignes:
            commande = f"`{ligne.commande}`" if ligne.commande else "—"
            lignes.append(
                f"| {LIBELLES[ligne.axe]} | {ligne.etat} | {commande} | "
                f"{_cellule(ligne.detail)} |"
            )
    if passage.etapes:
        lignes += ["", "<details><summary>Déroulé</summary>", ""]
        lignes += [
            f"- {etape.libelle}" + (f" — {_cellule(etape.detail)}" if etape.detail else "")
            for etape in passage.etapes
        ]
        lignes += ["", "</details>"]
    lignes.append("")
    return lignes


def _postes_en_mots(postes: Mapping[str, float]) -> str:
    """« , dont fil 1.20 $, orchestration 0.90 $ » — rien sans poste mesuré."""
    if not postes:
        return ""
    return ", dont " + ", ".join(f"{nom} {_dollars(cout)}" for nom, cout in postes.items())


def _gestes_en_mots(gestes: Sequence[str]) -> str:
    """« (1 message, 9 réponses d'outillage, …) » — dans l'ordre où ils ont eu lieu."""
    comptes: dict[str, int] = {}
    for geste in gestes:
        comptes[geste] = comptes.get(geste, 0) + 1
    if not comptes:
        return ""
    return " (" + ", ".join(f"{n} × {geste}" for geste, n in comptes.items()) + ")"


def _mesure_en_mots(mesure: Mesure | None, forme: Callable[[float | None], str]) -> str:
    if mesure is None:
        return "—"
    texte = forme(mesure.mediane)
    if len(mesure.valeurs) > 1:
        dispersion = (
            "" if mesure.dispersion is None else f", étendue {mesure.dispersion:.0%} de la médiane"
        )
        texte += f" ({forme(mesure.minimum)} → {forme(mesure.maximum)}{dispersion})"
    return texte


def _duree(secondes: float | None) -> str:
    if secondes is None:
        return "—"
    minutes, reste = divmod(round(secondes), 60)
    return f"{minutes} min {reste:02d} s" if minutes else f"{reste} s"


def _dollars(montant: float | None) -> str:
    return "—" if montant is None else f"{montant:.2f} $"


def _nombre(valeur: float | None) -> str:
    if valeur is None:
        return "—"
    return f"{valeur:g}"


def _cellule(texte: str, longueur: int = 300) -> str:
    """Un texte dans une cellule de tableau : une ligne, sans barre verticale, borné."""
    une = " ".join(texte.split()).replace("|", "\\|")
    return une if len(une) <= longueur else f"{une[: longueur - 1]}…"


# --- La ligne de commande ---------------------------------------------------


@dataclass
class _Options:
    objectif: str = OBJECTIF_DEFAUT
    passages: int = 1
    cotes: list[str] = field(default_factory=list)
    delai_s: float = DELAI_PASSAGE_S
    axes: tuple[str, ...] | None = None
    rejouer: list[tuple[str, Path]] = field(default_factory=list)
    liste: bool = False


def main(
    argv: Sequence[str] | None = None,
    *,
    client: ClientCompteur | None = None,
    fournisseur: ModelProvider | None = None,
    modele: str | None = None,
    lecteur: Lecteur | None = None,
    atelier: Atelier | None = None,
    poste: PosteDuBanc | None = None,
    racine_rapports: Path | None = None,
    horloge: Callable[[], float] = time.monotonic,
    dormir: Callable[[float], None] = time.sleep,
    sortie: TextIO | None = None,
    erreur: TextIO | None = None,
) -> int:
    """Point d'entrée : voir l'en-tête du module pour les options et les codes.

    `client`, `fournisseur`, `modele`, `lecteur`, `atelier`, `poste`,
    `racine_rapports`, `horloge` et `dormir` sont injectables **pour les tests** :
    une fausse API, un faux fournisseur, un faux lecteur, des dossiers jetables,
    aucune attente réelle — la couture de `maestro.scenarios.banc`.
    """
    sortie = sortie or sys.stdout
    erreur = erreur or sys.stderr
    try:
        options = _options(list(sys.argv[1:] if argv is None else argv))
        if options.liste:
            for nom in objectifs_connus():
                print(f"{nom}  {charger_objectif(nom).titre}", file=sortie)
            return CODE_ECRIT
        objectif = charger_objectif(options.objectif)
    except ValueError as refus:
        print(f"{_USAGE}\n  {refus}", file=erreur)
        return CODE_USAGE
    if options.axes is not None:
        objectif = replace(objectif, axes=options.axes)

    cotes = tuple(c for c in COTES if c in options.cotes) if options.cotes else COTES
    if COTE_MAESTRO in cotes and not options.rejouer:
        client = client or ClientCompteur(
            TransportHTTP(base_locale()), delai_modele_s=options.delai_s
        )
        if not client.sante():
            print(
                f"Banc refusé : l'API de la Control Tower ne répond pas sur {base_locale()} — "
                "le côté Maestro passe par le fil réel, il faut donc qu'elle soit allumée.\n"
                f"  Démarrer d'abord : {GESTE_PREALABLE}",
                file=erreur,
            )
            return CODE_API_MUETTE

    atelier = atelier or Atelier.reserver(horodatage_courant())
    juge = JugeModele()
    lecteur = lecteur or juge
    poste = poste or PosteDuBanc(horloge=horloge, dormir=dormir)

    if options.rejouer:
        passages = _rejouer_les_livrables(
            options.rejouer, objectif, atelier, lecteur, poste, sortie
        )
        comparatif = Comparatif(atelier.passage, objectif, passages, rejeu=True)
    else:
        print(_annonce(objectif, cotes, options, atelier), file=sortie)

        def consigner(jusqu_ici: tuple[Passage, ...]) -> None:
            ecrire(Comparatif(atelier.passage, objectif, jusqu_ici), racine=racine_rapports)

        passages = _jouer_les_passages(
            objectif,
            cotes,
            options,
            client=client,
            fournisseur=fournisseur,
            modele=modele,
            lecteur=lecteur,
            juge=juge,
            atelier=atelier,
            poste=poste,
            horloge=horloge,
            dormir=dormir,
            sortie=sortie,
            consigner=consigner,
        )
        comparatif = Comparatif(atelier.passage, objectif, passages)

    dossier = ecrire(comparatif, racine=racine_rapports)
    print(f"\nRapport : {dossier / FICHIER_MARKDOWN}", file=sortie)
    print(
        f"Projets et livrables conservés sous {atelier.racine} — ce sont les pièces du passage.",
        file=sortie,
    )
    return CODE_ECRIT if comparatif.joue else CODE_AUCUN_PASSAGE


def _annonce(objectif: Objectif, cotes: Sequence[str], options: _Options, atelier: Atelier) -> str:
    """L'en-tête du passage : ce qui va se jouer, et ce que cela devrait coûter (#1512)."""
    estimes = [
        f"~{objectif.cout_estime_usd[c]:.0f} $ par passage côté {c}"
        for c in cotes
        if c in objectif.cout_estime_usd
    ]
    total = sum(objectif.cout_estime_usd.get(c, 0.0) for c in cotes) * options.passages
    cout = (
        f"coût attendu {' et '.join(estimes)}, soit ~{total:.0f} $ pour {options.passages} "
        "passage(s) — le vrai modèle"
        if estimes
        else "coût attendu non renseigné — le vrai modèle"
    )
    return (
        f"Banc comparatif — {objectif.titre} · {', '.join(cotes)} · "
        f"{options.passages} passage(s) par côté · délai {options.delai_s:g} s par passage · "
        f"atelier {atelier.racine}\n  {cout}"
    )


def _jouer_les_passages(
    objectif: Objectif,
    cotes: Sequence[str],
    options: _Options,
    *,
    client: ClientCompteur | None,
    fournisseur: ModelProvider | None,
    modele: str | None,
    lecteur: Lecteur,
    juge: Juge,
    atelier: Atelier,
    poste: PosteDuBanc,
    horloge: Callable[[], float],
    dormir: Callable[[float], None],
    sortie: TextIO,
    consigner: Callable[[tuple[Passage, ...]], None] = lambda _passages: None,
) -> tuple[Passage, ...]:
    """Les passages, côtés **alternés** : une dérive du fournisseur pèse sur les deux.

    Le rapport s'écrit **après chaque passage** (`consigner`) : l'agent seul n'a aucun
    garde-fou, et un `taskkill` comme celui de C2 (#1502) tuerait le banc avec lui —
    ce qui a été mesuré avant doit lui survivre.
    """
    passages: list[Passage] = []
    for rang in range(1, options.passages + 1):
        for cote in cotes:
            print(f"— {cote} · passage {rang}/{options.passages}", file=sortie)
            journal = Journal(echo=lambda etape: print(_en_direct(etape), file=sortie))
            if cote == COTE_MAESTRO:
                assert client is not None  # vérifié par `main` avant de jouer
                compteur = _client_neuf(client)
                passage = jouer_maestro(
                    objectif,
                    rang=rang,
                    client=compteur,
                    atelier=atelier,
                    juge=juge,
                    journal=journal,
                    delai_s=options.delai_s,
                    horloge=horloge,
                    dormir=dormir,
                )
            else:
                fournisseur, modele = _fournisseur_et_modele(fournisseur, modele)
                passage = jouer_seul(
                    objectif,
                    rang=rang,
                    atelier=atelier,
                    fournisseur=fournisseur,
                    modele=modele,
                    journal=journal,
                    delai_s=options.delai_s,
                    horloge=horloge,
                )
            print(f"  {passage.issue} — {passage.motif}", file=sortie)
            if passage.compte:
                passage = _rejouer_le_livrable(
                    passage, objectif, atelier, lecteur, poste, journal
                )
            passages.append(passage)
            consigner(tuple(passages))
    return tuple(passages)


def _client_neuf(client: ClientCompteur) -> ClientCompteur:
    """Le même client, ses gestes remis à zéro : chaque passage compte les siens."""
    client.gestes = []
    return client


def _fournisseur_et_modele(
    fournisseur: ModelProvider | None, modele: str | None
) -> tuple[ModelProvider, str]:
    """Le fournisseur et le modèle de l'agent seul — ceux du poste, comme pour Maestro."""
    if fournisseur is not None and modele:
        return fournisseur, modele
    from maestro.config import load_settings
    from maestro.providers.factory import default_model, provider_from_settings

    settings = load_settings()
    fournisseur = fournisseur or provider_from_settings(settings)
    return fournisseur, modele or fournisseur.modele_configure or default_model(settings)


def _rejouer_le_livrable(
    passage: Passage,
    objectif: Objectif,
    atelier: Atelier,
    lecteur: Lecteur,
    poste: PosteDuBanc,
    journal: Journal,
) -> Passage:
    """Le livrable d'un passage rejoué sur un clone propre, et le déroulé qui l'a fait."""
    grille = rejouer(
        Path(passage.livrable),
        atelier.dossier(f"{passage.cote}-{passage.rang}-clone"),
        choix=objectif.choix,
        lecteur=lecteur,
        poste=poste,
        axes=objectif.axes,
        trace=journal.note,
    )
    return passage.avec_grille(grille, journal.etapes)


def _rejouer_les_livrables(
    livrables: Sequence[tuple[str, Path]],
    objectif: Objectif,
    atelier: Atelier,
    lecteur: Lecteur,
    poste: PosteDuBanc,
    sortie: TextIO,
) -> tuple[Passage, ...]:
    """`--rejouer` : la grille seule, sur des livrables qui existent déjà."""
    passages: list[Passage] = []
    rangs: dict[str, int] = {}
    for cote, depot in livrables:
        rangs[cote] = rangs.get(cote, 0) + 1
        print(f"— {cote} · livrable {depot}", file=sortie)
        journal = Journal(echo=lambda etape: print(_en_direct(etape), file=sortie))
        passage = Passage(
            cote=cote, rang=rangs[cote], issue=ABOUTI, motif="livrable rejoué", livrable=str(depot)
        )
        passages.append(_rejouer_le_livrable(passage, objectif, atelier, lecteur, poste, journal))
    return tuple(passages)


#: Ce qu'une étape occupe à l'écran pendant le passage : une ligne.
ETAPE_EN_DIRECT_MAX = 240


def _en_direct(etape: Etape) -> str:
    ligne = f"{etape.libelle} — {etape.detail}" if etape.detail else etape.libelle
    ligne = " ".join(ligne.split())
    if len(ligne) > ETAPE_EN_DIRECT_MAX:
        ligne = ligne[:ETAPE_EN_DIRECT_MAX].rstrip() + "…"
    return f"    · {ligne}"


def _options(args: list[str]) -> _Options:
    """Lit la ligne de commande — lève `ValueError` avec son motif sur tout écart."""
    options = _Options()
    reste = list(args)
    while reste:
        arg = reste.pop(0)
        nom, egal, valeur = arg.partition("=")
        if arg == "--liste":
            options.liste = True
            continue
        if nom not in ("--objectif", "--passages", "--cote", "--delai", "--axes", "--rejouer"):
            raise ValueError(f"argument inconnu : {arg}")
        if not egal:
            if not reste:
                raise ValueError(f"{nom} attend une valeur")
            valeur = reste.pop(0)
        if nom == "--objectif":
            options.objectif = valeur
        elif nom == "--passages":
            options.passages = _entier_positif(nom, valeur)
        elif nom == "--cote":
            for cote in (v.strip() for v in valeur.split(",") if v.strip()):
                if cote not in COTES:
                    raise ValueError(f"--cote attend {' ou '.join(COTES)} (reçu : {cote!r})")
                options.cotes.append(cote)
        elif nom == "--delai":
            options.delai_s = _secondes(valeur)
        elif nom == "--axes":
            axes = tuple(v.strip() for v in valeur.split(",") if v.strip())
            inconnus = [axe for axe in axes if axe not in AXES]
            if not axes or inconnus:
                raise ValueError(f"--axes attend des lignes de la grille parmi : {', '.join(AXES)}")
            options.axes = tuple(axe for axe in AXES if axe in axes)
        else:
            cote, separe, depot = valeur.partition("=")
            if not separe or not cote.strip() or not depot.strip():
                raise ValueError("--rejouer attend <côté>=<dépôt>")
            options.rejouer.append((cote.strip(), Path(depot.strip())))
    return options


def _entier_positif(nom: str, brut: str) -> int:
    try:
        valeur = int(brut)
    except ValueError as exc:
        raise ValueError(f"{nom} attend un entier (reçu : {brut!r})") from exc
    if valeur < 1:
        raise ValueError(f"{nom} doit être ≥ 1 (reçu : {brut!r})")
    return valeur


def _secondes(brut: str) -> float:
    try:
        valeur = float(brut)
    except ValueError as exc:
        raise ValueError(f"--delai attend un nombre de secondes (reçu : {brut!r})") from exc
    if valeur <= 0:
        raise ValueError(f"--delai doit être > 0 (reçu : {brut!r})")
    return valeur


if __name__ == "__main__":
    raise SystemExit(main())

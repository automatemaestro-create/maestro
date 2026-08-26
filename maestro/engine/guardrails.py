"""Garde-fous du POC : plafond de dépense, time-out, validation humaine (ticket #9).

Matérialise les « limites globales » de docs/01 §5 au niveau de la boucle
d'orchestration, en trois protections appliquées à chaque tâche :

- **plafond de dépense** (`plafond_cout_usd` en USD, `plafond_tokens` en tokens) :
  budget de l'**exécution entière** — la tâche qui fait déborder le cumul du run
  est stoppée, et une exécution au budget épuisé n'en démarre plus aucune. Les
  deux seuils sont indépendants et cumulables ; le seuil en tokens reste opérant
  sur un fournisseur qui ne rapporte pas de coût (#113), quand le seuil en USD n'a
  aucune prise. Les garde-fous ne comptent rien eux-mêmes (#56) : ce module ne
  porte que les seuils, le contrôle (`maestro.telemetry.PlafondDepense`) relit la
  comptabilité par tâche de l'exécution (#55) à chaque mesure d'usage —
  `maestro/telemetry` est la source unique du coût comme des tokens ;
- **time-out** (`timeout_s`) : la tâche est annulée si sa réalisation excède le
  délai (l'attente d'une validation humaine n'y est pas comptée) ;
- **validation humaine** : une tâche classée **sensible** (déploiement, production,
  suppression… — cf. `MOTS_SENSIBLES`) déclenche une `DemandeValidation` soumise au
  `validateur` configuré avant toute exécution. **Fail-safe** : sans validateur, ou
  si le validateur échoue, la demande est refusée — l'action sensible ne part
  jamais sans accord humain explicite (EF-08, ENF-04).

`Guardrails()` sans argument laisse plafond et time-out inactifs mais garde la
détection d'actions sensibles (refus par défaut). La classification par mots-clés
est assumée comme heuristique de POC : la V1 la remplacera par une liste d'actions
outillées classées côté serveur (docs/03, entité APPROVAL) sans changer ce contrat.

Depuis #582, ce même canal a un **second producteur** : l'agent peut demander
l'arbitrage lui-même (`maestro.providers.arbitrage`). Rien du mécanisme ci-dessus
n'a bougé pour l'accueillir — c'est une `DemandeValidation` de plus, soumise au
même `validateur`, donc frappée du même fail-safe : **sans validateur, elle est
refusée**. Ce qui la distingue est son `origine`, et cette distinction compte :
la classification est faite par nous et tient quand l'agent se trompe ou se fait
manipuler, sa demande n'est qu'un canal **de plus** dont le silence ne prouve
rien.

Depuis #586, ce canal a aussi **trois portes** au lieu d'une, et c'est le
`decideur` de la demande qui dit laquelle (`maestro.decideur`) :

- `auto` — personne n'est sollicité, la demande est accordée d'office. Le cran
  de ce qu'on veut **voir** sans vouloir l'arrêter : la décision est consignée
  comme les autres, aucun validateur n'est appelé ;
- `orchestrateur` — la machine tranche seule, par `orchestrateur` ;
- `humain` — le `validateur`, et lui seul. C'est le défaut, y compris pour une
  demande qui ne dit rien de son décideur.

L'asymétrie d'EF-08/ENF-04 tient au **routage** et non à un contrôle qu'on
aurait pu oublier d'écrire : sur le cran `humain`, `orchestrateur` n'est pas sur
le chemin — il n'est pas consulté, donc son avis, fût-il « oui », ne peut pas
devenir une approbation. Et le fail-safe vaut porte par porte : pas
d'orchestrateur pour un acte qui lui revient, pas de validateur pour un acte
humain, l'un ou l'autre en panne — refus, toujours.

À côté d'eux, et pour la même raison — ce sont les **limites du run**, elles
n'ont pas à vivre dans un troisième endroit —, `GardeFousIngestion` (#315,
ENF-07) plafonne la **matière d'entrée** d'un objectif : taille par source,
taille totale, nombre de sources. Ils sont appliqués **avant** la boucle, au
lancement (`maestro.sources.resolution`), là où l'utilisateur peut encore
retirer une pièce jointe.
"""

from __future__ import annotations

import asyncio
import inspect
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from maestro.decideur import DECIDEUR_DEFAUT, Decideur, decideur_depuis
from maestro.orchestrator.schema import Task

if TYPE_CHECKING:  # pragma: no cover - annotation seule (cf. `DemandeValidation.diff`)
    from maestro.projets.application import DiffProjet

#: Mots-clés (normalisés sans accents) classant une tâche comme sensible, d'après
#: les fiches de rôles (docs/04 : déploiement, migration destructive, suppression).
#: Des radicaux plutôt que des mots entiers : « deploi » couvre déploiement/déploie,
#: « supprim » couvre supprimer/suppression, « destructi » destructive/destructif.
MOTS_SENSIBLES: tuple[str, ...] = (
    "deploi",
    "deploy",
    "production",
    "supprim",
    "destructi",
    "drop table",
    "truncate",
    "secret",
    "mot de passe",
    "credential",
)

#: Taille maximale d'**une** source, en octets (10 Mio). Le plafond porte sur ce
#: qui entre, en octets, parce que c'est tout ce que ce niveau connaît : le
#: rapport octets → tokens dépend du format (un PDF de 10 Mio rend quelques
#: dizaines de kilo-octets de texte, un `.txt` de 10 Mio en rend dix mégas, soit
#: ~2,6 M tokens — treize fois une fenêtre de contexte). C'est donc une barrière
#: **grossière**, celle qui arrête l'absurde ; le plafond fin, en tokens, revient
#: à l'extraction (#316), seule à connaître le texte.
TAILLE_MAX_SOURCE_OCTETS = 10 * 1024 * 1024

#: Taille maximale de **l'ensemble** des sources d'un objectif (50 Mio) : sans
#: elle, cinquante fichiers sous le plafond unitaire passeraient tous.
TAILLE_MAX_INGESTION_OCTETS = 50 * 1024 * 1024

#: Nombre maximal de sources d'un objectif. Une borne de bon sens plus qu'un
#: coût : au-delà, ce n'est plus un objectif composé, c'est un dossier — et un
#: dossier a son propre type de source.
NB_MAX_SOURCES = 20

#: Provenance d'une demande de validation : **Maestro l'a déduite** (#582).
#: C'est le régime historique et le défaut — la classification d'une tâche
#: sensible (`Guardrails.raison_sensible`) comme la demande d'application dans
#: un projet (#227) naissent d'une règle à nous, jamais d'un aveu de l'agent.
ORIGINE_POLITIQUE = "politique"

#: Provenance d'une demande de validation : **l'agent l'a demandée lui-même**
#: (#582, `maestro.providers.arbitrage`). Un canal *de plus* : son silence ne
#: dispense d'aucune classification, et une demande qui porte cette origine ne
#: prouve rien de celles qui ne l'ont pas.
#:
#: La distinction est portée par un **champ** et non déduite du texte de la
#: raison : les deux provenances n'ont pas la même valeur de preuve, et laisser
#: quelqu'un la retrouver à la grammaire d'une phrase, c'est la perdre au
#: premier reformulage.
ORIGINE_AGENT = "agent"

#: Le détail traçable d'une demande accordée par le cran `auto` (#586) — celui
#: qui dit, à qui relira le journal, qu'aucune personne n'a été dérangée. C'est
#: la seule issue « approuvée » que personne n'a prononcée, et la taire ferait
#: lire un accord humain là où il n'y en a jamais eu.
DETAIL_AUTO = "accordée d'office — décideur « auto », personne n'a été sollicité"


@dataclass(frozen=True)
class GardeFousIngestion:
    """Plafonds de la matière d'entrée d'un objectif (#315, EF-39, ENF-07). Immuable.

    Trois seuils, tous **réglables** et tous **actifs par défaut** — c'est la
    différence assumée avec `Guardrails`, dont les plafonds sont inactifs à None.
    Un plafond de dépense absent laisse un run coûter ce qu'il coûte, ce qui se
    voit sur la barre ; un plafond d'ingestion absent laisse un document entrer
    **intégralement** dans le contexte, et alors « la barre de dépense ment »
    ([docs/24 §3.4](../../docs/24-projets-locaux-et-poste-de-travail.md)). Le
    défaut sûr est donc de plafonner, quitte à ce qu'on desserre.

    `None` reste possible sur chaque champ et vaut « aucun plafond » : c'est le
    réglage qu'on pose sciemment, jamais celui qu'on obtient en oubliant.

    Un dépassement n'est jamais accepté en silence : la résolution lève une
    `SourceRefusee` **motivée** (`source-trop-volumineuse`,
    `ingestion-trop-volumineuse`, `trop-de-sources`), que la route rend en 422.
    """

    taille_max_source_octets: int | None = TAILLE_MAX_SOURCE_OCTETS
    taille_max_totale_octets: int | None = TAILLE_MAX_INGESTION_OCTETS
    nb_max_sources: int | None = NB_MAX_SOURCES

    def __post_init__(self) -> None:
        for nom, valeur in (
            ("taille_max_source_octets", self.taille_max_source_octets),
            ("taille_max_totale_octets", self.taille_max_totale_octets),
            ("nb_max_sources", self.nb_max_sources),
        ):
            if valeur is not None and valeur <= 0:
                raise ValueError(f"{nom} doit être > 0 (reçu : {valeur}).")


@dataclass(frozen=True)
class DemandeValidation:
    """Demande de validation humaine sur une action sensible (docs/03, entité APPROVAL).

    Porte tout ce qu'un humain doit voir pour trancher : la tâche (id, titre,
    description), l'agent qui l'exécuterait, et la `raison` pour laquelle elle a
    été classée sensible.

    `run_id` et `projet_id` (#570) disent **d'où elle vient**, et ce n'est pas du
    contexte d'agrément : ce sont les deux critères de filtre de la Control Tower.
    Sans eux, l'événement `validation.demande` sort du run (absent de son journal)
    et du projet (écarté de toutes les vues, qui sont cadrées dessus) — mesuré le
    2026-08-26 (#568) : trois demandes bloquant un run, aucune affichée nulle part,
    et l'écran affirmant « aucune validation en attente » pendant treize minutes.

    On ne pouvait pas les déduire à l'arrivée, et c'est ce qui distingue ce champ
    d'un confort : la projection les cherchait sur la tâche déjà connue, or une
    validation qui **garde le démarrage de sa propre tâche** est publiée avant que
    cette tâche n'existe pour qui que ce soit. Le repli est en aval de ce qu'il
    répare ; il reste en place, pour les producteurs qui ne portent rien, mais il
    n'est plus la source.

    Les deux restent **optionnels** : une demande peut naître hors d'un run ou hors
    d'un projet, et les rendre obligatoires ferait échouer un appelant légitime là
    où l'absence se lit très bien (`""` / `None`).

    `diff` (#227, EF-37) est la **pièce jointe** d'une demande d'application dans
    le projet de l'utilisateur : les fichiers touchés et leurs lignes
    ajoutées/supprimées, ce sans quoi « appliquer ces modifications ? » n'est pas
    une question qu'on peut trancher. None pour toutes les autres actions
    sensibles, qui se décrivent en texte. L'annotation est différée (`TYPE_CHECKING`)
    pour garder ce module de garde-fous indépendant des projets à l'exécution :
    c'est `maestro.projets.application` qui dépend de lui, jamais l'inverse.

    `outil` et `arguments` (#581) portent l'**acte** qui a déclenché la demande —
    la pièce jointe de l'arbitrage sur l'acte, comme `diff` l'est de l'application
    dans le projet. Ils existent parce que le chantier #573 déplace le déclencheur
    du texte de la tâche vers ce que l'agent s'apprête à commettre : dès lors,
    « Rédiger le README » n'est plus ce qu'un humain doit lire pour trancher un
    `rm -rf`, c'est l'outil appelé et ce qu'on lui passe.

    Les deux sont **optionnels**, et le lot qui les introduit ne les remplit
    encore nulle part : une demande qui n'en porte pas — validation d'une tâche
    (#48), application d'un diff (#227) — reste valide et voyage exactement comme
    avant. `outil` vide est ce qui dit « cette demande ne porte pas d'acte » ;
    `arguments` à None dit qu'on n'en connaît aucun, ce qu'un outil sans paramètre
    partage avec un producteur qui ne les rapporte pas — la distinction n'aurait
    servi à personne, et un dict par défaut demanderait un `default_factory` pour
    la même valeur. Leur forme est celle de `maestro.acte` : du texte, clé par
    clé, chaque valeur bornée.

    `origine` (#582) dit **qui a demandé** : `ORIGINE_POLITIQUE`, le défaut —
    Maestro l'a déduite d'une de ses règles — ou `ORIGINE_AGENT` — l'agent a
    levé la main lui-même. Les deux ne valent pas la même chose et c'est tout
    l'intérêt de les distinguer : une demande déduite tient même quand l'agent
    se trompe ou se fait manipuler, une demande d'agent ne prouve que ce qu'il
    a bien voulu dire. Sans le champ, le journal rendrait les deux
    indiscernables, et une déclaration d'agent finirait par se lire comme une
    classification.

    `decideur` (#586) dit **qui doit trancher** : `auto`, `orchestrateur` ou
    `humain` (`maestro.decideur`). À ne pas confondre avec `origine`, qui est
    la question d'à côté et d'avant — celle-là dit *qui a demandé*, celle-ci
    *qui répond*, et les deux se combinent librement (un agent peut lever la
    main sur un acte qui revient à l'orchestrateur, notre classification peut
    désigner un humain).

    Le défaut est `humain`, et c'est le critère du ticket plutôt qu'un choix de
    commodité : un producteur qui ne dit rien de son décideur escalade, il ne
    s'auto-approuve pas. Les deux demandes qui existaient avant ce lot — une
    tâche classée sensible (#9), une application de diff (#227) — restent donc
    exactement ce qu'elles étaient, sans porter le champ.
    """

    task_id: str
    titre: str
    description: str
    agent: str
    role: str
    raison: str
    diff: DiffProjet | None = None
    run_id: str = ""
    projet_id: str | None = None
    outil: str = ""
    arguments: dict[str, str] | None = None
    origine: str = ORIGINE_POLITIQUE
    decideur: str = DECIDEUR_DEFAUT


#: Validateur humain : reçoit la demande, répond vrai (approuvée) ou faux (refusée).
#: Synchrone ou asynchrone — le moteur attend le résultat dans les deux cas.
Validateur = Callable[[DemandeValidation], bool | Awaitable[bool]]


@dataclass(frozen=True)
class Guardrails:
    """Garde-fous appliqués par la boucle à chaque tâche. Immuable.

    `plafond_cout_usd` (budget en USD) et `plafond_tokens` (budget en tokens),
    tous deux contrôlés via la comptabilité par tâche (#56) et inactifs à None,
    plafonnent l'exécution entière ; le seuil en tokens reste opérant sur un
    fournisseur sans coût rapporté (#113). `timeout_s` (par tâche) est inactif à
    None ; `mots_sensibles` pilote la classification (vide = détection désactivée) ;
    `validateur` est le canal de la décision humaine — absent, toute action
    sensible est refusée (fail-safe).

    `orchestrateur` (#586) est le canal du **cran du milieu** : ce que la
    machine tranche seule, sans réveiller personne, quand la politique l'a
    explicitement classé ainsi. Absent, un acte qui lui revient est refusé — même
    fail-safe que le validateur humain, et pour la même raison : ne trouver
    personne à qui demander n'a jamais autorisé une action sensible.

    Il n'est **jamais** consulté sur un acte classé `humain`. Ce n'est pas une
    règle qu'on applique, c'est un chemin qu'il n'emprunte pas
    (`demande_validation`) : un orchestrateur qui approuverait tout ne peut rien
    approuver de ce qui appartient à une personne, parce qu'on ne lui pose pas
    la question.
    """

    plafond_cout_usd: float | None = None
    plafond_tokens: int | None = None
    timeout_s: float | None = None
    validateur: Validateur | None = None
    orchestrateur: Validateur | None = None
    mots_sensibles: tuple[str, ...] = MOTS_SENSIBLES

    def __post_init__(self) -> None:
        if self.plafond_cout_usd is not None and self.plafond_cout_usd <= 0:
            raise ValueError(
                f"plafond_cout_usd doit être > 0 (reçu : {self.plafond_cout_usd})."
            )
        if self.plafond_tokens is not None and self.plafond_tokens <= 0:
            raise ValueError(
                f"plafond_tokens doit être > 0 (reçu : {self.plafond_tokens})."
            )
        if self.timeout_s is not None and self.timeout_s <= 0:
            raise ValueError(f"timeout_s doit être > 0 (reçu : {self.timeout_s}).")

    def raison_sensible(self, task: Task) -> str | None:
        """Classe la tâche : la raison si elle est sensible, None sinon.

        Heuristique POC : recherche des mots-clés dans le titre et la description,
        après normalisation (minuscules, accents retirés) pour tolérer les variantes.
        """
        texte = _normalise(f"{task.titre} {task.description}")
        for mot in self.mots_sensibles:
            if _normalise(mot) in texte:
                return f"mot sensible « {mot} » détecté dans la tâche"
        return None

    async def demande_validation(self, demande: DemandeValidation) -> tuple[bool, str]:
        """Soumet la demande à **son** décideur ; renvoie (approuvée ?, détail traçable).

        Trois portes, et c'est `demande.decideur` qui dit laquelle (#586) — le
        cran étant posé dans la politique, à froid, jamais déduit ici :

        - `auto` : accordée **d'office**, sans qu'aucun canal soit appelé. Elle
          n'en est pas moins tracée : c'est ce qui distingue ce cran d'un
          `allow`, où l'appel passe en silence ;
        - `orchestrateur` : soumise à `self.orchestrateur` ;
        - `humain` (le défaut) : soumise à `self.validateur`, comme depuis #9.

        Fail-safe, inchangé et désormais porte par porte : sans le canal qui
        correspond au cran, ou si ce canal lève une exception, la demande est
        **refusée** — jamais d'action sensible sans accord explicite. Le détail
        est destiné au journal et au message d'erreur de la tâche, et **nomme
        qui a tranché** : c'est ce qui rend la décision lisible là où elle est
        consignée, le champ restant la source.

        ⚠ Sur le cran `humain`, `self.orchestrateur` n'est pas appelé — pas
        « appelé puis ignoré ». La différence est tout le garde-fou : il n'y a
        aucun endroit où une approbation d'orchestrateur pourrait être prise
        pour une approbation humaine, fût-ce par erreur de relecture.
        """
        decideur = decideur_depuis(demande.decideur)
        if decideur is Decideur.AUTO:
            return True, DETAIL_AUTO
        if decideur is Decideur.ORCHESTRATEUR:
            return await _tranche(
                self.orchestrateur, demande, nom="orchestrateur", par="l'orchestrateur"
            )
        return await _tranche(
            self.validateur, demande, nom="validateur humain", par="le validateur humain"
        )


async def _tranche(
    canal: Validateur | None, demande: DemandeValidation, *, nom: str, par: str
) -> tuple[bool, str]:
    """Soumet `demande` à `canal` et rend (approuvée ?, détail) — **fail-safe compris**.

    Le corps de `demande_validation` d'avant #586, extrait tel quel pour servir
    les deux portes qui existent désormais : le validateur humain et
    l'orchestrateur. L'extraction n'est pas cosmétique — deux copies de ce
    fail-safe seraient deux endroits où l'oublier, et c'est précisément le genre
    de règle dont on découvre l'absence après coup.

    Les textes du cran humain sont **inchangés au caractère près** (« aucun
    validateur humain configuré — refus par défaut », « approuvée par le
    validateur humain »…) : ils sont lus par des tests, par le journal et par
    l'agent lui-même, dont le comportement diffère selon le motif
    (`maestro.providers.arbitrage.reponse`).
    """
    if canal is None:
        return False, f"aucun {nom} configuré — refus par défaut"
    try:
        decision: Any
        if inspect.iscoroutinefunction(canal):
            decision = await canal(demande)
        else:
            # Canal **synchrone** (console #9 : `input()`) : exécuté hors de la
            # boucle d'événements. Appelé directement, il la bloquerait tout le
            # temps de la délibération humaine — anodin en local, mais fatal en
            # mode durable (#96), où la boucle doit continuer à battre le cœur
            # des activités : un worker qui ne bat plus est réputé mort et sa
            # tâche relancée sous 30 s, en pleine question à l'opérateur.
            decision = await asyncio.to_thread(canal, demande)
            if inspect.isawaitable(decision):
                decision = await decision
    except Exception as exc:  # fail-safe : un canal en panne ne laisse rien passer
        return False, f"{nom} en erreur ({exc}) — refus par défaut"
    if decision:
        return True, f"approuvée par {par}"
    return False, f"refusée par {par}"


def _normalise(texte: str) -> str:
    """Minuscules et accents retirés — la forme sous laquelle les mots-clés matchent."""
    decompose = unicodedata.normalize("NFKD", texte.lower())
    return "".join(c for c in decompose if not unicodedata.combining(c))

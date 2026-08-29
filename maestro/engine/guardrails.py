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
- **validation humaine** : une action classée **sensible** déclenche une
  `DemandeValidation` soumise au `validateur` configuré avant toute exécution.
  **Fail-safe** : sans validateur, ou si le validateur échoue, la demande est
  refusée — l'action sensible ne part jamais sans accord humain explicite
  (EF-08, ENF-04).

**Ce qui déclenche un arbitrage (#585).** Le déclencheur est l'**acte que l'agent
s'apprête à commettre**, et non le texte de ce qu'on lui demande d'écrire. Trois
producteurs, un seul canal : un outil classé `ask` par la politique de l'agent
(`maestro.agents.permissions.PolitiqueOutils`, #580) suspend l'appel au hook
`PreToolUse` (#583) et compose une demande portant l'outil et ses arguments
(#581) ; l'agent peut lever la main lui-même (#582) ; et l'application d'un diff
dans le projet de l'utilisateur (#227) reste soumise au même accord. Tous
passent par `demande_validation`, donc par le même fail-safe.

**Ce qui reste du régime par mots-clés.** Le mécanisme est intact —
`raison_sensible` et `MOTS_SENSIBLES` n'ont pas bougé — mais il n'est plus
**armé** : `mots_sensibles` est **vide par défaut** depuis #585, et le régime
d'avant s'obtient en la renseignant (`Guardrails(mots_sensibles=MOTS_SENSIBLES)`,
ou une liste à soi). Le motif du désarmement est mesuré (#568) : le mot vient du
**brief** et se propage à toutes les descriptions que la décomposition en tire,
si bien qu'un objectif demandant « une sous-commande **supprimer** une note »
rendait **3 tâches sur 3** sensibles — « Rédiger le README » comprise. Développer
une fonction de suppression n'est pas exécuter une suppression. Désarmer ne
retire donc pas un garde-fou, ça retire un déclencheur qui jugeait le mauvais
objet — et ce lot vient **après** que l'arbitrage sur l'acte est vivant, jamais
avant.

**Ces producteurs ne se valent pas, et le champ `origine` (#582) est ce qui les
départage.** Rien du mécanisme n'a bougé pour accueillir l'agent qui lève la main
(`maestro.providers.arbitrage`) — c'est une `DemandeValidation` de plus, soumise
au même `validateur`, donc frappée du même fail-safe : **sans validateur, elle
est refusée**. Mais une demande que **nous** avons déduite (un outil classé
`ask`, un diff à appliquer) tient quand l'agent se trompe ou se fait manipuler,
là où la sienne n'est qu'un canal **de plus** dont le silence ne prouve rien.
C'est aussi pourquoi désarmer les mots-clés ne déplace pas le garde-fou vers
l'agent : le déclencheur nominal reste une règle à nous, appliquée à l'acte.

Depuis #586, ce canal a aussi **deux portes** au lieu d'une, et c'est le
`decideur` de la demande qui dit laquelle (`maestro.decideur`) :

- `auto` — personne n'est sollicité, la demande est accordée d'office. Le cran
  de ce qu'on veut **voir** sans vouloir l'arrêter : la décision est consignée
  comme les autres, aucun validateur n'est appelé ;
- `humain` — le `validateur`, et lui seul. C'est le défaut, y compris pour une
  demande qui ne dit rien de son décideur — ou qui porte un cran qu'on ne sait
  plus relire.

Il y avait une troisième porte, `orchestrateur`, retirée par #715 (décision de
cadrage #647, [docs/31](../../docs/31-decision-cran-orchestrateur.md)) : aucun
appelant de production n'a jamais fourni le canal, si bien qu'un acte qui lui
était classé rendait invariablement un refus. L'asymétrie d'EF-08/ENF-04 en sort
**plus forte** — elle tenait au **routage** (sur le cran `humain`, le canal
machine n'était pas sur le chemin, donc son avis, fût-il « oui », ne pouvait pas
devenir une approbation) ; elle n'a désormais plus de sujet du tout : *il n'existe
aucun canal machine sur aucun chemin*. Une propriété qu'on ne peut pas violer
faute de sujet est plus forte qu'une propriété tenue par un routage correct.

Le fail-safe, lui, n'a pas bougé : pas de validateur pour un acte humain, ou un
validateur en panne — refus, toujours.

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
#:
#: ⚠ **Ce n'est plus le défaut de `Guardrails.mots_sensibles`** depuis #585, qui
#: est vide : cette liste est ce qu'on **passe** pour retrouver l'ancien régime.
#: Elle est conservée nommée plutôt que recopiée par chaque appelant — le jour où
#: quelqu'un veut ce régime, il vaut mieux qu'il reprenne les radicaux éprouvés
#: que sa propre approximation.
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

    `decideur` (#586) dit **qui doit trancher** : `auto` ou `humain`
    (`maestro.decideur` — un troisième cran, `orchestrateur`, a été retiré par
    #715). À ne pas confondre avec `origine`, qui est la question d'à côté et
    d'avant — celle-là dit *qui a demandé*, celle-ci *qui répond*, et les deux se
    combinent librement (un agent peut lever la main sur un acte que la politique
    laisse passer, notre classification peut désigner un humain).

    ⚠ Le champ porte de la **donnée durable** et ne se réduit pas à l'ensemble
    admissible d'une politique : le journal est rejoué, et des événements déjà
    émis portent la chaîne `"orchestrateur"`. C'est le routage qui a été retiré,
    jamais la mémoire de ce qui a été décidé — `decideur_depuis` relit ces
    valeurs-là vers `humain`, le cran le plus fermé.

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
    None ; `validateur` est le canal de la décision humaine — absent, toute
    action sensible est refusée (fail-safe).

    ⚠ Un second canal `orchestrateur` a existé ici (#586), pour le **cran du
    milieu** — *ce que la machine tranche seule*. Il est parti avec son cran
    (#715, décision #647) : **aucun des cinq sites de production** qui montent un
    `Guardrails` ne l'a jamais fourni, si bien que le fail-safe le refusait à
    chaque fois. Ne pas le rétablir sans rouvrir la décision (docs/31 §8) : il n'y
    a désormais **aucun canal machine** sur aucun chemin de `demande_validation`,
    et c'est ce qui rend l'invariant d'EF-08 insurpassable plutôt que bien routé.

    `mots_sensibles` pilote la classification par mots-clés et est **vide par
    défaut** (#585) : elle ne se déclenche donc plus d'elle-même, l'arbitrage
    naissant de l'**acte** (cf. la docstring du module). La renseigner rearme
    l'ancien régime, tâche par tâche et sur le seul texte du titre et de la
    description — `Guardrails(mots_sensibles=MOTS_SENSIBLES)` pour les radicaux
    d'origine.

    Le défaut vide n'est pas du même ordre que les trois `None` au-dessus, et
    c'est voulu : un plafond absent laisse passer ce qu'on n'a pas voulu borner,
    là où cette liste absente ne retire aucun contrôle — elle retire un
    déclencheur que le hook `PreToolUse` a remplacé par un meilleur. Le
    fail-safe, lui, est ailleurs et n'a pas bougé : il porte sur `validateur` et
    frappe toute demande qui atteint `demande_validation`, quel qu'en soit le
    producteur.
    """

    plafond_cout_usd: float | None = None
    plafond_tokens: int | None = None
    timeout_s: float | None = None
    validateur: Validateur | None = None
    mots_sensibles: tuple[str, ...] = ()

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

        **Rend None pour toute tâche tant que `mots_sensibles` n'est pas
        renseignée**, ce qui est le cas par défaut depuis #585 : la boucle
        continue de l'appeler à chaque tâche, elle ne classe simplement plus rien.
        Le déclencheur nominal est l'acte (cf. la docstring du module) ; ceci
        reste le régime de qui veut arbitrer sur l'énoncé, en connaissance de sa
        limite — le mot cherché vient souvent du **brief**, donc de toutes les
        tâches qu'on en a tirées, et non de ce que la tâche fera.
        """
        texte = _normalise(f"{task.titre} {task.description}")
        for mot in self.mots_sensibles:
            if _normalise(mot) in texte:
                return f"mot sensible « {mot} » détecté dans la tâche"
        return None

    async def demande_validation(self, demande: DemandeValidation) -> tuple[bool, str]:
        """Soumet la demande à **son** décideur ; renvoie (approuvée ?, détail traçable).

        Deux issues, et c'est `demande.decideur` qui dit laquelle (#586) — le
        cran étant posé dans la politique, à froid, jamais déduit ici :

        - `auto` : accordée **d'office**, sans qu'aucun canal soit appelé. Elle
          n'en est pas moins tracée : c'est ce qui distingue ce cran d'un
          `allow`, où l'appel passe en silence ;
        - **tout le reste** : soumise à `self.validateur`, comme depuis #9. C'est
          le défaut `humain`, et c'est aussi où retombe un cran qu'on ne sait plus
          relire — l'ancien `orchestrateur` compris (#715).

        Fail-safe, inchangé : sans validateur, ou si ce canal lève une exception,
        la demande est **refusée** — jamais d'action sensible sans accord
        explicite. Le détail est destiné au journal et au message d'erreur de la
        tâche, et **nomme qui a tranché** : c'est ce qui rend la décision lisible
        là où elle est consignée, le champ restant la source.

        ⚠ La condition est **`is AUTO`**, jamais « n'est pas `HUMAIN` » : elle
        nomme la seule issue qui approuve sans personne, et tout ce qui n'est pas
        exactement elle escalade. Écrite à l'envers, un cran retiré demain — ou une
        valeur qu'on ne sait pas relire — deviendrait un laissez-passer par le
        simple fait de ne plus être reconnu.
        """
        if decideur_depuis(demande.decideur) is Decideur.AUTO:
            return True, DETAIL_AUTO
        return await _tranche(
            self.validateur, demande, nom="validateur humain", par="le validateur humain"
        )


async def _tranche(
    canal: Validateur | None, demande: DemandeValidation, *, nom: str, par: str
) -> tuple[bool, str]:
    """Soumet `demande` à `canal` et rend (approuvée ?, détail) — **fail-safe compris**.

    Le corps de `demande_validation` d'avant #586, extrait tel quel pour servir
    les deux portes d'alors : le validateur humain et l'orchestrateur.

    ⚠ **Il reste extrait bien qu'il ne serve plus qu'un appelant** (#715), et
    c'est délibéré : ce qu'il porte est le **fail-safe** — canal absent ou canal
    en panne ⇒ refus —, c'est-à-dire la règle dont deux copies seraient deux
    endroits où l'oublier. Le remettre en ligne parce qu'il n'y a plus qu'une
    porte défairait la raison même de son extraction, et rendrait au passage la
    règle indissociable du routage qui l'appelle. Un fail-safe se lit mieux seul,
    et il se teste seul.

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

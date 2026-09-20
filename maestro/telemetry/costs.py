"""Comptabilité de coût par tâche — le grand livre d'une exécution (ticket #55).

Le journal (#8) consigne déjà chaque étape avec son usage (tokens, coût, durée) ;
cette brique le **réorganise en comptabilité** : une entrée par tâche
(`TaskCost`), l'étape de planification à part, et l'agrégat de l'exécution
(`RunCost`) — la vue « coût de l'exécution, traçable par tâche » du critère MVP
n°6 (parent #49), que l'API Control Tower exposera (#57) et sur laquelle le
plafond de dépense s'adosse (`PlafondDepense`, #56) : le garde-fou (#9) relit ce
grand livre à chaque mesure d'usage, sans compteur parallèle.

L'attribution suit la convention d'étape du journal : `planification` et `brief`
(#318) pour l'orchestrateur, `<tache>` pour l'étape de la tâche elle-même, et
`<tache>:<annexe>` pour ses étapes annexes (validation humaine #48, message
inter-agents #44) — chaque annexe est rattachée à sa tâche. Chaque ligne du
journal est ainsi comptée exactement une fois : le total de la comptabilité
retombe sur `RunJournal.usage_totale`.

Cette liste d'étapes hors tâche est **fermée par le code, pas par convention** :
la règle par défaut est « toute autre étape est une tâche », donc une étape de run
qu'on oublierait d'y déclarer n'échouerait pas — elle ouvrirait une ligne de tâche
fantôme dans le grand livre, ce qui est bien pire qu'une erreur.

La comptabilité n'évalue **aucun prix** : le coût estimé est celui rapporté par
le fournisseur via la couche `ModelProvider` (#32) — la tarification par modèle
reste de son côté, jamais en dur ici. Un fournisseur qui ne rapporte pas de coût
laisse la tâche à coût « inconnu » (None, à distinguer d'un coût nul).

Ce coût inconnu rendrait le plafond de dépense inopérant sur un tel fournisseur
(#113) : `PlafondDepense` accepte donc un **plafond en tokens** en complément (ou
à la place) du plafond de coût — les tokens, eux, sont toujours rapportés. Le
plafond en USD garde la main dès que le coût est connu ; le plafond en tokens
prend le relais sinon, et `resume_controle_depense` dit à l'opérateur lequel des
deux tient réellement (au lieu d'un plafond silencieusement sans prise).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

from maestro.references import ReferenceTicket, ticket_en_dict
from maestro.telemetry.journal import RunJournal, StepRecord
from maestro.telemetry.usage import StepUsage


def intervalle_depuis(
    horodatage: str, duree_ms: int | None
) -> tuple[datetime, datetime] | None:
    """L'intervalle `[début, fin]` qu'une étape a **occupé**, ou None (#989).

    Le journal ne porte qu'un horodatage, celui de la **consignation** — donc la
    fin —, et la durée horloge de l'étape : le début s'en déduit. C'est déjà ce
    que fait l'export Langfuse (`maestro.telemetry.langfuse._debut_depuis`), et
    le refaire autrement donnerait deux débuts pour la même étape.

    None quand il n'y a pas d'intervalle à lire : aucune durée mesurée, une durée
    nulle ou négative, un horodatage vide ou illisible. Une étape sans intervalle
    ne compte pas dans l'union — elle ne la fausse pas non plus.

    Publique parce que le grand livre se construit par **deux** chemins, et
    qu'ils doivent lire le temps pareil : depuis le journal du moteur
    (`RunCost.depuis_journal`) et depuis le flux d'événements de la Control Tower
    (`EtatExecution.cout`). Une règle de temps recopiée des deux côtés d'une
    frontière est ce que #830 a vu casser.

    ⚠ L'horodatage est écrit à la **seconde** (`isoformat(timespec="seconds")`) :
    les bornes de l'union sont donc quantifiées à la seconde. C'est sans effet
    sur ce que l'union sert à corriger — un recouvrement de plusieurs minutes
    entre deux tâches menées de front — et rendre le journal plus fin pour cela
    seul coûterait à toutes ses lignes.
    """
    if duree_ms is None or duree_ms <= 0 or not horodatage:
        return None
    try:
        fin = datetime.fromisoformat(horodatage)
    except ValueError:
        return None
    return (fin - timedelta(milliseconds=duree_ms), fin)


def _intervalle(record: StepRecord) -> tuple[datetime, datetime] | None:
    """L'intervalle occupé par une ligne de journal — cf. `intervalle_depuis`."""
    return intervalle_depuis(record.horodatage, record.usage.duree_ms)


def union_ms(intervalles: list[tuple[datetime, datetime]]) -> int | None:
    """La durée couverte par l'**union** des intervalles, jamais leur somme (#989).

    Deux tâches menées de front occupent le run une seule fois sur la part qu'elles
    partagent. C'est la correction que l'outillage a faite sur les runs de
    `/orchestrate` (#497 — « l'occupation est l'union des intervalles et jamais
    leur somme ») et que le produit répétait à l'envers : 47 min annoncées pour
    43,5 min de mur (revue du 2026-08-26).

    None sur une liste vide — rien de mesuré n'est pas une durée nulle.
    """
    if not intervalles:
        return None
    # Triés par début, les intervalles se fondent en blocs contigus : on tient
    # le bloc courant et on ne compte que lorsqu'un trou le clôt.
    ordonnes = sorted(intervalles)
    debut_courant, fin_courante = ordonnes[0]
    couvert = timedelta()
    for debut, fin in ordonnes[1:]:
        if debut > fin_courante:
            # Un trou : le bloc précédent est clos, on le compte et on repart.
            couvert += fin_courante - debut_courant
            debut_courant, fin_courante = debut, fin
        elif fin > fin_courante:
            fin_courante = fin
    couvert += fin_courante - debut_courant
    return int(round(couvert.total_seconds() * 1000))


#: Étape du journal qui n'appartient à aucune tâche : la planification (#8).
ETAPE_PLANIFICATION = "planification"

#: Étape du journal qui n'appartient à aucune tâche : le **brief** structuré (#318).
#: À déclarer ici et pas seulement à consigner : sans cette ligne, `depuis_journal`
#: retomberait sur sa règle par défaut — « toute autre étape est une tâche » — et
#: ouvrirait une entrée de tâche fantôme nommée « brief » dans le grand livre. Son
#: usage serait bien compté, mais attribué à un travail qui n'existe pas.
ETAPE_BRIEF = "brief"

#: Étape du journal qui n'appartient à aucune tâche : la reprise d'un run
#: interrompu (#96) — un marqueur de run, pas un travail d'agent. Usage nul par
#: construction (aucun modèle n'est sollicité pour reprendre) : l'exclure du
#: grand livre lui évite une entrée de tâche fantôme, sans rien changer au total.
ETAPE_REPRISE = "reprise"


@dataclass(frozen=True)
class TaskCost:
    """L'entrée « tâche » du grand livre : qui a fait quoi, pour quel usage.

    `usage` fusionne l'étape de la tâche et ses étapes annexes (validation,
    message) — celles-ci rapportent un usage nul aujourd'hui, la fusion les
    garderait comptées si elles en portaient un demain. `nom`, `agent`, `role`
    et `statut` viennent de l'étape de la tâche elle-même ; ils restent vides
    tant que seule une annexe a été consignée (tâche encore en cours).

    `ticket` (#187) est le ticket dont relève la tâche : à la
    différence de l'identité, elle est prise sur **n'importe quelle** étape qui
    en porte une — l'annexe `<tache>:reference` en est justement le seul porteur
    quand un agent la pose en cours d'exécution.

    `projet_id` (#222) est le projet auquel la tâche appartient, pris au même
    régime que `ticket` : sur **n'importe quelle** étape qui en porte un — c'est
    ce qui permet de filtrer la comptabilité par projet.
    """

    tache_id: str
    nom: str = ""
    agent: str = ""
    role: str = ""
    statut: str = ""
    usage: StepUsage = StepUsage()
    ticket: ReferenceTicket | None = None
    projet_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Réémet l'entrée en dict JSON-sérialisable (la forme de l'API, #57)."""
        return {
            "tache_id": self.tache_id,
            "nom": self.nom,
            "agent": self.agent,
            "role": self.role,
            "statut": self.statut,
            "usage": self.usage.to_dict(),
            "ticket": ticket_en_dict(self.ticket),
            "projet_id": self.projet_id,
        }


@dataclass(frozen=True)
class RunCost:
    """Le grand livre d'une exécution : le coût par tâche et l'agrégat du run.

    `taches` suit l'ordre de première apparition au journal — l'ordre
    d'achèvement des tâches (#7), chaque entrée restant reliée au plan par
    `tache_id` ; `planification` porte l'usage de l'orchestrateur.

    `brief` (#318) porte l'usage de l'étape de **brief**, à part de la
    planification et à part des tâches. À part de la planification parce que ce
    sont deux appels modèle distincts, et que le brief peut être **régénéré**
    plusieurs fois par les allers-retours de clarification (#321) : les confondre
    masquerait ce que coûte réellement la mise au point de l'intention. Nul tant
    qu'aucun run n'est passé par cette étape — c'est le lot 6 (#320) qui la
    branche sur la boucle.
    """

    run_id: str
    planification: StepUsage = StepUsage()
    brief: StepUsage = StepUsage()
    taches: tuple[TaskCost, ...] = ()
    #: Ce que le run a **occupé** : l'union des intervalles de ses étapes (#989),
    #: `None` quand aucune n'a de durée lisible. C'est cette valeur que `total`
    #: porte comme durée, à la place d'une somme qui comptait deux fois les
    #: tâches menées de front.
    duree_mur_ms: int | None = None

    @property
    def total(self) -> StepUsage:
        """Usage agrégé de l'exécution — compteurs sommés, durée **unie** (#989).

        Tokens, appels, tours et coût se somment : deux tâches de front coûtent
        bien deux fois. La **durée**, non : c'est du temps de mur, et le run n'en
        a vécu qu'un — d'où `duree_mur_ms`, l'union des intervalles, à la place
        de la somme que `fusion` produirait.

        Ses **attentes** retombent à `None`, et c'est voulu : une somme d'attentes
        de tâches parallèles n'est pas une attente du run, et la retrancher d'une
        union donnerait un « travail du run » qui ne veut rien dire. Au niveau du
        run, la durée est une seule mesure — celle que GitHub Actions appelle
        « Total duration » ; la décomposition travail/attente se lit par tâche,
        là où elle a un sens.

        Les compteurs, eux, retombent bien sur `RunJournal.usage_totale`.
        """
        total = self.planification.fusion(self.brief)
        for tache in self.taches:
            total = total.fusion(tache.usage)
        return replace(
            total,
            duree_ms=self.duree_mur_ms,
            duree_arbitrage_ms=None,
            duree_attente_creneau_ms=None,
            duree_attente_atelier_ms=None,
        )

    @classmethod
    def depuis_journal(cls, journal: RunJournal) -> RunCost:
        """Comptabilise `journal` : chaque ligne attribuée à sa tâche, comptée une fois.

        S'appuie sur `RunJournal.records` (l'agrégat mémoire) : utilisable en
        fin d'exécution comme en cours de run (comptabilité partielle — une
        tâche dont seule une annexe est consignée apparaît sans identité ni
        statut, son usage déjà compté).
        """
        planification = StepUsage()
        brief = StepUsage()
        entrees: dict[str, TaskCost] = {}
        # Les intervalles de toutes les étapes comptées — planification et brief
        # compris : ils occupent le run comme le reste, et c'est le temps de mur
        # du run entier qu'on mesure, pas celui de ses seules tâches.
        intervalles: list[tuple[datetime, datetime]] = []
        for record in journal.records:
            if record.etape != ETAPE_REPRISE:
                intervalle = _intervalle(record)
                if intervalle is not None:
                    intervalles.append(intervalle)
            if record.etape == ETAPE_PLANIFICATION:
                planification = planification.fusion(record.usage)
                continue
            if record.etape == ETAPE_BRIEF:
                # Une étape du run, pas une tâche (#318) — et fusionnée plutôt
                # qu'affectée : un brief régénéré (#321) consigne une ligne par
                # tour, et le grand livre doit en porter le cumul.
                brief = brief.fusion(record.usage)
                continue
            if record.etape == ETAPE_REPRISE:
                # Marqueur de run (#96), rattaché à aucune tâche : rien à
                # comptabiliser — les étapes qu'il annonce sont, elles, réintégrées
                # au journal (`RunJournal.reconstitue`) et comptées à leur place.
                continue
            tache_id = record.etape.split(":", 1)[0]
            entree = entrees.get(tache_id)
            if entree is None:
                entree = TaskCost(tache_id=tache_id)
            if record.ticket is not None:
                # Le ticket externe (#187) vient de n'importe quelle étape de la
                # tâche — l'annexe `:reference` n'en porte même que ça.
                entree = replace(entree, ticket=record.ticket)
            if record.projet_id is not None:
                # Le projet (#222) suit la même règle : n'importe quelle étape
                # de la tâche fait foi, une étape sans projet n'efface rien.
                entree = replace(entree, projet_id=record.projet_id)
            if record.etape == tache_id:
                # L'étape de la tâche elle-même : elle fait foi pour l'identité.
                entree = replace(
                    entree,
                    nom=record.nom,
                    agent=record.agent,
                    role=record.role,
                    statut=record.statut,
                    usage=entree.usage.fusion(record.usage),
                )
            else:
                entree = replace(entree, usage=entree.usage.fusion(record.usage))
            entrees[tache_id] = entree
        return cls(
            run_id=journal.run_id,
            planification=planification,
            brief=brief,
            taches=tuple(entrees.values()),
            duree_mur_ms=union_ms(intervalles),
        )

    def to_dict(self) -> dict[str, Any]:
        """Réémet le grand livre en dict JSON-sérialisable (la forme de l'API, #57)."""
        return {
            "run_id": self.run_id,
            "planification": self.planification.to_dict(),
            "brief": self.brief.to_dict(),
            "total": self.total.to_dict(),
            "taches": [tache.to_dict() for tache in self.taches],
        }


class PlafondDepenseDepasse(RuntimeError):
    """Levée quand la dépense d'une exécution dépasse son plafond (#9).

    Émise depuis `report_usage` (donc depuis le fournisseur, entre deux appels
    modèle) quand `PlafondDepense.verifie` constate le dépassement : elle
    interrompt l'étape en cours, que l'appelant consigne comme stoppée par le
    garde-fou.
    """


class PlafondDepense:
    """Garde-fou de dépense d'une exécution (#9), adossé au grand livre (#56).

    C'est le contrôle que le collecteur d'usage consulte à chaque mesure
    (`collect_usage(plafond=...)`). Il ne tient **aucun compteur** : à chaque
    vérification, la dépense déjà engagée est relue dans la comptabilité de
    l'exécution (`RunCost.depuis_journal`) — la télémétrie est la source unique
    du coût — et complétée de l'usage de l'étape en cours, pas encore consignée
    au journal. Les étapes parallèles encore en vol ne comptent qu'à leur
    consignation : léger sous-comptage transitoire assumé (POC), jamais de
    double comptage.

    Deux seuils, dont au moins un doit être posé : `plafond_cout_usd` (budget en
    USD, sans prise sur un fournisseur qui ne rapporte pas de coût) et
    `plafond_tokens` (budget en tokens, opérant sur tout fournisseur — les tokens
    sont toujours rapportés, #113). Les deux sont vérifiés : le franchissement de
    l'un ou l'autre stoppe la tâche.
    """

    def __init__(
        self,
        journal: RunJournal,
        plafond_cout_usd: float | None = None,
        *,
        plafond_tokens: int | None = None,
    ) -> None:
        if plafond_cout_usd is None and plafond_tokens is None:
            raise ValueError(
                "PlafondDepense exige au moins un plafond (coût en USD ou tokens)."
            )
        if plafond_cout_usd is not None and plafond_cout_usd <= 0:
            raise ValueError(
                f"plafond_cout_usd doit être > 0 (reçu : {plafond_cout_usd})."
            )
        if plafond_tokens is not None and plafond_tokens <= 0:
            raise ValueError(
                f"plafond_tokens doit être > 0 (reçu : {plafond_tokens})."
            )
        self._journal = journal
        self._plafond_cout_usd = plafond_cout_usd
        self._plafond_tokens = plafond_tokens

    def verifie(self, en_cours: StepUsage) -> None:
        """Lève `PlafondDepenseDepasse` si la dépense du run, `en_cours` compris, dépasse.

        Le coût passe d'abord (le message le plus parlant quand il est connu), les
        tokens ensuite — le seuil en tokens tient même quand le coût est inconnu.
        """
        total = RunCost.depuis_journal(self._journal).total.fusion(en_cours)
        cout = total.cout_usd
        if (
            self._plafond_cout_usd is not None
            and cout is not None
            and cout > self._plafond_cout_usd
        ):
            raise PlafondDepenseDepasse(
                f"plafond de dépense dépassé : {cout:.4f} $ consommés sur l'exécution "
                f"pour un plafond de {self._plafond_cout_usd:.4f} $ — tâche stoppée."
            )
        if (
            self._plafond_tokens is not None
            and total.tokens_total > self._plafond_tokens
        ):
            raise PlafondDepenseDepasse(
                f"plafond de tokens dépassé : {total.tokens_total} tokens consommés sur "
                f"l'exécution pour un plafond de {self._plafond_tokens} — tâche stoppée."
            )


def resume_controle_depense(
    plafond_cout_usd: float | None,
    plafond_tokens: int | None,
    usage: StepUsage,
) -> str:
    """Décrit en une ligne le contrôle de dépense actif, pour l'opérateur (#113).

    Rend visible le cas « plafond silencieusement inopérant » : un plafond de coût
    armé sur un fournisseur qui ne rapporte pas de coût (`usage.cout_usd` None) n'a
    aucune prise — seul un plafond en tokens plafonne alors réellement. Destinée à
    la synthèse du run et au rapport JSON (`RunReport`), pas à la logique de
    contrôle (portée par `PlafondDepense.verifie`).
    """
    controles: list[str] = []
    if plafond_tokens is not None:
        controles.append(f"tokens ({usage.tokens_total}/{plafond_tokens})")
    if plafond_cout_usd is not None:
        if usage.cout_usd is not None:
            controles.append(f"coût réel ({usage.cout_usd:.4f}/{plafond_cout_usd:.4f} $)")
        elif plafond_tokens is None:
            # Le seul plafond posé est sans prise : la dépense n'est bornée par rien.
            return (
                f"plafond de coût de {plafond_cout_usd:.4f} $ armé mais SANS PRISE — "
                "le fournisseur ne rapporte pas de coût ; armez un plafond en tokens "
                "(--plafond-tokens) pour plafonner ce run."
            )
        else:
            controles.append(
                f"coût inopérant ({plafond_cout_usd:.4f} $ — fournisseur sans coût rapporté)"
            )
    if not controles:
        return "aucun plafond armé"
    return "plafond actif — " + ", ".join(controles)

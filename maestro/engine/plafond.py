"""Au plafond de dépense, le run se suspend et demande — le contrat (#1182).

Le défaut que ce module referme, relevé le 2026-09-21 par le balayage « rien de
figé » : quand un run atteignait son plafond de dépense, la tâche en vol était
**stoppée et son travail jeté** (`PlafondDepenseDepasse … tâche stoppée`), l'erreur
n'était pas rejouable, et chaque tâche restante était refusée à l'entrée de
l'exécuteur. À 101 % du budget, la personne perdait la tâche presque finie et tout
ce qui restait, sans qu'on lui ait rien demandé.

Le plafond lui-même est un garde-fou voulu (#494, #990), et il ne bouge pas : **rien
ne le dépasse sans la réponse de la personne**. C'est l'arrêt sec qui bridait.

## Ce qui se passe au plafond

1. la mesure qui franchit le plafond **interrompt** la tâche, comme avant — c'est
   la seule façon de ne rien dépenser de plus : un appel modèle ne se tarife
   qu'une fois fait ;
2. mais la tâche n'est pas soldée : elle est **mise de côté**. Son travail reste où
   il était — la branche `maestro/<tâche>` d'un projet versionné reçoit ce que le
   worktree portait (`maestro.sandbox.projet._solder_la_branche`), la racine d'un
   projet non versionné garde ce qui y a été écrit —, et ce qu'elle a dépensé
   entre au grand livre par sa ligne `<tâche>:plafond`, donc sous le plafond ;
3. le run **demande**, une fois, par l'arbitre câblé (`ArbitrePlafond`) : ce qui
   est dépensé, ce qui reste à faire, et la question — relever, réduire, arrêter.
   Les tâches qui atteignent le plafond pendant l'attente — une tâche parallèle
   à sa mesure suivante, une tâche prête qui se présente — attendent **la même**
   réponse : une seule question par franchissement ;
4. la réponse s'applique : **relever** pose le nouveau plafond et reprend les
   tâches mises de côté là où elles en étaient ; **réduire** fait de même en
   écartant les tâches que la personne a désignées ; **arrêter** solde le run sur
   ce qui est fait.

Le **coût estimé** du reste n'est pas calculé ici : c'est l'estimation du brief
(`apps/web/lib/estimation.ts`, docs/09 §4.3), et l'écran l'applique au nombre de
tâches que la demande porte. En inventer une seconde côté moteur ferait deux
chiffres pour une seule question.

## Qui peut suspendre

La suspension demande deux choses, et sans l'une ou l'autre le run garde l'arrêt
sec d'avant, dit au journal :

- un **arbitre** — quelqu'un à qui poser la question (la Control Tower la pose
  dans le fil, `maestro.controltower.plafond`) ;
- un exécuteur qui sache **relever** le plafond d'un run
  (`TaskExecutor.plafonds`). L'exécuteur local le sait ; un exécuteur distribué
  (`maestro.queue.CeleryExecutor`) garde ses plafonds côté worker, où la décision
  ne voyage pas — c'est une limite dite, comme celles de #1260.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from maestro.engine.guardrails import Guardrails
from maestro.telemetry import PlafondDepense, PlafondDepenseDepasse, RunJournal, StepUsage

#: Les trois réponses au plafond, dans l'ordre où elles se lisent.
GESTE_RELEVER = "relever"
GESTE_REDUIRE = "reduire"
GESTE_ARRETER = "arreter"
GESTES_PLAFOND = (GESTE_RELEVER, GESTE_REDUIRE, GESTE_ARRETER)

#: Suffixe de la ligne qu'une tâche **mise de côté** laisse au journal :
#: `<tâche>:plafond`. Elle porte la dépense de la tentative interrompue — c'est
#: ce qui la fait compter au grand livre, donc sous le plafond, sans solder la
#: tâche : le Kanban la garde « en cours » et l'étape finale viendra à sa reprise.
SUFFIXE_ETAPE_PLAFOND = ":plafond"

#: Statut de cette ligne : la tâche est suspendue sur le plafond du run.
STATUT_TACHE_SUSPENDUE = "suspendue_plafond"

#: Statuts des lignes de run (`ETAPE_PLAFOND`) : la question posée, puis ce qui en
#: est sorti. Un mot par issue, et `sans_decision` à part — personne n'a décidé
#: (le canal s'est refermé), ce qui ne se lit pas comme un arrêt voulu.
STATUT_PLAFOND_ATTEINT = "plafond_atteint"
STATUT_PLAFOND_RELEVE = "plafond_releve"
STATUT_PLAFOND_REDUIT = "plafond_reduit"
STATUT_PLAFOND_ARRETE = "plafond_arrete"
STATUT_PLAFOND_SANS_DECISION = "plafond_sans_decision"


@dataclass(frozen=True)
class TacheRestante:
    """Une tâche qui reste à faire au moment où le plafond est atteint.

    `interrompue` distingue la tâche **mise de côté** — elle avait commencé, son
    travail est conservé et elle reprendra là où elle en était — de celle qui n'a
    pas encore démarré. La personne ne décide pas pareil des deux : écarter une
    tâche presque finie n'est pas écarter une tâche à venir.
    """

    tache_id: str
    titre: str
    interrompue: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"tache_id": self.tache_id, "titre": self.titre, "interrompue": self.interrompue}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TacheRestante:
        return cls(
            tache_id=str(data.get("tache_id") or ""),
            titre=str(data.get("titre") or ""),
            interrompue=bool(data.get("interrompue", False)),
        )


@dataclass(frozen=True)
class DemandePlafond:
    """Ce que la personne reçoit quand son run atteint son plafond de dépense.

    Des **faits**, jamais une phrase : ce qui est dépensé (`depense_usd`, None
    quand le fournisseur ne tarifie pas, et `depense_tokens`), les plafonds en
    vigueur, la raison telle que le contrôle l'a levée, et ce qui reste à faire.
    L'écran en tire la question et l'estimation du reste ; la rédiger ici ferait
    un second texte à tenir d'accord avec la carte.
    """

    run_id: str
    projet_id: str | None
    objectif: str
    depense_usd: float | None
    depense_tokens: int
    plafond_cout_usd: float | None
    plafond_tokens: int | None
    raison: str
    restantes: tuple[TacheRestante, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "projet_id": self.projet_id,
            "objectif": self.objectif,
            "depense_usd": self.depense_usd,
            "depense_tokens": self.depense_tokens,
            "plafond_cout_usd": self.plafond_cout_usd,
            "plafond_tokens": self.plafond_tokens,
            "raison": self.raison,
            "restantes": [tache.to_dict() for tache in self.restantes],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DemandePlafond:
        restantes = data.get("restantes") or ()
        return cls(
            run_id=str(data.get("run_id") or ""),
            projet_id=data.get("projet_id") or None,
            objectif=str(data.get("objectif") or ""),
            depense_usd=_flottant(data.get("depense_usd")),
            depense_tokens=int(data.get("depense_tokens") or 0),
            plafond_cout_usd=_flottant(data.get("plafond_cout_usd")),
            plafond_tokens=_entier(data.get("plafond_tokens")),
            raison=str(data.get("raison") or ""),
            restantes=tuple(
                TacheRestante.from_dict(t) for t in restantes if isinstance(t, Mapping)
            ),
        )


@dataclass(frozen=True)
class DecisionPlafond:
    """La réponse de la personne au plafond — et ce qu'elle engage.

    `geste` est l'une des trois réponses. **Relever** et **réduire** posent un
    nouveau plafond (`plafond_cout_usd` et/ou `plafond_tokens` — None : inchangé) ;
    réduire écarte en plus les tâches nommées dans `ecartees`. **Arrêter** ne
    porte rien d'autre. `detail` est ce que le journal en dira.

    Validée à la construction : une décision qui reprendrait le run sans rien lui
    donner de plus à dépenser ne peut pas exister — elle le ramènerait au plafond
    à la mesure suivante, c'est-à-dire reposerait la même question.
    """

    geste: str
    plafond_cout_usd: float | None = None
    plafond_tokens: int | None = None
    ecartees: tuple[str, ...] = ()
    detail: str = ""

    def __post_init__(self) -> None:
        if self.geste not in GESTES_PLAFOND:
            raise ValueError(
                f"geste au plafond inconnu : {self.geste!r} "
                f"(attendu : {', '.join(GESTES_PLAFOND)})."
            )
        if self.plafond_cout_usd is not None and self.plafond_cout_usd <= 0:
            raise ValueError(f"plafond_cout_usd doit être > 0 (reçu : {self.plafond_cout_usd}).")
        if self.plafond_tokens is not None and self.plafond_tokens <= 0:
            raise ValueError(f"plafond_tokens doit être > 0 (reçu : {self.plafond_tokens}).")
        if self.geste == GESTE_ARRETER:
            return
        if self.plafond_cout_usd is None and self.plafond_tokens is None:
            raise ValueError(
                f"« {self.geste} » demande un nouveau plafond : sans lui, le run "
                "retomberait sur le même à sa prochaine mesure."
            )
        if self.geste == GESTE_REDUIRE and not self.ecartees:
            raise ValueError("« reduire » demande au moins une tâche à écarter.")

    @property
    def reprend(self) -> bool:
        """Le run reprend-il ? — relever ou réduire."""
        return self.geste != GESTE_ARRETER

    def to_dict(self) -> dict[str, Any]:
        return {
            "geste": self.geste,
            "plafond_cout_usd": self.plafond_cout_usd,
            "plafond_tokens": self.plafond_tokens,
            "ecartees": list(self.ecartees),
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DecisionPlafond:
        """Relit une décision — lève `ValueError` si elle n'en est pas une."""
        ecartees = data.get("ecartees") or ()
        return cls(
            geste=str(data.get("geste") or ""),
            plafond_cout_usd=_flottant(data.get("plafond_cout_usd")),
            plafond_tokens=_entier(data.get("plafond_tokens")),
            ecartees=tuple(str(i) for i in ecartees if str(i)),
            detail=str(data.get("detail") or ""),
        )


#: À qui le moteur pose la question du plafond, et ce qu'il en attend : la
#: décision, **sans borne**. Le run attend comme il attend une validation (#48,
#: #571) : sans réponse, rien ne se dépense — c'est exactement la promesse du
#: plafond —, et le run reste annulable pendant ce temps. Un arbitre qui **lève**
#: (bus refermé, transport en panne) n'invente pas de réponse : la boucle solde le
#: run comme avant ce lot, en disant que la question n'a pas pu être posée.
ArbitrePlafond = Callable[[DemandePlafond], Awaitable[DecisionPlafond]]


@dataclass
class Plafonds:
    """Les plafonds de dépense **en vigueur**, run par run — du lancement, relevés sur décision.

    `Guardrails` est immuable, et c'est juste : les bornes du lancement ne se
    réécrivent pas. Ce qui change au plafond, c'est le plafond **d'un run**, et
    seulement quand sa personne l'a dit. D'où ce registre, indexé par `run_id`
    comme les accords de fusion de l'exécuteur : un moteur qui sert plusieurs runs
    ne fait pas hériter à l'un la décision de l'autre.

    Il est **partagé** entre l'exécuteur, qui y arme le contrôle de chaque tâche,
    et le juge des échecs (#1178), qui dépense sous le même plafond : un plafond
    relevé vaut pour les deux, sans qu'aucun ait à l'apprendre.

    `suspendre(run_id)` est posé par la boucle quand elle sait demander : c'est ce
    qui dit à l'exécuteur qu'une tâche au plafond se **met de côté** au lieu de se
    solder en échec.
    """

    cout_usd: float | None = None
    tokens: int | None = None
    _releves: dict[str, tuple[float | None, int | None]] = field(default_factory=dict)
    _suspendus: set[str] = field(default_factory=set)

    @classmethod
    def de(cls, guardrails: Guardrails) -> Plafonds:
        """Les plafonds du lancement, tels que les garde-fous les portent."""
        return cls(cout_usd=guardrails.plafond_cout_usd, tokens=guardrails.plafond_tokens)

    def en_vigueur(self, run_id: str) -> tuple[float | None, int | None]:
        """Le plafond en USD et en tokens qui tient `run_id` à cet instant."""
        return self._releves.get(run_id, (self.cout_usd, self.tokens))

    def relever(
        self, run_id: str, *, cout_usd: float | None = None, tokens: int | None = None
    ) -> None:
        """Pose un nouveau plafond pour `run_id` — None garde celui qui tenait."""
        cout, jetons = self.en_vigueur(run_id)
        self._releves[run_id] = (
            cout_usd if cout_usd is not None else cout,
            tokens if tokens is not None else jetons,
        )

    def controle(self, journal: RunJournal) -> PlafondDepense | None:
        """Le contrôle de dépense du run de `journal` — None quand rien ne le plafonne."""
        cout, jetons = self.en_vigueur(journal.run_id)
        if cout is None and jetons is None:
            return None
        return PlafondDepense(journal, cout, plafond_tokens=jetons)

    def epuise(self, journal: RunJournal) -> bool:
        """Le budget du run est-il dépensé ? — rien de plus ne s'y engage alors."""
        controle = self.controle(journal)
        if controle is None:
            return False
        try:
            controle.verifie(StepUsage())
        except PlafondDepenseDepasse:
            return True
        return False

    def suspendre(self, run_id: str) -> None:
        """Au plafond, les tâches de `run_id` se mettent de côté plutôt que d'échouer."""
        self._suspendus.add(run_id)

    def suspend(self, run_id: str) -> bool:
        """Les tâches de `run_id` se mettent-elles de côté au plafond ?"""
        return run_id in self._suspendus


def ecartees_valides(decision: DecisionPlafond, restantes: Sequence[TacheRestante]) -> bool:
    """Les tâches que `decision` écarte sont-elles toutes parmi celles qui restent ?"""
    connues = {tache.tache_id for tache in restantes}
    return all(tache_id in connues for tache_id in decision.ecartees)


def _flottant(valeur: Any) -> float | None:
    if isinstance(valeur, bool) or not isinstance(valeur, int | float):
        return None
    return float(valeur)


def _entier(valeur: Any) -> int | None:
    if isinstance(valeur, bool) or not isinstance(valeur, int | float):
        return None
    return int(valeur)

"""Une tâche en échec se rattrape — la politique, le juge et ce que le journal en dit (#1178).

Le défaut que ce module referme, mesuré dans le code du moteur le 2026-09-21 :
une tâche en échec **barrait tout ce qui dépendait d'elle**, sans replanification
(`loop._consigne_blocage`), et ses nouvelles tentatives étaient **aveugles** —
trois essais identiques, toute erreur présumée passagère hors d'une courte liste
(`maestro.engine.retry`). Un accès refusé, un modèle inconnu ou un contexte trop
long échouaient trois fois à l'identique, puis la chaîne aval s'arrêtait, et la
seule issue était de relancer le run entier, brief et plan repayés.

## Deux étages, un seul juge

Le **Chef de projet** juge l'échec (`Orchestrator.rattrapage`) : il lit la cause,
dit sa nature — passager, configuration ou approche — et décide du geste. Il est
consulté aux deux étages où l'on retente, et c'est le même jugement qui sert aux
deux :

- **à l'étage de la tentative** (`LocalExecutor._realise`), avant chaque relance :
  la relance n'est plus présumée, elle est **jugée**. Un échec passager se rejoue
  là, sur la même délibération et la même checklist qu'avant (#584, #944) — c'est
  ce qui fait que ce jugement vit dans l'exécuteur et pas seulement au-dessus. Un
  échec qui ne l'est pas sort tout de suite, sans second essai identique ;
- **à l'étage de la tâche** (`OrchestrationEngine._rattrape`), quand l'exécuteur a
  rendu un échec : la tentative différente que le Chef de projet a proposée part —
  la tâche reprise autrement, confiée à un autre agent, ou redécoupée —, les tâches
  qui attendent sont ajustées, et elles repartent dès qu'elle aboutit. Ce que
  Maestro ne sait pas lever devient une question dans le fil.

Le verdict rendu au premier étage est **retenu** pour le second (`retenu`) : un
échec est diagnostiqué une fois, pas deux, et la tentative qui suit est bien celle
que le jugement a décidée.

## Ce qui n'est pas rattrapé

Un échec qui est une **décision** (`TaskResult.rattrapable`) : un humain a refusé
la tâche, ou le budget du run est dépensé. Rattraper serait contourner la décision.
Et un rattrapage ne dépense qu'**à l'intérieur** du budget : chaque diagnostic et
chaque tentative passent sous le même plafond que le reste du run (#9, #56), et un
plafond atteint arrête le rattrapage en le disant.

## Ce qui manque se propose (#1181)

Un échec dont la cause est un **prérequis** — un serveur MCP à authentifier, un rôle
que personne ne couvre, ce qu'un agent a signalé lui manquer — ne se rejoue ni ne se
redécoupe : il se **donne**. La boucle le propose dans le fil (`question_du_prerequis`,
un geste déclaré `CHOIX_PREREQUIS_LEVE` ; ou la carte d'équipe pour un rôle), et la
tâche reprend telle quelle. Ce module en porte le texte et les issues au journal
(`consigne_proposition`, statuts `prerequis_*`) ; la borne est
`PolitiqueRattrapage.max_propositions`.

## Une livraison non vérifiée se demande, elle ne se refait pas (#1396)

Quand l'agent **a livré** et que c'est le vérificateur qui est resté en panne
(`TaskResult.verification_en_panne`, #1388), ce n'est pas le travail qui a échoué.
Le run `da0a8ae6f1b2` l'avait pourtant redécoupé de zéro, sur des branches neuves :
le Chef de projet ne lisait que le texte de l'erreur, et le « Prompt is too long »
du juge disait « tâche trop grosse ». Le fait voyage désormais dans la tentative
(`Tentative.verification_en_panne`), et la boucle n'en demande aucun diagnostic :
le vérificateur a déjà été relancé seul, l'espace encore ouvert, et il reste à
**demander** (`question_de_la_livraison`) — prendre la livraison telle quelle, d'un
geste (`CHOIX_LIVRAISON_ACCEPTEE`), ou répondre en mots, que le Chef de projet lit
en sachant que le travail est livré. Sans réponse, la tâche reste en échec avec sa
livraison, et l'aval ne part pas sur un travail que personne n'a jugé.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from time import perf_counter

from maestro.agents.catalog import Agent
from maestro.engine.executor import (
    ACTEUR_ORCHESTRATEUR,
    ROLE_ORCHESTRATEUR,
    STATUT_QUESTION_REPONDUE,
    STATUT_QUESTION_SANS_REPONSE,
    SUFFIXE_ETAPE_QUESTION,
    _ecoule_ms,
)
from maestro.engine.plafond import Plafonds
from maestro.engine.questions import DemandeQuestion
from maestro.orchestrator.errors import RattrapageValidationError
from maestro.orchestrator.orchestrator import Orchestrator
from maestro.orchestrator.rattrapage import EchecDeTache, Rattrapage, Tentative
from maestro.orchestrator.schema import Task
from maestro.prerequis import PrerequisManquant
from maestro.telemetry import (
    PlafondDepense,
    RunJournal,
    StepUsage,
    collect_usage,
)

#: Suffixe des étapes du rattrapage au journal : `<task.id>:rattrapage`, une par
#: diagnostic et une pour l'issue quand la tâche reste en échec. Le pont Control
#: Tower les mue en activités de la tâche (`maestro.controltower.bridge`), comme
#: `:relance` — elles ne font changer la tâche d'aucune colonne.
SUFFIXE_ETAPE_RATTRAPAGE = ":rattrapage"

#: Le diagnostic n'a pas pu être rendu : fournisseur injoignable, réponse
#: illisible. Ce n'est pas un jugement, et rien n'est décidé sur lui.
STATUT_RATTRAPAGE_SANS_DIAGNOSTIC = "rattrapage_sans_diagnostic"
#: Le modèle a répondu, mais sa proposition ne s'exécute pas (rejouer un échec non
#: passager, retenter sans rien changer…) : la raison est consignée, et la suite
#: est une question.
STATUT_RATTRAPAGE_REFUSE = "rattrapage_refuse"
#: L'issue d'un rattrapage qui n'a pas abouti : la tâche reste en échec, et la
#: ligne dit pourquoi — sans réponse, abandon demandé, budget, personne à qui
#: demander.
STATUT_RATTRAPAGE_ECHOUE = "rattrapage_echoue"

#: Le verbe sous lequel une question de rattrapage entre dans les clés d'acte
#: (`maestro.deliberation.cle_acte`) — distinct de celui d'une question d'agent,
#: pour que les deux espaces d'identifiants ne se croisent jamais.
VERBE_RATTRAPAGE = "rattraper_une_tache"

#: Le verbe d'une **proposition de prérequis** dans les clés d'acte (#1181) — un
#: troisième espace, pour la même raison : la carte d'une proposition ne doit
#: jamais recevoir la réponse écrite pour une question.
VERBE_PREREQUIS = "proposer_un_prerequis"

#: Le geste que la carte d'une proposition offre (#1181) : la personne a donné ce
#: qui manquait, la tâche reprend. C'est un **choix déclaré** de la question
#: (`DemandeQuestion.choix`), donc un bouton — et c'est à lui seul que la boucle
#: reconnaît l'accord, par identité avec ce qu'elle a offert. Une réponse écrite
#: avec d'autres mots n'est pas lue par un motif : elle part au Chef de projet,
#: qui la juge (`JugeDesEchecs`, réponse qui fait autorité).
CHOIX_PREREQUIS_LEVE = "C'est fait — reprendre la tâche"

#: Le geste que la carte d'une **livraison non vérifiée** offre (#1396) : la
#: personne prend la livraison telle quelle, sans le jugement que le vérificateur
#: n'a pas pu rendre. Un choix déclaré, reconnu par identité comme le précédent ;
#: toute autre réponse part au Chef de projet.
CHOIX_LIVRAISON_ACCEPTEE = "Accepter la livraison telle quelle"

#: Les issues d'une proposition de prérequis, au journal (`<tâche>:rattrapage`).
#: Quatre et non deux, parce qu'elles n'appellent pas la même suite : **levé** —
#: la personne l'a donné, la tâche reprend ; **décliné** — un rôle qu'on n'a pas
#: voulu recruter, la tâche va au plus proche (#1260) ; **répondu** — une réponse
#: en mots, que le Chef de projet lit ; **sans réponse** — la tâche reste en échec.
STATUT_PREREQUIS_LEVE = "prerequis_leve"
STATUT_PREREQUIS_DECLINE = "prerequis_decline"
STATUT_PREREQUIS_REPONDU = "prerequis_repondu"
STATUT_PREREQUIS_SANS_REPONSE = "prerequis_sans_reponse"


def statut_du_geste(geste: str) -> str:
    """Le statut d'une ligne de diagnostic : le geste décidé, préfixé — `rattrapage_retenter`."""
    return f"rattrapage_{geste}"


@dataclass(frozen=True)
class PolitiqueRattrapage:
    """Combien de fois une tâche en échec se retente, et combien de questions on pose. Immuable.

    `max_tentatives` compte les tentatives **différentes** (ou les rejeux d'un échec
    jugé passager) engagées après l'échec, entre deux réponses de l'utilisateur :
    une réponse ouvre une nouvelle série, puisqu'elle apporte ce qui manquait.
    `max_questions` borne les questions posées pour une même tâche — 0 : on ne
    demande jamais, et la tâche reste en échec quand les tentatives sont épuisées.

    Ce sont des bornes de dépense, pas des défauts arbitraires : chaque tentative
    coûte, et le budget du run, quand il y en a un, les borne en plus.

    `max_propositions` (#1181) borne les **prérequis proposés** pour une même
    tâche — un serveur à authentifier, un secret, un rôle. La seconde proposition
    dit « il manque toujours » quand la reprise a rencontré le même manque ; au-delà,
    c'est le Chef de projet qui juge. Ce n'est pas une dépense de modèle — proposer
    ne coûte rien —, c'est le nombre de fois qu'on revient vers la personne avec la
    même demande. 0 : on ne propose jamais, et l'échec se rattrape comme avant.
    """

    max_tentatives: int = 2
    max_questions: int = 1
    max_propositions: int = 2

    def __post_init__(self) -> None:
        if self.max_tentatives < 1:
            raise ValueError(
                f"max_tentatives doit être ≥ 1 (reçu : {self.max_tentatives})."
            )
        if self.max_questions < 0:
            raise ValueError(f"max_questions doit être ≥ 0 (reçu : {self.max_questions}).")
        if self.max_propositions < 0:
            raise ValueError(
                f"max_propositions doit être ≥ 0 (reçu : {self.max_propositions})."
            )


#: Politique des vrais runs (`OrchestrationEngine.default()`) : deux tentatives
#: différentes, puis une question à l'utilisateur, puis deux de plus sur sa réponse.
RATTRAPAGE_DEFAUT = PolitiqueRattrapage()


@dataclass(frozen=True)
class Refus:
    """Le modèle a répondu, mais sa proposition ne s'exécute pas — et pourquoi.

    Distinct d'une absence de diagnostic : le modèle **a** jugé, et un refus dit
    au moins que ce n'était pas « rejouer tel quel ». C'est ce qui arrête la
    relance de l'exécuteur, là où un juge injoignable la laisse à la présomption.
    """

    raison: str


#: Ce que le juge rend : un rattrapage à exécuter, ou une proposition refusée.
Verdict = Rattrapage | Refus


@dataclass
class Dossier:
    """L'histoire d'une tâche du plan en échec, tenue le temps de son rattrapage.

    Partagée entre les deux étages : la boucle y ajoute chaque tentative, et
    l'exécuteur, quand il juge l'échec d'une tentative en cours, s'en sert pour
    que le Chef de projet la lise **dans son histoire** — sans quoi il pourrait
    reproposer ce qui a déjà échoué deux tentatives plus tôt.

    `en_cours` dit comment la tentative qui s'exécute a été prise (« reprise
    autrement »…), pour que sa ligne d'histoire le dise si elle échoue.
    """

    tache: Task
    tentatives: list[Tentative] = field(default_factory=list)
    question: str = ""
    reponse: str | None = None
    en_cours: str = "telle que planifiée"


class JugeDesEchecs:
    """Le Chef de projet consulté sur un échec, aux deux étages où l'on retente (#1178).

    Il tient ce que le jugement demande et que ni l'exécuteur ni la boucle n'ont
    seuls : l'objectif et le plan de chaque run (donc l'aval d'une tâche), le
    dossier de chaque tâche en rattrapage, et le verdict rendu à l'étage de la
    tentative, retenu pour l'étage de la tâche.

    Indexé par `run_id`, comme les accords de fusion de l'exécuteur : un moteur
    qui sert plusieurs runs ne fait pas hériter à l'un l'histoire de l'autre. Les
    dossiers sont fermés à l'issue du rattrapage ; les contextes de run, deux
    chaînes et un plan, ne sont pas purgés.
    """

    def __init__(
        self,
        orchestrator: Orchestrator,
        equipe: Callable[[str | None], Sequence[Agent]],
        plafonds: Plafonds,
    ) -> None:
        self._orchestrator = orchestrator
        self._equipe = equipe
        # Les plafonds **en vigueur** (#1182), partagés avec l'exécuteur : un
        # plafond relevé sur décision vaut pour les diagnostics comme pour les
        # tâches, sans qu'aucun des deux ait à l'apprendre.
        self._plafonds = plafonds
        self._runs: dict[str, tuple[str, tuple[Task, ...]]] = {}
        self._dossiers: dict[tuple[str, str], Dossier] = {}
        self._retenus: dict[tuple[str, str], Verdict] = {}

    def suit(self, run_id: str, objectif: str, taches: Sequence[Task]) -> None:
        """Retient l'objectif et le plan de `run_id` — l'aval d'une tâche s'y lit."""
        self._runs[run_id] = (objectif, tuple(taches))

    def objectif(self, run_id: str) -> str:
        """L'objectif de `run_id` — ce que la proposition d'un rôle rappelle (#1181)."""
        return self._runs.get(run_id, ("", ()))[0]

    def aval(self, run_id: str, tache_id: str) -> tuple[Task, ...]:
        """Les tâches qui attendent `tache_id`, directement ou non, dans l'ordre du plan."""
        _, taches = self._runs.get(run_id, ("", ()))
        attendent = {tache_id}
        for tache in taches:  # le plan est dans l'ordre topologique : un passage suffit
            if attendent.intersection(tache.dependances):
                attendent.add(tache.id)
        attendent.discard(tache_id)
        return tuple(tache for tache in taches if tache.id in attendent)

    def ouvre(self, run_id: str, tache: Task) -> Dossier:
        """Ouvre le dossier de rattrapage de `tache` — un dossier d'avant est remplacé."""
        dossier = Dossier(tache=tache)
        self._dossiers[(run_id, tache.id)] = dossier
        return dossier

    def ferme(self, run_id: str, tache_id: str) -> None:
        """Ferme le dossier de `tache_id` et oublie un verdict qui n'aurait pas servi."""
        self._dossiers.pop((run_id, tache_id), None)
        self._retenus.pop((run_id, tache_id), None)

    def retenu(self, run_id: str, tache_id: str) -> Verdict | None:
        """Le verdict rendu à l'étage de la tentative pour `tache_id`, et le retire."""
        return self._retenus.pop((run_id, tache_id), None)

    def budget_epuise(self, journal: RunJournal) -> bool:
        """Le budget du run est-il déjà dépensé ? — auquel cas plus rien ne s'engage."""
        return self._plafonds.epuise(journal)

    async def rejouer_la_tentative(
        self,
        task: Task,
        agent: Agent,
        erreur: str,
        tentative: int,
        journal: RunJournal,
        *,
        blocages: Sequence[str] = (),
    ) -> tuple[bool, str] | None:
        """Le juge de l'exécuteur : rejoue-t-on cette tentative telle quelle ?

        Rend `(True, diagnostic)` pour un échec passager, `(False, diagnostic)`
        sinon — le verdict est alors **retenu** pour la boucle, qui exécutera la
        tentative différente qu'il décide —, et `None` quand le juge n'a rien pu
        dire (fournisseur injoignable, réponse illisible) : l'exécuteur retombe
        alors sur la présomption d'avant, jamais sur un échec inventé.

        Appelé **dans** la tâche : l'usage du diagnostic entre au collecteur de
        l'exécution, sous son plafond, et l'étape finale de la tâche le porte — la
        ligne de diagnostic est donc consignée à usage nul, sans double compte.
        """
        dossier = self._dossiers.get((journal.run_id, task.id))
        geste = dossier.en_cours if dossier is not None else "telle que planifiée"
        if tentative > 1:
            geste = f"{geste}, rejouée {tentative - 1} fois à l'identique"
        courante = Tentative(
            taches=(task,),
            agent=agent.nom,
            role=agent.role,
            erreur=erreur,
            geste=geste,
            blocages=tuple(blocages),
        )
        base = dossier if dossier is not None else Dossier(tache=task)
        verdict, _ = await self._juge(
            task.id,
            base,
            (*base.tentatives, courante),
            journal,
            compte_a_part=False,
        )
        if verdict is None:
            return None
        if isinstance(verdict, Rattrapage) and verdict.rejoue:
            return True, verdict.diagnostic
        self._retenus[(journal.run_id, task.id)] = verdict
        return False, (
            verdict.diagnostic if isinstance(verdict, Rattrapage) else verdict.raison
        )

    async def juge(
        self, dossier: Dossier, journal: RunJournal
    ) -> tuple[Verdict | None, StepUsage]:
        """Le juge de la boucle : l'échec de la tâche du plan, dans toute son histoire.

        Appelé **hors** de toute tâche : le diagnostic ouvre son propre collecteur,
        sous le plafond du run, et sa ligne au journal porte ce qu'il a coûté. Rend
        aussi cet usage, que la boucle ajoute au résultat de la tâche — le rapport
        et le grand livre disent ainsi le même total.
        """
        return await self._juge(
            dossier.tache.id,
            dossier,
            tuple(dossier.tentatives),
            journal,
            compte_a_part=True,
        )

    async def _juge(
        self,
        tache_id: str,
        dossier: Dossier,
        tentatives: tuple[Tentative, ...],
        journal: RunJournal,
        *,
        compte_a_part: bool,
    ) -> tuple[Verdict | None, StepUsage]:
        objectif, _ = self._runs.get(journal.run_id, ("", ()))
        echec = EchecDeTache(
            tache=dossier.tache,
            tentatives=tentatives,
            objectif=objectif,
            aval=self.aval(journal.run_id, dossier.tache.id),
            question=dossier.question,
            reponse=dossier.reponse,
        )
        equipe = self._equipe(dossier.tache.projet_id)
        debut = perf_counter()
        usage = StepUsage()
        verdict: Verdict | None = None
        panne = ""
        try:
            if compte_a_part:
                with collect_usage(plafond=self._plafond(journal)) as recolte:
                    try:
                        verdict = await self._orchestrator.rattrapage(echec, equipe=equipe)
                    finally:
                        usage = recolte.total.avec_duree(_ecoule_ms(debut))
            else:
                verdict = await self._orchestrator.rattrapage(echec, equipe=equipe)
        except asyncio.CancelledError:
            raise
        except RattrapageValidationError as exc:
            verdict = Refus(str(exc))
        except Exception as exc:  # noqa: BLE001 — un juge muet ne condamne pas la tâche
            panne = str(exc) or type(exc).__name__
        consigne_diagnostic(
            journal,
            tache_id,
            dossier.tache,
            echec.derniere.erreur,
            verdict,
            panne,
            usage if compte_a_part else StepUsage(),
        )
        return verdict, usage

    def _plafond(self, journal: RunJournal) -> PlafondDepense | None:
        """Le plafond de dépense du run, armé comme celui de chaque tâche (#9, #56, #1182)."""
        return self._plafonds.controle(journal)


def consigne_diagnostic(
    journal: RunJournal,
    tache_id: str,
    tache: Task,
    cause: str,
    verdict: Verdict | None,
    panne: str,
    usage: StepUsage,
) -> None:
    """Écrit au journal ce que le Chef de projet a jugé d'un échec — ou pourquoi il n'a rien dit.

    `entree` porte la cause lue, `sortie` le jugement : la nature, le diagnostic,
    et le geste décidé. Une proposition refusée l'est **avec sa raison**, et un
    juge injoignable avec la panne : dans les trois cas, la ligne dit ce qui
    décide de la suite. `tache_id` est la tâche **exécutée** — celle du plan, ou
    une tâche de redécoupage jugée seule.
    """
    if isinstance(verdict, Rattrapage):
        statut = statut_du_geste(verdict.geste)
        sortie = f"{verdict.nature} — {verdict.diagnostic} → {_geste_en_clair(verdict)}"
    elif isinstance(verdict, Refus):
        statut = STATUT_RATTRAPAGE_REFUSE
        sortie = f"proposition refusée : {verdict.raison}"
    else:
        statut = STATUT_RATTRAPAGE_SANS_DIAGNOSTIC
        sortie = f"diagnostic impossible : {panne}"
    journal.consigne(
        etape=f"{tache_id}{SUFFIXE_ETAPE_RATTRAPAGE}",
        nom=f"Rattrapage — {tache.titre}",
        agent=ACTEUR_ORCHESTRATEUR,
        role=ROLE_ORCHESTRATEUR,
        statut=statut,
        entree=cause,
        sortie=sortie,
        usage=usage,
        ticket=tache.ticket,
        projet_id=tache.projet_id,
    )


def _geste_en_clair(verdict: Rattrapage) -> str:
    """Le geste décidé, dit comme on le lirait dans le fil d'activité."""
    if verdict.rejoue:
        return "rejouée telle quelle"
    if verdict.geste == "retenter":
        if len(verdict.taches) > 1:
            titres = ", ".join(f"« {t.titre} »" for t in verdict.taches)
            texte = f"redécoupée en {len(verdict.taches)} tâches : {titres}"
        else:
            texte = "reprise autrement"
        if verdict.ajustements:
            texte += " ; tâches aval ajustées : " + ", ".join(i for i, _ in verdict.ajustements)
        return texte
    if verdict.geste == "demander":
        return f"question à l'utilisateur : {verdict.question}"
    if verdict.prerequis is not None:
        return f"prérequis proposé à l'utilisateur : {verdict.prerequis.phrase()}"
    return "abandonnée sur la réponse de l'utilisateur"


def consigne_issue(journal: RunJournal, tache: Task, cause: str, issue: str) -> None:
    """Écrit au journal que le rattrapage de `tache` n'a pas abouti, et pourquoi."""
    journal.consigne(
        etape=f"{tache.id}{SUFFIXE_ETAPE_RATTRAPAGE}",
        nom=f"Rattrapage — {tache.titre}",
        agent=ACTEUR_ORCHESTRATEUR,
        role=ROLE_ORCHESTRATEUR,
        statut=STATUT_RATTRAPAGE_ECHOUE,
        entree=cause,
        sortie=f"la tâche reste en échec : {issue}",
        usage=StepUsage(),
        ticket=tache.ticket,
        projet_id=tache.projet_id,
    )


def consigne_question(
    journal: RunJournal,
    demande: DemandeQuestion,
    tache: Task,
    reponse: str | None,
    motif: str,
) -> None:
    """Écrit au journal l'issue de la question posée sur un échec — la forme d'une question d'agent.

    Étape `<tâche>:question`, statuts de #1023 : c'est le même fait (une question
    posée pendant le run, et ce qui en est sorti), et la liste des décisions d'un
    run la lit déjà sous cette forme. `sortie` porte l'issue nue — la réponse, ou
    l'hypothèse retenue —, `description` son motif.
    """
    repondue = reponse is not None
    journal.consigne(
        etape=f"{tache.id}{SUFFIXE_ETAPE_QUESTION}",
        nom=f"Question du Chef de projet — {tache.titre}",
        agent=ACTEUR_ORCHESTRATEUR,
        role=ROLE_ORCHESTRATEUR,
        statut=STATUT_QUESTION_REPONDUE if repondue else STATUT_QUESTION_SANS_REPONSE,
        entree=demande.resume(),
        sortie=reponse if reponse is not None else demande.hypothese,
        description=motif,
        usage=StepUsage(),
        projet_id=tache.projet_id,
    )


def question_du_rattrapage(
    tache: Task, dossier: Dossier, question: str, diagnostic: str
) -> str:
    """Ce que la carte du fil pose : la question, puis les faits — la cause et les tentatives.

    La question est celle du Chef de projet quand il en a écrit une ; à défaut
    (proposition refusée, juge injoignable, tentatives épuisées), une question
    ouverte qui dit qu'on ne sait pas lever l'échec seul. Les **faits** suivent,
    toujours, et c'est le critère du ticket : qu'est-ce qui a échoué, qu'a tenté
    Maestro — chaque tentative avec son erreur. Ils sont écrits ici et non par le
    modèle : une question qui oublierait la cause ferait répondre à l'aveugle.
    """
    entete = question.strip() or (
        f"La tâche « {tache.titre} » échoue et je ne sais pas la faire aboutir seul : "
        "que dois-je faire ?"
    )
    lignes = [entete, "", f"Tâche en échec : « {tache.titre} »", "Tentatives :"]
    lignes += [
        f"{rang}. {tentative.geste} — {tentative.role or 'non routée'} : "
        f"{_erreur_courte(tentative.erreur)}"
        for rang, tentative in enumerate(dossier.tentatives, start=1)
    ]
    if diagnostic.strip():
        lignes.append(f"Diagnostic : {diagnostic.strip()}")
    return "\n".join(lignes)


def question_du_prerequis(tache: Task, prerequis: PrerequisManquant, rang: int) -> str:
    """Ce que la carte d'une proposition pose : ce qui manque, puis comment le donner (#1181).

    Les **faits** seulement, écrits ici et non par un modèle, comme ceux d'une
    question de rattrapage : ce qui manque est constaté (la bibliothèque MCP, le
    routage) ou nommé par le Chef de projet, et la procédure est celle qu'il
    connaît. `rang` dit si c'est la première fois : à la seconde, la reprise a
    rencontré le même manque, et la carte le dit plutôt que de reposer la même
    demande comme si de rien n'était.

    Le geste qui répond est sur la carte (`CHOIX_PREREQUIS_LEVE`), et la phrase de
    fin dit ce qu'il fait — reprendre la tâche **dans ce run** : c'est la réponse
    à « et si je le donne, je dois tout relancer ? ».
    """
    etat = "est suspendue" if rang <= 1 else "est toujours suspendue"
    lignes = [f"La tâche « {tache.titre} » {etat} : {prerequis.phrase()}"]
    if prerequis.procedure.strip():
        lignes += ["", "Pour le lui donner :", prerequis.procedure.strip()]
    lignes += [
        "",
        "Quand c'est fait, dites-le : la tâche reprendra là où elle s'est arrêtée, "
        "sans relancer le run. Ne collez aucun secret ici.",
    ]
    return "\n".join(lignes)


def consigne_proposition(
    journal: RunJournal,
    tache: Task,
    prerequis: PrerequisManquant,
    statut: str,
    issue: str,
) -> None:
    """Écrit au journal ce qu'une proposition de prérequis a donné (#1181).

    Étape `<tâche>:rattrapage`, activité de la tâche comme un diagnostic : la
    proposition est un geste du Chef de projet sur elle, et la frise la montre à
    sa place. `entree` porte le prérequis, `sortie` son issue — levé, décliné,
    répondu en mots, ou sans réponse. Usage nul : proposer ne dépense rien.
    """
    journal.consigne(
        etape=f"{tache.id}{SUFFIXE_ETAPE_RATTRAPAGE}",
        nom=f"Prérequis — {tache.titre}",
        agent=ACTEUR_ORCHESTRATEUR,
        role=ROLE_ORCHESTRATEUR,
        statut=statut,
        entree=prerequis.phrase(),
        sortie=issue,
        usage=StepUsage(),
        ticket=tache.ticket,
        projet_id=tache.projet_id,
    )


def question_de_la_livraison(tache: Task) -> str:
    """Ce que la carte demande d'une livraison que le vérificateur n'a pas pu juger (#1396).

    Rien à diagnostiquer : le fait est typé (`TaskResult.verification_en_panne`),
    l'agent a livré et c'est son juge qui est tombé. Le Chef de projet ne refait
    donc rien — il demande s'il faut prendre la livraison telle quelle, et ce
    geste est sur la carte (`CHOIX_LIVRAISON_ACCEPTEE`). Les faits suivent, comme
    pour toute question de rattrapage (`question_du_rattrapage`) : la cause de la
    panne y est, puisque c'est elle qu'on accepte de ne pas avoir levée.

    Un juge qui n'a pas pu **jouer** un de ses contrôles (#1432 : une commande que
    le système a refusé de lancer) pose la même question : rien n'a été constaté
    faux, mais tout n'a pas été jugé.
    """
    return (
        f"La tâche « {tache.titre} » a livré, mais sa vérification n'a pas abouti : "
        "personne n'a pu juger si sa livraison tient en entier. La prendre telle "
        "quelle, ou que dois-je en faire ?"
    )


def hypothese_du_rattrapage(aval: Sequence[Task], *, livree: bool = False) -> str:
    """Ce qui se passera sans réponse — la tâche reste en échec, et ce qu'elle retient.

    `livree` (#1396) : la tâche a livré sans que sa livraison soit jugée. Elle la
    garde, mais rien ne part dessus — c'est ce qu'accepter la livraison changerait.
    """
    reste = "la tâche reste en échec avec sa livraison" if livree else "la tâche reste en échec"
    if not aval:
        return f"{reste} ; aucune autre tâche n'en dépend"
    titres = ", ".join(f"« {tache.titre} »" for tache in aval)
    return f"{reste}, et les tâches qui l'attendent ne s'exécutent pas : {titres}"


def _erreur_courte(erreur: str, borne: int = 300) -> str:
    """Une erreur sur une ligne, bornée — la carte du fil n'est pas un journal."""
    texte = " ".join(erreur.split()) or "aucune cause rendue"
    return texte if len(texte) <= borne else texte[: borne - 1] + "…"

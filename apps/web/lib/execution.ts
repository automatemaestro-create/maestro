/**
 * Ce que l'UI sait du **cycle de vie d'un run** hors du JSX (#348, #349, #474) :
 * lequel n'est plus porté par personne, lequel a du cadrage à rejouer, et — depuis
 * la liste des runs — lequel **travaille** au moment où on le regarde.
 *
 * Ces règles vivent ici et pas dans les composants pour la raison habituelle du
 * dépôt : elles sont **partagées** — le panneau du tableau de bord pose la question
 * « ce run est-il perdu ? », et le jour où un écran *Exécutions* la reposera, deux
 * formulations finiraient par diverger. Ce jour est arrivé avec #474, et c'est ici
 * que la réponse s'est écrite. Pendant exact de `lib/brief.ts`, sur l'autre
 * question : celui-là dit ce qu'un run attend **de quelqu'un**, celui-ci dit ce
 * qu'un run n'attend plus de **personne**.
 */

import { attendUnHumain } from "./brief";
import {
  CAUSE_EXTINCTION,
  EXECUTION_ANNULEE,
  EXECUTION_ECHEC,
  EXECUTION_EN_ATTENTE_ARBITRAGE,
  EXECUTION_EN_ATTENTE_REPONSES,
  EXECUTION_TERMINEE,
  VALIDATION_EN_ATTENTE,
  VITALITE_ORPHELIN,
  type ResumeExecution,
  type Tache,
  type Validation,
} from "./types";

/**
 * Ce run est-il **orphelin** — son hôte a battu, puis s'est tu (#348) ?
 *
 * Strictement le verdict du backend, jamais une seconde déduction faite ici à
 * partir de `debut` ou de `fin` : le seuil, ses écarts (horodatage illisible,
 * battement dans le futur) et le sens de chaque verdict vivent dans
 * `maestro/controltower/battement.py`, et une formule recopiée côté client se
 * périmerait à la première correction.
 *
 * `indetermine` n'en est **pas un**, et c'est le point : un run qui n'a jamais
 * battu est un run dont on ne sait rien, pas un run mort. L'API accepte quand même
 * de le relancer — sans quoi les quatre runs fantômes antérieurs au battement
 * seraient définitivement perdus —, mais l'UI ne le **propose** pas : proposer sur
 * une absence d'information serait deviner, ce que le troisième verdict existe
 * précisément pour refuser.
 */
export function estOrphelin(execution: ResumeExecution): boolean {
  return execution.vitalite === VITALITE_ORPHELIN;
}

/**
 * Ce run **attend-il quelqu'un depuis trop longtemps** (#737) ?
 *
 * Le frère du précédent sur l'**autre** question, et les deux ne se recouvrent
 * pas : `estOrphelin` demande « son hôte est-il là ? », celui-ci « ce run
 * avance-t-il ? ». Un run suspendu sur un humain depuis une heure est `vivant`
 * *et* en souffrance — c'est même la paire qu'a portée le run de #568.
 *
 * Strictement le verdict du backend, pour la raison exacte qui vaut au-dessus :
 * le seuil (15 min, `SEUIL_SOUFFRANCE_S`) et ses écarts — un horodatage illisible
 * rend `true`, l'inverse de `vitalite`, parce que « suspendu depuis on ne sait
 * quand » est pire que « suspendu depuis vingt minutes » — vivent dans
 * `maestro/controltower/souffrance.py`. Comparer `attente_depuis` ici donnerait
 * une seconde règle qui se périmerait à la première correction du seuil, et
 * `docs/33 §5.4` dit d'avance que ce chiffre bougera.
 *
 * L'absence du champ vaut **non** : une trace relue d'un backend antérieur au lot
 * ne porte pas de verdict, et en inventer un reviendrait à faire ici le calcul
 * que le paragraphe précédent refuse.
 */
export function estEnSouffrance(execution: ResumeExecution): boolean {
  return execution.en_souffrance === true;
}

/**
 * Ce run a-t-il été emporté par l'**extinction de Maestro** (#486) ?
 *
 * Le second cas d'un run qu'on peut reprendre, et il ne ressemble pas au premier :
 * ce run-là n'est pas perdu, il a été **soldé exprès** — `start.sh --stop`, ou la
 * fermeture de l'enveloppe le jour où elle existe. Son statut est donc terminal, et
 * `estOrphelin` répond non, à raison : son hôte n'a pas cessé de battre, on l'a
 * éteint.
 *
 * La reconnaissance passe par la **cause** et non par le statut, qui est celui de
 * n'importe quelle annulation (`annulee`) : c'est le seul champ qui distingue « on
 * a éteint l'application qui tenait ce run » de « quelqu'un a arrêté ce run-là ».
 * Les confondre reproposerait de reprendre un run que son auteur venait
 * délibérément d'annuler.
 *
 * Le statut est vérifié **en plus** de la cause, alors que le backend ne pose
 * jamais l'une sans l'autre : la projection efface la cause dès qu'un run repart
 * (`state.py`), donc les deux disent la même chose — et le jour où elles ne le
 * diraient plus, proposer « Reprendre » sur un run en vol serait la pire des deux
 * lectures.
 */
export function estEteint(execution: ResumeExecution): boolean {
  return estSolde(execution) && execution.cause === CAUSE_EXTINCTION;
}

/**
 * Ce run a-t-il un **cadrage à rejouer** (#349, #486) ?
 *
 * Deux moitiés, et aucune ne suffit. La première est l'état du run : **perdu**
 * (orphelin, son hôte s'est tu) ou **éteint** (Maestro s'est arrêté en l'emportant)
 * — un run qui travaille n'a pas à être repris. La seconde est son **brief
 * approuvé** : sans lui il n'y a rien à rejouer, le run s'étant arrêté avant que
 * quelqu'un ne valide son cadrage, et le relancer reviendrait à repartir de son
 * objectif brut, c'est-à-dire à sauter la validation qu'il attendait encore. L'API
 * refuse ce cas en 422 ; ne pas proposer le geste évite d'offrir un bouton qui
 * n'aboutira pas.
 *
 * Les deux états mènent au **même** bouton — c'est le critère de #486, « par le
 * bouton existant » —, et c'est justifié : ce que la relance rejoue est un cadrage,
 * et un cadrage payé se rejoue de la même façon qu'on l'ait perdu ou rangé.
 */
export function estRelancable(execution: ResumeExecution): boolean {
  return (
    (estOrphelin(execution) || estEteint(execution)) &&
    execution.brief_approuve === true
  );
}

/**
 * Les runs perdus dont le cadrage peut repartir, **dans l'ordre du backend**.
 *
 * Aucun tri ici, à dessein : `GET /api/executions` rend déjà ses résumés récents
 * d'abord, et c'est le bon ordre — le run qu'on vient de perdre est celui qu'on
 * veut récupérer, un fantôme du mois dernier peut attendre. Retrier localement
 * poserait une seconde règle d'ordre à tenir d'accord avec la première pour un
 * résultat identique.
 */
export function runsRelancables(
  executions: ResumeExecution[],
): ResumeExecution[] {
  return executions.filter(estRelancable);
}

/* ------------------------------------------------------------------ *
 * Le régime d'un run (#474) — travaille-t-il, à cet instant ?
 * ------------------------------------------------------------------ */

/**
 * Les trois **issues** d'un run : il a rendu son verdict, il ne bougera plus.
 *
 * `en_attente_brief`, `en_attente_reponses` et `en_attente_arbitrage` n'en sont
 * pas, et le contrat le dit en toutes lettres (`lib/types`) : ce sont des états
 * **non terminaux** — le run est en vol, simplement arrêté sur quelqu'un.
 */
export const STATUTS_EXECUTION_SOLDES: readonly string[] = [
  EXECUTION_TERMINEE,
  EXECUTION_ANNULEE,
  EXECUTION_ECHEC,
];

/**
 * Ce run a-t-il rendu son verdict ?
 *
 * Le repli d'un statut **inconnu** est « en vol », et c'est un choix : le flux peut
 * s'enrichir (`lib/types`), et déclarer soldé ce qu'on ne comprend pas ferait
 * disparaître un run des runs à surveiller. Un faux « en cours » se corrige d'un
 * coup d'œil ; un faux « terminé » se tait.
 */
export function estSolde(execution: ResumeExecution): boolean {
  return STATUTS_EXECUTION_SOLDES.includes(execution.statut);
}

/**
 * Ce run est-il **suspendu par quelqu'un** (#477) ?
 *
 * À ne pas confondre avec `REGIME_SUSPENDU` ci-dessous, qui dit « ce run attend un
 * humain » : ici c'est l'inverse du sens de la flèche — un humain a arrêté de lui
 * donner du travail, et rien ne repartira sans un second geste. Le drapeau vit à
 * côté du statut, qui ne bouge pas : un run peut être suspendu *et* arrêté sur son
 * brief, les deux étant vrais en même temps.
 */
export function estEnPause(execution: ResumeExecution): boolean {
  return execution.en_pause === true;
}

/**
 * Peut-on **suspendre** ce run ? — la même règle que la route, à l'écran (#477).
 *
 * Un run soldé n'a plus rien à suspendre, un run déjà suspendu non plus. Un run
 * orphelin est exclu à dessein, et c'est le seul arbitrage : son hôte est mort,
 * personne ne recevra l'ordre — proposer le geste inviterait à un clic sans effet,
 * là où ce run demande à être **repris** (#349), c'est-à-dire rejoué.
 */
export function peutEtreSuspendu(execution: ResumeExecution): boolean {
  return !estSolde(execution) && !estOrphelin(execution) && !estEnPause(execution);
}

/**
 * Peut-on **interrompre** ce run (#467) ? — la même règle que la route, à l'écran.
 *
 * Une seule condition, et c'est exactement celle de l'API : un run **soldé** a rendu
 * son verdict, il n'y a plus rien à interrompre (`409`). Tout le reste s'annule,
 * arrêt sur brief compris — un run qui attend une décision est en vol, il tient un
 * hôte et le cadrage déjà payé.
 *
 * ⚠ **L'orphelin en fait partie, et c'est la divergence assumée avec
 * `peutEtreSuspendu`.** La pause l'écarte parce que personne ne recevrait l'ordre ;
 * l'annulation, elle, n'a pas besoin qu'il soit reçu — l'attente est bornée côté API
 * (`DELAI_ANNULATION_S`) et **le run est soldé de toute façon**. C'est même le cas
 * qui a fait naître le ticket : quatre runs fantômes du 22 juillet, soldés au `curl`
 * le 2026-08-24 faute d'un bouton. Les exclure ici laisserait précisément les runs
 * qu'on n'a aucun autre moyen d'éteindre hors de portée de l'interface.
 *
 * Un run **en pause** s'annule aussi : la pause n'est pas une issue, seulement un
 * robinet fermé — le run tient toujours son hôte et son plan.
 */
export function peutEtreInterrompu(execution: ResumeExecution): boolean {
  return !estSolde(execution);
}

/** Ce qui retient un run en vol — les trois causes, et rien d'autre (#474). */
export const ATTENTE_BRIEF = "brief";
export const ATTENTE_REPONSES = "reponses";
export const ATTENTE_VALIDATION = "validation";

export type CauseAttente =
  | typeof ATTENTE_BRIEF
  | typeof ATTENTE_REPONSES
  | typeof ATTENTE_VALIDATION;

/**
 * Les runs dont **une tâche** attend une décision humaine (#48).
 *
 * C'est la troisième attente, et la seule qui ne se lise pas sur le run : une
 * demande de validation porte sa tâche (`tache_id`), jamais son run, et le statut
 * du run reste `en_cours` pendant qu'elle dort. L'appariement passe donc par les
 * tâches — les deux listes que le shell tient déjà (`lib/etatGlobal`), aucun appel
 * de plus.
 *
 * C'est **le** défaut d'origine du chantier : le 2026-08-14, une attente de
 * décision humaine est restée 53 minutes indiscernable d'un run qui travaillait
 * (#355), parce que rien à l'écran ne les séparait. Un run suspendu et un run qui
 * avance affichent tous deux `en_cours` ; sans cet appariement, la liste des runs
 * referait exactement la même promesse fausse.
 *
 * Seules les demandes **en attente** comptent : une validation tranchée a rendu la
 * tâche au moteur, elle ne retient plus rien.
 */
export function runsEnAttenteDeValidation(
  validations: Validation[],
  taches: Tache[],
): Set<string> {
  const runParTache = new Map(taches.map((tache) => [tache.id, tache.run_id]));
  const runs = new Set<string>();
  for (const tacheId of tachesEnAttenteDeValidation(validations)) {
    const runId = runParTache.get(tacheId);
    if (runId) runs.add(runId);
  }
  return runs;
}

/**
 * Les **tâches** dont une demande de validation dort encore (#491).
 *
 * La moitié amont de la fonction ci-dessus, et le tour de la question qui
 * intéresse un écran centré sur les tâches : la vue pipeline colore un **nœud**,
 * pas un run — savoir que « ce run attend quelque part » ne dit pas *où*, et sur
 * un graphe de douze boîtes c'est toute l'information.
 *
 * Elle est ici, avec sa jumelle, parce que c'est **la** source qui existe : le
 * moteur n'émet pas encore le statut `en_attente_validation` de la machine à
 * états (`maestro/controltower/progression.py` le dit en toutes lettres), si bien
 * qu'une tâche arrêtée sur un humain reste `en_cours` pour tout le monde. C'est
 * exactement ce que #355 reproche à l'écran — 53 minutes d'attente indiscernables
 * d'un travail en cours —, et la file des validations est le seul endroit où le
 * fait est écrit.
 */
export function tachesEnAttenteDeValidation(
  validations: Validation[],
): Set<string> {
  const taches = new Set<string>();
  for (const validation of validations) {
    if (validation.statut !== VALIDATION_EN_ATTENTE) continue;
    taches.add(validation.tache_id);
  }
  return taches;
}

/**
 * Ce qui retient ce run, ou `null` s'il n'attend personne.
 *
 * **Les trois attentes se lisent d'abord sur le statut du run** (#571), et c'est
 * le critère du ticket : `en_attente_arbitrage` est un statut d'exécution au même
 * titre que les deux du brief, posé par la projection à la réception de la demande
 * (`state.py`). Une seule question, une seule source — le régime, le badge, la
 * ligne d'attente et son ancienneté en découlent sans que personne n'ait à savoir
 * *laquelle* des trois c'est.
 *
 * L'appariement `attendUneValidation` **reste**, en second et jamais en premier.
 * Il vient de `runsEnAttenteDeValidation` ci-dessus — passé plutôt que redéduit
 * ici, parce qu'il demande les tâches du projet quand cette fonction-ci ne doit
 * connaître qu'un run — et il ne sert plus que de filet : une trace d'avant ce
 * lot, ou une demande publiée par un producteur qui ne porte pas son run (#570).
 * Il ne pouvait pas porter la réponse seul, et c'est le défaut d'origine du
 * chantier : la demande est publiée **avant** que sa tâche n'existe (une tâche
 * sensible est stoppée avant toute exécution), donc l'appariement n'avait rien à
 * apparier au moment exact où il aurait servi — treize minutes de blocage muet
 * (#568).
 */
export function causeDAttente(
  execution: ResumeExecution,
  attendUneValidation: boolean,
): CauseAttente | null {
  if (execution.statut === EXECUTION_EN_ATTENTE_ARBITRAGE) {
    return ATTENTE_VALIDATION;
  }
  if (attendUnHumain(execution)) {
    return execution.statut === EXECUTION_EN_ATTENTE_REPONSES
      ? ATTENTE_REPONSES
      : ATTENTE_BRIEF;
  }
  return attendUneValidation ? ATTENTE_VALIDATION : null;
}

/**
 * Les cinq régimes d'un run, tels que la liste les distingue **à l'œil** (#474).
 *
 * `travaille` et `suspendu` sont tous deux « en cours » pour l'API : c'est
 * précisément la confusion que ce vocabulaire supprime. `interrompu` est le
 * verdict de #348 — l'hôte ne bat plus — et `solde` regroupe les trois issues.
 *
 * `en_pause` (#477) est le cinquième, et il ne recouvre aucun des autres : le run
 * est vivant, il bat, personne ne l'attend — quelqu'un a simplement cessé de lui
 * donner du travail. Le mot est **« en pause »** et non « suspendu », alors même
 * que le ticket dit « suspendu » : ce dernier désigne déjà, ici et à l'écran, un
 * run arrêté *sur* un humain (#474). Deux choses différentes sous un même mot
 * feraient chercher un brief à valider sur un run qu'on vient de mettre de côté.
 */
export const REGIME_TRAVAILLE = "travaille";
export const REGIME_SUSPENDU = "suspendu";
export const REGIME_EN_PAUSE = "en_pause";
export const REGIME_INTERROMPU = "interrompu";
export const REGIME_SOLDE = "solde";

export type RegimeRun =
  | typeof REGIME_TRAVAILLE
  | typeof REGIME_SUSPENDU
  | typeof REGIME_EN_PAUSE
  | typeof REGIME_INTERROMPU
  | typeof REGIME_SOLDE;

/**
 * Le régime de ce run — **l'ordre des questions est la décision**, et chacune des
 * trois premières l'emporte sur la suivante pour sa propre raison :
 *
 * 1. **soldé** d'abord : un run qui a rendu son verdict n'attend plus personne,
 *    et une demande de validation restée ouverte sur une tâche d'un run annulé le
 *    ferait passer pour vivant ;
 * 2. **interrompu** ensuite, et c'est le seul arbitrage réellement discutable :
 *    un run orphelin *arrêté sur son brief* est bien en attente, mais personne ne
 *    recevra la réponse — son hôte est mort. Le proposer comme suspendu inviterait
 *    à un geste sans effet ; il faut le **reprendre** (#349), pas lui répondre ;
 * 3. **en pause** (#477) juste après, et avant l'attente : les deux peuvent être
 *    vrais à la fois — on suspend volontiers un run arrêté sur son brief —, mais
 *    c'est la pause qui décide de ce qu'il y a à faire. Montrer « brief à valider »
 *    sur un run mis de côté enverrait approuver un cadrage dont aucune tâche ne
 *    partirait ensuite ;
 * 4. **suspendu** enfin, avant `travaille` : c'est la moitié du signal — un run
 *    qui attend quelqu'un depuis trois heures ne « travaille » pas, et c'est
 *    exactement ce que #355 reproche à l'écran d'aujourd'hui.
 */
export function regimeDuRun(
  execution: ResumeExecution,
  attendUneValidation = false,
): RegimeRun {
  if (estSolde(execution)) return REGIME_SOLDE;
  if (estOrphelin(execution)) return REGIME_INTERROMPU;
  if (estEnPause(execution)) return REGIME_EN_PAUSE;
  if (causeDAttente(execution, attendUneValidation) !== null) {
    return REGIME_SUSPENDU;
  }
  return REGIME_TRAVAILLE;
}

/**
 * Les runs **qu'on a laissés attendre**, dans l'ordre du backend (#738).
 *
 * Le pendant de `runsRelancables` sur l'autre verdict, et sa seconde moitié est
 * tout le sujet : le verdict du backend **ne suffit pas à décider ce qu'on
 * signale**. `en_souffrance` juge une attente et rien d'autre, si bien qu'il dit
 * `true` sur des runs à qui l'écran n'a rien à proposer d'utile — et le module
 * les nomme lui-même :
 *
 * - un run **orphelin** arrêté sur son brief attend bien, mais **personne ne
 *   recevra la réponse** : il faut le *reprendre* (#349), pas aller lui répondre.
 *   C'est le deuxième cran de `regimeDuRun`, écrit là depuis #474 ;
 * - un run **en pause** est le seul état arrêté où **quelqu'un a déjà décidé**
 *   (`docs/33 §3.2` : « alerter dessus, ce serait alerter sur l'exercice d'une
 *   commande qu'on offre »). Le backend l'assume comme un faux positif — la pause
 *   est un drapeau à côté du statut, pas dedans — et l'écran, lui, a déjà tranché
 *   que la pause l'emporte sur l'attente ;
 * - un run **soldé** n'attend plus rien, et le verdict rend `false` de lui-même.
 *
 * D'où le filtre : le régime **suspendu**, qui est exactement « ni soldé, ni
 * orphelin, ni en pause, et il attend quelqu'un ». Ce n'est pas une seconde règle
 * écrite ici mais **celle du dépôt**, rejouée — l'ordre de ses quatre questions
 * *est* la décision, et la recopier en trois `!estOrphelin(…) && …` la ferait
 * diverger au premier régime ajouté.
 *
 * `attendUneValidation` n'est pas demandé, et ce n'est pas un oubli : ce filtre-ci
 * n'écarte jamais un run que le verdict a retenu — `en_souffrance` implique un
 * `statut` d'attente (`STATUTS_EXECUTION_EN_ATTENTE`), donc `causeDAttente`
 * répond déjà sans l'appariement par les tâches, qui n'est là que comme filet
 * (#571). Le demander obligerait le panneau à connaître les tâches du projet pour
 * une réponse identique.
 *
 * Aucun tri, comme au-dessus : `GET /api/executions` rend ses résumés récents
 * d'abord et retrier localement poserait une seconde règle d'ordre pour rien.
 */
export function runsEnSouffrance(
  executions: ResumeExecution[],
): ResumeExecution[] {
  return executions.filter(
    (execution) =>
      estEnSouffrance(execution) && regimeDuRun(execution) === REGIME_SUSPENDU,
  );
}

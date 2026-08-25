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
  EXECUTION_ANNULEE,
  EXECUTION_ECHEC,
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
 * Ce run orphelin a-t-il un **cadrage à rejouer** (#349) ?
 *
 * Les deux moitiés sont nécessaires et aucune ne suffit. Un run vivant n'a pas à
 * être repris ; un run orphelin **sans brief approuvé** n'a rien à rejouer — il
 * s'est arrêté avant que quelqu'un ne valide son cadrage, et le relancer
 * reviendrait à repartir de son objectif brut, c'est-à-dire à sauter la validation
 * qu'il attendait encore. L'API refuse ce cas en 422 ; ne pas proposer le geste
 * évite d'offrir un bouton qui n'aboutira pas.
 */
export function estRelancable(execution: ResumeExecution): boolean {
  return estOrphelin(execution) && execution.brief_approuve === true;
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
 * `en_attente_brief` et `en_attente_reponses` n'en sont pas, et le contrat le dit
 * en toutes lettres (`lib/types`) : ce sont des états **non terminaux** — le run
 * est en vol, simplement arrêté sur quelqu'un.
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
 * `attendUneValidation` vient de `runsEnAttenteDeValidation` ci-dessus : il est
 * passé plutôt que redéduit ici, parce que l'appariement demande les tâches du
 * projet et que cette fonction-ci ne doit connaître qu'un run.
 */
export function causeDAttente(
  execution: ResumeExecution,
  attendUneValidation: boolean,
): CauseAttente | null {
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

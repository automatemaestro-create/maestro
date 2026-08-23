/**
 * Ce que l'UI sait du **cycle de vie d'un run** hors du JSX (#348, #349) : lequel
 * n'est plus porté par personne, et lequel a du cadrage à rejouer.
 *
 * Ces règles vivent ici et pas dans les composants pour la raison habituelle du
 * dépôt : elles sont **partagées** — le panneau du tableau de bord pose la question
 * « ce run est-il perdu ? », et le jour où un écran *Exécutions* la reposera, deux
 * formulations finiraient par diverger. Pendant exact de `lib/brief.ts`, sur
 * l'autre question : celui-là dit ce qu'un run attend **de quelqu'un**, celui-ci
 * dit ce qu'un run n'attend plus de **personne**.
 */

import { VITALITE_ORPHELIN, type ResumeExecution } from "./types";

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

"use client";

/**
 * Les décisions qu'un agent a tranchées **seul** pendant un run (#1026), tenues
 * à jour au rythme du shell.
 *
 * Pendant exact de `lib/useFriseRun` (#355) et de `lib/useGrapheRun` (#491) —
 * même contrat, mêmes trois propriétés, et pour les mêmes raisons :
 *
 * - **aucune portée de projet** — `GET /api/executions/{run_id}/decisions` ne
 *   prend pas de `?projet=`, par la même porte que `/frise`, `/graphe` et
 *   `/cout` : le run seul suffit à désigner ce qu'on lit ;
 * - **`null` tant qu'il n'y a rien à lire** : c'est `GET /api/executions` qui dit
 *   si un run relève de ce projet, et redemander ses décisions à chaque battement
 *   pour s'entendre répondre un 404 n'apprendrait rien à personne ;
 * - **cette lecture n'a pas d'événement à elle.** Elle se recompose du journal du
 *   run ; ce sont les signaux déjà diffusés qui la font bouger —
 *   `tache.decision` quand un agent consigne ce qu'il vient de trancher,
 *   `agent.activite` quand une question se solde sans réponse. D'où l'abonnement
 *   au **pouls** du shell (`ControlTower.revision`) plutôt qu'à un type
 *   d'événement, et sans seconde WebSocket.
 *
 * La liste **ne se vide jamais pendant un rechargement** : l'ancienne reste à
 * l'écran jusqu'à ce que la nouvelle arrive. Même raison qu'à la frise — on la
 * regarde *pendant* un run, donc au moment où les rechargements s'enchaînent, et
 * un clignotement à chaque événement rendrait illisible ce qu'elle sert à lire.
 */

import { useEffect, useState } from "react";

import { chargerDecisionsExecution } from "./api";
import type { DecisionsRun } from "./types";

export type DecisionsDuRun = {
  /** `null` tant qu'aucune lecture n'a abouti — jamais une liste vide inventée. */
  decisions: DecisionsRun | null;
  /** Aucune lecture n'a encore abouti **pour ce run**. */
  chargement: boolean;
  /** API injoignable, ou run inconnu (404) à la dernière lecture. */
  erreur: string | null;
};

export function useDecisionsRun(
  runId: string | null,
  /** Le pouls du shell (`useEtatGlobal().revision`) : une lecture par battement. */
  revision: number,
): DecisionsDuRun {
  const [decisions, setDecisions] = useState<DecisionsRun | null>(null);
  // Le run de la dernière lecture aboutie — et non un booléen : c'est lui qui
  // distingue « rien encore lu » de « lu, mais pour le run d'avant ».
  const [lu, setLu] = useState<string | null>(null);
  const [erreur, setErreur] = useState<string | null>(null);

  useEffect(() => {
    if (runId === null) return;
    let abandonne = false;
    chargerDecisionsExecution(runId)
      .then((nouvelles) => {
        if (abandonne) return;
        setDecisions(nouvelles);
        setErreur(null);
      })
      .catch((e: unknown) => {
        if (abandonne) return;
        setErreur(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        // Posé même en échec : la question « a-t-on essayé pour ce run ? » a sa
        // réponse, et la bannière d'erreur dit le reste. Laisser `chargement` à
        // vrai ferait tourner un écran de chargement sur une API éteinte.
        if (!abandonne) setLu(runId);
      });
    return () => {
      abandonne = true;
    };
  }, [runId, revision]);

  return { decisions, chargement: runId !== null && lu !== runId, erreur };
}

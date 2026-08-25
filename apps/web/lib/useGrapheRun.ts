"use client";

/**
 * Le graphe d'**un run** (#491), tenu à jour au rythme du shell.
 *
 * Pendant exact de `lib/useTachesRun` (#475), à trois différences près, et
 * chacune vient du contrat de #490 :
 *
 * - **aucune portée de projet** — `GET /api/executions/{run_id}/graphe` ne prend
 *   pas de `?projet=`, par la même porte que `/cout` : le run seul suffit à
 *   désigner ce qu'on lit ;
 * - **`null` tant qu'il n'y a rien à lire**, et pour la même raison que là-bas :
 *   c'est `GET /api/executions` qui dit si un run relève de ce projet, et
 *   redemander son graphe à chaque battement pour s'entendre répondre un 404
 *   n'apprendrait rien à personne ;
 * - **le graphe n'a pas d'événement à lui.** Il se recompose à la lecture, en
 *   joignant le plan à l'état de chaque tâche ; ce sont les signaux déjà diffusés
 *   qui le font bouger — `run.plan` quand la décomposition rend son plan,
 *   `tache.statut` quand un nœud démarre et qu'une arête s'allume,
 *   `tache.detail` quand une étape se coche. D'où l'abonnement au **pouls** du
 *   shell (`ControlTower.revision`) plutôt qu'à un type d'événement : une lecture
 *   part à chaque instant où le shell vient de relire, ce qui tient le critère
 *   « la fin d'une action fait apparaître sa suite sans rechargement » sans
 *   ouvrir une seconde WebSocket.
 *
 * Le graphe **ne se vide jamais pendant un rechargement** : l'ancien reste à
 * l'écran jusqu'à ce que le nouveau arrive, sans quoi le dessin clignoterait à
 * chaque événement d'un run qui travaille — ce qui est le cas nominal.
 */

import { useEffect, useState } from "react";

import { chargerGrapheExecution } from "./api";
import type { GrapheRun } from "./types";

export type GrapheDuRun = {
  /** `null` tant qu'aucune lecture n'a abouti — jamais un graphe vide inventé. */
  graphe: GrapheRun | null;
  /** Aucune lecture n'a encore abouti **pour ce run**. */
  chargement: boolean;
  /** API injoignable, ou run inconnu (404) à la dernière lecture. */
  erreur: string | null;
};

export function useGrapheRun(
  runId: string | null,
  /** Le pouls du shell (`useEtatGlobal().revision`) : une lecture par battement. */
  revision: number,
): GrapheDuRun {
  const [graphe, setGraphe] = useState<GrapheRun | null>(null);
  // Le run de la dernière lecture aboutie — et non un booléen : c'est lui qui
  // distingue « rien encore lu » de « lu, mais pour le run d'avant ».
  const [lu, setLu] = useState<string | null>(null);
  const [erreur, setErreur] = useState<string | null>(null);

  useEffect(() => {
    if (runId === null) return;
    let abandonne = false;
    chargerGrapheExecution(runId)
      .then((nouveau) => {
        if (abandonne) return;
        setGraphe(nouveau);
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

  return { graphe, chargement: runId !== null && lu !== runId, erreur };
}

"use client";

/**
 * La frise d'activité d'**un run** (#355), tenue à jour au rythme du shell.
 *
 * Pendant exact de `lib/useGrapheRun` (#491) — même contrat, mêmes trois
 * propriétés, et pour les mêmes raisons :
 *
 * - **aucune portée de projet** — `GET /api/executions/{run_id}/frise` ne prend
 *   pas de `?projet=`, par la même porte que `/graphe` et `/cout` : le run seul
 *   suffit à désigner ce qu'on lit ;
 * - **`null` tant qu'il n'y a rien à lire** : c'est `GET /api/executions` qui dit
 *   si un run relève de ce projet, et redemander sa frise à chaque battement pour
 *   s'entendre répondre un 404 n'apprendrait rien à personne ;
 * - **la frise n'a pas d'événement à elle.** Elle se recompose à la lecture, en
 *   fusionnant le journal du run ; ce sont les signaux déjà diffusés qui la font
 *   bouger — `tache.statut` quand une tâche démarre ou se solde,
 *   `message.inter_agents` quand un relais passe, `validation.demande` quand une
 *   tâche s'arrête sur un humain. D'où l'abonnement au **pouls** du shell
 *   (`ControlTower.revision`) plutôt qu'à un type d'événement, et sans seconde
 *   WebSocket.
 *
 * La frise **ne se vide jamais pendant un rechargement** : l'ancienne reste à
 * l'écran jusqu'à ce que la nouvelle arrive. C'est ce qui compte le plus ici —
 * elle est faite pour être regardée *pendant* un run, donc au moment où les
 * rechargements s'enchaînent, et un clignotement à chaque événement rendrait
 * illisible précisément ce qu'elle sert à lire.
 */

import { useEffect, useState } from "react";

import { chargerFriseExecution } from "./api";
import type { FriseRun } from "./types";

export type FriseDuRun = {
  /** `null` tant qu'aucune lecture n'a abouti — jamais une frise vide inventée. */
  frise: FriseRun | null;
  /** Aucune lecture n'a encore abouti **pour ce run**. */
  chargement: boolean;
  /** API injoignable, ou run inconnu (404) à la dernière lecture. */
  erreur: string | null;
};

export function useFriseRun(
  runId: string | null,
  /** Le pouls du shell (`useEtatGlobal().revision`) : une lecture par battement. */
  revision: number,
): FriseDuRun {
  const [frise, setFrise] = useState<FriseRun | null>(null);
  // Le run de la dernière lecture aboutie — et non un booléen : c'est lui qui
  // distingue « rien encore lu » de « lu, mais pour le run d'avant ».
  const [lu, setLu] = useState<string | null>(null);
  const [erreur, setErreur] = useState<string | null>(null);

  useEffect(() => {
    if (runId === null) return;
    let abandonne = false;
    chargerFriseExecution(runId)
      .then((nouvelle) => {
        if (abandonne) return;
        setFrise(nouvelle);
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

  return { frise, chargement: runId !== null && lu !== runId, erreur };
}

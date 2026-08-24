"use client";

/**
 * Les tâches d'**un run** (#475), tenues à jour au rythme du shell.
 *
 * `GET /api/taches?projet=…&run=<run_id>` (#473, docs/05 §6.0bis) est la seule
 * source qui réponde à « qu'a fait *ce* run ». Le filtrage local n'en est pas
 * une, et c'est le renversement du lot 1 : `Tache.run_id` porte le **dernier**
 * run qui a touché la tâche, or un identifiant de tâche est un slug engendré
 * depuis son contenu — donc partagé dès que deux runs décomposent le même
 * objectif, ce qui est le cas nominal d'une relance (#349). Filtrer
 * `etatGlobal.taches` là-dessus ferait disparaître de la vue d'un run les tâches
 * que son propre successeur a reprises.
 *
 * **Aucune seconde WebSocket.** Le shell en ouvre une pour toute l'application
 * (`lib/etatGlobal`) et coalesce les rafales ; une vue de run qui rouvrirait la
 * sienne doublerait connexions et requêtes pour un flux identique. Elle s'abonne
 * donc au **pouls** du shell (`ControlTower.revision`) : à chaque lecture aboutie
 * là-bas, une lecture part ici. C'est ce qui tient le critère « la vue se met à
 * jour en direct sans rechargement » sans rien réimplémenter du temps réel.
 *
 * Trois comportements à connaître :
 *
 * - **`runId` vaut `null` tant qu'il n'y a rien à lire** — le run n'est pas (ou
 *   pas encore) dans la liste du projet actif. Rien ne part alors sur le réseau :
 *   c'est `GET /api/executions` qui dit si un run relève de ce projet, et
 *   redemander ses tâches à chaque battement pour s'entendre répondre une liste
 *   vide n'apprendrait rien à personne ;
 * - **la liste ne se vide jamais pendant un rechargement** — l'ancienne reste à
 *   l'écran jusqu'à ce que la nouvelle arrive, sans quoi le Kanban clignoterait à
 *   chaque événement d'un run qui travaille ;
 * - **`chargement` est propre au run demandé** : il retombe à vrai quand on passe
 *   d'un run à un autre, si bien qu'un Kanban vide ne se lit jamais « ce run n'a
 *   rien fait » alors que sa première lecture est encore en vol.
 */

import { useEffect, useState } from "react";

import { chargerTaches, type PorteeProjet } from "./api";
import type { Tache } from "./types";

export type TachesRun = {
  taches: Tache[];
  /** Aucune lecture n'a encore abouti **pour ce run**. */
  chargement: boolean;
  /** API injoignable ou run refusé (404 `run-inconnu`) à la dernière lecture. */
  erreur: string | null;
};

export function useTachesRun(
  portee: PorteeProjet,
  runId: string | null,
  /** Le pouls du shell (`useEtatGlobal().revision`) : une lecture par battement. */
  revision: number,
): TachesRun {
  const [taches, setTaches] = useState<Tache[]>([]);
  // Le run de la dernière lecture aboutie — et non un booléen : c'est lui qui
  // distingue « rien encore lu » de « lu, mais pour le run d'avant ».
  const [lu, setLu] = useState<string | null>(null);
  const [erreur, setErreur] = useState<string | null>(null);

  useEffect(() => {
    if (runId === null) return;
    let abandonne = false;
    chargerTaches(portee, runId)
      .then((nouvelles) => {
        if (abandonne) return;
        setTaches(nouvelles);
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
  }, [portee, runId, revision]);

  return { taches, chargement: runId !== null && lu !== runId, erreur };
}

"use client";

/**
 * Le bilan d'un run (#1285), tenu à jour au rythme du shell — et les entrées du
 * journal que ses pièces citent.
 *
 * Pendant exact de `lib/useDecisionsRun` et de `lib/useFriseRun` — même contrat,
 * et pour les mêmes raisons :
 *
 * - **aucune portée de projet** — `GET /api/executions/{run_id}/bilan` ne prend
 *   pas de `?projet=` : le run seul suffit à désigner ce qu'on lit ;
 * - **`null` tant qu'il n'y a rien à demander**. Ici la règle va un cran plus
 *   loin : un run **en vol** n'a pas de bilan, par construction (il est rendu à
 *   la fin), donc l'appelant passe `null` tant que le run n'est pas soldé — et
 *   personne ne redemande à chaque battement ce qu'on sait absent ;
 * - **pas d'événement à lui.** Le bilan voyage sur une activité de run
 *   (`agent.activite`, étape `bilan`) : c'est le **pouls** du shell qui fait
 *   relire, et l'arrivée du bilan après « en rédaction » se voit ainsi sans
 *   seconde WebSocket.
 *
 * La réponse **ne se vide jamais pendant un rechargement** : l'ancienne reste
 * jusqu'à ce que la nouvelle arrive — un bilan qui clignoterait à chaque
 * événement du run voisin rendrait illisible ce qu'il sert à lire.
 */

import { useEffect, useState } from "react";

import {
  chargerBilanExecution,
  chargerJournal,
  panneDe,
  type PanneApi,
  type PorteeProjet,
} from "./api";
import {
  ORDRE_DESC,
  TAILLE_PAGE_JOURNAL_MAX,
  TRI_JOURNAL_HORODATAGE,
  type EntreeJournal,
  type ReponseBilan,
} from "./types";

export type BilanDuRun = {
  /** `null` tant qu'aucune lecture n'a abouti — jamais un bilan inventé. */
  reponse: ReponseBilan | null;
  /** Aucune lecture n'a encore abouti **pour ce run**. */
  chargement: boolean;
  /** La panne de la dernière lecture, typée (#996) — null si tout va bien. */
  erreur: PanneApi | null;
};

export function useBilanRun(
  /** Le run dont on lit le bilan — `null` tant qu'il n'est pas soldé. */
  runId: string | null,
  /** Le pouls du shell (`useEtatGlobal().revision`) : une lecture par battement. */
  revision: number,
): BilanDuRun {
  const [reponse, setReponse] = useState<ReponseBilan | null>(null);
  // Le run de la dernière lecture aboutie — et non un booléen : c'est lui qui
  // distingue « rien encore lu » de « lu, mais pour le run d'avant ».
  const [lu, setLu] = useState<string | null>(null);
  const [erreur, setErreur] = useState<PanneApi | null>(null);

  useEffect(() => {
    if (runId === null) return;
    let abandonne = false;
    chargerBilanExecution(runId)
      .then((nouvelle) => {
        if (abandonne) return;
        setReponse(nouvelle);
        setErreur(null);
      })
      .catch((e: unknown) => {
        if (abandonne) return;
        setErreur(panneDe(e));
      })
      .finally(() => {
        // Posé même en échec : « a-t-on essayé pour ce run ? » a sa réponse, et
        // l'écran dit le reste. Laisser `chargement` à vrai ferait tourner un
        // écran de chargement sur une API éteinte.
        if (!abandonne) setLu(runId);
      });
    return () => {
      abandonne = true;
    };
  }, [runId, revision]);

  const courante = reponse !== null && reponse.run_id === runId ? reponse : null;
  return { reponse: courante, chargement: runId !== null && lu !== runId, erreur };
}

export type EntreesCitees = {
  /** Les entrées lues, par identifiant (`j-0042`) — vide tant que rien n'a abouti. */
  parId: ReadonlyMap<string, EntreeJournal>;
  /** La panne de la dernière lecture — null si tout va bien. */
  erreur: PanneApi | null;
};

/**
 * Les entrées du journal que les pièces d'un bilan citent, lues **par leur
 * identifiant** (`GET /api/journal?ids=`, #1285) — où qu'elles soient dans le
 * journal, y compris au-delà de la page de 200 que la vue du run lit.
 *
 * C'est ce qui permet de rendre une pièce **en clair** : la ligne du journal
 * telle que le journal la dit (`resumeEvenement`), et non le texte écrit pour le
 * modèle. Aucune lecture quand il n'y a rien à lire.
 */
export function useEntreesCitees(
  portee: PorteeProjet,
  runId: string,
  ids: readonly string[],
  revision: number,
): EntreesCitees {
  const [parId, setParId] = useState<ReadonlyMap<string, EntreeJournal>>(
    () => new Map(),
  );
  const [erreur, setErreur] = useState<PanneApi | null>(null);
  // Une chaîne et non le tableau : un tableau recréé à chaque rendu relancerait
  // la lecture en boucle (même raison que `useJournal`).
  const cle = ids.join(",");

  useEffect(() => {
    if (cle === "") return;
    let abandonne = false;
    chargerJournal(portee, {
      runId,
      ids: cle.split(","),
      tri: TRI_JOURNAL_HORODATAGE,
      ordre: ORDRE_DESC,
      taille: TAILLE_PAGE_JOURNAL_MAX,
    })
      .then((page) => {
        if (abandonne) return;
        setParId(new Map(page.entrees.map((entree) => [entree.id, entree])));
        setErreur(null);
      })
      .catch((e: unknown) => {
        if (!abandonne) setErreur(panneDe(e));
      });
    return () => {
      abandonne = true;
    };
  }, [portee, runId, cle, revision]);

  return { parId, erreur };
}

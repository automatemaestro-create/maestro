"use client";

/**
 * L'historique du journal persisté (#478), tenu à jour au rythme du shell.
 *
 * `GET /api/journal` (contrat #183, servi depuis #478) est ce qui manquait pour
 * qu'un fil d'activité survive à un rechargement. Ce hook le lit au montage —
 * donc **avant** qu'aucun événement temps réel ne soit arrivé — puis à chaque
 * battement du shell.
 *
 * **Aucune seconde WebSocket**, même raison qu'en #475 (`useTachesRun`) : le
 * shell en ouvre une pour toute l'application et coalesce les rafales ; une vue
 * qui rouvrirait la sienne doublerait connexions et requêtes pour un flux
 * identique. On s'abonne donc au **pouls** (`useEtatGlobal().revision`), et le
 * direct du shell se superpose à l'historique le temps qu'il le rattrape
 * (`fusionnerJournal`).
 *
 * Trois comportements à connaître :
 *
 * - **Les filtres sont ceux du contrat, et ils sont servis par l'API** — `runId`
 *   pour le journal d'un run (#478), `agent` pour celui d'un agent (#266) ;
 *   aucun des deux ne restreint quoi que ce soit quand il est absent, et c'est
 *   alors la page Journal, à la portée du projet actif. Le tri **doit** se faire
 *   là-bas et non ici : une page est plafonnée à 200 entrées, donc refiltrer une
 *   page du projet entier ne montrerait d'un agent discret que le silence des
 *   autres. C'est le même raisonnement qu'en #478 pour le `run_id` — un run un
 *   peu bavard s'y serait tronqué lui-même ;
 * - **la liste ne se vide jamais pendant un rechargement** : l'ancienne reste à
 *   l'écran jusqu'à ce que la nouvelle arrive, sans quoi le fil clignoterait à
 *   chaque battement d'un run qui travaille ;
 * - **la page est plafonnée** à `TAILLE_PAGE_JOURNAL_MAX` par le backend, et le
 *   `total` sort à côté des entrées : un écran qui n'en montre qu'une partie
 *   peut le **dire** au lieu de laisser croire qu'il montre tout.
 */

import { useEffect, useState } from "react";

import { chargerJournal, panneDe, type PanneApi, type PorteeProjet } from "./api";
import { evenementDepuisEntree } from "./journal";
import {
  ORDRE_DESC,
  TAILLE_PAGE_JOURNAL_MAX,
  TRI_JOURNAL_HORODATAGE,
  type Evenement,
} from "./types";

export type JournalPersiste = {
  /** L'historique lu, du plus récent au plus ancien, en forme d'événements. */
  evenements: Evenement[];
  /** Le nombre d'entrées **avant** pagination — ce que la page ne montre pas. */
  total: number;
  /** Aucune lecture n'a encore abouti pour cette portée. */
  chargement: boolean;
  /** La panne de la dernière tentative (null si tout va bien), **typée** (#996). */
  erreur: PanneApi | null;
};

/**
 * Ce sur quoi la lecture est restreinte — les filtres du contrat #183 que cette
 * application utilise. Un champ absent (ou `null`) ne restreint rien.
 */
export type FiltresJournal = {
  runId?: string | null;
  agent?: string | null;
};

export function useJournal(
  portee: PorteeProjet,
  filtres: FiltresJournal,
  /** Le pouls du shell (`useEtatGlobal().revision`) : une lecture par battement. */
  revision: number,
): JournalPersiste {
  const [evenements, setEvenements] = useState<Evenement[]>([]);
  const [total, setTotal] = useState(0);
  // La portée de la dernière lecture aboutie — et non un booléen : c'est elle
  // qui distingue « rien encore lu » de « lu, mais pour le projet d'avant ».
  const [lu, setLu] = useState<string | null>(null);
  const [erreur, setErreur] = useState<PanneApi | null>(null);

  // Les filtres sont dépliés en variables **avant** l'effet, qui dépend d'elles
  // et jamais de l'objet : un littéral passé à l'appel change d'identité à
  // chaque rendu, donc le mettre en dépendance relancerait la lecture en boucle.
  const runId = filtres.runId ?? null;
  const agent = filtres.agent ?? null;
  const cible = `${portee} ${runId ?? ""} ${agent ?? ""}`;

  useEffect(() => {
    let abandonne = false;
    chargerJournal(portee, {
      runId: runId ?? undefined,
      agent: agent ?? undefined,
      tri: TRI_JOURNAL_HORODATAGE,
      ordre: ORDRE_DESC,
      taille: TAILLE_PAGE_JOURNAL_MAX,
    })
      .then((page) => {
        if (abandonne) return;
        setEvenements(page.entrees.map(evenementDepuisEntree));
        setTotal(page.total);
        setErreur(null);
      })
      .catch((e: unknown) => {
        if (abandonne) return;
        setErreur(panneDe(e));
      })
      .finally(() => {
        // Posé même en échec : la question « a-t-on essayé ? » a sa réponse, et
        // la bannière d'erreur dit le reste. Laisser `chargement` à vrai ferait
        // tourner un écran de chargement sur une API éteinte.
        if (!abandonne) setLu(cible);
      });
    return () => {
      abandonne = true;
    };
  }, [portee, runId, agent, cible, revision]);

  return { evenements, total, chargement: lu !== cible, erreur };
}

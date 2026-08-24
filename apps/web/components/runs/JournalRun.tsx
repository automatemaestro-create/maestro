"use client";

/**
 * Le journal **d'un run** (#478, lot 6 de #472) : ce que ce run a dit, dans
 * l'ordre où il l'a dit, et qui survit à un rechargement.
 *
 * La vue d'un run devait porter « son Kanban, sa progression et **son journal** »
 * (docs/29 §3) ; les deux premiers sont arrivés avec #475, le troisième
 * manquait — faute d'une source. Il n'y en avait pas : le fil du shell ne
 * contient que ce qui est passé par le WebSocket depuis l'ouverture de la page,
 * donc ouvrir la vue d'un run terminé la veille ne montrait rien du tout.
 * `GET /api/journal?run_id=…` (contrat #183, servi par #478) est cette source.
 *
 * Trois décisions, les mêmes que pour le Kanban de #475 et pour les mêmes
 * raisons :
 *
 * - **L'appartenance au run vient de l'API** — le filtre `run_id` du contrat —,
 *   et non d'un tri sur le fil du shell : ce fil est borné aux derniers
 *   événements reçus, donc un run un peu bavard s'y serait tronqué lui-même.
 * - **Aucune seconde WebSocket** : la lecture suit le **pouls** du shell
 *   (`revision`), et le direct que l'historique n'a pas encore rattrapé se
 *   superpose (`fusionnerJournal`) — filtré sur ce run, lui.
 * - **La ligne n'est pas réécrite** : `FilActivite` rend ici exactement ce qu'il
 *   rend au tableau de bord et sur la page Journal. Seuls son titre et son vide
 *   sont nommés — un fil de run vide ne s'explique pas comme un projet sans
 *   activité.
 */

import { useMemo } from "react";

import { FilActivite } from "@/components/FilActivite";
import type { PorteeProjet } from "@/lib/api";
import { fusionnerJournal } from "@/lib/journal";
import type { Evenement } from "@/lib/types";
import { useJournal } from "@/lib/useJournal";

export function JournalRun({
  portee,
  runId,
  /** Le fil temps réel du shell — filtré sur ce run avant d'être superposé. */
  direct,
  revision,
}: {
  portee: PorteeProjet;
  runId: string;
  direct: Evenement[];
  revision: number;
}) {
  const historique = useJournal(portee, runId, revision);

  const evenements = useMemo(
    () =>
      fusionnerJournal(
        historique.evenements,
        direct.filter((evenement) => evenement.run_id === runId),
      ),
    [historique.evenements, direct, runId],
  );

  return (
    <FilActivite
      evenements={evenements}
      titre="Journal du run"
      messageVide={
        historique.chargement
          ? "Lecture du journal de ce run…"
          : historique.erreur !== null
            ? "Journal indisponible — la lecture a échoué."
            : "Aucun événement consigné pour ce run."
      }
    />
  );
}

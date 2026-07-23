"use client";

/**
 * Le tableau de bord de la Control Tower (ticket #47) : état des agents,
 * Kanban des tâches avec réassignation manuelle, coûts par exécution (#58),
 * fil d'activité, validations humaines en attente (#48) — le tout mis à jour
 * en temps réel par WebSocket, sans rechargement (critère MVP n°4). Depuis
 * #117, l'état vient du shell (contexte partagé) et l'en-tête est la barre
 * supérieure commune.
 */

import { BanniereErreurApi } from "@/components/BanniereErreurApi";
import { FilActivite } from "@/components/FilActivite";
import { Kanban } from "@/components/Kanban";
import { PanneauAgents } from "@/components/PanneauAgents";
import { PanneauCouts } from "@/components/PanneauCouts";
import { PanneauValidations } from "@/components/PanneauValidations";
import { useEtatGlobal } from "@/lib/etatGlobal";

export default function TableauDeBord() {
  const {
    taches,
    agents,
    evenements,
    validations,
    couts,
    chargement,
    erreur,
    reassigner,
    decider,
    reglerCapacite,
  } = useEtatGlobal();

  return (
    <>
      <BanniereErreurApi erreur={erreur} />
      {chargement ? (
        <p className="text-sm text-neutral-500">Chargement de l&apos;état…</p>
      ) : (
        <>
          <PanneauValidations validations={validations} decider={decider} />
          <PanneauAgents agents={agents} reglerCapacite={reglerCapacite} />
          <Kanban taches={taches} agents={agents} reassigner={reassigner} />
          <PanneauCouts couts={couts} />
          <FilActivite evenements={evenements} />
        </>
      )}
    </>
  );
}

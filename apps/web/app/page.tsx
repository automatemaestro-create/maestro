"use client";

/**
 * Le tableau de bord de la Control Tower (ticket #47) : état des agents,
 * Kanban des tâches avec réassignation manuelle, fil d'activité, validations
 * humaines en attente (#48) — le tout mis à jour en temps réel par WebSocket,
 * sans rechargement (critère MVP n°4).
 */

import { EnTete } from "@/components/EnTete";
import { FilActivite } from "@/components/FilActivite";
import { Kanban } from "@/components/Kanban";
import { PanneauAgents } from "@/components/PanneauAgents";
import { PanneauValidations } from "@/components/PanneauValidations";
import { useControlTower } from "@/lib/useControlTower";

export default function TableauDeBord() {
  const {
    taches,
    agents,
    evenements,
    validations,
    connecte,
    chargement,
    erreur,
    reassigner,
    decider,
  } = useControlTower();

  const couts = agents
    .map((a) => a.cout_usd)
    .filter((c): c is number => c !== null);
  const coutTotal = couts.length > 0 ? couts.reduce((s, c) => s + c, 0) : null;

  return (
    <main className="mx-auto flex w-full max-w-screen-2xl flex-1 flex-col gap-6 p-4 sm:p-6">
      <EnTete connecte={connecte} coutTotal={coutTotal} />
      {erreur && (
        <p
          role="alert"
          className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800 dark:border-rose-900 dark:bg-rose-950 dark:text-rose-300"
        >
          API injoignable : {erreur} — vérifier que le backend tourne (
          <code className="font-mono">maestro-api</code>).
        </p>
      )}
      {chargement ? (
        <p className="text-sm text-neutral-500">Chargement de l&apos;état…</p>
      ) : (
        <>
          <PanneauValidations validations={validations} decider={decider} />
          <PanneauAgents agents={agents} />
          <Kanban taches={taches} agents={agents} reassigner={reassigner} />
          <FilActivite evenements={evenements} />
        </>
      )}
    </main>
  );
}

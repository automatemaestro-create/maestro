"use client";

/**
 * La vue Kanban des tâches par statut (docs/05 §2.2) et la **réassignation
 * manuelle** (EF-11/EF-20) : chaque carte porte un sélecteur d'agent qui
 * appelle `POST /api/taches/{id}/reassigner`. Les colonnes suivent la machine
 * à états du moteur (docs/03 §3) ; un statut inconnu du front tombe dans une
 * colonne « Autres » plutôt que de disparaître.
 */

import { useState } from "react";

import { formatCout, formatHeure, libelleStatut } from "@/lib/format";
import type { EtatAgent, Tache } from "@/lib/types";

type Reassigner = (tacheId: string, agent: string) => Promise<void>;

type Props = {
  taches: Tache[];
  agents: EtatAgent[];
  reassigner: Reassigner;
};

/** Les colonnes du Kanban, dans l'ordre du flux de travail. */
const COLONNES: { statut: string; titre: string; accent: string }[] = [
  { statut: "assignee", titre: "Assignées", accent: "bg-sky-500" },
  { statut: "en_cours", titre: "En cours", accent: "bg-amber-500" },
  { statut: "bloquee", titre: "Bloquées", accent: "bg-violet-500" },
  { statut: "terminee", titre: "Terminées", accent: "bg-emerald-500" },
  { statut: "echec", titre: "Échecs", accent: "bg-rose-500" },
];

export function Kanban({ taches, agents, reassigner }: Props) {
  const connus = new Set(COLONNES.map((c) => c.statut));
  const autres = taches.filter((t) => !connus.has(t.statut));
  const colonnes = [
    ...COLONNES.map((colonne) => ({
      ...colonne,
      taches: taches.filter((t) => t.statut === colonne.statut),
    })),
    ...(autres.length > 0
      ? [{ statut: "", titre: "Autres", accent: "bg-neutral-400", taches: autres }]
      : []),
  ];

  return (
    <section aria-label="Tâches (Kanban)">
      <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-neutral-500 dark:text-neutral-400">
        Tâches
      </h2>
      <div className="grid auto-cols-fr grid-flow-row gap-3 md:grid-flow-col">
        {colonnes.map((colonne) => (
          <div
            key={colonne.titre}
            className="min-w-0 rounded-lg border border-neutral-200 bg-neutral-50 p-2 dark:border-neutral-800 dark:bg-neutral-950"
          >
            <h3 className="mb-2 flex items-center gap-2 px-1 text-sm font-medium">
              <span className={`size-2 rounded-full ${colonne.accent}`} />
              {colonne.titre}
              <span className="ml-auto rounded-full bg-neutral-200 px-2 text-xs text-neutral-600 dark:bg-neutral-800 dark:text-neutral-400">
                {colonne.taches.length}
              </span>
            </h3>
            <div className="space-y-2">
              {colonne.taches.map((tache) => (
                <CarteTache
                  key={tache.id}
                  tache={tache}
                  agents={agents}
                  reassigner={reassigner}
                />
              ))}
              {colonne.taches.length === 0 && (
                <p className="px-1 pb-1 text-xs text-neutral-400 dark:text-neutral-600">
                  Aucune tâche.
                </p>
              )}
            </div>
          </div>
        ))}
      </div>
      {taches.length === 0 && (
        <p className="mt-2 text-sm text-neutral-500">
          Aucune tâche pour l&apos;instant — elles apparaîtront dès qu&apos;un run
          publiera ses événements.
        </p>
      )}
    </section>
  );
}

function CarteTache({
  tache,
  agents,
  reassigner,
}: {
  tache: Tache;
  agents: EtatAgent[];
  reassigner: Reassigner;
}) {
  const [enCours, setEnCours] = useState(false);
  const [erreur, setErreur] = useState<string | null>(null);

  const surReassignation = async (agent: string) => {
    if (!agent) return;
    setEnCours(true);
    setErreur(null);
    try {
      await reassigner(tache.id, agent);
    } catch (e) {
      setErreur(e instanceof Error ? e.message : String(e));
    } finally {
      setEnCours(false);
    }
  };

  const candidats = agents.filter((a) => a.nom !== tache.agent);

  return (
    <article className="rounded-md border border-neutral-200 bg-white p-2.5 text-sm shadow-sm dark:border-neutral-800 dark:bg-neutral-900">
      <p className="font-medium" title={tache.id}>
        {tache.titre || tache.id}
      </p>
      <p className="mt-1 text-xs text-neutral-500 dark:text-neutral-400">
        🤖 {tache.agent || "non assignée"}
        {tache.role ? ` · ${tache.role}` : ""}
      </p>
      <p className="mt-0.5 flex justify-between gap-2 text-xs text-neutral-500 dark:text-neutral-400">
        <span>{libelleStatut(tache.statut)}</span>
        <span>
          {formatCout(tache.cout_usd)}
          {tache.horodatage ? ` · ${formatHeure(tache.horodatage)}` : ""}
        </span>
      </p>
      <select
        aria-label={`Réassigner la tâche ${tache.titre || tache.id}`}
        className="mt-2 w-full rounded border border-neutral-300 bg-transparent px-1.5 py-1 text-xs text-neutral-600 disabled:opacity-50 dark:border-neutral-700 dark:text-neutral-300 dark:[&>option]:bg-neutral-900"
        value=""
        disabled={enCours || candidats.length === 0}
        onChange={(e) => void surReassignation(e.target.value)}
      >
        <option value="" disabled>
          {enCours ? "Réassignation…" : "Réassigner à…"}
        </option>
        {candidats.map((agent) => (
          <option key={agent.nom} value={agent.nom}>
            {agent.nom}
            {agent.role ? ` — ${agent.role}` : ""}
          </option>
        ))}
      </select>
      {erreur && (
        <p className="mt-1 text-xs text-rose-600 dark:text-rose-400">{erreur}</p>
      )}
    </article>
  );
}

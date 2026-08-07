"use client";

/**
 * La réassignation manuelle d'une tâche (EF-11/EF-20) : `POST
 * /api/taches/{id}/reassigner` derrière un sélecteur d'agent.
 *
 * Extrait de la carte du Kanban par #251 — sans changer d'un iota ce qu'il fait
 * — pour que le panneau de détail l'offre aussi : ouvrir une tâche, lire ses
 * étapes et conclure qu'elle est pour quelqu'un d'autre est un enchaînement
 * naturel, et refermer le panneau pour retrouver le sélecteur de la carte serait
 * un geste de trop. Un seul composant, donc un seul comportement — l'état
 * « Réassignation… » et le message d'erreur sont locaux à l'endroit d'où le
 * geste est parti.
 *
 * Son échelle typographique suit celle du socle (`text-annexe`, #245) : le lot
 * a renommé ces classes dans la carte du Kanban pendant que celui-ci en était
 * extrait, et un `text-xs` resté ici aurait échappé au socle en silence.
 */

import { useState } from "react";

import type { EtatAgent, Tache } from "@/lib/types";

export type Reassigner = (tacheId: string, agent: string) => Promise<void>;

export function SelecteurReassignation({
  tache,
  agents,
  reassigner,
  className,
}: {
  tache: Tache;
  agents: EtatAgent[];
  reassigner: Reassigner;
  className?: string;
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

  // Un agent désactivé ne reçoit plus de tâches (#86) : il n'est pas proposé
  // à la réassignation — l'API la refuserait de toute façon (422).
  const candidats = agents.filter((a) => a.nom !== tache.agent && a.actif);

  return (
    <div className={className}>
      <select
        aria-label={`Réassigner la tâche ${tache.titre || tache.id}`}
        className="w-full rounded border border-neutral-300 bg-transparent px-1.5 py-1 text-annexe text-neutral-600 disabled:opacity-50 dark:border-neutral-700 dark:text-neutral-300 dark:[&>option]:bg-neutral-900"
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
        <p className="mt-1 text-annexe text-rose-600 dark:text-rose-400">{erreur}</p>
      )}
    </div>
  );
}

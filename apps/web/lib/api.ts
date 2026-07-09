/**
 * Client REST du backend Control Tower (maestro/controltower/app.py).
 *
 * L'URL de l'API vient de `NEXT_PUBLIC_MAESTRO_API_URL` (inlinée au build par
 * Next.js) et retombe sur l'écoute locale par défaut du backend
 * (`maestro-api`, 127.0.0.1:8000). Le WebSocket dérive de la même URL.
 */

import type { EtatAgent, Tache } from "./types";

const API_URL = (
  process.env.NEXT_PUBLIC_MAESTRO_API_URL ?? "http://localhost:8000"
).replace(/\/+$/, "");

/** L'URL du flux d'événements temps réel (`WS /ws/evenements`). */
export function urlEvenements(): string {
  return API_URL.replace(/^http/, "ws") + "/ws/evenements";
}

async function chargerJson<T>(chemin: string): Promise<T> {
  const reponse = await fetch(`${API_URL}${chemin}`, { cache: "no-store" });
  if (!reponse.ok) {
    throw new Error(`${chemin} a répondu ${reponse.status}`);
  }
  return (await reponse.json()) as T;
}

/** Les tâches connues du backend — la source du Kanban. */
export function chargerTaches(): Promise<Tache[]> {
  return chargerJson<Tache[]>("/api/taches");
}

/** L'état des agents (libre/occupé, tâche courante, compteurs, coût cumulé). */
export function chargerAgents(): Promise<EtatAgent[]> {
  return chargerJson<EtatAgent[]>("/api/agents");
}

/**
 * Réassigne manuellement une tâche à un agent (`POST /api/taches/{id}/reassigner`).
 * Relaye le `detail` du backend en cas de refus (404 tâche inconnue, 422 agent
 * inconnu) : c'est le message montré à l'utilisateur.
 */
export async function reassignerTache(
  tacheId: string,
  agent: string,
): Promise<void> {
  const reponse = await fetch(
    `${API_URL}/api/taches/${encodeURIComponent(tacheId)}/reassigner`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ agent }),
    },
  );
  if (!reponse.ok) {
    let detail = `réassignation refusée (${reponse.status})`;
    try {
      const corps = (await reponse.json()) as { detail?: unknown };
      if (typeof corps.detail === "string") detail = corps.detail;
    } catch {
      // corps non JSON : on garde le message générique
    }
    throw new Error(detail);
  }
}

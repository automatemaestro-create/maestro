/**
 * Client REST du backend Control Tower (maestro/controltower/app.py).
 *
 * L'URL de l'API vient de `NEXT_PUBLIC_MAESTRO_API_URL` (inlinée au build par
 * Next.js) et retombe sur l'écoute locale par défaut du backend
 * (`maestro-api`, 127.0.0.1:8000). Le WebSocket dérive de la même URL.
 */

import type { EtatAgent, Tache, Validation } from "./types";

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

/** Les demandes de validation humaine (#48) : contexte, statut, décision. */
export function chargerValidations(): Promise<Validation[]> {
  return chargerJson<Validation[]>("/api/validations");
}

/**
 * POST JSON dont l'échec relaye le `detail` du backend : c'est le message
 * montré à l'utilisateur (404 tâche inconnue, 409 demande déjà tranchée…).
 */
async function envoyerJson(
  chemin: string,
  corps: unknown,
  refusParDefaut: string,
): Promise<void> {
  const reponse = await fetch(`${API_URL}${chemin}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(corps),
  });
  if (!reponse.ok) {
    let detail = `${refusParDefaut} (${reponse.status})`;
    try {
      const contenu = (await reponse.json()) as { detail?: unknown };
      if (typeof contenu.detail === "string") detail = contenu.detail;
    } catch {
      // corps non JSON : on garde le message générique
    }
    throw new Error(detail);
  }
}

/** Réassigne manuellement une tâche à un agent (`POST /api/taches/{id}/reassigner`). */
export function reassignerTache(tacheId: string, agent: string): Promise<void> {
  return envoyerJson(
    `/api/taches/${encodeURIComponent(tacheId)}/reassigner`,
    { agent },
    "réassignation refusée",
  );
}

/**
 * Tranche une demande de validation humaine (#48) : approuve ou refuse
 * l'action sensible (`POST /api/validations/{tache_id}/decision`). Le moteur,
 * en pause sur cette demande, reprend la tâche ou l'annule proprement.
 */
export function deciderValidation(
  tacheId: string,
  approuve: boolean,
): Promise<void> {
  return envoyerJson(
    `/api/validations/${encodeURIComponent(tacheId)}/decision`,
    { approuve },
    "décision refusée",
  );
}

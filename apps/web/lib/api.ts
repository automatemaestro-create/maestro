/**
 * Client REST du backend Control Tower (maestro/controltower/app.py).
 *
 * L'URL de l'API vient de `NEXT_PUBLIC_MAESTRO_API_URL` (inlinée au build par
 * Next.js) et retombe sur l'écoute locale par défaut du backend
 * (`maestro-api`, 127.0.0.1:8000). Le WebSocket dérive de la même URL.
 */

import type {
  CoutExecution,
  EtatAgent,
  PlaybookDetail,
  PlaybookFiche,
  Tache,
  Validation,
  VersionPlaybook,
  VersionPlaybookDetail,
} from "./types";

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

/** Le grand livre d'une exécution (#57) : coût par tâche et agrégat du run. */
export function chargerCoutExecution(runId: string): Promise<CoutExecution> {
  return chargerJson<CoutExecution>(
    `/api/executions/${encodeURIComponent(runId)}/cout`,
  );
}

/**
 * Envoi JSON (POST par défaut) dont l'échec relaye le `detail` du backend :
 * c'est le message montré à l'utilisateur (404 tâche inconnue, 409 demande
 * déjà tranchée, 422 contenu vide…).
 */
async function envoyerJson(
  chemin: string,
  corps: unknown,
  refusParDefaut: string,
  methode: "POST" | "PUT" = "POST",
): Promise<void> {
  const reponse = await fetch(`${API_URL}${chemin}`, {
    method: methode,
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

/** Les playbooks des agents (#76) : version courante et provenance de chacun. */
export function chargerPlaybooks(): Promise<PlaybookFiche[]> {
  return chargerJson<PlaybookFiche[]>("/api/playbooks");
}

/** Le playbook courant d'un agent, contenu compris (celui chargé par le moteur). */
export function chargerPlaybook(agent: string): Promise<PlaybookDetail> {
  return chargerJson<PlaybookDetail>(
    `/api/playbooks/${encodeURIComponent(agent)}`,
  );
}

/** L'historique des versions du playbook d'un agent (métadonnées seules, EF-25). */
export function chargerVersionsPlaybook(
  agent: string,
): Promise<VersionPlaybook[]> {
  return chargerJson<VersionPlaybook[]>(
    `/api/playbooks/${encodeURIComponent(agent)}/versions`,
  );
}

/** Une version passée du playbook, contenu compris. */
export function chargerVersionPlaybook(
  agent: string,
  version: number,
): Promise<VersionPlaybookDetail> {
  return chargerJson<VersionPlaybookDetail>(
    `/api/playbooks/${encodeURIComponent(agent)}/versions/${version}`,
  );
}

/**
 * Publie une nouvelle version du playbook (`PUT /api/playbooks/{agent}`, #77) :
 * le contenu intégral, qui devient la version courante chargée par les moteurs
 * construits ensuite (l'application à chaud est le lot #78).
 */
export function ecrirePlaybook(agent: string, contenu: string): Promise<void> {
  return envoyerJson(
    `/api/playbooks/${encodeURIComponent(agent)}`,
    { contenu },
    "publication refusée",
    "PUT",
  );
}

/**
 * Retour arrière (EF-25) : republie une version passée comme nouvelle courante
 * (`POST /api/playbooks/{agent}/restaurer`). L'historique reste append-only.
 */
export function restaurerPlaybook(
  agent: string,
  version: number,
): Promise<void> {
  return envoyerJson(
    `/api/playbooks/${encodeURIComponent(agent)}/restaurer`,
    { version },
    "restauration refusée",
  );
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

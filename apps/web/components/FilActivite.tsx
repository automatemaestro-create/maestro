/**
 * Le fil d'activité en direct (docs/05 §2.1 : « qui fait quoi ») : les
 * derniers événements reçus sur `WS /ws/evenements`, du plus récent au plus
 * ancien. Le fil est éphémère — il reflète le flux depuis l'ouverture de la
 * page, l'état de référence restant le REST.
 */

import { formatHeure, libelleStatut } from "@/lib/format";
import {
  EVENEMENT_AGENT_ACTIVITE,
  EVENEMENT_MESSAGE_INTER_AGENTS,
  EVENEMENT_TACHE_REASSIGNATION,
  EVENEMENT_TACHE_STATUT,
  type Evenement,
} from "@/lib/types";

const ICONES: Record<string, string> = {
  [EVENEMENT_TACHE_STATUT]: "📋",
  [EVENEMENT_TACHE_REASSIGNATION]: "🔀",
  [EVENEMENT_AGENT_ACTIVITE]: "🤖",
  [EVENEMENT_MESSAGE_INTER_AGENTS]: "✉️",
};

function resume(evenement: Evenement): string {
  const sujet = evenement.titre || evenement.tache_id || evenement.run_id;
  switch (evenement.type) {
    case EVENEMENT_TACHE_REASSIGNATION:
      return `${sujet} réassignée à ${evenement.agent}`;
    case EVENEMENT_TACHE_STATUT:
      return `${sujet} — ${libelleStatut(evenement.statut)}${
        evenement.agent ? ` (${evenement.agent})` : ""
      }`;
    case EVENEMENT_MESSAGE_INTER_AGENTS:
      return `${evenement.agent} → message${sujet ? ` (${sujet})` : ""}`;
    default:
      return `${evenement.agent || "?"}${sujet ? ` — ${sujet}` : ""}${
        evenement.statut ? ` : ${libelleStatut(evenement.statut)}` : ""
      }`;
  }
}

export function FilActivite({ evenements }: { evenements: Evenement[] }) {
  return (
    <section aria-label="Activité en direct">
      <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-neutral-500 dark:text-neutral-400">
        Activité en direct
      </h2>
      <ol className="space-y-1 text-sm">
        {evenements.map((evenement, index) => (
          <li
            key={`${evenement.horodatage}-${index}`}
            className="flex items-baseline gap-2 rounded px-1 py-0.5"
          >
            <span className="shrink-0 font-mono text-xs text-neutral-400 dark:text-neutral-500">
              {formatHeure(evenement.horodatage)}
            </span>
            <span className="shrink-0">{ICONES[evenement.type] ?? "•"}</span>
            <span className="min-w-0 truncate" title={evenement.detail || undefined}>
              {resume(evenement)}
            </span>
          </li>
        ))}
        {evenements.length === 0 && (
          <li className="text-sm text-neutral-500">
            Aucun événement reçu pour l&apos;instant.
          </li>
        )}
      </ol>
    </section>
  );
}

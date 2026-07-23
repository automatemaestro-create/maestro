/**
 * Le fil d'activité en direct (docs/05 §2.1 : « qui fait quoi ») : les
 * derniers événements reçus sur `WS /ws/evenements`, du plus récent au plus
 * ancien. Le fil est éphémère — il reflète le flux depuis l'ouverture de la
 * page, l'état de référence restant le REST.
 */

import { iconeEvenement, resumeEvenement } from "@/lib/evenements";
import { formatHeure } from "@/lib/format";
import { type Evenement } from "@/lib/types";

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
            <span className="shrink-0">{iconeEvenement(evenement)}</span>
            <span className="min-w-0 truncate" title={evenement.detail || undefined}>
              {resumeEvenement(evenement)}
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

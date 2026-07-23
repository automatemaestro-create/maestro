"use client";

/**
 * La page Paramètres (#117, lot 1 de #116) — **provisoire** : le shell a besoin
 * d'une destination pour son entrée de menu, la page structurée (sections,
 * réglages persistés) est le lot #121. En attendant, elle affiche ce qui est
 * déjà réglable, c'est-à-dire la cible de l'API, et annonce ce qui vient.
 */

import { urlApi } from "@/lib/api";

/** Les sections annoncées par #121, dans l'ordre où elles arriveront. */
const SECTIONS_A_VENIR = [
  "Apparence — thème clair/sombre et densité d'affichage (lot #118)",
  "Notifications — ce qui remonte, et comment (lot #119)",
  "Connexion — URL de l'API et du flux temps réel",
  "Modèles & fournisseurs — clés, plafonds de coût",
];

export default function PageParametres() {
  return (
    <>
      <section
        aria-label="Connexion au backend"
        className="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm dark:border-neutral-800 dark:bg-neutral-900"
      >
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-neutral-500 dark:text-neutral-400">
          Connexion
        </h2>
        <p className="text-sm text-neutral-600 dark:text-neutral-300">
          API Control Tower : <code className="font-mono">{urlApi()}</code>
        </p>
        <p className="mt-1 text-xs text-neutral-500 dark:text-neutral-400">
          Réglée au build par <code className="font-mono">NEXT_PUBLIC_MAESTRO_API_URL</code>{" "}
          — elle deviendra modifiable ici avec la page structurée (#121).
        </p>
      </section>

      <section
        aria-label="Réglages à venir"
        className="rounded-lg border border-dashed border-neutral-300 p-4 dark:border-neutral-700"
      >
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-neutral-500 dark:text-neutral-400">
          À venir
        </h2>
        <ul className="list-inside list-disc space-y-1 text-sm text-neutral-600 dark:text-neutral-400">
          {SECTIONS_A_VENIR.map((section) => (
            <li key={section}>{section}</li>
          ))}
        </ul>
      </section>
    </>
  );
}

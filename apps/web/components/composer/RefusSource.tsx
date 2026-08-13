"use client";

/**
 * Le bandeau d'un refus de source (#319) — pendant exact de `RefusMotive` (#225)
 * pour les routes de composition d'un objectif.
 *
 * Trois choses, comme sur l'écran Projets, et pour la même raison : la **phrase**
 * du backend (ce qui s'est passé), le **conseil** quand l'écran en connaît un
 * (ce qu'on peut faire) et le **motif brut** affiché tel quel — un code stable
 * vaut mieux qu'une traduction approximative quand il faut chercher de l'aide.
 *
 * Un composant à part plutôt qu'un import de `RefusMotive` : le vocabulaire n'est
 * pas le même (`conseilSource` connaît les motifs d'ingestion, et délègue à celui
 * des projets pour les motifs de racine qu'ils partagent), et un refus de source
 * porte en plus un **index** — la position de la source fautive —, ce qui change
 * l'endroit où il s'affiche et non seulement son texte.
 */

import { conseilSource } from "@/lib/sources";

export function RefusSource({
  refus,
  titre,
}: {
  refus: { motif: string; message: string };
  titre: string;
}) {
  const conseil = conseilSource(refus.motif);
  return (
    <div
      role="alert"
      className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-annexe text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200"
    >
      <p className="font-medium">
        {titre} — {refus.message}
      </p>
      {conseil && <p className="mt-1">{conseil}</p>}
      <p className="mt-1 text-amber-700 dark:text-amber-400">
        motif : <code className="font-mono">{refus.motif}</code>
      </p>
    </div>
  );
}

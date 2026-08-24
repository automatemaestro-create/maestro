"use client";

/**
 * La page « Runs » (#474, docs/05 §2.4.1) : les runs du projet actif, du plus
 * récent au plus ancien.
 *
 * Une coquille, comme « Valider le brief » et « Composer un objectif » : le contenu
 * vit dans `components/runs/`, parce que c'est lui qui se teste. La page ne porte
 * ni titre ni en-tête — la barre supérieure les dérive du menu (#117).
 */

import { ListeRuns } from "@/components/runs/ListeRuns";

export default function PageRuns() {
  return <ListeRuns />;
}

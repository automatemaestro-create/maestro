"use client";

/**
 * La page « Valider le brief » (#322, docs/05 §2.7.4) : le point de contrôle où
 * un run s'arrête avant de décomposer.
 *
 * Une coquille, comme Composer un objectif : le contenu vit dans
 * `components/brief/`, parce que c'est lui qui se teste. La page ne porte ni
 * titre ni en-tête — la barre supérieure les dérive du menu (#117).
 */

import { ValidationBriefs } from "@/components/brief/ValidationBriefs";

export default function PageBrief() {
  return <ValidationBriefs />;
}

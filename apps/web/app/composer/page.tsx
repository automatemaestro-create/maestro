"use client";

/**
 * La page « Composer un objectif » (#319, docs/05 §2.7.3) : le formulaire de
 * lancement d'un run, avec la matière qu'il embarque.
 *
 * Une coquille, comme les pages Projets et Paramètres : le contenu vit dans
 * `components/composer/`, parce que c'est lui qui se teste. La page ne porte ni
 * titre ni en-tête — la barre supérieure les dérive du menu (#117).
 */

import { ComposerObjectif } from "@/components/composer/ComposerObjectif";

export default function PageComposer() {
  return <ComposerObjectif />;
}

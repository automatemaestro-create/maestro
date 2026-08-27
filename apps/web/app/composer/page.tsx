"use client";

/**
 * La page « Composer un objectif » (#319, docs/05 §2.7.3) : le formulaire de
 * lancement d'un run, avec la matière qu'il embarque.
 *
 * Une coquille, comme les pages Projets et Paramètres : le contenu vit dans
 * `components/composer/`, parce que c'est lui qui se teste. La page ne porte ni
 * titre ni en-tête — la barre supérieure les dérive du menu (#117).
 *
 * ⚠ **Ce fichier n'est plus atteint par son URL depuis #484** : `/composer`
 * redirige vers le fil (307, `next.config.ts`), et une redirection est évaluée
 * **avant** le routage par fichiers. Il n'est pas supprimé pour autant —
 * `ComposerObjectif` reste monté et testé là où le geste a déménagé —, mais
 * n'ajoutez rien ici en comptant sur un rendu : ce qui doit se voir se met dans
 * le fil (§2.7.5).
 */

import { ComposerObjectif } from "@/components/composer/ComposerObjectif";

export default function PageComposer() {
  return <ComposerObjectif />;
}

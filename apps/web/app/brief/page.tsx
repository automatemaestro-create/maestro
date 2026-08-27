"use client";

/**
 * La page « Valider le brief » (#322, docs/05 §2.7.4) : le point de contrôle où
 * un run s'arrête avant de décomposer.
 *
 * Une coquille, comme Composer un objectif : le contenu vit dans
 * `components/brief/`, parce que c'est lui qui se teste. La page ne porte ni
 * titre ni en-tête — la barre supérieure les dérive du menu (#117).
 *
 * ⚠ **Ce fichier n'est plus atteint par son URL depuis #484** : `/brief`
 * redirige vers le fil (307, `next.config.ts`), et une redirection est évaluée
 * **avant** le routage par fichiers. Le point de contrôle, lui, n'a pas disparu
 * — c'est tout l'arbitrage du 2026-08-24 (#470, D5 tient) : il se joue dans la
 * conversation depuis #483, par les mêmes routes et les mêmes composants de
 * `components/brief/`, qui sont donc bien vivants.
 */

import { ValidationBriefs } from "@/components/brief/ValidationBriefs";

export default function PageBrief() {
  return <ValidationBriefs />;
}

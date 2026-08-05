"use client";

/**
 * La page Projets de la Control Tower (#225, docs/05 §2.7) : déclarer et gérer
 * les projets de l'utilisateur — la racine sur le disque où Maestro travaille.
 *
 * Une coquille, comme la page Paramètres : le contenu vit dans
 * `components/projets/`, parce que c'est lui qui se teste. La page ne porte ni
 * titre ni en-tête — la barre supérieure les dérive du menu (#117).
 */

import { ListeProjets } from "@/components/projets/ListeProjets";

export default function PageProjets() {
  return <ListeProjets />;
}

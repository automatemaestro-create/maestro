"use client";

/**
 * L'horloge partagée des horodatages relatifs (#250).
 *
 * Un « il y a 3 min » ne vit que si quelque chose le rafraîchit : sans cela, une
 * ligne arrivée pendant une accalmie garde son étiquette jusqu'au prochain
 * événement. D'où un battement — mais **un seul pour toute l'application** : le
 * fil d'activité, la cloche et le Journal en affichent des dizaines de lignes,
 * et autant de `setInterval` que de lignes coûterait sans rien apporter (elles
 * partagent le même instant).
 *
 * Deux détails qui expliquent la forme du module :
 *
 * - `useSyncExternalStore` plutôt qu'un `useState` par composant : c'est
 *   l'abonnement à une source **extérieure à React**, et c'est lui qui donne le
 *   `getServerSnapshot` ci-dessous ;
 * - ce `getServerSnapshot` rend `null`, et c'est volontaire. `Date.now()` ne
 *   vaut pas la même chose sur le serveur et dans le navigateur : rendre
 *   l'instant dès la première image ferait diverger l'HTML hydraté. `null` veut
 *   dire « pas encore d'horloge » — `formatHeureRelative` retombe alors sur
 *   l'heure absolue, identique des deux côtés, et la ligne passe au relatif dès
 *   que l'abonnement est en place.
 */

import { useSyncExternalStore } from "react";

/**
 * Le pas du battement. Trente secondes : la plus petite unité affichée est la
 * minute, une étiquette n'est donc jamais fausse plus d'une demi-minute.
 */
const PAS_MS = 30_000;

const abonnes = new Set<() => void>();
let minuterie: ReturnType<typeof setInterval> | null = null;
let instantane: number | null = null;

function abonner(prevenir: () => void): () => void {
  abonnes.add(prevenir);
  if (minuterie === null) {
    // Premier abonné : on pose l'instant tout de suite (React relit
    // l'instantané juste après l'abonnement et re-rend si besoin) puis on lance
    // le battement.
    instantane = Date.now();
    minuterie = setInterval(() => {
      instantane = Date.now();
      for (const abonne of abonnes) abonne();
    }, PAS_MS);
  }
  return () => {
    abonnes.delete(prevenir);
    if (abonnes.size > 0 || minuterie === null) return;
    // Plus personne n'écoute : on rend le timer. `instantane` garde sa dernière
    // valeur — le prochain abonné la rafraîchit avant que React ne la relise.
    clearInterval(minuterie);
    minuterie = null;
  };
}

const lireInstantane = () => instantane;
const lireInstantaneCoteServeur = () => null;

/**
 * L'instant courant, rafraîchi toutes les 30 s — ou `null` tant que l'horloge
 * n'a pas démarré (rendu serveur, hydratation). À passer tel quel à
 * `formatHeureRelative`.
 */
export function useHorloge(): number | null {
  return useSyncExternalStore(
    abonner,
    lireInstantane,
    lireInstantaneCoteServeur,
  );
}

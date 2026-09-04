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
 *
 * ## Deux cadences, et pourquoi pas une seule (#837)
 *
 * Le pas d'une horloge se déduit de **la plus petite unité qu'elle affiche** :
 * une étiquette n'est jamais fausse plus d'un demi-pas. Trente secondes suffisent
 * à « il y a 3 min ». Le **signe de vie** d'une tâche qui travaille (#836) se lit,
 * lui, à la seconde — « il y a 12 s » —, parce que ce qu'il montre est un geste
 * d'agent toutes les 5 à 15 secondes : à trente secondes de pas, l'étiquette
 * dirait « il y a 12 s » pendant qu'il y en a quarante, et un signe de vie qui
 * ment sur son âge ne vaut pas mieux qu'un écran immobile. D'où une **seconde
 * horloge**, au pas d'une seconde, construite par la même fabrique et partagée
 * de la même façon — un seul timer pour tous les signes de vie à l'écran, qui
 * ne tourne que tant qu'au moins un est monté. Elle n'est **pas** le pas de
 * l'autre : passer tout le fil d'activité à la seconde ferait re-rendre des
 * dizaines de lignes chaque seconde pour des étiquettes à la minute.
 */

import { useSyncExternalStore } from "react";

/**
 * Le pas du battement. Trente secondes : la plus petite unité affichée est la
 * minute, une étiquette n'est donc jamais fausse plus d'une demi-minute.
 */
const PAS_MS = 30_000;

/**
 * Le pas de l'horloge **fine** (#837) : la seconde, plus petite unité d'un
 * signe de vie (`formatAnciennete`).
 */
const PAS_FIN_MS = 1_000;

type Horloge = {
  abonner: (prevenir: () => void) => () => void;
  lire: () => number | null;
};

/**
 * Une horloge partagée au pas donné : un seul timer, lancé au premier abonné et
 * rendu au dernier. Les deux fonctions rendues sont **stables** (créées une fois
 * par horloge), ce que `useSyncExternalStore` exige pour ne pas se réabonner à
 * chaque rendu.
 */
function creerHorloge(pasMs: number): Horloge {
  const abonnes = new Set<() => void>();
  let minuterie: ReturnType<typeof setInterval> | null = null;
  let instantane: number | null = null;

  const abonner = (prevenir: () => void): (() => void) => {
    abonnes.add(prevenir);
    if (minuterie === null) {
      // Premier abonné : on pose l'instant tout de suite (React relit
      // l'instantané juste après l'abonnement et re-rend si besoin) puis on
      // lance le battement.
      instantane = Date.now();
      minuterie = setInterval(() => {
        instantane = Date.now();
        for (const abonne of abonnes) abonne();
      }, pasMs);
    }
    return () => {
      abonnes.delete(prevenir);
      if (abonnes.size > 0 || minuterie === null) return;
      // Plus personne n'écoute : on rend le timer. `instantane` garde sa
      // dernière valeur — le prochain abonné la rafraîchit avant que React ne
      // la relise.
      clearInterval(minuterie);
      minuterie = null;
    };
  };

  return { abonner, lire: () => instantane };
}

const HORLOGE = creerHorloge(PAS_MS);
const HORLOGE_FINE = creerHorloge(PAS_FIN_MS);

const lireInstantaneCoteServeur = () => null;

/**
 * L'instant courant, rafraîchi toutes les 30 s — ou `null` tant que l'horloge
 * n'a pas démarré (rendu serveur, hydratation). À passer tel quel à
 * `formatHeureRelative`.
 */
export function useHorloge(): number | null {
  return useSyncExternalStore(
    HORLOGE.abonner,
    HORLOGE.lire,
    lireInstantaneCoteServeur,
  );
}

/**
 * L'instant courant, rafraîchi **chaque seconde** — ou `null` tant que l'horloge
 * n'a pas démarré. Réservé à ce qui s'affiche à la seconde : le signe de vie
 * d'une tâche qui travaille (`formatAnciennete`, #837). Un composant qui
 * l'appelle re-rend chaque seconde : à réserver aux **feuilles**, jamais à une
 * vue entière.
 */
export function useHorlogeFine(): number | null {
  return useSyncExternalStore(
    HORLOGE_FINE.abonner,
    HORLOGE_FINE.lire,
    lireInstantaneCoteServeur,
  );
}

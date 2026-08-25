"use client";

/**
 * La fenêtre d'agrégation des régions live (#538, lot 6 de #532) : ce qui
 * transforme un flux en **une phrase**.
 *
 * `lib/annonces` sait comparer deux relevés ; il reste à décider **quand**
 * comparer, et c'est là qu'est tout le sujet du ticket. La Control Tower coalesce
 * ses rechargements à 150 ms et plafonne son flux à 50 événements : un écran
 * chargé rend donc plusieurs fois par seconde, et une région live qui parlerait à
 * chaque rendu rendrait le lecteur d'écran inutilisable — c'est la panne que ce
 * hook existe pour ne pas créer.
 *
 * Le régime est un **étranglement à front avant** :
 *
 * - un changement isolé s'annonce **tout de suite** — attendre cinq secondes pour
 *   dire « 1 tâche terminée » quand rien d'autre ne bouge serait une latence sans
 *   contrepartie, et c'est le cas le plus fréquent d'un poste calme ;
 * - tout ce qui arrive **pendant la fenêtre qui suit** est retenu et dit d'un
 *   coup à la fin — la rafale coûte donc **une** phrase, quel qu'ait été son
 *   débit, et cette phrase compare les deux bouts de la fenêtre sans rien savoir
 *   du milieu (`phraseDesChangements`).
 *
 * Deux points à connaître avant d'y toucher :
 *
 * ① **Rien n'est annoncé au montage.** Le premier relevé sert de référence et se
 *    tait : arriver sur un écran n'est pas un changement d'état, et le lecteur
 *    d'écran est déjà en train de lire la page. C'est aussi ce qui rend la région
 *    montable **à côté du contenu qu'elle décrit** — un écran qui rend son contenu
 *    après son chargement monte sa région avec les données déjà là, donc sans
 *    annoncer l'arrivée de ce qu'on vient d'ouvrir.
 *
 * ② **La clé rendue à côté du texte n'est pas décorative.** Deux annonces
 *    identiques de suite (« 1 tâche terminée » deux fenêtres d'affilée) ne
 *    changent pas le texte du nœud, donc ne changent pas le DOM, donc **ne sont
 *    pas annoncées** : une région live parle sur mutation, pas sur affectation.
 *    La clé, posée sur le nœud interne de la région, force son remplacement à
 *    chaque annonce — la mutation existe même quand la phrase se répète.
 */

import { useEffect, useRef, useState } from "react";

import { phraseDesChangements, type Mesure } from "./annonces";

/**
 * La fenêtre d'agrégation d'une annonce polie, en millisecondes.
 *
 * Cinq secondes : l'ordre de grandeur de ce qu'un lecteur d'écran met à dire une
 * phrase courte. Plus court, les annonces se marcheraient dessus ; plus long, un
 * écran vivant paraîtrait figé.
 */
export const DELAI_ANNONCE_MS = 5_000;

/**
 * La fenêtre d'agrégation d'une annonce **assertive**, en millisecondes.
 *
 * Bien plus courte : une demande d'arbitrage interrompt, donc elle ne se fait pas
 * attendre. Elle n'est pas nulle pour autant — deux validations ouvertes dans la
 * même seconde forment une file, et « 2 validations en attente » vaut mieux que
 * deux interruptions dont la seconde coupe la première.
 */
export const DELAI_ARBITRAGE_MS = 1_000;

/** Une annonce, et la clé qui garantit qu'elle sera entendue même répétée. */
export type Annonce = { texte: string; cle: number };

export function useAnnonce(
  mesures: Mesure[],
  delaiMs: number = DELAI_ANNONCE_MS,
): Annonce {
  const [annonce, setAnnonce] = useState<Annonce>({ texte: "", cle: 0 });
  // Le relevé de la dernière annonce — `null` tant qu'on n'a rien pris pour
  // référence, ce qui n'arrive qu'au premier rendu (point ① de l'en-tête).
  const annoncees = useRef<Mesure[] | null>(null);
  // Le relevé du rendu courant, lu par la minuterie quand elle se déclenche :
  // elle a été armée sur un rendu antérieur et ne doit surtout pas dire ce
  // qu'elle voyait alors.
  const courantes = useRef(mesures);
  const minuterie = useRef<ReturnType<typeof setTimeout> | null>(null);
  const derniere = useRef(0);

  // Sans tableau de dépendances : le relevé est un tableau reconstruit à chaque
  // rendu, donc jamais identique d'un rendu à l'autre — une liste de dépendances
  // ne ferait que déplacer la comparaison, que le corps fait déjà par les
  // valeurs. L'effet sort immédiatement quand rien n'a bougé, ce qui est le cas
  // nominal.
  useEffect(() => {
    // Le relevé courant se publie **ici** et non pendant le rendu (écrire un ref
    // en cours de rendu est un défaut que le lint attrape) : l'effet suit chaque
    // commit, donc une minuterie armée plus tôt lira toujours le dernier relevé.
    courantes.current = mesures;

    if (annoncees.current === null) {
      annoncees.current = mesures;
      return;
    }
    // Une annonce est déjà armée : elle dira l'état au bout de la fenêtre, il n'y
    // a rien à réarmer. C'est ici que la rafale devient une phrase.
    if (minuterie.current !== null) return;
    if (phraseDesChangements(annoncees.current, mesures) === null) return;

    const dire = () => {
      minuterie.current = null;
      derniere.current = Date.now();
      const reference = annoncees.current ?? [];
      annoncees.current = courantes.current;
      const texte = phraseDesChangements(reference, courantes.current);
      if (texte !== null) setAnnonce((avant) => ({ texte, cle: avant.cle + 1 }));
    };

    const reste = delaiMs - (Date.now() - derniere.current);
    if (reste <= 0) dire();
    else minuterie.current = setTimeout(dire, reste);
  });

  useEffect(
    () => () => {
      if (minuterie.current !== null) clearTimeout(minuterie.current);
    },
    [],
  );

  return annonce;
}

"use client";

/**
 * Le comportement partagé des surfaces déroulées de la barre supérieure (#536,
 * lot 4 de #532) : le menu d'aide, la bascule de thème, le sélecteur de projet
 * et la cloche de notifications.
 *
 * Ce hook naît d'un constat de duplication, pas d'un goût pour l'abstraction :
 * les quatre composants portaient **le même bloc de dix-huit lignes recopié à
 * l'identique** (clic extérieur, `Échap`, focus rendu au déclencheur). C'est
 * exactement la maladie que la recherche #471 décrit à propos du langage visuel
 * — « 18 recopies de carte et 26 boutons refaits sont passés quand même » — et
 * c'est pour ça que la navigation aux flèches manquait aux quatre à la fois : il
 * aurait fallu l'écrire quatre fois. Écrite une fois, elle est acquise partout,
 * et l'audit du lot 5 (#537) n'a qu'une implémentation à juger.
 *
 * **Deux familles de surface, un seul hook.** Trois des quatre sont de vrais
 * menus (des entrées `menuitem`/`menuitemradio` et rien d'autre) ; la cloche,
 * elle, est un **panneau** — sections, titres, listes, cartes portant chacune
 * deux boutons d'arbitrage. La différence n'est pas cosmétique, elle décide du
 * clavier :
 *
 * - un menu **prend le focus sur sa première entrée**, se parcourt aux flèches,
 *   et se **referme sur `Tab`** (motif ARIA APG : la tabulation sort du menu, la
 *   navigation interne appartient aux flèches) ;
 * - un panneau prend le focus sur **lui-même** et laisse `Tab` faire son travail
 *   — s'y refermer rendrait ses boutons de décision inatteignables au clavier.
 *
 * Le hook ne choisit pas par un drapeau qu'un appelant pourrait poser de
 * travers : il **regarde ce que la surface contient**. Zéro entrée de menu ⇒
 * c'est un panneau. La donnée décide, pas la configuration.
 */

import { useEffect, type RefObject } from "react";

/** Les entrées d'un menu, au sens ARIA du terme. */
const SELECTEUR_ENTREE =
  '[role="menuitem"],[role="menuitemradio"],[role="menuitemcheckbox"]';

/** Les entrées de `surface`, dans l'ordre du document, hors entrées désactivées. */
function entreesDe(surface: HTMLElement): HTMLElement[] {
  return Array.from(
    surface.querySelectorAll<HTMLElement>(SELECTEUR_ENTREE),
  ).filter(
    (element) =>
      !element.hasAttribute("disabled") &&
      element.getAttribute("aria-disabled") !== "true",
  );
}

export function useSurfaceDeroulee({
  ouvert,
  fermer,
  conteneur,
  declencheur,
  surface,
}: {
  /** L'état d'ouverture — le hook ne s'arme que quand la surface est déployée. */
  ouvert: boolean;
  /** Ferme la surface. N'a pas à toucher au focus : le hook s'en charge. */
  fermer: () => void;
  /** Englobe le déclencheur **et** la surface : c'est lui qui borne « dehors ». */
  conteneur: RefObject<HTMLElement | null>;
  /** Reçoit le focus quand on referme au clavier. */
  declencheur: RefObject<HTMLElement | null>;
  /** La surface déployée elle-même : c'est là que vivent les entrées. */
  surface: RefObject<HTMLElement | null>;
}): void {
  // Le focus entre dans la surface à l'ouverture. Sans ça, `Échap` n'est entendu
  // que par le document et le lecteur d'écran reste sur le déclencheur, à
  // annoncer un menu ouvert dont il ne lit rien.
  useEffect(() => {
    if (!ouvert) return;
    const racine = surface.current;
    if (!racine) return;
    const entrees = entreesDe(racine);
    (entrees[0] ?? racine).focus();
  }, [ouvert, surface]);

  useEffect(() => {
    if (!ouvert) return;

    const refermer = () => {
      fermer();
      declencheur.current?.focus();
    };

    const surPointeur = (evenement: PointerEvent) => {
      if (!conteneur.current?.contains(evenement.target as Node)) {
        // Fermeture à la souris : le focus **ne bouge pas**. L'utilisateur vient
        // de désigner autre chose, le lui reprendre pour le rendre au
        // déclencheur serait un vol de curseur.
        fermer();
      }
    };

    const surTouche = (evenement: KeyboardEvent) => {
      if (evenement.key === "Escape") {
        evenement.preventDefault();
        refermer();
        return;
      }

      const racine = surface.current;
      if (!racine) return;
      const entrees = entreesDe(racine);
      // Panneau (aucune entrée de menu) : ni flèches, ni fermeture sur `Tab`.
      if (entrees.length === 0) return;

      if (evenement.key === "Tab") {
        refermer();
        return;
      }

      const courant = document.activeElement as HTMLElement | null;
      const rang = courant ? entrees.indexOf(courant) : -1;
      const dernier = entrees.length - 1;
      let vise: number;

      switch (evenement.key) {
        case "ArrowDown":
          // Le focus posé sur la surface (rang -1) entre par le haut : `+1`
          // donnerait 0 dans les deux cas, mais l'écrire ainsi garde le
          // bouclage juste quand on part de la dernière entrée.
          vise = rang === -1 || rang === dernier ? 0 : rang + 1;
          break;
        case "ArrowUp":
          vise = rang <= 0 ? dernier : rang - 1;
          break;
        case "Home":
          vise = 0;
          break;
        case "End":
          vise = dernier;
          break;
        default:
          return;
      }

      evenement.preventDefault();
      entrees[vise].focus();
    };

    document.addEventListener("pointerdown", surPointeur);
    document.addEventListener("keydown", surTouche);
    return () => {
      document.removeEventListener("pointerdown", surPointeur);
      document.removeEventListener("keydown", surTouche);
    };
  }, [ouvert, fermer, conteneur, declencheur, surface]);
}

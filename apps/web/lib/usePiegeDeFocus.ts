"use client";

/**
 * Le piège de focus des surfaces modales (#536, lot 4 de #532).
 *
 * L'état mesuré par la recherche #471 (docs/30 §3.4) est sans appel : la chaîne
 * `"Tab"` n'apparaissait **nulle part** dans `apps/web`, donc aucune des trois
 * modales ne retenait le focus. `Échap` et la restauration du focus, eux,
 * étaient déjà bons partout — ce hook ne les touche pas, il ajoute la seule
 * pièce qui manquait.
 *
 * Trois arbitrages méritent leur explication, parce qu'ils sont chacun le
 * contraire de ce qu'on écrit d'habitude :
 *
 * - **Le piège est tenu par `Tab`, et par rien d'autre.** Un vrai piège se
 *   double souvent d'un filet `focusin` qui ramène le focus quand il s'échappe
 *   autrement (clic, pilotage du navigateur). Ici il ferait plus de mal que de
 *   bien : nos deux modales sont couvertes par un **voile plein écran** qui
 *   absorbe déjà les clics (`PanneauDetailTache` ferme dessus,
 *   `GuidePriseEnMain` les avale), donc l'échappée par clic n'existe pas ; et
 *   surtout la **restauration du focus** de l'appelant s'exécute pendant que la
 *   modale est encore montée — `fermer()` fait `setOuverte(null)` puis
 *   `declencheur.focus()` dans le même tour, avant que React n'ait démonté quoi
 *   que ce soit. Un filet `focusin` rattraperait ce focus-là et le renverrait
 *   dans un panneau en train de mourir, c'est-à-dire casserait précisément ce
 *   qui marchait déjà.
 * - **Les candidats sont recalculés à chaque `Tab`**, jamais mémorisés : le
 *   contenu de nos modales bouge (le guide change d'étape, le détail de tâche
 *   déplie un sélecteur de réassignation). Une liste prise à l'ouverture serait
 *   fausse dès la première interaction.
 * - **Aucun filtre géométrique.** Le réflexe est d'écarter les éléments
 *   invisibles par `offsetParent` ou `getClientRects()` — sous jsdom, qui ne
 *   calcule aucune mise en page, ces deux sondes répondent « invisible » pour
 *   **tout** élément. Le piège deviendrait un no-op dans la suite de tests, et
 *   l'audit clavier du lot 5 (#537) garderait un ✓ sur une question jamais
 *   posée. On s'en tient donc à ce que le DOM porte vraiment : `disabled`,
 *   `hidden`, `inert` et un `tabindex` négatif.
 */

import { useEffect, type RefObject } from "react";

/**
 * Ce qui peut recevoir le focus. `[tabindex]` ratisse large à dessein — les
 * valeurs négatives sont écartées juste après, et c'est le seul moyen d'attraper
 * un élément rendu focusable à la main.
 */
const SELECTEUR_FOCUSABLE = [
  "a[href]",
  "area[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "iframe",
  "audio[controls]",
  "video[controls]",
  "[contenteditable]:not([contenteditable='false'])",
  "[tabindex]",
].join(",");

/** Les éléments focusables de `racine`, dans l'ordre de tabulation du document. */
export function elementsFocusables(racine: HTMLElement): HTMLElement[] {
  return Array.from(
    racine.querySelectorAll<HTMLElement>(SELECTEUR_FOCUSABLE),
  ).filter((element) => {
    if (element.hasAttribute("disabled")) return false;
    if (element.hidden) return false;
    // `inert` neutralise tout un sous-arbre : il faut donc interroger les
    // ancêtres, pas seulement l'élément.
    if (element.closest("[inert]")) return false;
    const tabindex = element.getAttribute("tabindex");
    return tabindex === null || Number.parseInt(tabindex, 10) >= 0;
  });
}

/**
 * Retient la tabulation à l'intérieur de `surface` tant que `actif` est vrai.
 *
 * Ne pose ni `Échap`, ni le focus d'entrée, ni sa restauration : les trois sont
 * déjà en place sur les deux appelants, chacun pour des raisons qui lui sont
 * propres (le guide se refocalise à **chaque étape**, le détail de tâche laisse
 * la restauration à l'appelant, seul à connaître le déclencheur).
 */
export function usePiegeDeFocus(
  surface: RefObject<HTMLElement | null>,
  actif = true,
): void {
  useEffect(() => {
    if (!actif) return;

    const surTouche = (evenement: KeyboardEvent) => {
      if (evenement.key !== "Tab") return;
      const racine = surface.current;
      if (!racine) return;

      const candidats = elementsFocusables(racine);
      // Une surface sans rien de focusable garde quand même le focus : sans ce
      // cas, `Tab` rendrait la main au reste de la page, c'est-à-dire à ce que
      // la modale est censée mettre hors de portée.
      if (candidats.length === 0) {
        evenement.preventDefault();
        racine.focus();
        return;
      }

      const premier = candidats[0];
      const dernier = candidats[candidats.length - 1];
      const courant = document.activeElement;

      // Le focus posé sur la surface elle-même (`tabIndex={-1}`, l'état où l'on
      // se trouve juste après l'ouverture) n'est ni le premier ni le dernier
      // candidat : c'est le bord d'où l'on entre, dans un sens comme dans
      // l'autre.
      if (courant === racine || !racine.contains(courant)) {
        evenement.preventDefault();
        (evenement.shiftKey ? dernier : premier).focus();
        return;
      }
      if (evenement.shiftKey && courant === premier) {
        evenement.preventDefault();
        dernier.focus();
        return;
      }
      if (!evenement.shiftKey && courant === dernier) {
        evenement.preventDefault();
        premier.focus();
      }
    };

    document.addEventListener("keydown", surTouche);
    return () => document.removeEventListener("keydown", surTouche);
  }, [surface, actif]);
}

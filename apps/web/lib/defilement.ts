"use client";

/**
 * Suivre le bas d'un fil qui n'a **plus son propre ascenseur** (#691, lot 1 de #690).
 *
 * Jusqu'ici la conversation défilait dans une boîte à elle (`max-h-[60vh]
 * overflow-y-auto`), et « aller en bas » se disait en une ligne :
 * `conteneur.scrollTop = conteneur.scrollHeight`. La revue du 2026-08-28 a
 * retiré cette boîte — le fil est l'écran, et c'est la **page** qui le parcourt.
 * Le geste change donc de destinataire : il ne vise plus un élément qu'on tient
 * par une `ref`, mais l'ascenseur du **cadre** (`Shell`), que le fil ne connaît
 * pas et n'a pas à connaître.
 *
 * D'où ces deux fonctions, et pas une de plus :
 *
 * - `ascenseurDe` **trouve** cet ascenseur en remontant les ancêtres. Le
 *   composant de fil est partagé (`components/Conversation`, #620) et monté à
 *   deux endroits — l'écran `/chat` et l'onglet Chat d'une fiche agent : coder
 *   en dur « le div du Shell » marcherait à un endroit et pas à l'autre, et
 *   casserait en silence le jour où la page changerait d'emboîtement ;
 * - `estEnBas` **décide** si le lecteur suit encore la conversation. Sans elle,
 *   suivre le fil revient à arracher l'écran des mains de qui remonte lire —
 *   le défaut que la note de #265 nomme et que le streaming (#695) rendra
 *   permanent, une réponse qui s'écrit produisant une rafale de rendus.
 *
 * ⚠ **Aucune des deux ne mesure quoi que ce soit sous jsdom**, et c'est voulu :
 * jsdom ne calcule ni hauteur ni défilement (#308, frontière du skill
 * `/banc-mise-en-page`). `ascenseurDe` y rend l'élément racine — aucun ancêtre
 * n'ayant d'`overflow` calculé — et lui écrire un `scrollTop` ne fait rien. Le
 * fil se rend donc normalement en test, sans qu'un faux verdict de géométrie
 * puisse s'y glisser.
 */

/**
 * Distance au bas (px) sous laquelle on tient le lecteur pour « en bas ».
 *
 * Généreuse à dessein : à l'exact pixel près, une hauteur de ligne
 * fractionnaire ou une image qui finit de charger suffit à faire décrocher le
 * suivi pour toujours. Trop généreuse, on ramènerait en bas quelqu'un qui vient
 * de remonter d'un cran — d'où l'ordre de grandeur d'un message court, et pas
 * d'un écran.
 */
export const SEUIL_BAS_PX = 96;

/** Les valeurs d'`overflow-y` qui font d'un élément un ascenseur. */
const DEFILANT = /^(auto|scroll|overlay)$/;

/**
 * Le premier ancêtre de `element` qui défile — l'ascenseur qui le porte.
 *
 * Retombe sur l'élément racine du document (`document.scrollingElement`) quand
 * aucun ancêtre ne défile : c'est le cas d'une page ordinaire, où c'est la
 * fenêtre qui fait l'ascenseur. Rend `null` hors document (élément détaché,
 * rendu serveur) — l'appelant n'a alors rien à faire.
 */
export function ascenseurDe(element: Element | null): HTMLElement | null {
  if (element === null) return null;
  const vue = element.ownerDocument?.defaultView;
  if (vue == null) return null;
  for (
    let noeud = element.parentElement;
    noeud !== null;
    noeud = noeud.parentElement
  ) {
    if (DEFILANT.test(vue.getComputedStyle(noeud).overflowY)) return noeud;
  }
  return (element.ownerDocument.scrollingElement as HTMLElement | null) ?? null;
}

/** `ascenseur` est-il assez près de son bas pour qu'on le suive encore ? */
export function estEnBas(
  ascenseur: HTMLElement,
  seuil: number = SEUIL_BAS_PX,
): boolean {
  const reste =
    ascenseur.scrollHeight - ascenseur.scrollTop - ascenseur.clientHeight;
  return reste <= seuil;
}

/**
 * Le logo Maestro (#120, lot 4 de #116) — un monogramme « M » plein qui remplace
 * l'emoji 🎼 provisoire. Le tracé est en `currentColor` : il hérite de la couleur
 * du texte et reste donc lisible en thème clair comme sombre, sans dépendre d'un
 * fond. Le point au-dessus de l'échancrure figure la « levée » du chef
 * d'orchestre — le geste qui lance la mesure.
 *
 * Le mark est **autonome** : réduit au seul glyphe (sans le libellé « Maestro »),
 * il tient lieu de variante compacte quand la sidebar est repliée. La même
 * géométrie (`M7 24V10l9 8 9-8v14` + le point) est reprise à l'identique par le
 * favicon (`app/icon.svg`) et les icônes applicatives (`app/favicon.ico`,
 * `app/apple-icon.png`), toutes régénérées par `scripts/build-icons.mjs`.
 */

import type { SVGProps } from "react";

export function LogoMaestro(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 32 32" fill="none" aria-hidden="true" {...props}>
      <path
        d="M7 24V10l9 8 9-8v14"
        stroke="currentColor"
        strokeWidth={3.4}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="16" cy="6.75" r="2.3" fill="currentColor" />
    </svg>
  );
}

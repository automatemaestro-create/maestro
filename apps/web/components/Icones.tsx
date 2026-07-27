/**
 * Le jeu d'icônes du shell applicatif (#117) : des traits SVG à `currentColor`,
 * lisibles à 20 px comme à 24 px — la sidebar repliée n'est plus qu'une colonne
 * d'icônes, il leur faut un contour net plutôt qu'un emoji. Le logo de marque,
 * lui, est un mark plein à part : voir `components/Logo.tsx` (#120).
 */

import type { SVGProps } from "react";

type Props = SVGProps<SVGSVGElement>;

/** Le gabarit commun : contour seul, épaisseur et jointures homogènes. */
function Trait({ children, ...props }: Props) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...props}
    >
      {children}
    </svg>
  );
}

export function IconeTableauDeBord(props: Props) {
  return (
    <Trait {...props}>
      <rect x="3" y="3" width="7" height="7" rx="1.5" />
      <rect x="14" y="3" width="7" height="7" rx="1.5" />
      <rect x="14" y="14" width="7" height="7" rx="1.5" />
      <rect x="3" y="14" width="7" height="7" rx="1.5" />
    </Trait>
  );
}

export function IconeAgents(props: Props) {
  return (
    <Trait {...props}>
      <rect x="5" y="5" width="14" height="14" rx="2.5" />
      <rect x="9.5" y="9.5" width="5" height="5" rx="1" />
      <path d="M9 2.5V5M15 2.5V5M9 19v2.5M15 19v2.5M2.5 9H5M2.5 15H5M19 9h2.5M19 15h2.5" />
    </Trait>
  );
}

export function IconePlaybooks(props: Props) {
  return (
    <Trait {...props}>
      <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
      <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2Z" />
      <path d="M8 7h8" />
    </Trait>
  );
}

export function IconeChat(props: Props) {
  return (
    <Trait {...props}>
      <path d="M21 14.5a2.5 2.5 0 0 1-2.5 2.5H8l-4 4V5.5A2.5 2.5 0 0 1 6.5 3h12A2.5 2.5 0 0 1 21 5.5Z" />
      <path d="M8.5 8.5h8M8.5 12h5" />
    </Trait>
  );
}

export function IconeCouts(props: Props) {
  return (
    <Trait {...props}>
      <path d="M3 3v16.5A1.5 1.5 0 0 0 4.5 21H21" />
      <path d="M7.5 16.5v-4M12 16.5v-8M16.5 16.5v-5.5M21 16.5V6" />
    </Trait>
  );
}

export function IconeValidations(props: Props) {
  return (
    <Trait {...props}>
      <path d="M12 21.5s7.5-3.75 7.5-9.5V5L12 2.5 4.5 5v7c0 5.75 7.5 9.5 7.5 9.5Z" />
      <path d="m8.75 11.75 2.25 2.25 4.25-4.25" />
    </Trait>
  );
}

export function IconeParametres(props: Props) {
  return (
    <Trait {...props}>
      <path d="M4 21v-6M4 11V3M12 21v-9M12 8V3M20 21v-4M20 13V3" />
      <path d="M1.5 15h5M9.5 8h5M17.5 17h5" />
    </Trait>
  );
}

export function IconeNotifications(props: Props) {
  return (
    <Trait {...props}>
      <path d="M18 8.5a6 6 0 1 0-12 0c0 6-2.5 8-2.5 8h17S18 14.5 18 8.5Z" />
      <path d="M13.75 20.5a2 2 0 0 1-3.5 0" />
    </Trait>
  );
}

/** Thème clair (#118). */
export function IconeSoleil(props: Props) {
  return (
    <Trait {...props}>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M2 12h2M20 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4" />
    </Trait>
  );
}

/** Thème sombre (#118). */
export function IconeLune(props: Props) {
  return (
    <Trait {...props}>
      <path d="M20.5 14.5A8.5 8.5 0 0 1 9.5 3.5a8.5 8.5 0 1 0 11 11Z" />
    </Trait>
  );
}

/** Thème « système » : on suit la préférence de l'appareil (#118). */
export function IconeEcran(props: Props) {
  return (
    <Trait {...props}>
      <rect x="2.5" y="4" width="19" height="12.5" rx="2" />
      <path d="M8.5 20.5h7M12 16.5v4" />
    </Trait>
  );
}

/** Marque l'option retenue dans un menu. */
export function IconeCoche(props: Props) {
  return (
    <Trait {...props}>
      <path d="m4.5 12.5 5 5 10-11" />
    </Trait>
  );
}

export function IconeAide(props: Props) {
  return (
    <Trait {...props}>
      <circle cx="12" cy="12" r="9.5" />
      <path d="M9.25 9.25a2.75 2.75 0 0 1 5.35.9c0 1.85-2.6 2.35-2.6 3.85" />
      <path d="M12 17.25h.01" />
    </Trait>
  );
}

/**
 * L'assistant d'aide (#123) : la bulle du chat frappée d'une étincelle — elle se
 * distingue de `IconeChat` (le chat avec un agent) sans changer de famille, les
 * deux restant des conversations.
 */
export function IconeAssistant(props: Props) {
  return (
    <Trait {...props}>
      <path d="M20 13.5a2.5 2.5 0 0 1-2.5 2.5H8l-4 4V5.5A2.5 2.5 0 0 1 6.5 3h5" />
      <path d="m18 2.5 1 2.5 2.5 1-2.5 1-1 2.5-1-2.5L14.5 6 17 5Z" />
    </Trait>
  );
}

/** Croix de fermeture — panneaux et surfaces flottantes. */
export function IconeFermer(props: Props) {
  return (
    <Trait {...props}>
      <path d="M6 6l12 12M18 6 6 18" />
    </Trait>
  );
}

/** Chevron de repli de la sidebar — pointe vers la gauche (replier). */
export function IconeReplier(props: Props) {
  return (
    <Trait {...props}>
      <path d="m14.5 18-6-6 6-6" />
    </Trait>
  );
}

/** Chevron de dépli de la sidebar — pointe vers la droite (déplier). */
export function IconeDeplier(props: Props) {
  return (
    <Trait {...props}>
      <path d="m9.5 18 6-6-6-6" />
    </Trait>
  );
}

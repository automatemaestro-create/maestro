/**
 * Le jeu d'icônes de la Control Tower : des traits SVG à `currentColor`,
 * lisibles à 20 px comme à 24 px — la sidebar repliée n'est plus qu'une colonne
 * d'icônes, il leur faut un contour net plutôt qu'un emoji. Le logo de marque,
 * lui, est un mark plein à part : voir `components/Logo.tsx` (#120).
 *
 * Posé pour le shell (#117), il couvre depuis #245 **tout** ce que l'interface
 * signait à l'émoji : menu, onglets de fiche agent, types d'événement, statuts
 * de tâche et actions. Deux règles tiennent le jeu et expliquent sa forme :
 *
 * - **Un seul gabarit.** Tout passe par `Trait` — même `viewBox`, même
 *   épaisseur, mêmes jointures. Une icône qui s'en écarte se voit à côté des
 *   autres, et c'est précisément ce que le lot corrige : l'émoji apportait avec
 *   lui sa propre graisse, sa propre couleur et son propre rendu par plateforme.
 * - **Décorative, jamais informative.** `Trait` pose `aria-hidden` : l'icône
 *   double un libellé texte, elle ne le remplace pas. Là où l'émoji portait
 *   seul le sens (« 🤖 nom » pour dire « agent »), le libellé est rétabli à
 *   côté ou en `title`/`aria-label` du conteneur.
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

export function IconeProjets(props: Props) {
  return (
    <Trait {...props}>
      <path d="M3 7.5A1.5 1.5 0 0 1 4.5 6h4L11 8.5h8.5A1.5 1.5 0 0 1 21 10v7.5a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5Z" />
      <path d="M3 10.5h18" />
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

/**
 * Le Journal (#249) : des lignes horodatées, chacune précédée de sa puce — le
 * fil d'activité vu comme un registre, là où `IconeTableauDeBord` (des tuiles)
 * dit l'écran de synthèse et `IconeCouts` (des barres) la mesure.
 */
export function IconeJournal(props: Props) {
  return (
    <Trait {...props}>
      <path d="M4.5 6h.01M4.5 12h.01M4.5 18h.01" />
      <path d="M9 6h10.5M9 12h10.5M9 18h6.5" />
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

/* ------------------------------------------------------------------ *
 * Entités — ce dont l'interface parle (#245)
 * ------------------------------------------------------------------ */

/**
 * Un agent, pris individuellement — l'ancien 🤖. À distinguer de `IconeAgents`,
 * qui désigne *la population* d'agents (l'entrée de menu, la liste).
 */
export function IconeAgent(props: Props) {
  return (
    <Trait {...props}>
      <rect x="4" y="8" width="16" height="11" rx="2.5" />
      <path d="M12 4.5V8M9.5 4.5h5" />
      <path d="M9 12.5h.01M15 12.5h.01" />
      <path d="M9.5 15.75h5" />
    </Trait>
  );
}

/** Un serveur MCP — l'ancien 🔌. */
export function IconeMcp(props: Props) {
  return (
    <Trait {...props}>
      <path d="M9 2.5v5M15 2.5v5" />
      <path d="M6 7.5h12v3a6 6 0 0 1-6 6 6 6 0 0 1-6-6Z" />
      <path d="M12 16.5v5" />
    </Trait>
  );
}

/** Une tâche — l'ancien 📋. */
export function IconeTache(props: Props) {
  return (
    <Trait {...props}>
      <rect x="4.5" y="4" width="15" height="17" rx="2" />
      <path d="M9 2.5h6v3H9Z" />
      <path d="m8.5 12 2 2 4-4" />
    </Trait>
  );
}

/** Un ticket externe (GitLab, Jira) — l'ancien 🎫. */
export function IconeTicket(props: Props) {
  return (
    <Trait {...props}>
      <path d="M3 8.5V6.5A1.5 1.5 0 0 1 4.5 5h15A1.5 1.5 0 0 1 21 6.5v2a2.5 2.5 0 0 0 0 5v2a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 15.5v-2a2.5 2.5 0 0 0 0-5Z" />
      <path d="M14 5v2M14 11v2M14 17v-2" />
    </Trait>
  );
}

/** Un dossier du disque — l'ancien 📁 de l'explorateur. */
export function IconeDossier(props: Props) {
  return (
    <Trait {...props}>
      <path d="M3 6.5A1.5 1.5 0 0 1 4.5 5h4L11 7.5h8.5A1.5 1.5 0 0 1 21 9v8.5a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5Z" />
    </Trait>
  );
}

/** Un montant en dollars — l'ancien 💰. */
export function IconeMonnaie(props: Props) {
  return (
    <Trait {...props}>
      <circle cx="12" cy="12" r="9.5" />
      <path d="M14.5 9.25A2.25 2.25 0 0 0 12.25 7.5h-.75a2 2 0 0 0 0 4h1a2 2 0 0 1 0 4h-.75a2.25 2.25 0 0 1-2.25-1.75" />
      <path d="M12 6v1.5M12 16.5V18" />
    </Trait>
  );
}

/** Un compte de tokens — l'ancien 🪙. */
export function IconeJetons(props: Props) {
  return (
    <Trait {...props}>
      <ellipse cx="12" cy="6.5" rx="7.5" ry="3" />
      <path d="M4.5 6.5v5c0 1.66 3.36 3 7.5 3s7.5-1.34 7.5-3v-5" />
      <path d="M4.5 11.5v5c0 1.66 3.36 3 7.5 3s7.5-1.34 7.5-3v-5" />
    </Trait>
  );
}

/** Une durée — l'ancien ⏱. */
export function IconeChrono(props: Props) {
  return (
    <Trait {...props}>
      <circle cx="12" cy="13.5" r="7.5" />
      <path d="M12 9.5v4l2.5 1.5" />
      <path d="M9.5 2.5h5M12 2.5V6" />
    </Trait>
  );
}

/** Le grand livre d'une exécution — l'ancien 🧾. */
export function IconeGrandLivre(props: Props) {
  return (
    <Trait {...props}>
      <path d="M5 3h14v18l-2.33-1.5L14.33 21 12 19.5 9.67 21l-2.34-1.5L5 21Z" />
      <path d="M9 8h6M9 12h6M9 16h3" />
    </Trait>
  );
}

/* ------------------------------------------------------------------ *
 * Événements et statuts — ce qui arrive (#245)
 * ------------------------------------------------------------------ */

/** Le flux d'événements en direct — le battement d'un run qui tourne. */
export function IconeActivite(props: Props) {
  return (
    <Trait {...props}>
      <path d="M2.5 12h4l2.5-6 4 12 2.5-6h6" />
    </Trait>
  );
}

/** Une réassignation de tâche — l'ancien 🔀. */
export function IconeReassignation(props: Props) {
  return (
    <Trait {...props}>
      <path d="M3 7h4l10 10h4M3 17h4l3-3M14.5 9.5l2.5-2.5h4" />
      <path d="m18.5 4 2.5 3-2.5 3M18.5 14l2.5 3-2.5 3" />
    </Trait>
  );
}

/** Un changement de capacité d'agent — l'ancien 🎚️. */
export function IconeCapacite(props: Props) {
  return (
    <Trait {...props}>
      <path d="M5 21V14M5 10V3M12 21v-9M12 8V3M19 21v-5M19 12V3" />
      <path d="M2.5 12h5M9.5 10h5M16.5 14h5" />
    </Trait>
  );
}

/** Un message entre agents — l'ancien ✉️. */
export function IconeMessage(props: Props) {
  return (
    <Trait {...props}>
      <rect x="2.5" y="5" width="19" height="14" rx="2" />
      <path d="m3 7 8.25 5.5a1.5 1.5 0 0 0 1.5 0L21 7" />
    </Trait>
  );
}

/** Une demande de validation humaine — l'ancien ⚠️. */
export function IconeAlerte(props: Props) {
  return (
    <Trait {...props}>
      <path d="M10.7 3.8 2.5 18a1.5 1.5 0 0 0 1.3 2.25h16.4A1.5 1.5 0 0 0 21.5 18L13.3 3.8a1.5 1.5 0 0 0-2.6 0Z" />
      <path d="M12 9.5v4M12 17h.01" />
    </Trait>
  );
}

/** Une décision de validation — l'ancien ⚖️. */
export function IconeArbitrage(props: Props) {
  return (
    <Trait {...props}>
      <path d="M12 4v16M7 20h10" />
      <path d="M4 7h16M8 7 5 13.5h6ZM16 7l-3 6.5h6Z" />
      <path d="M2 13.5a3 3 0 0 0 6 0M13 13.5a3 3 0 0 0 6 0" />
    </Trait>
  );
}

/** La puce neutre d'un événement dont le type n'a pas d'icône dédiée. */
export function IconePuce(props: Props) {
  return (
    <Trait {...props}>
      <circle cx="12" cy="12" r="3.5" />
    </Trait>
  );
}

/** Statut « assignée » : la tâche est confiée, pas encore ouverte. */
export function IconeStatutAssignee(props: Props) {
  return (
    <Trait {...props}>
      <path d="M3.5 13.5h4l1.5 3h6l1.5-3h4" />
      <path d="M5.5 6.5 3.5 13.5v4a1.5 1.5 0 0 0 1.5 1.5h14a1.5 1.5 0 0 0 1.5-1.5v-4l-2-7a1.5 1.5 0 0 0-1.45-1.1H6.95A1.5 1.5 0 0 0 5.5 6.5Z" />
    </Trait>
  );
}

/** Statut « en cours » : la tâche tourne. */
export function IconeStatutEnCours(props: Props) {
  return (
    <Trait {...props}>
      <circle cx="12" cy="12" r="9.5" />
      <path d="M10 8.75 15.5 12 10 15.25Z" />
    </Trait>
  );
}

/** Statut « bloquée » : la tâche attend un arbitrage ou une dépendance. */
export function IconeStatutBloquee(props: Props) {
  return (
    <Trait {...props}>
      <circle cx="12" cy="12" r="9.5" />
      <path d="M10 8.75v6.5M14 8.75v6.5" />
    </Trait>
  );
}

/** Statut « terminée ». */
export function IconeStatutTerminee(props: Props) {
  return (
    <Trait {...props}>
      <circle cx="12" cy="12" r="9.5" />
      <path d="m8 12.25 2.75 2.75L16 9.5" />
    </Trait>
  );
}

/** Statut « échec ». */
export function IconeStatutEchec(props: Props) {
  return (
    <Trait {...props}>
      <circle cx="12" cy="12" r="9.5" />
      <path d="m9 9 6 6M15 9l-6 6" />
    </Trait>
  );
}

/* ------------------------------------------------------------------ *
 * Actions et repères — ce qu'on fait (#245)
 * ------------------------------------------------------------------ */

/** Créer — l'ancien ➕. */
export function IconePlus(props: Props) {
  return (
    <Trait {...props}>
      <path d="M12 5v14M5 12h14" />
    </Trait>
  );
}

/** Les permissions d'un agent — l'ancien 🛡️. */
export function IconePermissions(props: Props) {
  return (
    <Trait {...props}>
      <path d="M12 21.5s7.5-3.75 7.5-9.5V5L12 2.5 4.5 5v7c0 5.75 7.5 9.5 7.5 9.5Z" />
      <path d="M12 9v3.5M12 15.5h.01" />
    </Trait>
  );
}

/** L'historique des versions — l'ancien 🕘. */
export function IconeHistorique(props: Props) {
  return (
    <Trait {...props}>
      <path d="M3.5 12a8.5 8.5 0 1 0 2.6-6.1" />
      <path d="M3 4v4h4" />
      <path d="M12 7.5V12l3 1.75" />
    </Trait>
  );
}

/** Le lien qui ouvre un onglet extérieur — l'ancien ↗. */
export function IconeLienExterne(props: Props) {
  return (
    <Trait {...props}>
      <path d="M14 4h6v6" />
      <path d="M20 4 11 13" />
      <path d="M18 14.5v4A1.5 1.5 0 0 1 16.5 20h-11A1.5 1.5 0 0 1 4 18.5v-11A1.5 1.5 0 0 1 5.5 6h4" />
    </Trait>
  );
}

/** Le renvoi « voir la page dédiée » — l'ancien → des liens de tuile. */
export function IconeFlecheDroite(props: Props) {
  return (
    <Trait {...props}>
      <path d="M4.5 12h15M13.5 6l6 6-6 6" />
    </Trait>
  );
}

/** Le retour en arrière — l'ancien ← du fil d'Ariane des fiches agent. */
export function IconeFlecheGauche(props: Props) {
  return (
    <Trait {...props}>
      <path d="M19.5 12h-15M10.5 6l-6 6 6 6" />
    </Trait>
  );
}

/** Remonter d'un cran dans l'arborescence — l'ancien ↑ de l'explorateur. */
export function IconeFlecheHaut(props: Props) {
  return (
    <Trait {...props}>
      <path d="M12 19.5v-15M6 10.5l6-6 6 6" />
    </Trait>
  );
}

/**
 * Chevron d'ouverture d'un menu déroulant — pointe vers le bas (#280).
 *
 * Ce qui distingue le sélecteur de projet d'un simple libellé : sans cette
 * pointe, la barre supérieure affiche le projet actif sans laisser deviner
 * qu'on peut en changer là.
 */
export function IconeChevronBas(props: Props) {
  return (
    <Trait {...props}>
      <path d="m6 9.5 6 6 6-6" />
    </Trait>
  );
}

/**
 * Le guide de prise en main de la Control Tower (#122, lot 6 de #116) : la
 * définition de la visite guidée et l'état qui décide de son déclenchement.
 *
 * Le contenu vit ici, à part du composant qui le rend : ajouter une étape se
 * fait dans une seule liste, sans toucher au mécanisme de surbrillance.
 *
 * Même contrat que `lib/theme.ts` et `lib/preferences.ts`, et pour la même
 * raison : la visite se lance depuis deux endroits — automatiquement à la
 * première visite, et à la demande depuis le menu d'aide (`MenuAide`), qui ne
 * connaît pas le composant. Le stockage retient qu'elle a été vue, l'événement
 * en porte la relance.
 */

/** Clé localStorage — même espace de noms que le thème (#118) et le repli (#121). */
export const CLE_GUIDE_VU = "maestro.guide.vu";

const EVENEMENT_LANCEMENT = "maestro:guide-lancer";

export type EtapeGuide = {
  /** Identifiant stable — clé de rendu, et repère pour les tests (lot 8). */
  id: string;
  titre: string;
  texte: string;
  /**
   * Les ancres candidates, par ordre de préférence : la première **présente et
   * visible** porte la surbrillance. Plusieurs plutôt qu'une seule, parce que
   * la cible idéale peut manquer — le panneau des validations disparaît quand
   * rien n'attend d'arbitrage, le coût cumulé est masqué sous `sm` —, et
   * qu'une étape doit rester ancrée sur du réel plutôt que de sauter.
   */
  ancres: string[];
  /** Page que l'étape présente ; la visite y navigue d'elle-même. */
  chemin?: string;
};

/**
 * La visite : de la navigation aux coûts, dans l'ordre où l'on découvre le
 * poste de pilotage. Chaque étape désigne un élément réel du shell (#117) ou
 * d'une page, marqué par un attribut `data-guide`.
 */
export const ETAPES_GUIDE: EtapeGuide[] = [
  {
    id: "bienvenue",
    titre: "Bienvenue dans la Control Tower",
    texte:
      "Le poste de pilotage de Maestro : vos agents, leurs tâches et leurs coûts, en temps réel. La visite prend moins d'une minute — vous pouvez la quitter à tout moment (Échap) et la relancer depuis l'aide.",
    ancres: ['[data-guide="marque"]'],
  },
  {
    id: "navigation",
    titre: "La navigation",
    texte:
      "Toutes les sections sont ici : tableau de bord, agents, chat, coûts & analytics, validations et paramètres. Une entrée par intention — un agent se consulte d'un seul endroit, ses facettes tenant en onglets sur sa fiche. Le bouton en haut à gauche replie la barre en un simple rail d'icônes.",
    ancres: ['[data-guide="navigation"]'],
  },
  {
    id: "tableau-de-bord",
    chemin: "/",
    titre: "Le tableau de bord",
    texte:
      "L'essentiel en un écran : ce qui attend votre arbitrage, quelques indicateurs de tête — run en cours, tâches par statut, agents occupés et libres, dépense —, l'état de vos runs et un aperçu de l'activité. Le détail vit dans les pages dédiées, vers lesquelles chaque tuile renvoie : ouvrir un run donne ses tâches en colonnes. Tout se met à jour par WebSocket, sans recharger la page.",
    // `etat-runs` et non `kanban` depuis #476 : le Kanban a quitté cet écran pour
    // la vue d'un run (docs/05 §2.1/§2.2). L'ancre l'aurait suivi en silence — le
    // test qui garde ce contrat vérifie qu'une ancre est posée *quelque part*, et
    // `Kanban.tsx` la pose toujours, sur une page où cette étape ne passe pas.
    ancres: [
      '[data-guide="indicateurs"]',
      '[data-guide="etat-runs"]',
      '[data-guide="contenu"]',
    ],
  },
  {
    id: "validations",
    chemin: "/",
    titre: "Les validations humaines",
    texte:
      "Une action sensible met la tâche en pause et attend votre arbitrage, avec le contexte pour trancher. Approuver la fait reprendre, Refuser l'annule proprement — les demandes en attente s'affichent en tête du tableau de bord.",
    ancres: ['[data-guide="validations"]', '[data-guide="notifications"]'],
  },
  {
    id: "notifications",
    titre: "Les notifications",
    texte:
      "La cloche vous suit de page en page : son badge compte les validations en attente, et son panneau permet de les trancher sans quitter la page courante, puis rappelle l'activité récente notable.",
    ancres: ['[data-guide="notifications"]'],
  },
  {
    id: "couts",
    chemin: "/couts",
    titre: "Le suivi des coûts",
    texte:
      "Coûts & analytics agrège la dépense sur la période choisie : total, évolution dans le temps, répartition par agent, détail par tâche et par exécution. Le coût cumulé, lui, reste affiché en permanence dans la barre supérieure.",
    ancres: ['[data-guide="couts"]', '[data-guide="cout-cumule"]'],
  },
  {
    id: "aide",
    titre: "À vous de jouer",
    texte:
      "Ce bouton d'aide relance la visite quand vous le souhaitez. Bonne orchestration !",
    ancres: ['[data-guide="aide"]'],
  },
];

/**
 * La visite a-t-elle déjà été vue ? Un stockage indisponible (navigation
 * privée, cookies bloqués) répond « oui » : sans persistance, répondre « non »
 * relancerait la visite à **chaque** chargement de page. Elle reste accessible
 * à la demande depuis le menu d'aide.
 */
export function lireGuideVu(): boolean {
  try {
    return window.localStorage.getItem(CLE_GUIDE_VU) === "1";
  } catch {
    return true;
  }
}

/** Retient que la visite a été menée à son terme — ou quittée en route. */
export function marquerGuideVu(): void {
  try {
    window.localStorage.setItem(CLE_GUIDE_VU, "1");
  } catch {
    // Voir `lireGuideVu` : l'absence de persistance ne casse pas la visite,
    // elle ne fait que retirer son déclenchement automatique.
  }
}

/** Relance la visite depuis le début, d'où qu'on la demande. */
export function lancerGuide(): void {
  window.dispatchEvent(new CustomEvent(EVENEMENT_LANCEMENT));
}

/** S'abonne aux demandes de relance. Rend la fonction de retrait. */
export function ecouterLancementGuide(rappel: () => void): () => void {
  const surLancement = () => rappel();
  window.addEventListener(EVENEMENT_LANCEMENT, surLancement);
  return () => window.removeEventListener(EVENEMENT_LANCEMENT, surLancement);
}

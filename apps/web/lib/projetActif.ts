/**
 * La mémoire du projet actif (#279, lot 3 de #276) : l'identifiant du projet
 * dans lequel la Control Tower est ouverte, retenu d'une visite à l'autre.
 *
 * Même contrat que le thème (#118) et le repli de la sidebar (#121), et pour la
 * même raison : le réglage sera commandé de plusieurs endroits — la porte
 * d'entrée ici, le sélecteur du shell au lot 4 (#280) — qui doivent s'accorder
 * sans se connaître. Le **stockage est la source de vérité**, l'événement en est
 * la notification, y compris dans l'onglet courant que `storage` ignore.
 *
 * Le choix tient au **poste** et non à l'orchestration : deux fenêtres ouvertes
 * sur deux projets sont un usage, pas un désaccord à réconcilier côté serveur —
 * d'où le `localStorage` plutôt qu'une préférence d'API.
 *
 * Ce module ne connaît que des **identifiants** : confronter celui-ci aux
 * projets réellement déclarés est le travail de `lib/etatProjetActif`, et c'est
 * ce qui le garde sans dépendance — les tests peuvent poser un projet retenu
 * sans embarquer le client REST.
 *
 * ── Ce que #1293 y change : retenu n'est plus ouvert ─────────────────────────
 *
 * Jusqu'ici l'identifiant retenu **faisait entrer** : relu au démarrage, il
 * passait la porte sans s'arrêter. C'est ce qui ramenait, à chaque démarrage, le
 * tableau de bord d'avant l'atelier (docs/43 §2.1). Il reste retenu d'une visite
 * à l'autre, mais il ne sert plus qu'à **proposer** : c'est le « Reprendre » de la
 * porte d'entrée. Entrer est un geste de la **session**, et c'est le second
 * repère de ce module — dans le `sessionStorage`, parce qu'une session est
 * exactement ce qu'il distingue :
 *
 * - un **démarrage** (la coque qu'on rouvre, un onglet neuf) trouve la session
 *   vide, donc la porte ;
 * - une navigation interne ne recharge rien, et un **rechargement** garde sa
 *   session — la page revient sans repasser par la porte, ce que la garde de
 *   #279 promettait déjà (« un rechargement retrouve sa page »).
 *
 * Aucun `if (electron)` ici ni ailleurs (ENF-12) : la coque et l'onglet ont l'un
 * et l'autre une session qui naît à l'ouverture et meurt à la fermeture.
 */

/** Clé localStorage — même espace de noms que le thème et la sidebar (#118). */
export const CLE_PROJET_ACTIF = "maestro.projet.actif";

const EVENEMENT_PROJET_ACTIF = "maestro:projet-actif";

/** L'identifiant retenu de la dernière visite, ou `null` s'il n'y en a pas. */
export function lireProjetActifId(): string | null {
  try {
    const id = window.localStorage.getItem(CLE_PROJET_ACTIF);
    return id === null || id === "" ? null : id;
  } catch {
    // Stockage indisponible (navigation privée, cookies bloqués) : le choix
    // reste celui de la session, il ne sera simplement pas mémorisé.
    return null;
  }
}

/** Mémorise le choix et prévient les abonnés. Silencieux si le stockage refuse. */
export function ecrireProjetActifId(id: string | null): void {
  try {
    if (id === null) window.localStorage.removeItem(CLE_PROJET_ACTIF);
    else window.localStorage.setItem(CLE_PROJET_ACTIF, id);
  } catch {
    // Voir `lireProjetActifId` : l'absence de persistance ne casse pas le choix.
  }
  // La valeur voyage dans l'événement : les abonnés suivent même quand le
  // stockage est indisponible et qu'une relecture rendrait l'ancienne.
  window.dispatchEvent(new CustomEvent(EVENEMENT_PROJET_ACTIF, { detail: id }));
}

/**
 * Clé sessionStorage — cette session est entrée dans un projet (#1293). Même
 * espace de noms que le reste ; la valeur ne dit **pas** lequel : le projet ouvert
 * reste celui de `CLE_PROJET_ACTIF`, que le sélecteur du shell change en cours de
 * session. Le repère ne dit qu'une chose, qu'on a déjà passé la porte.
 */
export const CLE_ENTREE_SESSION = "maestro.session.entree";

/**
 * Vrai si cette session a déjà passé la porte — un rechargement, pas un
 * démarrage. Stockage refusé : faux, donc la porte à chaque chargement ; c'est
 * le prix d'une fenêtre qui ne retient rien, et il se paie d'un geste.
 */
export function lireEntreeDeSession(): boolean {
  try {
    return window.sessionStorage.getItem(CLE_ENTREE_SESSION) === "1";
  } catch {
    return false;
  }
}

/** Note que cette session est entrée dans un projet. Silencieux si le stockage refuse. */
export function marquerEntreeDeSession(): void {
  try {
    window.sessionStorage.setItem(CLE_ENTREE_SESSION, "1");
  } catch {
    // Voir `lireEntreeDeSession` : l'entrée vaut pour la page, pas au-delà.
  }
}

/**
 * Suit les changements de projet actif, d'où qu'ils viennent : un autre contrôle
 * de la même page (événement interne) ou un autre onglet (`storage`). Rend la
 * fonction de retrait.
 */
export function ecouterProjetActif(
  rappel: (id: string | null) => void,
): () => void {
  const surInterne = (evenement: Event) => {
    rappel((evenement as CustomEvent<string | null>).detail);
  };
  const surStockage = (evenement: StorageEvent) => {
    if (evenement.key !== CLE_PROJET_ACTIF) return;
    rappel(lireProjetActifId());
  };
  window.addEventListener(EVENEMENT_PROJET_ACTIF, surInterne);
  window.addEventListener("storage", surStockage);
  return () => {
    window.removeEventListener(EVENEMENT_PROJET_ACTIF, surInterne);
    window.removeEventListener("storage", surStockage);
  };
}

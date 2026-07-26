/**
 * Les préférences d'affichage du navigateur (#121) : celles que l'API ne porte
 * pas — elles tiennent au poste, pas à l'orchestration — et qui vivent donc
 * dans le `localStorage`.
 *
 * Même contrat que `lib/theme.ts`, et pour la même raison : le réglage est
 * offert à deux endroits (le bouton de la barre supérieure et la section
 * Apparence des Paramètres), qui doivent s'accorder sans se connaître. Le
 * stockage est la source de vérité, l'événement en est la notification — y
 * compris dans le même onglet, que `storage` ignore.
 */

/** Clé localStorage — même espace de noms que le thème (#118). */
export const CLE_SIDEBAR_REPLIEE = "maestro.sidebar.repliee";

const EVENEMENT_REPLI = "maestro:sidebar-repliee";

/** Le repli persisté, ou déplié à défaut (première visite, stockage bloqué). */
export function lireRepliSidebar(): boolean {
  try {
    return window.localStorage.getItem(CLE_SIDEBAR_REPLIEE) === "1";
  } catch {
    // Stockage indisponible (navigation privée, cookies bloqués) : le repli
    // reste celui de la session, il ne sera simplement pas mémorisé.
    return false;
  }
}

/** Mémorise le repli et prévient les abonnés. Silencieux si le stockage refuse. */
export function ecrireRepliSidebar(repliee: boolean): void {
  try {
    window.localStorage.setItem(CLE_SIDEBAR_REPLIEE, repliee ? "1" : "0");
  } catch {
    // Voir `lireRepliSidebar` : l'absence de persistance ne casse pas la bascule.
  }
  // La valeur voyage dans l'événement : les abonnés suivent même quand le
  // stockage est indisponible et que `lireRepliSidebar` rendrait l'ancienne.
  window.dispatchEvent(new CustomEvent(EVENEMENT_REPLI, { detail: repliee }));
}

/**
 * Suit les changements de repli, d'où qu'ils viennent : un autre contrôle de la
 * même page (événement interne) ou un autre onglet (`storage`). Rend la
 * fonction de retrait.
 */
export function ecouterRepliSidebar(
  rappel: (repliee: boolean) => void,
): () => void {
  const surInterne = (evenement: Event) => {
    rappel((evenement as CustomEvent<boolean>).detail);
  };
  const surStockage = (evenement: StorageEvent) => {
    if (evenement.key !== CLE_SIDEBAR_REPLIEE) return;
    rappel(lireRepliSidebar());
  };
  window.addEventListener(EVENEMENT_REPLI, surInterne);
  window.addEventListener("storage", surStockage);
  return () => {
    window.removeEventListener(EVENEMENT_REPLI, surInterne);
    window.removeEventListener("storage", surStockage);
  };
}

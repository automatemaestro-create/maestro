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

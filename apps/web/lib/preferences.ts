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
 *
 * ── Ce que #925 y change : deux préférences, un seul contrat ─────────────────
 *
 * La troisième zone du shell (docs/35 §3) retient son ouverture « comme celle
 * de la barre latérale ». Ce module n'en portait qu'une, et sa mécanique était
 * écrite **en dur** : une clé, un nom d'événement, trois fonctions qui les
 * nomment. La recopier aurait donné deux implémentations à tenir d'accord,
 * c'est-à-dire le premier moyen pour qu'une préférence cesse de suivre un
 * onglet voisin sans que personne ne le remarque.
 *
 * Elle est donc **généralisée** : `preferenceBooleenne` fabrique le trio, et
 * l'API historique (`lireRepliSidebar`, `ecrireRepliSidebar`,
 * `ecouterRepliSidebar`) n'a pas bougé d'un caractère — trois fichiers et trois
 * suites en dépendent, et ce lot ne change aucun comportement existant.
 *
 * Le **défaut est `false` pour les deux**, et ce n'est pas une commodité : dans
 * les deux cas c'est l'état qui ne surprend pas (barre dépliée, colonne
 * fermée), donc celui qu'une première visite ou un stockage refusé doit rendre.
 * La valeur stockée est `"1"` / `"0"` — tout ce qui n'est pas `"1"` vaut
 * `false`, y compris une clé absente ou salie par une version antérieure.
 */

/** Le trio d'une préférence : lire l'état, l'écrire, suivre ses changements. */
type PreferenceBooleenne = {
  lire: () => boolean;
  ecrire: (valeur: boolean) => void;
  ecouter: (rappel: (valeur: boolean) => void) => () => void;
};

/**
 * Fabrique le trio d'une préférence booléenne du poste.
 *
 * `cle` est celle du `localStorage`, `evenement` le nom de l'événement interne
 * — deux préférences ne peuvent pas le partager, sans quoi une bascule
 * réveillerait les abonnés de l'autre.
 */
function preferenceBooleenne(
  cle: string,
  evenement: string,
): PreferenceBooleenne {
  const lire = (): boolean => {
    try {
      return window.localStorage.getItem(cle) === "1";
    } catch {
      // Stockage indisponible (navigation privée, cookies bloqués) : l'état
      // reste celui de la session, il ne sera simplement pas mémorisé.
      return false;
    }
  };

  const ecrire = (valeur: boolean): void => {
    try {
      window.localStorage.setItem(cle, valeur ? "1" : "0");
    } catch {
      // Voir `lire` : l'absence de persistance ne casse pas la bascule.
    }
    // La valeur voyage dans l'événement : les abonnés suivent même quand le
    // stockage est indisponible et que `lire` rendrait l'ancienne.
    window.dispatchEvent(new CustomEvent(evenement, { detail: valeur }));
  };

  const ecouter = (rappel: (valeur: boolean) => void): (() => void) => {
    const surInterne = (recu: Event) => {
      rappel((recu as CustomEvent<boolean>).detail);
    };
    const surStockage = (recu: StorageEvent) => {
      if (recu.key !== cle) return;
      rappel(lire());
    };
    window.addEventListener(evenement, surInterne);
    window.addEventListener("storage", surStockage);
    return () => {
      window.removeEventListener(evenement, surInterne);
      window.removeEventListener("storage", surStockage);
    };
  };

  return { lire, ecrire, ecouter };
}

/** Clé localStorage — même espace de noms que le thème (#118). */
export const CLE_SIDEBAR_REPLIEE = "maestro.sidebar.repliee";

/**
 * Clé de l'ouverture de la colonne de droite (#925) — même espace de noms, et
 * un nom de **zone** plutôt qu'un nom de composant : c'est la conversation qui
 * s'y installe (#926), et la clé lui survivra.
 */
export const CLE_CONVERSATION_OUVERTE = "maestro.conversation.ouverte";

const repliSidebar = preferenceBooleenne(
  CLE_SIDEBAR_REPLIEE,
  "maestro:sidebar-repliee",
);

const colonneConversation = preferenceBooleenne(
  CLE_CONVERSATION_OUVERTE,
  "maestro:conversation-ouverte",
);

/** Le repli persisté, ou déplié à défaut (première visite, stockage bloqué). */
export function lireRepliSidebar(): boolean {
  return repliSidebar.lire();
}

/** Mémorise le repli et prévient les abonnés. Silencieux si le stockage refuse. */
export function ecrireRepliSidebar(repliee: boolean): void {
  repliSidebar.ecrire(repliee);
}

/**
 * Suit les changements de repli, d'où qu'ils viennent : un autre contrôle de la
 * même page (événement interne) ou un autre onglet (`storage`). Rend la
 * fonction de retrait.
 */
export function ecouterRepliSidebar(
  rappel: (repliee: boolean) => void,
): () => void {
  return repliSidebar.ecouter(rappel);
}

/**
 * L'ouverture persistée de la colonne de droite, ou **fermée** à défaut.
 *
 * Fermée est le défaut du chantier et non une prudence de plus : c'est ce qui
 * rend ce lot mergeable seul — une colonne repliée ne change aucun écran
 * (docs/35 §5).
 */
export function lireConversationOuverte(): boolean {
  return colonneConversation.lire();
}

/** Mémorise l'ouverture de la colonne et prévient les abonnés. */
export function ecrireConversationOuverte(ouverte: boolean): void {
  colonneConversation.ecrire(ouverte);
}

/** Suit l'ouverture de la colonne, d'où qu'elle vienne. Rend le retrait. */
export function ecouterConversationOuverte(
  rappel: (ouverte: boolean) => void,
): () => void {
  return colonneConversation.ecouter(rappel);
}

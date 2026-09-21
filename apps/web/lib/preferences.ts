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
 * ── Ce que #1107 y change : un défaut se décide, il ne se subit pas ─────────
 *
 * Le défaut valait `false` pour les deux, au nom de « l'état qui ne surprend
 * pas ». Pour la barre latérale c'est toujours vrai. Pour la colonne de droite
 * ce n'en était pas un : c'était l'état qui ne **changeait rien**, écrit quand
 * la colonne était encore vide (#925, docs/35 §5), et personne ne l'a rediscuté
 * quand #926 l'a remplie ni quand #1106 y a mis les gestes du fil. Or le critère
 * C2 du jalon dit que la conversation est visible depuis n'importe quel écran.
 * Le défaut est donc **arbitré** (#1107, commentaire « ## Variante retenue » du
 * ticket) : la colonne est **ouverte au premier passage, au large seulement**.
 *
 * Deux conséquences dans ce module, et ce sont les seules :
 *
 * 1. **Le défaut est un paramètre**, et une fonction plutôt qu'un booléen : le
 *    défaut de la colonne se résout contre la largeur de la fenêtre, donc à
 *    l'appel — un booléen figé au chargement du module vaudrait pour toute la
 *    session, et le module est importé avant même que quoi que ce soit soit
 *    rendu.
 * 2. **`"0"` cesse d'être confondu avec une clé absente.** La lecture rendait
 *    `false` pour tout ce qui n'était pas `"1"` ; avec un défaut à vrai, cela
 *    rouvrirait la colonne à chaque visite de qui vient de la replier. Une clé
 *    absente (première visite) ou salie (valeur d'une version antérieure) rend
 *    désormais le **défaut** ; `"1"` et `"0"` sont les deux seuls **choix**, et
 *    ils sont tenus.
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
 * réveillerait les abonnés de l'autre. `defaut` est ce que rend `lire` quand
 * **personne n'a choisi** : première visite, valeur illisible, stockage refusé.
 */
function preferenceBooleenne(
  cle: string,
  evenement: string,
  defaut: () => boolean = () => false,
): PreferenceBooleenne {
  const lire = (): boolean => {
    try {
      const brut = window.localStorage.getItem(cle);
      // Les deux seules valeurs qu'`ecrire` produit sont les deux seules qui
      // comptent pour un choix. Tout le reste — clé absente, ou salie par une
      // version antérieure — n'est pas un choix : c'est l'absence de choix,
      // donc le défaut (point 2 de l'en-tête).
      if (brut === "1") return true;
      if (brut === "0") return false;
      return defaut();
    } catch {
      // Stockage indisponible (navigation privée, cookies bloqués) : l'état est
      // celui d'un premier passage — le défaut s'applique, il ne sera
      // simplement pas mémorisé. Traiter le refus comme un « non » ferait
      // qu'une fenêtre privée rendrait un autre produit que la fenêtre d'à côté.
      return defaut();
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

/**
 * La largeur à partir de laquelle la colonne **prend sa place** au lieu de
 * recouvrir le contenu — `lg`, le seuil que le shell emploie déjà (#925).
 *
 * ⚠ Ce `64rem` est le `lg` de Tailwind, et il doit rester d'accord avec les
 * `max-lg:` / `lg:` de `ColonneConversation` : c'est la **même** frontière, dite
 * une fois en CSS et une fois en JavaScript, parce qu'aucun des deux ne sait
 * lire l'autre. Un test la garde (`shell.test.tsx`, « résout le défaut sur le
 * seuil même du recouvrement »). Le jour où un troisième appelant en a besoin,
 * c'est cette constante qu'il importe — jamais la chaîne recopiée.
 */
export const REQUETE_COLONNE_AU_LARGE = "(min-width: 64rem)";

const repliSidebar = preferenceBooleenne(
  CLE_SIDEBAR_REPLIEE,
  "maestro:sidebar-repliee",
);

/**
 * Le défaut arbitré par #1107 : **ouverte au premier passage, au large
 * seulement**.
 *
 * Les deux moitiés sont indissociables. Ouverte, parce qu'un projet est déjà
 * choisi quand le shell est monté (`PorteProjet`) — c'est le partage que VS Code
 * fait depuis sa v1.104, dont la barre secondaire s'ouvre sur un dossier ouvert
 * et reste masquée sur une fenêtre vide ; ici la « fenêtre vide » est la porte
 * d'entrée, qui ne monte pas ce cadre du tout. Au large seulement, parce que
 * sous `lg` la colonne **recouvre** (#925) : un poste neuf en fenêtre étroite
 * trouverait son travail sous 320 px de conversation, ce que les variantes de
 * #1107 ont rendu et que le regard neuf a écarté en toutes lettres.
 */
const colonneConversation = preferenceBooleenne(
  CLE_CONVERSATION_OUVERTE,
  "maestro:conversation-ouverte",
  () => window.matchMedia(REQUETE_COLONNE_AU_LARGE).matches,
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
 * L'ouverture **choisie** de la colonne de droite, ou le défaut de #1107 quand
 * personne n'a choisi : ouverte au large, fermée en dessous de `lg`.
 *
 * ⚠ Ne se lit **qu'après l'hydratation** (`Shell`), comme le repli ci-dessus :
 * ni le `localStorage` ni la largeur de la fenêtre n'existent au rendu serveur,
 * et les lire pendant le rendu ferait diverger les deux arbres.
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

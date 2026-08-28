/**
 * La mémoire de la conversation ouverte (#696) : **laquelle** des conversations
 * d'un fil l'écran montre, retenue d'une visite à l'autre.
 *
 * C'est le troisième critère du ticket — « la conversation ouverte survit à un
 * rechargement de la page » — et il ne se tient pas tout seul : sans mémoire, un
 * rechargement retombe sur « la plus récente », qui est la bonne réponse tant
 * qu'on n'a rien ouvert d'autre et la mauvaise dès qu'on relit un fil ancien.
 *
 * Trois choses commandent le dessin :
 *
 * - **par agent, et non par écran.** La conversation ouverte est une propriété
 *   du **fil**, pas de la vue : `@dev` dans le chat global et l'onglet Chat de la
 *   fiche `dev` servent le même stockage (`GET /api/chat/dev`), ils doivent donc
 *   montrer la même conversation. Une clé par agent, et changer de destinataire
 *   retrouve *sa* conversation plutôt que d'en imposer une commune ;
 * - **au poste, pas à l'orchestration** — même raison que le thème (#118), le
 *   repli de la barre (#121) et le projet actif (#279) : deux fenêtres ouvertes
 *   sur deux conversations sont un usage, pas un désaccord à réconcilier côté
 *   serveur. D'où le `localStorage`, dans le même espace de noms ;
 * - **le stockage est la source de vérité, l'événement en est la notification** —
 *   le contrat des trois autres, repris tel quel. Ce qui s'y ajoute est un
 *   **abonnement** (`useConversationOuverte`) : le choix n'est pas recopié dans
 *   un `useState` que `lib/useChat` devrait resynchroniser à chaque changement de
 *   destinataire, il est **lu** de la mémoire à chaque rendu. C'est ce qui le
 *   rend insensible au remontage — la `key` de projet du `Shell` (#281) reconstruit
 *   la page, elle ne touche pas au stockage —, et ce qui évite d'écrire l'état
 *   deux fois, ce dont un lecteur d'écran comme un rechargement finiraient par
 *   voir la version en retard.
 *
 * ⚠ Ce module ne connaît que des **identifiants** : confronter celui-ci aux
 * conversations réellement servies est le travail de `lib/useChat`, qui oublie
 * une mémoire périmée plutôt que de laisser l'écran sur un 404. C'est ce qui le
 * garde sans dépendance — les tests peuvent poser une conversation retenue sans
 * embarquer le client REST (même partage que `lib/projetActif`).
 */

import { useCallback, useSyncExternalStore } from "react";

/** Préfixe localStorage — même espace de noms que le thème et le projet actif (#118). */
export const CLE_CONVERSATION_OUVERTE = "maestro.chat.conversation";

const EVENEMENT_CONVERSATION_OUVERTE = "maestro:conversation-ouverte";

/** La clé du fil d'un agent : une par agent, voir l'en-tête. */
function cle(agent: string): string {
  return `${CLE_CONVERSATION_OUVERTE}.${agent}`;
}

/**
 * La conversation retenue pour cet agent, ou `""` s'il n'y en a pas — auquel cas
 * l'API sert la plus récente, c'est-à-dire le comportement d'avant ce lot.
 */
export function lireConversationOuverte(agent: string): string {
  try {
    return window.localStorage.getItem(cle(agent)) ?? "";
  } catch {
    // Stockage indisponible (navigation privée, cookies bloqués) : le choix
    // reste celui de la session, il ne sera simplement pas mémorisé.
    return "";
  }
}

/** Mémorise la conversation ouverte de cet agent. Silencieux si le stockage refuse. */
export function ecrireConversationOuverte(agent: string, id: string): void {
  try {
    if (id === "") window.localStorage.removeItem(cle(agent));
    else window.localStorage.setItem(cle(agent), id);
  } catch {
    // Voir `lireConversationOuverte` : l'absence de persistance ne casse pas la
    // bascule, elle la limite à la durée de la page.
  }
  window.dispatchEvent(new CustomEvent(EVENEMENT_CONVERSATION_OUVERTE));
}

/**
 * Suit les changements, d'où qu'ils viennent : cette page (événement interne) ou
 * un autre onglet (`storage`). Le rappel ne porte **aucune valeur** — les
 * abonnés relisent, chacun pour son agent, ce qui évite d'avoir à filtrer une
 * charge utile par nom de fil.
 */
function abonner(rappel: () => void): () => void {
  window.addEventListener(EVENEMENT_CONVERSATION_OUVERTE, rappel);
  window.addEventListener("storage", rappel);
  return () => {
    window.removeEventListener(EVENEMENT_CONVERSATION_OUVERTE, rappel);
    window.removeEventListener("storage", rappel);
  };
}

/** Rendu serveur et première image : aucune mémoire, donc « la plus récente ». */
const lireCoteServeur = () => "";

/**
 * La conversation retenue pour cet agent, **abonnée** — elle se rafraîchit dès
 * qu'on en ouvre une autre, ici ou dans un autre onglet.
 *
 * `useSyncExternalStore` plutôt qu'un `useState` semé dans un effet (même choix
 * que `lib/horloge`) : le stockage *est* l'état, le recopier en donnerait un
 * second à tenir d'accord, et le lire pendant le rendu désaccorderait le HTML du
 * serveur de celui de l'hydratation — d'où l'instantané serveur ci-dessus, qui
 * rend la question sans objet plutôt que de la traiter.
 */
export function useConversationOuverte(agent: string): string {
  const lire = useCallback(() => lireConversationOuverte(agent), [agent]);
  return useSyncExternalStore(abonner, lire, lireCoteServeur);
}

/**
 * L'assistant d'aide de la Control Tower (#123, lot 7 de #116) : le nom du canal
 * et la commande d'ouverture du panneau flottant.
 *
 * Même contrat que `lib/guide.ts`, et pour la même raison : le panneau s'ouvre
 * depuis deux endroits — son propre bouton flottant, et le menu d'aide
 * (`MenuAide`) qui ne connaît pas le composant. L'événement porte l'ouverture,
 * le composant reste seul maître de son état.
 *
 * Le canal est un fil de chat ordinaire côté API (`/api/chat/assistance`,
 * `maestro/controltower/assistance.py`) : le panneau se branche donc sur
 * `useChat` comme la page Chat, historique REST et temps réel WebSocket compris.
 */

/**
 * Le nom du fil d'assistance — le segment d'URL de `/api/chat/{agent}` et le
 * `agent` des événements `chat.message` correspondants. Réservé côté backend :
 * aucun agent personnalisé ne peut le prendre.
 */
export const AGENT_ASSISTANCE = "assistance";

const EVENEMENT_OUVERTURE = "maestro:assistance-ouvrir";

/**
 * Le mot d'accueil, affiché sur un fil vide. Il vit côté client (et non en
 * premier message persisté) pour deux raisons : ouvrir le panneau ne doit rien
 * écrire, et un fil réellement vide reste distinguable d'une conversation.
 */
export const ACCUEIL_ASSISTANCE =
  "Bonjour ! Je réponds à vos questions sur la Control Tower : à quoi sert une page, où trouver un réglage, comment trancher une validation. Posez votre question ci-dessous.";

/**
 * Des questions d'amorce, proposées tant que la conversation n'a pas commencé :
 * elles montrent le périmètre de l'assistant mieux qu'une phrase d'explication,
 * et évitent la page blanche du premier usage.
 *
 * ⚠ **Chaque libellé tient en `CALIBRE_AMORCE` caractères au plus** (#908 —
 * même règle que `AMORCES_ORCHESTRATION`, même raison : une amorce ne
 * s'enveloppe jamais sur elle-même, `whitespace-nowrap`, donc sa longueur est
 * toute sa largeur). Raccourcir n'a rien changé à ce que chacune demande, et
 * `tests/test_assistance.py` le tient d'un autre côté : chacune doit encore
 * trouver son sujet dans le repli (`sujet_assistance`), en démo comme sans
 * fournisseur — le mot qui porte le sujet (« tableau de bord », « validation »,
 * « coûts », « playbook ») est resté dans chaque libellé.
 */
export const AMORCES_ASSISTANCE: string[] = [
  "Que dit le tableau de bord ?",
  "Approuver une validation ?",
  "Où voir les coûts ?",
  "Modifier un playbook ?",
];

/** Ouvre le panneau d'assistance, d'où qu'on le demande. */
export function ouvrirAssistant(): void {
  window.dispatchEvent(new CustomEvent(EVENEMENT_OUVERTURE));
}

/** S'abonne aux demandes d'ouverture. Rend la fonction de retrait. */
export function ecouterOuvertureAssistant(rappel: () => void): () => void {
  const surOuverture = () => rappel();
  window.addEventListener(EVENEMENT_OUVERTURE, surOuverture);
  return () => window.removeEventListener(EVENEMENT_OUVERTURE, surOuverture);
}

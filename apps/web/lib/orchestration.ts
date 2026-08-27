/**
 * Le fil global côté navigateur (#269, lot 2 de #244) : à qui l'on parle sur
 * `/chat`, et comment une **mention** y change d'interlocuteur.
 *
 * Le canal lui-même est un fil de chat ordinaire côté API — `/api/chat/{agent}`
 * avec `orchestrateur` pour nom de fil (`maestro/controltower/orchestration.py`,
 * #268), au même titre qu'`assistance` (#123, `lib/assistance`). Il n'y a donc
 * ici ni client HTTP ni hook : `useChat` sert les trois canaux, et c'est ce qui
 * garantit que le fil global et le fil d'un agent ne peuvent pas diverger — ils
 * lisent le même stockage par le même chemin.
 *
 * ## La mention est un changement de destinataire, jamais une copie
 *
 * Écrire `@dev ajoute la pagination` depuis `/chat` **n'écrit pas** dans le fil
 * global : le message part dans le fil de `dev`, celui-là même que sert l'onglet
 * Chat de sa fiche. C'est le seul dessin qui tienne le critère « les deux ne
 * divergent pas » : dupliquer le message dans les deux fils créerait deux
 * historiques d'une même conversation, et le premier rechargement les montrerait
 * déjà désaccordés (l'un porte la réponse, l'autre la copie d'avant).
 *
 * L'écran, lui, ne change pas : la mention déplace le **destinataire** du fil
 * affiché, elle ne navigue pas. Le fil par agent reste la vue détaillée — on y
 * va par un renvoi, quand on veut le profil, le playbook et les permissions
 * autour de la conversation.
 */

/**
 * Le nom du fil global — le segment d'URL de `/api/chat/{agent}` et le `agent`
 * des événements `chat.message` correspondants. Réservé côté backend
 * (`maestro.agents.store.NOMS_RESERVES`) : aucun agent personnalisé ne peut le
 * prendre. Il vaut l'acteur du cycle de vie d'un run (`events.ACTEUR_RUN`) à
 * dessein — le fil et le journal parlent du même orchestrateur.
 */
export const AGENT_ORCHESTRATION = "orchestrateur";

/** Son rôle affiché, celui du journal (`events.ROLE_RUN`). */
export const ROLE_ORCHESTRATION = "Orchestrateur";

/**
 * Comment le fil le nomme dans ses libellés — « Écrire à l'orchestration… ».
 * Séparé du nom technique : celui-ci voyage dans les URL et les événements,
 * celui-là se lit à l'écran.
 */
export const INTERLOCUTEUR_ORCHESTRATION = "l'orchestration";

/**
 * Le mot d'accueil d'un fil vide. Il vit côté client (et non en premier message
 * persisté) pour la raison qui vaut déjà pour l'assistant (`lib/assistance`) :
 * ouvrir l'écran ne doit rien écrire, et un fil réellement vide doit rester
 * distinguable d'une conversation.
 */
export const ACCUEIL_ORCHESTRATION =
  "Dites ce qu'il y a à faire — j'ouvre un run, je découpe l'objectif en tâches et je les confie aux agents compétents. Une question sur l'état en cours n'ouvre rien : j'y réponds. Pour vous adresser à un agent précis sans quitter cet écran, commencez par « @ » suivi de son nom.";

/**
 * Des amorces proposées tant que la conversation n'a pas commencé. Elles montrent
 * le périmètre du fil mieux qu'une phrase d'explication — et, ici, la **frontière
 * qui compte** : une demande de travail ouvre un run, une question n'ouvre rien
 * (`maestro/controltower/orchestration.py`). Les deux premières ouvrent, les deux
 * dernières non.
 */
export const AMORCES_ORCHESTRATION: string[] = [
  "Ajoute la pagination à la liste des projets",
  "Corrige le tri des tâches du Kanban",
  "Où en sont les runs ?",
  "Qu'est-ce qui attend mon arbitrage ?",
];

/** Le caractère qui ouvre une mention. */
const MENTION = "@";

/** Une mention détachée du brouillon : à qui l'on parle, et ce qui reste à dire. */
export type Mention = {
  /** Le nom du fil visé, tel qu'il est écrit dans la liste des destinataires. */
  agent: string;
  /** Le brouillon débarrassé de la mention — ce qui reste dans la zone de saisie. */
  reste: string;
};

/**
 * La mention qui ouvre `brouillon`, si elle en désigne un destinataire connu.
 *
 * Quatre décisions, toutes du même ordre — **ne rien faire dans le doute**, parce
 * qu'une mention mal reconnue détourne un message vers le mauvais fil :
 *
 * - elle doit être **en tête** : un « @ » au milieu d'une phrase (une adresse, un
 *   pseudonyme cité) n'est pas une adresse de fil ;
 * - elle n'est reconnue qu'**une fois close** par une espace : tant qu'on tape
 *   `@de`, rien ne bouge — sans quoi le destinataire sauterait d'un agent à
 *   l'autre à chaque frappe ;
 * - le nom doit figurer dans `destinataires` : `@quelquun` reste dans le texte,
 *   ce qui le rend visible plutôt que silencieusement ignoré ;
 * - la casse est ignorée, les noms de fil étant des slugs minuscules.
 */
export function mentionEnTete(
  brouillon: string,
  destinataires: string[],
): Mention | null {
  const texte = brouillon.trimStart();
  if (!texte.startsWith(MENTION)) return null;
  const fin = texte.search(/\s/);
  if (fin === -1) return null;
  const nom = texte.slice(MENTION.length, fin).toLowerCase();
  const cible = destinataires.find((d) => d.toLowerCase() === nom);
  if (cible === undefined) return null;
  return { agent: cible, reste: texte.slice(fin + 1).trimStart() };
}

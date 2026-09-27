/**
 * **Régler depuis le fil ce qui attend quelqu'un** (#1183) : ce qui attend une
 * confirmation, et les mots de chaque règlement.
 *
 * Un run qui attend une personne l'attendait sur un autre écran — la question d'un
 * agent (#1023), la validation d'une action sensible (#48). Le fil de l'orchestrateur
 * les règle désormais par le **même** service que ces écrans : « réponds-lui : prends
 * Postgres », « oui, valide », « refuse et archive plutôt » posent une carte
 * (`chat/ReglementDansLeFil`), confirmée d'un clic ou d'un « oui », puis réglée ; ce
 * qui a repris se lit sous la réponse (`TraceDuReglement`).
 *
 * ## La forme est celle du geste sur un run (#1179), appliquée
 *
 * Le ticket le dit : il **réutilise** la carte proposée puis confirmée de #1179, dont
 * la variante retenue sur pièces pose la question au verbe du geste, nomme sa cible à
 * ses faits, dit ce qui va se passer, puis porte un seul geste principal et « Pas
 * maintenant » ; une fois confirmé, une ligne cochée dit ce qui en est sorti. Rien de
 * cette grammaire n'est rouvert ici : seules la cible (une question, un acte) et les
 * mots changent.
 *
 * ## Ce qui va se passer est dit par le service qui le fera
 *
 * La conséquence d'une réponse ou d'une décision dépend de l'attente — l'agent attend
 * encore ou est déjà reparti, la demande porte un acte ou une écriture —, et c'est le
 * service des attentes qui la sait (`ServiceAttentes.suite`). La carte la montre
 * (`ReglementPropose.suite`), le fait d'après la redit (`ReglementFait.suite`) : une
 * seule rédaction, jamais une seconde table ici qui finirait par dire autre chose.
 */

import type {
  AttenteVisee,
  MessageChat,
  Reglement,
  ReglementPropose,
} from "@/lib/types";

/** Les mots d'un règlement : sa question, son verbe, et comment il se dit fait. */
export type LibellesDuReglement = {
  /** Le titre de la carte — la question que la confirmation tranche. */
  question: string;
  /** Le bouton principal. */
  verbe: string;
  /** Ce que la trace dit une fois le règlement parti. */
  fait: string;
};

/**
 * Les verbes sont ceux des écrans : « Approuver » et « Refuser » sont les boutons de
 * la carte d'une validation (`CarteValidation`), « Répondre » celui de la question
 * d'un agent (`QuestionDansLeFil`) — ici « Envoyer la réponse », parce que la réponse
 * est déjà écrite sur la carte et que le geste l'envoie. Une validation s'appelle une
 * **demande** : elle ne porte pas toujours un acte (une tâche, une écriture dans le
 * projet), et « cet acte » mentirait sur les autres.
 */
export const LIBELLES_DES_REGLEMENTS: Record<Reglement, LibellesDuReglement> = {
  reponse: {
    question: "Envoyer cette réponse ?",
    verbe: "Envoyer la réponse",
    fait: "Réponse transmise",
  },
  approbation: {
    question: "Approuver cette demande ?",
    verbe: "Approuver",
    fait: "Approuvée",
  },
  refus: {
    question: "Refuser cette demande ?",
    verbe: "Refuser",
    fait: "Refusée",
  },
};

/**
 * Les mots d'un règlement — ou `null` pour une action que l'écran ne connaît pas : le
 * backend a pu s'enrichir, et une carte sans question ni verbe ne s'affiche pas.
 */
export function libellesDuReglement(action: string): LibellesDuReglement | null {
  return (
    (LIBELLES_DES_REGLEMENTS as Record<string, LibellesDuReglement>)[action] ?? null
  );
}

/**
 * Le **règlement d'une attente** que ce fil propose encore — `null` s'il n'y en a pas.
 *
 * Le dernier message, et lui seul : la règle de `reglement_en_attente`
 * (`maestro/controltower/chat.py`), la même que celle des six autres demandes du fil.
 */
export function reglementEnAttente(messages: MessageChat[]): ReglementPropose | null {
  const dernier = messages.at(-1);
  return dernier?.reglement ?? null;
}

/**
 * Une phrase du service, prête à s'afficher seule sur sa ligne : première lettre en
 * capitale, point final. Le service la compose pour être lue **au milieu** d'une
 * phrase (« Ce qui en sort : la tâche reprend ») — la recomposer ici ferait deux
 * rédactions du même fait.
 */
export function enPhrase(texte: string): string {
  const propre = texte.trim();
  if (propre === "") return "";
  const tete = propre.charAt(0).toLocaleUpperCase("fr") + propre.slice(1);
  return /[.!?…]$/.test(tete) ? tete : `${tete}.`;
}

/** Qui porte l'attente, en une ligne : l'agent, son rôle, la tâche d'où elle vient. */
export function porteurDeLAttente(attente: AttenteVisee): string {
  return [attente.agent ? `Agent ${attente.agent}` : "", attente.role, attente.titre]
    .filter((morceau) => morceau !== "")
    .join(" · ");
}

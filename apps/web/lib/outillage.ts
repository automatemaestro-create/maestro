/**
 * Ce que le questionnaire d'outillage d'un projet neuf attend du fil (#1031).
 *
 * Le pendant de `lib/brief` sur l'autre demande que le canal sait porter, et il
 * existe pour la même raison : « ce qui attend » doit s'énoncer **une seule fois**
 * pour toutes les surfaces. Deux formulations de « y a-t-il une question en
 * attente ? » finiraient par ne plus désigner la même chose, et un écran affirmerait
 * « aucune » pendant que la question est posée — le constat G10 du retex du
 * 2026-09-11, qu'on ne refait pas.
 *
 * Ce module est le miroir exact de ce que l'API tient
 * (`maestro/controltower/chat.py` : `question_en_attente`, `choix_du_fil`), et c'est
 * délibéré : l'écran ne **déduit** rien du questionnaire, il lit ce que le fil porte.
 * Toute la logique — quelles options, quelle recommandation, quelle déduction — vit
 * côté moteur (`maestro/projets/outillage.py`), parce qu'elle décide de fichiers
 * qu'on écrira dans le projet de quelqu'un.
 */

import type { ChoixOutillage, MessageChat } from "@/lib/types";

/**
 * La **question d'outillage** que ce fil porte encore — `null` s'il n'y en a pas.
 *
 * La règle est **le dernier message, et lui seul**, quand il porte une `question` :
 * une question attend tant que rien n'a suivi. Ce qui la rend caduque n'est pas le
 * temps, c'est qu'on y ait répondu.
 *
 * Écrite à côté de `propositionEnAttente` plutôt que fondue avec elle : les deux
 * lisent le même dernier message, mais une proposition de run et une question
 * d'outillage sont deux choses, et une fonction commune obligerait chaque appelant
 * à dire laquelle il veut — c'est-à-dire à reposer la question deux fois.
 */
export function questionEnAttente(messages: MessageChat[]): MessageChat | null {
  const dernier = messages[messages.length - 1];
  if (dernier === undefined) return null;
  return dernier.question ? dernier : null;
}

/**
 * Les réponses d'outillage acquises sur ce fil, dans l'ordre où elles sont venues.
 *
 * Lues **structurellement**, sur le champ `choix` des messages, jamais dans leur
 * texte : reconnaître « oui, Vitest » dans une phrase serait le lexique que ce canal
 * a retiré (#685).
 *
 * L'écran s'en sert pour montrer ce qui a déjà été décidé — il n'en **déduit** pas la
 * question suivante, qui vient du moteur avec le message.
 */
export function choixDuFil(messages: MessageChat[]): ChoixOutillage[] {
  return messages
    .map((m) => m.choix)
    .filter((c): c is ChoixOutillage => c !== null && c !== undefined);
}

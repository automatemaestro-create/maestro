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
 * Toute la logique — quelles questions, quelles options, ce qui est compris — vit
 * côté moteur (`maestro/outillage/questionnaire.py` et le modèle depuis #1147),
 * parce qu'elle décide de fichiers qu'on écrira dans le projet de quelqu'un.
 *
 * ## Et comment on **nomme** ce que l'outillage contient (#1104)
 *
 * Les natures de docs/38 — un fichier d'instructions, un pont, un skill, un script —
 * s'écrivaient dans l'étape d'outillage du parcours de création, et elles s'écrivent
 * désormais aussi au pied du fil. Deux tables de libellés finiraient par ne plus
 * accorder les mêmes pluriels, et la même liste se lirait « 4 skills » ici et
 * « 4 skill » là. Elles vivent donc ici, avec le reste de ce que les deux surfaces
 * partagent, et aucune des deux ne les recopie.
 */

import type {
  ChoixOutillage,
  EntreeOutillage,
  MessageChat,
} from "@/lib/types";

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
 * Les réponses d'outillage données sur ce fil, dans l'ordre où elles sont venues —
 * cliquées ou tapées (#1147).
 *
 * Lues **structurellement**, sur le champ `choix` des messages, jamais dans leur
 * texte : reconnaître « oui, Vitest » dans une phrase serait le lexique que ce canal
 * a retiré (#685). Une phrase tapée pendant qu'une question attend porte son `choix`
 * libre, posé par le moteur : c'est lui qui la relie à la question, pas l'écran.
 *
 * L'écran s'en sert pour montrer ce qui a déjà été décidé — il n'en **déduit** pas la
 * question suivante, qui vient du moteur avec le message.
 */
export function choixDuFil(messages: MessageChat[]): ChoixOutillage[] {
  return messages
    .map((m) => m.choix)
    .filter((c): c is ChoixOutillage => c !== null && c !== undefined);
}

/**
 * Les réponses d'un questionnaire **conclu**, et ce qui en a été compris — `null`
 * tant qu'il ne l'est pas (#1104, #1147).
 *
 * C'est le troisième état du même questionnaire, à côté des deux que ce module
 * énonçait déjà : une question **attend** (`questionEnAttente`), des réponses ont
 * été **données** (`choixDuFil`)… et il arrive un moment où le questionnaire est
 * fini. Ce moment se **lit** sur le fil depuis #1147 : le dernier message d'agent qui
 * porte une compréhension ne pose plus de question. C'est la conclusion — et la
 * compréhension qu'elle porte est ce qu'on écrit, sans redemander au moteur de
 * comprendre une seconde fois (il pourrait comprendre autre chose que ce que
 * l'écran a montré).
 *
 * Un geste dont la suite n'a pas pu être produite (502 après l'écriture du geste)
 * laisse des réponses sans conclusion : le dernier message compris pose encore sa
 * question, donc rien n'est à valider — « interrompu » ne se lit plus comme
 * « conclu ».
 */
export function choixAValider(messages: MessageChat[]): ChoixOutillage[] | null {
  if (questionEnAttente(messages) !== null) return null;
  for (let i = messages.length - 1; i >= 0; i--) {
    const message = messages[i];
    const compris = message.comprehension;
    if (compris === undefined || compris.length === 0) continue;
    if (message.question) return null;
    return [...choixDuFil(messages), ...compris];
  }
  return null;
}

/**
 * Ce qu'une entrée vaut **par défaut** : tout ce qu'il y a à écrire est retenu
 * d'avance — le parti pris n° 1 de l'étape d'outillage (#1034), d'après Vercel
 * (« sets the best settings for you »), repris tel quel au pied du fil (#1104).
 *
 * `deja-present` fait exception, et ce n'est pas un oubli : le projet le porte
 * déjà, il n'y a rien à faire. L'entrée reste **dans la liste** (c'est ce que #1030
 * a voulu en la gardant plutôt qu'en la supprimant) et sa case se coche — c'est ce
 * qu'*ajouter* veut dire : demander que Maestro reprenne un fichier qu'on croyait
 * acquis.
 */
export function retenueParDefaut(entree: EntreeOutillage): boolean {
  return entree.etat !== "deja-present";
}

/** Le nom d'une nature (docs/38 §3), au singulier et au pluriel. */
const NATURES: Record<string, { un: string; des: string }> = {
  instructions: { un: "fichier d'instructions", des: "fichiers d'instructions" },
  pont: { un: "pont", des: "ponts" },
  skill: { un: "skill", des: "skills" },
  script: { un: "script", des: "scripts" },
};

/** L'ordre des natures — celui de docs/38 §3.6, jamais l'ordre alphabétique. */
export const ORDRE_NATURES = ["instructions", "pont", "skill", "script"];

/** « 2 ponts », « 1 skill » — le compte et sa nature, accordés. */
export function compte(type: string, nombre: number): string {
  const nature = NATURES[type];
  if (nature === undefined) return `${nombre} ${type}`;
  return `${nombre} ${nombre > 1 ? nature.des : nature.un}`;
}

/** Le badge de nature d'une ligne — toujours au singulier, il qualifie une entrée. */
export function libelleNature(type: string): string {
  const nature = NATURES[type];
  if (nature === undefined) return type;
  return nature.un.charAt(0).toUpperCase() + nature.un.slice(1);
}

/**
 * « 1 fichier d'instructions · 2 ponts · 4 skills » — ce que des entrées pèsent,
 * dans l'ordre des natures, les natures absentes tues.
 *
 * Écrit ici et pas dans l'un des deux appelants : l'étape d'outillage le rend dans
 * son en-tête de liste, la conclusion du fil dans sa phrase de compte, et c'est la
 * même phrase.
 */
export function comptesParNature(entrees: EntreeOutillage[]): string {
  return ORDRE_NATURES.map((type) => ({
    type,
    nombre: entrees.filter((e) => e.type === type).length,
  }))
    .filter((n) => n.nombre > 0)
    .map((n) => compte(n.type, n.nombre))
    .join(" · ");
}

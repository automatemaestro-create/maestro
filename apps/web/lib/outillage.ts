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
 * ## Et depuis #1161, la **pièce** qui attend, et comment elle se lit
 *
 * L'outillage ne s'écrit plus en une fois — ni à l'étape d'un formulaire, ni par la
 * carte qui concluait le questionnaire : il se construit **pièce par pièce** dans la
 * conversation, chaque fichier montré avec ce qui changera, puis écrit sur accord.
 * `pieceEnAttente` est la cinquième règle d'attente du canal, `diffDeLaPiece` ce que
 * sa carte montre. Les natures et leurs comptes (« 1 fichier d'instructions · 4
 * skills ») sont partis avec la liste à cocher qui les additionnait.
 */

import {
  compter,
  condenser,
  CONTEXTE,
  differencier,
  type EntreeDiff,
  type LigneDiff,
} from "@/lib/diff";
import type { ChoixOutillage, MessageChat, PieceProposee } from "@/lib/types";

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
 * La **pièce d'outillage** que ce fil propose encore — `null` s'il n'y en a pas (#1161).
 *
 * La même règle que la question, sur la cinquième demande du canal : le dernier
 * message, et lui seul, quand il porte une `piece`. Le miroir de `piece_en_attente`
 * (`maestro/controltower/chat.py`), jamais recopié ailleurs.
 */
export function pieceEnAttente(messages: MessageChat[]): MessageChat | null {
  const dernier = messages[messages.length - 1];
  if (dernier === undefined) return null;
  return dernier.piece ? dernier : null;
}

/**
 * Les versions de pièces que ce fil a proposées, par chemin **et** empreinte — de quoi
 * rendre à la trace d'une pièce tranchée ce que sa carte montrait (#1161).
 *
 * Le fait (`piece_ecrite`) ne porte que l'empreinte du contenu proposé : le diff et
 * les verdicts sont déjà persistés sur le message qui proposait cette version, et le
 * fil est la seule mémoire du canal — on les y relit plutôt que de les recopier.
 */
export function piecesDuFil(messages: MessageChat[]): Map<string, PieceProposee> {
  const vues = new Map<string, PieceProposee>();
  for (const message of messages) {
    if (message.piece) vues.set(cleDePiece(message.piece.chemin, message.piece.empreinte), message.piece);
  }
  return vues;
}

/** La clé d'une version de pièce : son chemin et l'empreinte de son contenu. */
export function cleDePiece(chemin: string, empreinte: string): string {
  return `${chemin}|${empreinte}`;
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
 * Ce que le geste fera au chemin d'une pièce, en mots — le badge de sa carte (#1161).
 *
 * Trois cas, et le troisième est celui qu'on veut savoir avant de laisser écrire chez
 * soi : un fichier qui est **au projet** ne reçoit que le bloc que Maestro y possède,
 * rien d'autre n'y est touché (docs/38 §4.2).
 */
export const SORTS_DE_PIECE: Record<string, string> = {
  cree: "nouveau fichier",
  reecrit: "fichier de Maestro modifié",
  bloc: "bloc ajouté à votre fichier",
};

/** Combien de lignes d'un diff se lisent sans rien déplier — le reste se déplie sur place. */
export const LIGNES_OUVERTES = 12;

/** Le diff d'une pièce, prêt à lire, et ses comptes — accordés entre eux. */
export type DiffDePiece = {
  /** Les lignes et plages repliées à montrer, dans l'ordre. */
  entrees: EntreeDiff[];
  ajouts: number;
  retraits: number;
  /** Un fichier neuf : tout est ajout, et la carte se lit comme un texte. */
  neuf: boolean;
};

/**
 * Le diff d'une pièce, **des textes qu'elle porte** — la même comparaison que
 * l'éditeur de playbook (`lib/diff`), réglée pour qu'un coup d'œil ne mente pas (#1161).
 *
 * Deux réglages, et ce sont deux constats du regard neuf sur la vraie stack :
 *
 * - **la fin de ligne finale ne compte pas.** Un fichier de 55 lignes se termine par
 *   un saut de ligne ; découpé tel quel, il en rendait 56, et la carte disait « +55 »
 *   à côté de « Voir les 56 lignes » ;
 * - **un fichier neuf n'a pas d'« avant ».** Comparé au texte vide, sa première ligne
 *   vide passait pour commune — une bande blanche sans signe au milieu des ajouts.
 *   Un fichier qu'on crée est tout entier ajouté, et il se dit comme tel.
 *
 * Une modification, elle, est **condensée** : le contexte autour de ce qui change, le
 * reste replié — ce qui fait qu'une correction d'une ligne se voit d'une ligne.
 */
export function diffDeLaPiece(piece: PieceProposee): DiffDePiece {
  const apres = sansFinDeLigne(piece.texte_apres);
  if (piece.sort === "cree" || piece.texte_avant === "") {
    const lignes: LigneDiff[] = apres
      .split("\n")
      .map((texte) => ({ type: "ajout", texte }));
    return { entrees: lignes, ajouts: lignes.length, retraits: 0, neuf: true };
  }
  const lignes = differencier(sansFinDeLigne(piece.texte_avant), apres);
  const { ajouts, retraits } = compter(lignes);
  return { entrees: condenser(lignes), ajouts, retraits, neuf: false };
}

/**
 * Ce que la carte montre **avant** qu'on déplie : les premières lignes du diff — sauf
 * pour un fichier neuf **corrigé**, qui s'ouvre sur le passage qui porte la commande
 * dite (#1161).
 *
 * Constat du regard neuf, cinquième relecture : un `AGENTS.md` neuf corrigé par « Nos
 * tests tournent avec `dotnet test` » montrait ses douze premières lignes — titre,
 * langages, gestionnaires —, et la ligne corrigée n'y était pas : pour lire ce que la
 * correction allait écrire, il fallait déplier le fichier. Une modification n'a pas ce
 * défaut, son diff est déjà condensé autour de ce qui change ; le fichier neuf l'est
 * ici de la même façon (`condenser`), autour des lignes qui nomment une commande
 * corrigée, et ses lignes restent des ajouts. Corrigé dans ses premières lignes, il se
 * montre comme avant. Une commande que Maestro **propose** (#1381) se montre de même :
 * c'est elle que l'accord écrira.
 */
export function apercuDeLaPiece(piece: PieceProposee, diff: DiffDePiece): EntreeDiff[] {
  const debut = diff.entrees.slice(0, LIGNES_OUVERTES);
  const dites = commandesMisesEnAvant(piece);
  if (!diff.neuf || dites.length === 0) return debut;
  const lignes = diff.entrees as LigneDiff[];
  const touchee = (l: LigneDiff) => dites.some((commande) => l.texte.includes(commande));
  const cachee = lignes.some((l, i) => touchee(l) && i + CONTEXTE >= LIGNES_OUVERTES);
  if (!cachee) return debut;
  return condenser(
    lignes.map((l): LigneDiff => ({ type: touchee(l) ? "ajout" : "commun", texte: l.texte })),
  )
    .map((e): EntreeDiff => (e.type === "repli" ? e : { type: "ajout", texte: e.texte }))
    .slice(0, LIGNES_OUVERTES);
}

/**
 * Les commandes qu'une pièce met en avant, verdict sous la légende : celles que la
 * personne a **dites** (#1161), puis celles que Maestro **propose** (#1381). Ce sont
 * les commandes que cette version change, et la raison pour laquelle elle revient.
 */
export function commandesMisesEnAvant(piece: PieceProposee): string[] {
  return [...(piece.corrigees ?? []), ...(piece.proposees ?? [])];
}

/** Le texte sans son dernier saut de ligne — celui qui n'ouvre aucune ligne. */
function sansFinDeLigne(texte: string): string {
  return texte.endsWith("\n") ? texte.slice(0, -1) : texte;
}

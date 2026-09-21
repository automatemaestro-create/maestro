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

import {
  CALIBRE_AMORCE,
  CALIBRE_PAIRE_SOUS_SM,
} from "@/components/Conversation";

import type { Projet } from "./types";

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
 *
 * ⚠ Il **promet ce que le canal fait**, et pas un cran de plus (#688) : il
 * annonçait « j'ouvre un run » quand, depuis #685, le canal *propose* et attend
 * un accord explicite. Une promesse d'accueil en avance sur le code est la pire
 * espèce de documentation périmée — elle est lue par l'utilisateur, avant tout
 * le reste, et c'est elle qui fixe ce qu'il croira avoir demandé.
 */
export const ACCUEIL_ORCHESTRATION =
  "Dites ce qu'il y a à faire — je vous propose un objectif, et j'ouvre le run dès que vous l'approuvez : je le découpe alors en tâches que je confie aux agents compétents. Rien ne part sans votre accord. Une question sur l'état en cours n'ouvre rien : j'y réponds. Pour vous adresser à un agent précis sans quitter cet écran, commencez par « @ » suivi de son nom.";

/**
 * Comment la première amorce désigne le projet quand son nom ne tient pas dans
 * le calibre — voir `amorcesDuProjet`. Un démonstratif, et non un nom générique
 * (« votre projet ») : l'écran est déjà **dans** le projet, que la barre
 * supérieure nomme.
 */
export const SUJET_SANS_NOM = "ce projet";

/**
 * La première amorce : *va lire le projet et dis-moi ce qu'il est*.
 *
 * L'impératif n'est pas un détail de style. Une amorce est envoyée **telle
 * quelle** au fil (`components/Conversation`), donc c'est le juge du canal qui
 * la lit (`maestro/controltower/orchestration.py`) : une demande de travail se
 * fait proposer un run, et c'est le run — seul à travailler dans la racine du
 * projet (#222) — qui peut réellement aller voir. La même idée tournée en
 * question (« Que fait ce projet ? ») serait rangée en « échange » et le modèle
 * y répondrait sans avoir rien lu : une amorce qui promet ce que le produit ne
 * fait pas est pire qu'une amorce générique.
 */
function decrire(sujet: string): string {
  return `Décris-moi ${sujet}`;
}

/**
 * La seconde amorce — l'autre geste honnête sur un projet qu'on ne connaît pas
 * encore : *lis-le et propose-moi quelque chose*. Elle ne présume rien du
 * contenu, là où « Corrige un bug » ou « Écris le README » affirmeraient qu'il
 * y a un bug, ou qu'il n'y a pas de README.
 */
export const AMORCE_PISTES = "Propose des pistes";

/**
 * Les deux amorces d'**état**, inchangées depuis #908 : elles portent la
 * frontière du canal (les deux premières mènent à une proposition de run, les
 * deux dernières à une réponse) et elles parlent déjà du projet ouvert —
 * l'aperçu que le canal lit est cadré sur lui (#683), comme toutes les vues de
 * travail (#277). Ce ne sont pas elles que le retex du 2026-09-11 a relevées.
 */
export const AMORCE_RUNS = "Où en sont les runs ?";
export const AMORCE_ARBITRAGES = "Que dois-je arbitrer ?";

/** La longueur d'un libellé en **points de code**, comme le calibre la compte. */
function calibre(libelle: string): number {
  return Array.from(libelle).length;
}

/**
 * Les amorces d'un fil vide, **dérivées du projet ouvert** (#942, constat G9 du
 * retex du 2026-09-11).
 *
 * ## Ce qui a changé, et pourquoi
 *
 * Elles étaient une liste figée — « Pagine les projets », « Corrige le tri
 * Kanban » —, c'est-à-dire le **backlog de Maestro** proposé à quelqu'un qui
 * vient de déclarer un minuteur. C'est G6 sous une autre forme : le produit
 * parle de lui-même. #916 avait réglé leur *calibrage*, jamais leur *contenu*.
 *
 * Les deux premières changent donc de sujet : au lieu de proposer un travail
 * que nous aurions choisi, elles proposent d'**aller voir** le projet ouvert.
 * C'est ce qui tient le second critère du ticket — *sur un projet dont on ne
 * sait rien encore, l'amorce reste utile et honnête plutôt que générique* : ni
 * l'une ni l'autre ne prétend connaître le projet, et la première le **nomme**,
 * ce qu'aucune liste figée ne peut faire.
 *
 * ## Ce qu'elle lit, et ce qu'elle ne lit pas
 *
 * La **fiche du projet**, déjà dans l'état global (`lib/etatGlobal`) : aucun
 * appel réseau, aucun appel de modèle, rien à charger. C'est la note technique
 * du ticket tenue au mot — *dériver une amorce ne doit pas coûter un appel de
 * modèle à chaque ouverture d'un chat vide* —, et le plus court chemin pour y
 * arriver : la fonction est pure, donc son prix est nul.
 *
 * Elle **ne lit pas la racine** du projet, bien que l'analyse existe
 * (`GET /api/projets/{id}/outillage/analyse`, #1030) et rendrait les langages
 * et les commandes. Deux raisons : cette analyse **parcourt le disque** par
 * milliers de fichiers, et cette surface-ci se monte à chaque ouverture de la
 * colonne de conversation (`components/ColonneConversation`) — ce serait le
 * prix par visite que le ticket écarte ; et ce qu'elle rendrait (« Python,
 * pytest ») ne dit pas *quel* geste proposer, seulement avec quoi il se ferait.
 * Ce que le projet contient, c'est la **première amorce** qui va le chercher,
 * par le seul moyen dont le produit dispose : un run.
 *
 * ## Le nom entre quand il tient, et pas autrement
 *
 * Le calibre n'est pas négociable (#908) : chaque libellé tient en
 * `CALIBRE_AMORCE` points de code, parce qu'une amorce ne s'enveloppe jamais
 * sur elle-même (`AMORCE_NOWRAP`) et que sa longueur est donc toute sa largeur ;
 * et les **deux premières** partagent une rangée à 375 px, d'où
 * `CALIBRE_PAIRE_SOUS_SM` à elles deux. Un nom de projet est une donnée de
 * l'utilisateur : il peut faire trois caractères comme quarante. Il entre donc
 * dans la première amorce **si et seulement si** les deux bornes tiennent
 * encore, et sinon le démonstratif reprend sa place. Un libellé tronqué
 * (« Décris-moi mon-très-long-p… ») a été écarté : il ne nomme plus rien, et
 * c'est précisément nommer qui avait un sens ici.
 *
 * L'**ordre** ne bouge pas (il reste éditorial, #908), et le **nombre** non
 * plus : quatre, comme avant. Que seules deux soient atteignables à 420 px est
 * le constat G8 du même retex, et il a son ticket — le corriger ici trancherait
 * à sa place.
 */
export function amorcesDuProjet(projet: Projet): string[] {
  const nom = projet.nom.trim();
  const nommee = decrire(nom);
  const tientSeule = calibre(nommee) <= CALIBRE_AMORCE;
  const tientEnPaire =
    calibre(nommee) + calibre(AMORCE_PISTES) <= CALIBRE_PAIRE_SOUS_SM;
  return [
    nom !== "" && tientSeule && tientEnPaire ? nommee : decrire(SUJET_SANS_NOM),
    AMORCE_PISTES,
    AMORCE_RUNS,
    AMORCE_ARBITRAGES,
  ];
}

/**
 * Les destinataires que `/chat` propose : l'orchestration en tête, puis le parc.
 * C'est l'ordre du menu (`/chat` avant `/agents`) et celui de l'usage — on
 * s'adresse à l'orchestration par défaut, à un exécutant par exception.
 *
 * **Elle est retirée du parc plutôt qu'ajoutée à côté** (#671), parce qu'elle y
 * figurait : `GET /api/agents` rendait les acteurs vus au journal, et
 * l'orchestrateur en est un (`events.ACTEUR_RUN`). Le prendre pour un exécutant
 * donnait deux entrées pour un seul fil, et deux enfants React sous la même clé.
 *
 * ⚠ **Depuis #1028, le parc ne le porte plus** : la projection le laisse dehors
 * (`state._hors_du_parc`), puisqu'il n'exécute aucune tâche et n'est pas un
 * membre du parc (docs/37 §4.2). La déduplication **reste** pour autant, et ce
 * n'est pas de la superstition : la réserve de `maestro.agents.store.NOMS_RESERVES`
 * interdit qu'un agent *personnalisé* prenne ce nom, elle ne promet pas qu'aucune
 * projection n'en fabriquera jamais un — une API d'une version antérieure, un flux
 * rejoué. Elle ne coûte rien et elle empêche un écran de casser ; l'ôter
 * échangerait une ligne contre une classe de panne déjà rencontrée.
 *
 * Le doublon ne se voyait ni en `--demo` ni en test, dont les parcs n'ont jamais
 * porté l'orchestrateur : seul le mode réel servait cette forme-là. C'est pourquoi
 * la règle vit ici, éprouvable sans monter d'écran, et pourquoi le parc des tests
 * d'écran porte l'orchestrateur — c'est désormais le seul endroit où il en reste
 * un, et c'est bien ce que ce test garde.
 *
 * Le reste du parc passe **tel quel** : un exécutant en double serait un défaut de
 * la projection, que l'écran masquerait au lieu de le montrer.
 */
export function destinatairesDuFil(parc: readonly string[]): string[] {
  return [AGENT_ORCHESTRATION, ...parc.filter((nom) => nom !== AGENT_ORCHESTRATION)];
}

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

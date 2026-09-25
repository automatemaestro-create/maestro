/**
 * Un projet naît dans la conversation, côté écran (#1294, docs/43 §2.2).
 *
 * « Nouveau projet » ouvre le fil de l'orchestration : elle comprend ce qu'on veut
 * faire, propose un projet sur une carte (`DemandeDeProjet`), et l'accord le
 * déclare. Ce module tient les deux questions que deux surfaces se posent — la
 * carte qui attend un geste (`GestesDuFil`) et la porte qui ouvre le projet né
 * (`NaissanceProjet`) — pour qu'elles ne les formulent pas deux fois.
 */

import type { DemandeProjet, MessageChat, ProjetCree } from "@/lib/types";

/**
 * La **proposition de projet** que ce fil porte encore, `null` sinon (#1294).
 *
 * La règle des trois autres demandes du fil — le dernier message, et lui seul —,
 * énoncée une fois de ce côté-ci comme elle l'est côté moteur
 * (`chat.projet_en_attente`). Ce qui la solde est qu'on y ait répondu : un clic,
 * un « oui » tapé, ou une correction, qui appelle une proposition nouvelle.
 */
export function projetEnAttente(messages: MessageChat[]): DemandeProjet | null {
  const dernier = messages[messages.length - 1];
  if (dernier === undefined) return null;
  return dernier.projet_propose ?? null;
}

/**
 * Le **dernier** projet que ce fil a fait naître, `null` s'il n'en a fait naître
 * aucun (#1294).
 *
 * Lu dans les messages **persistés** — le fait que la réponse d'un accord porte
 * (`projet_cree`) —, jamais dans une phrase : c'est ce qui fait qu'un accord donné
 * d'un clic comme d'un « oui » tapé ouvre le projet, et qu'une porte rechargée
 * après coup le retrouve.
 */
export function projetNeDuFil(messages: MessageChat[]): ProjetCree | null {
  for (let rang = messages.length - 1; rang >= 0; rang -= 1) {
    const cree = messages[rang].projet_cree;
    if (cree) return cree;
  }
  return null;
}

/**
 * La clé de session qui porte « Nouveau projet » demandé **depuis un projet**.
 *
 * Un projet naît sur la porte d'entrée, hors du cadre d'un autre (docs/43 §2.2) :
 * l'écran Projets quitte donc le projet ouvert, et la porte doit savoir en se
 * montant qu'on vient y créer, pas y choisir. La session et non le poste : une
 * demande de création n'a pas à survivre à la fenêtre.
 */
export const CLE_NAISSANCE_DEMANDEE = "maestro.porte.naissance";

/** Note qu'on quitte le projet ouvert **pour en créer un** (#1294). */
export function demanderNaissance(): void {
  try {
    window.sessionStorage.setItem(CLE_NAISSANCE_DEMANDEE, "1");
  } catch {
    // Stockage indisponible (navigation privée stricte) : la porte montrera la
    // liste, et « Nouveau projet » y reste à un clic.
  }
}

/**
 * La demande de création que la porte trouve en se montant — lue **une fois** :
 * elle est retirée en la lisant, pour qu'un rechargement de la porte ne rouvre
 * pas la création qu'on a quittée.
 */
export function naissanceDemandee(): boolean {
  try {
    const demandee = window.sessionStorage.getItem(CLE_NAISSANCE_DEMANDEE) === "1";
    window.sessionStorage.removeItem(CLE_NAISSANCE_DEMANDEE);
    return demandee;
  } catch {
    return false;
  }
}

/** Le versionnement d'une proposition, en mots — ce que la carte affiche. */
export function versionnementEnMots(demande: DemandeProjet): string {
  if (demande.deja_versionne) return "Déjà sous Git";
  return demande.versionner ? "Git, en local" : "Sans versionnement";
}

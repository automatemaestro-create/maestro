/**
 * Résumé et tri d'importance des événements du flux temps réel (`WS
 * /ws/evenements`, #46). Partagé entre le fil d'activité du tableau de bord
 * (#47) et le centre de notifications déroulant (#119) : « qui fait quoi » se
 * lit à l'identique aux deux endroits, et le second n'a qu'à filtrer le notable.
 */

import { libelleStatut } from "@/lib/format";
import {
  CAPACITE_ACTIVE,
  EVENEMENT_AGENT_ACTIVITE,
  EVENEMENT_AGENT_CAPACITE,
  EVENEMENT_CHAT_MESSAGE,
  EVENEMENT_MESSAGE_INTER_AGENTS,
  EVENEMENT_PLAYBOOK_PROPOSITION,
  EVENEMENT_TACHE_REASSIGNATION,
  EVENEMENT_TACHE_REFERENCE,
  EVENEMENT_TACHE_STATUT,
  EVENEMENT_VALIDATION_DECISION,
  EVENEMENT_VALIDATION_DEMANDE,
  VALIDATION_APPROUVEE,
  type Evenement,
} from "@/lib/types";

const ICONES: Record<string, string> = {
  [EVENEMENT_TACHE_STATUT]: "📋",
  [EVENEMENT_TACHE_REASSIGNATION]: "🔀",
  [EVENEMENT_TACHE_REFERENCE]: "🎫",
  [EVENEMENT_AGENT_ACTIVITE]: "🤖",
  [EVENEMENT_AGENT_CAPACITE]: "🎚️",
  [EVENEMENT_MESSAGE_INTER_AGENTS]: "✉️",
  [EVENEMENT_VALIDATION_DEMANDE]: "⚠️",
  [EVENEMENT_VALIDATION_DECISION]: "⚖️",
};

/** L'émoji qui coiffe un événement dans une liste (puce neutre par défaut). */
export function iconeEvenement(evenement: Evenement): string {
  return ICONES[evenement.type] ?? "•";
}

/** Une phrase courte décrivant l'événement — le texte de la ligne de liste. */
export function resumeEvenement(evenement: Evenement): string {
  const sujet = evenement.titre || evenement.tache_id || evenement.run_id;
  switch (evenement.type) {
    case EVENEMENT_TACHE_REASSIGNATION:
      return `${sujet} réassignée à ${evenement.agent}`;
    case EVENEMENT_TACHE_REFERENCE:
      // #187 : l'événement ne porte que le ticket — ni statut ni agent à dire.
      return `${sujet} — ticket ${
        evenement.ticket?.id || "externe"
      }`;
    case EVENEMENT_TACHE_STATUT:
      return `${sujet} — ${libelleStatut(evenement.statut)}${
        evenement.agent ? ` (${evenement.agent})` : ""
      }`;
    case EVENEMENT_AGENT_CAPACITE:
      return `${evenement.agent} — ${
        evenement.statut === CAPACITE_ACTIVE ? "activé" : "désactivé"
      }${evenement.instances !== null ? `, ${evenement.instances} instance(s)` : ""}`;
    case EVENEMENT_MESSAGE_INTER_AGENTS:
      return `${evenement.agent} → message${sujet ? ` (${sujet})` : ""}`;
    case EVENEMENT_VALIDATION_DEMANDE:
      return `Validation demandée — ${sujet}${
        evenement.agent ? ` (${evenement.agent})` : ""
      }`;
    case EVENEMENT_VALIDATION_DECISION:
      return `${sujet} — validation ${
        evenement.statut === VALIDATION_APPROUVEE ? "approuvée" : "refusée"
      }`;
    default:
      return `${evenement.agent || "?"}${sujet ? ` — ${sujet}` : ""}${
        evenement.statut ? ` : ${libelleStatut(evenement.statut)}` : ""
      }`;
  }
}

/**
 * Les statuts de tâche qui méritent une notification globale — miroir des états
 * terminaux/bloquant du moteur (`STATUT_TERMINEE`/`ECHEC`/`BLOQUEE`,
 * maestro/engine/executor). Les transitions intermédiaires (assignée, en cours)
 * sont du bruit hors du tableau de bord.
 */
const STATUTS_TACHE_NOTABLES = new Set(["terminee", "echec", "bloquee"]);

/**
 * Un événement digne du centre de notifications (#119) : ce qu'un utilisateur
 * parti sur une autre page voudrait savoir — demandes et décisions de
 * validation, fin ou blocage d'une tâche, changement de capacité d'un agent.
 * Le menu fretin temps réel (agent occupé/libre, messages inter-agents, chaque
 * transition de statut) reste au fil d'activité du tableau de bord.
 */
export function estNotableNotification(evenement: Evenement): boolean {
  switch (evenement.type) {
    case EVENEMENT_VALIDATION_DEMANDE:
    case EVENEMENT_VALIDATION_DECISION:
    case EVENEMENT_AGENT_CAPACITE:
      return true;
    case EVENEMENT_TACHE_STATUT:
      return STATUTS_TACHE_NOTABLES.has(evenement.statut);
    default:
      return false;
  }
}

/**
 * Le nom lisible d'un **type** d'événement — ce que le filtre du Journal (#249)
 * propose dans sa liste déroulante, là où `resumeEvenement` décrit une ligne en
 * particulier. Les deux vivent ici pour la même raison : le vocabulaire du flux
 * temps réel se dit d'un seul endroit.
 */
const LIBELLES_TYPE: Record<string, string> = {
  [EVENEMENT_TACHE_STATUT]: "Statut de tâche",
  [EVENEMENT_TACHE_REASSIGNATION]: "Réassignation",
  [EVENEMENT_TACHE_REFERENCE]: "Ticket rattaché",
  [EVENEMENT_AGENT_ACTIVITE]: "Activité d'agent",
  [EVENEMENT_AGENT_CAPACITE]: "Capacité d'agent",
  [EVENEMENT_MESSAGE_INTER_AGENTS]: "Message inter-agents",
  [EVENEMENT_VALIDATION_DEMANDE]: "Validation demandée",
  [EVENEMENT_VALIDATION_DECISION]: "Décision de validation",
  [EVENEMENT_CHAT_MESSAGE]: "Message de chat",
  [EVENEMENT_PLAYBOOK_PROPOSITION]: "Proposition de playbook",
};

/**
 * Le libellé d'un type d'événement, ou le type brut si le flux s'est enrichi
 * côté backend — même contrat que `libelleStatut` : on n'invente rien, mais on
 * ne masque pas non plus un type qu'on ne connaît pas encore.
 */
export function libelleTypeEvenement(type: string): string {
  return LIBELLES_TYPE[type] ?? type;
}

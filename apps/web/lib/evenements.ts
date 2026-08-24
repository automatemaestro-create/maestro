/**
 * Résumé et tri d'importance des événements du flux temps réel (`WS
 * /ws/evenements`, #46). Partagé entre le fil d'activité du tableau de bord
 * (#47) et le centre de notifications déroulant (#119) : « qui fait quoi » se
 * lit à l'identique aux deux endroits, et le second n'a qu'à filtrer le notable.
 */

import {
  IconeAgent,
  IconeAlerte,
  IconeArbitrage,
  IconeCapacite,
  IconeMessage,
  IconePuce,
  IconeReassignation,
  IconeTache,
  IconeTicket,
} from "@/components/Icones";
import type { Icone } from "@/components/Primitives";
import { libelleStatut } from "@/lib/format";
import {
  CAPACITE_ACTIVE,
  CHAT_AUTEUR_UTILISATEUR,
  EVENEMENT_AGENT_ACTIVITE,
  EVENEMENT_AGENT_CAPACITE,
  EVENEMENT_CHAT_MESSAGE,
  EVENEMENT_EXECUTION_STATUT,
  EVENEMENT_MESSAGE_INTER_AGENTS,
  EVENEMENT_PLAYBOOK_PROPOSITION,
  EVENEMENT_TACHE_REASSIGNATION,
  EVENEMENT_TACHE_REFERENCE,
  EVENEMENT_TACHE_STATUT,
  EVENEMENT_VALIDATION_DECISION,
  EVENEMENT_VALIDATION_DEMANDE,
  EXECUTION_ANNULEE,
  EXECUTION_ECHEC,
  EXECUTION_EN_COURS,
  EXECUTION_TERMINEE,
  ORDRE_PAUSE,
  ORDRE_REPRISE,
  VALIDATION_APPROUVEE,
  type Evenement,
} from "@/lib/types";

/**
 * L'icône par type d'événement (#245 — c'étaient des émojis jusque-là). Un
 * type inconnu du front retombe sur la puce neutre : le flux peut s'enrichir
 * côté backend sans que la ligne perde sa colonne de gauche.
 */
const ICONES: Record<string, Icone> = {
  [EVENEMENT_TACHE_STATUT]: IconeTache,
  [EVENEMENT_TACHE_REASSIGNATION]: IconeReassignation,
  [EVENEMENT_TACHE_REFERENCE]: IconeTicket,
  [EVENEMENT_AGENT_ACTIVITE]: IconeAgent,
  [EVENEMENT_AGENT_CAPACITE]: IconeCapacite,
  [EVENEMENT_MESSAGE_INTER_AGENTS]: IconeMessage,
  [EVENEMENT_VALIDATION_DEMANDE]: IconeAlerte,
  [EVENEMENT_VALIDATION_DECISION]: IconeArbitrage,
};

/**
 * L'icône qui coiffe un événement dans une liste (puce neutre par défaut).
 * Elle est **décorative** : `resumeEvenement` porte déjà le sens en toutes
 * lettres à côté d'elle — ce que l'émoji, seul devant certaines lignes, ne
 * faisait pas.
 */
export function iconeEvenement(evenement: Evenement): Icone {
  return ICONES[evenement.type] ?? IconePuce;
}

/** Ce dont parle un événement : son titre lisible, à défaut un identifiant. */
function sujetEvenement(evenement: Evenement): string {
  return evenement.titre || evenement.tache_id || evenement.run_id;
}

/**
 * Un sujet cité, prêt à entrer dans une phrase — vide s'il n'y en a pas, pour
 * que l'appelant puisse choisir son repli plutôt que d'écrire « a terminé « » ».
 */
function cite(texte: string): string {
  return texte ? `« ${texte} »` : "";
}

/**
 * La transition de statut d'une tâche (#250), dite du point de vue de qui
 * regarde travailler son équipe : « dev a terminé « Écrire les tests » » plutôt
 * que « tache-42 — Terminée (dev) ».
 *
 * Chaque statut a deux formes, avec et sans agent : le flux n'en porte pas
 * toujours un (une tâche assignée par le planificateur, une reprise), et une
 * phrase qui commence par un blanc est pire que pas de phrase du tout. Le
 * `default` couvre les statuts que l'UI ne connaît pas encore — même contrat que
 * `libelleStatut`, qui rend le statut brut plutôt que rien.
 */
function phraseStatutTache(evenement: Evenement): string {
  const quoi = cite(sujetEvenement(evenement)) || "une tâche";
  const qui = evenement.agent;
  switch (evenement.statut) {
    case "assignee":
      return qui ? `${qui} prend en charge ${quoi}` : `${quoi} est assignée`;
    case "en_cours":
      return qui ? `${qui} travaille sur ${quoi}` : `${quoi} démarre`;
    case "bloquee":
      return qui ? `${qui} signale un blocage sur ${quoi}` : `${quoi} est bloquée`;
    case "terminee":
      return qui ? `${qui} a terminé ${quoi}` : `${quoi} est terminée`;
    case "echec":
      return qui ? `${qui} a échoué sur ${quoi}` : `${quoi} a échoué`;
    default:
      return evenement.statut
        ? `${quoi} passe à ${libelleStatut(evenement.statut)}${qui ? ` (${qui})` : ""}`
        : `Mise à jour de ${quoi}${qui ? ` (${qui})` : ""}`;
  }
}

/**
 * Une étape **hors tâche** (`agent.activite`) : planification, reprise, relance
 * automatique, refus d'outil, validation humaine — voir
 * `maestro/controltower/bridge.py`, qui les range toutes sous ce type. Leur
 * `titre` porte déjà un nom lisible (« Planification », « Validation humaine —
 * … ») : la phrase l'habille de ce qui lui est arrivé.
 */
function phraseEtapeAgent(evenement: Evenement): string {
  const quoi = evenement.titre || "une étape";
  const qui = evenement.agent;
  switch (evenement.statut) {
    // Le moteur consigne la décision humaine sur l'étape `:validation`
    // (executor.py) — c'est la même information que `validation.decision`, vue
    // depuis le journal du run.
    case "approuve":
      return `Accord donné : ${quoi}`;
    case "refuse":
      return `Accord refusé : ${quoi}`;
    case "refus_outil":
      return qui ? `${qui} s'est vu refuser un outil : ${quoi}` : `Outil refusé : ${quoi}`;
    case "terminee":
      return qui ? `${qui} a terminé : ${quoi}` : `${quoi} — terminé`;
    case "echec":
      return qui ? `${qui} a échoué : ${quoi}` : `${quoi} — échec`;
    case "en_cours":
      return qui ? `${qui} — ${quoi} en cours` : `${quoi} en cours`;
    default:
      return `${qui ? `${qui} — ` : ""}${quoi}${
        evenement.statut ? ` : ${libelleStatut(evenement.statut)}` : ""
      }`;
  }
}

/** Le cycle de vie d'un run (#185) : ce qui commence, finit ou s'interrompt. */
function phraseExecution(evenement: Evenement): string {
  const objectif = cite(evenement.titre) || "un objectif";
  switch (evenement.statut) {
    case EXECUTION_EN_COURS:
      return `Nouvelle exécution lancée sur ${objectif}`;
    case EXECUTION_TERMINEE:
      return `Exécution terminée — ${objectif}`;
    case EXECUTION_ECHEC:
      return `Exécution en échec — ${objectif}`;
    case EXECUTION_ANNULEE:
      return `Exécution annulée — ${objectif}`;
    // Les deux ordres de pause (#477) empruntent le même événement sans être des
    // statuts : le fil les rend en toutes lettres, faute de quoi ils sortiraient
    // sous le « Exécution — … » générique, indiscernables l'un de l'autre alors
    // qu'ils sont exactement inverses.
    case ORDRE_PAUSE:
      return `Exécution suspendue — ${objectif}`;
    case ORDRE_REPRISE:
      return `Exécution reprise — ${objectif}`;
    default:
      return `Exécution — ${objectif}`;
  }
}

/**
 * Une phrase orientée utilisateur décrivant l'événement — le texte de la ligne
 * de liste (#250).
 *
 * Le contrat de ce module : **qui fait quoi, sur quoi, avec quel résultat**, et
 * jamais un identifiant suivi d'un statut. Ce que la phrase laisse de côté (les
 * identifiants, le statut du bus, le texte libre du moteur) n'est pas perdu — il
 * est rendu par `detailEvenement`, que la ligne déplie.
 *
 * Le `default` est une **garde**, et il le reste : le backend peut diffuser un type
 * que ce front ne connaît pas encore (c'était le cas d'`execution.statut` avant
 * ce lot), et une ligne approximative vaut mieux qu'une ligne disparue.
 */
export function resumeEvenement(evenement: Evenement): string {
  const sujet = sujetEvenement(evenement);
  switch (evenement.type) {
    case EVENEMENT_TACHE_STATUT:
      return phraseStatutTache(evenement);
    case EVENEMENT_TACHE_REASSIGNATION: {
      const quoi = cite(sujet) || "une tâche";
      return evenement.agent
        ? `${quoi} est confiée à ${evenement.agent}`
        : `${quoi} change de mains`;
    }
    case EVENEMENT_TACHE_REFERENCE: {
      // #187 : l'événement ne porte que le ticket — ni statut ni agent à dire.
      const quoi = cite(sujet) || "une tâche";
      return evenement.ticket?.id
        ? `${quoi} est rattachée au ticket ${evenement.ticket.id}`
        : `${quoi} est rattachée à un ticket externe`;
    }
    case EVENEMENT_AGENT_ACTIVITE:
      return phraseEtapeAgent(evenement);
    case EVENEMENT_AGENT_CAPACITE: {
      const qui = evenement.agent || "un agent";
      // Tout ce qui n'est pas « active » vaut désactivation — c'était déjà la
      // lecture d'avant ce lot, et elle évite d'inventer un troisième état.
      if (evenement.statut !== CAPACITE_ACTIVE) {
        return `${qui} est désactivé et ne recevra plus de tâches`;
      }
      return evenement.instances !== null
        ? `${qui} est activé — jusqu'à ${evenement.instances} tâche(s) en parallèle`
        : `${qui} est activé et peut recevoir des tâches`;
    }
    case EVENEMENT_MESSAGE_INTER_AGENTS: {
      const apropos = cite(sujet) ? ` à propos de ${cite(sujet)}` : "";
      return evenement.agent
        ? `${evenement.agent} a envoyé un message${apropos}`
        : `Message entre agents${apropos}`;
    }
    case EVENEMENT_VALIDATION_DEMANDE: {
      const quoi = cite(sujet) || "une action";
      return evenement.agent
        ? `${evenement.agent} attend votre accord pour ${quoi}`
        : `Votre accord est attendu pour ${quoi}`;
    }
    case EVENEMENT_VALIDATION_DECISION: {
      const quoi = cite(sujet) || "une action";
      return evenement.statut === VALIDATION_APPROUVEE
        ? `Vous avez approuvé ${quoi}`
        : `Vous avez refusé ${quoi}`;
    }
    case EVENEMENT_CHAT_MESSAGE: {
      // #84 : `agent` est le fil, `statut` l'auteur du message.
      const fil = evenement.agent || "un agent";
      return evenement.statut === CHAT_AUTEUR_UTILISATEUR
        ? `Vous avez écrit à ${fil}`
        : `${fil} vous a répondu`;
    }
    case EVENEMENT_PLAYBOOK_PROPOSITION: {
      // #183 : `statut` porte le numéro de brouillon, `detail` la justification.
      const fil = evenement.agent || "un agent";
      const brouillon = evenement.statut ? ` (brouillon n° ${evenement.statut})` : "";
      return `${fil} propose une amélioration de son playbook${brouillon}`;
    }
    case EVENEMENT_EXECUTION_STATUT:
      return phraseExecution(evenement);
    default:
      return `${evenement.agent || "?"}${sujet ? ` — ${sujet}` : ""}${
        evenement.statut ? ` : ${libelleStatut(evenement.statut)}` : ""
      }`;
  }
}

/** Un champ brut d'un événement, tel que le dépli d'une ligne le montre. */
export type ChampEvenement = { libelle: string; valeur: string };

/**
 * Le détail **brut** d'un événement (#250) — exactement ce que la phrase a
 * volontairement laissé de côté : identifiants, statut du bus, exécution,
 * ticket, projet, texte libre du moteur, horodatage complet.
 *
 * C'est la contrepartie du critère « une phrase plutôt qu'un identifiant » :
 * l'identifiant n'a pas disparu de l'UI, il a changé de plan. Les champs vides
 * sont omis — un événement n'en renseigne jamais la totalité (une activité
 * d'agent n'a pas forcément de `tache_id`). L'horodatage sort **tel quel**, en
 * ISO : c'est le seul endroit où on veut la valeur du bus, pas sa mise en forme.
 */
export function detailEvenement(evenement: Evenement): ChampEvenement[] {
  const champs: ChampEvenement[] = [
    { libelle: "Type", valeur: evenement.type },
    { libelle: "Tâche", valeur: evenement.tache_id },
    { libelle: "Statut", valeur: evenement.statut },
    {
      libelle: "Agent",
      valeur: evenement.agent
        ? `${evenement.agent}${evenement.role ? ` (${evenement.role})` : ""}`
        : "",
    },
    { libelle: "Exécution", valeur: evenement.run_id },
    { libelle: "Projet", valeur: evenement.projet_id ?? "" },
    { libelle: "Ticket", valeur: evenement.ticket?.id ?? "" },
    { libelle: "Horodatage", valeur: evenement.horodatage },
    { libelle: "Détail", valeur: evenement.detail },
  ];
  return champs.filter((champ) => champ.valeur !== "");
}

/**
 * Une **rafale** : des événements consécutifs qui parlent de la même chose et ne
 * méritent qu'une ligne (#250). `tete` est le plus récent — le flux arrive du
 * plus récent au plus ancien — et c'est lui qui donne la phrase ; `evenements`
 * garde la rafale entière, dans cet ordre, pour le dépli.
 */
export type GroupeEvenements = {
  /** Clé stable pour le rendu (React) — unique dans la liste rendue. */
  cle: string;
  /** Ce qui rassemble la rafale, vide quand l'événement ne groupe jamais. */
  rafale: string;
  tete: Evenement;
  evenements: Evenement[];
};

/**
 * Ce qui rassemble deux événements consécutifs : **la même tâche** avant tout —
 * c'est la rafale que décrit le ticket (N transitions d'une même tâche). À
 * défaut de tâche, le même type pour le même agent, ce qui replie les séries
 * d'étapes hors tâche (planification, relances) sans jamais mélanger deux
 * sujets. Une chaîne vide signifie « ne groupe jamais » : sans tâche ni agent,
 * rien ne dit que deux lignes voisines parlent de la même chose.
 */
function cleRafale(evenement: Evenement): string {
  if (evenement.tache_id) return `tache:${evenement.tache_id}`;
  if (evenement.agent) return `${evenement.type}@${evenement.agent}`;
  return "";
}

/**
 * Replie les rafales d'une liste d'événements (#250) : les transitions
 * consécutives d'une même tâche deviennent une ligne qui se déplie.
 *
 * Le repli est **consécutif** à dessein : il resserre le bruit d'une tâche qui
 * s'agite sans jamais réordonner le fil ni rapprocher deux moments éloignés —
 * une ligne reste à sa place dans le temps, ce qui est tout l'intérêt d'un fil
 * d'activité. Un événement seul rend un groupe d'un seul élément : l'appelant
 * n'a qu'un cas à rendre.
 */
export function grouperEvenements(evenements: Evenement[]): GroupeEvenements[] {
  const groupes: GroupeEvenements[] = [];
  evenements.forEach((evenement, index) => {
    const rafale = cleRafale(evenement);
    const precedent = groupes[groupes.length - 1];
    if (rafale !== "" && precedent?.rafale === rafale) {
      precedent.evenements.push(evenement);
      return;
    }
    groupes.push({
      cle: `${evenement.horodatage}-${index}`,
      rafale,
      tete: evenement,
      evenements: [evenement],
    });
  });
  return groupes;
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

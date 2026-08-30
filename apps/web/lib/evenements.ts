/**
 * Résumé et tri d'importance des événements du flux temps réel (`WS
 * /ws/evenements`, #46). Partagé entre le fil d'activité du tableau de bord
 * (#47), le centre de notifications déroulant (#119) et l'onglet Logs d'un agent
 * (#266) : « qui fait quoi » se lit à l'identique aux trois endroits, et chacun
 * n'a qu'à trier — le notable pour la cloche, le **niveau** pour les logs.
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
  EVENEMENT_RUN_PLAN,
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
  STATUT_ACTIVITE,
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
    // Ce que l'agent fait **pendant** que sa tâche dure (#479). Seule branche de
    // cette table où la phrase est le `detail` et non le `titre` : le titre est
    // celui de la tâche, que la carte du Kanban montre déjà à côté — le rendre
    // ici dirait « dev — Écrire le module en cours » et tairait le geste, qui est
    // la seule chose que cette ligne apporte.
    //
    // Le détail est déjà une phrase composée par le fournisseur
    // (`maestro/providers/activite.py`) : un geste seul (« Read ·
    // engine/executor.py ») ou une salve qui annonce son regroupement (« 7
    // gestes · … »). Le front ne la réécrit pas — la composition vit là où l'on
    // sait ce qui a été observé et ce qui a été regroupé.
    case STATUT_ACTIVITE:
      return evenement.detail
        ? `${qui ? `${qui} · ` : ""}${evenement.detail}`
        : `${qui ? `${qui} ` : ""}travaille sur ${quoi}`;
    // Le moteur consigne la décision humaine sur l'étape `:validation`
    // (executor.py) — c'est la même information que `validation.decision`, vue
    // depuis le journal du run.
    case "approuve":
      return `Accord donné : ${quoi}`;
    case "refuse":
      return `Accord refusé : ${quoi}`;
    case "refus_outil":
      return qui ? `${qui} s'est vu refuser un outil : ${quoi}` : `Outil refusé : ${quoi}`;
    // L'issue d'un **arbitrage humain sur un acte** (#583) — approuvé, refusé,
    // ou toujours en attente. C'est la seule des trois que `refus_outil` ne
    // pouvait pas porter : « s'est vu refuser un outil » est faux pour un appel
    // qu'une personne vient d'approuver. On rend donc le `detail`, qui *est*
    // l'issue (`maestro/providers/arbitrage.py`) — et non le titre de la tâche,
    // que la carte du Kanban montre déjà, comme pour l'activité plus haut.
    case "arbitrage_outil":
      return evenement.detail
        ? `${qui ? `${qui} · ` : ""}${evenement.detail}`
        : `${qui ? `${qui} — ` : ""}arbitrage sur ${quoi}`;
    // Ce qui est arrivé au **projet** de l'utilisateur quand la tâche s'est
    // soldée (#705). Comme l'activité et l'arbitrage plus haut, la phrase est le
    // `detail` — le résumé du diff fusionné, ou la cause du refus : le titre ne
    // dirait que le nom de la tâche, que la carte du Kanban montre déjà, et
    // tairait la seule chose que cette ligne apporte.
    //
    // Les trois issues sont rendues, « rien à fusionner » comprise : c'est elle
    // qui répond quand un run vert laisse le projet vide, le défaut que #568 a
    // mesuré et que le silence reproduirait.
    case "fusion_faite":
    case "fusion_sans_objet":
    case "fusion_refusee":
      return `${libelleStatut(evenement.statut)}${
        evenement.detail ? ` — ${evenement.detail}` : ` : ${quoi}`
      }`;
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
    case EVENEMENT_RUN_PLAN:
      // #490 : le plan du run est arrêté. Le volume est lu dans `detail`, où le
      // backend l'a mis, et non recompté sur `plan` : le journal requêtable
      // (#478) ne garde pas les charges lourdes d'un événement, si bien qu'un
      // fil relu après rechargement compterait zéro nœud et l'annoncerait.
      // Historique et direct disent la même phrase parce qu'ils lisent le même
      // champ, pas parce que deux calculs concordent.
      return evenement.detail
        ? `Le plan du run est arrêté : ${evenement.detail}`
        : "Le plan du run est arrêté";
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

/* ------------------------------------------------------------------ *
 * Le niveau d'une ligne (#266)
 * ------------------------------------------------------------------ */

/** Ce qui a échoué ou s'est arrêté. */
export const NIVEAU_ERREUR = "erreur";
/** Ce que la politique de permissions a écarté. */
export const NIVEAU_REFUS = "refus";
/** Ce qu'un humain a tranché, ou attend de trancher. */
export const NIVEAU_DECISION = "decision";
/** Le travail ordinaire : gestes, statuts, messages. */
export const NIVEAU_INFO = "info";

export type NiveauLog =
  | typeof NIVEAU_ERREUR
  | typeof NIVEAU_REFUS
  | typeof NIVEAU_DECISION
  | typeof NIVEAU_INFO;

/**
 * Les quatre niveaux, du plus au moins pressant — l'ordre du filtre de l'onglet
 * Logs (#266).
 *
 * **Le niveau est la FAMILLE, pas une sévérité de plus.** C'était l'arbitrage du
 * ticket : « erreur / avertissement / info » aurait été le réflexe, mais aucun de
 * ces trois mots ne permet d'isoler *les refus*, qui sont la question qu'on pose
 * le plus souvent à un journal d'agent (« qu'est-ce qu'on lui a interdit ? »).
 * Les quatre valeurs ci-dessous sont donc exactement les quatre choses que le
 * ticket promet de couvrir — appels d'outil, refus de permission, décisions,
 * erreurs —, si bien que chacune s'isole d'un choix. La sévérité, elle, ne sert
 * plus qu'à les **ordonner**.
 *
 * Elles se lisent sur le `statut` du bus plutôt que sur le type d'événement, et
 * c'est là que vit le sens : un `agent.activite` est tour à tour un geste, un
 * refus, un arbitrage ou une fusion refusée selon le seul statut qu'il porte
 * (`maestro/engine/executor.py`, `STATUT_*`).
 */
export const NIVEAUX_LOG: { cle: NiveauLog; libelle: string }[] = [
  { cle: NIVEAU_ERREUR, libelle: "Erreur" },
  { cle: NIVEAU_REFUS, libelle: "Refus" },
  { cle: NIVEAU_DECISION, libelle: "Décision" },
  { cle: NIVEAU_INFO, libelle: "Info" },
];

/**
 * Ce qui a échoué : une tâche ou une étape en échec, une exécution en échec
 * (`EXECUTION_ECHEC` vaut le même mot), une tâche que la cascade de #43 a tuée
 * avant qu'elle démarre, une fusion que Git ou le périmètre a repoussée (#705).
 */
const STATUTS_ERREUR = new Set(["echec", "bloquee", "fusion_refusee"]);

/**
 * Le refus d'un **appel d'outil** par la politique allow/deny (#110,
 * `STATUT_REFUS_OUTIL`). Il est seul de sa famille, et il la mérite : c'est le
 * seul événement qui dise ce que l'agent n'a **pas** pu faire.
 */
const STATUTS_REFUS = new Set(["refus_outil"]);

/**
 * Ce qui passe par un humain. Les trois premiers sont l'issue d'une étape
 * `:validation` ou d'un arbitrage sur un acte (#583) ; le quatrième est un
 * blocage que l'agent **déclare** (#719) — il travaille encore et demande de
 * l'aide, ce qui est une décision attendue et non une panne : le ranger en
 * erreur annoncerait un abandon à l'instant précis où quelqu'un appelle.
 */
const STATUTS_DECISION = new Set([
  "approuve",
  "refuse",
  "arbitrage_outil",
  "blocage_signale",
]);

/** Les deux canaux qui *sont* une décision humaine, quel que soit leur statut. */
const TYPES_DECISION = new Set<string>([
  EVENEMENT_VALIDATION_DEMANDE,
  EVENEMENT_VALIDATION_DECISION,
]);

/**
 * Le niveau d'une ligne de journal (#266) — la famille dont elle relève.
 *
 * Même contrat que `libelleStatut` et `libelleTypeEvenement` : ce que le front
 * ne sait pas classer retombe sur `info` plutôt que de disparaître. Un niveau
 * fourre-tout est le prix d'un flux qui s'enrichit côté backend sans que ce
 * module le sache ; une ligne escamotée par un filtre serait bien pire, le
 * propre d'un journal étant qu'on y cherche ce qu'on n'attendait pas.
 */
export function niveauEvenement(evenement: Evenement): NiveauLog {
  if (STATUTS_REFUS.has(evenement.statut)) return NIVEAU_REFUS;
  if (STATUTS_ERREUR.has(evenement.statut)) return NIVEAU_ERREUR;
  if (
    STATUTS_DECISION.has(evenement.statut) ||
    TYPES_DECISION.has(evenement.type)
  ) {
    return NIVEAU_DECISION;
  }
  return NIVEAU_INFO;
}

/** Le nom lisible d'un niveau — ce que la liste déroulante du filtre propose. */
export function libelleNiveau(niveau: NiveauLog): string {
  return NIVEAUX_LOG.find(({ cle }) => cle === niveau)?.libelle ?? niveau;
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

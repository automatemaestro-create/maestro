/**
 * Miroir TypeScript des formes JSON du backend Control Tower (ticket #46) :
 * `EtatTache.to_dict` / `EtatAgent.to_dict` (REST) et `Event.to_dict` (WebSocket)
 * — voir maestro/controltower/state.py et events.py. Les champs inconnus du
 * front restent des chaînes libres : le flux peut s'enrichir sans casser l'UI.
 */

/**
 * La mesure d'usage d'une étape (`StepUsage.to_dict`, #57) : tokens
 * entrée/sortie, coût estimé, durées. `cout_usd` et les durées restent null
 * quand rien n'a été rapporté (inconnu ≠ nul).
 */
export type Usage = {
  appels: number;
  tokens_entree: number;
  tokens_sortie: number;
  tokens_total: number;
  cout_usd: number | null;
  duree_ms: number | null;
  duree_api_ms: number | null;
  tours: number;
  outils: string[];
};

/** Une tâche telle que servie par `GET /api/taches` — la carte du Kanban. */
export type Tache = {
  id: string;
  titre: string;
  statut: string;
  agent: string;
  role: string;
  run_id: string;
  cout_usd: number | null;
  usage: Usage | null;
  horodatage: string;
};

/** Un agent tel que servi par `GET /api/agents` — la fiche du panneau Agents. */
export type EtatAgent = {
  nom: string;
  role: string;
  statut: string;
  tache_courante: string;
  taches_terminees: number;
  taches_echouees: number;
  cout_usd: number | null;
  derniere_activite: string;
};

/** Un événement du flux `WS /ws/evenements` — la ligne du fil d'activité. */
export type Evenement = {
  type: string;
  run_id: string;
  tache_id: string;
  titre: string;
  agent: string;
  role: string;
  statut: string;
  detail: string;
  description: string;
  cout_usd: number | null;
  usage: Usage | null;
  horodatage: string;
};

/** L'entrée « tâche » du grand livre d'un run (`TaskCost.to_dict`, #57). */
export type CoutTache = {
  tache_id: string;
  nom: string;
  agent: string;
  role: string;
  statut: string;
  usage: Usage;
};

/**
 * Le grand livre d'une exécution, servi par `GET /api/executions/{run_id}/cout`
 * (`RunCost.to_dict`, #57) : la part de planification (l'orchestrateur), le
 * coût par tâche et l'agrégat du run — la matière du panneau Coûts (#58).
 */
export type CoutExecution = {
  run_id: string;
  planification: Usage;
  total: Usage;
  taches: CoutTache[];
};

/**
 * Une demande de validation humaine telle que servie par `GET /api/validations`
 * (#48, `EtatValidation.to_dict`) : le contexte pour trancher — tâche, agent,
 * action demandée (description), justification (raison) — puis l'issue.
 */
export type Validation = {
  tache_id: string;
  titre: string;
  description: string;
  agent: string;
  role: string;
  raison: string;
  statut: string;
  decision: string;
  horodatage: string;
};

/** Statuts d'agent exposés par l'API (maestro/controltower/state.py). */
export const AGENT_LIBRE = "libre";
export const AGENT_OCCUPE = "occupe";

/** Statuts d'une demande de validation humaine (maestro/controltower/state.py). */
export const VALIDATION_EN_ATTENTE = "en_attente";
export const VALIDATION_APPROUVEE = "approuvee";
export const VALIDATION_REFUSEE = "refusee";

/** Types d'événements diffusés (maestro/controltower/events.py). */
export const EVENEMENT_TACHE_STATUT = "tache.statut";
export const EVENEMENT_TACHE_REASSIGNATION = "tache.reassignation";
export const EVENEMENT_AGENT_ACTIVITE = "agent.activite";
export const EVENEMENT_MESSAGE_INTER_AGENTS = "message.inter_agents";
export const EVENEMENT_VALIDATION_DEMANDE = "validation.demande";
export const EVENEMENT_VALIDATION_DECISION = "validation.decision";

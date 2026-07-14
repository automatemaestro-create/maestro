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

/**
 * Un agent tel que servi par `GET /api/agents` — la fiche du panneau Agents.
 * `actif` et `instances` (#86, EF-21) portent le contrôle de capacité : un
 * agent désactivé ne reçoit plus de tâches, `instances` plafonne ses
 * exécutions simultanées — `statut` reste l'activité (libre/occupé).
 */
export type EtatAgent = {
  nom: string;
  role: string;
  statut: string;
  tache_courante: string;
  taches_terminees: number;
  taches_echouees: number;
  cout_usd: number | null;
  derniere_activite: string;
  actif: boolean;
  instances: number;
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
  instances: number | null;
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

/** Granularité de la série temporelle analytics (maestro/controltower/analytics.py, #87). */
export type PasSerie = "minute" | "heure" | "jour";

/** La ligne « par agent » de la vue analytics (`CoutAgent.to_dict`, #87). */
export type CoutAgentAgrege = {
  agent: string;
  role: string;
  /** Tâches distinctes auxquelles son usage a été attribué (0 : planification). */
  taches: number;
  usage: Usage;
};

/** La ligne « par tâche » de la vue analytics (`CoutTacheAgregee.to_dict`, #87). */
export type CoutTacheAgregee = {
  tache_id: string;
  nom: string;
  agent: string;
  role: string;
  statut: string;
  /** Exécutions où la tâche est apparue (> 1 : re-tentatives, dépense cumulée). */
  executions: number;
  usage: Usage;
};

/** La ligne « par exécution » de la vue analytics (`CoutExecutionResume.to_dict`, #87). */
export type CoutExecutionResume = {
  run_id: string;
  nb_taches: number;
  debut: string;
  fin: string;
  usage: Usage;
};

/** Un seau de la série temporelle (`PointCout.to_dict`, #87) : usage cumulé sur la période. */
export type PointCout = {
  periode: string;
  usage: Usage;
};

/**
 * La vue coûts & analytics, servie par `GET /api/analytics/couts`
 * (`AnalyticsCouts.to_dict`, #87) : agrégats transverses par tâche, par agent
 * et par exécution, total de la fenêtre et série temporelle du coût.
 */
export type AnalyticsCouts = {
  depuis: string | null;
  pas: string;
  total: Usage;
  executions: CoutExecutionResume[];
  agents: CoutAgentAgrege[];
  taches: CoutTacheAgregee[];
  serie: PointCout[];
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

/**
 * La fiche du playbook d'un agent (`GET /api/playbooks`, #76) : version
 * courante et provenance. `version` 0 et `source` « defaut » tant que le
 * playbook n'a jamais été édité (le contenu effectif est le prompt du code).
 */
export type PlaybookFiche = {
  agent: string;
  role: string;
  version: number;
  nb_versions: number;
  source: string;
  cree_le: string | null;
};

/** La fiche avec le contenu effectif (`GET /api/playbooks/{agent}`). */
export type PlaybookDetail = PlaybookFiche & { contenu: string };

/**
 * Une entrée de l'historique d'un playbook
 * (`GET /api/playbooks/{agent}/versions`, EF-25) — métadonnées seules.
 */
export type VersionPlaybook = {
  agent: string;
  version: number;
  cree_le: string;
};

/** Une version passée, contenu compris (`GET .../versions/{version}`). */
export type VersionPlaybookDetail = VersionPlaybook & { contenu: string };

/**
 * Une fiche du catalogue d'agents (`GET /api/catalogue`, #72) : les agents par
 * défaut du code (`source` « defaut ») et les personnalisés persistés
 * (« personnalise »). Les dates ne sont posées que sur les personnalisés ;
 * `modele` null signifie « le modèle par défaut des exécutants » et
 * `fournisseur` est déclaratif au POC (le moteur est mono-fournisseur).
 */
export type AgentCatalogue = {
  nom: string;
  role: string;
  competences: string[];
  modele: string | null;
  fournisseur: string | null;
  source: string;
  cree_le: string | null;
  modifie_le: string | null;
};

/** La fiche avec sa définition complète (`GET /api/catalogue/{nom}`). */
export type AgentCatalogueDetail = AgentCatalogue & { playbook: string };

/**
 * Les champs éditables d'un agent personnalisé (#73) : le corps de
 * `POST /api/catalogue` (avec le nom en plus) et de `PUT /api/catalogue/{nom}`
 * (le nom vit dans l'URL — c'est la clé de routage, il ne change pas).
 */
export type DefinitionAgent = {
  role: string;
  competences: string[];
  playbook: string;
  modele: string | null;
  fournisseur: string | null;
};

/**
 * Un message du fil de chat utilisateur ↔ agent (`MessageChat.to_dict`, #84) :
 * `agent` est le fil d'appartenance (le nom d'agent du catalogue), `auteur`
 * l'émetteur — `utilisateur` ou ce même nom d'agent.
 */
export type MessageChat = {
  agent: string;
  auteur: string;
  contenu: string;
  horodatage: string;
};

/** Le fil complet d'un agent (`GET /api/chat/{agent}`, #84). */
export type FilChat = {
  agent: string;
  role: string;
  messages: MessageChat[];
};

/** L'auteur « humain » d'un message du fil (maestro/controltower/chat.py, #84). */
export const CHAT_AUTEUR_UTILISATEUR = "utilisateur";

/** Provenances d'un playbook (maestro/controltower/app.py, #76). */
export const PLAYBOOK_SOURCE_DEFAUT = "defaut";
export const PLAYBOOK_SOURCE_STOCKAGE = "stockage";

/** Provenances d'une fiche du catalogue d'agents (maestro/controltower/app.py, #72). */
export const AGENT_SOURCE_DEFAUT = "defaut";
export const AGENT_SOURCE_PERSONNALISE = "personnalise";

/** Statuts d'agent exposés par l'API (maestro/controltower/state.py). */
export const AGENT_LIBRE = "libre";
export const AGENT_OCCUPE = "occupe";

/** États portés par un événement `agent.capacite` (#86, maestro/controltower/state.py). */
export const CAPACITE_ACTIVE = "active";
export const CAPACITE_DESACTIVE = "desactive";

/** Statuts d'une demande de validation humaine (maestro/controltower/state.py). */
export const VALIDATION_EN_ATTENTE = "en_attente";
export const VALIDATION_APPROUVEE = "approuvee";
export const VALIDATION_REFUSEE = "refusee";

/** Types d'événements diffusés (maestro/controltower/events.py). */
export const EVENEMENT_TACHE_STATUT = "tache.statut";
export const EVENEMENT_TACHE_REASSIGNATION = "tache.reassignation";
export const EVENEMENT_AGENT_ACTIVITE = "agent.activite";
export const EVENEMENT_AGENT_CAPACITE = "agent.capacite";
export const EVENEMENT_MESSAGE_INTER_AGENTS = "message.inter_agents";
export const EVENEMENT_VALIDATION_DEMANDE = "validation.demande";
export const EVENEMENT_VALIDATION_DECISION = "validation.decision";
export const EVENEMENT_CHAT_MESSAGE = "chat.message";

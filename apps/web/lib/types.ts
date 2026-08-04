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

/** La réponse de la sonde de vitalité (`GET /api/sante`) : `{ statut: "ok" }`. */
export type Sante = {
  statut: string;
};

/**
 * Une tâche telle que servie par `GET /api/taches` — la carte du Kanban.
 * `ticket` (#183/#187) porte la référence du ticket externe dont elle relève —
 * `null` quand aucune n'a été transportée (inconnu ≠ absent). La vue reste
 * alors strictement inchangée.
 */
export type Tache = {
  id: string;
  titre: string;
  statut: string;
  agent: string;
  role: string;
  run_id: string;
  cout_usd: number | null;
  usage: Usage | null;
  ticket: ReferenceTicket | null;
  horodatage: string;
};

/**
 * Un agent tel que servi par `GET /api/agents` — la fiche du panneau Agents.
 * `actif` et `instances` (#86, EF-21) portent le contrôle de capacité : un
 * agent désactivé ne reçoit plus de tâches, `instances` plafonne ses
 * exécutions simultanées — `statut` reste l'activité (libre/occupé).
 * `taches_en_cours` (#100) liste les tâches portées en même temps par les
 * instances de l'agent (ordre de démarrage) ; `tache_courante` reste la plus
 * récemment démarrée encore en vol.
 */
export type EtatAgent = {
  nom: string;
  role: string;
  statut: string;
  tache_courante: string;
  taches_en_cours: string[];
  taches_terminees: number;
  taches_echouees: number;
  cout_usd: number | null;
  derniere_activite: string;
  actif: boolean;
  instances: number;
};

/**
 * Un événement du flux `WS /ws/evenements` — la ligne du fil d'activité.
 * `ticket` (#183/#187) accompagne les événements de tâche : la référence de
 * ticket externe voyage avec le flux — `null` quand aucune n'est portée.
 */
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
  ticket: ReferenceTicket | null;
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
  /** Le ticket externe dont relève la tâche (#187), `null` s'il n'y en a pas. */
  ticket: ReferenceTicket | null;
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
  /** Le ticket externe dont relève la tâche (#187), `null` s'il n'y en a pas. */
  ticket: ReferenceTicket | null;
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
 * Une proposition d'auto-amélioration en brouillon
 * (`GET /api/playbooks/{agent}/propositions`, #111) : une version candidate
 * suggérée à partir des échecs d'un run, jamais courante et jamais chargée par
 * le moteur tant qu'un humain ne l'a pas appliquée. `version` est son numéro de
 * brouillon (numérotation propre, distincte de celle des versions) et
 * `justification` la raison liée aux échecs analysés (absente si le modèle n'en
 * a pas rendu).
 */
export type PropositionPlaybook = {
  agent: string;
  version: number;
  cree_le: string;
  provenance: string;
  justification?: string;
};

/** Une proposition, contenu compris (`GET .../propositions/{numero}`). */
export type PropositionPlaybookDetail = PropositionPlaybook & {
  contenu: string;
};

/**
 * Un serveur MCP déclaré pour un agent (`ServeurMcp.to_dict`, #104) : une
 * commande locale (`type` « stdio » : commande + args + env) ou un endpoint
 * distant (« sse »/« http » : url + headers). Forme publique : les valeurs
 * d'env/headers sans référence `${VAR}` sont masquées par le backend.
 * `optionnel` (#125) : serveur omis du montage (sans échec) tant que ses
 * références ne sont pas résolues — capacité activée par un secret fourni
 * par l'humain.
 */
export type ServeurMcp = {
  nom: string;
  type: string;
  commande: string;
  args: string[];
  url: string;
  env: Record<string, string>;
  headers: Record<string, string>;
  optionnel: boolean;
};

/** Les trois modes d'auth classés par la revue #126 (maestro/agents/mcp_registry.py). */
export const MCP_MODE_TOKEN = "token_statique";
export const MCP_MODE_APPAIRAGE = "appairage";
export const MCP_MODE_OAUTH = "oauth_importe";

/**
 * Une variable `${VAR}` à fournir pour instancier une entrée du registre
 * (`VariableSecret.to_dict`, #131) : sa clé, une aide de saisie, et si c'est un
 * vrai secret (token, à chiffrer/masquer) ou un identifiant non sensible mais
 * requis (canal d'appairage, ID d'espace de travail).
 */
export type VariableSecret = {
  cle: string;
  description: string;
  secret: boolean;
};

/**
 * Une entrée du **registre curé** de serveurs MCP (`GET /api/mcp/registre`,
 * #131) : un template recherchable portant transport, gabarit `${VAR}`, mode
 * d'auth (docs/21), variables à fournir (`secrets`) et lien de procédure côté
 * outil. `curee: true` marque l'appartenance à l'allowlist — seule une entrée
 * servie ici est instanciable (garde-fou supply-chain, docs/19).
 */
export type EntreeRegistreMcp = {
  id: string;
  nom: string;
  description: string;
  mode_auth: string;
  transport: string;
  commande: string;
  args: string[];
  url: string;
  env: Record<string, string>;
  headers: Record<string, string>;
  tags: string[];
  secrets: VariableSecret[];
  procedure_url: string;
  optionnel: boolean;
  curee: boolean;
};

/**
 * L'état d'un secret d'une intégration du pool (#133), sans sa valeur :
 * `present` dit s'il est configuré dans le coffre projet, `valide` s'il est
 * résolvable au montage (un token OAuth expiré ne l'est plus), `ephemere`
 * marque une valeur d'appairage jetable, `expire_le` l'échéance d'un token
 * expirable.
 */
export type EtatSecretPool = {
  cle: string;
  description: string;
  secret: boolean;
  present: boolean;
  valide: boolean;
  ephemere: boolean;
  expire_le: string | null;
};

/**
 * Une intégration du **pool projet** (`GET /api/mcp/pool`, #133) : la
 * déclaration instanciée (`serveur`, secrets masqués) enrichie de son mode
 * d'auth, de sa procédure et de l'état de ses secrets côté coffre projet —
 * jamais une valeur de secret. Le secret est saisi **une seule fois** ici, puis
 * partagé par tout agent qui active l'intégration.
 */
export type IntegrationPoolMcp = {
  id: string;
  serveur: ServeurMcp;
  mode_auth: string | null;
  procedure_url: string;
  curee: boolean;
  secrets: EtatSecretPool[];
};

/** Le pool projet servi par `GET /api/mcp/pool` : ses intégrations + une cause d'erreur. */
export type PoolMcp = {
  integrations: IntegrationPoolMcp[];
  erreur: string | null;
};

/**
 * La politique de permissions d'un agent (`PolitiqueOutils.to_dict`, #110) :
 * allow/deny par outil — noms d'outils intégrés, `mcp__<serveur>` pour un
 * serveur MCP entier, `mcp__<serveur>__<outil>` pour un outil MCP précis.
 * `deny` l'emporte ; `allow` vide = tout ce que le profil expose est permis.
 */
export type PolitiquePermissions = {
  allow: string[];
  deny: string[];
};

/**
 * Une fiche du catalogue d'agents (`GET /api/catalogue`, #72) : les agents par
 * défaut du code (`source` « defaut ») et les personnalisés persistés
 * (« personnalise »). Les dates ne sont posées que sur les personnalisés ;
 * `modele` null signifie « le modèle par défaut des exécutants » et
 * `fournisseur` est déclaratif au POC (le moteur est mono-fournisseur).
 * `mcp_serveurs` (#104) liste les serveurs MCP **effectifs** montés pour l'agent
 * (héritage `<agent>.json` composé avec le pool activé) ; `mcp_erreur` porte la
 * cause si une source est invalide. `mcp_pool` (#133) est le **pool projet** des
 * intégrations configurables (avec l'état de leurs secrets), `mcp_pool_erreur`
 * la cause si le pool stocké est invalide, et `mcp_activations` les ids du pool
 * **activés** pour cet agent — de quoi remplacer l'affichage lecture seule par
 * des interrupteurs par agent. `permissions` (#110) porte la politique
 * allow/deny effective appliquée à l'exécution (null : aucune politique — tout
 * permis) ; `permissions_erreur` la cause si la politique stockée est invalide.
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
  mcp_serveurs: ServeurMcp[];
  mcp_erreur: string | null;
  mcp_pool: IntegrationPoolMcp[];
  mcp_pool_erreur: string | null;
  mcp_activations: string[];
  permissions: PolitiquePermissions | null;
  permissions_erreur: string | null;
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
/** #187 : rattache une tâche à son ticket externe — ne porte que `ticket`. */
export const EVENEMENT_TACHE_REFERENCE = "tache.reference";
export const EVENEMENT_AGENT_ACTIVITE = "agent.activite";
export const EVENEMENT_AGENT_CAPACITE = "agent.capacite";
export const EVENEMENT_MESSAGE_INTER_AGENTS = "message.inter_agents";
export const EVENEMENT_VALIDATION_DEMANDE = "validation.demande";
export const EVENEMENT_VALIDATION_DECISION = "validation.decision";
export const EVENEMENT_CHAT_MESSAGE = "chat.message";
/**
 * L'apparition d'une proposition d'auto-amélioration de playbook (#183) : un
 * signal **global** (sans `run_id`) que l'UI badge et pousse en notification —
 * `agent` le fil, `role` son rôle, `statut` le numéro de brouillon, `detail` la
 * justification (cadrage #182, item 9).
 */
export const EVENEMENT_PLAYBOOK_PROPOSITION = "playbook.proposition";

// ---------------------------------------------------------------------------
// Contrats d'API v2 (#183) — formes JSON figées des routes des Phases 5/6,
// servies en fixtures par la démo (maestro.controltower.demo, module
// maestro/controltower/fixtures.py) et documentées à docs/05 §6. La voie front
// code contre ces formes ; le backend réel les remplira à contrat identique —
// les routes répondent 501 tant que leur lot n'est pas livré.
// ---------------------------------------------------------------------------

/**
 * La référence d'un ticket externe portée par une tâche (`GET /api/taches`,
 * #187) : identifiant lisible + URL. Générique — GitLab, Jira, Linear passent
 * par la même forme (aucun champ propre à un outil). `url` est vide quand seul
 * l'identifiant est connu (l'agent a nommé le ticket sans en donner le lien).
 */
export type ReferenceTicket = {
  id: string;
  url: string;
};

/** Statuts d'une exécution (maestro/controltower/state.py, #185). */
export const EXECUTION_EN_COURS = "en_cours";
export const EXECUTION_TERMINEE = "terminee";
export const EXECUTION_ANNULEE = "annulee";
export const EXECUTION_ECHEC = "echec";

/**
 * Le corps de `POST /api/executions` (#185) : l'objectif à décomposer, les
 * garde-fous (chacun `null` laisse le défaut du moteur) et le ticket externe
 * dont part le run (`null` si le run n'en relève d'aucun).
 */
export type LancementExecution = {
  objectif: string;
  plafond_cout_usd: number | null;
  plafond_tokens: number | null;
  timeout_tache_s: number | null;
  parallelisme: number | null;
  ticket: ReferenceTicket | null;
};

/**
 * Le résumé d'une exécution (`GET /api/executions`, `POST /api/executions` et
 * `.../annuler`, #185) : identité, `statut` (`EXECUTION_*`), nombre de tâches et
 * coût cumulé (`null` : aucun coût rapporté, inconnu ≠ nul). `debut` est posé au
 * lancement ; `fin` reste `null` tant que le run est en cours. `ticket` porte la
 * référence externe si le run en relève.
 */
export type ResumeExecution = {
  run_id: string;
  objectif: string;
  statut: string;
  nb_taches: number;
  cout_usd: number | null;
  ticket: ReferenceTicket | null;
  debut: string;
  fin: string | null;
};

/** Clés de tri et sens du journal requêtable (maestro/controltower/fixtures.py). */
export const TRI_JOURNAL_HORODATAGE = "horodatage";
export const TRI_JOURNAL_AGENT = "agent";
export const TRI_JOURNAL_TYPE = "type";
export const ORDRE_ASC = "asc";
export const ORDRE_DESC = "desc";

/**
 * Une entrée du journal requêtable (`GET /api/journal`) : un événement persisté
 * doté d'un `id` stable (référençable, triable) — le reste reprend la forme d'un
 * `Evenement` (type, run, tâche, agent, statut, détail, horodatage).
 */
export type EntreeJournal = {
  id: string;
  type: string;
  run_id: string;
  tache_id: string;
  agent: string;
  role: string;
  statut: string;
  detail: string;
  horodatage: string;
};

/**
 * Une page du journal requêtable (`GET /api/journal`) : les `entrees` de la
 * page, le `total` (après filtres, **avant** pagination), la `page` courante
 * (1-indexée), la `taille` de page et le nombre de `pages`.
 */
export type PageJournal = {
  entrees: EntreeJournal[];
  total: number;
  page: number;
  taille: number;
  pages: number;
};

/** Types d'un réglage de configuration (maestro/controltower/fixtures.py). */
export type TypeReglage = "chaine" | "entier" | "decimal" | "booleen" | "secret";

/**
 * Un réglage produit éditable (`GET /api/configuration`, couche 1 du cadrage
 * sécurité #182) : sa `valeur` courante (masquée par des points si `secret`),
 * son `type` (`TypeReglage`), sa `valeur_defaut`, s'il est `modifiable` depuis
 * l'UI (liste blanche stricte), sa `source` (`defaut` tant qu'il n'a jamais été
 * édité, `stockage` sinon), sa `version` (0 au défaut) et `modifie_le` (`null`
 * sur un réglage jamais touché).
 */
export type ReglageConfiguration = {
  cle: string;
  valeur: string;
  type: string;
  description: string;
  categorie: string;
  valeur_defaut: string;
  modifiable: boolean;
  secret: boolean;
  source: string;
  version: number;
  modifie_le: string | null;
};

/**
 * Le registre de configuration (`GET /api/configuration`) : les `reglages`, la
 * `version` du registre versionné (append-only) et une cause d'`erreur` si le
 * stockage est illisible (même contrat de visibilité que `mcp_erreur`).
 */
export type RegistreConfiguration = {
  reglages: ReglageConfiguration[];
  version: number;
  erreur: string | null;
};

/**
 * Une proposition d'auto-amélioration vue **globalement**
 * (`GET /api/playbooks/propositions`, #183) : la `PropositionPlaybook` de
 * l'agent enrichie de son `role` — de quoi l'afficher (badge, notifications)
 * sans un aller-retour par le catalogue. Le pendant temps réel est l'événement
 * `EVENEMENT_PLAYBOOK_PROPOSITION` du WebSocket.
 */
export type PropositionPlaybookGlobale = PropositionPlaybook & { role: string };

/** Types de trame d'un flux SSE de chat (maestro/controltower/fixtures.py). */
export const FRAGMENT_CHAT_DEBUT = "debut";
export const FRAGMENT_CHAT_DELTA = "fragment";
export const FRAGMENT_CHAT_FIN = "fin";
export const FRAGMENT_CHAT_ERREUR = "erreur";

/**
 * Une trame du flux SSE d'un fil de chat (`GET /api/chat/{agent}/flux`, #183) :
 * chaque `data: <json>` du `text/event-stream` en est une. `type` dit son rôle
 * (`debut` ouvre, `fragment` incrémente, `fin` clôt, `erreur` signale) ;
 * `auteur` est l'émetteur (l'agent) ; `delta` porte l'incrément de texte (vide
 * hors `fragment`) ; `message` le `MessageChat` complet reconstitué, posé sur la
 * seule trame `fin` (`null` ailleurs).
 */
export type FragmentChat = {
  type: string;
  agent: string;
  auteur: string;
  delta: string;
  message: MessageChat | null;
};

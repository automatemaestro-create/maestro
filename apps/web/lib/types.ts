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
 * `projet_id` (#222) porte le projet auquel la tâche appartient — `null` quand
 * elle ne relève d'aucun projet. Le Kanban se restreint à un projet par
 * `GET /api/taches?projet=<id>` ; sans le paramètre, toutes les tâches sortent.
 *
 * `description`, `etapes` et `liens` (#246) portent le **détail** que la carte
 * ouvre sur place (#251). Ils sont déclarés **optionnels**, et c'est un choix :
 * le backend ne les sert pas encore, si bien qu'ils arrivent `undefined` sur
 * chaque tâche d'aujourd'hui — les typer requis mentirait au compilateur et
 * ferait planter la moindre lecture (`tache.etapes.length`). Le front les lit
 * donc toujours par `detailDe()` (lib/detailTache), jamais en direct.
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
  projet_id: string | null;
  horodatage: string;
  description?: string | null;
  etapes?: EtapeTache[] | null;
  liens?: LienUtile[] | null;
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
 * `projet_id` (#222) voyage de la même façon, sur les événements de tâche comme
 * sur ceux du cycle de vie d'un run — `null` hors de tout projet.
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
  projet_id: string | null;
  /**
   * Ce que portent les événements `brief.*` (#320, #321) — et rien d'autre :
   * `brief` le brief soumis, questionné ou retenu, `reponses` les réponses de
   * clarification appariées par position à **ses** questions, `tour`/`tours_max`
   * l'aller-retour et son plafond.
   *
   * Déclarés **optionnels** parce qu'ils le sont dans les faits : le backend les
   * sérialise sur tous les événements, mais les canaux de clarification n'existent
   * que depuis #321 — une trace relue d'un run antérieur n'en porte pas. C'est
   * `toursDeClarification` (lib/brief) qui les lit, jamais un composant en direct :
   * reconstituer un aller-retour demande d'apparier deux événements, et cette
   * règle-là n'a pas à vivre dans du JSX.
   */
  brief?: Brief | null;
  reponses?: string[] | null;
  tour?: number;
  tours_max?: number;
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
  /** Le projet auquel la tâche appartient (#222), `null` hors de tout projet. */
  projet_id: string | null;
};

/**
 * Le grand livre d'une exécution, servi par `GET /api/executions/{run_id}/cout`
 * (`RunCost.to_dict`, #57) : la part de planification (l'orchestrateur), le
 * coût par tâche et l'agrégat du run — la matière du panneau Coûts (#58).
 *
 * `brief` (#318) est la part de l'étape de **brief structuré**, comptée à part de
 * la planification : ce sont deux appels modèle distincts, et le brief peut être
 * régénéré par les allers-retours de clarification (#321). Nulle tant qu'aucun run
 * ne passe par cette étape — c'est le lot 6 (#320) qui la branche sur la boucle.
 */
export type CoutExecution = {
  run_id: string;
  planification: Usage;
  brief: Usage;
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
  /** Le projet auquel la tâche appartient (#222), `null` hors de tout projet. */
  projet_id: string | null;
};

/** La ligne « par exécution » de la vue analytics (`CoutExecutionResume.to_dict`, #87). */
export type CoutExecutionResume = {
  run_id: string;
  nb_taches: number;
  debut: string;
  fin: string;
  usage: Usage;
  /** Le projet dans lequel le run travaille (#222), `null` hors de tout projet. */
  projet_id: string | null;
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
 * `projet` (#222) rappelle l'identifiant demandé — `null` dès que la vue ne
 * porte pas sur un projet précis. `portee` (#277) lève l'ambiguïté de ce `null` :
 * elle dit **laquelle** des trois lectures a été servie (`tous`, `aucun` ou
 * l'identifiant) — un total ne se lit pas sans savoir de quoi il est le total.
 */
export type AnalyticsCouts = {
  depuis: string | null;
  pas: string;
  projet: string | null;
  portee: string;
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
 * `projet_id` (#277) est le projet de la tâche mise en pause : c'est par lui que
 * `?projet=` filtre le panneau, `null` quand elle ne relève d'aucun projet.
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
  diff: DiffProjet | null;
  projet_id: string | null;
  horodatage: string;
};

/**
 * Un fichier du diff d'application (#227, `Modification.to_dict`) : le chemin
 * **relatif à la racine du projet**, ce qui lui arrive, et de combien de lignes.
 * `ajouts`/`suppressions` valent 0 quand `binaire` est vrai — on sait alors que
 * le fichier change, pas de combien.
 */
export type ModificationProjet = {
  chemin: string;
  nature: string;
  ajouts: number;
  suppressions: number;
  binaire: boolean;
};

/**
 * Ce qu'une application écrirait dans le projet de l'utilisateur (#227, EF-37,
 * `DiffProjet.to_dict`) — la pièce jointe d'une demande de validation dont la
 * question est « applique-t-on ceci ? ». `branche` et `base` ne sont renseignées
 * que pour un projet **versionné** : l'accord y vaut fusion de `branche` vers
 * `base`, là où un projet non versionné voit ses fichiers recopiés dans la racine.
 */
export type DiffProjet = {
  modifications: ModificationProjet[];
  branche: string;
  base: string;
  fichiers: number;
  ajouts: number;
  suppressions: number;
};

/** Les natures d'une `ModificationProjet` (miroir des `NATURE_*` du moteur). */
export const NATURE_AJOUT = "ajoute";
export const NATURE_MODIFICATION = "modifie";
export const NATURE_SUPPRESSION = "supprime";

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
 * Le cycle de vie d'un run piloté par l'API (#185, `EVENEMENT_EXECUTION_STATUT`
 * côté moteur) : `statut` l'état résultant (`EXECUTION_*`), `titre` l'objectif et
 * `detail` la raison. Le backend le diffuse depuis #185 ; le front l'ignorait
 * jusqu'à #250, où il retombait sur la garde des types inconnus.
 */
export const EVENEMENT_EXECUTION_STATUT = "execution.statut";
/**
 * L'apparition d'une proposition d'auto-amélioration de playbook (#183) : un
 * signal **global** (sans `run_id`) que l'UI badge et pousse en notification —
 * `agent` le fil, `role` son rôle, `statut` le numéro de brouillon, `detail` la
 * justification (cadrage #182, item 9).
 */
export const EVENEMENT_PLAYBOOK_PROPOSITION = "playbook.proposition";
/**
 * Les quatre canaux du **cadrage** d'un run (#320, #321) — le run s'y arrête sur
 * un humain, ce qu'aucun autre événement ne dit.
 *
 * `brief.demande` suspend le run sur sa validation et porte le brief à relire ;
 * `brief.decision` le tranche (approuvé, corrigé ou refusé). `brief.questions` et
 * `brief.reponses` sont l'aller-retour de clarification qui les précède : le
 * premier porte le brief **dont** on pose les questions et le rang du tour, le
 * second les réponses appariées par position. Deux paires distinctes et non une :
 * décider clôt le cadrage, répondre le poursuit.
 */
export const EVENEMENT_BRIEF_DEMANDE = "brief.demande";
export const EVENEMENT_BRIEF_DECISION = "brief.decision";
export const EVENEMENT_BRIEF_QUESTIONS = "brief.questions";
export const EVENEMENT_BRIEF_REPONSES = "brief.reponses";

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

/**
 * L'avancement d'une étape de tâche (#246). Chaîne libre comme tous les statuts
 * du flux : un état inconnu du front n'efface pas l'étape, il la rend « à faire »
 * (même règle que la colonne « Autres » du Kanban).
 */
export const ETAPE_A_FAIRE = "a_faire";
export const ETAPE_EN_COURS = "en_cours";
export const ETAPE_FAITE = "faite";

/**
 * Une étape de la tâche (`GET /api/taches`, #246) — la ligne de checklist du
 * panneau de détail (#251) : ce qu'il y a à faire, et où on en est.
 */
export type EtapeTache = {
  libelle: string;
  etat: string;
};

/**
 * La nature d'un lien utile (#246). C'est elle — et non l'URL — qui décide du
 * rendu côté front (#251) : deviner « Figma » ou « GitLab » d'après le domaine
 * marcherait jusqu'à la première instance auto-hébergée.
 */
export const LIEN_MAQUETTE = "maquette";
export const LIEN_TICKET = "ticket";
export const LIEN_DEPOT = "depot";

/**
 * Un lien utile porté par la tâche (`GET /api/taches`, #246) : maquette Figma,
 * ticket Azure/GitLab, dépôt de code. `libelle` peut être vide (le front retombe
 * alors sur le nom de la nature) ; `url` passe par `lienExterneSur` avant tout
 * `href`, comme celle du ticket externe (#192).
 */
export type LienUtile = {
  libelle: string;
  url: string;
  nature: string;
};

/** Statuts d'une exécution (maestro/controltower/state.py, #185). */
export const EXECUTION_EN_COURS = "en_cours";
export const EXECUTION_TERMINEE = "terminee";
export const EXECUTION_ANNULEE = "annulee";
export const EXECUTION_ECHEC = "echec";
/**
 * Le run s'est arrêté sur son **brief** et attend une décision humaine (#320,
 * décision D5) : aucune tâche n'est créée d'ici là. État **non terminal** — le run
 * reste annulable comme n'importe quel run en vol.
 */
export const EXECUTION_EN_ATTENTE_BRIEF = "en_attente_brief";
/**
 * Le run a **posé les questions** de son brief et attend les réponses (#321), en
 * amont de la validation ci-dessus. Non terminal pour la même raison, et c'est ici
 * la troisième exigence du ticket : une attente de réponses peut durer, un run
 * qu'on ne pourrait plus arrêter pendant ce temps serait indiscernable d'un run
 * planté. Distinct d'`en_attente_brief` parce que ce n'est pas la même question —
 * on répond, on n'approuve pas : proposer « approuver/refuser » à quelqu'un à qui
 * on pose des questions serait une impasse.
 */
export const EXECUTION_EN_ATTENTE_REPONSES = "en_attente_reponses";

/**
 * Le **régime du brief** d'un run (#320) : `sans` décompose l'objectif brut (le
 * comportement d'avant ce lot), `auto` rédige le brief et le décompose sans
 * attendre personne (lancement headless), `humain` arrête le run dessus jusqu'à
 * décision. La Control Tower lance en `humain` par défaut : c'est la voie qui a,
 * par construction, quelqu'un devant.
 */
export const MODE_BRIEF_SANS = "sans";
export const MODE_BRIEF_AUTO = "auto";
export const MODE_BRIEF_HUMAIN = "humain";

/**
 * La **vitalité** d'un run non soldé (#348) : son hôte bat-il encore ? Un run
 * lancé depuis la Control Tower s'exécute en tâche de fond du process de l'API et
 * ne lui survit pas ; le journal durable conservant le dernier état publié, un run
 * dont l'hôte est tombé restait `en_cours` **pour toujours**. L'hôte publie donc un
 * battement périodique, et ces trois verdicts en découlent.
 *
 * `indetermine` n'est pas une commodité mais le refus explicite de deviner : le run
 * n'a **jamais** battu (trace antérieure à #348, registre injoignable), donc on ne
 * sait pas. Un run soldé, lui, n'en porte aucun (`null`) — la question ne se pose
 * plus pour un run qui a rendu son issue.
 */
export const VITALITE_VIVANT = "vivant";
export const VITALITE_ORPHELIN = "orphelin";
export const VITALITE_INDETERMINE = "indetermine";

/** Les trois types de source d'un objectif (#315, EF-39) — et rien d'autre. */
export const SOURCE_FICHIER = "fichier";
export const SOURCE_DOSSIER = "dossier";
export const SOURCE_URL = "url";

/**
 * Une source **déclarée** au lancement (#315/#317, docs/05 §6.1) : un fichier
 * téléversé (désigné par l'`id` rendu par `POST /api/sources`, à défaut par son
 * `nom` et sa `taille`), un dossier de références (`chemin`) ou une page
 * (`valeur`). Les champs inutiles au type sont simplement absents — c'est la
 * résolution côté backend qui juge, l'écran ne fait que déclarer.
 */
export type SourceDeclaree = {
  type: string;
  id?: string;
  nom?: string;
  chemin?: string;
  valeur?: string;
  taille?: number;
};

/**
 * La réponse de `POST /api/sources` (#317, docs/05 §6.8) : les fichiers reçus,
 * chacun avec l'`id` à reporter dans `sources[]` au lancement. `nom` est celui
 * que le serveur a assaini et `taille` compte les octets **reçus**, jamais ceux
 * qu'un client annonce.
 */
export type TeleversementSources = {
  sources: { id: string; type: string; nom: string; taille: number }[];
  total_octets: number;
};

/** Les trois états d'une lecture (#316) — « échoué » n'en est pas un. */
export const LECTURE_LUE = "lu";
export const LECTURE_TRONQUEE = "tronque";
export const LECTURE_IGNOREE = "ignore";

/**
 * Ce qu'une source est devenue à la lecture (#316, docs/05 §6.8) : son `etat`,
 * son coût estimé en `tokens`, et selon l'état le `motif`/`message` d'un rejet
 * ou la `limite` atteinte par une troncature. `entrees` porte les lectures
 * **filles** d'un dossier — une par fichier parcouru, avec son propre état.
 */
export type LectureSource = {
  nom: string;
  type: string;
  etat: string;
  tokens: number;
  motif: string;
  message: string;
  limite: string;
  entrees: LectureSource[];
};

/**
 * Le rapport de lecture d'un ensemble de sources : une ligne par source déclarée
 * — y compris celles qui n'ont pas été lues — et le coût estimé de l'ensemble.
 * Rendu par l'aperçu (`POST /api/sources/apercu`, #319) avant de lancer, et par
 * le lancement lui-même (#317).
 */
export type RapportLecture = {
  tokens: number;
  lectures: LectureSource[];
};

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
  /** Le projet dans lequel le run travaille (#222) — `null` : aucun projet. */
  projet_id: string | null;
  /**
   * La matière de l'objectif (#317, EF-39) — absente ou vide, le lancement est
   * exactement celui d'avant la Phase 8.
   */
  sources?: SourceDeclaree[];
  /**
   * Le régime du brief (#320, `MODE_BRIEF_*`) — omis ou `null` : `humain`, le
   * défaut de la Control Tower.
   */
  brief?: string | null;
};

/**
 * Le corps de `POST /api/executions/{run_id}/brief/decision` (#320) : approuver
 * — avec un brief **corrigé** qui devient l'entrée de la décomposition, ou sans
 * (`brief: null`) pour approuver tel quel — ou refuser, ce qui solde le run en
 * « annulée » sans qu'aucune tâche ait été créée.
 */
export type DecisionBrief = {
  approuve: boolean;
  brief: Brief | null;
};

/**
 * Le corps de `POST /api/executions/{run_id}/brief/reponses` (#321) : les réponses
 * aux questions de clarification, **appariées par position** aux `questions` du
 * brief que le run attend (`GET /api/executions/{run_id}` → `brief.questions`).
 *
 * Pas de clé ni d'identifiant de question, et c'est une décision (#318) : le brief
 * est régénéré **en entier** à chaque tour, donc une question n'a pas d'identité
 * stable d'une version à l'autre. Le tableau doit faire **exactement** la longueur
 * de `brief.questions`, sans quoi l'API répond 422 — une liste décalée affecterait
 * des réponses aux mauvaises questions en silence. Une chaîne **vide** est licite
 * et vaut « je ne sais pas » : la question partira en hypothèse explicite plutôt
 * que d'être reposée.
 */
export type ReponsesBrief = {
  reponses: string[];
};

/**
 * Le brief structuré (#318) — miroir de `packages/shared/schemas/brief.schema.json`
 * et de `maestro.orchestrator.schema.Brief`. Aucune clé n'est omise : les quatre
 * listes facultatives sont valides vides, ce qui permet à l'écran de validation
 * (#322) de présenter les sept sections sans distinguer « absent » de « vide ».
 */
export type Brief = {
  objectif: string;
  perimetre: string[];
  hors_perimetre: string[];
  contraintes: string[];
  criteres_acceptation: string[];
  hypotheses: string[];
  questions: string[];
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
  /** Le projet dans lequel le run travaille (#222), `null` hors de tout projet. */
  projet_id: string | null;
  /**
   * Le régime du brief de ce run (#320, `MODE_BRIEF_*`) — chaîne vide pour un run
   * publié hors de l'API, qui n'annonce aucun mode. Le **brief lui-même** n'est pas
   * dans le résumé mais dans le détail (`GET /api/executions/{run_id}`) : la liste
   * des runs n'a pas à porter sept sections de texte par ligne.
   */
  mode_brief?: string;
  /**
   * Depuis quand ce run attend un geste humain (#321) — horodatage ISO-8601 de
   * l'événement qui l'a suspendu, `null` dès qu'il repart ou qu'il est soldé.
   * C'est l'**ancienneté** de l'attente : sans elle, un run suspendu est
   * indiscernable d'un run planté, et c'est *depuis quand* qui permet d'en juger.
   * Renseignée pour les deux attentes (`en_attente_brief` et
   * `en_attente_reponses`) — une seule question, une seule réponse.
   */
  attente_depuis?: string | null;
  /**
   * Le tour de clarification en cours et le plafond annoncé (#321) — `0` tant que
   * le run n'en a joué aucun. C'est l'annonce de la borne, telle que l'écran la
   * rend : « tour 1 sur 2 ». Les **questions** elles-mêmes ne sont pas ici mais
   * dans le détail (`brief.questions`) : on ne peut pas y répondre depuis une liste.
   */
  tour_clarification?: number;
  tours_clarification_max?: number;
  /**
   * L'hôte de ce run bat-il encore (#348, `VITALITE_*`) ? `null` sur un run soldé :
   * la question ne se pose pas. C'est le seul signal qui distingue un run qui
   * travaille d'un run mort trois jours plus tôt — les deux affichent `en_cours`.
   */
  vitalite?: string | null;
  /**
   * Le cadrage de ce run a-t-il été **approuvé par un humain** (#349) ? Distinct de
   * « le run a un brief » : dès que le brief est soumis, le détail en porte un —
   * celui qui est *proposé*. Un run mort pendant l'attente en a donc un que personne
   * n'a validé, et il n'y a rien à y rejouer. C'est ce booléen qui dit si la relance
   * a de la matière, sans avoir à charger les sept sections pour le savoir.
   */
  brief_approuve?: boolean;
  /**
   * Le run **dont celui-ci est la suite** (#349) — chaîne vide pour un run qui ne
   * reprend personne. Le lien ne s'écrit que dans ce sens : le run repris n'est
   * jamais réécrit pour désigner son successeur, comme le fichier `reprise-de`
   * entre deux runs d'orchestration (#204).
   */
  reprise_de?: string;
  debut: string;
  fin: string | null;
  /**
   * Le rapport de lecture des sources (#317) — **seulement** dans la réponse du
   * lancement, jamais dans une relecture : il décrit une lecture, pas un fait du
   * run. Absent quand le run n'a pas de matière.
   */
  rapport?: RapportLecture;
};

/**
 * Le **détail** d'une exécution (`GET /api/executions/{run_id}`, #185) : son
 * résumé, plus ce que le résumé n'a pas — le `brief` soumis ou retenu (#320,
 * `null` si le run n'est pas passé par l'étape), le grand livre (#57) et la trace
 * événement par événement.
 *
 * C'est **la** lecture de l'écran de validation (#322) : le brief à relire et le
 * coût déjà engagé arrivent d'un seul appel, sur le run qu'on est en train de
 * trancher. La liste (`GET /api/executions`) dit lesquels attendent ; celle-ci dit
 * quoi montrer.
 */
export type DetailExecution = ResumeExecution & {
  brief: Brief | null;
  cout: CoutExecution;
  evenements: Evenement[];
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
 * `Evenement` (type, run, tâche, agent, statut, détail, projet, horodatage).
 * Le journal se restreint à un projet par `?projet=<id>` (#222) ; une entrée
 * sans `projet_id` ne relève d'aucun et sort donc de toute vue filtrée.
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
  projet_id: string | null;
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

/**
 * Le gestionnaire de versions d'un projet (docs/05 §6.7) — **détecté** sur le
 * disque, jamais déclaré par le client. `branche_base` est vide en HEAD
 * détaché, `distant` `null` sur un dépôt purement local.
 */
export type VcsProjet = {
  type: string;
  branche_base: string;
  distant: string | null;
};

/**
 * Ce qu'un projet expose aux agents : motifs `inclus` et `exclus`, **relatifs à
 * la racine** (style glob). `exclus` l'emporte sur `inclus`.
 */
export type PerimetreProjet = {
  inclus: string[];
  exclus: string[];
};

/**
 * Un projet de l'utilisateur (`GET /api/projets`, #223) : une racine sur le
 * disque et son périmètre. `racine` est **canonicalisée** et rendue en POSIX sur
 * les trois OS ; `vcs` est `null` quand le projet n'est pas versionné — ce qui
 * reste parfaitement déclarable.
 */
export type Projet = {
  id: string;
  nom: string;
  racine: string;
  origine: string;
  vcs: VcsProjet | null;
  perimetre: PerimetreProjet;
  cree_le: string;
  modifie_le: string;
};

/** Corps de `POST`/`PUT /api/projets` — le `vcs` n'y figure pas : il est constaté. */
export type DeclarationProjet = {
  nom: string;
  racine: string;
  origine: string;
  inclus: string[] | null;
  exclus: string[] | null;
};

/**
 * Un dossier listé par l'explorateur : son `nom`, son `chemin` absolu, le
 * marqueur `depot_git` (qui décide du patron d'écriture de #224) et le
 * `projet_id` du projet qui l'a déjà déclaré — `null` sinon.
 */
export type DossierExplorateur = {
  nom: string;
  chemin: string;
  depot_git: boolean;
  projet_id: string | null;
  /**
   * Pourquoi ce dossier est proposé — renseigné sur la **page d'entrée**
   * seulement (#278), `null` pour un sous-dossier énuméré. `utilisateur` : le
   * dossier utilisateur ; `recent` : le parent d'un projet récemment déclaré ;
   * `projet` : une racine déjà déclarée ; `volume` : un disque du poste ;
   * `configuree` : une racine de `MAESTRO_EXPLORATEUR_RACINES`.
   */
  origine: OrigineDossier | null;
};

/** Les origines qu'un point d'entrée de l'explorateur peut porter (#278). */
export type OrigineDossier =
  | "utilisateur"
  | "recent"
  | "projet"
  | "volume"
  | "configuree";

/**
 * L'état du **sélecteur de dossier natif** (`GET /api/projets/selecteur`, #278).
 * `disponible` décide entre un bouton et une phrase : l'écran ne montre jamais
 * un bouton qui échouerait au clic. `motif` porte le code stable
 * (`selecteur-hors-poste` en mode serveur, `selecteur-desactive`,
 * `selecteur-sans-outil`), `outil` le programme qui ouvrira le dialogue.
 */
export type DisponibiliteSelecteur = {
  disponible: boolean;
  motif: string | null;
  message: string;
  outil: string | null;
};

/**
 * Ce que rend `POST /api/projets/selecteur` (#278). `annule` distingue « la
 * fenêtre a été fermée » — un geste normal, pas une erreur — d'un chemin
 * choisi. `racine_valide` dit si ce chemin est **déclarable tel quel** ; sinon
 * `refus` porte le motif d'EF-38 (racine de disque, dossier utilisateur nu…),
 * que le formulaire affiche au lieu de le découvrir à la soumission.
 */
export type ChoixSelecteur = {
  annule: boolean;
  chemin: string | null;
  racine_valide: boolean;
  refus: RefusProjet | null;
};

/**
 * Une page de l'explorateur de dossiers (`GET /api/projets/explorateur`, #223) :
 * un navigateur ne livre jamais de chemin absolu, c'est le backend qui énumère
 * (docs/05 §2.7). `chemin` est `null` sur la page d'entrée (les racines
 * elles-mêmes) ; `parent` est `null` quand remonter sortirait des racines — la
 * frontière se **voit** dans la réponse au lieu de se découvrir au clic suivant.
 * `tronque` dit qu'au-delà de 500 entrées la liste est coupée.
 */
export type PageExplorateur = {
  chemin: string | null;
  parent: string | null;
  racines: string[];
  dossiers: DossierExplorateur[];
  tronque: boolean;
};

/**
 * Le corps d'erreur des routes projets : un **objet**, pas une phrase. `motif`
 * est un code stable (`chemin-sensible`, `hors-racines-explorables`,
 * `dossier-absent`…) que l'écran peut traduire ou router ; `message` la phrase
 * lisible. Un refus en porte toujours un — l'explorateur ne rend jamais une
 * liste vide à la place (docs/05 §6.7).
 */
export type RefusProjet = {
  motif: string;
  message: string;
};

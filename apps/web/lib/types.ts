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
  /**
   * La cause d'arrêt (#479, `CAUSE_*`) — portée par l'issue d'un run
   * (`execution.statut` terminal) et vide partout ailleurs. Optionnelle pour la
   * même raison que les champs ci-dessus : une trace relue d'un run antérieur au
   * lot n'en porte pas.
   */
  cause?: string;
  /**
   * Le **plan du run** (#490) — porté par le seul `run.plan`, et `null` partout
   * ailleurs. Optionnel pour la même raison que les champs ci-dessus : une trace
   * relue d'un run antérieur au lot n'en porte pas. Un client n'a pas à le
   * recomposer lui-même : `GET /api/executions/{run_id}/graphe` rend le graphe
   * déjà joint à l'état des tâches ; cet événement dit seulement *qu'il a
   * changé*.
   */
  plan?: NoeudPlan[] | null;
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
 * `run_id` (#570) est le run que la demande **retient** — vide quand elle ne
 * relève d'aucun run. Les deux voyagent désormais **sur la demande** et ne sont
 * plus déduits à l'arrivée : une demande qui garde le démarrage de sa propre
 * tâche est publiée avant que cette tâche n'existe, et se trouvait donc écartée
 * de toutes les vues (#568).
 *
 * `outil` et `arguments` (#581) portent l'**acte** qui a déclenché la demande —
 * l'outil appelé et ce qu'on lui passe, déjà expurgé côté backend. C'est ce que
 * l'écran d'arbitrage montre en tête quand ils sont là : depuis que le
 * déclencheur est l'acte (#573), le titre de la tâche n'est plus la question
 * qu'on pose — « Rédiger le README » au-dessus d'un `rm -rf` ferait trancher à
 * côté. `outil` vide et `arguments` à `null` pour une demande qui n'en porte pas
 * — validation de tâche (#48), application d'un diff (#227) —, et l'écran retombe
 * alors sur le titre, comme avant ce lot.
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
  run_id: string;
  outil: string;
  arguments: Record<string, string> | null;
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
 * …et le cas dégénéré (#271) : un serveur qui n'émet aucun secret (utilitaire
 * local, endpoint public). Ce n'est pas un quatrième parcours de saisie — c'est
 * l'absence de saisie, et le formulaire de configuration n'a donc aucun champ.
 */
export const MCP_MODE_SANS_SECRET = "sans_secret";

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
 * La source d'une entrée de bibliothèque : curée à la main (#677), découverte
 * dans le registre officiel (#677), ou **admise** — une découverte qu'un geste
 * humain tracé a fait entrer dans l'allowlist (#678).
 *
 * ⚠ À ne pas confondre avec `curee`, qui est le champ du garde-fou. La source
 * dit **d'où ça vient**, `curee` dit **si c'est montable** : une entrée `admise`
 * est `curee: true`. Pour savoir si on peut l'ajouter au pool, lire `curee` —
 * jamais la source.
 */
export type SourceRegistreMcp = "curee" | "decouverte" | "admise";

/**
 * Les statuts que le registre officiel déclare sur une entrée (#679), et que le
 * champ `statut` d'une entrée d'amont porte.
 *
 * ⚠ Il en existe un troisième, `deleted` (retiré par la modération amont), et
 * il n'a **pas** de constante ici parce qu'il ne peut pas arriver à l'écran : le
 * miroir l'exclut et la traduction le refuse (`maestro/agents/mcp_amont.py`,
 * `mcp_traduction.py`). Une entrée déjà **admise** que l'amont passe `deleted`
 * ne revient pas non plus sous ce statut — elle reste servie figée, avec un
 * `SignalAmontMcp` de genre `amont_supprimee`, qui est la forme sous laquelle
 * l'écran doit la traiter.
 */
export const MCP_STATUT_ACTIF = "active";
export const MCP_STATUT_DEPRECIE = "deprecated";

/**
 * La trace d'une admission (#678) telle qu'elle voyage **sur une entrée** :
 * qui, quand, depuis quelle source amont, et si elle vaut encore.
 *
 * Elle ne porte pas l'entrée qu'elle a figée — c'est celle qui la porte. La
 * forme du journal (`GET /api/mcp/admissions`) l'emboîte dans l'autre sens
 * (`AdmissionMcp`).
 */
export type TraceAdmissionMcp = {
  id: string;
  /** Le nom amont complet (`io.github.alice/serveur`) — la clé stable côté registre. */
  nom_amont: string;
  /** La version **épinglée** au moment de l'admission : c'est elle qu'on monte. */
  version: string;
  editeur: string;
  depot: string;
  /** Le registre moissonné dont l'entrée vient. */
  amont: string;
  /** L'horodatage du miroir au moment de l'admission — l'âge de la matière admise. */
  miroir_le: string;
  par: string;
  le: string;
  note: string;
  /** Faux dès qu'elle est révoquée : c'est ce qui décide du montage. */
  active: boolean;
  revoquee_par: string;
  revoquee_le: string;
  /** Le motif de la **révocation** (vide tant qu'elle est active). */
  motif: string;
};

/** Une admission telle que le journal la rend (#678) : sa trace **et** l'entrée figée. */
export type AdmissionMcp = TraceAdmissionMcp & {
  entree: EntreeRegistreMcp;
};

/**
 * Ce que l'amont dit **aujourd'hui** d'une entrée admise **hier** (#678).
 *
 * Calculé à chaque composition de la bibliothèque, jamais persisté. Aucun de ces
 * signaux ne retire quoi que ce soit : l'entrée reste montable telle qu'elle a
 * été admise, le signal est là pour qu'un humain en décide — « jamais retirée en
 * silence » ne veut pas dire « retirée d'office ».
 */
export type SignalAmontMcp = {
  id: string;
  genre:
    | "amont_depreciee"
    | "amont_supprimee"
    | "amont_disparue"
    | "version_nouvelle";
  message: string;
  /** La version que l'amont sert aujourd'hui (vide si l'entrée a disparu). */
  version_amont: string;
  statut_amont: string;
};

/**
 * Une entrée de la **bibliothèque** de serveurs MCP (`GET /api/mcp/registre`,
 * #131) : un template recherchable portant transport, gabarit `${VAR}`, mode
 * d'auth (docs/21), variables à fournir (`secrets`) et lien de procédure côté
 * outil.
 *
 * La bibliothèque a **trois sources** depuis #678, et deux champs qui ne
 * répondent pas à la même question : `curee` dit si l'entrée appartient à
 * l'allowlist — donc si elle est instanciable (garde-fou supply-chain,
 * docs/19) —, `source` d'où elle vient. Une entrée **découverte** vient du
 * miroir du registre MCP officiel : elle se lit et se cherche, elle ne se monte
 * pas, et elle porte à la place les signaux de confiance de l'amont (`editeur`,
 * `version`, `depot`, `statut`). Une entrée **admise** est la même chose, plus
 * un geste humain tracé qui l'a fait entrer dans l'allowlist : elle est donc
 * `curee: true` tout en venant de l'amont, et porte son `admission`.
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
  /** Qui publie ce serveur (#271) — une intégration se choisit aussi sur son éditeur. */
  editeur: string;
  /** Palier d'usage (#271) : plus grand = plus courant. Clé du tri après la source. */
  popularite: number;
  /** Dans l'allowlist — donc instanciable (#677). Vrai pour une curée **et** une admise (#678). */
  curee: boolean;
  /** D'où elle vient — **pas** la même question que `curee` (#677, #678). */
  source: SourceRegistreMcp;
  /** Version épinglée déclarée par l'amont — vide sur une entrée curée (#677). */
  version: string;
  /** URL du dépôt déclarée par l'amont — vide sur une entrée curée (#677). */
  depot: string;
  /** Statut amont (`active`/`deprecated`) — vide sur une entrée curée (#677). */
  statut: string;
  /**
   * Date de publication déclarée par l'amont (ISO 8601) — vide sur une curée
   * (#679). Le seul des signaux d'amont à répondre à « depuis quand ça
   * existe ? » : une version épinglée dit *quoi*, pas *depuis quand*.
   */
  publie_le: string;
  /** Le geste qui l'a fait entrer dans l'allowlist — null sauf si `source === "admise"` (#678). */
  admission: TraceAdmissionMcp | null;
  /** Ce que l'amont dit d'elle depuis son admission — vide s'il n'a rien à dire (#678). */
  signaux: SignalAmontMcp[];
};

/** Une source citée par la curation (#271) : d'où vient une entrée, où la revérifier. */
export type SourceCitee = {
  libelle: string;
  url: string;
};

/**
 * La provenance de la source **curée** : d'où vient la liste, quand elle a été
 * revue, et combien d'entrées elle sert (#271, #677).
 */
export type ProvenanceCuree = {
  source: "curee";
  resume: string;
  sources: SourceCitee[];
  revue_le: string;
  total: number;
};

/**
 * La provenance de la source **découverte** (#677) : quel registre a été
 * moissonné, quand, et ce qu'il en reste. Elle ne répond pas à la même question
 * que la curée — une liste écrite à la main se date par sa revue humaine, un
 * miroir par son rafraîchissement.
 *
 * `nombre` est ce que le miroir porte, `total` ce que la bibliothèque en sert :
 * l'écart (entrées non traduisibles, collisions avec le seed) est à montrer, pas
 * à masquer. `moissonnee` est faux tant qu'aucune entrée n'en est sortie —
 * c'est-à-dire l'état normal d'un poste qui n'a pas encore moissonné.
 */
export type ProvenanceDecouverte = {
  source: "decouverte";
  amont: string;
  rafraichi_le: string;
  moissonne_le: string;
  nombre: number;
  retenues: number;
  moissonnee: boolean;
  cause: string;
  echoue_le: string;
  total: number;
};

/**
 * La provenance de la source **admise** (#678) : ce qu'un humain a fait entrer
 * dans l'allowlist depuis le registre officiel.
 *
 * Elle ne se date ni par une revue de code (la curée) ni par un
 * rafraîchissement de miroir (la découverte) mais par le **geste** :
 * `derniere_le` est la date de la plus récente admission. `revoquees` compte ce
 * qui a été retiré — gardé au journal et non effacé —, `signaux` ce que l'amont
 * a dit depuis de ce qui reste.
 */
export type ProvenanceAdmise = {
  source: "admise";
  resume: string;
  total: number;
  revoquees: number;
  derniere_le: string;
  signaux: number;
};

/**
 * D'où vient la bibliothèque et quand elle a été revue
 * (`GET /api/mcp/registre/provenance`, #271). `tags` porte les pistes de
 * recherche — ce qu'on propose quand une recherche ne rend rien, plutôt que de
 * répéter qu'elle n'a rien trouvé.
 *
 * Les clés à plat décrivent la source **curée** et n'ont pas bougé de sens
 * (#677) ; `provenances` porte les **trois** sources côte à côte (#678), et
 * `total` couvre l'ensemble de ce que sert `GET /api/mcp/registre` sans filtre —
 * `total_curees`/`total_admises`/`total_decouvertes` le détaillent.
 *
 * ⚠ `total_curees` compte le **seed seul** depuis #678 : une entrée admise
 * bascule dans `total_admises` sans cesser d'être montable. C'est le seul
 * chiffre de ce contrat dont la portée a changé.
 */
export type ProvenanceRegistreMcp = {
  resume: string;
  sources: SourceCitee[];
  revue_le: string;
  tags: string[];
  total: number;
  total_curees: number;
  total_admises: number;
  total_decouvertes: number;
  provenances: [ProvenanceCuree, ProvenanceAdmise, ProvenanceDecouverte];
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
  /** D'où vient l'entrée qui l'autorise — null si elle n'est plus dans la bibliothèque (#678). */
  source: SourceRegistreMcp | null;
  /** Le geste qui l'autorise, ou celui qui l'a **révoquée** — null pour une curée (#678). */
  admission: TraceAdmissionMcp | null;
  /** Ce que l'amont dit de son entrée depuis l'admission (#678). */
  signaux: SignalAmontMcp[];
  /**
   * Pourquoi ce serveur est monté sans être dans l'allowlist — vide quand tout
   * va bien (#678). Une révocation ne démonte rien : elle laisse cette phrase,
   * qui nomme qui a révoqué, quand, et le geste pour retirer du pool.
   */
  alerte: string;
  secrets: EtatSecretPool[];
};

/** Le pool projet servi par `GET /api/mcp/pool` : ses intégrations + une cause d'erreur. */
export type PoolMcp = {
  integrations: IntegrationPoolMcp[];
  erreur: string | null;
};

/**
 * Le journal des admissions (`GET /api/mcp/admissions`, #678).
 *
 * Deux listes plutôt qu'une à filtrer, parce que ce ne sont pas les mêmes
 * lectures : `admissions` répond à « qu'est-ce qu'on autorise ? », `revoquees` à
 * « qu'a-t-on retiré, et pour quel motif ? ». `politique` nomme qui garde la
 * porte — `defaut: true` veut dire que le geste humain suffit, faux qu'une
 * politique d'entreprise a été branchée. `erreur` porte la cause d'un journal
 * illisible : ce cas-là retire de l'allowlist tout ce qu'il autorisait, il ne se
 * tait pas.
 */
export type JournalAdmissionsMcp = {
  admissions: AdmissionMcp[];
  revoquees: AdmissionMcp[];
  signaux: SignalAmontMcp[];
  politique: { nom: string; module: string; defaut: boolean };
  erreur: string | null;
};

/**
 * Ce qu'une révocation laisse debout (`DELETE /api/mcp/admissions/{id}`, #678).
 *
 * L'entrée sort de l'allowlist, **rien n'est démonté** : `pool.montee` dit si le
 * serveur reste dans le pool projet et `pool.agents` chez qui il est activé.
 * Casser un run en cours pour appliquer une décision d'allowlist serait un
 * remède pire que le mal — ce qui est promis est « jamais sans le dire ».
 */
export type RevocationAdmissionMcp = {
  admission: AdmissionMcp;
  pool: { montee: boolean; agents: string[]; erreur: string | null };
  message: string;
};

/**
 * La politique de permissions d'un agent (`PolitiqueOutils.to_dict`, #110) :
 * allow/ask/deny par outil — noms d'outils intégrés, `mcp__<serveur>` pour un
 * serveur MCP entier, `mcp__<serveur>__<outil>` pour un outil MCP précis.
 * Priorité `deny` > `ask` > `allow` (#580) ; `allow` vide = tout ce que le
 * profil expose est permis ; `ask` suspend l'appel le temps qu'une personne
 * tranche — l'outil n'est pas interdit, il est arbitré. Une politique écrite
 * avant #580 arrive avec `ask` vide, donc sous le régime d'hier.
 */
export type PolitiquePermissions = {
  allow: string[];
  ask: string[];
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
 * `effort` (#253) est le niveau d'effort demandé au modèle (null : aucun
 * réglage — le régime par défaut du fournisseur) ; ce que le fournisseur retenu
 * admet se lit sur `GET /api/fournisseurs`, jamais dans une liste écrite ici.
 */
export type AgentCatalogue = {
  nom: string;
  role: string;
  competences: string[];
  modele: string | null;
  fournisseur: string | null;
  effort: string | null;
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
 * Un fait mesuré sur le poste par la sonde (#487) : un CLI résolu sur le `PATH`,
 * un serveur de modèles local qui répond, une clé présente dans l'environnement.
 * N'existe que pour ce qui est **présent** — un poste nu rend zéro constat.
 * `utilisable` distingue le présent-et-prêt du présent-mais-empêché (un serveur
 * qui écoute sans répondre, un endpoint distant que la sonde ne joint pas), et
 * `incertitude` dit ce qu'elle ne peut pas savoir plutôt que de le deviner :
 * jamais la valeur d'une clé, jamais la version d'un binaire qu'il faudrait
 * lancer pour la lire.
 */
export type ConstatPoste = {
  /** « cli », « serveur_local » ou « cle ». */
  genre: string;
  cle: string;
  libelle: string;
  fournisseur: string | null;
  utilisable: boolean;
  detail: string;
  origine: string | null;
  modeles: string[];
  incertitude: string | null;
};

/**
 * Un fournisseur du catalogue (`GET /api/fournisseurs`, #253 + #487) : le
 * registre du code dit qu'il est **supporté** et annonce sa gamme, la sonde dit
 * s'il est **présent ici**. Les deux colonnes ne se confondent jamais — c'est ce
 * qui permet de proposer un fournisseur armé sur cette machine sans cacher celui
 * qui ne l'est pas encore, et c'est aussi pourquoi la fiche du registre est
 * **reprise telle quelle** (`Fournisseur`) au lieu d'être recopiée : `modeles`
 * est ce que Maestro annonce, `modeles_ici` ce que la sonde a vu.
 */
export type FournisseurCatalogue = Fournisseur & {
  supporte: boolean;
  present_ici: boolean;
  utilisable_ici: boolean;
  /** Les modèles que la sonde a **vus ici** (un serveur local les nomme). */
  modeles_ici: string[];
  constats: ConstatPoste[];
};

/**
 * Le catalogue complet. `hors_registre` porte ce que le poste a de plus que
 * Maestro — un agent CLI tiers non branché (docs/34) : montré, jamais proposé.
 * `incertitudes` porte ce qui pèse sur les **absences** (le `PATH` du process
 * qui sert l'API n'est pas celui de votre terminal).
 */
export type CatalogueFournisseurs = {
  fournisseurs: FournisseurCatalogue[];
  hors_registre: ConstatPoste[];
  incertitudes: string[];
};

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
  /**
   * Le niveau d'effort demandé au modèle (#253). **Facultatif** dans le corps —
   * l'API le met à null quand il est absent —, le temps que le formulaire le
   * propose ; les valeurs recevables se lisent sur `GET /api/fournisseurs`.
   */
  effort?: string | null;
};

/**
 * Une définition d'agent **proposée** (`POST /api/catalogue/generation`, #257).
 *
 * Ce n'est pas un agent : rien n'a été créé côté backend, et rien ne le sera
 * tant que l'utilisateur n'aura pas cliqué « Créer l'agent » — c'est le principe
 * des propositions de playbook (#111/#140) appliqué à la définition entière. Les
 * champs arrivent donc dans le formulaire comme un **brouillon**, modifiable,
 * régénérable, abandonnable.
 *
 * `nom` est une commodité (libre au moment de la réponse, pas réservé pour
 * autant) ; `fournisseur` et `modele` sont `null` quand le modèle n'a rien
 * proposé que le registre reconnaisse — un champ vide, que le formulaire lit
 * « réglages par défaut », jamais un nom inventé. `intention` est la phrase dont
 * la proposition est née : c'est ce que l'écran ré-affiche pour régénérer.
 */
export type DefinitionAgentProposee = {
  intention: string;
  nom: string;
  role: string;
  competences: string[];
  playbook: string;
  fournisseur: string | null;
  modele: string | null;
};

/**
 * Un modèle annoncé par un fournisseur (`GET /api/fournisseurs`, #253).
 *
 * `nom` est l'identifiant à écrire dans `DefinitionAgent.modele` (la chaîne
 * exacte qu'attend le fournisseur), `libelle` le nom lisible. `efforts` liste
 * les niveaux d'effort admis **sur ce modèle** : une liste vide dit que ce
 * modèle ne se règle pas en effort, pas qu'on l'ignore.
 */
export type ModeleFournisseur = {
  nom: string;
  libelle: string;
  efforts: string[];
};

/**
 * Un fournisseur de modèles disponible (`GET /api/fournisseurs`, #253).
 *
 * La source est le **registre des fournisseurs** du moteur : un fournisseur qui
 * s'y inscrit apparaît ici, donc à l'écran, sans qu'aucune liste ne soit écrite
 * côté front. `nom` est l'identifiant à écrire dans `DefinitionAgent.fournisseur`.
 *
 * `modeles` est la gamme annoncée et `modeles_libres` dit qu'un nom **hors**
 * gamme reste recevable — les deux se lisent ensemble : gamme vide *et* libre
 * (le cas d'`openai`, qui fédère des endpoints aux nommages hétéroclites) veut
 * dire « saisis le nom que sert ton endpoint », là où gamme vide et fermée
 * voudrait dire « rien à proposer ».
 *
 * C'est la **moitié registre** de `FournisseurCatalogue`, qui l'étend des
 * colonnes du poste (#487) : la route en rend une seule ligne par fournisseur,
 * ce type dit ce qu'elle en doit au code plutôt qu'à la machine.
 */
export type Fournisseur = {
  nom: string;
  modeles: ModeleFournisseur[];
  modeles_libres: boolean;
};

/**
 * Un message du fil de chat utilisateur ↔ agent (`MessageChat.to_dict`, #84) :
 * `agent` est le fil d'appartenance (le nom d'agent du catalogue), `auteur`
 * l'émetteur — `utilisateur` ou ce même nom d'agent.
 *
 * Depuis #482 un message peut porter des **sources** — la matière que
 * l'utilisateur y a déposée, telle que la chaîne d'ingestion l'a résolue — et le
 * **rapport de lecture** (#316) de cette matière. Les deux sont facultatifs : un
 * fil écrit avant ce lot n'en a pas, et un message de texte rend `[]` et `null`.
 *
 * Le Markdown extrait, lui, ne vient **pas** : il est fait pour un prompt et non
 * pour un écran (`Lecture.to_dict` applique déjà la même règle). Ce qui se lit
 * ici, c'est ce que Maestro a lu et ce que ça coûte — pas le contenu.
 *
 * `run_id`/`tache_id` (#268) rattachent au message **ce qui en découle** : le run
 * que l'orchestration a ouvert en répondant. Chaînes vides partout ailleurs — un
 * message ordinaire ne rattache rien. Ce que le message **embarque** et ce qu'il
 * **ouvre** sont deux questions distinctes, portées par le même objet.
 *
 * `conversation` (#694) est le fil d'appartenance **à l'intérieur** de l'agent,
 * comme `agent` l'est à l'intérieur du dépôt. L'API le sert toujours ; il est
 * facultatif ici parce que les fils simulés des tests n'ont pas à le porter pour
 * que le composant les rende.
 */
export type MessageChat = {
  agent: string;
  auteur: string;
  contenu: string;
  horodatage: string;
  run_id: string;
  tache_id: string;
  /** La conversation d'appartenance (#694) — `origine` pour celle d'un agent par défaut. */
  conversation?: string;
  /** La matière résolue que le message embarque (#482) — absente ou vide : aucune. */
  sources?: SourceResolue[];
  /** Le rapport de lecture de cette matière (#316) — `null` quand il n'y en a pas. */
  rapport?: RapportLecture | null;
};

/**
 * Une conversation d'un fil (`Conversation.to_dict`, #694) — sa carte, pas son
 * contenu : de quoi peupler un historique sans charger un seul message.
 *
 * `titre` est **dérivé** du premier message et vaut `""` tant que rien n'a été
 * dit — c'est l'écran qui décide comment appeler un fil vierge. `derniere` est
 * la dernière activité, celle qui ordonne : la première conversation servie est
 * donc celle qu'un envoi sans précision rejoindrait.
 *
 * Nommée `ConversationChat` et non `Conversation` — comme `FilChat`,
 * `MessageChat` et `FragmentChat` — parce qu'un composant du dépôt porte déjà
 * ce nom-là (`components/Conversation.tsx`) : l'écran qui affichera l'historique
 * importera les deux.
 */
export type ConversationChat = {
  agent: string;
  id: string;
  titre: string;
  debut: string;
  derniere: string;
  messages: number;
};

/** Les conversations d'un fil (`GET /api/chat/{agent}/conversations`, #694). */
export type ConversationsChat = {
  agent: string;
  role: string;
  /** La plus récente d'abord ; jamais vide — un agent a toujours son `origine`. */
  conversations: ConversationChat[];
};

/** Le fil complet d'un agent (`GET /api/chat/{agent}`, #84). */
export type FilChat = {
  agent: string;
  role: string;
  /** La conversation servie (#694) — celle demandée, sinon la plus récente. */
  conversation?: string;
  messages: MessageChat[];
};

/** La conversation qu'un agent a par défaut (maestro/controltower/chat.py, #694). */
export const CHAT_CONVERSATION_ORIGINE = "origine";

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
/**
 * Le **graphe du run** (#490), publié une fois : à l'instant où la décomposition
 * rend son plan. `plan` porte les nœuds et leurs dépendances, `run_id` le run
 * visé (jamais `tache_id` : l'événement porte sur le run entier). Il ne dit rien
 * de l'état — ni agent, ni statut, ni coût —, aucun de ces faits n'existant
 * encore ; c'est `GET /api/executions/{run_id}/graphe` qui joint les deux.
 *
 * Il **double** l'`agent.activite` de la planification, il ne le remplace pas :
 * ce que le cadrage a coûté et ce qu'il a décidé sont deux faits.
 */
export const EVENEMENT_RUN_PLAN = "run.plan";

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
 * Le run s'est arrêté sur un **arbitrage de tâche** (#571) : une action sensible
 * attend qu'un humain l'approuve ou la refuse (#48). Non terminal comme les deux
 * autres, et posé par la projection sur la demande elle-même — pas déduit d'un
 * appariement validation → tâche → run, qui arrive trop tard (`lib/execution`).
 *
 * Troisième exemplaire d'un motif qui existait déjà deux fois, et c'est tout le
 * ticket : sans lui, un run bloqué et un run qui travaille rendaient la même
 * réponse — `en_cours`, 0 tâche, coût figé, cœur battant (#568).
 */
export const EXECUTION_EN_ATTENTE_ARBITRAGE = "en_attente_arbitrage";

/**
 * Les deux **ordres de pause** d'un run (#477), tels qu'ils voyagent dans
 * `execution.statut` sur le flux — le canal de l'annulation, et pas un second.
 *
 * ⚠ Ce ne sont **pas** des statuts, et c'est la décision du lot : une pause ne dit
 * pas où en est le run, elle dit qu'on a cessé de lui donner du travail. Les deux
 * faits coexistent — un run peut être suspendu *et* en attente de son brief —, d'où
 * un drapeau à côté du statut (`ResumeExecution.en_pause`) plutôt qu'une valeur de
 * plus dedans. Ils n'apparaissent donc jamais dans `statut` : les lire ici sert au
 * seul fil d'activité, qui rend l'ordre en toutes lettres.
 */
export const ORDRE_PAUSE = "pause";
export const ORDRE_REPRISE = "reprise";

/**
 * **Pourquoi** un run s'est arrêté (#479, `maestro/controltower/causes.py`).
 *
 * Cinq codes, et pas un de plus : le moteur les *connaissait* déjà — un plafond
 * de tours lève `TurnLimitReached`, un plafond de dépense `PlafondDepenseDepasse`,
 * un hôte qui ne part pas `DemarrageHoteRate` — mais rien ne les acheminait, si
 * bien que la liste des runs disait « Échec » à des pannes qui ne se réparent pas
 * de la même façon.
 *
 * Un run que le backend n'a pas su classer porte la **chaîne vide** plutôt qu'un
 * code fourre-tout de plus : « je n'ai pas su ranger ceci » n'est pas un
 * diagnostic, et son `detail` reste lisible au fil d'activité.
 *
 * Le sixième est arrivé avec #486, et c'est le seul dont l'UI tire une
 * **conséquence** plutôt qu'une phrase : `extinction` dit que Maestro s'est éteint
 * en emportant le run, donc que celui-ci est **reprenable** au redémarrage
 * (`lib/execution`). Un run délibérément annulé ne l'est pas — les confondre
 * ferait reproposer un run que quelqu'un venait d'arrêter.
 */
export const CAUSE_PLAFOND_TOURS = "plafond_tours";
export const CAUSE_PLAFOND_COUT = "plafond_cout";
export const CAUSE_LIMITE_USAGE = "limite_usage";
export const CAUSE_HOTE_NON_DEMARRE = "hote_non_demarre";
export const CAUSE_ANNULATION = "annulation";
export const CAUSE_EXTINCTION = "extinction";

/**
 * Le statut d'une étape d'**activité** (#479) : ce que l'agent fait pendant que
 * sa tâche dure. Il ne dit rien de l'état de la tâche — il est là pour que le fil
 * rende la salve elle-même (`phraseEtapeAgent`) plutôt que de redire « en cours »,
 * que la carte du Kanban montre déjà.
 */
export const STATUT_ACTIVITE = "activite";

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
 * lancé depuis la Control Tower vit dans son propre process (#446) : il survit à
 * l'arrêt de l'API, mais pas au sommeil de sa machine — et le journal durable
 * conservant le dernier état publié, un run dont l'hôte est tombé restait
 * `en_cours` **pour toujours**. L'hôte publie donc un battement périodique, et ces
 * trois verdicts en découlent.
 *
 * `orphelin` s'est resserré avec #446 : un hôte publie désormais son **issue** en
 * partant, donc ce verdict ne désigne plus un run terminé dont personne n'a écrit
 * la fin, mais un run mort **sans avoir pu le dire**. C'est exactement celui que
 * le panneau *Runs qui n'avancent plus* propose de relancer (#349).
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
 * Une source **résolue** (`Source.to_dict`, #315) : ce qu'une déclaration est
 * devenue une fois confrontée au disque, au réseau et aux plafonds. C'est la
 * forme que rend le backend — un message du fil (#482) la porte telle quelle.
 *
 * À ne pas confondre avec `SourceDeclaree`, qui est ce que l'écran **envoie** :
 * l'une déclare une intention, l'autre constate un résultat. `chemin` est
 * l'emplacement d'ingestion d'un fichier ou le dossier canonicalisé ; `valeur`
 * l'adresse d'une URL ; `taille` les octets d'un fichier, `null` sinon.
 */
export type SourceResolue = {
  type: string;
  nom: string;
  chemin: string;
  valeur: string;
  taille: number | null;
  lecture_seule: boolean;
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
 * Où en est un run : ses tâches réparties par statut (#473), **comptées par le
 * backend** sur la machine à états du moteur (docs/03 §3) et jamais recomptées
 * ici — le front ne voit que les tâches qu'il a chargées, ce qui ferait d'une
 * barre de progression une mesure de sa propre pagination.
 *
 * Les cinq compartiments sont ceux du Kanban, plus `autres`, qui ramasse un
 * statut que la table du backend ne connaît pas (`maestro/controltower/
 * progression.py`) : sans lui, `total` cesserait d'égaler `nb_taches` sans que
 * rien ne le montre.
 *
 * `total` vaut le `nb_taches` du run et `soldees` compte ce qui ne bougera plus
 * (terminées + échecs + bloquées) : une barre se dessine par `soldees / total`,
 * sans avoir à savoir lesquels des compartiments sont terminaux.
 */
export type Progression = {
  a_faire: number;
  en_cours: number;
  bloquees: number;
  terminees: number;
  echecs: number;
  autres: number;
  soldees: number;
  total: number;
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
  /**
   * La progression du run par statut de tâche (#473) — de quoi dresser une liste
   * de runs utile sans un appel par ligne. Les tâches qu'elle compte sont
   * exactement celles que rend `GET /api/taches?projet=…&run=<run_id>`.
   */
  progression?: Progression;
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
   * Ce run **attend-il quelqu'un depuis trop longtemps** (#737, `docs/05 §2.6`) ?
   *
   * Le **second** verdict de veille, et il ne répond pas de la même question que
   * le précédent : `vitalite` dit si l'**hôte** du run est encore là, celui-ci si
   * le **run avance**. Un run suspendu sur un humain depuis une heure est
   * `vivant` *et* en souffrance — c'est la paire exacte qu'a portée le run de
   * #568 pendant que rien ne le signalait.
   *
   * Booléen et non ternaire : le troisième état — « il attend, mais pas depuis
   * trop longtemps » — est déjà porté par le `statut`, et le reporter ici serait
   * un second support pour un même fait. Un run soldé rend donc `false` comme un
   * run au travail, si long soit-il : ce verdict juge l'**attente**, jamais la
   * durée.
   *
   * Il est **dérivé de `statut` + `attente_depuis`, jamais stocké** : il se
   * recalcule à chaque lecture et survit à un redémarrage de l'API sans que rien
   * ne soit persisté. Le seuil et ses écarts (horodatage illisible → `true`,
   * l'inverse de `vitalite`) vivent dans `maestro/controltower/souffrance.py` ;
   * l'écran ne les redéduit jamais — `estEnSouffrance` (`lib/execution`) ne fait
   * que lire ce champ. Absent des flux antérieurs au lot, d'où l'optionnel.
   */
  en_souffrance?: boolean;
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
  /**
   * Le run est-il **suspendu** (#477) ? Aucune tâche nouvelle n'est lancée, celles
   * qui étaient en vol sont allées à leur terme. Un drapeau **en plus** du statut,
   * qui ne bouge pas : un run suspendu reste `en_cours`, ou `en_attente_brief`,
   * ou ce qu'il était — c'est ce qu'on regarde pour décider de le reprendre.
   * Absent des flux antérieurs au lot, d'où l'optionnel.
   */
  en_pause?: boolean;
  /**
   * **Pourquoi** ce run s'est arrêté (#479, `CAUSE_*`) — chaîne vide tant qu'il
   * n'y a rien à dire, ce qui est le cas d'un run en cours comme d'un run qui a
   * fini normalement. Dans le **résumé** et non dans le seul détail, parce que
   * c'est la liste qui doit distinguer un run à court de budget d'un run dont
   * l'hôte n'a jamais démarré : les deux affichent « Échec » et ne se réparent
   * pas du tout de la même façon.
   *
   * Elle vient **en plus** du `detail` de l'événement d'issue, jamais à sa
   * place : le code dit de quoi il s'agit, le détail ce qui s'est passé (quelle
   * borne, quel montant). Absente des flux antérieurs au lot, d'où l'optionnel.
   */
  cause?: string;
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

/**
 * Un nœud du **plan** tel qu'il voyage sur `run.plan` (#490,
 * `maestro/plan_run.py`) : ce que la décomposition a écrit, et rien de plus —
 * ni agent, ni statut, ni coût, aucun de ces faits n'existant au moment du plan.
 * `etapes` est l'**ossature** de la checklist (#489) : des libellés seuls, jamais
 * un état.
 */
export type NoeudPlan = {
  id: string;
  titre: string;
  dependances: string[];
  etapes: string[];
};

/**
 * L'état d'une arête, c'est-à-dire du passage de relais entre deux tâches
 * (#490) : `attendue` tant que l'amont n'a pas rendu son issue, `franchie`
 * quand il a terminé (la main passe), `rompue` quand il a échoué ou a été
 * bloqué — l'aval ne démarrera pas et se bloquera à son tour (#43).
 */
export const ARETE_ATTENDUE = "attendue";
export const ARETE_FRANCHIE = "franchie";
export const ARETE_ROMPUE = "rompue";

/**
 * Un nœud du graphe d'un run (#490) : la tâche du plan **jointe** à là où elle en
 * est. `niveau` est son rang topologique — le plus long chemin qui y mène — et
 * `rang` sa position dans ce niveau : de quoi poser la boîte sans recalculer un
 * tri topologique en TypeScript. `compartiment` est la couleur, lue dans la table
 * partagée de la progression (#473) et non dans une correspondance réinventée
 * ici. `dependances` sont les arêtes entrantes, `dependants` les sortantes.
 *
 * Un nœud qui n'a pas démarré porte le statut `backlog` et une checklist tirée de
 * l'ossature du plan : c'est ce qui rend une tâche lisible **avant** qu'elle
 * tourne, et sur un graphe c'est la moitié des boîtes.
 */
export type NoeudGraphe = {
  id: string;
  titre: string;
  dependances: string[];
  dependants: string[];
  niveau: number;
  rang: number;
  statut: string;
  compartiment: string;
  agent: string;
  role: string;
  cout_usd: number | null;
  duree_ms: number | null;
  etapes: EtapeTache[];
};

/** Une arête du graphe (#490) : `de` l'amont, `vers` l'aval — le sens du flux. */
export type AreteGraphe = {
  de: string;
  vers: string;
  etat: string;
};

/**
 * Le graphe d'un run, servi par `GET /api/executions/{run_id}/graphe` (#490) :
 * la lecture qui dit **quoi après quoi**, là où le Kanban dit « combien dans
 * quel état » et la progression « où en est-on ».
 *
 * `niveaux` range les identifiants par rang topologique : deux tâches sans
 * dépendance entre elles y tombent au **même** niveau, donc se lisent comme
 * parallèles au lieu de paraître séquentielles. `largeur` est le niveau le plus
 * peuplé — la parallélisation que le plan **autorise**, jamais celle qu'il
 * obtiendra (le `parallelisme` du moteur peut être plus étroit).
 *
 * Deux booléens qu'il ne faut pas confondre. `plat` : aucune arête — le cas
 * courant d'un plan dont les tâches sont indépendantes, et un graphe normal, pas
 * un vide. `plan_connu` : le run a-t-il publié son plan ? À `false`, les nœuds
 * sont reconstruits de ses seules tâches vues et il n'y a **aucune** arête faute
 * de les connaître ; le dessin est le même, ce qu'on a le droit d'en conclure ne
 * l'est pas.
 *
 * ⚠ `nb_noeuds` ne vaut pas `nb_taches` (donc pas `progression.total`) : le plan
 * annonce ce qui **sera** fait, le run compte ce qu'il a **porté**. Les deux se
 * rejoignent à la fin d'un run qui va au bout.
 */
export type GrapheRun = {
  run_id: string;
  plan_connu: boolean;
  plat: boolean;
  nb_noeuds: number;
  nb_aretes: number;
  profondeur: number;
  largeur: number;
  noeuds: NoeudGraphe[];
  aretes: AreteGraphe[];
  niveaux: string[][];
};

/**
 * Le statut que la frise résout pour une **attente de validation humaine**
 * (#355) — celui de la machine à états de docs/03 §3, que
 * `maestro/controltower/progression.py` nomme depuis #473 et que le moteur
 * n'émet pas : c'est la frise qui le produit, depuis `validation.demande`.
 *
 * Il est nommé ici parce que c'est **le** statut du troisième critère : une
 * tâche en attente d'un humain doit se distinguer d'une tâche en cours, à
 * l'œil. Le comparer à une chaîne écrite sur place le ferait diverger en
 * silence le jour où le backend changera de mot.
 */
export const STATUT_EN_ATTENTE_VALIDATION = "en_attente_validation";

/**
 * La clé du **couloir de repli** d'une frise (#355) : ce qui n'a pas d'agent
 * résoluble. Une chaîne vide, comme côté serveur — et il y a plus dedans qu'on
 * ne croit : le moteur consigne `agent="—"` sur une tâche **jamais routée**
 * (`_consigne_blocage`, #43), que le backend range ici plutôt que d'ouvrir un
 * couloir nommé « — ».
 */
export const COULOIR_REPLI = "";

/**
 * Une entrée de la frise d'activité d'un run (#355) : un fait daté, attribué à
 * un couloir.
 *
 * `id` est celui du journal requêtable (`j-0007`) : rien n'est créé, et les deux
 * lectures se recoupent entrée par entrée. `type` reste le **type d'événement
 * d'origine** — c'est lui qui sépare les deux flux que la frise mêle
 * (`tache.statut` et `message.inter_agents`, plus les deux temps d'une
 * validation).
 *
 * `statut` est le statut de tâche **résolu** par le backend, vide pour un
 * message : c'est la seule valeur que la frise calcule, et celle qui distingue
 * une tâche bloquée d'une tâche qui attend un humain et d'une tâche en cours.
 *
 * `couloir` est **toujours** l'un des couloirs servis : c'est l'invariant du
 * deuxième critère (aucune entrée perdue faute de couloir), tenu côté serveur.
 * `objet` est ce que l'entrée dit — le détail du journal, ou le titre de la
 * tâche quand une issue réussie ne dit rien d'autre.
 */
export type EntreeFrise = {
  id: string;
  type: string;
  couloir: string;
  agent: string;
  role: string;
  tache_id: string;
  titre: string;
  statut: string;
  objet: string;
  horodatage: string;
};

/**
 * Un couloir de la frise (#355) : un agent, et les **identifiants** des entrées
 * qui lui reviennent — les entrées elles-mêmes vivent une fois, dans `entrees`.
 *
 * Un couloir vide est légitime : un agent du run qui n'a encore rien dit se lit
 * comme tel, là où l'omettre le ferait apparaître en cours de route sans qu'on
 * sache s'il était prévu. `repli` marque le couloir de ce qui n'a pas d'agent —
 * il ferme toujours la liste, et n'existe que s'il a recueilli quelque chose.
 */
export type CouloirFrise = {
  agent: string;
  role: string;
  repli: boolean;
  entrees: string[];
};

/**
 * La frise d'activité d'un run, servie par
 * `GET /api/executions/{run_id}/frise` (#355) : la lecture qui dit **dans quel
 * ordre**, là où le pipeline dit « quoi après quoi », le Kanban « combien dans
 * quel état » et le journal « qu'a-t-il fait ».
 *
 * `entrees` est la chronologie, triée par le backend (instant, puis rang du
 * journal — les horodatages sont à la seconde, donc deux entrées d'un run
 * parallèle en partagent couramment un). `couloirs` la range par agent sans
 * qu'aucun regroupement ne soit à refaire ici.
 *
 * `total` compte **avant** le plafond et `tronquee` dit s'il a mordu : la frise
 * retient les entrées les plus récentes, et une borne muette ferait passer un
 * run d'une heure pour un run de cinq cents lignes.
 */
export type FriseRun = {
  run_id: string;
  entrees: EntreeFrise[];
  couloirs: CouloirFrise[];
  total: number;
  plafond: number;
  tronquee: boolean;
};

/** Clés de tri et sens du journal requêtable (maestro/controltower/journal.py). */
export const TRI_JOURNAL_HORODATAGE = "horodatage";
export const TRI_JOURNAL_AGENT = "agent";
export const TRI_JOURNAL_TYPE = "type";
export const ORDRE_ASC = "asc";
export const ORDRE_DESC = "desc";

/** Plafond dur d'une page de journal (au-delà, le backend refuse en 422). */
export const TAILLE_PAGE_JOURNAL_MAX = 200;

/**
 * Une entrée du journal requêtable (`GET /api/journal`) : un événement persisté
 * doté d'un `id` stable (référençable, triable) — le reste reprend la forme d'un
 * `Evenement` (type, run, tâche, titre, agent, statut, détail, description,
 * projet, horodatage).
 * Le journal se restreint à un projet par `?projet=<id>` (#222) ; une entrée
 * sans `projet_id` ne relève d'aucun et sort donc de toute vue filtrée.
 *
 * `titre` et `description` s'y sont ajoutés en #478, quand la route a cessé
 * d'être une promesse : ce sont eux que la ligne d'activité **prononce**
 * (`resumeEvenement`), et sans eux un fil relu après rechargement dirait « dev a
 * terminé : une étape » là où le direct disait le nom de l'étape. Les charges
 * lourdes d'un `Evenement` (`usage`, `brief`, `sources`, `diff`) restent dehors :
 * aucune ligne ne les montre, et une page en compte jusqu'à 200.
 */
export type EntreeJournal = {
  id: string;
  type: string;
  run_id: string;
  tache_id: string;
  titre: string;
  agent: string;
  role: string;
  statut: string;
  detail: string;
  description: string;
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

/** Types de trame d'un flux SSE de chat (maestro/controltower/chat.py). */
export const FRAGMENT_CHAT_DEBUT = "debut";
export const FRAGMENT_CHAT_DELTA = "fragment";
export const FRAGMENT_CHAT_FIN = "fin";
export const FRAGMENT_CHAT_INTERROMPU = "interrompu";
export const FRAGMENT_CHAT_ERREUR = "erreur";

/**
 * Une trame du flux SSE d'un fil de chat (`POST /api/chat/{agent}/flux`, #183) :
 * chaque `data: <json>` du `text/event-stream` en est une. `type` dit son rôle
 * (`debut` ouvre, `fragment` incrémente, `fin` clôt sur la réponse entière,
 * `interrompu` clôt sur ce qui a été persisté d'une réponse **arrêtée** (#695),
 * `erreur` signale qu'aucune réponse ne viendra) ; `auteur` est l'émetteur
 * (l'agent) ; `delta` porte l'incrément de texte — vide hors `fragment`, où il
 * porte la cause sur `erreur`.
 *
 * `message` porte un `MessageChat` complet sur les trames qui **bornent**
 * l'échange : celui de l'utilisateur sur `debut` — avec ses sources et leur
 * rapport de lecture (#692/#316) —, la réponse sur `fin`, et ce qui a été
 * persisté de la réponse arrêtée sur `interrompu` (`null` quand l'arrêt précède
 * le premier incrément). `null` sur `fragment` et `erreur`.
 *
 * `echange` (#695) nomme le flux lui-même et voyage sur **toutes** les trames :
 * c'est lui qu'on rend à `POST …/flux/{echange}/arret` pour arrêter la
 * génération.
 *
 * `conversation` (#694) dit **où** la réponse s'écrit, et voyage elle aussi sur
 * toutes les trames — `debut` comprise : un fil affiché sait dès la première si
 * ce qui arrive est le sien, sans attendre le `MessageChat` de la trame `fin`.
 */
export type FragmentChat = {
  type: string;
  agent: string;
  auteur: string;
  delta: string;
  message: MessageChat | null;
  echange: string;
  conversation?: string;
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

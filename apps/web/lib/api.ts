/**
 * Client REST du backend Control Tower (maestro/controltower/app.py).
 *
 * L'URL de l'API vient de `NEXT_PUBLIC_MAESTRO_API_URL` (inlinée au build par
 * Next.js) et retombe sur l'écoute locale par défaut du backend
 * (`maestro-api`, 127.0.0.1:8000). Le WebSocket dérive de la même URL.
 */

import type {
  AgentCatalogue,
  AgentCatalogueDetail,
  AnalyticsCouts,
  CoutExecution,
  ChoixSelecteur,
  DecisionBrief,
  DeclarationProjet,
  DefinitionAgent,
  DetailExecution,
  DisponibiliteSelecteur,
  EntreeRegistreMcp,
  EtatAgent,
  FilChat,
  FriseRun,
  GrapheRun,
  IntegrationPoolMcp,
  LancementExecution,
  PageExplorateur,
  PageJournal,
  PasSerie,
  PlaybookDetail,
  PlaybookFiche,
  PoolMcp,
  Projet,
  PropositionPlaybook,
  PropositionPlaybookDetail,
  ProvenanceRegistreMcp,
  RapportLecture,
  RefusProjet,
  ReponsesBrief,
  ResumeExecution,
  Sante,
  SourceDeclaree,
  SourceRegistreMcp,
  Tache,
  TeleversementSources,
  Validation,
  VersionPlaybook,
  VersionPlaybookDetail,
} from "./types";

const API_URL = (
  process.env.NEXT_PUBLIC_MAESTRO_API_URL ?? "http://localhost:8000"
).replace(/\/+$/, "");

/** L'URL du backend visé — celle qu'affiche la page Paramètres (#117). */
export function urlApi(): string {
  return API_URL;
}

/**
 * La **portée projet** d'une lecture (#277) : l'identifiant d'un projet, ou l'un
 * des deux mots réservés. Le backend l'exige sur toutes les vues qui agrègent —
 * Kanban, exécutions, coûts, validations, journal, flux temps réel — et refuse
 * une lecture qui n'en porte pas (`projet-requis`) : « rien plutôt qu'un
 * mélange ».
 *
 * Depuis #281 la portée n'a **pas de défaut** ici : elle est un paramètre exigé
 * de chaque appel, et c'est le compilateur qui tient la promesse « aucun écran
 * ne montre autre chose que le projet actif ». Un défaut à `tous` — ce qu'il y
 * avait tant que le shell ne portait pas le projet — rendrait la vue transverse
 * à qui aurait simplement oublié de cadrer sa lecture, c'est-à-dire exactement
 * la fuite que ce lot ferme. Le shell passe l'identifiant du projet actif
 * (`components/Shell` → `lib/etatGlobal`) ; `tous` et `aucun` restent
 * disponibles pour une vue qui les demande explicitement.
 */
export type PorteeProjet = string;
export const PORTEE_TOUS = "tous";
export const PORTEE_AUCUN = "aucun";

/** L'URL du flux d'événements temps réel (`WS /ws/evenements`), à la portée demandée. */
export function urlEvenements(portee: PorteeProjet): string {
  return (
    API_URL.replace(/^http/, "ws") +
    `/ws/evenements?projet=${encodeURIComponent(portee)}`
  );
}

async function chargerJson<T>(chemin: string): Promise<T> {
  const reponse = await fetch(`${API_URL}${chemin}`, { cache: "no-store" });
  if (!reponse.ok) {
    throw new Error(`${chemin} a répondu ${reponse.status}`);
  }
  return (await reponse.json()) as T;
}

/**
 * La sonde de vitalité du backend (`GET /api/sante`) : ce que teste le bouton
 * « Tester la connexion » des Paramètres (#121). Le WebSocket dit si le flux
 * temps réel est ouvert ; ceci dit si le REST répond, ce qui n'est pas la même
 * panne (backend éteint, mauvaise URL, CORS).
 */
export function chargerSante(): Promise<Sante> {
  return chargerJson<Sante>("/api/sante");
}

/**
 * Les tâches connues du backend, à la portée demandée — la source du Kanban.
 *
 * `runId` (#473) **s'ajoute** à la portée projet sans la remplacer : passé, il
 * restreint aux tâches que ce run a portées — le Kanban **d'un run** —, et la
 * portée projet reste requise, un run appartenant à un projet. Omis, la lecture
 * est celle d'avant : toutes les tâches de la portée.
 */
export function chargerTaches(
  portee: PorteeProjet,
  runId?: string,
): Promise<Tache[]> {
  const run = runId ? `&run=${encodeURIComponent(runId)}` : "";
  return chargerJson<Tache[]>(
    `/api/taches?projet=${encodeURIComponent(portee)}${run}`,
  );
}

/**
 * L'état des agents (libre/occupé, tâche courante, compteurs, coût cumulé).
 *
 * **Sans portée projet, et c'est une décision** (#281, docs/05 §2.3) : un agent
 * est une ressource du **poste** — son playbook, sa capacité et ses instances
 * (#86) valent pour toute la Control Tower —, il n'appartient à aucun projet et
 * le backend ne le filtre pas (#277 ne porte pas `?projet=` sur cette route).
 * Ce qui doit être cadré, c'est ce qu'un écran en **dit** : voir
 * `IndicateursTableauDeBord`, dont la tuile compte les agents au travail **sur
 * le projet actif** et nomme le parc comme partagé.
 */
export function chargerAgents(): Promise<EtatAgent[]> {
  return chargerJson<EtatAgent[]>("/api/agents");
}

/** Les demandes de validation humaine (#48) : contexte, statut, décision. */
export function chargerValidations(
  portee: PorteeProjet,
): Promise<Validation[]> {
  return chargerJson<Validation[]>(
    `/api/validations?projet=${encodeURIComponent(portee)}`,
  );
}

/** Le grand livre d'une exécution (#57) : coût par tâche et agrégat du run. */
export function chargerCoutExecution(runId: string): Promise<CoutExecution> {
  return chargerJson<CoutExecution>(
    `/api/executions/${encodeURIComponent(runId)}/cout`,
  );
}

/**
 * Le graphe d'une exécution (`GET /api/executions/{run_id}/graphe`, #490) : un
 * nœud par tâche du plan, une arête par dépendance, les nœuds rangés par
 * niveaux — deux tâches indépendantes y tombent au même niveau, donc se lisent
 * comme parallèles.
 *
 * Tout ce qui sert à dessiner est **servi** (`niveau`/`rang` par nœud,
 * `compartiment`, `profondeur`, `largeur`, `plat`) : rien n'est à recalculer
 * ici. Se recharge sur les événements du flux — `run.plan`, `tache.statut`,
 * `tache.detail` —, le graphe n'ayant pas de canal à lui.
 */
export function chargerGrapheExecution(runId: string): Promise<GrapheRun> {
  return chargerJson<GrapheRun>(
    `/api/executions/${encodeURIComponent(runId)}/graphe`,
  );
}

/**
 * La frise d'activité d'une exécution (`GET /api/executions/{run_id}/frise`,
 * #355) : les changements de statut de tâche **et** les messages inter-agents
 * sur une même chronologie, rangés en couloirs — un par agent, plus un repli
 * pour ce qui n'a pas d'agent résoluble.
 *
 * Tout ce qui sert à dessiner est **servi** : le tri (instant puis rang du
 * journal, déterministe quel que soit l'ordre d'arrivée), le couloir de chaque
 * entrée, et le statut résolu qui distingue une tâche bloquée d'une tâche qui
 * attend un humain. Rien à regrouper ni à retrier ici.
 *
 * Pas de `?projet=`, par la même porte que `/graphe` et `/cout` : le run seul
 * suffit à désigner ce qu'on lit. Se recharge sur le pouls du shell, la frise
 * n'ayant pas de canal à elle.
 */
export function chargerFriseExecution(runId: string): Promise<FriseRun> {
  return chargerJson<FriseRun>(
    `/api/executions/${encodeURIComponent(runId)}/frise`,
  );
}

/**
 * La vue coûts & analytics (`GET /api/analytics/couts`, #87) : agrégats par
 * tâche, par agent et par exécution, total et série temporelle du coût.
 * `depuis` (ISO) restreint la fenêtre — la période sélectionnable de l'UI ;
 * `pas` fixe la granularité des seaux de la série (minute/heure/jour).
 */
export function chargerAnalyticsCouts(options: {
  depuis?: string;
  pas?: PasSerie;
  projet: PorteeProjet;
}): Promise<AnalyticsCouts> {
  const params = new URLSearchParams();
  if (options.depuis !== undefined) params.set("depuis", options.depuis);
  if (options.pas !== undefined) params.set("pas", options.pas);
  params.set("projet", options.projet);
  return chargerJson<AnalyticsCouts>(`/api/analytics/couts?${params.toString()}`);
}

/**
 * Une page du **journal persisté** (`GET /api/journal`, #478) : l'historique des
 * événements, filtré, trié et paginé côté serveur.
 *
 * C'est ce qui rend un fil d'activité relisible après un rechargement — le
 * WebSocket ne sert plus qu'au direct, par-dessus cet historique. Comme toutes
 * les lectures qui agrègent, la portée projet est **exigée** (#277) ; `runId`
 * s'y ajoute par le filtre `run_id` déjà au contrat (#183), et c'est lui que la
 * vue d'un run utilise pour n'avoir que son journal.
 *
 * `taille` est plafonnée à 200 par le backend (`422` au-delà) : un appelant qui
 * veut « tout » demande la plus grande page, il ne demande pas l'infini.
 */
export function chargerJournal(
  portee: PorteeProjet,
  options: {
    runId?: string;
    agent?: string;
    type?: string;
    depuis?: string;
    jusqua?: string;
    tri?: string;
    ordre?: string;
    page?: number;
    taille?: number;
  } = {},
): Promise<PageJournal> {
  const params = new URLSearchParams({ projet: portee });
  if (options.runId !== undefined) params.set("run_id", options.runId);
  if (options.agent !== undefined) params.set("agent", options.agent);
  if (options.type !== undefined) params.set("type", options.type);
  if (options.depuis !== undefined) params.set("depuis", options.depuis);
  if (options.jusqua !== undefined) params.set("jusqua", options.jusqua);
  if (options.tri !== undefined) params.set("tri", options.tri);
  if (options.ordre !== undefined) params.set("ordre", options.ordre);
  if (options.page !== undefined) params.set("page", String(options.page));
  if (options.taille !== undefined) params.set("taille", String(options.taille));
  return chargerJson<PageJournal>(`/api/journal?${params.toString()}`);
}

/**
 * Envoi JSON (POST par défaut, corps optionnel — un DELETE n'en porte pas)
 * dont l'échec relaye le `detail` du backend : c'est le message montré à
 * l'utilisateur (404 tâche inconnue, 409 demande déjà tranchée, 422 contenu
 * vide…).
 */
async function envoyerJson(
  chemin: string,
  corps: unknown,
  refusParDefaut: string,
  methode: "POST" | "PUT" | "DELETE" = "POST",
): Promise<void> {
  const reponse = await fetch(`${API_URL}${chemin}`, {
    method: methode,
    ...(corps !== undefined && {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(corps),
    }),
  });
  if (!reponse.ok) {
    let detail = `${refusParDefaut} (${reponse.status})`;
    try {
      const contenu = (await reponse.json()) as { detail?: unknown };
      if (typeof contenu.detail === "string") detail = contenu.detail;
    } catch {
      // corps non JSON : on garde le message générique
    }
    throw new Error(detail);
  }
}

/** Les playbooks des agents (#76) : version courante et provenance de chacun. */
export function chargerPlaybooks(): Promise<PlaybookFiche[]> {
  return chargerJson<PlaybookFiche[]>("/api/playbooks");
}

/** Le playbook courant d'un agent, contenu compris (celui chargé par le moteur). */
export function chargerPlaybook(agent: string): Promise<PlaybookDetail> {
  return chargerJson<PlaybookDetail>(
    `/api/playbooks/${encodeURIComponent(agent)}`,
  );
}

/** L'historique des versions du playbook d'un agent (métadonnées seules, EF-25). */
export function chargerVersionsPlaybook(
  agent: string,
): Promise<VersionPlaybook[]> {
  return chargerJson<VersionPlaybook[]>(
    `/api/playbooks/${encodeURIComponent(agent)}/versions`,
  );
}

/** Une version passée du playbook, contenu compris. */
export function chargerVersionPlaybook(
  agent: string,
  version: number,
): Promise<VersionPlaybookDetail> {
  return chargerJson<VersionPlaybookDetail>(
    `/api/playbooks/${encodeURIComponent(agent)}/versions/${version}`,
  );
}

/**
 * Publie une nouvelle version du playbook (`PUT /api/playbooks/{agent}`, #77) :
 * le contenu intégral, qui devient la version courante chargée par les moteurs
 * construits ensuite (l'application à chaud est le lot #78).
 */
export function ecrirePlaybook(agent: string, contenu: string): Promise<void> {
  return envoyerJson(
    `/api/playbooks/${encodeURIComponent(agent)}`,
    { contenu },
    "publication refusée",
    "PUT",
  );
}

/**
 * Retour arrière (EF-25) : republie une version passée comme nouvelle courante
 * (`POST /api/playbooks/{agent}/restaurer`). L'historique reste append-only.
 */
export function restaurerPlaybook(
  agent: string,
  version: number,
): Promise<void> {
  return envoyerJson(
    `/api/playbooks/${encodeURIComponent(agent)}/restaurer`,
    { version },
    "restauration refusée",
  );
}

/**
 * Les propositions d'auto-amélioration en brouillon d'un agent (#111) :
 * métadonnées et justification, sans le contenu. Une proposition n'est jamais
 * la version courante tant qu'elle n'a pas été appliquée.
 */
export function chargerPropositionsPlaybook(
  agent: string,
): Promise<PropositionPlaybook[]> {
  return chargerJson<PropositionPlaybook[]>(
    `/api/playbooks/${encodeURIComponent(agent)}/propositions`,
  );
}

/** Une proposition en brouillon, contenu compris — de quoi la relire avant d'agir. */
export function chargerPropositionPlaybook(
  agent: string,
  numero: number,
): Promise<PropositionPlaybookDetail> {
  return chargerJson<PropositionPlaybookDetail>(
    `/api/playbooks/${encodeURIComponent(agent)}/propositions/${numero}`,
  );
}

/**
 * Adopte une proposition (#140) : son contenu devient la version courante,
 * chargée à chaud par le moteur dès la tâche suivante (#78), et elle sort des
 * brouillons.
 */
export function appliquerPropositionPlaybook(
  agent: string,
  numero: number,
): Promise<void> {
  return envoyerJson(
    `/api/playbooks/${encodeURIComponent(agent)}/propositions/${numero}/appliquer`,
    undefined,
    "application refusée",
  );
}

/** Écarte une proposition (#140) : elle disparaît, la version courante ne bouge pas. */
export function rejeterPropositionPlaybook(
  agent: string,
  numero: number,
): Promise<void> {
  return envoyerJson(
    `/api/playbooks/${encodeURIComponent(agent)}/propositions/${numero}/rejeter`,
    undefined,
    "rejet refusé",
  );
}

/** Le catalogue d'agents (#72) : les agents par défaut du code puis les personnalisés. */
export function chargerCatalogue(): Promise<AgentCatalogue[]> {
  return chargerJson<AgentCatalogue[]>("/api/catalogue");
}

/** La définition complète d'un agent du catalogue, playbook compris. */
export function chargerAgentCatalogue(
  nom: string,
): Promise<AgentCatalogueDetail> {
  return chargerJson<AgentCatalogueDetail>(
    `/api/catalogue/${encodeURIComponent(nom)}`,
  );
}

/**
 * Crée un agent personnalisé (`POST /api/catalogue`, #73) : persisté hors du
 * code, il est routable et exécutable par les moteurs construits ensuite.
 */
export function creerAgent(
  nom: string,
  definition: DefinitionAgent,
): Promise<void> {
  return envoyerJson("/api/catalogue", { nom, ...definition }, "création refusée");
}

/**
 * Remplace la définition d'un agent personnalisé (`PUT /api/catalogue/{nom}`) :
 * l'intégrale, pas un diff — le nom, clé de routage, ne change pas.
 */
export function modifierAgent(
  nom: string,
  definition: DefinitionAgent,
): Promise<void> {
  return envoyerJson(
    `/api/catalogue/${encodeURIComponent(nom)}`,
    definition,
    "modification refusée",
    "PUT",
  );
}

/** Supprime un agent personnalisé du catalogue (`DELETE /api/catalogue/{nom}`). */
export function supprimerAgent(nom: string): Promise<void> {
  return envoyerJson(
    `/api/catalogue/${encodeURIComponent(nom)}`,
    undefined,
    "suppression refusée",
    "DELETE",
  );
}

/** Réassigne manuellement une tâche à un agent (`POST /api/taches/{id}/reassigner`). */
export function reassignerTache(tacheId: string, agent: string): Promise<void> {
  return envoyerJson(
    `/api/taches/${encodeURIComponent(tacheId)}/reassigner`,
    { agent },
    "réassignation refusée",
  );
}

/**
 * Règle la capacité d'un agent (`POST /api/agents/{nom}/capacite`, #86,
 * EF-21) : activer/désactiver et/ou ajuster le plafond d'instances. Chaque
 * champ omis laisse la valeur en place ; le réglage est persisté côté backend
 * et pris en compte par le moteur et les workers dès la tâche suivante.
 */
export function reglerCapaciteAgent(
  nom: string,
  reglage: { actif?: boolean; instances?: number },
): Promise<void> {
  return envoyerJson(
    `/api/agents/${encodeURIComponent(nom)}/capacite`,
    reglage,
    "réglage de capacité refusé",
  );
}

/** Le fil de chat utilisateur ↔ agent (`GET /api/chat/{agent}`, #84), persisté. */
export function chargerFilChat(agent: string): Promise<FilChat> {
  return chargerJson<FilChat>(`/api/chat/${encodeURIComponent(agent)}`);
}

/**
 * Envoie un message utilisateur à l'agent (`POST /api/chat/{agent}/messages`,
 * #84) : la réponse est produite dans la requête, la paire message/réponse
 * rejoint le fil persisté et part en `chat.message` sur le WebSocket. Un échec
 * de réponse (502) ne perd pas le message utilisateur, déjà acquis au fil.
 *
 * `sources` (#482) est la matière que le message embarque, **dans l'ordre où
 * l'écran l'a composée** : un fichier y voyage par l'`id` que
 * `televerserSources` lui a rendu, un dossier par son `chemin`, une page par sa
 * `valeur`. Omise ou vide, l'appel est exactement celui d'avant ce lot.
 *
 * Le refus emprunte le chemin motivé des sources et **non** celui d'`envoyerJson`
 * : une source refusée porte un `motif` et un `index`, et c'est l'index qui
 * permet au fil d'afficher le refus sur la source fautive plutôt qu'en bloc.
 *
 * `projetId` (#683) est le **projet de la fenêtre** d'où part le message. Ce
 * n'est pas une portée de lecture — le fil reste transverse (`useChat`), et
 * aucun `PORTEE_TOUS`/`PORTEE_AUCUN` n'a de sens ici : c'est l'identifiant du
 * projet actif, et lui seul. Il ne sert qu'à ce que la réponse **ouvre** : un
 * run dicté à l'orchestration appartient au projet où on l'a demandé, donc il
 * figure dans sa liste de runs et s'ouvre en détail. Omis, le run part sans
 * projet et n'apparaît alors dans la liste d'aucun (#277).
 */
export async function envoyerMessageChat(
  agent: string,
  contenu: string,
  sources: SourceDeclaree[] = [],
  projetId: string | null = null,
): Promise<void> {
  const reponse = await fetch(
    `${API_URL}/api/chat/${encodeURIComponent(agent)}/messages`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        contenu,
        ...(sources.length > 0 && { sources }),
        ...(projetId !== null && { projet_id: projetId }),
      }),
    },
  );
  if (!reponse.ok) throw await refusSource(reponse, "envoi refusé");
}

/**
 * Tranche une demande de validation humaine (#48) : approuve ou refuse
 * l'action sensible (`POST /api/validations/{tache_id}/decision`). Le moteur,
 * en pause sur cette demande, reprend la tâche ou l'annule proprement.
 *
 * `motif` (#272) est la raison **facultative** d'un refus. Vide, la clé n'est
 * pas envoyée du tout : un refus sans motif produit l'appel d'avant ce lot, à
 * l'octet près — le backend la déclare optionnelle, mais l'omettre garde le
 * contrat lisible pour qui relit une trace réseau. Ignoré sur une approbation,
 * côté backend comme ici : approuver ne se motive pas dans ce canal.
 */
export function deciderValidation(
  tacheId: string,
  approuve: boolean,
  motif = "",
): Promise<void> {
  const raison = approuve ? "" : motif.trim();
  return envoyerJson(
    `/api/validations/${encodeURIComponent(tacheId)}/decision`,
    { approuve, ...(raison !== "" && { motif: raison }) },
    "décision refusée",
  );
}

/**
 * Comme `envoyerJson`, mais **rend le corps de la réponse** — pour les
 * écritures dont l'appelant relit le résultat (l'intégration créée au pool, le
 * résumé d'un run qu'on vient de suspendre).
 *
 * Corps `undefined` : ni en-tête ni charge utile, comme `envoyerJson`. Un ordre
 * qui tient tout entier dans son URL (`…/{run_id}/pause`) n'a rien à envoyer, et
 * annoncer un `Content-Type: application/json` sans rien derrière décrirait un
 * corps qui n'existe pas.
 */
async function envoyerJsonEtLire<T>(
  chemin: string,
  corps: unknown,
  refusParDefaut: string,
  methode: "POST" | "PUT" = "POST",
): Promise<T> {
  const reponse = await fetch(`${API_URL}${chemin}`, {
    method: methode,
    ...(corps !== undefined && {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(corps),
    }),
  });
  if (!reponse.ok) {
    let detail = `${refusParDefaut} (${reponse.status})`;
    try {
      const contenu = (await reponse.json()) as { detail?: unknown };
      if (typeof contenu.detail === "string") detail = contenu.detail;
    } catch {
      // corps non JSON : on garde le message générique
    }
    throw new Error(detail);
  }
  return (await reponse.json()) as T;
}

/**
 * La bibliothèque de serveurs MCP (`GET /api/mcp/registre`, #131), recherchable
 * par nom/tag/éditeur (`q` vide → tout le registre). Chaque entrée guide sa
 * configuration selon son mode d'auth.
 *
 * Elle a **deux sources** depuis #677 : `source` n'en demande qu'une (`curee` ou
 * `decouverte`), et sans elle les deux viennent, curées d'abord. Seule une
 * entrée **curée** est instanciable (garde-fou supply-chain, docs/19) — `curee`
 * le dit sur chaque entrée.
 */
export function chargerRegistreMcp(
  q = "",
  source?: SourceRegistreMcp,
): Promise<EntreeRegistreMcp[]> {
  const parametres = new URLSearchParams();
  if (q.trim()) parametres.set("q", q.trim());
  if (source) parametres.set("source", source);
  const requete = parametres.size ? `?${parametres}` : "";
  return chargerJson<EntreeRegistreMcp[]>(`/api/mcp/registre${requete}`);
}

/**
 * La provenance de la bibliothèque (`GET /api/mcp/registre/provenance`, #271) :
 * d'où vient chaque source, quand elle a été revue ou rafraîchie, et les tags
 * par lesquels chercher. Route sœur et non enveloppe : `chargerRegistreMcp`
 * rend toujours une liste nue.
 */
export function chargerProvenanceRegistreMcp(): Promise<ProvenanceRegistreMcp> {
  return chargerJson<ProvenanceRegistreMcp>("/api/mcp/registre/provenance");
}

/**
 * Le pool projet des intégrations MCP configurées (`GET /api/mcp/pool`, #133) :
 * chaque intégration avec son mode d'auth et l'état (présent/valide) de ses
 * secrets côté coffre projet — jamais une valeur de secret.
 */
export function chargerPoolMcp(): Promise<PoolMcp> {
  return chargerJson<PoolMcp>("/api/mcp/pool");
}

/**
 * Ajoute (ou reconfigure) une intégration du registre dans le pool projet
 * (`POST /api/mcp/pool`, #133) : instancie l'entrée curée et pose ses secrets
 * **une seule fois** dans le coffre projet chiffré. Rend l'intégration créée.
 */
export function ajouterIntegrationPoolMcp(corps: {
  registre_id: string;
  nom?: string;
  secrets: { cle: string; valeur: string; expire_le?: string | null }[];
}): Promise<IntegrationPoolMcp> {
  return envoyerJsonEtLire<IntegrationPoolMcp>(
    "/api/mcp/pool",
    corps,
    "ajout au pool refusé",
  );
}

/**
 * Retire une intégration du pool projet (`DELETE /api/mcp/pool/{id}`, #133) :
 * la désactive chez chaque agent et purge les secrets qu'elle seule
 * référençait.
 */
export function supprimerIntegrationPoolMcp(id: string): Promise<void> {
  return envoyerJson(
    `/api/mcp/pool/${encodeURIComponent(id)}`,
    undefined,
    "retrait du pool refusé",
    "DELETE",
  );
}

/**
 * Fixe les intégrations du pool **activées** pour un agent
 * (`PUT /api/mcp/activations/{agent}`, #133) : remplacement intégral (liste
 * vide pour tout désactiver). L'écriture derrière l'interrupteur par agent.
 */
export function definirActivationsMcp(
  agent: string,
  integrations: string[],
): Promise<void> {
  return envoyerJson(
    `/api/mcp/activations/${encodeURIComponent(agent)}`,
    { integrations },
    "activation refusée",
    "PUT",
  );
}

// --- Projets de l'utilisateur (#225, API #223) ----------------------------
//
// Ces six routes n'empruntent pas les enveloppes ci-dessus parce qu'elles ne
// refusent pas de la même façon : leur `detail` est un **objet**
// `{motif, message}` et non une phrase (docs/05 §6.7). Le `motif` est un code
// stable — c'est lui qui permet à l'écran de dire *pourquoi* une racine est
// refusée (EF-38) sans analyser du texte, et de distinguer « je refuse de
// regarder là » d'un dossier réellement vide. L'aplatir en `string` le
// perdrait ; on le porte donc jusqu'au composant.

/**
 * Un refus motivé d'une route projets. `motif` est le code stable du backend
 * (`chemin-sensible`, `hors-racines-explorables`, `dossier-absent`…), `message`
 * la phrase à montrer. Un refus sans corps exploitable retombe sur
 * `requete-invalide`, jamais sur une exception muette.
 */
export class ErreurProjet extends Error {
  readonly motif: string;

  constructor(motif: string, message: string) {
    super(message);
    this.name = "ErreurProjet";
    this.motif = motif;
  }
}

/** Le refus porté par une réponse en échec — motif compris quand il y en a un. */
async function refusProjet(
  reponse: Response,
  refusParDefaut: string,
): Promise<ErreurProjet> {
  let motif = "requete-invalide";
  let message = `${refusParDefaut} (${reponse.status})`;
  try {
    const contenu = (await reponse.json()) as { detail?: unknown };
    const detail = contenu.detail;
    if (typeof detail === "string") {
      message = detail;
    } else if (detail !== null && typeof detail === "object") {
      const refus = detail as Partial<RefusProjet>;
      if (typeof refus.motif === "string") motif = refus.motif;
      if (typeof refus.message === "string") message = refus.message;
    }
  } catch {
    // corps non JSON : on garde le motif et le message génériques
  }
  return new ErreurProjet(motif, message);
}

/** Lecture d'une route projets, dont l'échec porte son motif. */
async function lireProjets<T>(
  chemin: string,
  refusParDefaut: string,
): Promise<T> {
  const reponse = await fetch(`${API_URL}${chemin}`, { cache: "no-store" });
  if (!reponse.ok) throw await refusProjet(reponse, refusParDefaut);
  return (await reponse.json()) as T;
}

/** Écriture d'une route projets (corps optionnel : un DELETE n'en porte pas). */
async function ecrireProjet<T>(
  chemin: string,
  corps: unknown,
  refusParDefaut: string,
  methode: "POST" | "PUT" | "DELETE" = "POST",
): Promise<T> {
  const reponse = await fetch(`${API_URL}${chemin}`, {
    method: methode,
    ...(corps !== undefined && {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(corps),
    }),
  });
  if (!reponse.ok) throw await refusProjet(reponse, refusParDefaut);
  return (await reponse.json()) as T;
}

/** Les projets déclarés (`GET /api/projets`), dans l'ordre des identifiants. */
export function chargerProjets(): Promise<Projet[]> {
  return lireProjets<Projet[]>("/api/projets", "projets illisibles");
}

/**
 * Une page de l'explorateur de dossiers (`GET /api/projets/explorateur`) :
 * `chemin` `null` demande le point d'entrée, c'est-à-dire les racines
 * explorables elles-mêmes. C'est le backend — qui tourne sur le poste — qui
 * énumère : le navigateur ne fabrique jamais de chemin absolu.
 */
export function chargerExplorateur(
  chemin: string | null = null,
): Promise<PageExplorateur> {
  const requete =
    chemin === null ? "" : `?chemin=${encodeURIComponent(chemin)}`;
  return lireProjets<PageExplorateur>(
    `/api/projets/explorateur${requete}`,
    "dossier illisible",
  );
}

/**
 * L'état du sélecteur de dossier natif (`GET /api/projets/selecteur`, #278).
 * Toujours 200 : une indisponibilité est une **réponse**, pas une panne — c'est
 * elle que l'écran affiche à la place d'un bouton mort.
 */
export function chargerDisponibiliteSelecteur(): Promise<DisponibiliteSelecteur> {
  return lireProjets<DisponibiliteSelecteur>(
    "/api/projets/selecteur",
    "sélecteur natif injoignable",
  );
}

/**
 * Ouvre le dialogue de dossier de l'OS (`POST /api/projets/selecteur`, #278) et
 * rend le chemin choisi. Le backend tourne sur le poste : c'est lui qui ouvre
 * la fenêtre, le navigateur ne livrant jamais de chemin absolu.
 *
 * L'attente est **longue par nature** — le temps que quelqu'un parcoure son
 * disque —, et une annulation revient en 200 (`annule: true`). Seuls les vrais
 * empêchements lèvent, avec leur motif.
 */
export function ouvrirSelecteurNatif(
  depart: string | null = null,
): Promise<ChoixSelecteur> {
  return ecrireProjet<ChoixSelecteur>(
    "/api/projets/selecteur",
    { depart },
    "sélecteur natif indisponible",
  );
}

/**
 * Déclare un projet (`POST /api/projets`) : la racine est **validée** côté
 * serveur (EF-38) et le VCS constaté sur le disque — d'où la relecture du
 * projet créé plutôt qu'une recopie de ce qui a été envoyé.
 */
export function creerProjet(declaration: DeclarationProjet): Promise<Projet> {
  return ecrireProjet<Projet>("/api/projets", declaration, "projet refusé");
}

/**
 * Remplace la déclaration d'un projet (`PUT /api/projets/{id}`) : l'intégrale,
 * pas un diff — un champ omis retombe sur son défaut.
 */
export function modifierProjet(
  id: string,
  declaration: DeclarationProjet,
): Promise<Projet> {
  return ecrireProjet<Projet>(
    `/api/projets/${encodeURIComponent(id)}`,
    declaration,
    "modification refusée",
    "PUT",
  );
}

/**
 * Oublie un projet (`DELETE /api/projets/{id}`). Ne touche **jamais** au
 * dossier sur le disque : oublier un projet n'est pas supprimer le travail.
 */
export function supprimerProjet(id: string): Promise<void> {
  return ecrireProjet<{ id: string; supprime: boolean }>(
    `/api/projets/${encodeURIComponent(id)}`,
    undefined,
    "suppression refusée",
    "DELETE",
  ).then(() => undefined);
}

// --- Composer un objectif (#319, API #315/#316/#317) -----------------------
//
// Trois routes, un même régime de refus : leur `detail` est l'objet
// `{motif, message}` des routes projets (docs/05 §6.7), augmenté d'un `index`
// quand le refus **vise une source** (docs/05 §6.1). C'est ce qui permet à
// l'écran d'afficher un refus **à l'endroit du geste refusé** — sur la source
// fautive et non en tête de formulaire — et de distinguer « rien à lire ici »
// d'un « je refuse de lire ça ». L'aplatir en `string` perdrait les deux.

/**
 * Un refus motivé d'une route de sources. Même contrat qu'`ErreurProjet` (#225),
 * plus l'`index` de la source visée quand il y en a une : « une source est trop
 * grosse » sans dire laquelle obligerait à tout relire pour savoir quoi retirer.
 */
export class ErreurSource extends Error {
  readonly motif: string;
  readonly index: number | null;

  constructor(motif: string, message: string, index: number | null = null) {
    super(message);
    this.name = "ErreurSource";
    this.motif = motif;
    this.index = index;
  }
}

/** Le refus porté par une réponse en échec — motif et index compris. */
async function refusSource(
  reponse: Response,
  refusParDefaut: string,
): Promise<ErreurSource> {
  let motif = "requete-invalide";
  let message = `${refusParDefaut} (${reponse.status})`;
  let index: number | null = null;
  try {
    const contenu = (await reponse.json()) as { detail?: unknown };
    const detail = contenu.detail;
    if (typeof detail === "string") {
      message = detail;
    } else if (detail !== null && typeof detail === "object") {
      const refus = detail as {
        motif?: unknown;
        message?: unknown;
        index?: unknown;
      };
      if (typeof refus.motif === "string") motif = refus.motif;
      if (typeof refus.message === "string") message = refus.message;
      if (typeof refus.index === "number") index = refus.index;
    }
  } catch {
    // corps non JSON : on garde le motif et le message génériques
  }
  return new ErreurSource(motif, message, index);
}

/**
 * L'aperçu d'ingestion (`POST /api/sources/apercu`, #319) : ce que les sources
 * **donneraient**, sans lancer et sans rien conserver. C'est le geste gratuit du
 * critère 2 — voir avant de dépenser —, et il porte les octets plutôt que des
 * identifiants : un aperçu ne dépose rien.
 *
 * Le n-ième `fichier` correspond à la n-ième source de type `fichier` de
 * `sources` : un multipart ne transporte pas de correspondance, il transporte
 * deux listes ordonnées.
 */
export async function apercuSources(
  sources: SourceDeclaree[],
  fichiers: File[],
): Promise<RapportLecture> {
  const corps = new FormData();
  corps.set("sources", JSON.stringify(sources));
  for (const fichier of fichiers) corps.append("fichier", fichier);
  const reponse = await fetch(`${API_URL}/api/sources/apercu`, {
    method: "POST",
    body: corps,
  });
  if (!reponse.ok) throw await refusSource(reponse, "aperçu refusé");
  return (await reponse.json()) as RapportLecture;
}

/**
 * Téléverse les fichiers déposés (`POST /api/sources`, #317) et rend leurs
 * **identifiants de source**, à reporter dans `sources[]` au lancement. Un seul
 * appel pour tout le dépôt : le champ `fichier` est répétable.
 *
 * Les octets vont dans le dépôt de téléversement — hors de tout projet et hors
 * de tout run —, et c'est le lancement qui les rattache au run qui les consomme
 * (docs/05 §6.8).
 */
export async function televerserSources(
  fichiers: File[],
): Promise<TeleversementSources> {
  const corps = new FormData();
  for (const fichier of fichiers) corps.append("fichier", fichier);
  const reponse = await fetch(`${API_URL}/api/sources`, {
    method: "POST",
    body: corps,
  });
  if (!reponse.ok) throw await refusSource(reponse, "téléversement refusé");
  return (await reponse.json()) as TeleversementSources;
}

/**
 * Lance une exécution (`POST /api/executions`, #185/#317) et rend son résumé,
 * `run_id` compris. Le run se déroule **hors** de la requête : la réponse dit
 * qu'il est parti, la suite arrive par le flux d'événements.
 *
 * Le refus emprunte le chemin motivé des sources : un objectif vide sort en
 * `requete-invalide`, une source fautive avec son motif **et son index**.
 */
export async function lancerExecution(
  corps: LancementExecution,
): Promise<ResumeExecution> {
  const reponse = await fetch(`${API_URL}/api/executions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(corps),
  });
  if (!reponse.ok) throw await refusSource(reponse, "lancement refusé");
  return (await reponse.json()) as ResumeExecution;
}

/**
 * Suspend un run en cours (`POST /api/executions/{run_id}/pause`, #477) et rend
 * son résumé, `en_pause` posé.
 *
 * **Aucune tâche nouvelle n'est lancée ; celles qui sont en vol vont à leur
 * terme.** C'est ce qui sépare ce geste de l'annulation, où les tâches sont tuées
 * là où elles en sont et perdent leur travail. Le run n'est pas soldé et ne change
 * même pas de statut : la pause est un drapeau à côté.
 *
 * `409` sur un run **déjà soldé** ou **déjà suspendu**, relayé tel quel.
 */
export function suspendreExecution(runId: string): Promise<ResumeExecution> {
  return envoyerJsonEtLire<ResumeExecution>(
    `/api/executions/${encodeURIComponent(runId)}/pause`,
    undefined,
    "mise en pause refusée",
  );
}

/**
 * Reprend un run suspendu **là où il en était**
 * (`POST /api/executions/{run_id}/reprendre`, #477) et rend son résumé, `en_pause`
 * retiré.
 *
 * ⚠ À ne pas confondre avec `relancerExecution` ci-dessous, qui rejoue un run
 * **mort** depuis son brief et repaie une planification : c'est un nouveau run,
 * avec un nouvel identifiant. Ici il n'y a qu'un run, le même, dont les tâches
 * repartent — rien n'a été tué, il n'y a rien à reconstruire.
 *
 * `409` si le run n'est pas suspendu : il n'y a rien à reprendre d'un run qui
 * travaille.
 */
export function reprendreExecution(runId: string): Promise<ResumeExecution> {
  return envoyerJsonEtLire<ResumeExecution>(
    `/api/executions/${encodeURIComponent(runId)}/reprendre`,
    undefined,
    "reprise refusée",
  );
}

/**
 * Interrompt un run en vol (`POST /api/executions/{run_id}/annuler`, #185) et rend
 * son résumé, passé à `annulee` et `fin` posée.
 *
 * **Les tâches en vol sont tuées là où elles en sont et perdent leur travail** —
 * c'est tout ce qui sépare ce geste de `suspendreExecution`, qui laisse celles qui
 * tournent aller à leur terme. Le run est soldé : rien ne le fera repartir, et le
 * seul recours est de le **relancer** (`relancerExecution` ci-dessous), c'est-à-dire
 * de rejouer son cadrage dans un run neuf.
 *
 * L'API borne son attente (`DELAI_ANNULATION_S`) : un hôte qui ne répond plus ne
 * suspend pas la requête, le run est soldé de toute façon. C'est ce qui rend le
 * geste utile sur un run **orphelin** — quatre fantômes du 22 juillet ont dû être
 * soldés au `curl` le 2026-08-24, faute de ce bouton (#467).
 *
 * Refus relayés tels quels : `404` run inconnu, `409` run **déjà soldé** — un run
 * terminé n'est plus interruptible, et le dire vaut mieux que faire croire à une
 * annulation.
 */
export function annulerExecution(runId: string): Promise<ResumeExecution> {
  return envoyerJsonEtLire<ResumeExecution>(
    `/api/executions/${encodeURIComponent(runId)}/annuler`,
    undefined,
    "annulation refusée",
  );
}

/**
 * Rejoue un run interrompu **sur son brief approuvé**
 * (`POST /api/executions/{run_id}/relancer`, #349) et rend le résumé du **nouveau**
 * run — celui qui porte `reprise_de`.
 *
 * Ce qu'un run mort emporte n'est pas du temps machine mais un cadrage validé par
 * un humain : clarification, brief, approbation. La relance le rejoue en mode
 * `sans`, donc sans repasser par la clarification ni par la validation, en
 * conservant le projet. Le run repris est soldé dans le même geste.
 *
 * Les refus du backend sont relayés tels quels, motif compris : `409` sur un run
 * **déjà soldé** (rien à reprendre) ou **encore vivant** (l'interrompre d'abord si
 * c'est bien voulu), `422` sur un run dont le brief n'a jamais été approuvé — il
 * n'y a rien à rejouer, et le dire vaut mieux que repartir en silence sur son
 * objectif brut. C'est le message de l'API qui s'affiche : lui seul sait ce qu'il
 * a refusé.
 */
export async function relancerExecution(
  runId: string,
): Promise<ResumeExecution> {
  const reponse = await fetch(
    `${API_URL}/api/executions/${encodeURIComponent(runId)}/relancer`,
    { method: "POST" },
  );
  if (!reponse.ok) throw await refusSource(reponse, "relance refusée");
  return (await reponse.json()) as ResumeExecution;
}

/**
 * Les exécutions connues du backend, à la portée demandée (`GET /api/executions`,
 * #185) — un **résumé** par run, sans son brief ni sa trace.
 *
 * C'est ce qui rend un run suspendu **visible** (#322) : `statut` vaut alors
 * `en_attente_brief` (ou `en_attente_reponses`, #321 ; ou `en_attente_arbitrage`,
 * #571) et rien d'autre ne le dirait — un run arrêté sur son brief n'a créé aucune
 * tâche, donc il n'apparaît ni au Kanban ni dans les grands livres, qui se
 * dérivent des tâches ; et un run arrêté sur un arbitrage n'y bouge plus, ce qui
 * le rend indiscernable d'un run qui travaille partout ailleurs (#568).
 */
export function chargerExecutions(
  portee: PorteeProjet,
): Promise<ResumeExecution[]> {
  return chargerJson<ResumeExecution[]>(
    `/api/executions?projet=${encodeURIComponent(portee)}`,
  );
}

/**
 * Le détail d'une exécution (`GET /api/executions/{run_id}`, #185) : le résumé,
 * le **brief** (#320), le grand livre (#57) et la trace. Un seul appel pour tout
 * ce que l'écran de validation (#322) met sous les yeux de qui décide — le brief
 * à relire d'un côté, le coût déjà engagé de l'autre.
 */
export function chargerExecution(runId: string): Promise<DetailExecution> {
  return chargerJson<DetailExecution>(
    `/api/executions/${encodeURIComponent(runId)}`,
  );
}

/**
 * Tranche le brief d'un run (`POST /api/executions/{run_id}/brief/decision`,
 * #320) : approuver — tel quel (`brief: null`) ou **corrigé**, le brief envoyé
 * devenant alors l'entrée de la décomposition — ou refuser, ce qui solde le run
 * sans qu'aucune tâche ait été créée.
 *
 * Les refus du backend sont relayés tels quels : 409 si le run n'attend plus de
 * décision (déjà tranchée ailleurs, run annulé entre-temps), 422 si le brief
 * corrigé ne respecte pas le schéma partagé — un périmètre vidé, un critère
 * d'acceptation en double. C'est le message de l'API qui s'affiche, pas une
 * reformulation d'ici : lui seul sait ce qu'il a refusé.
 */
export function deciderBrief(
  runId: string,
  decision: DecisionBrief,
): Promise<void> {
  return envoyerJson(
    `/api/executions/${encodeURIComponent(runId)}/brief/decision`,
    decision,
    "décision refusée",
  );
}

/**
 * Répond aux questions de clarification d'un brief
 * (`POST /api/executions/{run_id}/brief/reponses`, #321) : le run repart rédiger
 * son brief en les intégrant.
 *
 * Les réponses s'apparient **par position** aux `questions` du brief en attente,
 * et la liste doit en faire exactement le compte (422 sinon) : une liste décalée
 * affecterait des réponses aux mauvaises questions sans que rien ne le signale.
 * Une réponse **vide** est licite et vaut « je ne sais pas » — la question part
 * en hypothèse explicite plutôt que d'être reposée.
 */
export function repondreBrief(
  runId: string,
  reponses: string[],
): Promise<void> {
  return envoyerJson(
    `/api/executions/${encodeURIComponent(runId)}/brief/reponses`,
    { reponses } satisfies ReponsesBrief,
    "réponses refusées",
  );
}

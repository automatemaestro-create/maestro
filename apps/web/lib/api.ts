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
  DeclarationProjet,
  DefinitionAgent,
  DisponibiliteSelecteur,
  EntreeRegistreMcp,
  EtatAgent,
  FilChat,
  IntegrationPoolMcp,
  PageExplorateur,
  PasSerie,
  PlaybookDetail,
  PlaybookFiche,
  PoolMcp,
  Projet,
  PropositionPlaybook,
  PropositionPlaybookDetail,
  RefusProjet,
  Sante,
  Tache,
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
 * mélange ». Tant que le projet actif n'est pas porté par le shell (#280/#281),
 * les appels ci-dessous demandent explicitement la vue transverse.
 */
export type PorteeProjet = string;
export const PORTEE_TOUS = "tous";
export const PORTEE_AUCUN = "aucun";

/** L'URL du flux d'événements temps réel (`WS /ws/evenements`), à la portée demandée. */
export function urlEvenements(portee: PorteeProjet = PORTEE_TOUS): string {
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

/** Les tâches connues du backend, à la portée demandée — la source du Kanban. */
export function chargerTaches(portee: PorteeProjet = PORTEE_TOUS): Promise<Tache[]> {
  return chargerJson<Tache[]>(`/api/taches?projet=${encodeURIComponent(portee)}`);
}

/** L'état des agents (libre/occupé, tâche courante, compteurs, coût cumulé). */
export function chargerAgents(): Promise<EtatAgent[]> {
  return chargerJson<EtatAgent[]>("/api/agents");
}

/** Les demandes de validation humaine (#48) : contexte, statut, décision. */
export function chargerValidations(
  portee: PorteeProjet = PORTEE_TOUS,
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
 * La vue coûts & analytics (`GET /api/analytics/couts`, #87) : agrégats par
 * tâche, par agent et par exécution, total et série temporelle du coût.
 * `depuis` (ISO) restreint la fenêtre — la période sélectionnable de l'UI ;
 * `pas` fixe la granularité des seaux de la série (minute/heure/jour).
 */
export function chargerAnalyticsCouts(options: {
  depuis?: string;
  pas?: PasSerie;
  projet?: PorteeProjet;
}): Promise<AnalyticsCouts> {
  const params = new URLSearchParams();
  if (options.depuis !== undefined) params.set("depuis", options.depuis);
  if (options.pas !== undefined) params.set("pas", options.pas);
  params.set("projet", options.projet ?? PORTEE_TOUS);
  return chargerJson<AnalyticsCouts>(`/api/analytics/couts?${params.toString()}`);
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
 */
export function envoyerMessageChat(
  agent: string,
  contenu: string,
): Promise<void> {
  return envoyerJson(
    `/api/chat/${encodeURIComponent(agent)}/messages`,
    { contenu },
    "envoi refusé",
  );
}

/**
 * Tranche une demande de validation humaine (#48) : approuve ou refuse
 * l'action sensible (`POST /api/validations/{tache_id}/decision`). Le moteur,
 * en pause sur cette demande, reprend la tâche ou l'annule proprement.
 */
export function deciderValidation(
  tacheId: string,
  approuve: boolean,
): Promise<void> {
  return envoyerJson(
    `/api/validations/${encodeURIComponent(tacheId)}/decision`,
    { approuve },
    "décision refusée",
  );
}

/**
 * Comme `envoyerJson`, mais **rend le corps de la réponse** — pour les
 * écritures dont l'appelant relit le résultat (l'intégration créée au pool).
 */
async function envoyerJsonEtLire<T>(
  chemin: string,
  corps: unknown,
  refusParDefaut: string,
  methode: "POST" | "PUT" = "POST",
): Promise<T> {
  const reponse = await fetch(`${API_URL}${chemin}`, {
    method: methode,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(corps),
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
 * La bibliothèque curée de serveurs MCP (`GET /api/mcp/registre`, #131),
 * recherchable par nom/tag (`q` vide → tout le registre). Chaque entrée guide
 * sa configuration selon son mode d'auth ; seule une entrée servie ici est
 * instanciable (garde-fou supply-chain, docs/19).
 */
export function chargerRegistreMcp(q = ""): Promise<EntreeRegistreMcp[]> {
  const requete = q.trim() ? `?q=${encodeURIComponent(q.trim())}` : "";
  return chargerJson<EntreeRegistreMcp[]>(`/api/mcp/registre${requete}`);
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

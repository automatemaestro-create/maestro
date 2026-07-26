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
  DefinitionAgent,
  EtatAgent,
  FilChat,
  PasSerie,
  PlaybookDetail,
  PlaybookFiche,
  PropositionPlaybook,
  PropositionPlaybookDetail,
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

/** L'URL du flux d'événements temps réel (`WS /ws/evenements`). */
export function urlEvenements(): string {
  return API_URL.replace(/^http/, "ws") + "/ws/evenements";
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

/** Les tâches connues du backend — la source du Kanban. */
export function chargerTaches(): Promise<Tache[]> {
  return chargerJson<Tache[]>("/api/taches");
}

/** L'état des agents (libre/occupé, tâche courante, compteurs, coût cumulé). */
export function chargerAgents(): Promise<EtatAgent[]> {
  return chargerJson<EtatAgent[]>("/api/agents");
}

/** Les demandes de validation humaine (#48) : contexte, statut, décision. */
export function chargerValidations(): Promise<Validation[]> {
  return chargerJson<Validation[]>("/api/validations");
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
}): Promise<AnalyticsCouts> {
  const params = new URLSearchParams();
  if (options.depuis !== undefined) params.set("depuis", options.depuis);
  if (options.pas !== undefined) params.set("pas", options.pas);
  const requete = params.toString();
  return chargerJson<AnalyticsCouts>(
    `/api/analytics/couts${requete ? `?${requete}` : ""}`,
  );
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

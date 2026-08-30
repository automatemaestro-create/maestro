/**
 * La fiche agent à onglets (#190, lot 1 de #189) : la liste des onglets et la
 * fabrique des chemins qui y mènent.
 *
 * Même contrat que `lib/navigation.ts` pour le menu principal, et pour la même
 * raison : la barre d'onglets, la liste des agents (dont les cartes visent un
 * onglet précis) et les routes `/agents/[nom]/[onglet]` lisent toutes cette
 * liste — ajouter une facette à un agent se fait ici, pas dans trois endroits.
 *
 * Les quatre premiers onglets reprennent les trois anciennes pages —
 * `/catalogue` (profil), `/playbooks` (playbook), `/chat` (chat) — plus les
 * serveurs MCP et les permissions, qui n'avaient jusqu'ici aucune page à eux.
 * Le cinquième, **Logs** (#266), n'en reprend aucune : ce qu'un agent fait ne se
 * lisait que dans le fil global du tableau de bord, tous agents confondus.
 */

import {
  IconeAgent,
  IconeChat,
  IconeJournal,
  IconeMcp,
  IconePlaybooks,
} from "@/components/Icones";
import type { Icone } from "@/components/Primitives";

export type CleOngletAgent = "profil" | "playbook" | "mcp" | "chat" | "logs";

export type OngletAgent = {
  cle: CleOngletAgent;
  /** Libellé de l'onglet — aussi ce que lit un lecteur d'écran. */
  libelle: string;
  /**
   * Le pictogramme de la facette, pris au jeu commun (#245). Décoratif : le
   * `libelle` l'accompagne toujours, ici comme dans le menu latéral.
   */
  icone: Icone;
};

/**
 * L'ordre d'affichage : de l'identité de l'agent à ce qu'il en a fait.
 *
 * Les quatre premiers vont du plus stable au plus vivant — qui il est, ce qu'on
 * lui a appris, ce qu'on lui a permis, ce qu'on lui dit. **Logs ferme la
 * rangée** (#266) pour la même raison que le journal ferme la bascule d'un run
 * (`lib/vuesRun`, #516) : c'est la trace, on l'ouvre en dernier, quand ce que
 * les quatre autres montrent ne suffit plus à comprendre ce qui s'est passé.
 */
export const ONGLETS_AGENT: OngletAgent[] = [
  { cle: "profil", libelle: "Profil", icone: IconeAgent },
  { cle: "playbook", libelle: "Playbook", icone: IconePlaybooks },
  { cle: "mcp", libelle: "MCP & permissions", icone: IconeMcp },
  { cle: "chat", libelle: "Chat", icone: IconeChat },
  { cle: "logs", libelle: "Logs", icone: IconeJournal },
];

/** L'onglet servi quand aucun n'est demandé — `/agents/<nom>` y redirige. */
export const ONGLET_AGENT_DEFAUT: CleOngletAgent = "profil";

/** Vrai si `valeur` désigne un onglet connu (garde des routes dynamiques). */
export function estOngletAgent(
  valeur: string | undefined,
): valeur is CleOngletAgent {
  return ONGLETS_AGENT.some((onglet) => onglet.cle === valeur);
}

/**
 * L'onglet demandé, ou le défaut. Sert aux entrées non maîtrisées — le
 * `?onglet=` d'une redirection depuis `/playbooks`, un segment d'URL saisi à la
 * main : un onglet inconnu retombe sur le profil au lieu de casser la page.
 */
export function ongletAgentOuDefaut(
  valeur: string | string[] | undefined,
): CleOngletAgent {
  const candidat = Array.isArray(valeur) ? valeur[0] : valeur;
  return estOngletAgent(candidat) ? candidat : ONGLET_AGENT_DEFAUT;
}

/** Le chemin de la fiche d'un agent, ouverte sur l'onglet demandé. */
export function cheminOnglet(
  nom: string,
  onglet: CleOngletAgent = ONGLET_AGENT_DEFAUT,
): string {
  return `/agents/${encodeURIComponent(nom)}/${onglet}`;
}

/**
 * L'écran de **création** d'un agent (#254) : une route à part entière, servie
 * par `app/agents/nouveau/`, et non plus un formulaire déplié sous la liste.
 *
 * Il est écrit ici, à côté de `cheminOnglet`, pour la raison qui a fait naître
 * ce module : la liste y mène, l'écran s'y trouve, et un test le confronte au
 * dossier réel — trois endroits qui doivent dire le même chemin.
 */
export const CHEMIN_CREATION_AGENT = "/agents/nouveau";

/**
 * Le segment que cette route occupe **sous** `/agents`, dérivé du chemin plutôt
 * que réécrit : les deux ne peuvent pas diverger.
 */
const SEGMENT_CREATION = CHEMIN_CREATION_AGENT.split("/").filter(Boolean)[1];

/**
 * Vrai si ce nom d'agent est celui que la route de création occupe déjà.
 *
 * ⚠ C'est la contrepartie assumée d'une route **statique** sous `/agents` : un
 * segment fixe l'emporte sur le segment dynamique `[nom]`, donc un agent qui
 * s'appellerait « nouveau » verrait `/agents/nouveau` rendre la création au lieu
 * de sa fiche. Sa fiche resterait atteignable (`cheminOnglet` écrit toujours les
 * trois segments, `/agents/nouveau/profil`), mais l'ambiguïté n'a aucune raison
 * d'être créée : le nom est refusé à la saisie, avec sa cause. Rien n'est
 * rétroactif — un agent déjà nommé ainsi (créé par l'API) n'est pas touché,
 * seule son adresse courte est prise.
 */
export function estNomAgentReserve(nom: string): boolean {
  return nom.trim().toLowerCase() === SEGMENT_CREATION;
}

/**
 * L'onglet que porte un chemin de fiche (`/agents/<nom>/<onglet>`) — ce qui
 * permet à la barre d'onglets de se marquer active sans que la page le lui
 * dise. Hors d'une fiche, ou sur `/agents/<nom>` nu, c'est le défaut.
 */
export function ongletDuChemin(chemin: string): CleOngletAgent {
  const segments = chemin.split("/").filter((segment) => segment !== "");
  return segments[0] === "agents"
    ? ongletAgentOuDefaut(segments[2])
    : ONGLET_AGENT_DEFAUT;
}

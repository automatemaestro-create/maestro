/**
 * La fiche agent à onglets (#190, lot 1 de #189) : la liste des onglets et la
 * fabrique des chemins qui y mènent.
 *
 * Même contrat que `lib/navigation.ts` pour le menu principal, et pour la même
 * raison : la barre d'onglets, la liste des agents (dont les cartes visent un
 * onglet précis) et les routes `/agents/[nom]/[onglet]` lisent toutes cette
 * liste — ajouter une facette à un agent se fait ici, pas dans trois endroits.
 *
 * Les quatre onglets reprennent les trois anciennes pages — `/catalogue`
 * (profil), `/playbooks` (playbook), `/chat` (chat) — plus les serveurs MCP et
 * les permissions, qui n'avaient jusqu'ici aucune page à eux.
 */

import {
  IconeAgent,
  IconeChat,
  IconeMcp,
  IconePlaybooks,
} from "@/components/Icones";
import type { Icone } from "@/components/Primitives";

export type CleOngletAgent = "profil" | "playbook" | "mcp" | "chat";

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

/** L'ordre d'affichage : de l'identité de l'agent à la conversation avec lui. */
export const ONGLETS_AGENT: OngletAgent[] = [
  { cle: "profil", libelle: "Profil", icone: IconeAgent },
  { cle: "playbook", libelle: "Playbook", icone: IconePlaybooks },
  { cle: "mcp", libelle: "MCP & permissions", icone: IconeMcp },
  { cle: "chat", libelle: "Chat", icone: IconeChat },
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

/**
 * « Qui utilise cette intégration ? » (#270) — la question que l'écran pose au
 * pool, et à laquelle rien ne répondait.
 *
 * L'index de l'API est **unidirectionnel** : `core/mcp/activations.json` est
 * rangé par agent (`{"<agent>": ["figma-officiel", …]}`) et chaque fiche du
 * catalogue en rend sa part dans `mcp_activations`. Il n'existe aucune route
 * « quels agents ont activé X » — le seul balayage inverse du backend est écrit
 * en dur dans le `DELETE /api/mcp/pool/{id}`, pour désactiver l'intégration
 * partout.
 *
 * On le renverse donc **ici**, sur `GET /api/catalogue` — que la Control Tower
 * appelle déjà pour la liste des agents et pour les Paramètres, et qui porte
 * `mcp_activations` sur chaque fiche depuis #133. Un seul aller, aucune route
 * nouvelle : le lot n'a pas eu à toucher au backend.
 *
 * ⚠ **« Je ne sais pas » n'est pas « personne ».** Le catalogue est une source
 * *secondaire* de cet écran — le pool se lit sans lui. S'il ne répond pas, dire
 * « aucun agent ne l'utilise » serait un contresens exactement sur la question
 * que le ticket pose : on nomme l'ignorance (`connu: false`) au lieu de la
 * rendre en silence sous la forme d'une liste vide.
 */

import type { AgentCatalogue } from "@/lib/types";

export type UsageDuPool = {
  /**
   * Les agents qui ont activé chaque intégration, par id du pool. Un id absent
   * de la table veut dire « aucun agent », et se lit ainsi **seulement** si
   * `connu` vaut `true`.
   */
  parIntegration: Map<string, AgentCatalogue[]>;
  /** Le nombre d'agents qui ont activé au moins une intégration. */
  agentsEquipes: number;
  /** Le nombre d'agents du catalogue, équipés ou non. */
  agents: number;
  /**
   * Le catalogue a-t-il pu être lu ? À `false`, tout le reste de cet objet est
   * vide et ne dit rien — surtout pas « personne n'utilise rien ».
   */
  connu: boolean;
};

/** Ce qu'on sait quand le catalogue n'a pas répondu : rien, et on le dit. */
export const USAGE_INCONNU: UsageDuPool = {
  parIntegration: new Map(),
  agentsEquipes: 0,
  agents: 0,
  connu: false,
};

/**
 * Renverse le catalogue : de « cet agent a activé ces intégrations » vers
 * « cette intégration est activée par ces agents ».
 *
 * L'ordre des agents suit celui du catalogue (les agents par défaut du code,
 * puis les personnalisés) et non un tri à nous : deux intégrations voisines
 * doivent nommer leurs agents dans le même ordre, sans quoi la même liste se
 * lit différemment d'une ligne à l'autre.
 */
export function usageDuPool(fiches: AgentCatalogue[]): UsageDuPool {
  const parIntegration = new Map<string, AgentCatalogue[]>();
  let agentsEquipes = 0;
  for (const fiche of fiches) {
    if (fiche.mcp_activations.length > 0) agentsEquipes += 1;
    for (const id of fiche.mcp_activations) {
      const deja = parIntegration.get(id);
      if (deja === undefined) parIntegration.set(id, [fiche]);
      else deja.push(fiche);
    }
  }
  return {
    parIntegration,
    agentsEquipes,
    agents: fiches.length,
    connu: true,
  };
}

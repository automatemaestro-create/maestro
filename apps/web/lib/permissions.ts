/**
 * Ce que l'écran doit savoir d'une politique de permissions pour la faire
 * éditer (#262, lot 10/15 de #243) — la règle de **portée** d'une entrée, et
 * rien d'autre.
 *
 * Elle vit ici plutôt que dans le composant parce qu'elle ne se voit ni au
 * lint, ni au typage, ni à l'écran : une entrée `mcp__slack__send_message`
 * n'est pas « inconnue » sous un serveur `mcp__slack` exposé, et
 * `mcp__slackbot` n'est pas couvert par `mcp__slack` — le préfixe ne vaut
 * **qu'aux frontières `__`**. C'est le pendant exact de `_correspond`
 * (`maestro/agents/permissions.py`), qui décide du même appariement à
 * l'exécution ; les deux doivent dire la même chose, et une règle recopiée dans
 * du JSX ne se compare à rien.
 *
 * ⚠ Rien ici ne **refuse** quoi que ce soit : c'est ce qui distingue ce module
 * de la validation, qui vit côté API et là seulement (une seule définition de
 * « entrée admissible », celle qu'applique le moteur). Ce qu'il produit est un
 * **signalement** — le même parti que les compétences inédites (#256) : on
 * suggère, on marque ce qui sort du connu, on n'interdit pas. Un outil peut
 * très bien être visé avant d'exister.
 */

import type { OutilExpose } from "@/lib/types";

/**
 * `entree` couvre-t-elle `outil` ? Nom exact, ou préfixe **à une frontière
 * `__`** — jamais en plein mot.
 */
export function couvre(entree: string, outil: string): boolean {
  return outil === entree || outil.startsWith(`${entree}__`);
}

/**
 * Une entrée de politique se rattache-t-elle à un outil que l'agent expose ?
 *
 * Vraie dans les deux sens, et c'est le point : l'entrée peut **couvrir** un
 * outil exposé (`mcp__figma` cité pour `mcp__figma__get_file`) ou être
 * **couverte** par lui (`mcp__figma__get_file` cité sous le serveur
 * `mcp__figma`, seule forme que la fiche suggère). Ne regarder qu'un sens
 * marquerait comme inconnue la moitié des entrées légitimes.
 */
export function entreeConnue(entree: string, outils: readonly OutilExpose[]): boolean {
  return outils.some(
    ({ nom }) => couvre(entree, nom) || couvre(nom, entree),
  );
}

/**
 * Les entrées d'une liste que rien d'exposé n'explique — à marquer, jamais à
 * refuser : un serveur MCP désactivé depuis, un outil à venir, une faute de
 * frappe se ressemblent ici, et seule la dernière est un défaut.
 */
export function entreesHorsPortee(
  entrees: readonly string[],
  outils: readonly OutilExpose[],
): Set<string> {
  return new Set(entrees.filter((entree) => !entreeConnue(entree, outils)));
}

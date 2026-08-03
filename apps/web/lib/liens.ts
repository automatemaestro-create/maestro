/**
 * Le traitement des URL qui arrivent par le flux (#192).
 *
 * Une URL portée par une tâche n'est pas écrite par l'UI : elle vient du plan
 * d'un run ou d'un agent équipé du serveur MCP de l'outil de ticketing. C'est
 * donc une **donnée non fiable**, au même titre qu'un titre de tâche — sauf
 * qu'un `href` non filtré exécute du code (`javascript:`) ou embarque une
 * charge utile (`data:`). Un seul point de passage la valide avant qu'elle
 * touche un attribut de lien.
 */

/** Les seuls schémas qu'un lien de l'UI accepte de suivre. */
const SCHEMAS_SUIVIS = new Set(["http:", "https:"]);

/**
 * L'URL absolue à poser sur un `href`, ou `null` si elle n'est pas suivable —
 * schéma inattendu, adresse relative, chaîne illisible ou vide. On rend la
 * forme **normalisée** par l'analyseur (`href`) plutôt que la chaîne d'origine :
 * espaces, tabulations et retours à la ligne insérés pour tromper la lecture
 * disparaissent au passage.
 */
export function lienExterneSur(url: string | null | undefined): string | null {
  if (!url) return null;
  let analysee: URL;
  try {
    analysee = new URL(url);
  } catch {
    return null;
  }
  return SCHEMAS_SUIVIS.has(analysee.protocol) ? analysee.href : null;
}

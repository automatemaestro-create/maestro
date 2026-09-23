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

/**
 * Les trois formes d'un chemin **absolu du poste** — et seulement absolu.
 *
 * `C:\dossier`, `C:/dossier`, `\\serveur\partage\…`, `/home/…`. Un chemin
 * relatif n'en est pas : il serait résolu contre on ne sait quoi, et la coque
 * le refuse de toute façon (`apps/desktop/main.js`).
 *
 * ⚠ La forme Windows se teste **avant** toute lecture d'URL : `C:` ressemble à
 * un schéma, et `new URL("C:\\x")` n'échoue pas partout. C'est pourquoi ce
 * module ne passe pas par `URL` ici — la question n'est pas « est-ce une
 * adresse ? » mais « est-ce un chemin du disque ? ».
 */
const CHEMIN_WINDOWS = /^[A-Za-z]:[\\/]/;
const CHEMIN_UNC = /^\\\\[^\\/]/;
const CHEMIN_POSIX = /^\/[^/]/;

/**
 * Le chemin de fichier à poser sur un geste, ou `null` si ce n'en est pas un (#1224).
 *
 * Le pendant de `lienExterneSur` pour ce qui **ne sort pas** : un fichier du
 * livrable d'un run, que le récit de fin nomme pour qu'on puisse l'ouvrir d'un
 * geste. Les deux sont exclusifs et se demandent dans cet ordre — une adresse
 * `http(s)` est un lien, tout le reste n'est un chemin que s'il est absolu.
 *
 * ⚠ **Ceci n'autorise rien.** Le chemin rendu n'est pas canonicalisé et n'a
 * franchi aucune frontière : c'est la coque qui décide ce qu'elle en fait, et
 * elle ne fait que le **montrer** dans l'explorateur — jamais l'ouvrir avec son
 * application par défaut, ce qui reviendrait à exécuter un `.exe` (`lib/poste`).
 * Le texte d'où il vient est produit par un modèle, donc non fiable ; la
 * validation vit là où le geste a lieu, en un seul endroit.
 *
 * Les caractères de contrôle sont écartés : insérés dans un chemin, ils
 * tromperaient la lecture de ce qu'on s'apprête à ouvrir — même raison que la
 * normalisation de `lienExterneSur`.
 */
export function cheminLocalSur(cible: string | null | undefined): string | null {
  const brut = (cible ?? "").trim();
  if (brut === "") return null;
  if (/[\u0000-\u001f\u007f]/.test(brut)) return null;
  const absolu =
    CHEMIN_WINDOWS.test(brut) || CHEMIN_UNC.test(brut) || CHEMIN_POSIX.test(brut);
  return absolu ? brut : null;
}

/**
 * Le nom du fichier au bout d'un chemin — ce qu'un geste nomme quand le libellé
 * manque.
 *
 * Les deux séparateurs, parce que le chemin vient du poste et non du
 * navigateur : un `C:\p\app.py` lu sous Linux n'en reste pas moins un chemin de
 * Windows. Rend le chemin entier s'il n'a aucun séparateur utile — mieux vaut
 * un libellé long qu'un libellé vide.
 */
export function nomDuFichier(chemin: string): string {
  const segments = chemin.split(/[\\/]/).filter((segment) => segment !== "");
  return segments[segments.length - 1] ?? chemin;
}

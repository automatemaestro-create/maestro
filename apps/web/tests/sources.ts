/**
 * Le périmètre du produit et la lecture de ses sources — partagés par les
 * balayages qui jugent ce qui est **écrit** dans les écrans plutôt que ce
 * qu'ils rendent : `a11y.test.tsx` (la garde de mouvement, les contrôles de
 * saisie), `couleurs.test.ts` (les paires de couleur écrites à la main, #895)
 * et `langue-du-produit.test.ts` (ce que le produit dit du dépôt, #939).
 *
 * Ce qui est partagé ici est le **périmètre**, jamais un verdict : chaque suite
 * garde son motif et sa table. Deux parcours recopiés seraient le premier moyen
 * qu'un balayage juge un produit que l'autre ne voit plus — un dossier ajouté à
 * côté d'`app/` et de `components/` entrerait dans l'un et pas dans l'autre.
 * C'est la raison qui a déjà mis les dix écrans dans `./ecrans` (#539).
 *
 * `hydratation.test.ts` ne s'y replie **pas**, et c'est délibéré : il juge un
 * fichier **nommé** (`app/layout.tsx`) et non le périmètre du produit, et sa
 * variante de `sansCommentaires` retire en plus les commentaires JSX en
 * accolades, dont il a besoin et dont ces deux balayages-ci n'ont que faire.
 */

import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

/**
 * ⚠ Le chemin se construit en deux temps, et surtout **pas** avec la tournure
 * `new URL("../app", import.meta.url)`, qui est pourtant celle qu'on écrit
 * d'ordinaire en ESM : Vite la reconnaît **statiquement** et la réécrit en
 * référence d'**asset**, si bien qu'à l'exécution la base n'est plus le fichier
 * mais `self.location` — donc une URL `http:`, et `fileURLToPath` sort sur
 * « The URL must be of scheme file » (piège documenté dans `contraste.test.ts`).
 */
const RACINE = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");

/** Une source du produit, lue depuis la racine d'`apps/web`. */
export const lireSource = (relatif: string): string =>
  readFileSync(path.join(RACINE, relatif), "utf8");

/**
 * Une source sans ses commentaires. Les balayages de ce dépôt lisent des
 * **chaînes**, pas des lignes, et la prose ferait le gros du résultat sans ce
 * filtre : `a11y.test.tsx` parle de « transition » en français, #832 cite
 * `focus:border-emerald-500` en toutes lettres dans le commentaire qui explique
 * pourquoi il n'y est plus, et #895 en cite une dizaine pour la même raison.
 *
 * Le second motif épargne le `//` d'une URL (`https://…`), qui n'ouvre aucun
 * commentaire.
 */
export function sansCommentaires(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, " ")
    .replace(/(^|[^:])\/\/[^\n]*/g, "$1");
}

/**
 * Tout ce que le produit rend : les écrans (`app/`) et les composants. La liste
 * est **parcourue** et non écrite — un fichier neuf entre dans le périmètre le
 * jour où il est créé, ce qui est la seule façon qu'un balayage reste vrai.
 *
 * L'extension est un paramètre parce que les deux appelants ne cherchent pas la
 * même chose au même endroit : le mouvement et les contrôles de saisie vivent
 * dans du JSX (`.tsx`), une feuille de classes peut vivre dans un `.ts` de
 * constantes. Le défaut reste `.tsx` pour que le périmètre d'`a11y.test.tsx` ne
 * bouge pas d'un fichier en changeant de source.
 */
export function sourcesDuProduit(
  extensions: readonly string[] = [".tsx"],
  dossiers: readonly string[] = DOSSIERS_RENDUS,
): string[] {
  return dossiers.flatMap((dossier) =>
    readdirSync(path.join(RACINE, dossier), { recursive: true })
      .map(String)
      .filter((fichier) => extensions.some((ext) => fichier.endsWith(ext)))
      .map((fichier) => path.posix.join(dossier, fichier.split(path.sep).join("/"))),
  );
}

/** Ce qui **rend** : les écrans et les composants. Le périmètre par défaut. */
export const DOSSIERS_RENDUS = ["app", "components"] as const;

/**
 * Le périmètre élargi à `lib/`, pour les balayages qui jugent le **texte** plutôt
 * que les classes (#939 : la langue du produit). Une phrase affichée y vit aussi —
 * les descriptions des sections de Paramètres (`lib/parametres.ts`), les messages
 * d'erreur de `lib/projets.ts`, les libellés de `lib/detailTache.ts` — et un
 * balayage qui s'arrêterait à `app/` + `components/` laisserait ces phrases-là
 * hors de portée.
 *
 * Il reste **séparé** du périmètre par défaut, et ce n'est pas une recopie : les
 * sondes de couleur et de mouvement jugent ce que le produit **peint**, ce qui
 * n'arrive que dans du JSX. Élargir le leur y ferait entrer des fichiers qu'aucune
 * de leurs mesures n'a jugés.
 */
export const DOSSIERS_AFFICHANT_DU_TEXTE = ["app", "components", "lib"] as const;

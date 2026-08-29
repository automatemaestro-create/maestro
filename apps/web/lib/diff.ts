/**
 * Différentiel ligne à ligne entre deux textes (#261).
 *
 * L'assistant de rédaction rend un playbook **intégral**, jamais un diff (c'est
 * le contrat de `POST /api/playbooks/{agent}/redaction`, comme celui de la
 * proposition d'après-run) : le différentiel est donc calculé **ici**, au moment
 * de montrer à l'utilisateur ce qui changerait s'il appliquait la réécriture à
 * son brouillon.
 *
 * ⚠ **Aucune dépendance ajoutée.** Le front ne dépend aujourd'hui que de Next et
 * React ; tirer une bibliothèque de diff serait la première dépendance runtime
 * en plus depuis, pour un algorithme qui tient en quarante lignes sur la seule
 * granularité dont on ait besoin — la ligne. Un playbook est un document
 * Markdown de structure, pas de la prose serrée : ce qui s'y voit est une
 * section ajoutée, une puce reformulée, un garde-fou retiré.
 *
 * La comparaison est une plus longue sous-séquence commune (LCS) classique, en
 * O(n·m). Sur des documents de quelques centaines de lignes c'est instantané ;
 * au-delà de `PLAFOND_LIGNES` on ne calcule rien et on rend un remplacement en
 * bloc, plutôt que de figer l'onglet sur un document que personne n'écrira à la
 * main.
 */

/** Ce qu'une ligne devient dans le passage de l'ancien texte au nouveau. */
export type TypeLigneDiff = "commun" | "ajout" | "retrait";

/** Une ligne du différentiel, avec son sort et son texte. */
export type LigneDiff = {
  type: TypeLigneDiff;
  texte: string;
};

/**
 * Une plage de lignes inchangées, repliée parce qu'elle est trop longue pour
 * apprendre quoi que ce soit — un playbook réécrit garde l'essentiel de son
 * texte, et le montrer en entier noierait les cinq lignes qui changent.
 */
export type PlageRepliee = {
  type: "repli";
  lignes: number;
};

/** Ce que le rendu affiche : des lignes, et des plages communes repliées. */
export type EntreeDiff = LigneDiff | PlageRepliee;

/**
 * Au-delà de ce nombre de lignes d'un côté ou de l'autre, on renonce au calcul
 * exact (O(n·m) devient sensible) et on rend un remplacement en bloc.
 */
export const PLAFOND_LIGNES = 2000;

/**
 * Nombre de lignes communes conservées de chaque côté d'un changement. Deux
 * suffisent à situer une modification dans un document à sections ; au-delà on
 * relit le playbook plutôt qu'on ne lit un diff.
 */
export const CONTEXTE = 2;

/**
 * Une plage commune n'est repliée que si elle dépasse cette longueur : replier
 * trois lignes pour en afficher une de repli ne gagne rien et hache la lecture.
 */
const REPLI_MINIMUM = 2 * CONTEXTE + 2;

/**
 * Le différentiel ligne à ligne de `avant` vers `apres`.
 *
 * Les lignes communes sont rendues telles quelles, les disparues en `retrait`,
 * les nouvelles en `ajout`. Une ligne modifiée apparaît donc comme un retrait
 * suivi d'un ajout : c'est la lecture juste pour un document de structure, où
 * « cette phrase a bougé » n'a pas de sens hors de sa section.
 */
export function differencier(avant: string, apres: string): LigneDiff[] {
  const a = avant.split("\n");
  const b = apres.split("\n");
  if (a.length > PLAFOND_LIGNES || b.length > PLAFOND_LIGNES) {
    return [
      ...a.map((texte): LigneDiff => ({ type: "retrait", texte })),
      ...b.map((texte): LigneDiff => ({ type: "ajout", texte })),
    ];
  }
  return depuisTable(a, b, longueursCommunes(a, b));
}

/** Combien de lignes le différentiel ajoute et retire — le résumé d'un coup d'œil. */
export function compter(lignes: LigneDiff[]): { ajouts: number; retraits: number } {
  return {
    ajouts: lignes.filter((l) => l.type === "ajout").length,
    retraits: lignes.filter((l) => l.type === "retrait").length,
  };
}

/**
 * Replie les longues plages inchangées, en gardant `CONTEXTE` lignes de part et
 * d'autre de chaque changement.
 *
 * Un différentiel qui n'a **que** des lignes communes n'est pas replié : c'est
 * le cas « le modèle n'a rien changé », qu'il vaut mieux montrer tel quel que
 * réduire à un « ⋯ 90 lignes inchangées » qui ressemble à une panne.
 */
export function condenser(lignes: LigneDiff[]): EntreeDiff[] {
  if (!lignes.some((l) => l.type !== "commun")) return lignes;
  const garde: boolean[] = new Array(lignes.length).fill(false);
  lignes.forEach((ligne, i) => {
    if (ligne.type === "commun") return;
    const debut = Math.max(0, i - CONTEXTE);
    const fin = Math.min(lignes.length - 1, i + CONTEXTE);
    for (let j = debut; j <= fin; j += 1) garde[j] = true;
  });
  const entrees: EntreeDiff[] = [];
  let tampon: LigneDiff[] = [];
  const vider = () => {
    if (tampon.length === 0) return;
    // Sous le minimum, replier coûterait une ligne pour en cacher trois : on
    // rend les lignes telles quelles plutôt que de hacher la lecture.
    if (tampon.length >= REPLI_MINIMUM) {
      entrees.push({ type: "repli", lignes: tampon.length });
    } else {
      entrees.push(...tampon);
    }
    tampon = [];
  };
  lignes.forEach((ligne, i) => {
    if (garde[i]) {
      vider();
      entrees.push(ligne);
    } else {
      tampon.push(ligne);
    }
  });
  vider();
  return entrees;
}

/**
 * La table des longueurs de plus longue sous-séquence commune, remplie depuis
 * la fin : `table[i][j]` est la longueur de la LCS de `a[i:]` et `b[j:]`.
 */
function longueursCommunes(a: string[], b: string[]): Uint32Array[] {
  const table: Uint32Array[] = Array.from(
    { length: a.length + 1 },
    () => new Uint32Array(b.length + 1),
  );
  for (let i = a.length - 1; i >= 0; i -= 1) {
    for (let j = b.length - 1; j >= 0; j -= 1) {
      table[i][j] =
        a[i] === b[j]
          ? table[i + 1][j + 1] + 1
          : Math.max(table[i + 1][j], table[i][j + 1]);
    }
  }
  return table;
}

/**
 * Le parcours de la table, du début vers la fin. À égalité de longueur on sort
 * le **retrait** avant l'ajout : une ligne réécrite se lit « voici ce qui était
 * là, voici ce qui le remplace », dans cet ordre.
 */
function depuisTable(a: string[], b: string[], table: Uint32Array[]): LigneDiff[] {
  const lignes: LigneDiff[] = [];
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      lignes.push({ type: "commun", texte: a[i] });
      i += 1;
      j += 1;
    } else if (table[i + 1][j] >= table[i][j + 1]) {
      lignes.push({ type: "retrait", texte: a[i] });
      i += 1;
    } else {
      lignes.push({ type: "ajout", texte: b[j] });
      j += 1;
    }
  }
  while (i < a.length) {
    lignes.push({ type: "retrait", texte: a[i] });
    i += 1;
  }
  while (j < b.length) {
    lignes.push({ type: "ajout", texte: b[j] });
    j += 1;
  }
  return lignes;
}

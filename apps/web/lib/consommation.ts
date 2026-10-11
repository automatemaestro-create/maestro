/**
 * Ce qu'une tâche a consommé, dit pour l'écran (#1398).
 *
 * La carte du Kanban affichait `tokens_total` seul : la **somme des contextes de
 * chacun de ses tours**, cache relu compris. Sur p5, `socle-nextjs` y montrait
 * 1 845 438 tokens pour un contexte qui n'a jamais dépassé ≈ 66 k et un coût de
 * 1,87 $ — et la personne y lisait, à raison, une tâche qui consommait trop. Le
 * total n'était pas faux, c'est sa lecture seule qui trompait.
 *
 * Le choix d'écran est consigné sur le ticket (`## Variante retenue`, variante C) :
 *
 * - la **carte** dit le contexte atteint, à la place exacte de l'ancien chiffre,
 *   et le coût garde la sienne (d'après la barre d'état de Claude Code, qui suit
 *   le contexte du dernier appel et non le cumul) ;
 * - le **détail** de la tâche ventile : contexte, entrée — dont le cache relu et
 *   le cache écrit, en sous-lignes —, sortie, total traité, coût (d'après
 *   LangSmith, où « cache read » est une sous-ligne en retrait de l'entrée, et
 *   Langfuse, où la ventilation vit sur la vue de détail de l'élément) ;
 * - un contexte **inconnu** ne se fabrique pas : sans mesure (ligne d'avant ce
 *   ticket, fournisseur qui ne dit rien par appel), la carte retombe sur le
 *   volume, **nommé** « tokens traités ».
 *
 * Aucune règle de calcul ici : contexte, parts de cache et total arrivent du
 * backend (`maestro/telemetry/usage.py`). Ce module ne fait que les nommer.
 */

import { formatTokens } from "@/lib/format";
import type { Usage } from "@/lib/types";

/** Le contexte atteint, quand il a été mesuré — `null` sinon (inconnu ≠ zéro). */
export function contexteAtteint(usage: Usage): number | null {
  return usage.contexte_max ?? null;
}

/**
 * Le chiffre de la carte : « contexte 16 074 tokens », ou — contexte inconnu —
 * « 90 585 tokens traités », jamais un total qui se lirait comme une consommation.
 */
export function libelleConsommation(usage: Usage): string {
  const contexte = contexteAtteint(usage);
  return contexte !== null
    ? `contexte ${formatTokens(contexte)} tokens`
    : `${formatTokens(usage.tokens_total)} tokens traités`;
}

/**
 * L'infobulle de la carte : le volume traité et ses tours, **en une ligne** pas
 * plus longue que celle d'avant ce ticket (« N tokens en entrée / M en sortie »).
 *
 * La ventilation — et la part relue en cache — vit dans le détail de la tâche.
 * Ouverte à l'intérieur d'une colonne du Kanban qui défile, une bulle plus haute
 * est rognée : la variante A (ventilation entière) l'a été sur capture, et une
 * phrase de trois lignes l'a été à la relecture visuelle. Une ligne courte rogne
 * comme celle d'avant, pas davantage — le défaut de la primitive dans une colonne
 * qui défile est antérieur à ce ticket.
 */
export function resumeConsommation(usage: Usage): string {
  const contexte = contexteAtteint(usage);
  // Sans contexte mesuré, la carte dit déjà le volume : la bulle rend alors la
  // ventilation d'avant ce ticket, entrée et sortie.
  if (contexte === null) {
    return `${formatTokens(usage.tokens_entree)} tokens en entrée / ${formatTokens(usage.tokens_sortie)} en sortie`;
  }
  const tours = usage.tours ? ` en ${usage.tours} tour${usage.tours > 1 ? "s" : ""}` : "";
  return `${formatTokens(usage.tokens_total)} tokens traités${tours}`;
}

/** Une ligne de la ventilation : son nom, sa valeur, et si c'est une part de la ligne d'au-dessus. */
export type LigneConsommation = {
  libelle: string;
  valeur: string;
  part: boolean;
};

/**
 * La ventilation du détail, ligne à ligne, dans l'ordre de lecture : ce qu'on suit
 * (le contexte), ce qui est entré — et, en parts, ce que le cache en a servi —, ce
 * qui est sorti, puis le volume traité qui additionne les tours.
 *
 * Les deux parts de cache ne s'écrivent que si le backend les a rapportées : une
 * mesure d'avant #1398 n'en porte pas, et « 0 relu en cache » y serait faux.
 */
export function lignesConsommation(usage: Usage): LigneConsommation[] {
  const contexte = contexteAtteint(usage);
  const lignes: LigneConsommation[] = [
    {
      libelle: "Contexte atteint",
      valeur: contexte !== null ? formatTokens(contexte) : "non mesuré",
      part: false,
    },
    { libelle: "Entrée", valeur: formatTokens(usage.tokens_entree), part: false },
  ];
  if (usage.tokens_cache_lus !== undefined) {
    lignes.push({
      libelle: "dont relu en cache",
      valeur: formatTokens(usage.tokens_cache_lus),
      part: true,
    });
  }
  if (usage.tokens_cache_ecrits !== undefined) {
    lignes.push({
      libelle: "dont écrit en cache",
      valeur: formatTokens(usage.tokens_cache_ecrits),
      part: true,
    });
  }
  lignes.push(
    { libelle: "Sortie", valeur: formatTokens(usage.tokens_sortie), part: false },
    {
      libelle: usage.tours
        ? `Traités en ${usage.tours} tour${usage.tours > 1 ? "s" : ""}`
        : "Traités",
      valeur: formatTokens(usage.tokens_total),
      part: false,
    },
  );
  return lignes;
}

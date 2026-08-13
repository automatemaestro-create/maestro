/**
 * L'**ordre de grandeur de ce qui suit** un brief approuvé (#322, critère 3).
 *
 * Le point de contrôle du brief (décision D5, #218) n'est rentable que si celui
 * qui décide voit les deux montants qu'il compare : ce qui est **déjà dépensé**
 * — que ni un accord ni un refus ne rendront — et ce que l'accord **engage**. Le
 * premier est mesuré (le grand livre du run, #57) ; le second ne peut pas
 * l'être, puisqu'il dépend d'une décomposition qui n'a pas eu lieu. C'est
 * exactement pourquoi il est ici, chiffré et sourcé, plutôt que tu par prudence :
 * sans lui, un refus reste **timide** au lieu d'être **rationnel** — on n'ose pas
 * jeter ce qu'on a payé sans savoir ce qu'on économise.
 *
 * Les chiffres viennent de [docs/09 §4.3](../../../docs/09-exemple-chiffre.md),
 * l'estimation détaillée d'une fonctionnalité complète, et **d'aucune mesure de
 * ce run-ci** — c'est une référence, pas une prévision. Ils y sont eux-mêmes
 * annoncés comme des ordres de grandeur : le coût réel dépend de la taille du
 * code, du nombre de relances et du cache de prompts. L'écran les rend donc en
 * **fourchette**, jamais en montant unique, et le dit.
 *
 * Deux limites assumées, à connaître avant de raffiner :
 *
 * - **le nombre de tâches est inconnu** avant la décomposition, par construction.
 *   Le meilleur indice que porte le brief est son nombre de **critères
 *   d'acceptation** : c'est le grain auquel il énumère ce qu'il faut livrer, et
 *   c'est ce que le Chef de projet découpera. L'exemple de docs/09 le confirme à
 *   l'échelle (une fonctionnalité complète → 7 tickets) sans le prouver ;
 * - **sur abonnement, ces dollars n'en sont pas** (docs/09 §4.1) : un run consomme
 *   une part du budget d'usage de la fenêtre de 5 h. La fourchette reste le bon
 *   ordre de grandeur de l'**effort**, et l'écran le précise plutôt que de faire
 *   croire à une facture.
 */

import type { Brief } from "./types";

/**
 * Le découpage lui-même (le Chef de projet, en Opus) — docs/09 §4.3, ligne
 * « Découpage + synthèse ». C'est le seul poste qu'un accord engage à coup sûr,
 * quel que soit ce que la décomposition trouve ensuite.
 */
export const COUT_DECOMPOSITION_USD = 0.8;

/**
 * Ce que coûte une tâche, borne basse et borne haute — docs/09 §4.3, les sept
 * lignes Sonnet de l'exemple (0,74 $ pour une spec d'écran, 1,40 $ pour des
 * endpoints back-end). L'écart entre les deux **est** l'information : il dit
 * qu'une tâche coûte « autour d'un dollar », pas « 1,03 $ ».
 */
export const COUT_TACHE_USD_BAS = 0.74;
export const COUT_TACHE_USD_HAUT = 1.4;

/**
 * La marge de relance portée sur la **borne haute seule** — docs/09 §4.3, où le
 * sous-total de 7,0 $ devient « ≈ 9 $ » une fois les allers-retours QA → Dev
 * comptés, soit ≈ +30 %. Sur la borne basse elle n'a rien à faire : la borne
 * basse est le cas où rien n'est repris.
 */
export const MARGE_RELANCES = 1.3;

/**
 * Le plancher du nombre de tâches. Un brief à un seul critère d'acceptation ne
 * produit pas un run à une tâche : il en faut au moins de quoi faire, vérifier et
 * livrer. Trois est le plus petit compte qui ne mente pas par optimisme.
 */
export const NB_TACHES_PLANCHER = 3;

/**
 * Ce qu'un accord engage, en ordre de grandeur : le nombre de tâches attendu et
 * la fourchette de dépense qui va avec, décomposition comprise.
 *
 * `nb_taches` est une **estimation**, pas une promesse — l'écran qui la rend doit
 * le dire, et c'est pour cela qu'elle sort d'ici plutôt que d'être calculée dans
 * un composant : la formule et sa source vivent au même endroit que le
 * commentaire qui les justifie.
 */
export type EstimationSuite = {
  /** Tâches attendues à la décomposition (plancher appliqué). */
  nbTaches: number;
  /** Borne basse, en dollars : la décomposition et des tâches sans reprise. */
  bas: number;
  /** Borne haute, en dollars : les tâches les plus chères, relances comprises. */
  haut: number;
};

/**
 * Estime ce que coûterait la suite si ce brief était approuvé tel qu'il est.
 *
 * Le brief passé est celui **qu'on s'apprête à approuver** — corrigé compris :
 * retirer trois critères d'acceptation à la relecture doit faire baisser
 * l'estimation sous les yeux de celui qui les retire. C'est ce qui en fait un
 * outil de décision et pas une étiquette de prix.
 */
export function estimerSuite(brief: Brief): EstimationSuite {
  const nbTaches = Math.max(
    NB_TACHES_PLANCHER,
    brief.criteres_acceptation.length,
  );
  return {
    nbTaches,
    bas: COUT_DECOMPOSITION_USD + nbTaches * COUT_TACHE_USD_BAS,
    haut:
      COUT_DECOMPOSITION_USD + nbTaches * COUT_TACHE_USD_HAUT * MARGE_RELANCES,
  };
}

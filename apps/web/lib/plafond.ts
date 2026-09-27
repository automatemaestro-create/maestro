/**
 * Ce que l'UI sait d'un run **arrêté sur son plafond de dépense** hors du JSX
 * (#1182) : lesquels attendent une décision, quel plafond a été franchi, ce que
 * coûterait le reste, quel plafond proposer, et quelle décision envoyer.
 *
 * Ces règles vivent ici et pas dans la carte pour la raison habituelle du dépôt
 * (`lib/brief`, `lib/questions`) : la carte du fil les applique, la table des
 * attentes d'un run (`components/runs/EtatRun`) et la cloche posent la même
 * question — « ce run attend-il une décision sur son budget ? » —, et deux
 * formulations finiraient par diverger.
 *
 * ## Ce qui ne bouge pas
 *
 * **Le plafond posé par la personne reste un plafond** : rien ici ne le relève
 * sans elle. Le plafond proposé n'est qu'un préremplissage — la dépense, plus la
 * borne haute de l'estimation du reste —, que la personne lit sur le bouton avant
 * de le cliquer. Et le coût du reste est **l'estimation du brief**
 * (`estimerReste`, `lib/estimation`), jamais une autre.
 */

import { estimerReste, type EstimationSuite } from "./estimation";
import {
  EXECUTION_EN_ATTENTE_PLAFOND,
  GESTE_REDUIRE,
  GESTE_RELEVER,
  type DecisionPlafond,
  type DemandePlafond,
  type ResumeExecution,
} from "./types";

/**
 * Le **libellé de menu** de la page où se tranche un plafond — celle du fil,
 * comme `PAGE_DES_QUESTIONS` (`lib/questions`) et pour la même raison : un libellé
 * et non un chemin (#191), et une constante à elle parce que la question
 * (« où se décide un budget ? ») pourrait un jour cesser d'avoir la même réponse.
 */
export const PAGE_DU_PLAFOND = "Chat";

/** Un run qui attend une décision sur son budget, avec la question qu'il pose. */
export type RunAuPlafond = ResumeExecution & { plafond: DemandePlafond };

/** Ce run est-il arrêté sur son plafond, question comprise ? */
export function estAuPlafond(execution: ResumeExecution): execution is RunAuPlafond {
  return (
    execution.statut === EXECUTION_EN_ATTENTE_PLAFOND &&
    execution.plafond !== null &&
    execution.plafond !== undefined
  );
}

/**
 * Les runs qui attendent une décision sur leur budget, **le plus ancien d'abord**
 * — l'ancienneté est la moitié du signal, comme pour `runsEnAttente` (`lib/brief`).
 */
export function runsAuPlafond(executions: ResumeExecution[]): RunAuPlafond[] {
  return executions
    .filter(estAuPlafond)
    .slice()
    .sort((a, b) => {
      const [ga, gb] = [a.attente_depuis || a.debut, b.attente_depuis || b.debut];
      return ga < gb ? -1 : ga > gb ? 1 : 0;
    });
}

/** L'unité d'un plafond : en dollars quand le fournisseur tarifie, en tokens sinon. */
export type UnitePlafond = "usd" | "tokens";

/**
 * Les plafonds que la dépense **atteint** — ceux qu'une reprise doit relever.
 *
 * Le plus souvent un seul : le plafond en dollars quand il est posé et que la
 * dépense est tarifée, sinon celui en tokens (#113 — le seul qui ait prise sur un
 * fournisseur qui ne tarifie pas). Les deux quand la dépense les atteint tous les
 * deux : relever l'un laisserait le run retomber sur l'autre à sa première mesure.
 */
export function unitesFranchies(demande: DemandePlafond): UnitePlafond[] {
  const unites: UnitePlafond[] = [];
  if (
    demande.plafond_cout_usd !== null &&
    demande.depense_usd !== null &&
    demande.depense_usd >= demande.plafond_cout_usd
  ) {
    unites.push("usd");
  }
  if (
    demande.plafond_tokens !== null &&
    demande.depense_tokens >= demande.plafond_tokens
  ) {
    unites.push("tokens");
  }
  // Une demande où rien n'est franchi (le contrôle a vu une mesure que le grand
  // livre n'a pas encore) : on propose de relever celui qui est posé.
  if (unites.length === 0) {
    unites.push(demande.plafond_cout_usd !== null ? "usd" : "tokens");
  }
  return unites;
}

/** Ce que coûterait ce qui est gardé — l'estimation du brief, appliquée au reste. */
export function coutDuReste(nbGardees: number): EstimationSuite {
  return estimerReste(nbGardees);
}

/**
 * Le plafond que la carte **propose** pour une unité — `null` quand elle ne sait
 * pas le proposer honnêtement.
 *
 * En dollars : la dépense, plus la borne haute de l'estimation de ce qui est
 * gardé, arrondie au centime **supérieur** — de quoi finir dans le pire cas que
 * l'estimation prévoit. En tokens : rien. L'estimation du brief est en dollars,
 * et la convertir en tokens demanderait un prix au token que Maestro ne connaît
 * pas pour ce fournisseur ; inventer un chiffre ici serait le contraire de ce que
 * le plafond protège. La personne l'écrit.
 */
export function plafondPropose(
  demande: DemandePlafond,
  unite: UnitePlafond,
  nbGardees: number,
): number | null {
  if (unite === "tokens") return null;
  const depense = demande.depense_usd ?? 0;
  return Math.ceil((depense + coutDuReste(nbGardees).haut) * 100) / 100;
}

/** Ce qui est déjà dépensé, dans l'unité d'un plafond. */
export function depenseEn(demande: DemandePlafond, unite: UnitePlafond): number {
  return unite === "usd" ? (demande.depense_usd ?? 0) : demande.depense_tokens;
}

/**
 * La décision qui part vers l'API — `relever` quand tout est gardé, `reduire`
 * sinon, avec le nouveau plafond de chaque unité franchie. Les tâches écartées
 * sont celles de la question qui ne sont plus cochées : jamais une tâche que la
 * question ne nommait pas.
 */
export function decisionDeReprise(
  demande: DemandePlafond,
  gardees: ReadonlySet<string>,
  montants: Partial<Record<UnitePlafond, number>>,
): DecisionPlafond {
  const ecartees = demande.restantes
    .map((tache) => tache.tache_id)
    .filter((tacheId) => !gardees.has(tacheId));
  return {
    geste: ecartees.length > 0 ? GESTE_REDUIRE : GESTE_RELEVER,
    plafond_cout_usd: montants.usd ?? null,
    plafond_tokens: montants.tokens ?? null,
    ...(ecartees.length > 0 && { ecartees }),
  };
}

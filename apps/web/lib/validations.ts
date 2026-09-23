/**
 * Ce qui attend un **arbitrage humain** (#48) : l'ordre de la file, la demande
 * qui dort sur une tâche, celles qui retiennent un run donné.
 *
 * Ces trois questions se posaient jusqu'ici à trois endroits — `fileDAttente`
 * dans `components/PanneauValidations`, l'appariement run ↔ demande dans
 * `lib/execution`, et rien du tout pour « laquelle attend sur cette tâche ? ».
 * #1228 les réunit ici pour la raison qui a fait exister `lib/brief` et
 * `lib/questions` : **cinq** surfaces tranchent désormais une validation — le
 * tableau de bord, la page, la cloche, l'en-tête d'un run et les deux lectures
 * denses de ce run —, et une file qu'on ordonne autrement selon l'écran ferait
 * trancher d'abord, ici, ce qui attend depuis le moins longtemps.
 *
 * Rien de neuf côté données : ce module ne fait que lire `validations` et
 * `taches`, les deux listes que le shell tient déjà pour le projet actif
 * (`lib/etatGlobal`).
 */

import { VALIDATION_EN_ATTENTE, type Tache, type Validation } from "@/lib/types";

/**
 * Les demandes qui attendent, **la plus ancienne en tête**.
 *
 * Le tri se fait sur la chaîne ISO du backend, comparée telle quelle : à fuseau
 * égal — et le backend n'en émet qu'un, UTC — l'ordre lexicographique d'un ISO 8601
 * *est* l'ordre chronologique, sans construire une `Date` par comparaison. Une
 * demande sans horodatage (donnée ancienne, événement amputé) passe **en
 * dernier** : elle n'a pas d'âge à faire valoir, et la mettre en tête ferait
 * traiter d'abord celle dont on sait le moins.
 */
export function fileDAttente(validations: Validation[]): Validation[] {
  return validations
    .filter((v) => v.statut === VALIDATION_EN_ATTENTE)
    .sort((a, b) => {
      if (a.horodatage === b.horodatage) return 0;
      if (!a.horodatage) return 1;
      if (!b.horodatage) return -1;
      return a.horodatage < b.horodatage ? -1 : 1;
    });
}

/**
 * La demande qui dort sur chaque **tâche**, indexée par `tache_id` (#1228).
 *
 * C'est ce qu'un écran dense a besoin de savoir : le nœud de pipeline et la
 * carte de Kanban disent déjà *qu'*une tâche est arrêtée sur un humain
 * (`tachesEnAttenteDeValidation`, un ensemble d'identifiants) — trancher sur
 * place demande la **demande elle-même**, pas seulement son existence.
 *
 * Une seule par tâche : c'est déjà l'hypothèse de `tache_id` comme clé de React
 * dans la file (`components/CarteValidation`). Si deux demandes portaient la
 * même tâche, **la plus ancienne gagne** — le même ordre que la file, et non
 * celui du backend, qui n'est celui de personne.
 */
export function arbitragesEnAttente(
  validations: Validation[],
): Map<string, Validation> {
  const par_tache = new Map<string, Validation>();
  // La file est triée de la plus ancienne à la plus récente : la première vue
  // reste, les suivantes ne l'écrasent pas.
  for (const validation of fileDAttente(validations)) {
    if (!par_tache.has(validation.tache_id)) {
      par_tache.set(validation.tache_id, validation);
    }
  }
  return par_tache;
}

/**
 * Les demandes en attente qui retiennent **ce run**, la plus ancienne en tête
 * (#1228).
 *
 * Deux chemins, et l'ordre compte. La demande **porte son run** depuis #570
 * (`run_id`), et c'est la source : elle est publiée *avant* que sa tâche
 * n'existe pour qui que ce soit — une tâche sensible est stoppée avant toute
 * exécution —, si bien qu'un appariement passant d'abord par les tâches ratait
 * exactement le cas nominal (#568, docs/05 §2.6). Le passage par les tâches
 * reste le **filet**, pour une trace d'avant #570 ou un producteur qui ne
 * porte rien — c'est le même partage que `runsEnAttenteDeValidation`, pris par
 * l'autre bout.
 */
export function validationsDuRun(
  validations: Validation[],
  taches: Tache[],
  runId: string,
): Validation[] {
  const runParTache = new Map(taches.map((tache) => [tache.id, tache.run_id]));
  return fileDAttente(validations).filter((validation) =>
    validation.run_id
      ? validation.run_id === runId
      : runParTache.get(validation.tache_id) === runId,
  );
}

/**
 * Le journal **persisté** vu du front (#478) : de quoi lire un historique
 * comme on lit le direct.
 *
 * Le fil d'activité était éphémère par construction — il ne contenait que ce qui
 * était passé par le WebSocket depuis l'ouverture de la page, donc un
 * rechargement pendant un run d'une heure effaçait tout. `GET /api/journal` sert
 * désormais cet historique (contrat #183, servi par #478) ; ce module fait le
 * pont entre ses entrées et ce que les écrans savent déjà rendre.
 *
 * Deux fonctions, et rien d'autre :
 *
 * - `evenementDepuisEntree` **rhabille** une entrée en `Evenement`, la forme que
 *   `resumeEvenement`, `grouperEvenements` et `LigneActivite` consomment déjà.
 *   Aucun rendu n'est réécrit pour l'occasion : historique et direct se lisent
 *   dans la même ligne, avec le même vocabulaire, ce qui est la seule façon de
 *   ne pas les faire diverger. Les champs qu'une entrée ne porte pas (`usage`,
 *   `cout_usd`, `instances`, `ticket`) retombent sur ce que le flux met quand il
 *   n'en sait rien — `null` : la ligne les traite déjà comme inconnus.
 * - `fusionnerJournal` **superpose** le direct à l'historique. L'ordre est celui
 *   du fil, du plus récent au plus ancien, et un événement déjà consigné n'y
 *   apparaît qu'une fois : l'historique est relu à chaque battement du shell,
 *   donc il rattrape le direct au bout de quelques secondes, et sans dédoublonnage
 *   chaque ligne apparaîtrait deux fois pendant l'intervalle.
 */

import type { EntreeJournal, Evenement } from "@/lib/types";

/** Une entrée d'historique, rhabillée en événement du fil. */
export function evenementDepuisEntree(entree: EntreeJournal): Evenement {
  return {
    type: entree.type,
    run_id: entree.run_id,
    tache_id: entree.tache_id,
    titre: entree.titre,
    agent: entree.agent,
    role: entree.role,
    statut: entree.statut,
    detail: entree.detail,
    description: entree.description,
    cout_usd: null,
    usage: null,
    instances: null,
    ticket: null,
    projet_id: entree.projet_id,
    horodatage: entree.horodatage,
  };
}

/**
 * Ce qui fait qu'un événement du direct **est** une entrée déjà consignée.
 *
 * Les entrées ne portent pas l'`id` du bus — il n'y en a pas : un événement est
 * un fait, pas une ressource. On compare donc ce qui le décrit. Deux événements
 * qui coïncident sur ces huit champs sont indiscernables **à l'écran comme dans
 * l'historique**, donc les replier n'enlève rien à ce qui est montré.
 */
function cleEvenement(evenement: Evenement): string {
  return [
    evenement.horodatage,
    evenement.type,
    evenement.run_id,
    evenement.tache_id,
    evenement.agent,
    evenement.statut,
    evenement.titre,
    evenement.detail,
  ].join("");
}

/**
 * L'historique persisté, augmenté du direct qu'il n'a pas encore rattrapé —
 * du plus récent au plus ancien, comme le fil.
 *
 * `historique` arrive déjà trié en `desc` par le backend ; `direct` est le fil
 * du shell, lui aussi du plus récent au plus ancien. Le tri final est refait ici
 * plutôt que supposé : les deux sources n'ont pas la même fraîcheur, et deux
 * listes ordonnées concaténées ne le sont pas.
 */
export function fusionnerJournal(
  historique: Evenement[],
  direct: Evenement[],
): Evenement[] {
  const connus = new Set(historique.map(cleEvenement));
  const inedits = direct.filter(
    (evenement) => !connus.has(cleEvenement(evenement)),
  );
  return [...historique, ...inedits].sort((a, b) =>
    b.horodatage.localeCompare(a.horodatage),
  );
}

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
 *
 * #266 lui en a ajouté deux, qui répondent à la même question — « de quelle
 * tâche cette ligne parle-t-elle ? » — et qui vivent ici plutôt que dans l'écran
 * qui les appelle : `optionsTache` nomme les tâches d'un fil pour une liste
 * déroulante (la page Journal l'avait en privé depuis #249, l'onglet Logs en
 * avait besoin à l'identique — deux formulations de « comment s'appelle cette
 * tâche ? » auraient fini par diverger d'un écran à l'autre), et
 * `grouperParTache` range le fil par tâche pour l'onglet Logs.
 */

import type { OptionFiltre } from "@/components/Primitives";
import { hrefRun } from "@/lib/navigation";
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

/**
 * Les tâches apparues dans le fil, nommées et triées par libellé — les options
 * du filtre « Tâche », de la page Journal (#249) comme de l'onglet Logs (#266).
 *
 * Le titre n'accompagne pas tous les événements d'une même tâche
 * (`tache.reference` n'en porte pas) : le premier rencontré fait foi,
 * l'identifiant sert de repli en attendant. Rien n'est figé en dur — la liste
 * sort du fil lui-même, donc aucune option morte qui ne rendrait jamais rien.
 */
export function optionsTache(evenements: Evenement[]): OptionFiltre[] {
  return [...titresParTache(evenements).entries()]
    .map(([valeur, libelle]) => ({ valeur, libelle }))
    .sort((a, b) => a.libelle.localeCompare(b.libelle, "fr"));
}

/**
 * Le nom de chaque tâche du fil, indexé par identifiant, dans l'ordre où les
 * tâches apparaissent. Les deux fonctions publiques ci-dessus et ci-dessous en
 * dépendent, et c'est tout l'intérêt : « comment s'appelle cette tâche ? » a une
 * seule réponse, que le fil soit trié pour une liste déroulante ou découpé en
 * groupes.
 */
function titresParTache(evenements: Evenement[]): Map<string, string> {
  const parId = new Map<string, string>();
  for (const evenement of evenements) {
    const id = evenement.tache_id;
    if (!id) continue;
    const connu = parId.get(id);
    if (connu === undefined || (connu === id && evenement.titre)) {
      parId.set(id, evenement.titre || id);
    }
  }
  return parId;
}

/** Le nom du groupe qui recueille ce qui ne relève d'aucune tâche. */
export const GROUPE_HORS_TACHE = "Hors tâche";

/**
 * Les lignes d'un fil rangées par tâche (#266) — ce que l'onglet Logs affiche.
 *
 * `renvoi` est le lien vers la tâche « quand elle existe », et les deux moitiés
 * de cette condition comptent. Il n'y a **pas de route par tâche** dans la
 * Control Tower : une tâche s'ouvre dans la vue de son run (`components/runs`),
 * en panneau. Le renvoi mène donc là, par `hrefRun` — jamais par un chemin écrit
 * en dur : le helper rend `undefined` tant que la page n'existe pas, ce qui
 * éteint le renvoi au lieu de poser un lien mort (même contrat qu'en #475/#191).
 * Et le groupe **hors tâche** n'en porte aucun : il n'y a pas de tâche à voir.
 */
export type GroupeTacheLogs = {
  /** L'identifiant de la tâche, vide pour le groupe hors tâche. */
  tacheId: string;
  /** Son nom lisible, à défaut son identifiant. */
  libelle: string;
  renvoi?: { href: string; libelle: string };
  evenements: Evenement[];
};

/**
 * Range un fil par tâche, **dans l'ordre du fil** : le groupe qui ouvre est
 * celui dont la ligne la plus récente est la plus récente. C'est la même règle
 * que le fil lui-même — du plus récent au plus ancien —, appliquée aux groupes,
 * ce qui met en tête la tâche sur laquelle l'agent travaille en ce moment.
 *
 * Les événements d'un groupe gardent l'ordre qu'ils avaient, donc `FilActivite`
 * et `grouperEvenements` s'y appliquent sans rien réordonner : ce découpage
 * ajoute un niveau de rangement, il ne refait pas le fil.
 */
export function grouperParTache(evenements: Evenement[]): GroupeTacheLogs[] {
  const titres = titresParTache(evenements);
  const groupes = new Map<string, GroupeTacheLogs>();
  for (const evenement of evenements) {
    const tacheId = evenement.tache_id;
    const existant = groupes.get(tacheId);
    if (existant) {
      existant.evenements.push(evenement);
      continue;
    }
    // Le renvoi se dérive de la **première** ligne du groupe — la plus récente,
    // donc celle dont le run est le dernier à avoir porté cette tâche.
    const vue = tacheId ? hrefRun(evenement.run_id) : undefined;
    groupes.set(tacheId, {
      tacheId,
      libelle: tacheId
        ? (titres.get(tacheId) ?? tacheId)
        : GROUPE_HORS_TACHE,
      renvoi:
        vue && evenement.run_id
          ? { href: vue, libelle: "Voir la tâche" }
          : undefined,
      evenements: [evenement],
    });
  }
  return [...groupes.values()];
}

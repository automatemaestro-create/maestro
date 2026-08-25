/**
 * Ce que la **vue pipeline** lit dans le graphe d'un run (#491, lot 3 de #488),
 * hors du JSX.
 *
 * Le backend (#490) sert déjà tout ce qui se dessine — `niveaux`, `niveau`/`rang`
 * par nœud, `compartiment`, `plat`/`profondeur`/`largeur`. Rien de tout cela
 * n'est recalculé ici, et c'est le premier critère du lot précédent : un client
 * qui redéduirait les niveaux réécrirait un tri topologique en TypeScript, sur
 * les seuls nœuds qu'il a chargés.
 *
 * Ce module ne porte donc que ce que le dessin ajoute au contrat, c'est-à-dire
 * **trois questions que le backend ne pose pas** :
 *
 * - **« ce nœud attend-il un humain ? »** — la question qui a motivé tout le
 *   chantier (#355 : une attente de décision restée 53 minutes indiscernable
 *   d'un travail en cours). Le compartiment ne la porte pas, et ce n'est pas un
 *   oubli : `en_attente_validation` compte pour **en cours** dans la table
 *   partagée (`maestro/controltower/progression.py`), à raison — la tâche est en
 *   vol. Mais « en vol » et « quelqu'un doit trancher » ne se ressemblent pas à
 *   l'œil, et c'est exactement ce que la vue existe pour séparer ;
 * - **« ce nœud vient-il d'apparaître ? »** — un nœud à faire dont *toutes* les
 *   arêtes entrantes sont franchies est celui qui part maintenant. Sur un plan
 *   déclaré d'avance, « la suite apparaît » ne peut pas vouloir dire qu'une boîte
 *   se crée (elle était là, grise, depuis le début) : ça veut dire qu'elle
 *   s'allume ;
 * - **« qu'est-ce que la branche courante ? »** — de quoi suivre un flux au lieu
 *   de tout montrer, sur un graphe qui déborde (note technique du ticket).
 *
 * Aucun import React : ces règles se testent sans rendu — `apps/web/tests/
 * pipeline.test.tsx` (#492) les éprouve hors de tout montage, et l'**ordre** dans
 * lequel les questions ci-dessus sont posées y est gardé comme la décision qu'il
 * est. La table qui les traduit en pixels vit, elle, dans
 * `components/runs/VuePipeline`.
 */

import {
  ARETE_FRANCHIE,
  ETAPE_EN_COURS,
  ETAPE_FAITE,
  type AreteGraphe,
  type EtapeTache,
  type GrapheRun,
  type NoeudGraphe,
  type Progression,
} from "./types";

/**
 * Le statut de la machine à états (docs/03 §3) qui dit « cette tâche attend un
 * humain ». Le moteur ne l'émet pas encore — la source qui existe aujourd'hui
 * est la file des validations (`lib/execution.tachesEnAttenteDeValidation`) —,
 * mais il est nommé dans le contrat partagé et une vue qui l'ignorerait
 * deviendrait fausse le jour où il circulera, c'est-à-dire au pire moment.
 */
export const STATUT_EN_ATTENTE_VALIDATION = "en_attente_validation";

/* ------------------------------------------------------------------ *
 * L'état d'un nœud, tel que la vue le distingue
 * ------------------------------------------------------------------ */

/** Quelqu'un doit trancher — le nœud ne bougera pas sans geste humain. */
export const NOEUD_ATTENTE_HUMAIN = "attente_humain";
/** Un agent travaille dessus, en ce moment. */
export const NOEUD_EN_COURS = "en_cours";
/** Toutes ses arêtes entrantes sont franchies : c'est ce qui part ensuite. */
export const NOEUD_PRET = "pret";
/** Déclaré au plan, rien ne l'a encore débloqué. */
export const NOEUD_A_FAIRE = "a_faire";
export const NOEUD_BLOQUE = "bloque";
export const NOEUD_TERMINE = "termine";
export const NOEUD_ECHEC = "echec";
/** Statut que ce front ne connaît pas encore — montré, jamais escamoté. */
export const NOEUD_AUTRE = "autre";

export type EtatNoeud =
  | typeof NOEUD_ATTENTE_HUMAIN
  | typeof NOEUD_EN_COURS
  | typeof NOEUD_PRET
  | typeof NOEUD_A_FAIRE
  | typeof NOEUD_BLOQUE
  | typeof NOEUD_TERMINE
  | typeof NOEUD_ECHEC
  | typeof NOEUD_AUTRE;

/**
 * Du compartiment servi (#473) à l'état dessiné. La correspondance est directe
 * et sans arbitrage : le compartiment **est** la couleur, lue dans la table
 * partagée du backend et jamais réinventée par écran. Ce que la vue ajoute —
 * l'attente humaine, la disponibilité — se décide au-dessus, dans
 * `etatDuNoeud`.
 */
const ETAT_PAR_COMPARTIMENT: Record<keyof Progression | string, EtatNoeud> = {
  a_faire: NOEUD_A_FAIRE,
  en_cours: NOEUD_EN_COURS,
  bloquees: NOEUD_BLOQUE,
  terminees: NOEUD_TERMINE,
  echecs: NOEUD_ECHEC,
  autres: NOEUD_AUTRE,
};

/**
 * L'état auquel ce nœud se dessine.
 *
 * **L'ordre des questions est la décision**, comme pour le régime d'un run
 * (`lib/execution`) :
 *
 * 1. **l'attente humaine l'emporte sur tout le reste** — y compris sur « en
 *    cours », qui est pourtant son compartiment. C'est la moitié du signal : un
 *    nœud arrêté sur quelqu'un depuis trois heures ne travaille pas, et les
 *    confondre est le défaut d'origine du chantier ;
 * 2. **la disponibilité ensuite**, et seulement sur ce qui reste à faire : un
 *    nœud dont toutes les arêtes entrantes sont franchies est celui qui part
 *    maintenant. Sans **aucune** dépendance il n'est pas « débloqué » mais
 *    simplement prêt depuis le départ — le marquer ferait clignoter tout le
 *    niveau 0 d'un plan plat, où *tout* est prêt et où le signal ne dirait plus
 *    rien ;
 * 3. **le compartiment sinon**, tel qu'il est servi.
 */
export function etatDuNoeud(
  noeud: NoeudGraphe,
  attendUnHumain: boolean,
  entrantes: readonly AreteGraphe[],
): EtatNoeud {
  if (attendUnHumain || noeud.statut === STATUT_EN_ATTENTE_VALIDATION) {
    return NOEUD_ATTENTE_HUMAIN;
  }
  const etat = ETAT_PAR_COMPARTIMENT[noeud.compartiment] ?? NOEUD_AUTRE;
  if (
    etat === NOEUD_A_FAIRE &&
    entrantes.length > 0 &&
    entrantes.every((arete) => arete.etat === ARETE_FRANCHIE)
  ) {
    return NOEUD_PRET;
  }
  return etat;
}

/** Les états dont on dit que le run **travaille** ou **attend** dessus. */
const ETATS_VIVANTS: readonly EtatNoeud[] = [NOEUD_EN_COURS, NOEUD_ATTENTE_HUMAIN];

/* ------------------------------------------------------------------ *
 * Index de lecture
 * ------------------------------------------------------------------ */

/**
 * Les arêtes **entrantes** de chaque nœud, indexées une fois. Le graphe les
 * porte à plat (`aretes`) et chaque nœud porte ses `dependances` par
 * identifiant : sans cet index, savoir si un nœud est débloqué coûterait un
 * balayage de toutes les arêtes par nœud.
 */
export function aretesEntrantes(
  graphe: GrapheRun,
): Map<string, AreteGraphe[]> {
  const index = new Map<string, AreteGraphe[]>();
  for (const arete of graphe.aretes) {
    const deja = index.get(arete.vers);
    if (deja) deja.push(arete);
    else index.set(arete.vers, [arete]);
  }
  return index;
}

/** L'état de chaque nœud, calculé une fois pour toute la vue. */
export function etatsDesNoeuds(
  graphe: GrapheRun,
  enAttenteHumaine: ReadonlySet<string>,
): Map<string, EtatNoeud> {
  const entrantes = aretesEntrantes(graphe);
  return new Map(
    graphe.noeuds.map((noeud) => [
      noeud.id,
      etatDuNoeud(noeud, enAttenteHumaine.has(noeud.id), entrantes.get(noeud.id) ?? []),
    ]),
  );
}

/* ------------------------------------------------------------------ *
 * La branche courante
 * ------------------------------------------------------------------ */

/**
 * Les nœuds **d'où part le regard** : ce qui travaille ou attend quelqu'un ;
 * à défaut, ce qui est sur le point de partir.
 *
 * Le repli sur `pret` couvre l'instant qui suit une fin de tâche — rien ne
 * tourne encore, mais la suite est désignée. Un run entièrement soldé n'a, lui,
 * aucune amorce : c'est un état normal, et le cadrage sur la branche s'y éteint
 * plutôt que de choisir un nœud au hasard.
 */
export function amorcesDeBranche(
  graphe: GrapheRun,
  etats: ReadonlyMap<string, EtatNoeud>,
): string[] {
  const vivants = graphe.noeuds
    .filter((noeud) => ETATS_VIVANTS.includes(etats.get(noeud.id) ?? NOEUD_AUTRE))
    .map((noeud) => noeud.id);
  if (vivants.length > 0) return vivants;
  return graphe.noeuds
    .filter((noeud) => etats.get(noeud.id) === NOEUD_PRET)
    .map((noeud) => noeud.id);
}

/**
 * La **branche courante** : les amorces, tout ce qui y mène et tout ce qui en
 * découle — jamais leurs cousins.
 *
 * C'est la réponse à la note technique du ticket (« un graphe ne se lit pas s'il
 * déborde : permettre de suivre la branche courante plutôt que de tout
 * montrer »). Amont **et** aval, parce que les deux moitiés répondent à des
 * questions qu'on se pose ensemble : d'où vient ce qui tourne, et qu'est-ce que
 * ça va déclencher.
 *
 * Les branches **sœurs** — deux tâches parallèles issues du même amont, dont une
 * seule tourne — sortent du cadrage : c'est ce qui fait gagner de la place, et
 * c'est assumé. La bascule « Tout le graphe » les ramène.
 */
export function brancheCourante(
  graphe: GrapheRun,
  amorces: readonly string[],
): Set<string> {
  const parId = new Map(graphe.noeuds.map((noeud) => [noeud.id, noeud]));
  const retenus = new Set<string>();

  const parcourir = (depart: readonly string[], sens: "amont" | "aval") => {
    const file = [...depart];
    const vus = new Set<string>(depart);
    while (file.length > 0) {
      const id = file.shift() as string;
      retenus.add(id);
      const noeud = parId.get(id);
      if (noeud === undefined) continue;
      const voisins = sens === "amont" ? noeud.dependances : noeud.dependants;
      for (const voisin of voisins) {
        // `vus` et non `retenus` : les deux parcours partagent les amorces, et
        // un cycle (un plan relu du bus, jamais revalidé — #490) ferait boucler
        // sans lui.
        if (vus.has(voisin)) continue;
        vus.add(voisin);
        file.push(voisin);
      }
    }
  };

  parcourir(amorces, "amont");
  parcourir(amorces, "aval");
  return retenus;
}

/**
 * Les niveaux **restreints** à un sous-ensemble de nœuds, les niveaux devenus
 * vides étant retirés.
 *
 * Retirer les colonnes vides est ce qui fait tenir un cadrage à l'écran : garder
 * la place d'un niveau dont plus rien n'est retenu rendrait une chaîne de trois
 * tâches avec sept colonnes de trous. Le `niveau` porté par chaque nœud, lui, ne
 * bouge pas — il reste le rang **dans le plan**, que le cadrage ne renumérote
 * pas.
 */
export function niveauxRetenus(
  graphe: GrapheRun,
  retenus: ReadonlySet<string> | null,
): string[][] {
  if (retenus === null) return graphe.niveaux;
  return graphe.niveaux
    .map((niveau) => niveau.filter((id) => retenus.has(id)))
    .filter((niveau) => niveau.length > 0);
}

/* ------------------------------------------------------------------ *
 * La checklist d'un nœud
 * ------------------------------------------------------------------ */

/** Combien d'étapes sont faites, sur combien (#489). */
export function comptesEtapes(etapes: readonly EtapeTache[]): {
  faites: number;
  total: number;
} {
  return {
    faites: etapes.filter((etape) => etape.etat === ETAPE_FAITE).length,
    total: etapes.length,
  };
}

/**
 * L'étape que le nœud est **en train** de faire, ou la prochaine qui l'attend —
 * `null` quand la checklist est vide ou entièrement cochée.
 *
 * Un nœud n'a pas la place d'afficher sa checklist entière : cinq nœuds de front
 * en feraient une page de listes à puces. Une case par étape (la barre) dit
 * *combien*, cette ligne dit *quoi* — et c'est ce couple qui rend une checklist
 * lisible d'un coup d'œil. Le détail complet s'ouvre au clic, dans le panneau
 * qui existe déjà (#251).
 */
export function etapeCourante(
  etapes: readonly EtapeTache[],
): EtapeTache | null {
  return (
    etapes.find((etape) => etape.etat === ETAPE_EN_COURS) ??
    etapes.find((etape) => etape.etat !== ETAPE_FAITE) ??
    null
  );
}

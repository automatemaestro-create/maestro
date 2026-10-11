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
 * Et depuis #1297, une question de **géométrie**, la seule que le serveur ne
 * peut pas poser puisqu'elle dépend des boîtes mesurées : **par où passe une
 * arête** pour ne jamais croiser une carte (`tracerArete`). Qu'une arête soit
 * redondante, en revanche, est servi (`AreteGraphe.redondante`), jamais déduit
 * ici.
 *
 * Et depuis #1393, **quelles flèches partagent un tronc** — une topologie, que
 * le serveur pourrait poser mais qui dépend des colonnes **dessinées**, donc du
 * cadrage (`troncsDesEventails`) — et par où passe ce tronc (`tracerTroncs`,
 * `tracerDansTronc`).
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
  // Le statut de la machine à états (docs/03 §3) qui dit « cette tâche attend
  // un humain ». Le moteur ne l'émet pas encore — la source qui existe
  // aujourd'hui est la file des validations
  // (`lib/execution.tachesEnAttenteDeValidation`) —, mais il est nommé dans le
  // **contrat partagé** et une vue qui l'ignorerait deviendrait fausse le jour
  // où il circulera, c'est-à-dire au pire moment. Ce module en portait sa
  // propre copie jusqu'à #1111 : deux déclarations du même mot, dont la
  // seconde n'aurait pas suivi si le backend en changeait.
  STATUT_EN_ATTENTE_VALIDATION,
  type AreteGraphe,
  type EtapeTache,
  type GrapheRun,
  type NoeudGraphe,
  type Progression,
} from "./types";

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
/**
 * Arrêtée en vol quand son run a été soldé (#1390) — ni ratée ni acquise : son
 * travail est à reprendre.
 */
export const NOEUD_INTERROMPU = "interrompu";
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
  | typeof NOEUD_INTERROMPU
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
  interrompues: NOEUD_INTERROMPU,
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
 * Le tracé d'une arête — sans jamais passer sous une carte (#1297)
 * ------------------------------------------------------------------ */

/** Un rectangle dans la toile du dessin, en pixels — la boîte mesurée d'un nœud. */
export type Rect = { x: number; y: number; largeur: number; hauteur: number };

export type Point = { x: number; y: number };

/**
 * Un morceau de tracé : une **courbe** dans la gouttière entre deux colonnes, ou
 * une **ligne** qui traverse une colonne par un couloir libre.
 */
export type Segment =
  | { forme: "courbe"; de: Point; c1: Point; c2: Point; vers: Point }
  | { forme: "ligne"; de: Point; vers: Point };

/**
 * Le tracé d'une arête : ses segments (ce que les tests échantillonnent), le
 * chemin SVG qui les dessine, et le point le plus bas où il passe — de quoi
 * réserver sous le dessin la place d'un couloir qui longe la plus haute colonne.
 */
export type TraceArete = { segments: Segment[]; d: string; bas: number };

/** L'écart entre la dernière carte d'une colonne et le couloir qui passe dessous. */
export const ECART_COULOIR = 12;

/**
 * L'écart entre deux flèches qui empruntent **le même couloir** d'une colonne
 * (#1393) : sans lui, elles s'y superposaient et se lisaient comme un seul trait.
 */
export const ECART_VOIE = 4;

/**
 * Une courbe à tangentes horizontales, d'un bord de gouttière à l'autre. Les
 * points de contrôle sont à mi-distance, avec un plancher — sinon deux colonnes
 * voisines rendraient une courbe si plate qu'elle se confondrait avec le bord.
 * Tant que la gouttière fait plus de deux fois le plancher (elle en fait 64 px,
 * `gap-16`), les deux points de contrôle restent **dans** la gouttière, donc la
 * courbe aussi : elle ne peut toucher aucune carte.
 */
function courbe(de: Point, vers: Point): Segment {
  const tension = Math.max(24, (vers.x - de.x) / 2);
  return {
    forme: "courbe",
    de,
    c1: { x: de.x + tension, y: de.y },
    c2: { x: vers.x - tension, y: vers.y },
    vers,
  };
}

/**
 * Un couloir où l'on traverse une colonne **sans toucher une carte** : sa
 * hauteur, et la marge libre de part et d'autre — `null` sous la dernière carte,
 * où rien ne borne le couloir vers le bas.
 */
type Couloir = { y: number; marge: number | null };

/**
 * Les couloirs d'une colonne : le milieu de l'intervalle entre deux cartes, et
 * sous la dernière. Pas au-dessus de la première — la colonne commence en haut
 * de la toile, il n'y a pas de place.
 */
function couloirsLibres(cartes: readonly Rect[]): Couloir[] {
  const libres: Couloir[] = [];
  for (let i = 0; i + 1 < cartes.length; i += 1) {
    const bas = cartes[i].y + cartes[i].hauteur;
    if (cartes[i + 1].y > bas) {
      libres.push({ y: (bas + cartes[i + 1].y) / 2, marge: (cartes[i + 1].y - bas) / 2 - 1 });
    }
  }
  const derniere = cartes[cartes.length - 1];
  libres.push({ y: derniere.y + derniere.hauteur + ECART_COULOIR, marge: null });
  return libres;
}

/**
 * La voie d'une flèche dans un couloir déjà emprunté (#1393) : la première passe
 * au milieu, les suivantes s'en écartent de part et d'autre, sans sortir de
 * l'intervalle libre — et sous la dernière carte, vers le bas seulement.
 */
function voieDans(couloir: Couloir, deja: number): number {
  if (deja === 0) return couloir.y;
  if (couloir.marge === null) return couloir.y + deja * ECART_VOIE;
  const ecart = Math.min(Math.ceil(deja / 2) * ECART_VOIE, couloir.marge);
  return couloir.y + (deja % 2 === 1 ? ecart : -ecart);
}

function cheminSvg(segments: readonly Segment[]): string {
  if (segments.length === 0) return "";
  const debut = segments[0].de;
  return segments.reduce(
    (d, segment) =>
      segment.forme === "courbe"
        ? `${d} C ${segment.c1.x} ${segment.c1.y}, ${segment.c2.x} ${segment.c2.y}, ${segment.vers.x} ${segment.vers.y}`
        : `${d} L ${segment.vers.x} ${segment.vers.y}`,
    `M ${debut.x} ${debut.y}`,
  );
}

/**
 * Le tracé d'une dépendance, du **bord droit de l'amont** au **bord gauche de
 * l'aval** — et **jamais sous une carte** (#1297).
 *
 * Deux colonnes voisines : une seule courbe, dans leur gouttière. C'est le cas
 * courant, et le dessin d'avant ce ticket.
 *
 * Une arête qui **saute** des colonnes se traçait d'une traite, et passait donc
 * sous la carte intermédiaire : c'est la flèche qui « semblait venir d'ailleurs »
 * sur le run `3fe501fc0878` (une redondante, que la vue ne dessine plus depuis
 * #1393 ; une arête qui saute une colonne sans l'être — les illustrations qui ne
 * servent que la recette finale — reste à contourner). Elle
 * traverse désormais chaque colonne sautée par un **couloir libre** (entre deux
 * cartes, ou sous la dernière), choisi au plus près de la droite qui joindrait
 * ses deux bouts, et rejoint le couloir suivant par une courbe de gouttière. Une
 * courbe reste dans sa gouttière, une ligne dans son couloir : aucun morceau ne
 * peut toucher une carte — c'est ce que `pipeline.test.tsx` échantillonne, et ce
 * que le banc de mise en page mesure sur la vraie stack.
 *
 * `colonnes` sont celles **dessinées** (`niveauxRetenus`, après cadrage), pas les
 * niveaux du plan : une colonne retirée par le cadrage n'a rien à contourner.
 *
 * `voies` (#1393) est le registre des couloirs déjà empruntés, partagé par les
 * tracés d'un même dessin : deux flèches qui choisissent le même couloir d'une
 * colonne y passent l'une à côté de l'autre (`ECART_VOIE`) au lieu de se
 * superposer. Sans registre, chaque tracé se calcule seul, comme avant.
 *
 * `null` tant que les deux boîtes ne sont pas mesurées : un tracé vers `(0,0)`
 * traverserait le dessin le temps d'une image.
 */
export function tracerArete(
  de: string,
  vers: string,
  colonnes: readonly (readonly string[])[],
  boites: ReadonlyMap<string, Rect>,
  voies?: Map<string, number>,
): TraceArete | null {
  const depart = boites.get(de);
  const arrivee = boites.get(vers);
  if (depart === undefined || arrivee === undefined) return null;
  if (depart.largeur === 0 || arrivee.largeur === 0) return null;

  const sortie = { x: depart.x + depart.largeur, y: depart.y + depart.hauteur / 2 };
  const entree = { x: arrivee.x, y: arrivee.y + arrivee.hauteur / 2 };

  const colonneDe = new Map<string, number>();
  colonnes.forEach((colonne, rang) => colonne.forEach((id) => colonneDe.set(id, rang)));
  const ci = colonneDe.get(de);
  const cj = colonneDe.get(vers);

  const segments: Segment[] = [];
  let ici = sortie;
  if (ci !== undefined && cj !== undefined && cj > ci + 1) {
    for (let k = ci + 1; k < cj; k += 1) {
      const cartes = colonnes[k]
        .map((id) => boites.get(id))
        .filter((boite): boite is Rect => boite !== undefined && boite.largeur > 0)
        .sort((a, b) => a.y - b.y);
      if (cartes.length === 0) continue;
      const vise = sortie.y + ((entree.y - sortie.y) * (k - ci)) / (cj - ci);
      const couloir = couloirsLibres(cartes).reduce((meilleur, candidat) =>
        Math.abs(candidat.y - vise) < Math.abs(meilleur.y - vise) ? candidat : meilleur,
      );
      const cle = `${k}|${couloir.y}`;
      const deja = voies?.get(cle) ?? 0;
      voies?.set(cle, deja + 1);
      const y = voieDans(couloir, deja);
      const gauche = { x: Math.min(...cartes.map((carte) => carte.x)), y };
      const droite = { x: Math.max(...cartes.map((carte) => carte.x + carte.largeur)), y };
      segments.push(courbe(ici, gauche), { forme: "ligne", de: gauche, vers: droite });
      ici = droite;
    }
  }
  segments.push(courbe(ici, entree));

  const bas = Math.max(
    ...segments.flatMap((segment) => [segment.de.y, segment.vers.y]),
  );
  return { segments, d: cheminSvg(segments), bas };
}

/**
 * Des points pris le long d'un tracé — `pas` par segment, extrémités comprises.
 * Ce que les tests confrontent aux cartes : une courbe de Bézier ne se juge pas
 * à ses seuls points de contrôle.
 */
export function pointsDuTrace(trace: TraceArete, pas = 24): Point[] {
  const points: Point[] = [];
  for (const segment of trace.segments) {
    for (let i = 0; i <= pas; i += 1) {
      const t = i / pas;
      if (segment.forme === "ligne") {
        points.push({
          x: segment.de.x + (segment.vers.x - segment.de.x) * t,
          y: segment.de.y + (segment.vers.y - segment.de.y) * t,
        });
        continue;
      }
      const u = 1 - t;
      const poids = [u * u * u, 3 * u * u * t, 3 * u * t * t, t * t * t];
      const [a, b, c, d] = [segment.de, segment.c1, segment.c2, segment.vers];
      points.push({
        x: poids[0] * a.x + poids[1] * b.x + poids[2] * c.x + poids[3] * d.x,
        y: poids[0] * a.y + poids[1] * b.y + poids[2] * c.y + poids[3] * d.y,
      });
    }
  }
  return points;
}

/* ------------------------------------------------------------------ *
 * Les éventails en troncs (#1393)
 * ------------------------------------------------------------------ */

/**
 * Un **tronc** : un éventail **exact** entre deux colonnes voisines — chaque
 * source y est attendue par chacune des cibles, et aucune cible n'attend une
 * autre tâche de la colonne de départ. Ses flèches partagent un trait vertical
 * dans la gouttière, au lieu d'autant de courbes : c'est la variante retenue sur
 * pièces par le regard neuf, d'après le graphe d'un run GitHub Actions, où le job
 * qui dépend de tous les autres reçoit un seul trait.
 *
 * L'exactitude est ce qui rend le tronc **vrai** : un trait commun à des liens
 * qui ne se correspondent pas ferait lire « A attend C » là où le plan ne le dit
 * pas — le défaut que la veille laisse à GitHub Actions. Un recouvrement
 * partiel retombe donc en courbes, une par dépendance.
 */
export type Tronc = {
  /** Stable d'un rendu à l'autre : la gouttière, les sources et les cibles. */
  cle: string;
  /** Le rang de la colonne de départ — le tronc vit dans la gouttière qui la suit. */
  gouttiere: number;
  /** Dans l'ordre de leur colonne, comme le dessin les empile. */
  sources: string[];
  cibles: string[];
  aretes: AreteGraphe[];
};

/**
 * Les troncs d'un dessin : les éventails exacts entre colonnes voisines, d'au
 * moins trois tâches (une source vers deux cibles, deux sources vers une cible,
 * ou davantage) — en deçà, une courbe suffit et un tronc n'apprendrait rien.
 *
 * Une **topologie**, sans géométrie : elle se décide sur les colonnes dessinées
 * (`niveauxRetenus`, après cadrage), et se teste sans aucune boîte mesurée.
 */
export function troncsDesEventails(
  aretes: readonly AreteGraphe[],
  colonnes: readonly (readonly string[])[],
): Tronc[] {
  const colonneDe = new Map<string, number>();
  const rangDe = new Map<string, number>();
  colonnes.forEach((colonne, c) =>
    colonne.forEach((id, r) => {
      colonneDe.set(id, c);
      rangDe.set(id, r);
    }),
  );
  const parRang = (a: string, b: string) => (rangDe.get(a) ?? 0) - (rangDe.get(b) ?? 0);

  const sorties = new Map<string, AreteGraphe[]>();
  const entrees = new Map<string, Set<string>>();
  for (const arete of aretes) {
    const ci = colonneDe.get(arete.de);
    if (ci === undefined || colonneDe.get(arete.vers) !== ci + 1) continue;
    sorties.set(arete.de, [...(sorties.get(arete.de) ?? []), arete]);
    entrees.set(arete.vers, (entrees.get(arete.vers) ?? new Set()).add(arete.de));
  }

  // Les sources qui servent **exactement** les mêmes cibles de la colonne
  // suivante forment un éventail candidat.
  const candidats = new Map<string, Omit<Tronc, "cle">>();
  for (const [source, liste] of sorties) {
    const cibles = [...new Set(liste.map((arete) => arete.vers))].sort(parRang);
    const gouttiere = colonneDe.get(source) as number;
    const cle = `${gouttiere}|${[...cibles].sort().join(",")}`;
    const candidat = candidats.get(cle) ?? { gouttiere, sources: [], cibles, aretes: [] };
    candidat.sources.push(source);
    candidat.aretes.push(...liste);
    candidats.set(cle, candidat);
  }

  const troncs: Tronc[] = [];
  for (const candidat of candidats.values()) {
    if (candidat.sources.length + candidat.cibles.length < 3) continue;
    const exact = candidat.cibles.every((cible) =>
      [...(entrees.get(cible) ?? [])].every((source) => candidat.sources.includes(source)),
    );
    if (!exact) continue;
    const sources = [...candidat.sources].sort(parRang);
    troncs.push({
      ...candidat,
      sources,
      cle: `${candidat.gouttiere}|${sources.join(",")}→${candidat.cibles.join(",")}`,
    });
  }
  return troncs;
}

/**
 * La géométrie d'un tronc : son abscisse dans la gouttière, l'amorce de chaque
 * source jusqu'à lui, son trait vertical — en **morceaux**, interrompu là où une
 * autre flèche l'enjambe —, et son arrivée sur chaque cible.
 */
export type TraceTronc = {
  x: number;
  departs: { id: string; d: string }[];
  morceaux: string[];
  arrivees: { id: string; d: string }[];
};

/** La demi-largeur de l'interruption d'un tronc sous une flèche qui l'enjambe. */
export const DEMI_ENJAMBEE = 5;

/**
 * Où un tracé franchit la verticale `x` — les hauteurs, interpolées entre deux
 * points échantillonnés qui l'encadrent. L'intervalle est semi-ouvert : un point
 * posé **sur** la verticale compte une fois, pas deux, et pas zéro.
 */
function franchissements(trace: TraceArete, x: number): number[] {
  const points = pointsDuTrace(trace, 48);
  const ys: number[] = [];
  for (let i = 0; i + 1 < points.length; i += 1) {
    const [p, q] = [points[i], points[i + 1]];
    const [avant, apres] = [p.x - x, q.x - x];
    if (!((avant < 0 && apres >= 0) || (avant > 0 && apres <= 0))) continue;
    ys.push(p.y + ((q.y - p.y) * (x - p.x)) / (q.x - p.x));
  }
  return ys;
}

/**
 * Le tracé des troncs d'un dessin, mesuré sur les boîtes réelles.
 *
 * Plusieurs troncs dans une même gouttière s'y répartissent à intervalles égaux.
 * `enjambees` sont les tracés des **autres** flèches : là où l'une d'elles
 * franchit un tronc sans y entrer, le tronc s'interrompt sous elle
 * (`DEMI_ENJAMBEE`) — sans quoi rien ne dirait qu'elle n'y entre pas (réserve du
 * regard neuf). Un franchissement au ras d'une amorce, là où des flèches se
 * rejoignent, n'interrompt rien.
 *
 * `null` pour un tronc dont une boîte n'est pas encore mesurée.
 */
export function tracerTroncs(
  troncs: readonly Tronc[],
  colonnes: readonly (readonly string[])[],
  boites: ReadonlyMap<string, Rect>,
  enjambees: readonly TraceArete[] = [],
): Map<string, TraceTronc | null> {
  const resultat = new Map<string, TraceTronc | null>();
  const mesuree = (id: string) => {
    const boite = boites.get(id);
    return boite !== undefined && boite.largeur > 0 ? boite : undefined;
  };
  for (const tronc of troncs) {
    const voisins = troncs.filter((autre) => autre.gouttiere === tronc.gouttiere);
    const departs = tronc.sources.map(mesuree);
    const arrivees = tronc.cibles.map(mesuree);
    const gauches = colonnes[tronc.gouttiere].map(mesuree);
    const droites = (colonnes[tronc.gouttiere + 1] ?? []).map(mesuree);
    if ([...departs, ...arrivees].some((boite) => boite === undefined)) {
      resultat.set(tronc.cle, null);
      continue;
    }
    const bord = Math.max(...gauches.map((b) => (b === undefined ? -Infinity : b.x + b.largeur)));
    const suivant = Math.min(...droites.map((b) => (b === undefined ? Infinity : b.x)));
    const x = bord + ((suivant - bord) * (voisins.indexOf(tronc) + 1)) / (voisins.length + 1);
    const milieu = (boite: Rect) => boite.y + boite.hauteur / 2;
    const amorces = [...(departs as Rect[]), ...(arrivees as Rect[])].map(milieu);
    const [haut, bas] = [Math.min(...amorces), Math.max(...amorces)];

    const coupures = enjambees
      .flatMap((trace) => franchissements(trace, x))
      .filter(
        (y) =>
          y > haut + DEMI_ENJAMBEE &&
          y < bas - DEMI_ENJAMBEE &&
          amorces.every((amorce) => Math.abs(amorce - y) > DEMI_ENJAMBEE),
      )
      .sort((a, b) => a - b);
    const morceaux: string[] = [];
    let depuis = haut;
    for (const y of coupures) {
      if (y - DEMI_ENJAMBEE > depuis) morceaux.push(`M ${x} ${depuis} V ${y - DEMI_ENJAMBEE}`);
      depuis = Math.max(depuis, y + DEMI_ENJAMBEE);
    }
    if (bas > depuis) morceaux.push(`M ${x} ${depuis} V ${bas}`);

    resultat.set(tronc.cle, {
      x,
      departs: tronc.sources.map((id, i) => {
        const boite = departs[i] as Rect;
        return { id, d: `M ${boite.x + boite.largeur} ${milieu(boite)} H ${x}` };
      }),
      morceaux,
      arrivees: tronc.cibles.map((id, i) => {
        const boite = arrivees[i] as Rect;
        return { id, d: `M ${x} ${milieu(boite)} H ${boite.x}` };
      }),
    });
  }
  return resultat;
}

/**
 * Le tracé d'**une** flèche d'un tronc, à elle seule : son amorce, la portion de
 * tronc qui relie ses deux bouts, son arrivée. C'est ce qui s'allume quand on la
 * regarde — ou qu'on survole l'une de ses tâches —, et la cible qu'on survole et
 * qu'on focalise : jamais le tronc entier, qui ne dirait plus lesquels de ses
 * liens sont ceux de la tâche (réserve du regard neuf, ce que la veille laisse à
 * GitHub Actions). Trois lignes, toutes dans la gouttière : aucune ne touche une
 * carte.
 */
export function tracerDansTronc(
  de: string,
  vers: string,
  x: number,
  boites: ReadonlyMap<string, Rect>,
): TraceArete | null {
  const depart = boites.get(de);
  const arrivee = boites.get(vers);
  if (depart === undefined || arrivee === undefined) return null;
  if (depart.largeur === 0 || arrivee.largeur === 0) return null;
  const sortie = { x: depart.x + depart.largeur, y: depart.y + depart.hauteur / 2 };
  const entree = { x: arrivee.x, y: arrivee.y + arrivee.hauteur / 2 };
  const coude1 = { x, y: sortie.y };
  const coude2 = { x, y: entree.y };
  const segments: Segment[] = [
    { forme: "ligne", de: sortie, vers: coude1 },
    { forme: "ligne", de: coude1, vers: coude2 },
    { forme: "ligne", de: coude2, vers: entree },
  ];
  return { segments, d: cheminSvg(segments), bas: Math.max(sortie.y, entree.y) };
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

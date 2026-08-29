/**
 * Le vocabulaire des compétences d'agent (#256, lot 4/15 de #243).
 *
 * Les compétences ne sont pas décoratives : c'est sur elles que le routeur
 * auto-assigne une tâche (docs/01 §3.2). Et il s'en sert de **deux** façons, qui
 * ne pardonnent pas la même chose — c'est toute la raison d'être de ce module :
 *
 * 1. **La règle** (`Agent.couverture`, `maestro/agents/catalog.py`) est une
 *    intersection d'ensembles : `frozenset & frozenset`. Le rapprochement y est
 *    une **égalité stricte**, casse comprise — « React » et « react » n'y sont
 *    pas la même compétence, et « reactjs » n'y vaut rien du tout. C'est là
 *    qu'une faute de frappe coûte : la tâche part ailleurs, en silence.
 * 2. **Le classifieur** (`maestro/router/classifier.py`) reçoit les mêmes
 *    compétences en **prose** et les fait lire à un modèle. Le rapprochement y
 *    est sémantique : « reactjs » peut très bien y être compris.
 *
 * D'où la définition retenue d'une compétence **inédite** : *absente du
 * vocabulaire du catalogue au sens strict* — le seul sens dans lequel le signal
 * déterministe fonctionne. Et d'où, tout aussi directement, le fait qu'elle se
 * **signale sans jamais se refuser** : le classifieur peut encore la rattraper,
 * et surtout un vocabulaire ne s'enrichit que si quelqu'un a le droit d'y
 * ajouter un mot. Ce qui est automatique est la détection du manque, jamais le
 * verdict.
 *
 * ⚠ Le vocabulaire est **dérivé du catalogue**, jamais écrit ici : c'est une
 * donnée (`GET /api/catalogue`), pas une liste de mots-clés qu'il faudrait tenir
 * à jour et qui se tromperait sur le premier métier qu'on n'avait pas prévu.
 * Sans catalogue lu, il n'y a pas de vocabulaire — donc **rien n'est inédit**
 * (voir `inedites`) : se taire est le seul comportement juste quand on ne sait
 * pas, une alerte sur toutes les compétences valant moins que pas d'alerte.
 *
 * Rien ici ne **réécrit** ce que la personne a saisi : le contrat d'API garde sa
 * forme (une liste de chaînes) et le dépôt sa casse (`_valide`,
 * `maestro/agents/store.py`, qui épure et dédoublonne sans rien minusculer).
 * C'est la saisie qui est cadrée, pas le modèle.
 */

import type { AgentCatalogue } from "@/lib/types";

/**
 * Les séparateurs qui découpent une saisie : la virgule — que le champ virgulé
 * d'avant ce lot laisse dans les habitudes et dans les presse-papiers —, le
 * point-virgule et le saut de ligne. Coller « frontend, react, css » doit donc
 * continuer de marcher, en rendant trois jetons au lieu d'un.
 */
const SEPARATEURS = /[,;\n\r\t]+/;

/**
 * Le jeton épuré : bords rognés, espaces internes ramenés à un seul.
 *
 * Volontairement **sans minuscule** : le dépôt garde la casse, donc la changer
 * ici ferait enregistrer autre chose que ce qui est à l'écran. Une compétence
 * qui ne diffère du catalogue que par sa casse est signalée comme inédite —
 * ce qu'elle est, au sens de la règle — et son voisin lui est proposé.
 */
export function normaliserCompetence(brut: string): string {
  return brut.trim().replace(/\s+/g, " ");
}

/**
 * Les jetons d'une saisie libre, épurés et dans l'ordre, les vides écartés.
 *
 * Sert aux deux entrées d'un champ à jetons : la frappe (un jeton, validé par
 * Entrée) et le **collage** (souvent une liste entière, héritée du champ
 * virgulé). Une seule fonction pour les deux, sans quoi coller ne rendrait pas
 * ce que taper rend.
 */
export function decouperSaisie(brut: string): string[] {
  return brut
    .split(SEPARATEURS)
    .map(normaliserCompetence)
    .filter((jeton) => jeton !== "");
}

/**
 * Le vocabulaire du catalogue : toutes les compétences déjà déclarées par un
 * agent, dédoublonnées et rangées par ordre alphabétique.
 *
 * Dédoublonnage **exact**, comme celui du dépôt : deux variantes de casse sont
 * deux entrées du vocabulaire, parce que ce sont deux compétences distinctes
 * pour la règle de routage. Les fondre ici mentirait sur ce que le routeur voit.
 */
export function vocabulaireDuCatalogue(fiches: AgentCatalogue[]): string[] {
  const vus = new Set<string>();
  for (const fiche of fiches) {
    for (const competence of fiche.competences) {
      const jeton = normaliserCompetence(competence);
      if (jeton !== "") vus.add(jeton);
    }
  }
  return [...vus].sort((a, b) => a.localeCompare(b, "fr"));
}

/**
 * La distance d'édition entre deux mots, **transposition comprise**
 * (Damerau-Levenshtein en alignement simple).
 *
 * La transposition compte pour 1 et non pour 2, parce que c'est la faute de
 * frappe la plus courante : « dcoker » est à un geste de « docker », et le
 * Levenshtein nu l'en éloignerait autant que d'un mot sans rapport.
 */
function distance(a: string, b: string): number {
  if (a === b) return 0;
  if (a === "") return b.length;
  if (b === "") return a.length;

  // Trois rangées suffisent : la courante, la précédente, et l'avant-dernière
  // — c'est cette dernière que la transposition consulte.
  let avantPrecedente: number[] = [];
  let precedente = [...Array(b.length + 1).keys()];
  let courante: number[] = [];

  for (let i = 1; i <= a.length; i += 1) {
    courante = [i];
    for (let j = 1; j <= b.length; j += 1) {
      const cout = a[i - 1] === b[j - 1] ? 0 : 1;
      let valeur = Math.min(
        precedente[j] + 1, // suppression
        courante[j - 1] + 1, // insertion
        precedente[j - 1] + cout, // substitution
      );
      if (i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) {
        valeur = Math.min(valeur, avantPrecedente[j - 2] + 1); // transposition
      }
      courante.push(valeur);
    }
    avantPrecedente = precedente;
    precedente = courante;
  }
  return precedente[b.length];
}

/**
 * Sous quelle distance deux mots de cette longueur sont encore « le même mot
 * mal tapé ». Un cran, puis deux passé six caractères : plus loin, on ne
 * proposerait plus une correction mais un autre métier — « backend » est à
 * quatre gestes de « frontend », et les confondre serait pire que se taire.
 */
function seuil(longueur: number): number {
  return longueur <= 6 ? 1 : 2;
}

/**
 * En deçà de cette longueur, aucune correction par distance n'est proposée : sur
 * trois lettres, tout est proche de tout (« ui » et « ux » sont à un geste l'un
 * de l'autre et désignent deux métiers). La seule proximité qui vaille sur un
 * mot court est l'égalité à la casse près, traitée avant.
 */
const LONGUEUR_MINIMALE = 4;

/**
 * La compétence du vocabulaire la plus proche de `jeton`, ou `null`.
 *
 * Deux rapprochements, dans cet ordre, et le premier est le plus utile :
 *
 * 1. **la même à la casse près** — « React » quand le catalogue dit « react ».
 *    C'est le cas le plus traître : deux mots identiques pour un lecteur humain,
 *    étrangers l'un à l'autre pour l'intersection d'ensembles du routeur ;
 * 2. **la plus proche à la frappe**, sous le seuil ci-dessus.
 *
 * `jeton` lui-même est écarté : on ne se propose pas à soi-même. À égalité de
 * distance, l'ordre du vocabulaire (alphabétique) tranche — un départage
 * arbitraire mais stable vaut mieux qu'un résultat qui change d'un rendu à
 * l'autre.
 */
export function competenceProche(
  jeton: string,
  vocabulaire: readonly string[],
): string | null {
  const cible = normaliserCompetence(jeton);
  if (cible === "") return null;

  const minuscule = cible.toLocaleLowerCase("fr");
  const memeMot = vocabulaire.find(
    (connue) => connue !== cible && connue.toLocaleLowerCase("fr") === minuscule,
  );
  if (memeMot !== undefined) return memeMot;

  if (cible.length < LONGUEUR_MINIMALE) return null;

  let meilleure: string | null = null;
  // Un cran au-dessus du recevable : rien ne sera retenu tant qu'aucun candidat
  // ne passe sous le seuil. `<` et non `<=` — à égalité, le premier rencontré
  // garde la place, donc l'ordre alphabétique du vocabulaire.
  let meilleureDistance = seuil(cible.length) + 1;
  for (const connue of vocabulaire) {
    if (connue === cible) continue;
    const ecart = distance(minuscule, connue.toLocaleLowerCase("fr"));
    if (ecart < meilleureDistance) {
      meilleure = connue;
      meilleureDistance = ecart;
    }
  }
  return meilleure;
}

/**
 * Celles de `valeurs` que le catalogue ne connaît pas — au mot près, comme le
 * routeur.
 *
 * `vocabulaire` à `null` veut dire « le catalogue n'a pas pu être lu », et rend
 * alors une liste **vide** : ne rien savoir n'autorise pas à alerter. C'est la
 * même asymétrie que partout ailleurs dans le produit — un catalogue absent
 * laisse le formulaire tel qu'il était avant ce lot, sans rien signaler.
 */
export function inedites(
  valeurs: readonly string[],
  vocabulaire: readonly string[] | null,
): string[] {
  if (vocabulaire === null) return [];
  const connues = new Set(vocabulaire);
  return valeurs.filter((valeur) => !connues.has(normaliserCompetence(valeur)));
}

/**
 * Le vocabulaire de l'écran « composer un objectif » (#319) : ce qui traduit les
 * codes de l'API (#315/#316/#317) en mots d'interface, et ce qui met en forme un
 * coût qu'on lit avant de le dépenser.
 *
 * Séparé des composants pour la même raison que `lib/projets.ts` (#225) : c'est
 * de la **donnée**. Les motifs de refus et les trois états d'une lecture sont des
 * ensembles fermés publiés par docs/05 §6.8, et les tester ne devrait pas
 * demander de monter un DOM.
 */

import { conseilMotif } from "./projets";
import {
  LECTURE_IGNOREE,
  LECTURE_LUE,
  LECTURE_TRONQUEE,
  SOURCE_DOSSIER,
  SOURCE_FICHIER,
  SOURCE_URL,
  type LectureSource,
  type SourceDeclaree,
} from "./types";

/**
 * Le conseil attaché à un motif de refus **de source** — ce que l'utilisateur
 * peut faire, là où le `message` du backend dit ce qui s'est passé.
 *
 * Les motifs d'un `dossier` ne sont **pas** répétés ici : la résolution conserve
 * tels quels ceux de `valider_racine` (docs/05 §6.8), donc leur conseil est déjà
 * écrit — `conseilSource` délègue à celui de l'écran Projets. Deux tables pour
 * un même code finiraient par diverger, et c'est la moins relue qui gagnerait.
 */
const CONSEILS: Record<string, string> = {
  "type-inconnu":
    "Seuls trois types de source existent : un fichier déposé, un dossier de références, une adresse http(s).",
  "url-non-suivable":
    "Seules les adresses http(s) sont lues : un chemin local passe par le dossier de références.",
  "url-absente": "Coller l'adresse de la page à lire, ou retirer cette source.",
  "url-trop-longue":
    "Adresse trop longue pour être suivie : viser la page elle-même plutôt qu'une URL de recherche.",
  "source-trop-volumineuse":
    "Ce document dépasse le plafond par source : le scinder, ou n'en joindre que la partie utile.",
  "ingestion-trop-volumineuse":
    "Le cumul des sources dépasse le plafond : en retirer une avant de relancer l'aperçu.",
  "trop-de-sources":
    "Trop de sources d'un coup : un dossier de références vaut mieux qu'une longue liste de fichiers.",
  "televersement-inconnu":
    "Ce dépôt n'est plus connu du backend (expiré ou redémarré) : redéposer le fichier.",
  "nom-invalide": "Un nom de fichier, pas un chemin : renommer avant de déposer.",
  "nom-absent": "Ce fichier n'a pas de nom exploitable : le renommer avant de le déposer.",
  "nom-trop-long": "Nom de fichier trop long : le raccourcir avant de le déposer.",
  "taille-absente":
    "La taille de ce fichier n'a pas pu être mesurée : le redéposer depuis l'explorateur du système.",
  "taille-invalide": "Taille de fichier inexploitable : redéposer le document.",
  "apercu-sans-octets":
    "L'aperçu et les fichiers déposés se sont désynchronisés : retirer puis redéposer le document.",
  "sources-illisibles":
    "La liste des sources n'a pas pu être lue par le backend : retirer la dernière source ajoutée.",
  "api-injoignable":
    "Backend injoignable : l'aperçu et le lancement passent tous deux par lui.",
};

/**
 * Le conseil d'un motif de refus de source, ou `null` s'il n'y en a pas.
 *
 * Le message de l'API est toujours affiché : ceci ne le remplace pas, il le
 * prolonge. Un motif inconnu (l'API en gagnera) ne rend rien plutôt qu'une
 * phrase inventée — l'écran reste juste, seulement moins aidant.
 */
export function conseilSource(motif: string): string | null {
  return CONSEILS[motif] ?? conseilMotif(motif);
}

/**
 * Ce que l'écran dit d'un motif d'**extraction** — le régime opposé à celui des
 * refus : ici rien n'est fautif, une source simplement n'a pas été lue.
 *
 * La distinction est le critère 3 du ticket : « rien à lire ici » et « je refuse
 * de lire ça » ne s'affichent jamais pareil. Les premiers sont des constats
 * (format non géré, page injoignable) et se rangent dans le rapport ; les
 * seconds sont des refus et remontent au geste qui les a provoqués.
 */
const MOTIFS_LECTURE: Record<string, string> = {
  "format-non-gere": "Format non géré — seuls le texte, le Markdown, le .docx et le .pdf sont lus.",
  "convertisseur-absent": "Convertisseur absent sur le backend pour ce format.",
  "source-absente": "Aucun octet n'est arrivé : le fichier n'a pas été téléversé.",
  "budget-epuise": "Budget de lecture épuisé par les sources précédentes.",
  "dossier-vide": "Aucun fichier lisible dans ce dossier.",
  "url-injoignable": "La page n'a pas répondu dans le délai imparti.",
  "fichier-illisible": "Le backend n'a pas réussi à ouvrir ce fichier.",
};

/** La phrase d'un motif de lecture, ou `null` si l'écran ne le connaît pas. */
export function libelleMotifLecture(motif: string): string | null {
  return MOTIFS_LECTURE[motif] ?? null;
}

/** Le mot d'interface d'un état de lecture (#316). */
export function libelleEtat(etat: string): string {
  if (etat === LECTURE_LUE) return "Lu";
  if (etat === LECTURE_TRONQUEE) return "Tronqué";
  if (etat === LECTURE_IGNOREE) return "Ignoré";
  return etat;
}

/** Le mot d'interface d'un type de source. */
export function libelleType(type: string): string {
  if (type === SOURCE_FICHIER) return "Fichier";
  if (type === SOURCE_DOSSIER) return "Dossier";
  if (type === SOURCE_URL) return "Adresse";
  return type;
}

/**
 * Une taille en octets, lisible. Les puissances de 1024 (Kio, Mio) et non de
 * 1000 : ce sont celles des plafonds d'ingestion (docs/05 §6.8), et afficher
 * « 10,5 Mo » à côté d'un plafond de « 10 Mio » ferait chercher l'erreur.
 */
export function formaterOctets(octets: number): string {
  if (octets < 1024) return `${octets} o`;
  const kio = octets / 1024;
  if (kio < 1024) return `${kio.toFixed(kio < 10 ? 1 : 0)} Kio`;
  return `${(kio / 1024).toFixed(2)} Mio`;
}

/** Un coût estimé en tokens, séparateurs de milliers compris. */
export function formaterTokens(tokens: number): string {
  return tokens.toLocaleString("fr-FR");
}

/**
 * Une source de l'écran en déclaration d'API.
 *
 * Un fichier voyage par son **identifiant de téléversement** dès qu'il en a un
 * (docs/05 §6.8) : le nom et la taille sont alors ceux des octets reçus, jamais
 * ceux qu'un navigateur annonce. Tant qu'il n'en a pas — c'est le cas de
 * l'aperçu, qui porte les octets eux-mêmes —, il se déclare par son nom et sa
 * taille, forme que la résolution accepte aussi.
 */
export function declarationDe(source: SourceComposee): SourceDeclaree {
  if (source.type === SOURCE_FICHIER) {
    return source.id === null
      ? { type: SOURCE_FICHIER, nom: source.nom, taille: source.taille }
      : { type: SOURCE_FICHIER, id: source.id };
  }
  if (source.type === SOURCE_DOSSIER) {
    return { type: SOURCE_DOSSIER, chemin: source.valeur, nom: source.nom };
  }
  return { type: SOURCE_URL, valeur: source.valeur, nom: source.nom };
}

/**
 * Une source telle que l'écran la tient : ce qu'il en montre (`nom`, `valeur`,
 * `taille`) et ce qui la relie à l'API (`id` de téléversement, `fichier` pour
 * l'aperçu). La `cle` est locale et stable — deux fichiers peuvent porter le
 * même nom, et un index de tableau change dès qu'on retire une ligne.
 */
export type SourceComposee = {
  cle: string;
  type: string;
  nom: string;
  /** Le chemin d'un dossier, l'adresse d'une URL ; vide pour un fichier. */
  valeur: string;
  /** La taille d'un fichier en octets ; `undefined` pour les deux autres. */
  taille?: number;
  /** L'identifiant rendu par `POST /api/sources`, `null` tant qu'il n'a pas été téléversé. */
  id: string | null;
  /** Les octets du fichier, gardés pour l'aperçu ; `null` pour les deux autres. */
  fichier: File | null;
};

/** La lecture qui correspond à cette source dans un rapport, ou `null`. */
export function lecturePour(
  lectures: LectureSource[],
  index: number,
): LectureSource | null {
  return lectures[index] ?? null;
}

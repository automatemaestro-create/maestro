/**
 * Le détail d'une tâche tel que la carte du Kanban l'ouvre (#251) : description,
 * étapes en checklist et liens utiles, lus sur les champs que la tâche porte
 * depuis #246.
 *
 * Tout passe par ici, et une seule règle le gouverne : **rien à montrer ⇒ rien
 * à rendre**. Une tâche sans description, sans étape ni lien doit afficher
 * exactement la carte d'avant — pas un cadre vide, pas un « — » de remplissage.
 * C'est un critère du ticket, et c'est aussi le cas **courant** tant que le lot
 * modèle (#246) n'est pas livré : le backend ne sert aujourd'hui aucun de ces
 * trois champs, ils arrivent donc `undefined` sur chaque tâche. D'où des lectures
 * défensives (`Array.isArray`, `?.`) plutôt qu'une confiance dans le type : le
 * contrat décrit ce que le flux **finira** par porter, pas ce qu'il porte.
 *
 * La normalisation retire ce qui n'apprend rien — étape sans libellé, lien sans
 * libellé ni URL suivable — au lieu de le rendre en blanc.
 */

import { lienExterneSur } from "@/lib/liens";
import {
  ETAPE_A_FAIRE,
  ETAPE_EN_COURS,
  ETAPE_FAITE,
  LIEN_DEPOT,
  LIEN_MAQUETTE,
  LIEN_TICKET,
  type Tache,
} from "@/lib/types";

/** Une étape prête à rendre : libellé non vide, état ramené aux trois connus. */
export type EtapeAffichee = {
  libelle: string;
  etat: typeof ETAPE_A_FAIRE | typeof ETAPE_EN_COURS | typeof ETAPE_FAITE;
};

/** La nature d'un lien, ramenée aux trois connues ou au repli générique. */
export type NatureAffichee =
  | typeof LIEN_MAQUETTE
  | typeof LIEN_TICKET
  | typeof LIEN_DEPOT
  | "lien";

/**
 * Un lien prêt à rendre. `url` est `null` quand elle n'est pas suivable : le
 * libellé reste lisible, il n'y a juste rien à cliquer — même règle que la
 * référence de ticket externe (#192), jamais de lien mort.
 */
export type LienAffiche = {
  libelle: string;
  nature: NatureAffichee;
  url: string | null;
};

/** Le détail complet d'une tâche, normalisé. `vide` : il n'y a rien à ouvrir. */
export type DetailTache = {
  description: string;
  etapes: EtapeAffichee[];
  liens: LienAffiche[];
  /** Nombre d'étapes terminées — le numérateur de l'avancement affiché. */
  faites: number;
  vide: boolean;
};

/** Le libellé de repli d'un lien qui n'en porte pas, par nature. */
const LIBELLE_PAR_NATURE: Record<NatureAffichee, string> = {
  [LIEN_MAQUETTE]: "Maquette",
  [LIEN_TICKET]: "Ticket",
  [LIEN_DEPOT]: "Dépôt",
  lien: "Lien",
};

const ETATS_CONNUS = new Set<string>([ETAPE_A_FAIRE, ETAPE_EN_COURS, ETAPE_FAITE]);
const NATURES_CONNUES = new Set<string>([LIEN_MAQUETTE, LIEN_TICKET, LIEN_DEPOT]);

/** Le nom lisible d'un lien quand il n'en donne pas — jamais une URL nue. */
export function libelleDeNature(nature: NatureAffichee): string {
  return LIBELLE_PAR_NATURE[nature];
}

function texte(valeur: unknown): string {
  return typeof valeur === "string" ? valeur.trim() : "";
}

/**
 * Un état inconnu du front ne fait pas disparaître l'étape : elle retombe sur
 * « à faire », comme un statut inconnu retombe dans la colonne « Autres ».
 */
function etatDe(brut: unknown): EtapeAffichee["etat"] {
  const valeur = texte(brut).toLowerCase();
  return ETATS_CONNUS.has(valeur)
    ? (valeur as EtapeAffichee["etat"])
    : ETAPE_A_FAIRE;
}

function natureDe(brut: unknown): NatureAffichee {
  const valeur = texte(brut).toLowerCase();
  return NATURES_CONNUES.has(valeur) ? (valeur as NatureAffichee) : "lien";
}

/**
 * Des étapes brutes du flux aux étapes affichables — la normalisation seule,
 * détachée de la tâche qui les porte.
 *
 * Elle est publique depuis #491 parce qu'un **nœud du graphe** d'un run porte
 * lui aussi ses `etapes` (#490, `NoeudGraphe.etapes`) sans être une `Tache` : il
 * n'a ni description, ni liens, ni `usage`. Passer par la même fonction est ce
 * qui garantit qu'une checklist se compte pareil des deux côtés — l'étape sans
 * libellé y est retirée au même endroit, et l'état inconnu y retombe sur « à
 * faire » plutôt que de disparaître.
 */
export function normaliserEtapes(brutes: unknown): EtapeAffichee[] {
  if (!Array.isArray(brutes)) return [];
  return brutes
    .map((etape) => ({
      libelle: texte(etape?.libelle),
      etat: etatDe(etape?.etat),
    }))
    // Une étape sans libellé est une case à cocher sans énoncé : rien à lire,
    // et elle fausserait l'avancement en gonflant le dénominateur.
    .filter((etape) => etape.libelle !== "");
}

/** Les étapes affichables de la tâche, dans l'ordre où le flux les a posées. */
export function etapesDe(tache: Tache): EtapeAffichee[] {
  return normaliserEtapes(tache.etapes);
}

/** Les liens affichables de la tâche, URL filtrée par `lienExterneSur`. */
export function liensDe(tache: Tache): LienAffiche[] {
  const bruts = tache.liens;
  if (!Array.isArray(bruts)) return [];
  return bruts
    .map((lien) => ({
      nature: natureDe(lien?.nature),
      url: lienExterneSur(typeof lien?.url === "string" ? lien.url : null),
      libelle: texte(lien?.libelle),
    }))
    // Ni URL suivable ni libellé propre : il ne resterait que le nom de la
    // nature, que l'icône dit déjà — on ne rend pas « Lien » tout seul.
    .filter((lien) => lien.url !== null || lien.libelle !== "")
    .map((lien) => ({
      ...lien,
      libelle: lien.libelle || LIBELLE_PAR_NATURE[lien.nature],
    }));
}

/**
 * Le détail d'une tâche, prêt à rendre. `vide` répond à la seule question que se
 * pose la carte : y a-t-il quelque chose à ouvrir ?
 */
export function detailDe(tache: Tache): DetailTache {
  const description = texte(tache.description);
  const etapes = etapesDe(tache);
  const liens = liensDe(tache);
  return {
    description,
    etapes,
    liens,
    faites: etapes.filter((etape) => etape.etat === ETAPE_FAITE).length,
    vide: description === "" && etapes.length === 0 && liens.length === 0,
  };
}

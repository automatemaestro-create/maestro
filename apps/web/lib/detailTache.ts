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
  CONSTAT_NON_JOUE,
  CONSTAT_NON_TENU,
  CONSTAT_TENU,
  ETAPE_A_FAIRE,
  ETAPE_EN_COURS,
  ETAPE_FAITE,
  LIEN_DEPOT,
  LIEN_MAQUETTE,
  LIEN_TICKET,
  VERIFICATION_IMPOSSIBLE,
  VERIFICATION_NON_TENUE,
  VERIFICATION_TENUE,
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

/** Une attente mesurée de la tâche, prête à rendre : son nom, sa durée. */
export type AttenteAffichee = {
  libelle: string;
  dureeMs: number;
};

/** Un contrôle d'une vérification (#1177), prêt à rendre : état ramené aux trois connus. */
export type ConstatAffiche = {
  critere: string;
  etat: typeof CONSTAT_TENU | typeof CONSTAT_NON_TENU | typeof CONSTAT_NON_JOUE;
  preuve: string;
  commande: string;
  code: number | null;
};

/**
 * La dernière vérification d'une tâche (#1177), prête à rendre. `statut` est
 * ramené aux trois issues connues ; `tenus` est le numérateur du compteur ;
 * `livraison` vaut `null` quand le flux ne la numérote pas (un renvoi de QA).
 */
export type VerificationAffichee = {
  statut:
    | typeof VERIFICATION_TENUE
    | typeof VERIFICATION_NON_TENUE
    | typeof VERIFICATION_IMPOSSIBLE;
  resume: string;
  empechement: string;
  livraison: number | null;
  renvoi: string;
  constats: ConstatAffiche[];
  tenus: number;
};

/** Le détail complet d'une tâche, normalisé. `vide` : il n'y a rien à ouvrir. */
export type DetailTache = {
  description: string;
  etapes: EtapeAffichee[];
  liens: LienAffiche[];
  /** Nombre d'étapes terminées — le numérateur de l'avancement affiché. */
  faites: number;
  /**
   * Les attentes **non nulles** de la tâche (#989), dans l'ordre où elles
   * arrivent. Vide quand la tâche n'a rien attendu — ou quand personne ne l'a
   * mesuré : c'est cette liste, et elle seule, qui décide s'il y a un bloc de
   * temps à rendre.
   */
  attentes: AttenteAffichee[];
  /**
   * Le **travail** de la tâche, en regard duquel ses attentes se lisent (#989).
   *
   * Il n'ouvre jamais le bloc à lui seul : sans attente, il n'y a rien à mettre
   * en regard, et le panneau n'a pas à répéter un chiffre que la carte porte
   * déjà. C'est la réserve n°2 du regard neuf — « les 12 min 38 s sont seules,
   * sans rien à quoi les comparer » — et c'est ce que fait la référence
   * (GitLab CI met « Durée » et « En file d'attente » dans le même bloc).
   */
  travailMs: number | null;
  /**
   * La dernière vérification de la tâche (#1177) — `null` tant qu'aucune n'a eu
   * lieu, et le panneau n'en dit alors rien. À elle seule, elle ouvre le
   * panneau : « a-t-elle tenu ? » est la question qu'on lui pose d'abord.
   */
  verification: VerificationAffichee | null;
  vide: boolean;
};

/**
 * Les trois attentes d'une tâche, dans l'ordre où le moteur les rencontre : le
 * créneau d'instance de l'agent (#86), l'atelier du projet (#839), puis
 * l'arbitrage humain qui peut suspendre le travail (#584).
 *
 * L'ordre est celui du moteur et non celui des durées : trié par valeur, un
 * lecteur ne saurait plus dire à quel moment la tâche a attendu.
 */
const ATTENTES: readonly [keyof NonNullable<Tache["usage"]>, string][] = [
  ["duree_attente_creneau_ms", "File d'attente de l'agent"],
  ["duree_attente_atelier_ms", "Atelier du projet occupé"],
  ["duree_arbitrage_ms", "Décision humaine attendue"],
];

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
  const attentes = attentesDe(tache);
  const verification = verificationDe(tache);
  return {
    description,
    etapes,
    liens,
    faites: etapes.filter((etape) => etape.etat === ETAPE_FAITE).length,
    attentes,
    travailMs: tache.usage?.duree_execution_ms ?? tache.usage?.duree_ms ?? null,
    verification,
    vide:
      description === "" &&
      etapes.length === 0 &&
      liens.length === 0 &&
      attentes.length === 0 &&
      verification === null,
  };
}

const ETATS_CONSTAT = new Set<string>([CONSTAT_TENU, CONSTAT_NON_TENU, CONSTAT_NON_JOUE]);

/**
 * La dernière vérification de la tâche (#1177), normalisée — `null` sans
 * vérification lisible.
 *
 * Même règle que les étapes : un contrôle sans critère est retiré (il n'y a rien
 * à lire), un état inconnu retombe sur « non joué » — ni tenu, ni non tenu :
 * ce qu'on ne sait pas lire ne vaut jamais un vert. L'issue est celle du flux
 * quand il en porte une connue, **déduite des constats** sinon, par la règle du
 * moteur : tout tient, ou un critère ne tient pas, ou rien n'est vérifié en
 * entier.
 */
export function verificationDe(tache: Tache): VerificationAffichee | null {
  const brute = tache.verification;
  if (!brute || typeof brute !== "object") return null;
  const constats: ConstatAffiche[] = (Array.isArray(brute.constats) ? brute.constats : [])
    .map((constat) => {
      const etat = texte(constat?.etat);
      return {
        critere: texte(constat?.critere),
        etat: (ETATS_CONSTAT.has(etat) ? etat : CONSTAT_NON_JOUE) as ConstatAffiche["etat"],
        preuve: typeof constat?.preuve === "string" ? constat.preuve.trim() : "",
        commande: texte(constat?.commande),
        code: typeof constat?.code === "number" ? constat.code : null,
      };
    })
    .filter((constat) => constat.critere !== "");
  const empechement = texte(brute.empechement);
  if (constats.length === 0 && empechement === "") return null;
  const tenus = constats.filter((c) => c.etat === CONSTAT_TENU).length;
  const statutBrut = texte(brute.statut);
  const statut: VerificationAffichee["statut"] =
    statutBrut === VERIFICATION_TENUE ||
    statutBrut === VERIFICATION_NON_TENUE ||
    statutBrut === VERIFICATION_IMPOSSIBLE
      ? statutBrut
      : !empechement && constats.length > 0 && tenus === constats.length
        ? VERIFICATION_TENUE
        : constats.some((c) => c.etat === CONSTAT_NON_TENU)
          ? VERIFICATION_NON_TENUE
          : VERIFICATION_IMPOSSIBLE;
  return {
    statut,
    resume: texte(brute.resume),
    empechement,
    livraison:
      typeof brute.livraison === "number" && brute.livraison > 0 ? brute.livraison : null,
    renvoi: texte(brute.renvoi),
    constats,
    tenus,
  };
}

/**
 * Les attentes **non nulles** de la tâche (#989) — la même règle que partout
 * ici : rien à montrer ⇒ rien à rendre. Une attente mesurée à zéro n'est pas
 * une information, et l'annoncer sur chacune des tâches d'un run apprendrait à
 * ne plus lire la ligne (la règle est déjà écrite pour l'arbitrage, #584).
 */
export function attentesDe(tache: Tache): AttenteAffichee[] {
  const usage = tache.usage;
  if (!usage) return [];
  return ATTENTES.map(([champ, libelle]) => ({
    libelle,
    dureeMs: typeof usage[champ] === "number" ? (usage[champ] as number) : 0,
  })).filter((attente) => attente.dureeMs > 0);
}

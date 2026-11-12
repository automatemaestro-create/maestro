/**
 * Le premier lancement, côté écran (#642) : ce que la porte se demande pour
 * guider un poste neuf jusqu'à son premier projet.
 *
 * Trois étapes, dans l'ordre où elles se servent l'une l'autre :
 *
 * 1. **le modèle** — le fournisseur par défaut, choisi puis éprouvé (#1609) :
 *    c'est lui qui répondra dans la conversation de l'étape 3 ;
 * 2. **le dossier des projets** — le répertoire où un projet neuf naît (#1022),
 *    et les dossiers autorisés depuis l'explorateur (#1610) ;
 * 3. **le premier projet** — né dans la conversation (#1294), la fin du parcours.
 *
 * Une étape **se coche sur un fait lu à l'API**, jamais sur un clic de « suivant »
 * (partis pris de la veille, d'après les walkthroughs de VS Code) : un choix de
 * fournisseur enregistré — il n'est enregistré qu'éprouvé —, un répertoire accepté,
 * un projet déclaré. Ce module tient ces règles et les phrases qui les disent, sans
 * rien lire lui-même : la porte lui passe ce qu'elle a lu.
 *
 * ⚠ Il n'importe pas `@/lib/api`, et ne doit pas l'importer : `tests/aides.tsx` s'en
 * sert, et un module évalué avant les mocks d'un fichier de test garderait le client
 * réel (voir `tests/setup.ts`).
 */

import { cleEffective, cleEnregistree, optionsAcces } from "@/lib/fournisseurParDefaut";
import { libelleMotif } from "@/lib/projets";
import type {
  CatalogueFournisseurs,
  EtatFournisseurDefaut,
  RepertoireProjets,
} from "@/lib/types";

/**
 * La clé de session qui porte « Revoir le premier lancement », demandé depuis les
 * Paramètres. La session et non le poste : revoir le parcours est une demande de
 * cette fenêtre, et il ne se rejoue pas au démarrage suivant (C3 de #642).
 */
export const CLE_PREMIER_LANCEMENT_DEMANDE = "maestro.porte.premier-lancement";

/** Note qu'on quitte le projet ouvert **pour revoir le premier lancement**. */
export function demanderPremierLancement(): void {
  try {
    window.sessionStorage.setItem(CLE_PREMIER_LANCEMENT_DEMANDE, "1");
  } catch {
    // Stockage indisponible (navigation privée stricte) : la porte montrera la
    // liste des projets, et chaque réglage reste dans les Paramètres.
  }
}

/**
 * La demande que la porte trouve en se montant — lue **une fois** : elle est
 * retirée en la lisant, pour qu'un rechargement ne rouvre pas le parcours quitté.
 */
export function premierLancementDemande(): boolean {
  try {
    const demande = window.sessionStorage.getItem(CLE_PREMIER_LANCEMENT_DEMANDE) === "1";
    window.sessionStorage.removeItem(CLE_PREMIER_LANCEMENT_DEMANDE);
    return demande;
  } catch {
    return false;
  }
}

export type NumeroEtape = 1 | 2 | 3;

/** L'ordre du parcours : chaque étape sert la suivante. */
export const NUMEROS_ETAPES: readonly NumeroEtape[] = [1, 2, 3];

/**
 * Où en est une étape. `attend` : elle a besoin d'une étape d'avant — le premier
 * projet naît d'une conversation, à laquelle il faut un modèle qui réponde.
 */
export type EtatEtape = "fait" | "a-faire" | "attend";

/**
 * Ce que la porte a lu de chaque étape. `null` tant que la lecture n'est pas
 * revenue : une étape qu'on n'a pas encore lue n'est pas faite, mais elle ne se
 * dit pas en panne non plus.
 */
export type FaitsDuParcours = {
  modele: boolean | null;
  dossier: boolean | null;
  projet: boolean;
};

export type EtapesDuParcours = {
  etats: Record<NumeroEtape, EtatEtape>;
  /** L'étape dont l'écran est ouvert — `null` quand tout est fait. */
  courante: NumeroEtape | null;
};

/**
 * L'état de chaque étape, et celle dont l'écran est ouvert.
 *
 * `choisie` est l'étape qu'on a ouverte d'un clic : elle reste ouverte, même
 * faite — on rouvre une étape pour la changer. Sans elle, c'est la première qui
 * reste à faire, ce qui fait passer à la suivante dès qu'une étape se coche.
 */
export function etapesDuParcours(
  faits: FaitsDuParcours,
  choisie: NumeroEtape | null,
): EtapesDuParcours {
  const etats: Record<NumeroEtape, EtatEtape> = {
    1: faits.modele === true ? "fait" : "a-faire",
    2: faits.dossier === true ? "fait" : "a-faire",
    3: faits.projet ? "fait" : faits.modele === true ? "a-faire" : "attend",
  };
  if (choisie !== null && etats[choisie] !== "attend") {
    return { etats, courante: choisie };
  }
  const courante = NUMEROS_ETAPES.find((numero) => etats[numero] === "a-faire") ?? null;
  return { etats, courante };
}

/** Une lecture revenue en panne, que l'écran de l'étape dira en s'ouvrant. */
export const ILLISIBLE = "illisible";

const PHRASE_ILLISIBLE = "Réglage illisible pour l'instant : l'étape dit pourquoi.";
const PHRASE_LECTURE = "Lecture du réglage…";

/**
 * Le résumé de l'étape du modèle. Non faite, il dit **à quoi sert** le choix,
 * dans les mots de quelqu'un qui n'a jamais ouvert les Paramètres (réserve 4 du
 * regard neuf) ; faite, ce qui répondra, et depuis quand c'est éprouvé.
 */
export function resumeDuModele(
  etat: EtatFournisseurDefaut | null | typeof ILLISIBLE,
  catalogue: CatalogueFournisseurs | null,
): string {
  if (etat === ILLISIBLE) return PHRASE_ILLISIBLE;
  if (etat === null) return PHRASE_LECTURE;
  if (etat.choix === null) {
    return "Maestro confie chaque conversation et chaque tâche à un fournisseur de modèle. Choisissez celui de ce poste : il est éprouvé avant d'être retenu.";
  }
  const effective = cleEffective(etat);
  const option =
    catalogue === null
      ? undefined
      : optionsAcces(catalogue).find((candidate) => candidate.cle === effective);
  const libelle = option?.acces.libelle ?? etat.effectif.fournisseur;
  const modele = etat.effectif.modele ?? etat.effectif.modele_par_defaut;
  const ce = modele === null ? libelle : `${libelle} · modèle ${modele}`;
  // L'environnement du poste l'emporte sur le choix : le taire ferait croire que
  // ce qui a été éprouvé ici est ce qui répond.
  if (cleEnregistree(etat) !== effective) {
    return `${ce}, imposé par l'environnement du poste.`;
  }
  return `${ce}, éprouvé le ${dateCourte(etat.choix.eprouve_le)}.`;
}

/** L'étape du modèle est faite : un choix est enregistré, et il ne l'est qu'éprouvé. */
export function modeleFait(etat: EtatFournisseurDefaut | null | typeof ILLISIBLE): boolean | null {
  if (etat === null) return null;
  return etat !== ILLISIBLE && etat.choix !== null;
}

/** Le résumé de l'étape du dossier : où naîtront les projets, ou pourquoi pas là. */
export function resumeDuDossier(repertoire: RepertoireProjets | null | typeof ILLISIBLE): string {
  if (repertoire === ILLISIBLE) return PHRASE_ILLISIBLE;
  if (repertoire === null) return PHRASE_LECTURE;
  if (repertoire.refus !== null) {
    return `${repertoire.chemin} ne peut pas les recevoir (${libelleMotif(repertoire.refus.motif)}) : choisissez-en un autre.`;
  }
  if (!repertoire.existe) return `${repertoire.chemin} n'existe pas encore.`;
  return repertoire.cree
    ? `Vos projets naissent dans ${repertoire.chemin}, que Maestro vient de créer.`
    : `Vos projets naissent dans ${repertoire.chemin}.`;
}

/** L'étape du dossier est faite : le répertoire existe et peut recevoir un projet. */
export function dossierFait(repertoire: RepertoireProjets | null | typeof ILLISIBLE): boolean | null {
  if (repertoire === null) return null;
  return repertoire !== ILLISIBLE && repertoire.refus === null && repertoire.existe;
}

/** Le résumé de l'étape du premier projet. */
export function resumeDuProjet(projetsDeclares: number, modeleEstFait: boolean): string {
  if (projetsDeclares > 0) {
    const pluriel = projetsDeclares > 1 ? "s" : "";
    return `${projetsDeclares} projet${pluriel} déclaré${pluriel} sur ce poste.`;
  }
  if (!modeleEstFait) return "Après le modèle : c'est lui qui vous répondra.";
  return "Il naît d'une conversation : dites ce que vous voulez construire.";
}

/** Une date ISO en clair, au jour près : « 11 novembre 2026 ». */
function dateCourte(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleDateString("fr-FR", { dateStyle: "long" });
}

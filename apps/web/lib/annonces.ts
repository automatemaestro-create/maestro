/**
 * Ce qu'un écran temps réel **dit** quand il bouge sans qu'on l'ait touché
 * (#538, lot 6 de #532 — docs/30 §3.3).
 *
 * Le constat qui ouvre le ticket : la Control Tower reçoit jusqu'à trois
 * WebSockets simultanées, les tâches changent de colonne, les coûts montent, les
 * validations arrivent — et **rien n'était annoncé**. Aucun `aria-label`, si
 * soigné soit-il, ne compense ça : un écran qui bouge tout seul et ne le dit pas
 * est muet pour qui ne le regarde pas.
 *
 * Le piège de la réponse naïve est nommé dans le ticket, et c'est lui qui
 * commande tout ce module : brancher une région live sur le **flux** annoncerait
 * plusieurs fois par seconde (rechargements coalescés à 150 ms, flux plafonné à
 * `MAX_EVENEMENTS`) et rendrait le lecteur d'écran inutilisable. Il faut annoncer
 * un **état**, pas un journal.
 *
 * D'où la forme retenue : un écran ne fournit pas des phrases, il fournit un
 * **relevé** — une liste de `Mesure`, c'est-à-dire des compteurs nommés. Deux
 * relevés se comparent, et c'est la comparaison qui produit la phrase (« 3 tâches
 * terminées, 1 run terminé. »). Trois propriétés en découlent, et aucune n'est un
 * effet de bord :
 *
 * - **Seules les hausses parlent.** Une tâche qui passe de « en cours » à
 *   « terminée » fait baisser une colonne et monter l'autre ; annoncer les deux
 *   dirait deux fois le même événement, en sens contraires.
 * - **Une rafale ne coûte qu'une phrase**, parce que la comparaison porte sur les
 *   deux bouts de la fenêtre d'agrégation et jamais sur ce qui s'est passé au
 *   milieu (la fenêtre elle-même vit dans `lib/useAnnonce`).
 * - **Rien de tout ceci n'est du React**, donc tout se teste sans monter d'écran.
 *
 * ⚠ Ce module ne connaît **pas** l'urgence : `aria-live="polite"` et
 * `aria-live="assertive"` se distinguent par ce qu'on leur donne à dire, pas par
 * la façon de le dire. Les arbitrages (`mesuresDesArbitrages`) partent dans la
 * région assertive du shell, tout le reste dans la région polie de l'écran —
 * la frontière est dans `components/RegionLive`.
 */

import { runsEnAttente } from "./brief";
import { formatCout } from "./format";
import {
  EXECUTION_ANNULEE,
  EXECUTION_ECHEC,
  EXECUTION_EN_COURS,
  EXECUTION_TERMINEE,
  VALIDATION_EN_ATTENTE,
  type ResumeExecution,
  type Tache,
  type Validation,
} from "./types";

/**
 * Un compteur nommé, et ce qu'il **dit** quand il monte.
 *
 * `annonce` reçoit la hausse et non la valeur : ce qui s'annonce est ce qui vient
 * d'arriver (« 3 tâches terminées »), jamais le stock (« 47 tâches terminées »),
 * qui se relit à l'écran et ne serait une nouvelle pour personne. Les mesures qui
 * veulent dire un état — la dépense franchissant un seuil, la file d'arbitrage —
 * l'ignorent et passent par `jalon`.
 */
export type Mesure = {
  /** Ce qui apparie deux relevés d'un instant à l'autre. */
  cle: string;
  /** La valeur observée. Seule sa **hausse** produit une annonce. */
  valeur: number;
  /** La phrase de cette hausse, sans ponctuation finale. */
  annonce: (hausse: number) => string;
};

/**
 * Un compteur qui se dit au singulier ou au pluriel selon la hausse : « 1 tâche
 * terminée », « 3 tâches terminées ».
 *
 * Les deux formes sont données en toutes lettres plutôt que dérivées d'un « s » :
 * les libellés du produit portent des accords que la règle simple manque
 * (« tâche en échec » est invariable, « run interrompu » ne l'est pas).
 */
export function compte(
  cle: string,
  valeur: number,
  singulier: string,
  pluriel: string,
): Mesure {
  return {
    cle,
    valeur,
    annonce: (hausse) => `${hausse} ${hausse > 1 ? pluriel : singulier}`,
  };
}

/**
 * Un franchissement : la valeur ne compte rien, elle marque un seuil passé, et la
 * phrase dit l'**état** atteint plutôt que le nombre de seuils traversés.
 *
 * C'est la forme des annonces qui répondent à « où en est-on ? » et non à « que
 * vient-il de se passer ? » — la dépense, la file d'arbitrage.
 */
export function jalon(cle: string, franchis: number, phrase: string): Mesure {
  return { cle, valeur: franchis, annonce: () => phrase };
}

/**
 * Ce qui a changé d'un relevé à l'autre, en une phrase — `null` quand rien
 * d'annonçable n'a bougé, et c'est le cas nominal.
 *
 * Une clé **absente du relevé précédent** est ignorée plutôt que comptée depuis
 * zéro : elle signale un écran qui vient de changer de forme (un run choisi, un
 * filtre posé), pas une activité. La compter annoncerait tout le contenu de la
 * page comme s'il venait d'arriver.
 */
export function phraseDesChangements(
  avant: Mesure[],
  apres: Mesure[],
): string | null {
  const precedent = new Map(avant.map((mesure) => [mesure.cle, mesure.valeur]));
  const morceaux: string[] = [];
  for (const mesure of apres) {
    const reference = precedent.get(mesure.cle);
    if (reference === undefined) continue;
    const hausse = mesure.valeur - reference;
    if (hausse > 0) morceaux.push(mesure.annonce(hausse));
  }
  return morceaux.length === 0 ? null : `${morceaux.join(", ")}.`;
}

/**
 * Les cinq colonnes du Kanban, dites comme une **phrase** et non comme un titre
 * de colonne (« 2 tâches terminées », là où la colonne s'intitule « Terminées »).
 *
 * La table est ici et non partagée avec `components/Kanban` à dessein : une
 * colonne se nomme, une annonce s'accorde, et les deux formes divergent sur trois
 * des cinq statuts. Ce qui doit rester d'accord — les statuts eux-mêmes — vient
 * de la machine à états de docs/03 §3, comme là-bas.
 */
const ETIQUETTES_TACHE: {
  statut: string;
  singulier: string;
  pluriel: string;
}[] = [
  { statut: "assignee", singulier: "tâche assignée", pluriel: "tâches assignées" },
  { statut: "en_cours", singulier: "tâche en cours", pluriel: "tâches en cours" },
  { statut: "bloquee", singulier: "tâche bloquée", pluriel: "tâches bloquées" },
  { statut: "terminee", singulier: "tâche terminée", pluriel: "tâches terminées" },
  { statut: "echec", singulier: "tâche en échec", pluriel: "tâches en échec" },
];

/** Le relevé des tâches : une mesure par colonne du Kanban. */
export function mesuresDesTaches(taches: Tache[]): Mesure[] {
  return ETIQUETTES_TACHE.map(({ statut, singulier, pluriel }) =>
    compte(
      `tache:${statut}`,
      taches.filter((tache) => tache.statut === statut).length,
      singulier,
      pluriel,
    ),
  );
}

/**
 * Les statuts de run qui s'annoncent — **les deux attentes humaines en sont
 * absentes**, et c'est le partage du ticket : un run arrêté sur son brief ou sur
 * ses questions attend une action, donc il relève de la région assertive
 * (`mesuresDesArbitrages`). Le dire aussi ici le ferait annoncer deux fois.
 */
const ETIQUETTES_RUN: {
  statut: string;
  singulier: string;
  pluriel: string;
}[] = [
  { statut: EXECUTION_EN_COURS, singulier: "run démarré", pluriel: "runs démarrés" },
  { statut: EXECUTION_TERMINEE, singulier: "run terminé", pluriel: "runs terminés" },
  { statut: EXECUTION_ECHEC, singulier: "run en échec", pluriel: "runs en échec" },
  {
    statut: EXECUTION_ANNULEE,
    singulier: "run interrompu",
    pluriel: "runs interrompus",
  },
];

/**
 * Le relevé des runs : une mesure par statut.
 *
 * Vaut pour une liste entière comme pour **un seul** run — la vue d'un run lui
 * passe son propre résumé, et « 1 run terminé » y dit exactement ce qui vient
 * d'arriver. Une seconde formule dédiée au cas unitaire aurait dû rester d'accord
 * avec celle-ci pour un résultat identique.
 */
export function mesuresDesRuns(executions: ResumeExecution[]): Mesure[] {
  return ETIQUETTES_RUN.map(({ statut, singulier, pluriel }) =>
    compte(
      `run:${statut}`,
      executions.filter((execution) => execution.statut === statut).length,
      singulier,
      pluriel,
    ),
  );
}

/**
 * Le pas d'annonce de la dépense, en dollars.
 *
 * Un dollar : assez gros pour qu'une série d'appels à quelques millièmes ne
 * déclenche rien (le cas courant sur un fournisseur local, #113), assez petit
 * pour qu'un run qui dérape se signale avant la fin. C'est un **seuil
 * d'annonce**, pas un plafond : rien ne s'arrête ici.
 */
export const PAS_SEUIL_COUT_USD = 1;

/**
 * Le relevé de la dépense : un jalon par dollar franchi, qui dit le **total**.
 *
 * Le total et non la hausse : « la dépense a augmenté de 2 $ » oblige à se
 * rappeler d'où l'on partait, « dépense du projet : 7,00 $US » se suffit. Une
 * dépense non rapportée (`null`) vaut zéro seuil franchi — inconnu n'est pas nul,
 * mais aucun des deux ne franchit quoi que ce soit.
 */
export function mesureDeLaDepense(total: number | null): Mesure {
  return jalon(
    "depense",
    Math.floor((total ?? 0) / PAS_SEUIL_COUT_USD),
    `dépense du projet : ${formatCout(total)}`,
  );
}

/**
 * Le relevé des validations **déjà tranchées**.
 *
 * Celles en attente n'y sont pas : elles attendent une action, donc elles partent
 * dans la région assertive. Ce qui reste ici est le mouvement inverse — une
 * décision prise, y compris depuis un autre onglet ou par quelqu'un d'autre.
 */
export function mesureDesValidationsTranchees(validations: Validation[]): Mesure {
  return compte(
    "validation:tranchee",
    validations.filter((validation) => validation.statut !== VALIDATION_EN_ATTENTE)
      .length,
    "validation tranchée",
    "validations tranchées",
  );
}

/**
 * Le relevé du journal : le compte d'entrées affichées.
 *
 * C'est l'écran que le ticket vise nommément — celui dont le code portait « pas
 * de région live : le flux temps réel ferait de ce compteur un bavard permanent ».
 * Il l'est en effet, **à condition d'annoncer chaque ligne** ; agrégé sur la
 * fenêtre de `lib/useAnnonce`, il tient en « 12 nouveaux événements » toutes les
 * cinq secondes.
 */
export function mesureDesEvenements(nombre: number): Mesure {
  return compte("evenement", nombre, "nouvel événement", "nouveaux événements");
}

/** Le relevé d'un fil de conversation : le compte de messages reçus. */
export function mesureDesMessages(nombre: number): Mesure {
  return compte("message", nombre, "nouveau message", "nouveaux messages");
}

/**
 * « 1 validation et 2 briefs en attente » — `null` quand rien n'attend personne.
 *
 * Les deux familles sont dites séparément quand les deux sont là, et chacune
 * seule quand elle est seule : « 3 en attente » obligerait à ouvrir l'écran pour
 * savoir de quoi il retourne, alors que répondre à des questions et approuver une
 * action sensible ne demandent ni la même disponibilité ni la même personne.
 *
 * Partagé avec l'étiquette de la cloche (`components/CentreNotifications`) depuis
 * #538 : les deux disent la même file, et deux formulations auraient fini par
 * diverger — c'est déjà la raison d'être de `lib/brief` et `lib/execution`.
 */
export function resumeArbitrages(
  validations: number,
  briefs: number,
): string | null {
  const morceaux: string[] = [];
  if (validations > 0) {
    morceaux.push(`${validations} validation${validations > 1 ? "s" : ""}`);
  }
  if (briefs > 0) morceaux.push(`${briefs} brief${briefs > 1 ? "s" : ""}`);
  if (morceaux.length === 0) return null;
  return `${morceaux.join(" et ")} en attente`;
}

/**
 * Le relevé des **demandes d'arbitrage humain** — les seuls événements qui
 * attendent une action, donc les seuls qui aient droit à `aria-live="assertive"`.
 *
 * Une **seule** mesure pour les deux familles, et non une par famille : ce qui
 * doit interrompre quelqu'un est « on vous attend », pas la ventilation de la
 * file. Deux mesures auraient produit deux phrases coupant la parole à la
 * première, pour un état que la phrase unique dit déjà en entier.
 */
export function mesuresDesArbitrages(
  validations: Validation[],
  executions: ResumeExecution[],
): Mesure[] {
  const enAttente = validations.filter(
    (validation) => validation.statut === VALIDATION_EN_ATTENTE,
  ).length;
  const briefs = runsEnAttente(executions).length;
  const resume = resumeArbitrages(enAttente, briefs);
  return [
    jalon("arbitrage", enAttente + briefs, `Arbitrage requis : ${resume ?? ""}`),
  ];
}

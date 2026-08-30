/**
 * Le vocabulaire de la **liste** des agents (#258, lot 6 de #243) : ce qu'une
 * carte montre d'un agent, et de quoi la liste se filtre et se trie.
 *
 * Il vit à part de `lib/agents.ts`, qui répond d'une autre question — les
 * **facettes** d'un agent et les chemins qui y mènent. Ici, rien ne navigue :
 * on décrit un agent vu de l'extérieur (son rôle, sa provenance, son état) et
 * on range une liste. Deux modules parce que deux questions ; le composant les
 * lit tous les deux.
 *
 * Trois choses tiennent ce fichier, et il vaut mieux les connaître avant d'y
 * toucher :
 *
 * - **le rôle décide de l'icône, et la table est fermée.** Les cinq libellés
 *   viennent de `maestro/agents/catalog.py` ; tout le reste retombe sur
 *   `IconeAgent`. Ce n'est pas de la prudence : le rôle d'un agent personnalisé
 *   est du **texte libre**, et deviner une icône à partir de mots qu'il
 *   contiendrait reviendrait à juger du texte au lexique — ce que ce dépôt
 *   s'interdit (#746). Une icône fausse est pire qu'une icône générique : elle
 *   affirme ;
 * - **deux sources, une jointure, et l'absence est nommée.** La fiche vient du
 *   catalogue (`GET /api/catalogue`), l'état du parc (`GET /api/agents`). Les
 *   deux se recouvrent en pratique, jamais par contrat : un agent que le parc ne
 *   connaît pas encore rend l'état « inconnu » plutôt qu'un « libre » inventé.
 *   Inconnu ≠ disponible, exactement comme `cout_usd: null` ≠ 0 ;
 * - **l'état est porté par la forme autant que par la couleur** (docs/30 §1.6) :
 *   chaque état a son glyphe — ▶ en marche, ✓ prêt, ‖ en pause —, et les trois
 *   se distinguent en noir et blanc comme pour qui ne sépare pas le vert du
 *   rouge. La couleur appuie, elle ne porte pas.
 */

import {
  IconeAgent,
  IconePause,
  IconePuce,
  IconeRoleBaseDeDonnees,
  IconeRoleDesign,
  IconeRoleDeveloppeur,
  IconeRoleDevops,
  IconeRoleQa,
  IconeStatutEnCours,
  IconeStatutTerminee,
} from "@/components/Icones";
import type { Icone, TonBadge } from "@/components/Primitives";
import {
  AGENT_OCCUPE,
  AGENT_SOURCE_DEFAUT,
  AGENT_SOURCE_PERSONNALISE,
  AGENT_SOURCE_SURCHARGE,
  estAgentDuCode,
  type AgentCatalogue,
  type EtatAgent,
} from "@/lib/types";

/* ------------------------------------------------------------------ *
 * Le rôle
 * ------------------------------------------------------------------ */

/**
 * Les rôles que le code déclare, et leur icône. Les libellés sont ceux de
 * `DEFAULT_AGENTS` (`maestro/agents/catalog.py`) — ils voyagent tels quels dans
 * `AgentCatalogue.role`, donc c'est sur eux que la table porte.
 *
 * Un libellé recopié ici et changé là-bas ne casse rien : l'agent retombe sur
 * l'icône générique. C'est la propriété qui rend cette table acceptable en
 * façade — elle **enrichit**, elle ne conditionne rien.
 */
const ICONE_PAR_ROLE: ReadonlyMap<string, Icone> = new Map([
  ["developpeur", IconeRoleDeveloppeur],
  ["base de donnees", IconeRoleBaseDeDonnees],
  ["devops", IconeRoleDevops],
  ["designer", IconeRoleDesign],
  ["qa / testeur", IconeRoleQa],
]);

/**
 * La forme sous laquelle un libellé de rôle se compare : sans accents, sans
 * casse, sans espaces superflus. Elle absorbe les écarts de saisie
 * (« DevOps », « devops ») sans rien deviner de plus.
 */
function cleDeRole(role: string): string {
  return role
    .normalize("NFD")
    // Les diacritiques par leur **propriété** Unicode, jamais par un intervalle
    // de caractères combinants recopié dans la source : celui-ci ne survit pas
    // au premier fichier relu dans un autre encodage, et il se relit mal.
    .replace(/\p{Diacritic}/gu, "")
    .toLowerCase()
    .replace(/\s+/g, " ")
    .trim();
}

/** L'icône du rôle, ou celle de l'agent quand le rôle n'est pas du code. */
export function iconeDuRole(role: string): Icone {
  return ICONE_PAR_ROLE.get(cleDeRole(role)) ?? IconeAgent;
}

/* ------------------------------------------------------------------ *
 * L'origine
 * ------------------------------------------------------------------ */

/**
 * Ce qu'on lit sous le rôle : d'où vient la fiche — **trois** provenances (#259).
 *
 * Un agent du code dont on a surchargé les réglages de modèle n'est ni tout à
 * fait « du code » — il ne suit plus le code sur ces points-là — ni
 * « personnalisé », puisqu'il n'a pas été dupliqué. Le rendre « personnalisé »
 * disait l'inverse du chantier : que le régler l'avait détaché du code, alors que
 * son rôle, ses compétences et son playbook continuent d'en venir.
 */
export function libelleOrigine(source: string): string {
  if (source === AGENT_SOURCE_DEFAUT) return "du code";
  if (source === AGENT_SOURCE_SURCHARGE) return "du code, surchargé";
  return "personnalisé";
}

/**
 * Les origines proposées au filtre — **deux**, quand l'API en distingue trois.
 *
 * C'est délibéré : la question qu'on pose à ce filtre est « qui vient du code ? »,
 * et un agent surchargé y répond oui. En faire une troisième entrée obligerait à
 * cocher deux cases pour une seule question, et surtout ferait disparaître un
 * agent du code de la réponse « Du code » — le contraire de ce qu'on demande.
 * `estAgentDuCode` porte la règle, ici comme partout ailleurs.
 */
export const ORIGINES_AGENT: ReadonlyArray<{ valeur: string; libelle: string }> =
  [
    { valeur: AGENT_SOURCE_DEFAUT, libelle: "Du code" },
    { valeur: AGENT_SOURCE_PERSONNALISE, libelle: "Personnalisé" },
  ];

/* ------------------------------------------------------------------ *
 * L'état
 * ------------------------------------------------------------------ */

export type CleEtatAgent = "occupe" | "libre" | "desactive" | "inconnu";

export type EtatCarte = {
  cle: CleEtatAgent;
  libelle: string;
  ton: TonBadge;
  /** Le glyphe qui dit l'état — la forme, pas la couleur (docs/30 §1.6). */
  icone: Icone;
  /** Ce qui travaille bat ; un état stable, non. */
  pulse: boolean;
};

/**
 * Les quatre états, dans l'ordre où ils intéressent — « qui travaille, qui est
 * disponible » avant « qui ne travaillera pas ». C'est aussi l'ordre du tri par
 * état et celui du filtre : une seule liste, donc pas deux à tenir d'accord.
 */
export const ETATS_AGENT: ReadonlyArray<EtatCarte> = [
  {
    cle: "occupe",
    libelle: "Occupé",
    ton: "attention",
    icone: IconeStatutEnCours,
    pulse: true,
  },
  {
    cle: "libre",
    libelle: "Libre",
    ton: "positif",
    icone: IconeStatutTerminee,
    pulse: false,
  },
  {
    cle: "desactive",
    libelle: "Désactivé",
    ton: "neutre",
    icone: IconePause,
    pulse: false,
  },
  {
    cle: "inconnu",
    libelle: "État inconnu",
    ton: "neutre",
    icone: IconePuce,
    pulse: false,
  },
];

function etatParCle(cle: CleEtatAgent): EtatCarte {
  // La table est exhaustive par construction (`CleEtatAgent` la reflète) ; le
  // repli existe pour que la fonction reste totale sans `!` ni assertion.
  return ETATS_AGENT.find((etat) => etat.cle === cle) ?? ETATS_AGENT[3];
}

/**
 * L'état d'un agent tel que sa carte le montre.
 *
 * L'ordre des questions est le contenu de la décision : **désactivé d'abord**.
 * Un agent hors service porte encore le `statut` de sa dernière activité, si
 * bien que lire `statut` en premier afficherait « libre » sur un agent qui ne
 * prendra aucune tâche — le contraire de ce que la liste doit dire.
 */
export function etatDeLAgent(parc: EtatAgent | undefined): EtatCarte {
  if (parc === undefined) return etatParCle("inconnu");
  if (!parc.actif) return etatParCle("desactive");
  return etatParCle(parc.statut === AGENT_OCCUPE ? "occupe" : "libre");
}

/* ------------------------------------------------------------------ *
 * La ligne : une fiche, son état, sa charge
 * ------------------------------------------------------------------ */

export type LigneAgent = {
  fiche: AgentCatalogue;
  etat: EtatCarte;
  /**
   * Le plafond d'exécutions simultanées (#86) — `null` quand le parc ne connaît
   * pas l'agent : afficher « 1 instance » serait annoncer un réglage qu'on n'a
   * pas lu.
   */
  instances: number | null;
  /** Les tâches que l'agent mène en ce moment — 0 quand l'état est inconnu. */
  tachesEnCours: number;
};

/** Joint le catalogue et le parc, fiche par fiche, dans l'ordre du catalogue. */
export function composerLignesAgents(
  fiches: AgentCatalogue[],
  parc: EtatAgent[],
): LigneAgent[] {
  const parNom = new Map(parc.map((agent) => [agent.nom, agent]));
  return fiches.map((fiche) => {
    const agent = parNom.get(fiche.nom);
    return {
      fiche,
      etat: etatDeLAgent(agent),
      instances: agent?.instances ?? null,
      tachesEnCours: agent?.taches_en_cours.length ?? 0,
    };
  });
}

/* ------------------------------------------------------------------ *
 * Filtre et tri
 * ------------------------------------------------------------------ */

export type FiltresAgents = {
  /** Une recherche sur le nom et le rôle — vide : tout passe. */
  recherche: string;
  /** Un libellé de rôle exact, tel que les fiches le portent. */
  role: string;
  /** `AGENT_SOURCE_*`, ou la chaîne vide pour « toutes ». */
  origine: string;
  /** Une `CleEtatAgent`, ou la chaîne vide pour « tous ». */
  etat: string;
};

export const FILTRES_VIDES: FiltresAgents = {
  recherche: "",
  role: "",
  origine: "",
  etat: "",
};

/** Vrai dès qu'un filtre restreint la liste — de quoi proposer de les lever. */
export function filtresActifs(filtres: FiltresAgents): boolean {
  return (
    filtres.recherche.trim() !== "" ||
    filtres.role !== "" ||
    filtres.origine !== "" ||
    filtres.etat !== ""
  );
}

/**
 * Les rôles réellement présents, triés — le filtre ne propose que ce que la
 * liste contient, et non les cinq du code : offrir un rôle qui ne rendrait
 * aucune carte est le plus court chemin vers « le filtre est cassé ».
 */
export function rolesPresents(lignes: LigneAgent[]): string[] {
  const roles = new Set(
    lignes.map((ligne) => ligne.fiche.role).filter((role) => role !== ""),
  );
  return [...roles].sort((a, b) => a.localeCompare(b, "fr"));
}

export type CleTriAgents = "nom" | "role" | "etat";

export const TRIS_AGENTS: ReadonlyArray<{
  cle: CleTriAgents;
  libelle: string;
}> = [
  { cle: "nom", libelle: "Nom (A → Z)" },
  { cle: "role", libelle: "Rôle" },
  { cle: "etat", libelle: "État" },
];

/**
 * Le tri par défaut. Alphabétique et non « l'ordre du catalogue » : celui-ci est
 * celui du routage (`DEFAULT_AGENTS` départage les ex æquo), il n'a jamais été
 * un ordre de lecture, et un agent personnalisé s'y range à la fin, là où on ne
 * le cherche pas.
 */
export const TRI_AGENTS_DEFAUT: CleTriAgents = "nom";

function correspond(ligne: LigneAgent, filtres: FiltresAgents): boolean {
  const recherche = cleDeRole(filtres.recherche);
  if (
    recherche !== "" &&
    !cleDeRole(`${ligne.fiche.nom} ${ligne.fiche.role}`).includes(recherche)
  ) {
    return false;
  }
  if (filtres.role !== "" && ligne.fiche.role !== filtres.role) return false;
  // « Du code » retient les **deux** états du code (#259) : un agent dont on a
  // surchargé le modèle vient toujours du code, et l'égalité stricte le faisait
  // disparaître de la seule réponse où on le cherche.
  if (filtres.origine !== "" && !correspondOrigine(ligne.fiche.source, filtres.origine)) {
    return false;
  }
  return filtres.etat === "" || ligne.etat.cle === filtres.etat;
}

/** La source `source` répond-elle à l'origine demandée au filtre (#259) ? */
function correspondOrigine(source: string, demandee: string): boolean {
  return demandee === AGENT_SOURCE_DEFAUT
    ? estAgentDuCode(source)
    : source === demandee;
}

/** Le rang d'un état dans `ETATS_AGENT` — l'ordre du tri, écrit une seule fois. */
function rangEtat(ligne: LigneAgent): number {
  return ETATS_AGENT.findIndex((etat) => etat.cle === ligne.etat.cle);
}

function parNom(a: LigneAgent, b: LigneAgent): number {
  return a.fiche.nom.localeCompare(b.fiche.nom, "fr");
}

/**
 * Filtre puis trie — sur une copie, la liste d'origine venant du chargement et
 * n'ayant pas à bouger sous un réglage d'affichage.
 *
 * Tout tri départage ses ex æquo **par le nom** : sans cela, deux agents du même
 * rôle changeraient de place d'un rendu à l'autre selon l'ordre d'arrivée du
 * catalogue, et l'écran donnerait à lire un mouvement qui n'existe pas.
 */
export function vueDesAgents(
  lignes: LigneAgent[],
  filtres: FiltresAgents,
  tri: CleTriAgents,
): LigneAgent[] {
  const retenues = lignes.filter((ligne) => correspond(ligne, filtres));
  const compare =
    tri === "role"
      ? (a: LigneAgent, b: LigneAgent) =>
          a.fiche.role.localeCompare(b.fiche.role, "fr") || parNom(a, b)
      : tri === "etat"
        ? (a: LigneAgent, b: LigneAgent) =>
            rangEtat(a) - rangEtat(b) || parNom(a, b)
        : parNom;
  return [...retenues].sort(compare);
}

/**
 * Les aides partagées des tests de la Control Tower (#124) : ce que jsdom ne
 * fournit pas, les fabriques d'objets du domaine, et le rendu d'un composant
 * dans le contexte que le shell lui donne en vrai.
 *
 * Principe des fabriques : **des valeurs par défaut complètes, un `Partial` en
 * argument**. Un test ne pose que le champ qui l'intéresse (le statut d'une
 * validation, le coût d'un agent) et reste lisible ; ajouter un champ au type
 * côté backend ne casse pas trente tests, seulement la fabrique.
 */

import { render, type RenderResult } from "@testing-library/react";
import type { ReactNode } from "react";

import { FournisseurEtatGlobal } from "@/lib/etatGlobal";
import { ecrireProjetActifId } from "@/lib/projetActif";
import type { ControlTower } from "@/lib/useControlTower";
import {
  AGENT_SOURCE_DEFAUT,
  EVENEMENT_TACHE_STATUT,
  EXECUTION_EN_COURS,
  TAILLE_PAGE_JOURNAL_MAX,
} from "@/lib/types";
import type {
  AgentCatalogue,
  CoutExecution,
  CoutTache,
  CoutTacheAgregee,
  DossierExplorateur,
  EntreeJournal,
  EtatAgent,
  Evenement,
  MessageChat,
  PageExplorateur,
  PageJournal,
  Projet,
  ResumeExecution,
  Tache,
  Usage,
  Validation,
} from "@/lib/types";

// --- Préférence système (thème #118) ---------------------------------------

const REQUETE_SOMBRE = "(prefers-color-scheme: dark)";

type EcouteurMedia = (evenement: MediaQueryListEvent) => void;

let ecouteursMedia = new Set<EcouteurMedia>();
let systemeSombre = false;

/**
 * Installe le `matchMedia` que jsdom n'a pas. Appelé avant chaque test
 * (`setup.ts`) : l'OS y est en clair par défaut, et sans écouteur hérité du
 * test précédent.
 */
export function installerMatchMedia(): void {
  ecouteursMedia = new Set();
  systemeSombre = false;
  window.matchMedia = ((requete: string) => ({
    media: requete,
    // Getter et non valeur figée : une bascule d'OS doit être vue par les
    // appels suivants sans qu'on ait à réinstaller quoi que ce soit.
    get matches() {
      return requete === REQUETE_SOMBRE && systemeSombre;
    },
    addEventListener: (_type: string, ecouteur: EcouteurMedia) => {
      ecouteursMedia.add(ecouteur);
    },
    removeEventListener: (_type: string, ecouteur: EcouteurMedia) => {
      ecouteursMedia.delete(ecouteur);
    },
    // L'API dépréciée, au cas où une dépendance s'en sert encore.
    addListener: (ecouteur: EcouteurMedia) => ecouteursMedia.add(ecouteur),
    removeListener: (ecouteur: EcouteurMedia) => ecouteursMedia.delete(ecouteur),
    onchange: null,
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}

/** Règle la préférence de l'OS **sans** prévenir personne (état de départ). */
export function poserPreferenceSysteme(sombre: boolean): void {
  systemeSombre = sombre;
}

/**
 * Bascule la préférence de l'OS **en cours de session** et notifie les
 * abonnés — le cas que le mode « système » doit suivre (coucher du soleil,
 * réglage changé dans une autre fenêtre).
 */
export function basculerPreferenceSysteme(sombre: boolean): void {
  systemeSombre = sombre;
  for (const ecouteur of [...ecouteursMedia]) {
    ecouteur({ matches: sombre, media: REQUETE_SOMBRE } as MediaQueryListEvent);
  }
}

// --- Chemin courant et routeur (navigation #117, visite guidée #122) -------

let chemin = "/";

/** Le chemin que `usePathname` rendra — le mock vit dans `setup.ts`. */
export function poserChemin(nouveau: string): void {
  chemin = nouveau;
}

export function cheminCourant(): string {
  return chemin;
}

/**
 * Les navigations demandées par `router.push` depuis le dernier test. La visite
 * guidée s'en sert pour amener l'utilisateur sur la page d'une étape : c'est
 * l'observable qui dit qu'elle l'a fait.
 */
export const navigations: string[] = [];

/** Le routeur factice de `next/navigation` — poussée enregistrée, pas jouée. */
export function routeurFactice() {
  return {
    push: (url: string) => {
      navigations.push(url);
      chemin = url;
    },
    replace: (url: string) => {
      navigations.push(url);
      chemin = url;
    },
    back: () => {},
    forward: () => {},
    refresh: () => {},
    prefetch: () => {},
  };
}

// --- Fil de l'assistant (assistance #123) ----------------------------------

/** Ce que `useChat` rendra au panneau d'assistance — réglé par le test. */
export type FilFactice = {
  messages: MessageChat[];
  connecte: boolean;
  chargement: boolean;
  erreur: string | null;
  envoi: boolean;
  envoyer: (contenu: string) => Promise<void>;
};

function filParDefaut(): FilFactice {
  return {
    messages: [],
    connecte: true,
    chargement: false,
    erreur: null,
    envoi: false,
    envoyer: async () => {},
  };
}

let fil: FilFactice = filParDefaut();

export function poserFilAssistance(partiel: Partial<FilFactice> = {}): void {
  fil = { ...filParDefaut(), ...partiel };
}

export function filAssistanceCourant(): FilFactice {
  return fil;
}

export function messageFactice(partiel: Partial<MessageChat> = {}): MessageChat {
  return {
    agent: "assistance",
    auteur: "utilisateur",
    contenu: "Bonjour",
    horodatage: "2026-07-28T10:00:00Z",
    ...partiel,
  };
}

// --- État temps réel (contexte du shell) -----------------------------------

function etatParDefaut(): ControlTower {
  return {
    taches: [],
    agents: [],
    evenements: [],
    validations: [],
    executions: [],
    couts: [],
    connecte: true,
    // Le pouls du shell (#475) : immobile par défaut, parce qu'un état factice ne
    // recharge rien. Un test qui exerce une vue abonnée au pouls
    // (`lib/useTachesRun`) le fait avancer d'un `poserEtatGlobal` au suivant —
    // c'est le seul observable qui distingue « rien n'a bougé » d'un rechargement.
    revision: 0,
    chargement: false,
    erreur: null,
    reassigner: async () => {},
    decider: async () => {},
    trancherBrief: async () => {},
    repondreAuBrief: async () => {},
    // Rend le **nouveau** run, comme la vraie : c'est lui qui porte `reprise_de`,
    // et un test qui n'en veut rien peut ignorer la valeur.
    relancerRun: async (runId: string) => ({
      run_id: `suite-de-${runId}`,
      objectif: "# Brief",
      statut: EXECUTION_EN_COURS,
      nb_taches: 0,
      cout_usd: null,
      ticket: null,
      projet_id: null,
      reprise_de: runId,
      debut: "2026-07-28T10:00:00Z",
      fin: null,
    }),
    // Les deux ordres de pause (#477) rendent le run **tel quel**, drapeau posé
    // ou retiré : la vraie implémentation rend le résumé relu après l'ordre, et
    // c'est ce que l'appelant en attend. Un test qui exerce le geste passe le
    // sien (`vi.fn()`), comme il le fait déjà de `relancerRun`.
    suspendreRun: async (runId: string) => ({
      ...runFactice({ run_id: runId }),
      en_pause: true,
    }),
    reprendreRun: async (runId: string) => ({
      ...runFactice({ run_id: runId }),
      en_pause: false,
    }),
    reglerCapacite: async () => {},
  };
}

/**
 * Un résumé de run minimal — le socle des doubles ci-dessus, et **exporté**
 * depuis #480 : trois écrans du chantier #472 en rendent (la liste, la vue d'un
 * run, l'état des runs au tableau de bord), et chacun réécrivait le sien.
 *
 * Les champs facultatifs du contrat (`vitalite`, `progression`, `en_pause`,
 * `cause`, `attente_depuis`…) restent **absents** plutôt que posés à une valeur
 * neutre : c'est ce que rend un backend antérieur au lot qui les a ajoutés, donc
 * le cas qu'un écran doit savoir traiter. Un test qui les veut les passe.
 */
export function runFactice(
  partiel: Partial<ResumeExecution> = {},
): ResumeExecution {
  return {
    run_id: "run-1",
    objectif: "objectif",
    statut: EXECUTION_EN_COURS,
    nb_taches: 0,
    cout_usd: null,
    ticket: null,
    projet_id: null,
    debut: "2026-07-28T10:00:00Z",
    fin: null,
    ...partiel,
  };
}

let etatGlobal: ControlTower = etatParDefaut();

/** Règle ce que le hook temps réel (mocké dans `setup.ts`) va rendre. */
export function poserEtatGlobal(partiel: Partial<ControlTower> = {}): void {
  etatGlobal = { ...etatParDefaut(), ...partiel };
}

export function etatGlobalCourant(): ControlTower {
  return etatGlobal;
}

/**
 * Les portées passées à `useControlTower` depuis le début du test (#281).
 *
 * Le hook est mocké — son va-et-vient réseau ne peut donc pas être observé —,
 * mais **ce qu'on lui demande** l'est, et c'est là qu'est la promesse du lot :
 * « aucun écran ne montre autre chose que le projet actif » se vérifie au
 * paramètre de la lecture, pas au contenu d'une réponse factice. Une liste et
 * non la dernière valeur : c'est l'enchaînement qui compte quand on change de
 * projet.
 */
export const porteesDemandees: string[] = [];

export function noterPortee(portee: string): void {
  porteesDemandees.push(portee);
}

// --- Projets déclarés et projet actif (porte d'entrée #279) ----------------

let projets: Projet[] = [];

/**
 * Ce que `chargerProjets` rendra — le mock vit dans `setup.ts`, au même titre
 * que celui des hooks temps réel : la porte d'entrée du shell lit cette liste à
 * chaque montage, aucun test n'a donc à monter de faux serveur pour elle.
 */
export function poserProjets(liste: Projet[]): void {
  projets = liste;
}

export function projetsDeclares(): Projet[] {
  return projets;
}

// --- Journal persisté (#478) -----------------------------------------------

let entreesJournal: EntreeJournal[] = [];

/**
 * Ce que `chargerJournal` rendra — le mock vit dans `setup.ts`, au même titre
 * que celui des hooks temps réel.
 *
 * Depuis #478 la page Journal et la vue d'un run **partent de l'historique
 * persisté** au lieu du seul WebSocket : sans ce point d'entrée, tout test qui
 * les rend taperait le réseau, et son premier rendu resterait figé sur « Lecture
 * du journal… » — un écran de chargement qu'aucune assertion ne cherche.
 * L'historique par défaut est **vide**, ce qui rend exactement ce que rendait un
 * fil sans événement avant ce lot : les tests d'avant continuent de dire vrai.
 */
export function poserJournal(entrees: EntreeJournal[]): void {
  entreesJournal = entrees;
}

/** Une page de journal factice — le `total` suit ce qui a été posé. */
export function pageJournalCourante(): PageJournal {
  return {
    entrees: entreesJournal,
    total: entreesJournal.length,
    page: 1,
    taille: TAILLE_PAGE_JOURNAL_MAX,
    pages: entreesJournal.length > 0 ? 1 : 0,
  };
}

/**
 * Une entrée de journal, réduite à ce qu'un test nomme — le reste vide, comme
 * une vraie entrée dont l'événement ne renseignait pas tout.
 */
export function entreeJournalFactice(
  partiel: Partial<EntreeJournal> = {},
): EntreeJournal {
  return {
    id: "j-0001",
    type: EVENEMENT_TACHE_STATUT,
    run_id: "",
    tache_id: "",
    titre: "",
    agent: "",
    role: "",
    statut: "",
    detail: "",
    description: "",
    projet_id: null,
    horodatage: "2026-07-28T10:00:00+00:00",
    ...partiel,
  };
}

/**
 * Le projet retenu d'une visite à l'autre : **déclaré** et **mémorisé**, les
 * deux conditions pour que la garde du shell laisse entrer (#279). Sans lui,
 * tout rendu du `Shell` s'arrête à la porte — ce qui est le comportement voulu,
 * mais pas ce que la plupart des tests observent.
 */
export function poserProjetActif(projet: Projet = projetFactice()): Projet {
  poserProjets([projet]);
  ecrireProjetActifId(projet.id);
  return projet;
}

/**
 * Rend un composant sous le fournisseur d'état du shell — le contexte qu'il a
 * en vrai. Le fournisseur est le **vrai** (`lib/etatGlobal`) : seule la source
 * temps réel sous lui est factice, si bien que ce qu'il calcule lui-même (le
 * coût cumulé) reste exercé.
 *
 * Le **projet** lui est donné ici comme le shell le donne en vrai (#281) : sans
 * lui, aucun écran ne saurait nommer le vide qu'il affiche. Un test qui regarde
 * ce nom passe le sien en troisième argument ; les autres n'ont rien à changer.
 */
export function rendreAvecEtat(
  ui: ReactNode,
  partiel: Partial<ControlTower> = {},
  projet: Projet = projetFactice(),
): RenderResult {
  poserEtatGlobal(partiel);
  return render(
    <FournisseurEtatGlobal projet={projet}>{ui}</FournisseurEtatGlobal>,
  );
}

// --- Fabriques du domaine --------------------------------------------------

export function agentFactice(partiel: Partial<EtatAgent> = {}): EtatAgent {
  return {
    nom: "dev",
    role: "Développeur",
    statut: "libre",
    tache_courante: "",
    taches_en_cours: [],
    taches_terminees: 0,
    taches_echouees: 0,
    cout_usd: null,
    derniere_activite: "2026-07-28T10:00:00Z",
    actif: true,
    instances: 1,
    ...partiel,
  };
}

export function tacheFactice(partiel: Partial<Tache> = {}): Tache {
  return {
    id: "T-1",
    run_id: "run-1",
    titre: "Écrire les tests",
    statut: "assignee",
    agent: "dev",
    role: "Développeur",
    cout_usd: null,
    usage: null,
    // Le cas courant est l'absence de ticket externe (#192) : les fabriques
    // partent de là, un test qui en veut un le passe en `partiel`.
    ticket: null,
    // Idem pour le projet (#222) : le cas courant est la tâche hors projet.
    projet_id: null,
    horodatage: "2026-07-28T10:00:00Z",
    ...partiel,
  };
}

/**
 * L'usage d'une ligne de coût — tout à zéro, rien de rapporté. Un test qui
 * regarde un montant pose `cout_usd` ; les autres n'ont pas à s'en soucier.
 */
export function usageFactice(partiel: Partial<Usage> = {}): Usage {
  return {
    appels: 0,
    tokens_entree: 0,
    tokens_sortie: 0,
    tokens_total: 0,
    cout_usd: null,
    duree_ms: null,
    duree_api_ms: null,
    tours: 0,
    outils: [],
    ...partiel,
  };
}

/** Une ligne du grand livre d'un run (`PanneauCouts`). */
export function coutTacheFactice(partiel: Partial<CoutTache> = {}): CoutTache {
  return {
    tache_id: "T-1",
    nom: "Écrire les tests",
    agent: "dev",
    role: "Développeur",
    statut: "terminee",
    usage: usageFactice(),
    ticket: null,
    projet_id: null,
    ...partiel,
  };
}

/** Le grand livre d'une exécution (`GET /api/executions/{run_id}/cout`). */
export function coutExecutionFactice(
  partiel: Partial<CoutExecution> = {},
): CoutExecution {
  return {
    run_id: "run-1",
    planification: usageFactice(),
    brief: usageFactice(),
    total: usageFactice(),
    taches: [],
    ...partiel,
  };
}

/** Une ligne « par tâche » de la vue analytics (table de la page Coûts). */
export function coutTacheAgregeeFactice(
  partiel: Partial<CoutTacheAgregee> = {},
): CoutTacheAgregee {
  return {
    tache_id: "T-1",
    nom: "Écrire les tests",
    agent: "dev",
    role: "Développeur",
    statut: "terminee",
    executions: 1,
    usage: usageFactice(),
    ticket: null,
    projet_id: null,
    ...partiel,
  };
}

/** Une fiche du catalogue, telle que la liste des agents (#190) la rend. */
export function ficheCatalogueFactice(
  partiel: Partial<AgentCatalogue> = {},
): AgentCatalogue {
  return {
    nom: "dev",
    role: "Développeur",
    competences: [],
    modele: null,
    fournisseur: null,
    source: AGENT_SOURCE_DEFAUT,
    cree_le: null,
    modifie_le: null,
    mcp_serveurs: [],
    mcp_erreur: null,
    mcp_pool: [],
    mcp_pool_erreur: null,
    mcp_activations: [],
    permissions: null,
    permissions_erreur: null,
    ...partiel,
  };
}

export function evenementFactice(partiel: Partial<Evenement> = {}): Evenement {
  return {
    type: "tache.statut",
    run_id: "run-1",
    tache_id: "T-1",
    titre: "Écrire les tests",
    agent: "dev",
    role: "Développeur",
    statut: "en_cours",
    detail: "",
    description: "",
    cout_usd: null,
    usage: null,
    instances: null,
    ticket: null,
    projet_id: null,
    horodatage: "2026-07-28T10:00:00Z",
    ...partiel,
  };
}

export function validationFactice(partiel: Partial<Validation> = {}): Validation {
  return {
    tache_id: "T-1",
    titre: "Publier la version 1.2",
    description: "Pousser l'image en production",
    agent: "devops",
    role: "Ops",
    raison: "Action irréversible",
    statut: "en_attente",
    decision: "",
    diff: null,
    projet_id: null,
    horodatage: "2026-07-28T10:00:00Z",
    ...partiel,
  };
}

/** Un projet déclaré (#225) — versionné par défaut, c'est le cas courant. */
export function projetFactice(partiel: Partial<Projet> = {}): Projet {
  return {
    id: "prj-7f3a1c2b",
    nom: "Dépensio",
    racine: "D:/projets/depensio",
    origine: "existant",
    vcs: { type: "git", branche_base: "main", distant: null },
    perimetre: {
      inclus: ["."],
      exclus: [".git", "node_modules", ".env", "**/secrets/**"],
    },
    cree_le: "2026-08-05T09:00:00+00:00",
    modifie_le: "2026-08-05T09:00:00+00:00",
    ...partiel,
  };
}

/** Un dossier rendu par l'explorateur (#225). */
export function dossierFactice(
  partiel: Partial<DossierExplorateur> = {},
): DossierExplorateur {
  return {
    nom: "depensio",
    chemin: "D:/projets/depensio",
    depot_git: false,
    projet_id: null,
    // `null` par défaut : le cas courant est un sous-dossier énuméré, qui n'a
    // pas d'origine — seule la page d'entrée en porte une (#278).
    origine: null,
    ...partiel,
  };
}

/** Une page de l'explorateur (#225) — le point d'entrée par défaut. */
export function pageExplorateurFactice(
  partiel: Partial<PageExplorateur> = {},
): PageExplorateur {
  return {
    chemin: null,
    parent: null,
    racines: ["D:/projets"],
    dossiers: [dossierFactice({ nom: "projets", chemin: "D:/projets" })],
    tronque: false,
    ...partiel,
  };
}

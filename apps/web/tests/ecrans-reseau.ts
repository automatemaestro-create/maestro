/**
 * Ce qu'il faut débrancher pour monter les dix écrans sans backend (#537, #539).
 *
 * ⚠ Le réseau est débranché **deux fois**, et il faut les deux : `tests/setup.ts`
 * mocke `useControlTower`/`useChat`, mais **ni `chargerCatalogue`, ni
 * `chargerSante`, ni le pool MCP, ni l'explorateur** (piège documenté dans
 * `apps/web/README.md`). Un écran qui les lit partirait sur un vrai `fetch` et
 * n'offrirait à la sonde qu'une bannière d'erreur — donc un écran vert parce
 * qu'il est vide. Les fabriques ci-dessous **remplacent** celles du setup, d'où
 * la reconduction de `chargerProjets`/`chargerJournal`.
 *
 * ⚠ Ce module est **séparé de `ecrans.tsx`** à dessein, et ce n'est pas un
 * découpage de confort : `vi.mock` est hissé en tête du fichier de test, ses
 * fabriques ne peuvent donc charger leurs dépendances que **dedans**
 * (`await import(…)`) — et si ce qu'elles chargent importait les pages, le mock
 * de `@/lib/useAnalyticsCouts` se rappellerait lui-même par `app/couts/page` et
 * rendrait un module à moitié construit. Ici, rien n'importe une page.
 */

import {
  EXECUTION_EN_ATTENTE_BRIEF,
  type AnalyticsCouts,
  type DetailExecution,
} from "@/lib/types";

import {
  coutExecutionFactice,
  coutTacheAgregeeFactice,
  ficheCatalogueFactice,
  pageExplorateurFactice,
  pageJournalCourante,
  projetsDeclares,
  runFactice,
  usageFactice,
} from "./aides";

const CATALOGUE = [
  ficheCatalogueFactice({ nom: "dev", role: "Développeur" }),
  ficheCatalogueFactice({ nom: "qa", role: "Testeur" }),
];

/** Le détail que l'écran « Valider le brief » ouvre sur le run en attente. */
export function detailFactice(): DetailExecution {
  return {
    ...runFactice({ run_id: "run-1", statut: EXECUTION_EN_ATTENTE_BRIEF }),
    brief: {
      objectif: "Ajouter l'export CSV des dépenses",
      perimetre: ["Écran Dépenses", "API /export"],
      hors_perimetre: ["Le format XLSX"],
      contraintes: ["Pas de dépendance nouvelle"],
      criteres_acceptation: ["Le fichier s'ouvre dans un tableur"],
      hypotheses: ["Les montants sont déjà en euros"],
      questions: [],
    },
    cout: coutExecutionFactice(),
    evenements: [],
  };
}

/** Ce que `@/lib/api` doit rendre pour que les dix écrans se montent peuplés. */
export function mocksApi() {
  return {
    // Reconduits : ces mocks **remplacent** ceux de `tests/setup.ts`.
    chargerProjets: async () => projetsDeclares(),
    chargerJournal: async () => pageJournalCourante(),
    // Ce que le setup ne couvre pas, et sans quoi quatre des dix écrans se
    // liraient à l'état « bannière d'erreur ».
    chargerCatalogue: async () => CATALOGUE,
    chargerSante: async () => ({ statut: "ok" }),
    chargerRegistreMcp: async () => [],
    chargerPoolMcp: async () => ({ integrations: [], erreur: null }),
    chargerExplorateur: async () => pageExplorateurFactice(),
    chargerDisponibiliteSelecteur: async () => ({
      disponible: false,
      motif: "hors_poste",
      message: "Le sélecteur natif n'est pas disponible ici.",
      outil: null,
    }),
    chargerExecution: async (): Promise<DetailExecution> => detailFactice(),
  };
}

/**
 * La vue analytics est mockée **au hook** et non à l'API, contrairement au
 * reste : `useAnalyticsCouts` ouvre sa propre WebSocket et se reconnecte en
 * backoff. La couper à la source laisserait le principe « aucun test n'a besoin
 * de backend » (docs/10 §8) tenu par un `fetch` qui échoue et des minuteurs qui
 * survivent au test. Mock **partiel** chez l'appelant : `PERIODES`, que la page
 * lit à côté du hook, doit passer tel quel.
 */
export function mockAnalytics() {
  return {
    useAnalyticsCouts: () => ({
      vue: vueAnalytics(),
      connecte: true,
      chargement: false,
      rafraichissement: false,
      erreur: null,
    }),
  };
}

export function vueAnalytics(): AnalyticsCouts {
  const usage = usageFactice({
    appels: 12,
    tokens_entree: 4200,
    tokens_sortie: 900,
    tokens_total: 5100,
    cout_usd: 1.42,
    duree_ms: 43_000,
    tours: 3,
  });
  return {
    depuis: null,
    pas: "heure",
    projet: "prj-7f3a1c2b",
    portee: "prj-7f3a1c2b",
    total: usage,
    executions: [
      {
        run_id: "run-1",
        nb_taches: 3,
        debut: "2026-07-28T10:00:00Z",
        fin: "2026-07-28T10:12:00Z",
        usage,
        projet_id: "prj-7f3a1c2b",
      },
    ],
    agents: [{ agent: "dev", role: "Développeur", taches: 2, usage }],
    taches: [coutTacheAgregeeFactice({ usage })],
    serie: [{ periode: "2026-07-28T10:00:00Z", usage }],
  };
}

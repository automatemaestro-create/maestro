/**
 * Ce qu'il faut débrancher pour monter les écrans du menu sans backend (#537,
 * #539).
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
  MCP_MODE_OAUTH,
  MCP_MODE_TOKEN,
  type AnalyticsCouts,
  type DetailExecution,
  type EntreeRegistreMcp,
  type IntegrationPoolMcp,
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

/**
 * Le catalogue des deux sondes. `dev` a **activé une intégration** et `qa` non
 * (#270) : c'est de cette liste que l'écran « Intégrations » tire son « utilisée
 * par », et les deux cas doivent être à l'écran — celui qui nomme des agents et
 * celui qui dit « aucun », qui ne rendent pas les mêmes balises.
 */
const CATALOGUE = [
  ficheCatalogueFactice({
    nom: "dev",
    role: "Développeur",
    mcp_activations: ["figma-officiel"],
  }),
  ficheCatalogueFactice({ nom: "qa", role: "Testeur" }),
];

/** Un serveur MCP monté, réduit à ce dont l'UI se sert. */
function serveurFactice(nom: string) {
  return {
    nom,
    type: "stdio",
    commande: "npx",
    args: [],
    url: "",
    env: {},
    headers: {},
    optionnel: false,
  };
}

/**
 * Le pool projet des deux sondes (#270). **Deux intégrations et pas zéro** :
 * `chargerPoolMcp` rendait un pool vide, si bien que la ligne du pool — ses
 * badges, son bouton « Retirer », ses agents — n'était montée par aucune des
 * deux suites. Un écran vide n'a presque pas de balises : l'auditer rend un
 * vert qui ne parle que du vide (même raison que `peuplerEtat`).
 *
 * Les deux états qui comptent y sont : un secret **valide** sur une intégration
 * qu'un agent utilise, un secret **à configurer** sur une intégration que
 * personne n'a activée.
 */
const POOL: IntegrationPoolMcp[] = [
  {
    id: "figma-officiel",
    serveur: serveurFactice("figma-officiel"),
    mode_auth: MCP_MODE_OAUTH,
    procedure_url: "https://www.figma.com/developers",
    curee: true,
    secrets: [
      {
        cle: "FIGMA_TOKEN",
        description: "Jeton OAuth",
        secret: true,
        present: true,
        valide: true,
        ephemere: false,
        expire_le: "2026-12-31T23:00:00Z",
      },
    ],
  },
  {
    id: "gitlab",
    serveur: serveurFactice("gitlab"),
    mode_auth: MCP_MODE_TOKEN,
    procedure_url: "",
    curee: true,
    secrets: [
      {
        cle: "GITLAB_TOKEN",
        description: "Jeton d'accès",
        secret: true,
        present: false,
        valide: false,
        ephemere: false,
        expire_le: null,
      },
    ],
  },
];

/** La bibliothèque curée des deux sondes : une entrée, dépliable. */
const REGISTRE: EntreeRegistreMcp[] = [
  {
    id: "slack",
    nom: "Slack",
    description: "Lire et écrire dans les canaux de l'espace de travail.",
    mode_auth: MCP_MODE_TOKEN,
    transport: "stdio",
    commande: "npx",
    args: [],
    url: "",
    env: {},
    headers: {},
    tags: ["slack", "messagerie"],
    secrets: [{ cle: "SLACK_TOKEN", description: "Jeton", secret: true }],
    procedure_url: "",
    optionnel: false,
    curee: true,
  },
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

/** Ce que `@/lib/api` doit rendre pour que les écrans se montent peuplés. */
export function mocksApi() {
  return {
    // Reconduits : ces mocks **remplacent** ceux de `tests/setup.ts`.
    chargerProjets: async () => projetsDeclares(),
    chargerJournal: async () => pageJournalCourante(),
    // Ce que le setup ne couvre pas, et sans quoi plusieurs écrans se liraient
    // à l'état « bannière d'erreur ».
    chargerCatalogue: async () => CATALOGUE,
    chargerSante: async () => ({ statut: "ok" }),
    chargerRegistreMcp: async () => REGISTRE,
    chargerPoolMcp: async () => ({ integrations: POOL, erreur: null }),
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

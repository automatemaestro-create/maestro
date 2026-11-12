/**
 * Un geste ou une lecture que l'API n'a pas servis (#1640), suite de #1638.
 *
 * Les écritures qui relisent leur réponse (`envoyerJsonEtLire`) et les lectures
 * lèvent `ErreurApi.injoignable(chemin)` quand le `fetch` rejette. Le `message` de
 * cette erreur nomme une route (« /api/… n'a pas répondu ») : un écran qui le
 * recopie montre la route au lieu de dire ce qui n'a pas eu lieu. Chaque écran
 * reconnaît donc la panne à sa classe (`refusEnMots`, `lectureEnMots`), jamais à
 * son texte (docs/44).
 *
 * Les écrans sont branchés sur le **vrai** client : `setup.ts` ne remplace que
 * quelques lectures, et le `fetch` est celui du test.
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { GesteSurUnRun } from "@/components/chat/GesteSurUnRun";
import { PlafondDansLeFil } from "@/components/chat/PlafondDansLeFil";
import { CreationAgent, EditeurAgent } from "@/components/EditeurAgent";
import { EditeurPlaybook } from "@/components/EditeurPlaybook";
import { BibliothequeMcp } from "@/components/integrations/BibliothequeMcp";
import { McpEtPermissionsAgent } from "@/components/OngletMcpAgent";
import { PanneauRunsImmobiles, TITRE_RUNS_IMMOBILES } from "@/components/PanneauRunsImmobiles";
import { BoutonInterrompre, BoutonsPause } from "@/components/runs/EtatRun";
import {
  annulerExecution,
  deciderPlafond,
  GESTE_ABOUTI_SANS_RELECTURE,
  reprendreExecution,
  suspendreExecution,
  trancherGesteDuFil,
} from "@/lib/api";
import type { RunAuPlafond } from "@/lib/plafond";
import {
  EXECUTION_EN_ATTENTE_PLAFOND,
  EXECUTION_EN_COURS,
  MCP_MODE_TOKEN,
  VITALITE_ORPHELIN,
  type AccordEtendu,
  type AgentCatalogueDetail,
  type EntreeRegistreMcp,
  type PlaybookDetail,
  type ServeurMcp,
} from "@/lib/types";

import { ficheCatalogueFactice, rendreAvecEtat, runFactice } from "./aides";

/** Le navigateur ne joint plus l'API : chaque `fetch` rejette, comme sur une API éteinte. */
function couperApi(): void {
  vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new TypeError("Failed to fetch"))));
}

/** Ni le texte brut du navigateur, ni la route qui n'a pas répondu. */
const BRUT = /Failed to fetch|\/api\//;

/**
 * L'API sert les lectures nommées (par leur chemin, sans la requête) ; toute autre
 * route répond 404. `couperApi` la coupe ensuite, avant le geste.
 */
function servir(lectures: Record<string, unknown>): void {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init?: RequestInit) => {
      const chemin = new URL(url).pathname;
      const reponse =
        (init?.method ?? "GET") === "GET" && chemin in lectures
          ? new Response(JSON.stringify(lectures[chemin]), {
              status: 200,
              headers: { "Content-Type": "application/json" },
            })
          : new Response("{}", { status: 404 });
      return Promise.resolve(reponse);
    }),
  );
}

/** La phrase d'un geste que l'API n'a pas reçu : ce qui n'a pas eu lieu, puis quoi faire. */
const sansApi = (ce: string) =>
  `L'API n'a pas répondu : ${ce}. Vérifiez que maestro-api tourne, puis réessayez.`;

/** La phrase d'une lecture que l'API n'a pas servie, sous son titre. */
const LECTURE = "L'API n'a pas répondu. Vérifiez que maestro-api tourne, puis réessayez.";

function serveur(nom: string, partiel: Partial<ServeurMcp> = {}): ServeurMcp {
  return {
    nom,
    type: "http",
    commande: "",
    args: [],
    url: "https://exemple.test/mcp",
    env: {},
    headers: { Authorization: "Bearer ${JETON}" },
    optionnel: false,
    ...partiel,
  };
}

function entreeRegistre(
  id: string,
  nom: string,
  partiel: Partial<EntreeRegistreMcp> = {},
): EntreeRegistreMcp {
  return {
    id,
    nom,
    description: `Intégration ${nom}.`,
    mode_auth: MCP_MODE_TOKEN,
    transport: "stdio",
    commande: "npx",
    args: [],
    url: "",
    env: {},
    headers: {},
    tags: [nom.toLowerCase()],
    secrets: [{ cle: `${id.toUpperCase()}_TOKEN`, description: "Jeton", secret: true }],
    procedure_url: "",
    optionnel: false,
    editeur: `Éditeur ${nom}`,
    popularite: 50,
    curee: true,
    source: "curee",
    version: "",
    depot: "",
    statut: "",
    publie_le: "",
    admission: null,
    signaux: [],
    ...partiel,
  };
}

const REGISTRE = [
  entreeRegistre("gitlab", "GitLab"),
  // Une découverte (#679) : elle s'examine et s'admet, elle ne se configure pas.
  entreeRegistre("io-github-alice-veille", "veille", {
    curee: false,
    source: "decouverte",
    editeur: "io.github.alice",
    tags: [],
    secrets: [],
  }),
];

function fichePlaybook(partiel: Partial<PlaybookDetail> = {}): PlaybookDetail {
  return {
    agent: "dev",
    role: "Développeur",
    version: 2,
    nb_versions: 2,
    source: "stockage",
    cree_le: null,
    contenu: "## Mission\nTu écris du code.",
    ...partiel,
  } as PlaybookDetail;
}

const VERSIONS_PLAYBOOK = [
  { agent: "dev", version: 2, cree_le: "2026-08-22T10:00:00Z", source: "stockage" },
  { agent: "dev", version: 1, cree_le: "2026-08-21T10:00:00Z", source: "stockage" },
];

function ficheMcp(partiel: Partial<AgentCatalogueDetail> = {}): AgentCatalogueDetail {
  return {
    ...ficheCatalogueFactice({ nom: "qa", role: "Testeur" }),
    playbook: "",
    ...partiel,
  } as AgentCatalogueDetail;
}

const ACCORD: AccordEtendu = {
  id: "a-run",
  agent: "qa",
  outil: "Bash",
  etendue: "run",
  run_id: "run-1",
  tache_id: "t-1",
  accorde_le: "2026-09-27T10:00:00+00:00",
};

function runAuPlafond(): RunAuPlafond {
  return {
    ...runFactice({
      run_id: "run-plafond",
      titre: "Écrire NOTES.md puis CHANGELOG.md",
      statut: EXECUTION_EN_ATTENTE_PLAFOND,
      attente_depuis: "2026-09-27T13:56:00Z",
      projet_id: "prj-7f3a1c2b",
    }),
    plafond: {
      run_id: "run-plafond",
      projet_id: "prj-7f3a1c2b",
      objectif: "Écrire NOTES.md puis CHANGELOG.md",
      depense_usd: 0.2213166,
      depense_tokens: 24_373,
      plafond_cout_usd: 0.01,
      plafond_tokens: null,
      raison: "plafond de dépense dépassé : 0.2213 $ consommés sur l'exécution",
      restantes: [{ tache_id: "ecrire-notes", titre: "Écrire NOTES.md", interrompue: true }],
    },
  } as RunAuPlafond;
}

describe("à l'écran : un geste sur un run que l'API n'a pas reçu se dit en mots (#1640)", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("une mise en pause sans réponse dit que le run n'a pas été mis en pause", async () => {
    const utilisateur = userEvent.setup();
    rendreAvecEtat(<BoutonsPause run={runFactice()} />, { suspendreRun: suspendreExecution });
    couperApi();

    await utilisateur.click(screen.getByRole("button", { name: "Mettre en pause" }));

    expect(
      await screen.findByText(sansApi("le run n'a pas été mis en pause")),
    ).toBeInTheDocument();
    expect(screen.queryByText(BRUT)).toBeNull();
  });

  it("une reprise sans réponse dit que le run n'a pas repris", async () => {
    const utilisateur = userEvent.setup();
    rendreAvecEtat(<BoutonsPause run={runFactice({ en_pause: true })} />, {
      reprendreRun: reprendreExecution,
    });
    couperApi();

    await utilisateur.click(screen.getByRole("button", { name: "Reprendre" }));

    expect(await screen.findByText(sansApi("le run n'a pas repris"))).toBeInTheDocument();
    expect(screen.queryByText(BRUT)).toBeNull();
  });

  it("une interruption sans réponse dit que le run n'a pas été interrompu", async () => {
    const utilisateur = userEvent.setup();
    rendreAvecEtat(<BoutonInterrompre run={runFactice()} />, {
      interrompreRun: annulerExecution,
    });
    couperApi();

    await utilisateur.click(screen.getByRole("button", { name: /Interrompre/ }));
    await utilisateur.click(screen.getByRole("button", { name: "Confirmer l'interruption" }));

    expect(
      await screen.findByText(sansApi("le run n'a pas été interrompu")),
    ).toBeInTheDocument();
    expect(screen.queryByText(BRUT)).toBeNull();
  });

  it("la reprise d'un run immobile, sans réponse, dit que le run n'a pas repris", async () => {
    const utilisateur = userEvent.setup();
    rendreAvecEtat(
      <PanneauRunsImmobiles
        executions={[
          runFactice({
            run_id: "3ff0bcb065f9",
            statut: EXECUTION_EN_COURS,
            vitalite: VITALITE_ORPHELIN,
            brief_approuve: true,
            nb_taches: 5,
          }),
        ]}
        reprendre={reprendreExecution}
      />,
    );
    couperApi();
    const panneau = screen.getByRole("region", { name: TITRE_RUNS_IMMOBILES });

    await utilisateur.click(within(panneau).getByRole("button", { name: "Reprendre" }));

    expect(
      await within(panneau).findByText(sansApi("le run n'a pas repris")),
    ).toBeInTheDocument();
    expect(within(panneau).queryByText(BRUT)).toBeNull();
    expect(within(panneau).getByRole("button", { name: "Reprendre" })).toBeEnabled();
  });

  it("un geste confirmé dans le fil, sans réponse, dit que la décision n'est pas partie", async () => {
    const utilisateur = userEvent.setup();
    render(
      <GesteSurUnRun
        demande={{
          action: "pause",
          bornes: null,
          run: {
            run_id: "a9f4ec9ae5c7",
            titre: "Ajouter trois chants au carnet",
            statut: "en_cours",
            en_pause: false,
            pause_depuis: null,
            etat: "En cours",
          },
        }}
        trancher={async (approuve) => {
          await trancherGesteDuFil("orchestrateur", { approuve });
        }}
      />,
    );
    couperApi();

    await utilisateur.click(screen.getByRole("button", { name: "Mettre en pause" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      sansApi("votre décision n'a pas été transmise"),
    );
    expect(screen.queryByText(BRUT)).toBeNull();
  });

  it("une décision au plafond, sans réponse, dit que le run attend toujours", async () => {
    const utilisateur = userEvent.setup();
    render(
      <PlafondDansLeFil
        run={runAuPlafond()}
        trancher={async (runId, decision) => {
          await deciderPlafond(runId, decision);
        }}
      />,
    );
    couperApi();
    const carte = screen.getByRole("region", { name: "Budget du run atteint" });

    await utilisateur.click(within(carte).getByRole("button", { name: "Arrêter le run" }));

    expect(await within(carte).findByRole("alert")).toHaveTextContent(
      sansApi("votre décision n'a pas été transmise, le run attend toujours"),
    );
    expect(within(carte).queryByText(BRUT)).toBeNull();
    expect(within(carte).getByRole("button", { name: "Arrêter le run" })).toBeEnabled();
  });
});

describe("à l'écran : un geste d'agent ou d'intégration que l'API n'a pas reçu se dit en mots (#1640)", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("une réécriture demandée à l'assistant, sans réponse, dit que le brouillon est intact", async () => {
    const utilisateur = userEvent.setup();
    servir({
      "/api/playbooks/dev": fichePlaybook(),
      "/api/playbooks/dev/versions": [],
      "/api/playbooks/dev/propositions": [],
    });
    render(<EditeurPlaybook agent="dev" />);
    await screen.findByRole("region", { name: "Playbook de dev" });
    couperApi();

    await utilisateur.click(screen.getByRole("button", { name: "Assistant" }));
    await utilisateur.click(screen.getByRole("button", { name: /Proposer une réécriture/ }));

    const panneau = screen.getByRole("group", { name: "Assistant de rédaction" });
    expect(await within(panneau).findByRole("alert")).toHaveTextContent(
      sansApi("aucune réécriture n'a été proposée, votre brouillon est intact"),
    );
    expect(within(panneau).queryByText(BRUT)).toBeNull();
  });

  it("une définition demandée à l'assistant, sans réponse, dit que le formulaire est intact", async () => {
    const utilisateur = userEvent.setup();
    rendreAvecEtat(<CreationAgent onCreation={() => {}} />);
    couperApi();

    await utilisateur.type(
      screen.getByRole("textbox", { name: /Décrire l'agent en une phrase/ }),
      "Un agent qui relit mes migrations SQL",
    );
    await utilisateur.click(screen.getByRole("button", { name: "Générer" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      sansApi("aucune définition n'a été proposée, le formulaire est intact"),
    );
    expect(screen.queryByText(BRUT)).toBeNull();
  });

  it("une migration sans réponse dit que les déclarations n'ont pas été migrées", async () => {
    const utilisateur = userEvent.setup();
    servir({
      "/api/catalogue/qa": ficheMcp({ mcp_herites: [serveur("forge")] }),
      "/api/mcp/registre": [],
    });
    render(<McpEtPermissionsAgent nom="qa" />);
    const bouton = await screen.findByRole("button", { name: "Migrer vers le pool projet" });
    couperApi();

    await utilisateur.click(bouton);

    expect(
      await screen.findByText(sansApi("les déclarations n'ont pas été migrées")),
    ).toBeInTheDocument();
    expect(screen.queryByText(BRUT)).toBeNull();
  });

  it("une migration aboutie dont la relecture reste sans réponse ne dit pas « rien n'a changé »", async () => {
    const utilisateur = userEvent.setup();
    servir({
      "/api/catalogue/qa": ficheMcp({ mcp_herites: [serveur("forge")] }),
      "/api/mcp/registre": [],
    });
    render(<McpEtPermissionsAgent nom="qa" />);
    const bouton = await screen.findByRole("button", { name: "Migrer vers le pool projet" });
    // La migration est reçue ; la relecture de la fiche qui suit ne l'est plus.
    vi.stubGlobal(
      "fetch",
      vi.fn((_url: string, init?: RequestInit) =>
        (init?.method ?? "GET") === "POST"
          ? Promise.resolve(
              new Response(
                JSON.stringify({
                  agent: "qa",
                  ajoutees: [],
                  reprises: [],
                  activations: [],
                  fichier_retire: true,
                }),
                {
                  status: 200,
                  headers: { "Content-Type": "application/json" },
                },
              ),
            )
          : Promise.reject(new TypeError("Failed to fetch")),
      ),
    );

    await utilisateur.click(bouton);

    expect(await screen.findByText(GESTE_ABOUTI_SANS_RELECTURE)).toBeInTheDocument();
    expect(screen.queryByText(/n'ont pas été migrées/)).toBeNull();
    expect(screen.queryByText(BRUT)).toBeNull();
  });

  it("le retrait d'un accord sans réponse dit que l'accord n'a pas été retiré", async () => {
    const utilisateur = userEvent.setup();
    servir({
      "/api/catalogue/qa": ficheMcp({ permissions_accords: [ACCORD] }),
      "/api/mcp/registre": [],
    });
    render(<McpEtPermissionsAgent nom="qa" />);
    const bouton = await screen.findByRole("button", {
      name: "Retirer l'accord Bash pour ce run · run-1",
    });
    couperApi();

    await utilisateur.click(bouton);

    expect(
      await screen.findByText(sansApi("l'accord n'a pas été retiré")),
    ).toBeInTheDocument();
    expect(screen.queryByText(BRUT)).toBeNull();
    expect(screen.getByText("Bash")).toBeInTheDocument();
  });

  it("une admission sans réponse dit que le serveur n'a pas été admis", async () => {
    const utilisateur = userEvent.setup();
    servir({ "/api/mcp/registre": REGISTRE });
    render(<BibliothequeMcp idsPool={new Set()} onAjout={() => {}} />);
    const ligne = (await screen.findByText("veille")).closest("li") as HTMLElement;
    couperApi();

    await utilisateur.click(within(ligne).getByRole("button", { name: "Examiner" }));
    await utilisateur.click(
      within(ligne).getByRole("button", { name: /Admettre dans l'allowlist/ }),
    );

    expect(
      await within(ligne).findByText(sansApi("le serveur n'a pas été admis")),
    ).toBeInTheDocument();
    expect(within(ligne).queryByText(BRUT)).toBeNull();
  });

  it("un ajout au pool sans réponse dit que l'intégration n'a pas été ajoutée", async () => {
    const utilisateur = userEvent.setup();
    servir({ "/api/mcp/registre": REGISTRE });
    render(<BibliothequeMcp idsPool={new Set()} onAjout={() => {}} />);
    const ligne = (await screen.findByText("GitLab")).closest("li") as HTMLElement;
    await utilisateur.click(within(ligne).getByRole("button", { name: "Configurer" }));
    const champ = await screen.findByLabelText(/GITLAB_TOKEN/);
    couperApi();

    await utilisateur.type(champ, "glpat-xxx");
    await utilisateur.click(within(ligne).getByRole("button", { name: "Ajouter au pool" }));

    expect(
      await within(ligne).findByText(sansApi("l'intégration n'a pas été ajoutée au pool")),
    ).toBeInTheDocument();
    expect(within(ligne).queryByText(BRUT)).toBeNull();
  });
});

describe("à l'écran : une lecture que l'API n'a pas servie se dit en mots (#1640)", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("le registre MCP illisible ne montre pas sa route", async () => {
    couperApi();
    render(<BibliothequeMcp idsPool={new Set()} onAjout={() => {}} />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      `Registre illisible : ${LECTURE}`,
    );
    expect(screen.queryByText(BRUT)).toBeNull();
  });

  it("une lecture refusée garde son motif, sans la route", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          new Response(JSON.stringify({ detail: "registre en cours de rafraîchissement" }), {
            status: 503,
            headers: { "Content-Type": "application/json" },
          }),
        ),
      ),
    );
    render(<BibliothequeMcp idsPool={new Set()} onAjout={() => {}} />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Registre illisible : registre en cours de rafraîchissement",
    );
    expect(screen.queryByText(BRUT)).toBeNull();
  });

  it("le playbook illisible ne montre pas sa route", async () => {
    couperApi();
    render(<EditeurPlaybook agent="dev" />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      `Playbook illisible : ${LECTURE}`,
    );
    expect(screen.queryByText(BRUT)).toBeNull();
  });

  it("une version du playbook que l'API ne sert plus dit qu'elle n'a pas pu être lue", async () => {
    const utilisateur = userEvent.setup();
    servir({
      "/api/playbooks/dev": fichePlaybook(),
      "/api/playbooks/dev/versions": VERSIONS_PLAYBOOK,
      "/api/playbooks/dev/propositions": [],
    });
    render(<EditeurPlaybook agent="dev" />);
    await screen.findByRole("region", { name: "Playbook de dev" });
    couperApi();

    await utilisateur.selectOptions(screen.getByLabelText(/Historique/), "v:1");

    expect(await screen.findByRole("alert")).toHaveTextContent(
      sansApi("ce texte n'a pas pu être lu"),
    );
    expect(screen.queryByText(BRUT)).toBeNull();
  });

  it("la fiche d'un agent illisible ne montre pas sa route", async () => {
    couperApi();
    rendreAvecEtat(<EditeurAgent nom="veilleur" onSuppression={() => {}} />);

    expect(await screen.findByRole("alert")).toHaveTextContent(`Fiche illisible : ${LECTURE}`);
    expect(screen.queryByText(BRUT)).toBeNull();
  });

  it("la fiche MCP & permissions illisible ne montre pas sa route", async () => {
    couperApi();
    render(<McpEtPermissionsAgent nom="qa" />);

    expect(await screen.findByRole("alert")).toHaveTextContent(`Fiche illisible : ${LECTURE}`);
    expect(screen.queryByText(BRUT)).toBeNull();
  });
});

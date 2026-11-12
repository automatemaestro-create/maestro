/**
 * Une écriture que l'API n'a pas reçue (#1638).
 *
 * Quand le `fetch` rejette — API éteinte, mauvaise URL, CORS —, le client lève
 * `ErreurApi.injoignable(chemin)`. La panne est ainsi typée à la source : un écran
 * la reconnaît à sa classe, jamais à son texte (docs/44). Sans ce type, le
 * « Failed to fetch » du navigateur montait jusqu'à l'écran.
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CarteValidation } from "@/components/CarteValidation";
import { QuestionDansLeFil } from "@/components/chat/QuestionDansLeFil";
import { EditeurAgent } from "@/components/EditeurAgent";
import { ParametresAgents } from "@/components/parametres/ParametresAgents";
import { SelecteurReassignation } from "@/components/SelecteurReassignation";
import {
  deciderValidation,
  reassignerTache,
  reglerCapaciteAgent,
  repondreQuestion,
} from "@/lib/api";
import { AGENT_SOURCE_PERSONNALISE, AGENT_SOURCE_SURCHARGE } from "@/lib/types";

import {
  agentFactice,
  ficheCatalogueFactice,
  questionFactice,
  rendreAvecEtat,
  tacheFactice,
  validationFactice,
} from "./aides";

type Api = typeof import("@/lib/api");

/** Le navigateur ne joint plus l'API : chaque `fetch` rejette, comme sur une API éteinte. */
function couperApi(): void {
  vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new TypeError("Failed to fetch"))));
}

/** Ni le texte brut du navigateur, ni la route qui n'a pas répondu. */
const BRUT = /Failed to fetch|\/api\//;

/**
 * L'API sert les lectures nommées, le temps que l'écran se charge ; toute autre
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

const POLITIQUE = { allow: [], ask: {}, deny: [] };
const DEFINITION = {
  role: "Développe",
  competences: [],
  playbook: "",
  modele: null,
  fournisseur: null,
};
const REGLAGES = { fournisseur: null, modele: null, effort: null };

describe("côté client : une écriture sans réponse lève une panne « injoignable » (#1638)", () => {
  afterEach(() => vi.unstubAllGlobals());

  it.each<[string, string, (api: Api) => Promise<unknown>]>([
    ["la réponse à une question", "/api/questions/Q-1/reponse", (api) => api.repondreQuestion("Q-1", "oui")],
    ["la publication d'un playbook", "/api/playbooks/dev", (api) => api.ecrirePlaybook("dev", "# Dev")],
    ["la restauration d'un playbook", "/api/playbooks/dev/restaurer", (api) => api.restaurerPlaybook("dev", 2)],
    ["l'adoption d'une proposition", "/api/playbooks/dev/propositions/1/appliquer", (api) => api.appliquerPropositionPlaybook("dev", 1)],
    ["le rejet d'une proposition", "/api/playbooks/dev/propositions/1/rejeter", (api) => api.rejeterPropositionPlaybook("dev", 1)],
    ["la création d'un agent", "/api/catalogue", (api) => api.creerAgent("veilleur", DEFINITION)],
    ["la modification d'un agent", "/api/catalogue/veilleur", (api) => api.modifierAgent("veilleur", DEFINITION)],
    ["la suppression d'un agent", "/api/catalogue/veilleur", (api) => api.supprimerAgent("veilleur")],
    ["la surcharge d'un agent du code", "/api/catalogue/dev/reglages", (api) => api.surchargerAgent("dev", REGLAGES)],
    ["le retour au défaut", "/api/catalogue/dev/reglages", (api) => api.annulerSurchargeAgent("dev")],
    ["la réassignation d'une tâche", "/api/taches/T-1/reassigner", (api) => api.reassignerTache("T-1", "qa")],
    ["le réglage de capacité", "/api/agents/dev/capacite", (api) => api.reglerCapaciteAgent("dev", { actif: false })],
    ["la décision d'une validation", "/api/validations/T-1/decision", (api) => api.deciderValidation("T-1", true)],
    ["le retrait du pool", "/api/mcp/pool/github", (api) => api.supprimerIntegrationPoolMcp("github")],
    ["les activations d'un agent", "/api/mcp/activations/dev", (api) => api.definirActivationsMcp("dev", ["github"])],
    ["la politique de permissions", "/api/permissions/dev", (api) => api.definirPermissions("dev", POLITIQUE)],
    ["la décision d'un brief", "/api/executions/R-1/brief/decision", (api) => api.deciderBrief("R-1", { approuve: true, brief: null })],
    ["les réponses à un brief", "/api/executions/R-1/brief/reponses", (api) => api.repondreBrief("R-1", ["oui"])],
  ])("%s, sans réponse, lève une panne « injoignable », pas l'erreur du navigateur", async (_, chemin, geste) => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new TypeError("Failed to fetch"))));
    // Le vrai client, et non les doubles de `setup.ts`.
    const reel = await vi.importActual<Api>("@/lib/api");

    const echec = await geste(reel).catch((erreur: unknown) => erreur);

    expect(echec).toBeInstanceOf(reel.ErreurApi);
    expect((echec as InstanceType<Api["ErreurApi"]>).statut).toBeNull();
    expect((echec as InstanceType<Api["ErreurApi"]>).chemin).toBe(chemin);
  });

  it("un refus motivé garde son `detail`, et n'est pas une panne de transport", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          new Response(JSON.stringify({ detail: "demande déjà tranchée" }), {
            status: 409,
            headers: { "Content-Type": "application/json" },
          }),
        ),
      ),
    );
    const reel = await vi.importActual<Api>("@/lib/api");

    const echec = await reel.deciderValidation("T-1", true).catch((erreur: unknown) => erreur);

    expect(echec).not.toBeInstanceOf(reel.ErreurApi);
    expect((echec as Error).message).toBe("demande déjà tranchée");
  });
});

/*
 * Les écrans, branchés sur le vrai client : le geste part, le `fetch` rejette, et
 * l'écran dit en mots ce qui n'a pas eu lieu, puis quoi faire.
 */
describe("à l'écran : un geste que l'API n'a pas reçu se dit en mots (#1638)", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("une réassignation sans réponse dit que la tâche reste à son agent", async () => {
    const utilisateur = userEvent.setup();
    render(
      <SelecteurReassignation
        tache={tacheFactice()}
        agents={[agentFactice(), agentFactice({ nom: "qa", role: "Testeur" })]}
        reassigner={reassignerTache}
      />,
    );
    couperApi();

    await utilisateur.selectOptions(
      screen.getByRole("combobox", { name: "Réassigner la tâche Écrire les tests" }),
      "qa",
    );

    expect(
      await screen.findByText(
        "L'API n'a pas répondu : la tâche n'a pas été réassignée. Vérifiez que maestro-api tourne, puis réessayez.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(BRUT)).toBeNull();
  });

  it("une décision sans réponse dit qu'elle n'est pas partie, et rend les boutons", async () => {
    const utilisateur = userEvent.setup();
    render(
      <CarteValidation
        validation={validationFactice()}
        decider={deciderValidation}
        maintenant={Date.parse("2026-07-28T10:05:00Z")}
      />,
    );
    couperApi();

    await utilisateur.click(screen.getByRole("button", { name: "Approuver" }));

    expect(
      await screen.findByText(
        "L'API n'a pas répondu : la décision n'a pas été transmise. Vérifiez que maestro-api tourne, puis réessayez.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(BRUT)).toBeNull();
    expect(screen.getByRole("button", { name: "Approuver" })).toBeEnabled();
  });

  it("une réponse à une question, sans réponse de l'API, dit qu'elle n'est pas partie", async () => {
    const utilisateur = userEvent.setup();
    const question = questionFactice();
    rendreAvecEtat(<QuestionDansLeFil question={question} repondre={repondreQuestion} />);
    couperApi();
    const region = within(
      screen.getByRole("region", { name: `Question de l'agent ${question.agent}` }),
    );

    await utilisateur.click(
      within(region.getByRole("list")).getByRole("button", { name: "Postgres" }),
    );

    expect(
      await region.findByText(
        "L'API n'a pas répondu : votre réponse n'a pas été envoyée. Vérifiez que maestro-api tourne, puis réessayez.",
      ),
    ).toBeInTheDocument();
    expect(region.queryByText(BRUT)).toBeNull();
  });

  it("un réglage de capacité sans réponse dit que rien n'a changé", async () => {
    const utilisateur = userEvent.setup();
    rendreAvecEtat(<ParametresAgents />, {
      agents: [agentFactice()],
      reglerCapacite: reglerCapaciteAgent,
    });
    couperApi();

    await utilisateur.click(screen.getByRole("button", { name: "Ajouter une instance à dev" }));

    expect(
      await screen.findByText(
        "L'API n'a pas répondu : la capacité de l'agent n'a pas changé. Vérifiez que maestro-api tourne, puis réessayez.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(BRUT)).toBeNull();
  });

  describe("le profil d'un agent", () => {
    const PERSONNALISE = {
      ...ficheCatalogueFactice({
        nom: "veilleur",
        role: "Veille",
        competences: ["veille"],
        source: AGENT_SOURCE_PERSONNALISE,
        herite: [],
      }),
      playbook: "## Mission",
    };

    async function monterProfil(fiche: typeof PERSONNALISE) {
      const utilisateur = userEvent.setup();
      servir({ [`/api/catalogue/${fiche.nom}`]: fiche });
      rendreAvecEtat(<EditeurAgent nom={fiche.nom} onSuppression={() => {}} />);
      await screen.findByRole("region", {
        name:
          fiche.source === AGENT_SOURCE_PERSONNALISE
            ? `Configuration de ${fiche.nom}`
            : `Fiche de ${fiche.nom}`,
      });
      couperApi();
      return utilisateur;
    }

    it("une modification sans réponse dit que rien n'a été enregistré", async () => {
      const utilisateur = await monterProfil(PERSONNALISE);

      await utilisateur.type(screen.getByLabelText(/^Rôle/), " des concurrents");
      await utilisateur.click(
        screen.getByRole("button", { name: "Enregistrer les modifications" }),
      );

      expect(
        await screen.findByText(
          "L'API n'a pas répondu : rien n'a été enregistré. Vérifiez que maestro-api tourne, puis réessayez.",
        ),
      ).toBeInTheDocument();
      expect(screen.queryByText(BRUT)).toBeNull();
    });

    it("une suppression sans réponse dit que l'agent est toujours là", async () => {
      const utilisateur = await monterProfil(PERSONNALISE);

      await utilisateur.click(screen.getByRole("button", { name: "Supprimer l'agent…" }));
      await utilisateur.click(screen.getByRole("button", { name: "Confirmer la suppression" }));

      expect(
        await screen.findByText(
          "L'API n'a pas répondu : l'agent n'a pas été supprimé. Vérifiez que maestro-api tourne, puis réessayez.",
        ),
      ).toBeInTheDocument();
      expect(screen.queryByText(BRUT)).toBeNull();
    });

    it("un retour aux réglages du code sans réponse dit que rien n'a changé", async () => {
      const utilisateur = await monterProfil({
        ...PERSONNALISE,
        nom: "dev",
        source: AGENT_SOURCE_SURCHARGE,
        herite: ["fournisseur", "effort"],
        modele: "claude-sonnet-5",
      });

      await utilisateur.click(
        screen.getByRole("button", { name: "Revenir aux réglages du code" }),
      );

      expect(
        await screen.findByText(
          "L'API n'a pas répondu : les réglages de l'agent n'ont pas changé. Vérifiez que maestro-api tourne, puis réessayez.",
        ),
      ).toBeInTheDocument();
      expect(screen.queryByText(BRUT)).toBeNull();
    });
  });
});

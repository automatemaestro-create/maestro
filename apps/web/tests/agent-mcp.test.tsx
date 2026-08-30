/**
 * L'onglet **MCP & permissions** d'une fiche agent (#263, lot 11/15 de #243).
 *
 * ⚠ La couverture de cette surface est **différée au lot 15**, et ce fichier
 * n'est pas elle : il tient les quatre gestes du ticket, pas ses états de bord
 * (secrets à revoir, pool invalide, échecs de chaque appel, accessibilité). Le
 * lot 15 étendra ce fichier plutôt que d'en ouvrir un second.
 *
 * **Pourquoi un lot intermédiaire en porte quand même** (docs/10 §5.1 : « si sa
 * logique est critique ») : jusqu'ici **aucun test ne montait**
 * `McpEtPermissionsAgent` — ni celui-ci, ni ses ancêtres dans `EditeurAgent`.
 * L'écran écrit dans le pool projet et dans les activations, et un rendu qui
 * casse ne se voit ni au typage, ni au lint, ni au `next build`.
 *
 * Ce que ce fichier a déjà payé : le compte rendu de migration vivait dans le
 * bloc des déclarations héritées, c'est-à-dire **dans ce que la migration
 * supprime** — une migration réussie recharge la fiche, remonte la section et
 * fait disparaître le bloc avec son message. On cliquait, tout s'évanouissait,
 * et rien ne disait ce qui venait de se passer. Le compte rendu est remonté au
 * composant qui survit au rechargement (`McpEtPermissionsAgent`).
 *
 * Couvre :
 *
 * ① le montage — les deux groupes séparés (**actives en tête**, disponibles
 *    ensuite), le compte, la phrase qui dit qu'éteindre ne retire pas du pool,
 *    l'issue du bloc hérité, et les permissions non perdues au déménagement ;
 * ② l'interrupteur — l'écriture partie sur l'ensemble activé complet ;
 * ③ la migration — l'appel, et le compte rendu **lisible après coup** ;
 * ④ l'ajout depuis la fiche — et sa seconde moitié, celle qui fait le critère 2 :
 *    l'intégration ajoutée au pool est **activée dans la foulée**.
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { McpEtPermissionsAgent } from "@/components/OngletMcpAgent";
import type {
  AgentCatalogueDetail,
  IntegrationPoolMcp,
  ServeurMcp,
} from "@/lib/types";

import { ficheCatalogueFactice } from "./aides";

const chargerAgentCatalogue = vi.hoisted(() => vi.fn());
const definirActivationsMcp = vi.hoisted(() => vi.fn());
const migrerDeclarationsMcp = vi.hoisted(() => vi.fn());
const ajouterIntegrationPoolMcp = vi.hoisted(() => vi.fn());
const chargerRegistreMcp = vi.hoisted(() => vi.fn());
const chargerProvenanceRegistreMcp = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...reel,
    chargerAgentCatalogue: (...a: unknown[]) => chargerAgentCatalogue(...a),
    definirActivationsMcp: (...a: unknown[]) => definirActivationsMcp(...a),
    migrerDeclarationsMcp: (...a: unknown[]) => migrerDeclarationsMcp(...a),
    ajouterIntegrationPoolMcp: (...a: unknown[]) => ajouterIntegrationPoolMcp(...a),
    chargerRegistreMcp: (...a: unknown[]) => chargerRegistreMcp(...a),
    chargerProvenanceRegistreMcp: (...a: unknown[]) =>
      chargerProvenanceRegistreMcp(...a),
  };
});

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

function integration(id: string): IntegrationPoolMcp {
  return {
    id,
    serveur: serveur(id),
    mode_auth: "token_statique",
    procedure_url: "",
    curee: true,
    source: "curee",
    admission: null,
    signaux: [],
    alerte: "",
    secrets: [],
  };
}

function fiche(partiel: Partial<AgentCatalogueDetail> = {}) {
  return {
    ...ficheCatalogueFactice({ nom: "qa", role: "Testeur" }),
    playbook: "",
    mcp_pool: [integration("slack"), integration("gitlab")],
    mcp_activations: ["slack"],
    mcp_herites: [serveur("forge", { optionnel: true })],
    ...partiel,
  } as AgentCatalogueDetail;
}

describe("l'onglet MCP & permissions d'un agent", () => {
  it("monte, sépare actives et disponibles, et dit ce qu'un retrait fait", async () => {
    chargerAgentCatalogue.mockResolvedValue(fiche());
    chargerRegistreMcp.mockResolvedValue([]);
    chargerProvenanceRegistreMcp.mockRejectedValue(new Error("hors banc"));

    render(<McpEtPermissionsAgent nom="qa" />);

    await screen.findByRole("region", { name: "Intégrations MCP de qa" });
    expect(screen.getByText("Actives sur cet agent")).toBeInTheDocument();
    expect(screen.getByText("Au pool projet, non activées")).toBeInTheDocument();
    expect(screen.getByText(/1 active sur 2 au pool projet/)).toBeInTheDocument();
    // Le critère 2, dit à l'écran : désactiver ne retire pas du pool.
    expect(
      screen.getByText(/désactive pour cet agent seul/),
    ).toBeInTheDocument();
    // Le critère 3 : le bloc hérité a une issue, plus un cul-de-sac.
    expect(screen.getByText("Héritées d'un fichier")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Migrer vers le pool projet" }),
    ).toBeInTheDocument();
    // Les permissions n'ont pas été perdues au déménagement.
    expect(
      screen.getByRole("region", { name: "Permissions de qa" }),
    ).toBeInTheDocument();
  });

  it("bascule une activation par l'interrupteur", async () => {
    chargerAgentCatalogue.mockResolvedValue(fiche());
    chargerRegistreMcp.mockResolvedValue([]);
    chargerProvenanceRegistreMcp.mockRejectedValue(new Error("hors banc"));
    definirActivationsMcp.mockResolvedValue(undefined);

    render(<McpEtPermissionsAgent nom="qa" />);
    await screen.findByRole("region", { name: "Intégrations MCP de qa" });

    await userEvent.click(
      screen.getByRole("switch", { name: "Activer gitlab pour cet agent" }),
    );
    await waitFor(() =>
      expect(definirActivationsMcp).toHaveBeenCalledWith("qa", [
        "slack",
        "gitlab",
      ]),
    );
  });

  it("migre les héritées en un geste, et rend ce que la migration a fait", async () => {
    chargerAgentCatalogue.mockResolvedValue(fiche());
    chargerRegistreMcp.mockResolvedValue([]);
    chargerProvenanceRegistreMcp.mockRejectedValue(new Error("hors banc"));
    migrerDeclarationsMcp.mockResolvedValue({
      agent: "qa",
      ajoutees: [integration("github")],
      reprises: [],
      activations: ["slack", "github"],
      fichier_retire: true,
    });

    render(<McpEtPermissionsAgent nom="qa" />);
    await screen.findByRole("region", { name: "Intégrations MCP de qa" });

    await userEvent.click(
      screen.getByRole("button", { name: "Migrer vers le pool projet" }),
    );
    await waitFor(() =>
      expect(migrerDeclarationsMcp).toHaveBeenCalledWith("qa"),
    );
    expect(
      await screen.findByText(/1 intégration ajoutée au pool projet/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/core\/mcp\/qa\.json a été retiré/),
    ).toBeInTheDocument();
  });

  it("ajoute depuis la fiche ET active dans la foulée", async () => {
    chargerAgentCatalogue.mockResolvedValue(fiche());
    chargerProvenanceRegistreMcp.mockRejectedValue(new Error("hors banc"));
    chargerRegistreMcp.mockResolvedValue([
      {
        id: "notion",
        nom: "Notion",
        description: "Une base de connaissances.",
        mode_auth: "sans_secret",
        transport: "http",
        commande: "",
        args: [],
        url: "https://exemple.test/notion",
        env: {},
        headers: {},
        tags: [],
        secrets: [],
        procedure_url: "",
        optionnel: false,
        editeur: "Notion",
        popularite: 50,
        curee: true,
        source: "curee",
        version: "",
        depot: "",
        statut: "",
        publie_le: "",
        admission: null,
        signaux: [],
      },
    ]);
    ajouterIntegrationPoolMcp.mockResolvedValue(integration("notion"));
    definirActivationsMcp.mockResolvedValue(undefined);

    render(<McpEtPermissionsAgent nom="qa" />);
    await screen.findByRole("region", { name: "Intégrations MCP de qa" });

    await userEvent.click(
      screen.getByRole("button", { name: /Chercher dans la bibliothèque/ }),
    );
    await userEvent.click(await screen.findByRole("button", { name: "Configurer" }));
    await userEvent.click(await screen.findByRole("button", { name: "Ajouter au pool" }));

    await waitFor(() =>
      expect(ajouterIntegrationPoolMcp).toHaveBeenCalledWith({
        registre_id: "notion",
        secrets: [],
      }),
    );
    // La moitié qui compte : l'ajout ne s'arrête pas au pool.
    await waitFor(() =>
      expect(definirActivationsMcp).toHaveBeenCalledWith("qa", [
        "slack",
        "notion",
      ]),
    );
  });
});

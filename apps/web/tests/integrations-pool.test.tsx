/**
 * Le **pool projet** de l'écran Intégrations — la part que #270 a laissée au lot
 * 6 (#273, lot 6/6 de #244).
 *
 * `integrations.test.tsx` tient le harnais et les trois critères du ticket à leur
 * plus mince ; sa propre en-tête renvoie ici pour « le comportement de l'écran ».
 * C'est ce fichier — le bloc rendu, ses quatre états, le retrait, et surtout la
 * question que l'écran existe pour poser : **qui utilise cette intégration ?**
 *
 * Ce qui s'y joue vraiment, et qu'aucune relecture ne rattrape : « je ne sais
 * pas » ne doit jamais s'écrire « personne ». Le catalogue est une source
 * *secondaire* de cet écran — le pool se lit sans lui —, et le jour où il ne
 * répond pas, afficher « aucun agent ne l'a activée » est un contresens **sur la
 * question même** que le ticket pose : on retirerait une intégration en croyant
 * qu'elle ne sert à rien. Les trois états d'`UtiliseePar` sont donc éprouvés un
 * par un, l'ignorance comprise.
 *
 * Couvre :
 *
 * ① `usageDuPool` — le renversement du catalogue (rangé *par agent*) vers « par
 *    intégration », l'ordre des agents qui suit le catalogue et non un tri à
 *    nous, le compte des équipés, et `USAGE_INCONNU` qui ne dit rien ;
 * ② `libelleMode` — les quatre modes de docs/21 §2, `sans_secret` (#271) compris,
 *    et l'inconnu qui retombe sur sa clé plutôt que de disparaître ;
 * ③ le bloc rendu — chargement, pool invalide, pool vide, et la ligne d'une
 *    intégration : identité, id affiché **seulement** s'il dit autre chose que le
 *    nom, mode d'auth, état de chaque secret ;
 * ④ le retrait — l'appel parti sur l'id, le rechargement demandé au succès, et
 *    l'échec qui nomme sa cause **et rend la main** ;
 * ⑤ « qui l'utilise » — les agents nommés et menant à l'onglet où l'activation se
 *    défait, l'intégration montée nulle part, et le catalogue muet.
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { PoolProjet } from "@/components/integrations/PoolProjet";
import { libelleMode } from "@/components/integrations/modes";
import { USAGE_INCONNU, usageDuPool } from "@/components/integrations/usage";
import type {
  AgentCatalogue,
  EtatSecretPool,
  IntegrationPoolMcp,
} from "@/lib/types";

import { ficheCatalogueFactice } from "./aides";

const supprimerIntegrationPoolMcp = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...reel,
    supprimerIntegrationPoolMcp: (...args: unknown[]) =>
      supprimerIntegrationPoolMcp(...args),
  };
});

function secretFactice(partiel: Partial<EtatSecretPool> = {}): EtatSecretPool {
  return {
    cle: "FIGMA_TOKEN",
    description: "Jeton d'API",
    secret: true,
    present: true,
    valide: true,
    ephemere: false,
    expire_le: null,
    ...partiel,
  };
}

function integrationFactice(
  partiel: Partial<IntegrationPoolMcp> = {},
): IntegrationPoolMcp {
  const nom = partiel.serveur?.nom ?? partiel.id ?? "figma-officiel";
  return {
    id: "figma-officiel",
    serveur: {
      nom,
      type: "http",
      commande: "",
      args: [],
      url: "https://mcp.figma.com/mcp",
      env: {},
      headers: {},
      optionnel: false,
    },
    mode_auth: "oauth_importe",
    procedure_url: "",
    curee: true,
    source: "curee",
    admission: null,
    signaux: [],
    alerte: "",
    secrets: [],
    ...partiel,
  };
}

/** Le bloc, monté avec ce qu'il lui faut — `render` nu : il ne lit aucun contexte. */
function monterLePool({
  pool = [integrationFactice()],
  erreur = null,
  chargement = false,
  usage = usageDuPool([]),
  onChangement = vi.fn(),
}: Partial<Parameters<typeof PoolProjet>[0]> = {}) {
  return render(
    <PoolProjet
      pool={pool}
      erreur={erreur}
      chargement={chargement}
      usage={usage}
      onChangement={onChangement}
    />,
  );
}

// ── ① le renversement du catalogue ───────────────────────────────────────────

describe("usageDuPool (#270)", () => {
  it("renverse « cet agent active ces intégrations » en « cette intégration est activée par »", () => {
    const usage = usageDuPool([
      ficheCatalogueFactice({ nom: "dev", mcp_activations: ["figma-officiel", "github"] }),
      ficheCatalogueFactice({ nom: "qa", mcp_activations: ["github"] }),
    ]);

    expect(usage.parIntegration.get("github")?.map((a) => a.nom)).toEqual([
      "dev",
      "qa",
    ]);
    expect(usage.parIntegration.get("figma-officiel")?.map((a) => a.nom)).toEqual([
      "dev",
    ]);
  });

  it("suit l'ordre du catalogue, jamais un tri à nous", () => {
    // Deux intégrations voisines doivent nommer leurs agents dans le même
    // ordre : sans ça la même liste se lit différemment d'une ligne à l'autre.
    const usage = usageDuPool([
      ficheCatalogueFactice({ nom: "zeta", mcp_activations: ["github"] }),
      ficheCatalogueFactice({ nom: "alpha", mcp_activations: ["github"] }),
    ]);

    expect(usage.parIntegration.get("github")?.map((a) => a.nom)).toEqual([
      "zeta",
      "alpha",
    ]);
  });

  it("compte les agents équipés une fois chacun, quel qu'en soit le nombre d'activations", () => {
    const usage = usageDuPool([
      ficheCatalogueFactice({ nom: "dev", mcp_activations: ["github", "slack"] }),
      ficheCatalogueFactice({ nom: "qa", mcp_activations: [] }),
    ]);

    expect(usage.agentsEquipes).toBe(1);
    expect(usage.agents).toBe(2);
    expect(usage.connu).toBe(true);
  });

  it("un catalogue vide est un catalogue LU — l'ignorance a son propre objet", () => {
    expect(usageDuPool([]).connu).toBe(true);
    expect(USAGE_INCONNU.connu).toBe(false);
    expect(USAGE_INCONNU.parIntegration.size).toBe(0);
  });
});

// ── ② le vocabulaire des modes d'auth ────────────────────────────────────────

describe("libelleMode (#270/#271)", () => {
  it.each([
    ["token_statique", "Token statique"],
    ["appairage", "Appairage (sans token)"],
    ["oauth_importe", "Token OAuth importé"],
    ["sans_secret", "Sans secret"],
  ])("dit « %s » en clair", (mode, libelle) => {
    expect(libelleMode(mode)).toBe(libelle);
  });

  it("retombe sur la clé d'un mode inconnu plutôt que de l'effacer", () => {
    expect(libelleMode("mode_a_venir")).toBe("mode_a_venir");
  });
});

// ── ③ les quatre états du bloc ───────────────────────────────────────────────

describe("le pool projet, rendu (#270)", () => {
  it("annonce son chargement au lieu d'un pool vide", () => {
    monterLePool({ chargement: true, pool: [] });

    expect(screen.getByText("Chargement des intégrations…")).toBeInTheDocument();
    expect(screen.queryByText(/Aucune intégration configurée/)).not.toBeInTheDocument();
  });

  it("rend un pool invalide comme une alerte, avec sa cause", () => {
    monterLePool({ erreur: "JSON illisible ligne 4", pool: [] });

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Pool invalide : JSON illisible ligne 4",
    );
  });

  it("oriente vers la bibliothèque quand le pool est vide", () => {
    monterLePool({ pool: [] });

    expect(screen.getByText(/Aucune intégration configurée/)).toBeInTheDocument();
    expect(screen.getByText(/Cherchez-en une dans la bibliothèque/)).toBeInTheDocument();
  });

  it("compte les intégrations et accorde le mot", () => {
    monterLePool({
      pool: [
        integrationFactice({ id: "github" }),
        integrationFactice({ id: "slack" }),
      ],
    });

    expect(screen.getByText("2 intégrations")).toBeInTheDocument();
  });

  it("n'affiche l'id que s'il dit autre chose que le nom du serveur", () => {
    const { unmount } = monterLePool({
      pool: [integrationFactice({ id: "figma-officiel" })],
    });
    // Nominal : le pool nomme le serveur d'après l'entrée du registre, donc la
    // ligne afficherait deux fois la même chaîne — du bruit.
    expect(screen.getAllByText("figma-officiel")).toHaveLength(1);
    unmount();

    monterLePool({
      pool: [
        integrationFactice({
          id: "figma-perso",
          serveur: { ...integrationFactice().serveur, nom: "figma-officiel" },
        }),
      ],
    });
    // Renommée à l'ajout : l'id devient une information.
    expect(screen.getByText("figma-perso")).toBeInTheDocument();
    expect(screen.getByText("figma-officiel")).toBeInTheDocument();
  });

  it("dit l'état de chaque secret, et le distingue de son absence", () => {
    monterLePool({
      pool: [
        integrationFactice({
          secrets: [
            secretFactice({ cle: "A_CONFIGURER", present: false }),
            secretFactice({ cle: "EXPIRE", valide: false }),
            secretFactice({ cle: "JETABLE", ephemere: true }),
            secretFactice({ cle: "BON" }),
          ],
        }),
      ],
    });

    expect(screen.getByText("à configurer")).toBeInTheDocument();
    expect(screen.getByText("expiré")).toBeInTheDocument();
    expect(screen.getByText("appairage (jetable)")).toBeInTheDocument();
    expect(screen.getByText("configuré")).toBeInTheDocument();
  });

  it("date une expiration quand le coffre la connaît", () => {
    monterLePool({
      pool: [
        integrationFactice({
          secrets: [
            secretFactice({ valide: false, expire_le: "2026-08-01T10:00:00Z" }),
          ],
        }),
      ],
    });

    expect(screen.getByText(/^expiré le /)).toBeInTheDocument();
  });
});

// ── ④ retirer une intégration ────────────────────────────────────────────────

describe("retirer du pool (#270)", () => {
  it("appelle l'API sur l'id, puis demande le rechargement", async () => {
    const utilisateur = userEvent.setup();
    const onChangement = vi.fn();
    supprimerIntegrationPoolMcp.mockResolvedValueOnce(undefined);
    monterLePool({ pool: [integrationFactice({ id: "slack" })], onChangement });

    await utilisateur.click(screen.getByRole("button", { name: "Retirer" }));

    expect(supprimerIntegrationPoolMcp).toHaveBeenCalledWith("slack");
    expect(onChangement).toHaveBeenCalled();
  });

  it("nomme l'échec et rend la main, sans recharger", async () => {
    const utilisateur = userEvent.setup();
    const onChangement = vi.fn();
    supprimerIntegrationPoolMcp.mockRejectedValueOnce(
      new Error("409 : encore activée sur dev"),
    );
    monterLePool({ onChangement });

    await utilisateur.click(screen.getByRole("button", { name: "Retirer" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "409 : encore activée sur dev",
    );
    expect(onChangement).not.toHaveBeenCalled();
    // Rendue : le pool n'a pas bougé, la ligne est toujours là et retirable.
    expect(screen.getByRole("button", { name: "Retirer" })).toBeEnabled();
  });
});

// ── ⑤ qui utilise cette intégration ──────────────────────────────────────────

describe("« qui l'utilise » et ses trois états (#270)", () => {
  it("nomme les agents et mène à l'onglet où l'activation se défait", () => {
    monterLePool({
      pool: [integrationFactice({ id: "github" })],
      usage: usageDuPool([
        ficheCatalogueFactice({ nom: "dev", mcp_activations: ["github"] }),
        ficheCatalogueFactice({ nom: "qa", mcp_activations: ["github"] }),
      ]),
    });

    const ligne = screen.getByRole("listitem");
    expect(within(ligne).getByText("Utilisée par")).toBeInTheDocument();
    // La fiche à son onglet MCP, pas la fiche nue : on y va pour agir.
    expect(within(ligne).getByRole("link", { name: /dev/ })).toHaveAttribute(
      "href",
      "/agents/dev/mcp",
    );
    expect(within(ligne).getByRole("link", { name: /qa/ })).toHaveAttribute(
      "href",
      "/agents/qa/mcp",
    );
  });

  it("dit qu'une intégration configurée n'est montée nulle part, et où l'activer", () => {
    monterLePool({
      pool: [integrationFactice({ id: "github" })],
      usage: usageDuPool([
        ficheCatalogueFactice({ nom: "dev", mcp_activations: ["slack"] }),
      ]),
    });

    expect(screen.getByText(/Aucun agent ne l'a activée/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Activer sur un agent" })).toHaveAttribute(
      "href",
      "/agents",
    );
  });

  it("nomme son ignorance quand le catalogue n'a pas répondu — jamais « personne »", () => {
    // Le contresens que ce test existe pour empêcher : rendre `USAGE_INCONNU`
    // comme un pool sans utilisateur ferait retirer une intégration en croyant
    // qu'elle ne sert à rien.
    monterLePool({ pool: [integrationFactice({ id: "github" })], usage: USAGE_INCONNU });

    expect(
      screen.getByText(/impossible de dire qui utilise cette intégration/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Aucun agent ne l'a activée/)).not.toBeInTheDocument();
    expect(screen.queryByText("Utilisée par")).not.toBeInTheDocument();
  });
});

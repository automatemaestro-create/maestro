/**
 * Les **permissions** de l'onglet MCP & permissions (#262, lot 10 de #243).
 *
 * Le lot a livré sans tests (docs/10 §5.1) ; ce fichier les rattrape. Il ne couvre
 * que la **politique allow/ask/deny** — le volet des intégrations MCP de la même
 * section relève du lot #263, qui apporte les siens.
 *
 * `lib/permissions` (`couvre`, `entreeConnue`, `entreesHorsPortee`) est déjà gardé
 * par `tests/permissions.test.ts`, qui annonçait en toutes lettres ses tests
 * « différés au lot 15 pour tout le reste du ticket ». Le reste, c'est le geste
 * d'écriture, et il tient en quatre promesses :
 *
 * ① **chaque geste écrit** — pas de bouton « Enregistrer ». L'état local ne bouge
 *    qu'**après** l'accord de l'API, si bien qu'une entrée refusée s'efface d'
 *    elle-même en laissant son motif ;
 * ② **le motif affiché est celui du dépôt** — il nomme la liste et l'entrée en
 *    faute, là où un « politique refusée » de notre cru n'apprendrait rien ;
 * ③ **trois listes, dont une en lecture** — `ask` est montrée sans être éditée
 *    (le cran se pose à froid) ; n'en rendre que deux ferait passer un outil
 *    arbitré pour un outil sans contrainte, puisqu'il n'apparaîtrait nulle part ;
 * ④ **une politique invalide se répare d'ici** — c'est le seul geste qui débloque,
 *    et il n'est possible que parce que l'écriture ne relit pas ce qu'elle écrase.
 *
 * La section est montée par `ContenuOngletAgent` et non en important son
 * composant : c'est le point d'entrée que la fiche utilise, et il reste vrai si
 * le composant change de fichier (ce que #263 prépare).
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ContenuOngletAgent } from "@/components/ContenuOngletAgent";
import type { AgentCatalogueDetail, PolitiquePermissions } from "@/lib/types";

import { ficheCatalogueFactice, rendreAvecEtat } from "./aides";

/** Ce que `chargerAgentCatalogue` rendra — posé par chaque test. */
let ficheServie: AgentCatalogueDetail;

/** Les politiques passées à `definirPermissions`, dans l'ordre. */
const ecrites: { agent: string; politique: PolitiquePermissions }[] = [];

/** Ce que la prochaine écriture fera : rien, ou le refus posé ici. */
let refus: string | null = null;

vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  const aides = await import("./aides");
  return {
    ...reel,
    chargerProjets: () => Promise.resolve(aides.projetsDeclares()),
    chargerJournal: () => Promise.resolve(aides.pageJournalCourante()),
    chargerFournisseurs: () => Promise.resolve(aides.fournisseursDuPoste()),
    chargerCatalogue: () => Promise.resolve(aides.catalogueAgents()),
    chargerAgentCatalogue: () => Promise.resolve(ficheServie),
    definirPermissions: (agent: string, politique: PolitiquePermissions) => {
      ecrites.push({ agent, politique });
      return refus === null
        ? Promise.resolve()
        : Promise.reject(new Error(refus));
    },
  };
});

/** Les outils que la fiche suggère — les trois origines de `permissions_outils`. */
const OUTILS_EXPOSES = [
  { nom: "Read", origine: "integre", libelle: "outil intégré du profil" },
  { nom: "Bash", origine: "integre", libelle: "outil intégré du profil" },
  {
    nom: "mcp__maestro__demander_arbitrage",
    origine: "maestro",
    libelle: "demander un arbitrage",
  },
  {
    nom: "mcp__slack",
    origine: "mcp",
    libelle: "serveur MCP slack (tous ses outils)",
  },
] as const;

/** Pose la fiche que l'onglet lira, sur les défauts d'un agent du code. */
function poserFiche(partiel: Partial<AgentCatalogueDetail> = {}) {
  ficheServie = {
    ...ficheCatalogueFactice({
      nom: "dev",
      permissions_outils: [...OUTILS_EXPOSES],
    }),
    playbook: "",
    ...partiel,
  };
}

/** Monte l'onglet MCP & permissions de `dev` et attend la fiche. */
async function monter() {
  const utilisateur = userEvent.setup();
  const vue = rendreAvecEtat(<ContenuOngletAgent nom="dev" onglet="mcp" />);
  await screen.findByRole("region", { name: "Permissions de dev" });
  return { utilisateur, ...vue };
}

/** La section des permissions — jamais l'écran entier : le voisin MCP en est un autre. */
function section() {
  return screen.getByRole("region", { name: "Permissions de dev" });
}

/** La dernière politique écrite. */
function derniereEcriture() {
  return ecrites.at(-1)?.politique;
}

beforeEach(() => {
  ecrites.length = 0;
  refus = null;
  poserFiche();
});

describe("① chaque geste écrit, et l'écran ne devance pas l'API", () => {
  it("crée une politique dédiée à la première entrée ajoutée", async () => {
    const { utilisateur } = await monter();
    expect(within(section()).getByText(/Aucune politique dédiée/)).toBeInTheDocument();

    await utilisateur.type(
      screen.getByLabelText("allow — liste fermée"),
      "Read{Enter}",
    );

    // Pas de bouton « Enregistrer » : le geste *est* l'écriture.
    await waitFor(() => expect(ecrites).toHaveLength(1));
    expect(ecrites[0].agent).toBe("dev");
    expect(derniereEcriture()).toEqual({ allow: ["Read"], ask: {}, deny: [] });
    expect(within(section()).queryByText(/Aucune politique dédiée/)).toBeNull();
  });

  it("écrit la liste entière, jamais la seule entrée ajoutée", async () => {
    poserFiche({ permissions: { allow: ["Read"], ask: {}, deny: ["WebFetch"] } });
    const { utilisateur } = await monter();

    await utilisateur.type(
      screen.getByLabelText("allow — liste fermée"),
      "Bash{Enter}",
    );

    // Le remplacement est intégral côté API : envoyer un diff perdrait `deny`.
    await waitFor(() =>
      expect(derniereEcriture()).toEqual({
        allow: ["Read", "Bash"],
        ask: {},
        deny: ["WebFetch"],
      }),
    );
  });

  it("retire une entrée par son jeton", async () => {
    poserFiche({ permissions: { allow: ["Read", "Bash"], ask: {}, deny: [] } });
    const { utilisateur } = await monter();

    await utilisateur.click(
      within(section()).getByRole("button", {
        name: /Retirer l'entrée allow « Bash »/,
      }),
    );

    await waitFor(() =>
      expect(derniereEcriture()).toEqual({ allow: ["Read"], ask: {}, deny: [] }),
    );
  });

  it("laisse l'écran sur l'état d'avant quand l'API refuse", async () => {
    poserFiche({ permissions: { allow: ["Read"], ask: {}, deny: [] } });
    refus = "politique de permissions invalide pour l'agent 'dev' : entrée allow 'mon outil'";
    const { utilisateur } = await monter();

    await utilisateur.type(
      screen.getByLabelText("allow — liste fermée"),
      "mon outil{Enter}",
    );

    // L'entrée refusée s'efface d'elle-même : l'état local ne suit qu'en cas
    // d'accord, donc rien ne laisse croire qu'elle est en vigueur. Les jetons se
    // comptent par leur bouton de retrait — le nom du jeton, lui, se répète dans
    // le libellé de ce bouton et dans les suggestions.
    await screen.findByRole("alert");
    expect(
      within(section()).queryByRole("button", { name: /« mon outil »/ }),
    ).toBeNull();
    expect(
      within(section()).getByRole("button", {
        name: /Retirer l'entrée allow « Read »/,
      }),
    ).toBeInTheDocument();
  });
});

describe("② le motif affiché est celui du dépôt", () => {
  it("rend la cause exacte du refus, sans la reformuler", async () => {
    refus =
      "politique de permissions invalide pour l'agent 'dev' : entrée deny 'mon outil' (nom d'outil attendu).";
    const { utilisateur } = await monter();

    await utilisateur.type(
      screen.getByLabelText("deny — l'emporte sur tout"),
      "mon outil{Enter}",
    );

    // C'est ce motif-là qui est utile — il nomme la liste et l'entrée en faute —,
    // pas un « politique refusée » de notre cru.
    const alerte = await within(section()).findByRole("alert");
    expect(alerte).toHaveTextContent("entrée deny");
    expect(alerte).toHaveTextContent("mon outil");
  });

  it("signale une entrée hors des outils exposés sans l'interdire", async () => {
    poserFiche({
      permissions: { allow: ["Read", "OutilFantome"], ask: {}, deny: [] },
    });

    await monter();

    // La saisie reste libre — un outil MCP précis se désigne à la frappe —, mais
    // ce que le profil n'expose pas se voit.
    expect(
      within(section()).getByText(/hors des outils exposés/),
    ).toBeInTheDocument();
  });

  it("propose les outils que l'agent peut réellement appeler", async () => {
    await monter();

    const champ = screen.getByLabelText("allow — liste fermée");
    const liste = document.getElementById(champ.getAttribute("list") ?? "");
    const proposes = [...(liste?.querySelectorAll("option") ?? [])].map((option) =>
      option.getAttribute("value"),
    );
    // Les trois origines servies avec la fiche : intégrés, verbes « maestro », MCP.
    expect(proposes).toEqual([
      "Read",
      "Bash",
      "mcp__maestro__demander_arbitrage",
      "mcp__slack",
    ]);
  });
});

describe("③ trois listes, dont une en lecture", () => {
  it("montre les entrées arbitrées avec qui les tranche", async () => {
    poserFiche({
      permissions: { allow: [], ask: { Bash: "humain" }, deny: [] },
    });

    await monter();

    // N'en rendre que deux ferait passer un outil arbitré pour un outil sans
    // contrainte, puisqu'il n'apparaîtrait dans aucune. Le jeton se lit par son
    // cran — « Bash » seul se trouve aussi dans les suggestions des deux autres
    // listes, et l'y chercher ne prouverait rien.
    expect(within(section()).getByText("ask — soumis à arbitrage")).toBeInTheDocument();
    const cran = within(section()).getByText("— humain");
    expect(cran.parentElement).toHaveTextContent("Bash");
  });

  it("n'offre aucun geste sur ask, et dit où le cran se pose", async () => {
    poserFiche({ permissions: { allow: [], ask: { Bash: "auto" }, deny: [] } });

    await monter();

    // Une entrée qu'on pourrait créer sans choisir son décideur retomberait sur
    // le défaut sans le dire — or le défaut *est* le cran le plus fermé.
    expect(screen.queryByLabelText(/^ask/)).toBeNull();
    expect(
      within(section()).getByText(/il n'est pas réglable ici/),
    ).toBeInTheDocument();
    // Nommé deux fois — pour le cran d'arbitrage, et pour la politique entière,
    // qui est un fichier versionné avec le dépôt.
    expect(
      within(section()).getAllByText("core/permissions/dev.json"),
    ).toHaveLength(2);
  });

  it("dit ce que chaque liste vide veut dire", async () => {
    poserFiche({ permissions: { allow: [], ask: {}, deny: [] } });

    await monter();

    // Trois vides, trois sens différents : les confondre ferait lire une
    // politique fermée là où tout est permis.
    const dedans = within(section());
    expect(
      dedans.getByText(/tout ce que le profil expose est permis/),
    ).toBeInTheDocument();
    expect(dedans.getByText("Vide — aucun outil interdit.")).toBeInTheDocument();
    expect(
      dedans.getByText("Vide — aucun outil soumis à arbitrage."),
    ).toBeInTheDocument();
  });

  it("rappelle l'ordre d'application et la portée d'une entrée", async () => {
    await monter();

    expect(
      within(section()).getByText(/deny l'emporte sur ask, qui l'emporte sur allow/),
    ).toBeInTheDocument();
  });
});

describe("④ une politique invalide se répare d'ici", () => {
  it("nomme la cause et n'offre que le geste qui débloque", async () => {
    poserFiche({
      permissions: null,
      permissions_erreur: "décideur 'orchestrateur' de l'entrée ask 'Bash' inconnu",
    });

    await monter();

    const alerte = within(section()).getByRole("alert");
    expect(alerte).toHaveTextContent("Politique invalide");
    expect(alerte).toHaveTextContent("décideur 'orchestrateur'");
    // Tant qu'elle est illisible elle n'est appliquée à rien : les champs
    // d'édition n'ont donc rien à montrer, et seraient trompeurs.
    expect(screen.queryByLabelText("allow — liste fermée")).toBeNull();
    expect(
      within(section()).getByRole("button", { name: /Repartir d'une politique vide/ }),
    ).toBeInTheDocument();
  });

  it("remplace la politique illisible et rouvre l'édition", async () => {
    poserFiche({
      permissions: null,
      permissions_erreur: "politique de permissions illisible pour l'agent 'dev'",
    });
    const { utilisateur } = await monter();

    await utilisateur.click(
      within(section()).getByRole("button", { name: /Repartir d'une politique vide/ }),
    );

    // L'écriture ne relit pas ce qu'elle écrase : c'est ce qui permet de corriger
    // depuis l'écran un fichier que la lecture refuse.
    await waitFor(() =>
      expect(derniereEcriture()).toEqual({ allow: [], ask: {}, deny: [] }),
    );
    expect(screen.getByLabelText("allow — liste fermée")).toBeInTheDocument();
    expect(within(section()).queryByText(/Politique invalide/)).toBeNull();
  });

  it("garde le diagnostic quand la réparation échoue à son tour", async () => {
    poserFiche({
      permissions: null,
      permissions_erreur: "politique de permissions illisible pour l'agent 'dev'",
    });
    refus = "backend injoignable";
    const { utilisateur } = await monter();

    await utilisateur.click(
      within(section()).getByRole("button", { name: /Repartir d'une politique vide/ }),
    );

    await waitFor(() => expect(ecrites).toHaveLength(1));
    // Le fichier est toujours illisible : rouvrir l'édition ferait croire à une
    // réparation qui n'a pas eu lieu.
    expect(within(section()).getByText(/Politique invalide/)).toBeInTheDocument();
    expect(screen.queryByLabelText("allow — liste fermée")).toBeNull();
  });
});

describe("la fiche, avant les permissions", () => {
  it("dit qu'elle se charge, puis pourquoi elle est illisible", async () => {
    const echec = vi
      .spyOn(await import("@/lib/api"), "chargerAgentCatalogue")
      .mockRejectedValue(new Error("agent inconnu : dev"));

    rendreAvecEtat(<ContenuOngletAgent nom="dev" onglet="mcp" />);

    expect(screen.getByText("Chargement de la fiche…")).toBeInTheDocument();
    const alerte = await screen.findByRole("alert");
    expect(alerte).toHaveTextContent("Fiche illisible : agent inconnu : dev");
    echec.mockRestore();
  });
});

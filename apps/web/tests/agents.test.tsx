/**
 * La fiche agent à onglets et la survie des anciennes routes (#193, lot 4/4 de
 * la navigation v2 #189 — tests différés du lot #190).
 *
 * Trois promesses, toutes cassables sans que le lint ni le build n'y voient
 * rien :
 *
 * - **un agent se consulte d'un seul endroit** : `lib/agents.ts` est la source
 *   unique des facettes, exactement comme `lib/navigation.ts` l'est du menu.
 *   La barre d'onglets, les cartes de la liste et les routes dynamiques la
 *   lisent toutes — une facette ajoutée là apparaît partout, et nulle part
 *   ailleurs il n'y a de liste à tenir à jour ;
 * - **aucun signet ne casse** : `/catalogue`, `/playbooks` et `/chat/<agent>`
 *   ont disparu du produit mais pas du monde — ils sont écrits dans la doc,
 *   dans des tickets, dans des fils de discussion. Les redirections de
 *   `next.config.ts` sont le contrat de non-régression du lot, et rien à
 *   l'exécution ne signalerait qu'une entrée y a été perdue ;
 * - **une entrée non maîtrisée ne casse pas la page** : le `?onglet=` d'une
 *   redirection et le segment d'URL saisi à la main viennent tous deux de
 *   l'extérieur, et retombent sur le profil plutôt que sur un écran vide.
 */

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { CreationAgentEcran } from "@/components/CreationAgentEcran";
import { ListeAgents } from "@/components/ListeAgents";
import { OngletsAgent } from "@/components/OngletsAgent";
import {
  CHEMIN_CREATION_AGENT,
  cheminOnglet,
  estNomAgentReserve,
  estOngletAgent,
  ONGLET_AGENT_DEFAUT,
  ONGLETS_AGENT,
  ongletAgentOuDefaut,
  ongletDuChemin,
} from "@/lib/agents";
import { entreeCourante, MENU } from "@/lib/navigation";
import { REDIRECTIONS_NAVIGATION_V1 } from "@/next.config";

import { ficheCatalogueFactice, navigations, poserChemin } from "./aides";

// La liste des agents charge le catalogue par le REST : le réseau reste
// débranché (`setup.ts`), c'est la fixture qui décide de ce qu'elle affiche.
// `creerAgent` est déclaré parce que l'écran de création l'importe — jamais
// appelé ici, ces tests portant sur les sorties et non sur le `POST`.
const catalogue = vi.hoisted(() => ({ fiches: [] as unknown[] }));
// ⚠ Ce mock est **total** (pas d'`importOriginal`) : il *remplace* celui de
// `setup.ts`, donc ce qu'il n'énumère pas n'existe pas — c'est la leçon de #249,
// et elle mord ici. L'écran de création monte `FormulaireDefinition`, qui lit le
// catalogue des fournisseurs depuis #487 : sans cette entrée, les cinq tests de
// l'écran tombent sur « No "chargerFournisseurs" export is defined ».
//
// Le défaut n'était visible d'aucune des deux PR qui l'ont créé : #487 a rendu
// la lecture obligatoire, #810 a monté ce formulaire dans ces tests-là, et
// chacune était **verte seule**. Le rouge n'est né que de leur rencontre sur
// `main` — d'où sa réparation ici plutôt qu'un signalement.
//
// `genererDefinitionAgent` (#257) est là pour la même raison, un lot plus tard :
// jamais appelé ici — ces tests ne touchent pas au bouton « Générer » —, mais un
// mock qui ne le porterait pas lèverait au premier test qui le fera.
//
// L'import est chargé **dans** la fabrique : `vi.mock` est hissé au-dessus des
// imports du fichier, donc y nommer `fournisseursDuPoste` lèverait un « Cannot
// access before initialization » (même contrainte que `tests/ecrans-reseau.ts`).
vi.mock("@/lib/api", async () => ({
  chargerCatalogue: async () => catalogue.fiches,
  creerAgent: async () => undefined,
  chargerFournisseurs: async () => {
    const { fournisseursDuPoste } = await import("./aides");
    return fournisseursDuPoste();
  },
  genererDefinitionAgent: async () => {
    throw new Error("génération non scriptée dans ce fichier de tests");
  },
}));

/** Monte la liste et attend la fin de son chargement différé d'un tick. */
async function rendreListe(
  fiches: ReturnType<typeof ficheCatalogueFactice>[],
  props: Parameters<typeof ListeAgents>[0] = {},
) {
  catalogue.fiches = fiches;
  render(<ListeAgents {...props} />);
  await waitFor(() =>
    expect(screen.queryByText("Chargement du catalogue…")).toBeNull(),
  );
}

describe("les facettes d'un agent (lib/agents)", () => {
  it("reprend les trois anciennes pages, plus MCP & permissions", () => {
    // Profil ← /catalogue, Playbook ← /playbooks, Chat ← /chat/<agent> ; MCP &
    // permissions n'avait aucune page à soi et n'en gagne pas une, seulement
    // une facette de la fiche.
    expect(ONGLETS_AGENT.map((onglet) => onglet.cle)).toEqual([
      "profil",
      "playbook",
      "mcp",
      "chat",
    ]);
  });

  it("ouvre la fiche sur le profil quand rien n'est demandé", () => {
    expect(ONGLET_AGENT_DEFAUT).toBe("profil");
    expect(estOngletAgent(ONGLET_AGENT_DEFAUT)).toBe(true);
  });

  it("ne reconnaît que les onglets déclarés", () => {
    expect(estOngletAgent("playbook")).toBe(true);
    expect(estOngletAgent("permissions")).toBe(false);
    expect(estOngletAgent(undefined)).toBe(false);
  });

  it("retombe sur le profil pour une valeur venue de l'extérieur", () => {
    // Le `?onglet=` d'une redirection, un segment d'URL tapé à la main : on ne
    // maîtrise ni l'un ni l'autre. Aucun des deux ne doit rendre une page vide.
    expect(ongletAgentOuDefaut("playbook")).toBe("playbook");
    expect(ongletAgentOuDefaut("inconnu")).toBe(ONGLET_AGENT_DEFAUT);
    expect(ongletAgentOuDefaut(undefined)).toBe(ONGLET_AGENT_DEFAUT);
    // Next rend un tableau quand le paramètre est répété (`?onglet=a&onglet=b`).
    expect(ongletAgentOuDefaut(["chat", "profil"])).toBe("chat");
    expect(ongletAgentOuDefaut([])).toBe(ONGLET_AGENT_DEFAUT);
  });

  it("fabrique le chemin d'une facette, nom échappé", () => {
    expect(cheminOnglet("dev")).toBe("/agents/dev/profil");
    expect(cheminOnglet("dev", "chat")).toBe("/agents/dev/chat");
    // Un agent personnalisé porte le nom qu'on lui a donné : il finit dans une
    // URL, il doit donc y être échappé.
    expect(cheminOnglet("chef de projet", "mcp")).toBe(
      "/agents/chef%20de%20projet/mcp",
    );
  });

  it("relit l'onglet dans le chemin, sans que la page le redise", () => {
    expect(ongletDuChemin("/agents/dev/playbook")).toBe("playbook");
    // `/agents/<nom>` nu, avant que la page ne redirige : le défaut.
    expect(ongletDuChemin("/agents/dev")).toBe(ONGLET_AGENT_DEFAUT);
    expect(ongletDuChemin("/agents/dev/inconnu")).toBe(ONGLET_AGENT_DEFAUT);
    // Hors d'une fiche, la question n'a pas de sens : pas de plantage non plus.
    expect(ongletDuChemin("/couts")).toBe(ONGLET_AGENT_DEFAUT);
    expect(ongletDuChemin("/")).toBe(ONGLET_AGENT_DEFAUT);
  });

  it("garde toute fiche sous l'entrée de menu « Agents »", () => {
    // Sans quoi l'entrée perdrait sa mise en évidence dès qu'on ouvre un agent.
    for (const { cle } of ONGLETS_AGENT) {
      expect(entreeCourante(cheminOnglet("dev", cle))?.href).toBe("/agents");
    }
  });
});

describe("la barre d'onglets (OngletsAgent)", () => {
  const monter = (nom = "dev") =>
    render(
      <OngletsAgent nom={nom}>
        <p>contenu de l&apos;onglet</p>
      </OngletsAgent>,
    );

  it("rend un onglet par facette, dans l'ordre déclaré", () => {
    poserChemin("/agents/dev/profil");
    monter();
    const barre = screen.getByRole("navigation", { name: "Facettes de dev" });
    expect(
      within(barre)
        .getAllByRole("link")
        .map((lien) => lien.getAttribute("href")),
    ).toEqual(ONGLETS_AGENT.map(({ cle }) => cheminOnglet("dev", cle)));
  });

  it("déduit l'onglet actif du chemin", () => {
    poserChemin("/agents/dev/playbook");
    monter();
    expect(screen.getByRole("link", { name: /Playbook/ })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByRole("link", { name: /Profil/ })).not.toHaveAttribute(
      "aria-current",
    );
  });

  it("marque le profil sur une fiche sans onglet dans l'URL", () => {
    poserChemin("/agents/dev");
    monter();
    expect(screen.getByRole("link", { name: /Profil/ })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("garde le retour à la liste et le contenu de l'onglet", () => {
    poserChemin("/agents/dev/chat");
    monter();
    expect(
      screen.getByRole("link", { name: /Tous les agents/ }),
    ).toHaveAttribute("href", "/agents");
    expect(screen.getByText("contenu de l'onglet")).toBeInTheDocument();
  });

  it("survit à un nom d'agent qui doit être échappé", () => {
    poserChemin("/agents/chef%20de%20projet/mcp");
    monter("chef de projet");
    const barre = screen.getByRole("navigation", {
      name: "Facettes de chef de projet",
    });
    expect(
      within(barre).getByRole("link", { name: /MCP & permissions/ }),
    ).toHaveAttribute("href", "/agents/chef%20de%20projet/mcp");
  });
});

describe("la liste des agents (ListeAgents)", () => {
  it("ouvre chaque agent sur son profil par défaut", async () => {
    await rendreListe([
      ficheCatalogueFactice({ nom: "dev" }),
      ficheCatalogueFactice({ nom: "qa", role: "Testeur" }),
    ]);
    expect(screen.getByRole("link", { name: /dev/ })).toHaveAttribute(
      "href",
      "/agents/dev/profil",
    );
    expect(screen.getByRole("link", { name: /qa/ })).toHaveAttribute(
      "href",
      "/agents/qa/profil",
    );
  });

  it("porte l'intention d'une redirection jusqu'aux cartes", async () => {
    // `/playbooks` n'a pas d'agent à ouvrir : la redirection amène ici avec
    // `?onglet=playbook`, et les cartes visent alors directement cet onglet —
    // un signet sur l'ancienne page mène toujours au bon endroit, sans détour
    // par le profil.
    await rendreListe([ficheCatalogueFactice({ nom: "dev" })], {
      ongletCible: "playbook",
    });
    expect(screen.getByRole("link", { name: /dev/ })).toHaveAttribute(
      "href",
      "/agents/dev/playbook",
    );
    expect(screen.getByText(/Ouvre l'onglet « Playbook »/)).toBeInTheDocument();
  });

  it("dit ce qu'est une fiche quand on arrive sans intention", async () => {
    await rendreListe([ficheCatalogueFactice()]);
    expect(
      screen.getByText(/profil, playbook, MCP & permissions, chat/),
    ).toBeInTheDocument();
  });

  it("propose de créer un agent quand le catalogue est vide", async () => {
    await rendreListe([]);
    expect(screen.getByText(/Aucun agent au catalogue/)).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /Nouvel agent/ }),
    ).toHaveAttribute("href", CHEMIN_CREATION_AGENT);
  });

  it("met la porte de création en tête, avant les cartes (#254)", async () => {
    // Le reproche du ticket, pris littéralement : le bouton était **sous** les
    // cartes, donc d'autant plus loin qu'il y avait d'agents. L'ordre du DOM est
    // ce qui le dit — c'est aussi celui que suit la tabulation.
    await rendreListe([
      ficheCatalogueFactice({ nom: "dev" }),
      ficheCatalogueFactice({ nom: "qa", role: "Testeur" }),
    ]);
    const porte = screen.getByRole("link", { name: /Nouvel agent/ });
    const premiere = screen.getByRole("link", { name: /dev/ });
    expect(
      porte.compareDocumentPosition(premiere) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("offre la création avant même que le catalogue soit lu", () => {
    // Elle n'en dépend pas, et un bouton qui apparaît après coup déplace ce
    // qu'on s'apprêtait à cliquer. On ne laisse donc pas passer le tick.
    catalogue.fiches = [];
    render(<ListeAgents />);
    expect(screen.getByText("Chargement du catalogue…")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /Nouvel agent/ }),
    ).toBeInTheDocument();
  });
});

describe("l'écran de création (#254)", () => {
  const monter = () => {
    poserChemin(CHEMIN_CREATION_AGENT);
    return render(<CreationAgentEcran />);
  };

  /** Écrit dans le champ « Nom » — le geste le plus court qui fait un brouillon. */
  const commencerUneSaisie = async (
    utilisateur: ReturnType<typeof userEvent.setup>,
  ) => {
    await utilisateur.type(
      screen.getByRole("textbox", { name: /Nom/ }),
      "dev-front",
    );
  };

  it("est servi par une route à lui", async () => {
    // La constante et le dossier doivent dire le même chemin : rien à
    // l'exécution ne le signalerait, la liste mènerait simplement à un 404.
    const { existsSync } = await import("node:fs");
    const path = await import("node:path");
    const { fileURLToPath } = await import("node:url");
    const page = path.join(
      path.dirname(fileURLToPath(import.meta.url)),
      "..",
      "app",
      ...CHEMIN_CREATION_AGENT.split("/").filter(Boolean),
      "page.tsx",
    );
    expect(existsSync(page), `« ${CHEMIN_CREATION_AGENT} » n'a pas de page`).toBe(
      true,
    );
  });

  it("garde le cadre : la page reste sous l'entrée « Agents »", () => {
    // Le premier critère du ticket vu du menu — barre latérale et barre
    // supérieure restent en place, seul le contenu change.
    expect(entreeCourante(CHEMIN_CREATION_AGENT)?.href).toBe("/agents");
  });

  it("revient à la liste sans rien demander quand rien n'est saisi", () => {
    monter();
    const retour = screen.getByRole("link", { name: /Tous les agents/ });
    expect(retour).toHaveAttribute("href", "/agents");
    // `fireEvent` rend faux quand le clic a été retenu : sans brouillon, le lien
    // est suivi comme un lien — rien à demander, rien à intercepter. (jsdom note
    // au passage qu'il ne sait pas naviguer ; c'est précisément la preuve que le
    // défaut n'a pas été empêché.)
    expect(fireEvent.click(retour)).toBe(true);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("quitte sur Échap", async () => {
    const utilisateur = userEvent.setup();
    monter();
    await utilisateur.keyboard("{Escape}");
    expect(navigations).toContain("/agents");
  });

  it("signale le brouillon avant de le perdre, par le retour comme par Échap", async () => {
    const utilisateur = userEvent.setup();
    monter();
    await commencerUneSaisie(utilisateur);

    await utilisateur.keyboard("{Escape}");
    expect(screen.getByRole("alert")).toHaveTextContent(/Brouillon non enregistré/);
    expect(navigations).not.toContain("/agents");

    // Reprendre la saisie retire la question et laisse le brouillon en place.
    await utilisateur.click(screen.getByRole("button", { name: /Reprendre la saisie/ }));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByRole("textbox", { name: /Nom/ })).toHaveValue("dev-front");

    // Le lien de retour pose la même question, et ne navigue pas de lui-même.
    await utilisateur.click(screen.getByRole("link", { name: /Tous les agents/ }));
    expect(screen.getByRole("alert")).toHaveTextContent(/Brouillon non enregistré/);
    expect(navigations).not.toContain("/agents");

    // Abandonner reste un geste explicite.
    await utilisateur.click(
      screen.getByRole("button", { name: /Quitter sans enregistrer/ }),
    );
    expect(navigations).toContain("/agents");
  });

  it("ne tranche pas la question sur une frappe répétée d'Échap", async () => {
    // Un second Échap retire la question au lieu de valider la perte : rien ne
    // se perd sur une touche qu'on relâche deux fois.
    const utilisateur = userEvent.setup();
    monter();
    await commencerUneSaisie(utilisateur);
    await utilisateur.keyboard("{Escape}");
    await utilisateur.keyboard("{Escape}");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(navigations).not.toContain("/agents");
  });

  it("refuse le nom que la route de création occupe déjà", async () => {
    const utilisateur = userEvent.setup();
    monter();
    await utilisateur.type(
      screen.getByRole("textbox", { name: /Nom/ }),
      "nouveau",
    );
    expect(estNomAgentReserve("Nouveau ")).toBe(true);
    expect(estNomAgentReserve("nouveau-2")).toBe(false);
    expect(screen.getByText(/est l'adresse de cette page/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Créer l'agent/ })).toBeDisabled();
  });
});

describe("les anciennes routes (next.config)", () => {
  /** La redirection déclarée pour ce chemin d'origine, s'il y en a une. */
  const versDepuis = (source: string) =>
    REDIRECTIONS_NAVIGATION_V1.find((regle) => regle.source === source);

  it("mène chaque ancien chemin à l'onglet qu'il visait", () => {
    expect(versDepuis("/catalogue")?.destination).toBe("/agents");
    expect(versDepuis("/catalogue/:nom")?.destination).toBe(
      "/agents/:nom/profil",
    );
    expect(versDepuis("/playbooks/:nom")?.destination).toBe(
      "/agents/:nom/playbook",
    );
    expect(versDepuis("/chat/:nom")?.destination).toBe("/agents/:nom/chat");
  });

  it("passe l'intention à la liste quand l'URL ne nomme pas d'agent", () => {
    // `/playbooks` nu n'a pas de fiche à ouvrir : on garde ce que la personne
    // cherchait, la liste s'en sert pour viser les cartes.
    expect(versDepuis("/playbooks")?.destination).toBe(
      "/agents?onglet=playbook",
    );
  });

  it("laisse « /chat » nu à sa page, qui existe toujours", () => {
    // Le piège du lot : rediriger `/chat/:nom` sans épargner `/chat`, qui reste
    // au menu pour le chat global (chantier « Chat » de la Phase 6). Une entrée
    // de menu qui se redirige elle-même est un aller simple.
    expect(versDepuis("/chat")).toBeUndefined();
    expect(MENU.map((entree) => entree.href)).toContain("/chat");
  });

  it("redirige en 307, jamais en 308", () => {
    // Un 308 est mis en cache par le navigateur pour de bon : ces chemins ne
    // pourraient plus jamais être corrigés côté serveur.
    for (const regle of REDIRECTIONS_NAVIGATION_V1) {
      expect(regle.permanent, `« ${regle.source} » redirige en permanent`).toBe(
        false,
      );
    }
  });

  it("ne laisse aucun ancien chemin sans reprise", () => {
    // La liste des trois pages fusionnées par #190, écrite ici en toutes
    // lettres : en retirer une de `next.config` casserait des signets en
    // silence, aucun test d'exécution ne passant plus par ces URL.
    for (const source of [
      "/catalogue",
      "/catalogue/:nom",
      "/playbooks",
      "/playbooks/:nom",
      "/chat/:nom",
    ]) {
      expect(versDepuis(source), `« ${source} » n'est plus repris`).toBeDefined();
    }
  });

  it("ne renvoie que vers des chemins que le produit sert encore", async () => {
    // Une redirection vers une page supprimée est un lien mort de plus, pas de
    // moins. On confronte chaque destination aux routes réellement présentes
    // sous `app/` — le seul endroit où l'information existe. Un segment est
    // servi soit par le dossier du même nom, soit par un segment dynamique
    // (`profil` l'est par `[onglet]`, `dev` par `[nom]`).
    const { existsSync, readdirSync } = await import("node:fs");
    const path = await import("node:path");
    const { fileURLToPath } = await import("node:url");
    const app = path.join(
      path.dirname(fileURLToPath(import.meta.url)),
      "..",
      "app",
    );

    const dossierServant = (parent: string, segment: string) => {
      const exact = path.join(parent, segment);
      if (existsSync(exact)) return exact;
      const dynamique = readdirSync(parent, { withFileTypes: true }).find(
        (entree) => entree.isDirectory() && entree.name.startsWith("["),
      );
      return dynamique ? path.join(parent, dynamique.name) : null;
    };

    for (const { source, destination } of REDIRECTIONS_NAVIGATION_V1) {
      const segments = destination
        .split("?")[0]
        .split("/")
        .filter((segment) => segment !== "");

      let dossier: string | null = app;
      for (const segment of segments) {
        dossier = dossier === null ? null : dossierServant(dossier, segment);
      }
      expect(
        dossier !== null && existsSync(path.join(dossier, "page.tsx")),
        `« ${source} » mène à « ${destination} », qui n'a pas de page`,
      ).toBe(true);
    }
  });

  it("ne vise que des onglets déclarés", () => {
    // Le contrôle de route ci-dessus s'arrête au fichier qui répond, et
    // `[onglet]` répond à *n'importe quel* segment : `/agents/dev/playbok`
    // rendrait bien une page — le profil, silencieusement, au lieu du playbook
    // demandé. Ce qui décide vraiment de la destination, c'est `lib/agents`.
    for (const { source, destination } of REDIRECTIONS_NAVIGATION_V1) {
      const [chemin, requete] = destination.split("?");
      const segments = chemin.split("/").filter((segment) => segment !== "");

      // `/agents/:nom/<onglet>` — le troisième segment nomme la facette.
      if (segments[0] === "agents" && segments.length === 3) {
        expect(
          estOngletAgent(segments[2]),
          `« ${source} » mène à l'onglet « ${segments[2]} », qui n'est pas déclaré`,
        ).toBe(true);
      }

      // `/agents?onglet=<onglet>` — l'intention passée à la liste.
      const cible = new URLSearchParams(requete ?? "").get("onglet");
      if (cible !== null) {
        expect(
          estOngletAgent(cible),
          `« ${source} » demande l'onglet « ${cible} », qui n'est pas déclaré`,
        ).toBe(true);
      }
    }
  });

  it("mène chaque ancienne page à la facette qui l'a absorbée", () => {
    // La correspondance page v1 → onglet v2, prise du seul côté qui compte pour
    // la personne qui suit un vieux lien : elle arrive sur ce qu'elle cherchait.
    const facetteDe = (source: string) => {
      const destination = versDepuis(source)?.destination ?? "";
      const [chemin, requete] = destination.split("?");
      return (
        new URLSearchParams(requete ?? "").get("onglet") ??
        chemin.split("/").filter(Boolean)[2]
      );
    };
    expect(facetteDe("/catalogue/:nom")).toBe("profil");
    expect(facetteDe("/playbooks")).toBe("playbook");
    expect(facetteDe("/playbooks/:nom")).toBe("playbook");
    expect(facetteDe("/chat/:nom")).toBe("chat");
  });
});

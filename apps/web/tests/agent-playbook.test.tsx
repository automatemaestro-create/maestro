/**
 * L'onglet **Playbook** d'une fiche agent (#260 et #261, lots 8 et 9 de #243).
 *
 * Les deux lots ont livré sans tests (docs/10 §5.1) ; ce fichier les rattrape.
 * `lib/completionsPlaybook` et `lib/diff` sont gardés à part, sur leur propre
 * règle : ce qui manquait est l'**écran**, et ce qu'il promet tient en deux
 * moitiés que le ticket a séparées.
 *
 * ① **une publication versionnée qui se lit** (#260) — la version en vigueur sans
 *    rien ouvrir, le playbook d'origine compté comme une version (v0, *pas un
 *    trou*), l'historique qui ne mange plus l'écran mais reste ouvrable, et les
 *    trois gestes qui en sortent (restaurer, appliquer, rejeter). Le point le
 *    moins évident, et le plus facile à défaire : **la version courante n'entre
 *    pas dans l'historique** — l'offrir deux fois ferait chercher la différence
 *    entre les deux ;
 * ② **une rédaction assistée** (#261) — des complétions **locales** servies par le
 *    lexique du dépôt, et un assistant qui rend un **différentiel** dont rien
 *    n'est publié : appliquer envoie le texte dans la zone d'édition, publier
 *    reste un geste à part. Deux frontières que le lot pose et qu'un raccourci
 *    ferait tomber d'un coup.
 *
 * Un troisième fil traverse les deux : **ce qui n'est pas publié se dit**. Un
 * brouillon modifié, une proposition en attente, une réécriture en vol — chacun a
 * sa mention, parce qu'un texte à l'écran qu'on croit en vigueur est le seul vrai
 * danger d'un éditeur de prompt système.
 *
 * L'onglet est monté par `ContenuOngletAgent` : c'est le point d'entrée que la
 * fiche utilise. Le réseau est débranché ici même (mock local de `@/lib/api`).
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ContenuOngletAgent } from "@/components/ContenuOngletAgent";
import {
  PLAYBOOK_SOURCE_DEFAUT,
  type LexiquePlaybook,
  type PlaybookDetail,
  type PropositionPlaybook,
  type RedactionPlaybook,
  type VersionPlaybook,
} from "@/lib/types";

import { rendreAvecEtat } from "./aides";

/** L'état du playbook servi — posé par chaque test avant de monter. */
let fiche: PlaybookDetail;
let versions: VersionPlaybook[];
let propositions: PropositionPlaybook[];
let lexique: LexiquePlaybook | null;
/** Le contenu d'une entrée d'historique, indexé par sa référence (`v2`, `p3`). */
let contenus: Record<string, string>;
/** La réécriture que l'assistant rendra, ou l'échec posé ici. */
let redaction: RedactionPlaybook | Error;

/** Les écritures faites, dans l'ordre : c'est là qu'on lit ce qui a été publié. */
const gestes: { verbe: string; argument: unknown }[] = [];

vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  const aides = await import("./aides");
  const noter = (verbe: string) => (_agent: string, argument?: unknown) => {
    gestes.push({ verbe, argument });
    return Promise.resolve();
  };
  return {
    ...reel,
    chargerProjets: () => Promise.resolve(aides.projetsDeclares()),
    chargerJournal: () => Promise.resolve(aides.pageJournalCourante()),
    chargerFournisseurs: () => Promise.resolve(aides.fournisseursDuPoste()),
    chargerCatalogue: () => Promise.resolve(aides.catalogueAgents()),
    chargerPlaybook: () => Promise.resolve(fiche),
    chargerVersionsPlaybook: () => Promise.resolve(versions),
    chargerPropositionsPlaybook: () => Promise.resolve(propositions),
    chargerLexiquePlaybook: () =>
      lexique === null
        ? Promise.reject(new Error("lexique indisponible"))
        : Promise.resolve(lexique),
    chargerVersionPlaybook: (_agent: string, numero: number) =>
      Promise.resolve({
        agent: "dev",
        version: numero,
        cree_le: "2026-08-20T10:00:00Z",
        contenu: contenus[`v${numero}`] ?? "",
      }),
    chargerPropositionPlaybook: (_agent: string, numero: number) =>
      Promise.resolve({
        agent: "dev",
        version: numero,
        cree_le: "2026-08-21T10:00:00Z",
        provenance: "run-42",
        contenu: contenus[`p${numero}`] ?? "",
      }),
    ecrirePlaybook: noter("publier"),
    restaurerPlaybook: noter("restaurer"),
    appliquerPropositionPlaybook: noter("appliquer"),
    rejeterPropositionPlaybook: noter("rejeter"),
    redigerPlaybook: () =>
      redaction instanceof Error
        ? Promise.reject(redaction)
        : Promise.resolve(redaction),
  };
});

/** Pose la fiche du playbook servie, sur les défauts d'un agent jamais édité. */
function poserPlaybook(partiel: Partial<PlaybookDetail> = {}) {
  fiche = {
    agent: "dev",
    role: "Développeur",
    version: 0,
    nb_versions: 0,
    source: PLAYBOOK_SOURCE_DEFAUT,
    cree_le: null,
    contenu: "## Mission\nTu écris du code.",
    ...partiel,
  };
}

/** Monte l'onglet Playbook de `dev` et attend la fin du chargement. */
async function monter() {
  const utilisateur = userEvent.setup();
  const vue = rendreAvecEtat(<ContenuOngletAgent nom="dev" onglet="playbook" />);
  await screen.findByRole("region", { name: "Playbook de dev" });
  return { utilisateur, ...vue };
}

/** La section du playbook. */
function section() {
  return screen.getByRole("region", { name: "Playbook de dev" });
}

/** La zone d'édition du brouillon. */
function zone() {
  return screen.getByRole("combobox", { name: "Contenu du playbook" });
}

/** Le sélecteur d'historique — absent quand il n'y a rien à consulter. */
function historique() {
  return screen.getByLabelText(/Historique/);
}

beforeEach(() => {
  gestes.length = 0;
  contenus = {};
  versions = [];
  propositions = [];
  lexique = { structures: [], tournures: [] };
  redaction = { contenu: "", justification: "" };
  poserPlaybook();
});

describe("① la version en vigueur se lit sans rien ouvrir", () => {
  it("compte le playbook d'origine comme une version, pas comme un trou", async () => {
    await monter();

    // « d'origine » et non « du code » : l'onglet sert aussi les agents
    // personnalisés, dont l'origine est le playbook de leur définition.
    expect(within(section()).getByText(/en vigueur : v0/)).toHaveTextContent(
      "playbook d’origine",
    );
    expect(
      within(section()).getByText("Aucune version antérieure"),
    ).toBeInTheDocument();
  });

  it("annonce le numéro que la publication va créer", async () => {
    poserPlaybook({ version: 3, source: "stockage" });

    await monter();

    expect(within(section()).getByText(/en vigueur : v3/)).toBeInTheDocument();
    // Le numéro que le clic va créer, dans tous les états — un « Publier » nu
    // laisserait deviner ce qu'on s'apprête à faire.
    expect(
      screen.getByRole("button", { name: "Publier la version 4" }),
    ).toBeInTheDocument();
  });

  it("compte les propositions en attente d'une décision", async () => {
    propositions = [
      { agent: "dev", version: 1, cree_le: "2026-08-21T10:00:00Z", provenance: "run-42" },
      { agent: "dev", version: 2, cree_le: "2026-08-22T10:00:00Z", provenance: "run-43" },
    ];

    await monter();

    expect(within(section()).getByText("2 en attente")).toBeInTheDocument();
  });

  it("dit qu'un brouillon n'est pas publié, et sait revenir en arrière", async () => {
    const { utilisateur } = await monter();

    await utilisateur.type(zone(), " Encore une ligne.");

    // Le seul vrai danger d'un éditeur de prompt système : un texte à l'écran
    // qu'on croit en vigueur.
    expect(
      within(section()).getByText(/Modifications non publiées/),
    ).toBeInTheDocument();

    await utilisateur.click(
      screen.getByRole("button", { name: "Annuler les modifications" }),
    );

    expect(within(section()).queryByText(/Modifications non publiées/)).toBeNull();
    expect(gestes).toHaveLength(0);
  });

  it("ne publie que ce qui a changé et n'est pas vide", async () => {
    const { utilisateur } = await monter();
    const bouton = screen.getByRole("button", { name: "Publier la version 1" });
    expect(bouton).toBeDisabled();

    await utilisateur.clear(zone());
    expect(bouton).toBeDisabled(); // modifié, mais vide

    await utilisateur.type(zone(), "## Mission\nTu écris des tests.");
    await utilisateur.click(bouton);

    await waitFor(() =>
      expect(gestes).toEqual([
        { verbe: "publier", argument: "## Mission\nTu écris des tests." },
      ]),
    );
  });
});

describe("① l'historique s'ouvre sans manger l'écran", () => {
  beforeEach(() => {
    poserPlaybook({ version: 3, source: "stockage", cree_le: "2026-08-22T10:00:00Z" });
    versions = [
      { agent: "dev", version: 2, cree_le: "2026-08-20T10:00:00Z" },
      { agent: "dev", version: 3, cree_le: "2026-08-22T10:00:00Z" },
    ];
    propositions = [
      { agent: "dev", version: 1, cree_le: "2026-08-21T10:00:00Z", provenance: "run-42" },
    ];
    contenus = { v2: "Le texte de la v2.", p1: "Le texte proposé." };
  });

  it("n'offre pas la version courante dans l'historique consultable", async () => {
    await monter();

    const valeurs = within(historique())
      .getAllByRole("option")
      .map((option) => option.getAttribute("value"));
    // Son contenu *est* celui de l'éditeur : l'offrir deux fois ferait chercher
    // la différence entre les deux.
    expect(valeurs).toEqual(["courante", "p:1", "v:2"]);
    expect(valeurs.filter((valeur) => valeur === "v:3")).toEqual([]);
  });

  it("range les propositions avant les versions — elles attendent une décision", async () => {
    await monter();

    const groupes = within(historique())
      .getAllByRole("group")
      .map((groupe) => groupe.getAttribute("label"));
    expect(groupes).toEqual(["Propositions en attente", "Versions publiées"]);
  });

  it("ouvre une version passée en lecture seule, et dit ce que restaurer fait", async () => {
    const { utilisateur } = await monter();

    await utilisateur.selectOptions(historique(), "v:2");

    const apercu = await screen.findByRole("textbox", {
      name: "Contenu de version 2",
    });
    expect(apercu).toHaveValue("Le texte de la v2.");
    expect(apercu).toHaveAttribute("readonly");
    // Rien n'est écrasé : la version en vigueur reste dans l'historique.
    expect(
      within(section()).getByText(/Restaurer republie ce texte comme version 4/),
    ).toHaveTextContent("la version 3 reste dans l’historique");

    await utilisateur.click(
      screen.getByRole("button", { name: "Restaurer la version 2" }),
    );

    await waitFor(() =>
      expect(gestes).toEqual([{ verbe: "restaurer", argument: 2 }]),
    );
  });

  it("offre d'appliquer ou de rejeter une proposition, sans confondre les deux", async () => {
    const { utilisateur } = await monter();

    await utilisateur.selectOptions(historique(), "p:1");

    await screen.findByRole("textbox", { name: "Contenu de proposition 1" });
    expect(
      within(section()).getByText(/Rejeter l’écarte sans toucher à la version en vigueur/),
    ).toBeInTheDocument();

    await utilisateur.click(screen.getByRole("button", { name: "Rejeter" }));

    await waitFor(() =>
      expect(gestes).toEqual([{ verbe: "rejeter", argument: 1 }]),
    );
  });

  it("revient à l'édition sans rien avoir touché au brouillon", async () => {
    const { utilisateur } = await monter();
    await utilisateur.type(zone(), " Un ajout.");

    await utilisateur.selectOptions(historique(), "v:2");
    await screen.findByRole("textbox", { name: "Contenu de version 2" });
    await utilisateur.selectOptions(historique(), "courante");

    expect(zone()).toHaveValue("## Mission\nTu écris du code. Un ajout.");
    expect(gestes).toHaveLength(0);
  });
});

describe("② les complétions viennent du lexique du dépôt", () => {
  beforeEach(() => {
    lexique = {
      structures: [
        { texte: "## Mission", roles: 5 },
        { texte: "## Méthode", roles: 4 },
      ],
      tournures: [{ texte: "Tu ne modifies jamais un fichier hors du périmètre.", roles: 3 }],
    };
    poserPlaybook({ contenu: "" });
  });

  it("propose ce que les playbooks livrés ont en commun, et dit d'où ça vient", async () => {
    const { utilisateur } = await monter();

    await utilisateur.type(zone(), "## M");

    const liste = await screen.findByRole("listbox", {
      name: "Complétions proposées",
    });
    const options = within(liste).getAllByRole("option");
    // Triées par nombre de playbooks : une suggestion dont on voit la provenance
    // se refuse en connaissance de cause.
    expect(options[0]).toHaveTextContent("## Mission");
    expect(options[0]).toHaveTextContent("structure · 5 playbooks");
    expect(options[1]).toHaveTextContent("## Méthode");
    expect(screen.getByRole("status")).toHaveTextContent(
      "2 propositions — Tab pour accepter, Échap pour ignorer.",
    );
  });

  it("accepte la proposition retenue sur Tab", async () => {
    const { utilisateur } = await monter();
    await utilisateur.type(zone(), "## M");
    await screen.findByRole("listbox", { name: "Complétions proposées" });

    await utilisateur.keyboard("{Tab}");

    expect(zone()).toHaveValue("## Mission");
    expect(
      screen.queryByRole("listbox", { name: "Complétions proposées" }),
    ).toBeNull();
  });

  it("se tait sur Échap, sans rien effacer de la frappe", async () => {
    const { utilisateur } = await monter();
    await utilisateur.type(zone(), "## M");
    await screen.findByRole("listbox", { name: "Complétions proposées" });

    await utilisateur.keyboard("{Escape}");

    expect(
      screen.queryByRole("listbox", { name: "Complétions proposées" }),
    ).toBeNull();
    expect(zone()).toHaveValue("## M");
  });

  it("laisse l'éditeur entier quand le lexique est indisponible", async () => {
    lexique = null;
    const { utilisateur } = await monter();

    await utilisateur.type(zone(), "## M");

    // Un lexique absent n'est pas une panne de l'éditeur : les complétions se
    // taisent, tout le reste fonctionne.
    expect(
      screen.queryByRole("listbox", { name: "Complétions proposées" }),
    ).toBeNull();
    expect(
      screen.getByRole("button", { name: "Publier la version 1" }),
    ).toBeEnabled();
  });
});

describe("② l'assistant propose, il ne publie pas", () => {
  beforeEach(() => {
    poserPlaybook({ contenu: "## Mission\nTu écris du code." });
    redaction = {
      contenu: "## Mission\nTu écris du code testé.",
      justification: "Ajout de la contrainte de test.",
    };
  });

  it("s'ouvre et se ferme à la demande", async () => {
    const { utilisateur } = await monter();
    const bouton = screen.getByRole("button", { name: "Assistant" });
    expect(bouton).toHaveAttribute("aria-expanded", "false");

    await utilisateur.click(bouton);
    const panneau = screen.getByRole("group", { name: "Assistant de rédaction" });
    expect(panneau).toBeInTheDocument();

    await utilisateur.click(within(panneau).getByRole("button", { name: "Fermer" }));

    expect(
      screen.queryByRole("group", { name: "Assistant de rédaction" }),
    ).toBeNull();
  });

  it("refuse de partir d'un brouillon vide, en le disant", async () => {
    poserPlaybook({ contenu: "" });
    const { utilisateur } = await monter();

    await utilisateur.click(screen.getByRole("button", { name: "Assistant" }));

    const panneau = screen.getByRole("group", { name: "Assistant de rédaction" });
    expect(
      within(panneau).getByRole("button", { name: /Proposer une réécriture/ }),
    ).toBeDisabled();
    expect(
      within(panneau).getByText(/l'assistant part de ce que vous avez déjà/),
    ).toBeInTheDocument();
  });

  it("rend un différentiel dont rien n'est encore appliqué", async () => {
    const { utilisateur } = await monter();
    await utilisateur.click(screen.getByRole("button", { name: "Assistant" }));

    await utilisateur.click(
      screen.getByRole("button", { name: /Proposer une réécriture/ }),
    );

    const differentiel = await screen.findByRole("region", {
      name: "Différentiel de la réécriture proposée",
    });
    expect(differentiel).toHaveTextContent("Tu écris du code testé.");
    expect(screen.getByText("Ajout de la contrainte de test.")).toBeInTheDocument();
    expect(screen.getByText("+1 / −1 lignes")).toBeInTheDocument();
    // La zone d'édition n'a pas bougé : la proposition est un candidat en vol.
    expect(zone()).toHaveValue("## Mission\nTu écris du code.");
    expect(gestes).toHaveLength(0);
  });

  it("appliquer envoie le texte au brouillon, et ne publie rien", async () => {
    const { utilisateur } = await monter();
    await utilisateur.click(screen.getByRole("button", { name: "Assistant" }));
    await utilisateur.click(
      screen.getByRole("button", { name: /Proposer une réécriture/ }),
    );
    await screen.findByRole("region", {
      name: "Différentiel de la réécriture proposée",
    });

    await utilisateur.click(
      screen.getByRole("button", { name: "Appliquer au brouillon" }),
    );

    // La frontière du lot : le texte part dans la zone d'édition, la publication
    // reste un geste à part.
    expect(zone()).toHaveValue("## Mission\nTu écris du code testé.");
    expect(gestes).toHaveLength(0);
    expect(
      within(section()).getByText(/Modifications non publiées/),
    ).toBeInTheDocument();
  });

  it("garde le brouillon intact quand la rédaction échoue", async () => {
    redaction = new Error("modèle injoignable");
    const { utilisateur } = await monter();
    await utilisateur.click(screen.getByRole("button", { name: "Assistant" }));

    await utilisateur.click(
      screen.getByRole("button", { name: /Proposer une réécriture/ }),
    );

    const alerte = await screen.findByRole("alert");
    expect(alerte).toHaveTextContent("modèle injoignable");
    expect(alerte).toHaveTextContent("votre brouillon est intact");
    expect(zone()).toHaveValue("## Mission\nTu écris du code.");
  });
});

describe("l'onglet, avant le playbook", () => {
  it("dit qu'il charge, puis pourquoi le playbook est illisible", async () => {
    const echec = vi
      .spyOn(await import("@/lib/api"), "chargerPlaybook")
      .mockRejectedValue(new Error("agent inconnu"));

    rendreAvecEtat(<ContenuOngletAgent nom="dev" onglet="playbook" />);

    expect(screen.getByText("Chargement du playbook…")).toBeInTheDocument();
    const alerte = await screen.findByRole("alert");
    expect(alerte).toHaveTextContent("Playbook illisible : agent inconnu");
    echec.mockRestore();
  });
});

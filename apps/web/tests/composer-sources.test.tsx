/**
 * Le reste de l'écran « composer un objectif » — la couverture différée (#323).
 *
 * `composer.test.tsx` (#319) garde ce qu'un écran ne peut pas rattraper : ce
 * qu'on compose, ce que l'aperçu montre, la façon dont un refus se pose. Ce
 * fichier-ci prend ce que le lot avait laissé au lot final, et qui se répartit
 * en trois familles :
 *
 * - le **rapport de lecture** rendu seul (`RapportExtraction`) — le cas sans
 *   source, les pluriels du résumé, les fichiers d'un dossier sous leur dossier.
 *   Rendu isolément parce que c'est le composant, et non l'écran, qui décide de
 *   ce qui se voit d'une extraction ;
 * - les **verrous du geste** — un aperçu sans source et un lancement sans
 *   objectif ne se proposent pas, et rien n'est cliquable pendant un appel ;
 * - le **vocabulaire** (`lib/sources`) — les trois types, les trois états, les
 *   motifs de lecture et les deux déclarations que `composer.test.tsx`
 *   n'exerçait pas (dossier, adresse).
 *
 * Ce que ce fichier ne couvre pas, et pourquoi : l'écran **valider le brief**
 * (#322) a sa propre suite (`apps/web/tests/brief.test.tsx`), livrée avec son
 * lot ; le **bout en bout** dans un vrai navigateur reste le rôle de `/verify`
 * et la **mise en page** celle de `/banc-mise-en-page` (`apps/web/README.md`).
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ComposerObjectif } from "@/components/composer/ComposerObjectif";
import { RapportExtraction } from "@/components/composer/RapportExtraction";
import type { LectureSource, RapportLecture } from "@/lib/types";

import { pageExplorateurFactice, projetFactice, rendreAvecEtat } from "./aides";

const apercuSources = vi.fn();
const televerserSources = vi.fn();
const lancerExecution = vi.fn();
const chargerExplorateur = vi.fn();
const chargerDisponibiliteSelecteur = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  // Mock **partiel** : `ErreurSource` doit rester la vraie classe, sinon les
  // `instanceof` de l'écran ne reconnaîtraient plus les refus (cf. #319).
  const reel = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...reel,
    apercuSources: (...args: unknown[]) => apercuSources(...args),
    televerserSources: (...args: unknown[]) => televerserSources(...args),
    lancerExecution: (...args: unknown[]) => lancerExecution(...args),
    chargerExplorateur: (...args: unknown[]) => chargerExplorateur(...args),
    chargerDisponibiliteSelecteur: () => chargerDisponibiliteSelecteur(),
  };
});

function lecture(partiel: Partial<LectureSource> = {}): LectureSource {
  return {
    nom: "cdc.md",
    type: "fichier",
    etat: "lu",
    tokens: 4200,
    motif: "",
    message: "",
    limite: "",
    entrees: [],
    ...partiel,
  };
}

function rapport(lectures: LectureSource[]): RapportLecture {
  return {
    tokens: lectures.reduce((total, une) => total + une.tokens, 0),
    lectures,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  apercuSources.mockResolvedValue(rapport([]));
  televerserSources.mockResolvedValue({ sources: [], total_octets: 0 });
  lancerExecution.mockResolvedValue({
    run_id: "9f2c1ab34de5",
    objectif: "Refondre l'écran de lancement",
    statut: "en_cours",
    nb_taches: 0,
    cout_usd: null,
    ticket: null,
    projet_id: projetFactice().id,
    debut: "2026-08-12T09:00:00+00:00",
    fin: null,
  });
  chargerExplorateur.mockResolvedValue(pageExplorateurFactice());
  chargerDisponibiliteSelecteur.mockResolvedValue({
    disponible: false,
    motif: "selecteur-desactive",
    message: "Sélecteur natif éteint.",
    outil: "",
  });
});

async function ecran() {
  rendreAvecEtat(<ComposerObjectif />);
  return await screen.findByRole("region", { name: "Composer un objectif" });
}

function fichier(nom = "cdc.md", contenu = "# Cahier des charges") {
  return new File([contenu], nom, { type: "text/markdown" });
}

// --- Le rapport de lecture, rendu seul ------------------------------------

describe("le rapport de lecture (#316 à l'écran)", () => {
  it("dit qu'un run sans source partira sur l'objectif seul", () => {
    rendreAvecEtat(<RapportExtraction rapport={rapport([])} />);
    expect(
      screen.getByText(/Aucune source déclarée/),
    ).toBeInTheDocument();
    // Le coût reste annoncé : « zéro » est une information, pas une absence.
    expect(screen.getByText(/0 tokens/)).toBeInTheDocument();
  });

  it("résume au singulier ce qui n'est arrivé qu'une fois", () => {
    rendreAvecEtat(
      <RapportExtraction
        rapport={rapport([
          lecture({ nom: "gros.md", etat: "tronque", limite: "plafond par source" }),
          lecture({ nom: "logo.png", etat: "ignore", motif: "format-non-gere", tokens: 0 }),
        ])}
      />,
    );
    expect(screen.getByText(/1 source tronquée —/)).toBeInTheDocument();
    expect(screen.getByText(/1 source ignorée — elle n'entrera pas/)).toBeInTheDocument();
  });

  it("résume au pluriel ce qui est arrivé plusieurs fois", () => {
    rendreAvecEtat(
      <RapportExtraction
        rapport={rapport([
          lecture({ nom: "a.md", etat: "tronque", limite: "budget total" }),
          lecture({ nom: "b.md", etat: "tronque", limite: "budget total" }),
          lecture({ nom: "c.png", etat: "ignore", motif: "format-non-gere", tokens: 0 }),
          lecture({ nom: "d.png", etat: "ignore", motif: "format-non-gere", tokens: 0 }),
        ])}
      />,
    );
    expect(screen.getByText(/2 sources tronquées —/)).toBeInTheDocument();
    expect(screen.getByText(/2 sources ignorées — elles n'entreront pas/)).toBeInTheDocument();
  });

  it("ne résume rien quand tout a été lu — le détail suffit", () => {
    rendreAvecEtat(<RapportExtraction rapport={rapport([lecture(), lecture({ nom: "b.md" })])} />);
    expect(screen.queryByText(/tronquée/)).toBeNull();
    expect(screen.queryByText(/ignorée/)).toBeNull();
  });

  it("range les fichiers d'un dossier sous leur dossier, avec leur motif", () => {
    rendreAvecEtat(
      <RapportExtraction
        rapport={rapport([
          lecture({
            nom: "references",
            type: "dossier",
            tokens: 900,
            entrees: [
              lecture({ nom: "specification.md", tokens: 900, entrees: [] }),
              lecture({
                nom: "logo.png",
                etat: "ignore",
                motif: "format-non-gere",
                tokens: 0,
                entrees: [],
              }),
            ],
          }),
        ])}
      />,
    );
    // La ligne du dossier porte ses entrées : les renvoyer à la fin ferait
    // perdre à quelle source elles appartiennent.
    const ligneDossier = screen.getByText("references").closest("li");
    expect(ligneDossier).not.toBeNull();
    expect(within(ligneDossier!).getByText("specification.md")).toBeInTheDocument();
    expect(within(ligneDossier!).getByText("logo.png")).toBeInTheDocument();
    expect(within(ligneDossier!).getAllByText("format-non-gere").length).toBeGreaterThan(0);
  });

  it("dit la limite atteinte d'une source tronquée, et le motif d'une ignorée", () => {
    rendreAvecEtat(
      <RapportExtraction
        rapport={rapport([
          lecture({ nom: "gros.pdf", etat: "tronque", limite: "4 Mio lus" }),
          lecture({
            nom: "page",
            type: "url",
            etat: "ignore",
            motif: "url-injoignable",
            message: "Le serveur n'a pas répondu.",
            tokens: 0,
          }),
        ])}
      />,
    );
    expect(screen.getByText(/Limite atteinte : 4 Mio lus/)).toBeInTheDocument();
    // Le motif connu est traduit, et le code brut reste affiché à côté : l'un
    // se lit, l'autre se cherche dans la doc.
    expect(screen.getByText(/La page n'a pas répondu dans le délai/)).toBeInTheDocument();
    expect(screen.getByText("url-injoignable")).toBeInTheDocument();
  });

  it("retombe sur le message du backend quand le motif lui est inconnu", () => {
    rendreAvecEtat(
      <RapportExtraction
        rapport={rapport([
          lecture({
            nom: "exotique.xyz",
            etat: "ignore",
            motif: "motif-que-l-ecran-ignore",
            message: "Le backend explique ce qui s'est passé.",
            tokens: 0,
          }),
        ])}
      />,
    );
    expect(
      screen.getByText(/Le backend explique ce qui s'est passé./),
    ).toBeInTheDocument();
  });
});

// --- Les verrous du geste --------------------------------------------------

describe("ce que l'écran refuse de proposer (#319)", () => {
  it("n'offre pas d'aperçu tant qu'aucune source n'est déclarée", async () => {
    await ecran();
    expect(screen.getByRole("button", { name: "Voir ce qui sera lu" })).toBeDisabled();
  });

  it("n'offre pas de lancement tant que l'objectif est vide, et le dit", async () => {
    const utilisateur = userEvent.setup();
    await ecran();
    const lancer = screen.getByRole("button", { name: "Lancer l'orchestration" });
    expect(lancer).toBeDisabled();
    expect(screen.getByText(/Un objectif est nécessaire/)).toBeInTheDocument();

    await utilisateur.type(screen.getByLabelText("Objectif"), "Refondre l'écran");
    expect(lancer).toBeEnabled();
  });

  it("n'ajoute pas une adresse vide", async () => {
    await ecran();
    expect(screen.getByRole("button", { name: "Ajouter l'adresse" })).toBeDisabled();
  });

  it("gèle la saisie le temps d'un appel, puis la rend", async () => {
    const utilisateur = userEvent.setup();
    let repondre: (rapport: RapportLecture) => void = () => {};
    apercuSources.mockReturnValue(
      new Promise<RapportLecture>((resoudre) => {
        repondre = resoudre;
      }),
    );
    await ecran();
    await utilisateur.upload(screen.getByLabelText("Fichiers à joindre"), fichier());
    await utilisateur.click(screen.getByRole("button", { name: "Voir ce qui sera lu" }));

    // Pendant l'appel : le bouton dit ce qu'il fait, et l'objectif ne se saisit pas.
    expect(
      await screen.findByRole("button", { name: "Lecture des sources…" }),
    ).toBeDisabled();
    expect(screen.getByLabelText("Objectif")).toBeDisabled();

    repondre(rapport([lecture()]));
    await waitFor(() => expect(screen.getByLabelText("Objectif")).toBeEnabled());
  });
});

// --- Le lancement : les deux autres types de source ------------------------

describe("le lancement des sources qui n'ont rien à téléverser (#319)", () => {
  it("déclare un dossier par son chemin et une adresse par sa valeur", async () => {
    const utilisateur = userEvent.setup();
    await ecran();

    await utilisateur.click(
      screen.getByRole("button", { name: "Choisir un dossier de références…" }),
    );
    const explorateur = await screen.findByRole("region", {
      name: "Explorateur de dossiers",
    });
    await utilisateur.click(
      within(explorateur).getByRole("button", { name: "Choisir projets" }),
    );

    await utilisateur.type(
      screen.getByLabelText("Adresse à lire"),
      "https://exemple.test/spec",
    );
    await utilisateur.click(screen.getByRole("button", { name: "Ajouter l'adresse" }));

    await utilisateur.type(screen.getByLabelText("Objectif"), "Reprendre le cadrage");
    await utilisateur.click(screen.getByRole("button", { name: "Lancer l'orchestration" }));

    await waitFor(() => expect(lancerExecution).toHaveBeenCalled());
    const envoye = lancerExecution.mock.calls[0][0] as { sources: unknown[] };
    // Aucun octet à porter : ni l'un ni l'autre ne passe par le téléversement.
    expect(televerserSources).not.toHaveBeenCalled();
    expect(envoye.sources).toEqual([
      expect.objectContaining({ type: "dossier" }),
      { type: "url", valeur: "https://exemple.test/spec", nom: "https://exemple.test/spec" },
    ]);
  });

  it("traite un téléversement en échec comme un refus, sans lancer", async () => {
    const utilisateur = userEvent.setup();
    televerserSources.mockRejectedValue(new TypeError("Failed to fetch"));
    await ecran();

    await utilisateur.upload(screen.getByLabelText("Fichiers à joindre"), fichier());
    await utilisateur.type(screen.getByLabelText("Objectif"), "Reprendre le cadrage");
    await utilisateur.click(screen.getByRole("button", { name: "Lancer l'orchestration" }));

    expect(await screen.findByText(/Lancement refusé/)).toBeInTheDocument();
    expect(lancerExecution).not.toHaveBeenCalled();
    // La saisie survit au refus : c'est elle qu'on vient corriger.
    expect(screen.getByLabelText("Objectif")).toHaveValue("Reprendre le cadrage");
  });
});

// --- Le vocabulaire (lib/sources) -----------------------------------------

describe("le vocabulaire des sources (lib/sources)", () => {
  it("nomme les trois types et les trois états, et laisse passer l'inconnu", async () => {
    const { libelleEtat, libelleType } = await import("@/lib/sources");
    expect([libelleType("fichier"), libelleType("dossier"), libelleType("url")]).toEqual([
      "Fichier",
      "Dossier",
      "Adresse",
    ]);
    expect([libelleEtat("lu"), libelleEtat("tronque"), libelleEtat("ignore")]).toEqual([
      "Lu",
      "Tronqué",
      "Ignoré",
    ]);
    // Un code que l'API gagnera s'affiche tel quel : l'écran reste juste,
    // seulement moins lisible — jamais vide.
    expect(libelleType("presse-papier")).toBe("presse-papier");
    expect(libelleEtat("perdu")).toBe("perdu");
  });

  it("traduit un motif de lecture connu, et rend null sinon", async () => {
    const { libelleMotifLecture } = await import("@/lib/sources");
    expect(libelleMotifLecture("convertisseur-absent")).toMatch(/Convertisseur absent/);
    expect(libelleMotifLecture("budget-epuise")).toMatch(/Budget de lecture épuisé/);
    expect(libelleMotifLecture("motif-inconnu")).toBeNull();
  });

  it("déclare un dossier par son chemin et une adresse par sa valeur", async () => {
    const { declarationDe } = await import("@/lib/sources");
    expect(
      declarationDe({
        cle: "1",
        type: "dossier",
        nom: "maquettes",
        valeur: "D:/refs/maquettes",
        id: null,
        fichier: null,
      }),
    ).toEqual({ type: "dossier", chemin: "D:/refs/maquettes", nom: "maquettes" });
    expect(
      declarationDe({
        cle: "2",
        type: "url",
        nom: "https://a.test/spec",
        valeur: "https://a.test/spec",
        id: null,
        fichier: null,
      }),
    ).toEqual({ type: "url", valeur: "https://a.test/spec", nom: "https://a.test/spec" });
  });

  it("apparie une lecture à sa source par le rang, et rend null hors liste", async () => {
    const { lecturePour } = await import("@/lib/sources");
    const lectures = [lecture({ nom: "a.md" }), lecture({ nom: "b.md" })];
    expect(lecturePour(lectures, 1)?.nom).toBe("b.md");
    expect(lecturePour(lectures, 2)).toBeNull();
  });

  it("arrondit une taille selon son ordre de grandeur", async () => {
    const { formaterOctets, formaterTokens } = await import("@/lib/sources");
    expect(formaterOctets(512)).toBe("512 o");
    expect(formaterOctets(2048)).toBe("2.0 Kio");
    // Au-delà de 10 Kio, la décimale n'apprend plus rien.
    expect(formaterOctets(40 * 1024)).toBe("40 Kio");
    expect(formaterOctets(10 * 1024 * 1024)).toBe("10.00 Mio");
    expect(formaterTokens(24200)).toMatch(/24\s?200/);
  });
});

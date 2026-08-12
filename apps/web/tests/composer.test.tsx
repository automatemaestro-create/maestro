/**
 * L'écran « composer un objectif » (#319, docs/05 §2.7.3).
 *
 * Trois critères, et ce sont eux qui structurent le fichier : ce que l'écran
 * **compose** (texte, fichiers, dossier venu de l'explorateur, adresses, projet
 * actif), ce qu'il **montre avant de lancer** (le rapport de lecture, gratuit et
 * réversible) et la façon dont il **refuse** — à l'endroit du geste refusé, sans
 * perdre la saisie, et sans confondre « rien à lire ici » avec « je refuse de
 * lire ça ».
 *
 * Le reste de la couverture de la Phase 8 est différé au lot final #323
 * (checklist de #314).
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ComposerObjectif } from "@/components/composer/ComposerObjectif";
import { ErreurSource } from "@/lib/api";
import type { RapportLecture } from "@/lib/types";

import { pageExplorateurFactice, projetFactice, rendreAvecEtat } from "./aides";

const apercuSources = vi.fn();
const televerserSources = vi.fn();
const lancerExecution = vi.fn();
const chargerExplorateur = vi.fn();
const chargerDisponibiliteSelecteur = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  // `importOriginal` : `ErreurSource` doit rester **la** classe, sinon les
  // `instanceof` de l'écran ne reconnaîtraient plus les refus qu'on lui envoie.
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

const RAPPORT_VIDE: RapportLecture = { tokens: 0, lectures: [] };

function lecture(partiel: Partial<RapportLecture["lectures"][number]> = {}) {
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

beforeEach(() => {
  vi.clearAllMocks();
  apercuSources.mockResolvedValue(RAPPORT_VIDE);
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

// --- Critère 1 : composer -------------------------------------------------

describe("composer un objectif (#319)", () => {
  it("annonce le projet actif comme cadre du run, sans le demander", async () => {
    await ecran();
    expect(screen.getByLabelText("Objectif")).toBeInTheDocument();
    expect(screen.getByText(/Dépensio/)).toBeInTheDocument();
  });

  it("joint un fichier déposé, avec son nom et sa taille", async () => {
    const utilisateur = userEvent.setup();
    await ecran();

    await utilisateur.upload(screen.getByLabelText("Fichiers à joindre"), fichier());

    const liste = await screen.findByRole("list", { name: "Sources déclarées" });
    expect(within(liste).getByText("cdc.md")).toBeInTheDocument();
    expect(within(liste).getByText("Fichier")).toBeInTheDocument();
  });

  it("joint une adresse, et seulement des http(s) — l'API tranche", async () => {
    const utilisateur = userEvent.setup();
    await ecran();

    await utilisateur.type(
      screen.getByLabelText("Adresse à lire"),
      "https://exemple.test/spec",
    );
    await utilisateur.click(screen.getByRole("button", { name: "Ajouter l'adresse" }));

    const liste = await screen.findByRole("list", { name: "Sources déclarées" });
    expect(within(liste).getByText("https://exemple.test/spec")).toBeInTheDocument();
    // Le champ se vide : l'adresse est **déclarée**, elle n'attend plus.
    expect(screen.getByLabelText("Adresse à lire")).toHaveValue("");
  });

  it("prend le dossier de références dans l'explorateur, jamais dans une saisie", async () => {
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

    const liste = await screen.findByRole("list", { name: "Sources déclarées" });
    expect(within(liste).getByText("projets")).toBeInTheDocument();
    expect(within(liste).getByText("D:/projets")).toBeInTheDocument();
    // Le chemin vient de l'API : c'est elle qui a énuméré ce dossier.
    expect(chargerExplorateur).toHaveBeenCalled();
  });

  it("retire une source déclarée", async () => {
    const utilisateur = userEvent.setup();
    await ecran();
    await utilisateur.upload(screen.getByLabelText("Fichiers à joindre"), fichier());

    await utilisateur.click(await screen.findByRole("button", { name: "Retirer cdc.md" }));

    expect(screen.queryByRole("list", { name: "Sources déclarées" })).toBeNull();
  });
});

// --- Critère 2 : voir avant de lancer -------------------------------------

describe("l'aperçu de l'extraction (#319, critère 2)", () => {
  it("montre par source ce qui est lu, tronqué ou ignoré, et le coût estimé", async () => {
    const utilisateur = userEvent.setup();
    apercuSources.mockResolvedValue({
      tokens: 24_200,
      lectures: [
        lecture(),
        lecture({
          nom: "specification",
          type: "dossier",
          etat: "tronque",
          tokens: 20_000,
          limite: "20000 tokens (plafond par source)",
        }),
        lecture({
          nom: "maquette.png",
          etat: "ignore",
          tokens: 0,
          motif: "format-non-gere",
          message: "Format non géré.",
        }),
      ],
    });
    await ecran();
    await utilisateur.upload(screen.getByLabelText("Fichiers à joindre"), fichier());

    await utilisateur.click(screen.getByRole("button", { name: "Voir ce qui sera lu" }));

    const apercu = await screen.findByRole("region", { name: "Aperçu de l'extraction" });
    expect(within(apercu).getByText(/24 200 tokens/)).toBeInTheDocument();
    expect(within(apercu).getByText("Lu")).toBeInTheDocument();
    expect(within(apercu).getByText("Tronqué")).toBeInTheDocument();
    expect(within(apercu).getByText("Ignoré")).toBeInTheDocument();
    expect(within(apercu).getByText(/plafond par source/)).toBeInTheDocument();
    expect(within(apercu).getByText("format-non-gere")).toBeInTheDocument();
  });

  it("ne lance rien : l'aperçu est gratuit et sans effet", async () => {
    const utilisateur = userEvent.setup();
    await ecran();
    await utilisateur.upload(screen.getByLabelText("Fichiers à joindre"), fichier());

    await utilisateur.click(screen.getByRole("button", { name: "Voir ce qui sera lu" }));

    await screen.findByRole("region", { name: "Aperçu de l'extraction" });
    expect(lancerExecution).not.toHaveBeenCalled();
    expect(televerserSources).not.toHaveBeenCalled();
  });

  it("périme le rapport dès qu'une source change", async () => {
    const utilisateur = userEvent.setup();
    apercuSources.mockResolvedValue({ tokens: 4200, lectures: [lecture()] });
    await ecran();
    await utilisateur.upload(screen.getByLabelText("Fichiers à joindre"), fichier());
    await utilisateur.click(screen.getByRole("button", { name: "Voir ce qui sera lu" }));
    await screen.findByRole("region", { name: "Aperçu de l'extraction" });

    await utilisateur.click(screen.getByRole("button", { name: "Retirer cdc.md" }));

    // Un rapport qui survivrait au retrait décrirait un état que plus rien ne
    // produit — pire que pas d'aperçu du tout.
    expect(screen.queryByRole("region", { name: "Aperçu de l'extraction" })).toBeNull();
  });

  it("une source ignorée n'est pas un refus : aucune alerte ne s'affiche", async () => {
    const utilisateur = userEvent.setup();
    apercuSources.mockResolvedValue({
      tokens: 0,
      lectures: [
        lecture({
          nom: "maquette.png",
          etat: "ignore",
          tokens: 0,
          motif: "format-non-gere",
          message: "Format non géré.",
        }),
      ],
    });
    await ecran();
    await utilisateur.upload(
      screen.getByLabelText("Fichiers à joindre"),
      fichier("maquette.png", "binaire"),
    );

    await utilisateur.click(screen.getByRole("button", { name: "Voir ce qui sera lu" }));

    await screen.findByRole("region", { name: "Aperçu de l'extraction" });
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

// --- Critère 3 : refuser sans casser --------------------------------------

describe("les refus (#319, critère 3)", () => {
  it("affiche un refus indexé sur la source qu'il vise, et garde la saisie", async () => {
    const utilisateur = userEvent.setup();
    apercuSources.mockRejectedValue(
      new ErreurSource(
        "source-trop-volumineuse",
        "Source 2 trop volumineuse : 12000000 octets, 10485760 au maximum.",
        1,
      ),
    );
    await ecran();
    await utilisateur.type(screen.getByLabelText("Objectif"), "Refondre l'écran");
    await utilisateur.upload(screen.getByLabelText("Fichiers à joindre"), [
      fichier("petit.md"),
      fichier("enorme.pdf"),
    ]);

    await utilisateur.click(screen.getByRole("button", { name: "Voir ce qui sera lu" }));

    const refus = await screen.findByRole("alert");
    expect(refus).toHaveTextContent("trop volumineuse");
    expect(refus).toHaveTextContent("source-trop-volumineuse");
    // Le refus est **dans la ligne** de la source fautive, pas en tête d'écran.
    const ligne = screen.getByText("enorme.pdf").closest("li");
    expect(ligne).not.toBeNull();
    expect(within(ligne as HTMLElement).getByRole("alert")).toBe(refus);
    // Rien n'est perdu : ni l'objectif, ni les autres sources.
    expect(screen.getByLabelText("Objectif")).toHaveValue("Refondre l'écran");
    expect(screen.getByText("petit.md")).toBeInTheDocument();
  });

  it("affiche au geste un refus qui ne vise aucune source", async () => {
    const utilisateur = userEvent.setup();
    lancerExecution.mockRejectedValue(
      new ErreurSource("requete-invalide", "objectif vide : il n'y a rien à orchestrer."),
    );
    await ecran();
    await utilisateur.type(screen.getByLabelText("Objectif"), "   .");

    await utilisateur.click(screen.getByRole("button", { name: "Lancer l'orchestration" }));

    const refus = await screen.findByRole("alert");
    expect(refus).toHaveTextContent("Lancement refusé");
    expect(refus).toHaveTextContent("requete-invalide");
    expect(screen.getByLabelText("Objectif")).toHaveValue("   .");
  });

  it("traite une panne du backend comme un refus motivé, jamais comme un silence", async () => {
    const utilisateur = userEvent.setup();
    apercuSources.mockRejectedValue(new TypeError("Failed to fetch"));
    await ecran();
    await utilisateur.upload(screen.getByLabelText("Fichiers à joindre"), fichier());

    await utilisateur.click(screen.getByRole("button", { name: "Voir ce qui sera lu" }));

    const refus = await screen.findByRole("alert");
    expect(refus).toHaveTextContent("api-injoignable");
  });
});

// --- Le lancement ----------------------------------------------------------

describe("le lancement (#319)", () => {
  it("téléverse les octets puis lance avec les identifiants et le projet actif", async () => {
    const utilisateur = userEvent.setup();
    televerserSources.mockResolvedValue({
      sources: [{ id: "9f2c1ab34de5", type: "fichier", nom: "cdc.md", taille: 20 }],
      total_octets: 20,
    });
    await ecran();
    await utilisateur.type(screen.getByLabelText("Objectif"), "Refondre l'écran");
    await utilisateur.upload(screen.getByLabelText("Fichiers à joindre"), fichier());

    await utilisateur.click(screen.getByRole("button", { name: "Lancer l'orchestration" }));

    await waitFor(() => expect(lancerExecution).toHaveBeenCalled());
    expect(lancerExecution.mock.calls[0][0]).toMatchObject({
      objectif: "Refondre l'écran",
      projet_id: projetFactice().id,
      sources: [{ type: "fichier", id: "9f2c1ab34de5" }],
    });
  });

  it("ne téléverse rien quand aucun fichier n'est joint", async () => {
    const utilisateur = userEvent.setup();
    await ecran();
    await utilisateur.type(screen.getByLabelText("Objectif"), "Refondre l'écran");

    await utilisateur.click(screen.getByRole("button", { name: "Lancer l'orchestration" }));

    await waitFor(() => expect(lancerExecution).toHaveBeenCalled());
    expect(televerserSources).not.toHaveBeenCalled();
    // Un lancement sans matière est exactement celui d'avant la Phase 8.
    expect(lancerExecution.mock.calls[0][0]).not.toHaveProperty("sources");
  });

  it("rend le run parti, et remet l'écran à zéro", async () => {
    const utilisateur = userEvent.setup();
    await ecran();
    await utilisateur.type(screen.getByLabelText("Objectif"), "Refondre l'écran");

    await utilisateur.click(screen.getByRole("button", { name: "Lancer l'orchestration" }));

    expect(await screen.findByText("Run lancé")).toBeInTheDocument();
    expect(screen.getByText("9f2c1ab34de5")).toBeInTheDocument();
    expect(screen.getByLabelText("Objectif")).toHaveValue("");
  });
});

// --- Le vocabulaire, sans DOM ---------------------------------------------

describe("le vocabulaire des sources (lib/sources)", () => {
  it("prolonge le message du backend par un conseil, motif par motif", async () => {
    const { conseilSource } = await import("@/lib/sources");
    expect(conseilSource("url-non-suivable")).toMatch(/http\(s\)/);
    // Les motifs d'un dossier sont ceux de la validation de racine (#221) :
    // leur conseil est celui de l'écran Projets, pas une seconde table.
    expect(conseilSource("chemin-sensible")).toMatch(/Zone protégée/);
    expect(conseilSource("motif-que-personne-ne-connait")).toBeNull();
  });

  it("déclare un fichier par son identifiant dès qu'il en a un", async () => {
    const { declarationDe } = await import("@/lib/sources");
    const base = { cle: "s1", type: "fichier", nom: "cdc.md", valeur: "", taille: 20 };
    expect(declarationDe({ ...base, id: null, fichier: null })).toEqual({
      type: "fichier",
      nom: "cdc.md",
      taille: 20,
    });
    expect(declarationDe({ ...base, id: "9f2c", fichier: null })).toEqual({
      type: "fichier",
      id: "9f2c",
    });
  });

  it("compte les octets en puissances de 1024, comme les plafonds", async () => {
    const { formaterOctets } = await import("@/lib/sources");
    expect(formaterOctets(512)).toBe("512 o");
    expect(formaterOctets(2048)).toBe("2.0 Kio");
    expect(formaterOctets(10 * 1024 * 1024)).toBe("10.00 Mio");
  });
});

// Un garde-fou de forme, pas de fond : l'explorateur (#312) interdit un `<form>`
// imbriqué, et cet écran l'accueille — il n'en pose donc aucun.
it("n'imbrique aucun formulaire autour de l'explorateur", async () => {
  const utilisateur = userEvent.setup();
  await ecran();
  await utilisateur.click(
    screen.getByRole("button", { name: "Choisir un dossier de références…" }),
  );
  await screen.findByRole("region", { name: "Explorateur de dossiers" });
  expect(document.querySelectorAll("form")).toHaveLength(0);
});

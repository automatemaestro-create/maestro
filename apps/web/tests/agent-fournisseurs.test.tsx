/**
 * Le formulaire d'agent propose ce que le poste a (#487).
 *
 * Trois promesses, aucune visible d'un lint ni d'un `next build` :
 *
 * - **les deux colonnes ne se confondent pas** — « supporté par Maestro » vient
 *   du registre du code, « présent ici » de la sonde, et un outil trouvé sur la
 *   machine que Maestro ne sait pas piloter est **montré sans être proposé**.
 *   C'est le seul vrai mensonge que cet écran pourrait dire ;
 * - **la sonde suggère, elle ne restreint pas** — voir la nuance ci-dessous ;
 * - **ce que la sonde ne peut pas savoir est à l'écran**, pas seulement dans la
 *   charge de l'API : une absence n'est pas un constat, et le `PATH` du process
 *   qui sert l'API n'est pas celui du terminal.
 *
 * ⚠ **La deuxième promesse a été partagée en deux par #255**, et ce fichier
 * garde le partage plutôt que l'ancienne formule. « Les deux champs restent en
 * saisie libre » recouvrait deux cas que le contrat de #253 distingue :
 *
 * - le **modèle** reste libre quand le fournisseur l'admet (`modeles_libres`) —
 *   `OpenAICompatProvider.supports` accepte tout nom non vide, et un endpoint
 *   peut servir un modèle que personne n'a listé ;
 * - le **fournisseur**, lui, n'a jamais été dans ce cas : le registre est
 *   **exhaustif**, un nom qui n'y figure pas ne s'exécute pas. Le laisser en
 *   saisie libre n'offrait que la faute de frappe, d'où le `<select>` de #255.
 *
 * Le réseau est débranché par `tests/setup.ts`, qui sert `fournisseursDuPoste()`
 * — un **poste nu** par défaut, ce qui est le contrat de la sonde.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CreationAgent } from "@/components/EditeurAgent";
import type { CatalogueFournisseurs } from "@/lib/types";

import {
  CATALOGUE_POSTE_NU,
  constatPosteFactice,
  poserFournisseurs,
} from "./aides";

const OLLAMA = constatPosteFactice({
  genre: "serveur_local",
  cle: "serveur:ollama",
  libelle: "Ollama",
  fournisseur: "openai",
  detail: "servi par le fournisseur `openai`",
  origine: "http://127.0.0.1:11434",
  modeles: ["qwen2.5:3b", "llama3:8b"],
  incertitude: "aucun coût rapporté par ce fournisseur (#113)",
});

const GEMINI = constatPosteFactice({
  cle: "cli:gemini",
  libelle: "Gemini CLI",
  fournisseur: null,
  utilisable: false,
  detail: "agent CLI tiers — non branché (docs/34)",
});

const POSTE_EQUIPE: CatalogueFournisseurs = {
  fournisseurs: [
    {
      ...CATALOGUE_POSTE_NU.fournisseurs[0],
      nom: "claude",
      present_ici: false,
      utilisable_ici: false,
      modeles_ici: [],
      constats: [],
    },
    {
      ...CATALOGUE_POSTE_NU.fournisseurs[1],
      nom: "openai",
      present_ici: true,
      utilisable_ici: true,
      modeles_ici: ["qwen2.5:3b", "llama3:8b"],
      constats: [OLLAMA],
    },
  ],
  hors_registre: [GEMINI],
  incertitudes: [
    "les CLI sont résolus sur le `PATH` du process qui sert l'API, qui n'est pas toujours celui de votre terminal",
  ],
};

/**
 * Monte le formulaire de création et attend que le catalogue soit arrivé.
 *
 * Le repère est la **liste des fournisseurs peuplée** et non plus la présence
 * d'une `<datalist>` : depuis #255 le champ modèle n'en porte une que si le
 * fournisseur choisi offre quelque chose, ce qui n'est pas le cas au premier
 * rendu d'un formulaire vierge sur un poste nu.
 */
async function rendreFormulaire() {
  const vue = render(<CreationAgent onCreation={() => {}} />);
  await waitFor(() =>
    expect(
      within(screen.getByLabelText(/^Fournisseur/)).getAllByRole("option")
        .length,
    ).toBeGreaterThan(1),
  );
  return vue;
}

/** Les couples valeur → texte des options d'un `<select>`, dans l'ordre du rendu. */
function optionsListe(nom: RegExp): [string, string][] {
  const champ = screen.getByLabelText(nom);
  return within(champ)
    .getAllByRole("option")
    .map((o) => [o.getAttribute("value") ?? "", o.textContent ?? ""]);
}

/**
 * Les couples valeur → libellé d'une `<datalist>`, dans l'ordre du rendu — vide
 * si le champ n'en porte aucune, ce qui est un état attendu (rien à proposer)
 * et non un oubli : sans cette garde, `#` seul part en `SyntaxError`.
 */
function suggestions(racine: HTMLElement, nom: RegExp): [string, string][] {
  const champ = screen.getByLabelText(nom);
  const id = champ.getAttribute("list");
  if (!id) return [];
  const liste = racine.querySelector<HTMLDataListElement>(`#${CSS.escape(id)}`);
  return [...(liste?.querySelectorAll("option") ?? [])].map((o) => [
    o.getAttribute("value") ?? "",
    o.getAttribute("label") ?? "",
  ]);
}

describe("le formulaire d'agent, éclairé par le poste (#487)", () => {
  it("propose les fournisseurs du registre en disant lesquels sont ici", async () => {
    poserFournisseurs(POSTE_EQUIPE);
    await rendreFormulaire();

    expect(optionsListe(/^Fournisseur/)).toEqual([
      ["", "— défaut de l’exécution"],
      ["claude", "claude — supporté par Maestro · absent d'ici"],
      ["openai", "openai — supporté par Maestro · présent ici"],
    ]);
  });

  it("propose les modèles que le poste sert", async () => {
    poserFournisseurs(POSTE_EQUIPE);
    const { container } = await rendreFormulaire();

    // Aucun fournisseur choisi : l'offre est celle du poste, tous fournisseurs
    // confondus — « les siens » suppose un « il » (#255).
    expect(suggestions(container, /^Modèle/).map(([valeur]) => valeur)).toEqual([
      "qwen2.5:3b",
      "llama3:8b",
    ]);
  });

  it("montre l'outil non supporté sans jamais le proposer", async () => {
    poserFournisseurs(POSTE_EQUIPE);
    await rendreFormulaire();

    expect(screen.getByText(/Gemini CLI/)).toBeInTheDocument();
    expect(optionsListe(/^Fournisseur/).map(([valeur]) => valeur)).not.toContain(
      "gemini",
    );
  });

  it("garde le modèle en saisie libre tant que le fournisseur l'admet", async () => {
    poserFournisseurs(POSTE_EQUIPE);
    await rendreFormulaire();

    // Les deux fournisseurs d'aujourd'hui sont `modeles_libres` : un `<select>`
    // rendrait insaisissable ce que le catalogue ignore.
    expect(screen.getByLabelText(/^Modèle/)).toHaveProperty("tagName", "INPUT");
    // Le fournisseur, lui, est borné par le registre — voir l'en-tête.
    expect(screen.getByLabelText(/^Fournisseur/)).toHaveProperty(
      "tagName",
      "SELECT",
    );
  });

  it("dit à l'écran ce que la sonde ne peut pas savoir", async () => {
    poserFournisseurs(POSTE_EQUIPE);
    await rendreFormulaire();

    expect(
      screen.getByText(/pas toujours celui de votre terminal/i),
    ).toBeInTheDocument();
  });

  it("rattache les incertitudes aux deux champs qu'elles concernent", async () => {
    poserFournisseurs(POSTE_EQUIPE);
    await rendreFormulaire();

    const decrit = screen
      .getByLabelText(/^Fournisseur/)
      .getAttribute("aria-describedby");
    expect(decrit).toBeTruthy();
    expect(screen.getByLabelText(/^Modèle/)).toHaveAttribute(
      "aria-describedby",
      decrit,
    );
  });

  it("sur un poste nu, ne propose aucun modèle et le dit", async () => {
    // Le défaut de `setup.ts` : deux fournisseurs au registre, rien de détecté.
    await rendreFormulaire();

    expect(screen.getByLabelText(/^Modèle/)).not.toHaveAttribute("list");
    expect(optionsListe(/^Fournisseur/)).toEqual([
      ["", "— défaut de l’exécution"],
      ["claude", "claude — supporté par Maestro · absent d'ici"],
      ["openai", "openai — supporté par Maestro · absent d'ici"],
    ]);
    expect(
      screen.getByText(/aucun fournisseur armé n’a été détecté/),
    ).toBeInTheDocument();
  });
});

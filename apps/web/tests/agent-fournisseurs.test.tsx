/**
 * Le formulaire d'agent propose ce que le poste a (#487).
 *
 * Trois promesses, aucune visible d'un lint ni d'un `next build` :
 *
 * - **les deux colonnes ne se confondent pas** — « supporté par Maestro » vient
 *   du registre du code, « présent ici » de la sonde, et un outil trouvé sur la
 *   machine que Maestro ne sait pas piloter est **montré sans être proposé**.
 *   C'est le seul vrai mensonge que cet écran pourrait dire ;
 * - **la sonde suggère, elle ne restreint pas** : les deux champs restent en
 *   saisie libre. `OpenAICompatProvider.supports` accepte tout nom non vide, et
 *   un endpoint peut servir un modèle que personne n'a listé — un `<select>`
 *   rendrait insaisissable ce que le catalogue ignore ;
 * - **ce que la sonde ne peut pas savoir est à l'écran**, pas seulement dans la
 *   charge de l'API : une absence n'est pas un constat, et le `PATH` du process
 *   qui sert l'API n'est pas celui du terminal.
 *
 * Le réseau est débranché par `tests/setup.ts`, qui sert `fournisseursDuPoste()`
 * — un **poste nu** par défaut, ce qui est le contrat de la sonde.
 */

import { render, screen, waitFor } from "@testing-library/react";
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

/** Monte le formulaire de création et attend que le catalogue soit arrivé. */
async function rendreFormulaire() {
  const vue = render(<CreationAgent onCreation={() => {}} />);
  await waitFor(() =>
    expect(vue.container.querySelector("datalist")).not.toBeNull(),
  );
  return vue;
}

/** Les couples valeur → libellé d'une `<datalist>`, dans l'ordre du rendu. */
function options(racine: HTMLElement, apres: string): [string, string][] {
  const champ = screen.getByLabelText(new RegExp(apres, "i"));
  const liste = racine.querySelector<HTMLDataListElement>(
    `#${CSS.escape(champ.getAttribute("list") ?? "")}`,
  );
  return [...(liste?.querySelectorAll("option") ?? [])].map((o) => [
    o.getAttribute("value") ?? "",
    o.getAttribute("label") ?? "",
  ]);
}

describe("le formulaire d'agent, éclairé par le poste (#487)", () => {
  it("propose les fournisseurs du registre en disant lesquels sont ici", async () => {
    poserFournisseurs(POSTE_EQUIPE);
    const { container } = await rendreFormulaire();

    expect(options(container, "^Fournisseur")).toEqual([
      ["claude", "supporté par Maestro · absent d'ici"],
      ["openai", "supporté par Maestro · présent ici"],
    ]);
  });

  it("propose les modèles que le poste sert", async () => {
    poserFournisseurs(POSTE_EQUIPE);
    const { container } = await rendreFormulaire();

    expect(options(container, "^Modèle").map(([valeur]) => valeur)).toEqual([
      "qwen2.5:3b",
      "llama3:8b",
    ]);
  });

  it("montre l'outil non supporté sans jamais le proposer", async () => {
    poserFournisseurs(POSTE_EQUIPE);
    const { container } = await rendreFormulaire();

    expect(screen.getByText(/Gemini CLI/)).toBeInTheDocument();
    expect(
      options(container, "^Fournisseur").map(([valeur]) => valeur),
    ).not.toContain("gemini");
  });

  it("laisse les deux champs en saisie libre", async () => {
    poserFournisseurs(POSTE_EQUIPE);
    await rendreFormulaire();

    // Un `<select>` aurait rendu insaisissable ce que le catalogue ignore.
    expect(screen.getByLabelText(/^Fournisseur/)).toHaveProperty(
      "tagName",
      "INPUT",
    );
    expect(screen.getByLabelText(/^Modèle/)).toHaveProperty("tagName", "INPUT");
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
    const { container } = await rendreFormulaire();

    expect(screen.getByLabelText(/^Modèle/)).not.toHaveAttribute("list");
    expect(options(container, "^Fournisseur")).toEqual([
      ["claude", "supporté par Maestro · absent d'ici"],
      ["openai", "supporté par Maestro · absent d'ici"],
    ]);
    expect(
      screen.getByText(/aucun fournisseur armé n’a été détecté/),
    ).toBeInTheDocument();
  });
});

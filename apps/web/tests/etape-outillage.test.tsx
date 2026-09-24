/**
 * L'étape d'outillage envoie à la génération ce dont la liste a été dérivée (#1100).
 *
 * Le défaut que ces tests gardent : un projet **neuf** montrait la
 * recommandation de ses **réponses** (quatre skills), puis le bouton « Générer »
 * n'envoyait que les chemins cochés. Le serveur rederivait alors la liste de
 * l'analyse d'une racine vide, et les skills cochés n'étaient pas écrits — sans
 * qu'aucune ligne du rapport le dise. Chaque morceau, lu seul, était correct ;
 * c'est **ce qui voyage** entre l'écran et la génération qui manquait.
 *
 * D'où trois gardes :
 *
 * 1. un projet neuf envoie ses réponses — données **et** ce qui en a été compris,
 *    ce que l'écran tient — avec les chemins retenus ;
 * 2. un projet existant n'en envoie aucune : son outillage se dérive de son
 *    analyse, et y mêler des réponses qu'il n'a pas données n'aurait pas de sens ;
 * 3. un chemin retenu que la génération n'a pas reconnu se **nomme** dans le
 *    rapport à l'écran, comme ce qui n'a pas été écrasé.
 *
 * Et depuis #1147, le chemin de « p2 » : un projet neuf se **décrit avec ses mots**
 * (la première question n'a pas d'options), Maestro dit ce qu'il a compris avant la
 * question suivante, et ce qu'il comprend à chaque tour **remplace** ce qu'il avait
 * compris au tour d'avant au lieu de s'y empiler.
 *
 * `genererOutillage` est ensuite joué pour de vrai, `fetch` simulé : les
 * réponses doivent atteindre le corps de la requête, pas seulement l'appel.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { EtapeOutillage } from "@/components/projets/EtapeOutillage";
import type {
  ChoixOutillage,
  EntreeOutillage,
  RapportGenerationOutillage,
  RecommandationOutillage,
} from "@/lib/types";

import { projetFactice } from "./aides";

const questionOutillage = vi.fn();
const recommandationOutillage = vi.fn();
const analyserOutillage = vi.fn();
const genererOutillage = vi.fn();
const reporterOutillage = vi.fn();

// `importOriginal` : `ErreurProjet` doit rester **la** classe du module (voir
// `projets.test.tsx`), et le test du corps rejoue la vraie `genererOutillage`.
vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...reel,
    questionOutillage: (id: string, choix: ChoixOutillage[]) =>
      questionOutillage(id, choix),
    recommandationOutillage: (id: string, choix: ChoixOutillage[]) =>
      recommandationOutillage(id, choix),
    analyserOutillage: (id: string) => analyserOutillage(id),
    genererOutillage: (
      id: string,
      retenus?: string[],
      choix?: ChoixOutillage[],
    ) => genererOutillage(id, retenus, choix),
    reporterOutillage: (id: string) => reporterOutillage(id),
  };
});

const SKILL_TESTS = ".agents/skills/lancer-les-tests/SKILL.md";

function entree(partiel: Partial<EntreeOutillage>): EntreeOutillage {
  return {
    type: "instructions",
    nom: "AGENTS.md",
    chemin: "AGENTS.md",
    etat: "a-generer",
    raison: "le fichier d'instructions que tous les clients lisent",
    justification: null,
    commandes: [],
    ...partiel,
  };
}

/** La recommandation que les réponses — ou l'analyse — rendent : deux entrées. */
const RECOMMANDATION: RecommandationOutillage = {
  entrees: [
    entree({}),
    entree({
      type: "skill",
      nom: "lancer-les-tests",
      chemin: SKILL_TESTS,
      raison: "réponse « tests » = vitest",
      commandes: ["npx vitest run"],
    }),
  ],
  ecartes: [],
};

/** Le projet, décrit avec les mots de la personne — ce que « p2 » n'a pas pu dire. */
const DESCRIPTION = "Une application mobile Flutter pour réserver des terrains";

/** Les réponses données à l'écran, puis ce que le moteur en a compris. */
const DECRITE: ChoixOutillage = {
  cle: "nature",
  valeur: DESCRIPTION,
  deduit: false,
  parce_que: "",
  libre: true,
};
const CLIQUEE: ChoixOutillage = {
  cle: "forge",
  valeur: "github",
  deduit: false,
  parce_que: "",
  libre: false,
};
const COMPRISE: ChoixOutillage = {
  cle: "tester",
  valeur: "flutter test",
  deduit: true,
  parce_que: "le lanceur livré avec Flutter",
  sujet: "tests",
};
const FORGE_COMPRISE: ChoixOutillage = {
  cle: "forge",
  valeur: "github",
  deduit: true,
  parce_que: "votre réponse",
  sujet: "forge",
};

const QUESTION_OUVERTE = {
  cle: "nature",
  intitule: "Qu'est-ce que ce projet ?",
  options: [],
  recommande: "",
  pourquoi: "Dites-le avec vos mots.",
  rang: 1,
};

const QUESTION_FORGE = {
  cle: "forge",
  intitule: "Où le code vivra-t-il ?",
  options: [
    { valeur: "github", libelle: "GitHub", raison: "Pull Requests et Actions" },
    { valeur: "aucun", libelle: "Nulle part", raison: "le projet reste local" },
  ],
  recommande: "github",
  pourquoi: "vous comptez le publier",
  rang: 2,
};

function rapport(
  partiel: Partial<RapportGenerationOutillage> = {},
): RapportGenerationOutillage {
  return {
    projet_id: "prj-neuf",
    analyse: "",
    regime: "en-place",
    rapport: {
      cible: "D:/projets/vitrine",
      manifeste: ".maestro/outillage/manifeste.json",
      refus: "",
      ecrits: ["AGENTS.md", SKILL_TESTS],
      refuses: [],
      ignores: [],
      retires: [],
    },
    retenus_inconnus: [],
    application: null,
    ...partiel,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  // Le moteur répond selon le nombre de réponses **données** : d'abord la question
  // ouverte, puis — la description comprise — la forge, puis plus rien.
  questionOutillage.mockImplementation((_id: string, choix: ChoixOutillage[]) => {
    const donnees = choix.filter((c) => !c.deduit).length;
    return Promise.resolve(
      donnees === 0
        ? { question: QUESTION_OUVERTE, deductions: [], terminee: false, message: "" }
        : donnees === 1
          ? {
              question: QUESTION_FORGE,
              deductions: [COMPRISE],
              terminee: false,
              message: "",
            }
          : {
              question: null,
              deductions: [COMPRISE, FORGE_COMPRISE],
              terminee: true,
              message: "",
            },
    );
  });
  recommandationOutillage.mockResolvedValue({
    projet_id: "prj-neuf",
    source: { type: "choix" },
    choix: [DECRITE, CLIQUEE, COMPRISE, FORGE_COMPRISE],
    recommandation: RECOMMANDATION,
  });
  analyserOutillage.mockResolvedValue({
    analyse: 1,
    id: "ana-1",
    projet_id: "prj-7f3a1c2b",
    racine: "D:/projets/depensio",
    faite_le: "2026-09-21T10:00:00+00:00",
    resume: "TypeScript ; npm",
    parcours: {
      fichiers_vus: 12,
      dossiers_vus: 3,
      profondeur_atteinte: 2,
      tronque: false,
      troncatures: [],
      ignores_rencontres: [],
    },
    recommandation: RECOMMANDATION,
  });
  genererOutillage.mockResolvedValue(rapport());
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const generer = async () => {
  const bouton = await screen.findByRole("button", {
    name: "Générer l'outillage (2)",
  });
  await userEvent.click(bouton);
};

/** Le projet décrit avec ses mots, puis la forge gardée : le questionnaire conclu. */
const repondreAuQuestionnaire = async () => {
  await userEvent.type(
    await screen.findByRole("textbox", { name: "Votre réponse" }),
    DESCRIPTION,
  );
  await userEvent.click(screen.getByRole("button", { name: "Envoyer ma réponse" }));
  await userEvent.click(
    await screen.findByRole("button", { name: "Garder ce choix" }),
  );
};

describe("le questionnaire d'un projet neuf, dans l'étape d'outillage (#1147)", () => {
  it("commence par une question ouverte : le projet se dit avec ses mots", async () => {
    render(
      <EtapeOutillage
        projet={projetFactice({ id: "prj-neuf", origine: "nouveau" })}
        onTermine={() => {}}
      />,
    );

    await screen.findByText("Qu'est-ce que ce projet ?");
    // Aucune liste de sortes de projet où il faudrait entrer.
    expect(screen.queryByRole("radiogroup")).toBeNull();
    const envoyer = screen.getByRole("button", { name: "Envoyer ma réponse" });
    expect(envoyer).toBeDisabled();

    await userEvent.type(
      screen.getByRole("textbox", { name: "Votre réponse" }),
      DESCRIPTION,
    );
    await userEvent.click(envoyer);

    expect(questionOutillage).toHaveBeenLastCalledWith("prj-neuf", [DECRITE]);
    // Ce qui a été compris se lit avant la question suivante.
    expect(await screen.findByText(/Ce que j'ai compris/)).toBeInTheDocument();
    expect(screen.getByText(/tests : flutter test/)).toBeInTheDocument();
    expect(screen.getByText("Où le code vivra-t-il ?")).toBeInTheDocument();
  });

  it("renvoie les réponses données, et remplace ce qui avait été compris", async () => {
    render(
      <EtapeOutillage
        projet={projetFactice({ id: "prj-neuf", origine: "nouveau" })}
        onTermine={() => {}}
      />,
    );

    await repondreAuQuestionnaire();

    await waitFor(() => expect(recommandationOutillage).toHaveBeenCalledTimes(1));
    // Au second tour, seules les réponses **données** repartent : la compréhension
    // du tour d'avant n'est pas renvoyée comme si c'était une réponse.
    expect(questionOutillage).toHaveBeenLastCalledWith("prj-neuf", [DECRITE, CLIQUEE]);
    expect(recommandationOutillage).toHaveBeenCalledWith("prj-neuf", [
      DECRITE,
      CLIQUEE,
      COMPRISE,
      FORGE_COMPRISE,
    ]);
  });
});

describe("le bouton « Générer » de l'étape d'outillage", () => {
  it("envoie les réponses d'un projet neuf avec les chemins retenus", async () => {
    render(
      <EtapeOutillage
        projet={projetFactice({ id: "prj-neuf", origine: "nouveau" })}
        onTermine={() => {}}
      />,
    );

    await repondreAuQuestionnaire();
    await generer();

    await waitFor(() => expect(genererOutillage).toHaveBeenCalledTimes(1));
    const [id, retenus, choix] = genererOutillage.mock.calls[0];
    expect(id).toBe("prj-neuf");
    expect(retenus).toEqual(["AGENTS.md", SKILL_TESTS]);
    // Les réponses **données et ce qui en a été compris** : ce d'où la liste a été
    // dérivée, exactement ce que la recommandation a reçu.
    expect(choix).toEqual([DECRITE, CLIQUEE, COMPRISE, FORGE_COMPRISE]);
    expect(choix).toEqual(recommandationOutillage.mock.calls[0][1]);
  });

  it("n'envoie aucune réponse pour un projet existant, dérivé de son analyse", async () => {
    render(
      <EtapeOutillage
        projet={projetFactice({ origine: "existant" })}
        onTermine={() => {}}
      />,
    );

    await generer();

    await waitFor(() => expect(genererOutillage).toHaveBeenCalledTimes(1));
    expect(genererOutillage).toHaveBeenCalledWith(
      "prj-7f3a1c2b",
      ["AGENTS.md", SKILL_TESTS],
      undefined,
    );
    expect(questionOutillage).not.toHaveBeenCalled();
  });

  it("nomme dans le rapport un élément retenu que la génération n'a pas reconnu", async () => {
    genererOutillage.mockResolvedValue(
      rapport({
        rapport: { ...rapport().rapport, ecrits: ["AGENTS.md"] },
        retenus_inconnus: [SKILL_TESTS],
      }),
    );
    render(
      <EtapeOutillage
        projet={projetFactice({ origine: "existant" })}
        onTermine={() => {}}
      />,
    );

    await generer();

    const ligne = await screen.findByText(/1 élément retenu non écrit/);
    expect(ligne).toHaveTextContent(SKILL_TESTS);
    expect(ligne).toHaveTextContent("la génération ne les recommande plus");
  });

  it("ne dit rien d'inconnu quand tout ce qui a été retenu est écrit", async () => {
    render(
      <EtapeOutillage
        projet={projetFactice({ origine: "existant" })}
        onTermine={() => {}}
      />,
    );

    await generer();

    await screen.findByText(/2 fichiers écrits/);
    expect(screen.queryByText(/retenu.* non écrit/)).toBeNull();
  });
});

describe("genererOutillage", () => {
  /** La vraie fonction, `fetch` simulé : ce qui compte est le corps envoyé. */
  const corpsEnvoye = async (
    ...args: [string, string[]?, ChoixOutillage[]?]
  ): Promise<unknown> => {
    const fetch = vi.fn(async () => Response.json(rapport()));
    vi.stubGlobal("fetch", fetch);
    const { genererOutillage: reelle } =
      await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
    await reelle(...args);
    const [, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    return JSON.parse(String(init.body));
  };

  it("transmet les réponses dans le corps, à côté des chemins retenus", async () => {
    expect(await corpsEnvoye("prj-neuf", ["AGENTS.md"], [DECRITE, COMPRISE])).toEqual({
      retenus: ["AGENTS.md"],
      choix: [DECRITE, COMPRISE],
    });
  });

  it("n'ajoute pas de réponses qu'on ne lui a pas données", async () => {
    expect(await corpsEnvoye("prj-7f3a1c2b", ["AGENTS.md"])).toEqual({
      retenus: ["AGENTS.md"],
    });
    expect(await corpsEnvoye("prj-7f3a1c2b")).toEqual({});
  });
});

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
 * 1. un projet neuf envoie ses réponses — données **et** déduites, le `tous` que
 *    l'écran tient — avec les chemins retenus ;
 * 2. un projet existant n'en envoie aucune : son outillage se dérive de son
 *    analyse, et y mêler des réponses qu'il n'a pas données n'aurait pas de sens ;
 * 3. un chemin retenu que la génération n'a pas reconnu se **nomme** dans le
 *    rapport à l'écran, comme ce qui n'a pas été écrasé.
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

/** La réponse donnée à l'écran, puis celle que le serveur en déduit. */
const DONNEE: ChoixOutillage = {
  cle: "langages",
  valeur: "typescript",
  deduit: false,
  parce_que: "",
};
const DEDUITE: ChoixOutillage = {
  cle: "tests",
  valeur: "vitest",
  deduit: true,
  parce_que: "Déduit de « TypeScript »",
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
  questionOutillage.mockImplementation((_id: string, choix: ChoixOutillage[]) =>
    Promise.resolve(
      choix.length === 0
        ? {
            question: {
              cle: "langages",
              intitule: "Dans quel langage ?",
              options: [
                { valeur: "typescript", libelle: "TypeScript", raison: "web" },
                { valeur: "python", libelle: "Python", raison: "service" },
              ],
              recommande: "typescript",
              pourquoi: "c'est une application web",
              rang: 1,
              total: 6,
            },
            deductions: [],
            terminee: false,
          }
        : { question: null, deductions: [DEDUITE], terminee: true },
    ),
  );
  recommandationOutillage.mockResolvedValue({
    projet_id: "prj-neuf",
    source: { type: "choix" },
    choix: [DONNEE, DEDUITE],
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

describe("le bouton « Générer » de l'étape d'outillage", () => {
  it("envoie les réponses d'un projet neuf avec les chemins retenus", async () => {
    render(
      <EtapeOutillage
        projet={projetFactice({ id: "prj-neuf", origine: "nouveau" })}
        onTermine={() => {}}
      />,
    );

    await userEvent.click(
      await screen.findByRole("button", { name: "Garder ce choix" }),
    );
    await generer();

    await waitFor(() => expect(genererOutillage).toHaveBeenCalledTimes(1));
    const [id, retenus, choix] = genererOutillage.mock.calls[0];
    expect(id).toBe("prj-neuf");
    expect(retenus).toEqual(["AGENTS.md", SKILL_TESTS]);
    // Les réponses **données et déduites** : celles d'où la liste a été dérivée,
    // exactement ce que la recommandation a reçu.
    expect(choix).toEqual([DONNEE, DEDUITE]);
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
    expect(await corpsEnvoye("prj-neuf", ["AGENTS.md"], [DONNEE, DEDUITE])).toEqual({
      retenus: ["AGENTS.md"],
      choix: [DONNEE, DEDUITE],
    });
  });

  it("n'ajoute pas de réponses qu'on ne lui a pas données", async () => {
    expect(await corpsEnvoye("prj-7f3a1c2b", ["AGENTS.md"])).toEqual({
      retenus: ["AGENTS.md"],
    });
    expect(await corpsEnvoye("prj-7f3a1c2b")).toEqual({});
  });
});

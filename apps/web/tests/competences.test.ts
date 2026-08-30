/**
 * Le vocabulaire des compétences (#256, lot 4/15 de #243).
 *
 * ⚠ Ce fichier ne couvre pas le formulaire, seulement `lib/competences` — la part
 * **pure**, et la seule dont une erreur ne se verrait nulle part. Le reste (le
 * champ à jetons, ce que l'écran signale) était différé au lot 15 (« tests +
 * doc ») et l'a rejoint : il se lit dans `agent-listes-liees.test.tsx` (le
 * vocabulaire proposé, la saisie inédite qui passe) et `agent-creation.test.tsx`
 * (les jetons qui deviennent la liste envoyée à l'API).
 *
 * Pourquoi celle-ci quand même : c'est de l'arithmétique de chaînes — des seuils
 * de distance d'édition, une transposition qui doit compter pour un geste et non
 * deux. Un seuil trop large propose « frontend » pour « backend » ; trop étroit,
 * il ne rattrape plus la faute de frappe qui est toute la raison d'être du lot.
 * Ni le lint, ni le typage, ni `next build` n'en verraient rien, et à l'écran un
 * mauvais voisin se lit comme une suggestion plausible.
 *
 * Deux promesses tiennent le fichier, et ce sont les deux moitiés du même
 * arbitrage : **ce qui doit parler parle** (la casse, la transposition, la lettre
 * en trop) et **ce qui doit se taire se tait** (deux métiers voisins, un mot
 * court, un catalogue qu'on n'a pas pu lire). La seconde compte autant : un
 * signalement qui se déclenche à tort n'est plus lu.
 */

import { describe, expect, it } from "vitest";

import {
  competenceProche,
  decouperSaisie,
  inedites,
  normaliserCompetence,
  vocabulaireDuCatalogue,
} from "@/lib/competences";
import type { AgentCatalogue } from "@/lib/types";

/** Les compétences des cinq agents par défaut (`maestro/agents/catalog.py`). */
const VOCABULAIRE = [
  "api",
  "backend",
  "ci-cd",
  "data",
  "deploy",
  "design-system",
  "docker",
  "e2e",
  "figma",
  "frontend",
  "infra",
  "migration",
  "qa",
  "refactor",
  "review",
  "schema",
  "sql",
  "tests",
  "ui",
  "ux",
];

/** Une fiche réduite à ce que le vocabulaire lui demande. */
function fiche(competences: string[]): AgentCatalogue {
  return { competences } as unknown as AgentCatalogue;
}

describe("la normalisation d'un jeton", () => {
  it("rogne et resserre — sans jamais toucher à la casse", () => {
    // Le dépôt garde la casse (`_valide`, maestro/agents/store.py) : la changer
    // ici ferait enregistrer autre chose que ce qui est à l'écran.
    expect(normaliserCompetence("  React  ")).toBe("React");
    expect(normaliserCompetence("design   system")).toBe("design system");
    expect(normaliserCompetence("   ")).toBe("");
  });
});

describe("le découpage d'une saisie", () => {
  it("rend une liste d'un collage virgulé — le format d'avant le lot", () => {
    expect(decouperSaisie("frontend, react, css")).toEqual([
      "frontend",
      "react",
      "css",
    ]);
  });

  it("encaisse les séparateurs vides sans rendre de jeton vide", () => {
    expect(decouperSaisie("a;;b\nc\t d ")).toEqual(["a", "b", "c", "d"]);
    expect(decouperSaisie("  ,  , ")).toEqual([]);
  });
});

describe("le vocabulaire du catalogue", () => {
  it("dédoublonne et range, en écartant le vide", () => {
    expect(
      vocabulaireDuCatalogue([fiche(["ui", "ux"]), fiche(["api", "ui", " "])]),
    ).toEqual(["api", "ui", "ux"]);
  });
});

describe("le voisin le plus proche", () => {
  it("attrape la casse — le cas que l'œil ne voit pas", () => {
    // « React » et « react » sont le même mot pour qui les lit, et deux
    // compétences étrangères pour l'intersection d'ensembles du routeur.
    expect(competenceProche("React", ["react", "api"])).toBe("react");
    expect(competenceProche("SQL", VOCABULAIRE)).toBe("sql");
  });

  it("attrape la transposition et la lettre manquante", () => {
    expect(competenceProche("dcoker", VOCABULAIRE)).toBe("docker");
    expect(competenceProche("frontnd", VOCABULAIRE)).toBe("frontend");
    expect(competenceProche("cicd", VOCABULAIRE)).toBe("ci-cd");
  });

  it("se tait plutôt que de confondre deux métiers", () => {
    expect(competenceProche("backend", ["frontend"])).toBeNull();
    expect(competenceProche("robotique", VOCABULAIRE)).toBeNull();
  });

  it("se tait sur un mot trop court, où tout est proche de tout", () => {
    // « ui » et « ux » sont à un geste l'un de l'autre et désignent deux
    // métiers : sur trois lettres, seule l'égalité à la casse près vaut.
    expect(competenceProche("uy", VOCABULAIRE)).toBeNull();
  });

  it("ne se propose jamais lui-même", () => {
    expect(competenceProche("api", ["api"])).toBeNull();
  });
});

describe("les compétences inédites", () => {
  it("compare au mot près, comme la règle de recouvrement", () => {
    expect(inedites(["api", "React"], VOCABULAIRE)).toEqual(["React"]);
  });

  it("juge tout inédit sur un catalogue lu et vide", () => {
    expect(inedites(["api"], [])).toEqual(["api"]);
  });

  it("se tait quand le catalogue n'a pas pu être lu", () => {
    // `null` n'est pas `[]` : ne rien savoir n'autorise pas à alerter, et une
    // alerte sur toutes les compétences vaudrait moins que pas d'alerte.
    expect(inedites(["nimporte", "quoi"], null)).toEqual([]);
  });
});

/**
 * La carte de la **proposition de l'outillage entier** (#1533).
 *
 * Sur p5, le fil posait neuf questions une à une ; il propose désormais l'outillage
 * d'un bloc, que la personne retient d'un geste ou corrige avec ses mots. La forme est
 * la variante A retenue sur pièces par le regard neuf (commentaire « Variante retenue »
 * de #1533). Ce que ce filet garde :
 *
 * ① **tout est sous les yeux** : une ligne par élément — le sujet, le nom, la commande
 *   en chasse fixe quand le nom ne la dit pas, la raison dessous —, et ce qui a été
 *   compris en tête ;
 * ② **un seul geste**, qui retient la proposition (`PROPOSITION_RETENUE`, sans mots) ;
 *   aucun champ ni interrupteur : corriger se dit dans la zone de saisie, et la carte
 *   le dit ;
 * ③ un outil que le poste n'a pas se dit sur sa ligne, par ses mots ;
 * ④ un refus du moteur se dit, sans rien perdre ;
 * ⑤ dans une carte étroite, chaque élément passe sur deux lignes — le sujet au-dessus.
 *
 * Aucun rendu jugé ici : c'est l'affaire de la relecture visuelle.
 */

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { PropositionDOutillage } from "@/components/chat/PropositionDOutillage";
import { estUneProposition, PROPOSITION_RETENUE } from "@/lib/outillage";
import type { ChoixOutillage, QuestionOutillage } from "@/lib/types";

/** Une proposition telle que la vraie stack l'a servie pour un livre de recettes. */
const PROPOSITION: QuestionOutillage = {
  cle: "outillage",
  intitule: "L'outillage que je vous propose",
  options: [],
  recommande: "",
  pourquoi: "Retenez-les d'un geste, ou dites avec vos mots ce qui doit changer.",
  rang: 3,
  proposition: [
    {
      cle: "gestionnaire",
      valeur: "npm",
      libelle: "npm",
      raison: "Fourni avec Node.js : rien de plus à installer.",
      sujet: "gestionnaire",
      commande: false,
    },
    {
      cle: "tester",
      valeur: "npm test",
      libelle: "Vitest",
      raison: "Rapide, et natif pour TypeScript.",
      sujet: "tests",
      commande: true,
      pour: "Jouer les tests des fiches de recettes, avant de rendre.",
    },
    {
      cle: "installer",
      valeur: "pnpm install",
      libelle: "pnpm install",
      raison: "Installe ce que package.json déclare — `pnpm` n'est pas installé sur ce poste.",
      sujet: "installation",
      commande: true,
      absents: ["pnpm"],
    },
    {
      cle: "ci",
      valeur: ".github/workflows/ci.yml",
      libelle: "GitHub Actions",
      raison: "Le code est sur GitHub.",
      sujet: "intégration continue",
      commande: false,
    },
  ],
};

const COMPRIS: ChoixOutillage[] = [
  { cle: "langages", valeur: "TypeScript", deduit: true, parce_que: "dit", sujet: "langage" },
  { cle: "cadre", valeur: "Next.js", deduit: true, parce_que: "dit", sujet: "cadre" },
];

/** La liste de ce qui est proposé — celle que « Ce que je propose » nomme. */
function listeProposee(): HTMLElement {
  return screen.getAllByRole("definition")[0].closest("dl") as HTMLElement;
}

/** Les lignes de la proposition : chaque `<dt>` et le `<dd>` qui le suit. */
function lignes(): { sujet: string; valeur: HTMLElement }[] {
  const liste = listeProposee();
  return [...liste.querySelectorAll("dt")].map((dt) => ({
    sujet: dt.textContent ?? "",
    valeur: dt.nextElementSibling as HTMLElement,
  }));
}

describe("la proposition de l'outillage entier", () => {
  it("① montre chaque élément avec son sujet, son nom, sa commande et sa raison", () => {
    render(
      <PropositionDOutillage
        question={PROPOSITION}
        compris={COMPRIS}
        repondre={() => Promise.resolve()}
      />,
    );

    const carte = screen.getByRole("region", { name: "Proposition d'outillage" });
    expect(
      within(carte).getByRole("heading", { name: "L'outillage que je vous propose" }),
    ).toBeInTheDocument();
    expect(lignes().map((l) => l.sujet)).toEqual([
      "gestionnaire",
      "tests",
      "installation",
      "intégration continue",
    ]);
    const [npm, tests, , ci] = lignes();
    // La commande en chasse fixe quand le nom ne la dit pas : « Vitest » écrira `npm test`.
    expect(within(tests.valeur).getByText("Vitest")).toHaveClass("font-medium");
    const commande = within(tests.valeur).getByText("npm test");
    expect(commande.tagName).toBe("CODE");
    // Quand le nom la dit déjà (npm), elle n'est pas répétée.
    expect(npm.valeur.querySelector("code")).toBeNull();
    expect(within(ci.valeur).getByText(".github/workflows/ci.yml").tagName).toBe("CODE");
    // La raison se lit sur chaque ligne, sans survol.
    expect(within(tests.valeur).getByText("Rapide, et natif pour TypeScript.")).toBeVisible();
    // Ce qui a été compris, en tête — distinct par la forme de ce qui est proposé.
    const compris = within(carte).getByRole("list", { name: "Ce que j'ai compris" });
    expect(within(compris).getAllByRole("listitem").map((e) => e.textContent)).toEqual([
      "langage TypeScript",
      "cadre Next.js",
    ]);
    expect(within(carte).getByText("Ce que je propose")).toBeInTheDocument();
  });

  it("② un seul geste, qui retient la proposition telle quelle — corriger se dit en mots", async () => {
    const repondre = vi.fn(() => Promise.resolve());
    render(<PropositionDOutillage question={PROPOSITION} repondre={repondre} />);

    const carte = screen.getByRole("region", { name: "Proposition d'outillage" });
    const gestes = within(carte).getAllByRole("button");
    expect(gestes.map((g) => g.textContent)).toEqual(["Retenir cet outillage"]);
    // Aucun champ, aucun interrupteur : ce serait le formulaire que #1161 a retiré.
    expect(within(carte).queryByRole("textbox")).toBeNull();
    expect(within(carte).queryByRole("radio")).toBeNull();
    expect(within(carte).queryByRole("checkbox")).toBeNull();
    expect(within(carte).getByText(/Dites-le juste en dessous, avec vos mots/)).toBeVisible();

    await userEvent.click(gestes[0]);

    expect(repondre).toHaveBeenCalledWith(PROPOSITION_RETENUE, false);
    expect(PROPOSITION_RETENUE).toBe("retenue");
  });

  it("③ un outil absent du poste se dit sur sa ligne, par ses mots", () => {
    render(<PropositionDOutillage question={PROPOSITION} repondre={() => Promise.resolve()} />);

    const installation = lignes()[2].valeur;
    const raison = within(installation).getByText(/n'est pas installé sur ce poste/);
    expect(raison).toHaveClass("text-attention-texte");
    // Les autres raisons restent au ton secondaire.
    expect(within(lignes()[1].valeur).getByText(/Rapide/)).toHaveClass("text-texte-secondaire");
  });

  it("④ dit le refus du moteur", async () => {
    const repondre = vi.fn(() => Promise.reject(new Error("réponse refusée (422)")));
    render(<PropositionDOutillage question={PROPOSITION} repondre={repondre} />);

    await userEvent.click(screen.getByRole("button", { name: "Retenir cet outillage" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("réponse refusée (422)");
    expect(screen.getByRole("button", { name: "Retenir cet outillage" })).toBeEnabled();
  });

  it("⑤ dans une carte étroite, chaque élément passe sur deux lignes", () => {
    render(<PropositionDOutillage question={PROPOSITION} repondre={() => Promise.resolve()} />);

    // jsdom n'évalue pas les requêtes de conteneur : on lit les deux régimes sur les
    // classes. Étroite, une colonne et le sujet au-dessus ; large, la grille à deux.
    const liste = listeProposee();
    expect(liste).toHaveClass("flex-col", "@md:grid", "@md:grid-cols-[auto_minmax(0,1fr)]");
    expect(liste.parentElement).toHaveClass("@container");
    const groupe = liste.querySelector("dt")?.parentElement;
    expect(groupe).toHaveClass("flex-col", "@md:contents");
  });

  it("se distingue d'une question à options par ce que le moteur sert", () => {
    expect(estUneProposition(PROPOSITION)).toBe(true);
    expect(estUneProposition({ ...PROPOSITION, proposition: [] })).toBe(false);
    expect(estUneProposition({ ...PROPOSITION, proposition: undefined })).toBe(false);
  });
});

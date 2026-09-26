/**
 * La carte d'une **pièce d'outillage** : ce qui sera écrit, pourquoi, et le geste qui
 * en décide (#1161).
 *
 * La forme est la variante A retenue sur pièces par le regard neuf (commentaires
 * « Veille de conception » et « Variante retenue » du ticket) : le diff ouvert et
 * borné, l'en-tête `chemin · ce que le geste fera · +N −M`, le pourquoi en une ligne
 * — ou la phrase de la personne après une correction —, le verdict des commandes
 * légendé, et trois gestes nommés à leur portée. Ce que ce filet garde :
 *
 * ① un fichier **neuf** se lit comme un texte : tout y est ajout, le « + » en
 *   gouttière sans aplat, les 12 premières lignes ouvertes, le reste d'un geste — et
 *   « +N » compte exactement les lignes qu'on déplie (le regard neuf avait vu « +55 »
 *   à côté de « Voir les 56 lignes ») ;
 * ② une pièce **corrigée** porte la phrase de la personne, le diff condensé autour de
 *   ce qui change, et la liste des commandes **dépliée** — la commande revérifiée y
 *   est lue avec son verdict ;
 * ③ une version dont la commande corrigée a **échoué** ne s'offre pas à l'écriture :
 *   pas de « Écrire ce fichier », et la carte dit que rien n'a été écrit ;
 * ④ les trois gestes partent avec l'**empreinte** de la version montrée, et un refus
 *   de l'API se lit sur la carte ;
 * ⑤ `diffDeLaPiece` : ni ligne fantôme pour la fin de fichier, ni ligne « commune »
 *   dans un fichier qu'on crée.
 *
 * Aucun rendu jugé ici : c'est l'affaire de la relecture visuelle.
 */

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { PieceDOutillage } from "@/components/chat/PieceDOutillage";
import { diffDeLaPiece, LIGNES_OUVERTES } from "@/lib/outillage";
import type { PieceProposee, VerificationOutillage } from "@/lib/types";

/** Un `AGENTS.md` de 20 lignes, terminé par un saut de ligne comme tout fichier écrit. */
const AGENTS = Array.from({ length: 20 }, (_, i) => `ligne ${i + 1}`).join("\n") + "\n";

const DOTNET = "Nos tests tournent avec `dotnet test`";

function verdict(partiel: Partial<VerificationOutillage> = {}): VerificationOutillage {
  return {
    usage: "tester",
    commande: "npm test",
    etat: "verifiee",
    raison: "elle a rendu la main sans erreur",
    code: 0,
    sortie: "",
    duree_s: 0.4,
    ...partiel,
  };
}

function piece(partiel: Partial<PieceProposee> = {}): PieceProposee {
  return {
    projet_id: "prj-7f3a1c2b",
    projet_nom: "Dépensio",
    cible: "D:/projets/depensio",
    chemin: "AGENTS.md",
    nom: "AGENTS.md",
    nature: "instructions",
    raison: "le fichier d'instructions que tous les clients lisent",
    texte_avant: "",
    texte_apres: AGENTS,
    sort: "cree",
    verifications: [verdict()],
    correction: "",
    echec: "",
    ecrivable: true,
    empreinte: "sha256:aaaa",
    rang: 1,
    total: 6,
    regime: "en-place",
    ...partiel,
  };
}

/** La même pièce, déjà écrite, corrigée : la ligne des tests change. */
function pieceCorrigee(partiel: Partial<PieceProposee> = {}): PieceProposee {
  const avant = AGENTS.replace("ligne 10", "- **Tests** : `npm test`");
  const apres = AGENTS.replace(
    "ligne 10",
    `- **Tests** : \`dotnet test\` — dite par la personne (« ${DOTNET} »)`,
  );
  return piece({
    texte_avant: avant,
    texte_apres: apres,
    sort: "reecrit",
    correction: DOTNET,
    empreinte: "sha256:bbbb",
    verifications: [verdict({ commande: "dotnet test" })],
    ...partiel,
  });
}

function carte(): HTMLElement {
  return screen.getByRole("region", { name: "Pièce d'outillage à écrire" });
}

describe("la carte d'une pièce d'outillage", () => {
  it("① un fichier neuf : l'en-tête, le pourquoi, les 12 premières lignes puis le reste", async () => {
    render(<PieceDOutillage piece={piece()} trancher={vi.fn()} />);
    const region = carte();

    expect(within(region).getByText("Écrire cette pièce ?")).toBeInTheDocument();
    expect(within(region).getByText("Pièce 1 sur 6 · Dépensio")).toBeInTheDocument();
    expect(within(region).getByText("AGENTS.md")).toBeInTheDocument();
    expect(within(region).getByText("nouveau fichier")).toBeInTheDocument();
    // « +20 » : les 20 lignes du fichier, pas une de plus pour son saut de ligne final.
    expect(within(region).getByText("+20 −0")).toBeInTheDocument();
    expect(
      within(region).getByText("Le fichier d'instructions que tous les clients lisent."),
    ).toBeInTheDocument();
    expect(within(region).getByText("ligne 12")).toBeInTheDocument();
    expect(within(region).queryByText("ligne 13")).toBeNull();

    await userEvent.click(
      within(region).getByRole("button", { name: "Voir le fichier entier (20 lignes)" }),
    );

    expect(within(region).getByText("ligne 20")).toBeInTheDocument();
    // Un fichier qu'on crée se lit comme un texte : le signe dit l'ajout, sans aplat.
    const ligne = within(region).getByText("ligne 5").closest("p");
    expect(ligne?.className).not.toMatch(/bg-emerald/);
    expect(ligne?.textContent).toBe("+ligne 5");
  });

  it("② une pièce corrigée porte la phrase, le changement et les commandes rejouées", () => {
    render(<PieceDOutillage piece={pieceCorrigee()} trancher={vi.fn()} />);
    const region = carte();

    expect(within(region).getByText("fichier de Maestro modifié")).toBeInTheDocument();
    expect(within(region).getByText("+1 −1")).toBeInTheDocument();
    // La phrase de la personne a le poids du corps de texte : c'est la justification.
    const phrase = within(region).getByText(/Corrigée d'après votre demande/);
    expect(phrase.className).toMatch(/text-corps/);
    expect(phrase.textContent).toContain("Nos tests tournent avec dotnet test");
    // Le changement, entouré de son contexte ; le reste replié.
    expect(within(region).getAllByText(/lignes inchangées/).length).toBeGreaterThan(0);
    expect(within(region).getByText(/`npm test`/)).toBeInTheDocument();
    // La revérification se voit : la liste est dépliée, la commande corrigée y est.
    expect(
      within(region).getByText("Commandes, rejouées après votre correction"),
    ).toBeInTheDocument();
    expect(within(region).getAllByText("dotnet test").length).toBeGreaterThan(0);
  });

  it("③ une correction en échec ne s'offre pas à l'écriture, et le dit", () => {
    render(
      <PieceDOutillage
        piece={pieceCorrigee({
          ecrivable: false,
          echec: "`dotnet test` a échoué à l'exécution (elle a rendu la main en erreur (code 1)).",
          verifications: [
            verdict({
              commande: "dotnet test",
              etat: "echouee",
              raison: "elle a rendu la main en erreur (code 1)",
              code: 1,
              sortie: "MSB1003: aucun projet trouvé",
            }),
          ],
        })}
        trancher={vi.fn()}
      />,
    );
    const region = carte();

    expect(within(region).queryByRole("button", { name: "Écrire ce fichier" })).toBeNull();
    expect(within(region).getByRole("alert")).toHaveTextContent(
      "Rien n'a été écrit : dotnet test a échoué à l'exécution",
    );
    expect(within(region).getByText(/MSB1003/)).toBeInTheDocument();
    // Les deux autres issues restent : passer, ou tout remettre à plus tard.
    expect(within(region).getByRole("button", { name: "Pas cette pièce" })).toBeEnabled();
  });

  it("④ les trois gestes partent avec l'empreinte de la version montrée", async () => {
    const trancher = vi.fn(() => Promise.resolve());
    render(<PieceDOutillage piece={piece()} trancher={trancher} />);
    const region = carte();

    await userEvent.click(within(region).getByRole("button", { name: "Écrire ce fichier" }));
    await userEvent.click(within(region).getByRole("button", { name: "Pas cette pièce" }));
    await userEvent.click(
      within(region).getByRole("button", { name: "Remettre l'outillage à plus tard" }),
    );

    expect(trancher.mock.calls).toEqual([
      ["ecrire", "sha256:aaaa"],
      ["passer", "sha256:aaaa"],
      ["plus-tard", "sha256:aaaa"],
    ]);
  });

  it("④ un refus de l'API se lit sur la carte", async () => {
    const trancher = vi.fn(() =>
      Promise.reject(new Error("cette pièce n'attend plus de réponse — la conversation a repris.")),
    );
    render(<PieceDOutillage piece={piece()} trancher={trancher} />);

    await userEvent.click(within(carte()).getByRole("button", { name: "Écrire ce fichier" }));

    expect(await within(carte()).findByRole("alert")).toHaveTextContent(
      "cette pièce n'attend plus de réponse",
    );
  });

  it("dit qu'un projet versionné reçoit la pièce sur une branche fusionnée à l'accord", () => {
    render(<PieceDOutillage piece={piece({ regime: "branche" })} trancher={vi.fn()} />);

    expect(carte()).toHaveTextContent(
      "Ce projet est versionné : la pièce s'écrit sur une branche, fusionnée à votre accord.",
    );
  });
});

describe("diffDeLaPiece", () => {
  it("⑤ un fichier neuf : autant d'ajouts que de lignes, aucune ligne commune", () => {
    const diff = diffDeLaPiece(piece());

    expect(diff.neuf).toBe(true);
    expect(diff.ajouts).toBe(20);
    expect(diff.entrees).toHaveLength(20);
    expect(diff.entrees.every((e) => e.type === "ajout")).toBe(true);
  });

  it("⑤ une modification : comptée ligne à ligne, condensée autour du changement", () => {
    const diff = diffDeLaPiece(pieceCorrigee());

    expect(diff.neuf).toBe(false);
    expect([diff.ajouts, diff.retraits]).toEqual([1, 1]);
    expect(diff.entrees.some((e) => e.type === "repli")).toBe(true);
    expect(diff.entrees.length).toBeLessThan(LIGNES_OUVERTES);
  });
});

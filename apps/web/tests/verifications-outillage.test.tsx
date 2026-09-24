/**
 * Les cas limites du verdict des commandes écrites (#1160) — ce que les rapports
 * de l'étape de création et du fil ne montrent pas d'eux-mêmes.
 *
 * Les deux montages — la liste à plat de la page, le récapitulatif du fil — sont
 * éprouvés dans `etape-outillage.test.tsx` et `conclusion-outillage.test.tsx`.
 * Ici, ce qu'une API ou un texte du moteur peuvent envoyer de travers :
 *
 * - un **état inconnu** (une API plus récente que l'écran) se lit « à vérifier » :
 *   ne pas savoir ce qu'une commande vaut, c'est exactement ce que ce verdict dit ;
 * - un **accent grave orphelin** dans une raison reste du texte, jamais un bloc de
 *   code ouvert jusqu'à la fin de la phrase ;
 * - un **échec sans code** (arrêté avant d'en rendre un) le dit au lieu d'écrire
 *   « code null ».
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  CompteVerifications,
  LIGNES_DE_SORTIE,
  ListeVerifications,
  RecapitulatifVerifications,
  TexteAvecCode,
} from "@/components/projets/VerificationsOutillage";
import type { VerificationOutillage } from "@/lib/types";

function verification(partiel: Partial<VerificationOutillage>): VerificationOutillage {
  return {
    usage: "tester",
    commande: "pytest",
    etat: "verifiee",
    raison: "elle a rendu la main sans erreur",
    code: 0,
    sortie: "",
    duree_s: 1,
    ...partiel,
  };
}

describe("le verdict des commandes écrites", () => {
  it("lit un état inconnu comme « à vérifier », dans la liste comme dans le compte", () => {
    const verifications = [
      verification({ commande: "make", etat: "rejouee-plus-tard", raison: "un état futur" }),
    ];
    render(
      <>
        <CompteVerifications verifications={verifications} />
        <ListeVerifications verifications={verifications} />
      </>,
    );

    expect(screen.getByText(/1 commande/).closest("p")).toHaveTextContent(
      "1 commande écrite dans l'outillage : 1 à vérifier.",
    );
    const ligne = screen.getByRole("listitem");
    expect(ligne).toHaveTextContent("à vérifier");
    expect(ligne).toHaveTextContent("un état futur");
  });

  it("garde en texte un accent grave orphelin", () => {
    const { container } = render(<TexteAvecCode texte="`a` puis `b et la suite" />);

    expect([...container.querySelectorAll("code")].map((c) => c.textContent)).toEqual([
      "a",
    ]);
    expect(container).toHaveTextContent("a puis `b et la suite");
  });

  it("dit qu'un échec n'a pas rendu de code plutôt que d'écrire « code null »", () => {
    render(
      <ListeVerifications
        verifications={[verification({ etat: "echouee", code: null, sortie: "coupé" })]}
      />,
    );

    expect(screen.getByRole("listitem")).toHaveTextContent("sans code de retour");
    expect(screen.getByRole("listitem")).not.toHaveTextContent("null");
  });

  it("montre la fin d'une longue sortie, sans zone qui défile, et dit où lire le reste", () => {
    const sortie = Array.from({ length: 30 }, (_, i) => `ligne ${i + 1}`).join("\n");
    render(
      <ListeVerifications
        verifications={[verification({ etat: "echouee", code: 2, sortie })]}
      />,
    );

    const bloc = screen.getByText(/ligne 30/);
    expect(bloc.tagName).toBe("PRE");
    expect(bloc).toHaveTextContent(`ligne ${30 - LIGNES_DE_SORTIE + 1}`);
    expect(bloc).not.toHaveTextContent(/ligne 1\b/);
    expect(bloc.className).not.toMatch(/overflow/);
    expect(screen.getByRole("listitem")).toHaveTextContent(
      ".maestro/outillage/manifeste.json",
    );
  });

  it("ne compte que les verdicts présents dans le récapitulatif du fil", () => {
    render(
      <RecapitulatifVerifications
        verifications={[verification({}), verification({ commande: "ruff check ." })]}
      />,
    );

    expect(screen.getByText("2 vérifiées")).toBeInTheDocument();
    expect(screen.queryByText(/échouée/)).toBeNull();
    expect(screen.queryByText(/à vérifier/)).toBeNull();
  });
});

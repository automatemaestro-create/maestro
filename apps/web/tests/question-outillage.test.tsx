/**
 * La carte de la question d'outillage : **répondre avec ses mots** (#1147).
 *
 * Le défaut du ticket, vu de l'écran : « Quelle sorte de projet est-ce ? » n'offrait
 * que quatre natures, et la carte n'avait **aucune sortie**. La variante retenue sur
 * pièces (commentaire « Variante retenue » de #1147, d'après le contrat
 * `AskUserQuestion` du Claude Agent SDK) : les options que Maestro a écrites pour ce
 * projet, puis **« Autre chose »**, qui ouvre un champ. Ce que ce filet garde :
 *
 * ① une question **sans options** — la première — ne montre que le champ, avec un
 *   libellé visible, et le geste reste désarmé tant que rien n'est écrit ;
 * ② sur une question à options, « Autre chose » est **la dernière ligne**, une
 *   seule ligne est sélectionnée à la fois, et le texte part comme **la** réponse
 *   (`libre`) — jamais à côté d'une option restée cochée ;
 * ③ ce que #1031 a tranché tient : la recommandée en tête, présélectionnée, badge,
 *   « Garder ce choix » — et le plafond « question N sur 6 » a disparu ;
 * ④ « Ce que j'ai compris » se lit en tête, et au pied du fil la carte dit que la
 *   zone de saisie répond aussi.
 *
 * Aucun rendu jugé ici : c'est l'affaire de la relecture visuelle.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { QuestionDOutillage } from "@/components/chat/QuestionDOutillage";
import type { ChoixOutillage, QuestionOutillage } from "@/lib/types";

const OUVERTE: QuestionOutillage = {
  cle: "nature",
  intitule: "Qu'est-ce que ce projet ?",
  options: [],
  recommande: "",
  pourquoi: "Dites-le avec vos mots.",
  rang: 1,
};

const FORGE: QuestionOutillage = {
  cle: "forge",
  intitule: "Où le code vivra-t-il ?",
  options: [
    { valeur: "gitlab", libelle: "GitLab", raison: "Merge Requests et GitLab CI" },
    { valeur: "github", libelle: "GitHub", raison: "Pull Requests et Actions" },
  ],
  recommande: "github",
  pourquoi: "vous comptez publier l'application",
  rang: 2,
};

const COMPRIS: ChoixOutillage[] = [
  { cle: "langages", valeur: "Dart", deduit: true, parce_que: "Flutter", sujet: "langage" },
  { cle: "tester", valeur: "flutter test", deduit: true, parce_que: "SDK", sujet: "tests" },
];

describe("la question d'outillage", () => {
  it("① une question sans options ne montre que le champ, libellé visible", async () => {
    const repondre = vi.fn(() => Promise.resolve());
    render(<QuestionDOutillage question={OUVERTE} repondre={repondre} />);

    expect(screen.queryByRole("radiogroup")).toBeNull();
    expect(screen.queryByText("Autre chose")).toBeNull();
    // Le libellé se lit à l'écran, pas seulement dans le texte indicatif.
    expect(screen.getByText("Votre réponse")).toBeVisible();
    const envoyer = screen.getByRole("button", { name: "Envoyer ma réponse" });
    expect(envoyer).toBeDisabled();

    await userEvent.type(
      screen.getByRole("textbox", { name: "Votre réponse" }),
      "  Une infra Terraform  ",
    );
    await userEvent.click(envoyer);

    expect(repondre).toHaveBeenCalledWith("Une infra Terraform", true);
  });

  it("② « Autre chose » est la dernière ligne et envoie le texte comme la réponse", async () => {
    const repondre = vi.fn(() => Promise.resolve());
    render(<QuestionDOutillage question={FORGE} repondre={repondre} />);

    const lignes = screen.getAllByRole("radio");
    expect(lignes.map((l) => l.textContent)).toEqual([
      expect.stringContaining("GitHub"),
      expect.stringContaining("GitLab"),
      expect.stringContaining("Autre chose"),
    ]);
    // Fermée, la carte n'a pas de champ : une ligne de plus, rien d'autre.
    expect(screen.queryByRole("textbox")).toBeNull();

    await userEvent.click(lignes[2]);

    // Une seule réponse à la fois : la recommandée n'est plus cochée.
    expect(
      screen.getAllByRole("radio").filter((l) => l.getAttribute("aria-checked") === "true"),
    ).toHaveLength(1);
    expect(lignes[2]).toHaveAttribute("aria-checked", "true");
    const champ = screen.getByRole("textbox", { name: "Votre réponse, avec vos mots" });
    const envoyer = screen.getByRole("button", { name: "Envoyer ma réponse" });
    expect(envoyer).toBeDisabled();

    await userEvent.type(champ, "Sur Bitbucket, chez le client");
    await userEvent.click(envoyer);

    expect(repondre).toHaveBeenCalledWith("Sur Bitbucket, chez le client", true);
  });

  it("③ la recommandée en tête, présélectionnée, et plus de plafond annoncé", async () => {
    const repondre = vi.fn(() => Promise.resolve());
    render(<QuestionDOutillage question={FORGE} repondre={repondre} />);

    const [premiere] = screen.getAllByRole("radio");
    expect(premiere).toHaveAttribute("aria-checked", "true");
    expect(premiere).toHaveTextContent("Recommandé");
    expect(premiere).toHaveTextContent("vous comptez publier l'application");
    expect(screen.queryByText(/question \d+ sur/)).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "Garder ce choix" }));
    expect(repondre).toHaveBeenLastCalledWith("github", false);

    await userEvent.click(screen.getAllByRole("radio")[1]);
    await userEvent.click(screen.getByRole("button", { name: "Retenir ce choix" }));
    expect(repondre).toHaveBeenLastCalledWith("gitlab", false);
  });

  it("④ dit ce que Maestro a compris, et au pied du fil que la saisie répond aussi", () => {
    render(
      <QuestionDOutillage
        question={FORGE}
        compris={COMPRIS}
        repondre={() => Promise.resolve()}
        depuisLeFil
      />,
    );

    expect(screen.getByText(/Ce que j'ai compris/).parentElement).toHaveTextContent(
      "Ce que j'ai compris : langage : Dart · tests : flutter test",
    );
    expect(
      screen.getByText(/répondre dans la zone de saisie/),
    ).toBeInTheDocument();
  });

  it("④ hors du fil, elle ne renvoie à aucune zone de saisie", () => {
    render(<QuestionDOutillage question={FORGE} repondre={() => Promise.resolve()} />);

    expect(screen.queryByText(/zone de saisie/)).toBeNull();
    expect(screen.queryByText(/Ce que j'ai compris/)).toBeNull();
  });

  it("dit le refus du moteur sans perdre ce qui a été écrit", async () => {
    const repondre = vi.fn(() => Promise.reject(new Error("réponse refusée (422)")));
    render(<QuestionDOutillage question={OUVERTE} repondre={repondre} />);

    await userEvent.type(screen.getByRole("textbox", { name: "Votre réponse" }), "x");
    await userEvent.click(screen.getByRole("button", { name: "Envoyer ma réponse" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("réponse refusée (422)");
    expect(screen.getByRole("textbox", { name: "Votre réponse" })).toHaveValue("x");
  });
});

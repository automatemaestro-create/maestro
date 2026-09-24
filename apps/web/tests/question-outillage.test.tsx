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

import { render, screen, within } from "@testing-library/react";
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
  {
    cle: "tester",
    valeur: "flutter test",
    deduit: true,
    parce_que: "SDK",
    sujet: "tests",
    commande: true,
  },
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

    // Une entrée par constat — le sujet et sa valeur ensemble, jamais coupés l'un de
    // l'autre (constat du regard neuf sur la vraie stack) —, la valeur en relief.
    // Courte, la liste se lit dépliée **partout** : un seul rendu, sans second
    // niveau — la troisième relecture a vu un seul constat replié dans la colonne,
    // lu ouvert au même moment sur /chat.
    const compris = screen.getByRole("list", { name: "Ce que j'ai compris" });
    const entrees = within(compris).getAllByRole("listitem");
    expect(entrees.map((e) => e.textContent)).toEqual(["langage Dart", "tests flutter test"]);
    expect(within(entrees[0]).getByText("Dart")).toHaveClass("font-medium");
    // Une commande se lit en chasse fixe, comme dans les options (relecture de
    // #1147 : « dart format . » en romain gras se lisait comme une fin de phrase).
    const commande = within(entrees[1]).getByText("flutter test");
    expect(commande.tagName).toBe("CODE");
    expect(commande).toHaveClass("font-mono");
    expect(document.querySelector("details")).toBeNull();
    expect(
      screen.getByText(/répondre dans la zone de saisie/),
    ).toBeInTheDocument();
  });

  it("④ une longue liste passe à un second niveau dans une carte étroite seulement", () => {
    const long: ChoixOutillage[] = ["langages", "manifeste", "installer", "tester", "lint"].map(
      (cle) => ({ cle, valeur: `v-${cle}`, deduit: true, parce_que: "", sujet: cle }),
    );
    render(
      <QuestionDOutillage question={FORGE} compris={long} repondre={() => Promise.resolve()} />,
    );

    // Deux rendus du même contenu, que la largeur de la **carte** départage (jsdom
    // ne l'évalue pas) : déplié dans une carte large, derrière un second niveau dans
    // une carte étroite — la colonne de conversation, où la carte dépassait le fil.
    const [large, etroite] = screen.getAllByRole("list", { name: "Ce que j'ai compris" });
    for (const liste of [large, etroite]) {
      expect(within(liste).getAllByRole("listitem")).toHaveLength(5);
    }
    expect(large.parentElement).toHaveClass("@md:flex");
    const second = etroite.closest("details");
    expect(second).toHaveClass("@md:hidden");
    expect(second?.querySelector("summary")).toHaveTextContent(
      "Ce que j'ai compris · 5 constats",
    );
  });

  it("⑤ montre la valeur concrète d'une option quand son nom ne la dit pas", () => {
    // Vu sur la vraie stack : « Quelle commande de build ? » coiffait « Android
    // uniquement » — la commande qui serait écrite n'était lisible nulle part.
    render(
      <QuestionDOutillage
        question={{
          ...FORGE,
          intitule: "Quelle commande de build ?",
          options: [
            { valeur: "flutter build apk", libelle: "Android uniquement", raison: "un magasin" },
            { valeur: "GitHub", libelle: "GitHub", raison: "la forge" },
            { valeur: "conventional-commits", libelle: "Conventional Commits", raison: "" },
            { valeur: "aucun", libelle: "Aucun manifeste", raison: "rien à déclarer" },
            { valeur: "aucune", libelle: "Pas de tests pour l'instant", raison: "" },
          ],
          recommande: "flutter build apk",
        }}
        repondre={() => Promise.resolve()}
      />,
    );

    const [android, github, conventions, manifeste, tests] = screen.getAllByRole("radio");
    expect(within(android).getByText("flutter build apk")).toHaveClass("font-mono");
    expect(github.querySelector(".font-mono")).toBeNull();
    expect(conventions.querySelector(".font-mono")).toBeNull();
    // « aucun » n'est pas une valeur à lire : le nom dit déjà qu'il n'y a rien
    // (troisième relecture : un « aucun » gris à la place d'une commande).
    expect(manifeste.querySelector(".font-mono")).toBeNull();
    expect(tests.querySelector(".font-mono")).toBeNull();
  });

  it("④ hors du fil, elle ne renvoie à aucune zone de saisie", () => {
    render(<QuestionDOutillage question={FORGE} repondre={() => Promise.resolve()} />);

    expect(screen.queryByText(/zone de saisie/)).toBeNull();
    expect(screen.queryByText(/Ce que j'ai compris/)).toBeNull();
  });

  it("② ouvrir « Autre chose » amène le champ et son geste sous les yeux", async () => {
    // Vu sur la vraie stack : le champ agrandit la carte sous le bas du fil, et le
    // bouton d'envoi passait sous la zone de saisie tant qu'on ne faisait pas défiler.
    const vus: Element[] = [];
    const avant = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = function (this: Element) {
      vus.push(this);
    };
    try {
      render(<QuestionDOutillage question={FORGE} repondre={() => Promise.resolve()} />);
      expect(vus).toHaveLength(0);

      await userEvent.click(screen.getAllByRole("radio")[2]);

      const geste = screen.getByRole("button", { name: "Envoyer ma réponse" });
      expect(vus.some((el) => el.contains(geste))).toBe(true);
    } finally {
      Element.prototype.scrollIntoView = avant;
    }
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

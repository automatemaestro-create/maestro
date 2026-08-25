/**
 * Lot 7 de la refonte UX (#123) : l'assistant flottant.
 *
 * Le lot repose sur une contrainte de fond — **aider sans gêner** : le bouton
 * ne doit masquer aucune action de la page, et le panneau ne doit pas se
 * refermer au premier clic ailleurs, puisqu'on le consulte *pendant* qu'on agit
 * sur la page. C'est ce qui le distingue des menus de la barre supérieure
 * (thème, notifications, aide), qui eux se ferment au clic extérieur — une
 * différence délibérée, donc facile à « corriger » par erreur : elle est tenue
 * ici par un test.
 *
 * Le reste couvre le fil lui-même : accueil et amorces sur conversation vide,
 * envoi au clavier, et le rattrapage d'un envoi qui échoue.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { AssistantFlottant } from "@/components/AssistantFlottant";
import { MenuAide } from "@/components/MenuAide";
import {
  ACCUEIL_ASSISTANCE,
  AGENT_ASSISTANCE,
  AMORCES_ASSISTANCE,
  ecouterOuvertureAssistant,
  ouvrirAssistant,
} from "@/lib/assistance";

import { messageFactice, poserFilAssistance } from "./aides";

const ouvrirPanneau = async (utilisateur: ReturnType<typeof userEvent.setup>) => {
  await utilisateur.click(
    screen.getByRole("button", { name: "Ouvrir l'assistant" }),
  );
  return screen.getByRole("region", { name: "Assistant de la Control Tower" });
};

describe("le canal d'assistance (lib/assistance)", () => {
  it("porte un nom de fil réservé côté backend", () => {
    expect(AGENT_ASSISTANCE).toBe("assistance");
  });

  it("propose des questions d'amorce", () => {
    // Elles montrent le périmètre de l'assistant mieux qu'une explication, et
    // évitent la page blanche du premier usage.
    expect(AMORCES_ASSISTANCE.length).toBeGreaterThan(0);
    for (const amorce of AMORCES_ASSISTANCE) expect(amorce).not.toBe("");
  });

  it("porte les demandes d'ouverture à qui veut les entendre", () => {
    let ouvertures = 0;
    const detacher = ecouterOuvertureAssistant(() => (ouvertures += 1));
    ouvrirAssistant();
    detacher();
    ouvrirAssistant();
    expect(ouvertures).toBe(1);
  });
});

describe("le bouton flottant (AssistantFlottant)", () => {
  it("attend d'être sollicité", () => {
    render(<AssistantFlottant />);
    expect(
      screen.getByRole("button", { name: "Ouvrir l'assistant" }),
    ).toHaveAttribute("aria-expanded", "false");
    expect(
      screen.queryByRole("region", { name: "Assistant de la Control Tower" }),
    ).not.toBeInTheDocument();
  });

  it("ouvre puis referme le panneau depuis son en-tête", async () => {
    const utilisateur = userEvent.setup();
    render(<AssistantFlottant />);

    const panneau = await ouvrirPanneau(utilisateur);
    await utilisateur.click(
      within(panneau).getByRole("button", { name: "Fermer l'assistant" }),
    );
    expect(
      screen.queryByRole("region", { name: "Assistant de la Control Tower" }),
    ).not.toBeInTheDocument();
  });

  it("referme aussi le panneau par le bouton flottant", async () => {
    // Ouvert, le bouton flottant devient une croix : c'est la même commande,
    // et son étiquette doit suivre son état.
    const utilisateur = userEvent.setup();
    render(<AssistantFlottant />);
    await ouvrirPanneau(utilisateur);

    // Par le nom accessible et non par un `title` : le second a disparu avec
    // #536, il ne faisait que redoubler le premier. Ouvert, deux boutons
    // portent ce nom — la croix de l'en-tête du panneau et le bouton flottant,
    // qui font la même chose ; on vise celui qui vit **hors** du panneau.
    const panneau = screen.getByRole("region", {
      name: "Assistant de la Control Tower",
    });
    const flottant = screen
      .getAllByRole("button", { name: "Fermer l'assistant" })
      .find((bouton) => !panneau.contains(bouton));
    expect(flottant).toBeDefined();
    expect(flottant).toHaveAttribute("aria-expanded", "true");
    await utilisateur.click(flottant!);
    expect(
      screen.queryByRole("region", { name: "Assistant de la Control Tower" }),
    ).not.toBeInTheDocument();
  });

  it("s'ouvre sur demande du menu d'aide, sans le connaître", async () => {
    const utilisateur = userEvent.setup();
    render(
      <>
        <MenuAide />
        <AssistantFlottant />
      </>,
    );

    await utilisateur.click(screen.getByRole("button", { name: "Aide" }));
    await utilisateur.click(
      screen.getByRole("menuitem", { name: /Poser une question/ }),
    );
    expect(
      screen.getByRole("region", { name: "Assistant de la Control Tower" }),
    ).toBeInTheDocument();
  });

  it("se ferme sur Échap en rendant le focus au bouton", async () => {
    const utilisateur = userEvent.setup();
    render(<AssistantFlottant />);
    await ouvrirPanneau(utilisateur);

    await utilisateur.keyboard("{Escape}");
    expect(
      screen.queryByRole("region", { name: "Assistant de la Control Tower" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Ouvrir l'assistant" }),
    ).toHaveFocus();
  });

  it("reste ouvert quand on agit ailleurs dans la page", async () => {
    // La différence délibérée avec les menus de la barre supérieure : on
    // consulte l'assistant *en même temps* qu'on travaille.
    const utilisateur = userEvent.setup();
    render(
      <>
        <AssistantFlottant />
        <button type="button">action de la page</button>
      </>,
    );
    await ouvrirPanneau(utilisateur);

    await utilisateur.click(
      screen.getByRole("button", { name: "action de la page" }),
    );
    expect(
      screen.getByRole("region", { name: "Assistant de la Control Tower" }),
    ).toBeInTheDocument();
  });
});

describe("le panneau d'assistance", () => {
  it("accueille sur un fil vide, sans rien écrire côté backend", async () => {
    const utilisateur = userEvent.setup();
    render(<AssistantFlottant />);
    const panneau = await ouvrirPanneau(utilisateur);

    expect(within(panneau).getByText(ACCUEIL_ASSISTANCE)).toBeInTheDocument();
    for (const amorce of AMORCES_ASSISTANCE) {
      expect(within(panneau).getByRole("button", { name: amorce })).toBeInTheDocument();
    }
  });

  it("ouvre prêt à recevoir la question", async () => {
    const utilisateur = userEvent.setup();
    render(<AssistantFlottant />);
    await ouvrirPanneau(utilisateur);
    expect(
      screen.getByRole("textbox", { name: "Question à l'assistant" }),
    ).toHaveFocus();
  });

  it("envoie l'amorce qu'on choisit", async () => {
    const utilisateur = userEvent.setup();
    const envoyer = vi.fn().mockResolvedValue(undefined);
    poserFilAssistance({ envoyer });
    render(<AssistantFlottant />);
    const panneau = await ouvrirPanneau(utilisateur);

    await utilisateur.click(
      within(panneau).getByRole("button", { name: AMORCES_ASSISTANCE[0] }),
    );
    expect(envoyer).toHaveBeenCalledWith(AMORCES_ASSISTANCE[0]);
  });

  it("envoie la question saisie sur Entrée", async () => {
    const utilisateur = userEvent.setup();
    const envoyer = vi.fn().mockResolvedValue(undefined);
    poserFilAssistance({ envoyer });
    render(<AssistantFlottant />);
    await ouvrirPanneau(utilisateur);

    await utilisateur.type(
      screen.getByRole("textbox", { name: "Question à l'assistant" }),
      "Où voir les coûts ?{Enter}",
    );
    expect(envoyer).toHaveBeenCalledWith("Où voir les coûts ?");
  });

  it("garde Maj+Entrée pour aller à la ligne", async () => {
    const utilisateur = userEvent.setup();
    const envoyer = vi.fn().mockResolvedValue(undefined);
    poserFilAssistance({ envoyer });
    render(<AssistantFlottant />);
    await ouvrirPanneau(utilisateur);

    const saisie = screen.getByRole("textbox", { name: "Question à l'assistant" });
    await utilisateur.type(saisie, "première ligne{Shift>}{Enter}{/Shift}suite");
    expect(envoyer).not.toHaveBeenCalled();
    expect(saisie).toHaveValue("première ligne\nsuite");
  });

  it("refuse d'envoyer une question vide", async () => {
    const utilisateur = userEvent.setup();
    const envoyer = vi.fn().mockResolvedValue(undefined);
    poserFilAssistance({ envoyer });
    render(<AssistantFlottant />);
    const panneau = await ouvrirPanneau(utilisateur);

    expect(within(panneau).getByRole("button", { name: "Envoyer" })).toBeDisabled();
    await utilisateur.type(
      screen.getByRole("textbox", { name: "Question à l'assistant" }),
      "   {Enter}",
    );
    expect(envoyer).not.toHaveBeenCalled();
  });

  it("rend la question quand l'envoi échoue", async () => {
    // Relancer doit rester un simple Entrée : le texte revient dans la zone de
    // saisie plutôt que d'être perdu.
    const utilisateur = userEvent.setup();
    const envoyer = vi.fn().mockRejectedValue(new Error("assistant indisponible"));
    poserFilAssistance({ envoyer });
    render(<AssistantFlottant />);
    await ouvrirPanneau(utilisateur);

    const saisie = screen.getByRole("textbox", { name: "Question à l'assistant" });
    await utilisateur.type(saisie, "Une question{Enter}");

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("assistant indisponible"),
    );
    expect(saisie).toHaveValue("Une question");
  });

  it("montre la conversation, l'utilisateur et l'assistant distingués", async () => {
    const utilisateur = userEvent.setup();
    poserFilAssistance({
      messages: [
        messageFactice({ auteur: "utilisateur", contenu: "Où voir les coûts ?" }),
        messageFactice({
          auteur: "assistance",
          contenu: "Dans « Coûts & analytics ».",
        }),
      ],
    });
    render(<AssistantFlottant />);
    const panneau = await ouvrirPanneau(utilisateur);

    const fil = within(panneau).getByRole("list", {
      name: "Échanges avec l'assistant",
    });
    expect(within(fil).getAllByRole("listitem")).toHaveLength(2);
    expect(within(fil).getByText("Où voir les coûts ?")).toBeInTheDocument();
    expect(within(fil).getByText("Dans « Coûts & analytics ».")).toBeInTheDocument();
    // Conversation entamée : l'accueil et les amorces s'effacent.
    expect(within(panneau).queryByText(ACCUEIL_ASSISTANCE)).not.toBeInTheDocument();
  });

  it("dit que l'assistant rédige sa réponse", async () => {
    const utilisateur = userEvent.setup();
    poserFilAssistance({ envoi: true });
    render(<AssistantFlottant />);
    const panneau = await ouvrirPanneau(utilisateur);
    expect(within(panneau).getByText("L'assistant répond…")).toBeInTheDocument();
  });

  it("signale un fil illisible sans se refermer", async () => {
    const utilisateur = userEvent.setup();
    poserFilAssistance({ erreur: "API injoignable" });
    render(<AssistantFlottant />);
    const panneau = await ouvrirPanneau(utilisateur);

    expect(within(panneau).getByRole("alert")).toHaveTextContent("API injoignable");
    expect(
      within(panneau).getByRole("textbox", { name: "Question à l'assistant" }),
    ).toBeInTheDocument();
  });

  it("annonce la reconnexion quand le temps réel est coupé", async () => {
    const utilisateur = userEvent.setup();
    poserFilAssistance({ connecte: false });
    render(<AssistantFlottant />);
    const panneau = await ouvrirPanneau(utilisateur);
    expect(within(panneau).getByText("Reconnexion…")).toBeInTheDocument();
  });
});

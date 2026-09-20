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
 *
 * ⚠ **Sous `FournisseurEtatGlobal` depuis #945**, parce que le panneau monte
 * désormais `Conversation` — le fil du produit — et qu'une bulle y lit l'état
 * temps réel du projet pour dire ce qu'un message a rattaché (`Suite`). Ce n'est
 * pas une contrainte nouvelle du panneau : il vit dans le shell, qui fournit cet
 * état partout ; seul ce test le rendait hors de lui.
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { AssistantFlottant } from "@/components/AssistantFlottant";
import { AMORCE_NOWRAP } from "@/components/Conversation";
import { FilChat } from "@/components/FilChat";
import { MenuAide } from "@/components/MenuAide";
import {
  ACCUEIL_ASSISTANCE,
  AGENT_ASSISTANCE,
  AMORCES_ASSISTANCE,
  ecouterOuvertureAssistant,
  ouvrirAssistant,
} from "@/lib/assistance";

import { messageFactice, poserFilAssistance, rendreAvecEtat } from "./aides";

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
    rendreAvecEtat(<AssistantFlottant />);
    expect(
      screen.getByRole("button", { name: "Ouvrir l'assistant" }),
    ).toHaveAttribute("aria-expanded", "false");
    expect(
      screen.queryByRole("region", { name: "Assistant de la Control Tower" }),
    ).not.toBeInTheDocument();
  });

  it("ouvre puis referme le panneau depuis son en-tête", async () => {
    const utilisateur = userEvent.setup();
    rendreAvecEtat(<AssistantFlottant />);

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
    rendreAvecEtat(<AssistantFlottant />);
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
    rendreAvecEtat(
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
    rendreAvecEtat(<AssistantFlottant />);
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
    rendreAvecEtat(
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
    rendreAvecEtat(<AssistantFlottant />);
    const panneau = await ouvrirPanneau(utilisateur);

    expect(within(panneau).getByText(ACCUEIL_ASSISTANCE)).toBeInTheDocument();
    for (const amorce of AMORCES_ASSISTANCE) {
      expect(within(panneau).getByRole("button", { name: amorce })).toBeInTheDocument();
    }
  });

  it("interdit à chaque amorce de s'envelopper sur elle-même (#908)", async () => {
    // Même règle que le composeur (`composeur.test.tsx` ⑪) : le marqueur est
    // sur chaque bouton, et c'est le groupe qui enveloppe. Le panneau fait
    // 343 px à 375 px, ce qui tient avec des libellés au calibre — la sonde
    // lit le contrat écrit, jsdom n'enveloppant rien (#308).
    const utilisateur = userEvent.setup();
    rendreAvecEtat(<AssistantFlottant />);
    const panneau = await ouvrirPanneau(utilisateur);
    for (const amorce of AMORCES_ASSISTANCE) {
      const bouton = within(panneau).getByRole("button", { name: amorce });
      expect(bouton.classList.contains(AMORCE_NOWRAP)).toBe(true);
      expect(bouton.parentElement?.className.split(/\s+/)).toEqual(
        expect.arrayContaining(["flex", "flex-wrap"]),
      );
    }
  });

  it("ouvre prêt à recevoir la question", async () => {
    const utilisateur = userEvent.setup();
    rendreAvecEtat(<AssistantFlottant />);
    await ouvrirPanneau(utilisateur);
    expect(
      screen.getByRole("textbox", { name: "Message à l'assistant" }),
    ).toHaveFocus();
  });

  it("monte le composeur du chat, et non un second qui lui ressemble (#945)", async () => {
    // Le critère du ticket, pris au mot : « le composeur de l'assistant est **celui du
    // chat** ». On ne le vérifie donc pas en relisant des classes — un second composeur
    // aligné à la main les aurait aussi, le jour où on l'aligne — mais en **comparant**
    // le panneau à l'onglet Chat d'un agent, qui monte le même `Conversation`. Deux
    // rendus identiques ne peuvent plus diverger ; deux listes de classes recopiées, si.
    const utilisateur = userEvent.setup();
    rendreAvecEtat(
      <>
        <FilChat agent="dev" />
        <AssistantFlottant />
      </>,
    );
    const panneau = await ouvrirPanneau(utilisateur);
    const ongletAgent = screen.getByRole("region", { name: "Chat avec dev" });

    const envoiPanneau = within(panneau).getByRole("button", { name: "Envoyer" });
    const envoiAgent = within(ongletAgent).getByRole("button", { name: "Envoyer" });
    expect(envoiPanneau.className).toBe(envoiAgent.className);

    // Et le cadre de saisie avec lui : c'est le composeur entier qui est partagé, pas
    // seulement son bouton.
    const cadre = (racine: HTMLElement) =>
      racine.querySelector("textarea")?.parentElement?.parentElement?.className;
    expect(cadre(panneau)).toBe(cadre(ongletAgent));
  });

  it("porte son action primaire sur la couleur d'accent du socle (#945)", async () => {
    // C6 du retex du 2026-09-11 : « Envoyer » était `bg-sky-600` quand la couleur
    // d'action du produit est `--accent` (emerald-700). Il n'est plus écrit ici du
    // tout — il vient de `Bouton`, dont le ton par défaut est `accent` —, et c'est ce
    // qui fait que ce fichier est sorti du résidu de `couleurs.test.ts` (30 → 0).
    const utilisateur = userEvent.setup();
    rendreAvecEtat(<AssistantFlottant />);
    const panneau = await ouvrirPanneau(utilisateur);

    const envoi = within(panneau).getByRole("button", { name: "Envoyer" });
    expect(envoi.className).toContain("bg-accent");
    expect(panneau.innerHTML).not.toMatch(/-sky-/);
  });

  it("ne réserve pas dans sa propre carte la bande du bouton flottant (#945)", async () => {
    // La seule chose que le panneau demande au composeur de faire autrement, et elle
    // n'est pas une préférence : la bande (`bottom-16`, #726) réserve la place du bouton
    // flottant là où il **recouvre** le fil. Ce panneau est la carte que ce bouton ouvre
    // et se tient au-dessus de lui — rien à réserver. Les deux sens sont tenus ici :
    // le panneau s'en passe, et l'onglet d'un agent la garde.
    const utilisateur = userEvent.setup();
    rendreAvecEtat(
      <>
        <FilChat agent="dev" />
        <AssistantFlottant />
      </>,
    );
    const panneau = await ouvrirPanneau(utilisateur);
    const ongletAgent = screen.getByRole("region", { name: "Chat avec dev" });

    const composeur = panneau.querySelector("form");
    expect(composeur?.className).toContain("bottom-0");
    expect(composeur?.className).not.toContain("bottom-16");
    expect(ongletAgent.querySelector("form")?.className).toContain("bottom-16");
  });

  it("envoie l'amorce qu'on choisit", async () => {
    const utilisateur = userEvent.setup();
    const envoyer = vi.fn().mockResolvedValue(undefined);
    poserFilAssistance({ envoyer });
    rendreAvecEtat(<AssistantFlottant />);
    const panneau = await ouvrirPanneau(utilisateur);

    await utilisateur.click(
      within(panneau).getByRole("button", { name: AMORCES_ASSISTANCE[0] }),
    );
    // Deux arguments depuis #945 : le panneau monte le composeur du produit, dont
    // l'envoi porte le texte **et** les sources jointes (ici aucune). Le canal
    // d'aide les accepte comme les autres fils — c'est une décision de l'API, qui
    // refuse de faire dépendre un 422 du nom du fil (`app.py`).
    expect(envoyer).toHaveBeenCalledWith(AMORCES_ASSISTANCE[0], []);
  });

  it("envoie la question saisie sur Entrée", async () => {
    const utilisateur = userEvent.setup();
    const envoyer = vi.fn().mockResolvedValue(undefined);
    poserFilAssistance({ envoyer });
    rendreAvecEtat(<AssistantFlottant />);
    await ouvrirPanneau(utilisateur);

    await utilisateur.type(
      screen.getByRole("textbox", { name: "Message à l'assistant" }),
      "Où voir les coûts ?{Enter}",
    );
    expect(envoyer).toHaveBeenCalledWith("Où voir les coûts ?", []);
  });

  it("garde Maj+Entrée pour aller à la ligne", async () => {
    const utilisateur = userEvent.setup();
    const envoyer = vi.fn().mockResolvedValue(undefined);
    poserFilAssistance({ envoyer });
    rendreAvecEtat(<AssistantFlottant />);
    await ouvrirPanneau(utilisateur);

    const saisie = screen.getByRole("textbox", { name: "Message à l'assistant" });
    await utilisateur.type(saisie, "première ligne{Shift>}{Enter}{/Shift}suite");
    expect(envoyer).not.toHaveBeenCalled();
    expect(saisie).toHaveValue("première ligne\nsuite");
  });

  it("refuse d'envoyer une question vide", async () => {
    const utilisateur = userEvent.setup();
    const envoyer = vi.fn().mockResolvedValue(undefined);
    poserFilAssistance({ envoyer });
    rendreAvecEtat(<AssistantFlottant />);
    const panneau = await ouvrirPanneau(utilisateur);

    expect(within(panneau).getByRole("button", { name: "Envoyer" })).toBeDisabled();
    await utilisateur.type(
      screen.getByRole("textbox", { name: "Message à l'assistant" }),
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
    rendreAvecEtat(<AssistantFlottant />);
    await ouvrirPanneau(utilisateur);

    const saisie = screen.getByRole("textbox", { name: "Message à l'assistant" });
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
    rendreAvecEtat(<AssistantFlottant />);
    const panneau = await ouvrirPanneau(utilisateur);

    // Les libellés du fil **dérivent de l'interlocuteur** depuis #945, comme sur
    // toutes les surfaces de conversation du produit : un lecteur d'écran entend
    // le même nom partout, au lieu d'un vocabulaire par panneau.
    const fil = within(panneau).getByRole("list", {
      name: "Messages échangés avec l'assistant",
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
    rendreAvecEtat(<AssistantFlottant />);
    const panneau = await ouvrirPanneau(utilisateur);
    expect(within(panneau).getByText("l'assistant répond…")).toBeInTheDocument();
  });

  it("signale un fil illisible sans se refermer", async () => {
    const utilisateur = userEvent.setup();
    poserFilAssistance({ erreur: "API injoignable" });
    rendreAvecEtat(<AssistantFlottant />);
    const panneau = await ouvrirPanneau(utilisateur);

    expect(within(panneau).getByRole("alert")).toHaveTextContent("API injoignable");
    expect(
      within(panneau).getByRole("textbox", { name: "Message à l'assistant" }),
    ).toBeInTheDocument();
  });

  it("annonce la reconnexion quand le temps réel est coupé", async () => {
    const utilisateur = userEvent.setup();
    poserFilAssistance({ connecte: false });
    rendreAvecEtat(<AssistantFlottant />);
    const panneau = await ouvrirPanneau(utilisateur);
    expect(within(panneau).getByText("Reconnexion…")).toBeInTheDocument();
  });
});

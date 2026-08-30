/**
 * L'onglet **Chat** d'une fiche agent (#264 et #265, lots 12 et 13 de #243).
 *
 * Les deux lots ont livré sans tests sur *cet* onglet (docs/10 §5.1). Le fil
 * lui-même est couvert de bout en bout par `chat-direct`, `chat-global`,
 * `chat-pleine-page` et `fil-lisible` — qui montent tous le chat **global**
 * (`app/chat/page`) ou le hook. Ce fichier ne les rejoue pas : il garde ce qui
 * n'appartient qu'à l'onglet, et qui est précisément ce que #264/#265 promettent
 * pour lui — *le fil par agent reste la vue détaillée, et les deux ne divergent
 * pas*.
 *
 * ① **l'onglet parle au bon fil** — `useChat` est appelé avec le nom de la fiche,
 *    et lui seul. C'est la seule preuve qui existe : le contenu rendu vient de
 *    `poserFilAssistance` quoi qu'il arrive, donc « cet onglet montre la
 *    conversation de cet agent » ne s'observe qu'au canal demandé ;
 * ② **une seule mise en page, celle du produit** — l'onglet ne redessine rien, il
 *    monte `Conversation` comme le chat global. Les libellés en portent la
 *    preuve : ils nomment l'interlocuteur, donc ils viennent du composant commun ;
 * ③ **la réponse s'écrit** (#264) — l'attente avant le premier mot, puis le texte
 *    qui la remplace, puis l'interruption qui laisse ce qui est arrivé ;
 * ④ **la conversation se lit** (#265) — le fil vide qui invite, les messages dans
 *    l'ordre d'écriture, et les fautes au pied du fil.
 *
 * L'onglet est monté par `ContenuOngletAgent` : c'est le point d'entrée que la
 * fiche utilise, et il reste vrai si le composant change de fichier. Aucun mock
 * d'API n'est nécessaire — `useChat` est déjà factice (`tests/setup.ts`).
 */

import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ContenuOngletAgent } from "@/components/ContenuOngletAgent";
import { FilChat } from "@/components/FilChat";

import {
  canalCourant,
  canauxDemandes,
  messageFactice,
  poserFilAssistance,
  rendreAvecEtat,
} from "./aides";

/** Monte l'onglet Chat de `dev`, avec le fil que le test a posé. */
function monterOnglet(nom = "dev") {
  return {
    utilisateur: userEvent.setup(),
    ...rendreAvecEtat(<ContenuOngletAgent nom={nom} onglet="chat" />),
  };
}

/** Le fil rendu — la région que `Conversation` pose autour de la conversation. */
function fil(nom = "dev") {
  return screen.getByRole("region", { name: `Chat avec ${nom}` });
}

describe("① l'onglet parle au fil de son agent", () => {
  it("demande le fil de la fiche, et lui seul", () => {
    monterOnglet("qa");

    // Un onglet qui lirait le fil de l'assistance, ou le dernier fil ouvert,
    // afficherait une conversation qui n'est pas celle de cette fiche — et rien
    // à l'écran ne le dirait.
    expect(canalCourant()).toBe("qa");
    expect(new Set(canauxDemandes)).toEqual(new Set(["qa"]));
  });

  it("suit la fiche quand on passe à un autre agent", () => {
    const { rerender } = rendreAvecEtat(
      <ContenuOngletAgent nom="dev" onglet="chat" />,
    );

    rerender(<ContenuOngletAgent nom="devops" onglet="chat" />);

    expect(canalCourant()).toBe("devops");
  });

  it("nomme l'interlocuteur partout où l'on écrit ou lit", () => {
    monterOnglet();

    // Le nom de l'agent est porté par l'en-tête de la fiche (#190) : le titre du
    // fil ne le répète pas, mais tout ce qui désigne un destinataire, si.
    expect(fil()).toBeInTheDocument();
    expect(
      screen.getByRole("list", { name: "Messages échangés avec dev" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Message à dev" })).toBeInTheDocument();
  });
});

describe("② une seule mise en page, celle du produit", () => {
  it("monte le composant de fil commun, sans en redessiner un second", () => {
    monterOnglet();

    // #269 : bulles, saisie, région live et rattachements vivent dans
    // `components/Conversation`, que le chat global monte de la même façon. La
    // région live en est le témoin le moins déplaçable — un `role="status"`
    // annoncé aux lecteurs d'écran, et non une région de plus à la navigation.
    expect(
      screen.getByRole("status", { name: "Activité du fil avec dev" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Envoyer" })).toBeInTheDocument();
  });

  it("donne le rôle en titre quand l'appelant le connaît", () => {
    // L'onglet d'une fiche n'a que le nom en main — le charger ne vaudrait pas la
    // requête —, mais le composant sait le rendre quand on le lui passe.
    // `role` est ici la prop **métier** de `FilChat` (le rôle de l'agent), pas un
    // rôle ARIA : jsx-a11y ne peut pas faire la différence sur un composant.
    // eslint-disable-next-line jsx-a11y/aria-role
    rendreAvecEtat(<FilChat agent="dev" role="Développeur" />);

    expect(
      screen.getByRole("heading", { name: /Conversation · Développeur/ }),
    ).toBeInTheDocument();
  });

  it("signale une connexion perdue sans masquer le fil", () => {
    poserFilAssistance({
      connecte: false,
      messages: [messageFactice({ agent: "dev", contenu: "Message d'avant" })],
    });

    monterOnglet();

    expect(within(fil()).getByText("Reconnexion…")).toBeInTheDocument();
    expect(screen.getByText("Message d'avant")).toBeInTheDocument();
  });
});

describe("③ la réponse s'écrit (#264)", () => {
  it("dit que l'agent répond tant qu'aucun mot n'est arrivé", () => {
    poserFilAssistance({ envoi: true, reponseEnCours: null });

    monterOnglet();

    // L'attente **avant le premier mot** seulement : un indicateur immobile sur
    // toute la génération ne distinguerait pas une réponse longue d'un blocage.
    expect(screen.getByText("dev répond…")).toBeInTheDocument();
  });

  it("laisse le texte prendre le relais dès le premier incrément", () => {
    poserFilAssistance({
      envoi: true,
      reponseEnCours: { auteur: "dev", texte: "Je regarde le", figee: false },
    });

    monterOnglet();

    expect(screen.getByText(/Je regarde le/)).toBeInTheDocument();
    // Dès qu'un incrément arrive, c'est le texte lui-même qui dit que ça travaille.
    expect(screen.queryByText("dev répond…")).toBeNull();
  });

  it("garde ce qui est arrivé d'une réponse interrompue, en le disant", () => {
    poserFilAssistance({
      envoi: false,
      reponseEnCours: { auteur: "dev", texte: "Je regarde le", figee: true },
    });

    monterOnglet();

    expect(screen.getByText(/Je regarde le/)).toBeInTheDocument();
    expect(
      screen.getByText("Réponse interrompue — ce qui précède est incomplet."),
    ).toBeInTheDocument();
  });

  it("offre d'interrompre pendant l'envoi, et rien à interrompre sinon", async () => {
    const interrompre = vi.fn();
    poserFilAssistance({ envoi: true, interrompre });
    const { utilisateur } = monterOnglet();

    await utilisateur.click(screen.getByRole("button", { name: "Interrompre" }));

    expect(interrompre).toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "Envoyer" })).toBeNull();
  });
});

describe("④ la conversation se lit (#265)", () => {
  it("invite à écrire quand le fil est vide", () => {
    poserFilAssistance({ messages: [] });

    monterOnglet();

    expect(
      screen.getByText(/Aucun message pour l'instant/),
    ).toBeInTheDocument();
  });

  it("rend les messages dans l'ordre d'écriture", () => {
    poserFilAssistance({
      messages: [
        messageFactice({
          agent: "dev",
          auteur: "utilisateur",
          contenu: "Peux-tu relire ce module ?",
          horodatage: "2026-07-28T10:00:00Z",
        }),
        messageFactice({
          agent: "dev",
          auteur: "agent",
          contenu: "Oui, je commence par les tests.",
          horodatage: "2026-07-28T10:01:00Z",
        }),
      ],
    });

    monterOnglet();

    const bulles = within(
      screen.getByRole("list", { name: "Messages échangés avec dev" }),
    ).getAllByRole("listitem");
    expect(bulles[0]).toHaveTextContent("Peux-tu relire ce module ?");
    expect(bulles[1]).toHaveTextContent("Oui, je commence par les tests.");
  });

  it("envoie ce qui est saisi au fil de cet agent", async () => {
    const envoyer = vi.fn().mockResolvedValue(undefined);
    poserFilAssistance({ envoyer });
    const { utilisateur } = monterOnglet();

    await utilisateur.type(
      screen.getByRole("textbox", { name: "Message à dev" }),
      "Relis le module de routage",
    );
    await utilisateur.click(screen.getByRole("button", { name: "Envoyer" }));

    expect(envoyer).toHaveBeenCalledWith("Relis le module de routage", []);
  });

  it("pose une lecture impossible au pied du fil, jamais au-dessus", () => {
    poserFilAssistance({
      erreur: "backend injoignable",
      messages: [messageFactice({ agent: "dev", contenu: "Message d'avant" })],
    });

    monterOnglet();

    // #697 : « Fil illisible » se posait au-dessus de la conversation et poussait
    // tous les messages d'un coup. Ici elle arrive là où l'œil est déjà.
    const alerte = screen.getByRole("alert");
    expect(alerte).toHaveTextContent("Fil illisible : backend injoignable");
    expect(screen.getByText("Message d'avant")).toBeInTheDocument();
  });

  it("dit que la première lecture du fil est en cours", () => {
    poserFilAssistance({ chargement: true, messages: [] });

    monterOnglet();

    expect(screen.getByText("Chargement du fil…")).toBeInTheDocument();
  });
});

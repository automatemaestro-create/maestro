/**
 * Le fil qui accepte fichiers, images, dossiers et liens (#482, lot 1 de #481).
 *
 * ⚠ **Couverture volontairement partielle.** Le ticket diffère les tests au lot
 * final (« Tests différés → #485 ») ; ce fichier ne garde que ce que la règle de
 * découpage (docs/10 §5.1) laisse à un lot intermédiaire — la **logique
 * critique**, celle dont une régression serait silencieuse :
 *
 * - un fichier déposé part par son **identifiant de téléversement**, jamais par
 *   ses octets ni par son nom. C'est ce qui garantit qu'il n'atterrit pas dans le
 *   dossier de l'utilisateur, et rien à l'écran ne le dirait s'il cessait d'être
 *   vrai ;
 * - un **refus de source reste dans le fil**, et sur la source qu'il vise. C'est
 *   le critère 2 en toutes lettres : « il ne disparaît pas dans une console » ;
 * - le **rapport de lecture est consultable depuis le message** qui a porté les
 *   sources (critère 3), replié par défaut.
 *
 * Le reste — parcours complets, panneau d'assistance, régressions d'écran —
 * revient à #485.
 */

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { FilChat } from "@/components/FilChat";
import { ErreurSource } from "@/lib/api";
import type { RapportLecture } from "@/lib/types";

import { messageFactice, poserFilAssistance, rendreAvecEtat } from "./aides";

vi.mock("@/lib/api", async (original) => ({
  // `importOriginal` garde `ErreurSource` **la** classe du module : sans cela le
  // `instanceof` qui distingue un refus motivé d'une panne réseau ne
  // reconnaîtrait plus rien (même piège que `composer.test.tsx`).
  ...(await original<Record<string, unknown>>()),
  televerserSources: vi.fn(),
}));

const { televerserSources } = await import("@/lib/api");
const televerse = vi.mocked(televerserSources);

/** Un fichier déposable, tel qu'un navigateur le livrerait. */
function fichierFactice(nom: string, contenu = "# Cahier\n") {
  return new File([contenu], nom, { type: "text/markdown" });
}

/** Le dépôt d'un ou plusieurs fichiers sur la conversation, glisser-déposer compris. */
function glisserSur(cible: HTMLElement, fichiers: File[]) {
  const transfert = { files: fichiers, items: [], types: ["Files"] };
  fireEvent.dragOver(cible, { dataTransfer: transfert });
  fireEvent.drop(cible, { dataTransfer: transfert });
}

beforeEach(() => {
  televerse.mockReset();
  televerse.mockResolvedValue({
    sources: [{ id: "tv-1", type: "fichier", nom: "cahier.md", taille: 9 }],
    total_octets: 9,
  });
});

describe("le fil accepte des sources (#482)", () => {
  it("envoie un fichier glissé par son identifiant de téléversement, pas par ses octets", async () => {
    const envoyer = vi.fn().mockResolvedValue(undefined);
    poserFilAssistance({ envoyer });
    rendreAvecEtat(<FilChat agent="dev" />);

    glisserSur(screen.getByLabelText("Chat avec dev"), [fichierFactice("cahier.md")]);

    // Le dépôt ouvre le panneau : des pièces jointes invisibles partiraient sans
    // que rien ne l'ait dit.
    const jointes = await screen.findByRole("list", {
      name: "Sources jointes au message",
    });
    expect(within(jointes).getByText("cahier.md")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Message à dev"), {
      target: { value: "Voici le cahier." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Envoyer" }));

    await waitFor(() => expect(envoyer).toHaveBeenCalled());
    expect(televerse).toHaveBeenCalledTimes(1);
    expect(envoyer).toHaveBeenCalledWith("Voici le cahier.", [
      { type: "fichier", id: "tv-1" },
    ]);
  });

  it("laisse partir un message fait de sources seules", async () => {
    const envoyer = vi.fn().mockResolvedValue(undefined);
    poserFilAssistance({ envoyer });
    rendreAvecEtat(<FilChat agent="dev" />);

    glisserSur(screen.getByLabelText("Chat avec dev"), [fichierFactice("cahier.md")]);
    await screen.findByRole("list", { name: "Sources jointes au message" });

    // Déposer un cahier des charges *est* le message : le bouton ne doit pas
    // rester barré faute de texte.
    const envoi = screen.getByRole("button", { name: "Envoyer" });
    expect(envoi).not.toBeDisabled();
    fireEvent.click(envoi);

    await waitFor(() => expect(envoyer).toHaveBeenCalledWith("", [
      { type: "fichier", id: "tv-1" },
    ]));
  });

  it("garde le refus dans le fil, sur la source qu'il vise, sans rien perdre", async () => {
    const envoyer = vi
      .fn()
      .mockRejectedValue(
        new ErreurSource(
          "source-trop-volumineuse",
          "Source 1 trop volumineuse : 20000000 octets, 10485760 au maximum.",
          0,
        ),
      );
    poserFilAssistance({ envoyer });
    rendreAvecEtat(<FilChat agent="dev" />);

    glisserSur(screen.getByLabelText("Chat avec dev"), [fichierFactice("cahier.md")]);
    await screen.findByRole("list", { name: "Sources jointes au message" });
    fireEvent.change(screen.getByLabelText("Message à dev"), {
      target: { value: "Voici le cahier." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Envoyer" }));

    // Le refus est rendu **sur la ligne** de la source fautive (index 0), avec
    // son motif brut — pas dans une console.
    const ligne = await screen.findByRole("alert");
    expect(ligne).toHaveTextContent("Source refusée");
    expect(ligne).toHaveTextContent("source-trop-volumineuse");
    expect(
      within(
        screen.getByRole("list", { name: "Sources jointes au message" }),
      ).getByText("cahier.md"),
    ).toBeInTheDocument();
    // Rien n'est perdu : le texte revient et la source reste jointe.
    expect(screen.getByLabelText("Message à dev")).toHaveValue("Voici le cahier.");
  });

  it("rend consultable, depuis le message, ce qui a réellement été lu", async () => {
    const rapport: RapportLecture = {
      tokens: 1234,
      lectures: [
        {
          nom: "cahier.md",
          type: "fichier",
          etat: "lu",
          tokens: 1234,
          motif: "",
          message: "",
          limite: "",
          entrees: [],
        },
        {
          nom: "maquette.png",
          type: "fichier",
          etat: "ignore",
          tokens: 0,
          motif: "format-non-gere",
          message: "Format non géré.",
          limite: "",
          entrees: [],
        },
      ],
    };
    poserFilAssistance({
      messages: [
        messageFactice({
          agent: "dev",
          contenu: "Voici le cahier.",
          sources: [
            {
              type: "fichier",
              nom: "cahier.md",
              chemin: "/ing/cahier.md",
              valeur: "",
              taille: 9,
              lecture_seule: true,
            },
            {
              type: "fichier",
              nom: "maquette.png",
              chemin: "/ing/maquette.png",
              valeur: "",
              taille: 72,
              lecture_seule: true,
            },
          ],
          rapport,
        }),
      ],
    });
    rendreAvecEtat(<FilChat agent="dev" />);

    // Replié, le résumé suffit à savoir s'il faut déplier — et il compte les
    // **états**, l'image ignorée comprise.
    const bascule = screen.getByRole("button", { name: /ce qui a été lu/ });
    expect(bascule).toHaveTextContent("1 lue, 1 ignorée, 1 234 tokens");
    expect(
      screen.queryByRole("region", { name: "Aperçu de l'extraction" }),
    ).not.toBeInTheDocument();
    expect(screen.getByLabelText("Sources jointes (2)")).toBeInTheDocument();

    fireEvent.click(bascule);

    // Déplié : le rendu est celui du composer (#319), sans variante — « ignoré »
    // y porte son motif, et l'image se voit au lieu de disparaître.
    const apercu = await screen.findByLabelText("Aperçu de l'extraction");
    expect(within(apercu).getByText("maquette.png")).toBeInTheDocument();
    expect(within(apercu).getByText("format-non-gere")).toBeInTheDocument();
    expect(within(apercu).getByText("Ignoré")).toBeInTheDocument();
  });

  it("laisse une bulle sans source strictement telle qu'avant ce lot", () => {
    poserFilAssistance({
      messages: [messageFactice({ agent: "dev", contenu: "Bonjour." })],
    });
    rendreAvecEtat(<FilChat agent="dev" />);

    expect(screen.getByText("Bonjour.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /ce qui a été lu/ })).toBeNull();
    expect(screen.queryByLabelText(/^Sources jointes \(/)).toBeNull();
  });
});

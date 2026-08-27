/**
 * Le fil qui accepte fichiers, images, dossiers et liens (#482, lot 1 de #481).
 *
 * Deux moitiés, écrites à deux moments et qui se lisent ensemble.
 *
 * La première est celle du lot 1, qui différait ses tests au lot final
 * (« Tests différés → #485 ») en gardant ce que la règle de découpage
 * (docs/10 §5.1) laisse à un lot intermédiaire — la **logique critique**, celle
 * dont une régression serait silencieuse :
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
 * La seconde est due au **lot final (#485)** et couvre les deux autres types de
 * source — un **dossier** du poste et une **adresse**, que le titre du lot nomme
 * et qu'aucun test n'exerçait dans le fil —, le **cycle de vie de la
 * composition** (on retire avant d'envoyer, un envoi réussi vide la zone, un
 * refus la conserve) et le refus qui ne vise **aucune** source en particulier,
 * dont l'écran a un second endroit rien que pour lui.
 *
 * Côté API, la même chaîne est couverte de bout en bout par `tests/test_chat.py`
 * (section ④) et `tests/test_controltower.py` (section ⑧).
 */

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { FilChat } from "@/components/FilChat";
import { ErreurSource } from "@/lib/api";
import type { RapportLecture } from "@/lib/types";

import {
  messageFactice,
  pageExplorateurFactice,
  poserFilAssistance,
  rendreAvecEtat,
} from "./aides";

vi.mock("@/lib/api", async (original) => ({
  // `importOriginal` garde `ErreurSource` **la** classe du module : sans cela le
  // `instanceof` qui distingue un refus motivé d'une panne réseau ne
  // reconnaîtrait plus rien (même piège que `composer.test.tsx`).
  ...(await original<Record<string, unknown>>()),
  televerserSources: vi.fn(),
  chargerExplorateur: vi.fn(),
}));

const { televerserSources, chargerExplorateur } = await import("@/lib/api");
const televerse = vi.mocked(televerserSources);
const explorateur = vi.mocked(chargerExplorateur);

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
  explorateur.mockReset();
  explorateur.mockResolvedValue(pageExplorateurFactice());
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

describe("les deux autres types de source, et le cycle de la composition (#485)", () => {
  /** Ouvre le panneau de dépôt — replié tant qu'on n'en a pas besoin. */
  async function ouvrirLePanneau(utilisateur: ReturnType<typeof userEvent.setup>) {
    await utilisateur.click(
      screen.getByRole("button", { name: "Joindre des sources…" }),
    );
  }

  it("déclare un dossier par son chemin et une adresse par sa valeur, sans rien téléverser", async () => {
    const utilisateur = userEvent.setup();
    const envoyer = vi.fn().mockResolvedValue(undefined);
    poserFilAssistance({ envoyer });
    rendreAvecEtat(<FilChat agent="dev" />);

    await ouvrirLePanneau(utilisateur);
    // Le dossier vient de l'explorateur servi par le backend, jamais d'une
    // saisie : un navigateur ne livre pas de chemin absolu (#223/#278).
    await utilisateur.click(screen.getByRole("button", { name: "Choisir un dossier…" }));
    const panneau = await screen.findByRole("region", { name: "Explorateur de dossiers" });
    await utilisateur.click(
      within(panneau).getByRole("button", { name: "Choisir projets" }),
    );

    await utilisateur.type(
      screen.getByLabelText("Adresse à lire"),
      "https://exemple.test/spec",
    );
    await utilisateur.click(screen.getByRole("button", { name: "Ajouter l'adresse" }));

    fireEvent.change(screen.getByLabelText("Message à dev"), {
      target: { value: "Reprends ce cadrage." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Envoyer" }));

    await waitFor(() => expect(envoyer).toHaveBeenCalled());
    // Aucun octet à porter : ni l'un ni l'autre ne passe par le téléversement.
    expect(televerse).not.toHaveBeenCalled();
    expect(envoyer).toHaveBeenCalledWith("Reprends ce cadrage.", [
      expect.objectContaining({ type: "dossier", chemin: "D:/projets" }),
      {
        type: "url",
        valeur: "https://exemple.test/spec",
        nom: "https://exemple.test/spec",
      },
    ]);
  });

  it("retire une source jointe avant l'envoi", async () => {
    const utilisateur = userEvent.setup();
    const envoyer = vi.fn().mockResolvedValue(undefined);
    televerse.mockResolvedValue({
      sources: [{ id: "tv-2", type: "fichier", nom: "notes.md", taille: 4 }],
      total_octets: 4,
    });
    poserFilAssistance({ envoyer });
    rendreAvecEtat(<FilChat agent="dev" />);

    glisserSur(screen.getByLabelText("Chat avec dev"), [
      fichierFactice("cahier.md"),
      fichierFactice("notes.md", "note"),
    ]);
    const jointes = await screen.findByRole("list", {
      name: "Sources jointes au message",
    });
    await utilisateur.click(
      within(jointes).getByRole("button", { name: "Retirer cahier.md" }),
    );
    expect(within(jointes).queryByText("cahier.md")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Envoyer" }));

    await waitFor(() => expect(envoyer).toHaveBeenCalled());
    // Une seule source part, et c'est bien celle qui est restée : les
    // identifiants sont attribués **dans l'ordre de la composition**, donc
    // retirer la première ne doit pas décaler la seconde.
    expect(envoyer).toHaveBeenCalledWith("", [{ type: "fichier", id: "tv-2" }]);
  });

  it("vide la composition quand l'envoi réussit, et la conserve quand il échoue", async () => {
    const envoyer = vi.fn().mockResolvedValue(undefined);
    poserFilAssistance({ envoyer });
    rendreAvecEtat(<FilChat agent="dev" />);

    glisserSur(screen.getByLabelText("Chat avec dev"), [fichierFactice("cahier.md")]);
    await screen.findByRole("list", { name: "Sources jointes au message" });
    fireEvent.click(screen.getByRole("button", { name: "Envoyer" }));

    // Le succès seul efface la composition : sans cela, le message suivant
    // remporterait les mêmes pièces jointes sans que personne l'ait demandé.
    await waitFor(() =>
      expect(
        screen.queryByRole("list", { name: "Sources jointes au message" }),
      ).toBeNull(),
    );

    // Un échec ordinaire (502, panne réseau), lui, ne perd rien.
    envoyer.mockRejectedValueOnce(new Error("agent indisponible"));
    glisserSur(screen.getByLabelText("Chat avec dev"), [fichierFactice("cahier.md")]);
    await screen.findByRole("list", { name: "Sources jointes au message" });
    fireEvent.change(screen.getByLabelText("Message à dev"), {
      target: { value: "Deuxième essai." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Envoyer" }));

    await screen.findByText(/agent indisponible/);
    expect(
      within(
        screen.getByRole("list", { name: "Sources jointes au message" }),
      ).getByText("cahier.md"),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Message à dev")).toHaveValue("Deuxième essai.");
  });

  it("rend sous la saisie le refus qui ne vise aucune source en particulier", async () => {
    const envoyer = vi
      .fn()
      .mockRejectedValue(
        new ErreurSource(
          "trop-de-sources",
          "Trop de sources : 21 déclarées, 20 au maximum.",
          null,
        ),
      );
    poserFilAssistance({ envoyer });
    rendreAvecEtat(<FilChat agent="dev" />);

    glisserSur(screen.getByLabelText("Chat avec dev"), [fichierFactice("cahier.md")]);
    await screen.findByRole("list", { name: "Sources jointes au message" });
    fireEvent.click(screen.getByRole("button", { name: "Envoyer" }));

    // Sans index, le refus n'a pas de ligne où se poser : il se rend une fois,
    // sous le formulaire, et jamais collé à une source prise au hasard.
    const bandeau = await screen.findByRole("alert");
    expect(bandeau).toHaveTextContent("Sources refusées");
    expect(bandeau).toHaveTextContent("trop-de-sources");
    expect(screen.getAllByRole("alert")).toHaveLength(1);
  });
});

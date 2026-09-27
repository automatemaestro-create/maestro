/**
 * **Un projet né dans le fil d'un projet ouvert** (#1340) — l'en-tête et le fil
 * disent la même chose du projet ouvert.
 *
 * Un projet naît dans la conversation (#1294, docs/43 §2.2), et pas seulement sur
 * la porte d'entrée : dans le fil d'un projet déjà ouvert, « J'ai déjà un projet
 * dans C:/…, importe-le » fait proposer, puis déclarer, un autre projet. Vu sur la
 * vraie stack à la relecture de #1161 : le fil disait « Votre carnet de recettes
 * est maintenant le projet ouvert », son outillage commençait (« Pièce 1 sur 6 ·
 * Carnet de recettes (import) »), et l'en-tête restait sur « Projet neuf ». Pire
 * que le mot : la fenêtre gardait l'ancien projet, donc la demande tapée ensuite
 * partait avec lui (`useChat`, #683) — un run ou une correction d'outillage rangés
 * dans le projet qu'on venait de quitter.
 *
 * Ce que ces tests gardent :
 *
 * ① **le projet né devient le projet ouvert**, depuis la colonne comme depuis
 *   `/chat` — comme depuis la porte — et la page ne bouge pas ;
 * ② **seulement quand il naît.** Une conversation porte ses naissances pour
 *   toujours : y rentrer à chaque lecture ramènerait de force dans le projet
 *   qu'on vient de quitter d'un choix, et rouvrir une conversation d'hier ferait
 *   changer de projet sans rien avoir demandé.
 *
 * `useChat` est le double de `setup.ts` : le fil rendu est celui qu'on pose, et
 * une relecture du fil est un nouveau rendu du shell sur le fil reposé.
 */

import { act, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import PageChat from "@/app/chat/page";
import { Shell } from "@/components/Shell";
import { marquerGuideVu } from "@/lib/guide";
import { AGENT_ORCHESTRATION } from "@/lib/orchestration";
import { ecrireConversationOuverte } from "@/lib/preferences";
import { lireProjetActifId } from "@/lib/projetActif";
import type { DemandeProjet, MessageChat } from "@/lib/types";

import {
  cheminCourant,
  messageFactice,
  navigations,
  poserChemin,
  poserFilAssistance,
  poserProjetActif,
  poserProjets,
  projetFactice,
} from "./aides";

/** Le projet ouvert quand la conversation en fait naître un autre. */
const OUVERT = projetFactice({ id: "prj-neuf", nom: "Projet neuf" });

/** Le projet que l'import fait naître — déclaré, donc servi par la liste. */
const IMPORTE = projetFactice({
  id: "prj-carnet",
  nom: "Carnet de recettes",
  racine: "C:/Users/moi/carnet-recettes",
});

/** La proposition d'import, telle que la carte la montre. */
const PROPOSITION: DemandeProjet = {
  nom: IMPORTE.nom,
  racine: IMPORTE.racine,
  origine: "existant",
  versionner: false,
  deja_versionne: true,
  raison_nom: "Le nom de son dossier.",
  raison_dossier: "Le dossier que vous avez donné.",
  raison_versionnement: "Son dépôt Git est constaté tel quel.",
  ajustements: [],
};

/** Un tour du fil de l'orchestration, horodaté à son rang. */
function tour(rang: number, partiel: Partial<MessageChat>): MessageChat {
  return messageFactice({
    agent: AGENT_ORCHESTRATION,
    horodatage: `2026-09-27T10:0${rang}:00Z`,
    ...partiel,
  });
}

/** Le fil jusqu'à la carte : l'import demandé, puis proposé. */
function jusquALaCarte(): MessageChat[] {
  return [
    tour(0, {
      contenu: `J'ai déjà un projet dans ${IMPORTE.racine}, importe-le`,
    }),
    tour(1, {
      auteur: AGENT_ORCHESTRATION,
      contenu: "Je vous propose de l'importer.",
      projet_propose: PROPOSITION,
    }),
  ];
}

/** Le fil après l'accord : la réponse porte le projet déclaré (`projet_cree`). */
function apresLAccord(): MessageChat[] {
  return [
    ...jusquALaCarte(),
    tour(2, { contenu: "Oui, importe ce projet." }),
    tour(3, {
      auteur: AGENT_ORCHESTRATION,
      contenu: "Votre carnet de recettes est maintenant le projet ouvert.",
      projet_cree: {
        id: IMPORTE.id,
        nom: IMPORTE.nom,
        racine: IMPORTE.racine,
        origine: "existant",
        versionne: true,
        versionnement_refuse: "",
      },
    }),
  ];
}

/**
 * Le shell sur un écran quelconque — la colonne de conversation ouverte.
 *
 * Un élément **neuf** à chaque rendu : React ne re-rend pas un élément identique,
 * et le fil reposé ne serait alors jamais relu.
 */
const ecran = () => (
  <Shell>
    <p>un écran quelconque</p>
  </Shell>
);

/** L'en-tête : le sélecteur de la barre supérieure nomme le projet ouvert. */
function entete(nom: string) {
  return screen.findByRole("button", {
    name: new RegExp(`^Projet actif : ${nom} `),
  });
}

/** Laisse passer ce qu'une entrée aurait déclenché — lecture de la liste comprise. */
async function laisserPasser(): Promise<void> {
  await act(async () => {
    await new Promise((resoudre) => setTimeout(resoudre, 50));
  });
}

beforeEach(() => {
  marquerGuideVu();
  poserProjetActif(OUVERT);
  // Déclaré avant l'écran : la liste que l'entrée relit le connaît déjà.
  poserProjets([OUVERT, IMPORTE]);
  ecrireConversationOuverte(true);
  poserChemin("/runs");
});

describe("le projet né dans le fil d'un projet ouvert (#1340)", () => {
  it("devient le projet ouvert : l'en-tête nomme celui dont le fil parle", async () => {
    poserFilAssistance({ messages: jusquALaCarte() });
    const { rerender } = render(ecran());
    await screen.findByRole("complementary", { name: "Conversation" });
    expect(await entete(OUVERT.nom)).toBeInTheDocument();

    // L'accord : la réponse porte le projet déclaré, et le fil se relit.
    poserFilAssistance({ messages: apresLAccord() });
    rerender(ecran());

    expect(await entete(IMPORTE.nom)).toBeInTheDocument();
    expect(lireProjetActifId()).toBe(IMPORTE.id);
    // La conversation continue dans la colonne, là où elle a eu lieu.
    expect(
      await screen.findByRole("complementary", { name: "Conversation" }),
    ).toHaveTextContent("maintenant le projet ouvert");
    // Garde de shell, pas redirection : la page reste celle où l'on était.
    expect(cheminCourant()).toBe("/runs");
    expect(navigations).toEqual([]);
  });

  it("le devient aussi depuis /chat, où la conversation occupe l'écran", async () => {
    poserChemin("/chat");
    const page = () => (
      <Shell>
        <PageChat />
      </Shell>
    );
    poserFilAssistance({ messages: jusquALaCarte() });
    const { rerender } = render(page());
    await screen.findByRole("region", { name: "Chat global" });
    expect(await entete(OUVERT.nom)).toBeInTheDocument();

    poserFilAssistance({ messages: apresLAccord() });
    rerender(page());

    expect(await entete(IMPORTE.nom)).toBeInTheDocument();
    expect(lireProjetActifId()).toBe(IMPORTE.id);
    expect(cheminCourant()).toBe("/chat");
  });

  it("n'y ramène pas quand on rouvre un projet dont le fil porte une naissance passée", async () => {
    // Revenir sur l'ancien projet se fait d'un choix, et la conversation qu'on y
    // retrouve porte encore la naissance : y rentrer à la lecture ramènerait de
    // force dans le projet qu'on vient de quitter.
    poserFilAssistance({ messages: apresLAccord() });
    render(ecran());
    await screen.findByRole("complementary", { name: "Conversation" });
    await laisserPasser();

    expect(lireProjetActifId()).toBe(OUVERT.id);
    expect(await entete(OUVERT.nom)).toBeInTheDocument();
  });

  it("ne change pas de projet quand on rouvre une conversation où un projet est né", async () => {
    // La conversation d'hier a fait naître le carnet ; celle d'aujourd'hui, rien.
    // Relire l'une après l'autre n'est pas une naissance.
    poserFilAssistance({
      conversation: "conv-du-jour",
      messages: [tour(0, { contenu: "Où en est le run ?" })],
    });
    const { rerender } = render(ecran());
    await screen.findByRole("complementary", { name: "Conversation" });

    poserFilAssistance({ conversation: "conv-d-hier", messages: apresLAccord() });
    rerender(ecran());
    await laisserPasser();

    expect(lireProjetActifId()).toBe(OUVERT.id);
    expect(await entete(OUVERT.nom)).toBeInTheDocument();
  });

  it("n'entre dans rien tant que le fil n'a pas été servi", async () => {
    // Avant sa première lecture, le fil n'a ni conversation ni messages : ce
    // qu'il porte ensuite est ce qu'il portait déjà, pas une naissance.
    poserFilAssistance({ conversation: "", chargement: true, messages: [] });
    const { rerender } = render(ecran());
    await screen.findByRole("complementary", { name: "Conversation" });

    poserFilAssistance({ messages: apresLAccord() });
    rerender(ecran());
    await laisserPasser();

    await waitFor(() => expect(lireProjetActifId()).toBe(OUVERT.id));
  });
});

/**
 * Le démarrage de Maestro (#1293, docs/43 §2.1) : chaque démarrage arrive sur le
 * choix du projet, « Reprendre *le dernier projet* » en tête, et la conversation
 * s'y rouvre.
 *
 * Le défaut que ce ticket corrige venait de **deux réglages retenus par le
 * profil de la coque** — le projet actif, et une colonne de conversation fermée
 * par un `"0"` d'avant le 22/09 — qui ramenaient à chaque démarrage le tableau de
 * bord d'avant l'atelier. Ces tests posent donc ces réglages-là, tels quels, et
 * regardent ce qu'un démarrage en fait.
 *
 * « Démarrage » a un sens précis ici : une **session vide** (la coque qu'on
 * rouvre, un onglet neuf). `tests/setup.ts` vide le `sessionStorage` avant
 * chaque test, donc chaque test commence par un démarrage ; un rechargement se
 * joue en remontant le shell **sans** vider la session, un nouveau démarrage en
 * la vidant.
 *
 * Aucun `if (electron)` n'est en jeu (ENF-12) : la coque et l'onglet servent le
 * même front, et ce qui se teste ici vaut pour les deux.
 */

import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { metadata } from "@/app/layout";
import { Shell } from "@/components/Shell";
import { marquerGuideVu } from "@/lib/guide";
import {
  CLE_CONVERSATION_OUVERTE,
  lireConversationOuverte,
} from "@/lib/preferences";
import { ecrireProjetActifId, lireProjetActifId } from "@/lib/projetActif";

import { poserProjets, projetFactice } from "./aides";

/** Ce que la page demandée rend — sa présence dit qu'on est entré. */
const CONTENU = "contenu de la page demandée";

const DEPENSIO = projetFactice();
const RACINES = projetFactice({
  id: "prj-racines",
  nom: "Racines",
  racine: "E:/sites/racines",
  vcs: null,
});
const KOMBUCHA = projetFactice({
  id: "prj-kombucha",
  nom: "kombucha-vitrine",
  racine: "C:/Users/moi/Maestro/kombucha-vitrine",
});

const monter = () =>
  render(
    <Shell>
      <p>{CONTENU}</p>
    </Shell>,
  );

/** La porte d'entrée, une fois la première lecture des projets tranchée. */
const porte = () => screen.findByRole("main", { name: "Choix du projet" });

/** La colonne de conversation du shell, si elle est ouverte. */
const colonne = () =>
  screen.queryByRole("complementary", { name: "Conversation" });

/**
 * Le profil d'un poste qui a déjà servi : des projets déclarés, le dernier ouvert
 * retenu, et — si demandé — le `"0"` ancien de la colonne, **au `localStorage`**,
 * là où la version d'avant l'écrivait et où le profil de la coque l'a gardé.
 */
function posteQuiADejaServi({ colonneFermeeJadis = false } = {}) {
  // L'ordre de l'API (celui des identifiants) ne met pas le dernier en tête :
  // c'est à la porte de le faire.
  poserProjets([KOMBUCHA, DEPENSIO, RACINES]);
  ecrireProjetActifId(DEPENSIO.id);
  if (colonneFermeeJadis) {
    window.localStorage.setItem(CLE_CONVERSATION_OUVERTE, "0");
  }
}

beforeEach(() => {
  // Sans cela la visite guidée s'ouvrirait par-dessus le shell une fois entré.
  marquerGuideVu();
});

describe("au démarrage, le choix du projet (#1293)", () => {
  it("arrive sur le choix, « Reprendre » le dernier projet en tête", async () => {
    posteQuiADejaServi();
    monter();

    const ecran = await porte();
    // Rien de l'atelier avant d'avoir choisi : c'est le défaut du retex.
    expect(screen.queryByText(CONTENU)).toBeNull();
    expect(
      screen.queryByRole("navigation", { name: "Navigation principale" }),
    ).toBeNull();

    const reprendre = within(ecran).getByRole("button", {
      name: "Reprendre Dépensio",
    });
    // La racine le décrit : deux clones du même dépôt portent le même nom (#280).
    expect(reprendre).toHaveAccessibleDescription("D:/projets/depensio");
    // « Nouveau projet » à côté, en second rang : une seule action pleine.
    const nouveau = within(ecran).getByRole("button", { name: "Nouveau projet" });
    expect(nouveau.className).toContain("border");
    expect(reprendre.className).toContain("bg-accent");
    expect(nouveau.className).not.toContain("bg-accent");

    // En tête : les deux actions viennent avant la liste.
    const liste = within(ecran).getByRole("list", { name: "Projets déclarés" });
    for (const action of [reprendre, nouveau]) {
      expect(
        action.compareDocumentPosition(liste) & Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
    }
    // Et la liste commence par le dernier ouvert, marqué comme tel.
    const cartes = within(liste).getAllByRole("button");
    expect(cartes.map((carte) => carte.getAttribute("aria-label"))).toEqual([
      "Ouvrir Dépensio",
      "Ouvrir kombucha-vitrine",
      "Ouvrir Racines",
    ]);
    expect(cartes[0]).toHaveTextContent("Dernier ouvert");
    expect(cartes[1]).not.toHaveTextContent("Dernier ouvert");
  });

  it("reprend en un geste : l'atelier sur ce projet, conversation ouverte malgré un « 0 » ancien", async () => {
    const utilisateur = userEvent.setup();
    posteQuiADejaServi({ colonneFermeeJadis: true });
    monter();
    await porte();

    await utilisateur.click(
      screen.getByRole("button", { name: "Reprendre Dépensio" }),
    );

    expect(await screen.findByText(CONTENU)).toBeInTheDocument();
    expect(lireProjetActifId()).toBe(DEPENSIO.id);
    // Le shell de l'atelier, et la conversation à droite : le `"0"` d'avant le
    // 22/09 est resté au `localStorage`, et il ne ferme plus rien.
    expect(
      screen.getByRole("navigation", { name: "Navigation principale" }),
    ).toBeInTheDocument();
    await waitFor(() => expect(colonne()).toBeInTheDocument());
    expect(window.localStorage.getItem(CLE_CONVERSATION_OUVERTE)).toBe("0");
  });

  it("n'offre rien à reprendre sans projet retenu : « Nouveau projet » en tête, plein", async () => {
    poserProjets([KOMBUCHA, DEPENSIO]);
    monter();
    const ecran = await porte();

    expect(within(ecran).queryByRole("button", { name: /^Reprendre/ })).toBeNull();
    expect(within(ecran).queryByText("Dernier ouvert")).toBeNull();
    const nouveau = within(ecran).getByRole("button", { name: "Nouveau projet" });
    expect(nouveau.className).toContain("bg-accent");
  });

  it("dit pourquoi, et ne propose rien à reprendre, quand le dernier projet a disparu", async () => {
    poserProjets([KOMBUCHA]);
    ecrireProjetActifId("prj-disparu");
    monter();
    const ecran = await porte();

    expect(within(ecran).getByRole("alert")).toHaveTextContent("prj-disparu");
    expect(within(ecran).queryByRole("button", { name: /^Reprendre/ })).toBeNull();
    expect(
      within(ecran).getByRole("button", { name: "Ouvrir kombucha-vitrine" }),
    ).toBeInTheDocument();
  });
});

describe("la colonne de conversation, le temps d'une session (#1293)", () => {
  it("reste fermée aux rechargements de la session, et se rouvre au démarrage suivant", async () => {
    const utilisateur = userEvent.setup();
    posteQuiADejaServi();
    monter();
    await porte();
    await utilisateur.click(
      screen.getByRole("button", { name: "Reprendre Dépensio" }),
    );
    await waitFor(() => expect(colonne()).toBeInTheDocument());

    // Fermer : un geste du moment.
    await utilisateur.click(
      screen.getByRole("button", { name: "Fermer la conversation" }),
    );
    await waitFor(() => expect(colonne()).toBeNull());
    expect(lireConversationOuverte()).toBe(false);

    // Un rechargement : même session — la page revient sans la porte, et la
    // colonne reste fermée.
    cleanup();
    monter();
    expect(await screen.findByText(CONTENU)).toBeInTheDocument();
    expect(
      await screen.findByRole("button", { name: "Déplier la conversation" }),
    ).toBeInTheDocument();
    expect(colonne()).toBeNull();

    // Le démarrage suivant : session neuve — la porte, puis la conversation
    // rouverte dès qu'on reprend.
    cleanup();
    window.sessionStorage.clear();
    monter();
    await porte();
    await utilisateur.click(
      screen.getByRole("button", { name: "Reprendre Dépensio" }),
    );
    expect(await screen.findByText(CONTENU)).toBeInTheDocument();
    await waitFor(() => expect(colonne()).toBeInTheDocument());
  });
});

describe("le titre de la fenêtre (#1293)", () => {
  it("est « Maestro », dans la coque comme dans l'onglet", () => {
    // La coque Electron reprend le `<title>` du document : c'est le même titre
    // pour les deux, et il ne dit plus « Control Tower ».
    expect(metadata.title).toBe("Maestro");
  });

  it("nomme la porte du même nom", async () => {
    posteQuiADejaServi();
    monter();
    const ecran = await porte();
    expect(ecran).toHaveTextContent("Maestro");
    expect(ecran).not.toHaveTextContent("Control Tower");
  });
});

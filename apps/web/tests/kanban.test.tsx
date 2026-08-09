/**
 * La section Tâches devenue l'objet du tableau de bord (#248, tests différés au
 * lot 8 — docs/10 §5.1). C'est le seul lot de la vague livré **sans aucun
 * test** : ce fichier est sa couverture, pas un complément.
 *
 * Ce lot ne change ni les colonnes, ni les cartes, ni la réassignation : il
 * change la **place** qu'occupe le tableau. Ce qui se teste, dans jsdom qui ne
 * calcule aucune mise en page, ce n'est donc pas une hauteur en pixels — c'est
 * la **chaîne** de classes qui la rend possible :
 *
 * - une hauteur *définie* posée par le cadre (`h-full` sur le `<body>`), sans
 *   laquelle il n'y a rien à prendre ;
 * - `min-h-0` sur **chaque** maillon flex, faute de quoi le `min-height:auto`
 *   par défaut empêche l'élément de rétrécir sous son contenu ;
 * - `flex-1` pour prendre le reste, et le défilement rendu à chaque colonne.
 *
 * Un seul maillon manquant et le débordement remonte à la zone de contenu : la
 * page s'allonge, l'ascenseur de colonne ne sert plus à rien, et rien — ni le
 * lint, ni le build, ni un test qui ne regarderait que le texte — ne le dit.
 * D'où des assertions qui **parcourent** la chaîne plutôt que d'énumérer des
 * classes attendues à la main. Le rendu réel, lui, reste vérifié au navigateur
 * par le skill `/verify`.
 */

import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { Kanban } from "@/components/Kanban";
import { Shell } from "@/components/Shell";
import { marquerGuideVu } from "@/lib/guide";

import {
  agentFactice,
  poserProjetActif,
  projetFactice,
  tacheFactice,
} from "./aides";

const AGENTS = [agentFactice({ nom: "dev" }), agentFactice({ nom: "qa" })];

/**
 * Le projet dont ces tâches sont les tâches (#281) : le Kanban le reçoit en
 * prop pour **nommer le vide** qu'il affiche quand il n'y a rien à montrer.
 */
const PROJET = projetFactice();

function rendreKanban(taches = [tacheFactice()]) {
  return render(
    <Kanban
      taches={taches}
      agents={AGENTS}
      reassigner={vi.fn()}
      projet={PROJET}
    />,
  );
}

/** La section elle-même — elle porte son nom accessible depuis #191. */
const section = () => screen.getByRole("region", { name: "Tâches (Kanban)" });

/** La colonne d'un statut, repérée par son titre. */
const colonne = (titre: string) =>
  screen.getByRole("heading", { name: new RegExp(`^${titre}`) })
    .parentElement as HTMLElement;

/** La zone défilante d'une colonne : celle qui porte les cartes. */
function zoneDefilante(titreColonne: string): HTMLElement {
  const zone = colonne(titreColonne).querySelector(".overflow-y-auto");
  expect(zone).not.toBeNull();
  return zone as HTMLElement;
}

describe("les colonnes du Kanban", () => {
  it("suit la machine à états, une colonne par statut", () => {
    rendreKanban([
      tacheFactice({ id: "T-1", statut: "assignee" }),
      tacheFactice({ id: "T-2", statut: "en_cours" }),
      tacheFactice({ id: "T-3", statut: "bloquee" }),
      tacheFactice({ id: "T-4", statut: "terminee" }),
      tacheFactice({ id: "T-5", statut: "echec" }),
    ]);
    for (const titre of [
      "Assignées",
      "En cours",
      "Bloquées",
      "Terminées",
      "Échecs",
    ]) {
      expect(within(colonne(titre)).getByRole("heading")).toHaveTextContent("1");
    }
  });

  it("range un statut inconnu du front dans « Autres » au lieu de le perdre", () => {
    // Même garde que `libelleStatut` : le moteur peut enrichir sa machine à
    // états sans qu'une tâche disparaisse de l'écran de pilotage.
    rendreKanban([tacheFactice({ titre: "Venue d'ailleurs", statut: "inedit" })]);
    expect(within(colonne("Autres")).getByText("Venue d'ailleurs")).toBeInTheDocument();
  });

  it("n'ouvre la colonne « Autres » que s'il y a quelque chose à y mettre", () => {
    rendreKanban([tacheFactice({ statut: "en_cours" })]);
    expect(screen.queryByRole("heading", { name: /^Autres/ })).toBeNull();
  });

  it("dit « Aucune tâche. » dans chaque colonne vide", () => {
    rendreKanban([tacheFactice({ statut: "en_cours" })]);
    expect(within(colonne("Assignées")).getByText("Aucune tâche.")).toBeInTheDocument();
    expect(within(colonne("En cours")).queryByText("Aucune tâche.")).toBeNull();
  });
});

describe("la place que prend la section (#248)", () => {
  it("s'étire dès qu'il y a des tâches", () => {
    rendreKanban();
    expect(section().className).toContain("flex-1");
    // Le plancher : sur une fenêtre courte, le tableau garde les 24 rem qu'il
    // avait avant le lot et c'est la zone de contenu qui défile.
    expect(section().className).toContain("min-h-96");
  });

  it("ne s'étire pas quand il n'y a rien à montrer", () => {
    // Cinq colonnes « Aucune tâche. » n'ont pas besoin de tout l'écran — et
    // sans hauteur à partager, la chaîne n'aurait rien à distribuer.
    rendreKanban([]);
    expect(section().className).not.toContain("flex-1");
    expect(section().className).not.toContain("min-h-96");
  });

  it("garde la chaîne d'étirement entière, de la zone défilante à la section", () => {
    // Le cœur du lot : chaque maillon entre la liste qui défile et la section
    // doit accepter de rétrécir sous son contenu. Un `min-h-0` oublié quelque
    // part sur ce chemin, et le débordement remonte à la page entière.
    rendreKanban([tacheFactice({ statut: "en_cours" })]);
    const zone = zoneDefilante("En cours");
    expect(zone.className).toContain("min-h-0");
    expect(zone.className).toContain("flex-1");

    const manquants: string[] = [];
    for (
      let noeud = zone.parentElement;
      noeud !== null && noeud !== section();
      noeud = noeud.parentElement
    ) {
      if (!noeud.className.includes("min-h-0")) manquants.push(noeud.className);
    }
    expect(manquants).toEqual([]);
  });

  it("rend le défilement à chaque colonne plutôt qu'à la page", () => {
    rendreKanban([tacheFactice({ statut: "en_cours" })]);
    expect(zoneDefilante("Assignées")).not.toBe(zoneDefilante("En cours"));
  });

  it("n'a plus de borne de hauteur fixe", () => {
    // `max-h-96` (#191) protégeait la densité d'un écran qui portait encore
    // cinq panneaux de plein format ; ceux-ci sont partis, la borne est restée,
    // et les tâches tenaient dans le tiers supérieur d'un grand écran.
    rendreKanban();
    expect(section().outerHTML).not.toContain("max-h-96");
  });

  it("se replie sur une largeur minimale de colonne, pas sur un nombre de colonnes", () => {
    // `md:grid-flow-col` tassait les cinq colonnes de front dès 768 px, où une
    // carte n'avait plus que ~120 px de large.
    rendreKanban();
    const grille = zoneDefilante("Assignées").closest(".grid") as HTMLElement;
    expect(grille.className).toContain("auto-fit");
    expect(grille.className).not.toContain("grid-flow-col");
  });
});

describe("la hauteur que le cadre donne (#248)", () => {
  it("laisse la zone de contenu rétrécir sous son contenu", async () => {
    // La chaîne commence plus haut que le Kanban : sans `min-h-0` ici, la
    // hauteur définie du `<body>` ne descend pas jusqu'à la page, et le
    // `flex-1` du Kanban se dimensionne sur son contenu.
    //
    // `poserProjetActif` est la condition d'entrée depuis #279 : sans projet
    // retenu, le shell s'arrête à la porte et le `<main>` qu'on mesurerait
    // serait celui de l'écran de choix, pas celui du cadre applicatif.
    marquerGuideVu();
    poserProjetActif();
    render(
      <Shell>
        <p>contenu de la page</p>
      </Shell>,
    );
    await screen.findByText("contenu de la page");
    const contenu = screen.getByRole("main");
    expect(contenu.className).toContain("min-h-0");
    expect(contenu.className).toContain("flex-1");
    expect(contenu.parentElement?.className).toContain("min-h-0");
  });
});

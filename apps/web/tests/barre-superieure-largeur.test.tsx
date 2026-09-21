/**
 * La barre supérieure se mesure elle-même, et rien n'y peint hors de sa boîte
 * (#1108).
 *
 * ── Ce que ce test garde, et ce qu'il ne peut pas garder ─────────────────────
 *
 * Il ne **mesure** pas : jsdom ne fait pas de mise en page, donc aucun
 * `getBoundingClientRect` n'y dirait qu'un élément en recouvre un autre. La
 * géométrie est l'affaire du banc (`banc-mise-en-page`), et elle a été relevée à
 * la main sur la stack de démo, projet au nom long, colonne ouverte — avant :
 * sélecteur 304→558 par-dessus le titre 398→434 et le coût 446→591 à 1116 px de
 * fenêtre ; après : aucun chevauchement à 1116, 1280 ni 1440. Ce relevé-là ne se
 * rejoue pas en CI.
 *
 * Ce qui se rejoue, c'est **la règle qui l'a produit**, et c'est une règle que
 * rien d'autre ne voit : ni le lint, ni le build, ni un test de rendu ne
 * remarqueraient qu'un `sm:` revient dans cette barre. Or il y reviendrait tout
 * naturellement — c'est la classe qu'on écrit par habitude, et elle a l'air de
 * marcher partout où la colonne de conversation est fermée. Le défaut n'apparaît
 * qu'entre ~1024 et ~1270 px avec la colonne ouverte, c'est-à-dire nulle part
 * dans une session de développement ordinaire.
 *
 * Deux propriétés, donc :
 *
 *   1. la barre **déclare** être son propre conteneur de requête (`@container`) ;
 *   2. **aucun** de ses descendants ne conditionne son rendu à la largeur de la
 *      **fenêtre** — à une exception, nommée ici et pas ailleurs : le bouton de
 *      navigation, dont le `lg:` parle du rail de gauche (imposé sous `lg` par
 *      la fenêtre) et non de la place dans la barre.
 *
 * Et la sonde est **prouvée sur un échantillon fautif** avant de balayer : sans
 * cela, une expression régulière qui ne trouve jamais rien passe pour un test
 * vert (docs/10 §3.9, la règle des `grep` de `test_cycle_de_vie.py`, transposée
 * au DOM).
 */

import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { Shell } from "@/components/Shell";
import { marquerGuideVu } from "@/lib/guide";

import { poserProjetActif, projetFactice } from "./aides";

/**
 * Les préfixes de Tailwind qui lisent la largeur de la **fenêtre**. `@sm:` &
 * consorts, qui lisent celle du conteneur, ne leur ressemblent que de loin :
 * le `@` les en sépare, et le motif l'exige explicitement pour qu'aucun des deux
 * ne se fasse passer pour l'autre.
 */
const VARIANTE_DE_FENETRE = /(?:^|\s)(?:max-)?(?:sm|md|lg|xl|2xl):/;

/** Toutes les classes de la barre, élément par élément. */
function classesDeLaBarre(): { balise: string; classes: string }[] {
  const barre = screen.getByRole("banner");
  return [barre, ...barre.querySelectorAll("*")].map((element) => ({
    balise:
      element.tagName.toLowerCase() +
      (element.getAttribute("aria-label")
        ? ` [${element.getAttribute("aria-label")}]`
        : ""),
    classes: element.getAttribute("class") ?? "",
  }));
}

const monter = async () => {
  render(
    <Shell>
      <p>contenu de la page</p>
    </Shell>,
  );
  await screen.findByText("contenu de la page");
};

describe("la barre supérieure se règle sur sa propre largeur (#1108)", () => {
  beforeEach(() => {
    marquerGuideVu();
    // Le nom long est l'état qui a fait apparaître le défaut (rubrique « États à
    // couvrir » du ticket) : c'est lui qui remplit la barre.
    poserProjetActif(
      projetFactice({ nom: "Refonte du portail client — Groupe Vallée" }),
    );
  });

  it("la sonde reconnaît une variante de fenêtre, et pas une variante de conteneur", () => {
    // L'échantillon fautif, avant tout balayage : ce qui suit est exactement ce
    // qu'on ne veut plus voir dans cette barre, et ce qu'on veut y voir.
    expect(VARIANTE_DE_FENETRE.test("hidden sm:inline-flex")).toBe(true);
    expect(VARIANTE_DE_FENETRE.test("px-4 sm:px-6")).toBe(true);
    expect(VARIANTE_DE_FENETRE.test("max-sm:hidden")).toBe(true);
    expect(VARIANTE_DE_FENETRE.test("hidden @2xl:inline-flex")).toBe(false);
    expect(VARIANTE_DE_FENETRE.test("@container px-4 @2xl:px-6")).toBe(false);
  });

  it("déclare la barre comme conteneur de requête", async () => {
    await monter();
    expect(screen.getByRole("banner").className).toContain("@container");
  });

  it("ne lit la largeur de la fenêtre que pour le bouton de navigation", async () => {
    await monter();
    const fautifs = classesDeLaBarre().filter(
      ({ balise, classes }) =>
        VARIANTE_DE_FENETRE.test(classes) &&
        // La seule exception, et elle ne parle pas de la barre : sous `lg`, le
        // rail de gauche est imposé par la fenêtre, donc ce bouton n'a rien à
        // basculer. Reconnue par son nom accessible plutôt que par sa position :
        // un bouton qui déménagerait dans la barre garderait sa raison d'être.
        !/^button \[(Déplier|Replier) la navigation\]$/.test(balise),
    );
    expect(
      fautifs,
      "un seuil de cette barre lit la largeur de la FENÊTRE, qui ne dit plus " +
        "rien de sa place depuis que la colonne de conversation lui en prend " +
        "320 px (#1108) — le lire sur la barre : `@sm:`, `@2xl:`…",
    ).toEqual([]);
  });

  it("le sélecteur de projet ne peut pas dépasser la boîte qu'on lui donne", async () => {
    await monter();
    // La cause du chevauchement : un `<button>` se dimensionne sur son contenu.
    // Sans `max-w-full`, il garde sa largeur naturelle dans un parent rétréci et
    // peint par-dessus le titre de page — invisible au lint comme au build.
    const selecteur = screen.getByRole("button", {
      name: /^Projet actif :/,
    });
    expect(selecteur.className).toContain("max-w-full");
  });

  it("garde au nom du projet un préfixe lisible, jamais une icône nue", async () => {
    await monter();
    // Ce qui sépare la variante retenue des deux écartées : le nom se
    // raccourcit (B l'effaçait), et jamais jusqu'à une lettre (C y descendait).
    const nom = screen.getByText("Refonte du portail client — Groupe Vallée");
    expect(nom.className).toContain("truncate");
    expect(nom.className).toContain("min-w-8");
    expect(nom.className).not.toContain("hidden");
  });
});

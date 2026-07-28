/**
 * Lot 5 de la refonte UX (#121) : la page Paramètres structurée.
 *
 * Le principe du lot est qu'« aucune section n'est un lien mort ni un
 * interrupteur sans effet » — c'est cela qui se teste ici, plus que la présence
 * des titres :
 *
 * - le sommaire (`lib/parametres`) et le contenu de la page ne peuvent pas
 *   diverger : chaque ancre déclarée doit trouver sa section dans le DOM, sans
 *   quoi le sous-menu proposerait un saut dans le vide ;
 * - le repère du sous-menu (`DECALAGE_ANCRE_PX`) doit valoir exactement le
 *   `scroll-mt-20` des sections — les désaccorder suffit à ce qu'un clic sur
 *   une entrée en surligne une autre ;
 * - le réglage d'apparence est la **même commande** que celle de la barre
 *   supérieure (couvert par `theme.test.tsx`), pas une copie.
 */

import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { NavigationParametres } from "@/components/parametres/NavigationParametres";
import { SectionParametres as CadreSection } from "@/components/parametres/SectionParametres";
import {
  CLE_SIDEBAR_REPLIEE,
  ecouterRepliSidebar,
  ecrireRepliSidebar,
  lireRepliSidebar,
} from "@/lib/preferences";
import { DECALAGE_ANCRE_PX, SECTIONS_PARAMETRES } from "@/lib/parametres";

const racine = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");

describe("le sommaire des Paramètres (lib/parametres)", () => {
  it("couvre les six domaines de configuration annoncés", () => {
    expect(SECTIONS_PARAMETRES.map((section) => section.id)).toEqual([
      "general",
      "apparence",
      "agents",
      "fournisseurs",
      "couts",
      "notifications",
    ]);
  });

  it("décrit chaque section en une phrase", () => {
    // L'en-tête de section affiche cette description : une section muette
    // laisserait l'utilisateur deviner ce qu'elle règle.
    for (const section of SECTIONS_PARAMETRES) {
      expect(section.libelle).not.toBe("");
      expect(section.description).not.toBe("");
    }
  });

  it("garde le repère du sous-menu accordé au décalage d'ancre des sections", () => {
    // `DECALAGE_ANCRE_PX` (80) doit valoir le `scroll-mt-20` (5rem = 80 px) que
    // le cadre de section applique : c'est là que l'ancre dépose la section, et
    // donc là que le sous-menu doit la reconnaître comme courante.
    const source = readFileSync(
      path.join(racine, "components/parametres/SectionParametres.tsx"),
      "utf8",
    );
    expect(source).toContain("scroll-mt-20");
    expect(DECALAGE_ANCRE_PX).toBe(80);
  });
});

describe("le sous-menu (NavigationParametres)", () => {
  it("propose une ancre par section, dans l'ordre du sommaire", () => {
    render(<NavigationParametres />);
    const liens = within(
      screen.getByRole("navigation", { name: "Sections des paramètres" }),
    ).getAllByRole("link");
    expect(liens.map((lien) => lien.getAttribute("href"))).toEqual(
      SECTIONS_PARAMETRES.map((section) => `#${section.id}`),
    );
  });

  it("désigne la première section tant qu'on n'a pas défilé", async () => {
    render(<NavigationParametres />);
    await waitFor(() =>
      expect(screen.getByRole("link", { name: "Général" })).toHaveAttribute(
        "aria-current",
        "true",
      ),
    );
  });

  it("ne désigne qu'une seule section à la fois", async () => {
    render(<NavigationParametres />);
    await waitFor(() => {
      const designees = screen
        .getAllByRole("link")
        .filter((lien) => lien.getAttribute("aria-current") === "true");
      expect(designees).toHaveLength(1);
    });
  });
});

describe("le cadre d'une section (SectionParametres)", () => {
  it("porte l'ancre du sommaire et l'intitulé de la section", () => {
    const section = SECTIONS_PARAMETRES[0];
    const { container } = render(
      <CadreSection section={section}>
        <p>contenu</p>
      </CadreSection>,
    );
    const noeud = container.querySelector(`#${section.id}`);
    expect(noeud).not.toBeNull();
    expect(screen.getByRole("region", { name: section.libelle })).toBeInTheDocument();
    expect(screen.getByText(section.description)).toBeInTheDocument();
  });

  it("réserve la place de l'ancre sous la barre supérieure collante", () => {
    const { container } = render(
      <CadreSection section={SECTIONS_PARAMETRES[0]}>
        <p>contenu</p>
      </CadreSection>,
    );
    expect(container.querySelector("section")).toHaveClass("scroll-mt-20");
  });
});

describe("le repli de la barre latérale (lib/preferences)", () => {
  it("est déplié à la première visite", () => {
    expect(lireRepliSidebar()).toBe(false);
  });

  it("se mémorise et se relit", () => {
    ecrireRepliSidebar(true);
    expect(window.localStorage.getItem(CLE_SIDEBAR_REPLIEE)).toBe("1");
    expect(lireRepliSidebar()).toBe(true);

    ecrireRepliSidebar(false);
    expect(lireRepliSidebar()).toBe(false);
  });

  it("notifie les autres contrôles de la même page", () => {
    // Le bouton de la barre supérieure et l'interrupteur des Paramètres sont la
    // même commande : sans cet événement interne, ils divergeraient.
    const vus: boolean[] = [];
    const detacher = ecouterRepliSidebar((repliee) => vus.push(repliee));
    ecrireRepliSidebar(true);
    detacher();
    ecrireRepliSidebar(false);
    expect(vus).toEqual([true]);
  });

  it("suit un changement venu d'un autre onglet", () => {
    const vus: boolean[] = [];
    const detacher = ecouterRepliSidebar((repliee) => vus.push(repliee));
    window.localStorage.setItem(CLE_SIDEBAR_REPLIEE, "1");
    window.dispatchEvent(
      new StorageEvent("storage", { key: CLE_SIDEBAR_REPLIEE }),
    );
    detacher();
    expect(vus).toEqual([true]);
  });

  it("ignore les autres clés du stockage", () => {
    const vus: boolean[] = [];
    const detacher = ecouterRepliSidebar((repliee) => vus.push(repliee));
    window.dispatchEvent(new StorageEvent("storage", { key: "maestro.theme" }));
    detacher();
    expect(vus).toEqual([]);
  });
});

describe("l'interrupteur de repli des Paramètres", () => {
  it("bascule le repli et le mémorise", async () => {
    const { ParametresApparence } = await import(
      "@/components/parametres/ParametresApparence"
    );
    const utilisateur = userEvent.setup();
    render(<ParametresApparence />);

    const interrupteur = screen.getByRole("switch", {
      name: "Barre latérale repliée",
    });
    await waitFor(() => expect(interrupteur).toHaveAttribute("aria-checked", "false"));

    await utilisateur.click(interrupteur);
    await waitFor(() => expect(interrupteur).toHaveAttribute("aria-checked", "true"));
    expect(lireRepliSidebar()).toBe(true);
  });
});

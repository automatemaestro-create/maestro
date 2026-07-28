/**
 * Lot 4 de la refonte UX (#120) : la nouvelle identité visuelle — le monogramme
 * « M » qui remplace l'emoji 🎼, décliné du logo in-app au favicon.
 *
 * L'enjeu de ce lot n'est pas le rendu (un tracé SVG ne régresse pas tout
 * seul), c'est la **dérive** : la même géométrie est écrite à trois endroits —
 * `components/Logo.tsx` pour l'interface, `app/icon.svg` pour l'onglet, et
 * `scripts/build-icons.mjs` pour les binaires iOS/ICO. Rien dans le lint ni
 * dans `next build` ne remarquerait qu'on en a retouché un seul : le logo de la
 * sidebar et celui de l'onglet se mettraient à diverger en silence. Ces tests
 * confrontent les trois sources.
 */

import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { LogoMaestro } from "@/components/Logo";

const racine = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const lire = (relatif: string) =>
  readFileSync(path.join(racine, relatif), "utf8");

/** Le tracé du « M » — la seule forme qui doit se retrouver partout. */
const TRACE_MONOGRAMME = "M7 24V10l9 8 9-8v14";

describe("le logo in-app (LogoMaestro)", () => {
  it("porte le monogramme sur la grille de 32", () => {
    const { container } = render(<LogoMaestro />);
    const svg = container.querySelector("svg");
    expect(svg).toHaveAttribute("viewBox", "0 0 32 32");
    expect(container.querySelector("path")).toHaveAttribute("d", TRACE_MONOGRAMME);
  });

  it("hérite de la couleur du texte", () => {
    // `currentColor` est ce qui le rend lisible en clair comme en sombre sans
    // seconde version du fichier : une couleur en dur casserait le thème #118.
    const { container } = render(<LogoMaestro />);
    expect(container.querySelector("path")).toHaveAttribute(
      "stroke",
      "currentColor",
    );
    expect(container.querySelector("circle")).toHaveAttribute(
      "fill",
      "currentColor",
    );
  });

  it("reste décoratif pour les lecteurs d'écran", () => {
    // Le lien qui le porte est déjà intitulé « Maestro — Control Tower » :
    // annoncer le glyphe en plus ne ferait que doubler la lecture.
    render(<LogoMaestro data-testid="logo" />);
    expect(screen.getByTestId("logo")).toHaveAttribute("aria-hidden", "true");
  });

  it("accepte les classes du contexte qui l'affiche", () => {
    // La sidebar le dimensionne (`size-7`) et le rétrécit en version repliée.
    render(<LogoMaestro className="size-7" data-testid="logo" />);
    expect(screen.getByTestId("logo")).toHaveClass("size-7");
  });
});

describe("la déclinaison du monogramme", () => {
  it("est la même dans le favicon SVG que dans le logo in-app", () => {
    expect(lire("app/icon.svg")).toContain(TRACE_MONOGRAMME);
  });

  it("est la même dans le générateur d'icônes binaires", () => {
    expect(lire("scripts/build-icons.mjs")).toContain(TRACE_MONOGRAMME);
  });

  it("garde la levée du chef d'orchestre centrée au même point", () => {
    // Le point au-dessus de l'échancrure : son rayon diffère volontairement
    // (2,3 en app, 2,1 sur la tuile du favicon), sa position non.
    for (const fichier of ["app/icon.svg", "scripts/build-icons.mjs"]) {
      expect(lire(fichier)).toMatch(/cx="16"\s+cy="6\.75"/);
    }
  });

  it("donne au favicon un fond opaque, contrairement au logo in-app", () => {
    // Un favicon n'a pas de contexte de page : sans tuile, le « M » clair
    // disparaîtrait sur un onglet clair.
    const svg = lire("app/icon.svg");
    expect(svg).toContain("<rect");
    expect(svg).toContain("prefers-color-scheme: dark");
  });
});

describe("les icônes applicatives générées", () => {
  const octets = (relatif: string) =>
    readFileSync(path.join(racine, relatif));

  it("livre un favicon.ico multi-tailles", () => {
    // En-tête ICO : 2 octets réservés (0), 2 octets de type (1 = icône), 2
    // octets de compte. Le script en promet trois (16/32/48).
    const ico = octets("app/favicon.ico");
    expect(ico.readUInt16LE(0)).toBe(0);
    expect(ico.readUInt16LE(2)).toBe(1);
    expect(ico.readUInt16LE(4)).toBe(3);
    expect([ico.readUInt8(6), ico.readUInt8(22), ico.readUInt8(38)]).toEqual([
      16, 32, 48,
    ]);
  });

  it("livre une icône Apple de 180 px, pleine", () => {
    // Signature PNG puis dimensions dans le chunk IHDR (octets 16 à 24).
    const png = octets("app/apple-icon.png");
    expect(png.subarray(0, 8)).toEqual(
      Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    );
    expect(png.readUInt32BE(16)).toBe(180);
    expect(png.readUInt32BE(20)).toBe(180);
  });
});

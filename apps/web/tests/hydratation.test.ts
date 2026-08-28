/**
 * Ce que le layout racine tolère du dehors (#730).
 *
 * ── Pourquoi une sonde sur la SOURCE, et pas un rendu ────────────────────────
 *
 * Le symptôme ne se reproduit **nulle part automatiquement** : il faut un
 * navigateur, un rendu serveur à hydrater, et une extension installée qui
 * décore `<body>` avant que React ne passe (Grammarly y pose
 * `data-gr-ext-installed` et `data-new-gr-c-s-check-loaded`). jsdom n'hydrate
 * aucun HTML serveur et la CI n'a pas d'extensions — donc ni la suite ni le
 * pipeline ne verront jamais revenir l'erreur si l'attribut disparaît. Une
 * sonde sur les **octets du fichier** n'est pas ici un pis-aller : c'est le
 * seul filet qui puisse exister, et sans lui le correctif se défait au premier
 * refactor du layout sans que rien ne le dise.
 *
 * Elle garde **les deux** `suppressHydrationWarning`, et c'est le point : ils
 * ont l'air d'un doublon et n'en sont pas. Celui de `<html>` couvre le
 * `data-theme` que `SCRIPT_INIT_THEME` corrige avant le premier rendu (#118) ;
 * celui de `<body>` couvre ce que les extensions posent. Déplacer l'un sur
 * l'autre — le geste qu'on fait en croyant simplifier — ramène l'un des deux
 * écarts.
 *
 * ── Le piège que la sonde devait éviter ──────────────────────────────────────
 *
 * Le commentaire du layout **parle** de `<body>`, et il en parle *avant* la
 * balise. Une recherche naïve de `<body…>` tombe donc sur la prose, qui ne
 * porte évidemment pas l'attribut, et rend rouge un fichier correct. Les
 * commentaires sont retirés avant toute lecture, et l'échantillon fautif
 * ci-dessous **porte le piège** pour que ce retrait soit prouvé plutôt que
 * supposé.
 */

import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const RACINE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const LAYOUT = readFileSync(path.join(RACINE, "app", "layout.tsx"), "utf8");

// ─────────────────────────────────────────────────────────────────────────────
// LA SONDE
// ─────────────────────────────────────────────────────────────────────────────

/** La source débarrassée de ce qui n'est que de la prose. */
function sansCommentaires(source: string): string {
  return source
    .replace(/\{\/\*[\s\S]*?\*\/\}/g, "")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^\s*\/\/.*$/gm, "");
}

/**
 * `<balise …>` tolère-t-elle un écart d'hydratation sur ses propres attributs ?
 *
 * Refuse bruyamment quand la balise est absente, au lieu de rendre `false` :
 * une sonde qui ne trouve pas ce qu'elle juge doit le dire, pas conclure.
 */
function tolereLeDehors(source: string, balise: string): boolean {
  const ouvrante = sansCommentaires(source).match(
    new RegExp(`<${balise}\\b[^>]*>`),
  );
  if (ouvrante === null) {
    throw new Error(`<${balise}> introuvable dans la source examinée`);
  }
  return ouvrante[0].includes("suppressHydrationWarning");
}

/** Un layout fautif — et qui porte le piège : sa prose nomme `<body>`. */
const ECHANTILLON_FAUTIF = `
  {/* les extensions décorent <body> avant que React n'hydrate */}
  <body className="flex h-full flex-col">
    <Shell />
  </body>
`;

/** Le même, corrigé. */
const ECHANTILLON_CORRIGE = `
  {/* les extensions décorent <body> avant que React n'hydrate */}
  <body
    className="flex h-full flex-col"
    suppressHydrationWarning
  >
    <Shell />
  </body>
`;

// ─────────────────────────────────────────────────────────────────────────────
// 1. LA SONDE RÉPOND-ELLE À LA QUESTION QU'ON LUI POSE ?
// ─────────────────────────────────────────────────────────────────────────────

describe("la sonde", () => {
  it("voit l'absence de l'attribut, malgré une prose qui nomme la balise", () => {
    expect(tolereLeDehors(ECHANTILLON_FAUTIF, "body")).toBe(false);
  });

  it("voit sa présence", () => {
    expect(tolereLeDehors(ECHANTILLON_CORRIGE, "body")).toBe(true);
  });

  it("refuse de conclure quand la balise est absente", () => {
    expect(() => tolereLeDehors("<div />", "body")).toThrow(/introuvable/);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// 2. CE QUE LE LAYOUT RACINE DOIT TENIR
// ─────────────────────────────────────────────────────────────────────────────

describe("le layout racine", () => {
  it("tolère sur <body> les attributs que les extensions y posent (#730)", () => {
    expect(tolereLeDehors(LAYOUT, "body")).toBe(true);
  });

  it("garde celui de <html>, qui couvre un autre écart (#118)", () => {
    expect(tolereLeDehors(LAYOUT, "html")).toBe(true);
  });

  it("ne nomme aucune extension dans le code qu'il exécute", () => {
    // La classe entière est visée, pas un produit : un correctif par extension
    // serait à refaire à chaque nouvelle. Les commentaires, eux, ont le droit
    // de nommer Grammarly — c'est là qu'on explique d'où vient l'écart.
    expect(sansCommentaires(LAYOUT)).not.toMatch(/grammarly|data-gr-/i);
  });
});

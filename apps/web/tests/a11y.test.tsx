/**
 * Le filet d'accessibilité de la Control Tower (#537, lot 5 de #532).
 *
 * Le travail d'accessibilité du produit est sérieux — 104 `aria-label`, 44 rôles
 * corrects, un `<h1>` par écran, 0 saut de niveau (docs/30 §2.1). Ce qui
 * manquait n'était pas de la rigueur, c'était **ce qui la garde** : `axe-core`
 * était dans le dépôt depuis toujours, en transitif, et n'avait jamais été
 * importé une seule fois (docs/30 §3.4).
 *
 * Ce fichier porte **deux** des trois critères du lot — le troisième, le passage
 * de `plugin:jsx-a11y/recommended` en `error`, vit dans `eslint.config.mjs` et
 * n'a pas de test à lui : c'est le lint qui rougit. L'ordre des `describe`
 * ci-dessous est celui de leur dépendance :
 *
 * 1. **la sonde est prouvée avant de servir** — sur un échantillon fautif, puis
 *    sur un fragment sain. Sans cette première moitié, un audit mal branché
 *    (mauvais contexte, règles toutes désactivées) rendrait un ✓ sur une
 *    question jamais posée. C'est la méthode de `contraste.test.ts` (#534), et
 *    c'est ce qui distingue un filet d'un instantané ;
 * 2. **les 10 écrans**, montés dans leur shell réel et audités — table dérivée
 *    de `MENU`, jamais recopiée : un écran ajouté au menu sans cas d'audit fait
 *    rougir, plutôt que de passer inaperçu ;
 * 3. **les acquis qu'axe ne sait pas voir** — le lien d'évitement (rendu), la
 *    garde de mouvement (balayage des sources) et la taille des cibles (rendu,
 *    sur les classes déclarées). Chacun est gardé là où il est observable, et
 *    aucun ne mesure un pixel : jsdom n'en calcule aucun, et c'est la frontière
 *    de #308 — le pixel appartient au skill `/banc-mise-en-page`.
 *
 * ⚠ Le réseau est débranché deux fois, et il faut les deux : `tests/setup.ts`
 * mocke `useControlTower`/`useChat`, mais **ni `chargerCatalogue`, ni
 * `chargerSante`, ni le pool MCP, ni l'explorateur** (piège documenté dans
 * `apps/web/README.md`). Un écran qui les lit partirait sur un vrai `fetch` et
 * n'offrirait à l'audit qu'une bannière d'erreur — donc un écran vert parce
 * qu'il est vide. Les mocks ci-dessous **remplacent** ceux du setup, d'où la
 * reconduction de `chargerProjets`/`chargerJournal`.
 *
 * Le harnais — les dix écrans, leur état, leur montage — vit dans `./ecrans`
 * depuis #539 : `sobriete.test.tsx` monte exactement les mêmes pages dans le
 * même état, et deux tables recopiées seraient le premier moyen qu'une suite
 * audite un produit que l'autre ne monte plus.
 */

import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ID_CONTENU_PRINCIPAL } from "@/components/Shell";
import { marquerGuideVu } from "@/lib/guide";
import { MENU } from "@/lib/navigation";

import { auditerLaPage, bloquantes, raconter } from "./axe";
import { ECRANS, monterEcran, peuplerEtat } from "./ecrans";
import { poserProjetActif } from "./aides";

const racine = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");

const lireSource = (relatif: string) =>
  readFileSync(path.join(racine, relatif), "utf8");

/**
 * Tout ce que le produit rend : les écrans (`app/`) et les composants. La liste
 * est **parcourue** et non écrite — un fichier neuf entre dans le périmètre du
 * jour où il est créé, ce qui est la seule façon qu'un balayage reste vrai.
 */
function sourcesDuProduit(): string[] {
  return ["app", "components"].flatMap((dossier) =>
    readdirSync(path.join(racine, dossier), { recursive: true })
      .map(String)
      .filter((f) => f.endsWith(".tsx"))
      .map((f) => path.posix.join(dossier, f.split(path.sep).join("/"))),
  );
}

// --- Le réseau, pour de bon ------------------------------------------------

vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  const { mocksApi } = await import("./ecrans-reseau");
  return { ...reel, ...mocksApi() };
});

/** Mock **partiel** : `PERIODES`, que `/couts` lit à côté du hook, passe tel quel. */
vi.mock("@/lib/useAnalyticsCouts", async (original) => {
  const { mockAnalytics } = await import("./ecrans-reseau");
  return { ...(await original<Record<string, unknown>>()), ...mockAnalytics() };
});

// --- 1. La sonde, prouvée avant de servir -----------------------------------

describe("la sonde d'accessibilité (tests/axe.ts)", () => {
  it("trouve les fautes qu'elle est censée trouver", async () => {
    // Trois fautes de trois familles différentes : une image sans alternative
    // (`critical`), un bouton sans nom accessible (`critical`), un champ sans
    // étiquette (`serious`). Si l'audit était mal branché — mauvais contexte,
    // règles éteintes — il rendrait « aucune violation » sur ce fragment-ci
    // exactement comme sur un écran sain. La troisième n'est pas décorative :
    // c'est la seule `serious` du lot, donc la seule qui prouve que le seuil du
    // ticket descend bien sous `critical`.
    //
    // ⚠ Les deux exemptions ci-dessous sont **la preuve que l'autre moitié du
    // lot fonctionne** : depuis que `jsx-a11y/recommended` est en `error`, ce
    // fragment ne compile plus au lint — c'est exactement ce qu'on lui demande
    // partout ailleurs. Il faut donc le dire ici, à la ligne près et pour ces
    // règles-là ; un `eslint-disable` de fichier éteindrait aussi le fragment
    // sain d'à côté, qui doit rester jugé.
    render(
      <div>
        {/* eslint-disable-next-line jsx-a11y/alt-text, @next/next/no-img-element */}
        <img src="/x.png" />
        <button type="button" />
        <input type="text" />
      </div>,
    );
    const trouvees = bloquantes(await auditerLaPage());
    expect(trouvees.map((v) => v.id).sort()).toEqual(
      expect.arrayContaining(["button-name", "image-alt", "label"]),
    );
  });

  it("ne rend rien sur un fragment sain", async () => {
    // Le pendant du contrôle ci-dessus : une sonde qui crierait sur tout ne
    // dirait pas davantage qu'une sonde muette.
    render(
      <main>
        <h1>Titre</h1>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src="/x.png" alt="Un graphique" />
        <button type="button">Agir</button>
      </main>,
    );
    const violations = await auditerLaPage();
    expect(bloquantes(violations), raconter(violations)).toHaveLength(0);
  });

  it("garde ce que le harnais ne peut pas juger : langue et titre du document", () => {
    // `html-has-lang` et `document-title` sont écartées de l'audit : le `<html>`
    // vient de `app/layout.tsx` et le titre de son `metadata`, deux choses que
    // le rendu d'un composant ne monte pas — jsdom sert le sien, nu. Les règles
    // ne disparaissent pas pour autant, elles changent de juge : c'est la source
    // du layout qui répond, comme `globals.css` répond du contraste (#534).
    const layout = lireSource("app/layout.tsx");
    expect(layout).toContain('lang="fr"');
    expect(layout).toMatch(/title:\s*"[^"]+"/);
  });
});

// --- 2. Les dix écrans ------------------------------------------------------

describe("les dix écrans face à axe-core", () => {
  beforeEach(() => {
    marquerGuideVu();
    poserProjetActif();
    peuplerEtat();
  });

  it("audite exactement les écrans du menu", () => {
    // La table ci-dessus est **dérivée**, pas recopiée : une page ajoutée au
    // menu sans cas d'audit fait rougir ici, au lieu d'échapper au filet en
    // silence. C'est le même contrôle que celui qui confronte les entrées de
    // menu aux routes réelles (`navigation.test.tsx`).
    expect(ECRANS.map((e) => e.href)).toEqual(MENU.map((e) => e.href));
  });

  for (const ecran of ECRANS) {
    it(`ne laisse aucune violation serious/critical sur ${ecran.href}`, async () => {
      await monterEcran(ecran);
      const violations = await auditerLaPage();
      expect(bloquantes(violations), `\n${raconter(violations)}\n`).toHaveLength(0);
    });
  }
});

// --- 3. Ce qu'axe ne voit pas ----------------------------------------------

describe("le lien d'évitement (WCAG 2.2 §2.4.1)", () => {
  beforeEach(() => {
    marquerGuideVu();
    poserProjetActif();
    peuplerEtat();
  });

  it("est le premier arrêt de la tabulation, et vise le contenu", async () => {
    await monterEcran(ECRANS[0]);
    const lien = screen.getByRole("link", { name: "Aller au contenu principal" });
    expect(lien).toHaveAttribute("href", `#${ID_CONTENU_PRINCIPAL}`);

    // « Premier » se vérifie dans l'ordre du DOM et non à l'écran : c'est lui
    // qui décide de l'ordre de tabulation, et un lien d'évitement qui arrive
    // après la navigation ne sert à rien — c'est précisément la navigation
    // qu'il fait sauter.
    const focalisables = document.querySelectorAll<HTMLElement>(
      "a[href], button, input, select, textarea, [tabindex]:not([tabindex='-1'])",
    );
    expect(focalisables[0]).toBe(lien);
  });

  it("mène à un `<main>` que le focus peut atteindre", async () => {
    await monterEcran(ECRANS[0]);
    const contenu = document.getElementById(ID_CONTENU_PRINCIPAL);
    expect(contenu?.tagName).toBe("MAIN");
    // Sans `tabindex="-1"`, suivre l'ancre déplace le point d'insertion du
    // document mais pas le focus : la tabulation suivante repartirait du menu.
    expect(contenu).toHaveAttribute("tabindex", "-1");
    // Et il doit être **atteignable au clavier sans être une étape** : un
    // `tabindex="0"` ajouterait un arrêt de tabulation sur une zone entière.
    contenu?.focus();
    expect(document.activeElement).toBe(contenu);
  });
});

describe("la garde de mouvement (WCAG 2.2 §2.3.3)", () => {
  /**
   * Toute utilité Tailwind d'animation trouvée dans une chaîne de classes, avec
   * le fait de savoir si elle est gardée.
   *
   * Le balayage se fait **sur les chaînes littérales, commentaires retirés** —
   * pas sur les lignes brutes. Sans les deux filtres, la prose du dépôt ferait
   * le gros du résultat : ce fichier-ci, comme `GuidePriseEnMain`, parle de
   * « transition » en français et cite `transition-none` entre accents graves.
   */
  function chainesDeClasses(source: string): string[][] {
    const sansCommentaires = source
      .replace(/\/\*[\s\S]*?\*\//g, " ")
      .replace(/(^|[^:])\/\/[^\n]*/g, "$1");
    return [
      ...sansCommentaires.matchAll(/"([^"\n]*)"|'([^'\n]*)'|`([^`]*)`/g),
    ].map(([, guillemets, apostrophes, gabarit]) =>
      (guillemets ?? apostrophes ?? gabarit ?? "")
        .split(/[\s${}]+/)
        // Un gabarit avale les chaînes qu'il interpole : les guillemets de
        // l'expression ternaire restent collés au jeton (`"animate-pulse`).
        .map((jeton) => jeton.replace(/^["'`]+|["'`]+$/g, "")),
    );
  }

  /** Ce qui bouge dans une chaîne de classes, gardé ou non. */
  function mouvementsDe(jetons: string[]): { nus: string[]; gardes: number } {
    const nus = jetons.filter(
      (j) =>
        /^(transition(-\[[^\]]*\]|-[a-z]+)?|animate-[a-z-]+)$/.test(j) &&
        j !== "transition-none" &&
        j !== "animate-none",
    );
    const gardes = jetons.filter((j) =>
      /^motion-reduce:(transition|animate)-none$/.test(j),
    ).length;
    return { nus, gardes };
  }

  it("reconnaît une transition nue, et ne crie pas sur une transition gardée", () => {
    // La sonde avant ce qu'elle mesure, comme plus haut : un balayage dont le
    // motif ne matcherait plus rien rendrait « 0 transition nue » avec les mots
    // de « tout est gardé ».
    const lire = (source: string) => chainesDeClasses(source).map(mouvementsDe);
    expect(lire('"gap-2 transition-colors"')).toEqual([
      { nus: ["transition-colors"], gardes: 0 },
    ]);
    expect(lire('"animate-pulse motion-reduce:animate-none"')).toEqual([
      { nus: ["animate-pulse"], gardes: 1 },
    ]);
    // La forme du produit : une garde interpolée dans un gabarit.
    expect(lire('`x ${a ? "transition motion-reduce:transition-none" : ""}`')).toEqual(
      [{ nus: ["transition"], gardes: 1 }],
    );
    // Et ce qu'il doit ignorer : la prose, et l'utilité qui *est* la garde.
    expect(lire("/* une `transition` douce */")).toEqual([]);
    expect(lire('"transition-none"')).toEqual([{ nus: [], gardes: 0 }]);
  });

  it("garde chaque transition et chaque animation du produit", () => {
    // Le contrôle est **par chaîne de classes** et non par fichier : deux
    // gardes sur une chaîne ne rachètent pas une transition nue sur la chaîne
    // d'à côté, et c'est bien ce qui se passerait dans un fichier qui en porte
    // trois (`VuePipeline`).
    const fautifs: string[] = [];
    let mouvements = 0;
    for (const fichier of sourcesDuProduit()) {
      for (const jetons of chainesDeClasses(lireSource(fichier))) {
        const { nus, gardes } = mouvementsDe(jetons);
        mouvements += nus.length;
        if (nus.length > gardes) {
          fautifs.push(`  ${fichier} — sans garde : ${nus.join(", ")}`);
        }
      }
    }
    expect(fautifs, `\n${fautifs.join("\n")}\n`).toHaveLength(0);
    // Le plancher rend le ✓ opposable : un motif devenu muet — utilité Tailwind
    // renommée, chaînes construites autrement — rendrait « aucune transition
    // nue » avec les mots de « tout est gardé ». 19 est le compte **mesuré** au
    // lot (15 transitions, 4 animations) ; docs/30 §3.4 en relevait 19 et 4
    // avant que les écrans de #472 ne bougent. Un lot qui en retire
    // légitimement une baisse ce chiffre — comme #534 fait de ses 36 paires.
    expect(mouvements).toBeGreaterThanOrEqual(19);
  });
});

describe("la taille des cibles (WCAG 2.2 §2.5.8)", () => {
  /**
   * ⚠ Ce que ce contrôle **ne fait pas** : mesurer. jsdom ne calcule ni
   * hauteur, ni interligne, ni marge — c'est la frontière posée par #308, et le
   * pixel appartient au skill `/banc-mise-en-page`. Ce qui se garde ici est la
   * **déclaration** : une cible dont la feuille de classes ne promet pas 24 px.
   *
   * Il ne juge donc que les cibles qui **portent leur propre pas
   * typographique** (`text-annexe`, `text-micro`, `text-xs`) : sans lui, la
   * hauteur dépend d'un interligne hérité que rien ici ne connaît, et flaguer
   * au hasard ferait d'un filet une nuisance qu'on finit par éteindre. Ce n'est
   * pas un périmètre arbitraire : c'est **exactement** la famille où le défaut a
   * été mesuré — « quelques liens de renvoi à 22 px » (docs/30 §3.4), tous
   * écrits en petit corps sans plancher.
   *
   * Les variantes tombent avant l'examen (`focus:py-2` compte comme `py-2`) :
   * le lien d'évitement n'existe qu'au focus, et le juger sur son état caché
   * reviendrait à ne pas le juger. Le pas Tailwind vaut `0.25rem` — `6` = 24 px
   * pour une hauteur, `1.5` = 12 px de marge, qui s'ajoutent deux fois à une
   * ligne d'au moins 16 px.
   */
  const PAS_MENUS = ["text-annexe", "text-micro", "text-xs"];

  function utilites(classes: string): string[] {
    return classes
      .split(/\s+/)
      .map((jeton) => jeton.slice(jeton.lastIndexOf(":") + 1));
  }

  /** La cible écrit-elle son propre corps de texte — donc sa propre hauteur ? */
  function porteSonPas(classes: string): boolean {
    return utilites(classes).some((u) => PAS_MENUS.includes(u));
  }

  function declareUnPlancher(classes: string): boolean {
    return utilites(classes).some((u) => {
      const hauteur = /^(?:min-h|h|size)-(\d+(?:\.\d+)?)$/.exec(u);
      if (hauteur) return Number(hauteur[1]) >= 6;
      const marge = /^(?:p|py)-(\d+(?:\.\d+)?)$/.exec(u);
      return marge !== null && Number(marge[1]) >= 1.5;
    });
  }

  it("reconnaît un plancher, et refuse ce qui n'en a pas", () => {
    expect(declareUnPlancher("inline-flex text-annexe")).toBe(false);
    expect(declareUnPlancher("inline-flex min-h-6 text-annexe")).toBe(true);
    expect(declareUnPlancher("rounded px-3 py-1.5")).toBe(true);
    expect(declareUnPlancher("rounded px-3 py-0.5")).toBe(false);
    expect(declareUnPlancher("sr-only focus:not-sr-only focus:py-2")).toBe(true);
    expect(declareUnPlancher("size-12 rounded-full")).toBe(true);
  });

  it("ne se prononce que sur les cibles qui portent leur pas", () => {
    // Le pendant du contrôle ci-dessus : sans cette borne, le sélecteur de
    // projet (`px-2 py-1 text-sm`, 28 px en vrai) serait rendu fautif par un
    // `py-1` lu hors de son interligne — un faux positif par écran.
    expect(porteSonPas("inline-flex text-annexe font-medium")).toBe(true);
    expect(porteSonPas("rounded-md px-2 py-1 text-sm")).toBe(false);
  });

  beforeEach(() => {
    marquerGuideVu();
    poserProjetActif();
    peuplerEtat();
  });

  for (const ecran of ECRANS) {
    it(`ne laisse aucune cible sous 24 px sur ${ecran.href}`, async () => {
      await monterEcran(ecran);
      const maigres = [...document.querySelectorAll<HTMLElement>("a[href], button")]
        .map((cible) => ({ cible, classes: cible.getAttribute("class") ?? "" }))
        .filter(
          ({ classes }) => porteSonPas(classes) && !declareUnPlancher(classes),
        )
        .map(
          ({ cible, classes }) =>
            `  <${cible.tagName.toLowerCase()}> « ${(cible.textContent ?? "").trim().slice(0, 40)} » — ${classes}`,
        );
      expect(maigres, `\n${maigres.join("\n")}\n`).toHaveLength(0);
    });
  }
});

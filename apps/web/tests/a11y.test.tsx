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
 * Une source sans ses commentaires. Les deux balayages de ce fichier lisent
 * des **chaînes**, pas des lignes, et la prose du dépôt ferait le gros du
 * résultat sans ce filtre : ce fichier-ci, comme `GuidePriseEnMain`, parle de
 * « transition » en français, et #832 cite `focus:border-emerald-500` en toutes
 * lettres dans le commentaire qui explique pourquoi il n'y est plus.
 */
function sansCommentaires(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, " ")
    .replace(/(^|[^:])\/\/[^\n]*/g, "$1");
}

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
   * pas sur les lignes brutes (voir `sansCommentaires`).
   */
  function chainesDeClasses(source: string): string[][] {
    return [
      ...sansCommentaires(source).matchAll(/"([^"\n]*)"|'([^'\n]*)'|`([^`]*)`/g),
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

describe("le contrôle de saisie écrit hors des tokens (WCAG 2.2 §1.4.11)", () => {
  /**
   * Ce que #832 a relevé, au titre du §5 de `/design-veille` (« si le même
   * refus revient sur plusieurs surfaces, ce n'est plus un refus mais un manque
   * du socle ») : `CadreChamp` rendait **toujours** un libellé visible, donc les
   * trois composeurs du produit contournaient la primitive entière et
   * réécrivaient leur champ à la main — en couleurs brutes, avec un
   * `focus:border-emerald-500` qui n'existe nulle part dans le socle, et sans
   * le `focus:border-bord-fort` qui **identifie un contrôle** (§1.4.11) ni le
   * contour clavier. C'est la mécanique de docs/30 §2.2 : la primitive ne
   * couvre pas un cas, donc on la contourne, donc la palette cesse de tenir.
   *
   * Ce balayage-ci ne lit pas des chaînes au hasard mais **les contrôles de
   * saisie** — `<input>`, `<textarea>`, `<select>` — et la feuille de classes
   * que chacun porte, en résolvant `className={CLASSE_CHAMP}` par la constante
   * du fichier : c'est la forme sous laquelle **toutes** les recopies existent,
   * et un balayage des seules chaînes littérales n'en aurait vu aucune. Ce qu'il
   * ne sait pas lire — une constante définie ailleurs —, il le **refuse** au
   * lieu de le sauter (règle de #534) : une feuille illisible n'est pas une
   * feuille saine.
   */

  /** Une couleur brute de Tailwind, là où le socle met un token. */
  const COULEUR_BRUTE =
    /^(?:border|bg|text|placeholder|ring|outline|caret|accent|divide|from|via|to)-(?:white|black|neutral|gray|zinc|slate|stone|emerald|green|teal|cyan|sky|blue|indigo|violet|rose|red|orange|amber|yellow)(?:-\d{2,3})?(?:\/\d+)?$/;

  /** `dark:focus:border-x` → `border-x` : les variantes tombent, c'est l'utilité qui est jugée. */
  const utilite = (jeton: string) => jeton.slice(jeton.lastIndexOf(":") + 1);

  const litteraux = (expression: string): string[] =>
    [...expression.matchAll(/"([^"\n]*)"|'([^'\n]*)'|`([^`]*)`/g)].map(
      ([, a, b, c]) => a ?? b ?? c ?? "",
    );

  /**
   * Les constantes de chaîne d'un fichier, par leur nom — `const CLASSE_CHAMP =
   * "…" + "…";`, sur autant de lignes qu'il faut. La valeur est gardée telle
   * qu'écrite : un gabarit peut nommer une autre constante.
   */
  function constantesDe(source: string): Map<string, string> {
    const table = new Map<string, string>();
    for (const [, nom, valeur] of source.matchAll(
      /\bconst\s+([A-Za-z_$][\w$]*)\s*=\s*((?:(?:"[^"\n]*"|'[^'\n]*'|`[^`]*`)\s*\+?\s*)+);/g,
    )) {
      table.set(nom, valeur);
    }
    return table;
  }

  /** Ce qu'un fichier importe du socle — les seuls noms résolus hors de lui. */
  function importsDuSocle(source: string): Set<string> {
    const bloc = /import\s*\{([^}]*)\}\s*from\s*"@\/components\/Primitives"/.exec(source);
    return new Set(
      (bloc?.[1] ?? "")
        .split(",")
        .map((nom) => nom.replace(/^\s*type\s+/, "").trim())
        .filter(Boolean),
    );
  }

  type Tables = {
    locales: Map<string, string>;
    importees: Set<string>;
    socle: Map<string, string>;
  };

  /**
   * Les jetons de classe d'une expression `className`, constantes résolues —
   * et les noms qu'on n'a **pas** su résoudre. Une constante en MAJUSCULES qui
   * ne vient ni du fichier ni du socle est illisible ; un identifiant en
   * minuscules (`occupe ? … : …`) est une condition, pas une feuille.
   */
  function resoudre(
    expression: string,
    tables: Tables,
    vus = new Set<string>(),
  ): { jetons: string[]; illisibles: string[] } {
    const jetons: string[] = [];
    const illisibles: string[] = [];
    const noms = new Set<string>();
    for (const [, nom] of expression.matchAll(/\$\{\s*([A-Za-z_$][\w$]*)\s*\}/g)) {
      noms.add(nom);
    }
    const horsChaines = expression.replace(/"[^"\n]*"|'[^'\n]*'|`[^`]*`/g, " ");
    for (const [nom] of horsChaines.matchAll(/\b[A-Z][A-Z0-9_]{2,}\b/g)) noms.add(nom);
    for (const nom of noms) {
      if (vus.has(nom)) continue;
      vus.add(nom);
      const locale = tables.locales.get(nom);
      const definition =
        locale ?? (tables.importees.has(nom) ? tables.socle.get(nom) : undefined);
      if (definition === undefined) {
        illisibles.push(nom);
        continue;
      }
      const suite = resoudre(
        definition,
        locale === undefined ? { ...tables, locales: tables.socle } : tables,
        vus,
      );
      jetons.push(...suite.jetons);
      illisibles.push(...suite.illisibles);
    }
    for (const litteral of litteraux(expression)) {
      // Un gabarit avale les chaînes qu'il interpole : `${a ? "x" : "y"}` rend
      // ses deux branches, `${NOM}` a été résolu plus haut.
      const aplati = litteral.replace(/\$\{([^}]*)\}/g, (_, dedans: string) =>
        litteraux(dedans).join(" "),
      );
      jetons.push(...aplati.split(/\s+/).filter(Boolean));
    }
    return { jetons, illisibles };
  }

  /** L'indice du `>` qui ferme une balise ouvrante — hors accolades, et pas celui d'une flèche. */
  function finDeBalise(texte: string, depuis: number): number {
    let profondeur = 0;
    for (let i = depuis; i < texte.length; i++) {
      const c = texte[i];
      if (c === "{") profondeur += 1;
      else if (c === "}") profondeur -= 1;
      else if (c === ">" && profondeur === 0 && texte[i - 1] !== "=") return i;
    }
    return texte.length;
  }

  /** L'expression d'un `className={…}` — accolades appariées — ou d'un `className="…"`. */
  function expressionClassName(attributs: string): string | null {
    const debut = /\bclassName=/.exec(attributs);
    if (!debut) return null;
    const apres = debut.index + debut[0].length;
    if (attributs[apres] === '"') {
      return `"${attributs.slice(apres + 1, attributs.indexOf('"', apres + 1))}"`;
    }
    let profondeur = 0;
    for (let i = apres; i < attributs.length; i++) {
      if (attributs[i] === "{") profondeur += 1;
      else if (attributs[i] === "}") {
        profondeur -= 1;
        if (profondeur === 0) return attributs.slice(apres + 1, i);
      }
    }
    return attributs.slice(apres);
  }

  type Champ = { balise: string; brutes: string[]; illisibles: string[] };

  /** Chaque contrôle de saisie d'une source, avec ce que sa feuille de classes a de brut ou d'illisible. */
  function champsDe(source: string, socle: Map<string, string>): Champ[] {
    const propre = sansCommentaires(source);
    const tables: Tables = {
      locales: constantesDe(propre),
      importees: importsDuSocle(propre),
      socle,
    };
    const champs: Champ[] = [];
    for (const ouverture of propre.matchAll(/<(input|textarea|select)\b/g)) {
      const debut = (ouverture.index ?? 0) + ouverture[0].length;
      const attributs = propre.slice(debut, finDeBalise(propre, debut));
      const expression = expressionClassName(attributs);
      if (expression === null) {
        champs.push({ balise: ouverture[1], brutes: [], illisibles: [] });
        continue;
      }
      const { jetons, illisibles } = resoudre(expression, tables);
      if (jetons.length === 0 && illisibles.length === 0) {
        illisibles.push("(aucune chaîne de classes lisible)");
      }
      champs.push({
        balise: ouverture[1],
        brutes: jetons.filter((j) => COULEUR_BRUTE.test(utilite(j))),
        illisibles,
      });
    }
    return champs;
  }

  /** Le socle, lu une fois : c'est lui que `className={CLASSE_CONTROLE}` désigne. */
  const SOCLE = constantesDe(sansCommentaires(lireSource("components/Primitives.tsx")));

  it("reconnaît un contrôle en couleurs brutes, et sait lire la constante qui les porte", () => {
    // La sonde avant ce qu'elle mesure : un balayage dont le motif ne matcherait
    // plus rien, ou qui ne résoudrait plus les constantes, rendrait « aucun
    // contrôle hors des tokens » avec les mots de « tout est dans le socle ».
    const lire = (source: string) => champsDe(source, new Map());

    // La recopie telle qu'elle vivait dans les trois composeurs avant #832.
    expect(
      lire(
        'const CLASSE_CHAMP =\n  "w-full border border-neutral-200 focus:border-emerald-500 dark:bg-neutral-900";\n' +
          "<input id={idUrl} className={CLASSE_CHAMP} />",
      ),
    ).toEqual([
      {
        balise: "input",
        brutes: ["border-neutral-200", "focus:border-emerald-500", "dark:bg-neutral-900"],
        illisibles: [],
      },
    ]);
    // …concaténée à un `font-mono` — la forme d'`EditeurAgent`.
    expect(
      lire('const CLASSE_CHAMP = "border-neutral-200";\n<textarea className={CLASSE_CHAMP + " font-mono"} />')[0]
        .brutes,
    ).toEqual(["border-neutral-200"]);
    // …ou nommée dans un gabarit, avec une constante qui en nomme une autre.
    expect(
      lire(
        'const BASE = "bg-white";\nconst CHAMP = `${BASE} px-3`;\n<select className={`${CHAMP} w-full`} />',
      )[0].brutes,
    ).toEqual(["bg-white"]);
    // Une balise sur plusieurs lignes, une flèche dans un attribut : ce `>`-là
    // ne ferme pas la balise, et le `className` qui suit est bien lu.
    expect(
      lire('<input\n  onChange={(e) => setUrl(e.target.value)}\n  className="bg-white"\n/>')[0]
        .brutes,
    ).toEqual(["bg-white"]);

    // Ce qui est sain : les tokens du socle, un `sr-only`, un contrôle sans classe.
    expect(
      lire('<textarea className="w-full border border-bord bg-surface focus:border-bord-fort" />')[0],
    ).toEqual({ balise: "textarea", brutes: [], illisibles: [] });
    expect(lire('<input type="file" className="sr-only" />')[0].brutes).toEqual([]);
    expect(lire('<input type="checkbox" checked />')[0]).toEqual({
      balise: "input",
      brutes: [],
      illisibles: [],
    });

    // Ce qu'elle refuse de juger : une constante qu'elle ne trouve nulle part —
    // et, à l'inverse, celle du socle, lue dans `Primitives.tsx`.
    expect(lire("<input className={CLASSE_AILLEURS} />")[0].illisibles).toEqual([
      "CLASSE_AILLEURS",
    ]);
    const socle = new Map([["CLASSE_CONTROLE", '"border-bord bg-surface"']]);
    const importee =
      'import { Bouton, CLASSE_CONTROLE } from "@/components/Primitives";\n<input className={CLASSE_CONTROLE} />';
    expect(champsDe(importee, socle)[0]).toEqual({ balise: "input", brutes: [], illisibles: [] });
    expect(
      champsDe(importee, new Map([["CLASSE_CONTROLE", '"border-neutral-200"']]))[0].brutes,
    ).toEqual(["border-neutral-200"]);

    // Et la prose ne compte pas.
    expect(lire('// un <input className="bg-white"> dans un commentaire')).toEqual([]);
  });

  it("lit le socle lui-même dans les tokens", () => {
    // `CLASSE_CONTROLE` est ce que `Champ`, `ChampListe` et `ChampTexte`
    // composent, et ce que `ChampJetons` importe : une couleur brute qui s'y
    // glisserait passerait sur tous les écrans à la fois. Le balayage écarte
    // `Primitives.tsx` (ses contrôles portent `classesControle(monospace)`, un
    // appel qu'aucune constante ne résout) ; c'est ici qu'il est jugé.
    const { jetons, illisibles } = resoudre("CLASSE_CONTROLE", {
      locales: SOCLE,
      importees: new Set(),
      socle: SOCLE,
    });
    expect(illisibles).toEqual([]);
    expect(jetons).toContain("focus:border-bord-fort");
    expect(jetons).toContain("focus-visible:outline-accent");
    expect(jetons.filter((j) => COULEUR_BRUTE.test(utilite(j)))).toEqual([]);
  });

  /**
   * Ce qui reste hors des tokens, **nommé** fichier par fichier avec son compte
   * exact et sa raison — jamais une liste où ranger ce qui échoue (règle de
   * `HORS_PAIRES`, #534). Le compte est exact et non un plafond : un contrôle
   * de plus rougit, un contrôle de moins rougit aussi, jusqu'à ce que la ligne
   * soit mise à jour ou retirée — c'est ainsi qu'un résidu ne peut que
   * décroître, et que chaque décroissance est un geste écrit.
   */
  const RECOPIE =
    "recopie antérieure à #832, hors du périmètre du ticket (les trois composeurs) — " +
    "à replier sur `Champ`/`ChampListe`/`ChampTexte`, ce qui retire la ligne";
  const RESIDU = new Map<string, { controles: number; raison: string }>([
    [
      "app/journal/page.tsx",
      {
        controles: 1,
        raison:
          "une case à cocher, que le socle ne couvre pas : un `Champ type=\"checkbox\"` " +
          "rendrait une case pleine largeur, ce n'est pas le même contrôle",
      },
    ],
    [
      "components/AssistantFlottant.tsx",
      {
        controles: 1,
        raison:
          "le quatrième composeur, hors du ticket : sa `ref` de mise au point n'a pas " +
          "de passage dans `ChampTexte`, qui ne relaie pas de `ref`",
      },
    ],
    ["components/EditeurAgent.tsx", { controles: 8, raison: RECOPIE }],
    [
      "components/SelecteurReassignation.tsx",
      {
        controles: 1,
        raison:
          "une liste posée dans une carte du Kanban, en fond transparent et corps " +
          "réduit — un `ChampListe libelleMasque` en est le remplaçant naturel (#832)",
      },
    ],
    ["components/brief/QuestionsBrief.tsx", { controles: 1, raison: RECOPIE }],
    ["components/brief/SectionsBrief.tsx", { controles: 2, raison: RECOPIE }],
    ["components/integrations/BibliothequeMcp.tsx", { controles: 3, raison: RECOPIE }],
    [
      "components/projets/ExplorateurDossiers.tsx",
      {
        controles: 1,
        raison:
          "un libellé `sr-only` posé à la main avec `htmlFor` — exactement ce que " +
          "`Champ libelleMasque` rend depuis #832, à replier",
      },
    ],
  ]);

  /** Les fichiers que le balayage ne juge pas, et pourquoi. */
  const HORS_BALAYAGE = new Map<string, string>([
    [
      "components/Primitives.tsx",
      "le socle : ses contrôles portent `classesControle(monospace)`, jugé ci-dessus par `CLASSE_CONTROLE`",
    ],
  ]);

  function residuMesure(): Map<string, string[]> {
    const parFichier = new Map<string, string[]>();
    for (const fichier of sourcesDuProduit()) {
      if (HORS_BALAYAGE.has(fichier)) continue;
      for (const { balise, brutes, illisibles } of champsDe(lireSource(fichier), SOCLE)) {
        if (brutes.length === 0 && illisibles.length === 0) continue;
        const detail = [
          brutes.length > 0 ? `hors des tokens : ${brutes.join(", ")}` : "",
          illisibles.length > 0 ? `illisible : ${illisibles.join(", ")}` : "",
        ]
          .filter(Boolean)
          .join(" ; ");
        parFichier.set(fichier, [...(parFichier.get(fichier) ?? []), `<${balise}> — ${detail}`]);
      }
    }
    return parFichier;
  }

  it("ne laisse aucun contrôle de saisie hors des tokens, hors du résidu nommé", () => {
    const mesure = residuMesure();
    const nouveaux = [...mesure]
      .filter(([fichier, controles]) => (RESIDU.get(fichier)?.controles ?? 0) < controles.length)
      .map(([fichier, controles]) => `  ${fichier}\n${controles.map((c) => `    ${c}`).join("\n")}`);
    expect(
      nouveaux,
      `\n${nouveaux.join("\n")}\n\nUn contrôle de saisie est un Champ, ChampListe ou ChampTexte du socle — ` +
        "libelleMasque quand son libellé n'a pas à s'afficher (#832).\n",
    ).toHaveLength(0);
  });

  it("examine bien les contrôles du produit, et pas un balayage devenu muet", () => {
    // Le plancher rend le ✓ opposable : un motif de balise qui ne matcherait
    // plus rien rendrait « aucun contrôle hors des tokens » avec les mots de
    // « tout est dans le socle ». 26 est le compte **mesuré** à #832 hors
    // `Primitives.tsx` — 19 dans le résidu, le reste dans les tokens ou en
    // `sr-only` (les entrées de fichiers). Un lot qui replie une recopie sur
    // le socle fait baisser ce chiffre (un `<Champ>` n'est plus un `<input>`),
    // et le réécrit — comme #534 fait de ses paires.
    let examines = 0;
    for (const fichier of sourcesDuProduit()) {
      if (HORS_BALAYAGE.has(fichier)) continue;
      examines += champsDe(lireSource(fichier), SOCLE).length;
    }
    expect(examines).toBeGreaterThanOrEqual(26);
  });

  it("ne garde aucune exemption périmée", () => {
    // Une ligne du résidu qui compte plus que ce qu'on mesure est une recopie
    // repliée sans que la ligne l'ait dit — et une ligne qu'on relira comme si
    // elle valait encore. Même contrôle sur les fichiers écartés du balayage.
    const mesure = residuMesure();
    for (const [fichier, { controles }] of RESIDU) {
      expect(
        mesure.get(fichier)?.length ?? 0,
        `${fichier} : le résidu annonce ${controles} contrôle(s) hors des tokens`,
      ).toBe(controles);
    }
    for (const fichier of HORS_BALAYAGE.keys()) {
      expect(sourcesDuProduit(), `${fichier} est écarté mais n'existe plus`).toContain(fichier);
    }
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

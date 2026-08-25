/**
 * Le contraste de la palette sémantique (#534, lot 2 de #532).
 *
 * #533 a posé les tokens **et** mesuré leurs 72 paires — 36 par thème, 0 faute.
 * Ce fichier ne refait pas cette mesure : il la **tient**. La promesse était
 * vérifiée, elle n'était pas gardée, et la différence n'est pas rhétorique — le
 * dépôt a déjà une doc de langage visuel détaillée, et 18 recopies de carte et
 * 26 boutons refaits sont passés quand même (docs/30 §3.6). Ce qui tient un
 * niveau sur tous les écrans, ici, c'est un test et pas une maquette : Code
 * Connect est refusé sur ce compte, donc rien ne relie une cible Figma au code
 * (docs/30 §5). Sans ce fichier, le premier écran retouché défait les tokens.
 *
 * ── Où la sonde va lire, et pourquoi là ──────────────────────────────────────
 *
 * Dans les blocs `:root` / `[data-theme="sombre"]` de `app/globals.css`, **en
 * octets**. Deux pièges s'annulent à cet endroit précis :
 *
 *  1. Un bloc `@theme inline` **n'émet rien** — il ne fait que brancher les
 *     tokens sur les utilitaires (`bg-surface`, `text-alerte-texte`…). Lire là
 *     rendrait des `var(--surface)` en guise de couleurs.
 *  2. Tailwind v4 émet sa propre palette en `oklch()`, qu'aucun parseur `rgb()`
 *     naïf ne lit. C'est le piège rencontré **et corrigé** pendant #471, qui a
 *     d'abord rendu des ratios faux (docs/30 §3.1). #533 a donc écrit la source
 *     en hexadécimal pour mettre ce piège hors sujet — et la sonde **refuse**
 *     bruyamment ce qu'elle ne sait pas lire, au lieu de le sauter. Un
 *     garde-fou qui saute est plus dangereux qu'un garde-fou absent : il rend
 *     un ✓ sur une question jamais posée.
 *
 * ── Ce sur quoi ce fichier se tait ───────────────────────────────────────────
 *
 * Il juge la **palette**, pas les écrans. Les 1 750 couleurs brutes encore en
 * place — `text-neutral-400` à 2,58:1, 230 occurrences ; le bouton
 * `bg-emerald-600` à 3,65:1 — sont le sujet du lot 3, et elles ne passeraient
 * pas ici. Ce fichier garantit que la bonne valeur **existe et reste bonne** ;
 * que les écrans l'emploient est une autre question, et un autre filet (#537).
 */

import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

// ─────────────────────────────────────────────────────────────────────────────
// 1. LA SONDE
// ─────────────────────────────────────────────────────────────────────────────

/** WCAG 2.2, 1.4.3 — du texte, quelle que soit sa taille rendue. */
const SEUIL_TEXTE = 4.5;

/**
 * WCAG 2.2, 1.4.11 — ce qui n'est pas du texte mais porte de l'information :
 * ce qui borne un contrôle, et l'aplat qui dit un état sans un mot. C'est aussi
 * le seuil du texte *large*, que la palette ne distingue pas : un token ne sait
 * pas à quelle taille il sera rendu, donc on lui applique le seuil strict.
 */
const SEUIL_NON_TEXTE = 3;

/** Les trois octets sRGB d'une couleur, tels qu'ils sont écrits dans la feuille. */
function octets(valeur: string): [number, number, number] {
  const brut = valeur.trim();
  const court = /^#([0-9a-f])([0-9a-f])([0-9a-f])$/i.exec(brut);
  const long = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(brut);
  if (court) {
    return [
      Number.parseInt(court[1] + court[1], 16),
      Number.parseInt(court[2] + court[2], 16),
      Number.parseInt(court[3] + court[3], 16),
    ];
  }
  if (long) {
    return [
      Number.parseInt(long[1], 16),
      Number.parseInt(long[2], 16),
      Number.parseInt(long[3], 16),
    ];
  }
  // Le refus est la moitié utile de la fonction. Une couleur en `oklch()`, en
  // `color-mix()` ou en `var(...)` n'est pas une couleur illisible qu'on peut
  // sauter : c'est la palette qui a cessé d'être mesurable, et le test doit le
  // dire au lieu de rendre un vert amputé (docs/30 §3.1, piège de #471).
  throw new Error(
    `« ${brut} » n'est pas un hexadécimal sRGB. La palette se lit en octets : ` +
      "une valeur en oklch()/color-mix()/var() la rend non mesurable, et un " +
      "ratio faux est pire qu'un ratio absent. Écrire la valeur en #rrggbb.",
  );
}

/** La composante linéaire d'un octet sRGB (WCAG 2.x, définition de la luminance relative). */
function canalLineaire(octet: number): number {
  const c = octet / 255;
  return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}

function luminance(valeur: string): number {
  const [r, v, b] = octets(valeur);
  return (
    0.2126 * canalLineaire(r) + 0.7152 * canalLineaire(v) + 0.0722 * canalLineaire(b)
  );
}

/** Le rapport de contraste WCAG entre deux couleurs, dans l'ordre qu'on veut. */
function contraste(a: string, b: string): number {
  const [clair, sombre] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (clair + 0.05) / (sombre + 0.05);
}

/** « 3.4014 » → « 3,40 » — la forme sous laquelle #533 et docs/30 citent leurs mesures. */
const ratioLisible = (ratio: number) => ratio.toFixed(2).replace(".", ",");

// ── La lecture de la feuille ────────────────────────────────────────────────

/**
 * ⚠ Le chemin se construit en deux temps, et surtout **pas** avec la tournure
 * `new URL("../app/globals.css", import.meta.url)`, qui est pourtant celle
 * qu'on écrit d'ordinaire en ESM. Vite la reconnaît **statiquement** et la
 * réécrit en référence d'**asset** : à l'exécution la base n'est plus le
 * fichier mais `self.location`, donc une URL `http:`, et `fileURLToPath` sort
 * sur « The URL must be of scheme file » — un message qui accuse le chemin
 * alors que le chemin était juste. Passer `import.meta.url` en **chaîne** à
 * `fileURLToPath` échappe à la réécriture.
 */
const ICI = path.dirname(fileURLToPath(import.meta.url));
const FEUILLE = readFileSync(path.join(ICI, "..", "app", "globals.css"), "utf8");

/**
 * Les commentaires partent d'abord : ceux de `globals.css` citent des teintes
 * (« neutral-200 ») et des ratios (« 3,54:1 »), et les laisser reviendrait à
 * mesurer la prose autant que la palette.
 */
const SANS_COMMENTAIRES = FEUILLE.replace(/\/\*[\s\S]*?\*\//g, "");

/** Les propriétés personnalisées déclarées par un bloc, à son sélecteur près. */
function declarations(selecteur: RegExp): ReadonlyMap<string, string> {
  const bloc = new RegExp(`${selecteur.source}\\s*\\{([^}]*)\\}`).exec(SANS_COMMENTAIRES);
  if (!bloc) {
    throw new Error(
      `Aucun bloc « ${selecteur.source} » dans app/globals.css. La palette a été ` +
        "déplacée ou renommée : c'est la sonde qu'il faut suivre, pas contourner.",
    );
  }
  const table = new Map<string, string>();
  for (const [, nom, valeur] of bloc[1].matchAll(/--([\w-]+)\s*:\s*([^;]+);/g)) {
    table.set(nom, valeur.trim());
  }
  return table;
}

const THEMES = [
  { nom: "clair", palette: declarations(/:root,\s*\[data-theme="clair"\]/) },
  { nom: "sombre", palette: declarations(/\[data-theme="sombre"\]/) },
];

// ─────────────────────────────────────────────────────────────────────────────
// 2. LES PAIRES LÉGITIMES
// ─────────────────────────────────────────────────────────────────────────────

/**
 * « Légitime » veut dire : une paire que le produit **peut** rendre. Mesurer
 * toutes les combinaisons de tokens deux à deux donnerait 400 paires dont
 * l'écrasante majorité n'arrive jamais à l'écran (`alerte-creux` sur
 * `info-creux`…), et ces fausses fautes finiraient par faire ignorer les vraies.
 * Le contrat des suffixes est celui qu'énonce `apps/web/README.md` :
 *
 *   *(aucun)*  l'aplat        ≥ 3:1 sur les deux surfaces
 *   `-texte`   le ton écrit   ≥ 4,5:1 sur les deux surfaces **et** sur son creux
 *   `-creux`   le fond teinté porte son `-texte` à ≥ 4,5:1
 */
type Paire = {
  /** Ce qui est devant : du texte, un contour, un aplat. */
  readonly avant: string;
  /** Ce sur quoi c'est posé. */
  readonly arriere: string;
  readonly seuil: number;
  /** Ce que cette paire garantit, en clair — c'est ce qu'on lit quand elle rougit. */
  readonly motif: string;
};

const TONS = ["accent", "info", "positif", "attention", "alerte"];
const SURFACES = ["surface", "surface-creuse"];

/**
 * Les tons qu'un **bouton** peut porter, donc les seuls à avoir un `-appui`
 * (l'aplat survolé, #535). `positif` n'en est pas, et son absence est une
 * décision et non un oubli : un aplat `positif` est un **état**, jamais une
 * action, donc il n'est jamais survolé. Itérer sur `TONS` ici réclamerait un
 * `--positif-appui` que `globals.css` refuse d'écrire — le trou dit quelque
 * chose, et le combler par symétrie dirait le contraire.
 */
const TONS_ACTION = ["accent", "info", "attention", "alerte"];

const PAIRES: readonly Paire[] = [
  // Le texte courant, sur les deux surfaces.
  ...SURFACES.flatMap((fond): Paire[] => [
    { avant: "texte", arriere: fond, seuil: SEUIL_TEXTE, motif: "le texte principal" },
    {
      avant: "texte-secondaire",
      arriere: fond,
      seuil: SEUIL_TEXTE,
      motif: "le second plan — le remplaçant de text-neutral-400",
    },
  ]),

  ...TONS.flatMap((ton): Paire[] => [
    // Le ton ÉCRIT : sur les deux surfaces, puis sur son propre creux. Cette
    // dernière est la plus courte marge de la palette côté texte (5,02 pour
    // `alerte-texte` sur `alerte-creux`), donc celle qui cassera la première.
    ...SURFACES.map(
      (fond): Paire => ({
        avant: `${ton}-texte`,
        arriere: fond,
        seuil: SEUIL_TEXTE,
        motif: `le ton « ${ton} » écrit`,
      }),
    ),
    {
      avant: `${ton}-texte`,
      arriere: `${ton}-creux`,
      seuil: SEUIL_TEXTE,
      motif: `la pastille « ${ton} »`,
    },
    // Ce qui s'écrit SUR l'aplat. Un seul `sur-ton` pour les cinq tons : c'est
    // une propriété vérifiée de la palette, et c'est ici qu'elle se vérifie.
    {
      avant: "sur-ton",
      arriere: ton,
      seuil: SEUIL_TEXTE,
      motif: `le bouton plein « ${ton} »`,
    },
    // L'aplat lui-même, quand il porte l'état sans un mot (point, jauge, bord
    // d'état) : objet graphique, donc 1.4.11 et non 1.4.3.
    ...SURFACES.map(
      (fond): Paire => ({
        avant: ton,
        arriere: fond,
        seuil: SEUIL_NON_TEXTE,
        motif: `l'aplat « ${ton} » comme indicateur`,
      }),
    ),
  ]),

  // Ce qui borne un contrôle — champ, case, contour de bouton.
  ...SURFACES.map(
    (fond): Paire => ({
      avant: "bord-fort",
      arriere: fond,
      seuil: SEUIL_NON_TEXTE,
      motif: "le contour d'un contrôle",
    }),
  ),

  // ── L'état survolé (#535) ────────────────────────────────────────────────
  // Ce que le survol change, c'est le FOND ; ce qui doit continuer de tenir,
  // c'est ce qui est écrit dessus. Sans ces paires, un `-appui` mal choisi
  // rendrait le libellé d'un bouton illisible pendant qu'on le vise — l'instant
  // exact où l'on en a besoin — et rien ici ne le verrait.
  ...TONS_ACTION.map(
    (ton): Paire => ({
      avant: "sur-ton",
      arriere: `${ton}-appui`,
      seuil: SEUIL_TEXTE,
      motif: `le bouton plein « ${ton} » survolé`,
    }),
  ),

  // Le fond d'un contrôle SANS aplat (bouton de contour, bouton discret, entrée
  // de menu). Les trois choses qui s'y posent, et rien d'autre : le libellé, le
  // libellé de second plan, et le trait qui borne le contrôle.
  {
    avant: "texte",
    arriere: "survol",
    seuil: SEUIL_TEXTE,
    motif: "le libellé d'un contrôle survolé",
  },
  {
    avant: "texte-secondaire",
    arriere: "survol",
    seuil: SEUIL_TEXTE,
    motif: "le second plan d'un contrôle survolé",
  },
  {
    avant: "bord-fort",
    arriere: "survol",
    seuil: SEUIL_NON_TEXTE,
    motif: "le contour d'un bouton de contour survolé",
  },
];

/**
 * Les tokens qu'aucune paire ne couvre, **et la raison de chacun**. Une liste
 * d'exclusions sans motifs redeviendrait l'endroit où l'on range ce qui échoue.
 */
const HORS_PAIRES = new Map([
  [
    "bord",
    "filet décoratif (contour de carte, séparateur) — hors du champ de WCAG " +
      "1.4.11, qui ne vise que ce qui identifie un contrôle : c'est `bord-fort`",
  ],
  ["background", "hérité d'avant #533 — la règle `body`, pas la palette"],
  ["foreground", "hérité d'avant #533 — la règle `body`, pas la palette"],
]);

/** Le verdict rendu sur une paire. C'est LUI que la palette réelle subit, et lui qu'on prouve d'abord. */
function mesure(paire: Paire, palette: ReadonlyMap<string, string>) {
  const valeur = (token: string) => {
    const hex = palette.get(token);
    if (hex === undefined) {
      throw new Error(
        `Le token « --${token} » n'existe pas dans ce thème, alors qu'une paire ` +
          "le nomme. Un token renommé se suit ici aussi.",
      );
    }
    return hex;
  };
  const avant = valeur(paire.avant);
  const arriere = valeur(paire.arriere);
  const ratio = contraste(avant, arriere);
  return {
    ratio,
    tient: ratio >= paire.seuil,
    recit:
      `${paire.motif} : --${paire.avant} (${avant}) sur --${paire.arriere} ` +
      `(${arriere}) rend ${ratioLisible(ratio)}:1, il en faut ${ratioLisible(paire.seuil)}`,
  };
}

/** La même palette, un token remplacé — de quoi glisser une faute sans toucher la feuille. */
function avec(
  palette: ReadonlyMap<string, string>,
  token: string,
  valeur: string,
): ReadonlyMap<string, string> {
  return new Map(palette).set(token, valeur);
}

const CLAIR = THEMES[0].palette;

// ─────────────────────────────────────────────────────────────────────────────
// 3. LA SONDE, PROUVÉE AVANT DE SERVIR
//
// Un test de contraste qui ne rougit jamais est indiscernable d'un test qui
// mesure mal — les deux rendent un ✓. Ce bloc pose donc à la sonde les
// questions dont on connaît déjà la réponse, **avant** qu'elle ne juge la
// palette réelle : les ratios mesurés sur le produit, ceux annoncés par #533,
// puis une faute glissée exprès dans une palette par ailleurs saine.
// ─────────────────────────────────────────────────────────────────────────────

describe("la sonde, prouvée avant de servir", () => {
  it("retrouve les deux fautes mesurées sur le produit réel (docs/30 §3.2)", () => {
    // Mesurées dans le navigateur pendant #471, par lecture de pixel sur un
    // canvas — donc par un tout autre chemin que celui-ci. Les retrouver au
    // centième prouve la formule, pas seulement la comparaison : c'est ce qui
    // manquait à la première version de la sonde de #471, qui comparait très
    // bien des ratios faux.
    //
    // `text-neutral-400` sur blanc — 230 occurrences, la classe n°1 du produit.
    expect(contraste("#a1a1a1", "#ffffff")).toBeCloseTo(2.58, 2);
    // Blanc sur `bg-emerald-600` — le bouton d'action primaire, 18 fichiers.
    expect(contraste("#ffffff", "#009966")).toBeCloseTo(3.65, 2);
  });

  it("retrouve les deux marges les plus courtes annoncées par #533", () => {
    // Annoncées dans `globals.css` et dans le README. Les retrouver ici, c'est
    // vérifier que la sonde et l'auteur de la palette comptent pareil — sans
    // quoi ce fichier garderait une promesse que personne n'a faite.
    expect(contraste("#888888", "#fafafa")).toBeCloseTo(3.4, 2); // bord-fort / surface-creuse
    expect(contraste("#c70036", "#ffe4e6")).toBeCloseTo(5.02, 2); // alerte-texte / alerte-creux
  });

  it("rend le même ratio quel que soit l'ordre des deux couleurs", () => {
    // Sinon une paire tiendrait ou non selon qu'on l'a écrite texte-sur-fond ou
    // fond-sur-texte, et la moitié de la table serait fausse en silence.
    expect(contraste("#171717", "#ffffff")).toBeCloseTo(contraste("#ffffff", "#171717"), 10);
  });

  it("refuse une valeur qu'elle ne sait pas lire, au lieu de la sauter", () => {
    // Le piège de #471. Si Tailwind (ou une retouche) réintroduit un `oklch()`
    // dans la palette, ce test doit devenir rouge — pas se taire.
    expect(() => contraste("oklch(0.708 0 0)", "#ffffff")).toThrow(/hexadécimal/);
    expect(() => contraste("var(--surface)", "#ffffff")).toThrow(/hexadécimal/);
  });

  it("rougit sur une paire fautive glissée dans la palette réelle", () => {
    // La faute n°1 du produit, remise à la place du token qui la remplace. La
    // palette est saine partout ailleurs : ce qui rougit est bien la paire, pas
    // un effet de bord.
    const fautive = avec(CLAIR, "texte-secondaire", "#a1a1a1");
    const surSurface = PAIRES.find(
      (p) => p.avant === "texte-secondaire" && p.arriere === "surface",
    );
    expect(surSurface, "la paire témoin a disparu de la table").toBeDefined();

    // Le témoin doit être sain AVANT d'être sali, sans quoi la démonstration ne
    // vaut rien : c'est la palette réelle, lue dans la feuille, donc ce contrôle
    // est aussi ce qui rattache tout ce bloc au fichier plutôt qu'à lui-même.
    expect(
      mesure(surSurface!, CLAIR).tient,
      "la paire témoin est déjà fautive dans la palette réelle : la preuve du " +
        "motif ne peut pas s'appuyer dessus (voir les rouges du bloc suivant)",
    ).toBe(true);
    expect(mesure(surSurface!, fautive).tient).toBe(false);
    expect(mesure(surSurface!, fautive).recit).toContain("2,58:1");
  });

  it("place le seuil du texte à 4,5 et pas à 3", () => {
    // Une valeur qui passerait le seuil des objets graphiques mais pas celui du
    // texte : sans cette borne, la sonde laisserait entrer tout le second plan
    // gris que ce chantier existe pour retirer.
    const tiede = avec(CLAIR, "texte-secondaire", "#8a8a8a"); // ~3,45:1 sur blanc
    const paire = PAIRES.find(
      (p) => p.avant === "texte-secondaire" && p.arriere === "surface",
    )!;
    expect(mesure(paire, tiede).ratio).toBeGreaterThan(SEUIL_NON_TEXTE);
    expect(mesure(paire, tiede).tient).toBe(false);
  });

  it("place le seuil des objets graphiques à 3 et pas à 4,5", () => {
    // La preuve par l'autre bord, et elle est déjà dans la palette : `bord-fort`
    // vit ENTRE les deux seuils (3,54 sur surface, 3,40 sur surface-creuse, et
    // 3,25 sur le `survol` de #535). Si la sonde lui appliquait le seuil du
    // texte, la palette réelle rougirait — ces paires-là sont donc le témoin
    // vivant que les deux seuils sont distincts, et il n'y a pas à en fabriquer
    // un. Le compte les épingle : une paire de contour qui disparaîtrait de la
    // table emporterait le témoin avec elle.
    const contour = PAIRES.filter((p) => p.avant === "bord-fort");
    expect(contour).toHaveLength(3);
    for (const paire of contour) {
      const { ratio, tient } = mesure(paire, CLAIR);
      expect(tient, paire.motif).toBe(true);
      expect(ratio, `${paire.arriere} : ${ratioLisible(ratio)}`).toBeLessThan(SEUIL_TEXTE);
    }
    // …et elle rougit quand même sous 3.
    const efface = avec(CLAIR, "bord-fort", "#c0c0c0"); // ~1,82:1 sur blanc
    expect(mesure(contour[0], efface).tient).toBe(false);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// 4. LA PALETTE RÉELLE
// ─────────────────────────────────────────────────────────────────────────────

describe.each(THEMES)("la palette réelle — thème $nom", ({ palette }) => {
  it.each(PAIRES)("$avant sur $arriere tient le contraste", (paire) => {
    const { tient, recit } = mesure(paire, palette);
    expect(tient, recit).toBe(true);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// 5. QUE RIEN NE PASSE À CÔTÉ DU FILET
//
// Les contrôles ci-dessus disent que les paires mesurées tiennent. Ceux-ci
// disent qu'il n'existe pas de paire qu'on aurait oublié de mesurer — c'est la
// moitié qu'un test de contraste perd le plus facilement, un token ajouté sans
// paire étant vert par construction.
// ─────────────────────────────────────────────────────────────────────────────

describe("la couverture du filet", () => {
  it("lit bien une palette, et pas un bloc vide", () => {
    // Si le sélecteur ou la forme des déclarations change, la sonde trouverait
    // zéro token et tous les tests ci-dessus deviendraient vacueusement verts.
    for (const { nom, palette } of THEMES) {
      expect(palette.size, `thème ${nom}`).toBeGreaterThanOrEqual(20);
      for (const [token, valeur] of palette) {
        expect(() => octets(valeur), `--${token} (${nom})`).not.toThrow();
      }
    }
  });

  it("mesure les 43 paires par thème qu'annoncent #533 et #535", () => {
    // Le chiffre est celui du README et de `globals.css` (« 86 paires mesurées,
    // 43 par thème » : les 36 de #533, plus les 7 dont #535 a besoin pour son
    // état survolé). Il garde le CONSTRUCTEUR de la table : une boucle qui
    // n'itère plus rendrait une table courte, donc un vert plus rapide et faux.
    // S'il bouge un jour, il bouge aux trois endroits ensemble.
    expect(PAIRES).toHaveLength(43);
  });

  it("couvre chaque token de la palette, ou nomme pourquoi il en est exempt", () => {
    // LE contrôle qui fait de ce fichier un filet plutôt qu'un instantané :
    // ajouter un token à `globals.css` sans lui déclarer de paire rougit ici.
    const couverts = new Set(PAIRES.flatMap((p) => [p.avant, p.arriere]));
    for (const { nom, palette } of THEMES) {
      const orphelins = [...palette.keys()].filter(
        (token) => !couverts.has(token) && !HORS_PAIRES.has(token),
      );
      expect(
        orphelins,
        `thème ${nom} : ${orphelins.map((t) => `--${t}`).join(", ")} — aucune paire ne ` +
          "les mesure. Leur déclarer une paire légitime, ou les inscrire dans " +
          "HORS_PAIRES avec le motif de leur exemption.",
      ).toEqual([]);
    }
  });

  it("ne garde aucune exemption périmée", () => {
    // Une exemption qui ne désigne plus rien est un motif qu'on relira comme
    // s'il valait encore.
    for (const token of HORS_PAIRES.keys()) {
      expect(CLAIR.has(token), `--${token} est exempté mais n'existe plus`).toBe(true);
      expect(
        [...new Set(PAIRES.flatMap((p) => [p.avant, p.arriere]))],
        `--${token} est exempté ET mesuré`,
      ).not.toContain(token);
    }
  });

  it("déclare les deux thèmes sur exactement le même jeu de tokens", () => {
    // Un token oublié en sombre ne casse rien à la compilation : l'utilitaire
    // garde alors sa valeur claire sous le thème sombre, ce qu'aucune des
    // mesures ci-dessus ne verrait — elles ne comparent que ce qu'elles
    // trouvent.
    const [clair, sombre] = THEMES.map(({ palette }) => [...palette.keys()].sort());
    expect(sombre).toEqual(clair);
  });
});

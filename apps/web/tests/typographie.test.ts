/**
 * La taille de texte écrite hors de l'échelle (#981).
 *
 * #533 a posé l'échelle typographique — cinq pas de texte nommés par leur
 * **rôle** (`micro`, `annexe`, `corps`, `titre`, `page`) plus le pas d'affichage
 * `chiffre` — et a tranché les doublons : `text-xs` et `text-sm` ne sont plus
 * que des **alias** de `text-annexe` et `text-corps` (`--text-xs:
 * var(--text-annexe)`). Mais il n'a migré aucun appelant, et rien n'empêchait la
 * taille suivante d'être écrite hors de l'échelle. Ce fichier est à la
 * typographie ce que `couleurs.test.ts` (#895) est à la couleur : l'**usage**.
 *
 * ── Ce que la sonde cherche, et pourquoi cette forme-là ──────────────────────
 *
 * Un utilitaire de **taille de texte** dont la valeur ne vient pas d'un pas
 * nommé. Deux familles, qui ne disent pas la même chose mais appellent le même
 * geste :
 *
 * - **Un pas de Tailwind** (`text-xs`, `text-sm`, `text-base`, `text-lg`…).
 *   Pour `xs` et `sm`, c'est la **jumelle** : le même corps écrit sous un autre
 *   nom, donc un défaut qui ne se voit pas à l'écran — il ne se voit qu'ici.
 *   Pour les autres, c'est une taille que l'échelle n'a pas du tout.
 * - **Une valeur arbitraire** (`text-[13px]`, `text-[length:var(--x)]`,
 *   `text-(length:--x)`). C'est la sortie de secours d'une ligne, et un filet
 *   qu'on contourne en une ligne n'en est pas un. Le dépôt en porte déjà neuf,
 *   et `globals.css` nomme lui-même ce symptôme : *« le symptôme qu'il manque un
 *   pas, c'est un `text-[0.6875rem]` improvisé dans un composant — #245 en a
 *   retiré cinq »*.
 *
 * ⚠ **`text-` est surchargé** — c'est toute la difficulté de cette sonde-ci, et
 * ce qui la distingue de celle des couleurs. Le même préfixe porte la taille
 * (`text-sm`), la couleur (`text-neutral-500`, `text-[#3987e5]`), l'alignement
 * (`text-left`), le retour à la ligne (`text-balance`), le débordement
 * (`text-ellipsis`) et, depuis Tailwind v4, l'ombre de texte
 * (`text-shadow-sm`). Une sonde qui balaierait « tout ce qui commence par
 * `text-` » crierait sur six familles pour en juger une. Elle est donc ancrée
 * sur deux ensembles **fermés et lus**, jamais devinés : les noms de pas que
 * Tailwind livre, et les pas que `globals.css` déclare.
 *
 * ── Ce sur quoi ce fichier se tait ───────────────────────────────────────────
 *
 * L'**interligne** (`leading-*`), la graisse (`font-*`) et la casse
 * (`uppercase`, `tracking-*`). Ce n'est pas un oubli mais la portée du ticket :
 * l'échelle de #533 ne porte que le corps, et l'interligne des jumelles n'est
 * délibérément **pas** aliasée (`text-xs` garde `calc(1 / 0.75)`) — refuser
 * `leading-4` ici reviendrait à réclamer une migration que ce lot s'interdit.
 *
 * Et la **migration** elle-même : les 165 appels comptés plus bas ne partent pas
 * dans ce lot. Employer `text-titre`/`text-page` là où ils manquent est une
 * décision d'écran (chantier #972), pas un compte à mettre à jour. Ce lot pose
 * le compte et refuse le suivant.
 *
 * ── Pourquoi la confrontation au tableau n'est pas partagée avec #895 ────────
 *
 * `couleurs.test.ts` porte une fonction `confronter` de même forme, et #982
 * (rayons, ombres) puis #983 (espacements) en voudront une aussi. Elle n'est
 * pourtant pas extraite ici, et c'est un choix : `sources.ts` partage le
 * **périmètre**, jamais un verdict (sa propre règle), les deux lots voisins sont
 * marqués parallèles — extraire maintenant leur imposerait un contrat qu'ils
 * n'ont pas vu —, et deux occurrences ne font pas encore une dispersion. La
 * **troisième** sonde de cette famille est le moment de l'extraire, avec les
 * trois messages sous les yeux.
 */

import { describe, expect, it } from "vitest";

import { lireSource, sansCommentaires, sourcesDuProduit } from "./sources";

// ─────────────────────────────────────────────────────────────────────────────
// 1. LES DEUX ÉCHELLES, LUES
//
// Celle de Tailwind dit ce qu'on refuse ; celle du dépôt dit ce qu'on
// recommande à la place. Les deux sont **lues** et non recopiées : une taille
// que Tailwind ajouterait (`text-10xl`) serait refusée le jour de la mise à
// jour, et un pas retiré de `globals.css` cesserait d'être recommandé le jour
// où il disparaît. C'est la règle de `tokensDeLaPalette()` dans #895.
// ─────────────────────────────────────────────────────────────────────────────

/** Les sources balayées. Une feuille de classes peut vivre dans un `.ts` de constantes. */
const EXTENSIONS = [".tsx", ".ts"] as const;

/**
 * Les noms de pas que Tailwind livre, lus dans son propre `theme.css`.
 *
 * Le motif n'accepte qu'un nom **sans tiret** : c'est ce qui laisse dehors
 * `--text-shadow-2xs` (une ombre, pas un corps) et `--text-xs--line-height`
 * (l'interligne du pas, pas le pas).
 */
function taillesDeTailwind(): Set<string> {
  const chemin = "node_modules/tailwindcss/theme.css";
  const feuille = lireSource(chemin);
  const noms = new Set(
    [...feuille.matchAll(/^\s*--text-([a-z0-9]+):/gm)].map(([, nom]) => nom),
  );
  if (noms.size < 10) {
    throw new Error(
      `Moins de dix pas de texte lus dans ${chemin} : c'est ce fichier qui dit ` +
        "quels noms sont ceux de Tailwind, et sans lui cette sonde ne saurait " +
        "plus lesquels refuser. Le chemin a-t-il bougé avec une version majeure ?",
    );
  }
  return noms;
}

/**
 * L'échelle du dépôt, lue dans le bloc `@theme` de `globals.css` — celui qui
 * n'est **pas** `inline` (le bloc `inline` est la palette, et n'émet rien).
 *
 * Elle se lit en deux moitiés, et la distinction est celle que #533 a tranchée :
 * un **pas** porte une valeur propre (`--text-annexe: 0.75rem`), un **alias**
 * pointe sur un pas (`--text-xs: var(--text-annexe)`). C'est ce qui permet de
 * refuser `text-xs` sans refuser le socle qui le déclare — et de dire, dans le
 * message d'échec, par quel pas le remplacer.
 */
function echelleDuDepot(): { pas: Map<string, string>; alias: Map<string, string> } {
  const feuille = lireSource("app/globals.css");
  const bloc = /@theme\s*\{([\s\S]*?)\n\}/.exec(feuille);
  if (!bloc) {
    throw new Error(
      "Aucun bloc « @theme » (non `inline`) dans app/globals.css : c'est lui qui " +
        "déclare les pas de l'échelle, et sans lui cette sonde ne saurait plus " +
        "quoi recommander à la place d'un pas de Tailwind. Suivre l'échelle, pas " +
        "la contourner.",
    );
  }
  const pas = new Map<string, string>();
  const alias = new Map<string, string>();
  for (const [, nom, valeur] of bloc[1].matchAll(/--text-([a-z0-9]+):\s*([^;]+);/g)) {
    const vise = /^var\(--text-([a-z0-9]+)\)$/.exec(valeur.trim());
    if (vise) alias.set(nom, vise[1]);
    else pas.set(nom, valeur.trim());
  }
  return { pas, alias };
}

const TAILLES_TAILWIND = taillesDeTailwind();
const ECHELLE = echelleDuDepot();

// ─────────────────────────────────────────────────────────────────────────────
// 2. LA SONDE
// ─────────────────────────────────────────────────────────────────────────────

/** `text-sm`, `text-2xl`, et leur interligne suffixée : `text-sm/6`, `text-lg/[1.4]`. */
const PAS_DE_TAILWIND = new RegExp(
  `^text-(?:${[...TAILLES_TAILWIND].join("|")})` +
    `(?:\\/(?:\\d+|none|\\[[^\\]]*\\]|\\([^)]*\\)))?$`,
);

/**
 * Les unités de longueur d'une taille de texte. Le pourcentage en fait partie
 * (`text-[110%]` est une taille relative parfaitement valide), et les unités de
 * fenêtre aussi — c'est par là qu'un titre « fluide » s'écrirait.
 */
const UNITES =
  "px|rem|em|rlh|lh|ch|ex|cap|ic|pt|pc|in|cm|mm|q|vmin|vmax|[sld]?v[wh]|%";

/**
 * `text-[13px]`, `text-[0.625rem]`, `text-[length:var(--x)]`,
 * `text-(length:--x)`, `text-[clamp(…)]`.
 *
 * Le crochet à lui seul ne suffit pas : `text-[#3987e5]` et
 * `text-[color:var(--x)]` en portent aussi, et ce sont des **couleurs** — que
 * `couleurs.test.ts` juge, pas ce fichier. Ce qui décide, c'est que la valeur
 * soit une **longueur** : d'où l'ancrage sur ses trois écritures possibles —
 * l'indice explicite (`length:`), une expression de calcul, ou un nombre suivi
 * d'une unité. La forme courte de Tailwind v4 n'est retenue qu'**avec** son
 * indice : `text-(--x)` nu désigne une couleur, `text-(length:--x)` une taille.
 */
const TAILLE_ARBITRAIRE = new RegExp(
  "^text-(?:" +
    "\\[length:" +
    "|\\(length:" +
    "|\\[(?:calc|clamp|min|max)\\(" +
    `|\\[\\.?\\d[\\d.]*(?:${UNITES})\\]` +
    ")",
  "i",
);

/**
 * `md:text-lg` → `text-lg` : les variantes tombent, c'est l'utilité qui est
 * jugée.
 *
 * ⚠ Le découpage ne peut pas se faire sur le **dernier** `:` comme dans #895 :
 * `text-[length:var(--x)]` en porte un **à l'intérieur** de sa valeur, et une
 * coupe naïve n'en garderait que `var(--x)]` — c'est-à-dire précisément le
 * contournement que la sonde doit voir. On ne coupe donc qu'aux `:` de
 * profondeur zéro.
 */
function utilite(jeton: string): string {
  let profondeur = 0;
  let coupe = -1;
  for (let i = 0; i < jeton.length; i += 1) {
    const c = jeton[i];
    if (c === "[" || c === "(") profondeur += 1;
    else if (c === "]" || c === ")") profondeur -= 1;
    else if (c === ":" && profondeur === 0) coupe = i;
  }
  return jeton.slice(coupe + 1);
}

/** Ce jeton fixe-t-il une taille de texte hors de l'échelle ? */
const estHorsEchelle = (jeton: string): boolean => {
  const u = utilite(jeton);
  return PAS_DE_TAILWIND.test(u) || TAILLE_ARBITRAIRE.test(u);
};

/**
 * Les tailles hors échelle d'une source, dans l'ordre où elles y sont écrites.
 *
 * Le balayage porte sur les **chaînes littérales**, comme celui des couleurs et
 * pour la même raison : c'est là que vivent les feuilles de classes, et lire le
 * texte entier reviendrait à compter les noms de variables et les chemins
 * d'import. Un gabarit avale les chaînes qu'il interpole, d'où le nettoyage des
 * guillemets restés collés au jeton d'un ternaire.
 */
function taillesDe(source: string): string[] {
  const tailles: string[] = [];
  for (const [, guillemets, apostrophes, gabarit] of sansCommentaires(
    source,
  ).matchAll(/"([^"\n]*)"|'([^'\n]*)'|`([^`]*)`/g)) {
    for (const brut of (guillemets ?? apostrophes ?? gabarit ?? "").split(
      /[\s${}]+/,
    )) {
      const jeton = brut.replace(/^["'`]+|["'`]+$/g, "");
      if (jeton && estHorsEchelle(jeton)) tailles.push(jeton);
    }
  }
  return tailles;
}

// ─────────────────────────────────────────────────────────────────────────────
// 3. LA SONDE, PROUVÉE AVANT DE SERVIR
//
// Un balayage qui ne rougit jamais est indiscernable d'un balayage devenu muet
// — les deux rendent un ✓. Ce bloc lui pose donc les questions dont on connaît
// déjà la réponse **avant** qu'il ne juge le produit : les formes exactes que le
// ticket a relevées, les contournements plausibles, et ce sur quoi il ne doit
// surtout pas crier — ici, cinq autres familles qui partagent son préfixe.
// C'est la règle de `contraste.test.ts` (#534) et de `couleurs.test.ts` (#895).
// ─────────────────────────────────────────────────────────────────────────────

describe("la sonde, prouvée avant de servir", () => {
  it("reconnaît les deux jumelles que le ticket a comptées", () => {
    // Le constat de départ de #981, dans la forme où le produit l'écrit. Si le
    // motif cesse de les voir, ce sont ces lignes-là qui rougissent.
    expect(
      taillesDe(
        '<p className="text-sm text-neutral-500">Chargement…</p>;' +
          '<span className="shrink-0 text-xs tabular-nums">12</span>',
      ),
    ).toEqual(["text-sm", "text-xs"]);
    // Les pas que l'échelle n'a pas du tout, eux, se voient à l'œil nu — et se
    // refusent de la même façon.
    expect(taillesDe('"text-base text-lg text-xl text-2xl text-9xl"')).toEqual([
      "text-base",
      "text-lg",
      "text-xl",
      "text-2xl",
      "text-9xl",
    ]);
  });

  it("voit la taille sous ses variantes, dans les deux ordres", () => {
    // Le produit n'en porte aucune aujourd'hui — raison de plus pour l'éprouver
    // ici : un motif ancré sur le début du jeton s'éteindrait au premier
    // `md:text-lg`, et personne ne le verrait.
    expect(taillesDe('"md:text-lg dark:sm:text-xs group-hover:text-sm"')).toEqual([
      "md:text-lg",
      "dark:sm:text-xs",
      "group-hover:text-sm",
    ]);
    // L'interligne suffixée, sous ses trois écritures.
    expect(taillesDe('"text-sm/6 text-lg/[1.4] text-xs/none"')).toEqual([
      "text-sm/6",
      "text-lg/[1.4]",
      "text-xs/none",
    ]);
  });

  it("refuse le contournement par valeur arbitraire", () => {
    // La sortie de secours d'une ligne. Le produit en porte neuf : quatre à
    // 10 px (`text-[10px]` trois fois, `text-[0.625rem]` une — la même valeur
    // sous deux écritures) et cinq à 11 px, c'est-à-dire `text-micro` réécrit à
    // la main.
    expect(
      taillesDe('"text-[11px] text-[10px] text-[0.625rem] text-[110%]"'),
    ).toEqual(["text-[11px]", "text-[10px]", "text-[0.625rem]", "text-[110%]"]);
    // Les écritures indirectes, que Tailwind v4 documente comme équivalentes :
    // `text-(length:--x)` s'étend en `text-[length:var(--x)]`. Ne voir que la
    // longue rendrait la courte silencieuse.
    expect(
      taillesDe('"text-[length:var(--pas)] text-(length:--pas) text-[clamp(1rem,2vw,2rem)]"'),
    ).toEqual([
      "text-[length:var(--pas)]",
      "text-(length:--pas)",
      "text-[clamp(1rem,2vw,2rem)]",
    ]);
    // …et la variante posée devant, qui contient elle aussi un « : ».
    expect(taillesDe('"md:text-[length:var(--pas)]"')).toEqual([
      "md:text-[length:var(--pas)]",
    ]);
  });

  it("ne confond pas une taille avec les cinq autres familles du préfixe « text- »", () => {
    // LE contrôle qui distingue cette sonde d'un balayage de préfixe. Une sonde
    // qui crierait sur tout ne dirait pas davantage qu'une sonde muette — et
    // celle-ci verrait rouge sur l'alignement d'un tableau de chiffres.
    expect(
      taillesDe('"text-left text-center text-right text-justify text-start text-end"'),
    ).toEqual([]);
    expect(
      taillesDe('"text-wrap text-nowrap text-balance text-pretty text-ellipsis text-clip"'),
    ).toEqual([]);
    // La couleur — l'affaire de `couleurs.test.ts`, jamais de ce fichier.
    expect(
      taillesDe('"text-neutral-500 text-white text-texte-secondaire text-alerte-texte"'),
    ).toEqual([]);
    expect(
      taillesDe('"text-[#3987e5] text-[rgb(57,135,229)] text-[color:var(--ton)]"'),
    ).toEqual([]);
    // L'ombre de texte de Tailwind v4, qui emprunte les mêmes suffixes que les
    // pas (`sm`, `lg`) : sans la coupe sur le tiret, `text-shadow-sm` serait lu
    // comme un `text-sm`.
    expect(taillesDe('"text-shadow-sm text-shadow-lg text-shadow-2xs"')).toEqual([]);
    // Et une valeur arbitraire qui n'est pas une taille de texte.
    expect(taillesDe('"w-[38ch] grid-cols-[1fr_auto] leading-[1.4]"')).toEqual([]);
  });

  it("ne crie pas sur l'échelle elle-même", () => {
    // Le pendant du contrôle ci-dessus, et le plus important : la sonde verrait
    // rouge sur la sortie qu'elle recommande.
    expect(
      taillesDe('"text-micro text-annexe text-corps text-titre text-page text-chiffre"'),
    ).toEqual([]);
    expect(taillesDe('"md:text-page dark:text-annexe text-corps/6"')).toEqual([]);
  });

  it("ne compte pas la prose, et lit les chaînes d'un gabarit", () => {
    // Ce fichier-ci, le README et les commentaires du produit citent des pas de
    // Tailwind **pour dire qu'il ne faut plus les écrire**. Les compter ferait
    // grossir le résidu à chaque explication ajoutée.
    expect(taillesDe("// remplacer text-xs par text-annexe")).toEqual([]);
    expect(taillesDe("/* l'ancien \"text-sm\" */")).toEqual([]);
    // Le `//` d'une URL n'ouvre aucun commentaire : sans cette précaution, tout
    // ce qui suit un lien dans le fichier disparaîtrait du balayage.
    expect(
      taillesDe('const u = "https://x.test";\nconst c = "text-sm";'),
    ).toEqual(["text-sm"]);
    // La forme du produit : une classe conditionnelle dans un gabarit.
    expect(taillesDe('`x ${dense ? "text-xs" : "text-sm"}`')).toEqual([
      "text-xs",
      "text-sm",
    ]);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// 4. LES DEUX ÉCHELLES, ET CE QUE CELLE DU DÉPÔT NE SAIT PAS RENDRE
//
// Toutes les tailles arbitraires ne sont pas des fautes, et un balayage qui les
// traiterait toutes pareil enverrait forcer des équivalences approximatives —
// c'est-à-dire changer le rendu pour faire taire un test. Là où **aucun pas ne
// correspond**, la bonne issue est de nommer le manque.
//
// ⚠ Ces manques ne sortent PAS du compte du résidu ci-dessous, et c'est
// délibéré : une exemption par motif serait la porte par laquelle n'importe
// quelle taille entrerait en invoquant « l'échelle ne couvre pas mon cas ». Ils
// disent seulement **jusqu'où** le résidu peut descendre sans que l'échelle
// bouge d'abord — et chacun est un ticket à ouvrir, pas une tolérance.
// ─────────────────────────────────────────────────────────────────────────────

type Manque = {
  /** Le pas qui manque, sous le nom qu'il porterait. */
  readonly pas: string;
  /** Un fichier qui en souffre, et la taille qu'on y lit faute de mieux. */
  readonly temoin: string;
  readonly classe: string;
  readonly raison: string;
};

const MANQUES_DE_L_ECHELLE: readonly Manque[] = [
  {
    pas: "graduation",
    temoin: "components/GraphiqueEvolutionCout.tsx",
    classe: "text-[10px]",
    raison:
      "l'étiquette d'une **graduation d'axe**, dans un SVG. Les cinq pas sont " +
      "nommés par un rôle d'interface — horodatage, détail, texte courant, " +
      "titre de carte, titre d'écran — et aucun ne décrit une échelle de " +
      "graphique ; le plus petit, `micro`, est à 11 px, soit un pixel au-dessus. " +
      "Le replier dessus changerait la densité du graphique pour faire taire " +
      "un test. C'est le pendant typographique du manque `serie` que " +
      "`couleurs.test.ts` nomme sur ce même fichier",
  },
];

describe("les deux échelles, lues et non recopiées", () => {
  it("lit bien celle de Tailwind, et pas un fichier vide", () => {
    // Sans ce contrôle, un `theme.css` déplacé rendrait zéro nom, donc un motif
    // qui ne refuse plus rien — vert par accident.
    expect(TAILLES_TAILWIND.size).toBeGreaterThanOrEqual(13);
    expect(TAILLES_TAILWIND).toContain("xs");
    expect(TAILLES_TAILWIND).toContain("base");
    expect(TAILLES_TAILWIND).toContain("9xl");
    // Ce qui partage le préfixe sans être un corps de texte reste dehors.
    expect(TAILLES_TAILWIND).not.toContain("shadow");
    expect([...TAILLES_TAILWIND].some((n) => n.includes("-"))).toBe(false);
  });

  it("lit bien celle du dépôt, et ses six pas nommés", () => {
    expect([...ECHELLE.pas.keys()].sort()).toEqual([
      "annexe",
      "chiffre",
      "corps",
      "micro",
      "page",
      "titre",
    ]);
  });

  it("garde les alias de #533 rabattus sur un pas qui existe", () => {
    // LE contrôle qui rend vrai le conseil du message d'échec. Tant que
    // `--text-xs: var(--text-annexe)`, « écrire text-annexe » ne change rien au
    // rendu et la migration est gratuite. Le jour où l'alias reprendrait une
    // valeur propre, ce ne serait plus une jumelle mais une divergence, et ce
    // fichier ne dirait plus la vérité sur ce qu'il refuse.
    expect([...ECHELLE.alias].sort()).toEqual([
      ["sm", "corps"],
      ["xs", "annexe"],
    ]);
    for (const [alias, vise] of ECHELLE.alias) {
      expect(ECHELLE.pas.has(vise), `--text-${alias} vise --text-${vise}, absent de l'échelle`).toBe(
        true,
      );
    }
  });

  it("ne laisse aucun pas de l'échelle porter un nom de Tailwind", () => {
    // Le garde-fou qui empêche la sonde de refuser le socle lui-même : un pas
    // baptisé `--text-xl` serait à la fois ce qu'on recommande et ce qu'on
    // refuse. Les deux alias sont l'exception, et ils sont reconnus comme tels
    // parce qu'ils pointent sur un pas au lieu de porter une valeur.
    for (const nom of ECHELLE.pas.keys()) {
      expect(
        TAILLES_TAILWIND.has(nom),
        `--text-${nom} porte un nom de pas Tailwind tout en ayant sa propre ` +
          "valeur : la sonde refuserait la classe que l'échelle recommande",
      ).toBe(false);
    }
  });
});

describe("ce que l'échelle ne sait pas rendre", () => {
  it("ne nomme aucun manque que l'échelle couvre déjà", () => {
    // Le contrôle qui empêche cette liste de devenir l'endroit où l'on range ce
    // qui échoue : le jour où le `--text-<pas>` est déclaré, la ligne rougit et
    // son témoin se replie. ⚠ Il ne tient que sur le **nom** — un pas ajouté
    // sous un autre nom (`--text-axe`) ne le déclencherait pas.
    for (const { pas, temoin } of MANQUES_DE_L_ECHELLE) {
      expect(
        ECHELLE.pas.has(pas) || ECHELLE.alias.has(pas),
        `--text-${pas} existe désormais : ${temoin} n'a plus de raison de ` +
          "l'écrire à la main, et cette ligne n'a plus de raison d'être",
      ).toBe(false);
    }
  });

  it("garde chaque manque rattaché à un témoin qui existe et qui le porte encore", () => {
    // Une exemption qui ne désigne plus rien est un motif qu'on relira comme
    // s'il valait encore (règle de `HORS_PAIRES`, #534).
    const sources = sourcesDuProduit(EXTENSIONS);
    for (const { pas, temoin, classe, raison } of MANQUES_DE_L_ECHELLE) {
      expect(sources, `${pas} : ${temoin} n'existe plus`).toContain(temoin);
      expect(
        taillesDe(lireSource(temoin)),
        `${pas} : ${temoin} ne porte plus « ${classe} » — le manque est-il ` +
          `comblé, ou la ligne périmée ?\n  ce qu'elle disait : ${raison}`,
      ).toContain(classe);
    }
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// 5. LE RÉSIDU, NOMMÉ ET COMPTÉ
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Ce qui restait de tailles écrites hors de l'échelle au moment de #981,
 * **fichier par fichier avec son compte exact** — mesuré le 2026-09-20, jamais
 * estimé.
 *
 * La raison est la même pour toutes les lignes et n'est donc pas répétée
 * trente-sept fois : ces écrans sont **antérieurs à l'échelle** de #533, qui n'a
 * migré aucun appelant. Ce ticket ne les migre pas non plus — il pose le compte
 * et refuse le suivant. La ligne qui porte en plus un manque de l'échelle est
 * annotée : celle-là ne descendra pas à zéro tant que l'échelle n'aura pas bougé.
 *
 * Le compte est **exact et non un plafond** : une taille de plus rougit, une
 * taille de **moins** rougit aussi tant que la ligne n'est pas mise à jour.
 * C'est ce qui fait qu'un résidu ne peut que décroître, et que chaque
 * décroissance est un geste **écrit** — sans quoi le chiffre du README serait
 * vrai le jour où on l'a mesuré et faux le lendemain. Le message d'échec rend la
 * ligne à recopier : la contrainte doit coûter une seconde, pas une enquête.
 */
const RESIDU = new Map<string, number>([
  ["app/couts/page.tsx", 8],
  ["app/journal/page.tsx", 7],
  ["app/page.tsx", 1],
  // `components/BanniereErreurApi.tsx` est **sorti** du tableau par #996 (1 → 0) :
  //   son `text-sm` était le seul pas hors échelle du fichier, et le bandeau
  //   réécrit pour nommer la panne écrit `text-corps` et `text-annexe` — la
  //   hiérarchie entre le message et le diagnostic est précisément ce que
  //   l'échelle sert à dire.
  ["components/BarreLaterale.tsx", 2],
  ["components/BarreSuperieure.tsx", 3],
  ["components/BasculeTheme.tsx", 1],
  ["components/CentreNotifications.tsx", 6],
  ["components/EditeurAgent.tsx", 27],
  ["components/GraphiqueEvolutionCout.tsx", 5], // manque : `graduation`
  ["components/GuidePriseEnMain.tsx", 6],
  ["components/IndicateursTableauDeBord.tsx", 1],
  ["components/MenuAide.tsx", 4],
  ["components/OngletMcpAgent.tsx", 8],
  ["components/PosteVide.tsx", 10],
  ["components/RepartitionAgents.tsx", 4],
  ["components/brief/ValidationBrief.tsx", 1],
  ["components/brief/ValidationBriefs.tsx", 1],
  ["components/chat/CadrageDansLeFil.tsx", 1],
  ["components/chat/FilDeCadrage.tsx", 1],
  ["components/chat/SourcesDuFil.tsx", 2],
  ["components/integrations/BibliothequeMcp.tsx", 6],
  ["components/parametres/NavigationParametres.tsx", 1],
  ["components/parametres/ParametresAgents.tsx", 4],
  ["components/parametres/ParametresApparence.tsx", 1],
  ["components/parametres/ParametresCouts.tsx", 1],
  ["components/parametres/ParametresFournisseurs.tsx", 6],
  ["components/parametres/ParametresGeneral.tsx", 4],
  ["components/parametres/ParametresNotifications.tsx", 1],
  ["components/parametres/SectionParametres.tsx", 2],
  ["components/projets/ChoixProjet.tsx", 10],
  ["components/projets/ExplorateurDossiers.tsx", 8],
  ["components/projets/FormulaireProjet.tsx", 9],
  ["components/projets/ListeProjets.tsx", 5],
  ["components/projets/SelecteurProjet.tsx", 5],
  ["components/runs/ListeRuns.tsx", 1],
  ["components/runs/VueRun.tsx", 1],
]);

/** Le compte du README — épinglé ici pour qu'il ne puisse pas dériver en silence. */
const TOTAL_ANNONCE = 164;

/** Ce que le produit porte aujourd'hui, fichier par fichier. */
function residuMesure(): Map<string, string[]> {
  const parFichier = new Map<string, string[]>();
  for (const fichier of sourcesDuProduit(EXTENSIONS)) {
    const tailles = taillesDe(lireSource(fichier));
    if (tailles.length > 0) parFichier.set(fichier, tailles);
  }
  return parFichier;
}

/** La ligne à recopier dans `RESIDU`, pour que la mise à jour coûte une seconde. */
const ligne = (fichier: string, compte: number) => `  ["${fichier}", ${compte}],`;

/**
 * Le verdict rendu sur une mesure, face au tableau — les deux moitiés du
 * critère : ce qui **entre** (une taille de plus qu'inscrit, ou un fichier qui
 * n'y est pas du tout) et ce qui a **vieilli** (une ligne qui annonce plus que
 * ce qu'on mesure).
 *
 * Chaque défaut est nommé **une fois** : un dépassement est une nouveauté, pas
 * une ligne périmée, et le compter des deux côtés ferait chercher deux causes là
 * où il n'y en a qu'une.
 */
function confronter(
  mesure: ReadonlyMap<string, readonly string[]>,
  tableau: ReadonlyMap<string, number>,
  sources: readonly string[],
): { nouvelles: string[]; perimees: string[] } {
  const nouvelles: string[] = [];
  for (const [fichier, tailles] of [...mesure].sort()) {
    const inscrit = tableau.get(fichier) ?? 0;
    if (tailles.length <= inscrit) continue;
    nouvelles.push(
      `  ${fichier} — ${tailles.length} taille(s) pour ${inscrit} au tableau\n` +
        `    au-delà du compte inscrit : ${tailles.slice(inscrit).join(", ")}\n` +
        (inscrit === 0
          ? ""
          : `    si le tableau doit suivre :\n  ${ligne(fichier, tailles.length)}\n`),
    );
  }

  const perimees: string[] = [];
  for (const [fichier, inscrit] of tableau) {
    const reel = mesure.get(fichier)?.length ?? 0;
    if (reel >= inscrit) continue;
    if (reel > 0) {
      perimees.push(
        `  ${fichier} — inscrit pour ${inscrit}, on en mesure ${reel}\n${ligne(fichier, reel)}`,
      );
    } else if (sources.includes(fichier)) {
      perimees.push(
        `  ${fichier} — inscrit pour ${inscrit}, on n'en mesure plus aucune : retirer la ligne`,
      );
    } else {
      perimees.push(
        `  ${fichier} — inscrit pour ${inscrit}, mais le fichier n'existe plus : retirer la ligne`,
      );
    }
  }
  return { nouvelles, perimees };
}

/**
 * Le conseil du message d'échec, **dérivé** de l'échelle plutôt que recopié :
 * « text-xs → text-annexe, text-sm → text-corps ». Le jour où un alias change de
 * cible, la phrase change avec lui.
 */
const CONSEIL = [...ECHELLE.alias]
  .map(([alias, vise]) => `text-${alias} → text-${vise}`)
  .sort()
  .join(", ");

describe("le verdict, prouvé sur une mesure fabriquée", () => {
  // Ce que le produit subit, ce n'est pas le motif seul mais le motif **plus**
  // la confrontation au tableau. Une comparaison qui cesserait de comparer
  // rendrait « rien à signaler » sur les contrôles suivants, et c'est
  // précisément le vert qu'on ne pourrait pas distinguer du bon.
  const sources = ["a.tsx", "b.tsx", "c.tsx"];
  const tableau = new Map([
    ["a.tsx", 2],
    ["b.tsx", 1],
  ]);

  it("se tait sur une mesure conforme", () => {
    const mesure = new Map([
      ["a.tsx", ["text-xs", "text-sm"]],
      ["b.tsx", ["text-[10px]"]],
    ]);
    expect(confronter(mesure, tableau, sources)).toEqual({ nouvelles: [], perimees: [] });
  });

  it("refuse un fichier que le tableau ne nomme pas", () => {
    // Le cas nominal du prochain écran : il n'a droit à aucune taille, et le
    // message ne lui propose pas de ligne à recopier — c'est un pas à choisir
    // dans l'échelle, pas un compte à mettre à jour.
    const mesure = new Map([
      ["a.tsx", ["text-xs", "text-sm"]],
      ["b.tsx", ["text-[10px]"]],
      ["c.tsx", ["text-lg"]],
    ]);
    const { nouvelles, perimees } = confronter(mesure, tableau, sources);
    expect(nouvelles).toHaveLength(1);
    expect(nouvelles[0]).toContain("c.tsx — 1 taille(s) pour 0 au tableau");
    expect(nouvelles[0]).toContain("text-lg");
    expect(nouvelles[0]).not.toContain("si le tableau doit suivre");
    // Un fichier absent du tableau ne peut pas rendre une ligne périmée : la
    // seconde moitié ne juge que ce qui y est inscrit.
    expect(perimees).toEqual([]);
  });

  it("refuse un dépassement, et ne le compte qu'une fois", () => {
    const mesure = new Map([
      ["a.tsx", ["text-xs", "text-sm", "text-[13px]"]],
      ["b.tsx", ["text-[10px]"]],
    ]);
    const { nouvelles, perimees } = confronter(mesure, tableau, sources);
    expect(nouvelles).toHaveLength(1);
    expect(nouvelles[0]).toContain('["a.tsx", 3],');
    expect(perimees).toEqual([]);
  });

  it("réclame la mise à jour d'une ligne devenue trop haute", () => {
    // Le versant qui fait décroître le résidu : une migration silencieuse est
    // une migration que le README ne dit pas.
    const mesure = new Map([
      ["a.tsx", ["text-xs"]],
      ["b.tsx", ["text-[10px]"]],
    ]);
    const { nouvelles, perimees } = confronter(mesure, tableau, sources);
    expect(nouvelles).toEqual([]);
    expect(perimees).toHaveLength(1);
    expect(perimees[0]).toContain('["a.tsx", 1],');
  });

  it("distingue un fichier migré d'un fichier disparu", () => {
    // Les deux appellent le même geste — retirer la ligne — mais pas la même
    // relecture : l'un est une migration réussie, l'autre un fichier supprimé
    // dont la ligne serait relue comme si elle valait encore.
    const sansTailles = new Map([["b.tsx", ["text-[10px]"]]]);
    const vide = confronter(sansTailles, tableau, sources);
    expect(vide.perimees).toEqual([
      "  a.tsx — inscrit pour 2, on n'en mesure plus aucune : retirer la ligne",
    ]);
    // Le même tableau, mais `a.tsx` n'est plus dans les sources parcourues.
    const disparu = confronter(sansTailles, tableau, ["b.tsx", "c.tsx"]);
    expect(disparu.perimees).toEqual([
      "  a.tsx — inscrit pour 2, mais le fichier n'existe plus : retirer la ligne",
    ]);
  });

  it("dérive son conseil de l'échelle, au lieu de le recopier", () => {
    expect(CONSEIL).toBe("text-sm → text-corps, text-xs → text-annexe");
  });
});

describe("le résidu de tailles écrites hors de l'échelle", () => {
  it("ne laisse entrer aucune taille nouvelle", () => {
    // Le critère du ticket : la sonde tolère ce qui est nommé, elle refuse le
    // suivant. Un fichier absent du tableau n'a droit à aucune taille ; un
    // fichier présent n'a droit qu'aux siennes.
    const { nouvelles } = confronter(residuMesure(), RESIDU, sourcesDuProduit(EXTENSIONS));
    expect(
      nouvelles,
      `\n${nouvelles.join("\n")}\n` +
        "Une taille se choisit dans l'échelle : `text-micro`, `text-annexe`,\n" +
        "`text-corps`, `text-titre`, `text-page` — nommés par leur rôle, pas par\n" +
        `leur valeur (voir apps/web/README.md, « L'échelle typographique »).\n` +
        `Les jumelles de Tailwind rendent la même taille sous un autre nom (${CONSEIL}) :\n` +
        "les écrire ne change rien au rendu, et fait diverger le vocabulaire.\n" +
        "Si aucun pas ne correspond, c'est un manque de l'échelle : l'inscrire dans\n" +
        "MANQUES_DE_L_ECHELLE et ouvrir le ticket, plutôt que de forcer une\n" +
        "équivalence qui changerait le rendu.\n",
    ).toHaveLength(0);
  });

  it("ne garde aucune ligne périmée : le compte est exact, pas un plafond", () => {
    // Le versant qui fait décroître le résidu. Une ligne qui annonce plus que
    // ce qu'on mesure est une migration faite sans que le tableau l'ait dit —
    // et un chiffre de README devenu faux.
    const { perimees } = confronter(residuMesure(), RESIDU, sourcesDuProduit(EXTENSIONS));
    expect(
      perimees,
      `\n${perimees.join("\n")}\n\nUne taille retirée est une bonne nouvelle : mettre le tableau à jour, ` +
        `et le total de apps/web/README.md avec (${TOTAL_ANNONCE} aujourd'hui).\n`,
    ).toHaveLength(0);
  });

  it("porte le total qu'annonce le README", () => {
    // Le chiffre du README n'est pas une note de bas de page : c'est ce sur quoi
    // le prochain lot mesurera son gain. Le laisser vivre hors d'un test, c'est
    // le laisser devenir faux.
    const total = [...RESIDU.values()].reduce((somme, n) => somme + n, 0);
    expect(total, "le total du tableau a bougé — mettre à jour apps/web/README.md").toBe(
      TOTAL_ANNONCE,
    );
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// 6. QUE LE BALAYAGE NE DEVIENNE PAS MUET
//
// Tout ce qui précède rend un ✓ si le motif cesse de matcher, si le parcours des
// sources rend une liste vide, ou si les chaînes ne se lisent plus. Ces planchers
// rendent le ✓ opposable — c'est la moitié que perd le plus facilement un
// balayage de sources.
// ─────────────────────────────────────────────────────────────────────────────

describe("la couverture du balayage", () => {
  it("parcourt bien le produit, et pas une liste vide", () => {
    const sources = sourcesDuProduit(EXTENSIONS);
    expect(sources.length).toBeGreaterThanOrEqual(100);
    expect(sources).toContain("components/Primitives.tsx");
    expect(sources.filter((f) => f.startsWith("app/")).length).toBeGreaterThanOrEqual(10);
  });

  it("lit bien des feuilles de classes, et pas un fichier vide", () => {
    // Un motif d'extraction cassé ramènerait les chaînes à zéro, donc « aucune
    // taille » — avec les mots de « tout est sur l'échelle ».
    let chaines = 0;
    for (const fichier of sourcesDuProduit(EXTENSIONS)) {
      chaines += [
        ...sansCommentaires(lireSource(fichier)).matchAll(
          /"([^"\n]*)"|'([^'\n]*)'|`([^`]*)`/g,
        ),
      ].length;
    }
    expect(chaines).toBeGreaterThanOrEqual(4000);
  });

  it("mesure encore un résidu, et le tableau le couvre entièrement", () => {
    // Le plancher inverse des deux contrôles ci-dessus, et le plus fort : tant
    // que le produit porte des tailles hors échelle, la sonde doit en voir — et
    // le tableau doit être exactement l'ensemble des fichiers qui en portent, ni
    // plus, ni moins. Le jour où le résidu tombe à zéro, ce test est ce qui
    // invite à retirer le tableau plutôt qu'à le laisser mentir.
    const mesure = residuMesure();
    expect([...mesure.keys()].sort()).toEqual([...RESIDU.keys()].sort());
    const total = [...mesure.values()].reduce((somme, t) => somme + t.length, 0);
    expect(total).toBe(TOTAL_ANNONCE);
  });

  it("voit encore les deux familles qu'elle refuse, dans le produit", () => {
    // Le plancher le plus fin : le compte global pourrait tenir alors que l'une
    // des deux moitiés du motif est morte. Le produit porte aujourd'hui les
    // deux, et c'est ce que le critère du ticket demande de garder visible.
    const tous = [...residuMesure().values()].flat();
    expect(tous.filter((t) => PAS_DE_TAILWIND.test(utilite(t))).length).toBeGreaterThan(0);
    expect(tous.filter((t) => TAILLE_ARBITRAIRE.test(utilite(t))).length).toBeGreaterThan(0);
  });
});

/**
 * Le padding des conteneurs et des contrôles, tenu par un barème (#983).
 *
 * #533 a posé la palette et #895 la sonde qui refuse la prochaine **couleur**
 * écrite à la main. Les espacements, eux, n'étaient gardés par rien : docs/30
 * §2.3 relevait **8 paddings de conteneur** et autant de paires de contrôle,
 * quand `Carte` n'en nomme que trois et `Bouton`/`Badge`/`CLASSE_CONTROLE` trois
 * autres. Cinq des huit paddings de conteneur sont hors barème — et c'est le
 * **rythme d'un écran**, ce qui se voit en premier quand deux blocs voisins ne
 * respirent pas pareil.
 *
 * ── Ce que la sonde juge, et pourquoi cette portée-là ────────────────────────
 *
 * Étroite, et c'est voulu : on ne juge que le padding **d'un conteneur ou d'un
 * contrôle**. Les marges, les `gap` et les paddings dirigés (`pt-`, `pl-`…)
 * restent dehors — ils règlent la mise en page, pas le rythme intérieur d'une
 * boîte, et un résidu étalé sur tout le dépôt ne serait plus lu (c'est le
 * partage de #832, qui ne balaie que les contrôles de saisie).
 *
 * Deux formes, deux rôles, et **c'est la forme qui dit le rôle** :
 *
 * - **`p-<n>`, le padding d'un conteneur.** Un écart égal des quatre côtés est
 *   le rythme intérieur d'une boîte : rien d'autre ne s'écrit ainsi. Il est donc
 *   jugé **partout**, sans condition — c'est ce qui fait voir la surcharge la
 *   plus directe du barème, `<Carte densite="aucune" className="p-5">`
 *   (`PosteVide`), qu'un filtre sur la feuille aurait manquée : la feuille y vaut
 *   `"p-5"`, et c'est la `Carte` autour qui porte le rayon.
 * - **La paire `px-<a> py-<b>`, le padding d'un contrôle.** Elle dissocie
 *   l'horizontale de la verticale, ce que fait un élément réglé sur une ligne de
 *   texte — un bouton, un onglet, une entrée de menu, un badge.
 *
 * ⚠ Mais une paire sert **aussi** à border une zone de page, et ces deux-là ne
 * se distinguent pas par leurs chiffres : mesuré le 2026-09-20, `px-3 py-2`
 * rend à la fois l'onglet d'`OngletsAgent` et la bannière de `BanniereErreurApi`,
 * et `px-4 py-3` ne rend **que** les trois bandes de `PanneauDetailTache`
 * (en-tête, corps, pied). D'où la condition posée sur la paire, et sur elle
 * seule : elle n'est jugée que si sa feuille habille quelque chose — un
 * **rayon**, ou une marque d'**interaction**. Sans elle, la sonde aurait réclamé
 * `Bouton` à qui posait le padding d'un `<main>` : quelques faux positifs
 * suffisent à ce qu'on cesse de lire un résidu.
 *
 * ── Ce sur quoi ce fichier se tait ───────────────────────────────────────────
 *
 * `components/Primitives.tsx`, qui **définit** le barème et n'est donc pas jugé
 * par lui — comme `couleurs.test.ts` lit `globals.css` au lieu de le balayer. Ce
 * n'est pas une exemption : ce que le socle écrit **est** le barème, et son
 * élargissement rougit plus bas, où le barème est épinglé.
 */

import { describe, expect, it } from "vitest";

import { lireSource, sansCommentaires, sourcesDuProduit } from "./sources";

// ─────────────────────────────────────────────────────────────────────────────
// 1. LA SONDE
// ─────────────────────────────────────────────────────────────────────────────

/** Les sources balayées. Une feuille de classes peut vivre dans un `.ts` de constantes. */
const EXTENSIONS = [".tsx", ".ts"] as const;

/** Le fichier qui **porte** le barème : lu pour l'établir, jamais jugé par lui. */
const SOCLE = "components/Primitives.tsx";

/** Un pas de l'échelle Tailwind (`3`, `1.5`, `0.5`) ou une valeur arbitraire (`[10px]`). */
const PAS = String.raw`\d+(?:\.\d+)?|\[[^\]]+\]`;

const P_UNIFORME = new RegExp(`^p-(?:${PAS})$`);
const PX = new RegExp(`^px-(?:${PAS})$`);
const PY = new RegExp(`^py-(?:${PAS})$`);

/** Ce qui fait d'une feuille une **boîte** : elle a une forme à elle. */
const RAYON = /^rounded(?:-|$)/;

/**
 * Ce qui fait d'une feuille un **contrôle** : elle répond. Le jeton entier est
 * jugé, variantes comprises — c'est là que vit la marque (`hover:bg-survol`), et
 * `group-hover:`/`peer-focus:` comptent aussi : un élément qui se peint quand
 * son groupe est survolé est la moitié visible d'un contrôle.
 */
const INTERACTION =
  /(?:^|:)(?:hover|focus|focus-visible|focus-within|active|disabled|group-hover|group-focus|peer-focus|peer-hover|aria-[\w-]+):/;

/** L'autre marque d'un contrôle, sans variante : il se clique. */
const CURSEUR = /^cursor-pointer$/;

/** `sm:hover:p-4` → `p-4` : les variantes tombent, c'est l'utilité qui est jugée. */
const utilite = (jeton: string) => jeton.slice(jeton.lastIndexOf(":") + 1);

/**
 * Les feuilles de classes d'une source — une par **chaîne littérale**, dans
 * l'ordre où elles y sont écrites.
 *
 * C'est là que vivent les feuilles de ce produit, et c'est aussi ce qui donne à
 * la condition de portée son unité de lecture : le rayon et le padding d'un
 * bouton s'écrivent dans la même chaîne (`"gap-1.5 rounded-md px-3 py-1.5"`), là
 * où une bande de panneau n'écrit que son padding. Un gabarit avale les chaînes
 * qu'il interpole, d'où les guillemets à retirer du jeton — la mécanique de
 * `pairesDe` dans `couleurs.test.ts`, et les deux ont besoin des mêmes
 * précautions.
 */
function feuillesDe(source: string): string[][] {
  const feuilles: string[][] = [];
  for (const [, guillemets, apostrophes, gabarit] of sansCommentaires(
    source,
  ).matchAll(/"([^"\n]*)"|'([^'\n]*)'|`([^`]*)`/g)) {
    const jetons = (guillemets ?? apostrophes ?? gabarit ?? "")
      .split(/[\s${}]+/)
      .map((brut) => brut.replace(/^["'`]+|["'`]+$/g, ""))
      .filter(Boolean);
    if (jetons.length > 0) feuilles.push(jetons);
  }
  return feuilles;
}

/** Cette feuille habille-t-elle une boîte ou un contrôle ? (la condition de la paire) */
const habille = (jetons: readonly string[]): boolean =>
  jetons.some(
    (jeton) =>
      RAYON.test(utilite(jeton)) ||
      INTERACTION.test(jeton) ||
      CURSEUR.test(utilite(jeton)),
  );

/**
 * Les paddings **jugeables** d'une feuille : ses `p-<n>`, et sa paire si elle en
 * porte une et que la feuille habille quelque chose.
 *
 * Une seule paire par feuille, la première : une feuille responsive écrit son
 * pas de base puis sa reprise (`px-4 py-10 sm:py-16`), et compter les deux
 * ferait du point de rupture un second padding à justifier.
 */
function paddingsDe(jetons: readonly string[]): string[] {
  const utilites = jetons.map(utilite);
  const paddings = utilites.filter((u) => P_UNIFORME.test(u));
  const px = utilites.find((u) => PX.test(u));
  const py = utilites.find((u) => PY.test(u));
  if (px && py && habille(jetons)) paddings.push(`${px} ${py}`);
  return paddings;
}

/** Tous les paddings jugeables d'une source, dans l'ordre. */
const paddingsDeLaSource = (source: string): string[] =>
  feuillesDe(source).flatMap(paddingsDe);

// ─────────────────────────────────────────────────────────────────────────────
// 2. LE BARÈME — LU DANS LE SOCLE, JAMAIS RECOPIÉ
//
// Le barème n'est pas une liste tenue ici : c'est **ce que les primitives
// écrivent**. Le lire plutôt que le recopier est ce qui fait qu'un pas changé
// dans `Primitives.tsx` déplace le barème du même geste, au lieu de laisser deux
// tables diverger — la règle de `tokensDeLaPalette` (#895) et de la purge (#830).
//
// La lecture ne filtre pas : tout padding écrit par le socle est au barème, y
// compris celui d'`EtatVide` (`p-4`). C'est cohérent avec ce que le socle est —
// l'endroit d'où l'on peut promettre qu'un pas a été choisi une fois.
// ─────────────────────────────────────────────────────────────────────────────

/** Les deux moitiés du barème, telles que le socle les écrit. */
function baremeDuSocle(): { conteneur: Set<string>; controle: Set<string> } {
  const conteneur = new Set<string>();
  const controle = new Set<string>();
  for (const jetons of feuillesDe(lireSource(SOCLE))) {
    const utilites = jetons.map(utilite);
    for (const u of utilites) if (P_UNIFORME.test(u)) conteneur.add(u);
    const px = utilites.find((u) => PX.test(u));
    const py = utilites.find((u) => PY.test(u));
    if (px && py) controle.add(`${px} ${py}`);
  }
  return { conteneur, controle };
}

const BAREME = baremeDuSocle();

/** Un padding au barème, quelle que soit sa moitié. */
const auBareme = (padding: string) =>
  BAREME.conteneur.has(padding) || BAREME.controle.has(padding);

describe("le barème, lu dans le socle", () => {
  it("rend les trois densités de `Carte` et les trois tailles de contrôle", () => {
    // L'épinglage : sans lui, un `p-6` glissé dans une primitive élargirait le
    // barème **en silence** et ferait taire le résidu d'autant. Un pas de plus
    // est une décision d'écran (docs/30 §2.5) — elle se prend, puis cette ligne
    // suit. Trois pas de conteneur, trois de contrôle, et pas une galerie :
    // c'est la promesse que docs/30 §2.2 a mesurée à l'envers.
    expect([...BAREME.conteneur].sort()).toEqual(["p-2.5", "p-3", "p-4"]);
    expect([...BAREME.controle].sort()).toEqual([
      "px-2 py-0.5",
      "px-2.5 py-1",
      "px-3 py-1.5",
    ]);
  });

  it("lit bien le socle, et pas un fichier vide", () => {
    // Un barème vide rendrait « tout est hors barème » ; un barème lu sur une
    // source muette rendrait l'inverse une fois le résidu retiré. Les deux
    // planchers tiennent au même endroit.
    expect(feuillesDe(lireSource(SOCLE)).length).toBeGreaterThanOrEqual(50);
    expect(sourcesDuProduit(EXTENSIONS)).toContain(SOCLE);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// 3. LA SONDE, PROUVÉE AVANT DE SERVIR
//
// Un balayage qui ne rougit jamais est indiscernable d'un balayage devenu muet —
// les deux rendent un ✓. Ce bloc lui pose donc les questions dont on connaît la
// réponse **avant** qu'il ne juge le produit : les formes que le ticket a
// relevées, les contournements plausibles, et ce sur quoi il ne doit surtout pas
// crier. C'est la règle de `contraste.test.ts` (#534) et de `couleurs.test.ts`.
// ─────────────────────────────────────────────────────────────────────────────

describe("la sonde, prouvée avant de servir", () => {
  it("reconnaît le padding de conteneur, partout et sans condition", () => {
    // La surcharge la plus directe du barème, recopiée de `PosteVide` : la
    // densité est mise à `aucune` et le pas repassé en `className`. La feuille
    // n'est qu'un `p-5` — aucun rayon, aucune interaction —, et c'est
    // précisément ce qu'un filtre posé sur la paire aurait laissé passer.
    expect(paddingsDeLaSource('<Carte densite="aucune" className="p-5">')).toEqual(["p-5"]);
    expect(paddingsDeLaSource('"rounded-lg border bg-surface p-3"')).toEqual(["p-3"]);
    // Les cinq pas hors barème relevés par docs/30 §2.3, d'un coup.
    expect(paddingsDeLaSource('"p-2" "p-1.5" "p-1" "p-5" "p-0.5"')).toEqual([
      "p-2",
      "p-1.5",
      "p-1",
      "p-5",
      "p-0.5",
    ]);
    // Sous ses variantes : un pas responsive reste un pas.
    expect(paddingsDeLaSource('"sm:p-6 dark:hover:p-2"')).toEqual(["p-6", "p-2"]);
  });

  it("reconnaît la paire d'un contrôle, à son rayon ou à son interaction", () => {
    // Les deux marques, chacune seule : le bouton se reconnaît à sa forme,
    // l'entrée de menu à ce qu'elle répond au survol.
    expect(paddingsDeLaSource('"gap-1.5 rounded-md px-3 py-2 text-annexe"')).toEqual([
      "px-3 py-2",
    ]);
    expect(
      paddingsDeLaSource('"flex w-full items-center px-3 py-2 text-left hover:bg-survol"'),
    ).toEqual(["px-3 py-2"]);
    expect(paddingsDeLaSource('"px-2 py-1 cursor-pointer"')).toEqual(["px-2 py-1"]);
    // Un contrôle désactivé ou piloté par son groupe en est un aussi.
    expect(paddingsDeLaSource('"px-2 py-1 disabled:opacity-50"')).toEqual(["px-2 py-1"]);
    expect(paddingsDeLaSource('"px-2 py-1 group-hover:bg-survol"')).toEqual(["px-2 py-1"]);
  });

  it("ne crie pas sur la mise en page — c'est ce qui rend le résidu lisible", () => {
    // Les trois bandes de `PanneauDetailTache` et la colonne de `ChoixProjet`,
    // recopiées telles quelles. Ce ne sont pas des contrôles : leur réclamer
    // `Bouton` discréditerait la sonde entière.
    expect(
      paddingsDeLaSource('"flex items-start gap-2 border-b border-bord px-4 py-3"'),
    ).toEqual([]);
    expect(
      paddingsDeLaSource('"mx-auto flex w-full max-w-2xl flex-col gap-6 px-4 py-10 sm:py-16"'),
    ).toEqual([]);
    // Un padding dirigé seul n'est ni l'un ni l'autre : c'est de la mise en page.
    expect(paddingsDeLaSource('"rounded-md px-4 pt-2 pb-6 pl-3"')).toEqual([]);
    // Les marges et les `gap`, hors compte par la portée du ticket.
    expect(paddingsDeLaSource('"m-4 mt-2 gap-3 space-y-2 rounded-md"')).toEqual([]);
  });

  it("ne compte qu'une paire par feuille, celle du pas de base", () => {
    // Une feuille responsive écrit son pas puis sa reprise ; compter les deux
    // ferait du point de rupture un second padding à justifier.
    expect(paddingsDeLaSource('"rounded-md px-3 py-1.5 sm:px-4 sm:py-2"')).toEqual([
      "px-3 py-1.5",
    ]);
    // Un `px-` sans `py-` (ou l'inverse) n'est pas une paire : c'est un réglage
    // sur un seul axe, donc de la mise en page. 69 `py-1` du produit sont dans
    // ce cas (mesuré le 2026-09-20) et aucun n'a de padding de contrôle à tenir.
    expect(paddingsDeLaSource('"rounded-md px-3 hover:bg-survol"')).toEqual([]);
    expect(paddingsDeLaSource('"rounded-md py-1 hover:bg-survol"')).toEqual([]);
  });

  it("ne compte pas la prose, et lit les chaînes d'un gabarit", () => {
    // Ce fichier-ci, le README et les commentaires du socle citent des pas
    // **pour dire lesquels tenir**. Les compter ferait grossir le résidu à
    // chaque explication ajoutée.
    expect(paddingsDeLaSource("// un p-2 de plus, à replier sur `Carte`")).toEqual([]);
    expect(paddingsDeLaSource('/* l\'ancien "rounded-md px-3 py-2" */')).toEqual([]);
    // Le `//` d'une URL n'ouvre aucun commentaire : sans cette précaution, tout
    // ce qui suit un lien dans le fichier disparaîtrait du balayage.
    expect(
      paddingsDeLaSource('const u = "https://x.test";\nconst c = "rounded-md p-2";'),
    ).toEqual(["p-2"]);
    // La forme du produit : une classe conditionnelle dans un gabarit.
    expect(paddingsDeLaSource('`x ${dense ? "rounded-md p-2" : "rounded-md p-4"}`')).toEqual([
      "p-2",
      "p-4",
    ]);
  });

  it("refuse le contournement par valeur arbitraire", () => {
    // La sortie de secours d'une ligne : le même écart, écrit en pixels. Un
    // filet qu'on contourne en une ligne n'en est pas un.
    expect(paddingsDeLaSource('"rounded-md p-[10px]"')).toEqual(["p-[10px]"]);
    expect(paddingsDeLaSource('"rounded-md px-[14px] py-[7px]"')).toEqual([
      "px-[14px] py-[7px]",
    ]);
    // …sans crier sur ce qui n'est pas un padding, ce qui distingue cette sonde
    // d'un balayage de crochets.
    expect(paddingsDeLaSource('"w-[38ch] grid-cols-[1fr_auto] gap-[3px]"')).toEqual([]);
  });

  it("tient le barème pour ce qu'il est : ce que le socle écrit", () => {
    // Le pendant des contrôles ci-dessus. Une sonde qui crierait sur le socle
    // lui-même ne dirait pas davantage qu'une sonde muette — elle verrait rouge
    // sur la sortie qu'elle recommande.
    expect(paddingsDeLaSource('"rounded-lg border p-3"').every(auBareme)).toBe(true);
    expect(paddingsDeLaSource('"gap-1.5 rounded-md px-3 py-1.5"').every(auBareme)).toBe(true);
    expect(paddingsDeLaSource('"inline-flex rounded-full px-2 py-0.5"').every(auBareme)).toBe(
      true,
    );
    expect(auBareme("px-3 py-2")).toBe(false);
    expect(auBareme("p-2")).toBe(false);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// 4. LE RÉSIDU, NOMMÉ ET COMPTÉ
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Ce qui restait de paddings hors barème au moment de #983, **fichier par
 * fichier avec son compte exact** — mesuré le 2026-09-20, jamais estimé.
 *
 * La raison est la même pour toutes les lignes et n'est donc pas répétée
 * quarante fois : ces écrans sont **antérieurs au barème**, qui n'a migré aucun
 * appelant — `Carte` a nommé trois densités sans que rien n'oblige à les
 * prendre, et `Bouton` deux tailles que 26 fichiers avaient déjà recopiées
 * (docs/30 §2.2). Ce ticket ne les migre pas non plus : il pose le compte et
 * refuse le suivant, comme #895 pour la couleur et #832 pour les contrôles de
 * saisie. La migration se fait écran par écran, dans des tickets à part.
 *
 * Le compte est **exact et non un plafond** : un padding de plus rougit, un
 * padding de **moins** rougit aussi tant que la ligne n'est pas mise à jour.
 * C'est ce qui fait qu'un résidu ne peut que décroître, et que chaque
 * décroissance est un geste **écrit** — sans quoi le chiffre du README serait
 * vrai le jour où on l'a mesuré et faux le lendemain. Le message d'échec rend la
 * ligne à recopier : la contrainte doit coûter une seconde, pas une enquête.
 */
const RESIDU = new Map<string, number>([
  ["app/chat/page.tsx", 2],
  ["app/couts/page.tsx", 1],
  ["components/BanniereErreurApi.tsx", 1],
  ["components/BarreLaterale.tsx", 1],
  ["components/BarreSuperieure.tsx", 4],
  ["components/BasculeDeVues.tsx", 1],
  ["components/BasculeTheme.tsx", 2],
  ["components/CentreNotifications.tsx", 4],
  ["components/ColonneConversation.tsx", 2],
  ["components/Conversation.tsx", 2],
  ["components/EditeurAgent.tsx", 2],
  ["components/EditeurPlaybook.tsx", 2],
  ["components/GraphiqueEvolutionCout.tsx", 1],
  ["components/GuidePriseEnMain.tsx", 1],
  ["components/Infobulle.tsx", 1],
  ["components/LigneActivite.tsx", 1],
  ["components/ListeAgents.tsx", 1],
  ["components/MenuAide.tsx", 3],
  ["components/OngletMcpAgent.tsx", 6],
  ["components/OngletsAgent.tsx", 1],
  ["components/PanneauBriefs.tsx", 1],
  ["components/PanneauDetailTache.tsx", 1],
  // Le `px-2 py-1.5` du diff et des arguments d'un acte, parti avec la carte
  // dans son fichier (#1228) : il était inscrit sous `PanneauValidations`.
  ["components/CarteValidation.tsx", 1],
  // Les deux pas que l'écran du poste vide assume : un `p-5` passé à une `Carte`
  // en `densite="aucune"`, et le pas de son encart. Le commentaire du fichier
  // dit pourquoi — « le pas de 1,25 rem de cet écran n'est aucun des trois » —,
  // et c'est exactement le genre d'écart que le barème est là pour rendre
  // visible : soit l'écran se replie, soit le quatrième pas se discute.
  ["components/PosteVide.tsx", 1],
  ["components/SelecteurReassignation.tsx", 1],
  ["components/Shell.tsx", 1],
  ["components/chat/BulleFil.tsx", 1],
  ["components/chat/FilDeCadrage.tsx", 1],
  ["components/composer/ComposerObjectif.tsx", 3],
  ["components/composer/RapportExtraction.tsx", 1],
  ["components/composer/RefusSource.tsx", 1],
  ["components/integrations/BibliothequeMcp.tsx", 5],
  ["components/integrations/PoolProjet.tsx", 2],
  ["components/parametres/NavigationParametres.tsx", 1],
  ["components/parametres/ParametresGeneral.tsx", 2],
  ["components/projets/ExplorateurDossiers.tsx", 2],
  ["components/projets/SelecteurProjet.tsx", 3],
  ["components/runs/FriseRun.tsx", 1],
  ["components/runs/VuePipeline.tsx", 1],
]);

/** Le compte du README — épinglé ici pour qu'il ne puisse pas dériver en silence. */
const TOTAL_ANNONCE = 69;

/** Les sources jugées : le produit, moins le fichier qui porte le barème. */
const sourcesJugees = (): string[] =>
  sourcesDuProduit(EXTENSIONS).filter((fichier) => fichier !== SOCLE);

/** Ce que le produit porte aujourd'hui hors barème, fichier par fichier. */
function residuMesure(): Map<string, string[]> {
  const parFichier = new Map<string, string[]>();
  for (const fichier of sourcesJugees()) {
    const hors = paddingsDeLaSource(lireSource(fichier)).filter((p) => !auBareme(p));
    if (hors.length > 0) parFichier.set(fichier, hors);
  }
  return parFichier;
}

/** La ligne à recopier dans `RESIDU`, pour que la mise à jour coûte une seconde. */
const ligne = (fichier: string, compte: number) => `  ["${fichier}", ${compte}],`;

/**
 * Le verdict rendu sur une mesure, face au tableau — les deux moitiés du
 * critère : ce qui **entre** (un padding de plus qu'inscrit, ou un fichier qui
 * n'y est pas du tout) et ce qui a **vieilli** (une ligne qui annonce plus que
 * ce qu'on mesure).
 *
 * Chaque défaut est nommé **une fois** : un dépassement est une nouveauté, pas
 * une ligne périmée, et le compter des deux côtés ferait chercher deux causes là
 * où il n'y en a qu'une. C'est la mécanique de `couleurs.test.ts`, reprise parce
 * que c'est elle qui fait décroître un résidu — pas le motif.
 */
function confronter(
  mesure: ReadonlyMap<string, readonly string[]>,
  tableau: ReadonlyMap<string, number>,
  sources: readonly string[],
): { nouvelles: string[]; perimees: string[] } {
  const nouvelles: string[] = [];
  for (const [fichier, paddings] of [...mesure].sort()) {
    const inscrit = tableau.get(fichier) ?? 0;
    if (paddings.length <= inscrit) continue;
    nouvelles.push(
      `  ${fichier} — ${paddings.length} padding(s) pour ${inscrit} au tableau\n` +
        `    au-delà du compte inscrit : ${paddings.slice(inscrit).join(", ")}\n` +
        (inscrit === 0
          ? ""
          : `    si le tableau doit suivre :\n  ${ligne(fichier, paddings.length)}\n`),
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
        `  ${fichier} — inscrit pour ${inscrit}, on n'en mesure plus aucun : retirer la ligne`,
      );
    } else {
      perimees.push(
        `  ${fichier} — inscrit pour ${inscrit}, mais le fichier n'existe plus : retirer la ligne`,
      );
    }
  }
  return { nouvelles, perimees };
}

describe("le verdict, prouvé sur une mesure fabriquée", () => {
  // Ce que le produit subit, ce n'est pas le motif seul mais le motif **plus**
  // la confrontation au tableau. Une comparaison qui cesserait de comparer
  // rendrait « rien à signaler » sur les contrôles suivants, et c'est précisément
  // le vert qu'on ne pourrait pas distinguer du bon.
  const sources = ["a.tsx", "b.tsx", "c.tsx"];
  const tableau = new Map([
    ["a.tsx", 2],
    ["b.tsx", 1],
  ]);

  it("se tait sur une mesure conforme", () => {
    const mesure = new Map([
      ["a.tsx", ["p-2", "px-3 py-2"]],
      ["b.tsx", ["p-1.5"]],
    ]);
    expect(confronter(mesure, tableau, sources)).toEqual({ nouvelles: [], perimees: [] });
  });

  it("refuse un fichier que le tableau ne nomme pas", () => {
    // Le cas nominal du prochain écran : il n'a droit à aucun padding hors
    // barème, et le message ne lui propose pas de ligne à recopier — c'est une
    // densité à prendre dans le socle, pas un compte à mettre à jour.
    const mesure = new Map([
      ["a.tsx", ["p-2", "px-3 py-2"]],
      ["b.tsx", ["p-1.5"]],
      ["c.tsx", ["px-4 py-2"]],
    ]);
    const { nouvelles, perimees } = confronter(mesure, tableau, sources);
    expect(nouvelles).toHaveLength(1);
    expect(nouvelles[0]).toContain("c.tsx — 1 padding(s) pour 0 au tableau");
    expect(nouvelles[0]).toContain("px-4 py-2");
    expect(nouvelles[0]).not.toContain("si le tableau doit suivre");
    // Un fichier absent du tableau ne peut pas rendre une ligne périmée : la
    // seconde moitié ne juge que ce qui y est inscrit.
    expect(perimees).toEqual([]);
  });

  it("refuse un dépassement, et ne le compte qu'une fois", () => {
    const mesure = new Map([
      ["a.tsx", ["p-2", "px-3 py-2", "p-1"]],
      ["b.tsx", ["p-1.5"]],
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
      ["a.tsx", ["p-2"]],
      ["b.tsx", ["p-1.5"]],
    ]);
    const { nouvelles, perimees } = confronter(mesure, tableau, sources);
    expect(nouvelles).toEqual([]);
    expect(perimees).toHaveLength(1);
    expect(perimees[0]).toContain('["a.tsx", 1],');
  });

  it("distingue un fichier replié sur le barème d'un fichier disparu", () => {
    // Les deux appellent le même geste — retirer la ligne — mais pas la même
    // relecture : l'un est une migration réussie, l'autre un fichier supprimé
    // dont la ligne serait relue comme si elle valait encore.
    const replie = new Map([["b.tsx", ["p-1.5"]]]);
    expect(confronter(replie, tableau, sources).perimees).toEqual([
      "  a.tsx — inscrit pour 2, on n'en mesure plus aucun : retirer la ligne",
    ]);
    expect(confronter(replie, tableau, ["b.tsx", "c.tsx"]).perimees).toEqual([
      "  a.tsx — inscrit pour 2, mais le fichier n'existe plus : retirer la ligne",
    ]);
  });
});

describe("le résidu de paddings hors barème", () => {
  it("ne laisse entrer aucun padding nouveau", () => {
    // Le critère du ticket : la sonde tolère ce qui est nommé, elle refuse le
    // suivant. Un fichier absent du tableau n'a droit à aucun padding hors
    // barème ; un fichier présent n'a droit qu'aux siens.
    const { nouvelles } = confronter(residuMesure(), RESIDU, sourcesJugees());
    expect(
      nouvelles,
      `\n${nouvelles.join("\n")}\n` +
        "Un pas se choisit UNE fois. Pour une boîte : `<Carte densite=\"compacte|normale|aeree\">`\n" +
        "(p-2.5 · p-3 · p-4). Pour un contrôle : `<Bouton taille=\"petite|normale\">`,\n" +
        "`CLASSE_CONTROLE` ou `<Badge>` (px-2.5 py-1 · px-3 py-1.5 · px-2 py-0.5).\n" +
        "Voir apps/web/README.md, « Le barème de padding ».\n" +
        "Si le pas manque vraiment au socle, c'est une densité à ajouter dans\n" +
        "`Primitives.tsx` — une décision d'écran, qui se prend là et se discute —,\n" +
        "plutôt qu'un pas de plus écrit dans un écran.\n",
    ).toHaveLength(0);
  });

  it("ne garde aucune ligne périmée : le compte est exact, pas un plafond", () => {
    // Le versant qui fait décroître le résidu. Une ligne qui annonce plus que ce
    // qu'on mesure est une migration faite sans que le tableau l'ait dit — et un
    // chiffre de README devenu faux.
    const { perimees } = confronter(residuMesure(), RESIDU, sourcesJugees());
    expect(
      perimees,
      `\n${perimees.join("\n")}\n\nUn padding replié sur le barème est une bonne nouvelle : ` +
        `mettre le tableau à jour, et le total de apps/web/README.md avec (${TOTAL_ANNONCE} aujourd'hui).\n`,
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
// 5. QUE LE BALAYAGE NE DEVIENNE PAS MUET
//
// Tout ce qui précède rend un ✓ si le motif cesse de matcher, si le parcours des
// sources rend une liste vide, ou si les chaînes ne se lisent plus. Ces planchers
// rendent le ✓ opposable — c'est la moitié que perd le plus facilement un
// balayage de sources.
// ─────────────────────────────────────────────────────────────────────────────

describe("la couverture du balayage", () => {
  it("parcourt bien le produit, et pas une liste vide", () => {
    const sources = sourcesJugees();
    expect(sources.length).toBeGreaterThanOrEqual(100);
    expect(sources).not.toContain(SOCLE);
    expect(sources).toContain("components/Kanban.tsx");
    expect(sources.filter((f) => f.startsWith("app/")).length).toBeGreaterThanOrEqual(10);
  });

  it("mesure encore des paddings AU barème, et pas seulement le résidu", () => {
    // Le plancher le plus utile : si le motif cessait de reconnaître les pas du
    // socle, tout deviendrait « hors barème » — et si la condition de portée
    // devenait fausse, plus rien ne serait jugé. Les deux se voient ici.
    const tous = sourcesJugees().flatMap((f) => paddingsDeLaSource(lireSource(f)));
    expect(tous.filter(auBareme).length).toBeGreaterThanOrEqual(25);
    expect(tous.length).toBeGreaterThanOrEqual(90);
  });

  it("mesure encore un résidu, et le tableau le couvre entièrement", () => {
    // Le plancher inverse, et le plus fort : tant que le produit porte des
    // paddings hors barème, la sonde doit en voir — et le tableau doit être
    // exactement l'ensemble des fichiers qui en portent, ni plus, ni moins. Le
    // jour où le résidu tombe à zéro, ce test est ce qui invite à retirer le
    // tableau plutôt qu'à le laisser mentir.
    const mesure = residuMesure();
    expect([...mesure.keys()].sort()).toEqual([...RESIDU.keys()].sort());
    const total = [...mesure.values()].reduce((somme, p) => somme + p.length, 0);
    expect(total).toBe(TOTAL_ANNONCE);
  });
});

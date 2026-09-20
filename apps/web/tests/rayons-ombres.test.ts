/**
 * Le rayon et l'ombre écrits hors barème (#982, lot 2 de #973).
 *
 * Le pendant de `couleurs.test.ts` pour les deux propriétés qui disent la
 * **profondeur** : le rayon et l'ombre. Avant ce lot, ni l'un ni l'autre n'avait
 * de token — 5 rayons et 5 ombres distincts dans `app/` + `components/`, et rien
 * pour refuser le sixième. Une session qui construit un écran imite le code
 * voisin : elle recopie la dispersion, et c'est ce qui rend le produit plat
 * (docs/30 §2.3).
 *
 * ── Ce que la sonde juge, et pourquoi ce n'est pas la valeur ─────────────────
 *
 * Elle juge le **nom**. `rounded-lg` et `rounded-carte` rendent aujourd'hui le
 * même pixel — `globals.css` alias l'un sur l'autre —, et pourtant l'un est hors
 * barème et l'autre dedans. Ce n'est pas un formalisme : le barème est un
 * vocabulaire de **rôles** (un contrôle, une surface posée, une surface
 * flottante, une pastille), et ce qui manquait au produit n'était pas une valeur
 * mais la possibilité de dire lequel des quatre on voulait. Un `rounded-md` ne
 * dit pas si l'auteur visait un contrôle ou s'il a recopié la ligne d'à côté.
 *
 * Corollaire mesurable, et c'est lui qui rend la migration tenable : parce que
 * les jumelles sont **aliasées** sur leur pas, renommer `rounded-lg` en
 * `rounded-carte` ne peut pas changer un pixel. Le résidu se vide donc par des
 * renommages sûrs — sauf les quatre ombres sans jumelle (`shadow-sm`,
 * `shadow-md`, `shadow-2xl`, `shadow` nu), dont le passage à `shadow-flottant`
 * est une **migration** et non un renommage : elle change le rendu, donc elle a
 * son ticket et sa relecture visuelle.
 *
 * ⚠ Elle reconnaît **quatre** écritures, et il faut les quatre : la classe nue
 * (`rounded-lg`), la classe **dirigée** (`rounded-t-md` — deux onglets du
 * produit), la valeur **arbitraire** (`rounded-[10px]`, `shadow-[0_2px_4px]`) et
 * l'écriture **en ligne** (`style={{ boxShadow: … }}`). Une sonde qui ne verrait
 * que la première s'éteindrait au premier `style={{ borderRadius: 10 }}` — un
 * contournement d'une ligne, et un filet qu'on contourne en une ligne n'en est
 * pas un. C'est la leçon de `dark:bg-[#3987e5]` dans `couleurs.test.ts`.
 *
 * ── Ce sur quoi ce fichier se tait, et pour quelle raison ────────────────────
 *
 * - `ring-*`. Tailwind les rend en `box-shadow`, mais un anneau est un **filet
 *   de focus**, pas l'élévation d'une surface ; il est déjà gardé où il compte,
 *   par `a11y.test.tsx` (WCAG 2.2 §2.4.7).
 * - `inset-shadow-*`. Un `inset` est un bord peint à l'intérieur — c'est ce
 *   qu'emploie GitHub pour ses 24 séparateurs de ligne (mesuré le 2026-09-20) —,
 *   pas quelque chose qui flotte.
 * - `drop-shadow-*` et `text-shadow-*`, qui portent sur un glyphe ou un SVG, pas
 *   sur une surface.
 *
 * Aucun des trois n'est employé dans le produit aujourd'hui. Les ouvrir ici
 * ferait entrer trois familles dans le résidu sans qu'aucune ait été jugée —
 * c'est le partage qu'a posé #895.
 */

import { describe, expect, it } from "vitest";

import { lireSource, sansCommentaires, sourcesDuProduit } from "./sources";

// ─────────────────────────────────────────────────────────────────────────────
// 1. LE BARÈME, LU DANS `globals.css` ET JAMAIS RECOPIÉ
//
// Le lire plutôt que l'inscrire ici est ce qui fait qu'un pas ajouté rougit au
// lieu d'être relu comme s'il valait déjà — la règle de `tokensDeLaPalette()`
// dans `couleurs.test.ts`.
// ─────────────────────────────────────────────────────────────────────────────

/** Les sources balayées. Une feuille de classes peut vivre dans un `.ts`. */
const EXTENSIONS = [".tsx", ".ts"] as const;

/**
 * Ce qu'un `@theme` déclare pour un préfixe donné, **partagé en deux** :
 *
 * - un **pas** porte une valeur littérale — c'est un rôle du barème ;
 * - une **jumelle** porte `var(--<prefixe>-<autre>)` — c'est une classe Tailwind
 *   aliasée sur un pas, donc un nom hors barème qui ne peut pas diverger.
 *
 * La distinction se lit dans la valeur, elle ne se déclare nulle part : c'est
 * ce qui empêche d'ajouter un pas en le faisant passer pour un alias, ou
 * l'inverse.
 */
function declarationsDe(prefixe: "radius" | "shadow"): {
  pas: Map<string, string>;
  jumelles: Map<string, string>;
} {
  const feuille = lireSource("app/globals.css");
  // Les blocs `@theme` **non `inline`** : un bloc `inline` n'émet rien à
  // l'exécution, et un alias exige que sa source existe (voir globals.css).
  const blocs = [...feuille.matchAll(/@theme\s*\{([\s\S]*?)\n\}/g)].map(
    ([, corps]) => corps,
  );
  const pas = new Map<string, string>();
  const jumelles = new Map<string, string>();
  for (const bloc of blocs) {
    for (const [, nom, valeur] of bloc.matchAll(
      new RegExp(`--${prefixe}-([\\w-]+)\\s*:\\s*([^;]+);`, "g"),
    )) {
      const alias = new RegExp(`^var\\(\\s*--${prefixe}-([\\w-]+)\\s*\\)$`).exec(
        valeur.trim(),
      );
      if (alias) jumelles.set(nom, alias[1]);
      else pas.set(nom, valeur.trim().replace(/\s+/g, " "));
    }
  }
  return { pas, jumelles };
}

/** Les quatre rôles du barème, et le pas d'ombre unique. */
const RAYONS = declarationsDe("radius");
const OMBRES = declarationsDe("shadow");

// ─────────────────────────────────────────────────────────────────────────────
// 2. LA SONDE
// ─────────────────────────────────────────────────────────────────────────────

/** Les coins qu'une classe dirigée peut viser (`rounded-t-md`, `rounded-se-lg`). */
const COINS = "t|b|l|r|s|e|tl|tr|bl|br|ss|se|es|ee";

/** `dark:hover:shadow-lg` → `shadow-lg` : les variantes tombent, l'utilité est jugée. */
const utilite = (jeton: string) => jeton.slice(jeton.lastIndexOf(":") + 1);

/**
 * Le **pas** qu'un jeton désigne, ou `null` si ce jeton ne parle ni de rayon ni
 * d'ombre de surface.
 *
 * `rounded` et `shadow` **nus** rendent `""` plutôt que `null` : ce sont des
 * écritures réelles, et les plus trompeuses des deux familles — `shadow` nu est
 * donné pour *déprécié* par Tailwind v4 lui-même et rend exactement `shadow-sm`,
 * si bien que le produit écrit la même ombre sous deux noms. Les laisser passer
 * pour « pas un pas » serait exempter précisément ce qu'on vient compter.
 */
function pasDe(jeton: string): { famille: "rayon" | "ombre"; pas: string } | null {
  const u = utilite(jeton);
  const rayon = new RegExp(`^rounded(?:-(?:${COINS}))?(?:-(.+))?$`).exec(u);
  if (rayon) return { famille: "rayon", pas: rayon[1] ?? "" };
  const ombre = /^shadow(?:-(.+))?$/.exec(u);
  if (ombre) return { famille: "ombre", pas: ombre[1] ?? "" };
  return null;
}

/** Ce jeton est-il hors barème ? */
function horsBareme(jeton: string): boolean {
  const trouve = pasDe(jeton);
  if (!trouve) return false;
  const { pas } = trouve.famille === "rayon" ? RAYONS : OMBRES;
  return !pas.has(trouve.pas);
}

/**
 * Les écritures **en ligne** d'un rayon ou d'une ombre, sous leurs deux formes :
 * la propriété d'un objet `style` (`boxShadow:`) et la déclaration CSS dans une
 * chaîne (`box-shadow:`). Elles ne vivent pas dans une feuille de classes, donc
 * le balayage des chaînes ci-dessous ne les verrait pas — et ce sont elles, la
 * sortie de secours d'une ligne.
 */
const EN_LIGNE = /\b(borderRadius|boxShadow)\b|\b(border-radius|box-shadow)\s*:/g;

/**
 * Ce qu'une source porte hors barème, dans l'ordre où c'est écrit.
 *
 * Le balayage des classes porte sur les **chaînes littérales**, comme celui de
 * `couleurs.test.ts` et pour la même raison : c'est là que vivent les feuilles
 * de classes, et lire le texte entier reviendrait à compter les noms de
 * variables et les chemins d'import. Les écritures en ligne, elles, se lisent
 * dans le texte — elles n'ont pas de guillemets autour de leur nom — et sont
 * rendues sous la forme `style:<propriété>`, qui se distingue à l'œil d'une
 * classe.
 */
function ecartsDe(source: string): string[] {
  const nu = sansCommentaires(source);
  const ecarts: string[] = [];
  for (const [, guillemets, apostrophes, gabarit] of nu.matchAll(
    /"([^"\n]*)"|'([^'\n]*)'|`([^`]*)`/g,
  )) {
    for (const brut of (guillemets ?? apostrophes ?? gabarit ?? "").split(
      /[\s${}]+/,
    )) {
      const jeton = brut.replace(/^["'`]+|["'`]+$/g, "");
      if (jeton && horsBareme(jeton)) ecarts.push(jeton);
    }
  }
  for (const [, propriete, declaration] of nu.matchAll(EN_LIGNE)) {
    ecarts.push(`style:${propriete ?? declaration}`);
  }
  return ecarts;
}

// ─────────────────────────────────────────────────────────────────────────────
// 3. LE BARÈME, PROUVÉ AVANT DE SERVIR
//
// Tout ce qui suit rend un ✓ si `declarationsDe` cesse de lire : un bloc vide
// rendrait « aucun pas », donc « tout est hors barème » — vrai par accident, et
// avec un résidu qui exploserait au lieu de se taire. Ces contrôles-là passent
// avant.
// ─────────────────────────────────────────────────────────────────────────────

describe("le barème, lu dans globals.css", () => {
  it("lit bien un barème, et pas un bloc vide", () => {
    expect([...RAYONS.pas.keys()].sort()).toEqual([
      "carte",
      "controle",
      "flottant",
      "pastille",
    ]);
    expect([...OMBRES.pas.keys()]).toEqual(["flottant"]);
  });

  it("reste court : quatre rayons, une ombre — un pas de plus se discute", () => {
    // Le contrôle qui tient le critère « peu de pas ». Ce n'est pas une borne
    // arbitraire : le produit est parti de 5 rayons et 5 ombres *sans rôle*, et
    // ce qui rend un écran cohérent est le petit nombre de valeurs, toujours les
    // mêmes. Un cinquième rayon peut très bien être le bon — mais il passe par
    // cette ligne, donc par une décision écrite, et pas par un composant.
    expect(
      RAYONS.pas.size,
      "un rayon a été ajouté ou retiré : c'est une décision de barème " +
        "(docs/30 §2.3bis), pas un réglage — la porter ici et dans la doc",
    ).toBe(4);
    expect(
      OMBRES.pas.size,
      "un second pas d'ombre a été ajouté : le barème n'en admet qu'un, et " +
        "c'est le relevé de GitHub et de Grafana qui l'a tranché (une seule " +
        "ombre d'élévation par page). Le rouvrir demande une veille, pas une ligne",
    ).toBe(1);
  });

  it("n'accepte comme jumelle que ce qui pointe sur un pas réel", () => {
    // Une jumelle qui désignerait un pas inexistant rendrait `var(--radius-x)`
    // vide : la classe cesserait d'arrondir, en silence. Et une jumelle qui
    // pointerait sur une autre jumelle ferait une chaîne que personne ne relit.
    for (const [nom, cible] of RAYONS.jumelles) {
      expect(RAYONS.pas.has(cible), `--radius-${nom} pointe sur --radius-${cible}, qui n'est pas un pas`).toBe(true);
    }
    for (const [nom, cible] of OMBRES.jumelles) {
      expect(OMBRES.pas.has(cible), `--shadow-${nom} pointe sur --shadow-${cible}, qui n'est pas un pas`).toBe(true);
    }
    // Et les jumelles attendues sont bien là : sans elles, renommer une classe
    // du résidu cesserait d'être sûr, ce qui est toute la promesse du §« Ce que
    // la sonde juge » en tête de fichier.
    expect([...RAYONS.jumelles.keys()].sort()).toEqual(["lg", "md", "xl"]);
    expect([...OMBRES.jumelles.keys()]).toEqual(["lg"]);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// 4. LA SONDE, PROUVÉE SUR UN ÉCHANTILLON FAUTIF
//
// Un balayage qui ne rougit jamais est indiscernable d'un balayage devenu muet.
// On lui pose donc les questions dont la réponse est connue **avant** qu'il ne
// juge le produit : les formes que le ticket a relevées, les contournements
// plausibles, et ce sur quoi il ne doit surtout pas crier.
// ─────────────────────────────────────────────────────────────────────────────

describe("la sonde, prouvée avant de servir", () => {
  it("reconnaît les cinq rayons et les cinq ombres relevés le 2026-09-20", () => {
    // Le constat de départ du ticket, recopié tel quel. Si le motif cesse de
    // les voir, ce sont ces lignes-là qui rougissent — pas un cas de bord.
    expect(
      ecartsDe('"rounded-md" "rounded-full" "rounded-lg" "rounded-t-md" "rounded-xl"'),
    ).toEqual([
      "rounded-md",
      "rounded-full",
      "rounded-lg",
      "rounded-t-md",
      "rounded-xl",
    ]);
    expect(
      ecartsDe('"shadow-sm" "shadow-lg" "shadow" "shadow-2xl" "shadow-md"'),
    ).toEqual(["shadow-sm", "shadow-lg", "shadow", "shadow-2xl", "shadow-md"]);
  });

  it("voit l'écart sous ses variantes, dans les deux ordres", () => {
    // `dark:hover:` et `hover:dark:` s'écrivent tous deux dans le produit ; un
    // motif ancré sur le début du jeton n'en verrait qu'un.
    expect(
      ecartsDe('"dark:hover:shadow-lg hover:dark:rounded-xl md:rounded-lg"'),
    ).toEqual(["dark:hover:shadow-lg", "hover:dark:rounded-xl", "md:rounded-lg"]);
  });

  it("refuse le rayon nu, l'ombre nue et tous les coins dirigés", () => {
    // `rounded` nu rend le pas *déprécié* de Tailwind (0,25 rem) et `shadow` nu
    // rend exactement `shadow-sm` : deux noms pour une valeur déjà écrite
    // ailleurs, c'est-à-dire la dispersion sous sa forme la plus discrète.
    expect(ecartsDe('"rounded shadow"')).toEqual(["rounded", "shadow"]);
    expect(ecartsDe('"rounded-t-lg rounded-br-xl rounded-se-md"')).toEqual([
      "rounded-t-lg",
      "rounded-br-xl",
      "rounded-se-md",
    ]);
  });

  it("refuse le contournement par valeur arbitraire", () => {
    // La sortie de secours d'une ligne : le même arrondi, écrit en pixels.
    expect(ecartsDe('"rounded-[10px] shadow-[0_2px_4px_rgba(0,0,0,.2)]"')).toEqual([
      "rounded-[10px]",
      "shadow-[0_2px_4px_rgba(0,0,0,.2)]",
    ]);
  });

  it("refuse le contournement par écriture en ligne", () => {
    // L'autre sortie de secours, et celle qu'un balayage de classes ne voit
    // jamais : la propriété CSS écrite dans un objet `style`.
    expect(ecartsDe("style={{ borderRadius: 10, boxShadow: '0 1px 2px #000' }}")).toEqual([
      "style:borderRadius",
      "style:boxShadow",
    ]);
    // …et la même chose dans une feuille de style écrite en chaîne.
    expect(ecartsDe('const css = "a { border-radius: 4px; box-shadow: none; }";')).toEqual([
      "style:border-radius",
      "style:box-shadow",
    ]);
  });

  it("ne crie pas sur le barème, ni sur ce qui n'est pas une surface", () => {
    // Le pendant du contrôle ci-dessus : une sonde qui crierait sur tout ne
    // dirait pas davantage qu'une sonde muette — et celle-ci verrait rouge sur
    // la sortie qu'elle recommande.
    expect(
      ecartsDe(
        '"rounded-controle rounded-carte rounded-flottant rounded-pastille shadow-flottant"',
      ),
    ).toEqual([]);
    // Un pas du barème sous ses variantes et sur un coin dirigé : c'est
    // exactement ce vers quoi le résidu doit pouvoir se replier.
    expect(
      ecartsDe('"dark:shadow-flottant hover:rounded-controle rounded-t-carte"'),
    ).toEqual([]);
    // Les trois familles laissées dehors, avec leur raison en tête de fichier.
    expect(
      ecartsDe('"ring-2 ring-offset-2 inset-shadow-xs drop-shadow-lg text-shadow-sm"'),
    ).toEqual([]);
    // Et ce qui commence par les mêmes lettres sans être une de ces utilités.
    expect(ecartsDe('"roundedness shadowbox border-bord outline-accent"')).toEqual([]);
  });

  it("ne compte pas la prose, et lit les chaînes d'un gabarit", () => {
    // Ce fichier-ci, le README et une dizaine de commentaires du produit citent
    // ces classes **pour dire qu'il ne faut plus les écrire**. Les compter
    // ferait grossir le résidu à chaque explication ajoutée.
    expect(ecartsDe("// remplacer rounded-lg par rounded-carte")).toEqual([]);
    expect(ecartsDe('/* l\'ancien "shadow-2xl", et son boxShadow en ligne */')).toEqual([]);
    // Le `//` d'une URL n'ouvre aucun commentaire : sans cette précaution, tout
    // ce qui suit un lien dans le fichier disparaîtrait du balayage.
    expect(ecartsDe('const u = "https://x.test";\nconst c = "rounded-lg";')).toEqual([
      "rounded-lg",
    ]);
    // La forme du produit : une classe conditionnelle dans un gabarit.
    expect(ecartsDe('`x ${actif ? "shadow-lg" : "shadow-sm"}`')).toEqual([
      "shadow-lg",
      "shadow-sm",
    ]);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// 5. CE QUE LE BARÈME NE SAIT PAS RENDRE
//
// Toutes les ombres écrites à la main ne sont pas des fautes, et un balayage qui
// les traiterait toutes pareil enverrait forcer des équivalences approximatives
// — c'est-à-dire changer le rendu pour faire taire un test.
//
// ⚠ Ces manques ne sortent PAS du compte du résidu, et c'est délibéré : une
// exemption par motif serait la porte par laquelle n'importe quel écart
// entrerait en invoquant « le barème ne couvre pas mon cas ». Ils disent
// seulement **jusqu'où** le résidu peut descendre sans que le barème bouge
// d'abord — et chacun est un ticket à ouvrir, pas une tolérance. C'est la règle
// de `MANQUES_DU_SOCLE` (#895).
// ─────────────────────────────────────────────────────────────────────────────

type Manque = {
  /** Le pas qui manque, sous le nom qu'il porterait. */
  readonly pas: string;
  readonly famille: "radius" | "shadow";
  /** Un fichier qui en souffre, et l'écart qu'on y lit faute de mieux. */
  readonly temoin: string;
  readonly ecart: string;
  readonly raison: string;
};

const MANQUES_DU_BAREME: readonly Manque[] = [
  {
    pas: "voile",
    famille: "shadow",
    temoin: "components/GuidePriseEnMain.tsx",
    ecart: "style:boxShadow",
    raison:
      "un `0 0 0 9999px` qui assombrit toute la page **sauf** un rectangle — " +
      "le voile du guide de prise en main. Ce n'est pas une élévation : rien " +
      "ne flotte, c'est le reste qui s'efface, et aucun pas d'ombre ne peut " +
      "l'exprimer (le découper en quatre bandes à recoller serait pire). " +
      "Un pas `--shadow-voile` se discuterait, mais il n'a qu'un appelant",
  },
];

describe("ce que le barème ne sait pas rendre", () => {
  it("ne nomme aucun manque que le barème couvre déjà", () => {
    // LE contrôle qui empêche cette liste de devenir l'endroit où l'on range ce
    // qui échoue : le jour où le pas est déclaré, la ligne rougit et son témoin
    // se replie. ⚠ Il ne tient que sur le **nom** — un pas ajouté sous un autre
    // nom (`--shadow-scrim`) ne le déclencherait pas.
    for (const { pas, famille, temoin } of MANQUES_DU_BAREME) {
      const declare = (famille === "radius" ? RAYONS : OMBRES).pas.has(pas);
      expect(
        declare,
        `--${famille}-${pas} existe désormais : ${temoin} n'a plus de raison ` +
          "de l'écrire à la main, et cette ligne n'a plus de raison d'être",
      ).toBe(false);
    }
  });

  it("garde chaque manque rattaché à un témoin qui existe et qui le porte encore", () => {
    // Une exemption qui ne désigne plus rien est un motif qu'on relira comme
    // s'il valait encore (règle de `HORS_PAIRES`, #534).
    const sources = sourcesDuProduit(EXTENSIONS);
    for (const { pas, temoin, ecart, raison } of MANQUES_DU_BAREME) {
      expect(sources, `${pas} : ${temoin} n'existe plus`).toContain(temoin);
      expect(
        ecartsDe(lireSource(temoin)),
        `${pas} : ${temoin} ne porte plus « ${ecart} » — le manque est-il ` +
          `comblé, ou la ligne périmée ?\n  ce qu'elle disait : ${raison}`,
      ).toContain(ecart);
    }
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// 6. LE RÉSIDU, NOMMÉ ET COMPTÉ
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Ce qui restait de rayons et d'ombres écrits hors barème au moment de #982,
 * **fichier par fichier avec son compte exact** — mesuré le 2026-09-20, jamais
 * estimé.
 *
 * La raison est la même pour toutes les lignes et n'est donc pas répétée
 * soixante fois : ces écrans sont **antérieurs au barème**, qui ne migre aucun
 * appelant. Ce ticket ne les migre pas non plus — il pose le compte et refuse le
 * suivant. Les seuls fichiers qui en sont déjà sortis sont ceux du socle
 * (`components/Primitives.tsx`), dont les rayons ont pris leur nom de rôle à
 * valeur constante : c'est ce qui prouve que le barème a des appelants.
 *
 * Le compte est **exact et non un plafond** : un écart de plus rougit, un écart
 * de **moins** rougit aussi tant que la ligne n'est pas mise à jour. C'est ce
 * qui fait qu'un résidu ne peut que décroître, et que chaque décroissance est un
 * geste **écrit** — sans quoi le chiffre du README serait vrai le jour où on l'a
 * mesuré et faux le lendemain. Le message d'échec rend la ligne à recopier : la
 * contrainte doit coûter une seconde, pas une enquête.
 */
const RESIDU = new Map<string, number>([
  ["app/chat/page.tsx", 2],
  ["app/couts/page.tsx", 1],
  ["app/journal/page.tsx", 1],
  ["components/AssistantFlottant.tsx", 4],
  ["components/BanniereErreurApi.tsx", 1],
  ["components/BarreLaterale.tsx", 1],
  ["components/BarreSuperieure.tsx", 5],
  ["components/BasculeDeVues.tsx", 1],
  ["components/BasculeTheme.tsx", 3],
  ["components/CentreNotifications.tsx", 4],
  ["components/ChampJetons.tsx", 2],
  ["components/ColonneConversation.tsx", 2],
  ["components/Conversation.tsx", 6],
  ["components/EditeurAgent.tsx", 9],
  ["components/EditeurPlaybook.tsx", 12],
  ["components/EtapesTache.tsx", 3],
  ["components/GraphiqueEvolutionCout.tsx", 2],
  ["components/GuidePriseEnMain.tsx", 8], // manque : `voile`
  ["components/Infobulle.tsx", 2],
  ["components/Kanban.tsx", 2],
  ["components/LienTicketExterne.tsx", 1],
  ["components/LigneActivite.tsx", 2],
  ["components/MenuAide.tsx", 3],
  ["components/OngletMcpAgent.tsx", 9],
  ["components/OngletsAgent.tsx", 1],
  ["components/PanneauBriefs.tsx", 1],
  ["components/PanneauDetailTache.tsx", 3],
  ["components/PanneauValidations.tsx", 2],
  ["components/PosteVide.tsx", 3],
  // Le socle lui-même, et il compte double — un écart retiré ici en retire des
  // recopies partout. Ses huit rayons sont partis avec ce lot (ils ont pris
  // leur nom de rôle, à valeur constante) ; ce qui reste est l'ombre de la
  // carte pleine, celle de la carte d'arbitrage et celle du champ de saisie.
  // Le barème dit qu'aucune n'a de pas : une surface posée se sépare par son
  // bord. Les retirer **change le rendu**, donc c'est une migration, avec sa
  // relecture visuelle et son thème sombre — pas ce lot-ci.
  ["components/Primitives.tsx", 3],
  ["components/RepartitionAgents.tsx", 2],
  ["components/SelecteurReassignation.tsx", 1],
  ["components/Shell.tsx", 2],
  ["components/brief/QuestionsBrief.tsx", 1],
  ["components/brief/SectionsBrief.tsx", 1],
  ["components/brief/ValidationBriefs.tsx", 1],
  ["components/chat/BlocDeCode.tsx", 1],
  ["components/chat/BulleFil.tsx", 2],
  ["components/chat/CadrageDansLeFil.tsx", 1],
  ["components/chat/FilDeCadrage.tsx", 2],
  ["components/chat/SourcesDuFil.tsx", 1],
  ["components/chat/SourcesDuMessage.tsx", 3],
  ["components/chat/TexteMarkdown.tsx", 1],
  ["components/composer/ComposerObjectif.tsx", 3],
  ["components/composer/RapportExtraction.tsx", 2],
  ["components/composer/RefusSource.tsx", 1],
  ["components/integrations/BibliothequeMcp.tsx", 9],
  ["components/integrations/PoolProjet.tsx", 3],
  ["components/parametres/NavigationParametres.tsx", 1],
  ["components/parametres/ParametresAgents.tsx", 2],
  ["components/parametres/ParametresApparence.tsx", 1],
  ["components/parametres/ParametresGeneral.tsx", 3],
  ["components/parametres/SectionParametres.tsx", 3],
  ["components/projets/ChoixProjet.tsx", 2],
  ["components/projets/ExplorateurDossiers.tsx", 5],
  ["components/projets/SelecteurProjet.tsx", 3],
  ["components/runs/EtatRun.tsx", 2],
  ["components/runs/FriseRun.tsx", 1],
  ["components/runs/VuePipeline.tsx", 6],
]);

/** Le compte du README — épinglé ici pour qu'il ne puisse pas dériver en silence. */
const TOTAL_ANNONCE = 165;

/** Ce que le produit porte aujourd'hui, fichier par fichier. */
function residuMesure(): Map<string, string[]> {
  const parFichier = new Map<string, string[]>();
  for (const fichier of sourcesDuProduit(EXTENSIONS)) {
    const ecarts = ecartsDe(lireSource(fichier));
    if (ecarts.length > 0) parFichier.set(fichier, ecarts);
  }
  return parFichier;
}

/** La ligne à recopier dans `RESIDU`, pour que la mise à jour coûte une seconde. */
const ligne = (fichier: string, compte: number) => `  ["${fichier}", ${compte}],`;

/**
 * Le verdict rendu sur une mesure, face au tableau — les deux moitiés du
 * critère : ce qui **entre** (un écart de plus qu'inscrit, ou un fichier qui n'y
 * est pas du tout) et ce qui a **vieilli** (une ligne qui annonce plus que ce
 * qu'on mesure).
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
  for (const [fichier, ecarts] of [...mesure].sort()) {
    const inscrit = tableau.get(fichier) ?? 0;
    if (ecarts.length <= inscrit) continue;
    nouvelles.push(
      `  ${fichier} — ${ecarts.length} écart(s) pour ${inscrit} au tableau\n` +
        `    au-delà du compte inscrit : ${ecarts.slice(inscrit).join(", ")}\n` +
        (inscrit === 0
          ? ""
          : `    si le tableau doit suivre :\n  ${ligne(fichier, ecarts.length)}\n`),
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
  // rendrait « rien à signaler » sur les contrôles suivants, et c'est
  // précisément le vert qu'on ne pourrait pas distinguer du bon.
  const sources = ["a.tsx", "b.tsx", "c.tsx"];
  const tableau = new Map([
    ["a.tsx", 2],
    ["b.tsx", 1],
  ]);

  it("se tait sur une mesure conforme", () => {
    const mesure = new Map([
      ["a.tsx", ["rounded-md", "shadow-sm"]],
      ["b.tsx", ["rounded-full"]],
    ]);
    expect(confronter(mesure, tableau, sources)).toEqual({ nouvelles: [], perimees: [] });
  });

  it("refuse un fichier que le tableau ne nomme pas", () => {
    // Le cas nominal du prochain écran : il n'a droit à aucun écart, et le
    // message ne lui propose pas de ligne à recopier — c'est un pas à choisir
    // dans le barème, pas un compte à mettre à jour.
    const mesure = new Map([
      ["a.tsx", ["rounded-md", "shadow-sm"]],
      ["b.tsx", ["rounded-full"]],
      ["c.tsx", ["rounded-xl"]],
    ]);
    const { nouvelles, perimees } = confronter(mesure, tableau, sources);
    expect(nouvelles).toHaveLength(1);
    expect(nouvelles[0]).toContain("c.tsx — 1 écart(s) pour 0 au tableau");
    expect(nouvelles[0]).toContain("rounded-xl");
    expect(nouvelles[0]).not.toContain("si le tableau doit suivre");
    // Un fichier absent du tableau ne peut pas rendre une ligne périmée : la
    // seconde moitié ne juge que ce qui y est inscrit.
    expect(perimees).toEqual([]);
  });

  it("refuse un dépassement, et ne le compte qu'une fois", () => {
    const mesure = new Map([
      ["a.tsx", ["rounded-md", "shadow-sm", "style:boxShadow"]],
      ["b.tsx", ["rounded-full"]],
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
      ["a.tsx", ["rounded-md"]],
      ["b.tsx", ["rounded-full"]],
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
    const migre = new Map([["b.tsx", ["rounded-full"]]]);
    expect(confronter(migre, tableau, sources).perimees).toEqual([
      "  a.tsx — inscrit pour 2, on n'en mesure plus aucun : retirer la ligne",
    ]);
    expect(confronter(migre, tableau, ["b.tsx", "c.tsx"]).perimees).toEqual([
      "  a.tsx — inscrit pour 2, mais le fichier n'existe plus : retirer la ligne",
    ]);
  });
});

describe("le résidu de rayons et d'ombres hors barème", () => {
  it("ne laisse entrer aucun écart nouveau", () => {
    // Le critère du ticket : la sonde tolère ce qui est nommé, elle refuse le
    // suivant. Un fichier absent du tableau n'a droit à aucun écart ; un
    // fichier présent n'a droit qu'aux siens.
    const { nouvelles } = confronter(residuMesure(), RESIDU, sourcesDuProduit(EXTENSIONS));
    expect(
      nouvelles,
      `\n${nouvelles.join("\n")}\n` +
        "Un rayon et une ombre se choisissent par leur RÔLE :\n" +
        "  `rounded-controle` un bouton, un champ, un onglet\n" +
        "  `rounded-carte`    une surface posée sur la page\n" +
        "  `rounded-flottant` une surface qui survole la page\n" +
        "  `rounded-pastille` ce qui est circulaire ou en pilule\n" +
        "  `shadow-flottant`  LE pas d'ombre — une surface posée se sépare par\n" +
        "                     son bord (`border-bord`), jamais par une ombre\n" +
        "(voir apps/web/README.md, « Le barème des rayons et des ombres »).\n" +
        "Les jumelles sont aliasées : renommer `rounded-md`/`lg`/`xl` ou\n" +
        "`shadow-lg` en son pas ne change aucun pixel. Si aucun pas ne convient,\n" +
        "c'est un manque du barème : l'inscrire dans MANQUES_DU_BAREME et ouvrir\n" +
        "le ticket, plutôt que de forcer une équivalence qui changerait le rendu.\n",
    ).toHaveLength(0);
  });

  it("ne garde aucune ligne périmée : le compte est exact, pas un plafond", () => {
    // Le versant qui fait décroître le résidu. Une ligne qui annonce plus que
    // ce qu'on mesure est une migration faite sans que le tableau l'ait dit —
    // et un chiffre de README devenu faux.
    const { perimees } = confronter(residuMesure(), RESIDU, sourcesDuProduit(EXTENSIONS));
    expect(
      perimees,
      `\n${perimees.join("\n")}\n\nUn écart retiré est une bonne nouvelle : mettre le tableau à jour, ` +
        `et le total de apps/web/README.md avec (${TOTAL_ANNONCE} aujourd'hui).\n`,
    ).toHaveLength(0);
  });

  it("porte le total qu'annonce le README", () => {
    // Le chiffre du README n'est pas une note de bas de page : c'est ce sur
    // quoi le prochain lot mesurera son gain. Le laisser vivre hors d'un test,
    // c'est le laisser devenir faux.
    const total = [...RESIDU.values()].reduce((somme, n) => somme + n, 0);
    expect(total, "le total du tableau a bougé — mettre à jour apps/web/README.md").toBe(
      TOTAL_ANNONCE,
    );
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// 7. QUE LE BALAYAGE NE DEVIENNE PAS MUET
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

  it("voit le barème employé, et pas seulement déclaré", () => {
    // Le plancher que `couleurs.test.ts` n'a pas eu à poser et qui manquerait
    // ici : un barème que personne n'écrit rendrait « zéro écart de moins »
    // avec les mots de « tout est au barème ». Le socle en est le premier
    // appelant, et c'est par lui que la migration se juge.
    const socle = lireSource("components/Primitives.tsx");
    for (const pas of ["rounded-controle", "rounded-carte", "rounded-pastille"]) {
      expect(socle, `le socle n'écrit plus ${pas}`).toContain(pas);
    }
  });

  it("mesure encore un résidu, et le tableau le couvre entièrement", () => {
    // Le plancher inverse, et le plus fort : tant que le produit porte des
    // écarts, la sonde doit en voir — et le tableau doit être exactement
    // l'ensemble des fichiers qui en portent, ni plus, ni moins. Le jour où le
    // résidu tombe à zéro, ce test est ce qui invite à retirer le tableau
    // plutôt qu'à le laisser mentir.
    const mesure = residuMesure();
    expect([...mesure.keys()].sort()).toEqual([...RESIDU.keys()].sort());
    const total = [...mesure.values()].reduce((somme, e) => somme + e.length, 0);
    expect(total).toBe(TOTAL_ANNONCE);
  });
});

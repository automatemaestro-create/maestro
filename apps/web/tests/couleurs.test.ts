/**
 * La couleur écrite à la main dans les écrans (#895).
 *
 * #533 a posé la palette sémantique et #534 la **garde** — mais tous deux
 * jugent la palette, jamais son emploi. Une couleur choisie hors d'elle passe
 * les deux : `contraste.test.ts` lit les octets de `globals.css`,
 * `a11y.test.tsx` ne balaie que les **contrôles de saisie** (#832). Ce fichier
 * est la moitié qui manquait — l'usage.
 *
 * ── Ce que la sonde cherche, et pourquoi cette forme-là ──────────────────────
 *
 * Une **paire écrite à la main** : une couleur choisie deux fois, une fois nue
 * et une fois en `dark:`. C'est la forme exacte que la palette a supprimée —
 * *« ce qui supprime du même geste les 542 `dark:` écrits à la main »*
 * (docs/30 §6.1) —, et c'est ce qui la distingue d'un token : `bg-surface`
 * change avec le thème **tout seul**, donc n'a aucun `dark:` à écrire. Un jeton
 * `dark:` porteur d'une couleur brute est ainsi le signe le plus sûr que le
 * socle a été contourné, et le seul qui ne se confonde avec rien.
 *
 * La sonde compte donc **le jeton `dark:`**, qui est le membre visible de la
 * paire, et non les deux : la moitié claire est parfois implicite
 * (`dark:bg-neutral-950` seul, quand le clair est le défaut), et l'apparier
 * ferait dépendre le compte d'une heuristique là où le `dark:` suffit. C'est
 * aussi ce que le ticket a mesuré, donc ce qui se compare à ses chiffres.
 *
 * ⚠ Elle reconnaît **deux** écritures et il faut les deux : la teinte nommée de
 * Tailwind (`dark:text-neutral-300`) et la valeur **arbitraire**
 * (`dark:bg-[#3987e5]`). Une sonde qui ne verrait que la première s'éteindrait
 * au premier `dark:bg-[#171717]` — un contournement d'une ligne, et un filet
 * qu'on peut contourner en une ligne n'en est pas un.
 *
 * ── Ce sur quoi ce fichier se tait ───────────────────────────────────────────
 *
 * Une couleur brute **sans** `dark:` (`bg-emerald-600` sur un bouton,
 * `outline-sky-600` sur le `<main>` du shell). Ce n'est pas un oubli mais la
 * portée du ticket : elle ne dit pas la même chose — une couleur qui n'a pas eu
 * à être choisie deux fois n'a pas contourné le mécanisme des thèmes —, et deux
 * de ses familles sont déjà gardées ailleurs (le bouton par
 * `socle-visuel.test.tsx`, le contrôle de saisie par `a11y.test.tsx`). L'ouvrir
 * ici ferait passer le résidu de 688 à plus du double d'un coup, sans qu'aucun
 * de ces ajouts ait été jugé.
 */

import { describe, expect, it } from "vitest";

import { lireSource, sansCommentaires, sourcesDuProduit } from "./sources";

// ─────────────────────────────────────────────────────────────────────────────
// 1. LA SONDE
// ─────────────────────────────────────────────────────────────────────────────

/** Les sources balayées. Une feuille de classes peut vivre dans un `.ts` de constantes. */
const EXTENSIONS = [".tsx", ".ts"] as const;

/** Les familles de Tailwind v4 — celles qu'un écran écrit quand il choisit une couleur. */
const TEINTES =
  "white|black|neutral|gray|zinc|slate|stone|red|orange|amber|yellow|lime|green|" +
  "emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose";

/**
 * Les utilités qui portent une couleur. `border-[xytrbles]` couvre les bords
 * dirigés (`border-t-neutral-200`), `divide-[xy]` les séparateurs de liste, et
 * `fill`/`stroke` le SVG — c'est par là qu'un graphique et le DAG d'un pipeline
 * écrivent les leurs, et les oublier laisserait deux fichiers entiers dehors.
 */
const UTILITES =
  "bg|text|border|border-[xytrbles]|ring|ring-offset|outline|divide|divide-[xy]|" +
  "placeholder|caret|accent|decoration|shadow|from|via|to|fill|stroke";

/** `dark:text-neutral-300`, `dark:bg-white/10`, `dark:border-t-amber-800`. */
const TEINTE_NOMMEE = new RegExp(
  `^(?:${UTILITES})-(?:${TEINTES})(?:-\\d{2,3})?(?:\\/\\d{1,3})?$`,
);

/**
 * `dark:bg-[#3987e5]`, `dark:fill-[rgb(…)]`. Le crochet à lui seul ne suffit
 * pas : `w-[38ch]` ou `grid-cols-[1fr_auto]` en portent aussi. Ce qui décide,
 * c'est que la valeur **soit une couleur** — d'où l'ancrage sur ses notations.
 */
const VALEUR_ARBITRAIRE = new RegExp(
  `^(?:${UTILITES})-\\[(?:#|rgb|hsl|hwb|lab|lch|oklab|oklch|color)`,
);

/** `dark:hover:bg-neutral-800` → `bg-neutral-800` : les variantes tombent, c'est l'utilité qui est jugée. */
const utilite = (jeton: string) => jeton.slice(jeton.lastIndexOf(":") + 1);

/**
 * Ce jeton est-il une paire écrite à la main ?
 *
 * Le `dark:` est cherché **n'importe où** dans la pile de variantes : le
 * produit écrit les deux ordres (`dark:hover:` et `hover:dark:`), et n'en
 * reconnaître qu'un laisserait passer la moitié d'un fichier sans que rien ne
 * le dise.
 */
const estUnePaireALaMain = (jeton: string): boolean =>
  /(?:^|:)dark:/.test(jeton) &&
  (TEINTE_NOMMEE.test(utilite(jeton)) || VALEUR_ARBITRAIRE.test(utilite(jeton)));

/**
 * Les paires d'une source, dans l'ordre où elles y sont écrites.
 *
 * Le balayage porte sur les **chaînes littérales** — c'est là que vivent les
 * feuilles de classes, et la mesure le confirme : sur les 688 paires du
 * produit, **zéro** est écrite ailleurs (mesuré le 2026-09-10, en comparant ce
 * balayage à un balayage du texte entier, fichier par fichier). Lire le texte
 * entier reviendrait à compter les noms de variables et les chemins d'import.
 *
 * Un gabarit avale les chaînes qu'il interpole : le découpage laisse donc les
 * guillemets d'un ternaire collés au jeton (`"dark:text-neutral-400`), qu'on
 * retire ensuite — c'est la mécanique de `chainesDeClasses` dans
 * `a11y.test.tsx`, et les deux ont besoin des mêmes précautions.
 */
function pairesDe(source: string): string[] {
  const paires: string[] = [];
  for (const [, guillemets, apostrophes, gabarit] of sansCommentaires(
    source,
  ).matchAll(/"([^"\n]*)"|'([^'\n]*)'|`([^`]*)`/g)) {
    for (const brut of (guillemets ?? apostrophes ?? gabarit ?? "").split(
      /[\s${}]+/,
    )) {
      const jeton = brut.replace(/^["'`]+|["'`]+$/g, "");
      if (jeton && estUnePaireALaMain(jeton)) paires.push(jeton);
    }
  }
  return paires;
}

// ─────────────────────────────────────────────────────────────────────────────
// 2. LA SONDE, PROUVÉE AVANT DE SERVIR
//
// Un balayage qui ne rougit jamais est indiscernable d'un balayage devenu muet
// — les deux rendent un ✓. Ce bloc lui pose donc les questions dont on connaît
// déjà la réponse **avant** qu'il ne juge le produit : la forme exacte que le
// ticket a relevée, les contournements plausibles, et ce sur quoi il ne doit
// surtout pas crier. C'est la règle de `contraste.test.ts` (#534).
// ─────────────────────────────────────────────────────────────────────────────

describe("la sonde, prouvée avant de servir", () => {
  it("reconnaît les trois paires relevées par la veille #868 sur SigneDeVie", () => {
    // Le constat de départ du ticket, recopié tel quel. Si le motif cesse de
    // les voir, ce sont ces lignes-là qui rougissent — pas un cas de bord.
    expect(
      pairesDe(
        '<span className="text-neutral-600 dark:text-neutral-300">agent</span>;' +
          '<time className="text-neutral-500 dark:text-neutral-400">2 min</time>;' +
          '<Icone className="size-3.5 text-sky-600 dark:text-sky-400" />',
      ),
    ).toEqual([
      "dark:text-neutral-300",
      "dark:text-neutral-400",
      "dark:text-sky-400",
    ]);
  });

  it("voit la paire sous ses variantes, dans les deux ordres", () => {
    // `dark:hover:` et `hover:dark:` s'écrivent tous deux dans le produit ; un
    // motif ancré sur le début du jeton n'en verrait qu'un.
    expect(pairesDe('"dark:hover:bg-neutral-900 hover:dark:text-neutral-100"')).toEqual([
      "dark:hover:bg-neutral-900",
      "hover:dark:text-neutral-100",
    ]);
    expect(pairesDe('"dark:focus-visible:outline-sky-400 dark:placeholder:text-neutral-500"')).toEqual(
      ["dark:focus-visible:outline-sky-400", "dark:placeholder:text-neutral-500"],
    );
    // Les bords dirigés, les séparateurs et le SVG : trois familles qu'un motif
    // écrit trop vite laisse dehors, et qui valent deux fichiers entiers ici.
    expect(pairesDe('"dark:border-t-amber-800 dark:divide-y-neutral-800 dark:stroke-neutral-700"')).toEqual([
      "dark:border-t-amber-800",
      "dark:divide-y-neutral-800",
      "dark:stroke-neutral-700",
    ]);
    // L'opacité suffixée, que le produit écrit sur ses voiles.
    expect(pairesDe('"dark:border-white/15 dark:bg-amber-950/40"')).toEqual([
      "dark:border-white/15",
      "dark:bg-amber-950/40",
    ]);
  });

  it("refuse le contournement par valeur arbitraire", () => {
    // La sortie de secours d'une ligne : la même couleur, écrite en
    // hexadécimal. Un filet qu'on contourne en une ligne n'en est pas un — et
    // le produit en porte déjà deux (le bleu de série des graphiques).
    expect(pairesDe('"bg-[#2a78d6] dark:bg-[#3987e5]"')).toEqual(["dark:bg-[#3987e5]"]);
    expect(pairesDe('"dark:fill-[rgb(57,135,229)]"')).toEqual(["dark:fill-[rgb(57,135,229)]"]);
    // …sans crier sur une valeur arbitraire qui n'est pas une couleur : c'est
    // ce qui distingue cette sonde d'un balayage de crochets.
    expect(pairesDe('"dark:w-[38ch] dark:grid-cols-[1fr_auto] dark:text-[10px]"')).toEqual([]);
  });

  it("ne crie pas sur un token du socle, ni sur ce qui n'est pas une couleur", () => {
    // Le pendant du contrôle ci-dessus : une sonde qui crierait sur tout ne
    // dirait pas davantage qu'une sonde muette — et celle-ci verrait rouge sur
    // le socle lui-même, c'est-à-dire sur la sortie qu'elle recommande.
    expect(
      pairesDe(
        '"bg-surface text-texte-secondaire border-bord-fort bg-alerte-creux ' +
          'hover:bg-survol focus-visible:outline-accent text-attention-texte"',
      ),
    ).toEqual([]);
    // Un token porte sa variante `dark:` sans être une couleur brute : rien à
    // signaler, et c'est bien la promesse de la palette.
    expect(pairesDe('"dark:bg-surface-creuse dark:text-info-texte"')).toEqual([]);
    // Et ce qui varie avec le thème sans être une couleur.
    expect(pairesDe('"dark:hidden dark:opacity-50 dark:shadow-none dark:invert"')).toEqual([]);
  });

  it("ne compte pas la prose, et lit les chaînes d'un gabarit", () => {
    // Ce fichier-ci, le README et une dizaine de commentaires du produit citent
    // des paires **pour dire qu'il ne faut plus les écrire**. Les compter
    // ferait grossir le résidu à chaque explication ajoutée.
    expect(pairesDe('// remplacer text-neutral-500 dark:text-neutral-400 par un token')).toEqual([]);
    expect(pairesDe('/* l\'ancien "dark:bg-neutral-900" */')).toEqual([]);
    // Le `//` d'une URL n'ouvre aucun commentaire : sans cette précaution, tout
    // ce qui suit un lien dans le fichier disparaîtrait du balayage.
    expect(pairesDe('const u = "https://x.test";\nconst c = "dark:text-neutral-300";')).toEqual([
      "dark:text-neutral-300",
    ]);
    // La forme du produit : une classe conditionnelle dans un gabarit.
    expect(pairesDe('`x ${actif ? "dark:bg-neutral-800" : "dark:bg-neutral-950"}`')).toEqual([
      "dark:bg-neutral-800",
      "dark:bg-neutral-950",
    ]);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// 3. CE QUE LE SOCLE NE SAIT PAS RENDRE
//
// Toutes les couleurs brutes ne sont pas des fautes, et un balayage qui les
// traiterait toutes pareil enverrait forcer des équivalences approximatives —
// c'est-à-dire changer le rendu pour faire taire un test. Là où **aucun token
// ne correspond**, la bonne issue est de nommer le manque.
//
// ⚠ Ces manques ne sortent PAS du compte du résidu ci-dessous, et c'est
// délibéré : une exemption par motif serait la porte par laquelle n'importe
// quelle paire entrerait en invoquant « le socle ne couvre pas mon cas ». Ils
// disent seulement **jusqu'où** le résidu peut descendre sans que la palette
// bouge d'abord — et chacun est un ticket à ouvrir, pas une tolérance.
// ─────────────────────────────────────────────────────────────────────────────

type Manque = {
  /** Le token qui manque, sous le nom qu'il porterait. */
  readonly token: string;
  /** Un fichier qui en souffre, et la paire qu'on y lit faute de mieux. */
  readonly temoin: string;
  readonly classe: string;
  readonly raison: string;
};

const MANQUES_DU_SOCLE: readonly Manque[] = [
  {
    token: "selectionne",
    temoin: "components/BarreLaterale.tsx",
    classe: "dark:bg-neutral-800",
    raison:
      "l'entrée de menu **active** est un cran plus marquée que la survolée " +
      "(`bg-neutral-200` contre le `#f5f5f5` de `survol` en clair) ; la palette " +
      "n'a qu'un fond de survol, et replier l'actif dessus effacerait la " +
      "distinction entre « où je suis » et « ce que je vise »",
  },
  // `neutre` (Primitives) et `sur-ton-bord` (chat/SourcesDuFil) ont figuré ici
  //   jusqu'à #910 : la veille #905 a établi qu'ils n'étaient pas des couleurs
  //   manquantes mais des **opacités de tokens existants** (`bg-texte/10`,
  //   `border-sur-ton/25`) — comblés sans qu'un token soit ajouté, donc sans que
  //   le contrôle « la palette couvre déjà ce manque » ait pu les voir partir.
  // `provenance` (Primitives) est le troisième parti, par l'autre chemin : #912
  //   a ajouté `--provenance`, `-texte`, `-creux` sur les valeurs violettes que
  //   le badge rendait déjà, et sa ligne est partie parce que le contrôle
  //   ci-dessous l'exigeait — c'est exactement ce pour quoi il existe.
  {
    token: "code",
    temoin: "components/PosteVide.tsx",
    classe: "dark:bg-black",
    raison:
      "le fond d'un bloc de commande, qui reste sombre dans les **deux** " +
      "thèmes — c'est la convention d'un terminal, et aucune des deux surfaces " +
      "de la palette n'est faite pour être invariante",
  },
  {
    token: "serie",
    temoin: "components/GraphiqueEvolutionCout.tsx",
    classe: "dark:fill-[#3987e5]",
    raison:
      "la couleur d'une **série de données**. Les cinq tons portent un sens " +
      "(alerte, positif…) qu'une courbe de dépense n'a pas ; le bleu employé " +
      "n'est même pas de la palette Tailwind, faute de place où le ranger",
  },
];

/**
 * Les utilitaires de couleur que la palette **émet** — c'est le bloc
 * `@theme inline` qui les branche, donc lui qui dit ce que `bg-…` sait rendre.
 * Les lire ici plutôt que de les recopier est ce qui fait qu'un manque comblé
 * rougit au lieu d'être relu comme s'il valait encore.
 */
function tokensDeLaPalette(): Set<string> {
  const feuille = lireSource("app/globals.css");
  const bloc = /@theme\s+inline\s*\{([\s\S]*?)\n\}/.exec(feuille);
  if (!bloc) {
    throw new Error(
      "Aucun bloc « @theme inline » dans app/globals.css : c'est lui qui " +
        "branche les tokens sur les utilitaires, et sans lui cette sonde ne " +
        "saurait plus dire ce que le socle rend. Suivre la palette, pas la " +
        "contourner.",
    );
  }
  return new Set([...bloc[1].matchAll(/--color-([\w-]+)\s*:/g)].map(([, nom]) => nom));
}

describe("ce que le socle ne sait pas rendre", () => {
  it("lit bien la palette, et pas un bloc vide", () => {
    // Sans ce contrôle, un `@theme inline` déplacé rendrait zéro token, donc
    // « aucun de ces manques n'est comblé » — vrai par accident.
    const tokens = tokensDeLaPalette();
    expect(tokens.size).toBeGreaterThanOrEqual(20);
    expect(tokens).toContain("surface");
    expect(tokens).toContain("texte-secondaire");
    expect(tokens).toContain("alerte-creux");
  });

  it("ne nomme aucun manque que la palette couvre déjà", () => {
    // LE contrôle qui empêche cette liste de devenir l'endroit où l'on range ce
    // qui échoue : le jour où `--color-selectionne` est déclaré, la ligne rougit
    // et son témoin se replie. ⚠ Il ne tient que sur le **nom** — un token
    // ajouté sous un autre nom (`--color-selection`) ne le déclencherait pas.
    const tokens = tokensDeLaPalette();
    for (const { token, temoin } of MANQUES_DU_SOCLE) {
      expect(
        tokens.has(token),
        `--color-${token} existe désormais : ${temoin} n'a plus de raison de ` +
          "l'écrire à la main, et cette ligne n'a plus de raison d'être",
      ).toBe(false);
    }
  });

  it("garde chaque manque rattaché à un témoin qui existe et qui le porte encore", () => {
    // Une exemption qui ne désigne plus rien est un motif qu'on relira comme
    // s'il valait encore (règle de `HORS_PAIRES`, #534).
    const sources = sourcesDuProduit(EXTENSIONS);
    for (const { token, temoin, classe, raison } of MANQUES_DU_SOCLE) {
      expect(sources, `${token} : ${temoin} n'existe plus`).toContain(temoin);
      expect(
        pairesDe(lireSource(temoin)),
        `${token} : ${temoin} ne porte plus « ${classe} » — le manque est-il ` +
          `comblé, ou la ligne périmée ?\n  ce qu'elle disait : ${raison}`,
      ).toContain(classe);
    }
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// 4. LE RÉSIDU, NOMMÉ ET COMPTÉ
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Ce qui restait de couleurs écrites à la main au moment de #895, **fichier par
 * fichier avec son compte exact** — mesuré le 2026-09-10, jamais estimé.
 *
 * La raison est la même pour toutes les lignes et n'est donc pas répétée
 * soixante-cinq fois : ces écrans sont **antérieurs à la palette** de #533, qui
 * n'a migré aucun appelant. Ce ticket ne les migre pas non plus — il pose le
 * compte et refuse le suivant. Les lignes qui portent en plus un manque du
 * socle sont annotées ci-dessous : celles-là ne descendront pas à zéro tant que
 * la palette n'aura pas bougé. (Six au lot ; #910 en a comblé deux sans toucher
 * à la palette — `neutre` et `sur-ton-bord` étaient des opacités, pas des
 * couleurs.)
 *
 * Le compte est **exact et non un plafond** : une paire de plus rougit, une
 * paire de **moins** rougit aussi tant que la ligne n'est pas mise à jour.
 * C'est ce qui fait qu'un résidu ne peut que décroître, et que chaque
 * décroissance est un geste **écrit** — sans quoi le chiffre du README serait
 * vrai le jour où on l'a mesuré et faux le lendemain. Le message d'échec rend
 * la ligne à recopier : la contrainte doit coûter une seconde, pas une enquête.
 */
const RESIDU = new Map<string, number>([
  ["app/couts/page.tsx", 19],
  ["app/journal/page.tsx", 9],
  ["components/AssistantFlottant.tsx", 30],
  ["components/BanniereErreurApi.tsx", 3],
  // + le fond de la barre latérale et son bord : `surface-creuse` et `bord` les
  //   rendent **au pixel près** dans les deux thèmes (mesuré). Le ticket les
  //   donnait pour un manque plausible du socle ; ils n'en sont pas un, et
  //   c'est la ligne la plus facile de ce tableau à faire baisser.
  ["components/BarreLaterale.tsx", 8], // manque : `selectionne`
  ["components/BarreSuperieure.tsx", 10],
  ["components/BasculeDeVues.tsx", 6],
  ["components/BasculeTheme.tsx", 8],
  ["components/CentreNotifications.tsx", 17],
  ["components/CreationAgentEcran.tsx", 4],
  ["components/EditeurAgent.tsx", 38],
  ["components/EditeurPlaybook.tsx", 4],
  ["components/EtapesTache.tsx", 4],
  ["components/FilActivite.tsx", 1],
  ["components/GraphiqueEvolutionCout.tsx", 13], // manque : `serie`
  ["components/GuidePriseEnMain.tsx", 11],
  ["components/IndicateursTableauDeBord.tsx", 1],
  ["components/Infobulle.tsx", 3],
  // La dernière ligne arrivée : #894 a ajouté une **cinquième** occurrence de
  //   `text-neutral-500 dark:text-neutral-400` dans ce fichier pendant que ce
  //   lot attendait son merge. La migrer seule aurait laissé ses quatre
  //   voisines identiques écrites à la main — une équivalence posée pour faire
  //   taire un test, ce que le message d'échec écarte précisément.
  ["components/Kanban.tsx", 8],
  ["components/LienTicketExterne.tsx", 3],
  ["components/LigneActivite.tsx", 11],
  ["components/ListeAgents.tsx", 5],
  ["components/MenuAide.tsx", 9],
  ["components/OngletLogs.tsx", 6],
  ["components/OngletMcpAgent.tsx", 36],
  ["components/OngletsAgent.tsx", 8],
  ["components/PanneauBriefs.tsx", 5],
  ["components/PanneauCouts.tsx", 9],
  ["components/PanneauDetailTache.tsx", 14],
  ["components/PanneauRunsImmobiles.tsx", 4],
  ["components/PanneauValidations.tsx", 5],
  ["components/PosteVide.tsx", 13], // manque : `code`
  // Le socle lui-même : la carte, les quatre tons d'état du badge (plein et
  // contour) et le contour de `provenance`, l'en-tête de section. C'est la
  // ligne qui compte double — une paire retirée ici retire des recopies
  // partout, et une paire ajoutée s'imprime sur tous les écrans à la fois. Elle
  // est dans le tableau comme les autres : l'écarter aurait exempté le fichier
  // le plus visible du produit. 43 au lot ; le ton `neutre` est parti avec #910
  // (`bg-texte/10`, `border-bord` — une opacité et un token, pas une couleur de
  // plus), et #912 en a retiré deux en écrivant le ton `provenance` du badge
  // plein sur ses tokens (son contour reste à la main, comme celui des quatre
  // tons d'état).
  ["components/Primitives.tsx", 37],
  ["components/RepartitionAgents.tsx", 7], // manque : `serie`
  ["components/SelecteurReassignation.tsx", 4],
  ["components/SigneDeVie.tsx", 3],
  ["components/brief/CoutBrief.tsx", 1],
  ["components/brief/QuestionsBrief.tsx", 15],
  ["components/brief/SectionsBrief.tsx", 11],
  ["components/brief/ValidationBrief.tsx", 7],
  ["components/brief/ValidationBriefs.tsx", 6],
  ["components/chat/CadrageDansLeFil.tsx", 8],
  ["components/chat/FilDeCadrage.tsx", 6],
  // 2 au lot ; le filet du haut est passé sur `sur-ton/25` avec #910. Ce qui
  //   reste est le fond du rapport déplié, qui reprend celui de l'écran.
  ["components/chat/SourcesDuFil.tsx", 1],
  ["components/composer/ComposerObjectif.tsx", 14],
  ["components/composer/RapportExtraction.tsx", 14],
  ["components/composer/RefusSource.tsx", 4],
  ["components/integrations/BibliothequeMcp.tsx", 49],
  ["components/integrations/PoolProjet.tsx", 16],
  ["components/parametres/NavigationParametres.tsx", 6],
  ["components/parametres/ParametresAgents.tsx", 11],
  ["components/parametres/ParametresApparence.tsx", 5],
  ["components/parametres/ParametresFournisseurs.tsx", 12],
  ["components/parametres/ParametresGeneral.tsx", 8],
  ["components/parametres/SectionParametres.tsx", 8],
  ["components/projets/ChoixProjet.tsx", 11],
  ["components/projets/ExplorateurDossiers.tsx", 19],
  ["components/projets/FormulaireProjet.tsx", 8],
  ["components/projets/ListeProjets.tsx", 7],
  ["components/projets/SelecteurProjet.tsx", 15],
  ["components/runs/EtatDesRuns.tsx", 1],
  ["components/runs/FriseRun.tsx", 13],
  ["components/runs/ListeRuns.tsx", 1],
  ["components/runs/VuePipeline.tsx", 26],
  ["components/runs/VueRun.tsx", 3],
]);

/** Le compte du README — épinglé ici pour qu'il ne puisse pas dériver en silence. */
const TOTAL_ANNONCE = 682;

/** Ce que le produit porte aujourd'hui, fichier par fichier. */
function residuMesure(): Map<string, string[]> {
  const parFichier = new Map<string, string[]>();
  for (const fichier of sourcesDuProduit(EXTENSIONS)) {
    const paires = pairesDe(lireSource(fichier));
    if (paires.length > 0) parFichier.set(fichier, paires);
  }
  return parFichier;
}

/** La ligne à recopier dans `RESIDU`, pour que la mise à jour coûte une seconde. */
const ligne = (fichier: string, compte: number) => `  ["${fichier}", ${compte}],`;

/**
 * Le verdict rendu sur une mesure, face au tableau — les deux moitiés du
 * critère : ce qui **entre** (une paire de plus qu'inscrit, ou un fichier qui
 * n'y est pas du tout) et ce qui a **vieilli** (une ligne qui annonce plus que
 * ce qu'on mesure).
 *
 * Chaque défaut est nommé **une fois** : un dépassement est une nouveauté, pas
 * une ligne périmée, et le compter des deux côtés ferait chercher deux causes
 * là où il n'y en a qu'une.
 */
function confronter(
  mesure: ReadonlyMap<string, readonly string[]>,
  tableau: ReadonlyMap<string, number>,
  sources: readonly string[],
): { nouvelles: string[]; perimees: string[] } {
  const nouvelles: string[] = [];
  for (const [fichier, paires] of [...mesure].sort()) {
    const inscrit = tableau.get(fichier) ?? 0;
    if (paires.length <= inscrit) continue;
    nouvelles.push(
      `  ${fichier} — ${paires.length} paire(s) pour ${inscrit} au tableau\n` +
        `    au-delà du compte inscrit : ${paires.slice(inscrit).join(", ")}\n` +
        (inscrit === 0
          ? ""
          : `    si le tableau doit suivre :\n  ${ligne(fichier, paires.length)}\n`),
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

describe("le verdict, prouvé sur une mesure fabriquée", () => {
  // Ce que le produit subit, ce n'est pas le motif seul mais le motif **plus**
  // la confrontation au tableau. Une comparaison qui cesserait de comparer
  // rendrait « rien à signaler » sur les deux contrôles suivants, et c'est
  // précisément le vert qu'on ne pourrait pas distinguer du bon.
  const sources = ["a.tsx", "b.tsx", "c.tsx"];
  const tableau = new Map([
    ["a.tsx", 2],
    ["b.tsx", 1],
  ]);

  it("se tait sur une mesure conforme", () => {
    const mesure = new Map([
      ["a.tsx", ["dark:bg-neutral-900", "dark:text-neutral-400"]],
      ["b.tsx", ["dark:border-amber-800"]],
    ]);
    expect(confronter(mesure, tableau, sources)).toEqual({ nouvelles: [], perimees: [] });
  });

  it("refuse un fichier que le tableau ne nomme pas", () => {
    // Le cas nominal du prochain écran : il n'a droit à aucune paire, et le
    // message ne lui propose pas de ligne à recopier — c'est une couleur à
    // choisir dans le socle, pas un compte à mettre à jour.
    const mesure = new Map([
      ["a.tsx", ["dark:bg-neutral-900", "dark:text-neutral-400"]],
      ["b.tsx", ["dark:border-amber-800"]],
      ["c.tsx", ["dark:text-neutral-300"]],
    ]);
    const { nouvelles, perimees } = confronter(mesure, tableau, sources);
    expect(nouvelles).toHaveLength(1);
    expect(nouvelles[0]).toContain("c.tsx — 1 paire(s) pour 0 au tableau");
    expect(nouvelles[0]).toContain("dark:text-neutral-300");
    expect(nouvelles[0]).not.toContain("si le tableau doit suivre");
    // Un fichier absent du tableau ne peut pas rendre une ligne périmée : la
    // seconde moitié ne juge que ce qui y est inscrit.
    expect(perimees).toEqual([]);
  });

  it("refuse un dépassement, et ne le compte qu'une fois", () => {
    const mesure = new Map([
      ["a.tsx", ["dark:bg-neutral-900", "dark:text-neutral-400", "dark:border-bord"]],
      ["b.tsx", ["dark:border-amber-800"]],
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
      ["a.tsx", ["dark:bg-neutral-900"]],
      ["b.tsx", ["dark:border-amber-800"]],
    ]);
    const { nouvelles, perimees } = confronter(mesure, tableau, sources);
    expect(nouvelles).toEqual([]);
    expect(perimees).toHaveLength(1);
    expect(perimees[0]).toContain('["a.tsx", 1],');
  });

  it("distingue un fichier vidé de ses couleurs d'un fichier disparu", () => {
    // Les deux appellent le même geste — retirer la ligne — mais pas la même
    // relecture : l'un est une migration réussie, l'autre un fichier supprimé
    // dont la ligne serait relue comme si elle valait encore.
    const sansCouleurs = new Map([["b.tsx", ["dark:border-amber-800"]]]);
    const vide = confronter(sansCouleurs, tableau, sources);
    expect(vide.perimees).toEqual([
      "  a.tsx — inscrit pour 2, on n'en mesure plus aucune : retirer la ligne",
    ]);
    // Le même tableau, mais `a.tsx` n'est plus dans les sources parcourues.
    const disparu = confronter(sansCouleurs, tableau, ["b.tsx", "c.tsx"]);
    expect(disparu.perimees).toEqual([
      "  a.tsx — inscrit pour 2, mais le fichier n'existe plus : retirer la ligne",
    ]);
  });
});

describe("le résidu de couleurs écrites à la main", () => {
  it("ne laisse entrer aucune paire nouvelle", () => {
    // Le critère du ticket : la sonde tolère ce qui est nommé, elle refuse le
    // suivant. Un fichier absent du tableau n'a droit à aucune paire ; un
    // fichier présent n'a droit qu'aux siennes.
    const { nouvelles } = confronter(residuMesure(), RESIDU, sourcesDuProduit(EXTENSIONS));
    expect(
      nouvelles,
      `\n${nouvelles.join("\n")}\n` +
        "Une couleur se choisit UNE fois : `bg-surface`, `text-texte-secondaire`,\n" +
        "`border-bord`, `text-alerte-texte` — les deux thèmes viennent avec le\n" +
        "token, donc il n'y a aucun `dark:` à écrire (voir apps/web/README.md,\n" +
        "« La palette sémantique »). Si aucun token ne correspond, c'est un manque\n" +
        "du socle : l'inscrire dans MANQUES_DU_SOCLE et ouvrir le ticket, plutôt\n" +
        "que de forcer une équivalence qui changerait le rendu.\n",
    ).toHaveLength(0);
  });

  it("ne garde aucune ligne périmée : le compte est exact, pas un plafond", () => {
    // Le versant qui fait décroître le résidu. Une ligne qui annonce plus que
    // ce qu'on mesure est une migration faite sans que le tableau l'ait dit —
    // et un chiffre de README devenu faux.
    const { perimees } = confronter(residuMesure(), RESIDU, sourcesDuProduit(EXTENSIONS));
    expect(
      perimees,
      `\n${perimees.join("\n")}\n\nUne paire retirée est une bonne nouvelle : mettre le tableau à jour, ` +
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
// 5. QUE LE BALAYAGE NE DEVIENNE PAS MUET
//
// Tout ce qui précède rend un ✓ si le motif cesse de matcher, si le parcours
// des sources rend une liste vide, ou si les chaînes ne se lisent plus. Ces
// planchers rendent le ✓ opposable — c'est la moitié que perd le plus
// facilement un balayage de sources.
// ─────────────────────────────────────────────────────────────────────────────

describe("la couverture du balayage", () => {
  it("parcourt bien le produit, et pas une liste vide", () => {
    const sources = sourcesDuProduit(EXTENSIONS);
    expect(sources.length).toBeGreaterThanOrEqual(100);
    expect(sources).toContain("components/Primitives.tsx");
    expect(sources.filter((f) => f.startsWith("app/")).length).toBeGreaterThanOrEqual(10);
  });

  it("lit bien des feuilles de classes, et pas un fichier vide", () => {
    // 4 670 chaînes littérales au lot. Un motif d'extraction cassé les ramènerait
    // à zéro, donc « aucune paire » — avec les mots de « tout est tokenisé ».
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
    // que le produit porte des paires, la sonde doit en voir — et le tableau
    // doit être exactement l'ensemble des fichiers qui en portent, ni plus, ni
    // moins. Le jour où le résidu tombe à zéro, ce test est ce qui invite à
    // retirer le tableau plutôt qu'à le laisser mentir.
    const mesure = residuMesure();
    expect([...mesure.keys()].sort()).toEqual([...RESIDU.keys()].sort());
    const total = [...mesure.values()].reduce((somme, p) => somme + p.length, 0);
    expect(total).toBe(TOTAL_ANNONCE);
  });
});

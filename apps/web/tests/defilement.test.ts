/**
 * **« En bas » est le bas du FIL, pas le bas de la PAGE** (#941).
 *
 * Le défaut : à 420 × 860, `/chat` s'ouvrait sur le bas de la pile de panneaux,
 * composeur 93 px **au-dessus** du bord supérieur de l'écran (retex du
 * 2026-09-11, constat G8). La cause n'est pas l'empilement — à 420 × 1400, où
 * la page ne déborde pas, l'ordre est juste : c'est que « coller en bas »
 * visait le bas de l'**ascenseur**, or sous `@4xl` la colonne de propriétés
 * passe **sous** la conversation.
 *
 * ⚠ **Aucune géométrie ici** (#308) : jsdom ne calcule ni hauteur ni
 * défilement, et ce fichier ne lui en demande pas. Ce qu'il éprouve est la
 * **règle** — une fonction de nombres —, sur les mesures que le banc a relevées
 * dans un vrai navigateur le 2026-09-19 (`.maestro/banc/`, verdict en
 * description de PR). Le pixel reste au banc, la règle vient ici : c'est
 * exactement le partage que docs/10 §8.4 demande, et c'est ce qui manquait à
 * #306 comme à ce défaut-ci.
 *
 * Chaque sonde est **prouvée sur un échantillon fautif avant de servir**
 * (méthode de #534/#537/#539) : l'échantillon est la règle d'avant ce lot — le
 * bas de l'ascenseur —, rejouée telle quelle sur les mêmes mesures.
 */

import { describe, expect, it } from "vitest";

import { SEUIL_BAS_PX, estEnBas, positionEnBas } from "@/lib/defilement";

/**
 * Une scène mesurée : un ascenseur, le fil qu'il porte, et le composeur à quai.
 *
 * Les rectangles suivent le défilement comme dans un navigateur — c'est la
 * seule chose que jsdom ne fait pas et que la règle interroge : reposer une
 * mesure fixe ferait passer n'importe quelle formule.
 */
function scene({
  vue,
  bout,
  /** Le bas du fil, en coordonnées de fenêtre, quand `scrollTop` vaut zéro. */
  basDuFilAuSommet,
  quaiCollant = true,
  reserveDuQuai = 64,
}: {
  vue: number;
  bout: number;
  basDuFilAuSommet: number;
  quaiCollant?: boolean;
  reserveDuQuai?: number;
}) {
  const ascenseur = document.createElement("div");
  let position = 0;
  Object.defineProperty(ascenseur, "clientHeight", { get: () => vue });
  Object.defineProperty(ascenseur, "scrollHeight", { get: () => vue + bout });
  Object.defineProperty(ascenseur, "scrollTop", {
    get: () => position,
    set: (valeur: number) => {
      position = valeur;
    },
  });
  // L'ascenseur occupe la fenêtre : son bas visible est donc `vue`.
  ascenseur.getBoundingClientRect = () =>
    ({ top: 0, bottom: vue, height: vue }) as DOMRect;

  const fil = document.createElement("section");
  fil.getBoundingClientRect = () =>
    ({ bottom: basDuFilAuSommet - position }) as DOMRect;

  const quai = document.createElement("form");
  if (quaiCollant) {
    quai.style.position = "sticky";
    quai.style.bottom = `${reserveDuQuai}px`;
  }

  return {
    ascenseur,
    fil,
    quai,
    /** La règle d'AVANT #941 — l'échantillon fautif. */
    regleDAvant: () => ascenseur.scrollHeight,
    /**
     * De combien le bas du fil serait **sous** le bas de la vue à cette
     * position (négatif : il est au-dessus, donc en vue — ou hors champ par le
     * haut quand il descend sous `-vue`).
     */
    resteAVoir: (cible: number) => basDuFilAuSommet - cible - vue,
  };
}

/**
 * Les mesures du banc, 2026-09-19, `/chat` en mode démo.
 *
 * - **téléphone** : 420 × 860, page de 1375 px (bout 515), bas du fil à 422 px
 *   du sommet. C'est la scène du ticket ;
 * - **poste de travail** : 1280 × 800, fil long (bout 866), bas du fil à
 *   1570 — c'est-à-dire 704 au bout du défilement, les 96 px de la réserve de
 *   fin de page (#888) sous lui.
 */
const TELEPHONE = { vue: 860, bout: 515, basDuFilAuSommet: 422 };
const POSTE = { vue: 800, bout: 866, basDuFilAuSommet: 1570 };

describe("la sonde, prouvée sur la règle d'avant #941", () => {
  it("attrape le défaut du ticket : le composeur emporté hors champ par le haut", () => {
    const { regleDAvant, resteAVoir } = scene(TELEPHONE);
    // Le bas de l'ascenseur, c'est 515 : le fil y finit 953 px au-dessus du bas
    // de la vue, c'est-à-dire 93 px au-dessus de son **sommet** (860 − 953).
    expect(regleDAvant()).toBeGreaterThan(TELEPHONE.bout - 1);
    expect(resteAVoir(TELEPHONE.bout)).toBe(-953);
    expect(-953 + TELEPHONE.vue).toBe(-93);
  });

  it("ne crie pas sur la scène où cette règle était juste", () => {
    // Au poste de travail le fil finit bien la page : la règle d'avant y pose
    // le bas du fil 96 px au-dessus du bord, et c'est le repos de #888.
    const { resteAVoir } = scene(POSTE);
    expect(resteAVoir(POSTE.bout)).toBe(-96);
  });
});

describe("positionEnBas vise la fin du fil", () => {
  it("au format téléphone, ne défile pas dans ce qui suit le fil", () => {
    const { ascenseur, fil, quai } = scene(TELEPHONE);
    // Le fil tient déjà en haut de l'écran : il n'y a rien à défiler, et la
    // page reste où elle est au lieu de partir au bas des panneaux.
    expect(positionEnBas(ascenseur, fil, quai)).toBe(0);
  });

  it("laisse au composeur la bande qu'il se réserve à quai", () => {
    // Fil long au format téléphone : la cible amène la fin du fil à 64 px du
    // bord — la réserve du quai —, pour que le composeur s'y pose à sa place
    // naturelle au lieu d'être décalé sur le dernier message (#888).
    const long = { vue: 860, bout: 1715, basDuFilAuSommet: 1622 };
    const { ascenseur, fil, quai, resteAVoir } = scene(long);
    const cible = positionEnBas(ascenseur, fil, quai);
    expect(cible).toBe(826);
    expect(resteAVoir(cible)).toBe(-64);
    // Sans quai déclaré, la fin du fil vient au ras du bord : c'est ce que la
    // réserve corrige, et la différence est exactement sa valeur.
    const nu = scene({ ...long, quaiCollant: false });
    expect(positionEnBas(nu.ascenseur, nu.fil, nu.quai)).toBe(cible - 64);
  });

  it("va jusqu'au bout quand ce qui suit le fil est la réserve de fin de page", () => {
    // Le repos de #888, que ce lot ne remonte pas d'un pixel : la cible
    // calculée tombe à 32 px du bout, donc sous le seuil, donc au bout.
    const { ascenseur, fil, quai } = scene(POSTE);
    expect(positionEnBas(ascenseur, fil, quai)).toBe(POSTE.bout);
  });

  it("ne demande jamais plus que ce que l'ascenseur peut défiler", () => {
    const { ascenseur, fil, quai } = scene({
      vue: 400,
      bout: 100,
      basDuFilAuSommet: 5000,
    });
    expect(positionEnBas(ascenseur, fil, quai)).toBe(100);
  });

  it("rend le bas de l'ascenseur là où il n'y a rien à mesurer", () => {
    // Sous jsdom, et sur un élément détaché : le geste d'avant ce lot. Une
    // cible calculée sur des zéros dirait « le haut de la page » avec les mots
    // de « le bas du fil » — c'est le pire des deux, puisqu'il se tait.
    const { ascenseur, fil, quai } = scene(TELEPHONE);
    expect(positionEnBas(ascenseur, null, quai)).toBe(TELEPHONE.bout);
    const aveugle = scene({ ...TELEPHONE, vue: 0 });
    expect(positionEnBas(aveugle.ascenseur, aveugle.fil, aveugle.quai)).toBe(
      TELEPHONE.bout,
    );
  });
});

describe("estEnBas lit la MÊME cible que le recollement", () => {
  it("dit « en bas » là où le recollement vient de poser la vue", () => {
    const { ascenseur, fil, quai } = scene(TELEPHONE);
    ascenseur.scrollTop = positionEnBas(ascenseur, fil, quai);
    expect(estEnBas(ascenseur, fil, quai)).toBe(true);
  });

  it("dit décroché des DEUX côtés de la cible", () => {
    // Remonté lire, mais aussi **descendu** dans ce qui suit le fil : les deux
    // sont un décrochage, et le geste de retour (#877) ramène au même endroit.
    const long = { vue: 860, bout: 1715, basDuFilAuSommet: 1622 };
    const { ascenseur, fil, quai } = scene(long);
    const cible = positionEnBas(ascenseur, fil, quai);
    ascenseur.scrollTop = cible - SEUIL_BAS_PX - 1;
    expect(estEnBas(ascenseur, fil, quai)).toBe(false);
    ascenseur.scrollTop = cible + SEUIL_BAS_PX + 1;
    expect(estEnBas(ascenseur, fil, quai)).toBe(false);
    ascenseur.scrollTop = cible + SEUIL_BAS_PX;
    expect(estEnBas(ascenseur, fil, quai)).toBe(true);
  });
});

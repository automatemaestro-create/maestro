/**
 * L'écran « Intégrations » (#270, lot 3/6 de #244) — et, avant lui, **le
 * harnais qui le mesure**.
 *
 * ⚠ La suite du chantier revient au lot 6 (#273) : ce fichier ne couvre pas le
 * comportement de l'écran (ajouter au pool, retirer, chercher — la bibliothèque
 * a le sien depuis #231, `integrations-bibliotheque.test.tsx`). Il tient deux
 * choses qu'on ne pouvait pas y différer sans les perdre :
 *
 * 1. **le harnais rend la main sur un écran chargé.** `monterEcran` n'attendait
 *    que le `h1` de la barre supérieure — qui vient du menu, donc présent au
 *    premier rendu —, si bien que tout écran chargeant en différé était audité
 *    sur son « Chargement… ». Mesuré en écrivant ce lot : le pool était **vide
 *    à l'écran** pendant que `a11y` et `sobriete` le déclaraient conforme. Le
 *    drain ajouté dans `ecrans.tsx` corrige ça, et rien ne le garderait : le
 *    retirer rendrait les deux sondes vertes **et muettes**, ce qui est le mode
 *    de panne que ces deux suites existent pour empêcher ;
 * 2. **les trois critères du ticket**, à leur plus mince — l'écran existe et
 *    porte ses blocs, il dit qui utilise chaque intégration, et l'ancre retirée
 *    des Paramètres mène bien ici.
 */

import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { marquerGuideVu } from "@/lib/guide";
import { MENU } from "@/lib/navigation";
import { SECTIONS_PARAMETRES } from "@/lib/parametres";

import { poserProjetActif } from "./aides";
import { ECRANS, monterEcran, peuplerEtat } from "./ecrans";

vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  const { mocksApi } = await import("./ecrans-reseau");
  return { ...reel, ...mocksApi() };
});

/**
 * Le routeur du dépôt (`aides.routeurFactice`) enregistre `push` et `replace`
 * dans la **même** liste de chaînes : il dit *où* l'on va, jamais *comment*. Or
 * ici c'est le comment qui porte la décision — un `push` laisserait derrière lui
 * une entrée d'historique qui redirige au retour arrière. D'où un routeur local
 * qui garde le verbe, et lui seul.
 */
const routeur = vi.hoisted(() => ({
  appels: [] as { verbe: string; url: string }[],
}));

vi.mock("next/navigation", async () => {
  const { cheminCourant } = await import("./aides");
  return {
    usePathname: () => cheminCourant(),
    useRouter: () => ({
      push: (url: string) => routeur.appels.push({ verbe: "push", url }),
      replace: (url: string) => routeur.appels.push({ verbe: "replace", url }),
      back: () => {},
      forward: () => {},
      refresh: () => {},
      prefetch: () => {},
    }),
  };
});

const INTEGRATIONS = ECRANS.find((ecran) => ecran.href === "/integrations");
const PARAMETRES = ECRANS.find((ecran) => ecran.href === "/parametres");

describe("l'écran Intégrations", () => {
  beforeEach(() => {
    marquerGuideVu();
    poserProjetActif();
    peuplerEtat();
  });

  it("entre au menu, juste après « Agents »", () => {
    const libelles = MENU.map((entree) => entree.libelle);
    expect(libelles).toContain("Intégrations");
    expect(libelles.indexOf("Intégrations")).toBe(
      libelles.indexOf("Agents") + 1,
    );
  });

  it("monte ses blocs peuplés, et non son écran de chargement", async () => {
    // La moitié qui garde le harnais : sans le drain de `monterEcran`, le pool
    // rendrait « Chargement des intégrations… » et cette assertion tomberait —
    // pendant que `a11y` et `sobriete`, elles, resteraient vertes sur du vide.
    expect(INTEGRATIONS).toBeDefined();
    await monterEcran(INTEGRATIONS!);

    const pool = screen.getByRole("region", {
      name: "Pool projet des intégrations MCP",
    });
    expect(pool).toBeInTheDocument();
    expect(pool.textContent).not.toContain("Chargement");
    expect(
      screen.getByRole("region", { name: "Bibliothèque de serveurs MCP" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("region", { name: "Vue d'ensemble des intégrations" }),
    ).toBeInTheDocument();

    // Le pool du harnais porte deux intégrations, la bibliothèque une entrée.
    expect(screen.getByText("figma-officiel")).toBeInTheDocument();
    expect(screen.getByText("gitlab")).toBeInTheDocument();
    expect(screen.getByText("Slack")).toBeInTheDocument();
  });

  it("dit qui utilise chaque intégration, et mène à sa fiche", async () => {
    await monterEcran(INTEGRATIONS!);

    // `dev` a activé `figma-officiel` (harnais) : son nom est un lien vers
    // l'onglet où l'activation se défait, pas vers la fiche nue.
    expect(screen.getByRole("link", { name: "dev" })).toHaveAttribute(
      "href",
      "/agents/dev/mcp",
    );
    // `qa` n'a rien activé : il n'apparaît donc sous aucune intégration.
    expect(screen.queryByRole("link", { name: "qa" })).not.toBeInTheDocument();
    // Et l'intégration que personne n'utilise le dit, au lieu de se taire.
    expect(screen.getByText(/Aucun agent ne l'a activée/)).toBeInTheDocument();
    // Le bandeau compte les agents équipés sur le total du catalogue.
    expect(screen.getByText("1 / 2")).toBeInTheDocument();
  });
});

describe("l'ancre `/parametres#mcp` après le déménagement (#270)", () => {
  beforeEach(() => {
    marquerGuideVu();
    poserProjetActif();
    peuplerEtat();
    routeur.appels.length = 0;
    window.location.hash = "";
  });

  it("ne laisse plus de section « mcp » au sommaire des Paramètres", () => {
    expect(SECTIONS_PARAMETRES.map((section) => section.id)).not.toContain(
      "mcp",
    );
  });

  it("renvoie le signet vers l'écran, en remplaçant l'entrée d'historique", async () => {
    // Monté par le harnais et non à la main : ce qui se garde ici est que la
    // redirection est **branchée dans la page**, pas seulement écrite.
    expect(PARAMETRES).toBeDefined();
    window.location.hash = "#mcp";
    await monterEcran(PARAMETRES!);
    // `replace` et non `push` : sinon le bouton « Précédent » ramènerait sur la
    // page qui redirige, donc sur une page dont on ne peut plus sortir.
    expect(routeur.appels).toContainEqual({
      verbe: "replace",
      url: "/integrations",
    });
  });

  it("ne détourne aucune autre ancre des Paramètres", async () => {
    // Le pendant du contrôle ci-dessus : une redirection qui partirait sur
    // n'importe quel fragment rendrait la page inatteignable par ses ancres.
    window.location.hash = "#apparence";
    await monterEcran(PARAMETRES!);
    expect(routeur.appels).toEqual([]);
  });
});

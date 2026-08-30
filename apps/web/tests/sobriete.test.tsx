/**
 * La règle des trois places, rendue opposable (#539, lot 7 de #532).
 *
 * Le tableau de bord a **déjà été épuré une fois** (#191 : cinq panneaux de
 * plein format ramenés à « ce qui se lit d'un coup d'œil »). Six mois plus tard
 * le compte était refait, et la cause n'était pas qu'on ait mal épuré : c'est
 * qu'**aucune règle n'a été laissée derrière**. Chaque ajout était légitime pris
 * seul ; c'est leur somme qui refaisait le problème.
 *
 * #471 a produit la règle manquante (docs/30 §4) — trois places, et une seule
 * pour chaque chose :
 *
 * 1. **le bandeau de tête** — au plus **4 chiffres** ;
 * 2. **le corps** — au plus **3 blocs de plein format**, les blocs
 *    d'**arbitrage** exceptés (ils ne comptent pas, et disparaissent quand leur
 *    file est vide) ;
 * 3. **la colonne de propriétés** — tout le reste, sans plafond.
 *
 * Ce fichier est ce qui la rend opposable à un ticket futur : elle se vérifie
 * par un **comptage**, donc par une machine, et non par le jugement de qui
 * relit. C'est exactement ce qui manquait à #191 — la doc du langage visuel
 * existait déjà, détaillée, et 18 recopies de carte sont passées quand même
 * (docs/30 §3.6). Une règle qu'aucune machine ne vérifie ne tient pas.
 *
 * Trois choix de conception à ne pas défaire :
 *
 * - **rien n'est déclaré, tout est dérivé.** Un bloc n'annonce pas sa place : le
 *   bandeau de tête est reconnu à ses `TuileChiffre` (`data-chiffre`, posé sur la
 *   primitive), la colonne de propriétés à sa balise `<aside>`, et l'arbitrage
 *   se **prouve** en montant l'écran une seconde fois, files vides. Un bloc qui
 *   prétendrait arbitrer sans disparaître compterait comme les autres ;
 * - **la sonde est prouvée sur un échantillon fautif avant de balayer.** Sans
 *   cette moitié, un comptage mal branché — mauvais sélecteur, mauvaise racine —
 *   rendrait « 0 dépassement » sur une question jamais posée. C'est la méthode
 *   de `contraste.test.ts` (#534) et de `a11y.test.tsx` (#537) ;
 * - **un bloc sans nom fait rougir.** Ce n'est pas de la cosmétique : c'est ce
 *   qui empêche un bloc d'échapper au recensement en silence, et ça double le
 *   `region` d'axe, qui veut la même chose pour une autre raison.
 *
 * ⚠ Ce qui **ne** se mesure **pas** ici : des pixels. jsdom n'en calcule aucun,
 * et c'est la frontière de #308 — la géométrie appartient au skill
 * `/banc-mise-en-page`. On compte des blocs, ce qui est précisément ce que la
 * règle plafonne.
 */

import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { cleanup, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TITRE_RUNS_IMMOBILES } from "@/components/PanneauRunsImmobiles";
import { ID_CONTENU_PRINCIPAL } from "@/components/Shell";
import { marquerGuideVu } from "@/lib/guide";
import { MENU } from "@/lib/navigation";

import { poserProjetActif } from "./aides";
import {
  ECRANS,
  monterEcran,
  peuplerEtat,
  peuplerEtatSansArbitrage,
  type Ecran,
} from "./ecrans";

// --- Le réseau, débranché comme pour l'audit d'accessibilité ----------------

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

// --- Les plafonds (docs/30 §4.1) -------------------------------------------

/** Bandeau de tête : « quatre est un plafond, pas une cible » (docs/30 §4.3). */
const CHIFFRES_MAX = 4;
/** Corps : trois blocs de plein format, arbitrage non compté. */
const BLOCS_MAX = 3;

/**
 * Ce qui est un bloc dans le DOM. `<section>` pour le corps et le bandeau,
 * `<aside>` pour la colonne de propriétés — les deux balises que le produit
 * emploie déjà, et non un attribut inventé pour l'occasion.
 *
 * Ce qui n'en est **pas** un, et le compte le montre : une `<nav>` (le filtre de
 * période de `/couts`, le sommaire de `/parametres`, la bascule de vues d'un
 * run) règle l'écran ou y navigue, elle n'occupe pas une place ; un `<article>`
 * ou une `<div>` est du contenu **dans** un bloc.
 */
const SELECTEUR_BLOC = "section, aside";

// --- La sonde ---------------------------------------------------------------

/** Le bloc est-il de premier niveau, c'est-à-dire sans bloc au-dessus de lui ? */
function estDePremierNiveau(noeud: Element, racine: Element): boolean {
  let parent = noeud.parentElement;
  while (parent !== null && parent !== racine) {
    if (parent.matches(SELECTEUR_BLOC)) return false;
    parent = parent.parentElement;
  }
  return true;
}

/**
 * Le nom d'un bloc — ce sous quoi le recensement le désigne, et ce qui permet de
 * le suivre d'un montage à l'autre. `aria-label` d'abord (la forme majoritaire),
 * puis le texte que `aria-labelledby` désigne, puis l'`id` de l'ancre. Un bloc
 * qui n'a rien de tout cela rend la chaîne vide, et c'est un échec.
 */
function nomDe(bloc: Element): string {
  const etiquette = bloc.getAttribute("aria-label");
  if (etiquette !== null && etiquette.trim() !== "") return etiquette.trim();
  const cible = bloc.getAttribute("aria-labelledby");
  if (cible !== null) {
    const titre = bloc.ownerDocument.getElementById(cible);
    const texte = (titre?.textContent ?? "").trim();
    if (texte !== "") return texte;
  }
  return bloc.id ?? "";
}

/**
 * Le bandeau de tête : un bloc dont **tous** les enfants directs sont des
 * chiffres (`TuileChiffre`). La condition porte sur *tous* et non sur *au moins
 * un* : sans cela, un bloc de corps qui afficherait une tuile en tête passerait
 * pour le bandeau et sortirait du plafond — c'est la seule façon de tricher que
 * ce comptage laisserait ouverte.
 */
function estBandeauDeTete(bloc: Element): boolean {
  return (
    bloc.children.length > 0 &&
    [...bloc.children].every((enfant) => enfant.matches("[data-chiffre]"))
  );
}

type Places = {
  /** Les chiffres du bandeau de tête — au plus `CHIFFRES_MAX`. */
  chiffres: string[];
  /** Les blocs du corps, par leur nom — au plus `BLOCS_MAX`. */
  corps: string[];
  /** La ou les colonnes de propriétés — il n'en faut jamais plus d'une. */
  colonnes: string[];
  /** Les blocs de premier niveau sans nom : toujours une faute. */
  anonymes: string[];
};

/** Range les blocs de premier niveau de `racine` dans les trois places. */
function placesDe(racine: Element): Places {
  const places: Places = { chiffres: [], corps: [], colonnes: [], anonymes: [] };
  const blocs = [...racine.querySelectorAll(SELECTEUR_BLOC)].filter((bloc) =>
    estDePremierNiveau(bloc, racine),
  );
  for (const bloc of blocs) {
    const nom = nomDe(bloc);
    if (nom === "") {
      places.anonymes.push(`<${bloc.tagName.toLowerCase()}>`);
      continue;
    }
    if (bloc.tagName === "ASIDE") places.colonnes.push(nom);
    else if (estBandeauDeTete(bloc))
      places.chiffres.push(
        ...[...bloc.querySelectorAll("[data-chiffre]")].map(
          (tuile) => (tuile.textContent ?? "").trim().slice(0, 30) || nom,
        ),
      );
    else places.corps.push(nom);
  }
  return places;
}

/** Le corps de l'écran monté : la racine sur laquelle le comptage porte. */
function contenuPrincipal(): HTMLElement {
  const contenu = document.getElementById(ID_CONTENU_PRINCIPAL);
  if (contenu === null) throw new Error("l'écran n'a pas de contenu principal");
  return contenu;
}

// --- 1. La sonde, prouvée avant de servir -----------------------------------

describe("la sonde de sobriété", () => {
  /** Rend un écran de laboratoire et rend ses places. */
  function mesurer(fragment: React.ReactElement): Places {
    const { container } = render(fragment);
    return placesDe(container);
  }

  it("compte les blocs de premier niveau, et refuse d'aller plus loin", () => {
    // La moitié qui prouve : quatre blocs comptent pour quatre, et les
    // sous-blocs du quatrième ne comptent pas. Un sélecteur qui aurait ratissé
    // toutes les `<section>` du document en trouverait six, et le plafond serait
    // franchi pour une raison fausse.
    const places = mesurer(
      <div>
        <section aria-label="Un">a</section>
        <div>
          <section aria-label="Deux">b</section>
        </div>
        <section aria-label="Trois">c</section>
        <section aria-label="Quatre">
          <section aria-label="Sous-bloc A">d</section>
          <section aria-label="Sous-bloc B">e</section>
        </section>
      </div>,
    );
    expect(places.corps).toEqual(["Un", "Deux", "Trois", "Quatre"]);
    expect(places.corps.length).toBeGreaterThan(BLOCS_MAX);
  });

  it("ne compte ni la navigation ni le contenu d'un bloc", () => {
    // La `<nav>` de période règle l'écran, la `<div>` est du contenu : ni l'une
    // ni l'autre n'occupe une place. Sans cette borne, `/couts` serait déclaré
    // fautif pour son filtre.
    const places = mesurer(
      <div>
        <nav aria-label="Période">filtres</nav>
        <div>du contenu</div>
        <section aria-label="Le seul bloc">x</section>
      </div>,
    );
    expect(places.corps).toEqual(["Le seul bloc"]);
  });

  it("reconnaît le bandeau de tête à ses chiffres, et compte les tuiles", () => {
    const places = mesurer(
      <div>
        <section aria-label="Totaux">
          <div data-chiffre="">1</div>
          <div data-chiffre="">2</div>
          <div data-chiffre="">3</div>
        </section>
      </div>,
    );
    expect(places.chiffres).toHaveLength(3);
    // Et il ne compte pas dans le corps : le plafond de trois blocs porte sur le
    // corps seul, sans quoi tout écran chiffré partirait avec un bloc de retard.
    expect(places.corps).toEqual([]);
  });

  it("ne prend pas un bloc de corps pour un bandeau parce qu'il porte un chiffre", () => {
    // Le pendant du contrôle ci-dessus, et la seule triche que ce comptage
    // laisserait ouverte : une table posée sous une tuile pour sortir du
    // plafond. Le bandeau n'est un bandeau que s'il n'est **que** des chiffres.
    const places = mesurer(
      <div>
        <section aria-label="Un bloc qui résume">
          <div data-chiffre="">42</div>
          <table>
            <tbody>
              <tr>
                <td>et tout le reste</td>
              </tr>
            </tbody>
          </table>
        </section>
      </div>,
    );
    expect(places.corps).toEqual(["Un bloc qui résume"]);
    expect(places.chiffres).toEqual([]);
  });

  it("range la colonne de propriétés à part, et sans plafond", () => {
    const places = mesurer(
      <div>
        <section aria-label="Le corps">x</section>
        <aside aria-label="Propriétés">
          <section aria-label="Rangée ici">y</section>
          <section aria-label="Et là aussi">z</section>
        </aside>
      </div>,
    );
    expect(places.corps).toEqual(["Le corps"]);
    expect(places.colonnes).toEqual(["Propriétés"]);
  });

  it("nomme les blocs anonymes plutôt que de les laisser passer", () => {
    const places = mesurer(
      <div>
        <section>sans nom</section>
        <section id="avec-ancre">nommé par son ancre</section>
      </div>,
    );
    expect(places.anonymes).toEqual(["<section>"]);
    expect(places.corps).toEqual(["avec-ancre"]);
  });
});

// --- 2. Les dix écrans ------------------------------------------------------

/** Ce que le recensement d'un écran donne à lire quand il dépasse. */
function raconter(ecran: Ecran, places: Places): string {
  return [
    ``,
    `${ecran.href} — ${places.chiffres.length} chiffre(s) de tête, ` +
      `${places.corps.length} bloc(s) de corps, ` +
      `${places.colonnes.length} colonne(s) de propriétés`,
    ...places.corps.map((nom) => `  corps        · ${nom}`),
    ...places.colonnes.map((nom) => `  propriétés   · ${nom}`),
    ...places.anonymes.map((balise) => `  SANS NOM     · ${balise}`),
    ``,
  ].join("\n");
}

describe("les dix écrans face à la règle des trois places", () => {
  beforeEach(() => {
    marquerGuideVu();
    poserProjetActif();
  });

  it("recense exactement les écrans du menu", () => {
    // La table est **dérivée**, pas recopiée : une page ajoutée au menu sans cas
    // de recensement fait rougir ici, au lieu d'échapper à la règle en silence.
    // Même contrôle que celui du filet d'accessibilité (#537).
    expect(ECRANS.map((e) => e.href)).toEqual(MENU.map((e) => e.href));
  });

  for (const ecran of ECRANS) {
    it(`tient les trois places sur ${ecran.href}`, async () => {
      // Deux montages, et c'est tout le mécanisme. Le premier, **files pleines**,
      // est l'écran le plus chargé que l'utilisateur puisse voir ; le second,
      // **files vides**, dit lesquels de ses blocs étaient de l'arbitrage. Ce
      // qui survit aux deux est ce que le plafond compte : personne n'a classé
      // quoi que ce soit, et un bloc qui prétendrait arbitrer sans disparaître
      // serait compté comme les autres.
      peuplerEtat();
      await monterEcran(ecran);
      const charge = placesDe(contenuPrincipal());
      cleanup();

      peuplerEtatSansArbitrage();
      await monterEcran(ecran);
      const calme = placesDe(contenuPrincipal());

      const recit = raconter(ecran, charge) + raconter(ecran, calme);
      const permanents = charge.corps.filter((nom) => calme.corps.includes(nom));

      expect(charge.anonymes, recit).toEqual([]);
      expect(calme.anonymes, recit).toEqual([]);
      expect(charge.chiffres.length, recit).toBeLessThanOrEqual(CHIFFRES_MAX);
      // Une seule colonne de propriétés : sans cette borne, la troisième place —
      // la seule sans plafond — deviendrait la sortie de secours de toutes les
      // autres, et il suffirait d'emballer chaque bloc dans son `<aside>` pour
      // que l'écran redevienne « conforme » sans avoir rien épuré.
      expect(charge.colonnes.length, recit).toBeLessThanOrEqual(1);

      expect(permanents.length, recit).toBeLessThanOrEqual(BLOCS_MAX);
      // L'écran calme n'a **aucune** exemption à faire valoir : rien n'y attend
      // d'arbitrage, donc tout ce qu'il montre compte. Sans ce second plafond,
      // un bloc qui n'apparaîtrait qu'à file vide échapperait au comptage.
      expect(calme.corps.length, recit).toBeLessThanOrEqual(BLOCS_MAX);
    });
  }
});

// --- 3. Ce que la troisième place doit à la mise en page --------------------

describe("la colonne de propriétés de /couts", () => {
  /**
   * ⚠ Ce contrôle est une **déclaration**, pas une mesure : jsdom ne calcule ni
   * hauteur ni défilement (#308), et le pixel appartient au skill
   * `/banc-mise-en-page`. Ce qu'il garde est la chaîne de classes, comme le
   * plancher de 24 px des cibles dans `a11y.test.tsx`.
   *
   * Ce qu'il empêche : une colonne **collante et non bornée**. La troisième
   * place « s'allonge sans plafond » — c'est sa définition —, or une surface
   * collante plus haute que la fenêtre voit son bas rester définitivement sous
   * le pli : aucun défilement ne le ramène, puisque c'est le défilement qui la
   * fige. C'est la classe de bug de #306, et la règle des trois places
   * l'invite par construction. Les deux utilitaires vont donc ensemble.
   */
  it("borne sa hauteur partout où elle est collante", () => {
    const source = readFileSync(
      path.join(
        path.dirname(fileURLToPath(import.meta.url)),
        "../app/couts/page.tsx",
      ),
      "utf8",
    );
    expect(source).toContain("@4xl:sticky");
    expect(source).toContain("@4xl:max-h-[calc(100dvh-6rem)]");
    expect(source).toContain("@4xl:overflow-y-auto");
  });
});

// --- 4. L'arbitrage se prouve ----------------------------------------------

describe("les blocs d'arbitrage (docs/30 §4.1)", () => {
  beforeEach(() => {
    marquerGuideVu();
    poserProjetActif();
  });

  const TABLEAU_DE_BORD = ECRANS[0];

  it("sont bien là quand une décision attend, sur le tableau de bord", async () => {
    // Le tableau de bord est le seul écran qui porte les trois. Sans ce
    // contrôle, l'exemption du §4.1 pourrait se vérifier sur un écran qui n'en
    // a jamais eu — un ✓ sur une question jamais posée.
    peuplerEtat();
    await monterEcran(TABLEAU_DE_BORD);
    const corps = placesDe(contenuPrincipal()).corps;
    expect(corps).toContain("Briefs en attente");
    expect(corps).toContain("Validations en attente");
  });

  it("disparaissent quand la file est vide, et rendent leur place", async () => {
    peuplerEtatSansArbitrage();
    await monterEcran(TABLEAU_DE_BORD);
    const places = placesDe(contenuPrincipal());
    expect(places.corps).not.toContain("Briefs en attente");
    expect(places.corps).not.toContain("Validations en attente");
    expect(places.corps).not.toContain(TITRE_RUNS_IMMOBILES);
    // Et ce qui reste tient tout seul dans le plafond : c'est l'acquis de #191
    // qu'il s'agissait de protéger, et que #476 n'a pas défait.
    expect(places.corps.length, places.corps.join(" · ")).toBeLessThanOrEqual(
      BLOCS_MAX,
    );
  });

  it("laisse le tableau de bord sur son écran, pas sur `PosteVide`", async () => {
    // La garde du harnais : vider **tout** l'état ferait basculer la page sur
    // « rien à regarder » (#186/#281), et on mesurerait alors un écran qui n'est
    // pas celui de la règle. Le projet doit donc rester peuplé par ailleurs.
    peuplerEtatSansArbitrage();
    await monterEcran(TABLEAU_DE_BORD);
    expect(
      screen.getByRole("region", { name: "Indicateurs de tête" }),
    ).toBeInTheDocument();
  });
});

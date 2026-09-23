/**
 * **La frontière shell / écran, rendue opposable** (#929, lot 11 de #921 —
 * docs/35 §3.4).
 *
 * C'est le livrable de ce lot, et il a un précédent exact. #539 a écrit la
 * règle des trois places sous forme de comptage (`sobriete.test.tsx`) parce
 * qu'une règle qu'aucune machine ne vérifie ne tient pas : la doc du langage
 * visuel existait déjà, détaillée, et 18 recopies de carte sont passées quand
 * même (docs/30 §3.6). Le chantier « L'atelier » ouvre une porte que cette
 * règle-là ne ferme pas, et docs/35 §3.4 la nomme :
 *
 * > Une troisième zone du shell rendue comme une `<aside>` serait exactement
 * > cette sortie de secours : un écran plein pourrait y ranger son quatrième
 * > bloc.
 *
 * Le comptage de #539 ne recense que `#contenu-principal`. C'est **juste** — une
 * zone du shell n'est pas un bloc de plus dans l'écran — et c'est précisément ce
 * qui fait du shell le seul endroit du DOM où un bloc d'écran ne serait compté
 * nulle part. Ce fichier tient l'autre moitié, en trois temps :
 *
 * 1. **le plafond ne se relève pas** — les deux nombres sont confrontés au texte
 *    de docs/30 §4.1, donc les changer oblige à réécrire la règle ;
 * 2. **le shell a ses zones, l'écran n'en pose aucune** — ce qu'un écran ajoute
 *    hors de `<main>` est comparé à ce que le shell rend **seul**, sur le même
 *    chemin et dans le même état. L'écart doit être vide ;
 * 3. **ouvrir la troisième zone ne rend aucune place à l'écran** — les places
 *    comptées dans `#contenu-principal` sont les mêmes, colonne ouverte ou
 *    fermée.
 *
 * ## Une seule sonde, partagée
 *
 * Le comptage vient de `./places`, celui-là même que `sobriete.test.tsx`
 * emploie et **prouve** sur un échantillon fautif. Deux sondes recopiées
 * seraient ici pires qu'ailleurs : la frontière n'a de sens que si les deux
 * côtés se comptent de la même façon, et une divergence d'un caractère rendrait
 * « conforme » un bloc rangé dans le shell.
 *
 * ## Ce qui n'est pas de ce ressort
 *
 * Les pixels (#308, skill `/banc-mise-en-page`) : jsdom n'en calcule aucun. Ce
 * qui est compté ici est ce que la règle plafonne — des blocs — et leur **côté**
 * de la frontière.
 */

import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { createPortal } from "react-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  ColonneConversation,
  ID_COLONNE_CONVERSATION,
} from "@/components/ColonneConversation";
import { Shell } from "@/components/Shell";
import { marquerGuideVu } from "@/lib/guide";
import { MENU } from "@/lib/navigation";
import { ecrireConversationOuverte } from "@/lib/preferences";

import { poserChemin, poserProjetActif } from "./aides";
import { ECRANS, monterEcran, peuplerEtat } from "./ecrans";
import { poserSante, SANTE_OK } from "./ecrans-reseau";
import {
  BLOCS_MAX,
  CHIFFRES_MAX,
  contenuPrincipal,
  placesDe,
  type Places,
} from "./places";

// --- Le réseau, débranché comme pour les deux autres filets d'écran ---------

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

// ===========================================================================
// 1. Le plafond ne se relève jamais
// ===========================================================================

const DOCS_30 = path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  "../../../docs/30-cible-visuelle-control-tower.md",
);

/**
 * Les deux plafonds, **lus dans la règle** plutôt que recopiés.
 *
 * `texte` est le document entier ; la section §4.1 en est découpée d'abord, pour
 * qu'une citation de la règle ailleurs dans le document (§4.2 la commente, §4.3
 * la met en scène) ne puisse pas servir de source.
 */
function plafondsDeLaRegle(texte: string): { chiffres: number; blocs: number } {
  const section = /^### 4\.1 [^\n]*\n([\s\S]*?)(?=^### )/m.exec(texte);
  if (section === null)
    throw new Error("docs/30 n'a plus de § 4.1 « La règle des trois places »");
  const corps = section[1];
  const chiffres = /au plus \*\*(\d+) chiffres\*\*/.exec(corps);
  const blocs = /au plus \*\*(\d+) blocs de plein format\*\*/.exec(corps);
  if (chiffres === null || blocs === null)
    throw new Error("la règle des trois places ne chiffre plus ses plafonds");
  return { chiffres: Number(chiffres[1]), blocs: Number(blocs[1]) };
}

describe("les plafonds de la règle des trois places", () => {
  // La moitié qui prouve, et sans elle la moitié utile ne vaudrait rien : une
  // extraction qui rendrait 4 et 3 quoi qu'elle lise serait d'accord avec les
  // constantes pour toujours, y compris le jour où la règle aurait changé.
  it("lit les nombres écrits, et pas ceux qu'on attend", () => {
    const faux = [
      "### 4.1 La règle des trois places",
      "",
      "> 1. **Le bandeau de tête** — au plus **9 chiffres**, et rien d'autre.",
      "> 2. **Le corps** — au plus **7 blocs de plein format**, plus l'arbitrage.",
      "",
      "### 4.2 Pourquoi elle est opposable",
      "",
    ].join("\n");
    expect(plafondsDeLaRegle(faux)).toEqual({ chiffres: 9, blocs: 7 });
  });

  it("refuse de deviner quand la règle ne chiffre plus rien", () => {
    const muet = [
      "### 4.1 La règle des trois places",
      "",
      "> Tout ce qu'un écran affiche occupe l'une de trois places.",
      "",
      "### 4.2 Pourquoi elle est opposable",
      "",
    ].join("\n");
    expect(() => plafondsDeLaRegle(muet)).toThrow(/ne chiffre plus/);
  });

  /**
   * ⚠ **C'est ici qu'on ne relève pas `BLOCS_MAX`** (docs/35 §3.4, CLAUDE.md).
   *
   * Une refonte qui ne passe pas le comptage a deux issues honnêtes — épurer
   * l'écran, ou ranger ce qui déborde dans une colonne de propriétés — et une
   * troisième qui ne l'est pas : monter la constante d'une unité, dans un
   * fichier de test que personne ne relit avec la règle sous les yeux. Ce
   * contrôle la ferme : le plafond du test est celui que docs/30 §4.1 écrit, et
   * le déplacer demande de réécrire la règle en toutes lettres, là où elle se
   * discute.
   */
  it("sont ceux que docs/30 §4.1 écrit, et le test ne peut pas les déplacer seul", () => {
    const regle = plafondsDeLaRegle(readFileSync(DOCS_30, "utf8"));
    expect(
      CHIFFRES_MAX,
      "le bandeau de tête ne se relève pas dans un fichier de test",
    ).toBe(regle.chiffres);
    expect(BLOCS_MAX, "le corps ne se relève pas dans un fichier de test").toBe(
      regle.blocs,
    );
  });
});

// ===========================================================================
// 2. Le shell a ses zones, l'écran n'en pose aucune
// ===========================================================================

/**
 * Le nom accessible de la troisième zone (#925) — `ColonneConversation` le tire
 * de son titre affiché, on le tire du composant plutôt que de le réécrire.
 */
const ZONE_CONVERSATION = "Conversation";

/**
 * Ce qui occupe une place **hors de l'écran**, c'est-à-dire dans le shell.
 *
 * La racine est `document.body` et non le conteneur de rendu : un écran qui
 * voudrait s'échapper le ferait par un portail, qui sort du conteneur autant
 * que de `<main>`. Le sous-arbre exclu est `#contenu-principal` — ce que
 * `sobriete.test.tsx` compte, et qu'il n'y a pas lieu de compter deux fois.
 */
function placesHorsEcran(): Places {
  return placesDe(document.body, contenuPrincipal());
}

/** Un contenu d'écran inerte : le shell rendu seul, sans rien à recenser. */
function EcranInerte() {
  return <p>contenu de la page</p>;
}

/**
 * Ce que le shell rend **de lui-même** sur ce chemin et dans cet état — la
 * référence à laquelle chaque écran sera comparé.
 *
 * Mesurée plutôt que recopiée, et mesurée **par chemin** : le shell replie sa
 * colonne sur `/chat` (une seule conversation à l'écran, #926), si bien qu'une
 * liste écrite en dur devrait porter ce cas particulier — c'est-à-dire recopier
 * une décision qui vit déjà dans `Shell.tsx`.
 */
async function zonesDuShell(chemin: string, ouverte: boolean): Promise<Places> {
  ecrireConversationOuverte(ouverte);
  poserChemin(chemin);
  render(
    <Shell>
      <EcranInerte />
    </Shell>,
  );
  await screen.findByText("contenu de la page");
  await attendreLaColonne(ouverte && chemin !== "/chat");
  return placesHorsEcran();
}

/**
 * La colonne est restituée par un effet **différé d'un tick** (#925) : sans
 * cette attente, le relevé serait pris avant qu'elle ne paraisse, et la
 * comparaison porterait sur deux états différents sans que rien ne le dise.
 */
async function attendreLaColonne(attendue: boolean): Promise<void> {
  await waitFor(() => {
    const colonne = screen.queryByRole("complementary", {
      name: ZONE_CONVERSATION,
    });
    expect(colonne === null).toBe(!attendue);
  });
}

describe("la sonde de frontière", () => {
  beforeEach(() => {
    marquerGuideVu();
    poserProjetActif();
  });

  /**
   * La moitié qui prouve : un écran qui range un bloc **hors** de son contenu
   * principal est vu. Le portail est la forme réaliste de l'évasion — c'est
   * ainsi qu'une page s'installerait dans une zone du shell — et c'est aussi
   * celle qu'un comptage branché sur le conteneur de rendu manquerait.
   */
  it("voit un bloc qu'un écran range hors de son contenu principal", async () => {
    poserChemin("/");
    render(
      <Shell>
        <EcranEvade />
      </Shell>,
    );
    await screen.findByText("contenu de la page");
    const hors = placesHorsEcran();
    expect(hors.corps).toContain("Bloc évadé");
    // …et il n'est **pas** dans l'écran : c'est exactement ce qui le rendrait
    // invisible au comptage de `sobriete.test.tsx`.
    expect(placesDe(contenuPrincipal()).corps).not.toContain("Bloc évadé");
  });

  it("voit aussi une colonne de propriétés rangée hors de l'écran", async () => {
    // L'autre forme, celle que docs/35 §3.4 nomme : la troisième place est la
    // seule sans plafond, donc c'est en `<aside>` qu'un bloc de trop voyagerait.
    poserChemin("/");
    render(
      <Shell>
        <EcranEvade balise="aside" nom="Colonne évadée" />
      </Shell>,
    );
    await screen.findByText("contenu de la page");
    expect(placesHorsEcran().colonnes).toContain("Colonne évadée");
  });

  it("ne voit rien de l'écran lui-même", async () => {
    // Le pendant : un bloc rangé là où il doit l'être ne compte pas comme une
    // évasion. Sans ce contrôle, une exclusion mal branchée ferait rougir tous
    // les écrans pour une raison fausse.
    poserChemin("/");
    render(
      <Shell>
        <section aria-label="Bloc en règle">dans l&apos;écran</section>
      </Shell>,
    );
    await screen.findByRole("region", { name: "Bloc en règle" });
    expect(placesHorsEcran().corps).not.toContain("Bloc en règle");
  });
});

/** Un écran fautif : il pose son bloc dans le shell, par-dessus la frontière. */
function EcranEvade({
  balise = "section",
  nom = "Bloc évadé",
}: {
  balise?: "section" | "aside";
  nom?: string;
}) {
  const Balise = balise;
  return (
    <>
      <p>contenu de la page</p>
      {createPortal(<Balise aria-label={nom}>évadé</Balise>, document.body)}
    </>
  );
}

describe("les zones du shell (docs/35 §3)", () => {
  beforeEach(() => {
    marquerGuideVu();
    poserProjetActif();
    peuplerEtat();
  });

  /**
   * L'inventaire **épinglé**, et c'est la moitié que les relevés par écran ne
   * donnent pas : eux comparent le shell à lui-même, donc ils resteraient verts
   * si le shell gagnait une quatrième zone. Celui-ci dit ce que le shell a le
   * droit de poser — une zone de plus se lit alors dans le diff de ce fichier,
   * où elle se discute, plutôt que de s'ajouter en silence.
   *
   * ⚠ **La ligne qui porte la frontière est la dernière** : le shell n'occupe
   * ni le corps, ni le bandeau de tête. Ses zones sont des `<aside>` — le rail
   * de navigation et la colonne de conversation —, c'est-à-dire des surfaces
   * qui bordent l'écran et n'en sont pas. Le jour où le shell poserait une
   * `<section>`, la question « à quelle place compte-t-elle ? » se poserait
   * pour de bon, et c'est ce jour-là qu'on veut voir rougir.
   */
  it("pose deux zones, et aucune n'occupe une place de l'écran", async () => {
    const zones = await zonesDuShell("/", true);
    // Le rail de navigation : une `<aside>` sans nom propre, parce que la
    // `<nav>` qu'elle contient le porte déjà (`BarreLaterale`). Lui en donner
    // un second ferait deux repères pour une seule zone.
    expect(zones.anonymes).toEqual(["<aside>"]);
    // La troisième zone (#925), nommée par son titre affiché. Elle est dans cet
    // inventaire-ci, donc **hors** de `#contenu-principal` : c'est la phrase de
    // docs/35 §3.4 dite par le comptage, et non par un commentaire.
    expect(zones.colonnes).toEqual([ZONE_CONVERSATION]);
    expect(
      contenuPrincipal().contains(
        document.getElementById(ID_COLONNE_CONVERSATION),
      ),
    ).toBe(false);
    expect(zones.corps).toEqual([]);
    expect(zones.chiffres).toEqual([]);
  });

  it("garde le même inventaire quand la conversation est repliée", async () => {
    // La colonne reste **dans le DOM** une fois fermée (#925 : l'`aria-controls`
    // de son bouton doit désigner un élément qui existe), simplement `hidden`.
    // L'inventaire ne bouge donc pas d'un état à l'autre, et c'est ce qui rend
    // la comparaison par écran lisible : ce qui change entre les deux relevés
    // vient de l'écran, jamais du shell.
    const zones = await zonesDuShell("/", false);
    expect(zones.anonymes).toEqual(["<aside>"]);
    expect(zones.colonnes).toEqual([ZONE_CONVERSATION]);
    expect(zones.corps).toEqual([]);
    expect(zones.chiffres).toEqual([]);
    expect(document.getElementById(ID_COLONNE_CONVERSATION)).toHaveAttribute(
      "hidden",
    );
  });

  it("dit la perte du magasin hors de l'écran, sans y prendre de place (#1206)", async () => {
    // Le bandeau système (variante C de #1206) est une zone du **shell** qui
    // n'existe que tant que la panne est vraie. Ce qu'on épingle : il vit hors
    // de `#contenu-principal` — l'écran n'en porte pas un bloc de plus — et il
    // ne pose ni `<section>` ni `<aside>` : l'inventaire ci-dessus ne bouge pas.
    poserSante({
      statut: "degrade",
      magasin: {
        disponible: false,
        lieu: "Redis, redis://127.0.0.1:6379/0",
        titre: "Magasin des événements injoignable",
        motif: "Error 10061. Rien de ce que l'écran montrerait n'est à jour",
        geste: "relancer Redis, l'API reprend seule",
        commande: "docker compose -f infra/docker-compose.yml up -d redis",
      },
    });
    try {
      const zones = await zonesDuShell("/", true);
      const bandeau = (
        await screen.findByText("Magasin des événements injoignable")
      ).closest('[role="alert"]');
      expect(bandeau).not.toBeNull();
      expect(contenuPrincipal().contains(bandeau)).toBe(false);
      expect(zones.anonymes).toEqual(["<aside>"]);
      expect(zones.colonnes).toEqual([ZONE_CONVERSATION]);
      expect(zones.corps).toEqual([]);
      expect(zones.chiffres).toEqual([]);
    } finally {
      poserSante(SANTE_OK);
    }
  });

  it("l'aside sans nom est bien le rail de navigation", async () => {
    // Sans ce contrôle, « une `<aside>` anonyme » ci-dessus serait une case
    // vide où n'importe quelle zone future pourrait se glisser sans être vue.
    await zonesDuShell("/", false);
    const rail = [...document.querySelectorAll("aside")].find(
      (bloc) => bloc.id !== ID_COLONNE_CONVERSATION,
    );
    expect(rail, "le shell n'a plus qu'une seule aside").toBeDefined();
    expect(
      rail?.querySelector('nav[aria-label="Navigation principale"]'),
    ).not.toBeNull();
  });

  it("nomme sa troisième zone comme le composant qui la rend", () => {
    // Le nom attendu par l'inventaire vient du titre affiché de la colonne
    // (`aria-labelledby`) : si `ColonneConversation` le change, l'inventaire
    // ci-dessus rougirait pour une raison qui n'est pas une évasion. Montée
    // **fermée** : ouverte, elle monte le fil et sa WebSocket, ce qui n'apprend
    // rien de plus sur son nom.
    const { container } = render(
      <ColonneConversation ouverte={false} fermer={() => {}} />,
    );
    expect(placesDe(container).colonnes).toEqual([ZONE_CONVERSATION]);
  });
});

// ===========================================================================
// 3. Les écrans, des deux côtés de la frontière
// ===========================================================================

/** Ce qu'un relevé donne à lire quand la frontière a bougé. */
function raconter(quoi: string, places: Places): string {
  return [
    ``,
    `${quoi} — ${places.chiffres.length} chiffre(s), ${places.corps.length} bloc(s), ` +
      `${places.colonnes.length} colonne(s)`,
    ...places.corps.map((nom) => `  bloc         · ${nom}`),
    ...places.colonnes.map((nom) => `  colonne      · ${nom}`),
    ...places.anonymes.map((balise) => `  SANS NOM     · ${balise}`),
    ``,
  ].join("\n");
}

describe("les écrans du menu face à la frontière", () => {
  beforeEach(() => {
    marquerGuideVu();
    poserProjetActif();
    // Files pleines : l'écran le plus chargé qu'on puisse voir, donc celui qui
    // aurait le plus à gagner à ranger un bloc dans le shell. L'état calme est
    // l'affaire de `sobriete.test.tsx`, qui s'en sert pour prouver l'arbitrage.
    peuplerEtat();
  });

  it("recense exactement les écrans du menu", () => {
    // Dérivé, jamais recopié — même contrôle que les deux autres filets
    // d'écran (`a11y`, `sobriete`) : une page ajoutée au menu sans cas de
    // recensement échapperait sinon à la frontière en silence.
    expect(ECRANS.map((e) => e.href)).toEqual(MENU.map((e) => e.href));
  });

  for (const ecran of ECRANS) {
    it(`ne range rien dans le shell depuis ${ecran.href}`, async () => {
      for (const ouverte of [false, true]) {
        const attendu = await zonesDuShell(ecran.href, ouverte);
        cleanup();

        ecrireConversationOuverte(ouverte);
        await monterEcran(ecran);
        await attendreLaColonne(ouverte && ecran.href !== "/chat");
        const obtenu = placesHorsEcran();
        const recit =
          `colonne ${ouverte ? "ouverte" : "fermée"}` +
          raconter("le shell seul", attendu) +
          raconter(ecran.href, obtenu);
        cleanup();

        // L'écart est **vide** : tout ce que l'écran rend est dans son contenu
        // principal, donc compté par la règle des trois places. Un bloc de plus
        // ici serait un bloc que plus rien ne plafonne.
        expect(obtenu.corps, recit).toEqual(attendu.corps);
        expect(obtenu.colonnes, recit).toEqual(attendu.colonnes);
        expect(obtenu.chiffres, recit).toEqual(attendu.chiffres);
        expect(obtenu.anonymes, recit).toEqual(attendu.anonymes);
      }
    });

    it(`ne gagne aucune place quand la conversation s'ouvre sur ${ecran.href}`, async () => {
      // L'autre sens de la sortie de secours, et le plus insidieux : un écran
      // qui déplacerait un bloc vers la colonne quand elle est ouverte tiendrait
      // le plafond dans l'état mesuré par `sobriete.test.tsx` (colonne fermée)
      // et le dépasserait à l'usage. Ce que l'écran doit à la règle ne dépend
      // pas d'une préférence d'affichage.
      ecrireConversationOuverte(false);
      await monterEcran(ecran);
      const ferme = placesDe(contenuPrincipal());
      cleanup();

      ecrireConversationOuverte(true);
      await monterEcran(ecran);
      await attendreLaColonne(ecran.href !== "/chat");
      const ouvert = placesDe(contenuPrincipal());

      const recit =
        raconter(`${ecran.href} — colonne fermée`, ferme) +
        raconter(`${ecran.href} — colonne ouverte`, ouvert);
      expect(ouvert.corps, recit).toEqual(ferme.corps);
      expect(ouvert.colonnes, recit).toEqual(ferme.colonnes);
      expect(ouvert.chiffres, recit).toEqual(ferme.chiffres);
      // Et la colonne de la conversation n'est **jamais** la colonne de
      // propriétés de l'écran : c'est la phrase de docs/35 §3.4, comptée.
      expect(ouvert.colonnes, recit).not.toContain(ZONE_CONVERSATION);
    });
  }
});

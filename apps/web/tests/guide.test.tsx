/**
 * Lot 6 de la refonte UX (#122) : le guide de prise en main interactif.
 *
 * Ce qui compte ici tient en quatre promesses, toutes cassables en silence :
 *
 * - **la visite ne se déclenche qu'une fois**, à la première visite, et jamais
 *   plus ensuite — même quittée en route. Le contraire (une visite qui revient
 *   à chaque chargement) est le défaut classique de ce genre de composant ;
 * - **elle reste ancrée sur du réel** : chaque étape désigne un élément que le
 *   shell rend vraiment, via un attribut `data-guide`. Retirer cet attribut
 *   d'un composant ne casserait ni le lint ni le build — seulement la visite ;
 * - **on peut en sortir**, au clavier comme à la souris ;
 * - **elle connaît les écrans qui existent** (#940). C'est la promesse que #122
 *   n'avait pas : sa visite énumérait six sections quand le menu en portait
 *   neuf, et rien ne protestait. La frontière visite ↔ `MENU` est donc gardée
 *   **dans les deux sens** — c'est la méthode de `tests/test_retex_utilisateur.py`
 *   sur les écrans du parcours, reprise ici —, et le parseur est **prouvé sur un
 *   échantillon fautif** avant d'être lâché sur le vrai menu (règle de #534 et de
 *   #830 : un motif mal branché rend un ✓ sur une question jamais posée).
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { GuidePriseEnMain } from "@/components/GuidePriseEnMain";
import { MenuAide } from "@/components/MenuAide";
import {
  CLE_GUIDE_VU,
  ETAPES_GUIDE,
  ETAPE_DU_MENU,
  TRAITEMENT_DES_ECRANS,
  type EtapeGuide,
  ecartsDeVisite,
  ecouterLancementGuide,
  lancerGuide,
  lireGuideVu,
  marquerGuideVu,
} from "@/lib/guide";
import { MENU, entreeCourante, entreeParLibelle } from "@/lib/navigation";

import { navigations } from "./aides";

/**
 * Un composeur factice, dans le conteneur du test (donc démonté avec lui), avec
 * la géométrie que jsdom ne donne pas : `trouverAncre` écarte les éléments de
 * taille nulle, et tout ici en a une.
 */
function monterComposeur(conteneur: HTMLElement): HTMLTextAreaElement {
  const hote = document.createElement("div");
  hote.setAttribute("data-guide", "composeur");
  hote.getBoundingClientRect = () =>
    ({
      top: 0,
      left: 0,
      right: 400,
      bottom: 80,
      width: 400,
      height: 80,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    }) as DOMRect;
  const champ = document.createElement("textarea");
  hote.appendChild(champ);
  conteneur.appendChild(hote);
  return champ;
}

/** L'étape qui porte l'inventaire du menu — elle existe, sinon il n'y a plus rien à garder. */
const etapeDuMenu = (): EtapeGuide => {
  const etape = ETAPES_GUIDE.find((autre) => autre.id === ETAPE_DU_MENU);
  if (etape === undefined)
    throw new Error(`l'étape « ${ETAPE_DU_MENU} » a disparu de la visite`);
  return etape;
};

/** La visite s'ouvre après un délai de politesse — on l'attend. */
const attendreVisite = () =>
  waitFor(() => expect(screen.getByRole("dialog")).toBeInTheDocument(), {
    timeout: 2000,
  });

describe("le contenu de la visite (lib/guide)", () => {
  it("enchaîne des étapes identifiées, titrées et expliquées", () => {
    expect(ETAPES_GUIDE.length).toBeGreaterThan(0);
    for (const etape of ETAPES_GUIDE) {
      expect(etape.id).not.toBe("");
      expect(etape.titre).not.toBe("");
      expect(etape.texte).not.toBe("");
    }
  });

  it("donne un identifiant unique à chaque étape", () => {
    // L'`id` sert de clé de rendu : un doublon ferait dérailler la liste de
    // pastilles d'avancement.
    const ids = ETAPES_GUIDE.map((etape) => etape.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("ancre chaque étape sur au moins une cible", () => {
    for (const etape of ETAPES_GUIDE) {
      expect(etape.ancres.length).toBeGreaterThan(0);
      for (const ancre of etape.ancres) {
        expect(ancre).toMatch(/^\[data-guide="[a-z-]+"\]$/);
      }
    }
  });

  it("ne vise que des ancres que le code pose vraiment", async () => {
    // Le contrat entre `lib/guide` et les composants : un `data-guide` retiré
    // d'un composant laisse une étape sans cible, sans que rien ne proteste.
    const { readFileSync, readdirSync } = await import("node:fs");
    const path = await import("node:path");
    const { fileURLToPath } = await import("node:url");
    const racine = path.join(
      path.dirname(fileURLToPath(import.meta.url)),
      "..",
    );

    const sources: string[] = [];
    const parcourir = (dossier: string) => {
      for (const entree of readdirSync(dossier, { withFileTypes: true })) {
        const complet = path.join(dossier, entree.name);
        if (entree.isDirectory()) parcourir(complet);
        else if (/\.tsx?$/.test(entree.name))
          sources.push(readFileSync(complet, "utf8"));
      }
    };
    parcourir(path.join(racine, "components"));
    parcourir(path.join(racine, "app"));
    const code = sources.join("\n");

    for (const etape of ETAPES_GUIDE) {
      for (const ancre of etape.ancres) {
        const nom = ancre.replace(/^\[data-guide="|"\]$/g, "");
        expect(
          code.includes(`data-guide="${nom}"`),
          `l'ancre « ${nom} » (étape « ${etape.id} ») n'est posée par aucun composant`,
        ).toBe(true);
      }
    }
  });

  it("ne présente que des pages que le menu porte encore", () => {
    // Le pendant de l'ancre pour la *page* d'une étape (#193) : la visite
    // navigue d'elle-même vers `chemin`, et la navigation v2 (#189) a déplacé
    // des pages — `/catalogue` et `/playbooks` ne sont plus que des
    // redirections. Une étape qui en viserait une ferait rebondir la visite
    // hors de la page qu'elle prétend montrer, sans que rien ne proteste.
    for (const etape of ETAPES_GUIDE) {
      if (etape.chemin === undefined) continue;
      expect(
        entreeCourante(etape.chemin),
        `l'étape « ${etape.id} » présente « ${etape.chemin} », qui n'est plus au menu`,
      ).toBeDefined();
    }
  });

  it("fait démarrer la visite sur la présentation générale", () => {
    expect(ETAPES_GUIDE[0].id).toBe("bienvenue");
  });

  it("termine sur le geste qui démarre un run, et y rend la main", () => {
    // #940 renverse la fin de #122 : la visite ne se terminait pas sur le
    // produit mais sur le bouton qui la relance, ce qui laissait l'utilisateur
    // devant l'aide au lieu du chat. Elle finit désormais sur le composeur, et
    // « Terminer » y pose le curseur (`rendreLaMain`) — sans quoi « mène à un
    // premier objectif composé » ne serait qu'une phrase.
    const fin = ETAPES_GUIDE[ETAPES_GUIDE.length - 1];
    expect(fin.ancres).toContain('[data-guide="composeur"]');
    expect(fin.rendreLaMain).toBe(true);
    expect(fin.chemin).toBe(entreeParLibelle("Chat")?.href);
  });

  it("nomme le chat comme la porte d'entrée", () => {
    // Premier critère de #940 : aucune des sept étapes de #122 ne le faisait,
    // alors que c'est la seule porte depuis #470/#484.
    const porte = ETAPES_GUIDE.find((etape) => etape.ecran === "Chat");
    expect(porte, "aucune étape ne présente le chat").toBeDefined();
    expect(`${porte?.titre} ${porte?.texte}`).toMatch(/porte d'entrée/i);
  });

  it("n'écrit aucun chemin en dur : une étape déclare son écran", () => {
    // Règle de #191, tenue ici par construction (`monter`) : le chemin vient du
    // menu. Une étape qui porterait un chemin sans écran l'aurait écrit à la
    // main, et ne suivrait pas un déménagement de page.
    for (const etape of ETAPES_GUIDE) {
      if (etape.chemin === undefined) continue;
      expect(
        etape.ecran,
        `l'étape « ${etape.id} » ouvre « ${etape.chemin} » sans dire quel écran c'est`,
      ).toBeDefined();
      expect(etape.chemin).toBe(entreeParLibelle(etape.ecran as string)?.href);
    }
  });
});

// =============================================================================
// La frontière visite ↔ menu (#940) — le troisième critère, et le livrable
// =============================================================================

/**
 * Le menu fautif : une entrée **ajoutée** (« Rapports »), une entrée du
 * traitement **retirée** du menu (« Journal »).
 */
const MENU_FAUTIF = [
  { libelle: "Tableau de bord" },
  { libelle: "Chat" },
  { libelle: "Rapports" },
];

const TRAITEMENT_FAUTIF = {
  "Tableau de bord": "etape",
  Chat: "etape",
  Journal: "cite",
} as const;

const ETAPES_FAUTIVES = [
  { id: "a", titre: "a", texte: "a", ancres: [], ecran: "Tableau de bord" },
  { id: "b", titre: "b", texte: "b", ancres: [], ecran: "Intégrations" },
] as EtapeGuide[];

describe("la frontière entre la visite et le menu (lib/guide)", () => {
  it("voit les cinq écarts sur un échantillon fautif", () => {
    // ⚠ Le contre-exemple d'abord : sans lui, un `ecartsDeVisite` qui rendrait
    // cinq listes vides passerait tous les contrôles ci-dessous en disant
    // « rien à signaler » sur une question jamais posée.
    const ecarts = ecartsDeVisite(
      MENU_FAUTIF,
      TRAITEMENT_FAUTIF,
      ETAPES_FAUTIVES,
      "Tableau de bord et Chat",
    );
    expect(ecarts.sansTraitement, "une entrée ajoutée au menu est vue").toEqual([
      "Rapports",
    ]);
    expect(
      ecarts.traitementOrphelin,
      "une décision sur un écran disparu est vue",
    ).toEqual(["Journal"]);
    expect(ecarts.promisSansEtape, "une promesse non tenue est vue").toEqual([
      "Chat",
    ]);
    expect(
      ecarts.etapesHorsMenu,
      "une étape qui ouvre un écran absent du menu est vue",
    ).toEqual(["Intégrations"]);
    expect(ecarts.nonNommes, "une entrée omise de l'énumération est vue").toEqual(
      ["Rapports"],
    );
  });

  it("ne signale rien quand la visite suit le menu", () => {
    // Le pendant du précédent : les mêmes contrôles, sur un échantillon sain,
    // ne doivent rien inventer.
    const ecarts = ecartsDeVisite(
      [{ libelle: "Chat" }],
      { Chat: "etape" },
      [{ id: "a", titre: "a", texte: "a", ancres: [], ecran: "Chat" }],
      "Chat",
    );
    expect(ecarts).toEqual({
      sansTraitement: [],
      traitementOrphelin: [],
      promisSansEtape: [],
      etapesHorsMenu: [],
      nonNommes: [],
    });
  });

  it("suit le menu réel, dans les deux sens", () => {
    // ⚠ C'est le troisième critère de #940, et le seul qui empêche la dérive de
    // se reproduire : le jour où une entrée de menu est ajoutée ou retirée, ce
    // qui ne suit pas rougit ici. Les deux premiers critères réparent la visite
    // d'aujourd'hui ; celui-ci la tient.
    expect(MENU.length, "le parseur ne voit plus le menu").toBeGreaterThan(2);
    const ecarts = ecartsDeVisite(
      MENU,
      TRAITEMENT_DES_ECRANS,
      ETAPES_GUIDE,
      etapeDuMenu().texte,
    );
    expect(
      ecarts.sansTraitement,
      "écrans du menu sans décision dans TRAITEMENT_DES_ECRANS — une étape à eux, ou seulement nommés ?",
    ).toEqual([]);
    expect(
      ecarts.traitementOrphelin,
      "décisions portant sur des écrans que le menu ne porte plus",
    ).toEqual([]);
    expect(
      ecarts.promisSansEtape,
      "écrans déclarés « etape » qu'aucune étape n'ouvre",
    ).toEqual([]);
    expect(
      ecarts.etapesHorsMenu,
      "étapes qui présentent un écran absent du menu",
    ).toEqual([]);
    expect(
      ecarts.nonNommes,
      `écrans du menu que l'étape « ${ETAPE_DU_MENU} » n'énumère pas`,
    ).toEqual([]);
  });

  it("garde l'inventaire du menu dans une seule étape", () => {
    // Le parti pris 1 de la veille : les étapes vont au chemin du premier run,
    // pas à l'inventaire. Deux étapes qui énuméreraient le menu, c'est deux
    // endroits à tenir à jour — et donc un qui dérivera.
    const enumerations = ETAPES_GUIDE.filter((etape) =>
      MENU.every((entree) => etape.texte.includes(entree.libelle)),
    );
    expect(enumerations.map((etape) => etape.id)).toEqual([ETAPE_DU_MENU]);
  });

  it("tient la visite sous le plafond de longueur", () => {
    // Parti pris 4, d'après la procédure pas à pas de VS Code pour le web : on
    // doit pouvoir lire combien il reste. Au-delà, la rangée de pastilles cesse
    // d'être lisible dans une carte de 320 px — mesuré sur la variante à onze
    // étapes, écartée pour cette raison entre autres.
    expect(ETAPES_GUIDE.length).toBeLessThanOrEqual(8);
  });
});

describe("la mémoire de la visite (lib/guide)", () => {
  it("est neuve à la première visite", () => {
    expect(lireGuideVu()).toBe(false);
  });

  it("retient qu'elle a été vue", () => {
    marquerGuideVu();
    expect(window.localStorage.getItem(CLE_GUIDE_VU)).toBe("1");
    expect(lireGuideVu()).toBe(true);
  });

  it("se tait quand le stockage est indisponible", () => {
    // Sans persistance, répondre « pas encore vue » relancerait la visite à
    // CHAQUE chargement de page : on répond « vue », elle reste accessible
    // depuis le menu d'aide.
    const vrai = Object.getOwnPropertyDescriptor(window, "localStorage");
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      get() {
        throw new Error("stockage interdit");
      },
    });
    expect(lireGuideVu()).toBe(true);
    expect(() => marquerGuideVu()).not.toThrow();
    if (vrai) Object.defineProperty(window, "localStorage", vrai);
  });

  it("porte les demandes de relance à qui veut les entendre", () => {
    let relances = 0;
    const detacher = ecouterLancementGuide(() => (relances += 1));
    lancerGuide();
    detacher();
    lancerGuide();
    expect(relances).toBe(1);
  });
});

describe("la visite (GuidePriseEnMain)", () => {
  it("s'ouvre d'elle-même à la première visite", async () => {
    render(<GuidePriseEnMain />);
    await attendreVisite();
    expect(screen.getByText(ETAPES_GUIDE[0].titre)).toBeInTheDocument();
    expect(
      screen.getByText(`Étape 1 sur ${ETAPES_GUIDE.length}`),
    ).toBeInTheDocument();
  });

  it("ne revient plus une fois vue", async () => {
    marquerGuideVu();
    render(<GuidePriseEnMain />);
    await new Promise((r) => setTimeout(r, 1000));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("se relance à la demande, même déjà vue", async () => {
    marquerGuideVu();
    render(<GuidePriseEnMain />);
    lancerGuide();
    await attendreVisite();
  });

  it("avance et recule d'une étape à l'autre", async () => {
    const utilisateur = userEvent.setup();
    render(<GuidePriseEnMain />);
    await attendreVisite();

    await utilisateur.click(screen.getByRole("button", { name: "Suivant" }));
    await waitFor(() =>
      expect(screen.getByText(ETAPES_GUIDE[1].titre)).toBeInTheDocument(),
    );

    await utilisateur.click(screen.getByRole("button", { name: "Précédent" }));
    await waitFor(() =>
      expect(screen.getByText(ETAPES_GUIDE[0].titre)).toBeInTheDocument(),
    );
  });

  it("ne propose pas de reculer depuis la première étape", async () => {
    render(<GuidePriseEnMain />);
    await attendreVisite();
    expect(screen.getByRole("button", { name: "Précédent" })).toBeDisabled();
  });

  it("se mène entièrement au clavier", async () => {
    const utilisateur = userEvent.setup();
    render(<GuidePriseEnMain />);
    await attendreVisite();

    await utilisateur.keyboard("{ArrowRight}");
    await waitFor(() =>
      expect(screen.getByText(ETAPES_GUIDE[1].titre)).toBeInTheDocument(),
    );
    await utilisateur.keyboard("{ArrowLeft}");
    await waitFor(() =>
      expect(screen.getByText(ETAPES_GUIDE[0].titre)).toBeInTheDocument(),
    );
  });

  it("se quitte sur Échap, et retient qu'elle a été vue", async () => {
    const utilisateur = userEvent.setup();
    render(<GuidePriseEnMain />);
    await attendreVisite();

    await utilisateur.keyboard("{Escape}");
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    // Quittée en route, elle ne se relancera plus d'elle-même : l'utilisateur
    // a tranché.
    expect(lireGuideVu()).toBe(true);
  });

  it("se quitte par le bouton dédié", async () => {
    const utilisateur = userEvent.setup();
    render(<GuidePriseEnMain />);
    await attendreVisite();
    // Le raccourci est passé du `title` au nom accessible (#536).
    await utilisateur.click(
      screen.getByRole("button", { name: "Quitter la visite (Échap)" }),
    );
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
  });

  it("navigue d'elle-même vers la page qu'une étape présente", async () => {
    const utilisateur = userEvent.setup();
    // La première étape qui change de page — depuis #940 c'est celle du chat,
    // et son chemin vient du menu, jamais d'un littéral (`entreeParLibelle`).
    const rang = ETAPES_GUIDE.findIndex((etape) => etape.chemin !== undefined);
    const attendu = ETAPES_GUIDE[rang].chemin as string;
    render(<GuidePriseEnMain />);
    await attendreVisite();

    for (let i = 0; i < rang; i += 1) {
      await utilisateur.keyboard("{ArrowRight}");
    }
    await waitFor(() => expect(navigations).toContain(attendu));
  });

  it("se referme sur la dernière étape avec « Terminer »", async () => {
    const utilisateur = userEvent.setup();
    render(<GuidePriseEnMain />);
    await attendreVisite();

    for (let i = 0; i < ETAPES_GUIDE.length - 1; i += 1) {
      await utilisateur.keyboard("{ArrowRight}");
    }
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Terminer" })).toBeInTheDocument(),
    );
    await utilisateur.click(screen.getByRole("button", { name: "Terminer" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(lireGuideVu()).toBe(true);
  });

  it("rend la main dans le composeur quand elle est menée à son terme", async () => {
    // #940 : « Terminer » EST le geste. Le parti pris vient de la procédure pas
    // à pas de VS Code pour le web, dont chaque étape porte un bouton qui fait
    // vraiment quelque chose ; ici on n'ajoute pas de contrôle, on déplace le
    // focus de sortie. Sans ce test, la promesse « mène à un premier objectif
    // composé » se casserait sans que rien ne proteste.
    const utilisateur = userEvent.setup();
    const { container } = render(<GuidePriseEnMain />);
    const champ = monterComposeur(container);
    await attendreVisite();

    for (let i = 0; i < ETAPES_GUIDE.length - 1; i += 1) {
      await utilisateur.keyboard("{ArrowRight}");
    }
    await utilisateur.click(screen.getByRole("button", { name: "Terminer" }));
    await waitFor(() => expect(champ).toHaveFocus());
  });

  it("ne détourne pas le focus quand on la quitte en route", async () => {
    // Le pendant du précédent, et la raison du `() => arreter()` du bouton :
    // passé nu, `arreter` recevrait l'événement de clic en premier argument et
    // prendrait toute sortie pour une visite menée à son terme.
    const utilisateur = userEvent.setup();
    const { container } = render(<GuidePriseEnMain />);
    const champ = monterComposeur(container);
    await attendreVisite();

    await utilisateur.click(
      screen.getByRole("button", { name: "Quitter la visite (Échap)" }),
    );
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(champ).not.toHaveFocus();
  });

  it("s'annonce comme une boîte de dialogue décrite", async () => {
    render(<GuidePriseEnMain />);
    await attendreVisite();
    const dialogue = screen.getByRole("dialog");
    expect(dialogue).toHaveAttribute("aria-modal", "true");
    expect(dialogue).toHaveAccessibleName(ETAPES_GUIDE[0].titre);
    expect(dialogue).toHaveAccessibleDescription(ETAPES_GUIDE[0].texte);
  });
});

describe("le menu d'aide (MenuAide)", () => {
  it("relance la visite depuis son entrée dédiée", async () => {
    const utilisateur = userEvent.setup();
    let relances = 0;
    const detacher = ecouterLancementGuide(() => (relances += 1));
    render(<MenuAide />);

    await utilisateur.click(screen.getByRole("button", { name: "Aide" }));
    await utilisateur.click(screen.getByRole("menuitem", { name: /Visite guidée/ }));

    detacher();
    expect(relances).toBe(1);
    // Le menu s'efface : il recouvrirait la première surbrillance.
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("annonce le nombre d'étapes de la visite", async () => {
    const utilisateur = userEvent.setup();
    render(<MenuAide />);
    await utilisateur.click(screen.getByRole("button", { name: "Aide" }));
    expect(
      screen.getByText(`Redécouvrir la Control Tower en ${ETAPES_GUIDE.length} étapes`),
    ).toBeInTheDocument();
  });

  it("se referme sur Échap", async () => {
    const utilisateur = userEvent.setup();
    render(<MenuAide />);
    await utilisateur.click(screen.getByRole("button", { name: "Aide" }));
    await utilisateur.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });
});

/**
 * Lot 6 de la refonte UX (#122) : le guide de prise en main interactif.
 *
 * Ce qui compte ici tient en trois promesses, toutes cassables en silence :
 *
 * - **la visite ne se déclenche qu'une fois**, à la première visite, et jamais
 *   plus ensuite — même quittée en route. Le contraire (une visite qui revient
 *   à chaque chargement) est le défaut classique de ce genre de composant ;
 * - **elle reste ancrée sur du réel** : chaque étape désigne un élément que le
 *   shell rend vraiment, via un attribut `data-guide`. Retirer cet attribut
 *   d'un composant ne casserait ni le lint ni le build — seulement la visite ;
 * - **on peut en sortir**, au clavier comme à la souris.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { GuidePriseEnMain } from "@/components/GuidePriseEnMain";
import { MenuAide } from "@/components/MenuAide";
import {
  CLE_GUIDE_VU,
  ETAPES_GUIDE,
  ecouterLancementGuide,
  lancerGuide,
  lireGuideVu,
  marquerGuideVu,
} from "@/lib/guide";

import { navigations } from "./aides";

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

  it("fait démarrer la visite sur la présentation générale", () => {
    expect(ETAPES_GUIDE[0].id).toBe("bienvenue");
  });

  it("termine sur l'aide, par où l'on relance la visite", () => {
    expect(ETAPES_GUIDE[ETAPES_GUIDE.length - 1].ancres).toContain(
      '[data-guide="aide"]',
    );
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
    await utilisateur.click(screen.getByRole("button", { name: "Quitter" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
  });

  it("navigue d'elle-même vers la page qu'une étape présente", async () => {
    const utilisateur = userEvent.setup();
    // Après « bienvenue » et « navigation », l'étape « tableau de bord » vise
    // « / » ; la première étape à changer de page est celle des coûts.
    const versCouts = ETAPES_GUIDE.findIndex(
      (etape) => etape.chemin === "/couts",
    );
    render(<GuidePriseEnMain />);
    await attendreVisite();

    for (let i = 0; i < versCouts; i += 1) {
      await utilisateur.keyboard("{ArrowRight}");
    }
    await waitFor(() => expect(navigations).toContain("/couts"));
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

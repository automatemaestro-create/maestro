/**
 * Lot 2 de la refonte UX (#118) : le thème clair / sombre / système.
 *
 * Trois invariants valent d'être tenus par des tests, parce qu'ils se cassent
 * en silence — l'interface reste fonctionnelle, elle clignote seulement :
 *
 * 1. **Le script d'init et le module doivent s'accorder.** Le premier s'exécute
 *    pendant l'analyse du HTML, le second après l'hydratation ; s'ils lisaient
 *    des clés différentes, la page s'afficherait dans un thème puis sauterait
 *    dans l'autre. Le test les confronte au lieu de relire la constante.
 * 2. **« Système » reste vivant.** Ce n'est pas un troisième thème figé mais un
 *    suivi de l'OS, y compris quand celui-ci bascule en cours de session.
 * 3. **Les deux contrôles ne divergent pas.** La bascule de la barre supérieure
 *    et la section Apparence des Paramètres (#121) sont la même commande : le
 *    stockage tranche, l'événement notifie — dans le même onglet aussi, ce que
 *    `storage` ne fait pas.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { BasculeTheme } from "@/components/BasculeTheme";
import { ParametresApparence } from "@/components/parametres/ParametresApparence";
import {
  appliquer,
  CLE_THEME,
  ecouterChoix,
  ecrireChoix,
  lireChoix,
  resoudre,
  SCRIPT_INIT_THEME,
} from "@/lib/theme";

import { basculerPreferenceSysteme, poserPreferenceSysteme } from "./aides";

const themeApplique = () => document.documentElement.getAttribute("data-theme");

describe("le choix de thème (lib/theme)", () => {
  it("part sur « système » à la première visite", () => {
    expect(lireChoix()).toBe("systeme");
  });

  it("relit le choix mémorisé", () => {
    window.localStorage.setItem(CLE_THEME, "sombre");
    expect(lireChoix()).toBe("sombre");
  });

  it("ignore une valeur stockée qui n'est pas un choix", () => {
    // Clé écrite par une version antérieure, ou à la main : on retombe sur le
    // défaut plutôt que de poser un `data-theme` que le CSS ne connaît pas.
    window.localStorage.setItem(CLE_THEME, "bleu");
    expect(lireChoix()).toBe("systeme");
  });

  it("résout « système » contre la préférence de l'appareil", () => {
    poserPreferenceSysteme(true);
    expect(resoudre("systeme")).toBe("sombre");
    poserPreferenceSysteme(false);
    expect(resoudre("systeme")).toBe("clair");
  });

  it("laisse un choix explicite l'emporter sur l'appareil", () => {
    poserPreferenceSysteme(true);
    expect(resoudre("clair")).toBe("clair");
  });

  it("applique le thème résolu au document", () => {
    appliquer("sombre");
    expect(themeApplique()).toBe("sombre");
  });

  it("notifie les autres contrôles de la même page", () => {
    // `storage` ne prévient que les AUTRES onglets : sans cet événement
    // interne, deux contrôles d'une même page divergeraient.
    const vus: string[] = [];
    const detacher = ecouterChoix((choix) => vus.push(choix));
    ecrireChoix("sombre");
    detacher();
    ecrireChoix("clair");
    expect(vus).toEqual(["sombre"]);
  });

  it("suit un changement venu d'un autre onglet", () => {
    const vus: string[] = [];
    const detacher = ecouterChoix((choix) => vus.push(choix));
    window.localStorage.setItem(CLE_THEME, "sombre");
    window.dispatchEvent(new StorageEvent("storage", { key: CLE_THEME }));
    detacher();
    expect(vus).toEqual(["sombre"]);
  });

  it("ignore le remue-ménage des autres clés du stockage", () => {
    const vus: string[] = [];
    const detacher = ecouterChoix((choix) => vus.push(choix));
    window.dispatchEvent(new StorageEvent("storage", { key: "autre.chose" }));
    detacher();
    expect(vus).toEqual([]);
  });
});

describe("le script d'init du layout (SCRIPT_INIT_THEME)", () => {
  // Le script est du texte injecté dans le HTML : on l'exécute ici tel quel,
  // pour confronter son comportement à celui du module — la garantie qu'ils ne
  // se désaccordent pas est le seul rempart contre le flash au chargement.
  const executer = () => new Function(SCRIPT_INIT_THEME)();

  it("pose le thème mémorisé avant tout rendu", () => {
    window.localStorage.setItem(CLE_THEME, "sombre");
    executer();
    expect(themeApplique()).toBe("sombre");
  });

  it("résout « système » comme le module", () => {
    window.localStorage.setItem(CLE_THEME, "systeme");
    poserPreferenceSysteme(true);
    executer();
    expect(themeApplique()).toBe(resoudre("systeme"));
    expect(themeApplique()).toBe("sombre");
  });

  it("s'en remet à l'appareil à la première visite", () => {
    poserPreferenceSysteme(true);
    executer();
    expect(themeApplique()).toBe("sombre");
  });

  it("ne casse pas la page quand le stockage est interdit", () => {
    // Navigation privée, cookies bloqués : `localStorage` lève. Le script est
    // en tête du `<head>` — s'il jetait, il emporterait le rendu avec lui.
    const vrai = Object.getOwnPropertyDescriptor(window, "localStorage");
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      get() {
        throw new Error("stockage interdit");
      },
    });
    expect(() => executer()).not.toThrow();
    if (vrai) Object.defineProperty(window, "localStorage", vrai);
  });
});

describe("la bascule de la barre supérieure (BasculeTheme)", () => {
  it("n'ouvre son menu qu'à la demande", async () => {
    const utilisateur = userEvent.setup();
    render(<BasculeTheme />);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();

    await utilisateur.click(
      screen.getByRole("button", { name: "Thème de l'interface" }),
    );
    expect(screen.getByRole("menu")).toBeInTheDocument();
    expect(screen.getAllByRole("menuitemradio")).toHaveLength(3);
  });

  it("coche le choix courant", async () => {
    window.localStorage.setItem(CLE_THEME, "sombre");
    const utilisateur = userEvent.setup();
    render(<BasculeTheme />);
    await utilisateur.click(
      screen.getByRole("button", { name: "Thème de l'interface" }),
    );
    expect(screen.getByRole("menuitemradio", { name: /Sombre/ })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    expect(screen.getByRole("menuitemradio", { name: /Clair/ })).toHaveAttribute(
      "aria-checked",
      "false",
    );
  });

  it("applique, mémorise et referme au choix d'un thème", async () => {
    const utilisateur = userEvent.setup();
    render(<BasculeTheme />);
    await utilisateur.click(
      screen.getByRole("button", { name: "Thème de l'interface" }),
    );
    await utilisateur.click(screen.getByRole("menuitemradio", { name: /Sombre/ }));

    expect(themeApplique()).toBe("sombre");
    expect(window.localStorage.getItem(CLE_THEME)).toBe("sombre");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    // Le focus revient au bouton : sans quoi il retomberait sur le document et
    // la navigation au clavier repartirait du début de la page.
    expect(screen.getByRole("button", { name: "Thème de l'interface" })).toHaveFocus();
  });

  // Ces deux-là gardent le hook partagé `useSurfaceDeroulee` (#536), pas la
  // bascule de thème : elle est ici le représentant des quatre surfaces, celle
  // dont les entrées sont les plus simples. L'audit complet — les quatre
  // surfaces, les sept écrans, `vitest-axe` — reste différé au lot 5 (#537) ;
  // ce qui est vérifié ici est seulement que le mécanisme existe et bouge, une
  // navigation clavier qui ne serait jouée par personne étant indiscernable de
  // code mort.
  it("amène le focus sur la première entrée à l'ouverture", async () => {
    const utilisateur = userEvent.setup();
    render(<BasculeTheme />);
    await utilisateur.click(
      screen.getByRole("button", { name: "Thème de l'interface" }),
    );
    expect(screen.getByRole("menuitemradio", { name: /Clair/ })).toHaveFocus();
  });

  it("se parcourt aux flèches, avec Home, End et le bouclage", async () => {
    const utilisateur = userEvent.setup();
    render(<BasculeTheme />);
    await utilisateur.click(
      screen.getByRole("button", { name: "Thème de l'interface" }),
    );
    const [clair, sombre, systeme] = screen.getAllByRole("menuitemradio");

    await utilisateur.keyboard("{ArrowDown}");
    expect(sombre).toHaveFocus();
    await utilisateur.keyboard("{End}");
    expect(systeme).toHaveFocus();
    // Depuis la dernière entrée, la flèche du bas revient à la première : un
    // menu boucle, il ne bute pas.
    await utilisateur.keyboard("{ArrowDown}");
    expect(clair).toHaveFocus();
    // Et symétriquement vers le haut.
    await utilisateur.keyboard("{ArrowUp}");
    expect(systeme).toHaveFocus();
    await utilisateur.keyboard("{Home}");
    expect(clair).toHaveFocus();
  });

  it("se referme sur Échap sans rien changer", async () => {
    const utilisateur = userEvent.setup();
    render(<BasculeTheme />);
    await utilisateur.click(
      screen.getByRole("button", { name: "Thème de l'interface" }),
    );
    await utilisateur.keyboard("{Escape}");

    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(window.localStorage.getItem(CLE_THEME)).toBeNull();
  });

  it("se referme sur un clic à l'extérieur", async () => {
    const utilisateur = userEvent.setup();
    render(
      <div>
        <BasculeTheme />
        <button type="button">ailleurs</button>
      </div>,
    );
    await utilisateur.click(
      screen.getByRole("button", { name: "Thème de l'interface" }),
    );
    await utilisateur.click(screen.getByRole("button", { name: "ailleurs" }));
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("continue de suivre l'appareil en mode « système »", async () => {
    // Le cœur du mode « système » : l'OS bascule au coucher du soleil, la page
    // est restée ouverte — elle doit basculer avec lui.
    render(<BasculeTheme />);
    poserPreferenceSysteme(false);
    appliquer("systeme");
    expect(themeApplique()).toBe("clair");

    basculerPreferenceSysteme(true);
    await waitFor(() => expect(themeApplique()).toBe("sombre"));
  });

  it("cesse de suivre l'appareil dès qu'un thème est choisi", async () => {
    const utilisateur = userEvent.setup();
    render(<BasculeTheme />);
    await utilisateur.click(
      screen.getByRole("button", { name: "Thème de l'interface" }),
    );
    await utilisateur.click(screen.getByRole("menuitemradio", { name: /Clair/ }));

    basculerPreferenceSysteme(true);
    expect(themeApplique()).toBe("clair");
  });
});

describe("les deux contrôles de thème (barre supérieure + Paramètres)", () => {
  it("s'accordent quand le choix est fait depuis les Paramètres", async () => {
    const utilisateur = userEvent.setup();
    render(
      <>
        <BasculeTheme />
        <ParametresApparence />
      </>,
    );

    const groupe = screen.getByRole("radiogroup", { name: "Thème de l'interface" });
    await utilisateur.click(
      within(groupe).getByRole("radio", { name: /Sombre/ }),
    );

    expect(themeApplique()).toBe("sombre");
    // La bascule de la barre supérieure a suivi, sans connaître les Paramètres.
    await utilisateur.click(
      screen.getByRole("button", { name: "Thème de l'interface" }),
    );
    expect(screen.getByRole("menuitemradio", { name: /Sombre/ })).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });

  it("s'accordent quand le choix est fait depuis la barre supérieure", async () => {
    const utilisateur = userEvent.setup();
    render(
      <>
        <BasculeTheme />
        <ParametresApparence />
      </>,
    );

    await utilisateur.click(
      screen.getByRole("button", { name: "Thème de l'interface" }),
    );
    await utilisateur.click(screen.getByRole("menuitemradio", { name: /Clair/ }));

    const groupe = screen.getByRole("radiogroup", { name: "Thème de l'interface" });
    await waitFor(() =>
      expect(within(groupe).getByRole("radio", { name: /Clair/ })).toHaveAttribute(
        "aria-checked",
        "true",
      ),
    );
  });

  it("restitue le choix mémorisé dans les Paramètres après hydratation", async () => {
    // Le rendu serveur ne connaît pas le stockage : la section part sur
    // « Système » puis rattrape, différé d'un tick.
    window.localStorage.setItem(CLE_THEME, "sombre");
    render(<ParametresApparence />);
    const groupe = screen.getByRole("radiogroup", { name: "Thème de l'interface" });
    await waitFor(() =>
      expect(within(groupe).getByRole("radio", { name: /Sombre/ })).toHaveAttribute(
        "aria-checked",
        "true",
      ),
    );
  });
});

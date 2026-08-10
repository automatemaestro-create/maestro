/**
 * Le sélecteur de projet du shell (#280, lot 4 de #276) : le projet actif visible
 * en permanence, et la bascule d'un projet à l'autre sans quitter la page.
 *
 * Ce que ces tests gardent en propre, et qu'aucun autre outil n'attrape :
 *
 * - **la bascule ne navigue pas.** C'est le critère du lot (« sans passer par
 *   une page intermédiaire ») et c'est aussi la seule chose qui distingue ce
 *   sélecteur d'un lien vers un écran de choix : à l'écran, les deux donnent le
 *   même parcours. `navigations` est donc l'observable, comme dans
 *   `projet-actif.test.tsx` pour la garde du shell ;
 * - **l'entrée « Projets » a quitté la sidebar sans emporter son écran.** Un
 *   chemin qui tombe en 404 ne casse ni le lint ni le build, et une page hors
 *   menu n'est plus vérifiée par le contrôle de routes de `navigation.test.tsx`
 *   — d'où le contrôle repris ici sur `HORS_MENU` ;
 * - **le sélecteur est branché dans le shell.** Le montage entier vit dans
 *   `shell.test.tsx` ; l'assertion est ici pour que le lot porte la sienne, sur
 *   le même principe : rien ne remarquerait qu'un emplacement de la barre
 *   supérieure a cessé d'être rempli.
 *
 * Le lot 6 (#282) y a ajouté les trois cas que le lot 4 avait laissés : la
 * fermeture par **clic à l'extérieur** (le geste le plus courant, et le seul
 * qui passe par un écouteur du document), le sélecteur **sans projet actif**
 * (il ne rend rien, plutôt que d'ouvrir une seconde porte d'entrée à côté de
 * celle de #279) et le poste **à un seul projet**, où il n'y a rien vers quoi
 * basculer mais où la gestion doit rester atteignable.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { Shell } from "@/components/Shell";
import { SelecteurProjet } from "@/components/projets/SelecteurProjet";
import { FournisseurProjetActif } from "@/lib/etatProjetActif";
import { marquerGuideVu } from "@/lib/guide";
import { entreeParLibelle, HORS_MENU, MENU } from "@/lib/navigation";
import { lireProjetActifId } from "@/lib/projetActif";

import {
  navigations,
  poserProjets,
  poserChemin,
  projetFactice,
} from "./aides";

const DEPENSIO = projetFactice({
  id: "prj-depensio",
  nom: "Dépensio",
  racine: "D:/projets/depensio",
});

const MAESTRO = projetFactice({
  id: "prj-maestro",
  nom: "Maestro",
  racine: "E:/projets/maestro",
});

/**
 * Le sélecteur seul, sous le **vrai** fournisseur de projet actif : c'est lui
 * qui confronte l'identifiant retenu à la liste servie, et la bascule n'a de
 * sens qu'observée à travers lui.
 */
const monterSelecteur = async (actif = DEPENSIO) => {
  poserProjets([DEPENSIO, MAESTRO]);
  window.localStorage.setItem("maestro.projet.actif", actif.id);
  render(
    <FournisseurProjetActif>
      <SelecteurProjet />
    </FournisseurProjetActif>,
  );
  return screen.findByRole("button", {
    name: new RegExp(`Projet actif : ${actif.nom}`),
  });
};

const ouvrirMenu = async (utilisateur: ReturnType<typeof userEvent.setup>) => {
  await utilisateur.click(
    screen.getByRole("button", { name: /Projet actif/ }),
  );
  return screen.getByRole("menu", { name: "Projet actif" });
};

describe("le sélecteur de projet (SelecteurProjet)", () => {
  it("affiche le projet actif, et sa racine pour le distinguer", async () => {
    const bouton = await monterSelecteur();
    expect(bouton).toHaveTextContent("Dépensio");
    expect(bouton).toHaveAttribute("title", "D:/projets/depensio");
  });

  it("liste les projets déclarés et coche celui qui est ouvert", async () => {
    const utilisateur = userEvent.setup();
    await monterSelecteur();
    const menu = await ouvrirMenu(utilisateur);

    const options = within(menu).getAllByRole("menuitemradio");
    expect(options.map((option) => option.textContent)).toEqual([
      "DépensioD:/projets/depensio",
      "MaestroE:/projets/maestro",
    ]);
    expect(options[0]).toHaveAttribute("aria-checked", "true");
    expect(options[1]).toHaveAttribute("aria-checked", "false");
  });

  it("bascule sur un autre projet sans quitter la page", async () => {
    // Le cœur du lot : le choix change, l'URL non. Une redirection vers un
    // écran de choix donnerait le même résultat à l'écran — seule l'absence de
    // poussée au routeur dit que l'utilisateur est resté où il était.
    const utilisateur = userEvent.setup();
    poserChemin("/couts");
    await monterSelecteur();
    await ouvrirMenu(utilisateur);

    await utilisateur.click(screen.getByRole("menuitemradio", { name: /Maestro/ }));

    expect(lireProjetActifId()).toBe(MAESTRO.id);
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: /Projet actif : Maestro/ }),
      ).toBeInTheDocument(),
    );
    expect(navigations).toEqual([]);
  });

  it("referme le menu sans rien réécrire quand on rechoisit le projet ouvert", async () => {
    // Une réécriture serait invisible ici, mais elle notifierait tous les
    // abonnés du projet actif — au lot 5, chaque écran relirait ses données
    // pour un changement qui n'a pas eu lieu.
    const utilisateur = userEvent.setup();
    await monterSelecteur();
    await ouvrirMenu(utilisateur);

    let notifications = 0;
    const compter = () => (notifications += 1);
    window.addEventListener("maestro:projet-actif", compter);
    await utilisateur.click(
      screen.getByRole("menuitemradio", { name: /Dépensio/ }),
    );
    window.removeEventListener("maestro:projet-actif", compter);

    expect(notifications).toBe(0);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("mène à l'écran de gestion des projets, sans en écrire le chemin", async () => {
    // Le troisième critère : déclarer, modifier et supprimer restent servis par
    // l'écran de #225, atteint d'ici puisque le menu ne le porte plus.
    const utilisateur = userEvent.setup();
    await monterSelecteur();
    const menu = await ouvrirMenu(utilisateur);

    expect(
      within(menu).getByRole("menuitem", { name: /Gérer les projets/ }),
    ).toHaveAttribute("href", "/projets");
  });

  it("se ferme sur Échap en rendant le focus au bouton", async () => {
    const utilisateur = userEvent.setup();
    await monterSelecteur();
    await ouvrirMenu(utilisateur);

    await utilisateur.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Projet actif/ })).toHaveFocus();
  });

  it("annonce l'état d'ouverture du menu", async () => {
    const utilisateur = userEvent.setup();
    await monterSelecteur();
    const bouton = screen.getByRole("button", { name: /Projet actif/ });
    expect(bouton).toHaveAttribute("aria-expanded", "false");
    await utilisateur.click(bouton);
    expect(bouton).toHaveAttribute("aria-expanded", "true");
  });

  // --- Trous du lot 4, comblés par le lot 6 (#282) -------------------------

  it("se ferme sur un clic à l'extérieur", async () => {
    // Le second des deux gestes de fermeture. Échap était couvert, pas
    // celui-ci — pourtant le plus courant, et le seul qui passe par un
    // écouteur posé sur le document (donc le seul qui puisse fuir au
    // démontage).
    const utilisateur = userEvent.setup();
    await monterSelecteur();
    await ouvrirMenu(utilisateur);

    await utilisateur.click(document.body);

    await waitFor(() => expect(screen.queryByRole("menu")).not.toBeInTheDocument());
  });

  it("ne montre rien tant qu'aucun projet n'est actif", async () => {
    // Le parti pris du composant : `null` plutôt qu'un état « aucun projet ».
    // Un sélecteur qui proposerait de choisir serait une **seconde porte
    // d'entrée** à côté de celle de #279, avec deux façons de rater la garde.
    poserProjets([DEPENSIO, MAESTRO]);
    window.localStorage.removeItem("maestro.projet.actif");
    render(
      <FournisseurProjetActif>
        <SelecteurProjet />
      </FournisseurProjetActif>,
    );

    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /Projet actif/ })).toBeNull(),
    );
  });

  it("reste utile avec un seul projet déclaré", async () => {
    // Le cas d'un poste qui débute — il n'y a rien vers quoi basculer, et
    // pourtant tout le reste doit tenir : le projet actif se lit, et la
    // gestion (déclarer le suivant) s'atteint. Un menu qui se réduirait à sa
    // seule ligne cochée serait un cul-de-sac au moment précis où l'on veut en
    // ajouter un.
    const utilisateur = userEvent.setup();
    poserProjets([DEPENSIO]);
    window.localStorage.setItem("maestro.projet.actif", DEPENSIO.id);
    render(
      <FournisseurProjetActif>
        <SelecteurProjet />
      </FournisseurProjetActif>,
    );
    await screen.findByRole("button", { name: /Projet actif : Dépensio/ });
    const menu = await ouvrirMenu(utilisateur);

    expect(within(menu).getAllByRole("menuitemradio")).toHaveLength(1);
    expect(
      within(menu).getByRole("menuitem", { name: /Gérer les projets/ }),
    ).toBeInTheDocument();
  });
});

describe("la fin de l'entrée « Projets » du menu (#280)", () => {
  it("a retiré « Projets » de la barre latérale", () => {
    expect(MENU.map((entree) => entree.href)).not.toContain("/projets");
    expect(MENU.map((entree) => entree.libelle)).not.toContain("Projets");
  });

  it("sert toujours les chemins des pages sorties du menu", async () => {
    // Le pendant, hors menu, du contrôle de routes de `navigation.test.tsx` :
    // une page qu'on retire du menu et dont on supprime la route tombe en 404
    // pour tous les liens déjà écrits, sans que rien ne le signale.
    const { existsSync } = await import("node:fs");
    const path = await import("node:path");
    const { fileURLToPath } = await import("node:url");
    const app = path.join(
      path.dirname(fileURLToPath(import.meta.url)),
      "..",
      "app",
    );

    expect(HORS_MENU.length).toBeGreaterThan(0);
    for (const { href, libelle } of HORS_MENU) {
      const segments = href.split("/").filter((segment) => segment !== "");
      expect(
        existsSync(path.join(app, ...segments, "page.tsx")),
        `« ${libelle} » est servie à « ${href} », qui n'a pas de page`,
      ).toBe(true);
    }
  });

  it("garde son titre de barre supérieure à la page sortie du menu", () => {
    // Sans `HORS_MENU`, la barre retomberait sur « Control Tower » : un écran
    // anonyme pour un chemin qui marche.
    expect(entreeParLibelle("Projets")?.href).toBe("/projets");
  });
});

describe("le sélecteur dans le shell", () => {
  beforeEach(() => {
    marquerGuideVu();
    poserProjets([DEPENSIO, MAESTRO]);
    window.localStorage.setItem("maestro.projet.actif", DEPENSIO.id);
  });

  it("occupe la barre supérieure de toutes les pages", async () => {
    poserChemin("/couts");
    render(
      <Shell>
        <p>contenu de la page</p>
      </Shell>,
    );
    await screen.findByText("contenu de la page");

    const barre = screen.getByRole("banner");
    expect(
      within(barre).getByRole("button", { name: /Projet actif : Dépensio/ }),
    ).toBeInTheDocument();
    // Le titre de page reste : le sélecteur s'ajoute au cadre, il ne le remplace
    // pas — on lit « ce projet-ci, cette page-là ».
    expect(within(barre).getByRole("heading", { level: 1 })).toHaveTextContent(
      "Coûts & analytics",
    );
  });

  it("ne met plus « Projets » dans la navigation du shell", async () => {
    render(
      <Shell>
        <p>contenu de la page</p>
      </Shell>,
    );
    await screen.findByText("contenu de la page");

    const navigation = screen.getByRole("navigation", {
      name: "Navigation principale",
    });
    expect(
      within(navigation).queryByRole("link", { name: /Projets/ }),
    ).not.toBeInTheDocument();
  });
});

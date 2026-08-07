/**
 * Lot 1 de la refonte UX (#117) : le shell de backoffice — le menu unique, la
 * sidebar qui en désigne la page courante, la barre supérieure qui en reprend
 * le titre, et le repli mémorisé d'une page à l'autre.
 *
 * Ce que ces tests protègent en propre : `MENU` est la **source unique** des
 * pages (sidebar et barre supérieure la lisent toutes deux), et l'état de repli
 * fait l'aller-retour par le `localStorage` plutôt que par un état React local
 * — c'est ce qui permet à la section Apparence des Paramètres (#121) de piloter
 * la même bascule sans connaître le shell.
 */

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { BarreLaterale } from "@/components/BarreLaterale";
import { BarreSuperieure } from "@/components/BarreSuperieure";
import { entreeCourante, entreeParLibelle, MENU } from "@/lib/navigation";

import { agentFactice, poserChemin, rendreAvecEtat } from "./aides";

describe("le menu (lib/navigation)", () => {
  it("porte une entrée par intention (#190)", () => {
    // « Playbooks » et le chat par agent regardaient le même objet que
    // « Agents » : ce sont désormais des onglets de la fiche agent. « Chat »
    // subsiste pour le chat global, qui est une autre intention.
    expect(MENU.map((entree) => entree.libelle)).toEqual([
      "Tableau de bord",
      "Projets",
      "Agents",
      "Chat",
      "Coûts & analytics",
      "Validations",
      "Journal",
      "Paramètres",
    ]);
  });

  it("désigne l'entrée qui porte le chemin courant", () => {
    expect(entreeCourante("/couts")?.libelle).toBe("Coûts & analytics");
    expect(entreeCourante("/parametres")?.libelle).toBe("Paramètres");
  });

  it("rattache un sous-chemin à sa section", () => {
    // La fiche d'un agent et ses onglets restent sous « Agents » : sans quoi
    // l'entrée perdrait sa mise en évidence dès qu'on ouvre un détail.
    expect(entreeCourante("/agents/dev/playbook")?.href).toBe("/agents");
  });

  it("ne rend la racine active que sur la racine", () => {
    // Le piège du `startsWith` : « / » préfixe TOUS les chemins, le tableau de
    // bord resterait donc actif sur chaque page.
    expect(entreeCourante("/")?.href).toBe("/");
    expect(entreeCourante("/chat")?.href).toBe("/chat");
  });

  it("ne désigne aucune entrée pour un chemin inconnu", () => {
    expect(entreeCourante("/inexistant")).toBeUndefined();
  });

  // --- La navigation v2 (#189) : ce que le menu ne doit plus porter ---------

  it("n'a plus d'entrée pour les pages fusionnées", () => {
    // Le pendant du test d'intention ci-dessus, pris par l'autre bout : une
    // entrée réintroduite ici rouvrirait un deuxième chemin vers le même objet,
    // et se redirigerait elle-même (`next.config.ts`) — un aller simple.
    const chemins = MENU.map((entree) => entree.href);
    expect(chemins).not.toContain("/catalogue");
    expect(chemins).not.toContain("/playbooks");
  });

  it("ne mène jamais deux entrées au même endroit", () => {
    const chemins = MENU.map((entree) => entree.href);
    expect(new Set(chemins).size).toBe(chemins.length);
    const libelles = MENU.map((entree) => entree.libelle);
    expect(new Set(libelles).size).toBe(libelles.length);
  });

  it("ne propose que des pages que l'application sert vraiment", async () => {
    // Une entrée de menu vers une page supprimée est un 404 offert à un clic,
    // et ni le lint ni le build ne le remarqueraient. On confronte le menu aux
    // routes présentes sous `app/`.
    const { existsSync } = await import("node:fs");
    const path = await import("node:path");
    const { fileURLToPath } = await import("node:url");
    const app = path.join(
      path.dirname(fileURLToPath(import.meta.url)),
      "..",
      "app",
    );

    for (const { href, libelle } of MENU) {
      const segments = href.split("/").filter((segment) => segment !== "");
      expect(
        existsSync(path.join(app, ...segments, "page.tsx")),
        `l'entrée « ${libelle} » mène à « ${href} », qui n'a pas de page`,
      ).toBe(true);
    }
  });
});

describe("les renvois par libellé (entreeParLibelle)", () => {
  it("résout une page du menu", () => {
    // Le tableau de bord épuré (#191) renvoie vers les pages qu'il a rangées en
    // passant par là : un renvoi suit de lui-même une page qui déménage — c'est
    // ce qui l'a fait suivre « Agents » de `/catalogue` à `/agents` (#190).
    expect(entreeParLibelle("Agents")?.href).toBe("/agents");
    expect(entreeParLibelle("Coûts & analytics")?.href).toBe("/couts");
  });

  it("allume un renvoi dès que sa page entre au menu", () => {
    // Le Journal est la démonstration du mécanisme : son renvoi était écrit
    // dans `FilActivite` dès #191 et resté éteint faute de page, jusqu'à ce
    // que #249 ajoute l'entrée — sans une ligne de plus dans le composant.
    expect(entreeParLibelle("Journal")?.href).toBe("/journal");
  });

  it("reste muet sur une page qui n'existe pas", () => {
    // Le pendant : un renvoi vers une page absente du menu ne s'allume pas,
    // plutôt que de fabriquer un lien mort.
    expect(entreeParLibelle("Rapports")).toBeUndefined();
  });

  it("exige le libellé exact, sans à-peu-près", () => {
    expect(entreeParLibelle("agents")).toBeUndefined();
    expect(entreeParLibelle("Coûts")).toBeUndefined();
  });
});

describe("la sidebar (BarreLaterale)", () => {
  it("rend un lien par section, dans l'ordre du menu", () => {
    render(<BarreLaterale repliee={false} />);
    const navigation = screen.getByRole("navigation", {
      name: "Navigation principale",
    });
    const liens = within(navigation).getAllByRole("link");
    expect(liens.map((lien) => lien.getAttribute("href"))).toEqual(
      MENU.map((entree) => entree.href),
    );
  });

  it("marque la page courante pour les lecteurs d'écran", () => {
    poserChemin("/couts");
    render(<BarreLaterale repliee={false} />);
    expect(screen.getByRole("link", { name: /Coûts & analytics/ })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(
      screen.getByRole("link", { name: /Tableau de bord/ }),
    ).not.toHaveAttribute("aria-current");
  });

  it("garde chaque section atteignable une fois repliée", () => {
    // Repliée, la sidebar masque les libellés : c'est le `title` (et l'icône)
    // qui portent alors le nom de la section — les liens doivent rester tous là.
    render(<BarreLaterale repliee />);
    const navigation = screen.getByRole("navigation", {
      name: "Navigation principale",
    });
    expect(within(navigation).getAllByRole("link")).toHaveLength(MENU.length);
    for (const { libelle } of MENU) {
      expect(within(navigation).getByTitle(libelle)).toBeInTheDocument();
    }
  });
});

describe("la barre supérieure (BarreSuperieure)", () => {
  const barre = (repliee = false, basculer = () => {}) => (
    <BarreSuperieure
      repliee={repliee}
      basculerRepli={basculer}
      theme={<span>thème</span>}
      aide={<span>aide</span>}
    />
  );

  it("titre la page d'après le menu", () => {
    poserChemin("/chat");
    rendreAvecEtat(barre());
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Chat");
  });

  it("retombe sur « Control Tower » hors des pages du menu", () => {
    poserChemin("/inexistant");
    rendreAvecEtat(barre());
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "Control Tower",
    );
  });

  it("annonce le flux temps réel connecté, puis la reconnexion", () => {
    const { unmount } = rendreAvecEtat(barre(), { connecte: true });
    expect(screen.getByText("Temps réel connecté")).toBeInTheDocument();
    unmount();

    rendreAvecEtat(barre(), { connecte: false });
    expect(screen.getByText("Reconnexion…")).toBeInTheDocument();
  });

  it("cumule le coût rapporté par les agents", () => {
    rendreAvecEtat(barre(), {
      agents: [
        agentFactice({ nom: "dev", cout_usd: 0.5 }),
        agentFactice({ nom: "qa", cout_usd: 0.25 }),
      ],
    });
    // Espace insécable étroit de `Intl` en fr-FR : on cible le nombre.
    expect(screen.getByText(/0,75/)).toBeInTheDocument();
  });

  it("distingue « aucun coût rapporté » de « coût nul »", () => {
    // Un agent qui n'a rien rapporté (cout_usd null) ne vaut pas 0 $ : la barre
    // affiche un tiret, sans quoi on lirait une gratuité qui n'existe pas.
    rendreAvecEtat(barre(), { agents: [agentFactice({ cout_usd: null })] });
    expect(screen.getByTitle(/Coût cumulé/)).toHaveTextContent("—");
  });

  it("bascule le repli et décrit l'état de la navigation", async () => {
    const utilisateur = userEvent.setup();
    let bascules = 0;
    rendreAvecEtat(barre(false, () => (bascules += 1)));

    const bouton = screen.getByRole("button", { name: "Replier la navigation" });
    expect(bouton).toHaveAttribute("aria-expanded", "true");
    expect(bouton).toHaveAttribute("aria-controls", "navigation-principale");

    await utilisateur.click(bouton);
    expect(bascules).toBe(1);
  });

  it("propose de déplier quand la navigation est repliée", () => {
    rendreAvecEtat(barre(true));
    expect(
      screen.getByRole("button", { name: "Déplier la navigation" }),
    ).toHaveAttribute("aria-expanded", "false");
  });

  it("réserve la place des notifications tant qu'aucune ne lui est donnée", () => {
    // La géométrie de la barre ne doit pas bouger d'un lot à l'autre : sans
    // cloche, l'emplacement est tenu par un bouton désactivé.
    rendreAvecEtat(barre());
    expect(
      screen.getByRole("button", { name: /Notifications — bientôt disponible/ }),
    ).toBeDisabled();
  });
});

// Le repli lui-même (persistance, notification des autres contrôles) est couvert
// par `parametres.test.tsx`, aux côtés des préférences du poste ; ici on ne
// vérifie que la géométrie qu'il commande.

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
import {
  entreeCourante,
  entreeParLibelle,
  HORS_MENU,
  MENU,
} from "@/lib/navigation";

import {
  agentFactice,
  coutExecutionFactice,
  poserChemin,
  rendreAvecEtat,
  usageFactice,
} from "./aides";

describe("le menu (lib/navigation)", () => {
  it("porte une entrée par intention (#190)", () => {
    // « Playbooks » et le chat par agent regardaient le même objet que
    // « Agents » : ce sont désormais des onglets de la fiche agent. « Chat »
    // subsiste pour le chat global, qui est une autre intention.
    //
    // « Composer un objectif » (#319) ouvre la liste, juste après l'accueil :
    // c'est le geste par lequel on entre dans un run, et une action qu'on ne
    // trouve pas est une action qui n'existe pas — le poste vide renvoyait
    // jusque-là à `curl`.
    //
    // « Valider le brief » (#322) la suit, dont elle est l'autre moitié : on
    // compose, le Chef de projet rédige, on tranche. Elle est **au menu** bien
    // qu'on y arrive surtout par la cloche ou le tableau de bord — un run
    // suspendu sur son brief ne crée aucune tâche, donc rien d'autre ne le
    // montre.
    //
    // « Runs » (#474) ferme ce groupe de tête : un run n'était l'objet d'aucun
    // écran — on y entrait par « Composer un objectif » et on n'y revenait
    // jamais, les runs passés n'étant listés nulle part (revue #470).
    expect(MENU.map((entree) => entree.libelle)).toEqual([
      "Tableau de bord",
      "Composer un objectif",
      "Valider le brief",
      "Runs",
      "Agents",
      "Chat",
      "Coûts & analytics",
      "Validations",
      "Journal",
      "Paramètres",
    ]);
  });

  it("ne range plus le projet parmi les destinations (#280)", () => {
    // Le reproche du bilan de la Phase 7 : « Projets » en entrée de sidebar
    // faisait du projet une destination parmi d'autres, alors qu'il est le
    // cadre de toutes. Il se change au sélecteur du shell, et son écran de
    // gestion s'atteint de là — d'où sa sortie du menu, sans sortir des pages.
    expect(MENU.map((entree) => entree.href)).not.toContain("/projets");
    expect(HORS_MENU.map((entree) => entree.href)).toContain("/projets");
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

    // `HORS_MENU` comprise (#280) : quitter la sidebar n'est pas quitter
    // l'application — une page hors menu dont le fichier disparaîtrait rendrait
    // un 404 au bout du sélecteur, exactement le lien mort qu'on évite ici.
    for (const { href, libelle } of [...MENU, ...HORS_MENU]) {
      const segments = href.split("/").filter((segment) => segment !== "");
      expect(
        existsSync(path.join(app, ...segments, "page.tsx")),
        `l'entrée « ${libelle} » mène à « ${href} », qui n'a pas de page`,
      ).toBe(true);
    }
  });

  it("titre encore une page sortie du menu (#280)", () => {
    // Sans `HORS_MENU` dans la résolution, `/projets` répondrait toujours mais
    // la barre supérieure retomberait sur « Control Tower » : un écran anonyme
    // pour un chemin qui marche. C'est le critère « les anciens chemins restent
    // servis » pris au niveau où il se casse en silence.
    expect(entreeCourante("/projets")?.libelle).toBe("Projets");
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

  it("résout encore une page hors menu (#280)", () => {
    // C'est ce qui permet au sélecteur de viser l'écran de gestion sans écrire
    // « /projets » en dur : le jour où cet écran déménage, le sélecteur suit.
    expect(entreeParLibelle("Projets")?.href).toBe("/projets");
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

  it("cumule les grands livres du projet actif", () => {
    // #281 : la source a changé — les coûts rapportés **par agent** valent pour
    // le poste entier (le parc n'appartient à aucun projet), les grands livres
    // sont dérivés des tâches du projet. Un montant qui suit l'utilisateur de
    // page en page ne pouvait pas rester le seul chiffre à parler d'ailleurs.
    rendreAvecEtat(barre(), {
      couts: [
        coutExecutionFactice({
          run_id: "run-1",
          total: usageFactice({ cout_usd: 0.5 }),
        }),
        coutExecutionFactice({
          run_id: "run-2",
          total: usageFactice({ cout_usd: 0.25 }),
        }),
      ],
      // Les coûts par agent ne comptent plus, même quand ils sont là.
      agents: [agentFactice({ nom: "dev", cout_usd: 12 })],
    });
    // Espace insécable étroit de `Intl` en fr-FR : on cible le nombre.
    expect(screen.getByText(/0,75/)).toBeInTheDocument();
    expect(screen.getByTitle(/Coût cumulé/)).not.toHaveTextContent("12");
  });

  it("distingue « aucun coût rapporté » de « coût nul »", () => {
    // Un grand livre sans montant (cout_usd null) ne vaut pas 0 $ : la barre
    // affiche un tiret, sans quoi on lirait une gratuité qui n'existe pas.
    rendreAvecEtat(barre(), { couts: [coutExecutionFactice()] });
    expect(screen.getByTitle(/Coût cumulé/)).toHaveTextContent("—");
  });

  it("dit de quel projet ce coût est le coût", () => {
    // Le titre porte le nom : sur une Control Tower multi-projets, un total
    // anonyme se lit comme le total de tout ce qui tourne (#281).
    rendreAvecEtat(barre(), {});
    expect(screen.getByTitle(/Coût cumulé sur Dépensio/)).toBeInTheDocument();
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

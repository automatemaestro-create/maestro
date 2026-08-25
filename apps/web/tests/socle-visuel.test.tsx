/**
 * Le langage visuel de la Control Tower (#245, tests différés au lot 8 —
 * docs/10 §5.1) : le jeu d'icônes et les primitives dont tous les écrans de la
 * vague héritent.
 *
 * Un socle se teste par ses **promesses**, pas par ses classes une à une — un
 * test qui figerait `bg-white` casserait à la première retouche de teinte sans
 * qu'aucune règle du produit n'ait bougé. Trois promesses sont vérifiables et
 * régressent en silence :
 *
 * 1. **Les deux thèmes viennent avec la brique.** Chaque primitive porte ses
 *    variants `dark:` elle-même ; c'est la seule façon de garantir qu'aucun
 *    écran n'oublie le thème sombre, et c'est le point sur lequel les classes
 *    recopiées divergeaient. Un appelant ne voit pas qu'un `dark:` manque : sur
 *    son poste en clair, tout va bien.
 * 2. **L'icône est décorative, jamais informative.** Elle double un libellé
 *    texte, elle ne le porte pas seule — l'émoji le faisait (« 🤖 dev »
 *    n'apprenait rien à qui ne le voyait pas), et c'est ce que le lot corrige.
 * 3. **Plus aucun écran ne signe une ligne d'un émoji.** Vérifié sur le rendu
 *    et non sur les sources : les commentaires du dépôt en citent (« l'ancien
 *    📁 »), et c'est ce que l'utilisateur voit qui est en cause.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { CentreNotifications } from "@/components/CentreNotifications";
import { FilActivite } from "@/components/FilActivite";
import * as Icones from "@/components/Icones";
import { IndicateursTableauDeBord } from "@/components/IndicateursTableauDeBord";
import { Kanban } from "@/components/Kanban";
import {
  BadgeEtat,
  Bouton,
  BoutonLien,
  Carte,
  Champ,
  ChampListe,
  EnTeteSection,
  EtatVide,
  TuileChiffre,
} from "@/components/Primitives";

import {
  agentFactice,
  coutExecutionFactice,
  evenementFactice,
  projetFactice,
  rendreAvecEtat,
  tacheFactice,
  validationFactice,
} from "./aides";

/**
 * Les émojis pictographiques — ceux que le lot a retirés. La plage exclut à
 * dessein la ponctuation courante (« — », « · », « ↗ ») : c'est le pictogramme
 * coloré qui posait problème, pas le signe typographique.
 */
const EMOJI = /\p{Extended_Pictographic}/u;

/** Toutes les icônes exportées par le jeu, sous leur nom. */
const JEU = Object.entries(Icones).filter(([nom]) => nom.startsWith("Icone"));

describe("le jeu d'icônes (components/Icones)", () => {
  it("rend chaque icône en SVG de trait, à la couleur du texte", () => {
    // `currentColor` est ce qui fait qu'une icône suit son libellé — en thème
    // sombre, dans un badge coloré, ou grisée dans un état vide.
    for (const [nom, Icone] of JEU) {
      const { container, unmount } = render(<Icone />);
      const svg = container.querySelector("svg");
      expect(svg, `${nom} ne rend pas de <svg>`).not.toBeNull();
      expect(svg?.getAttribute("stroke"), nom).toBe("currentColor");
      expect(svg?.getAttribute("viewBox"), nom).toBe("0 0 24 24");
      unmount();
    }
  });

  it("les tient toutes hors de l'arbre d'accessibilité", () => {
    // La promesse « décorative » : un lecteur d'écran ne doit jamais annoncer
    // une icône, puisque le libellé est toujours à côté.
    for (const [nom, Icone] of JEU) {
      const { container, unmount } = render(<Icone />);
      expect(container.querySelector("svg")?.getAttribute("aria-hidden"), nom).toBe(
        "true",
      );
      unmount();
    }
  });

  it("laisse l'appelant poser la taille et la couleur", () => {
    const { container } = render(<Icones.IconeTache className="size-4 text-rose-500" />);
    expect(container.querySelector("svg")?.getAttribute("class")).toContain("size-4");
  });
});

describe("les primitives (components/Primitives)", () => {
  it("porte les deux thèmes dans la brique, pas dans l'appelant", () => {
    const { container } = render(<Carte>du contenu</Carte>);
    const carte = container.firstElementChild as HTMLElement;
    expect(carte.className).toMatch(/\bdark:/);
  });

  it("rend la carte sous la balise que son emploi demande", () => {
    // Une carte dans une liste est un `li`, pas un `article` : la structure du
    // document suit le sens, et c'est elle que le lecteur d'écran annonce.
    const { container } = render(
      <Carte balise="li" ton="creuse" densite="compacte">
        colonne
      </Carte>,
    );
    expect(container.firstElementChild?.tagName).toBe("LI");
  });

  it("relaie les attributs HTML jusqu'à la surface rendue", () => {
    // Ce qui permet à la carte du Kanban (#251) d'être cliquable sans cesser
    // d'être une `Carte` du socle.
    const surClic = vi.fn();
    const { container } = render(<Carte onClick={surClic}>cliquable</Carte>);
    (container.firstElementChild as HTMLElement).click();
    expect(surClic).toHaveBeenCalledOnce();
  });

  it("accepte un chiffre composé, pas seulement une chaîne", () => {
    // Ce dont #247 a besoin : « 2 occupé(s) · 3 libre(s) », l'unité en petit.
    render(
      <TuileChiffre
        libelle="Agents"
        valeur={
          <>
            2<span> occupé(s)</span>
          </>
        }
        titre="2 occupé(s)"
        detail="4 au total"
      />,
    );
    expect(screen.getByTitle("2 occupé(s)")).toHaveTextContent("2 occupé(s)");
  });

  it("mène le renvoi d'une tuile vers la page qui porte le détail", () => {
    render(
      <TuileChiffre
        libelle="Dépense"
        valeur="12,00 $US"
        renvoi={{ href: "/couts", libelle: "Détail par période" }}
      />,
    );
    expect(screen.getByRole("link", { name: /Détail par période/ })).toHaveAttribute(
      "href",
      "/couts",
    );
  });

  it("garde le badge lisible sans sa couleur", () => {
    // La couleur appuie le sens, elle ne le porte jamais seule : une pastille
    // rouge et une verte se ressemblent pour qui ne les distingue pas, et
    // disparaissent à l'impression.
    render(<BadgeEtat ton="alerte">3 échecs</BadgeEtat>);
    expect(screen.getByText("3 échecs")).toBeInTheDocument();
  });

  it("suit la hiérarchie du document dans un en-tête de section", () => {
    // `niveau` porte le sens, pas l'apparence : deux en-têtes se ressemblent,
    // seul le rang change pour la lecture d'écran.
    const { rerender } = render(<EnTeteSection titre="Tâches" />);
    expect(screen.getByRole("heading", { level: 2 })).toBeInTheDocument();
    rerender(<EnTeteSection titre="Étapes" niveau={3} />);
    expect(screen.getByRole("heading", { level: 3 })).toBeInTheDocument();
  });

  it("dit toujours par où obtenir ce qui manque dans un état vide", () => {
    render(
      <EtatVide
        message="Aucun agent déclaré."
        lien={{ href: "/agents", libelle: "Déclarer un agent" }}
      />,
    );
    expect(screen.getByRole("link", { name: "Déclarer un agent" })).toHaveAttribute(
      "href",
      "/agents",
    );
  });
});

/**
 * Les deux briques de #535 (lot 3 de #532). Le gros des tests du chantier est
 * différé au lot 7 (#539) ; ce qui suit couvre ce qui régresserait **en
 * silence** — un état annoncé qui cesse de l'être ne se voit sur aucun écran.
 */
describe("le bouton (Bouton)", () => {
  it("ne soumet rien qu'on ne lui ait demandé de soumettre", () => {
    // Sans `type`, un bouton posé dans un formulaire le soumet : c'est le piège
    // que chaque appel du produit évitait à la main, une fois par bouton.
    render(<Bouton>Enregistrer</Bouton>);
    expect(screen.getByRole("button", { name: "Enregistrer" })).toHaveAttribute(
      "type",
      "button",
    );
  });

  it("distingue « occupé » de « désactivé » : les deux sont inertes, un seul le dit", () => {
    const surClic = vi.fn();
    const { rerender } = render(
      <Bouton disabled onClick={surClic}>
        Envoyer
      </Bouton>,
    );
    const desactive = screen.getByRole("button", { name: "Envoyer" });
    expect(desactive).toBeDisabled();
    expect(desactive).not.toHaveAttribute("aria-busy");

    rerender(
      <Bouton occupe onClick={surClic}>
        Envoi…
      </Bouton>,
    );
    const occupe = screen.getByRole("button", { name: /Envoi/ });
    expect(occupe).toBeDisabled();
    expect(occupe).toHaveAttribute("aria-busy", "true");

    occupe.click();
    expect(surClic).not.toHaveBeenCalled();
  });

  it("porte le ton et la variante en classes nommées, jamais en couleur brute", () => {
    // La promesse du lot : plus un seul `bg-emerald-600` (3,65:1) dans un écran.
    // Le test ne fige pas une teinte — il exige que la couleur vienne d'un
    // **token**, ce que `bg-accent` dit et que `bg-emerald-600` ne dit pas.
    const { container } = render(<Bouton>Approuver</Bouton>);
    const classes = (container.firstElementChild as HTMLElement).className;
    expect(classes).toContain("bg-accent");
    expect(classes).not.toMatch(/bg-(emerald|rose|amber|sky)-\d/);
  });

  it("rend une navigation comme un lien, et non comme un bouton", () => {
    // `BoutonLien` en a l'allure ; ce doit rester ce qui s'ouvre dans un onglet
    // et se copie. Un `<button>` qui navigue perdrait les deux.
    render(<BoutonLien href="/composer">Composer un objectif</BoutonLien>);
    expect(
      screen.getByRole("link", { name: "Composer un objectif" }),
    ).toHaveAttribute("href", "/composer");
    expect(screen.queryByRole("button")).toBeNull();
  });
});

describe("le champ (Champ)", () => {
  it("lie le libellé au contrôle sans dépendre de l'unicité d'un identifiant", () => {
    // Deux instances du même écran dans un document — ce que fait déjà
    // `projet-cadre.test.tsx` : un `htmlFor` résout par `getElementById`, donc
    // la seconde perdrait son nom accessible en silence.
    render(
      <>
        <Champ id="recherche" libelle="Rechercher" type="search" />
        <Champ id="recherche" libelle="Rechercher" type="search" />
      </>,
    );
    expect(screen.getAllByRole("searchbox", { name: "Rechercher" })).toHaveLength(2);
  });

  it("rattache l'aide et l'erreur au champ, et marque la saisie invalide", () => {
    render(
      <Champ
        id="nom-dossier"
        libelle="Nom du dossier"
        aide="Un nom, pas un chemin."
        erreur="Ni « / » ni « \ »."
      />,
    );
    const champ = screen.getByRole("textbox", { name: "Nom du dossier" });
    expect(champ).toHaveAttribute("aria-invalid", "true");
    expect(champ).toHaveAccessibleDescription(/Un nom, pas un chemin/);
    expect(champ).toHaveAccessibleDescription(/Ni « \/ »/);
  });

  it("n'annonce pas d'erreur là où il n'y en a pas", () => {
    render(<Champ id="nom" libelle="Nom du projet" />);
    expect(screen.getByRole("textbox", { name: "Nom du projet" })).not.toHaveAttribute(
      "aria-invalid",
    );
  });

  it("garde le nom accessible d'une liste hors de ses options", () => {
    render(
      <ChampListe id="agent" libelle="Agent">
        <option value="">Tous les agents</option>
        <option value="dev">dev</option>
      </ChampListe>,
    );
    expect(screen.getByRole("combobox", { name: "Agent" })).toBeInTheDocument();
  });
});

describe("plus aucun émoji rendu à l'écran", () => {
  const taches = [
    tacheFactice({ id: "T-1", statut: "en_cours" }),
    tacheFactice({ id: "T-2", statut: "echec", titre: "Une tâche en échec" }),
  ];
  const evenements = [
    evenementFactice({ type: "tache.statut", statut: "terminee" }),
    evenementFactice({ type: "agent.activite", statut: "refus_outil" }),
    evenementFactice({ type: "validation.demande" }),
    evenementFactice({ type: "trucmuche.inedit" }),
  ];

  it("ni sur les tuiles de tête, ni sur le Kanban", () => {
    const { container } = render(
      <>
        <IndicateursTableauDeBord
          taches={taches}
          agents={[agentFactice()]}
          couts={[coutExecutionFactice()]}
        />
        <Kanban
          taches={taches}
          agents={[agentFactice()]}
          reassigner={vi.fn()}
          projet={projetFactice()}
        />
      </>,
    );
    expect(container.textContent ?? "").not.toMatch(EMOJI);
  });

  it("ni sur le fil d'activité, quel que soit le type d'événement", () => {
    const { container } = render(<FilActivite evenements={evenements} />);
    expect(container.textContent ?? "").not.toMatch(EMOJI);
  });

  it("ni dans le centre de notifications, arbitrages compris", () => {
    const { container } = rendreAvecEtat(<CentreNotifications />, {
      evenements,
      validations: [validationFactice()],
    });
    expect(container.textContent ?? "").not.toMatch(EMOJI);
  });

  it("ni dans le détail d'une tâche, liens utiles compris", async () => {
    // L'écran le plus exposé : #251 a été écrit avant que le socle ne soit
    // posé, et signait encore ses lignes d'un 🤖 en en-tête et d'un
    // 🎨 / 🎫 / 📦 / 🔗 par nature de lien. Les natures sont toutes montées
    // ici — c'est une table, un cas oublié n'aurait pas d'autre garde.
    const detaillee = tacheFactice({
      titre: "Écrire les tests",
      description: "Couvrir le panneau et ses trois sections.",
      etapes: [
        { libelle: "Lire le ticket", etat: "faite" },
        { libelle: "Écrire le composant", etat: "en_cours" },
        { libelle: "Passer la revue", etat: "a_faire" },
      ],
      liens: [
        { libelle: "Écran", url: "https://figma.test/f/1", nature: "maquette" },
        { libelle: "#251", url: "https://gitlab.test/i/251", nature: "ticket" },
        { libelle: "maestro", url: "https://gitlab.test/m", nature: "depot" },
        { libelle: "Ailleurs", url: "https://exemple.test", nature: "inedit" },
        { libelle: "Sans URL", url: "javascript:alert(1)", nature: "ticket" },
      ],
    });
    const { container } = render(
      <Kanban
        taches={[detaillee]}
        agents={[agentFactice()]}
        reassigner={vi.fn()}
        projet={projetFactice()}
      />,
    );
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: /Ouvrir le détail/ }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(container.ownerDocument.body.textContent ?? "").not.toMatch(EMOJI);
  });
});

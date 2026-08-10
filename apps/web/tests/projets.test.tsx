/**
 * L'écran Projets (#225) — la voie front de la Phase 7.
 *
 * Ce que ces tests protègent en propre, au-delà du rendu :
 *
 * 1. **aucun chemin absolu ne se tape** — la racine vient toujours d'un chemin
 *    énuméré par l'API, y compris pour un dossier à créer (où l'utilisateur ne
 *    saisit qu'un *nom*). Un champ de saisie libre pour la racine ferait
 *    tomber le critère sans qu'aucun autre test s'en aperçoive ;
 * 2. **un refus s'affiche avec son motif et ne casse rien** (EF-38) — ni la
 *    liste, ni la navigation de l'explorateur, ni la saisie en cours ;
 * 3. **un dossier vide n'est pas un refus** — la distinction que docs/05 §6.7
 *    pose côté API doit rester visible côté écran, sans quoi une frontière se
 *    lit comme un dossier sans contenu.
 *
 * Les tests Python de la phase sont différés au lot 8 (#220) ; ceux-ci suivent
 * la convention de la Phase 6 — le composant arrive avec les siens.
 *
 * Les deux derniers `describe` sont le lot 6 de #276 (#282) et couvrent #278,
 * livré sans tests côté écran : le **sélecteur natif** et les **points
 * d'entrée**. Ce qui s'y mesure est moins l'ouverture d'une fenêtre que
 * l'absence de cul-de-sac dans les cinq façons dont elle peut ne pas s'ouvrir —
 * indisponible, annulée, refusée, non déclarable, injoignable. C'est en
 * l'écrivant qu'est apparu le défaut corrigé au passage dans
 * `ExplorateurDossiers.ouvrir` : le motif d'un dossier non déclarable était
 * posé, puis **effacé par le succès de l'ouverture** qui suivait.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ListeProjets } from "@/components/projets/ListeProjets";
import { ErreurProjet } from "@/lib/api";
import type { ChoixSelecteur, DisponibiliteSelecteur } from "@/lib/types";
import {
  cheminEnfant,
  motifsDepuisTexte,
  nomDossierValide,
  texteDepuisMotifs,
} from "@/lib/projets";

import { dossierFactice, pageExplorateurFactice, projetFactice } from "./aides";

const chargerProjets = vi.fn();
const chargerExplorateur = vi.fn();
const creerProjet = vi.fn();
const modifierProjet = vi.fn();
const supprimerProjet = vi.fn();
const chargerDisponibiliteSelecteur = vi.fn();
const ouvrirSelecteurNatif = vi.fn();

// `importOriginal` plutôt qu'un objet nu : `ErreurProjet` doit rester **la**
// classe du module, sinon le `instanceof` qui distingue un refus motivé d'une
// panne réseau ne reconnaîtrait plus rien et tous les motifs tomberaient sur
// « api-injoignable ».
vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...reel,
    chargerProjets: () => chargerProjets(),
    chargerExplorateur: (chemin: string | null) => chargerExplorateur(chemin),
    creerProjet: (declaration: unknown) => creerProjet(declaration),
    modifierProjet: (id: string, declaration: unknown) =>
      modifierProjet(id, declaration),
    supprimerProjet: (id: string) => supprimerProjet(id),
    chargerDisponibiliteSelecteur: () => chargerDisponibiliteSelecteur(),
    ouvrirSelecteurNatif: (depart: string | null) => ouvrirSelecteurNatif(depart),
  };
});

/** L'explorateur montre « D:/projets », qui contient le dépôt « depensio ». */
function pageProjets() {
  return pageExplorateurFactice({
    chemin: "D:/projets",
    parent: null,
    dossiers: [
      dossierFactice({
        nom: "depensio",
        chemin: "D:/projets/depensio",
        depot_git: true,
      }),
    ],
  });
}

/**
 * Le sélecteur natif **indisponible** par défaut : c'est l'état de la plupart
 * des tests de ce fichier, qui n'en parlent pas, et celui d'un backend distant.
 * Chaque test du sélecteur pose le sien.
 */
function selecteurIndisponible(
  motif = "selecteur-hors-poste",
  message = "Backend distant : le dialogue de l'OS s'ouvrirait sur le serveur.",
): DisponibiliteSelecteur {
  return { disponible: false, motif, message, outil: null };
}

beforeEach(() => {
  vi.clearAllMocks();
  chargerProjets.mockResolvedValue([]);
  chargerExplorateur.mockResolvedValue(pageProjets());
  creerProjet.mockResolvedValue(projetFactice());
  modifierProjet.mockResolvedValue(projetFactice());
  supprimerProjet.mockResolvedValue(undefined);
  chargerDisponibiliteSelecteur.mockResolvedValue(selecteurIndisponible());
  ouvrirSelecteurNatif.mockResolvedValue({
    annule: true,
    chemin: null,
    racine_valide: false,
    refus: null,
  });
});

/** La page rendue, une fois le premier chargement passé. */
async function page() {
  render(<ListeProjets />);
  return await screen.findByRole("region", { name: "Projets déclarés" });
}

/** Le formulaire de création, explorateur ouvert sur « D:/projets ». */
async function formulaireAvecExplorateur(utilisateur: ReturnType<typeof userEvent.setup>) {
  await page();
  await utilisateur.click(screen.getByRole("button", { name: /Nouveau projet/ }));
  await utilisateur.click(
    screen.getByRole("button", { name: /Choisir un dossier/ }),
  );
  return await screen.findByRole("region", {
    name: "Explorateur de dossiers",
  });
}

describe("la liste des projets", () => {
  it("montre la racine, l'origine, le périmètre et le VCS constaté", async () => {
    chargerProjets.mockResolvedValue([projetFactice()]);
    await page();

    const carte = await screen.findByRole("listitem", {
      name: "Projet Dépensio",
    });
    expect(carte).toHaveTextContent("D:/projets/depensio");
    expect(carte).toHaveTextContent("Dossier existant");
    expect(carte).toHaveTextContent("git · main");
    expect(carte).toHaveTextContent("node_modules");
  });

  it("dit « non versionné » plutôt que de taire l'absence de dépôt", async () => {
    // Le `vcs` décide du patron d'écriture de l'espace de travail (#224) :
    // l'omettre laisserait croire à un worktree Git là où il y aura une copie.
    chargerProjets.mockResolvedValue([projetFactice({ vcs: null })]);
    await page();
    expect(
      await screen.findByRole("listitem", { name: "Projet Dépensio" }),
    ).toHaveTextContent("Non versionné");
  });

  it("explique un backlog vide au lieu de n'afficher qu'une liste absente", async () => {
    await page();
    expect(
      await screen.findByText(/Aucun projet déclaré/),
    ).toBeInTheDocument();
  });

  it("annonce une API injoignable sans masquer le reste de l'écran", async () => {
    chargerProjets.mockRejectedValue(new Error("fetch failed"));
    await page();
    expect(await screen.findByRole("alert")).toHaveTextContent("fetch failed");
    // La page reste montée : le bouton de déclaration répond encore.
    expect(
      screen.getByRole("button", { name: /Nouveau projet/ }),
    ).toBeInTheDocument();
    // Mais elle ne prétend pas que le backlog est vide : on n'a rien pu lire,
    // ce qui n'est pas la même chose que n'avoir rien à lire.
    expect(screen.queryByText(/Aucun projet déclaré/)).toBeNull();
  });
});

describe("le choix de la racine (explorateur servi par l'API)", () => {
  it("déclare un projet sur un dossier énuméré, sans qu'un chemin soit saisi", async () => {
    const utilisateur = userEvent.setup();
    const explorateur = await formulaireAvecExplorateur(utilisateur);

    // Le dépôt Git se repère avant d'être choisi.
    expect(await within(explorateur).findByText("depensio")).toBeInTheDocument();
    expect(explorateur).toHaveTextContent("dépôt Git");

    await utilisateur.click(
      within(explorateur).getByRole("button", { name: "Choisir depensio" }),
    );

    // Le nom du dossier fait un premier jet de nom de projet.
    expect(screen.getByLabelText("Nom du projet")).toHaveValue("depensio");
    // Aucun champ de saisie ne porte la racine : elle n'est qu'affichée.
    expect(screen.queryByRole("textbox", { name: /[Rr]acine/ })).toBeNull();

    await utilisateur.click(
      screen.getByRole("button", { name: "Déclarer le projet" }),
    );

    expect(creerProjet).toHaveBeenCalledWith({
      nom: "depensio",
      racine: "D:/projets/depensio",
      origine: "existant",
      inclus: null,
      exclus: null,
    });
    // La liste se relit : la racine canonicalisée et le VCS viennent du backend.
    await waitFor(() => expect(chargerProjets).toHaveBeenCalledTimes(2));
  });

  it("navigue dans l'arborescence en demandant chaque page au backend", async () => {
    const utilisateur = userEvent.setup();
    const explorateur = await formulaireAvecExplorateur(utilisateur);

    expect(chargerExplorateur).toHaveBeenCalledWith(null);
    await utilisateur.click(
      await within(explorateur).findByRole("button", { name: "Ouvrir depensio" }),
    );
    expect(chargerExplorateur).toHaveBeenLastCalledWith("D:/projets/depensio");
  });

  it("refuse de rechoisir un dossier déjà déclaré", async () => {
    chargerExplorateur.mockResolvedValue(
      pageExplorateurFactice({
        chemin: "D:/projets",
        dossiers: [dossierFactice({ projet_id: "prj-7f3a1c2b" })],
      }),
    );
    const utilisateur = userEvent.setup();
    const explorateur = await formulaireAvecExplorateur(utilisateur);

    expect(explorateur).toHaveTextContent("déjà déclaré");
    expect(
      await within(explorateur).findByRole("button", {
        name: "Choisir depensio",
      }),
    ).toBeDisabled();
  });

  it("compose la racine d'un nouveau dossier à partir d'un parent énuméré", async () => {
    // Le cas que l'explorateur ne peut pas montrer — le dossier n'existe pas
    // encore. La saisie se réduit alors à un **nom**, jamais à un chemin.
    const utilisateur = userEvent.setup();
    await page();
    await utilisateur.click(
      screen.getByRole("button", { name: /Nouveau projet/ }),
    );
    await utilisateur.click(screen.getByLabelText("Nouveau dossier"));
    await utilisateur.click(
      screen.getByRole("button", { name: /Choisir un dossier/ }),
    );
    const explorateur = await screen.findByRole("region", {
      name: "Explorateur de dossiers",
    });
    await utilisateur.click(
      within(explorateur).getByRole("button", { name: "Choisir ce dossier" }),
    );

    await utilisateur.type(
      screen.getByLabelText("Nom du dossier à créer"),
      "depensio",
    );
    await utilisateur.type(screen.getByLabelText("Nom du projet"), "Dépensio");
    await utilisateur.click(
      screen.getByRole("button", { name: "Déclarer le projet" }),
    );

    expect(creerProjet).toHaveBeenCalledWith({
      nom: "Dépensio",
      racine: "D:/projets/depensio",
      origine: "nouveau",
      inclus: null,
      exclus: null,
    });
  });

  it("barre un nom de dossier qui serait en fait un chemin", async () => {
    const utilisateur = userEvent.setup();
    await page();
    await utilisateur.click(
      screen.getByRole("button", { name: /Nouveau projet/ }),
    );
    await utilisateur.click(screen.getByLabelText("Nouveau dossier"));
    await utilisateur.click(
      screen.getByRole("button", { name: /Choisir un dossier/ }),
    );
    const explorateur = await screen.findByRole("region", {
      name: "Explorateur de dossiers",
    });
    await utilisateur.click(
      within(explorateur).getByRole("button", { name: "Choisir ce dossier" }),
    );

    await utilisateur.type(screen.getByLabelText("Nom du projet"), "Dépensio");
    await utilisateur.type(
      screen.getByLabelText("Nom du dossier à créer"),
      "../ailleurs",
    );

    expect(
      screen.getByRole("button", { name: "Déclarer le projet" }),
    ).toBeDisabled();
    expect(screen.getByText(/pas un chemin/)).toBeInTheDocument();
  });
});

describe("l'explorateur rendu dans le formulaire de projet (#312)", () => {
  /**
   * Le formulaire **prêt à partir** (nom + racine), explorateur rouvert.
   *
   * C'est la seule mise en scène où une soumission fautive se voit : tant que
   * la racine manque, « Déclarer le projet » est désactivé et une `Entrée`
   * égarée ne prouverait rien — le bug de #312 passerait au vert.
   */
  async function formulairePretExplorateurOuvert(
    utilisateur: ReturnType<typeof userEvent.setup>,
  ) {
    const explorateur = await formulaireAvecExplorateur(utilisateur);
    await utilisateur.click(
      await within(explorateur).findByRole("button", {
        name: "Choisir depensio",
      }),
    );
    expect(
      screen.getByRole("button", { name: "Déclarer le projet" }),
    ).toBeEnabled();
    await utilisateur.click(
      screen.getByRole("button", { name: /Changer de dossier/ }),
    );
    return await screen.findByLabelText("Aller à un chemin absolu");
  }

  it("n'imbrique aucun <form> dans celui du formulaire", async () => {
    // HTML interdit les `<form>` imbriqués : l'analyseur jette le formulaire
    // interne du HTML rendu côté serveur alors que React le crée côté client,
    // d'où l'erreur d'hydratation. Le composant est partagé — il doit rester
    // rendable dans un formulaire comme hors d'un formulaire.
    const utilisateur = userEvent.setup();
    const explorateur = await formulaireAvecExplorateur(utilisateur);
    await within(explorateur).findByText("depensio");

    expect(explorateur.querySelector("form")).toBeNull();
    expect(document.querySelectorAll("form")).toHaveLength(1);
  });

  it("ouvre le chemin saisi sur Entrée, sans déclarer le projet", async () => {
    const utilisateur = userEvent.setup();
    const barre = await formulairePretExplorateurOuvert(utilisateur);

    await utilisateur.type(barre, "D:/depots{Enter}");

    expect(chargerExplorateur).toHaveBeenLastCalledWith("D:/depots");
    expect(creerProjet).not.toHaveBeenCalled();
  });

  it("ne déclare rien non plus sur une Entrée à vide", async () => {
    // La soumission implicite du navigateur est coupée *avant* de regarder la
    // saisie : sinon un champ vide laisserait passer `Entrée` jusqu'au
    // formulaire porteur — le geste le plus banal des deux.
    const utilisateur = userEvent.setup();
    const barre = await formulairePretExplorateurOuvert(utilisateur);
    const lectures = chargerExplorateur.mock.calls.length;

    await utilisateur.type(barre, "{Enter}");

    expect(creerProjet).not.toHaveBeenCalled();
    expect(chargerExplorateur).toHaveBeenCalledTimes(lectures);
  });

  it("ouvre le chemin par le bouton « Aller », qui ne soumet rien non plus", async () => {
    const utilisateur = userEvent.setup();
    const barre = await formulairePretExplorateurOuvert(utilisateur);

    await utilisateur.type(barre, "D:/depots");
    await utilisateur.click(screen.getByRole("button", { name: "Aller" }));

    expect(chargerExplorateur).toHaveBeenLastCalledWith("D:/depots");
    expect(creerProjet).not.toHaveBeenCalled();
  });
});

describe("le sélecteur de dossier natif (#278)", () => {
  /** Le sélecteur ouvrable, et le dialogue qui rendra `choix`. */
  function selecteurOuvrable(choix: Partial<ChoixSelecteur> = {}) {
    chargerDisponibiliteSelecteur.mockResolvedValue({
      disponible: true,
      motif: null,
      message: "Le dialogue de dossier de votre poste peut être ouvert.",
      outil: "powershell",
    });
    ouvrirSelecteurNatif.mockResolvedValue({
      annule: false,
      chemin: "D:/projets/depensio",
      racine_valide: true,
      refus: null,
      ...choix,
    });
  }

  const bouton = () =>
    screen.queryByRole("button", { name: /Parcourir sur mon poste/ });

  it("propose le dialogue du poste quand le backend peut l'ouvrir", async () => {
    selecteurOuvrable();
    const utilisateur = userEvent.setup();
    await formulaireAvecExplorateur(utilisateur);

    await waitFor(() => expect(bouton()).toBeInTheDocument());
  });

  it("dit pourquoi, plutôt que d'offrir un bouton mort, en mode serveur", async () => {
    // Le troisième critère de #278 : un backend distant ouvrirait sa fenêtre
    // sur le serveur, devant personne. Un bouton ferait croire à une panne, un
    // silence ferait croire que la fonction n'existe pas.
    const utilisateur = userEvent.setup();
    const explorateur = await formulaireAvecExplorateur(utilisateur);

    expect(
      await within(explorateur).findByText(/le dialogue de l'OS s'ouvrirait/i),
    ).toBeInTheDocument();
    expect(bouton()).not.toBeInTheDocument();
  });

  it("garde la saisie d'un chemin comme repli, disponible ou non", async () => {
    // Le repli qui marche partout, y compris en mode serveur : c'est l'API qui
    // vérifie le chemin, jamais le navigateur. Sans lui, un poste sans dialogue
    // natif serait revenu au cul-de-sac que ce lot ferme.
    const utilisateur = userEvent.setup();
    const explorateur = await formulaireAvecExplorateur(utilisateur);

    expect(
      within(explorateur).getByLabelText("Aller à un chemin absolu"),
    ).toBeInTheDocument();
  });

  it("choisit directement un dossier déclarable", async () => {
    selecteurOuvrable();
    const utilisateur = userEvent.setup();
    await formulaireAvecExplorateur(utilisateur);
    await waitFor(() => expect(bouton()).toBeInTheDocument());

    await utilisateur.click(bouton()!);

    expect(await screen.findByText(/Racine déclarée/)).toHaveTextContent(
      "D:/projets/depensio",
    );
  });

  it("ouvre l'explorateur sur un dossier lisible mais non déclarable, motif affiché", async () => {
    // La troisième issue, et la seule qui demandait une décision : un `D:/`
    // choisi au dialogue est lisible et non déclarable. Le traiter en erreur
    // renverrait à zéro ; on affiche le motif et on ouvre l'explorateur
    // **dessus**, de quoi descendre d'un cran.
    selecteurOuvrable({
      chemin: "D:/",
      racine_valide: false,
      refus: {
        motif: "racine-de-disque",
        message: "Une racine de disque ne peut pas être un projet.",
      },
    });
    const utilisateur = userEvent.setup();
    await formulaireAvecExplorateur(utilisateur);
    await waitFor(() => expect(bouton()).toBeInTheDocument());

    await utilisateur.click(bouton()!);

    expect(await screen.findByText(/racine de disque/i)).toBeInTheDocument();
    await waitFor(() => expect(chargerExplorateur).toHaveBeenLastCalledWith("D:/"));
  });

  it("ne touche à rien quand la fenêtre est fermée", async () => {
    // Annuler est un geste normal : ni racine posée, ni refus affiché, ni
    // explorateur déplacé — sans quoi fermer la fenêtre par réflexe punirait.
    selecteurOuvrable({ annule: true, chemin: null, racine_valide: false });
    const utilisateur = userEvent.setup();
    await formulaireAvecExplorateur(utilisateur);
    await waitFor(() => expect(bouton()).toBeInTheDocument());
    const lectures = chargerExplorateur.mock.calls.length;

    await utilisateur.click(bouton()!);

    expect(screen.queryByText(/Racine déclarée/)).toBeNull();
    expect(screen.getByText("aucun dossier choisi")).toBeInTheDocument();
    expect(chargerExplorateur).toHaveBeenCalledTimes(lectures);
  });

  it("montre le motif d'un empêchement au lieu de laisser le clic sans effet", async () => {
    selecteurOuvrable();
    ouvrirSelecteurNatif.mockRejectedValue(
      new ErreurProjet(
        "selecteur-en-cours",
        "Un dialogue de dossier est déjà ouvert.",
      ),
    );
    const utilisateur = userEvent.setup();
    await formulaireAvecExplorateur(utilisateur);
    await waitFor(() => expect(bouton()).toBeInTheDocument());

    await utilisateur.click(bouton()!);

    // Le bandeau porte les trois choses d'un refus (EF-38) : la phrase du
    // backend, le geste qui en sort, et le motif brut affiché tel quel.
    const bandeau = await screen.findByRole("alert");
    expect(bandeau).toHaveTextContent("Un dialogue de dossier est déjà ouvert.");
    expect(bandeau).toHaveTextContent("selecteur-en-cours");
  });

  it("reste un explorateur utilisable quand la disponibilité est injoignable", async () => {
    // L'état du sélecteur n'est pas une dépendance de l'explorateur : son échec
    // retombe sur « pas de bouton », pas sur une panne d'écran.
    chargerDisponibiliteSelecteur.mockRejectedValue(new Error("réseau"));
    const utilisateur = userEvent.setup();
    const explorateur = await formulaireAvecExplorateur(utilisateur);

    expect(await within(explorateur).findByText("depensio")).toBeInTheDocument();
    expect(bouton()).not.toBeInTheDocument();
  });
});

describe("les points d'entrée de l'explorateur (#278)", () => {
  it("dit d'où vient chaque dossier proposé à l'arrivée", async () => {
    // La frontière s'est élargie aux volumes du poste : sans son origine, la
    // page d'entrée serait une liste de chemins sans hiérarchie de sens, où
    // « D:/ » et « le dossier où je range mes dépôts » se ressembleraient.
    chargerExplorateur.mockResolvedValue(
      pageExplorateurFactice({
        chemin: null,
        parent: null,
        racines: ["C:/"],
        dossiers: [
          dossierFactice({
            nom: "moi",
            chemin: "C:/Users/moi",
            origine: "utilisateur",
          }),
          dossierFactice({
            nom: "depots",
            chemin: "D:/depots",
            origine: "recent",
          }),
          dossierFactice({ nom: "D:/", chemin: "D:/", origine: "volume" }),
        ],
      }),
    );
    const utilisateur = userEvent.setup();
    const explorateur = await formulaireAvecExplorateur(utilisateur);

    expect(await within(explorateur).findByText("dossier utilisateur")).toBeInTheDocument();
    expect(within(explorateur).getByText("récent")).toBeInTheDocument();
    expect(within(explorateur).getByText("disque")).toBeInTheDocument();
  });

  it("n'étiquette pas les sous-dossiers d'un dossier ouvert", async () => {
    // `origine` est propre à la page d'entrée (elle est `null` ailleurs) :
    // l'afficher partout ferait passer une raison d'être proposé pour une
    // propriété du dossier.
    const utilisateur = userEvent.setup();
    const explorateur = await formulaireAvecExplorateur(utilisateur);
    await within(explorateur).findByText("depensio");

    expect(within(explorateur).queryByText("dossier utilisateur")).toBeNull();
    expect(within(explorateur).queryByText("disque")).toBeNull();
  });
});

describe("un refus motivé (EF-38)", () => {
  it("montre le motif d'une racine refusée sans perdre la saisie ni la liste", async () => {
    chargerProjets.mockResolvedValue([projetFactice()]);
    creerProjet.mockRejectedValue(
      new ErreurProjet(
        "chemin-sensible",
        "Zone sensible : C:/Users/moi/.ssh est protégé",
      ),
    );
    const utilisateur = userEvent.setup();
    const explorateur = await formulaireAvecExplorateur(utilisateur);
    await utilisateur.click(
      await within(explorateur).findByRole("button", {
        name: "Choisir depensio",
      }),
    );
    await utilisateur.click(
      screen.getByRole("button", { name: "Déclarer le projet" }),
    );

    const refus = await screen.findByRole("alert");
    expect(refus).toHaveTextContent("Zone sensible");
    expect(refus).toHaveTextContent("chemin-sensible");
    // Le conseil prolonge le message du backend, il ne le remplace pas.
    expect(refus).toHaveTextContent(/Zone protégée/);

    // L'écran reste utilisable : la liste est là, la saisie aussi.
    expect(
      screen.getByRole("listitem", { name: "Projet Dépensio" }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Nom du projet")).toHaveValue("depensio");
  });

  it("garde la navigation de l'explorateur quand un dossier est refusé", async () => {
    const utilisateur = userEvent.setup();
    const explorateur = await formulaireAvecExplorateur(utilisateur);
    await within(explorateur).findByText("depensio");

    chargerExplorateur.mockRejectedValueOnce(
      new ErreurProjet(
        "hors-racines-explorables",
        "Dossier hors des racines explorables : C:/ailleurs",
      ),
    );
    await utilisateur.click(
      within(explorateur).getByRole("button", { name: "Ouvrir depensio" }),
    );

    const refus = await within(explorateur).findByRole("alert");
    expect(refus).toHaveTextContent("hors-racines-explorables");
    expect(refus).toHaveTextContent("MAESTRO_EXPLORATEUR_RACINES");
    // La page précédente est toujours là, et une porte de sortie est offerte.
    expect(within(explorateur).getByText("depensio")).toBeInTheDocument();
    expect(
      within(explorateur).getByRole("button", {
        name: "Revenir aux dossiers explorables",
      }),
    ).toBeInTheDocument();
  });

  it("distingue un dossier sans sous-dossier d'un dossier refusé", async () => {
    // Le piège que docs/05 §6.7 nomme : rendre une liste vide sur un refus
    // rendrait l'explorateur illisible — « rien ici » et « je refuse de
    // regarder là » se liraient pareil.
    chargerExplorateur.mockResolvedValue(
      pageExplorateurFactice({ chemin: "D:/projets/vide", dossiers: [] }),
    );
    const utilisateur = userEvent.setup();
    const explorateur = await formulaireAvecExplorateur(utilisateur);

    expect(
      await within(explorateur).findByText(/Aucun sous-dossier ici/),
    ).toBeInTheDocument();
    expect(within(explorateur).queryByRole("alert")).toBeNull();
  });

  it("signale une liste tronquée plutôt que de la couper en silence", async () => {
    chargerExplorateur.mockResolvedValue(
      pageExplorateurFactice({ chemin: "D:/projets", tronque: true }),
    );
    const utilisateur = userEvent.setup();
    const explorateur = await formulaireAvecExplorateur(utilisateur);
    expect(
      await within(explorateur).findByText(/plus de 500 sous-dossiers/),
    ).toBeInTheDocument();
  });
});

describe("la modification et la suppression", () => {
  it("renvoie la déclaration entière, périmètre compris (PUT intégral)", async () => {
    chargerProjets.mockResolvedValue([projetFactice()]);
    const utilisateur = userEvent.setup();
    await page();
    await utilisateur.click(
      await screen.findByRole("button", { name: "Modifier" }),
    );

    const nom = screen.getByLabelText("Nom du projet");
    expect(nom).toHaveValue("Dépensio");
    await utilisateur.clear(nom);
    await utilisateur.type(nom, "Dépensio v2");
    // L'origine raconte comment le projet est né : elle n'est plus éditable.
    expect(screen.queryByRole("radio")).toBeNull();

    await utilisateur.click(
      screen.getByRole("button", { name: "Enregistrer les modifications" }),
    );

    expect(modifierProjet).toHaveBeenCalledWith("prj-7f3a1c2b", {
      nom: "Dépensio v2",
      racine: "D:/projets/depensio",
      origine: "existant",
      inclus: ["."],
      exclus: [".git", "node_modules", ".env", "**/secrets/**"],
    });
  });

  it("arme la suppression en deux temps et ne promet que d'oublier", async () => {
    chargerProjets.mockResolvedValue([projetFactice()]);
    const utilisateur = userEvent.setup();
    await page();

    await utilisateur.click(
      await screen.findByRole("button", { name: "Supprimer" }),
    );
    expect(supprimerProjet).not.toHaveBeenCalled();
    expect(screen.getByText(/le dossier reste sur le disque/)).toBeInTheDocument();

    await utilisateur.click(
      screen.getByRole("button", { name: "Confirmer la suppression" }),
    );
    expect(supprimerProjet).toHaveBeenCalledWith("prj-7f3a1c2b");
    await waitFor(() => expect(chargerProjets).toHaveBeenCalledTimes(2));
  });

  it("laisse reculer avant de confirmer", async () => {
    chargerProjets.mockResolvedValue([projetFactice()]);
    const utilisateur = userEvent.setup();
    await page();

    await utilisateur.click(
      await screen.findByRole("button", { name: "Supprimer" }),
    );
    await utilisateur.click(
      screen.getByRole("button", { name: "Garder le projet" }),
    );
    expect(supprimerProjet).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Supprimer" })).toBeInTheDocument();
  });

  it("montre le motif d'une suppression refusée sur la carte concernée", async () => {
    chargerProjets.mockResolvedValue([projetFactice()]);
    supprimerProjet.mockRejectedValue(
      new ErreurProjet("projet-inconnu", "Projet inconnu : prj-7f3a1c2b"),
    );
    const utilisateur = userEvent.setup();
    await page();

    await utilisateur.click(
      await screen.findByRole("button", { name: "Supprimer" }),
    );
    await utilisateur.click(
      screen.getByRole("button", { name: "Confirmer la suppression" }),
    );

    const carte = screen.getByRole("listitem", { name: "Projet Dépensio" });
    expect(await within(carte).findByRole("alert")).toHaveTextContent(
      "projet-inconnu",
    );
  });
});

describe("le vocabulaire du périmètre (lib/projets)", () => {
  it("rend null sur une saisie vide — « laisse les défauts », pas « n'inclus rien »", () => {
    expect(motifsDepuisTexte("   ")).toBeNull();
    expect(motifsDepuisTexte("src, docs")).toEqual(["src", "docs"]);
    expect(motifsDepuisTexte("src, , docs ,")).toEqual(["src", "docs"]);
  });

  it("fait l'aller-retour entre la liste d'API et la ligne de saisie", () => {
    expect(motifsDepuisTexte(texteDepuisMotifs([".git", "node_modules"]))).toEqual(
      [".git", "node_modules"],
    );
  });

  it("compose un chemin d'enfant sans doubler le séparateur", () => {
    expect(cheminEnfant("D:/projets", "depensio")).toBe("D:/projets/depensio");
    expect(cheminEnfant("D:/projets/", "depensio")).toBe("D:/projets/depensio");
    expect(cheminEnfant("D:/projets", "  ")).toBe("D:/projets");
  });

  it("ne prend pour nom de dossier qu'un nom de dossier", () => {
    expect(nomDossierValide("depensio")).toBe(true);
    expect(nomDossierValide("a/b")).toBe(false);
    expect(nomDossierValide("a\\b")).toBe(false);
    expect(nomDossierValide("..")).toBe(false);
    expect(nomDossierValide(" ")).toBe(false);
  });
});

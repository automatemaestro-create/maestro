/**
 * L'écran Projets (#225) — la voie front de la Phase 7.
 *
 * Ce que ces tests protègent en propre, au-delà du rendu :
 *
 * 1. **aucun chemin absolu ne se tape** — la racine vient toujours d'un chemin
 *    énuméré par l'API. Un champ de saisie libre pour la racine ferait
 *    tomber le critère sans qu'aucun autre test s'en aperçoive ;
 *
 * ⚠ Depuis #1294 **un projet ne se crée plus ici** : il naît dans la
 * conversation, sur la porte d'entrée (`tests/projet-actif.test.tsx`).
 * « Nouveau projet » y renvoie (`nouveauProjet`), et le formulaire ne sert plus
 * qu'à **modifier** — c'est donc par « Modifier » que ces tests atteignent
 * l'explorateur, le dialogue du poste et les refus motivés, qui n'ont pas bougé.
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
 *
 * Le `describe` « mise sous Git » est #855 : le seul geste de l'écran qui
 * écrive dans le dossier de l'utilisateur. Ce qui s'y garde : il n'est proposé
 * que sur un projet **non versionné**, il s'arme en deux temps derrière une
 * confirmation qui **dit ce qui va être fait** (`git init`, premier commit de
 * toute la racine, `.gitignore` respecté) sans qu'aucun appel parte avant le
 * second clic, la liste se **relit** après (le `vcs` est constaté, jamais
 * recopié de la réponse), et un refus s'affiche **sur la carte** avec son motif
 * et le conseil qui va avec.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ListeProjets } from "@/components/projets/ListeProjets";
import { ErreurProjet } from "@/lib/api";
import type {
  ChoixSelecteur,
  DisponibiliteSelecteur,
  Projet,
  RepertoireProjets,
} from "@/lib/types";
import { motifsDepuisTexte, texteDepuisMotifs } from "@/lib/projets";

import { dossierFactice, pageExplorateurFactice, projetFactice } from "./aides";

const chargerProjets = vi.fn();
const chargerExplorateur = vi.fn();
const creerProjet = vi.fn();
const nouveauProjet = vi.fn();
const modifierProjet = vi.fn();
const supprimerProjet = vi.fn();
const versionnerProjet = vi.fn();
const chargerDisponibiliteSelecteur = vi.fn();
const ouvrirSelecteurNatif = vi.fn();
const chargerRepertoireProjets = vi.fn();

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
    versionnerProjet: (id: string) => versionnerProjet(id),
    chargerDisponibiliteSelecteur: () => chargerDisponibiliteSelecteur(),
    ouvrirSelecteurNatif: (depart: string | null) => ouvrirSelecteurNatif(depart),
    chargerRepertoireProjets: () => chargerRepertoireProjets(),
  };
});

/** Le répertoire des projets tel que l'API le rend (#1022). */
function repertoireFactice(
  surcharges: Partial<RepertoireProjets> = {},
): RepertoireProjets {
  return {
    chemin: "D:/projets",
    par_defaut: true,
    existe: true,
    cree: false,
    refus: null,
    ...surcharges,
  };
}

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
  versionnerProjet.mockResolvedValue(projetFactice());
  chargerDisponibiliteSelecteur.mockResolvedValue(selecteurIndisponible());
  chargerRepertoireProjets.mockResolvedValue(repertoireFactice());
  ouvrirSelecteurNatif.mockResolvedValue({
    annule: true,
    chemin: null,
    racine_valide: false,
    refus: null,
  });
});

/** La page rendue, une fois le premier chargement passé. */
async function page() {
  render(<ListeProjets nouveauProjet={nouveauProjet} />);
  return await screen.findByRole("region", { name: "Projets déclarés" });
}

/**
 * Le projet qu'on modifie pour atteindre l'explorateur : sa racine n'est **pas**
 * dans la page que l'explorateur montre, pour que choisir « depensio » soit un
 * vrai changement.
 */
const ANCIEN = projetFactice({ nom: "Ancien", racine: "D:/anciens/ancien" });

/**
 * Le formulaire de modification d'« Ancien », explorateur ouvert — les `autres`
 * projets restant listés à côté.
 */
async function formulaireAvecExplorateur(
  utilisateur: ReturnType<typeof userEvent.setup>,
  autres: Projet[] = [],
) {
  chargerProjets.mockResolvedValue([ANCIEN, ...autres]);
  await page();
  const carte = await screen.findByRole("listitem", { name: "Projet Ancien" });
  await utilisateur.click(within(carte).getByRole("button", { name: "Modifier" }));
  await utilisateur.click(
    screen.getByRole("button", { name: /Changer de dossier/ }),
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

describe("la création quitte l'écran Projets (#1294)", () => {
  it("« Nouveau projet » renvoie à la conversation, sans formulaire ni déclaration", async () => {
    const utilisateur = userEvent.setup();
    await page();

    await utilisateur.click(screen.getByRole("button", { name: /Nouveau projet/ }));

    // C'est l'appelant qui quitte le projet ouvert pour la porte d'entrée : ici,
    // rien ne s'ouvre, et rien ne se déclare.
    expect(nouveauProjet).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("form")).toBeNull();
    expect(creerProjet).not.toHaveBeenCalled();
    expect(chargerRepertoireProjets).not.toHaveBeenCalled();
  });

  it("n'offre pas de « Nouveau projet » sans chemin vers la conversation", async () => {
    render(<ListeProjets />);
    await screen.findByRole("region", { name: "Projets déclarés" });
    expect(screen.queryByRole("button", { name: /Nouveau projet/ })).toBeNull();
  });
});

describe("le choix de la racine (explorateur servi par l'API)", () => {
  it("change la racine pour un dossier énuméré, sans qu'un chemin soit saisi", async () => {
    const utilisateur = userEvent.setup();
    const explorateur = await formulaireAvecExplorateur(utilisateur);

    // Le dépôt Git se repère avant d'être choisi.
    expect(await within(explorateur).findByText("depensio")).toBeInTheDocument();
    expect(explorateur).toHaveTextContent("dépôt Git");

    await utilisateur.click(
      within(explorateur).getByRole("button", { name: "Choisir depensio" }),
    );

    // Aucun champ de saisie ne porte la racine : elle n'est qu'affichée.
    expect(screen.queryByRole("textbox", { name: /[Rr]acine/ })).toBeNull();
    expect(screen.getByText("D:/projets/depensio")).toBeInTheDocument();

    await utilisateur.click(
      screen.getByRole("button", { name: "Enregistrer les modifications" }),
    );

    expect(modifierProjet).toHaveBeenCalledWith(
      ANCIEN.id,
      expect.objectContaining({
        nom: "Ancien",
        racine: "D:/projets/depensio",
        origine: "existant",
      }),
    );
    // La liste se relit : la racine canonicalisée et le VCS viennent du backend.
    await waitFor(() => expect(chargerProjets).toHaveBeenCalledTimes(2));
  });

  it("navigue dans l'arborescence en demandant chaque page au backend", async () => {
    const utilisateur = userEvent.setup();
    const explorateur = await formulaireAvecExplorateur(utilisateur);

    // L'explorateur s'ouvre sur la racine du projet modifié.
    expect(chargerExplorateur).toHaveBeenCalledWith("D:/anciens/ancien");
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
    // Le motif du blocage est passé du `title` au nom accessible (#536) : sur
    // un bouton `disabled`, le `title` n'était affiché par personne.
    expect(
      await within(explorateur).findByRole("button", {
        name: "Choisir depensio — indisponible : ce dossier est déjà la racine d'un projet",
      }),
    ).toBeDisabled();
  });
});

describe("l'explorateur rendu dans le formulaire de projet (#312)", () => {
  /**
   * Le formulaire **prêt à partir** (nom + racine), explorateur rouvert.
   *
   * C'est la seule mise en scène où une soumission fautive se voit : tant que
   * le formulaire ne peut pas partir, une `Entrée` égarée ne prouverait rien —
   * le bug de #312 passerait au vert.
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
      screen.getByRole("button", { name: "Enregistrer les modifications" }),
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

  it("ouvre le chemin saisi sur Entrée, sans enregistrer le projet", async () => {
    const utilisateur = userEvent.setup();
    const barre = await formulairePretExplorateurOuvert(utilisateur);

    await utilisateur.type(barre, "D:/depots{Enter}");

    expect(chargerExplorateur).toHaveBeenLastCalledWith("D:/depots");
    expect(modifierProjet).not.toHaveBeenCalled();
  });

  it("n'enregistre rien non plus sur une Entrée à vide", async () => {
    // La soumission implicite du navigateur est coupée *avant* de regarder la
    // saisie : sinon un champ vide laisserait passer `Entrée` jusqu'au
    // formulaire porteur — le geste le plus banal des deux.
    const utilisateur = userEvent.setup();
    const barre = await formulairePretExplorateurOuvert(utilisateur);
    const lectures = chargerExplorateur.mock.calls.length;

    await utilisateur.type(barre, "{Enter}");

    expect(modifierProjet).not.toHaveBeenCalled();
    expect(chargerExplorateur).toHaveBeenCalledTimes(lectures);
  });

  it("ouvre le chemin par le bouton « Aller », qui ne soumet rien non plus", async () => {
    const utilisateur = userEvent.setup();
    const barre = await formulairePretExplorateurOuvert(utilisateur);

    await utilisateur.type(barre, "D:/depots");
    await utilisateur.click(screen.getByRole("button", { name: "Aller" }));

    expect(chargerExplorateur).toHaveBeenLastCalledWith("D:/depots");
    expect(modifierProjet).not.toHaveBeenCalled();
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

    // Le dossier choisi remplace la racine du projet, explorateur refermé.
    expect(await screen.findByText("D:/projets/depensio")).toBeInTheDocument();
    expect(screen.queryByText("D:/anciens/ancien")).toBeNull();
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

    // Le message du backend **et** le motif en mots (#946, C7) disent tous deux
    // « racine de disque » : viser le bandeau plutôt qu'un texte, qui en
    // trouverait deux.
    const bandeau = await screen.findByRole("alert");
    expect(bandeau).toHaveTextContent("Une racine de disque ne peut pas être un projet.");
    expect(bandeau).toHaveTextContent("motif : Racine de disque");
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

    // La racine du projet est restée la sienne.
    expect(screen.getAllByText("D:/anciens/ancien").length).toBeGreaterThan(0);
    expect(screen.queryByRole("alert")).toBeNull();
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
    expect(bandeau).toHaveTextContent("motif : Une fenêtre de choix est déjà ouverte");
    expect(bandeau).not.toHaveTextContent("selecteur-en-cours");
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
    modifierProjet.mockRejectedValue(
      new ErreurProjet(
        "chemin-sensible",
        "Zone sensible : C:/Users/moi/.ssh est protégé",
      ),
    );
    const utilisateur = userEvent.setup();
    const explorateur = await formulaireAvecExplorateur(utilisateur, [
      projetFactice({ id: "prj-autre", nom: "Dépensio" }),
    ]);
    await utilisateur.click(
      await within(explorateur).findByRole("button", {
        name: "Choisir depensio",
      }),
    );
    await utilisateur.clear(screen.getByLabelText("Nom du projet"));
    await utilisateur.type(screen.getByLabelText("Nom du projet"), "Ancien revu");
    await utilisateur.click(
      screen.getByRole("button", { name: "Enregistrer les modifications" }),
    );

    const refus = await screen.findByRole("alert");
    expect(refus).toHaveTextContent("Zone sensible");
    expect(refus).toHaveTextContent("motif : Zone protégée");
    expect(refus).not.toHaveTextContent("chemin-sensible");
    // Le conseil prolonge le message du backend, il ne le remplace pas.
    expect(refus).toHaveTextContent(/Zone protégée/);

    // L'écran reste utilisable : la liste est là, la saisie aussi.
    expect(
      screen.getByRole("listitem", { name: "Projet Dépensio" }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Nom du projet")).toHaveValue("Ancien revu");
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
    // Le motif en mots, pas son identifiant d'API (#946, C7).
    expect(refus).toHaveTextContent("Hors des dossiers explorables");
    expect(refus).not.toHaveTextContent("hors-racines-explorables");
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
    const refus = await within(carte).findByRole("alert");
    expect(refus).toHaveTextContent("Projet inconnu : prj-7f3a1c2b");
    // Le motif se lit en mots (#946, C7) : « motif : projet-inconnu » rendait à
    // l'utilisateur l'identifiant de l'API.
    expect(refus).toHaveTextContent("motif : Projet inconnu");
    expect(refus).not.toHaveTextContent("projet-inconnu");
  });
});

describe("la mise sous Git d'un projet non versionné (#855)", () => {
  const bouton = () => screen.queryByRole("button", { name: "Mettre sous Git" });

  /** La page avec un projet **non versionné**, sa carte rendue. */
  async function carteNonVersionnee() {
    chargerProjets.mockResolvedValue([projetFactice({ vcs: null })]);
    await page();
    return await screen.findByRole("listitem", { name: "Projet Dépensio" });
  }

  it("ne propose le geste que sur un projet non versionné", async () => {
    // Sur un projet déjà sous Git, la route rendrait la fiche telle quelle :
    // un bouton qui ne change rien apprendrait à ne plus lire les boutons.
    chargerProjets.mockResolvedValue([projetFactice()]);
    await page();
    await screen.findByRole("listitem", { name: "Projet Dépensio" });

    expect(bouton()).toBeNull();
  });

  it("s'arme en deux temps derrière une confirmation qui dit ce qui va être fait", async () => {
    const utilisateur = userEvent.setup();
    const carte = await carteNonVersionnee();

    await utilisateur.click(await within(carte).findByRole("button", { name: "Mettre sous Git" }));

    // Rien ne part avant le second clic : c'est le seul geste de l'écran qui
    // écrive dans le dossier de l'utilisateur.
    expect(versionnerProjet).not.toHaveBeenCalled();
    const confirmation = within(carte).getByRole("group", {
      name: "Confirmer la mise sous Git",
    });
    // Les trois choses que la confirmation doit dire (critère #855).
    expect(confirmation).toHaveTextContent("git init");
    expect(confirmation).toHaveTextContent(/premier commit/);
    expect(confirmation).toHaveTextContent(/toute la racine/);
    expect(confirmation).toHaveTextContent(".gitignore");
    // Et où : la racine du projet, pas un dossier deviné.
    expect(confirmation).toHaveTextContent("D:/projets/depensio");
  });

  it("appelle la route sans rien envoyer, puis relit la liste", async () => {
    const utilisateur = userEvent.setup();
    const carte = await carteNonVersionnee();
    await utilisateur.click(await within(carte).findByRole("button", { name: "Mettre sous Git" }));

    await utilisateur.click(
      within(carte).getByRole("button", { name: "Confirmer la mise sous Git" }),
    );

    // L'identifiant seul : le `vcs` n'est pas un champ de requête (EF-38).
    expect(versionnerProjet).toHaveBeenCalledWith("prj-7f3a1c2b");
    // Le `vcs` rendu n'est pas recopié : la liste se relit, comme après toute
    // écriture — c'est elle qui montrera « git · main ».
    await waitFor(() => expect(chargerProjets).toHaveBeenCalledTimes(2));
  });

  it("laisse reculer avant de confirmer", async () => {
    const utilisateur = userEvent.setup();
    const carte = await carteNonVersionnee();
    await utilisateur.click(await within(carte).findByRole("button", { name: "Mettre sous Git" }));

    await utilisateur.click(
      within(carte).getByRole("button", { name: "Garder non versionné" }),
    );

    expect(versionnerProjet).not.toHaveBeenCalled();
    expect(within(carte).queryByRole("group", { name: "Confirmer la mise sous Git" })).toBeNull();
    expect(bouton()).toBeInTheDocument();
  });

  it("n'arme qu'un geste à la fois : la suppression désarme la mise sous Git", async () => {
    // Deux confirmations sur une même carte ne parleraient pas de la même
    // chose ; armer l'une range l'autre.
    const utilisateur = userEvent.setup();
    const carte = await carteNonVersionnee();
    await utilisateur.click(await within(carte).findByRole("button", { name: "Supprimer" }));

    expect(bouton()).toBeNull();
    expect(within(carte).queryByRole("group", { name: "Confirmer la mise sous Git" })).toBeNull();

    await utilisateur.click(within(carte).getByRole("button", { name: "Garder le projet" }));
    expect(bouton()).toBeInTheDocument();
  });

  it("montre le motif d'un refus sur la carte, avec le conseil qui va avec", async () => {
    versionnerProjet.mockRejectedValue(
      new ErreurProjet(
        "depot-englobant",
        "D:/projets/depensio est déjà dans le dépôt Git D:/projets — un dépôt imbriqué modifierait celui-ci.",
      ),
    );
    const utilisateur = userEvent.setup();
    const carte = await carteNonVersionnee();
    await utilisateur.click(await within(carte).findByRole("button", { name: "Mettre sous Git" }));

    await utilisateur.click(
      within(carte).getByRole("button", { name: "Confirmer la mise sous Git" }),
    );

    const refus = await within(carte).findByRole("alert");
    expect(refus).toHaveTextContent("Mise sous Git refusée");
    expect(refus).toHaveTextContent("dépôt imbriqué");
    expect(refus).toHaveTextContent("motif : Dépôt Git englobant");
    expect(refus).not.toHaveTextContent("depot-englobant");
    // Le conseil prolonge le message du backend, il ne le remplace pas.
    expect(refus).toHaveTextContent(/racine de ce dépôt/);
    // La carte reste utilisable : le projet est toujours non versionné, le
    // geste se repropose, et rien n'a été relu (rien n'a été écrit).
    expect(bouton()).toBeInTheDocument();
    expect(chargerProjets).toHaveBeenCalledTimes(1);
  });
});

describe("le texte de l'écran (#946)", () => {
  it("dit ce qu'est un projet sans recoller deux mots autour du gras", async () => {
    const region = await page();

    expect(region).toHaveTextContent(
      "racine sur le disque et ce qu'elle expose aux agents",
    );
  });

  it("ne promet pas l'inverse du champ « Aller à un chemin absolu »", async () => {
    const region = await page();

    // La phrase disait « jamais en tapant un chemin » alors que l'explorateur
    // offre justement d'y sauter : c'est l'écran qu'elle décrit qu'elle
    // contredisait (C2 du retex du 2026-09-11).
    expect(region).not.toHaveTextContent("jamais en tapant un chemin");
  });

  it("donne un nom accessible au titre du formulaire", async () => {
    chargerProjets.mockResolvedValue([projetFactice()]);
    const utilisateur = userEvent.setup();
    await page();

    await utilisateur.click(await screen.findByRole("button", { name: "Modifier" }));

    // Le `aria-label` du `<form>` ne dispense pas le titre d'un nom : sans lui,
    // la carte n'a pas de tête dans l'arbre d'accessibilité (C12).
    expect(
      screen.getByRole("heading", { level: 3, name: "Modifier « Dépensio »" }),
    ).toBeInTheDocument();
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

});

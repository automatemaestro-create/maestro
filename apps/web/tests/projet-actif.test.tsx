/**
 * La porte d'entrée de la Control Tower (#279, lot 3 de #276).
 *
 * Ces tests montent le **shell entier**, et pas la porte seule : ce qui est en
 * jeu n'est pas le rendu d'un écran de plus, c'est qu'aucun autre ne s'atteigne
 * avant lui. Un `ChoixProjet` rendu isolément passerait tous ces tests sans rien
 * garantir — c'est la garde qui est le sujet.
 *
 * Ce qu'ils protègent en propre :
 *
 * 1. **la garde n'est pas une redirection** — l'URL demandée ne bouge pas, si
 *    bien que la page revient d'elle-même après le choix. Le jour où quelqu'un
 *    remplacerait la garde par un `router.push("/projets")`, le parcours aurait
 *    l'air identique et la page demandée serait pourtant perdue : les tests
 *    regardent donc aussi ce qui **n'a pas** été poussé ;
 * 2. **le choix retenu est confronté à l'état réel**, jamais recopié — un projet
 *    supprimé entre deux visites ramène au choix avec son motif ;
 * 3. **une API muette n'est pas une absence de projet** — même règle que la
 *    liste de #225 : on ne prétend pas que le backlog est vide quand on n'a rien
 *    pu lire, et on n'oublie pas le choix retenu pour autant ;
 * 4. **la porte défile** (#306) — être rendue *au-dessus* du cadre applicatif
 *    la prive du conteneur défilant du shell, et le `<body>` en `overflow-hidden`
 *    de #248 rognait alors le bas du formulaire de création. jsdom ne calcule
 *    aucune mise en page : ce qui se teste est la **chaîne de classes** qui rend
 *    le défilement possible, comme pour le Kanban (`kanban.test.tsx`).
 *
 * Les tests Python et la doc de la vague sont différés au lot 6 (#282).
 */

import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { EcranOuverture } from "@/components/projets/ChoixProjet";
import { Shell } from "@/components/Shell";
import { marquerGuideVu } from "@/lib/guide";
import { ecrireProjetActifId, lireProjetActifId } from "@/lib/projetActif";

import {
  cheminCourant,
  dossierFactice,
  navigations,
  pageExplorateurFactice,
  poserChemin,
  projetFactice,
} from "./aides";

const chargerProjets = vi.fn();
const chargerExplorateur = vi.fn();
const creerProjet = vi.fn();

// `importOriginal`, et non un objet nu : `ErreurProjet` doit rester **la**
// classe du module (voir `projets.test.tsx`). Cette déclaration prend le pas sur
// celle de `setup.ts`, qui ne sert que les fichiers n'ayant rien à régler ici.
vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...reel,
    chargerProjets: () => chargerProjets(),
    chargerExplorateur: (chemin: string | null) => chargerExplorateur(chemin),
    creerProjet: (declaration: unknown) => creerProjet(declaration),
  };
});

/** Ce que la page demandée rend — son absence dit que la garde tient. */
const CONTENU = "contenu de la page demandée";

const monter = () =>
  render(
    <Shell>
      <p>{CONTENU}</p>
    </Shell>,
  );

/** La porte d'entrée, une fois la première lecture des projets tranchée. */
const porte = () => screen.findByRole("main", { name: "Choix du projet" });

beforeEach(() => {
  vi.clearAllMocks();
  // Sans cela la visite guidée s'ouvrirait par-dessus le shell une fois entré.
  marquerGuideVu();
  chargerProjets.mockResolvedValue([]);
  chargerExplorateur.mockResolvedValue(
    pageExplorateurFactice({
      chemin: "D:/projets",
      dossiers: [
        dossierFactice({
          nom: "depensio",
          chemin: "D:/projets/depensio",
          depot_git: true,
        }),
      ],
    }),
  );
  creerProjet.mockResolvedValue(projetFactice());
});

describe("l'écran de choix du projet", () => {
  it("occupe l'écran tant qu'aucun projet n'est actif", async () => {
    chargerProjets.mockResolvedValue([projetFactice()]);
    monter();

    const ecran = await porte();
    expect(
      within(ecran).getByRole("button", { name: "Ouvrir Dépensio" }),
    ).toBeInTheDocument();
    // Ni la page, ni le cadre : on ne voit rien de la Control Tower avant de
    // savoir de quel projet elle parlerait.
    expect(screen.queryByText(CONTENU)).toBeNull();
    expect(
      screen.queryByRole("navigation", { name: "Navigation principale" }),
    ).toBeNull();
  });

  it("propose d'en créer un plutôt que d'afficher un vide", async () => {
    monter();
    const ecran = await porte();

    // Le formulaire de #225 est déjà ouvert : un écran qui n'a rien à lister
    // n'a qu'une chose à proposer, et l'ouvrir d'office épargne un clic sans
    // alternative.
    expect(
      await screen.findByRole("button", { name: "Déclarer le projet" }),
    ).toBeInTheDocument();
    expect(ecran).toHaveTextContent(/Aucun projet déclaré/);
  });

  it("entre dans le projet déclaré sur place", async () => {
    const utilisateur = userEvent.setup();
    creerProjet.mockResolvedValue(projetFactice({ id: "prj-neuf", nom: "Neuf" }));
    monter();
    await porte();

    await utilisateur.click(
      await screen.findByRole("button", { name: /Choisir un dossier/ }),
    );
    const explorateur = await screen.findByRole("region", {
      name: "Explorateur de dossiers",
    });
    await utilisateur.click(
      await within(explorateur).findByRole("button", {
        name: "Choisir depensio",
      }),
    );
    await utilisateur.click(
      screen.getByRole("button", { name: "Déclarer le projet" }),
    );

    // Le projet **relu** par le backend devient l'actif sans relecture de la
    // liste, dont l'échec laisserait devant la porte qu'on vient d'ouvrir.
    expect(await screen.findByText(CONTENU)).toBeInTheDocument();
    expect(lireProjetActifId()).toBe("prj-neuf");
    expect(chargerProjets).toHaveBeenCalledTimes(1);
  });
});

describe("la mémoire du projet actif", () => {
  it("retrouve le projet retenu d'une visite à l'autre", async () => {
    chargerProjets.mockResolvedValue([projetFactice()]);
    ecrireProjetActifId("prj-7f3a1c2b");
    monter();

    expect(await screen.findByText(CONTENU)).toBeInTheDocument();
    expect(screen.queryByRole("main", { name: "Choix du projet" })).toBeNull();
  });

  it("ramène au choix, avec son motif, sur un projet devenu introuvable", async () => {
    chargerProjets.mockResolvedValue([
      projetFactice({ id: "prj-autre", nom: "Autre" }),
    ]);
    ecrireProjetActifId("prj-disparu");
    monter();

    const motif = await screen.findByRole("alert");
    expect(motif).toHaveTextContent("prj-disparu");
    expect(motif).toHaveTextContent("projet-inconnu");
    // Le conseil du motif prolonge le message du backend (EF-38).
    expect(motif).toHaveTextContent(/recharger la liste/);

    // L'identifiant fantôme est oublié — sans quoi chaque visite rejouerait le
    // même refus —, et le choix reste possible sur ce qui existe encore.
    expect(lireProjetActifId()).toBeNull();
    expect(
      screen.getByRole("button", { name: "Ouvrir Autre" }),
    ).toBeInTheDocument();
  });

  it("ne prend pas une API muette pour une absence de projet", async () => {
    chargerProjets.mockRejectedValue(new Error("fetch failed"));
    ecrireProjetActifId("prj-7f3a1c2b");
    monter();

    expect(await screen.findByRole("alert")).toHaveTextContent("fetch failed");
    // On n'a rien pu lire, ce qui n'est pas n'avoir rien à lire : ni « aucun
    // projet déclaré », ni l'oubli du choix retenu.
    expect(screen.queryByText(/Aucun projet déclaré/)).toBeNull();
    expect(lireProjetActifId()).toBe("prj-7f3a1c2b");
    expect(screen.queryByText(CONTENU)).toBeNull();
  });

  it("laisse réessayer, et le choix retenu reprend dès que l'API répond", async () => {
    const utilisateur = userEvent.setup();
    chargerProjets.mockRejectedValueOnce(new Error("fetch failed"));
    chargerProjets.mockResolvedValue([projetFactice()]);
    ecrireProjetActifId("prj-7f3a1c2b");
    monter();
    await screen.findByRole("alert");

    await utilisateur.click(screen.getByRole("button", { name: "Réessayer" }));
    expect(await screen.findByText(CONTENU)).toBeInTheDocument();
  });

  it("repasse par la porte quand le projet actif est relâché depuis ailleurs", async () => {
    chargerProjets.mockResolvedValue([projetFactice()]);
    ecrireProjetActifId("prj-7f3a1c2b");
    monter();
    await screen.findByText(CONTENU);

    // Le stockage est la source de vérité et l'événement la notification : c'est
    // par là que passeront le sélecteur du lot 4 (#280) et un autre onglet.
    act(() => ecrireProjetActifId(null));
    expect(await porte()).toBeInTheDocument();
  });
});

describe("le défilement de la porte (#306)", () => {
  /**
   * Le conteneur qui **doit** défiler autour d'un écran de la porte. On le prend
   * par le parent du `<main>` et non par une recherche de classe : ce que le
   * test protège, c'est justement qu'il y en ait un *sur ce chemin-là*.
   */
  const conteneur = (ecran: HTMLElement) => {
    expect(ecran.parentElement).not.toBeNull();
    return ecran.parentElement as HTMLElement;
  };

  it("donne un ascenseur au choix du projet, formulaire de création compris", async () => {
    monter();
    const ecran = await porte();
    // Le formulaire est bien là, ouvert d'office : c'est lui qui déborde.
    expect(
      await screen.findByRole("button", { name: "Déclarer le projet" }),
    ).toBeInTheDocument();

    const defilant = conteneur(ecran);
    expect(defilant.className).toContain("overflow-y-auto");
    // `min-h-0` + `flex-1` : sans eux le conteneur se dimensionne sur le
    // formulaire au lieu de rétrécir sous lui, et l'`overflow-y-auto` n'a
    // jamais rien à faire défiler (même chaîne qu'au #248).
    expect(defilant.className).toContain("min-h-0");
    expect(defilant.className).toContain("flex-1");
  });

  it("met l'ascenseur au bord de l'écran, pas au bord de la colonne", async () => {
    monter();
    const ecran = await porte();
    // La colonne reste centrée et bornée…
    expect(ecran.className).toContain("max-w-2xl");
    expect(ecran.className).toContain("mx-auto");
    // …donc c'est bien le conteneur pleine largeur qui défile, et pas elle :
    // un `overflow-y-auto` posé ici collerait l'ascenseur au texte.
    expect(ecran.className).not.toContain("overflow");
    expect(conteneur(ecran).className).not.toContain("max-w-");
  });

  it("couvre aussi l'écran d'ouverture, qui partage le même cadre", () => {
    // Les deux écrans de la porte se ressemblent parce qu'ils sont le même
    // cadre : les séparer ferait revenir le défaut sur celui qu'on oublierait.
    render(<EcranOuverture />);
    const ecran = screen.getByRole("main", {
      name: "Ouverture de la Control Tower",
    });
    expect(conteneur(ecran).className).toContain("overflow-y-auto");
  });
});

describe("une page atteinte directement, sans projet actif", () => {
  it("passe par le choix puis rend la page demandée, sans redirection", async () => {
    poserChemin("/couts");
    chargerProjets.mockResolvedValue([projetFactice()]);
    const utilisateur = userEvent.setup();
    monter();

    // La page demandée est nommée : on sait où l'on retombe avant de choisir.
    expect(await porte()).toHaveTextContent("Coûts & analytics");

    await utilisateur.click(
      screen.getByRole("button", { name: "Ouvrir Dépensio" }),
    );

    // La page demandée revient — et dans son cadre, titre compris.
    expect(await screen.findByText(CONTENU)).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "Coûts & analytics",
    );
    // Garde de shell, pas redirection : rien n'a été poussé, donc rien n'a eu à
    // être mémorisé — et l'historique du navigateur n'a pas d'aller-retour à
    // défaire.
    expect(navigations).toEqual([]);
    expect(cheminCourant()).toBe("/couts");
  });
});

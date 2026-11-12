/**
 * La section « **Projets** » des Paramètres (#1022) : le répertoire où naît un
 * projet neuf.
 *
 * Trois choses à garder, et la première est celle qu'on défait sans y penser :
 *
 * ① **le chemin ne se tape pas** (#225). Le réglage se choisit dans le même
 *    explorateur qu'une racine de projet — jamais dans un champ texte. Un champ
 *    de saisie ici rouvrirait, dans les Paramètres, la porte qu'EF-38 ferme dans
 *    le formulaire ;
 * ② **choisir *est* l'écriture** : il n'y a pas de bouton « valider », donc pas
 *    d'état intermédiaire où l'écran montrerait un réglage que le backend
 *    n'aurait pas ;
 * ③ **le défaut porte un nom** : « le dossier que Maestro propose » n'est pas
 *    « aucun répertoire », et le bouton de retour n'apparaît que quand il y a
 *    quelque chose à défaire.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ParametresProjets } from "@/components/parametres/ParametresProjets";
import type { RepertoireProjets } from "@/lib/types";

import { dossierFactice, pageExplorateurFactice } from "./aides";

const chargerRepertoireProjets = vi.fn();
const reglerRepertoireProjets = vi.fn();
const chargerExplorateur = vi.fn();
const chargerDisponibiliteSelecteur = vi.fn();
const chargerDossiersAutorises = vi.fn();
const retirerDossierAutorise = vi.fn();
const autoriserDossier = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...reel,
    chargerDossiersAutorises: () => chargerDossiersAutorises(),
    retirerDossierAutorise: (chemin: string) => retirerDossierAutorise(chemin),
    autoriserDossier: (chemin: string) => autoriserDossier(chemin),
    chargerRepertoireProjets: () => chargerRepertoireProjets(),
    reglerRepertoireProjets: (chemin: string | null) =>
      reglerRepertoireProjets(chemin),
    chargerExplorateur: (chemin: string | null) => chargerExplorateur(chemin),
    chargerDisponibiliteSelecteur: () => chargerDisponibiliteSelecteur(),
  };
});

function repertoireFactice(
  surcharges: Partial<RepertoireProjets> = {},
): RepertoireProjets {
  return {
    chemin: "C:/Users/moi/Maestro",
    par_defaut: true,
    existe: true,
    cree: false,
    refus: null,
    ...surcharges,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  chargerDossiersAutorises.mockResolvedValue({ dossiers: [] });
  retirerDossierAutorise.mockResolvedValue({ dossiers: [] });
  autoriserDossier.mockImplementation(async (chemin: string) => ({
    chemin,
    dossiers: [{ chemin, refus: null }],
  }));
  chargerRepertoireProjets.mockResolvedValue(repertoireFactice());
  reglerRepertoireProjets.mockResolvedValue(
    repertoireFactice({ chemin: "D:/projets", par_defaut: false }),
  );
  chargerExplorateur.mockResolvedValue(
    pageExplorateurFactice({
      chemin: "D:/projets",
      parent: null,
      dossiers: [dossierFactice({ nom: "depensio", chemin: "D:/projets/depensio" })],
    }),
  );
  chargerDisponibiliteSelecteur.mockResolvedValue({
    disponible: false,
    motif: "selecteur-hors-poste",
    message: "Backend distant : le dialogue de l'OS s'ouvrirait sur le serveur.",
    outil: null,
  });
});

async function monter() {
  render(<ParametresProjets />);
  return await screen.findByText("C:/Users/moi/Maestro");
}

describe("① le répertoire se lit, et se choisit sans se taper", () => {
  it("décrit le réglage d'une phrase qui ne change pas avec son état", async () => {
    // Constat de la relecture visuelle : la description changeait selon que la
    // valeur était le défaut ou non — et donc aussi quand la lecture échouait,
    // sans que rien n'ait changé. Un réglage, un libellé.
    await monter();
    const attendu = /en est rempli d'office/;
    expect(screen.getByText(attendu)).toBeInTheDocument();

    chargerRepertoireProjets.mockResolvedValue(
      repertoireFactice({ chemin: "D:/projets", par_defaut: false }),
    );
    render(<ParametresProjets />);
    expect(await screen.findAllByText(attendu)).toHaveLength(2);
  });

  it("n'offre aucun champ de saisie de chemin", async () => {
    // Le contre-test qui donne son sens au précédent : la section ne doit pas
    // rouvrir dans les Paramètres la porte qu'EF-38 ferme ailleurs (#225).
    const { container } = render(<ParametresProjets />);
    await screen.findByText("C:/Users/moi/Maestro");
    expect(container.querySelectorAll("input")).toHaveLength(0);
  });

  it("écrit le dossier choisi dans l'explorateur, sans bouton « valider »", async () => {
    const utilisateur = userEvent.setup();
    await monter();
    await utilisateur.click(
      screen.getByRole("button", { name: /Changer de dossier/ }),
    );
    const explorateur = await screen.findByRole("region", {
      name: "Explorateur de dossiers",
    });
    await utilisateur.click(
      within(explorateur).getByRole("button", { name: "Choisir ce dossier" }),
    );

    expect(reglerRepertoireProjets).toHaveBeenCalledWith("D:/projets");
    expect(await screen.findByText("D:/projets")).toBeInTheDocument();
    // L'explorateur se referme : le geste est fini, il n'attend pas de confirmation.
    expect(
      screen.queryByRole("region", { name: "Explorateur de dossiers" }),
    ).not.toBeInTheDocument();
  });
});

describe("② le défaut, et ce qu'on peut en défaire", () => {
  it("ne propose de revenir au défaut que lorsqu'il y a quelque chose à défaire", async () => {
    await monter();
    expect(
      screen.queryByRole("button", { name: /Revenir au dossier proposé/ }),
    ).not.toBeInTheDocument();
  });

  it("revient au défaut en posant null, jamais un chemin réécrit à la main", async () => {
    const utilisateur = userEvent.setup();
    chargerRepertoireProjets.mockResolvedValue(
      repertoireFactice({ chemin: "D:/projets", par_defaut: false }),
    );
    reglerRepertoireProjets.mockResolvedValue(repertoireFactice());
    render(<ParametresProjets />);
    await screen.findByText("D:/projets");

    await utilisateur.click(
      screen.getByRole("button", { name: /Revenir au dossier proposé/ }),
    );

    expect(reglerRepertoireProjets).toHaveBeenCalledWith(null);
    expect(await screen.findByText("C:/Users/moi/Maestro")).toBeInTheDocument();
  });
});

describe("③ ce qui rate se dit", () => {
  it("montre le motif d'un répertoire devenu indisponible", async () => {
    chargerRepertoireProjets.mockResolvedValue(
      repertoireFactice({
        chemin: "E:/disparu",
        par_defaut: false,
        existe: false,
        refus: {
          motif: "dossier-absent",
          message: "Racine introuvable : E:/disparu n'existe pas.",
        },
      }),
    );
    render(<ParametresProjets />);

    expect(await screen.findByText(/E:\/disparu n'existe pas/)).toBeInTheDocument();
    // Le chemin réglé reste affiché : on corrige ce qu'on voit, pas un blanc.
    expect(screen.getByText("E:/disparu")).toBeInTheDocument();
  });

  it("distingue une lecture en panne d'un réglage refusé", async () => {
    // Constat de la relecture visuelle de #1022, en état « erreur » : la section
    // annonçait « Réglage refusé » à quelqu'un qui n'avait rien réglé, et laissait
    // « … » à l'écran alors que plus rien n'arrivait.
    const { ErreurProjet } = await import("@/lib/api");
    chargerRepertoireProjets.mockRejectedValue(
      new ErreurProjet("api-injoignable", "Erreur simulée."),
    );
    render(<ParametresProjets />);

    expect(await screen.findByText(/Répertoire illisible/)).toBeInTheDocument();
    expect(screen.queryByText(/Réglage refusé/)).not.toBeInTheDocument();
    expect(screen.getByText("—")).toBeInTheDocument();
    expect(screen.queryByText("…")).not.toBeInTheDocument();
  });

  it("garde le réglage précédent quand l'écriture est refusée", async () => {
    const utilisateur = userEvent.setup();
    const { ErreurProjet } = await import("@/lib/api");
    reglerRepertoireProjets.mockRejectedValue(
      new ErreurProjet("dossier-utilisateur-nu", "Dossier utilisateur refusé tel quel."),
    );
    await monter();
    await utilisateur.click(
      screen.getByRole("button", { name: /Changer de dossier/ }),
    );
    const explorateur = await screen.findByRole("region", {
      name: "Explorateur de dossiers",
    });
    await utilisateur.click(
      within(explorateur).getByRole("button", { name: "Choisir ce dossier" }),
    );

    expect(
      await screen.findByText(/Dossier utilisateur refusé tel quel/),
    ).toBeInTheDocument();
    expect(screen.getByText("C:/Users/moi/Maestro")).toBeInTheDocument();
  });
});

describe("④ les dossiers autorisés se relisent et se retirent (#1610)", () => {
  /** La ligne « Dossiers autorisés », une fois la section montée. */
  async function ligneDesAutorises() {
    render(<ParametresProjets />);
    const libelle = await screen.findByText("Dossiers autorisés");
    // La ligne de réglage est le bloc qui porte le libellé, son aide et sa valeur.
    return libelle.parentElement?.parentElement as HTMLElement;
  }

  it("dit en toutes lettres qu'aucun dossier n'est autorisé", async () => {
    const ligne = await ligneDesAutorises();

    expect(
      await within(ligne).findByText("Aucun dossier autorisé."),
    ).toBeInTheDocument();
    // La portée est dite une fois, en tête, et ne change pas avec l'état.
    expect(ligne).toHaveTextContent(/zones protégées y restent refusées/);
  });

  it("liste chaque dossier avec un retrait nommé, et le retire", async () => {
    chargerDossiersAutorises.mockResolvedValue({
      dossiers: [
        { chemin: "D:/clients/acme", refus: null },
        { chemin: "E:/archives", refus: null },
      ],
    });
    retirerDossierAutorise.mockResolvedValue({
      dossiers: [{ chemin: "E:/archives", refus: null }],
    });
    const utilisateur = userEvent.setup();
    await ligneDesAutorises();

    await utilisateur.click(
      await screen.findByRole("button", { name: "Retirer D:/clients/acme" }),
    );

    expect(retirerDossierAutorise).toHaveBeenCalledWith("D:/clients/acme");
    await waitFor(() =>
      expect(screen.queryByText("D:/clients/acme")).not.toBeInTheDocument(),
    );
    expect(screen.getByText("E:/archives")).toBeInTheDocument();
  });

  it("dit qu'une autorisation ne sert plus, sans la cacher", async () => {
    chargerDossiersAutorises.mockResolvedValue({
      dossiers: [
        {
          chemin: "F:/debranche",
          refus: {
            motif: "dossier-absent",
            message: "Racine introuvable : F:/debranche.",
          },
        },
      ],
    });
    await ligneDesAutorises();

    expect(await screen.findByText("F:/debranche")).toBeInTheDocument();
    expect(screen.getByText(/inutilisable — Dossier absent/)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Retirer F:/debranche" }),
    ).toBeInTheDocument();
  });

  it("garde le dossier à l'écran quand le retrait est refusé", async () => {
    const { ErreurProjet } = await import("@/lib/api");
    chargerDossiersAutorises.mockResolvedValue({
      dossiers: [{ chemin: "D:/clients/acme", refus: null }],
    });
    retirerDossierAutorise.mockRejectedValue(
      new ErreurProjet(
        "autorisation-inconnue",
        "Ce dossier n'est pas autorisé sur ce poste.",
      ),
    );
    const utilisateur = userEvent.setup();
    await ligneDesAutorises();

    await utilisateur.click(
      await screen.findByRole("button", { name: "Retirer D:/clients/acme" }),
    );

    expect(await screen.findByText(/Retrait refusé/)).toBeInTheDocument();
    expect(screen.getByText("D:/clients/acme")).toBeInTheDocument();
  });

  it("distingue une lecture en panne d'une liste vide", async () => {
    const { ErreurApi } = await import("@/lib/api");
    chargerDossiersAutorises.mockRejectedValue(
      ErreurApi.injoignable("/api/projets/dossiers-autorises"),
    );
    await ligneDesAutorises();

    expect(
      await screen.findByText(/Dossiers autorisés illisibles/),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Aucun dossier autorisé."),
    ).not.toBeInTheDocument();
  });

  it("se relit quand l'explorateur de la section vient d'en autoriser un", async () => {
    const { ErreurProjet } = await import("@/lib/api");
    const utilisateur = userEvent.setup();
    await monter();
    await utilisateur.click(
      screen.getByRole("button", { name: /Changer de dossier/ }),
    );
    const explorateur = await screen.findByRole("region", {
      name: "Explorateur de dossiers",
    });
    await within(explorateur).findByText("depensio");
    chargerExplorateur.mockRejectedValueOnce(
      new ErreurProjet(
        "hors-racines-explorables",
        "D:/projets/depensio est hors des dossiers explorables : C:/Users/moi.",
        { chemin: "D:/projets/depensio", refus: null },
      ),
    );
    await utilisateur.click(
      within(explorateur).getByRole("button", { name: "Ouvrir depensio" }),
    );
    await utilisateur.click(
      await within(explorateur).findByRole("button", {
        name: "Autoriser depensio…",
      }),
    );
    chargerDossiersAutorises.mockResolvedValue({
      dossiers: [{ chemin: "D:/projets/depensio", refus: null }],
    });
    await utilisateur.click(
      within(explorateur).getByRole("button", { name: "Autoriser ce dossier" }),
    );

    expect(autoriserDossier).toHaveBeenCalledWith("D:/projets/depensio");
    expect(
      await screen.findByRole("button", { name: "Retirer D:/projets/depensio" }),
    ).toBeInTheDocument();
  });

  it("fait relire l'explorateur ouvert quand on en retire un", async () => {
    // Constat de la relecture visuelle : après « Retirer », l'explorateur ouvert
    // au-dessus gardait la pastille « dossier autorisé » sur le dossier retiré
    // tant qu'on ne naviguait pas — deux vues du même réglage qui se contredisent.
    chargerDossiersAutorises.mockResolvedValue({
      dossiers: [{ chemin: "D:/clients/acme", refus: null }],
    });
    // L'arrivée, puis un dossier où l'on descend : c'est lui qu'on regarde au
    // moment du retrait, et lui seul doit se relire.
    chargerExplorateur.mockImplementation(async (chemin: string | null) =>
      chemin === "D:/clients"
        ? pageExplorateurFactice({
            chemin: "D:/clients",
            parent: null,
            dossiers: [
              dossierFactice({
                nom: "acme",
                chemin: "D:/clients/acme",
                origine: "autorise",
              }),
            ],
          })
        : pageExplorateurFactice({
            chemin: "C:/Users/moi/Maestro",
            parent: null,
            dossiers: [dossierFactice({ nom: "clients", chemin: "D:/clients" })],
          }),
    );
    const utilisateur = userEvent.setup();
    await monter();
    await utilisateur.click(
      screen.getByRole("button", { name: /Changer de dossier/ }),
    );
    const explorateur = await screen.findByRole("region", {
      name: "Explorateur de dossiers",
    });
    await utilisateur.click(
      await within(explorateur).findByRole("button", { name: "Ouvrir clients" }),
    );
    expect(
      await within(explorateur).findByText("dossier autorisé"),
    ).toBeInTheDocument();
    const lecturesAvant = chargerExplorateur.mock.calls.length;
    chargerExplorateur.mockResolvedValue(
      pageExplorateurFactice({
        chemin: "D:/clients",
        parent: null,
        dossiers: [dossierFactice({ nom: "acme", chemin: "D:/clients/acme" })],
      }),
    );

    await utilisateur.click(
      await screen.findByRole("button", { name: "Retirer D:/clients/acme" }),
    );

    await waitFor(() =>
      expect(chargerExplorateur.mock.calls.length).toBe(lecturesAvant + 1),
    );
    // La page relue est celle qu'on regardait, pas un retour à l'arrivée.
    expect(chargerExplorateur.mock.calls.at(-1)?.[0]).toBe("D:/clients");
    await waitFor(() =>
      expect(
        within(explorateur).queryByText("dossier autorisé"),
      ).not.toBeInTheDocument(),
    );
  });

  it("revient aux dossiers explorables quand le retrait sort la page affichée", async () => {
    // Constat de la relecture visuelle : relu après son retrait, le dossier
    // ouvert était refusé, et l'explorateur gardait sa liste sous « Dossier non
    // exploré » — le contenu d'un dossier qu'on dit ne plus explorer.
    const { ErreurProjet } = await import("@/lib/api");
    chargerDossiersAutorises.mockResolvedValue({
      dossiers: [{ chemin: "D:/clients/acme", refus: null }],
    });
    chargerRepertoireProjets.mockResolvedValue(
      repertoireFactice({ chemin: "D:/clients/acme", par_defaut: false }),
    );
    chargerExplorateur.mockResolvedValue(
      pageExplorateurFactice({
        chemin: "D:/clients/acme",
        parent: null,
        dossiers: [dossierFactice({ nom: "src", chemin: "D:/clients/acme/src" })],
      }),
    );
    const utilisateur = userEvent.setup();
    render(<ParametresProjets />);
    await utilisateur.click(
      await screen.findByRole("button", { name: /Changer de dossier/ }),
    );
    const explorateur = await screen.findByRole("region", {
      name: "Explorateur de dossiers",
    });
    await within(explorateur).findByText("src");
    chargerExplorateur.mockImplementation(async (chemin: string | null) => {
      if (chemin === null) {
        return pageExplorateurFactice({
          chemin: null,
          parent: null,
          dossiers: [dossierFactice({ nom: "Maestro", chemin: "C:/Users/moi/Maestro" })],
        });
      }
      throw new ErreurProjet(
        "hors-racines-explorables",
        "D:/clients/acme est hors des dossiers explorables : C:/Users/moi.",
        { chemin: "D:/clients/acme", refus: null },
      );
    });

    await utilisateur.click(
      await screen.findByRole("button", { name: "Retirer D:/clients/acme" }),
    );

    expect(await within(explorateur).findByText("Maestro")).toBeInTheDocument();
    expect(within(explorateur).queryByText("src")).not.toBeInTheDocument();
    expect(within(explorateur).getByRole("alert")).toHaveTextContent(
      "D:/clients/acme",
    );
    expect(
      within(explorateur).getByRole("button", { name: "Autoriser acme…" }),
    ).toBeInTheDocument();
    // On y est déjà : le refus n'offre pas d'y revenir.
    expect(
      within(explorateur).queryByRole("button", {
        name: "Revenir aux dossiers explorables",
      }),
    ).not.toBeInTheDocument();
  });
});

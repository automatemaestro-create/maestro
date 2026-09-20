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

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ParametresProjets } from "@/components/parametres/ParametresProjets";
import type { RepertoireProjets } from "@/lib/types";

import { dossierFactice, pageExplorateurFactice } from "./aides";

const chargerRepertoireProjets = vi.fn();
const reglerRepertoireProjets = vi.fn();
const chargerExplorateur = vi.fn();
const chargerDisponibiliteSelecteur = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...reel,
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
  it("montre le répertoire courant et dit que c'est celui que Maestro propose", async () => {
    await monter();
    expect(screen.getByText(/celui que Maestro propose/)).toBeInTheDocument();
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

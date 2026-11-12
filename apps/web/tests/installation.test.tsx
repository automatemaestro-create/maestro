/**
 * Ce que « Général » dit de l'installation (#1622) : la version de Maestro, et
 * le CLI `claude` qu'il lance.
 *
 * Deux moitiés :
 *
 * - `lib/installation` compose chaque ligne des champs typés de
 *   `GET /api/installation` — un écart (CLI absent, pris hors de l'installation,
 *   version illisible) porte son motif **et** son geste, qui dépend de la forme
 *   du produit (installation ou clone) ;
 * - la section les rend à l'endroit de la propriété, dans la colonne existante,
 *   et dit « Illisible » quand l'API ne les a pas rendues : une version ne
 *   s'invente pas.
 */

import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ParametresGeneral } from "@/components/parametres/ParametresGeneral";
import { Shell } from "@/components/Shell";
import {
  GESTE_REINSTALLER,
  GESTE_SDK_DU_DEPOT,
  ligneCli,
  lignesSansApi,
  ligneVersion,
} from "@/lib/installation";
import type { EtatInstallation } from "@/lib/types";

import {
  INSTALLATION_COHERENTE,
  poserInstallation,
  poserPanneInstallation,
  poserProjetActif,
  projetFactice,
} from "./aides";

const CHEMIN_DU_POSTE = "C:/Users/camille/.local/bin/claude.exe";

function etat(partiel: {
  forme?: EtatInstallation["forme"];
  produit?: Partial<EtatInstallation["produit"]>;
  cli?: Partial<EtatInstallation["cli_claude"]>;
}): EtatInstallation {
  return {
    forme: partiel.forme ?? INSTALLATION_COHERENTE.forme,
    produit: { ...INSTALLATION_COHERENTE.produit, ...partiel.produit },
    cli_claude: { ...INSTALLATION_COHERENTE.cli_claude, ...partiel.cli },
  };
}

describe("les lignes de l'installation (lib/installation)", () => {
  it("une installation cohérente ne signale rien, et dit d'où vient chaque version", () => {
    const version = ligneVersion(INSTALLATION_COHERENTE);
    const cli = ligneCli(INSTALLATION_COHERENTE);

    expect(version).toMatchObject({ valeur: "0.1.0", estVersion: true, ecart: false });
    expect(version.phrase).toMatch(/écrite quand son paquet a été construit/);
    expect(cli).toMatchObject({ valeur: "2.1.281", estVersion: true, ecart: false });
    expect(cli.phrase).toMatch(/il suit l'installation/);
    expect(cli.detail).toBe(INSTALLATION_COHERENTE.cli_claude.chemin);
  });

  it("dans un clone, la version est celle que porterait le paquet", () => {
    const version = ligneVersion(etat({ forme: "depot" }));

    expect(version).toMatchObject({ valeur: "0.1.0", ecart: false });
    expect(version.phrase).toMatch(/clone du dépôt/);
  });

  it("un CLI pris sur le poste est signalé, avec sa version, son chemin et le geste", () => {
    const cli = ligneCli(etat({ cli: { provenance: "poste", chemin: CHEMIN_DU_POSTE } }));

    expect(cli).toMatchObject({ valeur: "2.1.281", ecart: true, detail: CHEMIN_DU_POSTE });
    expect(cli.phrase).toMatch(/hors de l'installation/);
    expect(cli.phrase).toContain(GESTE_REINSTALLER);
  });

  it("le geste suit la forme du produit : un clone répare son venv, pas une installation", () => {
    const cli = ligneCli(
      etat({ forme: "depot", cli: { provenance: "poste", chemin: CHEMIN_DU_POSTE } }),
    );

    expect(cli.phrase).toContain(GESTE_SDK_DU_DEPOT);
    expect(cli.phrase).not.toContain(GESTE_REINSTALLER);
  });

  it("un CLI absent dit qu'aucun agent ne peut démarrer, sans inventer de version", () => {
    const cli = ligneCli(
      etat({ cli: { provenance: "absent", chemin: null, version: null } }),
    );

    expect(cli).toMatchObject({ valeur: "Introuvable", estVersion: false, ecart: true });
    expect(cli.phrase).toMatch(/aucun agent ne peut démarrer/);
    expect(cli.phrase).toContain(GESTE_REINSTALLER);
    expect(cli.detail).toBeNull();
  });

  it("un CLI embarqué qui ne dit pas sa version est signalé, jamais deviné", () => {
    const cli = ligneCli(etat({ cli: { version: null } }));

    expect(cli).toMatchObject({ valeur: "Version illisible", estVersion: false, ecart: true });
    expect(cli.phrase).toMatch(/n'a pas dit sa version/);
  });

  it("une installation qui ne dit pas sa version la dit illisible, avec le relevé", () => {
    const version = ligneVersion(
      etat({ produit: { version: null, releve: "version.json absent de l'installation" } }),
    );

    expect(version).toMatchObject({ valeur: "Illisible", estVersion: false, ecart: true });
    expect(version.phrase).toContain(GESTE_REINSTALLER);
    expect(version.detail).toBe("version.json absent de l'installation");
  });

  it("sans l'API, les deux lignes sont illisibles et ne supposent rien", () => {
    const [version, cli] = lignesSansApi("/api/installation n'a pas répondu");

    expect(version).toMatchObject({ valeur: "Illisible", ecart: true });
    expect(version.detail).toBe("/api/installation n'a pas répondu");
    expect(cli).toMatchObject({ valeur: "Illisible", ecart: true });
  });
});

describe("la section Général montre ce que l'installation fait tourner", () => {
  async function monter() {
    poserProjetActif(projetFactice({ id: "prj-a", nom: "depensio" }));
    render(
      <Shell>
        <ParametresGeneral />
      </Shell>,
    );
    await screen.findByText("Version de Maestro");
  }

  /** La ligne d'une propriété : le bloc qui porte son libellé. */
  function ligne(libelle: string): HTMLElement {
    const titre = screen.getByText(libelle);
    const bloc = titre.parentElement?.parentElement;
    if (!bloc) throw new Error(`ligne « ${libelle} » introuvable`);
    return bloc;
  }

  it("cohérente : la version de Maestro et celle du CLI, en tête de la colonne", async () => {
    await monter();

    expect(await within(ligne("Version de Maestro")).findByText("0.1.0")).toBeInTheDocument();
    expect(within(ligne("CLI claude")).getByText("2.1.281")).toBeInTheDocument();
    expect(
      within(ligne("CLI claude")).getByText(INSTALLATION_COHERENTE.cli_claude.chemin ?? ""),
    ).toBeInTheDocument();
    // Une colonne de propriétés : les versions précèdent l'API, rien ne s'y ajoute à côté.
    const libelles = screen
      .getAllByText(/^(Version de Maestro|CLI claude|API Control Tower)$/)
      .map((el) => el.textContent);
    expect(libelles).toEqual(["Version de Maestro", "CLI claude", "API Control Tower"]);
  });

  it("un CLI hors de l'installation se signale à l'endroit de la propriété, avec le geste", async () => {
    poserInstallation({
      cli_claude: { provenance: "poste", chemin: CHEMIN_DU_POSTE, version: "2.1.300" },
    });
    await monter();

    const cli = ligne("CLI claude");
    expect(await within(cli).findByText("2.1.300")).toBeInTheDocument();
    expect(within(cli).getByText(/hors de l'installation/)).toHaveTextContent(GESTE_REINSTALLER);
    expect(within(cli).getByText(CHEMIN_DU_POSTE)).toBeInTheDocument();
  });

  it("un CLI introuvable le dit, sans version inventée", async () => {
    poserInstallation({ cli_claude: { provenance: "absent", chemin: null, version: null } });
    await monter();

    const cli = ligne("CLI claude");
    expect(await within(cli).findByText("Introuvable")).toBeInTheDocument();
    expect(within(cli).getByText(/aucun agent ne peut démarrer/)).toBeInTheDocument();
  });

  it("l'API coupée, les versions sont illisibles — elles ne s'inventent pas", async () => {
    poserPanneInstallation("/api/installation n'a pas répondu");
    await monter();

    const version = ligne("Version de Maestro");
    expect(await within(version).findByText("Illisible")).toBeInTheDocument();
    expect(within(version).getByText("/api/installation n'a pas répondu")).toBeInTheDocument();
    expect(within(ligne("CLI claude")).getByText("Illisible")).toBeInTheDocument();
    expect(screen.queryByText("0.1.0")).toBeNull();
  });
});

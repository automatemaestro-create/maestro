/**
 * Un écran en panne dit la panne là où le contenu manque, jamais « aucun »
 * (#1217).
 *
 * Les bandeaux de #996 et de #1206 disaient la panne, mais l'écran gardait à
 * côté ses états vides et ses tuiles : « aucun événement de ce projet n'a été
 * consigné » et « 0 événement(s) » sous « API injoignable », « Aucun run en
 * cours… » et les tuiles d'avant sous la perte du magasin. Ils se lisent comme
 * la réponse de la lecture qui vient d'échouer. C'est le parti pris 3 de la
 * veille de #1206, d'après Primer (*Degraded experiences*). Ce qui se garde :
 *
 * ① **En panne, ni état vide ni chiffre** : chaque écran qui lit l'API rend
 *    « Impossible de lire … » à la place — qu'il n'ait rien lu, ou qu'il garde
 *    les valeurs d'avant la panne.
 * ② **La perte du magasin en route compte**, sans que l'écran ait rien relu :
 *    c'est le shell qui la signale, et l'écran s'y range.
 * ③ **Ce qui porte un geste reste** : une demande d'arbitrage déjà lue ne part
 *    pas avec la panne, un refus motivé en cours de frappe non plus.
 * ④ **Un vrai vide reste un vide** : l'API répond, rien n'est à montrer, et
 *    l'écran le dit comme avant.
 */

import { screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import PageCouts from "@/app/couts/page";
import PageJournal from "@/app/journal/page";
import TableauDeBord from "@/app/page";
import { EcranValidations as PageValidations } from "@/components/EcranValidations";
import { ValidationBriefs } from "@/components/brief/ValidationBriefs";
import { FilDeCadrage } from "@/components/chat/FilDeCadrage";
import { EcranIntegrations } from "@/components/integrations/EcranIntegrations";
import { ListeAgents } from "@/components/ListeAgents";
import { OngletLogs } from "@/components/OngletLogs";
import { ListeProjets } from "@/components/projets/ListeProjets";
import { ListeRuns } from "@/components/runs/ListeRuns";
import { VueRun } from "@/components/runs/VueRun";
import { ErreurApi } from "@/lib/api";
import { FournisseurMagasin } from "@/lib/magasin";
import type { EtatMagasin } from "@/lib/types";

import {
  evenementFactice,
  projetFactice,
  rendreAvecEtat,
  runFactice,
  tacheFactice,
  validationFactice,
} from "./aides";
import { poserSante, SANTE_OK } from "./ecrans-reseau";

/**
 * Les lectures qu'un test fait échouer, par nom de fonction du client : elles
 * rejettent comme une API éteinte. Celles qu'il fait répondre autrement
 * (`reponses`) rendent ce qu'il pose. Les autres rendent ce que rendent les
 * écrans peuplés des sondes (`ecrans-reseau`).
 */
const pannes = vi.hoisted(() => ({
  lectures: new Set<string>(),
  reponses: new Map<string, unknown>(),
  analytics: null as unknown,
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  const { mocksApi } = await import("./ecrans-reseau");
  const mocks: Record<string, (...args: never[]) => unknown> = mocksApi();
  const pilotes = Object.fromEntries(
    Object.entries(mocks).map(([nom, lire]) => [
      nom,
      (...args: never[]) =>
        pannes.lectures.has(nom)
          ? Promise.reject(reel.ErreurApi.injoignable(`/api/${nom}`))
          : pannes.reponses.has(nom)
            ? Promise.resolve(pannes.reponses.get(nom))
            : lire(...args),
    ]),
  );
  return { ...reel, ...pilotes };
});

vi.mock("@/lib/useAnalyticsCouts", async (original) => {
  const { mockAnalytics } = await import("./ecrans-reseau");
  const { useAnalyticsCouts } = mockAnalytics();
  return {
    ...(await original<Record<string, unknown>>()),
    useAnalyticsCouts: () => {
      const peuple = useAnalyticsCouts();
      return pannes.analytics === null
        ? peuple
        : { ...peuple, vue: null, connecte: false, erreur: pannes.analytics };
    },
  };
});

const PROJET = projetFactice({ id: "prj-1217", nom: "banc-s1-vider" });
const INJOIGNABLE = ErreurApi.injoignable("/api/taches");

const PERDU: EtatMagasin = {
  disponible: false,
  lieu: "Redis, redis://127.0.0.1:6379/0",
  titre: "Magasin des événements injoignable",
  motif: "Rien de ce que l'écran montrerait n'est à jour",
  geste: "relancer Redis, l'API reprend seule",
  commande: "docker compose -f infra/docker-compose.yml up -d redis",
};

/** Ce qu'un écran rend à la place de ce qu'il n'a pas pu lire. */
function indisponible(quoi: string): RegExp {
  return new RegExp(`^Impossible de lire ${quoi}\\.`);
}

beforeEach(() => {
  pannes.lectures.clear();
  pannes.reponses.clear();
  pannes.analytics = null;
});

afterEach(() => {
  poserSante(SANTE_OK);
});

describe("① en panne, ni état vide ni chiffre à côté du bandeau", () => {
  it("le tableau de bord qui n'a rien lu ne dit ni « aucun run » ni « aucun événement »", () => {
    rendreAvecEtat(<TableauDeBord />, { erreur: INJOIGNABLE }, PROJET);

    expect(screen.getByRole("alert")).toHaveTextContent(/API injoignable/);
    expect(
      screen.getByText(indisponible("l'état de banc-s1-vider")),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Aucun événement reçu/)).toBeNull();
    expect(screen.queryByText(/Aucun run en cours/)).toBeNull();
    expect(screen.queryByText("Run en cours")).toBeNull();
    // Ni l'écran du poste vide : conseiller « lancez un run » à qui n'a pas
    // d'API serait le contresens que #186 écartait déjà.
    expect(screen.queryByText(/Rien encore sur/)).toBeNull();
  });

  it("le tableau de bord d'avant la panne ne garde pas ses tuiles ni son fil", () => {
    rendreAvecEtat(
      <TableauDeBord />,
      {
        erreur: INJOIGNABLE,
        taches: [tacheFactice()],
        executions: [runFactice()],
        evenements: [evenementFactice()],
      },
      PROJET,
    );

    expect(
      screen.getByText(indisponible("l'état de banc-s1-vider")),
    ).toBeInTheDocument();
    for (const tuile of ["Run en cours", "Tâches", "Agents", "Dépense"]) {
      expect(screen.queryByText(tuile)).toBeNull();
    }
    expect(screen.queryByText(/Activité en direct/)).toBeNull();
  });

  it("le journal ne dit ni « rien encore », ni « 0 événement(s) », ni que le fil reste lisible", async () => {
    rendreAvecEtat(<PageJournal />, { erreur: INJOIGNABLE, connecte: false }, PROJET);

    expect(
      await screen.findByText(indisponible("le journal de banc-s1-vider")),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Rien encore/)).toBeNull();
    expect(screen.queryByText(/événement\(s\)/)).toBeNull();
    expect(screen.queryByText(/Flux temps réel interrompu/)).toBeNull();
    expect(screen.queryByRole("region", { name: "Filtres du journal" })).toBeNull();
  });

  it("le journal illisible sur une API par ailleurs debout le dit aussi", async () => {
    pannes.lectures.add("chargerJournal");
    rendreAvecEtat(<PageJournal />, {}, PROJET);

    expect(
      await screen.findByText(indisponible("le journal de banc-s1-vider")),
    ).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(/API injoignable/);
  });

  // Les écrans lus par l'état du shell : la panne est la sienne.
  it.each([
    {
      ecran: "les validations",
      ui: <PageValidations />,
      quoi: "les demandes d'arbitrage de banc-s1-vider",
      vide: /Rien encore sur|Aucune validation en attente/,
    },
    {
      ecran: "les briefs",
      ui: <ValidationBriefs />,
      quoi: "les briefs en attente sur banc-s1-vider",
      vide: /Aucun brief en attente/,
    },
    {
      ecran: "le cadrage",
      ui: <FilDeCadrage />,
      quoi: "les cadrages en attente sur banc-s1-vider",
      vide: /Aucun cadrage en attente/,
    },
    {
      ecran: "la liste des runs",
      ui: <ListeRuns />,
      quoi: "les runs de banc-s1-vider",
      vide: /Aucun run sur/,
    },
    {
      ecran: "la vue d'un run",
      ui: <VueRun runId="run-9" />,
      quoi: "le run run-9",
      vide: /Aucun run run-9/,
    },
    {
      ecran: "les logs d'un agent",
      ui: <OngletLogs nom="dev" />,
      quoi: "le journal de dev",
      vide: /Rien encore|ligne\(s\)/,
    },
  ])("$ecran rendent la panne à la place de leur vide", async ({ ui, quoi, vide }) => {
    rendreAvecEtat(ui, { erreur: INJOIGNABLE }, PROJET);

    expect(await screen.findByText(indisponible(quoi))).toBeInTheDocument();
    expect(screen.queryByText(vide)).toBeNull();
  });

  // Les écrans qui lisent eux-mêmes : la panne est celle de leur lecture.
  it.each([
    {
      ecran: "le catalogue des agents",
      ui: <ListeAgents />,
      lecture: "chargerCatalogue",
      quoi: "le catalogue des agents",
      vide: /Aucun agent au catalogue/,
    },
    {
      ecran: "les projets déclarés",
      ui: <ListeProjets />,
      lecture: "chargerProjets",
      quoi: "les projets déclarés",
      vide: /Aucun projet déclaré/,
    },
    {
      ecran: "le pool des intégrations",
      ui: <EcranIntegrations />,
      lecture: "chargerPoolMcp",
      quoi: "le pool de ce projet",
      vide: /Aucune intégration configurée|tous les secrets du pool sont valides/,
    },
  ])("$ecran rend la panne à la place de son vide", async ({ ui, lecture, quoi, vide }) => {
    pannes.lectures.add(lecture);
    rendreAvecEtat(ui, {}, PROJET);

    expect(await screen.findByText(indisponible(quoi))).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(/API injoignable/);
    expect(screen.queryByText(vide)).toBeNull();
  });

  it("les tuiles des intégrations disent « — » et non le zéro d'un pool qu'on n'a pas lu", async () => {
    pannes.lectures.add("chargerPoolMcp");
    rendreAvecEtat(<EcranIntegrations />, {}, PROJET);

    await screen.findByText(indisponible("le pool de ce projet"));
    const tuiles = screen.getByRole("region", {
      name: "Vue d'ensemble des intégrations",
    });
    expect(tuiles).not.toHaveTextContent(/\d/);
    expect(tuiles.textContent?.match(/—/g)).toHaveLength(3);
  });

  it("les coûts ne restent ni « en chargement » ni sur leurs compteurs", () => {
    pannes.analytics = ErreurApi.injoignable("/api/analytics/couts");
    rendreAvecEtat(<PageCouts />, {}, PROJET);

    expect(
      screen.getByText(indisponible("les coûts de banc-s1-vider")),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Chargement des agrégats/)).toBeNull();
    expect(screen.queryByRole("region", { name: "Totaux de la période" })).toBeNull();
  });
});

describe("② la perte du magasin en route, que l'écran n'a pas relue", () => {
  function sousLeShell(ui: ReactNode): ReactNode {
    return <FournisseurMagasin>{ui}</FournisseurMagasin>;
  }

  it("le tableau de bord déjà chargé cède sa place à la panne", async () => {
    poserSante({ statut: "degrade", magasin: PERDU });
    // Aucune erreur de lecture : l'écran a été lu avant la perte, et rien ne
    // l'a relu depuis. Seule la santé sondée par le shell la sait.
    rendreAvecEtat(
      sousLeShell(<TableauDeBord />),
      { taches: [tacheFactice()], executions: [runFactice()] },
      PROJET,
    );

    expect(
      await screen.findByText(indisponible("l'état de banc-s1-vider")),
    ).toBeInTheDocument();
    expect(screen.queryByText("Run en cours")).toBeNull();
    expect(screen.queryByText(/Aucun run en cours/)).toBeNull();
    expect(screen.queryByText(/Aucun événement reçu/)).toBeNull();
  });

  it("le journal déjà lu ne se donne plus pour le journal du projet", async () => {
    poserSante({ statut: "degrade", magasin: PERDU });
    rendreAvecEtat(sousLeShell(<PageJournal />), {}, PROJET);

    expect(
      await screen.findByText(indisponible("le journal de banc-s1-vider")),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Rien encore/)).toBeNull();
  });
});

describe("③ ce qui porte un geste reste sous le bandeau", () => {
  it("le tableau de bord garde la demande d'arbitrage déjà lue", () => {
    rendreAvecEtat(
      <TableauDeBord />,
      { erreur: INJOIGNABLE, validations: [validationFactice()] },
      PROJET,
    );

    expect(screen.getByText("Publier la version 1.2")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approuver" })).toBeEnabled();
    expect(
      screen.getByText(indisponible("l'état de banc-s1-vider")),
    ).toBeInTheDocument();
  });

  it("les validations gardent la file, et disent ce qu'elles n'ont pas pu lire", () => {
    rendreAvecEtat(
      <PageValidations />,
      { erreur: INJOIGNABLE, validations: [validationFactice()] },
      PROJET,
    );

    expect(screen.getByText("Publier la version 1.2")).toBeInTheDocument();
    expect(
      screen.getByText(
        indisponible("les arbitrages déjà tranchés sur banc-s1-vider"),
      ),
    ).toBeInTheDocument();
  });
});

describe("④ un vrai vide reste un vide", () => {
  it("le tableau de bord d'un projet neuf dit « rien encore », pas une panne", async () => {
    rendreAvecEtat(sousLeShellVide(<TableauDeBord />), {}, PROJET);

    expect(await screen.findByText(/Rien encore sur banc-s1-vider/)).toBeInTheDocument();
    expect(screen.queryByText(/Impossible de lire/)).toBeNull();
  });

  it("le journal d'un projet sans événement le dit, sans parler de panne", async () => {
    rendreAvecEtat(sousLeShellVide(<PageJournal />), {}, PROJET);

    expect(
      await screen.findByText(/aucun événement de ce projet n'a été consigné/),
    ).toBeInTheDocument();
    expect(screen.getByText("0 événement(s)")).toBeInTheDocument();
    expect(screen.queryByText(/Impossible de lire/)).toBeNull();
  });

  it("le catalogue vide invite toujours à créer un agent", async () => {
    // Un catalogue vide **lu** : les sondes des écrans en posent deux fiches.
    pannes.reponses.set("chargerCatalogue", []);
    rendreAvecEtat(sousLeShellVide(<ListeAgents />), {}, PROJET);

    expect(await screen.findByText(/Aucun agent au catalogue/)).toBeInTheDocument();
    expect(screen.queryByText(/Impossible de lire/)).toBeNull();
  });
});

/** Sous le shell, magasin sain : la sonde répond, aucun signal. */
function sousLeShellVide(ui: ReactNode): ReactNode {
  return <FournisseurMagasin>{ui}</FournisseurMagasin>;
}

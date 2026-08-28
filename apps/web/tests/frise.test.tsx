/**
 * La **frise d'activité** d'un run (#355) — l'écran, et ce qu'il garde.
 *
 * Le défaut qui a motivé le ticket ne se voit pas dans un test de données : il se
 * voit à l'écran, et il se dit en une phrase — pendant un run, **une attente de
 * décision humaine était indiscernable d'un travail en cours** (53 minutes
 * perdues le 14 août). Ces tests gardent donc trois choses, dans l'ordre des
 * critères :
 *
 * ① **Les deux flux sur une même frise, dans l'ordre du temps.** Statuts de tâche
 *    et messages inter-agents se lisent en lignes successives, et chaque ligne
 *    porte son horodatage et son objet.
 *
 * ② **Les couloirs.** Un tableau : le temps en lignes, les agents en colonnes.
 *    L'entrée d'un agent est dans **sa** colonne — vérifié par l'indice de
 *    cellule, seule façon de prouver un rangement (un `getByText` dirait
 *    seulement qu'elle est quelque part). Le couloir de repli existe dès qu'il a
 *    quelque chose, et **aucune entrée ne se perd** : c'est l'invariant du
 *    deuxième critère, vérifié à l'écran comme il l'est côté serveur.
 *
 * ③ **Les trois états, à l'œil.** Bloquée, attente humaine et en cours portent
 *    trois badges distincts, sans qu'on ouvre quoi que ce soit. La légende les
 *    nomme côte à côte — parce que « bloquée » et « en attente d'un humain » se
 *    ressemblent en ceci qu'aucune des deux n'avance, et que c'est exactement la
 *    confusion à lever.
 *
 * Et deux gardes qui n'appartiennent à aucun critère mais au dépôt : **le front
 * ne retrie rien** (l'ordre vient du backend, et une seconde règle de tri
 * finirait par contredire la première), et **la borne se dit** (une frise qui
 * rendrait ses dernières lignes en silence ferait passer un run d'une heure pour
 * un run court).
 *
 * Réseau débranché comme partout (`tests/setup.ts`) : `chargerFriseExecution`
 * est mocké ici, la vue est la vraie.
 */

import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { VueRun } from "@/components/runs/VueRun";
import {
  STATUT_EN_ATTENTE_VALIDATION,
  type FriseRun,
  type PageJournal,
} from "@/lib/types";

import {
  entreeFriseFactice,
  friseFactice,
  grapheFactice,
  pageJournalCourante,
  projetFactice,
  rendreAvecEtat,
  runFactice,
} from "./aides";

const RUN = "3ff0bcb065f9";

/** Ce que la fausse lecture rendra, et avec quel run on l'a appelée. */
const lecture = vi.hoisted(() => ({
  frise: null as FriseRun | null,
  appels: [] as string[],
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...reel,
    // Reconduits : ce mock **remplace** celui de `tests/setup.ts`.
    chargerProjets: async () => [],
    chargerJournal: async (): Promise<PageJournal> => pageJournalCourante(),
    chargerTaches: async () => [],
    chargerGrapheExecution: async () => grapheFactice({ run_id: RUN }),
    chargerFriseExecution: async (runId: string) => {
      lecture.appels.push(runId);
      return lecture.frise;
    },
  };
});

beforeEach(() => {
  lecture.frise = friseFactice({ run_id: RUN });
  lecture.appels.length = 0;
});

const monter = (partiel = {}) =>
  rendreAvecEtat(
    <VueRun runId={RUN} />,
    {
      executions: [
        runFactice({ run_id: RUN, objectif: "Prototyper un mini-CRM" }),
      ],
      ...partiel,
    },
    projetFactice({ id: "prj-7f3a1c2b", nom: "Dépensio" }),
  );

/** Bascule sur la frise — la vue ouvre sur le pipeline depuis #491. */
const versLaFrise = () =>
  userEvent.click(screen.getByRole("button", { name: "Frise" }));

/** Monte la vue du run, ouvre l'onglet Frise et rend sa région. */
async function frise() {
  monter();
  await versLaFrise();
  return within(
    await screen.findByRole("region", { name: "Frise d'activité du run" }),
  );
}

/** Les libellés des colonnes, hors la première (« Heure »). */
function couloirs(): string[] {
  return screen
    .getAllByRole("columnheader")
    .slice(1)
    .map((entete) => entete.textContent ?? "");
}

// ------------------------- ① Les deux flux, sur une même frise triée

describe("la frise mêle les deux flux dans l'ordre du temps", () => {
  it("rend les statuts de tâche et les messages en lignes successives", async () => {
    lecture.frise = friseFactice({
      run_id: RUN,
      entrees: [
        entreeFriseFactice({
          id: "j-0001",
          type: "tache.statut",
          statut: "en_cours",
          titre: "Schéma",
          objet: "démarrage de la tâche",
          horodatage: "2026-08-28T10:00:00+00:00",
        }),
        entreeFriseFactice({
          id: "j-0002",
          type: "message.inter_agents",
          statut: "",
          objet: "handoff de developpeur à qa : à toi",
          horodatage: "2026-08-28T10:00:05+00:00",
        }),
      ],
    });
    const vue = await frise();

    // Deux lignes de corps, dans l'ordre servi.
    const lignes = vue.getAllByRole("row").slice(1);
    expect(lignes).toHaveLength(2);
    expect(lignes[0]).toHaveTextContent("démarrage de la tâche");
    expect(lignes[1]).toHaveTextContent("handoff de developpeur à qa : à toi");
  });

  it("porte sur chaque entrée son horodatage, son agent et son objet", async () => {
    lecture.frise = friseFactice({
      run_id: RUN,
      entrees: [
        entreeFriseFactice({
          id: "j-0001",
          agent: "qa",
          couloir: "qa",
          role: "Testeur",
          objet: "notification de qa à developpeur : je reprends",
          horodatage: "2026-08-28T14:32:07+00:00",
        }),
      ],
    });
    const vue = await frise();

    // L'horodatage : une `<time>` machine-lisible, doublée de l'heure locale.
    const heure = vue.getByText((_, noeud) => noeud?.tagName === "TIME");
    expect(heure).toHaveAttribute("dateTime", "2026-08-28T14:32:07+00:00");
    // L'agent : porté par l'en-tête de colonne, donc annoncé avec la cellule —
    // c'est tout l'intérêt d'une table plutôt que d'une grille de `div`.
    expect(couloirs()[0]).toContain("qa");
    expect(vue.getByText(/je reprends/)).toBeInTheDocument();
  });

  it("ne répète pas l'objet quand il ne dit rien de plus que le titre", async () => {
    // L'issue **réussie** d'une tâche ne porte aucun détail : le backend
    // retombe alors sur le titre, et l'afficher deux fois n'apprendrait rien.
    lecture.frise = friseFactice({
      run_id: RUN,
      entrees: [
        entreeFriseFactice({
          type: "tache.statut",
          statut: "terminee",
          titre: "Schéma",
          objet: "Schéma",
        }),
      ],
    });
    const vue = await frise();

    expect(vue.getAllByText("Schéma")).toHaveLength(1);
  });

  it("n'invente aucun ordre : elle rend les entrées comme elles arrivent", async () => {
    // Le tri (instant, puis rang du journal) vit dans l'agrégat. Un second tri
    // ici finirait par contredire le premier — d'où une frise servie « à
    // l'envers » que la vue rend telle quelle.
    lecture.frise = friseFactice({
      run_id: RUN,
      entrees: [
        entreeFriseFactice({ id: "j-0009", objet: "servi en premier" }),
        entreeFriseFactice({ id: "j-0002", objet: "servi en second" }),
      ],
    });
    const vue = await frise();

    const lignes = vue.getAllByRole("row").slice(1);
    expect(lignes[0]).toHaveTextContent("servi en premier");
    expect(lignes[1]).toHaveTextContent("servi en second");
  });
});

// ------------------------- ② Les couloirs : rangée par agent, et rien ne se perd

describe("les couloirs", () => {
  it("ouvrent une colonne par agent du run, muet compris", async () => {
    lecture.frise = friseFactice({
      run_id: RUN,
      entrees: [entreeFriseFactice({ agent: "developpeur", couloir: "developpeur" })],
      couloirs: [
        { agent: "developpeur", role: "Développeur", repli: false, entrees: ["j-0001"] },
        { agent: "qa", role: "Testeur", repli: false, entrees: [] },
      ],
    });
    await frise();

    expect(couloirs()).toHaveLength(2);
    expect(couloirs()[1]).toContain("qa");
    // Une file muette est une information : le couloir existe et dit son compte.
    expect(couloirs()[1]).toContain("0 entrée(s)");
  });

  it("posent chaque entrée dans la colonne de son agent", async () => {
    lecture.frise = friseFactice({
      run_id: RUN,
      entrees: [
        entreeFriseFactice({
          id: "j-0001",
          agent: "developpeur",
          couloir: "developpeur",
          objet: "au développeur",
        }),
        entreeFriseFactice({
          id: "j-0002",
          agent: "qa",
          couloir: "qa",
          role: "Testeur",
          objet: "au testeur",
        }),
      ],
    });
    const vue = await frise();

    // L'indice de cellule, et non un `getByText` : c'est le **rangement** qui
    // est en cause, pas la présence.
    const lignes = vue.getAllByRole("row").slice(1);
    const premiere = within(lignes[0]).getAllByRole("cell");
    const seconde = within(lignes[1]).getAllByRole("cell");
    expect(premiere[0]).toHaveTextContent("au développeur");
    expect(premiere[1]).toBeEmptyDOMElement();
    expect(seconde[0]).toBeEmptyDOMElement();
    expect(seconde[1]).toHaveTextContent("au testeur");
  });

  it("recueillent au repli ce qu'aucun agent ne porte, et l'expliquent", async () => {
    // Le moteur consigne `agent="—"` sur une tâche jamais routée : le backend
    // la range au repli, et l'écran dit pourquoi ce couloir existe — sans quoi
    // « Sans agent » se lirait comme un défaut d'affichage.
    lecture.frise = friseFactice({
      run_id: RUN,
      entrees: [
        entreeFriseFactice({
          id: "j-0001",
          type: "tache.statut",
          statut: "bloquee",
          agent: "—",
          couloir: "",
          objet: "dépendance(s) non satisfaite(s) : T-0 (echec)",
        }),
      ],
    });
    const vue = await frise();

    expect(couloirs()[0]).toContain("Sans agent");
    expect(vue.getByText(/jamais été routée/)).toBeInTheDocument();
  });

  it("ne perdent aucune entrée : chaque couloir servi est une colonne", async () => {
    lecture.frise = friseFactice({
      run_id: RUN,
      entrees: [
        entreeFriseFactice({ id: "j-0001", agent: "developpeur", couloir: "developpeur" }),
        entreeFriseFactice({ id: "j-0002", agent: "—", couloir: "" }),
        entreeFriseFactice({ id: "j-0003", agent: "qa", couloir: "qa", role: "Testeur" }),
      ],
    });
    const vue = await frise();

    // Autant de lignes que d'entrées, et chacune occupe exactement une cellule.
    const lignes = vue.getAllByRole("row").slice(1);
    expect(lignes).toHaveLength(3);
    for (const ligne of lignes) {
      const remplies = within(ligne)
        .getAllByRole("cell")
        .filter((cellule) => cellule.textContent !== "");
      expect(remplies).toHaveLength(1);
    }
    // Le repli ferme la rangée des colonnes, jamais l'inverse.
    expect(couloirs().at(-1)).toContain("Sans agent");
  });
});

// ------------------------- ③ Les trois états, sans ouvrir de détail

describe("les trois états qui ne se confondent plus", () => {
  it("distingue bloquée, attente humaine et en cours", async () => {
    lecture.frise = friseFactice({
      run_id: RUN,
      entrees: [
        entreeFriseFactice({
          id: "j-0001",
          type: "tache.statut",
          statut: "en_cours",
          titre: "Schéma",
          objet: "démarrage de la tâche",
        }),
        entreeFriseFactice({
          id: "j-0002",
          type: "validation.demande",
          statut: STATUT_EN_ATTENTE_VALIDATION,
          titre: "Déployer",
          objet: "déploiement en production",
        }),
        entreeFriseFactice({
          id: "j-0003",
          type: "tache.statut",
          statut: "bloquee",
          agent: "—",
          couloir: "",
          titre: "Recette",
          objet: "dépendance(s) non satisfaite(s) : T-0 (echec)",
        }),
      ],
    });
    const vue = await frise();

    const lignes = vue.getAllByRole("row").slice(1);
    expect(lignes[0]).toHaveTextContent("En cours");
    expect(lignes[1]).toHaveTextContent("Attente humaine");
    expect(lignes[2]).toHaveTextContent("Bloquée");
  });

  it("nomme les trois côte à côte dans une légende", async () => {
    // « Bloquée » et « en attente d'un humain » se ressemblent en ceci
    // qu'aucune des deux n'avance : les nommer ensemble est la moitié du remède.
    lecture.frise = friseFactice({
      run_id: RUN,
      entrees: [entreeFriseFactice({})],
    });
    const vue = await frise();

    const legende = vue.getAllByRole("listitem").map((item) => item.textContent);
    expect(legende).toEqual(["En cours", "Attente humaine", "Bloquée"]);
  });

  it("donne un libellé à un message, qui n'a aucun statut de tâche", async () => {
    // Une pastille porte toujours du texte : la couleur n'a jamais le sens à
    // elle seule (règle du socle visuel).
    lecture.frise = friseFactice({
      run_id: RUN,
      entrees: [entreeFriseFactice({ type: "message.inter_agents", statut: "" })],
    });
    const vue = await frise();

    const ligne = vue.getAllByRole("row")[1];
    expect(within(ligne).getByText("Message")).toBeInTheDocument();
  });
});

// ------------------------- La lecture, la borne et les vides

describe("la lecture de la frise", () => {
  it("est demandée pour ce run, sans portée de projet", async () => {
    monter();
    await versLaFrise();

    await waitFor(() => expect(lecture.appels).toEqual([RUN]));
  });

  it("dit la borne au lieu de tronquer en silence", async () => {
    lecture.frise = friseFactice({
      run_id: RUN,
      entrees: [entreeFriseFactice({})],
      total: 812,
    });
    const vue = await frise();

    expect(vue.getByText(/1 entrée sur 812/)).toBeInTheDocument();
    expect(vue.getByText(/les plus récentes sur 812/)).toBeInTheDocument();
  });

  it("ne se dit pas tronquée quand tout est là", async () => {
    lecture.frise = friseFactice({
      run_id: RUN,
      entrees: [entreeFriseFactice({})],
    });
    const vue = await frise();

    expect(vue.queryByText(/les plus récentes sur/)).not.toBeInTheDocument();
  });

  it("explique un run sans activité au lieu d'afficher un tableau vide", async () => {
    lecture.frise = friseFactice({ run_id: RUN, entrees: [] });
    const vue = await frise();

    expect(vue.getByText(/Aucune tâche pour ce run/)).toBeInTheDocument();
    expect(vue.queryByRole("table")).not.toBeInTheDocument();
  });

  it("dit la panne de lecture au lieu de se lire « ce run n'a rien fait »", async () => {
    lecture.frise = null;
    const vue = await frise();

    // `null` sans erreur : la lecture n'a pas encore abouti pour ce run.
    expect(vue.queryByRole("table")).not.toBeInTheDocument();
  });
});

// ------------------------- Le débordement, que jsdom ne mesure pas

describe("le tableau des couloirs", () => {
  /**
   * ⚠ Une **déclaration**, pas une mesure : jsdom ne calcule ni largeur ni
   * défilement (#308), et le pixel appartient au skill `/banc-mise-en-page`. Ce
   * qu'il garde est la chaîne de classes, exactement comme la colonne collante
   * de `/couts` dans `sobriete.test.tsx` et le plancher de 24 px des cibles
   * dans `a11y.test.tsx`.
   *
   * Ce qu'il empêche : un run à six agents qui **pousse la page**. Les deux
   * utilitaires vont ensemble et n'ont de sens qu'ensemble — `min-w-max` sur la
   * table est ce qui fait qu'un couloir garde sa largeur au lieu de se
   * comprimer, et c'est précisément ce qui la fait déborder ; `overflow-x-auto`
   * sur le conteneur est ce qui garde ce débordement **chez lui**. Retirer le
   * second laisserait le corps de la page défiler horizontalement, ce que la
   * règle du dépôt sur le contenu large interdit ; retirer le premier rendrait
   * six colonnes illisibles plutôt qu'une frise.
   */
  it("garde son débordement chez lui plutôt que de pousser la page", () => {
    const source = readFileSync(
      path.join(
        path.dirname(fileURLToPath(import.meta.url)),
        "../components/runs/FriseRun.tsx",
      ),
      "utf8",
    );
    expect(source).toContain('className="overflow-x-auto"');
    expect(source).toContain("min-w-max");
  });
});

// ------------------------- L'onglet, dans la bascule

describe("l'onglet Frise", () => {
  it("s'insère avant le journal, qui ferme toujours la rangée", async () => {
    monter();

    const bascule = within(
      await screen.findByRole("navigation", { name: "Lectures de ce run" }),
    );
    const onglets = bascule
      .getAllByRole("button")
      .map((bouton) => bouton.textContent);
    expect(onglets).toEqual(["Pipeline", "Kanban", "Frise", "Journal"]);
  });

  it("ne montre qu'une lecture à la fois", async () => {
    monter();
    await versLaFrise();

    expect(
      await screen.findByRole("region", { name: "Frise d'activité du run" }),
    ).toBeInTheDocument();
    // Les vues ne se concurrencent jamais sur le même écran (#488).
    expect(
      screen.queryByRole("region", { name: "Pipeline du run" }),
    ).not.toBeInTheDocument();
  });
});

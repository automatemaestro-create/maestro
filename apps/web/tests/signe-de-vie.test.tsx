/**
 * Le **signe de vie** d'une tâche qui travaille, à l'écran (#837, lot 3 de #834 ;
 * couvert ici par #838, lot 4).
 *
 * Le backend sert `activite` — le dernier geste de l'agent et son horodatage —
 * sur la carte de tâche, le nœud `en_cours` du graphe et le couloir de la frise,
 * et `null` sur tout ce qui ne travaille pas (#836, gardé côté contrat par
 * `tests/test_run_qui_travaille.py`). Ce fichier garde la **moitié visible** :
 * ce que la ligne `LigneSigneDeVie` montre, où elle se monte, et ce qui la fait
 * compter. Trois étages, du plus pur au plus rendu :
 *
 * ① **Le format** (`lib/format.formatAnciennete`) — le seul du module qui
 *    descende sous la minute, parce qu'un geste d'agent tombe toutes les 5 à
 *    15 secondes : « il y a 12 s » dit *ça bouge*, « il y a 4 min » dit *ça s'est
 *    peut-être arrêté*. Le motif est prouvé sur le format d'**avant** :
 *    `formatHeureRelative` rend l'heure absolue sous la minute, c'est-à-dire un
 *    signe qui ne dit pas son âge.
 *
 * ② **La feuille et son horloge** (`components/SigneDeVie`, `lib/horloge`) —
 *    l'ancienneté avance **sans rechargement**, au pas de la seconde, et c'est
 *    la feuille seule qui s'abonne : un timer pour tous les signes montés, lancé
 *    au premier et rendu au dernier. Horloge factice — un test qui attendrait
 *    deux vraies secondes mesurerait la machine, pas le code.
 *
 * ③ **Les trois surfaces** (`VueRun` : Pipeline, Kanban, frise) — le signe
 *    n'apparaît que sur ce qui travaille, une tâche arrêtée rend la carte
 *    d'avant, un run soldé ne porte aucun `[data-signe-de-vie]`. Et le Pipeline
 *    ajoute **sa** réserve à celle du serveur : l'attente humaine l'emporte sur
 *    le signe comme elle l'emporte sur l'état — prouvé sur un **échantillon
 *    fautif**, un nœud arrêté que le payload doterait quand même d'un signe.
 *
 * ⚠ jsdom ne mesure rien (#308) : la largeur bornée de l'en-tête de couloir se
 * garde en chaîne de classes, comme le débordement de `frise.test.tsx`.
 *
 * Réseau débranché comme partout (`tests/setup.ts`) : les trois lectures d'un
 * run sont mockées ici, les vues sont les vraies.
 */

import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LigneSigneDeVie } from "@/components/SigneDeVie";
import { VueRun } from "@/components/runs/VueRun";
import { formatAnciennete, formatHeure, formatHeureRelative } from "@/lib/format";
import { useHorloge } from "@/lib/horloge";
import {
  ARETE_FRANCHIE,
  type FriseRun,
  type GrapheRun,
  type PageJournal,
  type SigneDeVie,
  type Tache,
} from "@/lib/types";

import {
  entreeFriseFactice,
  friseFactice,
  grapheFactice,
  noeudGrapheFactice,
  pageJournalCourante,
  projetFactice,
  rendreAvecEtat,
  runFactice,
  tacheFactice,
  validationFactice,
} from "./aides";

const RUN = "3ff0bcb065f9";

/** L'instant « maintenant » des tests à horloge factice, et un geste 12 s avant. */
const MAINTENANT = new Date("2026-08-30T07:41:12+00:00").getTime();
const GESTE: SigneDeVie = {
  horodatage: "2026-08-30T07:41:00+00:00",
  libelle: "Écrit api/contacts.py, puis relit le résultat",
};

/** Ce que les fausses lectures rendront — même dispositif que `pipeline.test`. */
const lecture = vi.hoisted(() => ({
  taches: [] as Tache[],
  graphe: null as GrapheRun | null,
  frise: null as FriseRun | null,
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...reel,
    chargerProjets: async () => [],
    chargerJournal: async (): Promise<PageJournal> => pageJournalCourante(),
    chargerTaches: async () => lecture.taches,
    chargerGrapheExecution: async () => lecture.graphe,
    chargerFriseExecution: async () => lecture.frise,
  };
});

beforeEach(() => {
  lecture.taches = [];
  lecture.graphe = null;
  lecture.frise = null;
});

/** Les lignes de signe de vie montées, où qu'elles soient. */
const signes = (racine: ParentNode = document) =>
  racine.querySelectorAll("[data-signe-de-vie]");

/* ==================================================================== *
 * ① Le format : sous la minute, et pas ailleurs
 * ==================================================================== */

describe("l'ancienneté d'un signe de vie (formatAnciennete)", () => {
  const ilYA = (ms: number) =>
    formatAnciennete(new Date(MAINTENANT - ms).toISOString(), MAINTENANT);

  it("prouve son motif sur le format d'avant : sous la minute, il taisait l'âge", () => {
    // `formatHeureRelative` rend l'heure absolue sous la minute — juste pour une
    // ligne de journal, qu'on corrèle ; faux pour un signe, dont l'âge est tout.
    const geste = new Date(MAINTENANT - 12_000).toISOString();
    expect(formatHeureRelative(geste, MAINTENANT)).toBe(formatHeure(geste));
    expect(formatHeureRelative(geste, MAINTENANT)).not.toMatch(/il y a/);
  });

  it("compte les secondes, parce que c'est là que « ça bouge » se lit", () => {
    expect(ilYA(12_000)).toBe("il y a 12 s");
    expect(ilYA(59_999)).toBe("il y a 59 s");
  });

  it("dit « à l'instant » sous la seconde et sur un âge négatif", () => {
    // Horloges désaccordées entre le poste et le backend : on n'écrit pas
    // « il y a -2 s ».
    expect(ilYA(400)).toBe("à l'instant");
    expect(ilYA(-2_000)).toBe("à l'instant");
  });

  it("délègue au-delà de la minute plutôt que de recopier les paliers", () => {
    // Deux tables de paliers finiraient par diverger : au-delà de la minute, le
    // signe dit exactement ce que dit une ligne du fil.
    for (const age of [60_000, 4 * 60_000, 3 * 3_600_000]) {
      const geste = new Date(MAINTENANT - age).toISOString();
      expect(formatAnciennete(geste, MAINTENANT)).toBe(
        formatHeureRelative(geste, MAINTENANT),
      );
    }
    expect(ilYA(4 * 60_000)).toBe("il y a 4 min");
  });

  it("rend l'heure absolue tant que l'horloge n'a pas démarré", () => {
    // `null` : rendu serveur ou première image — la même chose des deux côtés.
    expect(formatAnciennete(GESTE.horodatage, null)).toBe(formatHeure(GESTE.horodatage));
  });

  it("ne fabrique rien d'un horodatage vide ou illisible", () => {
    expect(formatAnciennete("", MAINTENANT)).toBe("");
    expect(formatAnciennete("pas une date", MAINTENANT)).toBe("pas une date");
  });
});

/* ==================================================================== *
 * ② La feuille et son horloge : l'âge avance sans rechargement
 * ==================================================================== */

describe("la ligne de signe de vie (LigneSigneDeVie)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(MAINTENANT);
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("montre le geste, son ancienneté, et une <time> machine-lisible", () => {
    render(<LigneSigneDeVie signe={GESTE} />);

    const ligne = signes()[0];
    expect(ligne).toHaveTextContent("Écrit api/contacts.py, puis relit le résultat");
    expect(ligne).toHaveTextContent("il y a 12 s");
    expect(ligne.querySelector("time")).toHaveAttribute("dateTime", GESTE.horodatage);
    // Le texte porte tout : un lecteur d'écran sait ce que la ligne est.
    expect(ligne).toHaveTextContent("Dernier geste de l'agent");
  });

  it("fait avancer l'ancienneté chaque seconde, sans rechargement", () => {
    // Le critère de #837 : « l'ancienneté se rafraîchit sans recharger la page ».
    // Rien ne vient du serveur ici — seule l'horloge fine bat.
    render(<LigneSigneDeVie signe={GESTE} />);
    expect(signes()[0]).toHaveTextContent("il y a 12 s");

    act(() => {
      vi.advanceTimersByTime(2_000);
    });

    expect(signes()[0]).toHaveTextContent("il y a 14 s");
  });

  it("dit « ça s'est peut-être arrêté » en changeant de palier, tout seul", () => {
    render(<LigneSigneDeVie signe={GESTE} />);

    act(() => {
      vi.advanceTimersByTime(4 * 60_000);
    });

    expect(signes()[0]).toHaveTextContent("il y a 4 min");
  });

  it("tient un seul timer pour tous les signes montés, et le rend au dernier", () => {
    // Un `setInterval` par ligne coûterait sans rien apporter : les signes d'un
    // écran partagent le même instant. Compté sur les appels au pas de la
    // seconde, et non sur les timers en attente — React en pose aussi.
    const poses = vi.spyOn(globalThis, "setInterval");
    const rendus = vi.spyOn(globalThis, "clearInterval");
    const auPasFin = () => poses.mock.calls.filter(([, pas]) => pas === 1_000);

    const { unmount } = render(
      <>
        <LigneSigneDeVie signe={GESTE} />
        <LigneSigneDeVie signe={{ ...GESTE, libelle: "Lance la suite" }} />
        <LigneSigneDeVie signe={GESTE} taille="micro" />
      </>,
    );
    expect(signes()).toHaveLength(3);
    expect(auPasFin()).toHaveLength(1);
    const indice = poses.mock.calls.findIndex(([, pas]) => pas === 1_000);
    const minuterie = poses.mock.results[indice]?.value;

    unmount();

    // Plus personne n'écoute : le timer est rendu, et c'est bien celui-là.
    expect(rendus.mock.calls.some(([m]) => m === minuterie)).toBe(true);
    // Un nouveau montage relance le battement — rien n'est resté accroché.
    render(<LigneSigneDeVie signe={GESTE} />);
    expect(auPasFin()).toHaveLength(2);

    poses.mockRestore();
    rendus.mockRestore();
  });

  it("n'entraîne pas l'horloge à 30 s dans son pas", () => {
    // Passer tout le fil d'activité à la seconde ferait re-rendre des dizaines
    // de lignes chaque seconde pour des étiquettes à la minute : la seconde
    // horloge n'est pas le pas de l'autre.
    const rendusLents: (number | null)[] = [];
    function Lente() {
      rendusLents.push(useHorloge());
      return null;
    }
    render(
      <>
        <Lente />
        <LigneSigneDeVie signe={GESTE} />
      </>,
    );
    const avant = rendusLents.length;

    act(() => {
      vi.advanceTimersByTime(5_000);
    });

    // La feuille a compté cinq fois ; la lente n'a pas re-rendu une seule fois.
    expect(signes()[0]).toHaveTextContent("il y a 17 s");
    expect(rendusLents.length).toBe(avant);
  });
});

/* ==================================================================== *
 * ③ Les trois surfaces, dans la vue d'un run
 * ==================================================================== */

/**
 * Le run de référence : un amont terminé, un nœud qui travaille, un nœud pas
 * démarré. Le signe n'est servi que sur celui qui travaille — c'est le contrat
 * (#836) —, et la carte de tâche dit la même chose que le nœud.
 */
function matiere(): { graphe: GrapheRun; taches: Tache[]; frise: FriseRun } {
  const graphe = grapheFactice({
    run_id: RUN,
    noeuds: [
      noeudGrapheFactice({
        id: "schema",
        titre: "Schéma SQL",
        niveau: 0,
        dependants: ["api"],
        statut: "terminee",
        compartiment: "terminees",
        agent: "bdd",
        role: "Base de données",
        activite: null,
      }),
      noeudGrapheFactice({
        id: "api",
        titre: "API CRUD",
        niveau: 1,
        dependances: ["schema"],
        statut: "en_cours",
        compartiment: "en_cours",
        agent: "developpeur",
        activite: GESTE,
      }),
      noeudGrapheFactice({ id: "ui", titre: "UI liste", niveau: 1, rang: 1 }),
    ],
    aretes: [{ de: "schema", vers: "api", etat: ARETE_FRANCHIE }],
  });
  const taches = [
    tacheFactice({
      id: "schema",
      titre: "Schéma SQL",
      statut: "terminee",
      agent: "bdd",
      activite: null,
    }),
    tacheFactice({
      id: "api",
      titre: "API CRUD",
      statut: "en_cours",
      agent: "developpeur",
      activite: GESTE,
    }),
  ];
  const frise = friseFactice({
    run_id: RUN,
    entrees: [
      entreeFriseFactice({
        id: "j-0001",
        type: "tache.statut",
        statut: "terminee",
        agent: "bdd",
        couloir: "bdd",
        role: "Base de données",
        tache_id: "schema",
        titre: "Schéma SQL",
        objet: "Schéma SQL",
      }),
      entreeFriseFactice({
        id: "j-0002",
        type: "tache.statut",
        statut: "en_cours",
        agent: "developpeur",
        couloir: "developpeur",
        tache_id: "api",
        titre: "API CRUD",
        objet: "démarrage de la tâche",
        horodatage: "2026-08-30T07:30:00+00:00",
      }),
    ],
    couloirs: [
      { agent: "bdd", role: "Base de données", repli: false, entrees: ["j-0001"], activite: null },
      {
        agent: "developpeur",
        role: "Développeur",
        repli: false,
        entrees: ["j-0002"],
        activite: GESTE,
      },
    ],
  });
  return { graphe, taches, frise };
}

function runQuiTravaille() {
  const { graphe, taches, frise } = matiere();
  lecture.graphe = graphe;
  lecture.taches = taches;
  lecture.frise = frise;
}

/** Le même run, soldé : plus rien ne travaille, aucun signe servi nulle part. */
function runSolde() {
  const { graphe, taches, frise } = matiere();
  lecture.graphe = grapheFactice({
    ...graphe,
    noeuds: graphe.noeuds.map((noeud) => ({
      ...noeud,
      statut: "terminee",
      compartiment: "terminees",
      activite: null,
    })),
  });
  lecture.taches = taches.map((tache) => ({ ...tache, statut: "terminee", activite: null }));
  lecture.frise = friseFactice({
    ...frise,
    couloirs: frise.couloirs.map((couloir) => ({ ...couloir, activite: null })),
  });
}

const monter = (partiel = {}) =>
  rendreAvecEtat(
    <VueRun runId={RUN} />,
    { executions: [runFactice({ run_id: RUN, objectif: "Prototyper un mini-CRM" })], ...partiel },
    projetFactice({ id: "prj-7f3a1c2b", nom: "Dépensio" }),
  );

async function pipeline() {
  await screen.findByText("Schéma SQL");
  return screen.getByRole("region", { name: "Pipeline du run" });
}

async function kanban() {
  await userEvent.click(screen.getByRole("button", { name: "Kanban" }));
  await screen.findByText("API CRUD");
  return screen.getByRole("region", { name: "Tâches (Kanban)" });
}

async function frise() {
  await userEvent.click(screen.getByRole("button", { name: "Frise" }));
  return await screen.findByRole("region", { name: "Frise d'activité du run" });
}

/**
 * Le plus petit ancêtre du texte `titre` qui porte une ligne de signe de vie —
 * `null` s'il n'y en a aucun sous `racine`. C'est ce qui prouve le
 * **rangement** : une ligne présente quelque part ne dit pas sur quelle boîte.
 */
function porteurDuSigne(racine: HTMLElement, titre: string): HTMLElement | null {
  let noeud: HTMLElement | null = within(racine).getByText(titre);
  while (noeud !== null && noeud !== racine) {
    if (noeud.querySelector("[data-signe-de-vie]") !== null) return noeud;
    noeud = noeud.parentElement;
  }
  return null;
}

describe("le nœud en cours du Pipeline", () => {
  it("porte le signe, et lui seul", async () => {
    runQuiTravaille();
    monter();
    const vue = await pipeline();

    // Une seule ligne sur tout le graphe : celle du nœud qui travaille.
    expect(signes(vue)).toHaveLength(1);
    expect(signes(vue)[0]).toHaveTextContent("Écrit api/contacts.py, puis relit le résultat");
    // Sur la boîte « API CRUD » — l'ancêtre qui la porte ne contient pas l'autre —
    // et sur elle seule : le seul ancêtre de « Schéma SQL » qui porte un signe
    // est le graphe entier, où « API CRUD » est aussi.
    const boite = porteurDuSigne(vue, "API CRUD");
    expect(boite).not.toBeNull();
    expect(boite).not.toHaveTextContent("Schéma SQL");
    const autre = porteurDuSigne(vue, "Schéma SQL");
    expect(autre === null || autre.textContent?.includes("API CRUD")).toBe(true);
  });

  it("échantillon fautif : un nœud arrêté que le payload dote d'un signe ne le montre pas", async () => {
    // Le serveur ne sert jamais ça (#836). La vue a pourtant sa propre réserve —
    // le signe ne va que sur une boîte dessinée « En cours » —, et c'est elle
    // qu'on éprouve : sans cette moitié, le contrôle précédent vaudrait pour un
    // payload sain et rien d'autre.
    const { graphe } = matiere();
    runQuiTravaille();
    lecture.graphe = grapheFactice({
      ...graphe,
      noeuds: graphe.noeuds.map((noeud) =>
        noeud.id === "schema" ? { ...noeud, activite: GESTE } : noeud,
      ),
    });
    monter();
    const vue = await pipeline();

    expect(signes(vue)).toHaveLength(1);
    const autre = porteurDuSigne(vue, "Schéma SQL");
    expect(autre === null || autre.textContent?.includes("API CRUD")).toBe(true);
  });

  it("efface le signe dès que la tâche attend un humain", async () => {
    // L'attente humaine l'emporte sur le signe comme elle l'emporte sur l'état
    // (`lib/graphe.etatDuNoeud`) : une tâche arrêtée sur quelqu'un ne « bouge »
    // pas, quel qu'ait été son dernier geste — la distinction même de #355.
    runQuiTravaille();
    monter({ validations: [validationFactice({ tache_id: "api", statut: "en_attente" })] });
    const vue = await pipeline();

    expect(within(vue).getAllByText("Attente humaine").length).toBeGreaterThan(0);
    expect(signes(vue)).toHaveLength(0);
  });
});

describe("la carte du Kanban", () => {
  it("montre le signe sur la tâche qui travaille, et rien sur l'autre", async () => {
    runQuiTravaille();
    monter();
    const vue = await kanban();

    expect(signes(vue)).toHaveLength(1);
    const carte = porteurDuSigne(vue, "API CRUD");
    expect(carte).not.toBeNull();
    expect(carte).not.toHaveTextContent("Schéma SQL");
    const autre = porteurDuSigne(vue, "Schéma SQL");
    expect(autre === null || autre.textContent?.includes("API CRUD")).toBe(true);
  });
});

describe("le couloir de la frise", () => {
  it("porte le signe dans son en-tête, jamais comme une entrée", async () => {
    runQuiTravaille();
    monter();
    const vue = await frise();

    // L'en-tête du couloir qui travaille porte la ligne ; l'autre non.
    const entetes = screen.getAllByRole("columnheader").slice(1);
    expect(entetes.map((e) => e.querySelectorAll("[data-signe-de-vie]").length)).toEqual([0, 1]);
    expect(entetes[1]).toHaveTextContent("Écrit api/contacts.py, puis relit le résultat");
    // Jamais une entrée : autant de lignes que d'entrées servies, pas une de plus.
    expect(within(vue).getAllByRole("row").slice(1)).toHaveLength(2);
    expect(signes(vue)).toHaveLength(1);
  });

  it("borne la largeur de la ligne pour que la colonne ne se déforme pas", async () => {
    // Déclaration, pas mesure (#308) : une cellule de tableau s'élargit sinon
    // jusqu'au libellé entier, et c'est la colonne qui se déformerait.
    runQuiTravaille();
    monter();
    await frise();

    expect(signes()[0].className).toContain("max-w-48");
  });
});

describe("un run soldé", () => {
  it("rend exactement la vue d'avant : aucun signe, sur aucune des trois surfaces", async () => {
    runSolde();
    monter();
    const pipe = await pipeline();
    expect(signes(pipe)).toHaveLength(0);

    const cartes = await kanban();
    expect(signes(cartes)).toHaveLength(0);

    const couloirs = await frise();
    expect(signes(couloirs)).toHaveLength(0);
    expect(signes()).toHaveLength(0);
  });
});

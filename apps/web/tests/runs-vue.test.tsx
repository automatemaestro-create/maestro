/**
 * La vue d'un run : son Kanban, sa progression et son journal (#475/#478 ;
 * couvert ici par #480, lot 8 de #472).
 *
 * L'écran qui manquait : ouvrir un run donnait enfin son backlog, là où le Kanban
 * était celui du **projet** (#248) et où, dans un projet à plusieurs runs
 * successifs, *ce que ce run avait fait* n'était visible nulle part.
 *
 * Ce que ce fichier garde, et pourquoi :
 *
 * ① **L'appartenance vient de l'API, jamais d'un filtre local.** Filtrer
 *    `etatGlobal.taches` sur `Tache.run_id` aurait été gratuit et **faux** : ce
 *    champ porte le *dernier* run qui a touché la tâche, or un identifiant de
 *    tâche est un slug engendré depuis son contenu, donc partagé entre un run et
 *    sa **relance** (#349). On vérifie donc que la lecture part avec `?run=`.
 * ② **Trois vides qui ne se confondent pas** — run d'un autre projet, run arrêté
 *    sur son brief, API injoignable. Un Kanban vide non expliqué se lirait « ce
 *    run n'a rien fait ».
 * ③ **Le journal persisté** (#478) — il vient de l'API et non du fil du shell,
 *    qui ne contient que ce qui est passé par le WebSocket depuis l'ouverture de
 *    la page : un run terminé la veille n'y aurait rien.
 *
 * ⚠ Depuis #491 la vue ouvre sur le **pipeline** et non sur le Kanban
 * (`lib/vuesRun` porte l'arbitrage) : les contrôles qui regardent une carte de
 * Kanban basculent donc d'abord d'onglet. Ce n'est pas un détour de test, c'est
 * ce que fait quelqu'un devant l'écran.
 *
 * ⚠ Ni `chargerTaches` ni `chargerGrapheExecution` ne sont mockés par
 * `tests/setup.ts` : sans le `vi.mock` local ci-dessous, la vue partirait sur un
 * vrai `fetch` et n'afficherait qu'une bannière d'erreur. Le mock local
 * **remplace** celui du setup, d'où le `importOriginal` et la reconduction de
 * `chargerProjets`/`chargerJournal`.
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { VueRun } from "@/components/runs/VueRun";
import { FournisseurEtatGlobal } from "@/lib/etatGlobal";
import { evenementDepuisEntree, fusionnerJournal } from "@/lib/journal";
import {
  ARETE_FRANCHIE,
  EXECUTION_EN_ATTENTE_BRIEF,
  EXECUTION_ECHEC,
  EXECUTION_TERMINEE,
  CAUSE_PLAFOND_COUT,
  type GrapheRun,
  type PageJournal,
  type Tache,
} from "@/lib/types";

import {
  entreeJournalFactice,
  evenementFactice,
  grapheFactice,
  noeudGrapheFactice,
  pageJournalCourante,
  poserEtatGlobal,
  poserJournal,
  projetFactice,
  rendreAvecEtat,
  runFactice,
  tacheFactice,
  validationFactice,
} from "./aides";

/** Ce que les fausses lectures rendront, et avec quels arguments on les a appelées. */
const lecture = vi.hoisted(() => ({
  taches: [] as Tache[],
  appels: [] as { portee: string; runId?: string }[],
  graphe: null as GrapheRun | null,
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...reel,
    // Reconduits : ce mock **remplace** celui de `tests/setup.ts`.
    chargerProjets: async () => [],
    chargerJournal: async (): Promise<PageJournal> => pageJournalCourante(),
    chargerTaches: async (portee: string, runId?: string) => {
      lecture.appels.push({ portee, runId });
      return lecture.taches;
    },
    chargerGrapheExecution: async () => lecture.graphe,
  };
});

beforeEach(() => {
  lecture.taches = [];
  lecture.appels.length = 0;
  // Le cas courant : un run dont le graphe n'a rien à montrer. Un test qui
  // regarde le pipeline pose ses nœuds.
  lecture.graphe = grapheFactice({ run_id: RUN });
});

const RUN = "3ff0bcb065f9";

const monter = (partiel = {}, runId = RUN) =>
  rendreAvecEtat(
    <VueRun runId={runId} />,
    { executions: [runFactice({ run_id: RUN, objectif: "Prototyper un mini-CRM" })], ...partiel },
    projetFactice({ id: "prj-7f3a1c2b", nom: "Dépensio" }),
  );

/** Bascule sur le Kanban — la vue ouvre sur le pipeline depuis #491. */
const versLeKanban = () =>
  userEvent.click(screen.getByRole("button", { name: "Kanban" }));

// ------------------------- ① L'appartenance au run vient de l'API

describe("les tâches d'un run", () => {
  it("sont lues avec la portée run, et non filtrées sur la carte", async () => {
    // `Tache.run_id` porte le **dernier** run qui a touché la tâche : la vue d'un
    // run repris y perdrait les tâches que son propre successeur a reprises.
    lecture.taches = [tacheFactice({ id: "T-1", titre: "Schéma", run_id: "run-successeur" })];
    monter();

    await waitFor(() => expect(lecture.appels.length).toBeGreaterThan(0));
    expect(lecture.appels[0]).toEqual({ portee: "prj-7f3a1c2b", runId: RUN });
    // La tâche s'affiche, bien que sa carte nomme un autre run.
    await versLeKanban();
    expect(await screen.findByText("Schéma")).toBeInTheDocument();
  });

  it("ne sont pas demandées quand le run n'est pas celui du projet actif", async () => {
    // Aucune lecture à faire : l'écran dit qu'il ne connaît pas ce run.
    monter({ executions: [] });

    await screen.findByText(/Aucun run 3ff0bcb065f9 sur Dépensio/);
    expect(lecture.appels).toEqual([]);
  });

  it("se relisent au pouls du shell, sans seconde WebSocket", async () => {
    const projet = projetFactice({ id: "prj-7f3a1c2b", nom: "Dépensio" });
    const { rerender } = monter();
    await waitFor(() => expect(lecture.appels).toHaveLength(1));

    // Le shell ouvre **une** connexion pour toute l'application (#117/#281) ; la
    // vue s'abonne à son **pouls** — un compteur incrémenté à chaque lecture
    // aboutie — au lieu d'en rouvrir une. Un compteur et non « le tableau
    // `taches` a changé d'identité » : la seconde formule marcherait aujourd'hui
    // et cesserait sans bruit le jour où un rechargement comparerait avant de
    // poser son état.
    poserEtatGlobal({ executions: [runFactice({ run_id: RUN })], revision: 1 });
    rerender(
      <FournisseurEtatGlobal projet={projet}>
        <VueRun runId={RUN} />
      </FournisseurEtatGlobal>,
    );

    await waitFor(() => expect(lecture.appels.length).toBeGreaterThan(1));
  });
});

// -------------------------------- ② Trois vides qui ne se confondent pas

describe("les trois cas qui ne se confondent pas", () => {
  it("dit qu'un run est hors de portée, et renvoie à la liste", async () => {
    monter({ executions: [] });

    expect(
      await screen.findByText(/relève peut-être d'un autre/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Voir les runs du projet" }),
    ).toHaveAttribute("href", "/runs");
    // Jamais une vue vide : elle se lirait « ce run n'a rien fait ».
    expect(
      screen.queryByRole("region", { name: "Tâches (Kanban)" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("region", { name: "Pipeline du run" }),
    ).not.toBeInTheDocument();
  });

  it("explique la vue vide d'un run arrêté sur son brief", async () => {
    monter({
      executions: [
        runFactice({ run_id: RUN, statut: EXECUTION_EN_ATTENTE_BRIEF }),
      ],
    });

    expect(
      await screen.findByText(/la décomposition n'a pas encore eu lieu/),
    ).toBeInTheDocument();
  });

  it("dit qu'un run sans tâche attend ses événements, sans accuser personne", async () => {
    monter();

    // La phrase ne nomme aucune des deux vues (#491) : elles la partagent, et un
    // pipeline vide qui promettrait de remplir « le tableau » désignerait
    // l'écran d'à côté.
    expect(
      await screen.findByText(/cette vue se remplira dès qu'il publiera/),
    ).toBeInTheDocument();
    await versLeKanban();
    expect(
      await screen.findByText(/cette vue se remplira dès qu'il publiera/),
    ).toBeInTheDocument();
  });

  it("montre la bannière et rien d'autre quand l'API est injoignable", async () => {
    monter({ erreur: "connexion refusée", executions: [] });

    expect(await screen.findByRole("alert")).toHaveTextContent(/API injoignable/);
  });

  it("dit qu'il charge plutôt que de conclure à un run inconnu", () => {
    monter({ chargement: true, executions: [] });

    expect(screen.getByText("Chargement du run…")).toBeInTheDocument();
    expect(screen.queryByText(/Aucun run 3ff0bcb065f9/)).not.toBeInTheDocument();
  });
});

// ------------------------------------------- La tête du run, et ses renvois

describe("la tête de la vue", () => {
  it("porte l'objectif, le badge et la barre ample", async () => {
    monter({
      executions: [
        runFactice({
          run_id: RUN,
          objectif: "Prototyper un mini-CRM",
          nb_taches: 4,
          progression: {
            a_faire: 1,
            en_cours: 1,
            bloquees: 0,
            terminees: 2,
            echecs: 0,
            autres: 0,
            soldees: 2,
            total: 4,
          },
        }),
      ],
    });

    const tete = screen.getByRole("region", { name: "Run" });
    expect(within(tete).getByRole("heading", { level: 2 })).toHaveTextContent(
      "Prototyper un mini-CRM",
    );
    // Le compte arrive **compté** sur la machine à états du moteur (#473) :
    // recompter ici depuis les tâches chargées ferait d'une barre d'avancement la
    // mesure de sa propre pagination.
    expect(
      await screen.findByRole("progressbar", { name: "Progression du run" }),
    ).toHaveAttribute("aria-valuetext", "2 tâches soldées sur 4");
  });

  it("mène à la liste des runs", () => {
    monter();

    expect(screen.getByRole("link", { name: /Tous les runs/ })).toHaveAttribute(
      "href",
      "/runs",
    );
  });

  it("mène au run qu'il reprend — sinon le cadrage déjà payé serait hors de portée", () => {
    monter({
      executions: [runFactice({ run_id: RUN, reprise_de: "4b33ea332e60" })],
    });

    expect(screen.getByRole("link", { name: "4b33ea332e60" })).toHaveAttribute(
      "href",
      "/runs/4b33ea332e60",
    );
  });

  it("dit la cause d'arrêt d'un run tombé (#479)", () => {
    monter({
      executions: [
        runFactice({ run_id: RUN, statut: EXECUTION_ECHEC, cause: CAUSE_PLAFOND_COUT }),
      ],
    });

    expect(screen.getByText("Plafond de dépense atteint")).toBeInTheDocument();
  });

  it("porte le geste de pause, comme la liste", () => {
    monter();

    expect(
      screen.getByRole("button", { name: "Mettre en pause" }),
    ).toBeInTheDocument();
  });
});

// ------------------------------------------------- La vue pipeline (#491)

/**
 * Le strict nécessaire pour que le **dessin** soit joué : la couverture du
 * pipeline est différée au lot 4 (#492), mais les trois vides ci-dessus rendent
 * tous un graphe *sans nœud*, donc n'entrent jamais dans le graphe lui-même.
 * Quatre contrôles suffisent à ce qu'une régression de rendu ne passe pas
 * inaperçue jusque-là — un par critère du ticket.
 *
 * ⚠ jsdom ne calcule aucune géométrie : les rectangles y sont tous nuls, donc
 * aucune courbe n'est tracée (`Arete` s'abstient sans mesure). C'est voulu — la
 * géométrie se vérifie au skill `/banc-mise-en-page`, jamais ici (#306/#308).
 */
describe("le pipeline d'un run", () => {
  const troisTaches = () =>
    grapheFactice({
      run_id: RUN,
      noeuds: [
        noeudGrapheFactice({
          id: "schema",
          titre: "Schéma SQL",
          niveau: 0,
          dependants: ["api", "ui"],
          statut: "terminee",
          compartiment: "terminees",
          agent: "Développeur backend",
          etapes: [{ libelle: "Lister les entités", etat: "faite" }],
        }),
        noeudGrapheFactice({
          id: "api",
          titre: "API CRUD",
          niveau: 1,
          rang: 0,
          dependances: ["schema"],
          statut: "en_cours",
          compartiment: "en_cours",
          etapes: [
            { libelle: "Routes", etat: "faite" },
            { libelle: "Sérialiseurs", etat: "en_cours" },
          ],
        }),
        noeudGrapheFactice({
          id: "ui",
          titre: "UI liste",
          niveau: 1,
          rang: 1,
          dependances: ["schema"],
        }),
      ],
      aretes: [
        { de: "schema", vers: "api", etat: ARETE_FRANCHIE },
        { de: "schema", vers: "ui", etat: ARETE_FRANCHIE },
      ],
    });

  /** Le pipeline, une fois sa lecture aboutie — sinon on tient sa coquille. */
  const pipelineCharge = async () => {
    await screen.findByText("Schéma SQL");
    return screen.getByRole("region", { name: "Pipeline du run" });
  };

  it("rend un nœud par tâche du plan, avec son agent et sa checklist", async () => {
    lecture.graphe = troisTaches();
    monter();

    const pipeline = await pipelineCharge();
    expect(within(pipeline).getByText("Schéma SQL")).toBeInTheDocument();
    expect(within(pipeline).getByText(/Développeur backend/)).toBeInTheDocument();
    // Une case par étape, jamais un pourcentage (#489) : le dénominateur peut
    // grandir, une barre proportionnelle se lirait comme un recul.
    expect(within(pipeline).getByText("1/2")).toBeInTheDocument();
    expect(within(pipeline).getByText("Sérialiseurs")).toBeInTheDocument();
  });

  it("range les tâches parallèles au même niveau, et non en file", async () => {
    lecture.graphe = troisTaches();
    monter();

    // Deuxième critère : deux tâches sans dépendance entre elles tombent au même
    // niveau — donc dans la même colonne, lisibles comme simultanées.
    await pipelineCharge();
    const niveau = screen.getByRole("list", { name: "Niveau 2" });
    expect(within(niveau).getByText("API CRUD")).toBeInTheDocument();
    expect(within(niveau).getByText("UI liste")).toBeInTheDocument();
  });

  it("allume la suite dès que l'arête amont est franchie", async () => {
    lecture.graphe = troisTaches();
    monter();

    // « UI liste » n'a pas démarré, mais sa seule dépendance a rendu la main :
    // elle sort du retrait au lieu de rester « À faire » avec le reste du plan.
    const pipeline = await pipelineCharge();
    expect(
      within(pipeline).getAllByText("Prête à partir").length,
    ).toBeGreaterThan(0);
  });

  it("distingue ce qui attend un humain de ce qui travaille", async () => {
    // **Le** défaut d'origine du chantier (#355) : une attente de décision est
    // restée 53 minutes indiscernable d'un travail en cours. Elle ne se lit pas
    // sur la tâche — le moteur n'émet pas `en_attente_validation` — mais dans la
    // file des validations.
    lecture.graphe = troisTaches();
    monter({
      validations: [validationFactice({ tache_id: "api", statut: "en_attente" })],
    });

    const pipeline = await pipelineCharge();
    expect(
      within(pipeline).getAllByText("Attente humaine").length,
    ).toBeGreaterThan(0);
    // Et le nœud ne se lit plus « En cours », alors que c'est bien son
    // compartiment côté backend.
    expect(within(pipeline).queryByText("En cours")).not.toBeInTheDocument();
  });
});

// ----------------------------------------------- ③ Le journal persisté du run

describe("le journal d'un run", () => {
  it("part du journal persisté, et non du fil ouvert à l'instant", async () => {
    // Le défaut que #478 ferme : ouvrir la vue d'un run terminé la veille ne
    // montrait rien du tout, le fil du shell ne contenant que ce qui était passé
    // par le WebSocket **depuis l'ouverture de la page**.
    poserJournal([
      entreeJournalFactice({
        id: "j-0001",
        run_id: RUN,
        titre: "Planification",
        agent: "orchestrateur",
        horodatage: "2026-08-23T09:00:00+00:00",
      }),
    ]);
    monter({
      executions: [runFactice({ run_id: RUN, statut: EXECUTION_TERMINEE })],
      evenements: [],
    });

    const journal = await screen.findByRole("region", { name: "Journal du run" });
    expect(within(journal).getByText(/Planification/)).toBeInTheDocument();
  });

  it("dit son vide au lieu de laisser croire à une panne d'affichage", async () => {
    poserJournal([]);
    monter();

    expect(
      await screen.findByText("Aucun événement consigné pour ce run."),
    ).toBeInTheDocument();
  });
});

describe("la fusion du journal et du direct", () => {
  const ligne = (partiel = {}) =>
    evenementFactice({ run_id: RUN, horodatage: "2026-08-23T09:00:00+00:00", ...partiel });

  it("ne montre pas deux fois un événement arrivé par les deux chemins", () => {
    // Le direct pousse **par-dessus** un historique déjà là : le même événement
    // relu au démarrage et reçu par le WebSocket est une seule ligne.
    const commun = ligne({ titre: "Schéma" });

    expect(fusionnerJournal([commun], [{ ...commun }])).toHaveLength(1);
  });

  it("garde deux événements que seul leur détail sépare", () => {
    const a = ligne({ titre: "Schéma", detail: "démarrage" });
    const b = ligne({ titre: "Schéma", detail: "terminée" });

    expect(fusionnerJournal([a], [b])).toHaveLength(2);
  });

  it("remet le direct à sa place dans l'ordre, du plus récent au plus ancien", () => {
    const veille = ligne({ titre: "veille", horodatage: "2026-08-23T09:00:00+00:00" });
    const matin = ligne({ titre: "matin", horodatage: "2026-08-24T08:00:00+00:00" });
    const midi = ligne({ titre: "midi", horodatage: "2026-08-24T12:00:00+00:00" });

    expect(
      fusionnerJournal([midi, veille], [matin]).map((e) => e.titre),
    ).toEqual(["midi", "matin", "veille"]);
  });

  it("supporte l'un des deux côtés vide", () => {
    const seul = ligne({ titre: "seul" });

    expect(fusionnerJournal([], [seul])).toHaveLength(1);
    expect(fusionnerJournal([seul], [])).toHaveLength(1);
    expect(fusionnerJournal([], [])).toEqual([]);
  });
});

describe("une entrée de journal relue devient un événement du fil", () => {
  it("garde ce qu'une ligne dit, et laisse dehors ce qu'elle ne porte pas", () => {
    // `FilActivite` rend ici ce qu'il rend au tableau de bord et sur la page
    // Journal : la ligne d'activité n'est pas réécrite pour cet écran.
    const evenement = evenementDepuisEntree(
      entreeJournalFactice({
        run_id: RUN,
        titre: "Schéma",
        agent: "bdd",
        statut: "terminee",
      }),
    );

    expect(evenement.titre).toBe("Schéma");
    expect(evenement.agent).toBe("bdd");
    expect(evenement.run_id).toBe(RUN);
    // Le coût et l'usage restent lisibles là où ils ont un sens (le résumé d'un
    // run, les coûts) : une page de 200 entrées doit rester une page.
    expect(evenement.cout_usd).toBeNull();
    expect(evenement.usage).toBeNull();
  });
});

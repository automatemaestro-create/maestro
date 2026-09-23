/**
 * **Depuis la vue d'un run, quelle qu'elle soit, on tranche sur place et l'on y
 * reste** (#1228).
 *
 * Le défaut, rapporté le 2026-09-22 : *« Si je suis par exemple dans la page run
 * pipeline et que je dois trancher, ça me redirige vers la page validation et je
 * ne sais pas comment revenir directement sur le run pipeline. »* La page d'un
 * run n'offrait **aucun geste de décision** — la tête, le nœud de pipeline et la
 * carte de Kanban menaient tous les trois à `/validations`, d'où rien ne
 * ramenait.
 *
 * Ce que ces tests gardent, et qui est exactement ce qui peut se défaire :
 *
 * ① **on tranche là où l'on est** — la tête porte la carte entière, les deux
 *    lectures denses portent un **bouton** qui ouvre la demande dans un panneau.
 *    Approuver et refuser-avec-motif partent du bon appel, et **rien ne
 *    navigue** ;
 * ② **la vue reste** — le panneau se referme et la lecture ouverte est toujours
 *    celle qu'on lisait ; la tâche qui attendait repart, c'est-à-dire que sa
 *    carte se démonte quand la demande quitte la file ;
 * ③ **aucun geste n'abandonne** — plus un seul lien nu vers `/validations`
 *    depuis la page d'un run ; celui qui y mène encore (la file entière et
 *    l'historique) emmène son retour, et l'écran d'arrivée le rend ;
 * ④ **une carte, toutes les surfaces** — la cloche monte la même, donc elle
 *    montre enfin l'acte et sait motiver un refus.
 *
 * Aucun réseau : `useControlTower` est mocké par `tests/setup.ts`.
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CentreNotifications } from "@/components/CentreNotifications";
import { EcranValidations } from "@/components/EcranValidations";
import { VueRun } from "@/components/runs/VueRun";
import { FournisseurEtatGlobal } from "@/lib/etatGlobal";
import { cheminDeRetour, hrefAvecRetour } from "@/lib/navigation";
import {
  EXECUTION_EN_ATTENTE_ARBITRAGE,
  type GrapheRun,
  type Tache,
} from "@/lib/types";
import { arbitragesEnAttente, validationsDuRun } from "@/lib/validations";
import {
  VUE_KANBAN,
  VUE_PIPELINE,
  vueRunOuDefaut,
  type VueRunCle,
} from "@/lib/vuesRun";

import {
  grapheFactice,
  noeudGrapheFactice,
  poserEtatGlobal,
  projetFactice,
  rendreAvecEtat,
  runFactice,
  tacheFactice,
  validationFactice,
} from "./aides";

const RUN = "run-1228";
const PROJET = projetFactice({ id: "prj-7f3a1c2b", nom: "Dépensio" });

/**
 * Les tâches d'un run viennent de l'API, jamais d'un filtre local
 * (`lib/useTachesRun`, #473) : ce mock **remplace** celui de `tests/setup.ts`
 * pour les servir, comme le fait `runs-vue.test.tsx`.
 */
const lecture = vi.hoisted(() => ({
  taches: [] as Tache[],
  graphe: null as GrapheRun | null,
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...reel,
    chargerProjets: async () => [],
    chargerTaches: async () => lecture.taches,
    chargerGrapheExecution: async () => lecture.graphe,
  };
});

beforeEach(() => {
  lecture.taches = [
    tacheFactice({ id: "deploiement", titre: "Déployer l'API", run_id: RUN }),
  ];
  // Un graphe d'un seul nœud : le pipeline a besoin d'une boîte à dessiner, et
  // c'est la même tâche que le Kanban montre en carte.
  lecture.graphe = grapheFactice({
    run_id: RUN,
    noeuds: [
      noeudGrapheFactice({
        id: "deploiement",
        titre: "Déployer l'API",
        statut: "en_cours",
        compartiment: "en_cours",
      }),
    ],
  });
});

/** La demande qui dort sur la tâche de ce run — avec son acte (#581). */
function demande(partiel = {}) {
  return validationFactice({
    tache_id: "deploiement",
    titre: "Déployer l'API",
    outil: "Bash",
    arguments: { commande: "kubectl apply -f prod.yaml" },
    run_id: RUN,
    ...partiel,
  });
}

/** Le run arrêté sur cet arbitrage, et la tâche qui le porte. */
function etatDuRun(decider = vi.fn(async () => {})) {
  return {
    executions: [
      runFactice({
        run_id: RUN,
        objectif: "Livrer la facturation",
        statut: EXECUTION_EN_ATTENTE_ARBITRAGE,
      }),
    ],
    taches: [
      tacheFactice({ id: "deploiement", titre: "Déployer l'API", run_id: RUN }),
    ],
    validations: [demande()],
    decider,
  };
}

const monter = (partiel = {}, vueCible: VueRunCle = VUE_PIPELINE) =>
  rendreAvecEtat(
    <VueRun runId={RUN} vueCible={vueCible} />,
    { ...etatDuRun(), ...partiel },
    PROJET,
  );

/** La tête du run — la carte qui porte son titre, son badge et ses gestes. */
const tete = () => screen.getByRole("region", { name: "Run" });

/** Le Kanban du run, une fois ses tâches lues — sinon on tient son vide. */
async function kanbanCharge() {
  await screen.findAllByText("Déployer l'API");
  return screen.getByRole("region", { name: "Tâches (Kanban)" });
}

// ------------------------------------------------- ① On tranche là où l'on est

describe("la tête d'un run", () => {
  it("porte la demande entière, décidable sans bouger", async () => {
    const decider = vi.fn(async () => {});
    monter({ decider });

    // L'acte, et non le titre de la tâche : c'est ce qu'on approuve (#573).
    expect(screen.getByText("Bash")).toBeTruthy();
    expect(screen.getByText("kubectl apply -f prod.yaml")).toBeTruthy();

    await userEvent.click(
      within(tete()).getByRole("button", { name: "Approuver" }),
    );
    expect(decider).toHaveBeenCalledWith("deploiement", true);
  });

  it("refuse avec une raison, et la raison part avec le refus", async () => {
    const decider = vi.fn(async () => {});
    monter({ decider });

    const entete = tete();
    await userEvent.click(
      within(entete).getByRole("button", { name: "Motiver le refus" }),
    );
    await userEvent.type(
      within(entete).getByLabelText(/Motif du refus/),
      "Pas en production un vendredi",
    );
    await userEvent.click(within(entete).getByRole("button", { name: "Refuser" }));

    expect(decider).toHaveBeenCalledWith(
      "deploiement",
      false,
      "Pas en production un vendredi",
    );
  });

  it("ne propose plus de partir trancher ailleurs", () => {
    monter();

    // Le « Trancher → » de la table `ATTENTES` : la tête porte le geste, elle
    // n'achemine plus (`LigneAttente surPlace`). La phrase, elle, reste.
    expect(
      within(tete()).queryByRole("link", { name: /Trancher/ }),
    ).toBeNull();
    expect(
      screen.getByText(/Une tâche attend un arbitrage humain/),
    ).toBeTruthy();
  });

  it("ne montre rien quand ce run n'attend personne", () => {
    monter({ validations: [demande({ run_id: "un-autre-run", tache_id: "x" })] });

    expect(
      within(tete()).queryByRole("button", { name: "Approuver" }),
    ).toBeNull();
  });
});

describe("les lectures denses d'un run", () => {
  it("ouvrent la demande sur place depuis le pipeline, sans navigation", async () => {
    const decider = vi.fn(async () => {});
    monter({ decider }, VUE_PIPELINE);

    await screen.findByText("Déployer l'API");
    const pipeline = screen.getByRole("region", { name: "Pipeline du run" });
    await userEvent.click(
      within(pipeline).getByRole("button", { name: "Trancher" }),
    );

    const panneau = screen.getByRole("dialog", { name: /Trancher la demande/ });
    // Le graphe est toujours là derrière : on n'a pas changé d'écran.
    expect(screen.getByRole("region", { name: "Pipeline du run" })).toBeTruthy();

    await userEvent.click(
      within(panneau).getByRole("button", { name: "Approuver" }),
    );
    expect(decider).toHaveBeenCalledWith("deploiement", true);
  });

  it("ouvrent la demande sur place depuis le Kanban, sans navigation", async () => {
    const decider = vi.fn(async () => {});
    monter({ decider }, VUE_KANBAN);

    const tableau = await kanbanCharge();
    await userEvent.click(
      within(tableau).getByRole("button", { name: "Trancher" }),
    );

    // Un dialogue, pas une page : la vue du run est toujours montée derrière.
    const panneau = screen.getByRole("dialog", { name: /Trancher la demande/ });
    expect(screen.getByRole("region", { name: "Tâches (Kanban)" })).toBeTruthy();

    await userEvent.click(
      within(panneau).getByRole("button", { name: "Approuver" }),
    );
    expect(decider).toHaveBeenCalledWith("deploiement", true);
  });

  it("referment le panneau sur Échap, en laissant la lecture ouverte", async () => {
    monter({}, VUE_KANBAN);

    const tableau = await kanbanCharge();
    await userEvent.click(
      within(tableau).getByRole("button", { name: "Trancher" }),
    );
    expect(screen.getByRole("dialog", { name: /Trancher la demande/ })).toBeTruthy();

    await userEvent.keyboard("{Escape}");
    await waitFor(() =>
      expect(
        screen.queryByRole("dialog", { name: /Trancher la demande/ }),
      ).toBeNull(),
    );
    expect(screen.getByRole("region", { name: "Tâches (Kanban)" })).toBeTruthy();
  });

  it("n'ouvrent pas le détail de la tâche par-dessus le panneau", async () => {
    // La carte du Kanban ouvre son détail au clic ; le panneau est monté dedans.
    // Sans l'exception `[role='dialog']`, un clic dans le panneau ferait
    // apparaître un second dialogue par-dessus la demande qu'on est en train de
    // lire.
    monter({}, VUE_KANBAN);

    const tableau = await kanbanCharge();
    await userEvent.click(
      within(tableau).getByRole("button", { name: "Trancher" }),
    );
    await userEvent.click(
      within(screen.getByRole("dialog", { name: /Trancher la demande/ })).getByText(
        "Bash",
      ),
    );

    expect(screen.getAllByRole("dialog")).toHaveLength(1);
  });

  it("la tâche qui attendait repart : la demande tranchée emporte le geste", async () => {
    // Le panneau ne se referme par aucun code : il se **démonte** avec sa
    // demande, dès que la file rafraîchie ne la porte plus. C'est ce qui fait
    // que « la tâche qui attendait repart sous les yeux ».
    const { rerender } = monter({}, VUE_KANBAN);
    const tableau = await kanbanCharge();
    await userEvent.click(
      within(tableau).getByRole("button", { name: "Trancher" }),
    );
    expect(screen.getByRole("dialog", { name: /Trancher la demande/ })).toBeTruthy();

    // Le `rerender` de Testing Library remplace **tout** l'arbre, fournisseur
    // compris (`tests/aides`) : on le remonte, sur une file vidée.
    poserEtatGlobal({ ...etatDuRun(), validations: [] });
    rerender(
      <FournisseurEtatGlobal projet={PROJET}>
        <VueRun runId={RUN} vueCible={VUE_KANBAN} />
      </FournisseurEtatGlobal>,
    );

    await waitFor(() =>
      expect(
        screen.queryByRole("dialog", { name: /Trancher la demande/ }),
      ).toBeNull(),
    );
    expect(screen.queryByRole("button", { name: "Trancher" })).toBeNull();
  });
});

// --------------------------------------- ③ Aucun geste n'abandonne sans retour

describe("le chemin de retour", () => {
  it("accompagne le seul lien qui mène encore aux validations", () => {
    monter({}, VUE_KANBAN);

    const lien = screen.getByRole("link", {
      name: /Toute la file et l'historique/,
    });
    // Le run **et** la lecture ouverte : sans la seconde moitié, revenir
    // rouvrirait le pipeline à qui lisait le Kanban.
    expect(lien.getAttribute("href")).toBe(
      `/validations?retour=${encodeURIComponent(`/runs/${RUN}?vue=${VUE_KANBAN}`)}`,
    );
  });

  it("aucun lien nu vers /validations ne subsiste sur la page d'un run", () => {
    monter({}, VUE_KANBAN);

    const nus = screen
      .getAllByRole("link")
      .filter((lien) => lien.getAttribute("href") === "/validations");
    expect(nus).toHaveLength(0);
  });

  it("se rend sur l'écran d'arrivée, et ramène là d'où l'on vient", () => {
    rendreAvecEtat(
      <EcranValidations retour={`/runs/${RUN}?vue=${VUE_PIPELINE}`} />,
      etatDuRun(),
      PROJET,
    );

    const retour = screen.getByRole("link", { name: /Revenir à « Runs »/ });
    expect(retour.getAttribute("href")).toBe(`/runs/${RUN}?vue=${VUE_PIPELINE}`);
  });

  it("ne se rend pas quand on est arrivé par le menu", () => {
    rendreAvecEtat(<EcranValidations />, etatDuRun(), PROJET);
    expect(screen.queryByRole("link", { name: /^Revenir/ })).toBeNull();
  });

  it("rouvre la lecture que le retour nomme", () => {
    monter({}, vueRunOuDefaut("kanban"));
    expect(screen.getByRole("region", { name: "Tâches (Kanban)" })).toBeTruthy();
  });
});

describe("le paramètre de retour, lu comme une entrée non maîtrisée", () => {
  it("accepte un chemin interne", () => {
    expect(cheminDeRetour("/runs/abc?vue=kanban")).toBe("/runs/abc?vue=kanban");
    expect(cheminDeRetour(["/runs/abc"])).toBe("/runs/abc");
  });

  it("refuse en silence ce qui mène hors du site", () => {
    // Un paramètre d'URL est la seule entrée de l'interface que personne du
    // produit n'a écrite : un lien de retour qui suivrait ce qu'on y met ferait
    // de chaque écran une rampe de lancement vers ailleurs.
    expect(cheminDeRetour("//ailleurs.example/piege")).toBeUndefined();
    expect(cheminDeRetour("https://ailleurs.example")).toBeUndefined();
    expect(cheminDeRetour("runs/abc")).toBeUndefined();
    expect(cheminDeRetour(undefined)).toBeUndefined();
  });

  it("encode le retour, qui porte lui-même une requête", () => {
    expect(hrefAvecRetour("/validations", "/runs/r1?vue=frise")).toBe(
      "/validations?retour=%2Fruns%2Fr1%3Fvue%3Dfrise",
    );
    // Une page qui porte déjà une requête n'en ouvre pas une seconde.
    expect(hrefAvecRetour("/validations?x=1", "/runs/r1")).toBe(
      "/validations?x=1&retour=%2Fruns%2Fr1",
    );
  });

  it("retombe sur le pipeline quand la lecture nommée n'existe pas", () => {
    expect(vueRunOuDefaut("kanban")).toBe(VUE_KANBAN);
    expect(vueRunOuDefaut("nimportequoi")).toBe(VUE_PIPELINE);
    expect(vueRunOuDefaut(undefined)).toBe(VUE_PIPELINE);
  });
});

// ------------------------------------------ ④ Une carte, toutes les surfaces

describe("la cloche monte la carte du produit", () => {
  it("montre l'acte et sait motiver un refus, ce qu'elle ne faisait pas", async () => {
    const decider = vi.fn(async () => {});
    rendreAvecEtat(<CentreNotifications />, etatDuRun(decider), PROJET);

    await userEvent.click(screen.getByRole("button", { name: /Notifications/ }));
    const panneau = screen.getByRole("dialog", { name: "Notifications" });

    // L'acte en tête — la recopie d'avant #1228 affichait « Déployer l'API »
    // au-dessus d'un appel de `Bash` sans jamais le nommer (#573).
    expect(within(panneau).getByText("Bash")).toBeTruthy();

    await userEvent.click(
      within(panneau).getByRole("button", { name: "Motiver le refus" }),
    );
    await userEvent.type(
      within(panneau).getByLabelText(/Motif du refus/),
      "Trop tôt",
    );
    await userEvent.click(
      within(panneau).getByRole("button", { name: "Refuser" }),
    );

    expect(decider).toHaveBeenCalledWith("deploiement", false, "Trop tôt");
  });
});

// --------------------------------------------- La règle, éprouvée hors rendu

describe("la file, lue par tâche et par run", () => {
  it("indexe les demandes en attente par tâche, la plus ancienne gagnant", () => {
    const vieille = demande({ horodatage: "2026-09-22T08:00:00Z", titre: "A" });
    const recente = demande({ horodatage: "2026-09-22T09:00:00Z", titre: "B" });
    const tranchee = demande({ tache_id: "autre", statut: "approuve" });

    const par_tache = arbitragesEnAttente([recente, vieille, tranchee]);
    expect(par_tache.get("deploiement")?.titre).toBe("A");
    expect(par_tache.has("autre")).toBe(false);
  });

  it("rattache une demande à son run par son champ, la tâche restant le filet", () => {
    // La demande porte son run depuis #570, et c'est la source : elle est
    // publiée **avant** que sa tâche n'existe pour qui que ce soit.
    const portee = demande({ run_id: RUN });
    const ancienne = demande({ tache_id: "vieille-tache", run_id: "" });
    const taches = [
      tacheFactice({ id: "vieille-tache", run_id: RUN }),
      tacheFactice({ id: "hors-run", run_id: "autre" }),
    ];

    const du_run = validationsDuRun([portee, ancienne], taches, RUN);
    expect(du_run.map((v) => v.tache_id).sort()).toEqual([
      "deploiement",
      "vieille-tache",
    ]);
    expect(validationsDuRun([portee], taches, "autre")).toHaveLength(0);
  });
});

/**
 * La **vue pipeline** d'un run (#491, lot 3 de #488 ; couverte ici par #492,
 * docs/05 §2.4.4).
 *
 * `runs-vue.test.tsx` en garde quatre traits depuis le lot 3 — un par critère —,
 * assez pour qu'une régression de rendu ne passe pas inaperçue en attendant ce
 * lot-ci. Ce fichier est la couverture différée, et il porte les quatre faits que
 * le ticket demande : **le nœud en cours**, **l'étape qui se coche**, **l'arête
 * qui s'allume**, **l'attente humaine distinguée**.
 *
 * Trois étages, du plus pur au plus rendu, parce qu'ils ne se gardent pas de la
 * même façon :
 *
 * ① **Les règles, hors JSX** (`lib/graphe`, `lib/vuesRun`). Le backend sert tout
 *    ce qui se dessine (#490) : ce module ne porte que les trois questions que le
 *    contrat ne pose pas — « ce nœud attend-il un humain ? », « vient-il
 *    d'être débloqué ? », « qu'est-ce que la branche courante ? ». Elles se
 *    testent sans rendu, et l'**ordre** dans lequel elles sont posées *est* la
 *    décision.
 * ② **La checklist rendue** (`components/EtapesTache`) — une case par étape et
 *    jamais un pourcentage : c'est le critère de #489, et la seule brique que le
 *    panneau de détail et le nœud de graphe partagent.
 * ③ **La vue montée** (`components/runs/VuePipeline` dans `VueRun`) — ce qu'on
 *    lit vraiment à l'écran, y compris ce qui bouge quand le run avance.
 *
 * ⚠ **jsdom ne calcule aucune géométrie** : les rectangles y sont tous nuls, donc
 * `Arete` s'abstient de tracer et aucune courbe n'est vérifiable ici. C'est
 * voulu — la géométrie se mesure au skill `/banc-mise-en-page` (#306/#308). Ce
 * qu'on garde de l'arête est donc son **état** : la liste en toutes lettres, que
 * le dessin double à dessein pour qui ne voit pas les courbes.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AvancementEtapes, LigneEtape } from "@/components/EtapesTache";
import { VueRun } from "@/components/runs/VueRun";
import type { EtapeAffichee } from "@/lib/detailTache";
import { FournisseurEtatGlobal } from "@/lib/etatGlobal";
import {
  amorcesDeBranche,
  aretesEntrantes,
  brancheCourante,
  comptesEtapes,
  etapeCourante,
  etatDuNoeud,
  etatsDesNoeuds,
  niveauxRetenus,
  NOEUD_A_FAIRE,
  NOEUD_ATTENTE_HUMAIN,
  NOEUD_AUTRE,
  NOEUD_BLOQUE,
  NOEUD_ECHEC,
  NOEUD_EN_COURS,
  NOEUD_PRET,
  NOEUD_TERMINE,
  STATUT_EN_ATTENTE_VALIDATION,
} from "@/lib/graphe";
import {
  ARETE_ATTENDUE,
  ARETE_FRANCHIE,
  ARETE_ROMPUE,
  ETAPE_A_FAIRE,
  ETAPE_EN_COURS,
  ETAPE_FAITE,
  type GrapheRun,
  type PageJournal,
  type Tache,
} from "@/lib/types";
import {
  VUES_RUN,
  VUE_JOURNAL,
  VUE_KANBAN,
  VUE_PIPELINE,
  VUE_RUN_DEFAUT,
} from "@/lib/vuesRun";

import {
  grapheFactice,
  noeudGrapheFactice,
  pageJournalCourante,
  poserEtatGlobal,
  projetFactice,
  rendreAvecEtat,
  runFactice,
  tacheFactice,
  validationFactice,
} from "./aides";

const RUN = "3ff0bcb065f9";

/** Ce que les fausses lectures rendront — même dispositif que `runs-vue`. */
const lecture = vi.hoisted(() => ({
  taches: [] as Tache[],
  graphe: null as GrapheRun | null,
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...reel,
    chargerProjets: async () => [],
    chargerJournal: async (): Promise<PageJournal> => pageJournalCourante(),
    chargerTaches: async () => lecture.taches,
    chargerGrapheExecution: async () => lecture.graphe,
  };
});

beforeEach(() => {
  lecture.taches = [];
  lecture.graphe = null;
});

/* ------------------------------------------------------------------ *
 * Fabriques : le graphe de référence de ce fichier
 * ------------------------------------------------------------------ */

/**
 * Le graphe qu'on relit dans presque tous les contrôles : un amont terminé, deux
 * branches simultanées dessous (dont une qui travaille), une convergence.
 *
 *     schema ──▶ api ────┐
 *        └────▶ ui ──────┴──▶ recette
 */
function grapheDeReference(partiel: Record<string, unknown> = {}): GrapheRun {
  return grapheFactice({
    run_id: RUN,
    noeuds: [
      noeudGrapheFactice({
        id: "schema",
        titre: "Schéma SQL",
        niveau: 0,
        dependants: ["api", "ui"],
        statut: "terminee",
        compartiment: "terminees",
        agent: "bdd",
        role: "Base de données",
        cout_usd: 0.02,
        duree_ms: 12_000,
        etapes: [{ libelle: "Lister les entités", etat: ETAPE_FAITE }],
      }),
      noeudGrapheFactice({
        id: "api",
        titre: "API CRUD",
        niveau: 1,
        rang: 0,
        dependances: ["schema"],
        dependants: ["recette"],
        statut: "en_cours",
        compartiment: "en_cours",
        agent: "developpeur",
        role: "Développeur",
        etapes: [
          { libelle: "Écrire les routes", etat: ETAPE_FAITE },
          { libelle: "Sérialiseurs", etat: ETAPE_EN_COURS },
          { libelle: "Tests d'intégration", etat: ETAPE_A_FAIRE },
        ],
      }),
      noeudGrapheFactice({
        id: "ui",
        titre: "UI liste",
        niveau: 1,
        rang: 1,
        dependances: ["schema"],
        dependants: ["recette"],
      }),
      noeudGrapheFactice({
        id: "recette",
        titre: "Recette",
        niveau: 2,
        dependances: ["api", "ui"],
      }),
    ],
    aretes: [
      { de: "schema", vers: "api", etat: ARETE_FRANCHIE },
      { de: "schema", vers: "ui", etat: ARETE_FRANCHIE },
      { de: "api", vers: "recette", etat: ARETE_ATTENDUE },
      { de: "ui", vers: "recette", etat: ARETE_ATTENDUE },
    ],
    ...partiel,
  });
}

const monter = (partiel = {}) =>
  rendreAvecEtat(
    <VueRun runId={RUN} />,
    { executions: [runFactice({ run_id: RUN, objectif: "Prototyper un mini-CRM" })], ...partiel },
    projetFactice({ id: "prj-7f3a1c2b", nom: "Dépensio" }),
  );

/** Le pipeline, une fois sa lecture aboutie — sinon on tient sa coquille. */
async function pipelineCharge() {
  await screen.findByText("Schéma SQL");
  return screen.getByRole("region", { name: "Pipeline du run" });
}

/* ==================================================================== *
 * ① Les règles, hors JSX
 * ==================================================================== */

describe("l'état auquel un nœud se dessine", () => {
  const noeud = (partiel = {}) => noeudGrapheFactice(partiel);

  it("suit le compartiment servi, sans le réinventer", () => {
    // La correspondance est directe et sans arbitrage : le compartiment **est**
    // la couleur, lue dans la table partagée du backend (#473).
    const table: [string, string][] = [
      ["a_faire", NOEUD_A_FAIRE],
      ["en_cours", NOEUD_EN_COURS],
      ["bloquees", NOEUD_BLOQUE],
      ["terminees", NOEUD_TERMINE],
      ["echecs", NOEUD_ECHEC],
      ["autres", NOEUD_AUTRE],
    ];
    for (const [compartiment, attendu] of table) {
      expect(etatDuNoeud(noeud({ compartiment }), false, [])).toBe(attendu);
    }
  });

  it("montre un compartiment qu'il ne connaît pas au lieu de l'escamoter", () => {
    // Un compartiment ajouté au backend n'a pas à faire disparaître une boîte du
    // dessin : elle se rend en « Autre », visible, en attendant sa couleur.
    expect(etatDuNoeud(noeud({ compartiment: "teleporte" }), false, [])).toBe(NOEUD_AUTRE);
  });

  it("fait passer l'attente humaine avant tout le reste", () => {
    // **Le** défaut d'origine du chantier (#355) : un nœud arrêté sur quelqu'un
    // depuis trois heures ne travaille pas, et son compartiment dit pourtant
    // « en cours » — à raison, la tâche est en vol.
    expect(
      etatDuNoeud(noeud({ compartiment: "en_cours" }), true, []),
    ).toBe(NOEUD_ATTENTE_HUMAIN);
  });

  it("lit aussi l'attente sur le statut, le jour où le moteur l'émettra", () => {
    // Le moteur ne l'émet pas encore, mais il est nommé dans le contrat partagé :
    // une vue qui l'ignorerait deviendrait fausse au pire moment.
    expect(
      etatDuNoeud(
        noeud({ statut: STATUT_EN_ATTENTE_VALIDATION, compartiment: "en_cours" }),
        false,
        [],
      ),
    ).toBe(NOEUD_ATTENTE_HUMAIN);
  });

  it("allume ce qui reste à faire quand toutes ses arêtes entrantes sont franchies", () => {
    // « La suite apparaît » ne peut pas vouloir dire qu'une boîte se crée — sur
    // un plan déclaré d'avance, elle était là, grise, depuis le début.
    expect(
      etatDuNoeud(noeud({ compartiment: "a_faire" }), false, [
        { de: "schema", vers: "ui", etat: ARETE_FRANCHIE },
      ]),
    ).toBe(NOEUD_PRET);
  });

  it("n'allume rien tant qu'une seule arête entrante attend", () => {
    expect(
      etatDuNoeud(noeud({ compartiment: "a_faire" }), false, [
        { de: "api", vers: "recette", etat: ARETE_FRANCHIE },
        { de: "ui", vers: "recette", etat: ARETE_ATTENDUE },
      ]),
    ).toBe(NOEUD_A_FAIRE);
  });

  it("n'allume rien derrière une arête rompue", () => {
    expect(
      etatDuNoeud(noeud({ compartiment: "a_faire" }), false, [
        { de: "api", vers: "recette", etat: ARETE_ROMPUE },
      ]),
    ).toBe(NOEUD_A_FAIRE);
  });

  it("ne marque pas « prêt » un nœud qui n'a aucune dépendance", () => {
    // Sur un plan plat, *tout* serait prêt au niveau 0 : le signal ne dirait plus
    // rien, et clignoterait sur l'écran entier.
    expect(etatDuNoeud(noeud({ compartiment: "a_faire" }), false, [])).toBe(NOEUD_A_FAIRE);
  });

  it("ne réveille pas ce qui a déjà vécu", () => {
    // La disponibilité ne se pose que sur ce qui reste à faire : une tâche
    // terminée dont l'amont est franchi reste terminée.
    expect(
      etatDuNoeud(noeud({ compartiment: "terminees" }), false, [
        { de: "schema", vers: "api", etat: ARETE_FRANCHIE },
      ]),
    ).toBe(NOEUD_TERMINE);
  });
});

describe("l'index des arêtes entrantes", () => {
  it("range chaque arête sous son aval", () => {
    // Sans lui, savoir si un nœud est débloqué coûterait un balayage de toutes
    // les arêtes par nœud.
    const index = aretesEntrantes(grapheDeReference());

    expect(index.get("recette")?.map((a) => a.de)).toEqual(["api", "ui"]);
    expect(index.get("schema")).toBeUndefined();
  });

  it("calcule l'état de chaque nœud en une passe", () => {
    const etats = etatsDesNoeuds(grapheDeReference(), new Set(["api"]));

    expect(etats.get("schema")).toBe(NOEUD_TERMINE);
    expect(etats.get("api")).toBe(NOEUD_ATTENTE_HUMAIN);
    expect(etats.get("ui")).toBe(NOEUD_PRET);
    expect(etats.get("recette")).toBe(NOEUD_A_FAIRE);
  });
});

describe("la branche courante", () => {
  it("part de ce qui travaille ou attend quelqu'un", () => {
    const graphe = grapheDeReference();

    expect(amorcesDeBranche(graphe, etatsDesNoeuds(graphe, new Set()))).toEqual(["api"]);
  });

  it("se rabat sur ce qui est sur le point de partir", () => {
    // L'instant qui suit une fin de tâche : rien ne tourne encore, mais la suite
    // est désignée.
    const graphe = grapheDeReference({
      noeuds: grapheDeReference()
        .noeuds.map((noeud) =>
          noeud.id === "api"
            ? { ...noeud, statut: "terminee", compartiment: "terminees" }
            : noeud,
        ),
    });

    expect(amorcesDeBranche(graphe, etatsDesNoeuds(graphe, new Set()))).toEqual(["ui"]);
  });

  it("n'en a aucune sur un run entièrement soldé", () => {
    // Un état normal : le cadrage s'y éteint plutôt que de désigner un nœud au
    // hasard.
    const graphe = grapheDeReference({
      noeuds: grapheDeReference().noeuds.map((noeud) => ({
        ...noeud,
        statut: "terminee",
        compartiment: "terminees",
      })),
    });

    expect(amorcesDeBranche(graphe, etatsDesNoeuds(graphe, new Set()))).toEqual([]);
  });

  it("retient tout ce qui mène à l'amorce et tout ce qui en découle", () => {
    // Les deux moitiés répondent à des questions qu'on se pose ensemble : d'où
    // vient ce qui tourne, et qu'est-ce que ça va déclencher.
    const branche = brancheCourante(grapheDeReference(), ["api"]);

    expect([...branche].sort()).toEqual(["api", "recette", "schema"]);
  });

  it("laisse dehors les branches sœurs — c'est ce qui fait gagner la place", () => {
    expect(brancheCourante(grapheDeReference(), ["api"]).has("ui")).toBe(false);
  });

  it("ne boucle pas sur un plan cyclique relu du bus", () => {
    // Un plan validé n'en porte pas, mais un plan relu du bus ne repasse par
    // aucune validation (#490) : sans le marquage des nœuds vus, le parcours
    // tournerait sans fin.
    const cycle = grapheFactice({
      noeuds: [
        noeudGrapheFactice({ id: "a", dependances: ["b"], dependants: ["b"] }),
        noeudGrapheFactice({ id: "b", dependances: ["a"], dependants: ["a"] }),
      ],
    });

    expect([...brancheCourante(cycle, ["a"])].sort()).toEqual(["a", "b"]);
  });

  it("retire les niveaux devenus vides, sans renuméroter les nœuds", () => {
    // Garder la place d'un niveau dont plus rien n'est retenu rendrait une chaîne
    // de trois tâches avec sept colonnes de trous. Le `niveau` porté par chaque
    // nœud, lui, reste le rang **dans le plan**.
    const graphe = grapheDeReference();
    const retenus = brancheCourante(graphe, ["recette"]);

    expect(niveauxRetenus(graphe, retenus)).toEqual([["schema"], ["api", "ui"], ["recette"]]);
    expect(niveauxRetenus(graphe, new Set(["schema", "recette"]))).toEqual([
      ["schema"],
      ["recette"],
    ]);
  });

  it("rend le graphe entier quand rien n'est cadré", () => {
    const graphe = grapheDeReference();

    expect(niveauxRetenus(graphe, null)).toBe(graphe.niveaux);
  });
});

describe("la checklist d'un nœud", () => {
  const etapes = [
    { libelle: "Écrire les routes", etat: ETAPE_FAITE },
    { libelle: "Sérialiseurs", etat: ETAPE_EN_COURS },
    { libelle: "Tests", etat: ETAPE_A_FAIRE },
  ];

  it("compte ce qui est fait sur ce qui est prévu", () => {
    expect(comptesEtapes(etapes)).toEqual({ faites: 1, total: 3 });
    expect(comptesEtapes([])).toEqual({ faites: 0, total: 0 });
  });

  it("désigne l'étape en cours", () => {
    // Le couple de la lisibilité : la rangée dit *combien*, cette ligne dit *quoi*.
    expect(etapeCourante(etapes)?.libelle).toBe("Sérialiseurs");
  });

  it("se rabat sur la prochaine à faire quand aucune n'est en cours", () => {
    expect(
      etapeCourante([
        { libelle: "Écrire les routes", etat: ETAPE_FAITE },
        { libelle: "Sérialiseurs", etat: ETAPE_A_FAIRE },
      ])?.libelle,
    ).toBe("Sérialiseurs");
  });

  it("ne désigne rien quand tout est coché, ni quand il n'y a rien", () => {
    expect(etapeCourante([{ libelle: "Écrire les routes", etat: ETAPE_FAITE }])).toBeNull();
    expect(etapeCourante([])).toBeNull();
  });
});

describe("l'arbitrage entre les trois lectures d'un run", () => {
  it("ouvre sur le pipeline", () => {
    // La question du Kanban est déjà à moitié répondue au-dessus de lui (la barre
    // de progression compte par compartiment) : ouvrir dessus, c'est ouvrir sur
    // une redondance et faire du pipeline une vue qu'on n'ouvre jamais.
    expect(VUE_RUN_DEFAUT).toBe(VUE_PIPELINE);
  });

  it("propose les trois : le flux, puis l'inventaire, puis le récit", () => {
    // L'ordre *est* la décision. Le journal ferme la rangée (#516) : c'est ce que
    // #478 défendait en le posant sous les tâches — on le consulte après avoir vu
    // où en est le run —, reporté sur la bascule au lieu d'un empilement.
    expect(VUES_RUN.map((onglet) => onglet.cle)).toEqual([
      VUE_PIPELINE,
      VUE_KANBAN,
      VUE_JOURNAL,
    ]);
  });

  it("donne à chaque onglet la question à laquelle il répond", () => {
    // « Pipeline », « Kanban » et « Journal » ne disent pas d'eux-mêmes lequel
    // montre quoi, et c'est précisément la confusion que l'arbitrage devait lever.
    const [pipeline, kanban, journal] = VUES_RUN;
    expect(pipeline.question).toMatch(/Quoi après quoi/);
    expect(kanban.question).toMatch(/Combien dans quel état/);
    expect(journal.question).toMatch(/Qu'a-t-il fait/);
  });
});

/* ==================================================================== *
 * ② La checklist rendue — une case par étape, jamais un pourcentage
 * ==================================================================== */

describe("l'avancement des étapes", () => {
  it("annonce ce qui est fait sur ce qui est prévu, en toutes lettres", () => {
    render(
      <AvancementEtapes
        etapes={[
          { libelle: "Écrire les routes", etat: ETAPE_FAITE },
          { libelle: "Sérialiseurs", etat: ETAPE_EN_COURS },
        ]}
        faites={1}
      />,
    );

    const jauge = screen.getByRole("progressbar", { name: "Avancement des étapes" });
    expect(jauge).toHaveAttribute("aria-valuenow", "1");
    expect(jauge).toHaveAttribute("aria-valuemax", "2");
    expect(jauge).toHaveAttribute("aria-valuetext", "1 étape terminée sur 2");
  });

  it("rend une case par étape, pas une barre proportionnelle", () => {
    // C'est le critère de #489, et il se garde **au dénominateur** : la checklist
    // est complétée par l'agent en cours de route, donc « 3/5 » peut devenir
    // « 3/8 ». Sur une barre, cet ajout se voit comme un recul ; une case par
    // étape retire au dénominateur son pouvoir de rétracter — la rangée
    // s'allonge, ce qui est acquis reste allumé.
    const { rerender } = render(
      <AvancementEtapes
        etapes={[
          { libelle: "A", etat: ETAPE_FAITE },
          { libelle: "B", etat: ETAPE_A_FAIRE },
        ]}
        faites={1}
      />,
    );
    const cases = () =>
      screen.getByRole("progressbar", { name: "Avancement des étapes" }).children;
    expect(cases()).toHaveLength(2);

    rerender(
      <AvancementEtapes
        etapes={[
          { libelle: "A", etat: ETAPE_FAITE },
          { libelle: "B", etat: ETAPE_A_FAIRE },
          { libelle: "C", etat: ETAPE_A_FAIRE },
        ]}
        faites={1}
      />,
    );

    expect(cases()).toHaveLength(3);
    // Le numérateur n'a pas bougé : rien n'a été perdu, et rien ne le prétend.
    expect(
      screen.getByRole("progressbar", { name: "Avancement des étapes" }),
    ).toHaveAttribute("aria-valuenow", "1");
  });

  it("garde la même unité de compte quelle que soit la taille", () => {
    // `compacte` sur un nœud de graphe, `ample` dans un panneau : seule
    // l'épaisseur change, sans quoi les deux écrans ne compteraient pas pareil.
    const etapes: EtapeAffichee[] = [
      { libelle: "A", etat: ETAPE_FAITE },
      { libelle: "B", etat: ETAPE_A_FAIRE },
    ];
    const { rerender } = render(<AvancementEtapes etapes={etapes} faites={1} />);
    const ample = screen.getByRole("progressbar").children.length;

    rerender(<AvancementEtapes etapes={etapes} faites={1} taille="compacte" />);

    expect(screen.getByRole("progressbar").children.length).toBe(ample);
  });
});

describe("une ligne de checklist", () => {
  it("dit son état à voix haute, la case étant décorative", () => {
    // La case n'est pas un `<input>` : l'avancement vient du moteur, il ne se
    // coche pas à la main — un contrôle cliquable promettrait une action qui
    // n'existe pas.
    render(
      <ul>
        <LigneEtape etape={{ libelle: "Écrire les routes", etat: ETAPE_FAITE }} />
      </ul>,
    );

    expect(screen.getByText(/Écrire les routes/)).toHaveTextContent("terminée");
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });
});

/* ==================================================================== *
 * ③ La vue montée
 * ==================================================================== */

describe("le nœud, tel qu'on le lit", () => {
  beforeEach(() => {
    lecture.graphe = grapheDeReference();
  });

  it("porte son titre, son agent et son rôle", async () => {
    monter();

    const pipeline = await pipelineCharge();
    expect(within(pipeline).getByText("API CRUD")).toBeInTheDocument();
    expect(within(pipeline).getByText(/developpeur · Développeur/)).toBeInTheDocument();
  });

  it("dit ce qui travaille en ce moment", async () => {
    monter();

    const pipeline = await pipelineCharge();
    expect(within(pipeline).getAllByText("En cours").length).toBeGreaterThan(0);
  });

  it("porte sa checklist : combien, puis quoi", async () => {
    monter();

    const pipeline = await pipelineCharge();
    expect(within(pipeline).getByText("1/3")).toBeInTheDocument();
    expect(within(pipeline).getByText("Sérialiseurs")).toBeInTheDocument();
  });

  it("porte son coût et sa durée quand ils sont mesurés", async () => {
    monter();

    const pipeline = await pipelineCharge();
    // Inconnu n'est pas zéro : les trois autres nœuds n'affichent rien.
    expect(within(pipeline).getByText("12 s")).toBeInTheDocument();
  });

  it("reste inerte quand il n'y a rien à ouvrir", async () => {
    // Même règle que la carte du Kanban : un nœud dont la tâche n'a pas démarré
    // n'annonce pas un panneau qui serait vide.
    monter();

    await pipelineCharge();
    expect(
      screen.queryByRole("button", { name: /Ouvrir le détail de la tâche/ }),
    ).not.toBeInTheDocument();
  });

  it("ouvre le panneau de détail qui existe déjà, en croisant par identifiant", async () => {
    // Un `NoeudGraphe` porte de quoi le dessiner mais pas de quoi le détailler :
    // ni description, ni liens, ni ticket. Croiser avec la tâche évite d'écrire
    // un second panneau.
    lecture.taches = [
      tacheFactice({ id: "api", titre: "API CRUD", description: "Exposer le CRUD." }),
    ];
    monter();

    await pipelineCharge();
    await userEvent.click(
      await screen.findByRole("button", { name: "Ouvrir le détail de la tâche API CRUD" }),
    );

    expect(await screen.findByRole("dialog")).toHaveTextContent("Exposer le CRUD.");
  });
});

describe("les branches parallèles", () => {
  it("rangent au même niveau deux tâches sans dépendance entre elles", async () => {
    // Le deuxième critère du chantier : une file les aurait mises l'une derrière
    // l'autre — vrai comme séquence, faux comme dessin.
    lecture.graphe = grapheDeReference();
    monter();

    await pipelineCharge();
    const niveau = screen.getByRole("list", { name: "Niveau 2" });
    expect(within(niveau).getByText("API CRUD")).toBeInTheDocument();
    expect(within(niveau).getByText("UI liste")).toBeInTheDocument();
  });

  it("annoncent la forme du graphe avant qu'on l'ait parcouru", async () => {
    lecture.graphe = grapheDeReference();
    monter();

    const pipeline = await pipelineCharge();
    // « jusqu'à N de front » et non « N en parallèle » : la largeur dit ce que le
    // plan **autorise**, jamais ce que le run fera.
    expect(within(pipeline).getByText(/4 tâches · 4 enchaînements · 3 niveaux/)).toBeInTheDocument();
    expect(within(pipeline).getByText(/jusqu'à 2 de front/)).toBeInTheDocument();
  });
});

describe("l'arête qui s'allume", () => {
  it("fait sortir du retrait la suite dont l'amont a rendu la main", async () => {
    lecture.graphe = grapheDeReference();
    monter();

    const pipeline = await pipelineCharge();
    // « UI liste » n'a pas démarré, mais sa seule dépendance est franchie. Le
    // badge apparaît deux fois — sur la boîte et dans la légende, qui ne liste
    // que les états réellement présents.
    expect(within(pipeline).getAllByText("Prête à partir")).toHaveLength(2);
  });

  it("rend les enchaînements en toutes lettres, le tracé n'ayant rien à annoncer", async () => {
    // Le `<svg>` est `aria-hidden` : sans cette liste, l'état d'une arête ne
    // serait lisible que par ceux qui voient les courbes.
    lecture.graphe = grapheDeReference();
    monter();

    const pipeline = await pipelineCharge();
    await userEvent.click(
      within(pipeline).getByText("Les 4 enchaînements en toutes lettres"),
    );

    expect(
      within(pipeline).getByText("Schéma SQL → API CRUD — franchie"),
    ).toBeInTheDocument();
    expect(
      within(pipeline).getByText("API CRUD → Recette — en attente"),
    ).toBeInTheDocument();
  });

  it("dit qu'un relais est rompu plutôt que de le laisser en attente", async () => {
    // L'aval ne démarrera pas et se bloquera à son tour (#43) : le confondre avec
    // « en attente » ferait espérer une suite qui ne viendra pas.
    lecture.graphe = grapheDeReference({
      aretes: [{ de: "schema", vers: "api", etat: ARETE_ROMPUE }],
    });
    monter();

    const pipeline = await pipelineCharge();
    await userEvent.click(within(pipeline).getByText("Les 1 enchaînement en toutes lettres"));

    expect(within(pipeline).getByText("Schéma SQL → API CRUD — rompue")).toBeInTheDocument();
  });
});

describe("l'étape qui se coche, sous les yeux", () => {
  it("suit le graphe relu au battement suivant, sans rechargement", async () => {
    // Le graphe n'a pas d'événement à lui : il se recompose à la lecture, et
    // c'est le **pouls** du shell qui déclenche la relecture (`useGrapheRun`).
    lecture.graphe = grapheDeReference();
    const projet = projetFactice({ id: "prj-7f3a1c2b", nom: "Dépensio" });
    const { rerender } = monter();
    const pipeline = await pipelineCharge();
    expect(within(pipeline).getByText("1/3")).toBeInTheDocument();

    lecture.graphe = grapheDeReference({
      noeuds: grapheDeReference().noeuds.map((noeud) =>
        noeud.id === "api"
          ? {
              ...noeud,
              etapes: [
                { libelle: "Écrire les routes", etat: ETAPE_FAITE },
                { libelle: "Sérialiseurs", etat: ETAPE_FAITE },
                { libelle: "Tests d'intégration", etat: ETAPE_EN_COURS },
              ],
            }
          : noeud,
      ),
    });
    // Le pouls du shell avance — un compteur, et non « le tableau a changé
    // d'identité » : la seconde formule marcherait aujourd'hui et cesserait sans
    // bruit le jour où un rechargement comparerait avant de poser son état.
    poserEtatGlobal({ executions: [runFactice({ run_id: RUN })], revision: 1 });
    rerender(
      <FournisseurEtatGlobal projet={projet}>
        <VueRun runId={RUN} />
      </FournisseurEtatGlobal>,
    );

    await waitFor(() =>
      expect(screen.getByRole("region", { name: "Pipeline du run" })).toHaveTextContent("2/3"),
    );
    expect(
      within(screen.getByRole("region", { name: "Pipeline du run" })).getByText(
        "Tests d'intégration",
      ),
    ).toBeInTheDocument();
  });
});

describe("ce qui attend un humain", () => {
  it("ne se lit plus « en cours », alors que c'est bien son compartiment", async () => {
    // Le troisième critère, et le défaut d'origine du chantier : le 2026-08-14,
    // une attente de décision est restée 53 minutes indiscernable d'un travail en
    // cours (#355).
    lecture.graphe = grapheDeReference();
    monter({ validations: [validationFactice({ tache_id: "api", statut: "en_attente" })] });

    const pipeline = await pipelineCharge();
    expect(within(pipeline).getAllByText("Attente humaine").length).toBeGreaterThan(0);
    expect(within(pipeline).queryByText("En cours")).not.toBeInTheDocument();
  });

  it("mène à l'écran qui porte le geste, plutôt que de le proposer sur place", async () => {
    // Même règle que la table `ATTENTES` de la liste des runs : un arbitrage se
    // tranche sur l'écran qui montre de quoi trancher, pas dans une boîte de 16 rem.
    lecture.graphe = grapheDeReference();
    monter({ validations: [validationFactice({ tache_id: "api", statut: "en_attente" })] });

    const pipeline = await pipelineCharge();
    expect(within(pipeline).getByRole("link", { name: /Trancher/ })).toHaveAttribute(
      "href",
      "/validations",
    );
  });

  it("ne colore rien quand la validation a déjà été tranchée", async () => {
    lecture.graphe = grapheDeReference();
    monter({ validations: [validationFactice({ tache_id: "api", statut: "approuve" })] });

    const pipeline = await pipelineCharge();
    expect(within(pipeline).queryByText("Attente humaine")).not.toBeInTheDocument();
  });
});

describe("le cadrage sur la branche courante", () => {
  it("retire les branches sœurs de l'écran", async () => {
    // La réponse au « un graphe ne se lit pas s'il déborde » du ticket, et elle
    // ne consiste pas à tout montrer plus petit.
    lecture.graphe = grapheDeReference();
    monter();

    const pipeline = await pipelineCharge();
    await userEvent.click(within(pipeline).getByRole("button", { name: /Branche courante/ }));

    expect(within(pipeline).getByText("API CRUD")).toBeInTheDocument();
    expect(within(pipeline).queryByText("UI liste")).not.toBeInTheDocument();
  });

  it("s'éteint quand il n'y a pas de branche à suivre", async () => {
    // Un run entièrement soldé : désigner un nœud au hasard vaudrait moins que de
    // tout montrer.
    lecture.graphe = grapheDeReference({
      noeuds: grapheDeReference().noeuds.map((noeud) => ({
        ...noeud,
        statut: "terminee",
        compartiment: "terminees",
      })),
    });
    monter();

    const pipeline = await pipelineCharge();
    expect(within(pipeline).getByRole("button", { name: /Branche courante/ })).toBeDisabled();
  });

  it("borne la légende à ce que le cadrage laisse voir", async () => {
    // Lister un état dont on vient soi-même de masquer les boîtes ferait chercher
    // ce qui n'est plus là.
    lecture.graphe = grapheDeReference();
    monter();

    const pipeline = await pipelineCharge();
    await userEvent.click(within(pipeline).getByRole("button", { name: /Branche courante/ }));

    expect(within(pipeline).queryByText("Prête à partir")).not.toBeInTheDocument();
  });
});

describe("les deux notes de lecture, jamais confondues", () => {
  it("dit qu'un plan plat est un graphe, pas un vide", async () => {
    lecture.graphe = grapheFactice({
      run_id: RUN,
      noeuds: [
        noeudGrapheFactice({ id: "schema", titre: "Schéma SQL" }),
        noeudGrapheFactice({ id: "ui", titre: "UI liste" }),
      ],
    });
    monter();

    const pipeline = await pipelineCharge();
    expect(
      within(pipeline).getByText(/Aucune dépendance déclarée/),
    ).toBeInTheDocument();
  });

  it("dit qu'un plan inconnu n'est pas un plan sans dépendance", async () => {
    // Les deux se dessinent pareil ; ce qu'on a le droit d'en conclure ne l'est
    // pas. La première recouvre la seconde, donc elle l'emporte.
    lecture.graphe = grapheFactice({
      run_id: RUN,
      plan_connu: false,
      noeuds: [noeudGrapheFactice({ id: "schema", titre: "Schéma SQL" })],
    });
    monter();

    const pipeline = await pipelineCharge();
    expect(
      within(pipeline).getByText(/n'a pas publié son plan/),
    ).toBeInTheDocument();
    expect(
      within(pipeline).queryByText(/Aucune dépendance déclarée/),
    ).not.toBeInTheDocument();
  });
});

describe("quand le graphe n'a rien à montrer", () => {
  it("nomme le vide sans désigner l'écran d'à côté", async () => {
    lecture.graphe = grapheFactice({ run_id: RUN });
    monter();

    expect(
      await screen.findByText(/cette vue se remplira dès qu'il publiera/),
    ).toBeInTheDocument();
  });

  it("dit son vide sans nommer aucune des deux vues", async () => {
    // La phrase est partagée avec le Kanban (#491) : un pipeline vide qui
    // promettrait de remplir « le tableau » désignerait l'écran d'à côté.
    lecture.graphe = null;
    monter();

    expect(
      await screen.findByText(/cette vue se remplira dès qu'il publiera/),
    ).toBeInTheDocument();
  });
});

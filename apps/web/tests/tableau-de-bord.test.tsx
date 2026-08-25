/**
 * Le tableau de bord épuré et les renvois qui le prolongent (#193, lot 4/4 de
 * la navigation v2 #189 — tests différés des lots #191 et #192).
 *
 * Le lot #191 a retiré trois panneaux de plein format du tableau de bord. La
 * promesse qui va avec — **rien n'est supprimé du produit, tout est rangé** —
 * est la seule chose qu'un test peut tenir : la disparition d'un panneau se
 * voit à l'œil nu, l'absence du renvoi qui le remplace ne se voit pas. D'où
 * deux angles ici : **ce qui reste** sur l'écran d'accueil, et **ce qui renvoie
 * ailleurs**, en passant par le menu (`entreeParLibelle`) plutôt que par un
 * chemin en dur — un renvoi doit suivre de lui-même une page qui déménage, et ne
 * pas s'allumer vers une page qui n'existe pas encore.
 *
 * S'y ajoute le lien vers le ticket externe (#192) là où il n'était pas encore
 * couvert : `ticket-externe.test.tsx` a gardé le filtrage d'URL et les cartes du
 * Kanban (logique critique, livrée avec le lot) ; les **tables de coûts** — le
 * grand livre et la vue par tâche — attendaient ce lot-ci.
 */

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import PageCouts from "@/app/couts/page";
import TableauDeBord from "@/app/page";
import { FilActivite } from "@/components/FilActivite";
import { IndicateursTableauDeBord } from "@/components/IndicateursTableauDeBord";
import { PanneauCouts } from "@/components/PanneauCouts";
import { entreeParLibelle, MENU } from "@/lib/navigation";

import {
  agentFactice,
  coutExecutionFactice,
  coutTacheAgregeeFactice,
  coutTacheFactice,
  evenementFactice,
  rendreAvecEtat,
  tacheFactice,
  usageFactice,
  validationFactice,
} from "./aides";

// La page Coûts agrège par le REST (`GET /api/analytics/couts`) : le réseau
// reste débranché, la vue est celle que le test pose.
const analytics = vi.hoisted(() => ({
  vue: null as unknown,
}));
vi.mock("@/lib/useAnalyticsCouts", async (original) => ({
  ...(await original<Record<string, unknown>>()),
  useAnalyticsCouts: () => ({
    vue: analytics.vue,
    connecte: true,
    chargement: analytics.vue === null,
    rafraichissement: false,
    erreur: null,
  }),
}));

const referenceFactice = {
  id: "#192",
  url: "https://gitlab.test/maestro/-/issues/192",
};

describe("les indicateurs de tête (IndicateursTableauDeBord)", () => {
  const monter = (props: Partial<Parameters<typeof IndicateursTableauDeBord>[0]> = {}) =>
    render(
      <IndicateursTableauDeBord
        taches={[]}
        agents={[]}
        couts={[]}
        {...props}
      />,
    );

  /** La tuile qui porte ce libellé — les tuiles n'ont pas de rôle à elles. */
  const tuile = (libelle: string) =>
    screen.getByText(libelle).closest("article") as HTMLElement;

  it("tient l'essentiel en quatre tuiles", () => {
    monter();
    const rangee = screen.getByRole("region", { name: "Indicateurs de tête" });
    expect(rangee.querySelectorAll("article")).toHaveLength(4);
    for (const libelle of ["Run en cours", "Tâches", "Agents", "Dépense"]) {
      expect(within(rangee).getByText(libelle)).toBeInTheDocument();
    }
  });

  it("nomme le run quand il n'y en a qu'un", () => {
    monter({ taches: [tacheFactice({ run_id: "run-7", statut: "en_cours" })] });
    expect(tuile("Run en cours")).toHaveTextContent("run-7");
    expect(tuile("Run en cours")).toHaveTextContent(
      "1 tâche(s) encore ouverte(s)",
    );
  });

  it("les compte au-delà d'un seul, sans en privilégier un", () => {
    monter({
      taches: [
        tacheFactice({ id: "T-1", run_id: "run-7", statut: "en_cours" }),
        tacheFactice({ id: "T-2", run_id: "run-8", statut: "assignee" }),
      ],
    });
    expect(tuile("Run en cours")).toHaveTextContent("2 runs");
    expect(tuile("Run en cours")).not.toHaveTextContent("run-7");
  });

  it("ne compte comme « en vol » que les tâches non soldées", () => {
    // Une tâche terminée ou en échec garde son `run_id` : sans ce filtre, un
    // run clos resterait affiché « en cours » indéfiniment.
    monter({
      taches: [
        tacheFactice({ id: "T-1", run_id: "run-7", statut: "terminee" }),
        tacheFactice({ id: "T-2", run_id: "run-7", statut: "echec" }),
      ],
    });
    expect(tuile("Run en cours")).toHaveTextContent("Aucun");
    expect(tuile("Run en cours")).toHaveTextContent(
      "toutes les tâches sont soldées",
    );
  });

  it("distingue « aucune tâche connue » de « toutes soldées »", () => {
    monter();
    expect(tuile("Run en cours")).toHaveTextContent("aucune tâche connue");
  });

  it("détaille les tâches par statut", () => {
    monter({
      taches: [
        tacheFactice({ id: "T-1", statut: "en_cours" }),
        tacheFactice({ id: "T-2", statut: "bloquee" }),
        tacheFactice({ id: "T-3", statut: "echec" }),
      ],
    });
    expect(tuile("Tâches")).toHaveTextContent("3");
    expect(tuile("Tâches")).toHaveTextContent(
      "1 en cours · 1 bloquée(s) · 1 échec(s)",
    );
  });

  it("met le travail sur ce projet et les libres en valeur, le reste en détail", () => {
    // #247 : c'est « combien travaillent, combien sont disponibles » qu'on vient
    // chercher — le total et les désactivés passent derrière.
    // #281 : « combien travaillent » devient « **ici** ». Le parc est celui du
    // poste (`GET /api/agents` n'a pas de portée, docs/05 §2.3), donc seul un
    // décompte dérivé des tâches **du projet** appartient à ce tableau de bord.
    monter({
      agents: [
        agentFactice({
          nom: "dev",
          actif: true,
          statut: "occupe",
          taches_en_cours: ["T-1"],
        }),
        agentFactice({ nom: "qa", actif: true, statut: "libre" }),
        agentFactice({ nom: "ops", actif: false, statut: "libre" }),
      ],
      taches: [tacheFactice({ id: "T-1", agent: "dev", statut: "en_cours" })],
    });
    expect(tuile("Agents")).toHaveTextContent("1 sur ce projet · 1 libre(s)");
    expect(tuile("Agents")).toHaveTextContent(
      "3 agent(s) du poste · 0 occupé(s) ailleurs · 1 désactivé(s)",
    );
  });

  it("renvoie au détail l'agent occupé sur un autre projet", () => {
    // Le cas que le lot rend possible et qu'il faut nommer : un agent au
    // travail, mais pas ici. Le compter en tête ferait mentir la tuile ; le
    // taire ferait croire qu'il est disponible.
    monter({
      agents: [
        agentFactice({
          nom: "dev",
          actif: true,
          statut: "occupe",
          // Une tâche en cours, mais pas une des nôtres : elle n'est pas dans
          // `taches`, qui est déjà filtré sur le projet actif.
          taches_en_cours: ["T-ailleurs"],
        }),
      ],
      taches: [],
    });
    expect(tuile("Agents")).toHaveTextContent("0 sur ce projet · 0 libre(s)");
    expect(tuile("Agents")).toHaveTextContent("1 occupé(s) ailleurs");
  });

  it("somme les grands livres, planification comprise", () => {
    // Et non les coûts rapportés par agent : la part de l'orchestrateur n'est
    // attribuée à personne — et, depuis #281, les coûts par agent valent pour
    // le poste entier là où les grands livres suivent les tâches du projet.
    // C'est la même source que la barre supérieure : les deux s'accordent.
    monter({
      couts: [
        coutExecutionFactice({
          run_id: "run-1",
          total: usageFactice({ cout_usd: 1.5 }),
        }),
        coutExecutionFactice({
          run_id: "run-2",
          total: usageFactice({ cout_usd: 0.25 }),
        }),
      ],
    });
    expect(tuile("Dépense")).toHaveTextContent(/1,75/);
    expect(tuile("Dépense")).toHaveTextContent(
      "2 exécution(s), planification comprise",
    );
  });

  it("distingue « rien de rapporté » de « zéro dollar »", () => {
    monter({ couts: [coutExecutionFactice()] });
    expect(tuile("Dépense")).toHaveTextContent("—");
  });

  it("renvoie vers la page de ce qu'il résume", () => {
    // Le point du lot : chaque tuile qui a remplacé un panneau dit où le détail
    // vit désormais.
    monter();
    expect(
      within(tuile("Agents")).getByRole("link", { name: /Voir les agents/ }),
    ).toHaveAttribute("href", entreeParLibelle("Agents")?.href);
    expect(
      within(tuile("Dépense")).getByRole("link", { name: /Détail par période/ }),
    ).toHaveAttribute("href", entreeParLibelle("Coûts & analytics")?.href);
  });

  it("résout ses renvois par le menu, pas par un chemin en dur", () => {
    // C'est ce qui a fait suivre le renvoi « agents » quand #190 a déplacé la
    // page de `/catalogue` à `/agents`, sans toucher à ce composant.
    monter();
    const lien = screen.getByRole("link", { name: /Voir les agents/ });
    expect(MENU.map((entree) => entree.href)).toContain(
      lien.getAttribute("href"),
    );
  });
});

describe("l'aperçu d'activité (FilActivite)", () => {
  const evenements = (nombre: number) =>
    Array.from({ length: nombre }, (_, i) =>
      evenementFactice({ tache_id: `T-${i}`, titre: `Tâche ${i}` }),
    );

  it("borne l'aperçu à la limite demandée", () => {
    render(<FilActivite evenements={evenements(10)} limite={3} />);
    const fil = screen.getByRole("region", { name: "Activité en direct" });
    expect(within(fil).getAllByRole("listitem")).toHaveLength(3);
  });

  it("rend tout le fil quand aucune limite n'est posée", () => {
    render(<FilActivite evenements={evenements(4)} />);
    const fil = screen.getByRole("region", { name: "Activité en direct" });
    expect(within(fil).getAllByRole("listitem")).toHaveLength(4);
  });

  it("dit ce qu'il masque quand il n'a pas de page où renvoyer", () => {
    render(<FilActivite evenements={evenements(10)} limite={3} />);
    expect(screen.getByText(/\+ 7 événement\(s\) plus anciens/)).toBeInTheDocument();
  });

  it("renvoie vers la page du fil complet dès qu'elle existe", () => {
    render(
      <FilActivite
        evenements={evenements(10)}
        limite={3}
        renvoi={{ href: "/journal", libelle: "Ouvrir le journal" }}
      />,
    );
    expect(
      screen.getByRole("link", { name: /Ouvrir le journal/ }),
    ).toHaveAttribute("href", "/journal");
    // Le renvoi remplace le compte masqué : l'un ou l'autre, jamais les deux.
    expect(screen.queryByText(/plus anciens/)).toBeNull();
  });

  it("reste lisible sans aucun événement", () => {
    render(<FilActivite evenements={[]} limite={6} />);
    expect(screen.getByText(/Aucun événement reçu/)).toBeInTheDocument();
  });
});

describe("le tableau de bord épuré (app/page)", () => {
  const monter = (partiel = {}) =>
    rendreAvecEtat(<TableauDeBord />, {
      taches: [tacheFactice()],
      agents: [agentFactice()],
      ...partiel,
    });

  it("garde l'arbitrage, les indicateurs, l'état des runs et l'aperçu", () => {
    monter({ validations: [validationFactice()] });
    for (const zone of [
      "Validations en attente",
      "Indicateurs de tête",
      "État des runs",
      "Activité en direct",
    ]) {
      expect(screen.getByRole("region", { name: zone })).toBeInTheDocument();
    }
  });

  it("a rendu le Kanban à la vue d'un run (#476, renverse #248)", () => {
    // Le Kanban *était* cet écran et en prenait toute la hauteur. Il n'est pas
    // supprimé, il a changé de portée : les tâches d'un **run** (§2.4.2) au lieu
    // de celles du projet entier. C'est la même promesse que #191 tenait déjà —
    // rien n'est retiré du produit, tout est rangé —, et le renvoi qui la tient
    // est ici l'en-tête de l'état des runs. Le regroupement lui-même (en cours,
    // suspendus, interrompus, soldés du jour) est couvert par le lot 8.
    monter();
    expect(screen.queryByRole("region", { name: "Tâches (Kanban)" })).toBeNull();
    expect(
      screen.getByRole("link", { name: /Tous les runs/ }),
    ).toHaveAttribute("href", entreeParLibelle("Runs")?.href ?? "");
  });

  it("a rangé le grand livre par exécution sur la page Coûts", () => {
    // Il n'a pas disparu : la tuile « Dépense » en porte le total et renvoie
    // vers la page où il vit maintenant.
    monter({
      couts: [
        coutExecutionFactice({ taches: [coutTacheFactice()] }),
      ],
    });
    expect(
      screen.queryByRole("region", { name: "Grand livre par exécution" }),
    ).toBeNull();
    expect(
      screen.getByRole("link", { name: /Détail par période/ }),
    ).toHaveAttribute("href", "/couts");
  });

  it("a rangé les fiches d'agent derrière l'entrée « Agents »", () => {
    // Le panneau d'agents de plein format (statut, capacité, coût par agent) a
    // laissé la place à une tuile qui compte et renvoie.
    monter();
    expect(
      screen.getByRole("link", { name: /Voir les agents/ }),
    ).toHaveAttribute("href", "/agents");
  });

  it("a rangé le fil complet derrière l'entrée « Journal »", () => {
    // Le renvoi dormait dans `FilActivite` depuis #191 ; #249 l'a allumé en
    // ajoutant la page au menu, sans toucher au composant. Le chemin se lit
    // donc dans le menu, pas dans le test : c'est ce qui le fait suivre une
    // page qui déménagerait.
    monter({ evenements: [evenementFactice()] });
    expect(
      screen.getByRole("link", { name: /Ouvrir le journal/ }),
    ).toHaveAttribute("href", entreeParLibelle("Journal")?.href ?? "");
  });

  it("ne borne l'activité qu'à un aperçu", () => {
    monter({
      evenements: Array.from({ length: 12 }, (_, i) =>
        evenementFactice({ tache_id: `T-${i}` }),
      ),
    });
    const fil = screen.getByRole("region", { name: "Activité en direct" });
    expect(within(fil).getAllByRole("listitem").length).toBeLessThan(12);
  });

  it("ne montre rien d'autre que le chargement pendant le premier appel", () => {
    monter({ chargement: true });
    expect(screen.getByText(/Chargement de l'état/)).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "État des runs" })).toBeNull();
  });
});

describe("le ticket externe dans les tables de coûts (#192)", () => {
  it("suit une tâche jusque dans le grand livre", () => {
    render(
      <PanneauCouts
        couts={[
          coutExecutionFactice({
            taches: [coutTacheFactice({ ticket: referenceFactice })],
          }),
        ]}
      />,
    );
    expect(
      screen.getByRole("link", { name: /Ouvrir le ticket externe #192/ }),
    ).toHaveAttribute("href", referenceFactice.url);
  });

  it("laisse le grand livre inchangé sans référence", () => {
    render(
      <PanneauCouts
        couts={[coutExecutionFactice({ taches: [coutTacheFactice()] })]}
      />,
    );
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("n'ouvre jamais une URL de schéma inattendu depuis le grand livre", () => {
    // Même garde que sur les cartes du Kanban : la référence vient du flux.
    render(
      <PanneauCouts
        couts={[
          coutExecutionFactice({
            taches: [
              coutTacheFactice({
                ticket: {
                  id: "MAE-42",
                  url: "javascript:alert(1)",
                },
              }),
            ],
          }),
        ]}
      />,
    );
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.getByText("MAE-42")).toBeInTheDocument();
  });

  it("suit une tâche jusque dans la vue par tâche de la page Coûts", () => {
    analytics.vue = {
      depuis: null,
      pas: "heure",
      projet: null,
      total: usageFactice(),
      executions: [],
      agents: [],
      taches: [coutTacheAgregeeFactice({ ticket: referenceFactice })],
      serie: [],
    };
    rendreAvecEtat(<PageCouts />);
    // Depuis #539 la vue par tâche est la première lecture du bloc « Détail de
    // la période » et non plus un bloc à elle : c'est la règle des trois places
    // appliquée à cet écran (docs/30 §4), pas un déplacement de la table.
    const table = screen.getByRole("region", { name: "Détail de la période" });
    expect(
      within(table).getByRole("link", { name: /Ouvrir le ticket externe #192/ }),
    ).toHaveAttribute("href", referenceFactice.url);
  });

  it("laisse la vue par tâche inchangée sans référence", () => {
    analytics.vue = {
      depuis: null,
      pas: "heure",
      projet: null,
      total: usageFactice(),
      executions: [],
      agents: [],
      taches: [coutTacheAgregeeFactice()],
      serie: [],
    };
    rendreAvecEtat(<PageCouts />);
    const table = screen.getByRole("region", { name: "Détail de la période" });
    expect(within(table).queryByRole("link")).toBeNull();
  });
});

describe("le second niveau de la page Coûts (#539)", () => {
  /**
   * Une période qui porte les deux lectures. C'est le cas qui compte : c'est
   * parce que les deux existent que la page dépassait de deux blocs.
   */
  function periodePeuplee() {
    analytics.vue = {
      depuis: null,
      pas: "heure",
      projet: null,
      total: usageFactice(),
      executions: [
        {
          run_id: "run-7",
          nb_taches: 2,
          debut: "2026-07-28T10:00:00Z",
          fin: "2026-07-28T10:12:00Z",
          usage: usageFactice(),
          projet_id: null,
        },
      ],
      agents: [
        { agent: "dev", role: "Développeur", taches: 1, usage: usageFactice() },
      ],
      taches: [coutTacheAgregeeFactice()],
      serie: [],
    };
  }

  it("ouvre sur la vue par tâche, une seule table à la fois", async () => {
    periodePeuplee();
    rendreAvecEtat(<PageCouts />);
    const bloc = screen.getByRole("region", { name: "Détail de la période" });
    expect(within(bloc).getByText("Par tâche")).toBeInTheDocument();
    // La table par tâche porte une colonne « Statut » que celle par exécution
    // n'a pas, et réciproquement pour « Exécution » : c'est le repère le plus
    // court pour dire laquelle des deux est rendue.
    expect(within(bloc).getByRole("columnheader", { name: "Statut" })).toBeInTheDocument();
    expect(
      within(bloc).queryByRole("columnheader", { name: "Exécution" }),
    ).toBeNull();
  });

  it("bascule sur la vue par exécution sans quitter le bloc", async () => {
    periodePeuplee();
    const utilisateur = userEvent.setup();
    rendreAvecEtat(<PageCouts />);
    const bloc = screen.getByRole("region", { name: "Détail de la période" });

    await utilisateur.click(within(bloc).getByRole("button", { name: "Par exécution" }));

    expect(
      within(bloc).getByRole("columnheader", { name: "Exécution" }),
    ).toBeInTheDocument();
    expect(within(bloc).queryByRole("columnheader", { name: "Statut" })).toBeNull();
    // Rien d'autre n'a bougé : le bloc est toujours là, et c'est bien un second
    // niveau — pas une navigation vers un autre écran.
    expect(
      screen.getByRole("region", { name: "Détail de la période" }),
    ).toBe(bloc);
  });

  it("range la répartition par agent dans la colonne de propriétés", () => {
    // La troisième place (docs/30 §4) : ce n'est pas un bloc de corps qu'on a
    // retiré, c'est une ventilation qu'on a rangée. `sobriete.test.tsx` compte,
    // celui-ci dit **où** elle a atterri.
    periodePeuplee();
    rendreAvecEtat(<PageCouts />);
    const colonne = screen.getByRole("complementary", {
      name: "Propriétés de la période",
    });
    expect(
      within(colonne).getByRole("heading", { name: "Répartition par agent" }),
    ).toBeInTheDocument();
  });

  it("efface le bloc quand la période n'a ni tâche ni exécution", () => {
    analytics.vue = {
      depuis: null,
      pas: "heure",
      projet: null,
      total: usageFactice(),
      executions: [],
      agents: [],
      taches: [],
      serie: [],
    };
    rendreAvecEtat(<PageCouts />);
    // Un bloc à deux onglets vides serait la page à moitié chargée que #281 a
    // précisément voulu éviter : la page dit « rien encore », les chiffres
    // restent, le bloc part.
    expect(
      screen.queryByRole("region", { name: "Détail de la période" }),
    ).toBeNull();
    expect(
      screen.getByRole("region", { name: "Totaux de la période" }),
    ).toBeInTheDocument();
  });
});

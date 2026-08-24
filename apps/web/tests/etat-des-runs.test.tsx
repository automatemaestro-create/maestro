/**
 * L'état des runs, ce qui a pris la place du Kanban au tableau de bord (#476 ;
 * couvert ici par #480, lot 8 de #472).
 *
 * `tests/tableau-de-bord.test.tsx` garde déjà que le Kanban a **quitté** l'écran
 * et que la région « État des runs » y est. Ce fichier couvre ce que la région
 * contient, et une propriété que rien ne gardait :
 *
 * **la table des groupes doit rester exhaustive**. Un régime sans groupe ne
 * dégrade pas l'affichage, il fait **disparaître** ces runs-là du tableau de
 * bord. C'est l'argument qui a fait ajouter « Interrompus » à #476 — le panneau
 * « Runs interrompus » qui précède ne montre que les récupérables (#349) —, et
 * c'est le même qui a fait ajouter « En pause » ici : #477 a créé ce régime
 * **après** le merge de #476, si bien que suspendre un run le retirait de l'écran
 * qui existe pour dire où l'on en est. Le premier test ci-dessous balaie
 * `regimeDuRun` au lieu de nommer les groupes un par un : un régime nouveau ne
 * doit pas pouvoir passer sans qu'un test rougisse.
 *
 * Deux pièges du harnais, à connaître avant d'y toucher :
 *
 * - `useHorloge()` rend un vrai `Date.now()` en jsdom, et **sans horloge personne
 *   n'est du jour** (#250) : un run soldé ne se range dans « Soldés du jour »
 *   qu'à la date **réelle** du poste, d'où `maintenant()` plutôt qu'une date
 *   écrite en dur ;
 * - un groupe ne s'affiche que s'il a au moins un run, ce qui est le
 *   comportement voulu — un écran calme ne montre pas quatre titres vides.
 */

import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { EtatDesRuns } from "@/components/runs/EtatDesRuns";
import {
  REGIME_EN_PAUSE,
  REGIME_INTERROMPU,
  REGIME_SOLDE,
  REGIME_SUSPENDU,
  REGIME_TRAVAILLE,
  regimeDuRun,
  type RegimeRun,
} from "@/lib/execution";
import {
  EXECUTION_EN_ATTENTE_BRIEF,
  EXECUTION_TERMINEE,
  VITALITE_ORPHELIN,
  type ResumeExecution,
  type Tache,
  type Validation,
} from "@/lib/types";

import {
  projetFactice,
  rendreAvecEtat,
  runFactice,
  tacheFactice,
  validationFactice,
} from "./aides";

/** Un instant du jour **réel** — voir l'en-tête : `useHorloge` lit l'horloge du poste. */
function maintenant(): string {
  return new Date().toISOString();
}

/** Le titre de groupe attendu pour chaque régime que `regimeDuRun` sait rendre. */
const TITRE_PAR_REGIME: Record<RegimeRun, string> = {
  [REGIME_TRAVAILLE]: "En cours",
  [REGIME_SUSPENDU]: "Suspendus",
  [REGIME_EN_PAUSE]: "En pause",
  [REGIME_INTERROMPU]: "Interrompus",
  [REGIME_SOLDE]: "Soldés du jour",
};

/** Un run représentatif de chaque régime — de quoi balayer la table entière. */
const RUN_PAR_REGIME: Record<RegimeRun, ResumeExecution> = {
  [REGIME_TRAVAILLE]: runFactice({ run_id: "r-travaille" }),
  [REGIME_SUSPENDU]: runFactice({
    run_id: "r-suspendu",
    statut: EXECUTION_EN_ATTENTE_BRIEF,
  }),
  [REGIME_EN_PAUSE]: runFactice({ run_id: "r-pause", en_pause: true }),
  [REGIME_INTERROMPU]: runFactice({
    run_id: "r-interrompu",
    vitalite: VITALITE_ORPHELIN,
  }),
  [REGIME_SOLDE]: runFactice({
    run_id: "r-solde",
    statut: EXECUTION_TERMINEE,
    fin: maintenant(),
  }),
};

const monter = (
  executions: ResumeExecution[],
  {
    validations = [] as Validation[],
    taches = [] as Tache[],
  }: { validations?: Validation[]; taches?: Tache[] } = {},
) =>
  rendreAvecEtat(
    <EtatDesRuns
      executions={executions}
      validations={validations}
      taches={taches}
      projet={projetFactice({ nom: "Dépensio" })}
    />,
  );

// --------------------------------------------------- L'exhaustivité de la table

describe("chaque régime a son groupe — aucun run ne disparaît de l'écran", () => {
  it.each(Object.keys(TITRE_PAR_REGIME) as RegimeRun[])(
    "affiche un run de régime %s",
    (regime) => {
      const run = RUN_PAR_REGIME[regime];
      // Le double est bien du régime annoncé : sans ce contrôle, un test vert ne
      // prouverait que la cohérence de sa propre fixture.
      expect(regimeDuRun(run)).toBe(regime);

      monter([run]);

      const groupe = screen.getByRole("list", { name: TITRE_PAR_REGIME[regime] });
      expect(within(groupe).getByRole("listitem")).toHaveTextContent(run.run_id);
    },
  );

  it("les rend tous ensemble, dans l'ordre de lecture de l'écran", () => {
    // L'ordre suit l'arbitrage de #349, déjà rendu un cran plus haut sur le même
    // écran : ce qui retient du travail **vivant** passe devant ce qui ne retient
    // plus rien.
    monter(Object.values(RUN_PAR_REGIME));

    // Les listes portent le titre en **nom accessible** plutôt qu'un second
    // repère de page : quatre régions imbriquées encombreraient les points de
    // repère pour un gain nul, là où une liste nommée se retrouve aussi bien.
    const groupes = screen
      .getAllByRole("list")
      .map((liste) => liste.getAttribute("aria-label"));

    expect(groupes).toEqual([
      "En cours",
      "Suspendus",
      "En pause",
      "Interrompus",
      "Soldés du jour",
    ]);
  });
});

// ----------------------------------------------------- Le contenu des groupes

describe("le regroupement", () => {
  it("compte les runs de chaque groupe dans son titre", () => {
    monter([
      runFactice({ run_id: "a" }),
      runFactice({ run_id: "b" }),
      runFactice({ run_id: "c", en_pause: true }),
    ]);

    expect(
      screen.getByRole("heading", { level: 3, name: /En cours/ }),
    ).toHaveTextContent("En cours2");
    expect(
      within(screen.getByRole("list", { name: "En cours" })).getAllByRole("listitem"),
    ).toHaveLength(2);
  });

  it("n'affiche pas les groupes vides — un écran calme ne montre pas des titres à zéro", () => {
    monter([runFactice()]);

    expect(screen.getByRole("list", { name: "En cours" })).toBeInTheDocument();
    expect(
      screen.queryByRole("list", { name: "Interrompus" }),
    ).not.toBeInTheDocument();
  });

  it("garde l'ordre du backend à l'intérieur d'un groupe", () => {
    // `GET /api/executions` rend ses résumés récents d'abord : retrier ici
    // poserait une seconde règle d'ordre à tenir d'accord avec la première.
    monter([
      runFactice({ run_id: "recent", objectif: "Le plus récent" }),
      runFactice({ run_id: "ancien", objectif: "Le plus ancien" }),
    ]);

    const [premier] = within(
      screen.getByRole("list", { name: "En cours" }),
    ).getAllByRole("listitem");
    expect(premier).toHaveTextContent("Le plus récent");
  });

  it("apparie la validation en attente au run de sa tâche", () => {
    monter([runFactice({ run_id: "run-2" })], {
      taches: [tacheFactice({ id: "T-9", run_id: "run-2" })],
      validations: [validationFactice({ tache_id: "T-9" })],
    });

    expect(screen.getByRole("list", { name: "Suspendus" })).toBeInTheDocument();
  });
});

// -------------------------------------------- Le seul groupe borné : les soldés

describe("les soldés du jour", () => {
  const solde = (run_id: string, fin: string) =>
    runFactice({ run_id, statut: EXECUTION_TERMINEE, fin });

  it("écarte ce qui s'est soldé un autre jour", () => {
    // Un run terminé avant-hier n'apprend rien sur « où en est-on », et la liste
    // des runs le garde.
    monter([
      solde("aujourdhui", maintenant()),
      solde("avant-hier", "2026-01-02T10:00:00Z"),
    ]);

    const groupe = screen.getByRole("list", { name: "Soldés du jour" });
    expect(within(groupe).getAllByRole("listitem")).toHaveLength(1);
    expect(groupe).toHaveTextContent("aujourdhui");
  });

  it("date un run soldé de son début quand il n'a pas de fin", () => {
    // Le contrat garde `fin` nullable : une trace relue d'un backend antérieur
    // peut être soldée sans porter sa date de fin — la dater de son début vaut
    // mieux que la faire disparaître.
    monter([
      runFactice({
        run_id: "sans-fin",
        statut: EXECUTION_TERMINEE,
        debut: maintenant(),
        fin: null,
      }),
    ]);

    expect(screen.getByRole("list", { name: "Soldés du jour" })).toHaveTextContent(
      "sans-fin",
    );
  });

  it("écarte un horodatage illisible plutôt que de deviner son jour", () => {
    monter([solde("illisible", "pas-une-date")]);

    expect(
      screen.queryByRole("list", { name: "Soldés du jour" }),
    ).not.toBeInTheDocument();
  });

  it("dit ce qu'il masque au-delà de cinq, au lieu de dérouler", () => {
    monter(Array.from({ length: 8 }, (_, n) => solde(`r-${n}`, maintenant())));

    const groupe = screen.getByRole("list", { name: "Soldés du jour" });
    expect(within(groupe).getAllByRole("listitem")).toHaveLength(5);
    expect(screen.getByText(/\+ 3 autres soldés aujourd'hui/)).toBeInTheDocument();
  });

  it("accorde son décompte au singulier", () => {
    monter(Array.from({ length: 6 }, (_, n) => solde(`r-${n}`, maintenant())));

    expect(screen.getByText(/\+ 1 autre soldé aujourd'hui/)).toBeInTheDocument();
  });

  it("ne borne que les soldés — les autres groupes s'affichent en entier", () => {
    // C'est précisément ce que l'écran existe pour montrer.
    monter(Array.from({ length: 8 }, (_, n) => runFactice({ run_id: `r-${n}` })));

    expect(
      within(screen.getByRole("list", { name: "En cours" })).getAllByRole("listitem"),
    ).toHaveLength(8);
  });
});

// ------------------------------------------------- Le vide, et les renvois

describe("l'écran quand rien ne tourne", () => {
  it("nomme le projet et dit où sont restés les runs d'hier", () => {
    monter([runFactice({ statut: EXECUTION_TERMINEE, fin: "2026-01-02T10:00:00Z" })]);

    expect(
      screen.getByText(/Aucun run en cours, suspendu ni soldé aujourd'hui sur Dépensio/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Voir tous les runs" }),
    ).toHaveAttribute("href", "/runs");
  });

  it("mène toujours à la liste, qui les porte tous", () => {
    monter([runFactice()]);

    expect(screen.getByRole("link", { name: /Tous les runs/ })).toHaveAttribute(
      "href",
      "/runs",
    );
  });

  it("reste l'ancre de la visite guidée que le Kanban occupait", () => {
    const { container } = monter([runFactice()]);

    expect(container.querySelector('[data-guide="etat-runs"]')).not.toBeNull();
  });
});

describe("il ne décide de rien", () => {
  it("porte l'état d'un run interrompu, jamais le bouton qui le reprend", () => {
    // Ce qui appelle un geste passe devant (« Runs interrompus », #349), ce qui
    // décrit l'état se lit d'un bloc — un run peut donc paraître deux fois sur
    // l'écran, et c'est voulu.
    monter([
      runFactice({
        run_id: "perdu",
        vitalite: VITALITE_ORPHELIN,
        brief_approuve: true,
      }),
    ]);

    expect(screen.getByRole("list", { name: "Interrompus" })).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Reprendre" }),
    ).not.toBeInTheDocument();
  });
});

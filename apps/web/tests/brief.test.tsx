/**
 * L'écran « Valider le brief » (#322, docs/05 §2.7.4).
 *
 * **Tests différés → #323**, et ce fichier n'est pas un oubli de ce report : le
 * lot final couvrira l'écran (états de chargement, refus de l'API, portée projet,
 * accessibilité). Ce qui est ici est la part que la règle de docs/10 §5.1 garde
 * au lot intermédiaire — sa **logique critique**, c'est-à-dire les trois endroits
 * où une erreur ne se voit pas :
 *
 * - **approuvé corrigé vs approuvé tel quel.** `brief: null` fait retenir au
 *   moteur sa propre proposition ; un brief envoyé devient l'entrée de la
 *   décomposition. Confondre les deux fait décomposer un texte que personne n'a
 *   validé, sans qu'aucun écran ne le montre ;
 * - **un refus n'emporte jamais de brief.** La route l'ignore, donc l'envoyer ne
 *   casse rien — mais laisse croire à la lecture du code qu'une correction a été
 *   retenue sur un run qu'on vient d'annuler ;
 * - **les réponses s'apparient par position.** Une liste courte d'un cran
 *   affecte chaque réponse à la question suivante ; l'API refuse une liste qui ne
 *   fait pas le compte, encore faut-il que l'écran envoie bien une entrée par
 *   question, chaînes vides comprises.
 *
 * Le reste des cas ci-dessous (sections rendues, coût, écran vide) tient en une
 * ligne chacun une fois le décor posé : c'est le décor qui coûte, pas l'assertion.
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ValidationBriefs } from "@/components/brief/ValidationBriefs";
import { CentreNotifications } from "@/components/CentreNotifications";
import { PanneauBriefs } from "@/components/PanneauBriefs";
import type { Brief, DetailExecution, Evenement, ResumeExecution } from "@/lib/types";

import { rendreAvecEtat, usageFactice, validationFactice } from "./aides";

// Seule `chargerExecution` est remplacée : l'écran charge le détail du run
// lui-même (le hook global n'en tient que les résumés). `importOriginal` garde le
// reste du module réel — même précaution que `composer.test.tsx`.
const chargerExecution = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  return { ...reel, chargerExecution: (runId: string) => chargerExecution(runId) };
});

const BRIEF: Brief = {
  objectif: "Ajouter l'authentification par e-mail et mot de passe",
  perimetre: ["Inscription", "Connexion"],
  hors_perimetre: [],
  contraintes: ["Ne pas casser les sessions existantes"],
  criteres_acceptation: [
    "Un compte se crée",
    "Une session expire",
    "Le mot de passe est haché",
  ],
  hypotheses: ["Base PostgreSQL"],
  questions: [],
};

function runFactice(partiel: Partial<ResumeExecution> = {}): ResumeExecution {
  return {
    run_id: "run-1",
    objectif: "Ajoute l'auth e-mail à mon app",
    statut: "en_attente_brief",
    nb_taches: 0,
    cout_usd: 0.42,
    ticket: null,
    projet_id: "p1",
    mode_brief: "humain",
    attente_depuis: "2026-08-12T10:00:00Z",
    debut: "2026-08-12T09:58:00Z",
    fin: null,
    ...partiel,
  };
}

function detailFactice(
  brief: Brief,
  evenements: Evenement[] = [],
): DetailExecution {
  return {
    ...runFactice(),
    brief,
    cout: {
      run_id: "run-1",
      planification: usageFactice(),
      brief: usageFactice(),
      total: { ...usageFactice(), cout_usd: 0.42 },
      taches: [],
    },
    evenements,
  };
}

function evenementBrief(partiel: Partial<Evenement>): Evenement {
  return {
    type: "",
    run_id: "run-1",
    tache_id: "",
    titre: "",
    agent: "orchestrateur",
    role: "Orchestrateur",
    statut: "",
    detail: "",
    description: "",
    cout_usd: null,
    usage: null,
    instances: null,
    ticket: null,
    projet_id: "p1",
    horodatage: "2026-08-12T09:58:00Z",
    ...partiel,
  };
}

/** Monte l'écran et attend qu'il ait chargé son brief. */
async function ecran(partiel: Parameters<typeof rendreAvecEtat>[1] = {}) {
  rendreAvecEtat(<ValidationBriefs />, {
    executions: [runFactice()],
    ...partiel,
  });
  return screen.findByRole("region", { name: "Valider le brief" });
}

beforeEach(() => {
  chargerExecution.mockReset();
  chargerExecution.mockResolvedValue(detailFactice(BRIEF));
});

describe("critère 1 — lire, corriger, trancher", () => {
  it("rend les sept sections du brief, éditables", async () => {
    await ecran();
    for (const libelle of [
      /^Objectif$/,
      /^Périmètre/,
      /^Hors-périmètre/,
      /^Contraintes/,
      /^Critères d'acceptation/,
      /^Hypothèses/,
      /^Questions/,
    ]) {
      expect(screen.getByLabelText(libelle)).toBeTruthy();
    }
  });

  it("approuve **tel quel** tant que rien n'a été touché (brief: null)", async () => {
    const trancherBrief = vi.fn().mockResolvedValue(undefined);
    await ecran({ trancherBrief });

    await userEvent.click(screen.getByRole("button", { name: "Approuver" }));

    // `null` et non une copie du brief : c'est ce qui fait retenir au moteur sa
    // propre proposition, sans la faire retraverser la validation de schéma.
    expect(trancherBrief).toHaveBeenCalledWith("run-1", {
      approuve: true,
      brief: null,
    });
  });

  it("approuve la version **corrigée** dès qu'une section change", async () => {
    const trancherBrief = vi.fn().mockResolvedValue(undefined);
    await ecran({ trancherBrief });

    const perimetre = screen.getByLabelText(/^Périmètre/);
    await userEvent.clear(perimetre);
    await userEvent.type(perimetre, "Inscription seule");

    // Le bouton **dit** ce qu'il envoie : approuver un texte modifié et
    // approuver la proposition de la machine ne sont pas le même geste.
    const bouton = await screen.findByRole("button", {
      name: "Approuver la version corrigée",
    });
    await userEvent.click(bouton);

    expect(trancherBrief).toHaveBeenLastCalledWith("run-1", {
      approuve: true,
      brief: { ...BRIEF, perimetre: ["Inscription seule"] },
    });
  });

  it("n'emporte jamais de brief sur un refus, même après correction", async () => {
    const trancherBrief = vi.fn().mockResolvedValue(undefined);
    await ecran({ trancherBrief });

    await userEvent.type(screen.getByLabelText(/^Hypothèses/), "\nAutre chose");
    await userEvent.click(screen.getByRole("button", { name: "Refuser" }));

    expect(trancherBrief).toHaveBeenLastCalledWith("run-1", {
      approuve: false,
      brief: null,
    });
  });

  it("bloque l'approbation d'un brief que le schéma refuserait, en le disant", async () => {
    await ecran();
    await userEvent.clear(screen.getByLabelText(/^Critères d'acceptation/));

    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Approuver la version corrigée" }),
      ).toHaveProperty("disabled", true),
    );
    expect(screen.getByText(/au moins un critère d'acceptation/)).toBeTruthy();
  });
});

describe("critère 1 — les questions et leurs réponses (#321)", () => {
  it("propose de répondre, jamais de trancher, quand le run pose des questions", async () => {
    const avecQuestions = {
      ...BRIEF,
      questions: ["Quel fournisseur d'e-mail ?", "Quelle durée de session ?"],
    };
    chargerExecution.mockResolvedValue(detailFactice(avecQuestions));
    const repondreAuBrief = vi.fn().mockResolvedValue(undefined);
    await ecran({
      executions: [
        runFactice({
          statut: "en_attente_reponses",
          tour_clarification: 1,
          tours_clarification_max: 2,
        }),
      ],
      repondreAuBrief,
    });

    // Proposer « approuver/refuser » à quelqu'un à qui on pose une question
    // serait une impasse : c'est la raison d'être du statut distinct.
    expect(screen.queryByRole("button", { name: "Approuver" })).toBeNull();
    expect(screen.getByText("tour 1 sur 2")).toBeTruthy();

    await userEvent.type(
      screen.getByLabelText("Quel fournisseur d'e-mail ?"),
      "SendGrid",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Envoyer les réponses" }),
    );

    // Une entrée par question, dans l'ordre, la chaîne vide comprise :
    // l'appariement est positionnel et une liste courte décalerait tout.
    expect(repondreAuBrief).toHaveBeenCalledWith("run-1", ["SendGrid", ""]);
  });

  it("relit les tours déjà joués, sans escamoter une question sans réponse", async () => {
    const evenements = [
      evenementBrief({
        type: "brief.reponses",
        reponses: ["SendGrid", ""],
        tour: 1,
        horodatage: "2026-08-12T09:59:00Z",
      }),
      evenementBrief({
        type: "brief.questions",
        tour: 1,
        brief: {
          ...BRIEF,
          questions: ["Quel fournisseur d'e-mail ?", "Quelle durée ?"],
        },
      }),
    ];
    chargerExecution.mockResolvedValue(detailFactice(BRIEF, evenements));
    await ecran();

    await userEvent.click(
      screen.getByRole("button", { name: "Voir les échanges" }),
    );
    expect(screen.getByText("SendGrid")).toBeTruthy();
    expect(screen.getByText(/Sans réponse — partie en hypothèse/)).toBeTruthy();
  });
});

describe("critère 3 — le coût face à la décision", () => {
  it("montre l'engagé mesuré et la suite estimée, dans le bloc de décision", async () => {
    await ecran();

    const decision = screen.getByRole("region", { name: "Décision sur le brief" });
    expect(decision.textContent).toContain("Déjà engagé");
    expect(decision.textContent).toContain("Si vous approuvez");
    // 3 critères → 3 tâches → 0,80 + 3 × 0,74 = 3,02 $ en borne basse.
    expect(decision.textContent).toContain("3,02");
    expect(decision.textContent).toMatch(/Refuser n'engage/);
  });

  it("réestime la suite quand la correction change le nombre de critères", async () => {
    await ecran();
    const criteres = screen.getByLabelText(/^Critères d'acceptation/);
    await userEvent.clear(criteres);
    await userEvent.type(criteres, "Un seul critère");

    // Plancher à trois tâches : un brief à un critère ne produit pas un run à
    // une tâche. La borne basse ne bouge donc pas, et c'est voulu.
    const decision = screen.getByRole("region", { name: "Décision sur le brief" });
    await waitFor(() => expect(decision.textContent).toContain("3 tâches"));
  });
});

describe("critère 2 — ce qui attend se voit", () => {
  it("nomme le projet quand rien n'attend", async () => {
    rendreAvecEtat(<ValidationBriefs />, { executions: [] });
    expect(
      await screen.findByText(/Aucun brief en attente sur Dépensio/),
    ).toBeTruthy();
  });

  it("dit depuis quand le run attend — un run suspendu n'est pas un run planté", async () => {
    const region = await ecran();
    expect(region.textContent).toContain("Attend votre décision");
    expect(region.textContent).toMatch(/depuis/);
  });

  it("compte les briefs dans le badge de la cloche, à côté des validations", () => {
    rendreAvecEtat(<CentreNotifications />, {
      executions: [runFactice()],
      validations: [validationFactice({ tache_id: "T-1" })],
    });
    // Une seule pastille pour les deux familles — la question à laquelle elle
    // répond est « combien de choses m'attendent ? » —, mais l'étiquette les
    // nomme : répondre à un brief et arbitrer une action sensible ne demandent
    // ni la même disponibilité ni la même personne.
    expect(
      screen.getByRole("button", {
        name: "Notifications — 1 validation et 1 brief en attente",
      }),
    ).toBeTruthy();
  });

  it("signale le brief sur le tableau de bord, sans proposer de le trancher", () => {
    rendreAvecEtat(<PanneauBriefs executions={[runFactice()]} />);
    const panneau = screen.getByRole("region", { name: "Briefs en attente" });
    expect(panneau.textContent).toContain("Le brief attend votre décision");
    // Il achemine, il ne décide pas : sept sections, des questions et un coût ne
    // tiennent pas dans une carte, et approuver sans lire est exactement ce que
    // le point de contrôle empêche.
    expect(within(panneau).queryByRole("button")).toBeNull();
    expect(
      within(panneau).getByRole("link", { name: /Relire/ }),
    ).toHaveProperty("pathname", "/brief");
  });
});

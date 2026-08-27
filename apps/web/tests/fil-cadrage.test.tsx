/**
 * Le cadrage décidé **dans le fil** (#483, lot 2 de #481, docs/05 §2.7.5).
 *
 * **Tests différés → #485**, et ce fichier n'est pas un oubli de ce report :
 * le lot final couvrira l'écran (états de chargement, refus de l'API, file à
 * plusieurs runs, accessibilité). Ce qui est ici est la part que la règle de
 * docs/10 §5.1 garde au lot intermédiaire — sa **logique critique**, c'est-à-dire
 * ce qu'un déménagement peut casser sans que rien ne le montre :
 *
 * - **le canal reste le canal.** Le critère 1 du ticket dit « la décision emprunte
 *   le canal existant, pas un second » : le fil appelle `trancherBrief` /
 *   `repondreAuBrief` du contexte, donc les deux routes de §6.10 (#320, #321), et
 *   avec le **même contrat** — `brief: null` quand rien n'a été touché, le brief
 *   corrigé sinon, jamais de brief sur un refus. Une seconde formulation
 *   décomposerait un texte que personne n'a validé, en silence ;
 * - **le rang du tour et son plafond restent en clair.** Le critère 2 dit
 *   « comme aujourd'hui » : c'est le même composant qui les rend, et le vérifier
 *   ici est ce qui empêche qu'un jour le fil en fabrique une seconde version qui
 *   les oublie ;
 * - **un run suspendu reste visible hors du fil.** Le critère 3, et le seul dont
 *   l'échec est *invisible depuis l'écran qu'on regarde* : les trois surfaces
 *   d'acheminement (§2.1) résolvent leur destination par le **menu**, donc un
 *   renvoi resté sur « Valider le brief » s'éteindra sans un mot le jour où #484
 *   retire l'entrée — et un run bloqué n'aura plus aucune surface pour se
 *   montrer. C'est exactement le défaut que le critère nomme.
 *
 * Le décor est celui de `brief.test.tsx` : seule `chargerExecution` est
 * remplacée, le fil chargeant le détail du run lui-même.
 */

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { FilDeCadrage } from "@/components/chat/FilDeCadrage";
import { ATTENTES } from "@/components/runs/EtatRun";
import { PAGE_DU_CADRAGE } from "@/lib/brief";
import { ATTENTE_BRIEF, ATTENTE_REPONSES } from "@/lib/execution";
import { entreeParLibelle } from "@/lib/navigation";
import type { Brief, DetailExecution, Evenement, ResumeExecution } from "@/lib/types";

import { rendreAvecEtat, usageFactice } from "./aides";

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
  criteres_acceptation: ["Un compte se crée", "Le mot de passe est haché"],
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

/** Monte le fil et attend que le cadrage du run soit chargé. */
async function fil(
  run: ResumeExecution = runFactice(),
  partiel: Parameters<typeof rendreAvecEtat>[1] = {},
) {
  rendreAvecEtat(<FilDeCadrage />, { executions: [run], ...partiel });
  return screen.findByRole("list", {
    name: `Cadrage de ${run.objectif}`,
  });
}

beforeEach(() => {
  chargerExecution.mockReset();
  chargerExecution.mockResolvedValue(detailFactice(BRIEF));
});

describe("critère 1 — le brief se lit, se corrige et se tranche dans le fil", () => {
  it("ouvre le fil sur l'objectif d'origine et rend les sept sections éditables", async () => {
    const conversation = await fil();

    // L'objectif ouvre le fil : c'est la demande, et tout ce qui suit y répond.
    expect(conversation.textContent).toContain("Ajoute l'auth e-mail à mon app");
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

  it("approuve **tel quel** par le canal existant tant que rien n'a été touché", async () => {
    const trancherBrief = vi.fn().mockResolvedValue(undefined);
    await fil(runFactice(), { trancherBrief });

    await userEvent.click(screen.getByRole("button", { name: "Approuver" }));

    // `null` et non une copie du brief : le moteur retient sa propre proposition
    // sans la faire retraverser la validation de schéma. Le fil n'a rien inventé,
    // il appelle le `trancherBrief` du contexte — donc `POST /brief/decision`.
    expect(trancherBrief).toHaveBeenCalledWith("run-1", {
      approuve: true,
      brief: null,
    });
  });

  it("approuve la version **corrigée** dès qu'une section change", async () => {
    const trancherBrief = vi.fn().mockResolvedValue(undefined);
    await fil(runFactice(), { trancherBrief });

    const perimetre = screen.getByLabelText(/^Périmètre/);
    await userEvent.clear(perimetre);
    await userEvent.type(perimetre, "Inscription seule");

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
    await fil(runFactice(), { trancherBrief });

    await userEvent.type(screen.getByLabelText(/^Hypothèses/), "\nAutre chose");
    await userEvent.click(screen.getByRole("button", { name: "Refuser" }));

    expect(trancherBrief).toHaveBeenLastCalledWith("run-1", {
      approuve: false,
      brief: null,
    });
  });

  it("bloque l'approbation d'un brief que le schéma refuserait, en le disant", async () => {
    await fil();
    await userEvent.clear(screen.getByLabelText(/^Critères d'acceptation/));

    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Approuver la version corrigée" }),
      ).toHaveProperty("disabled", true),
    );
    expect(screen.getByText(/au moins un critère d'acceptation/)).toBeTruthy();
  });
});

describe("critère 2 — les questions se répondent dans le fil", () => {
  const AVEC_QUESTIONS: Brief = {
    ...BRIEF,
    questions: ["Quel fournisseur d'e-mail ?", "Faut-il un SSO ?"],
  };
  const RUN_QUESTIONS = runFactice({
    statut: "en_attente_reponses",
    tour_clarification: 1,
    tours_clarification_max: 2,
  });

  it("annonce le rang du tour et son plafond, et ne propose pas de trancher", async () => {
    chargerExecution.mockResolvedValue(detailFactice(AVEC_QUESTIONS));
    await fil(RUN_QUESTIONS);

    // Savoir s'il reste un tour change la façon de répondre (#321) : on
    // développe au dernier, on va à l'essentiel avant.
    expect(screen.getByText("tour 1 sur 2")).toBeTruthy();
    // On ne demande pas d'approuver ce sur quoi on vient d'interroger quelqu'un.
    expect(screen.queryByRole("button", { name: /^Approuver/ })).toBeNull();
  });

  it("envoie une réponse par question, dans l'ordre, chaînes vides comprises", async () => {
    chargerExecution.mockResolvedValue(detailFactice(AVEC_QUESTIONS));
    const repondreAuBrief = vi.fn().mockResolvedValue(undefined);
    await fil(RUN_QUESTIONS, { repondreAuBrief });

    await userEvent.type(
      screen.getByLabelText("Quel fournisseur d'e-mail ?"),
      "SendGrid",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Envoyer les réponses" }),
    );

    // L'appariement est **positionnel** : une liste courte d'un cran affecterait
    // chaque réponse à la question suivante. Ne pas savoir est une réponse — la
    // question part en hypothèse explicite plutôt que d'être reposée.
    expect(repondreAuBrief).toHaveBeenCalledWith("run-1", ["SendGrid", ""]);
  });

  it("déroule les tours déjà joués dans le fil, sans replier, et nomme le sans-réponse", async () => {
    // Sur `/brief` ces échanges vivent dans un accordéon replié : ils y sont un
    // à-côté du geste. Ici ils **sont** la conversation, et les replier
    // reviendrait à cacher le fil dans le fil.
    chargerExecution.mockResolvedValue(
      detailFactice(BRIEF, [
        evenementBrief({
          type: "brief.reponses",
          reponses: ["SendGrid", ""],
          horodatage: "2026-08-12T09:59:00Z",
        }),
        evenementBrief({
          type: "brief.questions",
          tour: 1,
          brief: { ...BRIEF, questions: ["Quel e-mail ?", "Un SSO ?"] },
        }),
      ]),
    );
    const conversation = await fil();

    expect(conversation.textContent).toContain("Quel e-mail ?");
    expect(conversation.textContent).toContain("SendGrid");
    // Une hypothèse née d'un « je ne sais pas » assumé ne se conteste pas comme
    // une hypothèse que personne n'a vue passer.
    expect(conversation.textContent).toContain(
      "Sans réponse — partie en hypothèse",
    );
  });
});

describe("critère 3 — un run suspendu reste visible hors du fil", () => {
  it("achemine les trois surfaces vers la page où le geste vit désormais", () => {
    // Le contrôle porte sur le **libellé résolu par le menu** et non sur un
    // chemin écrit en dur : c'est ce qui fait qu'un renvoi suit sa page (#191).
    // Et c'est ici que le déménagement devient opposable — les trois surfaces de
    // §2.1 partagent une seule constante, donc elles bougent ensemble ou pas du
    // tout. Rester sur « Valider le brief » les éteindrait toutes les trois le
    // jour où #484 retire l'entrée, sans un message.
    expect(entreeParLibelle(PAGE_DU_CADRAGE)?.href).toBe("/chat");
    expect(ATTENTES[ATTENTE_BRIEF].page).toBe(PAGE_DU_CADRAGE);
    expect(ATTENTES[ATTENTE_REPONSES].page).toBe(PAGE_DU_CADRAGE);
  });

  it("nomme le projet quand aucun cadrage n'attend", async () => {
    rendreAvecEtat(<FilDeCadrage />, { executions: [] });

    expect(
      await screen.findByText(/Aucun cadrage en attente sur Dépensio/),
    ).toBeTruthy();
  });
});

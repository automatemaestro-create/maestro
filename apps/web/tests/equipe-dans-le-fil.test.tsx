/**
 * **Un projet sans équipe se la voit proposer dans le fil** (#1146).
 *
 * L'essai du 2026-09-21 : sur un projet qui n'avait aucun agent, le fil proposait
 * un run — « Je lance ? » —, qui payait cadrage et plan puis échouait au routage.
 * L'orchestration propose désormais l'**équipe** à la place, et c'est au pied du
 * fil qu'on la valide, sans quitter la conversation.
 *
 * Ce que ce filet garde, c'est ce qui est monté et ce qui part — chaque maillon
 * de la chaîne, le moteur compris, a déjà sa suite (`tests/test_chat_global.py`,
 * `tests/test_projet_outille_http.py`) :
 *
 * ① **la carte remplace la demande de cadrage** quand le dernier message porte une
 *    demande de recrutement, et n'apparaît pas sinon ;
 * ② **elle demande la proposition du projet de la demande**, pas de la fenêtre —
 *    c'est lui qui n'a personne ;
 * ③ **le récapitulatif se lit sans rien ouvrir**, autorisations `auto` comprises :
 *    le critère de l'étape d'équipe (#1040) tient malgré le repli ;
 * ④ **ce qui part est l'équipe gardée**, rôle retiré ou instances ajustées compris,
 *    dans la forme de la création (#1040) ;
 * ⑤ **« Plus tard » est une issue nommée**, qui décline sans rien envoyer d'autre.
 *
 * ⚠ Aucune géométrie ici (#308) et aucun jugement de rendu : ce que la carte
 * devient à 320 px est une mesure du banc, son rendu l'affaire de la relecture.
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import PageChat from "@/app/chat/page";
import { oublierPropositions, recrutementEnAttente } from "@/lib/equipe";
import { AGENT_ORCHESTRATION } from "@/lib/orchestration";
import type {
  MessageChat,
  PropositionEquipe,
  RoleEquipe,
} from "@/lib/types";

import {
  messageFactice,
  pageJournalCourante,
  poserFilAssistance,
  projetsDeclares,
  rendreAvecEtat,
} from "./aides";

const proposerEquipe = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...reel,
    chargerProjets: () => Promise.resolve(projetsDeclares()),
    chargerJournal: () => Promise.resolve(pageJournalCourante()),
    proposerEquipe: (id: string) => proposerEquipe(id),
  };
});

/** Le projet de la fenêtre dans `rendreAvecEtat` — celui dont la demande parle. */
const PROJET = "prj-7f3a1c2b";
const OBJECTIF = "Vider le dossier du projet Dépensio";

function role(partiel: Partial<RoleEquipe> = {}): RoleEquipe {
  return {
    nom: "dev",
    role: "Développeur",
    gabarit: "developpeur",
    competences: ["backend"],
    raison: "le projet est écrit en Python",
    justification: null,
    instances: 1,
    raison_instances: "une instance suffit à ce volume",
    outils: ["Read", "Write", "Bash"],
    playbook: "Tu écris le code de Dépensio.",
    playbook_origine: "gabarit",
    playbook_raison: "playbook du gabarit",
    intention: "",
    skills: [],
    autorisations: [
      {
        outil: "Bash(pytest *)",
        cran: "ask",
        decideur: "auto",
        raison: "lancer les tests déclarés",
      },
    ],
    politique: { allow: [], ask: { "Bash(pytest *)": "auto" }, deny: [] },
    ...partiel,
  };
}

const PROPOSITION: PropositionEquipe = {
  proposition: 1,
  id: "equ-42",
  projet_id: PROJET,
  faite_le: "2026-09-22T10:00:00+00:00",
  resume: "Développeur, QA — 2 rôle(s), 2 instance(s)",
  source: { type: "analyse" },
  roles: [
    role(),
    role({
      nom: "qa",
      role: "QA",
      gabarit: "qa",
      competences: ["tests"],
      raison: "des tests pytest existent",
      autorisations: [],
      politique: { allow: [], ask: {}, deny: [] },
    }),
  ],
  ecartes: [
    { nom: "orchestrateur", role: "Orchestrateur", raison: "c'est Maestro" },
  ],
  instances_total: 2,
  cree: false,
  validation: "requise",
};

/** Le message de l'orchestration qui propose l'équipe au lieu du run. */
function demandeDeRecrutement(projetId = PROJET): MessageChat {
  return messageFactice({
    agent: AGENT_ORCHESTRATION,
    auteur: AGENT_ORCHESTRATION,
    contenu: "Avant de lancer, il faut une équipe : ce projet n'a encore aucun agent…",
    recrutement: { objectif: OBJECTIF, projet_id: projetId },
  });
}

function carteAttendue(): Promise<HTMLElement> {
  return screen.findByRole("region", { name: "Équipe à valider" });
}

beforeEach(() => {
  proposerEquipe.mockResolvedValue(PROPOSITION);
});

afterEach(() => {
  vi.clearAllMocks();
  // La mémoire des propositions vit au module : sans l'oublier, un test hériterait
  // de la proposition du précédent et ne demanderait rien à l'API.
  oublierPropositions();
});

describe("la demande de recrutement, dans le fil", () => {
  it("n'attend que tant que rien ne l'a suivie", () => {
    const demande = demandeDeRecrutement();
    const suite = messageFactice({ auteur: "utilisateur", contenu: "plus tard" });

    expect(recrutementEnAttente([demande])).toBe(demande);
    expect(recrutementEnAttente([demande, suite])).toBeNull();
    expect(recrutementEnAttente([])).toBeNull();
  });

  it("pose la carte d'équipe à la place de « Je lance ? »", async () => {
    poserFilAssistance({ messages: [demandeDeRecrutement()] });
    rendreAvecEtat(<PageChat />);

    const carte = await carteAttendue();

    expect(
      within(carte).getByRole("heading", { name: "Recruter l'équipe ?" }),
    ).toBeInTheDocument();
    // Le projet de la demande est nommé, dans sa casse, hors du titre.
    expect(within(carte).getAllByText(/Dépensio/).length).toBeGreaterThan(0);
    // Et pas de « Je lance ? » : le run serait voué à l'échec.
    expect(
      screen.queryByRole("region", { name: "Décision sur le cadrage" }),
    ).not.toBeInTheDocument();
    expect(proposerEquipe).toHaveBeenCalledWith(PROJET);
  });

  it("ne pose rien sur un fil dont le dernier message ne demande pas d'équipe", async () => {
    poserFilAssistance({ messages: [messageFactice()] });
    rendreAvecEtat(<PageChat />);

    await screen.findByRole("region", { name: "Chat global" });
    expect(
      screen.queryByRole("region", { name: "Équipe à valider" }),
    ).not.toBeInTheDocument();
    expect(proposerEquipe).not.toHaveBeenCalled();
  });

  it("ne redemande pas la proposition quand la carte est remontée (une navigation)", async () => {
    // La colonne porte la carte sur chaque écran, et une proposition coûte une
    // analyse et un appel modèle par rôle : relevé à la relecture de #1146.
    poserFilAssistance({ messages: [demandeDeRecrutement()] });
    const premier = rendreAvecEtat(<PageChat />);
    await within(await carteAttendue()).findByText(/2 agents/);
    premier.unmount();

    rendreAvecEtat(<PageChat />);

    await within(await carteAttendue()).findByText(/2 agents/);
    expect(proposerEquipe).toHaveBeenCalledTimes(1);
  });

  it("demande la proposition du projet de la demande, pas de la fenêtre", async () => {
    poserFilAssistance({ messages: [demandeDeRecrutement("prj-autre0001")] });
    rendreAvecEtat(<PageChat />);

    const carte = await carteAttendue();

    await waitFor(() => expect(proposerEquipe).toHaveBeenCalledWith("prj-autre0001"));
    // Le nom de la fenêtre n'est pas prêté à un autre projet : on nomme l'identifiant.
    expect(within(carte).getAllByText(/prj-autre0001/).length).toBeGreaterThan(0);
  });
});

describe("le récapitulatif, sans rien ouvrir", () => {
  it("dit combien d'agents, lesquels, et où", async () => {
    poserFilAssistance({ messages: [demandeDeRecrutement()] });
    rendreAvecEtat(<PageChat />);

    const carte = await carteAttendue();

    expect(
      await within(carte).findByText(/2 agents/),
    ).toBeInTheDocument();
    expect(within(carte).getByText(/Développeur · QA/)).toBeInTheDocument();
    expect(
      within(carte).getByRole("button", { name: "Créer l'équipe (2)" }),
    ).toBeInTheDocument();
    // Le détail est replié : les lignes de rôle ne sont pas montées.
    expect(within(carte).queryByRole("checkbox")).not.toBeInTheDocument();
  });

  it("nomme les autorisations décidées d'avance malgré le repli (#716)", async () => {
    poserFilAssistance({ messages: [demandeDeRecrutement()] });
    rendreAvecEtat(<PageChat />);

    const carte = await carteAttendue();

    expect(await within(carte).findByText(/Décidé d'avance par vous/)).toBeInTheDocument();
    expect(within(carte).getByText("Bash(pytest *)")).toBeInTheDocument();
  });
});

describe("ce que la validation envoie", () => {
  it("envoie l'équipe gardée, rôle retiré et instances ajustées compris", async () => {
    const recruter = vi.fn(async () => {});
    poserFilAssistance({ messages: [demandeDeRecrutement()], recruter });
    rendreAvecEtat(<PageChat />);
    const carte = await carteAttendue();
    await within(carte).findByText(/2 agents/);

    await userEvent.click(within(carte).getByRole("button", { name: "Voir l'équipe" }));
    await userEvent.click(within(carte).getByRole("checkbox", { name: /QA/ }));
    const instances = within(carte).getAllByRole("spinbutton", { name: "Instances" })[0];
    await userEvent.clear(instances);
    await userEvent.type(instances, "3");
    await userEvent.click(within(carte).getByRole("button", { name: "Créer l'équipe (3)" }));

    await waitFor(() => expect(recruter).toHaveBeenCalledTimes(1));
    const [approuve, roles, propositionId] = recruter.mock.calls[0] as unknown as [
      boolean,
      { nom: string; instances: number; playbook: string; politique: unknown }[],
      string,
    ];
    expect(approuve).toBe(true);
    expect(propositionId).toBe("equ-42");
    expect(roles.map((r) => [r.nom, r.instances])).toEqual([["dev", 3]]);
    // Ce qui repart est ce qui a été montré : playbook et politique tels quels.
    expect(roles[0].playbook).toBe("Tu écris le code de Dépensio.");
    expect(roles[0].politique).toEqual(PROPOSITION.roles[0].politique);
  });

  it("« Plus tard » décline, sans rien envoyer d'autre", async () => {
    const recruter = vi.fn(async () => {});
    poserFilAssistance({ messages: [demandeDeRecrutement()], recruter });
    rendreAvecEtat(<PageChat />);
    const carte = await carteAttendue();
    await within(carte).findByText(/2 agents/);

    await userEvent.click(within(carte).getByRole("button", { name: "Plus tard" }));

    await waitFor(() => expect(recruter).toHaveBeenCalledWith(false));
  });

  it("ne crée rien quand tout a été retiré", async () => {
    poserFilAssistance({ messages: [demandeDeRecrutement()] });
    rendreAvecEtat(<PageChat />);
    const carte = await carteAttendue();
    await within(carte).findByText(/2 agents/);

    await userEvent.click(within(carte).getByRole("button", { name: "Voir l'équipe" }));
    for (const caseRole of within(carte).getAllByRole("checkbox")) {
      await userEvent.click(caseRole);
    }

    expect(within(carte).getByRole("button", { name: /Créer l'équipe/ })).toBeDisabled();
    expect(within(carte).getByText(/Tout a été retiré/)).toBeInTheDocument();
  });

  it("dit pourquoi la proposition manque, et laisse remettre à plus tard", async () => {
    proposerEquipe.mockRejectedValue(new Error("projet illisible"));
    poserFilAssistance({ messages: [demandeDeRecrutement()] });
    rendreAvecEtat(<PageChat />);

    const carte = await carteAttendue();

    expect(await within(carte).findByText(/projet illisible/)).toBeInTheDocument();
    expect(within(carte).getByRole("button", { name: "Plus tard" })).toBeEnabled();
    expect(
      within(carte).queryByRole("button", { name: /Créer l'équipe/ }),
    ).not.toBeInTheDocument();
  });
});

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
 * Depuis #1227, la **même** carte sert un second moment : un run suspendu entre
 * son plan et sa première tâche, dont le plan appelle un métier que l'équipe n'a
 * pas. Trois choses changent, et ce sont les trois que la seconde moitié de ce
 * fichier garde — ce qui est demandé à l'API (un rôle, pas une équipe), ce qui se
 * lit avant le geste (le rôle, la raison, les tâches qu'il prendrait), et ce que
 * les boutons promettent : « Continuer sans » n'est pas « Plus tard ».
 *
 * Depuis #1331, l'équipe montrée **se corrige avec ses mots** sur la carte même —
 * la demande de #1159, qui ne vivait qu'à l'étape d'équipe, partie avec elle. La
 * dernière partie du fichier garde ce que les critères demandent : un geste de la
 * carte qui ouvre la demande sur place (variante C, consignée sur le ticket) ;
 * « ajoute quelqu'un pour la sécurité » qui ajoute un rôle retenu et signalé,
 * « retire le designer » qui le décoche, « deux développeurs » qui change les
 * instances — rien de créé avant la validation ; une demande incomprise ou une
 * panne qui le disent sans rien perdre, navigation comprise. Le modèle, lui, a sa
 * suite côté moteur (`tests/test_equipe_composition.py`) : ici l'API est doublée.
 *
 * ⚠ Aucune géométrie ici (#308) et aucun jugement de rendu : ce que la carte
 * devient à 320 px est une mesure du banc, son rendu l'affaire de la relecture.
 */

import { cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import PageChat from "@/app/chat/page";
import { avecGras } from "@/components/chat/LigneRole";
import {
  appliquerCorrection,
  oublierPropositions,
  recrutementEnAttente,
} from "@/lib/equipe";
import { AGENT_ORCHESTRATION } from "@/lib/orchestration";
import type {
  CorrectionEquipe,
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
const corrigerEquipe = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...reel,
    chargerProjets: () => Promise.resolve(projetsDeclares()),
    chargerJournal: () => Promise.resolve(pageJournalCourante()),
    // Tous les arguments sont relayés : le troisième (#1227) décide si l'on
    // demande l'équipe entière ou le seul rôle qu'un plan appelle.
    proposerEquipe: (id: string, choix?: unknown, renfort?: unknown) =>
      proposerEquipe(id, choix, renfort),
    // La correction avec ses mots (#1331) : le modèle est doublé ici, jamais
    // appelé — ce qui part et ce qui s'affiche sont ce que ce filet garde.
    corrigerEquipe: (...args: unknown[]) => corrigerEquipe(...args),
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
  // Une réponse doublée ne passe jamais d'un test à l'autre.
  corrigerEquipe.mockReset();
});

afterEach(() => {
  // Démonter **avant** d'oublier : la carte retient l'équipe qu'elle montre à
  // chaque rendu (#1331), et une carte encore montée pouvait la réécrire après
  // l'oubli — le test suivant, sur la même demande, reprenait alors l'équipe
  // corrigée du précédent (vu une fois sur la suite entière, sous charge).
  cleanup();
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
    // Aucun renfort : l'équipe entière, comme #1146 l'a posé.
    expect(proposerEquipe).toHaveBeenCalledWith(PROJET, [], undefined);
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

    await waitFor(() =>
      expect(proposerEquipe).toHaveBeenCalledWith("prj-autre0001", [], undefined),
    );
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

// --- Le second moment : compléter une équipe pendant un run (#1227) ----------
//
// La **même** carte, réutilisée et non doublée. Trois choses changent, et ce sont
// les trois qu'on garde : ce qui est demandé à l'API (un rôle, pas une équipe),
// ce qui se lit avant le geste (le rôle, la raison, les tâches), et ce que les
// deux boutons promettent — « Continuer sans » n'est pas « Plus tard ».

/** La demande qu'un run suspendu pose dans le fil (#1227). */
function demandeDeRenfort(): MessageChat {
  return messageFactice({
    agent: AGENT_ORCHESTRATION,
    auteur: AGENT_ORCHESTRATION,
    contenu: "Avant d'exécuter, une chose : le plan appelle un rôle absent…",
    recrutement: {
      objectif: "une petite animation du logo Maestro",
      projet_id: PROJET,
      run_id: "run-42",
      role: "Designer",
      gabarit: "interface",
      raison: "le plan de ce travail demande design-system, ui, et aucun rôle de l'équipe ne le couvre.",
      taches: ["Dessiner le logo stylisé", "Écrire le script d'animation"],
    },
  });
}

const RENFORT_PROPOSE: PropositionEquipe = {
  ...PROPOSITION,
  id: "equ-77",
  resume: "Designer — 1 rôle(s), 1 instance(s)",
  roles: [
    role({
      nom: "interface",
      role: "Designer",
      gabarit: "designer",
      competences: ["ui", "ux"],
      raison: "le plan de ce travail demande design-system, ui",
      autorisations: [],
      politique: { allow: [], ask: {}, deny: [] },
    }),
  ],
  instances_total: 1,
};

describe("le renfort d'un run en cours", () => {
  beforeEach(() => {
    proposerEquipe.mockResolvedValue(RENFORT_PROPOSE);
  });

  it("demande le seul rôle que le plan appelle, jamais l'équipe entière", async () => {
    poserFilAssistance({ messages: [demandeDeRenfort()] });
    rendreAvecEtat(<PageChat />);

    await screen.findByRole("region", { name: "Renfort à valider" });

    await waitFor(() =>
      expect(proposerEquipe).toHaveBeenCalledWith(PROJET, [], {
        gabarit: "interface",
        raison:
          "le plan de ce travail demande design-system, ui, et aucun rôle de l'équipe ne le couvre.",
      }),
    );
  });

  it("dit le rôle, pourquoi, et les tâches qu'il prendrait", async () => {
    poserFilAssistance({ messages: [demandeDeRenfort()] });
    rendreAvecEtat(<PageChat />);

    const carte = await screen.findByRole("region", { name: "Renfort à valider" });

    expect(
      within(carte).getByRole("heading", { name: "Compléter l'équipe ?" }),
    ).toBeInTheDocument();
    expect(within(carte).getByText("Designer")).toBeInTheDocument();
    expect(within(carte).getByText(/aucun rôle de l'équipe ne le couvre/)).toBeInTheDocument();
    // Les tâches sont **nommées** : « 2 tâches » ne dirait pas ce qu'on confie.
    expect(within(carte).getByText(/Il prendrait/)).toBeInTheDocument();
    expect(within(carte).getByText(/Dessiner le logo stylisé/)).toBeInTheDocument();
    expect(within(carte).getByText(/Écrire le script d'animation/)).toBeInTheDocument();
  });

  it("promet « recruter » et « continuer sans », jamais « plus tard »", async () => {
    const recruter = vi.fn(async () => {});
    poserFilAssistance({ messages: [demandeDeRenfort()], recruter });
    rendreAvecEtat(<PageChat />);
    const carte = await screen.findByRole("region", { name: "Renfort à valider" });
    await within(carte).findByText(/1 agent/);

    // Décliner ne remet rien à plus tard : le run part avec l'équipe qu'on a.
    expect(within(carte).queryByRole("button", { name: "Plus tard" })).not.toBeInTheDocument();
    await userEvent.click(within(carte).getByRole("button", { name: "Continuer sans" }));

    await waitFor(() => expect(recruter).toHaveBeenCalledWith(false));
  });

  it("envoie le rôle tel qu'il a été montré", async () => {
    const recruter = vi.fn(async () => {});
    poserFilAssistance({ messages: [demandeDeRenfort()], recruter });
    rendreAvecEtat(<PageChat />);
    const carte = await screen.findByRole("region", { name: "Renfort à valider" });
    await within(carte).findByText(/1 agent/);

    await userEvent.click(within(carte).getByRole("button", { name: "Recruter (1)" }));

    await waitFor(() => expect(recruter).toHaveBeenCalledTimes(1));
    const [approuve, roles, propositionId] = recruter.mock.calls[0] as unknown as [
      boolean,
      { nom: string; role: string; playbook: string }[],
      string,
    ];
    expect(approuve).toBe(true);
    expect(propositionId).toBe("equ-77");
    expect(roles.map((r) => r.nom)).toEqual(["interface"]);
  });

  it("ne montre aucun « pourquoi ce rôle » sur une demande d'équipe entière", async () => {
    proposerEquipe.mockResolvedValue(PROPOSITION);
    poserFilAssistance({ messages: [demandeDeRecrutement()] });
    rendreAvecEtat(<PageChat />);

    const carte = await carteAttendue();
    await within(carte).findByText(/2 agents/);

    // Les raisons y sont rôle par rôle, dans le détail : une raison globale
    // ferait double emploi.
    expect(within(carte).queryByText(/Il prendrait/)).not.toBeInTheDocument();
  });

  it("se corrige avec ses mots, lui aussi", async () => {
    poserFilAssistance({ messages: [demandeDeRenfort()] });
    rendreAvecEtat(<PageChat />);
    const carte = await screen.findByRole("region", { name: "Renfort à valider" });
    await within(carte).findByText(/1 agent/);

    expect(
      within(carte).getByRole("button", { name: "Corriger avec vos mots" }),
    ).toBeEnabled();
  });
});

// --- Corriger l'équipe avec ses mots (#1331) --------------------------------
//
// La demande de #1159, portée sur la carte du fil. Sa place est la variante C
// retenue sur le ticket : un geste discret de la rangée des gestes, qui ouvre la
// demande sur place et la replie. L'API est doublée : ce qui part, ce qui
// s'affiche, et ce que la validation emporte.

const SECURITE = role({
  nom: "securite",
  role: "Sécurité applicative",
  gabarit: "",
  competences: ["securite", "audit"],
  raison: "vous demandez quelqu'un pour la sécurité",
  playbook: "# Sécurité applicative\n\nTu audites les dépendances de Dépensio.",
  playbook_origine: "genere",
  autorisations: [
    {
      outil: "Bash(pip-audit *)",
      cran: "ask",
      decideur: "auto",
      raison: "auditer les **dépendances** déclarées",
    },
  ],
  politique: { allow: [], ask: { "Bash(pip-audit *)": "auto" }, deny: [] },
});

/** Une équipe à trois rôles, dont un designer — ce que « retire le designer » vise. */
const AVEC_DESIGNER: PropositionEquipe = {
  ...PROPOSITION,
  roles: [
    ...PROPOSITION.roles,
    role({
      nom: "interface",
      role: "Designer",
      gabarit: "designer",
      competences: ["ui"],
      raison: "le projet a une interface",
      autorisations: [],
      politique: { allow: [], ask: {}, deny: [] },
    }),
  ],
  instances_total: 3,
};

function correction(partiel: Partial<CorrectionEquipe> = {}): CorrectionEquipe {
  return {
    reponse: "",
    ajouts: [],
    retraits: [],
    remis: [],
    instances: {},
    cree: false,
    ...partiel,
  };
}

async function carteChargee(): Promise<HTMLElement> {
  const carte = await carteAttendue();
  await within(carte).findByText(/Créer l'équipe/);
  return carte;
}

function champ(carte: HTMLElement): HTMLElement | null {
  return within(carte).queryByRole("textbox", { name: /Il manque quelqu'un/ });
}

/** Tape la demande et l'envoie — en ouvrant la correction si elle est repliée. */
async function demander(carte: HTMLElement, texte: string) {
  if (champ(carte) === null) {
    await userEvent.click(
      within(carte).getByRole("button", { name: "Corriger avec vos mots" }),
    );
  }
  const saisie = champ(carte) as HTMLElement;
  await userEvent.clear(saisie);
  await userEvent.type(saisie, texte);
  await userEvent.click(within(carte).getByRole("button", { name: "Ajouter ou corriger" }));
}

describe("corriger l'équipe avec ses mots (#1331)", () => {
  it("est un geste de la carte, qui ouvre la demande sur place et la replie", async () => {
    poserFilAssistance({ messages: [demandeDeRecrutement()] });
    rendreAvecEtat(<PageChat />);
    const carte = await carteChargee();

    // Replié par défaut : la face reste courte (parti pris 5 de la veille).
    expect(champ(carte)).toBeNull();
    const geste = within(carte).getByRole("button", { name: "Corriger avec vos mots" });
    expect(geste).toHaveAttribute("aria-expanded", "false");

    await userEvent.click(geste);

    const saisie = champ(carte);
    expect(saisie).not.toBeNull();
    // Le geste donne la main au champ : on tape tout de suite.
    expect(saisie).toHaveFocus();
    const replier = within(carte).getByRole("button", { name: "Replier la correction" });
    expect(replier).toHaveAttribute("aria-expanded", "true");

    await userEvent.click(replier);

    expect(champ(carte)).toBeNull();
  });

  it("« ajoute quelqu'un pour la sécurité » ajoute un rôle retenu et signalé, sans rien créer", async () => {
    corrigerEquipe.mockResolvedValue(
      correction({
        ajouts: [SECURITE],
        reponse: "J'ajoute un rôle Sécurité applicative, à côté du développeur.",
      }),
    );
    const recruter = vi.fn(async () => {});
    poserFilAssistance({ messages: [demandeDeRecrutement()], recruter });
    rendreAvecEtat(<PageChat />);
    const carte = await carteChargee();

    await demander(carte, "ajoute quelqu'un pour la sécurité");

    // La réponse de Maestro se lit, annoncée, à l'endroit du geste.
    expect(await within(carte).findByRole("status")).toHaveTextContent(
      "J'ajoute un rôle Sécurité applicative",
    );
    // Le récapitulatif compte ce qui sera créé, sans rien ouvrir — et nomme le
    // cran `auto` que le rôle ajouté apporte (#716).
    expect(within(carte).getByText(/3 agents/)).toBeInTheDocument();
    expect(within(carte).getByText(/Développeur · QA · Sécurité applicative/)).toBeInTheDocument();
    expect(within(carte).getByText("Bash(pip-audit *)")).toBeInTheDocument();
    expect(within(carte).getByRole("button", { name: "Créer l'équipe (3)" })).toBeEnabled();
    // Le champ se vide : la demande a été prise en compte.
    expect(champ(carte)).toHaveValue("");

    // Dans le détail, une ligne de plus, retenue, signalée par un mot.
    await userEvent.click(within(carte).getByRole("button", { name: "Voir l'équipe" }));
    const ligne = within(carte).getByText("Sécurité applicative").closest("li");
    expect(ligne).not.toBeNull();
    const zone = within(ligne as HTMLElement);
    expect(zone.getByText("ajouté à votre demande")).toBeInTheDocument();
    expect(zone.getByRole("checkbox")).toBeChecked();
    // Un rôle hors gabarit ne se prétend descendant d'aucun.
    expect(zone.queryByText(/^gabarit/)).toBeNull();

    // Rien n'est créé : seule la validation écrit.
    expect(recruter).not.toHaveBeenCalled();
  });

  it("le rôle ajouté repart à la validation, playbook compris", async () => {
    corrigerEquipe.mockResolvedValue(correction({ ajouts: [SECURITE], reponse: "Ajouté." }));
    const recruter = vi.fn(async () => {});
    poserFilAssistance({ messages: [demandeDeRecrutement()], recruter });
    rendreAvecEtat(<PageChat />);
    const carte = await carteChargee();
    await demander(carte, "ajoute quelqu'un pour la sécurité");
    await within(carte).findByRole("button", { name: "Créer l'équipe (3)" });

    await userEvent.click(within(carte).getByRole("button", { name: "Créer l'équipe (3)" }));

    await waitFor(() => expect(recruter).toHaveBeenCalledTimes(1));
    const [, roles] = recruter.mock.calls[0] as unknown as [
      boolean,
      { nom: string; playbook: string; politique: unknown }[],
    ];
    const securite = roles.find((r) => r.nom === "securite");
    expect(securite?.playbook).toContain("Tu audites les dépendances de Dépensio.");
    expect(securite?.politique).toEqual(SECURITE.politique);
  });

  it("« retire le designer » le décoche, « deux développeurs » change les instances", async () => {
    proposerEquipe.mockResolvedValue(AVEC_DESIGNER);
    corrigerEquipe
      .mockResolvedValueOnce(
        correction({ retraits: ["interface"], reponse: "Je retire le designer." }),
      )
      .mockResolvedValueOnce(
        correction({ instances: { dev: 2 }, reponse: "Deux développeurs, donc." }),
      );
    const recruter = vi.fn(async () => {});
    poserFilAssistance({ messages: [demandeDeRecrutement()], recruter });
    rendreAvecEtat(<PageChat />);
    const carte = await carteChargee();
    expect(within(carte).getByText(/3 agents/)).toBeInTheDocument();

    await demander(carte, "retire le designer");
    await within(carte).findByText(/Je retire le designer/);
    await demander(carte, "deux développeurs");
    await within(carte).findByText(/Deux développeurs, donc/);

    expect(within(carte).getByText(/Développeur ×2 · QA/)).toBeInTheDocument();
    await userEvent.click(within(carte).getByRole("button", { name: "Voir l'équipe" }));
    expect(within(carte).getByRole("checkbox", { name: /Designer/ })).not.toBeChecked();
    expect(within(carte).getAllByRole("spinbutton", { name: "Instances" })[0]).toHaveValue(2);

    // La seconde demande est partie sur l'équipe **déjà** corrigée par la première.
    expect(corrigerEquipe.mock.calls[1][2]).toEqual([
      { nom: "dev", role: "Développeur", retenu: true, instances: 1 },
      { nom: "qa", role: "QA", retenu: true, instances: 1 },
      { nom: "interface", role: "Designer", retenu: false, instances: 1 },
    ]);

    await userEvent.click(within(carte).getByRole("button", { name: "Créer l'équipe (3)" }));
    await waitFor(() => expect(recruter).toHaveBeenCalledTimes(1));
    const [, roles] = recruter.mock.calls[0] as unknown as [
      boolean,
      { nom: string; instances: number }[],
    ];
    expect(roles.map((r) => [r.nom, r.instances])).toEqual([
      ["dev", 2],
      ["qa", 1],
    ]);
  });

  it("la demande porte l'équipe montrée, sur le projet de la demande", async () => {
    corrigerEquipe.mockResolvedValue(correction({ reponse: "Rien à changer." }));
    poserFilAssistance({ messages: [demandeDeRecrutement("prj-autre0001")] });
    rendreAvecEtat(<PageChat />);
    const carte = await carteChargee();
    await userEvent.click(within(carte).getByRole("button", { name: "Voir l'équipe" }));
    await userEvent.click(within(carte).getByRole("checkbox", { name: /QA/ }));
    const instances = within(carte).getAllByRole("spinbutton", { name: "Instances" })[0];
    await userEvent.clear(instances);
    await userEvent.type(instances, "3");

    await demander(carte, "remets les tests");

    await waitFor(() => expect(corrigerEquipe).toHaveBeenCalledTimes(1));
    const [projet, texte, equipe] = corrigerEquipe.mock.calls[0];
    expect(projet).toBe("prj-autre0001");
    expect(texte).toBe("remets les tests");
    expect(equipe).toEqual([
      { nom: "dev", role: "Développeur", retenu: true, instances: 3 },
      { nom: "qa", role: "QA", retenu: false, instances: 1 },
    ]);
  });

  it("rien ne se crée tant qu'une correction est en vol", async () => {
    let repondre: (valeur: CorrectionEquipe) => void = () => {};
    corrigerEquipe.mockReturnValue(
      new Promise<CorrectionEquipe>((resolve) => {
        repondre = resolve;
      }),
    );
    poserFilAssistance({ messages: [demandeDeRecrutement()] });
    rendreAvecEtat(<PageChat />);
    const carte = await carteChargee();

    await demander(carte, "ajoute quelqu'un pour la sécurité");

    expect(within(carte).getByRole("button", { name: /Créer l'équipe/ })).toBeDisabled();
    expect(within(carte).getByRole("button", { name: "Composition…" })).toHaveAttribute(
      "aria-busy",
      "true",
    );

    repondre(correction({ ajouts: [SECURITE], reponse: "Ajouté." }));

    expect(
      await within(carte).findByRole("button", { name: "Créer l'équipe (3)" }),
    ).toBeEnabled();
  });

  it("une demande incomprise le dit, et ne perd ni l'équipe ni ce qui a été tapé", async () => {
    corrigerEquipe.mockResolvedValue(
      correction({ reponse: "Je n'ai pas compris quel rôle vous voulez changer." }),
    );
    poserFilAssistance({ messages: [demandeDeRecrutement()] });
    rendreAvecEtat(<PageChat />);
    const carte = await carteChargee();

    await demander(carte, "fais mieux");

    expect(await within(carte).findByRole("status")).toHaveTextContent(
      "Je n'ai pas compris quel rôle vous voulez changer.",
    );
    expect(champ(carte)).toHaveValue("fais mieux");
    expect(within(carte).getByText(/2 agents/)).toBeInTheDocument();
    expect(within(carte).getByRole("button", { name: "Créer l'équipe (2)" })).toBeEnabled();
  });

  it("un modèle en panne se dit à l'endroit du geste, et l'équipe reste intacte", async () => {
    corrigerEquipe.mockRejectedValue(
      new Error("la demande n'a pas pu être comprise : quota épuisé"),
    );
    poserFilAssistance({ messages: [demandeDeRecrutement()] });
    rendreAvecEtat(<PageChat />);
    const carte = await carteChargee();

    await demander(carte, "ajoute quelqu'un pour la sécurité");

    const alerte = await within(carte).findByRole("alert");
    expect(alerte).toHaveTextContent("Demande non traitée");
    expect(alerte).toHaveTextContent("quota épuisé");
    expect(within(carte).queryByText(/Sécurité applicative/)).toBeNull();
    expect(within(carte).getByText(/2 agents/)).toBeInTheDocument();
    expect(champ(carte)).toHaveValue("ajoute quelqu'un pour la sécurité");
    // Et la demande se rejoue d'un clic.
    expect(within(carte).getByRole("button", { name: "Ajouter ou corriger" })).toBeEnabled();
  });

  it("une demande vide ne part pas", async () => {
    poserFilAssistance({ messages: [demandeDeRecrutement()] });
    rendreAvecEtat(<PageChat />);
    const carte = await carteChargee();

    await userEvent.click(within(carte).getByRole("button", { name: "Corriger avec vos mots" }));

    expect(within(carte).getByRole("button", { name: "Ajouter ou corriger" })).toBeDisabled();
  });

  it("l'équipe corrigée et le texte en cours survivent à une navigation", async () => {
    corrigerEquipe.mockResolvedValue(
      correction({ ajouts: [SECURITE], reponse: "J'ajoute un rôle Sécurité applicative." }),
    );
    poserFilAssistance({ messages: [demandeDeRecrutement()] });
    const premier = rendreAvecEtat(<PageChat />);
    let carte = await carteChargee();
    await demander(carte, "ajoute quelqu'un pour la sécurité");
    await within(carte).findByText(/3 agents/);
    await userEvent.type(champ(carte) as HTMLElement, "et un testeur");
    premier.unmount();

    rendreAvecEtat(<PageChat />);

    carte = await carteAttendue();
    expect(await within(carte).findByText(/3 agents/)).toBeInTheDocument();
    expect(within(carte).getByRole("status")).toHaveTextContent(
      "J'ajoute un rôle Sécurité applicative.",
    );
    expect(champ(carte)).toHaveValue("et un testeur");
    // Rien n'a été redemandé : ni la proposition, ni la correction.
    expect(proposerEquipe).toHaveBeenCalledTimes(1);
    expect(corrigerEquipe).toHaveBeenCalledTimes(1);
  });

  it("une équipe vide se complète avec ses mots, au lieu de renvoyer ailleurs", async () => {
    proposerEquipe.mockResolvedValue({ ...PROPOSITION, roles: [], instances_total: 0 });
    corrigerEquipe.mockResolvedValue(
      correction({ ajouts: [SECURITE], reponse: "J'ajoute un rôle Sécurité applicative." }),
    );
    poserFilAssistance({ messages: [demandeDeRecrutement()] });
    rendreAvecEtat(<PageChat />);
    const carte = await carteAttendue();

    expect(
      await within(carte).findByText(/ne propose aucun rôle\. Dites qui il vous faut/),
    ).toBeInTheDocument();
    expect(within(carte).queryByRole("button", { name: /Créer l'équipe/ })).toBeNull();

    await demander(carte, "ajoute quelqu'un pour la sécurité");

    expect(
      await within(carte).findByRole("button", { name: "Créer l'équipe (1)" }),
    ).toBeEnabled();
    expect(within(carte).getByText(/1 agent/)).toBeInTheDocument();
    expect(corrigerEquipe.mock.calls[0][2]).toEqual([]);
  });

  it("le repli des rôles écartés renvoie au geste qui les ajoute", async () => {
    poserFilAssistance({ messages: [demandeDeRecrutement()] });
    rendreAvecEtat(<PageChat />);
    const carte = await carteChargee();

    await userEvent.click(within(carte).getByRole("button", { name: "Voir l'équipe" }));
    await userEvent.click(within(carte).getByText(/rôle écarté, et pourquoi/));

    expect(
      within(carte).getByText(/dites-le avec « Corriger avec vos mots »/),
    ).toBeInTheDocument();
  });
});

// --- La ligne d'un rôle, et la règle de la correction ------------------------
//
// Reprises de l'étape d'équipe (#1040, #1159), partie avec #1331 : la ligne se
// lit dans le détail de la carte, la règle se garde sans écran.

describe("la ligne d'un rôle", () => {
  it("rend en gras les `**…**` d'une raison d'autorisation", async () => {
    proposerEquipe.mockResolvedValue({
      ...PROPOSITION,
      roles: [
        role({
          autorisations: [
            {
              outil: "Bash(pytest *)",
              cran: "ask",
              decideur: "auto",
              raison: "il exécute **sans vous demander** dans le projet",
            },
          ],
        }),
      ],
    });
    poserFilAssistance({ messages: [demandeDeRecrutement()] });
    rendreAvecEtat(<PageChat />);
    const carte = await carteChargee();

    await userEvent.click(within(carte).getByRole("button", { name: "Voir l'équipe" }));

    expect(within(carte).getByText("sans vous demander").tagName).toBe("STRONG");
    expect(carte.textContent).not.toContain("**");
  });

  it("laisse intact un nombre impair de marqueurs", () => {
    expect(avecGras("un ** seul")).toEqual(["un ** seul"]);
  });
});

describe("appliquerCorrection — la règle, sans écran", () => {
  const montree = {
    roles: PROPOSITION.roles,
    retenus: new Set(["dev"]),
    instances: { dev: 1, qa: 1 },
  };

  it("un ajout déjà montré est ignoré, et rien de changé se dit", () => {
    const apres = appliquerCorrection(
      montree,
      correction({ ajouts: [role({ nom: "dev" })], reponse: "?" }),
    );

    expect(apres.roles).toHaveLength(2);
    expect(apres.ajoutes).toEqual([]);
    expect(apres.change).toBe(false);
  });

  it("un ajout vient en fin de liste, retenu, avec ses instances", () => {
    const apres = appliquerCorrection(
      montree,
      correction({ ajouts: [role({ nom: "securite", instances: 2 })] }),
    );

    expect(apres.roles.map((r) => r.nom)).toEqual(["dev", "qa", "securite"]);
    expect(apres.retenus.has("securite")).toBe(true);
    expect(apres.instances.securite).toBe(2);
    expect(apres.change).toBe(true);
  });

  it("une remise recoche", () => {
    const apres = appliquerCorrection(montree, correction({ remis: ["qa"] }));

    expect(apres.retenus.has("qa")).toBe(true);
  });
});

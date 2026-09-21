/**
 * **La voie du fil écrit l'outillage qu'un projet neuf y a choisi** (#1104 —
 * réserve R7 du bouclage de « L'équipe sur mesure », verdict du 2026-09-21).
 *
 * Le questionnaire d'outillage a deux voies : l'étape du parcours de création
 * (#1034), qui écrit depuis #1100, et **le fil de conversation** (#1031), qui
 * concluait sur « L'outillage recommandé : N entrée(s)… Rien n'est écrit dans le
 * projet tant que vous ne l'avez pas validé » — et **rien ne validait**. Le pied
 * du fil redevenait vide dès que le dernier message ne portait plus de question,
 * si bien que la phrase promettait un geste qui n'existait nulle part.
 *
 * Ce que ce filet garde, c'est la chaîne complète — chaque maillon, lu seul,
 * était déjà correct :
 *
 * ① **le geste existe**, et au bon moment : une fois les réponses données et
 *    plus aucune question en attente, jamais avant ;
 * ② **ce qui voyage est le corps de l'étape de création** — `{retenus, choix}`
 *    sur la même route (#1100). Un geste qui n'enverrait pas les réponses ferait
 *    rederiver l'outillage de l'analyse d'une racine vide, et les skills lus à
 *    l'écran ne seraient pas écrits ;
 * ③ **« plus tard » est une issue nommée**, à égalité — d'après Renovate, et par
 *    le même verbe que l'étape de création, pour qu'un report donné dans le fil
 *    se lise sur la fiche du projet ;
 * ④ **le fil ne redemande pas ce qui est fait** : un projet dont l'outillage est
 *    déjà écrit n'a rien à valider ;
 * ⑤ **un questionnaire interrompu n'est pas un questionnaire conclu** : des
 *    réponses sans question en attente peuvent aussi être un geste dont la suite
 *    n'a pas pu être produite. C'est le moteur qui tranche (`terminee`), pas
 *    l'écran.
 *
 * ⚠ Aucune géométrie ici (#308), aucun jugement de rendu : ce que la carte
 * devient à 320 px est une mesure du banc, et son rendu l'affaire de la
 * relecture visuelle. Ce qui est vérifié ici est **ce qui est monté et ce qui
 * part**.
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import PageChat from "@/app/chat/page";
import { AGENT_ORCHESTRATION } from "@/lib/orchestration";
import type {
  ChoixOutillage,
  EntreeOutillage,
  MessageChat,
  QuestionOutillage,
  RapportGenerationOutillage,
  RecommandationOutillage,
} from "@/lib/types";

import {
  messageFactice,
  pageJournalCourante,
  poserFilAssistance,
  projetFactice,
  projetsDeclares,
  rendreAvecEtat,
} from "./aides";

const questionOutillage = vi.fn();
const recommandationOutillage = vi.fn();
const genererOutillage = vi.fn();
const reporterOutillage = vi.fn();

// Le mock local prend le pas sur celui de `setup.ts` : les lectures qu'il
// remplaçait sont redéclarées ici, faute de quoi elles taperaient le réseau.
vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...reel,
    chargerProjets: () => Promise.resolve(projetsDeclares()),
    chargerJournal: () => Promise.resolve(pageJournalCourante()),
    questionOutillage: (id: string, choix: ChoixOutillage[]) =>
      questionOutillage(id, choix),
    recommandationOutillage: (id: string, choix: ChoixOutillage[]) =>
      recommandationOutillage(id, choix),
    genererOutillage: (
      id: string,
      retenus?: string[],
      choix?: ChoixOutillage[],
    ) => genererOutillage(id, retenus, choix),
    reporterOutillage: (id: string) => reporterOutillage(id),
  };
});

const SKILL_TESTS = ".agents/skills/lancer-les-tests/SKILL.md";

function entree(partiel: Partial<EntreeOutillage> = {}): EntreeOutillage {
  return {
    type: "instructions",
    nom: "AGENTS.md",
    chemin: "AGENTS.md",
    etat: "a-generer",
    raison: "le fichier d'instructions que tous les clients lisent",
    justification: null,
    commandes: [],
    ...partiel,
  };
}

const RECOMMANDATION: RecommandationOutillage = {
  entrees: [
    entree(),
    entree({
      type: "skill",
      nom: "Lancer les tests",
      chemin: SKILL_TESTS,
      raison: "vos tests tournent avec pytest",
    }),
  ],
  ecartes: [],
};

/** Les réponses **données** que le fil porte — les déductions viennent du moteur. */
const CHOIX: ChoixOutillage[] = [
  { cle: "nature", valeur: "service", deduit: false, parce_que: "" },
  { cle: "langages", valeur: "python", deduit: false, parce_que: "" },
];

const QUESTION: QuestionOutillage = {
  cle: "tests",
  intitule: "Comment lancez-vous vos tests ?",
  options: [
    { valeur: "pytest", libelle: "pytest", raison: "le standard Python" },
    { valeur: "aucun", libelle: "Aucun", raison: "pas encore de tests" },
  ],
  recommande: "pytest",
  pourquoi: "c'est un service Python",
  rang: 3,
  total: 6,
};

/** Un geste de réponse : le message d'utilisateur qui porte un `choix`. */
function geste(choix: ChoixOutillage): MessageChat {
  return messageFactice({
    agent: AGENT_ORCHESTRATION,
    auteur: "utilisateur",
    contenu: `${choix.cle} → ${choix.valeur}`,
    choix,
  });
}

/** Le message de conclusion : plus aucune question, et le compte annoncé. */
function conclusion(): MessageChat {
  return messageFactice({
    agent: AGENT_ORCHESTRATION,
    auteur: AGENT_ORCHESTRATION,
    contenu:
      "C'est tout ce qu'il me fallait. L'outillage recommandé : 2 entrée(s)…",
  });
}

/** Le fil d'un questionnaire **conclu** : deux gestes, puis la conclusion. */
function filConclu(): MessageChat[] {
  return [...CHOIX.map(geste), conclusion()];
}

function rapportFactice(
  partiel: Partial<RapportGenerationOutillage["rapport"]> = {},
): RapportGenerationOutillage {
  return {
    projet_id: "prj-7f3a1c2b",
    analyse: "",
    regime: "en-place",
    rapport: {
      cible: "D:/projets/depensio",
      manifeste: ".maestro/outillage/manifeste.json",
      refus: "",
      ecrits: ["AGENTS.md", SKILL_TESTS],
      refuses: [],
      ignores: [],
      retires: [],
      ...partiel,
    },
    retenus_inconnus: [],
    // `en-place` : il n'y a pas eu de validation humaine à rapporter — c'est le
    // régime d'un projet non versionné (docs/24 §2.4).
    application: null,
  };
}

/** La carte de conclusion, une fois les deux allers-retours faits. */
function carteAttendue(): Promise<HTMLElement> {
  return screen.findByRole("region", { name: "Outillage à écrire" });
}

beforeEach(() => {
  questionOutillage.mockResolvedValue({
    question: null,
    deductions: [],
    terminee: true,
  });
  recommandationOutillage.mockResolvedValue({
    projet_id: "prj-7f3a1c2b",
    source: {},
    choix: CHOIX,
    recommandation: RECOMMANDATION,
  });
  genererOutillage.mockResolvedValue(rapportFactice());
  reporterOutillage.mockResolvedValue(projetFactice());
});

afterEach(() => {
  vi.clearAllMocks();
});

// ===========================================================================
// ① Le geste existe — et au bon moment
// ===========================================================================

describe("la conclusion du questionnaire, au pied du fil", () => {
  it("offre le geste que la phrase de conclusion promet", async () => {
    poserFilAssistance({ messages: filConclu() });
    rendreAvecEtat(<PageChat />);

    const carte = await carteAttendue();
    const titre = within(carte).getByRole("heading", {
      name: "Écrire l'outillage ?",
    });
    // Le projet visé est **nommé**, et dans sa casse : le fil est transverse, ses
    // messages ne portent aucun projet, et c'est celui de la fenêtre qui recevra
    // l'écriture. Le titre le portait d'abord — mais `EnTeteSection` rend ses
    // titres en capitales (`uppercase`), donc « Dépensio » s'y serait lu
    // « DÉPENSIO », là même où le nom sert à reconnaître son projet (constat du
    // regard neuf). D'où la garde : le nom vit hors du titre, et il y est.
    expect(titre.textContent).not.toContain("Dépensio");
    expect(within(carte).getAllByText(/Dépensio/).length).toBeGreaterThan(0);
    // Le compte se lit sans rien ouvrir, avec le dossier de destination.
    expect(within(carte).getByText(/2 fichier/)).toBeInTheDocument();
    expect(
      within(carte).getByText(/D:\/projets\/depensio/),
    ).toBeInTheDocument();
    expect(
      within(carte).getByRole("button", { name: "Écrire l'outillage (2)" }),
    ).toBeInTheDocument();
  });

  it("ne l'offre pas tant qu'une question attend", async () => {
    // Le premier moment du questionnaire : c'est `QuestionDOutillage` qui tient
    // le pied, et les deux cartes ne cohabitent jamais.
    poserFilAssistance({
      messages: [
        geste(CHOIX[0]),
        messageFactice({
          agent: AGENT_ORCHESTRATION,
          auteur: AGENT_ORCHESTRATION,
          contenu: "Comment lancez-vous vos tests ?",
          question: QUESTION,
        }),
      ],
    });
    rendreAvecEtat(<PageChat />);

    await screen.findByRole("region", { name: "Question d'outillage" });
    expect(
      screen.queryByRole("region", { name: "Outillage à écrire" }),
    ).not.toBeInTheDocument();
    // Rien n'a même été demandé au moteur : le fil porte une question.
    expect(questionOutillage).not.toHaveBeenCalled();
  });

  it("ne l'offre pas sur un fil qui n'a répondu à rien", async () => {
    poserFilAssistance({ messages: [messageFactice()] });
    rendreAvecEtat(<PageChat />);

    await screen.findByRole("region", { name: "Chat global" });
    expect(
      screen.queryByRole("region", { name: "Outillage à écrire" }),
    ).not.toBeInTheDocument();
    expect(questionOutillage).not.toHaveBeenCalled();
  });

  // ⑤ Un questionnaire **interrompu** laisse la même trace qu'un questionnaire
  // conclu — des réponses, aucune question. La différence ne se lit pas dans le
  // fil : c'est le moteur qui la tranche.
  it("ne l'offre pas quand le moteur dit que le questionnaire n'est pas fini", async () => {
    questionOutillage.mockResolvedValue({
      question: QUESTION,
      deductions: [],
      terminee: false,
    });
    poserFilAssistance({ messages: filConclu() });
    rendreAvecEtat(<PageChat />);

    await waitFor(() => expect(questionOutillage).toHaveBeenCalled());
    expect(
      screen.queryByRole("region", { name: "Outillage à écrire" }),
    ).not.toBeInTheDocument();
    // La recommandation n'a même pas été demandée : il n'y a rien à proposer.
    expect(recommandationOutillage).not.toHaveBeenCalled();
  });

  // ④ Ce qui est fait ne se redemande pas — et c'est la **fiche** qui fait foi.
  it("ne l'offre pas quand l'outillage du projet est déjà écrit", async () => {
    poserFilAssistance({ messages: filConclu() });
    rendreAvecEtat(
      <PageChat />,
      {},
      projetFactice({
        outillage: { reporte_le: "", genere: true, a_faire: false },
      }),
    );

    await screen.findByRole("region", { name: "Chat global" });
    expect(
      screen.queryByRole("region", { name: "Outillage à écrire" }),
    ).not.toBeInTheDocument();
    expect(questionOutillage).not.toHaveBeenCalled();
  });
});

// ===========================================================================
// ② Ce qui voyage : le corps de l'étape de création
// ===========================================================================

describe("ce que le geste envoie à la génération", () => {
  it("envoie les chemins retenus ET les réponses du fil", async () => {
    poserFilAssistance({ messages: filConclu() });
    rendreAvecEtat(<PageChat />);

    const carte = await carteAttendue();
    await userEvent.click(
      within(carte).getByRole("button", { name: "Écrire l'outillage (2)" }),
    );

    await waitFor(() => expect(genererOutillage).toHaveBeenCalledTimes(1));
    // Le cœur du ticket : sans `choix`, le serveur rederiverait l'outillage de
    // l'analyse d'une racine vide et n'écrirait aucun des skills lus à l'écran.
    expect(genererOutillage).toHaveBeenCalledWith(
      "prj-7f3a1c2b",
      ["AGENTS.md", SKILL_TESTS],
      CHOIX,
    );
  });

  it("n'envoie que ce qui est resté coché", async () => {
    poserFilAssistance({ messages: filConclu() });
    rendreAvecEtat(<PageChat />);

    const carte = await carteAttendue();
    await userEvent.click(
      within(carte).getByRole("button", { name: "Ce qui sera écrit" }),
    );
    await userEvent.click(
      within(carte).getByRole("checkbox", { name: /Lancer les tests/ }),
    );
    // Le compte du bouton suit ce qui reste retenu, sans rien ouvrir de plus.
    await userEvent.click(
      within(carte).getByRole("button", { name: "Écrire l'outillage (1)" }),
    );

    await waitFor(() => expect(genererOutillage).toHaveBeenCalledTimes(1));
    expect(genererOutillage).toHaveBeenCalledWith(
      "prj-7f3a1c2b",
      ["AGENTS.md"],
      CHOIX,
    );
  });

  it("rend compte de ce qui a été écrit, et de ce qui ne l'a pas été", async () => {
    genererOutillage.mockResolvedValue(
      rapportFactice({ ecrits: ["AGENTS.md"], refuses: [SKILL_TESTS] }),
    );
    poserFilAssistance({ messages: filConclu() });
    rendreAvecEtat(<PageChat />);

    const carte = await carteAttendue();
    await userEvent.click(
      within(carte).getByRole("button", { name: "Écrire l'outillage (2)" }),
    );

    expect(await within(carte).findByText(/1 fichier écrit/)).toBeInTheDocument();
    // Taire un fichier non écrasé ferait passer une non-écriture pour une
    // écriture — c'est la liste qu'une personne doit relire.
    expect(
      within(carte).getByText(/1 fichier non écrasé/),
    ).toBeInTheDocument();
    // Le geste ne se repropose pas derrière son propre rapport.
    expect(
      within(carte).queryByRole("button", { name: /Écrire l'outillage/ }),
    ).not.toBeInTheDocument();
  });
});

// ===========================================================================
// ③ « Plus tard » — une issue nommée, à égalité
// ===========================================================================

describe("le report depuis le fil", () => {
  it("passe par le même verbe que l'étape de création", async () => {
    poserFilAssistance({ messages: filConclu() });
    rendreAvecEtat(<PageChat />);

    const carte = await carteAttendue();
    await userEvent.click(
      within(carte).getByRole("button", { name: "Plus tard" }),
    );

    await waitFor(() => expect(reporterOutillage).toHaveBeenCalledWith("prj-7f3a1c2b"));
    expect(genererOutillage).not.toHaveBeenCalled();
    expect(await within(carte).findByText(/Noté/)).toBeInTheDocument();
  });
});

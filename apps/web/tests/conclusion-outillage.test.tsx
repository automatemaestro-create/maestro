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
 *    n'a pas pu être produite. Depuis #1147 la différence se **lit** sur le fil —
 *    la conclusion porte ce que Maestro a compris, l'interruption non — et le
 *    moteur n'est plus interrogé : il comprendrait une seconde fois, et pourrait
 *    comprendre autre chose que ce que la personne a lu.
 * ⑥ **ce qui part est ce qui a été compris** (#1147) : les réponses données et la
 *    compréhension que porte la conclusion, jamais une seconde lecture.
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

/** Les réponses **données** que le fil porte — la première tapée, la seconde cliquée. */
const CHOIX: ChoixOutillage[] = [
  {
    cle: "nature",
    valeur: "Un service de réservation en Python",
    deduit: false,
    parce_que: "",
    libre: true,
  },
  { cle: "forge", valeur: "github", deduit: false, parce_que: "", libre: false },
];

/** Ce que Maestro en a compris — porté par la conclusion (#1147). */
const COMPRIS: ChoixOutillage[] = [
  { cle: "langages", valeur: "Python", deduit: true, parce_que: "vous l'avez dit", sujet: "langage" },
  { cle: "tester", valeur: "pytest", deduit: true, parce_que: "le standard Python", sujet: "tests" },
];

const QUESTION: QuestionOutillage = {
  cle: "ci",
  intitule: "Qu'est-ce qui vérifiera le code ?",
  options: [
    { valeur: ".github/workflows/ci.yml", libelle: "GitHub Actions", raison: "sur chaque PR" },
    { valeur: "aucun", libelle: "Rien", raison: "pas encore de CI" },
  ],
  recommande: ".github/workflows/ci.yml",
  pourquoi: "le code vivra sur GitHub",
  rang: 3,
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

/** Le message de conclusion : plus aucune question, ce qui a été compris, le compte. */
function conclusion(): MessageChat {
  return messageFactice({
    agent: AGENT_ORCHESTRATION,
    auteur: AGENT_ORCHESTRATION,
    contenu:
      "C'est tout ce qu'il me fallait. L'outillage recommandé : 2 entrée(s)…",
    comprehension: COMPRIS,
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

  // ⑤ Un questionnaire **interrompu** — le geste est au fil, sa suite n'a pas pu
  // être produite (502) — n'a pas de conclusion : la dernière compréhension est
  // celle d'une question. La différence se lit sur le fil (#1147), et le moteur
  // n'est pas rappelé pour la trancher.
  it("ne l'offre pas quand le questionnaire a été interrompu", async () => {
    poserFilAssistance({
      messages: [
        geste(CHOIX[0]),
        messageFactice({
          agent: AGENT_ORCHESTRATION,
          auteur: AGENT_ORCHESTRATION,
          contenu: "Qu'est-ce qui vérifiera le code ?",
          question: QUESTION,
          comprehension: COMPRIS,
        }),
        geste(CHOIX[1]),
      ],
    });
    rendreAvecEtat(<PageChat />);

    await screen.findByRole("region", { name: "Chat global" });
    expect(
      screen.queryByRole("region", { name: "Outillage à écrire" }),
    ).not.toBeInTheDocument();
    expect(questionOutillage).not.toHaveBeenCalled();
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
    // Depuis #1147 ils portent aussi ce qui a été compris — la conclusion le
    // tient —, et c'est exactement ce que la recommandation a reçu.
    expect(genererOutillage).toHaveBeenCalledWith(
      "prj-7f3a1c2b",
      ["AGENTS.md", SKILL_TESTS],
      [...CHOIX, ...COMPRIS],
    );
    expect(recommandationOutillage).toHaveBeenCalledWith("prj-7f3a1c2b", [
      ...CHOIX,
      ...COMPRIS,
    ]);
    expect(questionOutillage).not.toHaveBeenCalled();
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
      [...CHOIX, ...COMPRIS],
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

  it("dit le verdict des commandes écrites : le compte d'abord, le détail à la demande (#1160)", async () => {
    genererOutillage.mockResolvedValue(
      rapportFactice({
        verifications: [
          {
            usage: "installer",
            commande: "uv sync",
            etat: "verifiee",
            raison: "elle a rendu la main sans erreur",
            code: 0,
            sortie: "Resolved 12 packages",
            duree_s: 3.1,
          },
          {
            usage: "tester",
            commande: "pytest",
            etat: "echouee",
            raison: "elle a rendu la main en erreur (code 1)",
            code: 1,
            sortie: "2 failed, 10 passed",
            duree_s: 8.4,
          },
        ],
      }),
    );
    poserFilAssistance({ messages: filConclu() });
    rendreAvecEtat(<PageChat />);

    const carte = await carteAttendue();
    await userEvent.click(
      within(carte).getByRole("button", { name: "Écrire l'outillage (2)" }),
    );

    // Le récapitulatif se lit sans rien ouvrir — l'échec compris (#1104).
    const controle = await within(carte).findByRole("button", {
      name: "2 commandes",
    });
    expect(within(carte).getByText("1 vérifiée")).toBeInTheDocument();
    expect(within(carte).getByText("1 échouée")).toBeInTheDocument();
    expect(within(carte).queryByText("2 failed, 10 passed")).toBeNull();
    expect(controle).toHaveAttribute("aria-expanded", "false");

    await userEvent.click(controle);

    // Déplié, c'est la liste retenue pour la page — la même, pas une seconde.
    expect(controle).toHaveAttribute("aria-expanded", "true");
    const liste = within(carte).getByRole("list", {
      name: "Verdict de chaque commande",
    });
    expect(within(liste).getByText("code 1")).toBeInTheDocument();
    expect(within(liste).getByText("2 failed, 10 passed")).toBeInTheDocument();
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

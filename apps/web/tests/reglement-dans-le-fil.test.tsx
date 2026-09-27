/**
 * **Régler une attente depuis le fil** (#1183) — la carte qui propose de répondre à un
 * agent ou de trancher une validation, la ligne qui dit ce qui a repris, et ce qui
 * attend au pied du fil.
 *
 * Ce que ce filet garde, et qui se casserait sans rien faire échouer ailleurs :
 *
 * ① **la carte nomme ce qui attend et montre ce qui partira** — la question de l'agent
 *    ou l'acte de la validation (dans l'ordre de son écran : « Appel de » et l'outil),
 *    la réponse que l'agent lira, la raison d'un refus, et ce qui va se passer dans les
 *    mots du service ; « Refuser » en ton d'alerte ;
 * ② **le geste règle, et ce qu'il règle est la carte** — confirmer appelle
 *    `trancherReglement(true)`, écarter `trancherReglement(false)`, rien d'autre ne
 *    part ; un refus de l'API se lit sur la carte ;
 * ③ **la trace dit ce qui a repris**, ou le refus du service ; les attentes d'une
 *    demande ambiguë se lisent sans bouton ;
 * ④ **tout se règle sans changer d'écran** — au pied du fil de chaque écran : la
 *    carte, les validations en attente dans leur carte (refus motivé compris), et la
 *    question ou la validation que la carte vise, sortie de sa pile le temps qu'elle
 *    attende.
 *
 * ⚠ Aucune géométrie ici (#308) : le rendu est l'affaire de la relecture visuelle.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  AttentesCandidates,
  ReglementDansLeFil,
  TraceDuReglement,
} from "@/components/chat/ReglementDansLeFil";
import { Shell } from "@/components/Shell";
import { marquerGuideVu } from "@/lib/guide";
import { AGENT_ORCHESTRATION } from "@/lib/orchestration";
import { ecrireConversationOuverte } from "@/lib/preferences";
import { enPhrase, reglementEnAttente } from "@/lib/reglements";
import type {
  AttenteVisee,
  MessageChat,
  ReglementFait,
  ReglementPropose,
} from "@/lib/types";
import { validationsDuFil } from "@/lib/validations";

import {
  messageFactice,
  poserChemin,
  poserEtatGlobal,
  poserFilAssistance,
  poserProjetActif,
  questionFactice,
  validationFactice,
} from "./aides";

const QUESTION = "Postgres ou SQLite pour la démo ?";
const REPONSE = "Prends Postgres : la démo tourne déjà sur le compose.";
const RAISON = "archive plutôt les fichiers au lieu de les supprimer";
const SUITE_REPONSE = "l'agent attendait cette réponse : il la lit et reprend sa tâche";
const SUITE_REFUS = "l'appel est écarté : l'agent poursuit sa tâche sans lui";

function question(surcharges: Partial<AttenteVisee> = {}): AttenteVisee {
  return {
    genre: "question",
    identifiant: "t2:9f1c0a4bd3",
    agent: "bdd",
    role: "Base de données",
    titre: "Rédiger le schéma de données",
    objet: QUESTION,
    run_id: "run-1",
    outil: "",
    hypothese: "je pars sur SQLite",
    echeance: "",
    ...surcharges,
  };
}

function acte(surcharges: Partial<AttenteVisee> = {}): AttenteVisee {
  return {
    genre: "validation",
    identifiant: "nettoyer",
    agent: "dev",
    role: "Développeur",
    titre: "Nettoyer le dossier de build",
    objet: "Bash command=rm -rf build",
    run_id: "run-1",
    outil: "Bash",
    hypothese: "",
    echeance: "",
    ...surcharges,
  };
}

function carte(surcharges: Partial<ReglementPropose> = {}): ReglementPropose {
  return {
    action: "reponse",
    attente: question(),
    texte: REPONSE,
    suite: SUITE_REPONSE,
    ...surcharges,
  };
}

function fait(surcharges: Partial<ReglementFait> = {}): ReglementFait {
  return {
    action: "reponse",
    attente: question(),
    texte: REPONSE,
    suite: SUITE_REPONSE,
    refus: "",
    ...surcharges,
  };
}

// ===========================================================================
// ① La carte : ce qui attend, ce qui partira, ce qui va se passer
// ===========================================================================

describe("la carte d'un règlement", () => {
  it("nomme la question de l'agent, montre la réponse qui partira et ce qui va se passer", () => {
    render(<ReglementDansLeFil demande={carte()} trancher={vi.fn(async () => {})} />);

    const section = screen.getByRole("region", { name: "Règlement d'une attente" });
    expect(
      within(section).getByRole("heading", { name: "Envoyer cette réponse ?" }),
    ).toBeInTheDocument();
    // Ce qui attend, et qui le porte.
    expect(within(section).getByText(QUESTION)).toBeInTheDocument();
    expect(
      within(section).getByText("Agent bdd · Base de données · Rédiger le schéma de données"),
    ).toBeInTheDocument();
    // Ce qui partira, tel quel.
    expect(
      within(section).getByRole("blockquote", { name: "Réponse transmise à l'agent" }),
    ).toHaveTextContent(REPONSE);
    // Ce qui va se passer, dans les mots du service.
    expect(within(section).getByText(enPhrase(SUITE_REPONSE))).toBeInTheDocument();
    expect(
      within(section).getByRole("button", { name: "Envoyer la réponse" }).className,
    ).toContain("bg-accent");
    expect(within(section).getByRole("button", { name: "Pas maintenant" })).toBeInTheDocument();
    // Aucun champ : une correction se dit dans le composeur.
    expect(within(section).queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("nomme l'acte d'une validation dans l'ordre de son écran, et le refus avec sa raison", () => {
    render(
      <ReglementDansLeFil
        demande={carte({ action: "refus", attente: acte(), texte: RAISON, suite: SUITE_REFUS })}
        trancher={vi.fn(async () => {})}
      />,
    );

    expect(screen.getByRole("heading", { name: "Refuser cette demande ?" })).toBeInTheDocument();
    // « Appel de » et l'outil — jamais le titre de la tâche au-dessus d'un `rm -rf`.
    expect(screen.getByText("Appel de")).toBeInTheDocument();
    expect(screen.getByText("Bash command=rm -rf build")).toBeInTheDocument();
    expect(screen.getByText(RAISON)).toBeInTheDocument();
    expect(screen.getByText(/Raison transmise/)).toBeInTheDocument();
    expect(screen.getByText(enPhrase(SUITE_REFUS))).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Refuser" }).className).toContain("bg-alerte");
  });

  it("dit un refus sans raison, et approuve une demande sans acte par son objet", () => {
    const { rerender } = render(
      <ReglementDansLeFil
        demande={carte({ action: "refus", attente: acte(), texte: "", suite: "" })}
        trancher={vi.fn(async () => {})}
      />,
    );
    expect(screen.getByText("Sans raison donnée.")).toBeInTheDocument();

    rerender(
      <ReglementDansLeFil
        demande={carte({
          action: "approbation",
          attente: acte({ outil: "", objet: "Appliquer le travail dans le projet" }),
          texte: "",
          suite: "le travail s'écrit dans le projet",
        })}
        trancher={vi.fn(async () => {})}
      />,
    );
    expect(screen.getByRole("heading", { name: "Approuver cette demande ?" })).toBeInTheDocument();
    expect(screen.getByText("Appliquer le travail dans le projet")).toBeInTheDocument();
    expect(screen.queryByText("Appel de")).not.toBeInTheDocument();
    expect(screen.getByText("Le travail s'écrit dans le projet.")).toBeInTheDocument();
  });

  it("ne rend rien pour une action qu'il ne connaît pas", () => {
    const { container } = render(
      <ReglementDansLeFil demande={carte({ action: "ignorer" })} trancher={vi.fn(async () => {})} />,
    );

    expect(container).toBeEmptyDOMElement();
  });
});

// ===========================================================================
// ② Le geste règle — et ce qu'il règle est la carte
// ===========================================================================

describe("le geste de la carte", () => {
  it("confirme d'un clic, et rien d'autre ne part", async () => {
    const trancher = vi.fn(async () => {});
    render(<ReglementDansLeFil demande={carte()} trancher={trancher} />);

    await userEvent.click(screen.getByRole("button", { name: "Envoyer la réponse" }));

    expect(trancher).toHaveBeenCalledExactlyOnceWith(true);
  });

  it("écarte d'un clic", async () => {
    const trancher = vi.fn(async () => {});
    render(<ReglementDansLeFil demande={carte()} trancher={trancher} />);

    await userEvent.click(screen.getByRole("button", { name: "Pas maintenant" }));

    expect(trancher).toHaveBeenCalledExactlyOnceWith(false);
  });

  it("dit le refus de l'API sur la carte", async () => {
    const trancher = vi.fn(async () => {
      throw new Error("cette carte n'attend plus de réponse — la conversation a repris.");
    });
    render(<ReglementDansLeFil demande={carte()} trancher={trancher} />);

    await userEvent.click(screen.getByRole("button", { name: "Envoyer la réponse" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("la conversation a repris");
  });

  it("se désarme pendant qu'un échange est en vol", () => {
    render(<ReglementDansLeFil demande={carte()} trancher={vi.fn(async () => {})} enCours />);

    expect(screen.getByRole("button", { name: "Envoyer la réponse" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Pas maintenant" })).toBeDisabled();
  });
});

// ===========================================================================
// ③ La trace dit ce qui a repris — ou le refus ; les candidates se lisent
// ===========================================================================

describe("la trace d'un règlement confirmé", () => {
  it("dit la réponse transmise et ce qui a repris", () => {
    render(<TraceDuReglement fait={fait()} />);

    expect(screen.getByText("Réponse transmise").className).toContain("text-positif-texte");
    expect(screen.getByText(`· ${SUITE_REPONSE}`)).toBeInTheDocument();
  });

  it("coche un refus à la couleur du texte, jamais au vert d'une réussite", () => {
    render(
      <TraceDuReglement
        fait={fait({ action: "refus", attente: acte(), texte: RAISON, suite: SUITE_REFUS })}
      />,
    );

    const verbe = screen.getByText("Refusée");
    expect(verbe.className).toContain("text-texte");
    expect(verbe.className).not.toContain("text-positif");
    expect(screen.getByText(`· ${SUITE_REFUS}`)).toBeInTheDocument();
  });

  it("dit le refus du service et sa raison, sans coche", () => {
    render(
      <TraceDuReglement
        fait={fait({
          action: "approbation",
          attente: acte(),
          texte: "",
          suite: "",
          refus: "cette demande a déjà été tranchée — refusée.",
        })}
      />,
    );

    expect(
      screen.getByText("Approuver : refusé — cette demande a déjà été tranchée — refusée."),
    ).toBeInTheDocument();
    expect(screen.queryByText("Approuvée")).not.toBeInTheDocument();
  });
});

describe("les attentes d'une demande ambiguë", () => {
  it("les liste à leurs faits, chacune dans son encadré, sans bouton", () => {
    render(
      <AttentesCandidates
        attentes={[question(), question({ identifiant: "api:1", objet: "REST ou GraphQL ?", agent: "api" })]}
      />,
    );

    const liste = screen.getByRole("list", { name: "Attentes possibles" });
    expect(within(liste).getAllByRole("listitem")).toHaveLength(2);
    for (const candidate of within(liste).getAllByRole("listitem")) {
      expect(candidate.className).toContain("border");
    }
    expect(within(liste).getByText("REST ou GraphQL ?")).toBeInTheDocument();
    expect(within(liste).queryByRole("button")).not.toBeInTheDocument();
  });

  it("ne rend rien sans candidate", () => {
    const { container } = render(<AttentesCandidates attentes={[]} />);

    expect(container).toBeEmptyDOMElement();
  });
});

describe("les règles", () => {
  it("le règlement attend sur le dernier message, et lui seul", () => {
    const avecCarte = messageFactice({
      agent: AGENT_ORCHESTRATION,
      auteur: AGENT_ORCHESTRATION,
      reglement: carte(),
    });
    const suite = messageFactice({ contenu: "non merci" });

    expect(reglementEnAttente([avecCarte])).toEqual(carte());
    expect(reglementEnAttente([avecCarte, suite])).toBeNull();
    expect(reglementEnAttente([])).toBeNull();
  });

  it("le fil de l'orchestrateur porte toutes les validations en attente, un aparté les siennes", () => {
    const validations = [
      validationFactice({ tache_id: "a", agent: "dev", horodatage: "2026-09-27T10:00:00Z" }),
      validationFactice({ tache_id: "b", agent: "ops", horodatage: "2026-09-27T09:00:00Z" }),
      validationFactice({ tache_id: "c", agent: "dev", statut: "approuvee" }),
    ];

    expect(
      validationsDuFil(validations, AGENT_ORCHESTRATION, AGENT_ORCHESTRATION).map(
        (v) => v.tache_id,
      ),
    ).toEqual(["b", "a"]);
    expect(validationsDuFil(validations, "dev", AGENT_ORCHESTRATION).map((v) => v.tache_id)).toEqual([
      "a",
    ]);
  });
});

// ===========================================================================
// ④ Dans le fil : tout se règle sans changer d'écran
// ===========================================================================

async function filDeLaColonne(): Promise<HTMLElement> {
  ecrireConversationOuverte(true);
  poserChemin("/runs");
  render(
    <Shell>
      <p>un écran quelconque</p>
    </Shell>,
  );
  await screen.findByRole("heading", { level: 1 });
  const colonne = await screen.findByRole("complementary", { name: "Conversation" });
  return within(colonne).getByRole("region", { name: "Conversation" });
}

beforeEach(() => {
  marquerGuideVu();
  poserProjetActif();
  ecrireConversationOuverte(true);
});

describe("dans le fil", () => {
  it("porte la carte au pied du fil de chaque écran, et confirme d'ici", async () => {
    const trancherReglement = vi.fn(async () => {});
    const proposee: MessageChat = messageFactice({
      agent: AGENT_ORCHESTRATION,
      auteur: AGENT_ORCHESTRATION,
      contenu: "L'agent bdd demande quelle base utiliser : je lui réponds Postgres ?",
      reglement: carte(),
    });
    poserFilAssistance({ messages: [proposee], trancherReglement });
    const fil = await filDeLaColonne();

    const section = within(fil).getByRole("region", { name: "Règlement d'une attente" });
    await userEvent.click(within(section).getByRole("button", { name: "Envoyer la réponse" }));

    await waitFor(() => expect(trancherReglement).toHaveBeenCalledWith(true));
  });

  it("sort de sa pile la question que la carte vise, le temps qu'elle attende", async () => {
    // Deux cartes pour une seule réponse feraient répondre deux fois.
    poserEtatGlobal({
      questions: [
        questionFactice({ question_id: "t2:9f1c0a4bd3" }),
        questionFactice({ question_id: "api:1", question: "REST ou GraphQL ?", agent: "api" }),
      ],
    });
    const proposee: MessageChat = messageFactice({
      agent: AGENT_ORCHESTRATION,
      auteur: AGENT_ORCHESTRATION,
      contenu: "Je lui réponds ?",
      reglement: carte(),
    });
    poserFilAssistance({ messages: [proposee] });
    const fil = await filDeLaColonne();

    expect(within(fil).getByRole("region", { name: "Règlement d'une attente" })).toBeInTheDocument();
    expect(within(fil).queryByRole("region", { name: "Question de l'agent bdd" })).toBeNull();
    // L'autre question reste là où elle attend.
    expect(within(fil).getByRole("region", { name: "Question de l'agent api" })).toBeInTheDocument();
  });

  it("montre les validations en attente au pied du fil, et les tranche d'ici — refus motivé compris", async () => {
    const decider = vi.fn(async () => {});
    poserEtatGlobal({
      validations: [
        validationFactice({
          tache_id: "nettoyer",
          titre: "Nettoyer le dossier de build",
          agent: "dev",
          outil: "Bash",
          arguments: { command: "rm -rf build" },
        }),
      ],
      decider,
    });
    poserFilAssistance({ messages: [] });
    const fil = await filDeLaColonne();

    // La carte de l'écran des validations, montée telle quelle : l'acte en tête.
    expect(within(fil).getByText("Bash")).toBeInTheDocument();
    await userEvent.click(within(fil).getByRole("button", { name: "Motiver le refus" }));
    await userEvent.type(
      within(fil).getByRole("textbox", { name: /Motif du refus/ }),
      RAISON,
    );
    await userEvent.click(within(fil).getByRole("button", { name: "Refuser" }));

    await waitFor(() => expect(decider).toHaveBeenCalledWith("nettoyer", false, RAISON));
  });

  it("garde sous la réponse ce qui a repris, et la carte ne revient pas", async () => {
    const reponse: MessageChat = messageFactice({
      agent: AGENT_ORCHESTRATION,
      auteur: AGENT_ORCHESTRATION,
      contenu: "C'est transmis.",
      reglement_fait: fait(),
    });
    poserFilAssistance({ messages: [reponse] });
    const fil = await filDeLaColonne();

    expect(within(fil).getByText("Réponse transmise")).toBeInTheDocument();
    expect(within(fil).getByText(`· ${SUITE_REPONSE}`)).toBeInTheDocument();
    expect(within(fil).queryByRole("region", { name: "Règlement d'une attente" })).toBeNull();
  });

  it("liste sous la question les attentes qu'une demande ambiguë pouvait viser", async () => {
    const reponse: MessageChat = messageFactice({
      agent: AGENT_ORCHESTRATION,
      auteur: AGENT_ORCHESTRATION,
      contenu: "Deux agents attendent. Lequel ?",
      attentes_candidates: [question(), question({ identifiant: "api:1", agent: "api" })],
    });
    poserFilAssistance({ messages: [reponse] });
    const fil = await filDeLaColonne();

    expect(within(fil).getByRole("list", { name: "Attentes possibles" })).toBeInTheDocument();
    expect(within(fil).queryByRole("region", { name: "Règlement d'une attente" })).toBeNull();
  });
});

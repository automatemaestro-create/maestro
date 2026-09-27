/**
 * **Un geste sur un run, dans le fil** (#1179) — la carte qui le propose, la ligne
 * qui dit ce qu'il a donné, et les runs d'une demande ambiguë.
 *
 * Ce que ce filet garde, et qui se casserait sans rien faire échouer ailleurs :
 *
 * ① **la carte pose la question, nomme le run et dit ce qui va se passer** — au verbe
 *    des écrans des runs, avec la phrase qu'ils disent aussi (`lib/gestesRun`), le
 *    badge qu'ils montrent et le renvoi vers la vue du run ; « Interrompre », seul
 *    geste sans retour, en ton d'alerte ; les bornes d'une relance dites dans les
 *    deux sens ;
 * ② **le geste agit, et ce qu'il agit est la carte** — confirmer appelle
 *    `trancherGeste(true)`, écarter `trancherGeste(false)`, et rien d'autre ne part ;
 *    un refus de l'API se lit sur la carte ;
 * ③ **la trace dit l'état relu**, ou le refus du service et sa raison ; les
 *    candidats d'une demande ambiguë se lisent sans bouton ;
 * ④ **la carte vit là où les autres vivent** — au pied du fil, dans la colonne de
 *    chaque écran, et la trace sous la réponse, avec son renvoi.
 *
 * ⚠ Aucune géométrie ici (#308) : le rendu est l'affaire de la relecture visuelle.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  GesteSurUnRun,
  RunsCandidats,
  TraceDuGeste,
} from "@/components/chat/GesteSurUnRun";
import { Shell } from "@/components/Shell";
import { formatHeureCourte } from "@/lib/format";
import {
  gesteRunEnAttente,
  PHRASE_INTERRUPTION,
  PHRASE_PAUSE,
} from "@/lib/gestesRun";
import { marquerGuideVu } from "@/lib/guide";
import { AGENT_ORCHESTRATION } from "@/lib/orchestration";
import { ecrireConversationOuverte } from "@/lib/preferences";
import type {
  GesteRunFait,
  GesteRunPropose,
  MessageChat,
  RunVise,
} from "@/lib/types";

import {
  messageFactice,
  poserChemin,
  poserFilAssistance,
  poserProjetActif,
} from "./aides";

const PAUSE_A = "2026-09-27T12:02:00+00:00";

function run(surcharges: Partial<RunVise> = {}): RunVise {
  return {
    run_id: "a9f4ec9ae5c7",
    titre: "Ajouter trois chants au carnet",
    statut: "en_cours",
    en_pause: false,
    pause_depuis: null,
    etat: "En cours",
    ...surcharges,
  };
}

function carte(
  action: GesteRunPropose["action"] = "pause",
  surcharges: Partial<GesteRunPropose> = {},
): GesteRunPropose {
  return { action, run: run(), bornes: null, ...surcharges };
}

function fait(surcharges: Partial<GesteRunFait> = {}): GesteRunFait {
  return {
    action: "pause",
    run: run({ en_pause: true, pause_depuis: PAUSE_A }),
    nouveau: null,
    refus: "",
    ...surcharges,
  };
}

// ===========================================================================
// ① La carte : la question, le run, ce qui va se passer
// ===========================================================================

describe("la carte d'un geste sur un run", () => {
  it("pose la question au verbe du geste, nomme le run et dit ce qui va se passer", () => {
    render(<GesteSurUnRun demande={carte()} trancher={vi.fn(async () => {})} />);

    const section = screen.getByRole("region", { name: "Geste sur un run" });
    expect(
      within(section).getByRole("heading", { name: "Mettre ce run en pause ?" }),
    ).toBeInTheDocument();
    // Le run à ses faits : le badge des écrans des runs, son titre, son identifiant…
    expect(within(section).getByText("En cours")).toBeInTheDocument();
    expect(within(section).getByText("Ajouter trois chants au carnet")).toBeInTheDocument();
    expect(within(section).getByText("a9f4ec9ae5c7")).toBeInTheDocument();
    // … et le renvoi vers sa vue.
    expect(within(section).getByRole("link", { name: /Voir le run/ })).toHaveAttribute(
      "href",
      "/runs/a9f4ec9ae5c7",
    );
    // La phrase de l'écran d'un run, jamais une seconde formulation.
    expect(within(section).getByText(PHRASE_PAUSE)).toBeInTheDocument();
    expect(within(section).getByRole("button", { name: "Mettre en pause" })).toBeInTheDocument();
    expect(within(section).getByRole("button", { name: "Pas maintenant" })).toBeInTheDocument();
    // Aucun champ : une correction se dit dans le composeur.
    expect(within(section).queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("dit « Interrompre » en ton d'alerte, avec ce que l'interruption coûte", () => {
    render(<GesteSurUnRun demande={carte("annulation")} trancher={vi.fn(async () => {})} />);

    expect(screen.getByRole("heading", { name: "Interrompre ce run ?" })).toBeInTheDocument();
    expect(screen.getByText(PHRASE_INTERRUPTION)).toBeInTheDocument();
    const bouton = screen.getByRole("button", { name: "Interrompre" });
    expect(bouton.className).toContain("bg-alerte");
  });

  it.each([
    ["reprise", "Reprendre ce run ?", "Reprendre"],
    ["relance", "Relancer ce run ?", "Relancer"],
  ])("pose la question de la %s", (action, question, verbe) => {
    render(<GesteSurUnRun demande={carte(action)} trancher={vi.fn(async () => {})} />);

    expect(screen.getByRole("heading", { name: question })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: verbe }).className).toContain("bg-accent");
  });

  it("dit les bornes du nouveau run d'une relance — « aucune » comprise", () => {
    const { rerender } = render(
      <GesteSurUnRun
        demande={carte("relance", {
          bornes: {
            plafond_cout_usd: 15,
            plafond_tokens: null,
            timeout_tache_s: null,
            parallelisme: null,
          },
        })}
        trancher={vi.fn(async () => {})}
      />,
    );
    expect(screen.getByText(/Bornes du nouveau run : s'interrompt à 15,00/)).toBeInTheDocument();

    rerender(<GesteSurUnRun demande={carte("relance")} trancher={vi.fn(async () => {})} />);
    expect(
      screen.getByText("Bornes du nouveau run : Aucune borne — le run ira jusqu'au bout."),
    ).toBeInTheDocument();
  });

  it("ne rend rien pour une action qu'il ne connaît pas", () => {
    const { container } = render(
      <GesteSurUnRun demande={carte("supprimer")} trancher={vi.fn(async () => {})} />,
    );

    expect(container).toBeEmptyDOMElement();
  });

  it("montre le run en pause avec le badge des écrans des runs", () => {
    render(
      <GesteSurUnRun
        demande={carte("reprise", { run: run({ en_pause: true, pause_depuis: PAUSE_A }) })}
        trancher={vi.fn(async () => {})}
      />,
    );

    expect(screen.getByText("En pause")).toBeInTheDocument();
  });
});

// ===========================================================================
// ② Le geste agit — et ce qu'il agit est la carte
// ===========================================================================

describe("le geste de la carte", () => {
  it("confirme d'un clic, et rien d'autre ne part", async () => {
    const trancher = vi.fn(async () => {});
    render(<GesteSurUnRun demande={carte()} trancher={trancher} />);

    await userEvent.click(screen.getByRole("button", { name: "Mettre en pause" }));

    expect(trancher).toHaveBeenCalledExactlyOnceWith(true);
  });

  it("écarte d'un clic", async () => {
    const trancher = vi.fn(async () => {});
    render(<GesteSurUnRun demande={carte()} trancher={trancher} />);

    await userEvent.click(screen.getByRole("button", { name: "Pas maintenant" }));

    expect(trancher).toHaveBeenCalledExactlyOnceWith(false);
  });

  it("dit le refus de l'API sur la carte", async () => {
    const trancher = vi.fn(async () => {
      throw new Error("ce geste n'attend plus de réponse — la conversation a repris.");
    });
    render(<GesteSurUnRun demande={carte()} trancher={trancher} />);

    await userEvent.click(screen.getByRole("button", { name: "Mettre en pause" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("la conversation a repris");
  });

  it("se désarme pendant qu'un échange est en vol", () => {
    render(<GesteSurUnRun demande={carte()} trancher={vi.fn(async () => {})} enCours />);

    expect(screen.getByRole("button", { name: "Mettre en pause" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Pas maintenant" })).toBeDisabled();
  });
});

// ===========================================================================
// ③ La trace dit l'état relu — ou le refus ; les candidats se lisent
// ===========================================================================

describe("la trace d'un geste confirmé", () => {
  it("dit « Mis en pause » et l'heure de la pause, relue", () => {
    render(<TraceDuGeste fait={fait()} />);

    expect(screen.getByText("Mis en pause")).toBeInTheDocument();
    expect(
      screen.getByText(`· en pause depuis ${formatHeureCourte(PAUSE_A)}`),
    ).toBeInTheDocument();
    expect(screen.getByText("a9f4ec9ae5c7")).toBeInTheDocument();
  });

  it("dit le nouveau run d'une relance", () => {
    render(
      <TraceDuGeste
        fait={fait({
          action: "relance",
          run: run({ statut: "annulee" }),
          nouveau: run({ run_id: "b7c0d1e2f3a4" }),
        })}
      />,
    );

    expect(screen.getByText("Relancé")).toBeInTheDocument();
    expect(screen.getByText("· suivi par un nouveau run, en cours")).toBeInTheDocument();
    expect(screen.getByText("b7c0d1e2f3a4")).toBeInTheDocument();
  });

  it("dit le refus du service et sa raison, sans coche", () => {
    render(
      <TraceDuGeste
        fait={fait({
          run: run({ statut: "annulee" }),
          refus: "exécution déjà soldée (annulee) : a9f4ec9ae5c7.",
        })}
      />,
    );

    expect(
      screen.getByText(
        "Mettre en pause : refusé — exécution déjà soldée (annulee) : a9f4ec9ae5c7.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText("Mis en pause")).not.toBeInTheDocument();
  });

  it("dit une interruption sans redire l'état", () => {
    render(
      <TraceDuGeste fait={fait({ action: "annulation", run: run({ statut: "annulee" }) })} />,
    );

    expect(screen.getByText("Interrompu")).toBeInTheDocument();
    expect(screen.queryByText(/Annulée/)).not.toBeInTheDocument();
  });
});

describe("les runs d'une demande ambiguë", () => {
  it("les liste à leurs faits, sans bouton", () => {
    render(
      <RunsCandidats
        runs={[run(), run({ run_id: "3ff0bcb065f9", titre: "Corriger le tri" })]}
      />,
    );

    const liste = screen.getByRole("list", { name: "Runs possibles" });
    expect(within(liste).getAllByRole("listitem")).toHaveLength(2);
    expect(within(liste).getByText("Corriger le tri")).toBeInTheDocument();
    expect(within(liste).getAllByRole("link", { name: /Voir le run/ })).toHaveLength(2);
    expect(within(liste).queryByRole("button")).not.toBeInTheDocument();
  });

  it("ne rend rien sans candidat", () => {
    const { container } = render(<RunsCandidats runs={[]} />);

    expect(container).toBeEmptyDOMElement();
  });
});

describe("la règle d'attente", () => {
  it("vaut le dernier message, et lui seul", () => {
    const avecCarte = messageFactice({
      agent: AGENT_ORCHESTRATION,
      auteur: AGENT_ORCHESTRATION,
      geste_run: carte(),
    });
    const suite = messageFactice({ contenu: "non merci" });

    expect(gesteRunEnAttente([avecCarte])).toEqual(carte());
    expect(gesteRunEnAttente([avecCarte, suite])).toBeNull();
    expect(gesteRunEnAttente([])).toBeNull();
  });
});

// ===========================================================================
// ④ Dans le fil : la carte au pied, la trace sous la réponse
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
    const trancherGeste = vi.fn(async () => {});
    const proposee: MessageChat = messageFactice({
      agent: AGENT_ORCHESTRATION,
      auteur: AGENT_ORCHESTRATION,
      contenu: "Je vous propose de mettre ce run en pause.",
      geste_run: carte(),
    });
    poserFilAssistance({ messages: [proposee], trancherGeste });
    const fil = await filDeLaColonne();

    const section = within(fil).getByRole("region", { name: "Geste sur un run" });
    await userEvent.click(within(section).getByRole("button", { name: "Mettre en pause" }));

    await waitFor(() => expect(trancherGeste).toHaveBeenCalledWith(true));
  });

  it("garde sous la réponse la trace du geste et le renvoi vers le run", async () => {
    const reponse: MessageChat = messageFactice({
      agent: AGENT_ORCHESTRATION,
      auteur: AGENT_ORCHESTRATION,
      contenu: "Le run est en pause.",
      geste_fait: fait(),
    });
    poserFilAssistance({ messages: [reponse] });
    const fil = await filDeLaColonne();

    expect(within(fil).getByText("Mis en pause")).toBeInTheDocument();
    expect(within(fil).getByRole("link", { name: /Voir le run/ })).toHaveAttribute(
      "href",
      "/runs/a9f4ec9ae5c7",
    );
    // La carte ne revient pas : elle a été tranchée.
    expect(within(fil).queryByRole("region", { name: "Geste sur un run" })).toBeNull();
  });

  it("liste sous la question les runs qu'une demande ambiguë pouvait viser", async () => {
    const question: MessageChat = messageFactice({
      agent: AGENT_ORCHESTRATION,
      auteur: AGENT_ORCHESTRATION,
      contenu: "Deux runs tournent. Lequel ?",
      runs_candidats: [run(), run({ run_id: "3ff0bcb065f9", titre: "Corriger le tri" })],
    });
    poserFilAssistance({ messages: [question] });
    const fil = await filDeLaColonne();

    expect(within(fil).getByRole("list", { name: "Runs possibles" })).toBeInTheDocument();
    expect(within(fil).queryByRole("region", { name: "Geste sur un run" })).toBeNull();
  });
});

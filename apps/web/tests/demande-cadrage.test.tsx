/**
 * La demande de cadrage porte son geste, et la surface qui la liste la voit
 * (#943).
 *
 * Constat **G10** du retex du 2026-09-11 : l'orchestration écrit « Je lance ? »
 * dans le fil **sans bouton**, et le panneau « Cadrage en attente » affirme
 * « Aucun cadrage en attente » au moment même où la question est posée. Deux
 * défauts qui se renforcent, et une seule cause — la demande n'existait que
 * dans une phrase, donc aucune surface ne pouvait la voir.
 *
 * Ce qui est gardé ici, dans l'ordre où le défaut se reproduirait :
 *
 * ① **la règle**, et le fait qu'il n'y en ait qu'une (`propositionEnAttente`) :
 *    une demande attend tant que **rien n'a suivi**. Deux formulations de « ce
 *    qui attend » redonneraient deux surfaces qui se contredisent ;
 * ② **le geste** (`DemandeDeCadrage`) : accepter, refuser, amender — et le
 *    contrat que chacun envoie, `null` quand rien n'a été touché ;
 * ③ **le panneau** (`FilDeCadrage`) : il montre la demande, et ne dit « aucun »
 *    que lorsqu'il n'y en a pas ;
 * ④ **l'écran** (`/chat`) : les deux critères vus depuis la page, qui est le
 *    seul endroit où ils se rencontrent.
 *
 * ⚠ Le geste n'est **pas** redonné dans le panneau, et ce n'est pas un oubli :
 * c'est le partage que `PanneauBriefs` tient déjà — il signale et il achemine,
 * il ne décide pas. Le test ④ le vérifie, sans quoi la même question finirait
 * posée deux fois sur le même écran.
 */

import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import PageChat from "@/app/chat/page";
import { DemandeDeCadrage } from "@/components/chat/DemandeDeCadrage";
import { FilDeCadrage } from "@/components/chat/FilDeCadrage";
import { propositionEnAttente } from "@/lib/brief";
import {
  AGENT_ORCHESTRATION,
  ROLE_ORCHESTRATION,
} from "@/lib/orchestration";
import type { MessageChat } from "@/lib/types";

import {
  agentFactice,
  messageFactice,
  poserFilAssistance,
  rendreAvecEtat,
} from "./aides";

const OBJECTIF =
  "Développer une page web autonome de minuteur Pomodoro, exécutable sans build";

/** La réponse de l'orchestration qui **demande** l'accord. */
function demandeFactice(partiel: Partial<MessageChat> = {}): MessageChat {
  return messageFactice({
    agent: AGENT_ORCHESTRATION,
    auteur: AGENT_ORCHESTRATION,
    contenu: `J'ouvrirais un run sur : « ${OBJECTIF} ». Je lance ?`,
    horodatage: "2026-09-11T12:38:00Z",
    proposition: OBJECTIF,
    ...partiel,
  });
}

describe("① la règle — et il n'y en a qu'une", () => {
  it("retient la demande tant que rien ne lui a répondu", () => {
    const demande = demandeFactice();

    expect(propositionEnAttente([demande])).toBe(demande);
    expect(
      propositionEnAttente([messageFactice({ contenu: "bonjour" }), demande]),
    ).toBe(demande);
  });

  it("ne retient rien dès qu'un message a suivi — quel qu'il soit", () => {
    // Ce n'est pas le temps qui périme une demande, c'est qu'on y ait répondu.
    // Sans cette moitié, le geste resterait offert après coup et un second clic
    // ouvrirait un run de plus.
    expect(
      propositionEnAttente([
        demandeFactice(),
        messageFactice({ contenu: "plutôt pas" }),
      ]),
    ).toBeNull();
  });

  it("ne retient rien d'un fil ordinaire, ni d'un fil vide", () => {
    expect(propositionEnAttente([])).toBeNull();
    expect(propositionEnAttente([messageFactice()])).toBeNull();
  });
});

describe("② le geste — accepter, refuser, amender", () => {
  function monterLeGeste(trancher = vi.fn().mockResolvedValue(undefined)) {
    rendreAvecEtat(
      <DemandeDeCadrage demande={demandeFactice()} trancher={trancher} />,
    );
    return trancher;
  }

  it("montre l'objectif proposé, et une décision à prendre", () => {
    monterLeGeste();

    expect(
      screen.getByRole("region", { name: "Décision sur le cadrage" }),
    ).toBeTruthy();
    expect(
      (screen.getByLabelText("Objectif proposé") as HTMLTextAreaElement).value,
    ).toBe(OBJECTIF);
  });

  it("lance **tel quel** tant que rien n'a été touché", async () => {
    const trancher = monterLeGeste();

    await userEvent.click(screen.getByRole("button", { name: "Lancer" }));

    // `null` et non une copie : le corps ne recopie jamais un objectif qu'on
    // n'a pas corrigé — même contrat que `brief: null` (§6.10).
    expect(trancher).toHaveBeenCalledWith(true, null);
  });

  it("lance la version **corrigée** dès que l'objectif change", async () => {
    const trancher = monterLeGeste();

    const champ = screen.getByLabelText("Objectif proposé");
    await userEvent.clear(champ);
    await userEvent.type(champ, "Un minuteur, sans le son");

    const bouton = await screen.findByRole("button", {
      name: "Lancer la version corrigée",
    });
    await userEvent.click(bouton);

    expect(trancher).toHaveBeenLastCalledWith(true, "Un minuteur, sans le son");
  });

  it("refuse sans rien emporter", async () => {
    const trancher = monterLeGeste();

    await userEvent.click(screen.getByRole("button", { name: "Ne pas lancer" }));

    expect(trancher).toHaveBeenCalledWith(false, null);
  });

  it("ferme le lancement sur un objectif vidé, et le dit", async () => {
    const trancher = monterLeGeste();

    await userEvent.clear(screen.getByLabelText("Objectif proposé"));

    expect(
      (screen.getByRole("button", { name: "Lancer" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    expect(screen.getByText(/il n'y a rien à lancer/i)).toBeTruthy();
    expect(trancher).not.toHaveBeenCalled();
  });

  it("garde la demande à l'écran quand la décision est refusée par l'API", async () => {
    const trancher = vi
      .fn()
      .mockRejectedValue(new Error("cette demande de cadrage n'attend plus"));
    monterLeGeste(trancher);

    await userEvent.click(screen.getByRole("button", { name: "Lancer" }));

    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.getByLabelText("Objectif proposé")).toBeTruthy();
  });
});

describe("③ le panneau — il montre la demande, et ne dit « aucun » que s'il n'y en a pas", () => {
  it("affiche la demande au moment où elle est posée", async () => {
    rendreAvecEtat(<FilDeCadrage proposition={demandeFactice()} />, {
      executions: [],
    });

    expect(await screen.findByText(OBJECTIF)).toBeTruthy();
    expect(screen.queryByText(/Aucun cadrage en attente/)).toBeNull();
  });

  it("signale et achemine — il ne redonne pas le geste", async () => {
    // Le partage de `PanneauBriefs`, tenu ici : le bouton vit là où on lit la
    // demande. Deux boutons pour une question, ce serait la poser deux fois.
    rendreAvecEtat(<FilDeCadrage proposition={demandeFactice()} />, {
      executions: [],
    });

    await screen.findByText(OBJECTIF);
    expect(screen.queryByRole("button", { name: /^Lancer/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Ne pas lancer" })).toBeNull();
  });

  it("dit « aucun » quand il n'y a réellement rien", async () => {
    rendreAvecEtat(<FilDeCadrage proposition={null} />, { executions: [] });

    expect(
      await screen.findByText(/Aucun cadrage en attente sur Dépensio/),
    ).toBeTruthy();
  });
});

describe("④ l'écran — les deux critères, là où ils se rencontrent", () => {
  function monterLeChat(messages: MessageChat[]) {
    poserFilAssistance({ messages });
    return rendreAvecEtat(<PageChat />, {
      agents: [agentFactice({ nom: AGENT_ORCHESTRATION, role: ROLE_ORCHESTRATION })],
      executions: [],
    });
  }

  it("porte le geste au pied du fil, sous la question", async () => {
    monterLeChat([messageFactice({ contenu: "un minuteur" }), demandeFactice()]);

    const decision = await screen.findByRole("region", {
      name: "Décision sur le cadrage",
    });
    expect(within(decision).getByRole("button", { name: "Lancer" })).toBeTruthy();
  });

  it("le panneau « Cadrage en attente » cesse de dire « aucun »", async () => {
    monterLeChat([demandeFactice()]);

    const panneau = await screen.findByRole("complementary", {
      name: "Propriétés du fil",
    });
    expect(within(panneau).getByText(OBJECTIF)).toBeTruthy();
    expect(within(panneau).queryByText(/Aucun cadrage en attente/)).toBeNull();
  });

  it("ne propose aucun geste quand le fil n'a rien demandé", () => {
    monterLeChat([messageFactice({ contenu: "bonjour" })]);

    expect(
      screen.queryByRole("region", { name: "Décision sur le cadrage" }),
    ).toBeNull();
  });
});

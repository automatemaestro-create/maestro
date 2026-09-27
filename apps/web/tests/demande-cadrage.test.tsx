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

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import PageChat from "@/app/chat/page";
import { DemandeDeCadrage, SANS_PROJET } from "@/components/chat/DemandeDeCadrage";
import { FilDeCadrage } from "@/components/chat/FilDeCadrage";
import { ParametresCouts } from "@/components/parametres/ParametresCouts";
import { AUCUNE_BORNE, bornesEnLigne, phraseDesBornes } from "@/lib/bornes";
import { propositionEnAttente } from "@/lib/brief";
import { formatCout } from "@/lib/format";
import {
  AGENT_ORCHESTRATION,
  ROLE_ORCHESTRATION,
} from "@/lib/orchestration";
import type { EstimationRun, MessageChat, ProjetVise } from "@/lib/types";

import {
  agentFactice,
  messageFactice,
  poserFilAssistance,
  projetFactice,
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
    // n'a pas corrigé — même contrat que `brief: null` (§6.10). Le troisième
    // argument est le régime des bornes (#990), ici « aucune ».
    expect(trancher).toHaveBeenCalledWith(true, null, AUCUNE_BORNE);
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

    expect(trancher).toHaveBeenLastCalledWith(
      true,
      "Un minuteur, sans le son",
      AUCUNE_BORNE,
    );
  });

  it("refuse sans rien emporter", async () => {
    const trancher = monterLeGeste();

    await userEvent.click(screen.getByRole("button", { name: "Ne pas lancer" }));

    // Ni objectif corrigé, ni bornes : il n'y a pas de run à borner.
    expect(trancher).toHaveBeenCalledWith(false, null, null);
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

/**
 * ⑤ **borner le run, là où on le lance** (#990).
 *
 * Le moteur savait arrêter un run sur quatre garde-fous ; la conversation, seule
 * porte de lancement depuis #666, ne les passait pas — un run mesuré à 12,51 $
 * n'a pas pu l'être (retex du 2026-09-11, G5).
 *
 * La **forme** est un choix rendu sur pièces (commentaire « Variante retenue »
 * de #990) : repliée, le repli portant le récapitulatif, le contrôle à gauche de
 * la ligne. Ce qui se garde ici n'est pas cette apparence mais les trois
 * propriétés dont elle découle, et qu'une refonte devra tenir :
 *
 * - le régime se lit **sans rien ouvrir**, y compris quand il n'y a aucune
 *   borne (critère 3 — l'illimité est un choix affiché) ;
 * - ce qui est saisi **part** avec l'accord (critère 1) ;
 * - Paramètres **dit où** cela se règle (critère 2).
 */
describe("⑤ borner le run, là où on le lance", () => {
  function monterLeGeste(trancher = vi.fn().mockResolvedValue(undefined)) {
    rendreAvecEtat(
      <DemandeDeCadrage demande={demandeFactice()} trancher={trancher} />,
    );
    return trancher;
  }

  async function ouvrirLesBornes() {
    await userEvent.click(screen.getByRole("button", { name: /Bornes du run/ }));
  }

  it("dit le régime sans rien ouvrir, et l'absence de borne est un choix affiché", () => {
    monterLeGeste();

    // Critère 3 : le repli **fermé** porte déjà la réponse. C'est la lecture de
    // Vercel Spend Management, dont la ligne fermée porte l'état.
    expect(screen.getByText(/Aucune borne — le run ira jusqu'au bout/)).toBeTruthy();
    expect(screen.queryByLabelText("Coût maximal")).toBeNull();
  });

  it("offre les quatre garde-fous du moteur, et eux seuls", async () => {
    monterLeGeste();
    await ouvrirLesBornes();

    expect(screen.getByLabelText("Coût maximal")).toBeTruthy();
    expect(screen.getByLabelText("Tokens maximum")).toBeTruthy();
    expect(screen.getByLabelText("Délai par tâche")).toBeTruthy();
    expect(screen.getByLabelText("Tâches en parallèle")).toBeTruthy();
  });

  it("récapitule ce qui part, en disant ce que chaque borne **fait**", async () => {
    monterLeGeste();
    await ouvrirLesBornes();

    await userEvent.type(screen.getByLabelText("Coût maximal"), "5");
    await userEvent.type(screen.getByLabelText("Tâches en parallèle"), "2");

    // « s'interrompt à » et non « plafond » : le parti pris tiré des budgets
    // GitHub, où la borne et son effet sont deux informations.
    expect(await screen.findByText(/s'interrompt à/)).toBeTruthy();
    expect(screen.getByText(/2 tâches à la fois/)).toBeTruthy();
  });

  it("transmet les bornes saisies avec l'accord", async () => {
    const trancher = monterLeGeste();
    await ouvrirLesBornes();

    await userEvent.type(screen.getByLabelText("Coût maximal"), "5");
    await userEvent.type(screen.getByLabelText("Tokens maximum"), "200000");
    await userEvent.type(screen.getByLabelText("Délai par tâche"), "120");
    await userEvent.type(screen.getByLabelText("Tâches en parallèle"), "2");
    await userEvent.click(screen.getByRole("button", { name: "Lancer" }));

    expect(trancher).toHaveBeenCalledWith(true, null, {
      plafond_cout_usd: 5,
      plafond_tokens: 200000,
      timeout_tache_s: 120,
      parallelisme: 2,
    });
  });

  it("refuse de lancer sur une borne illisible, plutôt que de la perdre", async () => {
    // L'échantillon fautif de ce ticket : une borne qu'on ne sait pas lire
    // partirait en `null`, c'est-à-dire en run **sans limite** — exactement le
    // défaut qu'on corrige. On le dit, et on désarme.
    const trancher = monterLeGeste();
    await ouvrirLesBornes();

    await userEvent.type(screen.getByLabelText("Coût maximal"), "beaucoup");

    expect(
      (screen.getByRole("button", { name: "Lancer" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    expect(screen.getByText(/illisible/)).toBeTruthy();
    expect(trancher).not.toHaveBeenCalled();
  });

  it("dit la même chose des deux côtés — une seule règle (`phraseDesBornes`)", () => {
    // La page ne recopie pas la phrase : elle l'appelle. Deux formulations de
    // « qu'est-ce qui borne ce run ? » finiraient par ne plus dire la même
    // chose, et c'est la divergence que #943 a déjà corrigée sur « y a-t-il un
    // cadrage en attente ? ».
    monterLeGeste();

    expect(screen.getByText(phraseDesBornes(AUCUNE_BORNE))).toBeTruthy();
  });

  it("Paramètres ne dit plus « pas encore réglable » : il dit où", async () => {
    rendreAvecEtat(<ParametresCouts />);

    expect(screen.queryByText(/pas encore réglable/)).toBeNull();
    expect(screen.queryByText(/--plafond-cout/)).toBeNull();
    expect(screen.getByText(/Bornes d'un run/)).toBeTruthy();
    expect(
      screen.getByRole("link", { name: /Aller à la conversation/ }),
    ).toBeTruthy();
  });
});

/**
 * ⑥ **la carte dit sur quel projet le run travaillera** (#1180).
 *
 * Le fil est transverse : une proposition faite sur A se relit depuis B, et le
 * geste partait avec le projet de la fenêtre du clic. Elle porte désormais son
 * projet (`projet_vise`), c'est là que l'API l'ouvre, et la carte le dit. La
 * **forme** est un choix rendu sur pièces (« Variante retenue » de #1180) ; ce
 * qui se garde ici, ce sont les propriétés dont elle découle :
 *
 * - la cible est **nommée**, aussi quand c'est le projet ouvert, avec son dossier ;
 * - un **écart** avec la fenêtre se dit en mots, les deux projets nommés — et le
 *   geste reste offert : c'est la proposition qui décide où le run part ;
 * - **sans projet**, « Lancer » est désarmé, et la carte dit pourquoi : aucun run
 *   ne part sans projet depuis le fil.
 */
describe("⑥ la carte dit sur quel projet le run travaillera", () => {
  const OUVERT = projetFactice();
  const AUTRE: ProjetVise = {
    id: "prj-5c0ffee1",
    nom: "carnet-de-recettes",
    racine: "D:/projets/carnet-de-recettes",
  };
  const ICI: ProjetVise = { id: OUVERT.id, nom: OUVERT.nom, racine: OUVERT.racine };

  function monter(demande: MessageChat, trancher = vi.fn().mockResolvedValue(undefined)) {
    rendreAvecEtat(<DemandeDeCadrage demande={demande} trancher={trancher} />);
    return trancher;
  }

  function carte() {
    return screen.getByRole("region", { name: "Décision sur le cadrage" });
  }

  it("nomme le projet de la proposition et son dossier, même quand c'est le projet ouvert", () => {
    monter(demandeFactice({ projet_vise: ICI }));

    const decision = carte();
    expect(within(decision).getByText("Dans le projet")).toBeTruthy();
    expect(within(decision).getByText("Dépensio").tagName).toBe("STRONG");
    // Le dossier, coupé à ses séparateurs : son texte se lit en entier.
    expect(decision.textContent).toContain("D:/projets/depensio");
    expect(within(decision).queryByText("Pas le projet ouvert")).toBeNull();
  });

  it("dit l'écart en toutes lettres quand la fenêtre est sur un autre projet", async () => {
    const trancher = monter(demandeFactice({ projet_vise: AUTRE }));

    const decision = carte();
    expect(within(decision).getByText("carnet-de-recettes").tagName).toBe("STRONG");
    expect(within(decision).getByText("Pas le projet ouvert")).toBeTruthy();
    expect(decision.textContent).toMatch(
      /Ce run partira dans « carnet-de-recettes », là où il a été\s+proposé — pas dans le projet ouvert, « Dépensio »\./,
    );
    // Le geste reste offert : ce n'est pas la fenêtre qui décide, c'est la
    // proposition — et l'API l'ouvrira dans son projet à elle.
    await userEvent.click(within(decision).getByRole("button", { name: "Lancer" }));
    expect(trancher).toHaveBeenCalledWith(true, null, AUCUNE_BORNE);
  });

  it("une proposition d'avant ce ticket nomme le projet de la fenêtre, où l'API la lancera", () => {
    monter(demandeFactice());

    expect(within(carte()).getByText("Dépensio")).toBeTruthy();
    expect(within(carte()).queryByText("Pas le projet ouvert")).toBeNull();
  });

  it("sans aucun projet, « Lancer » est désarmé et la carte dit pourquoi", async () => {
    // La porte « Nouveau projet » : hors du shell, aucun projet ouvert — et une
    // proposition qui n'en porte pas. L'échantillon fautif est le run qui
    // partait sans projet, et n'apparaissait dans la liste d'aucun.
    const trancher = vi.fn().mockResolvedValue(undefined);
    render(<DemandeDeCadrage demande={demandeFactice()} trancher={trancher} />);

    expect(screen.getByText(SANS_PROJET)).toBeTruthy();
    const lancer = screen.getByRole("button", { name: "Lancer" }) as HTMLButtonElement;
    expect(lancer.disabled).toBe(true);
    await userEvent.click(lancer);
    expect(trancher).not.toHaveBeenCalled();
    // Refuser, lui, reste possible : il n'ouvre rien.
    expect(
      (screen.getByRole("button", { name: "Ne pas lancer" }) as HTMLButtonElement).disabled,
    ).toBe(false);
  });

  it("sur /chat, la carte au pied du fil nomme le projet de la proposition", async () => {
    poserFilAssistance({
      messages: [messageFactice({ contenu: "un minuteur" }), demandeFactice({ projet_vise: AUTRE })],
    });
    rendreAvecEtat(<PageChat />, {
      agents: [agentFactice({ nom: AGENT_ORCHESTRATION, role: ROLE_ORCHESTRATION })],
      executions: [],
    });

    const decision = await screen.findByRole("region", { name: "Décision sur le cadrage" });
    expect(within(decision).getByText("carnet-de-recettes")).toBeTruthy();
    expect(within(decision).getByText("Pas le projet ouvert")).toBeTruthy();
  });
});

/**
 * ⑦ **combien va coûter ce run, et quelles bornes lui ai-je vraiment posées ?**
 * (#1184).
 *
 * Trois raideurs au moment de lancer : une proposition sans estimation, une
 * proposition qui mourait à la première question, un accord tapé qui perdait ses
 * bornes. La **forme** de l'estimation est un choix rendu sur pièces (« Variante
 * retenue » de #1184 : au pied, à côté de « Lancer », hors de la boîte des
 * bornes, le seul chiffre en gras). Ce qui se garde ici, ce sont les propriétés
 * dont elle découle :
 *
 * - la carte **dit** ce que le run coûterait, avec ce qui fonde le chiffre, et le
 *   geste qui l'engage le porte en description ;
 * - l'estimation **ne borne rien** (#494) : aucun champ pré-rempli, « aucune
 *   borne » reste le régime, et l'accord part sans borne ;
 * - un plancher sans estimation du modèle se dit « au moins », jamais « ≈ » ;
 * - la proposition **reste acceptable** après les questions qui la suivent — la
 *   réponse gardée la porte, et la carte la suit au pied du fil ;
 * - sous la bulle qui a ouvert un run, les **bornes appliquées** se lisent, qu'un
 *   clic ou une phrase les ait posées — « aucune » comprise.
 */
describe("⑦ ce que le run coûterait, et les bornes qu'il a vraiment reçues", () => {
  const ESTIMATION: EstimationRun = {
    taches: 5,
    bas_usd: 4.5,
    haut_usd: 9.9,
    estimees: true,
  };
  const FOURCHETTE = `≈ ${formatCout(4.5)} à ${formatCout(9.9)}`;
  // `getByText` compare le texte **normalisé** du nœud (espaces insécables de
  // `Intl` ramenées à une espace) à la chaîne telle quelle : on normalise donc
  // la chaîne attendue de la même façon, sans rien changer à ce qu'elle dit.
  const normaliser = (texte: string) => texte.replace(/\s+/g, " ");

  function monter(demande: MessageChat, trancher = vi.fn().mockResolvedValue(undefined)) {
    rendreAvecEtat(<DemandeDeCadrage demande={demande} trancher={trancher} />);
    return trancher;
  }

  function monterLeChat(messages: MessageChat[]) {
    poserFilAssistance({ messages });
    return rendreAvecEtat(<PageChat />, {
      agents: [agentFactice({ nom: AGENT_ORCHESTRATION, role: ROLE_ORCHESTRATION })],
      executions: [],
    });
  }

  it("dit ce que le run coûterait, avec ce qui fonde le chiffre", () => {
    monter(demandeFactice({ estimation: ESTIMATION }));

    const decision = screen.getByRole("region", { name: "Décision sur le cadrage" });
    // Le seul chiffre en gras, dans une phrase — la forme retenue (fal.ai).
    expect(within(decision).getByText(normaliser(FOURCHETTE)).tagName).toBe("STRONG");
    expect(decision.textContent).toContain("Ce run coûterait");
    expect(decision.textContent).toContain(
      "découpage puis ≈ 5 tâches — ordre de grandeur estimé, pas une mesure ni une borne",
    );
  });

  it("« Lancer » porte le coût en description : c'est ce que le geste engage", () => {
    monter(demandeFactice({ estimation: ESTIMATION }));

    const lancer = screen.getByRole("button", { name: "Lancer" });
    expect(lancer.getAttribute("aria-describedby")).toBe("cadrage-estimation");
    expect(document.getElementById("cadrage-estimation")?.textContent).toContain(FOURCHETTE);
  });

  it("l'estimation ne borne rien : aucun champ pré-rempli, et l'accord part sans borne", async () => {
    // L'échantillon fautif serait le « budget suggéré » : la borne haute recopiée
    // dans le plafond de coût — une borne par défaut déguisée, que #494 refuse.
    const trancher = monter(demandeFactice({ estimation: ESTIMATION }));

    expect(screen.getByText(phraseDesBornes(AUCUNE_BORNE))).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: /Bornes du run/ }));
    expect((screen.getByLabelText("Coût maximal") as HTMLInputElement).value).toBe("");
    await userEvent.click(screen.getByRole("button", { name: "Lancer" }));

    expect(trancher).toHaveBeenCalledWith(true, null, AUCUNE_BORNE);
  });

  it("un plancher sans estimation du modèle se dit « au moins », jamais « ≈ »", () => {
    monter(demandeFactice({ estimation: { ...ESTIMATION, taches: 3, estimees: false } }));

    const decision = screen.getByRole("region", { name: "Décision sur le cadrage" });
    expect(decision.textContent).toContain("découpage puis au moins 3 tâches");
    expect(decision.textContent).not.toContain("≈ 3 tâches");
  });

  it("une proposition d'avant ce ticket ne se voit rien inventer", () => {
    monter(demandeFactice());

    expect(screen.queryByText(/Ce run coûterait/)).toBeNull();
    expect(
      screen.getByRole("button", { name: "Lancer" }).getAttribute("aria-describedby"),
    ).toBeNull();
  });

  it("reste acceptable après une ou plusieurs questions : la carte suit la réponse qui la garde", async () => {
    // Le fil tel que l'API le laisse : chaque réponse à une question sur la
    // proposition la **porte** à nouveau (objectif, projet, estimation), si bien
    // que la règle du dernier message la retrouve — rien n'est deviné ici.
    const gardee = (contenu: string) =>
      demandeFactice({ contenu, estimation: ESTIMATION, horodatage: "2026-09-27T19:04:05Z" });
    monterLeChat([
      messageFactice({ contenu: "Ajoute la pagination" }),
      demandeFactice({ estimation: ESTIMATION }),
      messageFactice({ contenu: "Combien ça va coûter ?" }),
      gardee("Comptez environ 4,50 à 9,90 $."),
      messageFactice({ contenu: "Et ça touchera à styles.css ?" }),
      gardee("Normalement, non."),
    ]);

    const decision = await screen.findByRole("region", { name: "Décision sur le cadrage" });
    expect(within(decision).getByRole("button", { name: "Lancer" })).toBeTruthy();
    expect(within(decision).getByText(normaliser(FOURCHETTE))).toBeTruthy();
    // Une seule carte, pas une par réponse gardée : c'est une seule proposition.
    expect(screen.getAllByRole("region", { name: "Décision sur le cadrage" })).toHaveLength(1);
  });

  it("sous la bulle qui a ouvert le run, les bornes appliquées se lisent — « aucune » comprise", async () => {
    monterLeChat([
      messageFactice({ contenu: "Bon, vas-y, mais 0,50 $ max." }),
      messageFactice({
        agent: AGENT_ORCHESTRATION,
        auteur: AGENT_ORCHESTRATION,
        contenu: "C'est lancé, avec un plafond de coût de 0,50 $.",
        run_id: "f40353261509",
        bornes: { ...AUCUNE_BORNE, plafond_cout_usd: 0.5 },
      }),
      messageFactice({ contenu: "Et un autre, sans limite." }),
      messageFactice({
        agent: AGENT_ORCHESTRATION,
        auteur: AGENT_ORCHESTRATION,
        contenu: "C'est lancé.",
        run_id: "a3c6bfc510fb",
        bornes: AUCUNE_BORNE,
      }),
    ]);

    expect(
      await screen.findByText(
        normaliser(`Bornes : ${bornesEnLigne({ ...AUCUNE_BORNE, plafond_cout_usd: 0.5 })}`),
      ),
    ).toBeTruthy();
    expect(screen.getByText(/s'interrompt à 0,50/)).toBeTruthy();
    expect(screen.getByText("Bornes : aucune — le run ira jusqu'au bout")).toBeTruthy();
  });

  it("un message d'avant ce ticket, qui n'en sait rien, ne dit aucune borne", async () => {
    monterLeChat([
      messageFactice({
        agent: AGENT_ORCHESTRATION,
        auteur: AGENT_ORCHESTRATION,
        contenu: "C'est parti.",
        run_id: "r-ancien",
      }),
    ]);

    // Le run se lit sous la bulle et dans « Ouvert depuis ce fil » : deux fois.
    await screen.findAllByText("r-ancien");
    expect(screen.queryByText(/^Bornes :/)).toBeNull();
  });
});

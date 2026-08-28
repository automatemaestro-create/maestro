/**
 * Le **chat global** — la porte d'entrée conversationnelle (#273, lot 6/6 de #244).
 *
 * `/chat` était un emplacement réservé depuis #190 ; #268 lui a donné son canal
 * (`orchestrateur`) et #269 son écran. Rien n'en était couvert côté navigateur,
 * et ce qui manquait n'était pas « des tests sur une page » mais la garde de la
 * décision la plus facile à défaire : **la mention change de destinataire, elle
 * ne recopie rien**. Recopier le message dans les deux fils est le raccourci
 * naturel — il donnerait deux historiques d'une même conversation, désaccordés
 * dès le premier rechargement, et rien à l'écran ne le montrerait.
 *
 * D'où la façon dont ce fichier observe : le fil rendu est factice, mais **le
 * canal demandé** à `useChat` est noté (`canauxDemandes`, même dessin que
 * `porteesDemandees` de #281). C'est le seul endroit où « à qui l'on parle » est
 * observable, le contenu affiché venant de `poserFilAssistance` quoi qu'il
 * arrive.
 *
 * Couvre :
 *
 * ① `mentionEnTete` — les quatre décisions du module, toutes du même ordre : ne
 *    rien faire dans le doute, une mention mal reconnue détournant un message
 *    vers le mauvais fil — et `destinatairesDuFil`, la liste qu'elle consulte ;
 * ② l'écran : le fil global par défaut, la bascule par mention et par bouton, le
 *    bandeau qui dit où part le message, et le retour à l'orchestration ;
 * ③ « Ouvert depuis ce fil » — ce que la conversation a ouvert, **lu** des
 *    messages et jamais déduit de ce qui a tourné pendant qu'on regardait.
 *
 * ⚠ **Le parc monté ici porte l'orchestrateur** (#671), et ce n'est pas un détail
 * de fixture : c'est la forme que sert le mode réel — `GET /api/agents` rend les
 * acteurs vus au journal, et l'orchestrateur en est un. Le parc d'avant
 * (`[dev, qa]`) était celui de `--demo`, si bien que l'écran n'a jamais été
 * éprouvé sur les données qu'il sert vraiment : le destinataire y était proposé
 * deux fois, sous la même clé React, et rien ici ne le voyait.
 *
 * Ce que ce fichier ne couvre pas, et pourquoi : la mise en page conversationnelle
 * elle-même (bulles, sources d'un message, défilement) appartient à
 * `components/Conversation`, partagé avec l'onglet Chat d'un agent et exercé par
 * `fil-sources.test.tsx` ; le bout en bout dans un vrai navigateur reste le rôle
 * de `/verify`.
 */

import { describe, expect, it } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import PageChat from "@/app/chat/page";
import {
  AGENT_ORCHESTRATION,
  INTERLOCUTEUR_ORCHESTRATION,
  ROLE_ORCHESTRATION,
  destinatairesDuFil,
  mentionEnTete,
} from "@/lib/orchestration";

import {
  agentFactice,
  canalCourant,
  canauxDemandes,
  messageFactice,
  poserFilAssistance,
  rendreAvecEtat,
  tacheFactice,
} from "./aides";

const DESTINATAIRES = [AGENT_ORCHESTRATION, "dev", "qa"];

/**
 * L'écran, avec le parc d'agents que la colonne de droite propose — dans la forme
 * que sert le mode réel : les exécutants, **puis l'orchestrateur**, que la
 * projection range parmi les acteurs qu'elle a vus au journal (relevé sur
 * `GET /api/agents` : `developpeur`, `bdd`, `devops`, `designer`, `qa`,
 * `orchestrateur`).
 */
function monterLeChat(etat: Parameters<typeof rendreAvecEtat>[1] = {}) {
  return rendreAvecEtat(<PageChat />, {
    agents: [
      agentFactice({ nom: "dev" }),
      agentFactice({ nom: "qa", role: "QA" }),
      agentFactice({ nom: AGENT_ORCHESTRATION, role: ROLE_ORCHESTRATION }),
    ],
    ...etat,
  });
}

/** La zone de saisie, nommée d'après l'interlocuteur courant. */
function saisiePour(interlocuteur: string): HTMLTextAreaElement {
  return screen.getByLabelText(`Message à ${interlocuteur}`) as HTMLTextAreaElement;
}

/** La colonne de propriétés (#539) — deux cartes : « Parler à », puis les runs. */
function proprietesDuFil(): HTMLElement {
  return screen.getByRole("complementary", { name: "Propriétés du fil" });
}

/**
 * Les lignes de « Ouvert depuis ce fil ».
 *
 * Ciblées par le titre de leur carte et non par le rang de la liste : la colonne
 * en porte deux (les destinataires en sont une), et un test qui prendrait « la
 * dernière » changerait de sujet le jour où une troisième carte s'ajoute.
 */
function ouvertsDepuisLeFil(): HTMLElement[] {
  const carte = within(proprietesDuFil())
    .getAllByRole("article")
    .find(
      (candidate) =>
        within(candidate).queryByRole("heading", {
          name: "Ouvert depuis ce fil",
        }) !== null,
    );
  if (carte === undefined) throw new Error("carte « Ouvert depuis ce fil » absente");
  return within(carte).queryAllByRole("listitem");
}

// ── ① la mention, hors de tout écran ─────────────────────────────────────────

describe("mentionEnTete (#269)", () => {
  it("détache une mention en tête et rend le reste du brouillon", () => {
    expect(mentionEnTete("@dev ajoute la pagination", DESTINATAIRES)).toEqual({
      agent: "dev",
      reste: "ajoute la pagination",
    });
  });

  it("ignore la casse, les noms de fil étant des slugs minuscules", () => {
    expect(mentionEnTete("@DEV corrige le tri", DESTINATAIRES)?.agent).toBe("dev");
  });

  it("ne reconnaît rien tant que la mention n'est pas close par une espace", () => {
    // Sans cette règle, le destinataire sauterait d'un agent à l'autre à chaque
    // frappe de « @de… ».
    expect(mentionEnTete("@dev", DESTINATAIRES)).toBeNull();
  });

  it("n'accepte pas une mention au milieu d'une phrase", () => {
    // Une adresse de courriel, un pseudonyme cité : ce n'est pas une adresse de fil.
    expect(mentionEnTete("écris à contact@dev pour lui dire", DESTINATAIRES)).toBeNull();
  });

  it("laisse un destinataire inconnu dans le texte plutôt que de l'avaler", () => {
    expect(mentionEnTete("@quelquun bonjour", DESTINATAIRES)).toBeNull();
  });

  it("rend une mention seule comme un brouillon vide", () => {
    expect(mentionEnTete("@qa ", DESTINATAIRES)).toEqual({ agent: "qa", reste: "" });
  });
});

describe("destinatairesDuFil (#671)", () => {
  it("met l'orchestration en tête, puis le parc dans son ordre", () => {
    expect(destinatairesDuFil(["dev", "qa"])).toEqual([AGENT_ORCHESTRATION, "dev", "qa"]);
  });

  it("ne la propose qu'une fois quand le parc la porte — la forme du mode réel", () => {
    // `GET /api/agents` rend les acteurs vus au journal, l'orchestrateur compris :
    // le trouver là est le cas nominal, pas une donnée aberrante.
    expect(destinatairesDuFil(["dev", "qa", AGENT_ORCHESTRATION])).toEqual([
      AGENT_ORCHESTRATION,
      "dev",
      "qa",
    ]);
  });

  it("laisse le reste du parc tel quel, doublons compris", () => {
    // Un exécutant en double serait un défaut de la projection : l'écran le
    // montre plutôt que de le masquer. Seule l'orchestration est retirée.
    expect(destinatairesDuFil(["dev", "dev"])).toEqual([
      AGENT_ORCHESTRATION,
      "dev",
      "dev",
    ]);
  });
});

// ── ② l'écran et son destinataire ────────────────────────────────────────────

describe("le chat global (#269)", () => {
  it("ouvre sur le fil de l'orchestration, sans avoir à choisir à qui parler", () => {
    monterLeChat();

    expect(canalCourant()).toBe(AGENT_ORCHESTRATION);
    expect(
      screen.getByRole("heading", { name: "Chat global", level: 2 }),
    ).toBeInTheDocument();
    expect(saisiePour(INTERLOCUTEUR_ORCHESTRATION)).toBeInTheDocument();
  });

  it("propose des amorces qui montrent la frontière du fil, tant qu'il est vide", () => {
    monterLeChat();

    // Deux ouvrent un run, deux n'ouvrent rien : c'est ce que le canal distingue.
    expect(
      screen.getByRole("button", { name: "Ajoute la pagination à la liste des projets" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Où en sont les runs ?" }),
    ).toBeInTheDocument();
  });

  it("change de destinataire sur une mention, et retire la mention du brouillon", async () => {
    const utilisateur = userEvent.setup();
    monterLeChat();

    await utilisateur.type(
      saisiePour(INTERLOCUTEUR_ORCHESTRATION),
      "@dev ajoute la pagination",
    );

    // Le fil lu est celui de `dev` : le message partira là-bas, il n'est
    // recopié nulle part.
    expect(canalCourant()).toBe("dev");
    // Et la mention a quitté le brouillon — elle a été consommée, pas envoyée.
    expect(saisiePour("dev")).toHaveValue("ajoute la pagination");
  });

  it("dit où part le message dès qu'on n'est plus sur le fil global", async () => {
    const utilisateur = userEvent.setup();
    monterLeChat();

    await utilisateur.type(saisiePour(INTERLOCUTEUR_ORCHESTRATION), "@dev ");

    expect(
      screen.getByRole("heading", { name: "Aparté avec dev", level: 2 }),
    ).toBeInTheDocument();
    expect(screen.getByText(/Rien n'est recopié ici/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Vue détaillée/ })).toHaveAttribute(
      "href",
      "/agents/dev/chat",
    );
  });

  it("revient à l'orchestration sans quitter l'écran", async () => {
    const utilisateur = userEvent.setup();
    monterLeChat();
    await utilisateur.type(saisiePour(INTERLOCUTEUR_ORCHESTRATION), "@qa ");
    expect(canalCourant()).toBe("qa");

    await utilisateur.click(
      screen.getByRole("button", { name: "Revenir à l'orchestration" }),
    );

    expect(canalCourant()).toBe(AGENT_ORCHESTRATION);
    expect(screen.queryByText(/Rien n'est recopié ici/)).not.toBeInTheDocument();
  });

  it("laisse une mention inconnue dans le texte, sur le fil global", async () => {
    const utilisateur = userEvent.setup();
    monterLeChat();

    await utilisateur.type(saisiePour(INTERLOCUTEUR_ORCHESTRATION), "@personne bonjour");

    expect(canalCourant()).toBe(AGENT_ORCHESTRATION);
    expect(saisiePour(INTERLOCUTEUR_ORCHESTRATION)).toHaveValue("@personne bonjour");
  });

  it("ne propose l'orchestration qu'une fois, alors que le parc la porte", () => {
    // La panne de #671, vue de l'écran : deux boutons pour un seul fil, et deux
    // enfants React sous la clé `orchestrateur`. Le test d'à côté vérifie déjà
    // la liste entière ; celui-ci nomme la question, pour qu'elle survive à une
    // réécriture de l'autre.
    monterLeChat();

    const boutons = within(proprietesDuFil()).getAllByRole("button");
    expect(boutons.filter((bouton) => bouton.textContent === "Orchestration")).toHaveLength(
      1,
    );
  });

  it("bascule aussi à la souris, l'orchestration en tête du parc", async () => {
    const utilisateur = userEvent.setup();
    monterLeChat();
    const proprietes = proprietesDuFil();

    expect(
      within(proprietes)
        .getAllByRole("button")
        .map((bouton) => bouton.textContent),
    ).toEqual(["Orchestration", "@dev", "@qa"]);

    await utilisateur.click(within(proprietes).getByRole("button", { name: "@qa" }));

    expect(canalCourant()).toBe("qa");
    expect(
      within(proprietes).getByRole("button", { name: "@qa" }),
    ).toHaveAttribute("aria-pressed", "true");
  });

  it("ne relit jamais deux fils à la fois", async () => {
    const utilisateur = userEvent.setup();
    monterLeChat();

    await utilisateur.type(saisiePour(INTERLOCUTEUR_ORCHESTRATION), "@dev ");

    // Un rendu ne demande qu'un canal : c'est ce qui distingue « changer de
    // destinataire » de « dupliquer la conversation ».
    expect(new Set(canauxDemandes).size).toBeLessThanOrEqual(2);
    expect(canauxDemandes.at(-1)).toBe("dev");
  });
});

// ── ③ ce que le fil a ouvert ─────────────────────────────────────────────────

describe("« Ouvert depuis ce fil » (#268/#269)", () => {
  it("invite à dicter un travail quand rien n'a encore été ouvert", () => {
    monterLeChat();

    expect(screen.getByText(/Rien encore\./)).toBeInTheDocument();
  });

  it("liste les runs rattachés aux messages, du plus récent au plus ancien", () => {
    poserFilAssistance({
      messages: [
        messageFactice({ agent: AGENT_ORCHESTRATION, contenu: "Ajoute la pagination" }),
        messageFactice({
          agent: AGENT_ORCHESTRATION,
          auteur: AGENT_ORCHESTRATION,
          contenu: "J'ouvre un run.",
          run_id: "run-ancien",
        }),
        messageFactice({
          agent: AGENT_ORCHESTRATION,
          auteur: AGENT_ORCHESTRATION,
          contenu: "J'ouvre un run.",
          run_id: "run-recent",
        }),
      ],
    });
    monterLeChat({
      agents: [agentFactice({ nom: "dev" })],
      taches: [
        tacheFactice({ id: "T-1", run_id: "run-recent" }),
        tacheFactice({ id: "T-2", run_id: "run-recent" }),
      ],
    });

    const ouverts = ouvertsDepuisLeFil();
    expect(ouverts.map((item) => item.textContent)).toEqual([
      expect.stringContaining("run-recent"),
      expect.stringContaining("run-ancien"),
    ]);
    // Le compte des tâches vient de l'état global, pas du message.
    expect(ouverts[0]).toHaveTextContent("2 tâches");
    // Un run dont la décomposition n'a rien produit encore le dit, plutôt que
    // d'afficher « 0 tâche ».
    expect(ouverts[1]).toHaveTextContent("décomposition en cours");
  });

  it("ne nomme un run qu'une fois, même rattaché à plusieurs messages", () => {
    poserFilAssistance({
      messages: [
        messageFactice({
          agent: AGENT_ORCHESTRATION,
          auteur: AGENT_ORCHESTRATION,
          run_id: "run-1",
        }),
        messageFactice({
          agent: AGENT_ORCHESTRATION,
          auteur: AGENT_ORCHESTRATION,
          run_id: "run-1",
        }),
      ],
    });
    monterLeChat();

    const ouverts = ouvertsDepuisLeFil();
    expect(ouverts).toHaveLength(1);
    expect(
      within(ouverts[0]).getByRole("link", { name: /Voir le run/ }),
    ).toHaveAttribute("href", "/runs/run-1");
  });

  it("ignore les messages sans run — la liste ne déduit rien", () => {
    poserFilAssistance({
      messages: [
        messageFactice({ agent: AGENT_ORCHESTRATION, contenu: "Où en sont les runs ?" }),
        messageFactice({
          agent: AGENT_ORCHESTRATION,
          auteur: AGENT_ORCHESTRATION,
          contenu: "Aucun run en cours.",
        }),
      ],
    });
    // Un run tourne pendant qu'on regarde l'écran : il n'a pas été ouvert
    // depuis ce fil, il n'y figure pas.
    monterLeChat({
      agents: [],
      taches: [tacheFactice({ run_id: "run-d-ailleurs" })],
    });

    expect(screen.getByText(/Rien encore\./)).toBeInTheDocument();
  });
});

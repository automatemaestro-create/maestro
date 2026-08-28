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

import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import PageChat from "@/app/chat/page";
import { envoyerMessageChat } from "@/lib/api";
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
  projetDuFilCourant,
  projetFactice,
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

/**
 * La colonne de propriétés (#539) — « Parler à », « Conversations » (#696) puis
 * les runs ouverts depuis le fil.
 */
function proprietesDuFil(): HTMLElement {
  return screen.getByRole("complementary", { name: "Propriétés du fil" });
}

/**
 * Une carte de la colonne, désignée par le **titre** qu'elle porte.
 *
 * Jamais par son rang : la colonne en portait deux, elle en porte trois depuis
 * #696, et un test qui prendrait « la dernière » changerait de sujet à chaque
 * ajout. C'est aussi ce qui garde chaque assertion sur *sa* carte — depuis que
 * l'historique en pose une, « tous les boutons de la colonne » ne désigne plus
 * les destinataires.
 */
function carteDeLaColonne(titre: string): HTMLElement {
  const carte = within(proprietesDuFil())
    .getAllByRole("article")
    .find(
      (candidate) =>
        within(candidate).queryByRole("heading", { name: titre }) !== null,
    );
  if (carte === undefined) throw new Error(`carte « ${titre} » absente`);
  return carte;
}

/** Les lignes de « Ouvert depuis ce fil ». */
function ouvertsDepuisLeFil(): HTMLElement[] {
  return within(carteDeLaColonne("Ouvert depuis ce fil")).queryAllByRole(
    "listitem",
  );
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

    // Deux mènent à une proposition de run, deux à une simple réponse : c'est la
    // frontière que le canal distingue. Aucune n'ouvre de run à elle seule depuis
    // #685 — c'est l'accord qui suit qui ouvre, jamais le texte de l'amorce.
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

    const boutons = within(carteDeLaColonne("Parler à")).getAllByRole("button");
    expect(boutons.filter((bouton) => bouton.textContent === "Orchestration")).toHaveLength(
      1,
    );
  });

  it("bascule aussi à la souris, l'orchestration en tête du parc", async () => {
    const utilisateur = userEvent.setup();
    monterLeChat();
    const parlerA = carteDeLaColonne("Parler à");

    expect(
      within(parlerA)
        .getAllByRole("button")
        .map((bouton) => bouton.textContent),
    ).toEqual(["Orchestration", "@dev", "@qa"]);

    await utilisateur.click(within(parlerA).getByRole("button", { name: "@qa" }));

    expect(canalCourant()).toBe("qa");
    expect(
      within(parlerA).getByRole("button", { name: "@qa" }),
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

// ── ④ le projet de la fenêtre part avec le message (#683) ────────────────────

describe("le projet de la fenêtre", () => {
  it("accompagne chaque message du fil", () => {
    const projet = projetFactice({ id: "prj-depensio", nom: "Dépensio" });

    rendreAvecEtat(<PageChat />, { agents: [] }, projet);

    // C'est ce que l'écran donne au fil, et c'est tout ce qui manquait : sans
    // lui, le run ouvert par l'orchestration ne relevait d'aucun projet, donc
    // n'entrait dans la liste d'aucun et refusait de s'ouvrir en détail — alors
    // même que le fil l'annonçait en cours.
    expect(projetDuFilCourant()).toBe("prj-depensio");
  });

  it("suit le destinataire quand la mention change de fil", async () => {
    const utilisateur = userEvent.setup();
    const projet = projetFactice({ id: "prj-depensio" });
    rendreAvecEtat(<PageChat />, { agents: [agentFactice({ nom: "dev" })] }, projet);

    await utilisateur.type(saisiePour(INTERLOCUTEUR_ORCHESTRATION), "@dev ");

    // Le canal change, le cadre non : on parle à quelqu'un d'autre **depuis la
    // même fenêtre**, donc depuis le même projet.
    expect(canalCourant()).toBe("dev");
    expect(projetDuFilCourant()).toBe("prj-depensio");
  });
});

// ── ⑤ ce que l'appel REST porte (#683) ───────────────────────────────────────

describe("envoyerMessageChat", () => {
  /** Le corps JSON du dernier `fetch`, tel qu'il part sur le réseau. */
  function corpsEnvoye(appel: ReturnType<typeof vi.fn>): Record<string, unknown> {
    const [, init] = appel.mock.calls[0] as [string, RequestInit];
    return JSON.parse(String(init.body)) as Record<string, unknown>;
  }

  function stubFetch(): ReturnType<typeof vi.fn> {
    const fetch = vi.fn(async () => new Response("{}", { status: 201 }));
    vi.stubGlobal("fetch", fetch);
    return fetch;
  }

  afterEach(() => vi.unstubAllGlobals());

  it("porte le projet dans le corps de la requête", async () => {
    const fetch = stubFetch();

    await envoyerMessageChat(AGENT_ORCHESTRATION, "Ajoute la pagination", [], "prj-depensio");

    expect(corpsEnvoye(fetch)).toMatchObject({
      contenu: "Ajoute la pagination",
      projet_id: "prj-depensio",
    });
  });

  it("n'envoie rien de plus quand il n'y a pas de projet", async () => {
    const fetch = stubFetch();

    await envoyerMessageChat(AGENT_ORCHESTRATION, "Ajoute la pagination");

    // L'appel d'avant le lot, à l'octet près : une clé `projet_id: null` ferait
    // dire au corps « aucun projet » là où l'ancien contrat ne disait rien, et
    // le rattachement est justement ce qui ne doit pas être deviné.
    expect(corpsEnvoye(fetch)).toEqual({ contenu: "Ajoute la pagination" });
  });
});

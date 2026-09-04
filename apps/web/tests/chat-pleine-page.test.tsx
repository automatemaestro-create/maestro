/**
 * **Ce que la conversation pleine page a gagné, et qu'un ticket suivant défera
 * sans le voir** — lot 8/8 de #690, la moitié navigateur.
 *
 * Les lots 1 à 7 ont différé leurs tests ici (docs/10 §5.1). Ce fichier ne
 * recouvre donc pas ce qui était déjà gardé — la mention qui change de
 * destinataire (`chat-global.test.tsx` ①-③), le direct à l'écran (④ du même
 * fichier), la couture flux → état sur le vrai hook (`chat-direct.test.tsx`),
 * l'analyseur Markdown lui-même (`fil-lisible.test.tsx`, la seule exception que
 * la règle des lots prévoit pour la logique critique). Il garde ce qui restait
 * sans garde : **ce que le chantier a RETIRÉ**, et ce que le lot 6 a ajouté à
 * l'écran.
 *
 * Une chose retirée est ce qu'il y a de plus difficile à tester : rien ne la
 * nomme à l'écran, aucun libellé ne la désigne, et le test qui la garde ne peut
 * qu'affirmer une absence. Or **une absence est vraie pour deux raisons** — la
 * bonne, et le fait que la sonde ne regarde pas au bon endroit. D'où la méthode
 * de #534/#537/#539, appliquée ici à chaque sonde : **elle est prouvée sur un
 * échantillon fautif avant de balayer**. Un test qui ne fait pas cette moitié
 * rend un ✓ sur une question jamais posée, et c'est exactement la panne qu'on
 * n'a aucun moyen de remarquer.
 *
 * Couvre :
 *
 * ① **le fil n'a plus d'ascenseur à lui** (#691) — la boîte `max-h-[60vh]
 *    overflow-y-auto` a disparu, et rien au-dessus du fil ne l'a remplacée ;
 * ② **ce qui va bien ne s'affiche plus** (#691) — « Temps réel connecté » ne
 *    revient pas, seule la coupure se dit ;
 * ③ **les conversations, à l'écran** (#696) — en ouvrir une neuve, retrouver
 *    les précédentes, et savoir laquelle on lit ;
 * ④ **le fil n'exécute rien** (#697, vu du fil et non du module) — ce qu'un
 *    modèle écrit reste du texte, dans la bulle comme dans la réponse qui
 *    s'écrit, et ce que l'utilisateur a tapé se relit tel qu'il l'a tapé.
 *
 * ⚠ **Aucune géométrie ici** (#308) : jsdom ne calcule ni hauteur, ni
 * `overflow`, ni défilement, et un test qui prétendrait mesurer l'un des trois
 * mesurerait zéro en se faisant passer pour vert. Ce que ① observe est le
 * **contrat de mise en page tel qu'il est écrit** — les utilitaires présents
 * dans le DOM —, pas son effet. L'effet est le rôle de `/banc-mise-en-page`, et
 * son verdict se consigne dans la PR.
 */

import { describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import PageChat from "@/app/chat/page";
import {
  AGENT_ORCHESTRATION,
  INTERLOCUTEUR_ORCHESTRATION,
  ROLE_ORCHESTRATION,
} from "@/lib/orchestration";

import {
  agentFactice,
  conversationFactice,
  messageFactice,
  poserFilAssistance,
  rendreAvecEtat,
} from "./aides";

/** L'écran, monté comme `chat-global.test.tsx` le monte — même parc. */
function monterLeChat() {
  return rendreAvecEtat(<PageChat />, {
    agents: [
      agentFactice({ nom: "dev" }),
      agentFactice({ nom: AGENT_ORCHESTRATION, role: ROLE_ORCHESTRATION }),
    ],
  });
}

/** Le fil de messages — la liste que `Conversation` nomme d'après l'interlocuteur. */
function filDuChat(): HTMLElement {
  return screen.getByRole("list", {
    name: `Messages échangés avec ${INTERLOCUTEUR_ORCHESTRATION}`,
  });
}

// ---------------------------------------------------------------------------
// ① Le fil n'a plus d'ascenseur à lui (#691)
// ---------------------------------------------------------------------------

/**
 * Les utilitaires par lesquels un élément se donne un **ascenseur à lui**, ou
 * se borne en hauteur — les deux moitiés de la boîte que #691 a retirée
 * (`max-h-[60vh] min-h-64 overflow-y-auto`).
 *
 * Le motif vise la **famille** et non les valeurs relevées avant le lot :
 * `max-h-[70vh]` serait le même défaut sous un autre chiffre, et un test qui
 * n'interdirait que `60vh` laisserait revenir la boîte au premier ajustement.
 * `min-h-` n'y est pas : un plancher n'emprisonne rien, et le `min-h-6` de la
 * ligne d'attente en est un usage légitime.
 */
const BORNES = /(^|:)(overflow-y-auto|overflow-y-scroll|overflow-auto|overflow-scroll|max-h-\S+|h-\[[^\]]*vh[^\]]*\])(?=\s|$)/;

/** Ce qu'un élément porte de bornant — vide quand il n'en porte pas. */
function bornesDe(element: Element): string[] {
  return Array.from(element.classList).filter((classe) => BORNES.test(classe));
}

/**
 * Ce qui borne le fil, de lui-même jusqu'à la racine du rendu — l'ascenseur
 * pouvant tout aussi bien être posé sur un parent, ce qui rendrait le même
 * défaut sans que le `<ol>` ne porte rien.
 */
function bornesAuDessusDu(fil: HTMLElement): string[] {
  const trouvees: string[] = [];
  let courant: Element | null = fil;
  while (courant !== null && courant !== document.body) {
    trouvees.push(...bornesDe(courant));
    courant = courant.parentElement;
  }
  return trouvees;
}

describe("le fil n'a plus d'ascenseur à lui (#691)", () => {
  it("reconnaît la boîte d'avant le lot — la sonde voit ce qu'elle cherche", () => {
    // La moitié sans laquelle les deux tests suivants ne prouveraient rien : la
    // chaîne exacte relevée dans `components/Conversation` avant #691, telle que
    // le ticket parent la cite.
    const avant = document.createElement("div");
    avant.className = "max-h-[60vh] min-h-64 overflow-y-auto rounded-md border";

    expect(bornesDe(avant)).toEqual(["max-h-[60vh]", "overflow-y-auto"]);
    // Et le plancher n'en est pas : `min-h-` reste un usage légitime.
    expect(bornesDe(avant)).not.toContain("min-h-64");
  });

  it("ne borne ni le fil ni rien au-dessus de lui", () => {
    poserFilAssistance({
      messages: [
        messageFactice({ contenu: "Salut" }),
        messageFactice({ auteur: AGENT_ORCHESTRATION, contenu: "Bonjour" }),
      ],
    });
    monterLeChat();

    // Le fil s'étend et c'est la page qui le parcourt : un seul ascenseur pour
    // un seul contenu, là où la boîte en donnait deux — dont l'intérieur ne
    // bougeait pas quand on tournait la molette sur la page.
    expect(bornesAuDessusDu(filDuChat())).toEqual([]);
  });

  it("occupe la hauteur disponible plutôt que de laisser un vide sous le composeur", () => {
    poserFilAssistance({ messages: [messageFactice({ contenu: "Salut" })] });
    monterLeChat();

    // L'autre moitié du lot, et la seule qui s'observe **en positif** : sans le
    // `flex-1`, un fil de deux messages laissait le composeur au milieu de
    // l'écran et ~270 px de vide dessous. La géométrie, elle, n'est pas jugée
    // ici (#308) — seulement l'utilitaire qui la demande.
    expect(Array.from(filDuChat().classList)).toContain("flex-1");
  });

  it("garde le composeur à quai, le fil défilant désormais avec la page", () => {
    poserFilAssistance({ messages: [messageFactice({ contenu: "Salut" })] });
    monterLeChat();

    // Corollaire du retrait de la boîte, et pas un détail : en fin de flux, le
    // composeur obligerait à redescendre tout l'historique avant de pouvoir
    // écrire. C'est la seule pièce de l'écran qui **doit** rester collée.
    // `bottom-16` et non `bottom-0` depuis #726 : à quai, il se tient au-dessus
    // de la bande du bouton flottant (#123) au lieu de lui réserver du vide à
    // droite — collé, donc, mais 64 px au-dessus du bord.
    const composeur = screen
      .getByLabelText(`Message à ${INTERLOCUTEUR_ORCHESTRATION}`)
      .closest("form");
    expect(composeur).not.toBeNull();
    expect(Array.from(composeur!.classList)).toEqual(
      expect.arrayContaining(["sticky", "bottom-16"]),
    );
  });
});

// ---------------------------------------------------------------------------
// ② Ce qui va bien ne s'affiche plus (#691)
// ---------------------------------------------------------------------------

/**
 * Ce que l'écran dit de l'état de la socket, quel qu'il soit.
 *
 * Balayer le document entier et non le seul en-tête : le défaut d'origine était
 * précisément que l'état s'affichait **deux fois**, et une sonde qui ne
 * regarderait qu'un endroit ne saurait pas le dire.
 */
function mentionsDeConnexion(): string[] {
  const dits = document.body.textContent ?? "";
  return [
    /Temps réel connecté/,
    /(^|\W)Connecté(\W|$)/,
    /Reconnexion…/,
  ]
    .filter((motif) => motif.test(dits))
    .map((motif) => motif.source);
}

describe("ce qui va bien ne s'affiche plus (#691)", () => {
  it("dit la coupure — la sonde voit un état quand il est affiché", () => {
    // L'échantillon fautif est ici l'état **anormal**, le seul qui ait encore
    // le droit d'occuper la place : s'il ne se voyait pas, l'absence constatée
    // au test suivant ne vaudrait rien.
    poserFilAssistance({ connecte: false });
    monterLeChat();

    expect(mentionsDeConnexion()).toEqual(["Reconnexion…"]);
  });

  it("ne dit rien de l'état nominal, ni une fois ni deux", () => {
    poserFilAssistance({ connecte: true });
    monterLeChat();

    // « Temps réel connecté » occupait la place la plus visible de l'écran —
    // l'en-tête du bloc principal — pour n'apprendre rien, et la barre du cadre
    // le disait déjà. Une place se gagne, elle ne se garde pas parce qu'on
    // l'avait (docs/30 §4).
    expect(mentionsDeConnexion()).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// ③ Les conversations, à l'écran (#696)
// ---------------------------------------------------------------------------

/** La carte « Conversations » de la colonne de propriétés. */
function carteDesConversations(): HTMLElement {
  const colonne = screen.getByRole("complementary", {
    name: "Propriétés du fil",
  });
  const carte = within(colonne)
    .getAllByRole("article")
    .find(
      (candidate) =>
        within(candidate).queryByRole("heading", { name: "Conversations" }) !==
        null,
    );
  if (carte === undefined) throw new Error("carte « Conversations » absente");
  return carte;
}

/** Le bouton qui ouvre un fil neuf — le seul de la carte à ne pas être une ligne. */
function boutonNouvelleConversation(): HTMLElement {
  return within(carteDesConversations()).getByRole("button", {
    name: "Nouvelle conversation",
  });
}

/**
 * Les lignes de l'historique, dans l'ordre où l'écran les pose.
 *
 * Écartées par **identité** et non par leur texte : un libellé recopié ici se
 * désaccorderait du jour où le bouton change de mot, et la liste se mettrait
 * silencieusement à compter un élément de trop.
 */
function lignesDeLHistorique(): HTMLElement[] {
  const bouton = boutonNouvelleConversation();
  return within(carteDesConversations())
    .getAllByRole("button")
    .filter((candidat) => candidat !== bouton);
}

describe("les conversations, à l'écran (#696)", () => {
  const HIER = conversationFactice({
    id: "20260827t090000-aaaaaa",
    titre: "Ajoute la pagination",
    derniere: "2026-08-27T09:12:00Z",
    messages: 4,
  });
  const AVANT = conversationFactice({
    id: "origine",
    titre: "Le fil d'avant",
    derniere: "2026-08-01T18:12:00Z",
    messages: 26,
  });

  it("liste les conversations dans l'ordre servi, sans en retrier aucune", () => {
    // L'ordre est celui de la dernière activité, et il est tenu par l'API
    // (§6.14). L'écran qui le rejouerait finirait par le contredire — c'est la
    // règle déjà écrite pour `grapheFactice` et `friseFactice`.
    poserFilAssistance({
      conversation: HIER.id,
      conversations: [HIER, AVANT],
    });
    monterLeChat();

    expect(lignesDeLHistorique().map((ligne) => ligne.textContent)).toEqual([
      expect.stringContaining("Ajoute la pagination"),
      expect.stringContaining("Le fil d'avant"),
    ]);
  });

  it("marque celle qu'on lit, et elle seule", () => {
    poserFilAssistance({
      conversation: AVANT.id,
      conversations: [HIER, AVANT],
    });
    monterLeChat();

    // `aria-current` et non un simple fond coloré : « celle que je lis » doit
    // s'entendre autant qu'elle se voit.
    const marquees = lignesDeLHistorique().filter(
      (ligne) => ligne.getAttribute("aria-current") === "true",
    );
    expect(marquees).toHaveLength(1);
    expect(marquees[0].textContent).toContain("Le fil d'avant");
  });

  it("nomme une conversation dont personne n'a rien dit", () => {
    // Le titre est **dérivé** du premier message (§6.14), donc vide tant que
    // rien n'a été dit. Le nommer ici plutôt que côté API garde le stockage
    // muet : une conversation vierge n'a pas de titre, elle n'en a pas encore.
    poserFilAssistance({
      conversations: [conversationFactice({ titre: "", messages: 0 })],
    });
    monterLeChat();

    expect(lignesDeLHistorique()[0].textContent).toContain(
      "Conversation vierge",
    );
  });

  it("ouvre une conversation neuve sans quitter l'écran", async () => {
    const utilisateur = userEvent.setup();
    const nouvelleConversation = vi.fn(async () => {});
    poserFilAssistance({ nouvelleConversation });
    monterLeChat();

    await utilisateur.click(boutonNouvelleConversation());

    // L'écran ne **décide** de rien : ni de l'identifiant, ni de l'ordre, ni de
    // l'idempotence de l'ouverture — tout cela appartient au canal (§6.14). Ce
    // qui se garde ici est que le geste existe et va bien là.
    expect(nouvelleConversation).toHaveBeenCalledTimes(1);
  });

  it("rouvre une conversation précédente d'un clic", async () => {
    const utilisateur = userEvent.setup();
    const ouvrirConversation = vi.fn();
    poserFilAssistance({
      conversation: HIER.id,
      conversations: [HIER, AVANT],
      ouvrirConversation,
    });
    monterLeChat();

    await utilisateur.click(lignesDeLHistorique()[1]);

    expect(ouvrirConversation).toHaveBeenCalledWith(AVANT.id);
  });
});

// ---------------------------------------------------------------------------
// ④ Le fil n'exécute rien (#697, vu du fil)
// ---------------------------------------------------------------------------

/**
 * Ce qui, dans un fragment de DOM, **fait autre chose que s'afficher** :
 * éléments actifs, gestionnaires d'événements en attribut, et cibles de
 * navigation qu'un clic exécuterait.
 *
 * `fil-lisible.test.tsx` garde déjà la propriété **du module** (`lib/markdown`
 * rend un arbre de données, jamais une chaîne de HTML). Ce qui se garde ici est
 * l'autre moitié, celle qu'un remaniement défait sans toucher au module : que le
 * **fil** passe bien par ce rendu-là, des deux côtés de la conversation et
 * jusque dans la réponse qui s'écrit. Un `dangerouslySetInnerHTML` réintroduit
 * dans une bulle ne ferait rougir aucun test du module.
 */
function elementsExecutables(racine: HTMLElement): string[] {
  const trouves: string[] = [];
  for (const element of Array.from(racine.querySelectorAll("*"))) {
    const balise = element.tagName.toLowerCase();
    // `svg` n'y est pas : le fil en porte légitimement (les icônes de « Suite »,
    // celles des sources d'un message), et il n'exécute rien. Ce qu'on cherche
    // est ce qui **charge** ou **exécute** — c'est `img` qui porte `onerror`.
    if (["script", "iframe", "object", "embed", "img"].includes(balise)) {
      trouves.push(balise);
    }
    for (const attribut of Array.from(element.attributes)) {
      if (attribut.name.startsWith("on")) trouves.push(`@${attribut.name}`);
      if (
        (attribut.name === "href" || attribut.name === "src") &&
        /^\s*javascript:/i.test(attribut.value)
      ) {
        trouves.push(`${attribut.name}=javascript:`);
      }
    }
  }
  return trouves;
}

/** Ce qu'un modèle peut écrire, et qu'aucun fil ne doit exécuter. */
const TEXTE_PIEGE =
  "Voici la correction : <script>alert(1)</script> puis " +
  '<img src=x onerror="alert(2)"> et [le lien](javascript:alert(3)).';

describe("le fil n'exécute rien (#697)", () => {
  it("repère un fragment actif — la sonde voit ce qu'elle cherche", () => {
    // L'échantillon fautif : le même texte, passé au navigateur **comme du
    // HTML**, c'est-à-dire ce qu'un `dangerouslySetInnerHTML` en ferait. Sans
    // cette moitié, les trois tests suivants diraient « rien d'actif » d'un fil
    // qu'on n'aurait pas su regarder.
    const fautif = document.createElement("div");
    fautif.innerHTML = TEXTE_PIEGE.replace("[le lien](javascript:alert(3))", "")
      + '<a href="javascript:alert(3)">le lien</a>';
    document.body.appendChild(fautif);

    const trouves = elementsExecutables(fautif);
    expect(trouves).toContain("script");
    expect(trouves).toContain("img");
    expect(trouves).toContain("@onerror");
    expect(trouves).toContain("href=javascript:");

    fautif.remove();
  });

  it("rend en toutes lettres ce qu'un agent écrit en balises", () => {
    poserFilAssistance({
      messages: [
        messageFactice({ auteur: AGENT_ORCHESTRATION, contenu: TEXTE_PIEGE }),
      ],
    });
    monterLeChat();

    const fil = filDuChat();
    expect(elementsExecutables(fil)).toEqual([]);
    // Et le texte ne disparaît pas non plus : ce qui n'est pas exécuté doit
    // rester **lisible**, sans quoi une réponse qui parle de HTML deviendrait
    // illisible pour avoir été mise en sécurité.
    expect(fil.textContent).toContain("<script>alert(1)</script>");
  });

  it("en fait autant de la réponse en train de s'écrire", () => {
    // La bulle en cours rend le **même** Markdown que celle qui la remplacera :
    // c'est ce qui évite que la clôture du flux reformate la réponse sous les
    // yeux — et c'est aussi ce qui fait qu'aucun des deux chemins n'échappe à
    // la règle. Un rendu brut pendant le flux serait une seconde porte.
    poserFilAssistance({
      envoi: true,
      reponseEnCours: {
        auteur: AGENT_ORCHESTRATION,
        texte: TEXTE_PIEGE,
        figee: false,
      },
    });
    monterLeChat();

    const fil = filDuChat();
    expect(elementsExecutables(fil)).toEqual([]);
    expect(fil.textContent).toContain("<script>alert(1)</script>");
  });

  it("relit le message de l'utilisateur tel qu'il l'a tapé", () => {
    // Le seul côté du fil où l'utilisateur est l'auteur : le reformater lui
    // ferait dire autre chose que ce qu'il a écrit. Les astérisques restent des
    // astérisques, et le HTML reste du texte pour la même raison qu'à côté.
    poserFilAssistance({
      messages: [
        messageFactice({ contenu: "regarde **ici** et <b>là</b>" }),
      ],
    });
    monterLeChat();

    const fil = filDuChat();
    expect(elementsExecutables(fil)).toEqual([]);
    expect(fil.textContent).toContain("**ici**");
    expect(fil.textContent).toContain("<b>là</b>");
  });
});

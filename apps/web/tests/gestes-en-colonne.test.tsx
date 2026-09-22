/**
 * **Les gestes du fil se font dans la colonne** (#1106 — réserve C2 du bouclage
 * de « L'atelier », verdict du 2026-09-21).
 *
 * La colonne de droite (#926) montait les **messages** du fil et s'arrêtait là.
 * Les trois demandes auxquelles on répond d'un geste — la question d'un agent
 * (#1025), la question d'outillage d'un projet neuf (#1031), « Je lance ? »
 * (#943) — étaient composées dans `app/chat/page.tsx`, et par elle seule : elles
 * **s'affichaient** dans la colonne, dans le texte des messages, sans rien pour
 * lancer, corriger ou refuser. Répondre obligeait à ouvrir `/chat`, c'est-à-dire
 * à changer de page — l'exact contraire de ce que la troisième zone du shell
 * existe pour offrir.
 *
 * Ce que ce filet garde n'est donc pas « la carte s'affiche » — chaque carte a
 * déjà sa suite (`question-agent`, `etape-outillage`, `demande-cadrage`) — mais
 * les trois propriétés qui se casseraient **sans rien faire échouer** :
 *
 * ① **la colonne porte les gestes, et ils agissent depuis là.** Le verdict porte
 *    sur ce que le geste appelle (`trancherCadrage`, `repondreAUneQuestion`),
 *    pas sur la présence d'un bouton : une carte rendue mais débranchée ferait
 *    exactement ce que le ticket décrit ;
 * ② **les deux surfaces rendent la MÊME chose.** C'est la propriété que le
 *    ticket nomme « un seul composant pour deux emplacements » (docs/35 §3.3),
 *    et la seule façon de la vérifier est de comparer : la composition vit dans
 *    `components/chat/GestesDuFil`, et une recopie dans l'une des deux surfaces
 *    passerait tous les autres tests ;
 * ③ **rien n'attend, rien ne s'affiche.** Un pied qui rendrait une boîte vide
 *    poserait 12 px de vide permanents sous le dernier message — dans une
 *    colonne de 320 px, c'est le fil qu'on rogne.
 *
 * Et une garde qui n'appartient à aucun critère mais que ce lot rend
 * nécessaire : depuis que la colonne porte **toutes** les questions du projet,
 * la même question peut être montée deux fois sur un même écran — la colonne et
 * l'onglet Chat de son agent. Deux champs de même `id` feraient perdre son nom
 * accessible au second (`components/Primitives`, `CadreChamp`).
 *
 * ⚠ Aucune géométrie ici (#308) : ce que les cartes deviennent à 320 px est une
 * mesure du banc (`/banc-mise-en-page`), et le rendu est l'affaire de la
 * relecture visuelle. Ce qui est vérifié ici est **ce qui est monté**, et où.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import PageChat from "@/app/chat/page";
import { QuestionsDuFil } from "@/components/chat/QuestionDansLeFil";
import { Shell } from "@/components/Shell";
import { marquerGuideVu } from "@/lib/guide";
import { AGENT_ORCHESTRATION } from "@/lib/orchestration";
import type { MessageChat, QuestionOutillage } from "@/lib/types";
import { ecrireConversationOuverte } from "@/lib/preferences";

import {
  messageFactice,
  poserChemin,
  poserEtatGlobal,
  poserFilAssistance,
  poserProjetActif,
  questionFactice,
  rendreAvecEtat,
} from "./aides";

const OBJECTIF = "Dédoublonner les fiches clients importées du CSV";

/** La réponse de l'orchestration qui **demande** l'accord (#943). */
function propositionFactice(): MessageChat {
  return messageFactice({
    agent: AGENT_ORCHESTRATION,
    auteur: AGENT_ORCHESTRATION,
    contenu: `Je vous propose de dédoublonner… Je lance ?`,
    proposition: OBJECTIF,
  });
}

const QUESTION_OUTILLAGE: QuestionOutillage = {
  cle: "langages",
  intitule: "Dans quel langage ?",
  options: [
    { valeur: "typescript", libelle: "TypeScript", raison: "web" },
    { valeur: "python", libelle: "Python", raison: "service" },
  ],
  recommande: "typescript",
  pourquoi: "c'est une application web",
  rang: 1,
  total: 6,
};

/** Le message qui pose la question d'outillage d'un projet neuf (#1031). */
function questionDOutillageFactice(): MessageChat {
  return messageFactice({
    agent: AGENT_ORCHESTRATION,
    auteur: AGENT_ORCHESTRATION,
    contenu: "Dans quel langage ?",
    question: QUESTION_OUTILLAGE,
  });
}

/**
 * Les cartes de geste montées dans un fil — leur nom accessible, dans l'ordre
 * du DOM.
 *
 * Les `<section>` **imbriquées** dans la section du fil, et rien d'autre : le
 * fil lui-même est la racine du relevé, donc ce qu'on compte est exactement ce
 * que `pied` y ajoute. C'est ce qui permet de comparer deux surfaces dont les
 * fils ne portent pas le même nom (« Chat global » ici, « Conversation »
 * là-bas) sans recopier la liste des cartes attendues.
 */
function gestesDe(fil: HTMLElement): string[] {
  return [...fil.querySelectorAll("section[aria-label]")].map(
    (carte) => carte.getAttribute("aria-label") ?? "",
  );
}

/** Le shell sur un écran quelconque, la colonne ouverte — puis la colonne. */
async function monterLaColonne(): Promise<HTMLElement> {
  ecrireConversationOuverte(true);
  poserChemin("/runs");
  render(
    <Shell>
      <p>un écran quelconque</p>
    </Shell>,
  );
  await screen.findByRole("heading", { level: 1 });
  // La colonne est restituée par un effet **différé d'un tick** (#925) : sans
  // cette attente, le relevé porterait sur un shell qui ne la monte pas encore.
  return screen.findByRole("complementary", { name: "Conversation" });
}

/** Le fil monté **dans** la colonne — la section que `Conversation` y rend. */
async function filDeLaColonne(): Promise<HTMLElement> {
  const colonne = await monterLaColonne();
  return within(colonne).getByRole("region", { name: "Conversation" });
}

beforeEach(() => {
  marquerGuideVu();
  poserProjetActif();
  ecrireConversationOuverte(true);
});

// ===========================================================================
// ① La colonne porte les gestes — et ils agissent depuis là
// ===========================================================================

describe("les gestes du fil, dans la colonne", () => {
  it("porte la proposition de run avec ses trois gestes", async () => {
    // Le symptôme même du ticket : « Je lance ? » s'affichait, sans rien pour
    // lancer, corriger l'objectif ou refuser.
    poserFilAssistance({ messages: [propositionFactice()] });
    const fil = await filDeLaColonne();

    const carte = within(fil).getByRole("region", {
      name: "Décision sur le cadrage",
    });
    expect(
      within(carte).getByRole("button", { name: "Lancer" }),
    ).toBeInTheDocument();
    expect(
      within(carte).getByRole("button", { name: "Ne pas lancer" }),
    ).toBeInTheDocument();
    // Amender, c'est corriger sur place : l'objectif part éditable (#943).
    expect(within(carte).getByLabelText("Objectif proposé")).toHaveValue(
      OBJECTIF,
    );
  });

  it("porte la question d'un agent", async () => {
    poserEtatGlobal({ questions: [questionFactice()] });
    poserFilAssistance({ messages: [messageFactice()] });
    const fil = await filDeLaColonne();

    expect(
      within(fil).getByRole("region", { name: "Question de l'agent bdd" }),
    ).toBeInTheDocument();
  });

  it("porte la question d'outillage d'un projet neuf", async () => {
    poserFilAssistance({ messages: [questionDOutillageFactice()] });
    const fil = await filDeLaColonne();

    expect(
      within(fil).getByRole("region", { name: "Question d'outillage" }),
    ).toBeInTheDocument();
  });

  it("porte une question d'agent ET une proposition en même temps", async () => {
    // L'un des états que le ticket demande de couvrir. Les deux ne s'excluent
    // pas — elles ne vivent pas sur le même support : la question vient de
    // l'exécution, la proposition du dernier message. L'ordre est celui de
    // #1025 : ce qui est le plus loin de la saisie a le moins à voir avec elle.
    poserEtatGlobal({ questions: [questionFactice()] });
    poserFilAssistance({ messages: [propositionFactice()] });
    const fil = await filDeLaColonne();

    expect(gestesDe(fil)).toEqual([
      "Question de l'agent bdd",
      "Décision sur le cadrage",
    ]);
  });

  it("porte l'équipe proposée à un projet sans agent, et la décline d'ici (#1146)", async () => {
    // La quatrième demande du fil prend le rang de « Je lance ? », qu'elle
    // remplace : elle doit agir depuis la colonne comme les trois autres.
    const recruter = vi.fn(async () => {});
    poserFilAssistance({
      messages: [
        messageFactice({
          agent: AGENT_ORCHESTRATION,
          auteur: AGENT_ORCHESTRATION,
          contenu: "Avant de lancer, il faut une équipe…",
          recrutement: { objectif: OBJECTIF, projet_id: "prj-7f3a1c2b" },
        }),
      ],
      recruter,
    });
    const fil = await filDeLaColonne();

    const carte = within(fil).getByRole("region", { name: "Équipe à valider" });
    expect(gestesDe(fil)).toEqual(["Équipe à valider"]);
    await userEvent.click(
      await within(carte).findByRole("button", { name: "Plus tard" }),
    );
    await waitFor(() => expect(recruter).toHaveBeenCalledWith(false));
  });

  it("lance le run depuis la colonne, sans changer de page", async () => {
    // ① Le cœur du ticket : le geste **agit** d'ici. Une carte rendue mais
    // débranchée referait le défaut sous une autre forme.
    const trancherCadrage = vi.fn(async () => {});
    poserFilAssistance({ messages: [propositionFactice()], trancherCadrage });
    const fil = await filDeLaColonne();

    await userEvent.click(
      within(fil).getByRole("button", { name: "Lancer" }),
    );
    await waitFor(() => expect(trancherCadrage).toHaveBeenCalledTimes(1));
    // Rien n'a été corrigé, donc l'objectif ne repart pas (§6.15) ; les bornes
    // sont celles de la carte, vierges ici.
    expect(trancherCadrage).toHaveBeenCalledWith(true, null, expect.anything());
  });

  it("répond à la question d'un agent depuis la colonne", async () => {
    const repondreAUneQuestion = vi.fn(async () => {});
    poserEtatGlobal({
      questions: [questionFactice()],
      repondreAUneQuestion,
    });
    poserFilAssistance({ messages: [messageFactice()] });
    const fil = await filDeLaColonne();

    const carte = within(fil).getByRole("region", { name: "Question de l'agent bdd" });
    await userEvent.click(
      within(carte).getByRole("button", { name: "SQLite" }),
    );
    await waitFor(() =>
      expect(repondreAUneQuestion).toHaveBeenCalledWith(
        "t2:9f1c0a4bd3",
        "SQLite",
      ),
    );
  });
});

// ===========================================================================
// ② Les deux surfaces rendent la même chose
// ===========================================================================

describe("la parité entre /chat et la colonne", () => {
  /** Ce que `/chat` monte au pied de son fil, dans le même état. */
  function gestesDeLaPage(): string[] {
    poserChemin("/chat");
    rendreAvecEtat(<PageChat />);
    return gestesDe(screen.getByRole("region", { name: "Chat global" }));
  }

  it("rend les mêmes gestes quand un run est proposé", async () => {
    // La propriété que le ticket appelle « un seul composant pour deux
    // emplacements » (docs/35 §3.3), vérifiée par comparaison : une recopie de
    // la composition dans l'une des deux surfaces passerait tous les autres
    // tests de ce fichier, et divergerait au premier changement.
    poserEtatGlobal({ questions: [questionFactice()] });
    poserFilAssistance({ messages: [propositionFactice()] });
    const surLaPage = gestesDeLaPage();
    cleanupRendu();

    const fil = await filDeLaColonne();
    expect(gestesDe(fil)).toEqual(surLaPage);
    expect(surLaPage.length).toBeGreaterThan(0);
  });

  it("rend les mêmes gestes sur la question d'outillage d'un projet neuf", async () => {
    poserFilAssistance({ messages: [questionDOutillageFactice()] });
    const surLaPage = gestesDeLaPage();
    cleanupRendu();

    const fil = await filDeLaColonne();
    expect(gestesDe(fil)).toEqual(surLaPage);
    expect(surLaPage).toContain("Question d'outillage");
  });
});

/**
 * Vide le DOM entre les deux montages d'un test de parité.
 *
 * `cleanup` de Testing Library est déjà automatique **entre** les tests
 * (`tests/setup.ts`) ; ici les deux surfaces sont montées dans le **même**
 * test, à dessein — l'état factice est posé une seule fois, donc ce qui les
 * sépare est la surface et rien d'autre.
 */
function cleanupRendu(): void {
  document.body.innerHTML = "";
}

// ===========================================================================
// ③ Rien n'attend, rien ne s'affiche
// ===========================================================================

describe("le fil au repos", () => {
  it("ne monte aucun geste quand rien n'attend", async () => {
    poserFilAssistance({ messages: [messageFactice({ contenu: "Bonjour" })] });
    const fil = await filDeLaColonne();

    expect(gestesDe(fil)).toEqual([]);
  });

  it("ne laisse pas de boîte vide sous le dernier message", async () => {
    // Le contrat de `Conversation` est que `pied` **absent** ne rend rien du
    // tout. Un composant qui rendrait `null` laisserait la boîte et son `mt-3`,
    // c'est-à-dire 12 px de vide permanents — dans 320 px, c'est du fil en
    // moins. D'où un hook, qui rend `undefined`.
    poserFilAssistance({ messages: [messageFactice({ contenu: "Bonjour" })] });
    const fil = await filDeLaColonne();

    expect(fil.querySelector("div.mt-3")).toBeNull();
  });
});

// ===========================================================================
// La garde que ce lot rend nécessaire : deux montages, deux identifiants
// ===========================================================================

describe("la même question montée deux fois", () => {
  it("donne à chaque champ son propre identifiant", () => {
    // Depuis que la colonne porte **toutes** les questions du projet, celle de
    // `bdd` s'affiche à la fois dans la colonne et dans l'onglet Chat de sa
    // fiche, sur le même écran. `question_id` ne distingue pas ces deux
    // montages : les deux zones de saisie portaient le même `id`, et avec elles
    // tout ce qui s'en dérive (`aria-describedby` vers `<id>-aide` et
    // `<id>-erreur`, `CadreChamp`). Un identifiant dupliqué sur un élément
    // focusable est aussi ce que le filet d'accessibilité refuse.
    const question = questionFactice();
    rendreAvecEtat(
      <>
        <QuestionsDuFil questions={[question]} repondre={async () => {}} />
        <QuestionsDuFil questions={[question]} repondre={async () => {}} />
      </>,
    );

    const champs = screen.getAllByLabelText("Ou répondez en une phrase");
    expect(champs).toHaveLength(2);
    expect(champs[0].id).not.toBe(champs[1].id);
    // …et plus généralement, aucun identifiant du document n'est porté deux
    // fois : les deux cartes sont montées entières, pas seulement leur champ.
    const identifiants = [...document.querySelectorAll("[id]")].map(
      (noeud) => noeud.id,
    );
    expect(new Set(identifiants).size).toBe(identifiants.length);
  });
});

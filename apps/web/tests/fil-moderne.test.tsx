/**
 * **Le fil se lit comme un chat moderne** (#1225, lot 4 de #1221) — qui parle,
 * ce qu'il fait en ce moment, ce qu'on peut faire ici.
 *
 * Le ticket posait une question et une seule : *à qui je parle, qu'est-ce qu'il
 * fait en ce moment, et qu'est-ce que je peux faire ici ?* Le fil n'y répondait
 * sur aucun des trois points — l'identité était un **pied** posé sous le dernier
 * message d'un tour et aligné **à droite**, c'est-à-dire du côté de la personne,
 * sous une réponse alignée à gauche ; l'attente était une ligne d'italique au
 * pied du `<ol>` ; aucun message n'avait d'action.
 *
 * La forme a été choisie sur pièces (#1009, #980) : quatre références capturées
 * en direct le 2026-09-23 (Zulip, Duck.ai, ChatGPT, Perplexity), trois variantes
 * rendues sur la vraie stack, et le choix du sous-agent `regard-neuf` consigné
 * sur le ticket sous « ## Variante retenue ». Ce fichier garde ce qui en a été
 * retenu, et **rien de ce qui relève du goût** :
 *
 * ① **un tour s'ouvre par qui parle** — médaillon d'initiale, nom, heure ; des
 *    deux côtés, et le médaillon **du seul côté de l'interlocuteur** (réserve du
 *    regard neuf : aucune des quatre références ne médaillonne l'utilisateur) ;
 * ② **le pied ne nomme plus à l'œil, et nomme toujours au lecteur d'écran** —
 *    la règle de #483 ne bouge pas, elle change de place ;
 * ③ **l'attente est l'en-tête du tour qui s'ouvre** — même médaillon, même nom,
 *    l'état à la place de l'heure. Et la règle de #695 tient : dès qu'un mot
 *    arrive, l'état s'efface (un indicateur immobile sur toute la génération ne
 *    distingue pas une réponse longue d'un blocage) ;
 * ④ **chaque message porte ses actions** — toujours présentes, jamais au survol
 *    seul (Zulip écarté sur ce point), et rien à copier ⇒ rien à rendre ;
 * ⑤ **tout ce qui n'est pas de la prose porte la même enveloppe** — les six
 *    gestes du fil sont des `chat/CarteDuFil`, la fin de run comprise, qui était
 *    le seul `div` nu.
 *
 * ⚠ Ce que ce fichier ne couvre pas : la colonne de lecture et le regroupement
 * des tours (`fil-en-colonne.test.tsx`, qui garde les partis pris de #876 et que
 * ce lot a fait évoluer), les lectures de l'interlocuteur
 * (`etapes-du-fil.test.tsx`, #1223), et le rendu — qui est l'affaire de la
 * relecture visuelle, pas de jsdom.
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ActionsDuMessage } from "@/components/chat/ActionsDuMessage";
import { BulleFil } from "@/components/chat/BulleFil";
import { CarteDuFil } from "@/components/chat/CarteDuFil";
import { ContenuOngletAgent } from "@/components/ContenuOngletAgent";
import { AnnonceIssueRun } from "@/components/runs/AnnonceIssueRun";
import { issueDuRun, type IssueRun } from "@/lib/issueRun";
import {
  CHAT_AUTEUR_UTILISATEUR,
  EXECUTION_TERMINEE,
} from "@/lib/types";

import {
  messageFactice,
  poserFilAssistance,
  projetFactice,
  rendreAvecEtat,
  runFactice,
} from "./aides";

/**
 * Le fil est monté par **l'onglet Chat d'une fiche agent** — la plus petite des
 * deux surfaces qui portent `components/Conversation` (docs/35 §3.3). Ce qu'on
 * juge ici est le composant partagé : `fil-en-colonne.test.tsx` vérifie déjà que
 * les deux emplacements ne divergent pas.
 */
const INTERLOCUTEUR = "dev";

function monterLeFil() {
  return rendreAvecEtat(<ContenuOngletAgent nom={INTERLOCUTEUR} onglet="chat" />);
}

const PROJET = projetFactice({
  id: "prj-1",
  nom: "Dépensio",
  racine: "D:/w/depensio",
});

/** L'issue d'un run soldé, telle que le fil et la cloche la reçoivent. */
function issueFactice(): IssueRun {
  const issue = issueDuRun(
    runFactice({
      run_id: "run-1",
      objectif: "Ajouter la pagination",
      statut: EXECUTION_TERMINEE,
      nb_taches: 2,
      cout_usd: 12.51,
      projet_id: PROJET.id,
      fin: "2026-09-23T17:04:00Z",
    }),
    PROJET,
  );
  if (issue === null) throw new Error("ce run n'est pas soldé");
  return issue;
}

/** Les lignes du fil qui portent une bulle, dans l'ordre. */
function bulles(): HTMLElement[] {
  const fil = screen.getByRole("list", {
    name: `Messages échangés avec ${INTERLOCUTEUR}`,
  });
  return (Array.from(fil.children) as HTMLElement[]).filter(
    (ligne) => ligne.querySelector("[data-bulle]") !== null,
  );
}

/** L'en-tête de tour d'une ligne — `null` quand la ligne ne nomme pas. */
function entete(ligne: HTMLElement): HTMLElement | null {
  return ligne.querySelector("[data-entete-de-tour]");
}

// ---------------------------------------------------------------------------
// ① et ② — l'identité ouvre le tour
// ---------------------------------------------------------------------------

describe("① un tour s'ouvre par qui parle", () => {
  it("met le médaillon, le nom et l'heure au-dessus du premier message d'un tour", () => {
    render(
      <ol>
        <BulleFil
          auteur="orchestrateur"
          horodatage="2026-09-23T17:04:00Z"
          ouvreUnTour
        >
          <p>Pour l&apos;essayer, ouvrez un terminal.</p>
        </BulleFil>
      </ol>,
    );

    const ligne = screen.getByRole("listitem");
    const tete = entete(ligne);
    expect(tete).not.toBeNull();
    // L'en-tête vient **avant** la bulle dans le DOM : c'est ce qui fait qu'on
    // sait qui parle avant d'avoir lu (Zulip, Duck.ai).
    const bulle = ligne.querySelector("[data-bulle]");
    expect(tete!.compareDocumentPosition(bulle!)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
    expect(tete!.textContent).toContain("orchestrateur");
    expect(tete!.querySelector("time")).toHaveAttribute(
      "dateTime",
      "2026-09-23T17:04:00Z",
    );
  });

  it("réserve le médaillon à l'interlocuteur, la bulle disant déjà l'autre côté", () => {
    // Réserve du regard neuf, tranchée à l'implémentation : aucune des quatre
    // références ne médaillonne l'utilisateur — ChatGPT, Duck.ai et Perplexity
    // laissent sa bulle parler seule.
    render(
      <ol>
        <BulleFil auteur="dev" ouvreUnTour>
          <p>Réponse</p>
        </BulleFil>
        <BulleFil auteur="vous" utilisateur ouvreUnTour>
          <p>Demande</p>
        </BulleFil>
      </ol>,
    );

    const [agent, personne] = screen.getAllByRole("listitem");
    expect(
      entete(agent)!.querySelector("[aria-hidden='true']")?.textContent,
    ).toBe("D");
    expect(entete(personne)!.textContent).toBe("vous");
  });

  it("ne rend aucun en-tête sur un message qui continue un tour", () => {
    render(
      <ol>
        <BulleFil auteur="dev" horodatage="2026-09-23T17:04:00Z" nomme={false}>
          <p>La suite</p>
        </BulleFil>
      </ol>,
    );

    expect(entete(screen.getByRole("listitem"))).toBeNull();
  });
});

describe("② le pied nomme encore, mais pour le lecteur d'écran seul", () => {
  it("garde le nom et l'heure sur chaque message, en `sr-only`", () => {
    // La règle de #483 ne bouge pas : une bulle relue au lecteur d'écran n'a ni
    // gauche ni droite, donc chaque message se nomme. Ce qui change est la place
    // de ce que l'**œil** lit.
    render(
      <ol>
        <BulleFil auteur="dev" horodatage="2026-09-23T17:04:00Z" nomme={false}>
          <p>La suite</p>
        </BulleFil>
      </ol>,
    );

    const pied = screen
      .getByRole("listitem")
      .querySelector("[data-bulle] > p:last-child") as HTMLElement;
    expect(pied.classList.contains("sr-only")).toBe(true);
    expect(pied.textContent).toMatch(/^dev · /);
  });

  it("ne met aucun arrêt de tabulation dans un pied qu'on ne voit pas", () => {
    // L'`Infobulle` porte `tabIndex={0}` (#536) : elle vit dans l'en-tête, qui
    // se voit, jamais dans le pied masqué.
    const { container } = render(
      <ol>
        <BulleFil auteur="dev" horodatage="2026-09-23T17:04:00Z" nomme={false}>
          <p>La suite</p>
        </BulleFil>
      </ol>,
    );

    expect(container.querySelectorAll("[tabindex]")).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// ③ — ce qu'il fait en ce moment
// ---------------------------------------------------------------------------

describe("③ l'attente est l'en-tête du tour qui s'ouvre", () => {
  it("nomme l'interlocuteur et son état, à la place de l'heure", () => {
    poserFilAssistance({ envoi: true, reponseEnCours: null });
    monterLeFil();

    // Perplexity pose « Recherche terminée 3s » à cette place exacte ; ChatGPT
    // annonce « Réflexion » puis « Réponse terminée ». L'attente cesse donc
    // d'être une ligne d'italique au pied du `<ol>`.
    const attente = screen
      .getByRole("list", { name: `Messages échangés avec ${INTERLOCUTEUR}` })
      .querySelector("[data-entete-de-tour]") as HTMLElement;
    expect(attente.textContent).toContain(INTERLOCUTEUR);
    expect(within(attente).getByText("répond…")).toBeInTheDocument();
  });

  it("efface l'état dès le premier mot, et garde l'identité", () => {
    // La règle de #695 ne bouge pas : un indicateur immobile sur toute la
    // génération ne distingue pas une réponse longue d'un blocage. Ce qui reste
    // est l'en-tête, donc l'identité — ce que le ticket demandait.
    poserFilAssistance({
      envoi: true,
      reponseEnCours: {
        auteur: INTERLOCUTEUR,
        texte: "Je regarde le",
        etapes: [],
        figee: false,
      },
    });
    monterLeFil();

    expect(screen.queryByText("répond…")).toBeNull();
    expect(entete(bulles()[0])!.textContent).toContain(INTERLOCUTEUR);
  });
});

// ---------------------------------------------------------------------------
// ④ — ce qu'on peut faire d'un message
// ---------------------------------------------------------------------------

describe("④ chaque message porte ses actions", () => {
  it("copie le message, et dit qu'il l'a fait", async () => {
    const ecrireTexte = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText: ecrireTexte } });
    render(<ActionsDuMessage texte="Pour l'essayer, ouvrez un terminal." />);

    await userEvent.click(screen.getByRole("button", { name: /Copier/ }));
    expect(ecrireTexte).toHaveBeenCalledWith(
      "Pour l'essayer, ouvrez un terminal.",
    );
    // Un geste sans retour ne se distingue pas d'une page figée.
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Message copié",
    );
  });

  it("ne rend rien quand il n'y a rien à copier", () => {
    // Un message qui ne porte qu'une pièce jointe ou qu'un rattachement de run
    // ne doit pas offrir un bouton qui copierait la chaîne vide.
    const { container } = render(<ActionsDuMessage texte="   " />);
    expect(container).toBeEmptyDOMElement();
  });

  it("les rend sur les deux côtés du fil, et hors de la bulle", async () => {
    // Duck.ai porte son rail des deux côtés : on recopie aussi bien ce qu'on a
    // demandé que ce qui a été répondu. Hors de la bulle, parce que sur le fond
    // plein de la personne une icône de second plan n'a pas de contraste à elle.
    poserFilAssistance({
      messages: [
        messageFactice({
          auteur: CHAT_AUTEUR_UTILISATEUR,
          contenu: "Comment j'essaie ?",
        }),
        messageFactice({ auteur: INTERLOCUTEUR, contenu: "Ouvrez un terminal." }),
      ],
    });
    monterLeFil();

    const gestes = screen.getAllByRole("button", { name: /Copier/ });
    expect(gestes).toHaveLength(2);
    for (const geste of gestes) {
      expect(geste.closest("[data-bulle]")).toBeNull();
    }
  });
});

// ---------------------------------------------------------------------------
// ⑤ — une seule enveloppe pour les gestes du fil
// ---------------------------------------------------------------------------

describe("⑤ les gestes du fil portent la même enveloppe", () => {
  it("nomme sa zone et titre en `h3`, dans le fil qui est déjà une section", () => {
    render(
      <CarteDuFil libelle="Décision sur le cadrage" titre="Lancer ce run ?">
        <p>Corps</p>
      </CarteDuFil>,
    );

    const carte = screen.getByRole("region", {
      name: "Décision sur le cadrage",
    });
    expect(
      within(carte).getByRole("heading", { level: 3, name: "Lancer ce run ?" }),
    ).toBeInTheDocument();
  });

  it("donne cette enveloppe à la fin d'un run, qui était le seul geste sans", () => {
    render(<AnnonceIssueRun issue={issueFactice()} />);

    // Une région nommée, comme les cinq autres gestes — et un verdict écrit,
    // jamais une couleur seule (docs/30 §3.2).
    expect(screen.getByRole("region")).toBeInTheDocument();
    expect(screen.getByText("Run terminé")).toBeInTheDocument();
  });

  it("laisse la cloche sans enveloppe : ce panneau n'est pas le fil", () => {
    // Une carte par fin empilerait des cadres dans 20 rem, et cette liste n'a
    // jamais eu à s'accorder avec les gestes d'une conversation.
    render(<AnnonceIssueRun issue={issueFactice()} compacte />);

    expect(screen.queryByRole("region")).toBeNull();
    expect(screen.getByText("Run terminé")).toBeInTheDocument();
  });
});

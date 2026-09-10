/**
 * **Le fil se lit comme une colonne** (#876) — les partis pris 1 à 3 de la
 * veille #820 (docs/30 §5.3, décision complète en commentaire de #820).
 *
 * Ce fichier ne recouvre pas ce qui est déjà gardé : la géométrie que #691/#698
 * ont posée vit dans `chat-pleine-page.test.tsx` (①–④) et le composeur dans
 * `composeur.test.tsx`. Les partis pris de cette veille **s'ajoutent** à cette
 * géométrie ; ce qui se garde ici est ce qu'ils changent, et rien d'autre.
 *
 * ⚠ **Aucune géométrie** (#308) : jsdom ne calcule ni largeur, ni recouvrement,
 * ni défilement. Ce que ces sondes observent est le **contrat de mise en page
 * tel qu'il est écrit** — les utilitaires présents dans le DOM —, pas son effet.
 * Le recouvrement de ~318 px que le ticket annonce est une mesure du banc
 * (`/banc-mise-en-page`), et son verdict se consigne dans la PR.
 *
 * Chaque sonde est **prouvée sur le fil d'avant #876 avant de balayer** (méthode
 * de #534/#537/#539) : deux des trois propriétés s'observent en **négatif** —
 * une section qui ne borne rien, une bulle qui n'a plus de cadre —, et une
 * absence est vraie pour deux raisons, la bonne et le fait que la sonde regarde
 * ailleurs. L'échantillon fautif est l'écran d'avant le lot, tel que la veille
 * l'a relevé : la section en pleine largeur, la bulle d'agent bordée et ombrée,
 * deux messages consécutifs du même agent portant chacun son pied visible.
 *
 * Le fil étant monté par **deux** surfaces — le chat global (`/chat`) et l'onglet
 * Chat d'une fiche agent —, tout ce qui le concerne est joué sur les deux : c'est
 * la seule façon de vérifier que la seconde en hérite « sans une ligne », comme
 * l'objectif du ticket le demande.
 *
 * Couvre :
 *
 * ① **une colonne de lecture bornée et centrée** — la section porte
 *    `mx-auto w-full max-w-3xl`, donc l'en-tête, le fil et le composeur ont la
 *    même largeur ;
 * ② **seule la personne a une bulle** — l'agent parle dans le texte de la page,
 *    la bulle de la personne ne bouge pas, `pleineLargeur` garde son cadre ;
 * ③ **un tour = un auteur, nommé une fois** — messages consécutifs groupés,
 *    pied visible sur le dernier de la suite et `sr-only` sur les autres, un
 *    séparateur de journée coupant un tour.
 */

import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import PageChat from "@/app/chat/page";
import { BulleFil } from "@/components/chat/BulleFil";
import { ContenuOngletAgent } from "@/components/ContenuOngletAgent";
import {
  AGENT_ORCHESTRATION,
  INTERLOCUTEUR_ORCHESTRATION,
  ROLE_ORCHESTRATION,
} from "@/lib/orchestration";
import { CHAT_AUTEUR_UTILISATEUR } from "@/lib/types";

import {
  agentFactice,
  messageFactice,
  poserFilAssistance,
  rendreAvecEtat,
} from "./aides";

// ---------------------------------------------------------------------------
// Les deux surfaces qui montent le fil
// ---------------------------------------------------------------------------

type Surface = {
  nom: string;
  /** Monte la surface avec le fil que le test a posé. */
  monter: () => void;
  /** Celui à qui l'on parle — le fil en tire son nom accessible. */
  interlocuteur: string;
  /** Le nom de la section de conversation : la colonne de lecture. */
  section: string;
};

const SURFACES: Surface[] = [
  {
    nom: "le chat global (/chat)",
    monter: () => {
      rendreAvecEtat(<PageChat />, {
        agents: [
          agentFactice({ nom: "dev" }),
          agentFactice({ nom: AGENT_ORCHESTRATION, role: ROLE_ORCHESTRATION }),
        ],
      });
    },
    interlocuteur: INTERLOCUTEUR_ORCHESTRATION,
    section: "Chat global",
  },
  {
    nom: "l'onglet Chat d'une fiche agent",
    monter: () => {
      rendreAvecEtat(<ContenuOngletAgent nom="dev" onglet="chat" />);
    },
    interlocuteur: "dev",
    section: "Chat avec dev",
  },
];

// ---------------------------------------------------------------------------
// Les sondes
// ---------------------------------------------------------------------------

/**
 * Ce qu'une chaîne de classes porte de **colonne de lecture** : est-elle centrée
 * dans la place qu'on lui donne, et jusqu'où va-t-elle ?
 *
 * `mx-auto` **et** `w-full` ensemble, parce qu'aucun des deux ne suffit : sans
 * `w-full` la colonne se réduit à son contenu, sans `mx-auto` elle se colle à
 * gauche. La borne est rendue telle qu'elle est écrite, et non comparée à une
 * valeur : `max-w-2xl` serait le même contrat sous un autre chiffre, et c'est
 * le ticket qui fixe lequel.
 */
function colonneDe(classes: string): { centree: boolean; borne: string | null } {
  const utilitaires = classes.split(/\s+/).filter((u) => u !== "");
  return {
    centree:
      utilitaires.includes("mx-auto") && utilitaires.includes("w-full"),
    borne: utilitaires.find((u) => /^max-w-/.test(u)) ?? null,
  };
}

/**
 * Ce qu'une enveloppe de bulle porte d'**habillage** — les trois utilitaires que
 * le parti pris 2 retire au seul côté de l'agent : un bord, un fond, une
 * élévation.
 *
 * Les familles et non les valeurs relevées avant le lot : `bg-neutral-50` serait
 * le même défaut que `bg-surface` sous un autre nom, et une sonde qui
 * n'interdirait que la seconde laisserait revenir la boîte au premier
 * ajustement. `border-bord` seul ne compte pas pour un bord — c'est `border` qui
 * donne l'épaisseur, et la barre de `LigneConversation` (#831) montre qu'une
 * couleur de bord sans épaisseur ne dessine rien.
 */
function habillageDe(classes: string): {
  bord: boolean;
  fond: boolean;
  ombre: boolean;
} {
  const utilitaires = classes.split(/\s+/).filter((u) => u !== "");
  return {
    bord: utilitaires.includes("border"),
    fond: utilitaires.some((u) => /^bg-/.test(u)),
    ombre: utilitaires.some((u) => /^shadow-/.test(u)),
  };
}

/**
 * Ce qu'une ligne du fil porte d'**espacement de tour**. Le fil porte `gap-3` ;
 * une ligne qui ouvre un tour y ajoute les 12 px qui font le `gap-6`, et
 * `first:mt-0` l'annule sur le premier message, qui n'ouvre rien.
 */
function espacementDe(ligne: HTMLElement): string[] {
  return Array.from(ligne.classList)
    .filter((classe) => /(^|:)mt-/.test(classe))
    .sort();
}

/** L'enveloppe d'une bulle — la boîte que le `<li>` contient. */
function enveloppeDe(ligne: HTMLElement): HTMLElement {
  const enveloppe = ligne.firstElementChild;
  if (!(enveloppe instanceof HTMLElement)) {
    throw new Error("cette ligne du fil n'a pas d'enveloppe");
  }
  return enveloppe;
}

/**
 * Le pied d'une bulle : ce qu'il dit, et s'il le dit **à l'œil**. Le dernier
 * élément de l'enveloppe, par construction de `chat/BulleFil` — et non un
 * `getByText` sur le nom de l'auteur, qui remonterait aussi le contenu d'un
 * message qui nomme quelqu'un.
 */
function piedDe(ligne: HTMLElement): { dit: string; visible: boolean } {
  const pied = enveloppeDe(ligne).lastElementChild;
  if (!(pied instanceof HTMLElement) || pied.tagName !== "P") {
    throw new Error("cette bulle ne finit pas par un pied");
  }
  return {
    dit: (pied.textContent ?? "").trim(),
    visible: !pied.classList.contains("sr-only"),
  };
}

// ---------------------------------------------------------------------------
// L'échantillon fautif : le fil d'avant #876
// ---------------------------------------------------------------------------

/** Les fragments posés à la main, retirés après chaque test. */
const fixtures: HTMLElement[] = [];

afterEach(() => {
  for (const fixture of fixtures.splice(0)) fixture.remove();
});

/**
 * La section de `Conversation` telle qu'elle était avant ce lot : elle occupe
 * tout ce qu'on lui donne — 1144 px à 1920×872 — pour des bulles bornées à 543,
 * d'où les deux colonnes qui ne se recouvrent pas et le composeur deux fois
 * plus large que ce qu'on lit.
 */
const SECTION_AVANT_876 = "flex min-w-0 flex-1 flex-col gap-3 rounded-md";

/**
 * L'enveloppe d'une bulle **d'agent** avant ce lot : la chaîne exacte de
 * `chat/BulleFil`, bord, fond et ombre comprises — chaque message des deux côtés
 * était une boîte.
 */
const BULLE_AGENT_AVANT_876 =
  "min-w-0 rounded-lg px-3 py-2 text-corps shadow-sm " +
  "max-w-[85%] sm:max-w-[min(70%,72ch)] border border-bord bg-surface text-texte";

/**
 * Le fil d'avant, posé à la main : **deux messages consécutifs du même agent**,
 * chacun dans sa boîte, chacun portant sa propre ligne « auteur · heure », et
 * aucun espacement qui distingue un tour d'un autre (le `gap-3` du fil, pour
 * tout le monde). C'est l'état que les trois sondes doivent reconnaître comme
 * fautif avant qu'on croie leur verdict sur le fil réel.
 */
function filDAvant876(): HTMLElement {
  const fil = document.createElement("ol");
  fixtures.push(fil);
  fil.className = "flex flex-1 flex-col justify-end gap-3 py-1";
  fil.innerHTML = [0, 1]
    .map(
      (rang) =>
        '<li class="flex justify-start">' +
        `<div class="${BULLE_AGENT_AVANT_876}">` +
        `<p>Réponse ${rang + 1}</p>` +
        '<p class="mt-1 text-right text-micro text-texte-secondaire">dev · 10:0' +
        `${rang}</p>` +
        "</div></li>",
    )
    .join("");
  document.body.appendChild(fil);
  return fil;
}

describe("les sondes de la colonne, prouvées sur le fil d'avant #876", () => {
  it("voient une section qui ne borne ni ne centre rien", () => {
    expect(colonneDe(SECTION_AVANT_876)).toEqual({
      centree: false,
      borne: null,
    });
  });

  it("voient la bulle d'agent bordée, remplie et élevée", () => {
    expect(habillageDe(BULLE_AGENT_AVANT_876)).toEqual({
      bord: true,
      fond: true,
      ombre: true,
    });
  });

  it("voient deux messages du même agent sans tour et avec deux pieds visibles", () => {
    const lignes = Array.from(filDAvant876().children) as HTMLElement[];
    expect(lignes).toHaveLength(2);
    // Rien ne groupe : aucune ligne n'ouvre de tour, et chacune se nomme.
    for (const ligne of lignes) expect(espacementDe(ligne)).toEqual([]);
    expect(lignes.map((ligne) => piedDe(ligne).visible)).toEqual([true, true]);
    expect(piedDe(lignes[0]).dit).toMatch(/^dev · /);
  });
});

// ---------------------------------------------------------------------------
// ① La colonne de lecture — ② la bulle — ③ les tours, sur les deux surfaces
// ---------------------------------------------------------------------------

describe.each(SURFACES)("le fil sur $nom", ({ monter, interlocuteur, section }) => {
  /** Le fil de messages — la liste que `Conversation` nomme d'après l'interlocuteur. */
  function fil(): HTMLElement {
    return screen.getByRole("list", {
      name: `Messages échangés avec ${interlocuteur}`,
    });
  }

  /**
   * Les bulles du fil, dans l'ordre. Les enfants **directs** du `<ol>` et non un
   * `getAllByRole("listitem")` — le contenu d'un message porte ses propres
   * listes (ses sources, ce qu'il a ouvert) — et seuls ceux que leur côté
   * désigne : les états transitoires du pied du fil (attente, fautes) et le
   * séparateur de journée sont des `<li>` eux aussi.
   */
  function bulles(): HTMLElement[] {
    return (Array.from(fil().children) as HTMLElement[]).filter(
      (ligne) =>
        ligne.classList.contains("justify-start") ||
        ligne.classList.contains("justify-end"),
    );
  }

  // ── ① une colonne de lecture bornée et centrée ──────────────────────────
  describe("① une colonne de lecture bornée et centrée", () => {
    it("borne et centre la section entière — en-tête, fil et composeur", () => {
      poserFilAssistance({ messages: [messageFactice({ contenu: "Salut" })] });
      monter();

      // La borne est posée **sur la section** et pas sur le seul fil : c'est ce
      // qui donne au composeur la largeur de ce qu'on lit, le défaut que la
      // veille a mesuré à deux fois trop large.
      expect(colonneDe(screen.getByLabelText(section).className)).toEqual({
        centree: true,
        borne: "max-w-3xl",
      });
    });

    it("tient le fil et le composeur dans cette colonne, sans en reborner aucun", () => {
      poserFilAssistance({ messages: [messageFactice({ contenu: "Salut" })] });
      monter();

      const colonne = screen.getByLabelText(section);
      const composeur = screen
        .getByLabelText(`Message à ${interlocuteur}`)
        .closest("form");
      expect(composeur).not.toBeNull();
      expect(colonne.contains(fil())).toBe(true);
      expect(colonne.contains(composeur!)).toBe(true);
      // Une seconde borne à l'intérieur ferait deux largeurs de lecture sur le
      // même écran, c'est-à-dire le défaut qu'on corrige déplacé d'un cran.
      expect(colonneDe(fil().className).borne).toBeNull();
      expect(colonneDe(composeur!.className).borne).toBeNull();
    });
  });

  // ── ② seule la personne a une bulle ─────────────────────────────────────
  describe("② seule la personne a une bulle", () => {
    it("laisse l'agent parler dans le texte de la page", () => {
      poserFilAssistance({
        messages: [
          messageFactice({ auteur: interlocuteur, contenu: "Bonjour" }),
        ],
      });
      monter();

      // Ni bord, ni fond, ni ombre : la réponse est du texte de page, comme chez
      // ChatGPT. Le pied reste là — c'est lui, avec le côté, qui dit qui parle.
      expect(habillageDe(enveloppeDe(bulles()[0]).className)).toEqual({
        bord: false,
        fond: false,
        ombre: false,
      });
      expect(piedDe(bulles()[0]).visible).toBe(true);
      expect(piedDe(bulles()[0]).dit).toMatch(new RegExp(`^${interlocuteur} · `));
    });

    it("ne touche pas à la bulle de la personne", () => {
      poserFilAssistance({
        messages: [
          messageFactice({
            auteur: CHAT_AUTEUR_UTILISATEUR,
            contenu: "Salut",
          }),
        ],
      });
      monter();

      const enveloppe = enveloppeDe(bulles()[0]);
      // Le fond d'accent et l'ombre restent : la bulle, redevenue rare, est ce
      // qui désigne **qui** parle, et l'état reste porté par la forme (le côté,
      // puis la bulle) plus le pied — jamais par la couleur seule.
      expect(habillageDe(enveloppe.className)).toEqual({
        bord: false,
        fond: true,
        ombre: true,
      });
      expect(Array.from(enveloppe.classList)).toEqual(
        expect.arrayContaining(["bg-accent", "text-sur-ton"]),
      );
      expect(bulles()[0].classList.contains("justify-end")).toBe(true);
    });
  });

  // ── ③ un tour = un auteur, nommé une fois ───────────────────────────────
  describe("③ un tour = un auteur, nommé une fois", () => {
    /** Un tour de trois réponses entre deux messages de la personne. */
    function filGroupe() {
      poserFilAssistance({
        messages: [
          messageFactice({ contenu: "Salut", horodatage: "2026-07-28T10:00:00Z" }),
          messageFactice({
            auteur: interlocuteur,
            contenu: "Bonjour",
            horodatage: "2026-07-28T10:01:00Z",
          }),
          messageFactice({
            auteur: interlocuteur,
            contenu: "Je regarde",
            horodatage: "2026-07-28T10:02:00Z",
          }),
          messageFactice({
            auteur: interlocuteur,
            contenu: "Voilà",
            horodatage: "2026-07-28T10:03:00Z",
          }),
          messageFactice({ contenu: "Merci", horodatage: "2026-07-28T10:04:00Z" }),
        ],
      });
      monter();
    }

    it("serre l'intérieur d'un tour et espace deux tours", () => {
      filGroupe();

      // `gap-3` pour tout le monde, plus `mt-3` à l'ouverture d'un tour : 12 px
      // dedans, 24 entre — les 36/12 px de ChatGPT à notre échelle. Le premier
      // message n'ouvre rien, d'où `first:mt-0`.
      expect(Array.from(fil().classList)).toContain("gap-3");
      expect(bulles().map(espacementDe)).toEqual([
        ["first:mt-0", "mt-3"],
        ["first:mt-0", "mt-3"],
        [],
        [],
        ["first:mt-0", "mt-3"],
      ]);
    });

    it("nomme l'auteur une fois à l'œil, et à chaque message au lecteur d'écran", () => {
      filGroupe();

      // Le pied n'est visible qu'au **dernier** message d'une suite ; les autres
      // le portent en `sr-only`, si bien qu'un lecteur d'écran garde le nom à
      // chaque message (#483 — une bulle relue n'a ni gauche ni droite).
      expect(bulles().map((bulle) => piedDe(bulle).visible)).toEqual([
        true,
        false,
        false,
        true,
        true,
      ]);
      for (const bulle of bulles()) {
        expect(piedDe(bulle).dit).not.toBe("");
      }
    });

    it("coupe un tour sur un séparateur de journée", () => {
      // Deux messages du même agent, à deux jours d'écart : le trait daté les
      // sépare (#697), donc ils ne sont pas un tour — l'heure seule ne dirait
      // pas lequel est lequel, et les grouper les ferait lire comme deux
      // répliques.
      poserFilAssistance({
        messages: [
          messageFactice({
            auteur: interlocuteur,
            contenu: "Hier",
            horodatage: "2026-07-27T10:00:00Z",
          }),
          messageFactice({
            auteur: interlocuteur,
            contenu: "Aujourd'hui",
            horodatage: "2026-07-28T10:00:00Z",
          }),
        ],
      });
      monter();

      const [avant, apres] = bulles();
      expect(espacementDe(apres)).toEqual(["first:mt-0", "mt-3"]);
      // Et chacun se nomme : le premier ferme sa suite, le second ouvre la
      // sienne.
      expect(piedDe(avant).visible).toBe(true);
      expect(piedDe(apres).visible).toBe(true);
    });

    it("ne groupe pas deux côtés sous un même nom d'auteur", () => {
      // Le regroupement se juge sur l'auteur **et** le côté : le fil du cadrage
      // nomme « vous » la personne (#483), et un agent qui porterait ce nom-là
      // resterait de l'autre côté du fil. Un tour traversant la conversation
      // serait un tour à deux voix.
      poserFilAssistance({
        messages: [
          messageFactice({
            auteur: CHAT_AUTEUR_UTILISATEUR,
            contenu: "Salut",
            horodatage: "2026-07-28T10:00:00Z",
          }),
          messageFactice({
            auteur: CHAT_AUTEUR_UTILISATEUR,
            contenu: "Encore moi",
            horodatage: "2026-07-28T10:01:00Z",
          }),
          messageFactice({
            auteur: interlocuteur,
            contenu: "Bonjour",
            horodatage: "2026-07-28T10:02:00Z",
          }),
        ],
      });
      monter();

      const [un, deux, trois] = bulles();
      expect(espacementDe(deux)).toEqual([]);
      expect(espacementDe(trois)).toEqual(["first:mt-0", "mt-3"]);
      expect([un, deux, trois].map((b) => piedDe(b).visible)).toEqual([
        false,
        true,
        true,
      ]);
    });
  });
});

// ---------------------------------------------------------------------------
// ② bis — la seule exception qui ne se monte pas depuis une surface de fil
// ---------------------------------------------------------------------------

describe("② le contenu pleine largeur garde son cadre", () => {
  it("borde, remplit et élève un `pleineLargeur`, là où un message ordinaire n'a plus rien", () => {
    // Le brief de #483 est un **formulaire** de sept sections éditables, pas de
    // la prose : sans bord il ne se distinguerait plus de la page sur laquelle
    // il est posé, et le corriger redeviendrait la contorsion que la largeur
    // pleine avait supprimée. Monté ici sur la primitive et non depuis `/chat` —
    // c'est une propriété de la bulle, et le faire naître d'un run arrêté
    // ajouterait trois fixtures pour observer une chaîne de classes.
    render(
      <ol>
        <BulleFil auteur="Chef de projet" pleineLargeur>
          <p>Brief</p>
        </BulleFil>
        <BulleFil auteur="dev">
          <p>Réponse</p>
        </BulleFil>
      </ol>,
    );

    const [brief, message] = Array.from(
      screen.getAllByRole("listitem"),
    ) as HTMLElement[];
    expect(habillageDe(enveloppeDe(brief).className)).toEqual({
      bord: true,
      fond: true,
      ombre: true,
    });
    expect(Array.from(enveloppeDe(brief).classList)).toContain("w-full");
    // Le voisin, lui, n'a plus rien : la même primitive, deux habillages, et
    // c'est `pleineLargeur` qui les départage.
    expect(habillageDe(enveloppeDe(message).className)).toEqual({
      bord: false,
      fond: false,
      ombre: false,
    });
  });

  it("laisse un pied masqué hors de l'ordre de tabulation", () => {
    // Un pied `sr-only` reste dans le DOM, donc l'`Infobulle` qui porte la date
    // complète y serait un arrêt de clavier **invisible** (son wrapper est
    // focusable, #536) — un par message groupé. L'horodatage y reste un `<time>`
    // nu : ce que l'infobulle ajoute est une information au survol, et on ne
    // survole pas ce qu'on ne voit pas.
    const { container } = render(
      <ol>
        <BulleFil auteur="dev" horodatage="2026-07-28T10:00:00Z" piedVisible={false}>
          <p>Réponse</p>
        </BulleFil>
      </ol>,
    );

    const ligne = within(container).getByRole("listitem");
    const pied = piedDe(ligne);
    expect(pied.visible).toBe(false);
    // Rien de focusable dans la bulle : c'est ce que l'`Infobulle` y aurait
    // ajouté, une fois par message groupé.
    expect(ligne.querySelectorAll("[tabindex]")).toHaveLength(0);
    // L'information, elle, n'est pas perdue : le nom et l'heure restent lisibles
    // par un lecteur d'écran, et l'heure reste une donnée (`<time>`).
    expect(pied.dit).toMatch(/^dev · /);
    expect(ligne.querySelector("time")?.getAttribute("datetime")).toBe(
      "2026-07-28T10:00:00Z",
    );
  });
});

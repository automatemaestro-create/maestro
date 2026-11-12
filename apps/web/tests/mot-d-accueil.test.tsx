/**
 * **Le mot d'accueil d'un fil vide a une forme à lui, quel que soit le fond où
 * il est posé** (#1630).
 *
 * `components/Conversation` est posé sur des fonds différents : la porte du
 * premier lancement (`background`, hors du shell), la colonne de conversation du
 * shell (`surface-creuse`), `/chat` dans le contenu du shell, le panneau de
 * l'assistant (`surface`). Le mot d'accueil n'avait pour forme que son fond,
 * `bg-surface-creuse`. Or ce fond **coïncide** avec celui sur lequel il est
 * posé à deux endroits au moins. En sombre, `--surface-creuse` et `--background`
 * valent tous deux `#0a0a0a` : la bulle disparaissait sur la porte. Et la colonne
 * du shell porte elle-même `bg-surface-creuse`, dans les deux thèmes. Il ne
 * restait que le texte, sans contour.
 *
 * Un fond ne fait donc pas une forme ici : quel que soit le token choisi, un fond
 * de la palette rencontre son égal quelque part. Ce qui tient sur tous les fonds
 * est un **bord** du socle. C'est ce que dit `Carte ton="creuse"`
 * (`Primitives`, « encart d'exemple »), et ce que fait la bulle bordée de
 * `chat/BulleFil`.
 *
 * ⚠ **Aucun rendu** (#308) : jsdom ne charge aucune feuille Tailwind. La sonde lit
 * le **contrat écrit** (les utilitaires présents dans le DOM), comme celles de
 * `fil-en-colonne.test.tsx`. Elle est **prouvée sur l'échantillon fautif**
 * avant de balayer. L'effet à l'écran, dans les deux thèmes, est l'affaire de la
 * relecture visuelle.
 *
 * Ce qui ne bouge pas, et que ce fichier garde aussi : le texte du mot d'accueil
 * et sa place, première ligne du fil. Les bulles du fil sont gardées par
 * `fil-en-colonne.test.tsx` et `fil-moderne.test.tsx`.
 */

import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import PageChat from "@/app/chat/page";
import { ColonneConversation } from "@/components/ColonneConversation";
import {
  ACCUEIL_NAISSANCE,
  NaissanceProjet,
} from "@/components/projets/NaissanceProjet";
import {
  ACCUEIL_ORCHESTRATION,
  AGENT_ORCHESTRATION,
  INTERLOCUTEUR_ORCHESTRATION,
  ROLE_ORCHESTRATION,
} from "@/lib/orchestration";

import { agentFactice, poserFilAssistance, rendreAvecEtat } from "./aides";

// ---------------------------------------------------------------------------
// La sonde
// ---------------------------------------------------------------------------

/**
 * La chaîne porte-t-elle un **bord du socle** : une épaisseur (`border`) et une
 * couleur prise dans les tokens de bord (`border-bord`, `border-bord-fort`) ?
 *
 * Les deux ensemble. `border` seul prend la couleur courante du texte, hors
 * palette. `border-bord` seul ne dessine rien : la barre de `LigneConversation`
 * (#831) l'a montré, une couleur sans épaisseur reste invisible.
 */
function bordDuSocle(classes: string): boolean {
  const utilitaires = classes.split(/\s+/).filter((u) => u !== "");
  return (
    utilitaires.includes("border") &&
    utilitaires.some((u) => /^border-bord(-fort)?$/.test(u))
  );
}

/** Le mot d'accueil d'avant #1630, tel que `Conversation` l'écrivait. */
const ACCUEIL_AVANT_1630 = "rounded-lg bg-surface-creuse px-3 py-2 text-corps text-texte";

describe("la sonde du bord, prouvée avant de balayer", () => {
  it("ne voit aucun bord sur le mot d'accueil d'avant #1630", () => {
    expect(bordDuSocle(ACCUEIL_AVANT_1630)).toBe(false);
  });

  it("ne prend ni une épaisseur sans couleur du socle, ni une couleur sans épaisseur", () => {
    expect(bordDuSocle("rounded-lg border bg-surface-creuse")).toBe(false);
    expect(bordDuSocle("rounded-lg border-bord bg-surface-creuse")).toBe(false);
  });

  it("voit le bord de la bulle bordée du fil", () => {
    // La chaîne de `chat/BulleFil` en `pleineLargeur`.
    expect(bordDuSocle("min-w-0 rounded-lg border border-bord bg-surface text-texte")).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Les fonds où le mot d'accueil est posé
// ---------------------------------------------------------------------------

type Emplacement = {
  nom: string;
  monter: () => void;
  accueil: string;
  interlocuteur: string;
};

const EMPLACEMENTS: Emplacement[] = [
  {
    nom: "la porte du premier lancement, à l'étape de la conversation de naissance",
    monter: () => {
      render(<NaissanceProjet enEtape />);
    },
    accueil: ACCUEIL_NAISSANCE,
    interlocuteur: INTERLOCUTEUR_ORCHESTRATION,
  },
  {
    nom: "la colonne de conversation du shell",
    monter: () => {
      rendreAvecEtat(<ColonneConversation ouverte fermer={() => {}} />);
    },
    accueil: ACCUEIL_ORCHESTRATION,
    interlocuteur: INTERLOCUTEUR_ORCHESTRATION,
  },
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
    accueil: ACCUEIL_ORCHESTRATION,
    interlocuteur: INTERLOCUTEUR_ORCHESTRATION,
  },
];

afterEach(() => {
  poserFilAssistance();
});

describe.each(EMPLACEMENTS)("le mot d'accueil sur $nom", ({ monter, accueil, interlocuteur }) => {
  function motDAccueil(): HTMLElement {
    poserFilAssistance({ messages: [] });
    monter();
    const ligne = screen.getByText(accueil).closest("li");
    if (ligne === null) throw new Error("le mot d'accueil n'est plus une ligne du fil");
    return ligne;
  }

  it("porte un bord du socle, qui le dessine quel que soit le fond", () => {
    expect(bordDuSocle(motDAccueil().className)).toBe(true);
  });

  it("reste la première ligne du fil, avec son texte", () => {
    const ligne = motDAccueil();
    const fil = screen.getByRole("list", { name: `Messages échangés avec ${interlocuteur}` });
    expect(fil.firstElementChild).toBe(ligne);
    expect(ligne.textContent).toBe(accueil);
  });
});

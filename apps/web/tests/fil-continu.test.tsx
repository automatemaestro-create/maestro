/**
 * **La continuité du fil** (#926, lot 5 de #921 — docs/35 §3.3) : le filet que
 * ce lot a différé au lot final (#929).
 *
 * La colonne de droite change une chose au fil de l'orchestration : on n'en sort
 * plus pour aller ailleurs. Les trois critères du ticket disent la même
 * propriété sous trois angles, et ce sont eux qui sont gardés ici :
 *
 * ① **une seule conversation à l'écran** — jamais deux fils montés en même
 *    temps. Ce n'est pas qu'une affaire de doublon visuel : `useChat` ouvre une
 *    **WebSocket par instance**, donc deux montages sur `orchestrateur` en
 *    ouvriraient deux. D'où le repli de la colonne sur `/chat` (`Shell`) et le
 *    montage conditionnel du fil (`ColonneConversation`) ;
 * ② **`/chat` reste servi, et reste la MÊME conversation** — la colonne
 *    demande le même canal, avec la même portée de projet. Il n'y a rien à
 *    synchroniser parce qu'il n'y a qu'un fil ;
 * ③ **changer d'écran ne perd ni le fil, ni un message en cours de saisie** —
 *    le brouillon vit hors du composant (`lib/brouillons`), donc il survit aux
 *    deux gestes qui démontent le composeur : aller sur `/chat` (la colonne s'y
 *    replie) et replier la colonne pour voir l'écran en entier.
 *
 * ⚠ **Le repli sur `/chat` n'écrit pas la préférence**, et c'est le point le
 * plus facile à défaire sans s'en apercevoir : on masque, on ne ferme pas. Sinon
 * la page aurait éteint un réglage qui ne lui appartient pas, et en quittant
 * `/chat` la colonne resterait repliée.
 *
 * ⚠ Aucune géométrie ici (#308) : la largeur de la colonne, le recouvrement sous
 * `lg`, les 420 px sans débordement sont l'affaire de `/banc-mise-en-page`.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import PageChat from "@/app/chat/page";
import { Shell } from "@/components/Shell";
import {
  ecrireBrouillon,
  lireBrouillon,
  oublierLesBrouillons,
  useBrouillon,
} from "@/lib/brouillons";
import { marquerGuideVu } from "@/lib/guide";
import {
  AGENT_ORCHESTRATION,
  INTERLOCUTEUR_ORCHESTRATION,
} from "@/lib/orchestration";
import {
  ecrireConversationOuverte,
  lireConversationOuverte,
} from "@/lib/preferences";

import {
  canauxDemandes,
  messageFactice,
  poserChemin,
  poserFilAssistance,
  poserProjetActif,
  projetDuFilCourant,
} from "./aides";

/** La zone de saisie du fil de l'orchestration, où qu'elle soit montée. */
function saisies(): HTMLElement[] {
  return screen.queryAllByLabelText(`Message à ${INTERLOCUTEUR_ORCHESTRATION}`);
}

/** Le shell, sur `chemin`, la colonne dans l'état demandé. */
async function monterLeShell(chemin: string, colonneOuverte: boolean) {
  ecrireConversationOuverte(colonneOuverte);
  poserChemin(chemin);
  const rendu = render(
    <Shell>{chemin === "/chat" ? <PageChat /> : <p>un écran quelconque</p>}</Shell>,
  );
  await screen.findByRole("heading", { level: 1 });
  return rendu;
}

// ===========================================================================
// 1. Le brouillon, hissé hors du composant (lib/brouillons)
// ===========================================================================

describe("le brouillon d'un fil (lib/brouillons)", () => {
  beforeEach(() => {
    oublierLesBrouillons();
  });

  it("survit au démontage du composeur", () => {
    // ③ C'est toute la raison d'être du module : un `useState` disparaîtrait
    // aux deux gestes qui démontent le composeur, et aucun des deux n'est un
    // abandon.
    function Composeur() {
      const [texte, poser] = useBrouillon(AGENT_ORCHESTRATION);
      return (
        <textarea
          aria-label="brouillon"
          value={texte}
          onChange={(evenement) => poser(evenement.target.value)}
        />
      );
    }

    const premier = render(<Composeur />);
    ecrireBrouillon(AGENT_ORCHESTRATION, "une phrase à moitié tapée");
    premier.unmount();

    render(<Composeur />);
    return waitFor(() =>
      expect(screen.getByLabelText("brouillon")).toHaveValue(
        "une phrase à moitié tapée",
      ),
    );
  });

  it("garde un brouillon par interlocuteur", () => {
    // Passer de l'orchestration à un aparté avec un agent (#671) ne mélange pas
    // les deux textes.
    ecrireBrouillon(AGENT_ORCHESTRATION, "pour l'orchestration");
    ecrireBrouillon("dev", "pour dev");
    expect(lireBrouillon(AGENT_ORCHESTRATION)).toBe("pour l'orchestration");
    expect(lireBrouillon("dev")).toBe("pour dev");
  });

  it("oublie ce qui est envoyé, et ne garde jamais la chaîne vide", () => {
    ecrireBrouillon(AGENT_ORCHESTRATION, "envoyé");
    ecrireBrouillon(AGENT_ORCHESTRATION, "");
    expect(lireBrouillon(AGENT_ORCHESTRATION)).toBe("");
  });

  it("n'est pas une préférence : rien n'en part au stockage du poste", () => {
    // Un brouillon est un **geste en cours**, pas un réglage qu'on veut
    // retrouver demain. Le persister ferait ressurgir dans six jours une phrase
    // dont personne ne se souvient, dans un fil qui a continué sans elle.
    ecrireBrouillon(AGENT_ORCHESTRATION, "ne me range pas");
    const stockage = { ...window.localStorage };
    expect(
      Object.values(stockage).some((valeur) =>
        String(valeur).includes("ne me range pas"),
      ),
    ).toBe(false);
  });
});

// ===========================================================================
// 2. Une seule conversation à l'écran
// ===========================================================================

describe("la conversation dans le shell", () => {
  beforeEach(() => {
    marquerGuideVu();
    poserProjetActif();
    poserFilAssistance({ messages: [messageFactice({ contenu: "Bonjour" })] });
  });

  it("ne monte aucun fil tant que la colonne est fermée", async () => {
    // ① `useChat` ouvre une WebSocket par instance : laisser le fil dans le DOM
    // sous `hidden` en ferait tourner une sur **tous** les écrans.
    await monterLeShell("/runs", false);
    expect(saisies()).toHaveLength(0);
  });

  it("monte le fil — et un seul — quand la colonne s'ouvre", async () => {
    await monterLeShell("/runs", true);
    await waitFor(() => expect(saisies()).toHaveLength(1));
  });

  it("n'en monte qu'un sur /chat, colonne ouverte ou non", async () => {
    // ① Le repli de la colonne sur `/chat` : le fil y occupe déjà le centre, et
    // deux fils montés sur `orchestrateur` ouvriraient deux sockets.
    await monterLeShell("/chat", true);
    await waitFor(() => expect(saisies()).toHaveLength(1));
    expect(
      screen.queryByRole("complementary", { name: "Conversation" }),
    ).not.toBeInTheDocument();
  });

  it("masque la colonne sur /chat sans éteindre la préférence", async () => {
    // Le point à ne pas défaire : on masque, on ne ferme pas. L'écrire à `false`
    // ferait qu'en quittant `/chat` la colonne resterait repliée — la page
    // aurait éteint un réglage qui ne lui appartient pas.
    await monterLeShell("/chat", true);
    await screen.findByRole("heading", { level: 1 });
    expect(lireConversationOuverte()).toBe(true);
  });

  it("demande le même canal et la même portée que /chat", async () => {
    // ② « La même conversation » n'est pas une synchronisation : c'est le même
    // appel. Le fil rendu est factice, mais **ce qu'on lui demande** est
    // exactement ce que le critère promet.
    const projet = poserProjetActif();
    canauxDemandes.length = 0;
    await monterLeShell("/runs", true);
    await waitFor(() => expect(saisies()).toHaveLength(1));
    expect(canauxDemandes).toContain(AGENT_ORCHESTRATION);
    expect(projetDuFilCourant()).toBe(projet.id);
  });
});

// ===========================================================================
// 3. Ce qu'on écrivait suit
// ===========================================================================

describe("le message en cours de saisie", () => {
  beforeEach(() => {
    marquerGuideVu();
    poserProjetActif();
    poserFilAssistance({ messages: [messageFactice({ contenu: "Bonjour" })] });
    oublierLesBrouillons();
  });

  it("commencé dans la colonne, se retrouve en grand sur /chat", async () => {
    // ③ Le sens fort du critère plutôt que sa lettre : ce n'est pas seulement
    // que le brouillon survive, c'est qu'il **suive**.
    const colonne = await monterLeShell("/runs", true);
    await waitFor(() => expect(saisies()).toHaveLength(1));
    await userEvent.type(saisies()[0], "et si on paginait");
    colonne.unmount();

    await monterLeShell("/chat", true);
    await waitFor(() => expect(saisies()).toHaveLength(1));
    await waitFor(() => expect(saisies()[0]).toHaveValue("et si on paginait"));
  });

  it("survit au repli de la colonne, puis à sa réouverture", async () => {
    const ouverte = await monterLeShell("/runs", true);
    await waitFor(() => expect(saisies()).toHaveLength(1));
    await userEvent.type(saisies()[0], "à reprendre");

    // Replier démonte le composeur — c'est ce que #926 a mesuré, et c'est
    // précisément le geste qui perdait le texte avant ce module.
    await userEvent.click(
      screen.getByRole("button", { name: "Replier la conversation" }),
    );
    await waitFor(() => expect(saisies()).toHaveLength(0));

    await userEvent.click(
      screen.getByRole("button", { name: "Déplier la conversation" }),
    );
    await waitFor(() => expect(saisies()).toHaveLength(1));
    await waitFor(() => expect(saisies()[0]).toHaveValue("à reprendre"));
    ouverte.unmount();
  });
});

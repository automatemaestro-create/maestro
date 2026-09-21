/**
 * Le chat d'un agent d'équipe est cadré par le projet actif (#1175).
 *
 * Depuis #1122, l'écran des agents montre l'équipe **du projet**. Son onglet Chat
 * appelait pourtant `/api/chat/{agent}` sans projet : l'API cherchait l'agent parmi
 * les gabarits, et un `developpeur-2` rendait 404. Les appels de chat passent
 * désormais par le cadre des routes de configuration d'agent (#1038), comme le
 * playbook ou les autorisations du même agent.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  chargerConversationsChat,
  chargerFilChat,
  ouvrirConversationChat,
} from "@/lib/api";
import { ecrireProjetActifId } from "@/lib/projetActif";

function espion(): string[] {
  const urls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      urls.push(url);
      return new Response(
        JSON.stringify({
          agent: "developpeur-2",
          role: "Développeur",
          messages: [],
          conversations: [],
          conversation: { id: "c1" },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }),
  );
  return urls;
}

afterEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

describe("le chat d'un agent porte le projet actif", () => {
  it("cadre le fil, ses conversations et leur ouverture", async () => {
    const urls = espion();
    ecrireProjetActifId("prj-7f3a1c2b");

    await chargerFilChat("developpeur-2", "c1");
    await chargerConversationsChat("developpeur-2");
    await ouvrirConversationChat("developpeur-2");

    expect(urls).toHaveLength(3);
    for (const url of urls) {
      expect(url).toContain("/api/chat/developpeur-2");
      expect(url).toContain("projet=prj-7f3a1c2b");
    }
    // Le cadre s'ajoute à la requête existante, sans la remplacer.
    expect(urls[0]).toContain("conversation=c1&projet=prj-7f3a1c2b");
  });

  it("ne cadre rien sans projet actif : les gabarits, comme avant", async () => {
    const urls = espion();

    await chargerFilChat("developpeur");

    expect(urls[0]).not.toContain("projet=");
  });
});

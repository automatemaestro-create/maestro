/**
 * La couture entre le flux SSE et le fil : `useChat` consomme les trames (#695).
 *
 * **Le reste des tests du chat global est différé au lot 8 (#698)** — ce fichier
 * ne garde que ce que la règle du dépôt appelle une logique critique, c'est-à-dire
 * les trois invariants qui se cassent en silence :
 *
 * 1. la réponse s'écrit **et** ne se dédouble pas. La même paire arrive deux
 *    fois — par le flux, puis par le fil rechargé que le `chat.message` du
 *    WebSocket déclenche — et c'est le piège que le ticket nomme ;
 * 2. un flux **cassé** ne perd ni le message utilisateur ni la portion reçue, et
 *    la lève en `ErreurReponse` : c'est ce code-là qui dit à l'écran de ne pas
 *    remettre le brouillon dans la saisie ;
 * 3. la réponse figée **s'efface** quand une vraie réponse au même message
 *    rejoint le fil — sans quoi la garantie du point 1 tombe précisément dans le
 *    cas où le backend achève sa production malgré la coupure (#268).
 *
 * ⚠ Ce fichier est le seul à jouer le **vrai** `useChat` : `tests/setup.ts` le
 * remplace partout ailleurs par un fil immobile, ce qui est exactement ce qu'il
 * faut pour juger un écran et exactement ce qui empêche de juger le hook. D'où
 * le `vi.unmock` ci-dessous — et d'où le fait que les assertions d'**écran** du
 * direct vivent, elles, dans `chat-global.test.tsx`, sur le hook factice.
 */

import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.unmock("@/lib/useChat");

import { ErreurReponse } from "@/lib/api";
import type { MessageChat } from "@/lib/types";
import { useChat } from "@/lib/useChat";

const DEMANDE: MessageChat = {
  agent: "qa",
  auteur: "utilisateur",
  contenu: "Salut",
  horodatage: "2026-08-28T10:00:00+00:00",
  run_id: "",
  tache_id: "",
};

const REPONSE: MessageChat = {
  agent: "qa",
  auteur: "qa",
  contenu: "Bonjour",
  horodatage: "2026-08-28T10:00:02+00:00",
  run_id: "",
  tache_id: "",
};

function sse(...trames: Record<string, unknown>[]): Response {
  return new Response(
    trames.map((t) => `data: ${JSON.stringify(t)}\n\n`).join(""),
    { status: 200, headers: { "Content-Type": "text/event-stream" } },
  );
}

function trame(type: string, extra: Record<string, unknown> = {}) {
  return {
    type,
    agent: "qa",
    auteur: "qa",
    delta: "",
    message: null,
    echange: "e1",
    ...extra,
  };
}

class SocketFactice {
  close() {}
}

function installer(fil: () => MessageChat[], flux: () => Response) {
  vi.stubGlobal("WebSocket", SocketFactice);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if ((init?.method ?? "GET") === "GET") {
        return new Response(
          JSON.stringify({ agent: "qa", role: "QA", messages: fil() }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      return flux();
    }),
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("useChat consomme le flux", () => {
  it("écrit la réponse au fil de l'eau puis ne la dédouble pas", async () => {
    let persistes: MessageChat[] = [];
    installer(
      () => persistes,
      () => {
        // Le backend a persisté la paire au moment où le flux se clôt.
        persistes = [DEMANDE, REPONSE];
        return sse(
          trame("debut", { message: DEMANDE }),
          trame("fragment", { delta: "Bon" }),
          trame("fragment", { delta: "jour" }),
          trame("fin", { message: REPONSE }),
        );
      },
    );

    const { result } = renderHook(() => useChat("qa"));
    await waitFor(() => expect(result.current.chargement).toBe(false));

    await act(async () => {
      await result.current.envoyer("Salut");
    });

    // La bulle en cours a rendu la main au message persisté…
    expect(result.current.reponseEnCours).toBeNull();
    expect(result.current.envoi).toBe(false);
    // …et le fil ne porte la paire **qu'une fois**, alors qu'elle est arrivée
    // deux fois : par le flux, puis par le rechargement.
    expect(result.current.messages.map((m) => m.contenu)).toEqual([
      "Salut",
      "Bonjour",
    ]);
  });

  it("fige ce qui a été reçu quand le flux casse, et dit que le message est acquis", async () => {
    installer(
      () => [DEMANDE],
      () =>
        sse(
          trame("debut", { message: DEMANDE }),
          trame("fragment", { delta: "Bonj" }),
          trame("erreur", { delta: "réponse interrompue après 4 caractère(s)" }),
        ),
    );

    const { result } = renderHook(() => useChat("qa"));
    await waitFor(() => expect(result.current.chargement).toBe(false));

    let leve: unknown = null;
    await act(async () => {
      await result.current.envoyer("Salut").catch((e: unknown) => {
        leve = e;
      });
    });

    expect(leve).toBeInstanceOf(ErreurReponse);
    expect((leve as ErreurReponse).recu).toBe("Bonj");
    // Ce qui a été lu reste à l'écran, marqué incomplet.
    expect(result.current.reponseEnCours).toEqual({
      auteur: "qa",
      texte: "Bonj",
      figee: true,
    });
    // Et le message utilisateur, lui, n'est pas perdu.
    expect(result.current.messages.map((m) => m.contenu)).toEqual(["Salut"]);
  });

  it("solde la réponse figée dès qu'une vraie réponse rejoint le fil", async () => {
    let persistes: MessageChat[] = [DEMANDE];
    installer(
      () => persistes,
      () =>
        sse(
          trame("debut", { message: DEMANDE }),
          trame("fragment", { delta: "Bonj" }),
          trame("erreur", { delta: "coupé" }),
        ),
    );

    const { result } = renderHook(() => useChat("qa"));
    await waitFor(() => expect(result.current.chargement).toBe(false));
    await act(async () => {
      await result.current.envoyer("Salut").catch(() => {});
    });
    expect(result.current.reponseEnCours?.figee).toBe(true);

    // Le backend finit sa réponse malgré la coupure (#268) : elle arrive au fil.
    persistes = [DEMANDE, REPONSE];
    await act(async () => {
      await result.current.envoyer("").catch(() => {});
    });

    await waitFor(() =>
      expect(result.current.messages.map((m) => m.contenu)).toEqual([
        "Salut",
        "Bonjour",
      ]),
    );
  });
});

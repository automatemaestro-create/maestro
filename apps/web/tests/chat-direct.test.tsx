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

  it("range les trames « etape » à part du texte, et les garde dans l'ordre", async () => {
    // #1223 : une lecture n'est pas un incrément. Le contrat SSE dit que la
    // concaténation des `delta` **est** le message final ; si une étape y
    // entrait, un client qui recolle ses `delta` n'obtiendrait plus la trame
    // `fin` — et `ErreurReponse.recu` mentirait sur ce qui a été reçu.
    let persistes: MessageChat[] = [];
    installer(
      () => persistes,
      () => {
        persistes = [DEMANDE, REPONSE];
        return sse(
          trame("debut", { message: DEMANDE }),
          trame("etape", {
            etape: { libelle: "A lu « README.md »", detail: "npm run dev" },
          }),
          trame("etape", {
            etape: { libelle: "A cherché « npm »", detail: "README.md:5" },
          }),
          trame("fragment", { delta: "Bon" }),
          trame("fin", { message: REPONSE }),
        );
      },
    );

    const { result } = renderHook(() => useChat("qa"));
    await waitFor(() => expect(result.current.chargement).toBe(false));

    const vues: string[][] = [];
    await act(async () => {
      await result.current.envoyer("Salut");
    });
    vues.push(result.current.messages.map((m) => m.contenu));

    // Le flux s'est clos : la bulle en cours a rendu la main, et le texte reçu
    // ne porte rien des étapes.
    expect(result.current.reponseEnCours).toBeNull();
    expect(vues[0]).toEqual(["Salut", "Bonjour"]);
  });

  it("montre les lectures dans la bulle en cours, avant le premier mot", async () => {
    // Le « pendant qu'il répond » du critère 2, vu du hook : les étapes sont
    // dans `reponseEnCours` alors que `texte` est encore vide.
    let vue: { texte: string; etapes: { libelle: string }[] } | null = null;
    installer(
      () => [DEMANDE],
      () =>
        sse(
          trame("debut", { message: DEMANDE }),
          trame("etape", {
            etape: { libelle: "A lu « README.md »", detail: "npm run dev" },
          }),
          trame("erreur", { delta: "coupé" }),
        ),
    );

    const { result } = renderHook(() => useChat("qa"));
    await waitFor(() => expect(result.current.chargement).toBe(false));

    await act(async () => {
      await result.current.envoyer("Salut").catch(() => {});
    });
    vue = result.current.reponseEnCours;

    expect(vue).not.toBeNull();
    expect(vue?.texte).toBe("");
    expect(vue?.etapes.map((e) => e.libelle)).toEqual(["A lu « README.md »"]);
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
      // Aucune étape ici (#1223) : ce flux n'en a publié aucune, et un agent du
      // catalogue ne lit rien du projet — seul l'orchestrateur le fait.
      etapes: [],
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

/**
 * Une coupure du flux temps réel relit l'état, et c'est la lecture qui dit la
 * panne (#1217).
 *
 * Vu sur la vraie stack à la relecture de #1217 : l'API coupée, un écran atteint
 * par le menu qui lit l'état du shell — `/validations`, `/runs`, le tableau de
 * bord — n'affichait **aucune** bannière. Rien n'avait échoué : le hook ne relit
 * qu'à la reconnexion, qui n'arrive jamais tant que l'API est tombée, si bien
 * que l'écran gardait ses valeurs d'avant et son « Rien encore… aucune demande
 * d'arbitrage » sous la seule pastille « Reconnexion… ». La coupure relit
 * désormais : sur une API tombée la lecture échoue, `erreur` le porte, et les
 * écrans rendent la panne (`tests/ecran-en-panne.test.tsx`).
 *
 * Le hook est ici le **vrai** : `tests/setup.ts` le remplace d'ordinaire par un
 * double, que ce fichier lève. Le réseau, lui, reste débranché — une socket
 * factice, et un `fetch` qui répond des listes vides ou rejette comme une API
 * éteinte. Débranché **au `fetch`** et non en remplaçant les lectures de
 * `@/lib/api` : le setup a déjà chargé ce hook, lié au client qu'il connaît, et
 * un double posé ici n'y arriverait pas.
 */

import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useControlTower } from "@/lib/useControlTower";

vi.mock("@/lib/useControlTower", async (original) => await original());

/** L'API répond-elle ? Et combien de fois l'a-t-on interrogée. */
const api = { debout: true, lectures: 0 };

function fetchFactice(): Promise<Response> {
  api.lectures += 1;
  return api.debout
    ? Promise.resolve(
        new Response("[]", {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      )
    : Promise.reject(new TypeError("Failed to fetch"));
}

/** Une WebSocket qu'on ouvre et qu'on coupe à la main. */
class SocketFactice {
  static ouvertes: SocketFactice[] = [];
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onmessage: ((message: MessageEvent<string>) => void) | null = null;
  onerror: (() => void) | null = null;

  constructor() {
    SocketFactice.ouvertes.push(this);
  }

  close() {
    this.onclose?.();
  }
}

function derniereSocket(): SocketFactice {
  const socket = SocketFactice.ouvertes.at(-1);
  if (socket === undefined) throw new Error("aucune socket ouverte");
  return socket;
}

beforeEach(() => {
  api.debout = true;
  api.lectures = 0;
  SocketFactice.ouvertes = [];
  vi.stubGlobal("WebSocket", SocketFactice);
  vi.stubGlobal("fetch", fetchFactice);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("une coupure du flux relit l'état", () => {
  it("dit la panne quand l'API est tombée avec le flux", async () => {
    const { result, unmount } = renderHook(() => useControlTower("prj-1"));
    derniereSocket().onopen?.();
    // Les deux lectures du démarrage — la planifiée et celle de l'ouverture —
    // doivent être **terminées** avant la coupure : une lecture encore en vol
    // échouerait après elle, et ferait passer ce test sans la relecture qu'il
    // garde (mesuré sur le hook d'avant #1217).
    await waitFor(() => expect(result.current.revision).toBe(2));
    expect(result.current.erreur).toBeNull();

    api.debout = false;
    derniereSocket().onclose?.();

    // Le type de la panne est celui de #996 : rien n'a répondu.
    await waitFor(() =>
      expect(result.current.erreur).toMatchObject({ statut: null }),
    );
    expect(result.current.connecte).toBe(false);
    unmount();
  });

  it("ne change rien quand l'API répond encore", async () => {
    const { result, unmount } = renderHook(() => useControlTower("prj-1"));
    derniereSocket().onopen?.();
    await waitFor(() => expect(result.current.revision).toBe(2));
    const avant = api.lectures;

    derniereSocket().onclose?.();

    // La relecture a lieu, et elle aboutit : aucune panne à dire.
    await waitFor(() => expect(result.current.revision).toBe(3));
    expect(api.lectures).toBeGreaterThan(avant);
    expect(result.current.erreur).toBeNull();
    unmount();
  });
});

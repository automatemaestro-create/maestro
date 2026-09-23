/**
 * L'API qui a perdu son magasin le dit, et l'écran aussi (#1206).
 *
 * Jusqu'ici, un Redis coupé donnait des lectures en 200 vides — un « aucune
 * donnée » menteur. L'API refuse désormais en `503` et **nomme** la panne dans
 * le corps (`panne: "magasin"`, l'état du magasin champ par champ). Ce qui se
 * garde ici, dans l'ordre où la panne voyage :
 *
 * ① **La lecture la reconnaît par son champ**, jamais dans le texte du
 *    `detail` — sur le chemin commun (`chargerJson`) comme sur celui de la porte
 *    des projets (`lireProjets`), qui passait par un refus motivé et la rangeait
 *    en « Lecture impossible ».
 * ② **Le bandeau d'écran la nomme** comme le serveur : le nom, ce qu'elle coûte,
 *    le geste sur la même ligne, la commande et le lieu en annexe — jamais
 *    « l'API a répondu en erreur » ni « voir le journal ».
 * ③ **Le bandeau système du shell la dit en route** (variante C, consignée sur
 *    le ticket), sur la seule foi de `/api/sante` : c'est lui qui parle quand un
 *    écran déjà chargé ne relit rien. Et **un seul message à la fois** : quand
 *    il parle, le bandeau d'écran de la même panne se tait.
 */

import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  BandeauMagasin,
  BanniereErreurApi,
} from "@/components/BanniereErreurApi";
import { chargerRepertoireProjets, chargerTaches, ErreurApi } from "@/lib/api";
import { FournisseurMagasin } from "@/lib/magasin";
import type { EtatMagasin, Sante } from "@/lib/types";

// La sonde de santé, pilotée test par test ; le reste du client est le vrai
// (`chargerJson`, `lireProjets`), servi par un `fetch` substitué. Une fonction
// simple et non un `vi.fn` : l'espion suit les promesses qu'il rend, et le rejet
// voulu d'une API injoignable remonterait comme l'erreur du test.
const sonde = vi.hoisted(() => ({
  appels: 0,
  reponse: (): Promise<unknown> => new Promise(() => {}),
}));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  chargerSante: () => {
    sonde.appels += 1;
    return sonde.reponse();
  },
}));

const COMMANDE = "docker compose -f infra/docker-compose.yml up -d redis";

const PERDU: EtatMagasin = {
  disponible: false,
  lieu: "Redis, redis://127.0.0.1:6379/0",
  titre: "Magasin des événements injoignable",
  motif:
    "Error 10061 connecting to 127.0.0.1:6379. Rien de ce que l'écran montrerait n'est à jour",
  geste: "relancer Redis, l'API reprend seule",
  commande: COMMANDE,
};

/** Le 503 que rend la garde de l'API (`maestro.controltower.magasin`). */
function refusDuMagasin(): Response {
  return new Response(
    JSON.stringify({
      detail: "Magasin des événements injoignable (…) — relancer Redis",
      panne: "magasin",
      magasin: PERDU,
    }),
    { status: 503, headers: { "Content-Type": "application/json" } },
  );
}

function stubFetch(reponse: () => Response) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => reponse()),
  );
}

function texte(element: HTMLElement): string {
  return (element.textContent ?? "").replace(/\s+/g, " ").trim();
}

beforeEach(() => {
  sonde.appels = 0;
  sonde.reponse = () => new Promise(() => {});
});
afterEach(() => vi.unstubAllGlobals());

// ① ----------------------------------------------------------------------

describe("la lecture reconnaît la perte du magasin par son champ", () => {
  it("sur le chemin commun des écrans", async () => {
    stubFetch(refusDuMagasin);

    const panne = await chargerTaches("prj-demo").catch((e: unknown) => e);

    expect(panne).toBeInstanceOf(ErreurApi);
    expect((panne as ErreurApi).statut).toBe(503);
    expect((panne as ErreurApi).magasin).toEqual(PERDU);
  });

  it("sur le chemin de la porte des projets, qui ne la range plus en refus motivé", async () => {
    stubFetch(refusDuMagasin);

    const panne = await chargerRepertoireProjets().catch((e: unknown) => e);

    expect(panne).toBeInstanceOf(ErreurApi);
    expect((panne as ErreurApi).magasin?.titre).toBe(
      "Magasin des événements injoignable",
    );
  });

  it("ne la devine pas dans le texte : un 503 sans le champ reste une réponse en erreur", async () => {
    stubFetch(
      () =>
        new Response(
          JSON.stringify({ detail: "Magasin des événements injoignable" }),
          { status: 503, headers: { "Content-Type": "application/json" } },
        ),
    );

    const panne = await chargerTaches("prj-demo").catch((e: unknown) => e);

    expect((panne as ErreurApi).magasin).toBeNull();
  });
});

// ② ----------------------------------------------------------------------

describe("le bandeau d'écran nomme la panne comme le serveur", () => {
  it("dit le nom, ce qu'elle coûte et le geste, avec la commande en annexe", () => {
    render(
      <BanniereErreurApi
        erreur={new ErreurApi("/api/projets", 503, "…", PERDU)}
      />,
    );

    const alerte = screen.getByRole("alert");
    const principal = texte(alerte.querySelector("p") as HTMLElement);
    expect(principal).toBe(
      "Magasin des événements injoignable — Error 10061 connecting to 127.0.0.1:6379. " +
        "Rien de ce que l'écran montrerait n'est à jour · relancer Redis, l'API reprend seule.",
    );
    expect(texte(alerte)).toContain(
      `${COMMANDE} · Redis, redis://127.0.0.1:6379/0`,
    );
    // Ni l'ancienne classe, ni son geste faux pour cette panne.
    expect(texte(alerte)).not.toContain("a répondu en erreur");
    expect(texte(alerte)).not.toContain("voir le journal");
  });
});

// ③ ----------------------------------------------------------------------

function sante(corps: Sante) {
  sonde.reponse = () => Promise.resolve(corps);
}

describe("le bandeau système dit la panne en route", () => {
  it("se montre quand la santé dit le magasin perdu", async () => {
    sante({ statut: "degrade", magasin: PERDU });

    render(
      <FournisseurMagasin>
        <BandeauMagasin />
      </FournisseurMagasin>,
    );

    const alerte = await screen.findByRole("alert");
    expect(texte(alerte)).toContain("Magasin des événements injoignable");
    expect(texte(alerte)).toContain(COMMANDE);
  });

  it("se tait quand le magasin répond", async () => {
    sante({
      statut: "ok",
      magasin: { ...PERDU, disponible: true, titre: null, motif: null },
    });

    render(
      <FournisseurMagasin>
        <BandeauMagasin />
      </FournisseurMagasin>,
    );

    await waitFor(() => expect(sonde.appels).toBeGreaterThan(0));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("se tait quand l'API est injoignable — ce n'est pas la même panne", async () => {
    sonde.reponse = () => Promise.reject(ErreurApi.injoignable("/api/sante"));

    render(
      <FournisseurMagasin>
        <BandeauMagasin />
      </FournisseurMagasin>,
    );

    await waitFor(() => expect(sonde.appels).toBeGreaterThan(0));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("un seul message à la fois : le bandeau d'écran de la même panne se tait", async () => {
    sante({ statut: "degrade", magasin: PERDU });

    render(
      <FournisseurMagasin>
        <BandeauMagasin />
        <BanniereErreurApi
          erreur={new ErreurApi("/api/taches", 503, "…", PERDU)}
        />
      </FournisseurMagasin>,
    );

    await screen.findByRole("alert");
    expect(screen.getAllByRole("alert")).toHaveLength(1);
  });

  it("mais une autre panne d'écran reste dite sous le bandeau système", async () => {
    sante({ statut: "degrade", magasin: PERDU });

    render(
      <FournisseurMagasin>
        <BandeauMagasin />
        <BanniereErreurApi erreur={ErreurApi.injoignable("/api/taches")} />
      </FournisseurMagasin>,
    );

    await waitFor(() => expect(screen.getAllByRole("alert")).toHaveLength(2));
  });
});

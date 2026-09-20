/**
 * La bannière d'erreur nomme **la panne qui a eu lieu** (#996).
 *
 * Le défaut d'origine tenait en une phrase : quand l'API répondait 500, les
 * treize écrans qui montent `BanniereErreurApi` affichaient « API injoignable
 * … — vérifier que le backend tourne », c'est-à-dire un diagnostic faux et un
 * geste inutile — le backend venait de répondre —, et le `detail` que l'API
 * rendait était jeté par `chargerJson`.
 *
 * Deux choses se gardent ici, et elles ne se recouvrent pas :
 *
 * ① **La classe de la panne vient de la lecture**, pas de l'écran. `chargerJson`
 *    distingue « rien n'a répondu » (le `fetch` rejette) de « le serveur a
 *    répondu en erreur » (4xx/5xx), et porte dans les deux cas ce qu'il sait :
 *    la route, le statut, le motif. Un écran n'a donc rien à deviner.
 * ② **La bannière lit ce type, jamais le texte.** C'est le point qui se perdrait
 *    le plus discrètement : une bannière qui chercherait « a répondu » dans le
 *    message rendrait le bon verdict aujourd'hui et le mauvais à la première
 *    reformulation — le dépôt refuse de juger un texte par son vocabulaire.
 *    D'où le test qui passe une **chaîne** disant « a répondu 500 » et vérifie
 *    qu'elle n'est PAS traitée comme une réponse du serveur.
 */

import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { BanniereErreurApi } from "@/components/BanniereErreurApi";
import { chargerTaches, ErreurApi, panneDe } from "@/lib/api";

const ROUTE = "/api/taches?projet=prj-demo";

function stubFetch(reponse: () => Response | Promise<Response>) {
  const fetch = vi.fn(async () => reponse());
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

/** Ce que la bannière rend, texte nettoyé de ses espaces insécables et doubles. */
function texteDeLaBanniere(): string {
  return (screen.getByRole("alert").textContent ?? "").replace(/\s+/g, " ").trim();
}

afterEach(() => vi.unstubAllGlobals());

describe("la lecture porte la classe de la panne", () => {
  it("rend une panne « aucune réponse » quand le fetch rejette", async () => {
    stubFetch(() => Promise.reject(new TypeError("Failed to fetch")));

    const panne = await chargerTaches("prj-demo").catch((e: unknown) => e);

    expect(panne).toBeInstanceOf(ErreurApi);
    expect((panne as ErreurApi).statut).toBeNull();
    expect((panne as ErreurApi).chemin).toBe(ROUTE);
  });

  it("rend le statut ET le motif du serveur quand l'API répond en erreur", async () => {
    // Le `detail` est ce que l'API rend de plus utile, et c'est précisément ce
    // que la lecture jetait : `envoyerJson` le relayait déjà pour les écritures.
    stubFetch(
      () =>
        new Response(JSON.stringify({ detail: "projet inconnu" }), {
          status: 500,
          headers: { "Content-Type": "application/json" },
        }),
    );

    const panne = (await chargerTaches("prj-demo").catch(
      (e: unknown) => e,
    )) as ErreurApi;

    expect(panne).toBeInstanceOf(ErreurApi);
    expect(panne.statut).toBe(500);
    expect(panne.motif).toBe("projet inconnu");
  });

  it("garde le statut quand le corps n'est pas exploitable", async () => {
    // Une page d'erreur de proxy, un corps vide : il n'y a pas de motif, et
    // l'absence de motif ne doit pas faire perdre le statut.
    stubFetch(() => new Response("<html>502</html>", { status: 502 }));

    const panne = (await chargerTaches("prj-demo").catch(
      (e: unknown) => e,
    )) as ErreurApi;

    expect(panne.statut).toBe(502);
    expect(panne.motif).toBe("");
  });

  it("laisse passer les pannes qui ne sont pas des lectures d'API", () => {
    // `panneDe` est le seul endroit où un `catch` choisit : la panne typée si
    // c'en est une, le texte sinon. Un écran ne réécrit pas ce choix.
    const typee = ErreurApi.injoignable(ROUTE);
    expect(panneDe(typee)).toBe(typee);
    expect(panneDe(new Error("brouillon vide"))).toBe("brouillon vide");
    expect(panneDe("refus brut")).toBe("refus brut");
  });
});

describe("la bannière nomme la panne", () => {
  it("ne rend rien quand il n'y a pas de panne", () => {
    const { container } = render(<BanniereErreurApi erreur={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("dit « injoignable » et envoie vérifier le backend quand rien n'a répondu", () => {
    render(<BanniereErreurApi erreur={ErreurApi.injoignable(ROUTE)} />);

    const texte = texteDeLaBanniere();
    expect(texte).toContain("API injoignable");
    expect(texte).toContain("maestro-api");
    expect(texte).toContain(ROUTE);
  });

  it("dit que l'API a répondu, avec son code et son motif — et n'envoie plus démarrer un backend qui tourne", () => {
    render(
      <BanniereErreurApi erreur={new ErreurApi(ROUTE, 500, "projet inconnu")} />,
    );

    const texte = texteDeLaBanniere();
    // Le verdict, d'abord : c'est tout le ticket.
    expect(texte).not.toContain("API injoignable");
    expect(texte).not.toContain("vérifier que le backend tourne");
    // Puis ce qu'on en sait : le motif du serveur, son code, sa route.
    expect(texte).toContain("projet inconnu");
    expect(texte).toContain("500");
    expect(texte).toContain(ROUTE);
  });

  it("reste utile quand le serveur répond sans motif", () => {
    render(<BanniereErreurApi erreur={new ErreurApi(ROUTE, 502, "")} />);

    const texte = texteDeLaBanniere();
    expect(texte).not.toContain("API injoignable");
    expect(texte).toContain("502");
  });

  it("ne devine pas la panne en relisant le message", () => {
    // Une chaîne qui *dit* « a répondu 500 » n'est pas une réponse du serveur :
    // c'est un texte, et la bannière n'a aucun moyen — ni aucun droit — d'en
    // déduire une classe. Elle le montre tel quel, sans code ni geste inventés.
    render(<BanniereErreurApi erreur="/api/taches a répondu 500" />);

    const texte = texteDeLaBanniere();
    expect(texte).toContain("/api/taches a répondu 500");
    expect(texte).not.toContain("API injoignable");
    expect(texte).not.toContain("voir le journal");
  });

  it("porte son état autrement que par la couleur", () => {
    // Le filet a11y du socle : un état qui ne tient qu'à une teinte ne se lit
    // pas en niveaux de gris. Ici l'icône et le libellé en tête le portent.
    const { container } = render(
      <BanniereErreurApi erreur={ErreurApi.injoignable(ROUTE)} />,
    );

    expect(container.querySelector("svg")).not.toBeNull();
    expect(screen.getByText("API injoignable")).toBeInTheDocument();
  });
});

/**
 * Le **jeton de l'API locale**, envoyé sans que personne le manipule (#638).
 *
 * L'API sert durcie : chaque requête porte `Authorization: Bearer <jeton>`, et
 * sans lui elle répond `401`. Ce que ce fichier tient n'est pas le refus — il
 * vit côté Python (`tests/test_acces_api.py`) — mais la moitié cliente du
 * contrat, et elle a deux propriétés qu'un test peut perdre de vue :
 *
 * 1. **toutes** les requêtes le portent, pas seulement les lectures. D'où la
 *    couverture d'un POST JSON et d'un téléversement multipart : ce sont les
 *    deux formes d'appel où un `headers:` propre pourrait écraser le lot commun,
 *    et le second n'en pose aucun de lui-même ;
 * 2. le **WebSocket** le porte autrement — en paramètre d'URL, un navigateur
 *    n'ayant pas d'en-tête à donner sur une poignée de main. L'oublier laisserait
 *    le flux temps réel dehors alors que le REST passerait.
 *
 * Le contre-cas est du même poids : sans jeton réglé (régime `ouvert`), l'appel
 * doit redevenir **exactement** celui d'avant ce lot — aucun en-tête vide, aucun
 * paramètre d'URL —, faute de quoi le mode ouvert servirait une requête que
 * personne n'a demandée.
 *
 * Le jeton se pose par l'**environnement**, celui que le lanceur donne au front,
 * et c'est `lib/jetonApi` qui le lit — **à chaque appel**, jamais figé à
 * l'import. C'est ce qui rend les deux régimes jouables dans le même fichier :
 * `@/lib/api` est déjà évalué quand un test démarre (`tests/setup.ts` le mocke
 * en gardant l'original), et une constante d'import serait hors d'atteinte.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  PORTEE_TOUS,
  reassignerTache,
  televerserSources,
  urlEvenements,
} from "@/lib/api";

// La **vraie** sonde de santé : `tests/setup.ts` la remplace depuis que le shell
// la lit (#1206), et c'est ici l'en-tête qu'elle pose qu'on regarde.
const { chargerSante } =
  await vi.importActual<typeof import("@/lib/api")>("@/lib/api");

const JETON = "jeton-de-test-638";

/** Le jeton que le front verra — posé comme `start.sh` le pose, par l'environnement. */
function poserJeton(valeur: string) {
  vi.stubEnv("NEXT_PUBLIC_MAESTRO_API_JETON", valeur);
}

/** Un `fetch` muet qui retient ce qu'on lui a passé. */
function espionFetch(corps: unknown = {}) {
  const espion = vi.fn<
    (url: string | URL, init?: RequestInit) => Promise<Response>
  >(async () => new Response(JSON.stringify(corps), { status: 200 }));
  vi.stubGlobal("fetch", espion);
  return espion;
}

/** Les en-têtes de l'appel retenu. */
function entetesDe(
  espion: ReturnType<typeof espionFetch>,
): Record<string, string> {
  const init = espion.mock.calls[0]?.[1];
  return (init?.headers ?? {}) as Record<string, string>;
}

beforeEach(() => {
  poserJeton("");
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("avec un jeton réglé", () => {
  beforeEach(() => {
    poserJeton(JETON);
  });

  it("le pose sur une lecture", async () => {
    const espion = espionFetch({ statut: "ok", espace: "commun" });

    await chargerSante();

    expect(entetesDe(espion).Authorization).toBe(`Bearer ${JETON}`);
  });

  it("le pose sur une écriture JSON, sans perdre le type du corps", async () => {
    const espion = espionFetch();

    await reassignerTache("t-1", "dev");

    const entetes = entetesDe(espion);
    expect(entetes.Authorization).toBe(`Bearer ${JETON}`);
    expect(entetes["Content-Type"]).toBe("application/json");
  });

  it("le pose sur un téléversement, qui ne porte aucun en-tête à lui", async () => {
    const espion = espionFetch({ sources: [] });

    await televerserSources([new File(["x"], "note.txt")]);

    expect(entetesDe(espion).Authorization).toBe(`Bearer ${JETON}`);
  });

  it("le passe au WebSocket par l'URL, faute d'en-tête possible", () => {
    const url = urlEvenements(PORTEE_TOUS);

    expect(url).toMatch(/^ws/);
    expect(url).toContain(`jeton=${JETON}`);
    expect(url).toContain("projet=tous");
  });
});

describe("sans jeton réglé (régime ouvert)", () => {
  it("n'ajoute aucun en-tête d'autorisation", async () => {
    const espion = espionFetch({ statut: "ok", espace: "commun" });

    await chargerSante();

    expect(entetesDe(espion)).not.toHaveProperty("Authorization");
  });

  it("laisse l'URL du flux telle qu'elle était", () => {
    expect(urlEvenements(PORTEE_TOUS)).not.toContain("jeton=");
  });
});

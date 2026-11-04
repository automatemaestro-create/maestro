/**
 * La **configuration du poste**, reçue au moment où le front est servi (#641).
 *
 * Un paquet d'installation se construit une fois et se pose sur N postes : le
 * jeton de l'API (propre à chaque poste) et son URL ne peuvent donc pas venir du
 * build. Ce fichier tient les trois moitiés du contrat :
 *
 * 1. le **serveur** lit ce que son lanceur lui a donné, et rien quand il n'a rien
 *    reçu — la page reste alors exactement celle d'avant ce lot ;
 * 2. le **script** qui la pose dans le document n'ouvre aucune injection : une
 *    valeur portant `</script>` ne ferme pas la balise ;
 * 3. la **page** la préfère aux valeurs du build, pour le REST comme pour le
 *    WebSocket — sans quoi un paquet servirait le jeton et l'URL du poste qui l'a
 *    construit.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { afterEach, describe, expect, it, vi } from "vitest";

import { urlApi, urlEvenements } from "@/lib/api";
import {
  CLE_CONFIG_POSTE,
  configPoste,
  configPosteServeur,
  scriptConfigPoste,
} from "@/lib/configPoste";
import { jetonApi } from "@/lib/jetonApi";

function poserDansLaPage(valeur: unknown) {
  (window as unknown as Record<string, unknown>)[CLE_CONFIG_POSTE] = valeur;
}

afterEach(() => {
  delete (window as unknown as Record<string, unknown>)[CLE_CONFIG_POSTE];
  vi.unstubAllEnvs();
});

describe("côté serveur", () => {
  it("lit l'URL et le jeton que le lanceur a donnés", () => {
    expect(
      configPosteServeur({
        MAESTRO_API_URL: " http://127.0.0.1:8041 ",
        MAESTRO_API_JETON: "jeton-du-poste",
      }),
    ).toEqual({ api: "http://127.0.0.1:8041", jeton: "jeton-du-poste" });
  });

  it("ne rend rien quand le lanceur n'a rien donné — ni clé vide, ni blanc", () => {
    expect(configPosteServeur({})).toEqual({});
    expect(configPosteServeur({ MAESTRO_API_URL: "  ", MAESTRO_API_JETON: "" })).toEqual(
      {},
    );
  });

  it("ne lit jamais les variables du build, qui sont celles du poste qui a construit", () => {
    expect(
      configPosteServeur({
        NEXT_PUBLIC_MAESTRO_API_URL: "http://ailleurs:9999",
        NEXT_PUBLIC_MAESTRO_API_JETON: "jeton-du-constructeur",
      }),
    ).toEqual({});
  });
});

describe("le script posé dans le document", () => {
  it("est absent quand il n'y a rien à poser", () => {
    expect(scriptConfigPoste({})).toBeNull();
  });

  it("pose la configuration sous la clé que la page relit", () => {
    const script = scriptConfigPoste({ api: "http://127.0.0.1:8000", jeton: "j" });
    expect(script).toBe(
      `window.${CLE_CONFIG_POSTE}={"api":"http://127.0.0.1:8000","jeton":"j"};`,
    );
  });

  it("ne laisse pas une valeur fermer la balise qui la porte", () => {
    const script = scriptConfigPoste({ jeton: "</script><script>alert(1)</script>" });
    expect(script).not.toContain("<");
    // Échappé, le texte reste le même pour qui le relit en JavaScript.
    const relu = new Function(`const window = {}; ${script} return window;`)() as Record<
      string,
      { jeton: string }
    >;
    expect(relu[CLE_CONFIG_POSTE].jeton).toBe("</script><script>alert(1)</script>");
  });
});

describe("côté page", () => {
  it("relit ce que le document a posé, à chaque appel", () => {
    expect(configPoste()).toEqual({});
    poserDansLaPage({ api: "http://127.0.0.1:8041", jeton: "jeton-du-poste" });
    expect(configPoste()).toEqual({ api: "http://127.0.0.1:8041", jeton: "jeton-du-poste" });
  });

  it("ignore ce qui n'est pas une chaîne non vide", () => {
    poserDansLaPage({ api: 42, jeton: "  " });
    expect(configPoste()).toEqual({});
    poserDansLaPage("pas un objet");
    expect(configPoste()).toEqual({});
  });

  it("préfère le jeton du document à celui du build", () => {
    vi.stubEnv("NEXT_PUBLIC_MAESTRO_API_JETON", "jeton-du-constructeur");
    expect(jetonApi()).toBe("jeton-du-constructeur");
    poserDansLaPage({ jeton: "jeton-du-poste" });
    expect(jetonApi()).toBe("jeton-du-poste");
  });

  it("préfère l'URL du document à celle du build, sans barre finale", () => {
    vi.stubEnv("NEXT_PUBLIC_MAESTRO_API_URL", "http://localhost:8000");
    expect(urlApi()).toBe("http://localhost:8000");
    poserDansLaPage({ api: "http://127.0.0.1:8041/" });
    expect(urlApi()).toBe("http://127.0.0.1:8041");
  });

  it("ouvre le flux temps réel sur l'URL et avec le jeton du document", () => {
    vi.stubEnv("NEXT_PUBLIC_MAESTRO_API_URL", "http://localhost:8000");
    vi.stubEnv("NEXT_PUBLIC_MAESTRO_API_JETON", "jeton-du-constructeur");
    poserDansLaPage({ api: "http://127.0.0.1:8041", jeton: "jeton-du-poste" });
    expect(urlEvenements("tous")).toBe(
      "ws://127.0.0.1:8041/ws/evenements?projet=tous&jeton=jeton-du-poste",
    );
  });
});

describe("le layout racine", () => {
  const layout = readFileSync(join(__dirname, "..", "app", "layout.tsx"), "utf8");

  it("lit la configuration pendant un rendu dynamique, jamais pendant le build", () => {
    // Sans `connection()` avant la lecture, `next build` figerait dans le HTML les
    // valeurs du poste qui construit le paquet : la panne exacte que ce lot lève.
    const attente = layout.indexOf("await connection();");
    const lecture = layout.indexOf("scriptConfigPoste(configPosteServeur())");
    expect(attente).toBeGreaterThan(-1);
    expect(lecture).toBeGreaterThan(attente);
  });

  it("pose le script dans le document et non dans un fichier servi à part", () => {
    // Un `<script src>` se charge depuis n'importe quelle origine : une page tierce
    // y lirait le jeton. Le document, lui, ne se lit pas d'ailleurs.
    expect(layout).toContain("dangerouslySetInnerHTML={{ __html: scriptPoste }}");
    expect(layout).not.toMatch(/<Script[^>]*src=/);
  });
});

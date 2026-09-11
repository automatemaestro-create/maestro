/**
 * Le layout racine déclare-t-il encore que le clavier virtuel redimensionne le
 * viewport de mise en page ? (#892)
 *
 * ── Ce que ce test garde, et ce qu'il ne peut pas garder ─────────────────────
 *
 * Il ne **mesure** rien : le symptôme demande un vrai clavier virtuel, donc un
 * téléphone, et aucun navigateur piloté sans appareil n'en ouvre — c'est écrit
 * dans le ticket, et c'est pourquoi la vérification a eu lieu à la main
 * (Chrome Android, 2026-09-11 : sans la clé, `clientHeight` reste à 779 pendant
 * que `visualViewport.height` tombe à 461 ; avec elle, il suit à 460). Ce
 * relevé-là ne se rejoue pas en CI.
 *
 * Ce qui se rejoue, c'est que la **déclaration soit toujours là**. Elle tient
 * en trois lignes, ne casse rien en disparaissant, et son effet est invisible
 * partout où la suite tourne — jsdom ne connaît pas de clavier virtuel, le
 * pipeline non plus. Un export retiré par mégarde au prochain remaniement du
 * layout ne ferait donc rougir personne, et le défaut reviendrait sans bruit
 * jusqu'au prochain téléphone. C'est exactement la situation de la sonde
 * d'hydratation (#730) : le filet ne peut pas éprouver le comportement, il
 * garde la seule chose qu'il puisse voir.
 *
 * On lit l'**export** plutôt que les octets du fichier : contrairement à
 * `suppressHydrationWarning`, qui est un attribut JSX au milieu d'une prose qui
 * le mentionne, `viewport` est une valeur que le module rend — la lire évite
 * une expression régulière, et c'est le compilateur qui garantit au passage que
 * `Viewport.interactiveWidget` existe encore côté Next.
 */

import { describe, expect, it } from "vitest";

import { viewport } from "../app/layout";

describe("viewport du layout racine (#892)", () => {
  it("déclare interactive-widget=resizes-content", () => {
    expect(viewport.interactiveWidget).toBe("resizes-content");
  });

  it("ne pose que cette clé — le reste du viewport garde les défauts de Next", () => {
    // Le ticket ne demande que la clé du clavier, et déclarer un `viewport`
    // n'efface pas les défauts : **vérifié** sur la page servie par next@16.2.10
    // le 2026-09-11, la balise émise est
    // `width=device-width, initial-scale=1, interactive-widget=resizes-content`.
    // C'était le risque sérieux du correctif — un export qui *remplace* aurait
    // fait perdre `width=device-width`, donc cassé tout le mobile pour réparer
    // une bande de 318 px. Ajouter une largeur ou une échelle ici reviendrait à
    // figer à la main ce que Next tient déjà, et à le désaccorder au premier
    // changement de son défaut.
    expect(Object.keys(viewport)).toEqual(["interactiveWidget"]);
  });
});

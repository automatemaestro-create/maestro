/**
 * Les formats partagés de l'UI (`lib/format`, tests différés du lot #247 —
 * docs/10 §5.1). C'est la **source unique** du rendu des montants : un composant
 * qui reformate dans son coin fait diverger l'écran de lui-même, et c'est ici
 * qu'on le verrouille plutôt que dans chaque écran qui affiche un prix.
 *
 * Les assertions portent sur les **cas distincts**, pas sur la ponctuation :
 * `Intl` rend l'espace insécable étroit et le symbole « $US » selon la version
 * d'ICU embarquée dans Node. Un test qui comparerait « 1,23 $US » à la lettre
 * casserait à la prochaine montée de version sans qu'aucune règle du produit
 * n'ait bougé — on vérifie donc le nombre de décimales et les trois verdicts.
 */

import { describe, expect, it } from "vitest";

import {
  formatCout,
  formatCoutAxe,
  formatDuree,
  formatHeure,
  formatTokens,
  libelleStatut,
} from "@/lib/format";

/** Le nombre écrit dans un montant, sans devise ni espace insécable. */
function chiffresDe(montant: string): string {
  return montant.replace(/[^\d,]/g, "");
}

describe("les montants à deux décimales (#247)", () => {
  it("arrête un montant à deux décimales, quel qu'en soit le détail", () => {
    expect(chiffresDe(formatCout(1.2345))).toBe("1,23");
    expect(chiffresDe(formatCout(12))).toBe("12,00");
  });

  it("arrondit au centime le plus proche plutôt que de tronquer", () => {
    expect(chiffresDe(formatCout(1.2367))).toBe("1,24");
  });

  it("distingue « rien de rapporté » d'une dépense nulle", () => {
    // Le point du ticket : `null` n'est pas zéro. Un agent dont le fournisseur
    // ne rapporte aucun coût (#113) ne doit pas passer pour gratuit.
    expect(formatCout(null)).toBe("—");
    expect(chiffresDe(formatCout(0))).toBe("0,00");
  });

  it("annonce « < 0,01 » une dépense trop petite pour deux décimales", () => {
    // Cas courant sur un fournisseur local : quelques dix-millièmes de dollar.
    // Sans ce cas, l'écran rendrait « 0,00 $US » — indiscernable de gratuit.
    const minuscule = formatCout(0.0004);
    expect(minuscule).toContain("<");
    expect(chiffresDe(minuscule)).toBe("0,01");
  });

  it("garde 0,005 $ du côté du centime, pas du « trop petit »", () => {
    // La bascule est à la moitié d'un centime : au-dessus, l'arrondi rend un
    // montant honnête et le « < » n'a plus lieu d'être.
    expect(formatCout(0.005)).not.toContain("<");
    expect(chiffresDe(formatCout(0.005))).toBe("0,01");
  });

  it("laisse les graduations d'axe à leur précision, exception assumée", () => {
    // Seul endroit où la règle des deux décimales ne tient pas : sur une série
    // de quelques millièmes, toutes les graduations tomberaient sur « 0,00 » et
    // l'axe ne dirait plus rien.
    expect(chiffresDe(formatCoutAxe(0.0025))).toBe("0,0025");
    expect(formatCoutAxe(0.25)).toContain("$");
  });
});

describe("les autres formats de l'UI", () => {
  it("sépare les milliers d'un compte de tokens", () => {
    expect(formatTokens(12345).replace(/ | |\s/g, "")).toBe("12345");
  });

  it("rend une durée dans l'unité qui se lit", () => {
    expect(formatDuree(850)).toBe("850 ms");
    expect(formatDuree(2400)).toMatch(/^2,4 s$/);
    expect(formatDuree(125_000)).toBe("2 min 05 s");
  });

  it("dit « — » d'une durée non rapportée, comme d'un coût", () => {
    expect(formatDuree(null)).toBe("—");
  });

  it("rend un statut inconnu du front tel quel plutôt que rien", () => {
    // Même garde que la colonne « Autres » du Kanban : le backend peut
    // enrichir la machine à états sans que l'UI efface la ligne.
    expect(libelleStatut("terminee")).toBe("Terminée");
    expect(libelleStatut("trucmuche")).toBe("trucmuche");
  });

  it("rend une heure lisible, et ne bute pas sur un horodatage vide", () => {
    expect(formatHeure("2026-07-28T10:03:00Z")).toMatch(/\d{2}:\d{2}/);
    expect(formatHeure("")).toBe("");
  });
});

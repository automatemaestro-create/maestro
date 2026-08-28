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
  formatDureeRun,
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

describe("combien de temps un run a tourné (#709)", () => {
  const DEBUT = "2026-07-28T10:00:00Z";
  const apres = (minutes: number) =>
    new Date(Date.parse(DEBUT) + minutes * 60_000).toISOString();

  it("prend `fin` pour terme quand le run est soldé, l'instant courant sinon", () => {
    // Le partage qui compte : un run soldé a une durée **figée**, calculable sans
    // horloge — donc juste dès le rendu serveur, sur la majorité d'une liste.
    expect(formatDureeRun(DEBUT, apres(42), null)).toBe("42 min");
    expect(formatDureeRun(DEBUT, null, Date.parse(apres(42)))).toBe("42 min");
  });

  it("écrit les heures comme une horloge, et les jours au-delà", () => {
    expect(formatDureeRun(DEBUT, apres(64), null)).toBe("1 h 04");
    expect(formatDureeRun(DEBUT, apres(60 * 27), null)).toBe("1 j 03 h");
  });

  it("dit « < 1 min » plutôt que « 0 min »", () => {
    // Les deux ne se lisent pas pareil : un run qui vient de partir n'a pas
    // tourné zéro minute. Même parti pris qu'`formatAttente` sous la minute.
    expect(formatDureeRun(DEBUT, apres(0), null)).toBe("< 1 min");
    // Une durée négative — horloges désaccordées, ou une `fin` antérieure au
    // départ — tombe dans le même cas : on n'écrit jamais « -2 min ».
    expect(formatDureeRun(DEBUT, "2026-07-28T09:00:00Z", null)).toBe("< 1 min");
  });

  it("ne rend rien plutôt qu'un zéro quand l'horloge n'a pas démarré", () => {
    // Le seul cas vide : un run **en cours** au rendu serveur, où `Date.now()`
    // ne vaut pas la même chose des deux côtés. `formatHeureRelative` retombe là
    // sur l'heure absolue ; une durée vivante n'a pas d'équivalent immobile.
    expect(formatDureeRun(DEBUT, null, null)).toBe("");
    expect(formatDureeRun("", null, Date.now())).toBe("");
    expect(formatDureeRun("pas une date", null, Date.now())).toBe("");
  });
});

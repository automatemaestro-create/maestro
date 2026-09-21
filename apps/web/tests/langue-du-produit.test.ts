/**
 * Le produit ne parle pas son dépôt (#939).
 *
 * Constat **G6** du retex du 2026-09-11 : cinq surfaces d'un parcours ordinaire
 * montraient à l'utilisateur ce qui n'existe que pour nous — une commande shell
 * (`bash scripts/controltower/start.sh --demo`, « à relancer depuis le dépôt »)
 * sur la page d'accueil, trois numéros de tickets internes (#481, #56, #281), un
 * catalogue qui dit « dans ce dépôt », des `docs/*.md` cités comme sources. Rien
 * de tout cela n'a de sens pour quelqu'un qui a *installé* Maestro : il n'a ni
 * dépôt, ni `scripts/`, ni suivi de tickets.
 *
 * ── Ce que cette sonde garde, et ce qu'elle ne garde pas ─────────────────────
 *
 * Elle garde le **front** : ce qui est écrit en toutes lettres dans les écrans,
 * les composants et les textes de `lib/`. Elle ne garde **pas** ce qu'un agent
 * rend — l'assistant, l'orchestration —, et ce partage est celui de la note du
 * ticket : là, le remède est dans le prompt, pas dans un `.tsx`. Il vit dans
 * `maestro/agents/playbooks_defaut/_registre.md`, gardé par
 * `tests/test_registre_de_langue.py`. Deux moitiés, deux filets, un seul texte
 * de règle de chaque côté.
 *
 * ── Chaque motif est prouvé sur un échantillon fautif ────────────────────────
 *
 * Avant de balayer, les trois motifs sont confrontés **aux chaînes réellement
 * relevées par le retex** : une sonde qu'on écrit sur un dépôt déjà propre ne
 * prouve rien, et c'est ainsi qu'un balayage finit par ne plus rien voir. Les
 * contre-exemples comptent autant : une couleur arbitraire (`dark:bg-[#3987e5]`)
 * n'est pas un numéro de ticket, et une route du produit (`/journal`) n'est pas
 * un chemin du dépôt.
 *
 * ── L'exemption, nommée et vérifiée ──────────────────────────────────────────
 *
 * `app/socle/page.tsx` est le **catalogue du socle** : il n'est pas un écran du
 * produit, il n'est pas servi en production, et il cite docs/30 à qui écrit de
 * l'interface. L'exemption n'est donc pas un pardon mais une conséquence — et
 * elle est **vérifiée** ici, en lisant la redirection qui l'écarte plutôt qu'en
 * croyant sur parole le commentaire qui la décrit.
 */

import { describe, expect, it } from "vitest";

import {
  DOSSIERS_AFFICHANT_DU_TEXTE,
  lireSource,
  sansCommentaires,
  sourcesDuProduit,
} from "./sources";

// ─────────────────────────────────────────────────────────────────────────────
// 1. LES MOTIFS
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Un renvoi à un ticket interne : `#481`, `#56`, `#281`.
 *
 * La frontière de mot à droite est ce qui laisse passer les couleurs
 * arbitraires de Tailwind (`bg-[#3987e5]`, `#171717`), dont les chiffres sont
 * suivis de lettres hexadécimales ; le caractère à gauche écarte celles dont la
 * valeur est **toute** numérique (`bg-[#1234]`), qui tiendraient autrement dans
 * quatre chiffres.
 */
const TICKET = /(^|[^[\w#])#\d{2,4}\b/;

/** Un chemin du dépôt : `scripts/ci/local.sh`, `bash scripts/controltower/…`. */
const SCRIPT = /\bscripts\//;

/** Un document du dépôt, numéroté (`docs/30 §4`) ou nommé (`docs/07-…md`). */
const DOCUMENT = /\bdocs\/(\d|[\w.-]+\.md)/;

const MOTIFS = [
  { nom: "un numéro de ticket interne", motif: TICKET },
  { nom: "un chemin de script du dépôt", motif: SCRIPT },
  { nom: "un fichier de documentation du dépôt", motif: DOCUMENT },
] as const;

/**
 * Ce que le retex a lu à l'écran le 2026-09-11, mot pour mot. C'est
 * l'échantillon fautif : si l'un d'eux cessait d'être reconnu, la sonde
 * balaierait dans le vide.
 */
const RELEVE_DU_RETEX: readonly [string, RegExp][] = [
  ['commande="bash scripts/controltower/start.sh --demo"', SCRIPT],
  ['note="Tout se passe dans le fil (#481) — l\'objectif…"', TICKET],
  ['"…borner ses exécutions simultanées (#86, EF-21)."', TICKET],
  ['aide="…cadré sur le projet actif (#281) : c\'est l\'URL…"', TICKET],
  ["`} — ordre de grandeur, docs/09`}", DOCUMENT],
  ["(découverte ≠ installation, docs/19)", DOCUMENT],
  ['procedure_url="docs/15-pilote-mcp-slack.md#2-installation-de-lapp"', DOCUMENT],
];

/** Ce qui ressemble aux motifs sans en être — la moitié qui garde la sonde utile. */
const FAUX_AMIS: readonly [string, RegExp][] = [
  ['className="dark:bg-[#3987e5]"', TICKET],
  ["--surface-creuse: #171717;", TICKET],
  ['href="/journal"', SCRIPT],
  ['<code className="font-mono">core/permissions/dev.json</code>', SCRIPT],
  ["Les documents de votre projet", DOCUMENT],
];

// ─────────────────────────────────────────────────────────────────────────────
// 2. LA SONDE, PROUVÉE PUIS BALAYÉE
// ─────────────────────────────────────────────────────────────────────────────

describe("les motifs reconnaissent ce que le retex a relevé", () => {
  it.each(RELEVE_DU_RETEX)("« %s » est reconnu", (extrait, motif) => {
    expect(motif.test(extrait)).toBe(true);
  });

  it.each(FAUX_AMIS)("« %s » n'est pas reconnu", (extrait, motif) => {
    expect(motif.test(extrait)).toBe(false);
  });
});

/**
 * Hors produit, avec sa raison. La liste est courte à dessein : chaque entrée
 * est un écran que l'utilisateur ne voit pas, et il faut pouvoir le démontrer.
 */
const HORS_PRODUIT: Record<string, string> = {
  "app/socle/page.tsx":
    "le catalogue du socle — pas un écran du produit, redirigé vers / en production, " +
    "hors du menu et des dérivations de routes ; il s'adresse à qui écrit de l'interface",
};

describe("aucun écran ne parle du dépôt", () => {
  const sources = sourcesDuProduit([".tsx", ".ts"], DOSSIERS_AFFICHANT_DU_TEXTE).filter(
    (fichier) => !(fichier in HORS_PRODUIT),
  );

  it("le périmètre n'est pas vide", () => {
    // Un balayage qui ne lit rien passe toujours. On le dit ici plutôt que de
    // s'en apercevoir le jour où `sourcesDuProduit` change de forme.
    expect(sources.length).toBeGreaterThan(50);
  });

  it.each(MOTIFS)("nulle part $nom", ({ nom, motif }) => {
    const fautifs: string[] = [];
    for (const fichier of sources) {
      const texte = sansCommentaires(lireSource(fichier));
      for (const [numero, ligne] of texte.split("\n").entries()) {
        if (motif.test(ligne)) fautifs.push(`${fichier}:${numero + 1}: ${ligne.trim()}`);
      }
    }
    expect(fautifs, `${nom} s'affiche dans l'interface :\n${fautifs.join("\n")}`).toEqual(
      [],
    );
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// 3. L'EXEMPTION SE VÉRIFIE
// ─────────────────────────────────────────────────────────────────────────────

describe("le catalogue du socle est bien hors produit", () => {
  it("il est redirigé vers l'accueil en production", () => {
    // La raison de l'exemption, lue là où elle vit. Le jour où cette redirection
    // disparaît, `/socle` devient un écran servi — et son exemption, un trou.
    const config = sansCommentaires(lireSource("next.config.ts"));
    expect(config).toContain('source: "/socle"');
    expect(config).toContain('process.env.NODE_ENV === "production"');
  });

  it("chaque fichier exempté existe encore", () => {
    // Une exemption qui survit à son fichier est une exemption qui protège
    // autre chose que ce qu'elle nomme.
    const toutes = sourcesDuProduit([".tsx", ".ts"], DOSSIERS_AFFICHANT_DU_TEXTE);
    for (const fichier of Object.keys(HORS_PRODUIT)) {
      expect(toutes, `${fichier} n'existe plus`).toContain(fichier);
    }
  });
});

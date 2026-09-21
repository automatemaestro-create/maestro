/**
 * Aucun renvoi par libellé ne s'éteint en silence (#1176).
 *
 * `entreeParLibelle` résout un renvoi par le **libellé** du menu, pour qu'il suive
 * sa page si elle déménage (#191). Le revers : quand une entrée **quitte** le menu,
 * chaque renvoi vers elle rend `undefined` et s'éteint sans un mot. C'est arrivé à
 * l'état vide du cadrage, qui visait « Composer un objectif » trois semaines après
 * le départ de cette entrée (#484).
 *
 * L'autre usage de ce mécanisme reste permis, et il est voulu : écrire un renvoi
 * vers une page **pas encore créée**, qui s'allume le jour où elle entre au menu
 * (le Journal, #191 → #249). Un tel libellé s'inscrit dans `PAGES_A_VENIR` avec sa
 * raison — c'est la différence entre un renvoi en attente et un renvoi mort.
 */
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { ATTENTES } from "@/components/runs/EtatRun";
import { PAGE_DU_CADRAGE } from "@/lib/brief";
import { PAGE_DU_FIL, entreeParLibelle } from "@/lib/navigation";
import { PAGE_DES_QUESTIONS } from "@/lib/questions";

/** Les pages qu'un renvoi peut viser avant qu'elles n'existent — libellé → raison. */
const PAGES_A_VENIR: Record<string, string> = {};

/** Un renvoi par libellé écrit en toutes lettres : `entreeParLibelle("…")`. */
const RENVOI_LITTERAL = /entreeParLibelle\(\s*"([^"]+)"\s*\)/g;

function libellesLitteraux(code: string): string[] {
  return [...code.matchAll(RENVOI_LITTERAL)].map((m) => m[1]);
}

function sourcesDuFront(): Map<string, string> {
  const racine = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
  const sources = new Map<string, string>();
  const parcourir = (dossier: string) => {
    for (const entree of readdirSync(dossier, { withFileTypes: true })) {
      const complet = path.join(dossier, entree.name);
      if (entree.isDirectory()) parcourir(complet);
      else if (/\.tsx?$/.test(entree.name))
        sources.set(path.relative(racine, complet), readFileSync(complet, "utf8"));
    }
  };
  for (const dossier of ["components", "app", "lib"]) parcourir(path.join(racine, dossier));
  return sources;
}

function resout(libelle: string): boolean {
  return entreeParLibelle(libelle) !== undefined || libelle in PAGES_A_VENIR;
}

describe("aucun renvoi par libellé ne vise une page disparue", () => {
  it("le motif attrape le renvoi mort d'origine, et laisse passer un renvoi vivant", () => {
    // L'échantillon fautif est la ligne même que #1176 a retirée.
    const fautif = 'const composer = entreeParLibelle("Composer un objectif");';
    expect(libellesLitteraux(fautif)).toEqual(["Composer un objectif"]);
    expect(resout("Composer un objectif")).toBe(false);
    expect(resout("Chat")).toBe(true);
  });

  it("chaque libellé écrit en toutes lettres résout une entrée du menu", () => {
    const morts: string[] = [];
    let vus = 0;
    for (const [fichier, code] of sourcesDuFront()) {
      for (const libelle of libellesLitteraux(code)) {
        vus += 1;
        if (!resout(libelle)) morts.push(`${fichier} → « ${libelle} »`);
      }
    }
    expect(vus).toBeGreaterThan(5); // le balayage a bien lu le front
    expect(morts).toEqual([]);
  });

  it("chaque constante de page passée à entreeParLibelle résout aussi", () => {
    const pages = [
      PAGE_DU_FIL,
      PAGE_DU_CADRAGE,
      PAGE_DES_QUESTIONS,
      ...Object.values(ATTENTES).map((attente) => attente.page),
    ];
    expect(pages.filter((page) => !resout(page))).toEqual([]);
  });

  it("l'inventaire des pages à venir ne couvre que des pages encore absentes", () => {
    // Une page arrivée au menu se retire de l'inventaire : sinon il couvrirait,
    // le jour où elle repartirait, un renvoi redevenu mort.
    const arrivees = Object.keys(PAGES_A_VENIR).filter(
      (libelle) => entreeParLibelle(libelle) !== undefined,
    );
    expect(arrivees).toEqual([]);
  });
});

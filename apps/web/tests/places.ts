/**
 * La sonde des **trois places** (docs/30 §4.1), et les deux plafonds qu'elle
 * compte — partagés par les suites qui en ont besoin.
 *
 * Elle est née dans `sobriete.test.tsx` (#539, lot 7 de #532), qui la prouve
 * encore sur un échantillon fautif avant de balayer les écrans du menu. Elle
 * vit ici depuis #929 (lot 11 de #921), parce qu'une **seconde** suite la
 * réclame : `frontiere-shell-ecran.test.tsx` compte, avec exactement le même
 * outil, ce qui se range **hors** de l'écran — dans le shell.
 *
 * ⚠ **Deux sondes seraient pires qu'une seule mal branchée.** C'est la leçon
 * que `tests/ecrans.tsx` porte déjà en tête (« deux harnais à tenir d'accord
 * seraient le premier moyen pour qu'une suite rende un verdict sur un produit
 * que l'autre ne monte plus »), et celle de `tests/harnais_forge.py` côté
 * outillage. Ici elle est plus tranchante encore : la frontière shell / écran
 * n'a de sens que si les deux côtés se comptent **de la même façon**. Une sonde
 * recopiée qui dériverait d'un caractère rendrait « conforme » un bloc rangé
 * dans le shell et compté nulle part.
 *
 * Ce module ne monte rien et ne rend aucun verdict : il range des nœuds du DOM
 * dans trois cases, et dit les plafonds. Qui s'en sert décide de ce qu'il en
 * fait.
 */

import { ID_CONTENU_PRINCIPAL } from "@/components/Shell";

// --- Les plafonds (docs/30 §4.1) -------------------------------------------
//
// ⚠ Ces deux nombres ne se relèvent pas pour faire passer une refonte
// (docs/35 §3.4, CLAUDE.md). Ils sont **confrontés au texte de la règle** par
// `frontiere-shell-ecran.test.tsx` : les changer ici oblige à réécrire
// docs/30 §4.1, c'est-à-dire à défaire la règle en toutes lettres plutôt qu'en
// silence dans un fichier de test.

/** Bandeau de tête : « quatre est un plafond, pas une cible » (docs/30 §4.3). */
export const CHIFFRES_MAX = 4;
/** Corps : trois blocs de plein format, arbitrage non compté. */
export const BLOCS_MAX = 3;

/**
 * Ce qui est un bloc dans le DOM. `<section>` pour le corps et le bandeau,
 * `<aside>` pour la colonne de propriétés — les deux balises que le produit
 * emploie déjà, et non un attribut inventé pour l'occasion.
 *
 * Ce qui n'en est **pas** un, et le compte le montre : une `<nav>` (le filtre de
 * période de `/couts`, le sommaire de `/parametres`, la bascule de vues d'un
 * run) règle l'écran ou y navigue, elle n'occupe pas une place ; un `<article>`
 * ou une `<div>` est du contenu **dans** un bloc.
 */
const SELECTEUR_BLOC = "section, aside";

/** Le bloc est-il de premier niveau, c'est-à-dire sans bloc au-dessus de lui ? */
function estDePremierNiveau(noeud: Element, racine: Element): boolean {
  let parent = noeud.parentElement;
  while (parent !== null && parent !== racine) {
    if (parent.matches(SELECTEUR_BLOC)) return false;
    parent = parent.parentElement;
  }
  return true;
}

/**
 * Le nom d'un bloc — ce sous quoi le recensement le désigne, et ce qui permet de
 * le suivre d'un montage à l'autre. `aria-label` d'abord (la forme majoritaire),
 * puis le texte que `aria-labelledby` désigne, puis l'`id` de l'ancre. Un bloc
 * qui n'a rien de tout cela rend la chaîne vide, et c'est un échec.
 */
function nomDe(bloc: Element): string {
  const etiquette = bloc.getAttribute("aria-label");
  if (etiquette !== null && etiquette.trim() !== "") return etiquette.trim();
  const cible = bloc.getAttribute("aria-labelledby");
  if (cible !== null) {
    const titre = bloc.ownerDocument.getElementById(cible);
    const texte = (titre?.textContent ?? "").trim();
    if (texte !== "") return texte;
  }
  return bloc.id ?? "";
}

/**
 * Le bandeau de tête : un bloc dont **tous** les enfants directs sont des
 * chiffres (`TuileChiffre`). La condition porte sur *tous* et non sur *au moins
 * un* : sans cela, un bloc de corps qui afficherait une tuile en tête passerait
 * pour le bandeau et sortirait du plafond — c'est la seule façon de tricher que
 * ce comptage laisserait ouverte.
 */
function estBandeauDeTete(bloc: Element): boolean {
  return (
    bloc.children.length > 0 &&
    [...bloc.children].every((enfant) => enfant.matches("[data-chiffre]"))
  );
}

export type Places = {
  /** Les chiffres du bandeau de tête — au plus `CHIFFRES_MAX`. */
  chiffres: string[];
  /** Les blocs du corps, par leur nom — au plus `BLOCS_MAX`. */
  corps: string[];
  /** La ou les colonnes de propriétés — il n'en faut jamais plus d'une. */
  colonnes: string[];
  /** Les blocs de premier niveau sans nom : toujours une faute. */
  anonymes: string[];
};

/**
 * Range les blocs de premier niveau de `racine` dans les trois places.
 *
 * `exclure` retire un sous-arbre entier du comptage — et c'est ce qui permet de
 * compter le **shell seul**, `<main>` mis de côté (#929). Sans lui, la sonde
 * confondrait ce que l'écran occupe et ce que le cadre lui tient autour.
 */
export function placesDe(racine: Element, exclure: Element | null = null): Places {
  const places: Places = { chiffres: [], corps: [], colonnes: [], anonymes: [] };
  const blocs = [...racine.querySelectorAll(SELECTEUR_BLOC)].filter(
    (bloc) =>
      (exclure === null || !exclure.contains(bloc)) &&
      estDePremierNiveau(bloc, racine),
  );
  for (const bloc of blocs) {
    const nom = nomDe(bloc);
    if (nom === "") {
      places.anonymes.push(`<${bloc.tagName.toLowerCase()}>`);
      continue;
    }
    if (bloc.tagName === "ASIDE") places.colonnes.push(nom);
    else if (estBandeauDeTete(bloc))
      places.chiffres.push(
        ...[...bloc.querySelectorAll("[data-chiffre]")].map(
          (tuile) => (tuile.textContent ?? "").trim().slice(0, 30) || nom,
        ),
      );
    else places.corps.push(nom);
  }
  return places;
}

/** Le corps de l'écran monté : la racine sur laquelle le comptage porte. */
export function contenuPrincipal(): HTMLElement {
  const contenu = document.getElementById(ID_CONTENU_PRINCIPAL);
  if (contenu === null) throw new Error("l'écran n'a pas de contenu principal");
  return contenu;
}

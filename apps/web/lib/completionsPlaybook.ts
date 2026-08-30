/**
 * Les complétions proposées en cours de frappe dans l'éditeur de playbook (#261).
 *
 * ## Pourquoi c'est local, et pas un appel au modèle
 *
 * Une complétion se juge à la frappe : proposée en moins d'une frappe, elle
 * aide ; arrivée une seconde plus tard, elle déplace le curseur de quelqu'un qui
 * a déjà continué. Un aller-retour modèle par frappe coûterait à la fois la
 * latence — qui rendrait la proposition fausse au moment où elle s'affiche — et
 * un appel facturé par caractère tapé. Le modèle intervient dans ce ticket, mais
 * à l'autre bout : au **bouton assistant**, une fois, sur un geste explicite.
 *
 * Ce qui est proposé ici est donc **déterministe** et vient du dépôt lui-même :
 * `GET /api/playbooks/lexique` dérive structures et tournures des playbooks
 * livrés avec le paquet (`maestro.agents.lexique_playbook`). Rien n'est inventé,
 * rien n'est recopié à la main.
 *
 * ## Ce qui déclenche une proposition
 *
 * Le **segment courant** — le texte de la ligne en cours jusqu'au curseur, son
 * amorce de puce retirée. Une entrée est candidate si le segment en est un
 * préfixe. C'est une règle qu'on peut tenir dans sa tête en tapant : les
 * propositions ne surgissent qu'en tête de ligne ou de puce, jamais au milieu
 * d'une phrase, et ce qu'on accepte remplace exactement ce qu'on a tapé.
 *
 * La comparaison ignore la casse et les accents (`## methode` trouve
 * `## Méthode`) : on tape vite, et un accent manquant ne doit pas faire
 * disparaître la seule proposition utile.
 */

import type { EntreeLexique, LexiquePlaybook } from "@/lib/types";

/** Une proposition prête à afficher : le texte à insérer, et d'où il vient. */
export type Completion = {
  /** Le texte complet qui remplacera le segment courant. */
  texte: string;
  /** Nombre de playbooks du dépôt où il figure — sert à l'ordre et à l'affichage. */
  roles: number;
  /** `structure` (un titre de section) ou `tournure` (une phrase récurrente). */
  famille: "structure" | "tournure";
};

/**
 * Longueur minimale du segment avant de proposer quoi que ce soit. À un
 * caractère, la moitié du lexique remonterait à chaque début de ligne : la liste
 * s'ouvrirait sans arrêt sans jamais viser juste.
 */
export const PREFIXE_MINIMUM = 2;

/** Nombre maximal de propositions affichées — au-delà on ne choisit plus, on lit. */
export const COMPLETIONS_MAX = 5;

/** L'amorce d'une puce ou d'une énumération, qui ne fait pas partie du segment. */
const AMORCE = /^(?:[-*+]|\d+\.)\s+/;

/**
 * Le segment courant : la ligne en cours jusqu'au curseur, amorce de puce
 * retirée.
 *
 * `position` est l'index du curseur dans le texte entier (`selectionStart` de la
 * zone d'édition).
 */
export function segmentCourant(texte: string, position: number): string {
  const debut = texte.lastIndexOf("\n", position - 1) + 1;
  return texte.slice(debut, position).replace(AMORCE, "");
}

/**
 * Les complétions du lexique dont `segment` est un préfixe, les plus récurrentes
 * d'abord.
 *
 * Rend une liste vide si le segment est trop court, s'il est déjà exactement une
 * entrée du lexique (il n'y a plus rien à compléter), ou si rien ne correspond —
 * l'absence de proposition est le cas nominal, pas une panne.
 */
export function completionsPour(
  lexique: LexiquePlaybook | null,
  segment: string,
): Completion[] {
  if (lexique === null) return [];
  const cherche = normaliser(segment);
  if (cherche.length < PREFIXE_MINIMUM) return [];
  const candidates = [
    ...retenir(lexique.structures, cherche, "structure"),
    ...retenir(lexique.tournures, cherche, "tournure"),
  ];
  return candidates
    .sort((a, b) => b.roles - a.roles || a.texte.length - b.texte.length)
    .slice(0, COMPLETIONS_MAX);
}

/**
 * Le texte obtenu en acceptant `completion` : le segment courant est remplacé
 * par l'entrée entière, le reste du document ne bouge pas.
 *
 * Rend aussi la nouvelle position du curseur — juste après le texte inséré,
 * pour que la frappe reprenne dans la foulée.
 */
export function accepter(
  texte: string,
  position: number,
  completion: Completion,
): { texte: string; position: number } {
  const debutLigne = texte.lastIndexOf("\n", position - 1) + 1;
  const ligne = texte.slice(debutLigne, position);
  const amorce = AMORCE.exec(ligne)?.[0] ?? "";
  const debutSegment = debutLigne + amorce.length;
  const insere = `${texte.slice(0, debutSegment)}${completion.texte}`;
  return { texte: `${insere}${texte.slice(position)}`, position: insere.length };
}

function retenir(
  entrees: EntreeLexique[],
  cherche: string,
  famille: Completion["famille"],
): Completion[] {
  return entrees
    .filter((entree) => {
      const cible = normaliser(entree.texte);
      return cible.startsWith(cherche) && cible !== cherche;
    })
    .map((entree) => ({ texte: entree.texte, roles: entree.roles, famille }));
}

/**
 * La forme comparable d'un texte : minuscules, sans accents, espaces réduits.
 *
 * `normalize("NFD")` sépare les accents de leur lettre ; le remplacement retire
 * les diacritiques ainsi isolés. C'est la seule normalisation faite ici — la
 * ponctuation et les astérisques du Markdown restent significatifs, un playbook
 * étant écrit avec.
 */
function normaliser(texte: string): string {
  return texte
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/\s+/g, " ")
    .trimStart();
}

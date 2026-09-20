/**
 * Les **bornes d'un run** posées au moment de lancer (ticket #990).
 *
 * Le moteur sait arrêter un run sur quatre garde-fous — un plafond de coût, un
 * plafond de tokens, un délai par tâche, un nombre de tâches en parallèle — et
 * `POST /api/executions` les expose depuis #185. Mais la **seule porte de
 * lancement de l'interface** est la conversation (#666), et elle ne les passait
 * pas : un run lancé depuis un écran était littéralement sans borne, mesuré à
 * 12,51 $ par le retex du 2026-09-11 (G5).
 *
 * Ce module porte la moitié « écran » de la question : ce qu'on **saisit** (des
 * chaînes, parce qu'un champ de formulaire ne contient jamais autre chose), ce
 * qu'on **envoie** (des nombres ou `null`), et surtout **ce qu'on en dit**.
 *
 * ## Une seule définition, parce que trois surfaces la lisent
 *
 * La carte « Lancer ce run ? » l'affiche avant de lancer, la section
 * « Coûts & plafonds » des Paramètres y renvoie, et le fil en garde la trace une
 * fois le run ouvert (côté Python, `maestro/controltower/bornes.py`). Deux
 * formulations de « qu'est-ce qui borne ce run ? » finiraient par ne plus dire
 * la même chose — c'est exactement ce que #943 a corrigé sur « y a-t-il un
 * cadrage en attente ? ». La règle vit donc ici et s'appelle.
 *
 * ## Ce que la veille a tranché, et qui se lit dans le code
 *
 * Deux partis pris de la veille de conception du ticket (commentaire de #990)
 * portent ce fichier :
 *
 * - **« sans borne » est une valeur affichée, jamais un champ vide** — d'après
 *   GitHub Actions, où aucun job n'est non borné et où le défaut est écrit
 *   (« Each job in a workflow can run for up to 6 hours »). D'où
 *   `phraseDesBornes`, qui **parle aussi quand il n'y a rien** ;
 * - **la borne dit ce qu'elle fait, pas seulement son chiffre** — d'après les
 *   budgets GitHub, où une colonne « Stop usage : Yes » accompagne le montant.
 *   D'où des verbes (« s'interrompt à ») plutôt que des étiquettes
 *   (« plafond »).
 */

import { formatCout, formatTokens } from "./format";

/** Ce que les quatre champs contiennent — de la frappe, donc des chaînes. */
export type SaisieBornes = {
  cout: string;
  tokens: string;
  delai: string;
  parallelisme: string;
};

/** Quatre champs vides : le régime par défaut, et celui d'avant ce ticket. */
export const SAISIE_VIERGE: SaisieBornes = {
  cout: "",
  tokens: "",
  delai: "",
  parallelisme: "",
};

/**
 * Ce que le corps de `POST /api/chat/{agent}/cadrage` porte (#990) — les mêmes
 * noms que `LancementExecution`, parce que c'est le même moteur qui les reçoit.
 * `null` vaut « pas de borne », comme partout ailleurs dans ce contrat.
 */
export type BornesRun = {
  plafond_cout_usd: number | null;
  plafond_tokens: number | null;
  timeout_tache_s: number | null;
  parallelisme: number | null;
};

/** Aucune borne : le run ira jusqu'au bout. */
export const AUCUNE_BORNE: BornesRun = {
  plafond_cout_usd: null,
  plafond_tokens: null,
  timeout_tache_s: null,
  parallelisme: null,
};

/**
 * Le nombre qu'une saisie porte, ou `null` — **`null` couvre deux cas** : le
 * champ vide (aucune borne voulue) et la saisie qui n'est pas un nombre
 * exploitable.
 *
 * Les confondre est délibéré ici et ne perd rien : `champFautif` les sépare
 * pour l'écran, qui est le seul endroit où la différence compte. Le corps de
 * requête, lui, n'a que deux valeurs à sa disposition — un nombre, ou `null` —
 * et envoyer une chaîne pour dire « c'est faux » ferait juger la faute par le
 * backend, un aller-retour plus tard.
 *
 * La virgule décimale est acceptée : on écrit « 2,50 » en français, et refuser
 * cette forme ferait buter sur le clavier plutôt que sur la règle.
 */
export function nombreSaisi(valeur: string): number | null {
  const propre = valeur.trim().replace(",", ".");
  if (propre === "") return null;
  const lu = Number(propre);
  return Number.isFinite(lu) && lu > 0 ? lu : null;
}

/**
 * Le champ est rempli mais ne porte pas de borne utilisable — une lettre, un
 * zéro, un nombre négatif. C'est la seule faute que l'écran sait nommer avant
 * d'envoyer : un plafond est un **maximum**, donc il est strictement positif,
 * et c'est déjà la règle que `ServiceExecutions.lancer` applique côté moteur
 * (« doit être > 0 »).
 */
export function champFautif(valeur: string): boolean {
  return valeur.trim() !== "" && nombreSaisi(valeur) === null;
}

/** L'une des quatre saisies au moins est remplie sans porter de borne. */
export function saisieFautive(saisie: SaisieBornes): boolean {
  return (
    champFautif(saisie.cout) ||
    champFautif(saisie.tokens) ||
    champFautif(saisie.delai) ||
    champFautif(saisie.parallelisme)
  );
}

/** Ce que la saisie envoie — les tokens et le parallélisme sont des entiers. */
export function bornesDepuis(saisie: SaisieBornes): BornesRun {
  const tokens = nombreSaisi(saisie.tokens);
  const parallelisme = nombreSaisi(saisie.parallelisme);
  return {
    plafond_cout_usd: nombreSaisi(saisie.cout),
    plafond_tokens: tokens === null ? null : Math.round(tokens),
    timeout_tache_s: nombreSaisi(saisie.delai),
    parallelisme: parallelisme === null ? null : Math.round(parallelisme),
  };
}

/** Aucune des quatre n'est posée. */
export function aucuneBorne(bornes: BornesRun): boolean {
  return (
    bornes.plafond_cout_usd === null &&
    bornes.plafond_tokens === null &&
    bornes.timeout_tache_s === null &&
    bornes.parallelisme === null
  );
}

/**
 * Ce que ces bornes **font**, en une ligne — y compris quand il n'y en a aucune.
 *
 * C'est le troisième critère du ticket : *un run sans borne le dit à son
 * lancement, l'illimité est un choix affiché, pas un oubli*. La phrase est donc
 * écrite dans les deux sens, comme la ligne `plan :` d'un run d'outillage
 * (#286) annonce son régime qu'il soit posé ou non.
 *
 * Elle est le **jumeau** de `BornesRun.en_phrase()` (`controltower/bornes.py`),
 * qui écrit la même chose dans le fil une fois le run parti. Deux
 * implémentations, parce que deux langages ; un seul vocabulaire, parce que
 * c'est la même phrase à deux instants — la relire dans le fil ne doit pas
 * donner l'impression d'avoir lancé autre chose.
 */
export function phraseDesBornes(bornes: BornesRun): string {
  if (aucuneBorne(bornes)) return "Aucune borne — le run ira jusqu'au bout.";
  const morceaux: string[] = [];
  if (bornes.plafond_cout_usd !== null) {
    morceaux.push(`s'interrompt à ${formatCout(bornes.plafond_cout_usd)}`);
  }
  if (bornes.plafond_tokens !== null) {
    morceaux.push(`s'interrompt à ${formatTokens(bornes.plafond_tokens)} tokens`);
  }
  if (bornes.timeout_tache_s !== null) {
    morceaux.push(`${bornes.timeout_tache_s} s par tâche`);
  }
  if (bornes.parallelisme !== null) {
    const tache = bornes.parallelisme <= 1 ? "tâche" : "tâches";
    morceaux.push(`${bornes.parallelisme} ${tache} à la fois`);
  }
  return morceaux.join(" · ");
}

/** La phrase des bornes telles qu'une saisie les porterait. */
export function phraseDeLaSaisie(saisie: SaisieBornes): string {
  return phraseDesBornes(bornesDepuis(saisie));
}

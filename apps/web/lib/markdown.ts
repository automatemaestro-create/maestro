/**
 * Le Markdown que le fil rend — **l'analyseur seul**, sans une ligne de rendu
 * (#697, lot 7 de #690 ; solde #265).
 *
 * ## Pourquoi un analyseur à nous, et pas `react-markdown`
 *
 * Deux avertissements du ticket, et le même geste y répond :
 *
 * - ⚠ « rendre du Markdown, c'est rendre du contenu produit par un modèle : pas
 *   de `dangerouslySetInnerHTML` sans assainissement, pas de HTML brut autorisé
 *   dans la source ». Un analyseur qui rend un **arbre de données** — jamais une
 *   chaîne de HTML — met la question hors sujet plutôt que de la traiter : il n'y
 *   a pas de chaîne à assainir, parce qu'il n'y a pas de chaîne. Le rendu
 *   (`components/chat/TexteMarkdown`) ne fait que traduire ces objets en
 *   éléments React, où tout texte est échappé par construction. Du HTML écrit
 *   dans la source du modèle (`<img onerror=…>`) ressort donc **en texte**, sans
 *   qu'aucune liste d'éléments autorisés n'ait à être tenue à jour ;
 * - ⚠ « le poids ajouté au bundle par un moteur Markdown est à peser : le job
 *   `web-build` mesure ». `react-markdown` + `remark-parse` pèsent une centaine
 *   de kilo-octets minifiés pour un sur-ensemble de CommonMark dont un fil de
 *   conversation n'emploie rien : tables de référence, notes de bas de page,
 *   HTML en ligne, entités. Ce fichier en fait quelques-uns.
 *
 * Le prix est assumé et il est écrit ci-dessous : **le sous-ensemble est
 * délibérément petit**, et ce qu'il ne reconnaît pas reste du texte lisible
 * plutôt que de disparaître. C'est la seule propriété qui compte pour un fil —
 * une réponse mal balisée doit rester lue, jamais escamotée.
 *
 * ## Le sous-ensemble, et pourquoi celui-là
 *
 * Ce qu'un agent produit réellement : des paragraphes, des listes, des titres de
 * section, des citations, des filets, du code — en ligne et en bloc —, du gras,
 * de l'italique et des liens. Tout y est. Trois écarts volontaires à CommonMark,
 * chacun motivé par ce que **ce** produit voit passer :
 *
 * 1. **`_` n'emphase pas.** `snake_case`, `run_id`, `tache_id`, `--max-budget-usd`
 *    traversent chaque réponse de l'orchestration ; traiter `_` comme un
 *    délimiteur mettrait la moitié d'un identifiant en italique une fois sur
 *    deux. Seuls `*` et `**` emphasent, ce qui est de toute façon ce que les
 *    modèles écrivent. `_` reste littéral.
 * 2. **Les emphases et le code en ligne ne franchissent pas la fin de ligne.**
 *    C'est ce qui garde les motifs sans quantificateur non borné : l'analyse
 *    reste linéaire, et une réponse d'un modèle est une entrée non fiable qu'on
 *    ne veut pas laisser choisir le temps de calcul (un motif à retour arrière
 *    exponentiel est un déni de service à une astérisque près).
 * 3. **Les listes sont plates.** Une entrée est une ligne. Une liste imbriquée
 *    rend ses entrées à plat, précédées de leur indentation — lisible, sans
 *    hiérarchie. C'est le seul appauvrissement de la table, et il est préféré à
 *    un analyseur récursif d'indentation dont personne ne relirait les cas.
 *
 * ## Ce que l'analyseur promet au **direct** (#695)
 *
 * Le texte lui arrive **par morceaux** : une réponse en cours d'écriture est du
 * Markdown incomplet, et il en reçoit une version de plus à chaque incrément.
 * D'où deux propriétés tenues exprès :
 *
 * - une clôture manquante n'annule rien. Un bloc de code encore ouvert est un
 *   bloc de code (`ferme: false`), qui se remplit ; une emphase encore ouverte
 *   reste du texte, et devient une emphase quand sa seconde étoile arrive. Rien
 *   ne bascule d'une forme à une autre à la fin du flux, donc rien ne saute ;
 * - `ferme` est **exposé** plutôt que consommé ici : c'est ce qui permet au
 *   rendu de ne pas offrir la copie d'un bloc qui n'est pas fini d'arriver.
 */

import { lienExterneSur } from "./liens";

// ─────────────────────────────────────────────────────────────────────────────
// L'arbre
// ─────────────────────────────────────────────────────────────────────────────

/** Ce qui vit **dans** une ligne de texte. */
export type Inline =
  | { type: "texte"; texte: string }
  /** Du code en ligne : jamais ré-analysé, c'est tout son intérêt. */
  | { type: "code"; texte: string }
  | { type: "fort"; enfants: Inline[] }
  | { type: "accent"; enfants: Inline[] }
  /** `href` est **déjà** passé par `lienExterneSur` : il est suivable ou absent. */
  | { type: "lien"; href: string; enfants: Inline[] }
  /** Un retour à la ligne simple, à l'intérieur d'un même paragraphe. */
  | { type: "saut" };

/** Ce qui s'empile verticalement. */
export type Bloc =
  | { type: "paragraphe"; enfants: Inline[] }
  /**
   * Un titre de section **dans le message**. Le niveau est conservé pour le
   * poids visuel ; le rendu n'en fait pas une balise de titre du document (voir
   * `TexteMarkdown` : un modèle ne décide pas du plan de la page).
   */
  | { type: "titre"; niveau: number; enfants: Inline[] }
  | { type: "code"; langage: string; texte: string; ferme: boolean }
  | { type: "liste"; ordonnee: boolean; depart: number; entrees: Inline[][] }
  | { type: "citation"; blocs: Bloc[] }
  | { type: "filet" };

// ─────────────────────────────────────────────────────────────────────────────
// Les motifs de bloc
// ─────────────────────────────────────────────────────────────────────────────

/** ` ```lang ` ou ` ~~~ ` — l'ouverture comme la fermeture d'un bloc de code. */
const CLOTURE = /^ {0,3}(`{3,}|~{3,})[ \t]*([^\s`]*)[ \t]*$/;
/** `## Titre` — les `#` de fermeture facultatifs sont retirés. */
const TITRE = /^ {0,3}(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*$/;
/** `---`, `***`, `___` — testé **avant** les puces, que `* * *` matcherait. */
const FILET = /^ {0,3}(?:-[ \t]*){3,}$|^ {0,3}(?:\*[ \t]*){3,}$|^ {0,3}(?:_[ \t]*){3,}$/;
const CITATION = /^ {0,3}>[ \t]?(.*)$/;
const PUCE = /^([ \t]*)[-*+][ \t]+(.*)$/;
const NUMERO = /^([ \t]*)(\d{1,9})[.)][ \t]+(.*)$/;

/**
 * Le Markdown d'un message, en blocs.
 *
 * Rend un tableau vide sur un texte vide ou blanc : l'appelant décide alors de
 * ne rien poser plutôt que de rendre une bulle creuse.
 */
export function analyserMarkdown(texte: string): Bloc[] {
  if (texte.trim() === "") return [];
  return blocsDeLignes(texte.replace(/\r\n?/g, "\n").split("\n"));
}

function blocsDeLignes(lignes: string[]): Bloc[] {
  const blocs: Bloc[] = [];
  let i = 0;

  while (i < lignes.length) {
    const ligne = lignes[i];

    if (ligne.trim() === "") {
      i++;
      continue;
    }

    // ── Le code en bloc, en premier : à l'intérieur, plus aucun motif ne joue.
    const ouverture = CLOTURE.exec(ligne);
    if (ouverture !== null) {
      const marqueur = ouverture[1];
      const corps: string[] = [];
      let ferme = false;
      i++;
      while (i < lignes.length) {
        const fin = CLOTURE.exec(lignes[i]);
        // Une fermeture est **nue** (aucun langage) et au moins aussi longue que
        // son ouverture, du même caractère : c'est ce qui laisse écrire un bloc
        // de quatre accents graves contenant un bloc de trois.
        if (
          fin !== null &&
          fin[1][0] === marqueur[0] &&
          fin[1].length >= marqueur.length &&
          fin[2] === ""
        ) {
          ferme = true;
          i++;
          break;
        }
        corps.push(lignes[i]);
        i++;
      }
      blocs.push({
        type: "code",
        langage: ouverture[2],
        texte: corps.join("\n"),
        ferme,
      });
      continue;
    }

    const titre = TITRE.exec(ligne);
    if (titre !== null) {
      blocs.push({
        type: "titre",
        niveau: titre[1].length,
        enfants: inlineDe(titre[2]),
      });
      i++;
      continue;
    }

    if (FILET.test(ligne)) {
      blocs.push({ type: "filet" });
      i++;
      continue;
    }

    // ── La citation : les lignes préfixées sont dépouillées puis **ré-analysées**,
    //    ce qui y autorise du code, des listes et des paragraphes sans les
    //    redécrire ici.
    if (CITATION.test(ligne)) {
      const dedans: string[] = [];
      while (i < lignes.length) {
        const citee = CITATION.exec(lignes[i]);
        if (citee === null) break;
        dedans.push(citee[1]);
        i++;
      }
      blocs.push({ type: "citation", blocs: blocsDeLignes(dedans) });
      continue;
    }

    // ── Les listes. L'indentation d'une entrée est **conservée dans son texte**
    //    (voir l'écart 3 de l'en-tête) : une liste imbriquée se lit à plat au
    //    lieu de perdre ses entrées.
    const puce = PUCE.exec(ligne);
    const numero = NUMERO.exec(ligne);
    if (puce !== null || numero !== null) {
      const ordonnee = numero !== null;
      const depart = ordonnee ? Number(numero[2]) : 1;
      const entrees: Inline[][] = [];
      while (i < lignes.length) {
        const suivante = ordonnee ? NUMERO.exec(lignes[i]) : PUCE.exec(lignes[i]);
        if (suivante === null) break;
        const indentation = suivante[1].replace(/\t/g, "    ");
        const contenu = ordonnee ? suivante[3] : suivante[2];
        entrees.push(inlineDe(indentation + contenu));
        i++;
      }
      blocs.push({ type: "liste", ordonnee, depart, entrees });
      continue;
    }

    // ── Un paragraphe : tout ce qui suit jusqu'à une ligne blanche ou au
    //    premier autre bloc. Les retours à la ligne y sont **gardés** — un agent
    //    aligne ses phrases, et les recoller changerait ce qu'il a écrit.
    const paragraphe: string[] = [];
    while (i < lignes.length) {
      const suivante = lignes[i];
      if (suivante.trim() === "") break;
      if (
        CLOTURE.test(suivante) ||
        TITRE.test(suivante) ||
        FILET.test(suivante) ||
        CITATION.test(suivante) ||
        PUCE.test(suivante) ||
        NUMERO.test(suivante)
      ) {
        break;
      }
      paragraphe.push(suivante);
      i++;
    }
    blocs.push({ type: "paragraphe", enfants: inlineDe(paragraphe.join("\n")) });
  }

  return blocs;
}

// ─────────────────────────────────────────────────────────────────────────────
// L'intérieur d'une ligne
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Les quatre marques en ligne, en une seule passe et **dans cet ordre** :
 * le code d'abord (il neutralise ce qu'il contient), puis le lien, puis le gras,
 * puis l'italique — sans quoi `*` mangerait la première étoile de `**`.
 *
 * Aucune classe ne contient `\n` : c'est ce qui borne l'analyse à la ligne et
 * garde le moteur d'expressions régulières linéaire (écart 2 de l'en-tête).
 *
 * ⚠ Une **source**, jamais une instance partagée : `inlineDe` se rappelle
 * lui-même sur le contenu d'un gras ou d'un libellé de lien, et un objet
 * `RegExp` global porte son `lastIndex` — la passe imbriquée déplacerait celui
 * de la passe qui l'a lancée. Un objet neuf par appel coûte une allocation et
 * rend la faute impossible plutôt que de la laisser dépendre de l'ordre des
 * lignes.
 */
const MOTIF_MARQUE =
  "`([^`\\n]+)`|\\[([^\\][\\n]*)\\]\\(([^\\s()]+)\\)|\\*\\*([^*\\n]+)\\*\\*|\\*([^*\\n]+)\\*";

/** Le contenu d'une ligne (ou d'un paragraphe), marques comprises. */
function inlineDe(texte: string): Inline[] {
  const noeuds: Inline[] = [];
  const marque = new RegExp(MOTIF_MARQUE, "g");
  let curseur = 0;

  let trouve: RegExpExecArray | null;
  while ((trouve = marque.exec(texte)) !== null) {
    const [entier, code, libelle, url, fort, accent] = trouve;

    // Un lien dont l'adresse n'est pas suivable n'est pas un lien : on le laisse
    // **tel qu'il a été écrit** plutôt que de poser un `href` mort ou dangereux.
    // `lienExterneSur` est le point de passage unique du produit (#192) — il
    // écarte `javascript:`, `data:` et les adresses relatives. Le curseur ne
    // bouge pas : le fragment repart avec le texte brut qui l'entoure.
    const href = url === undefined ? null : lienExterneSur(url);
    if (url !== undefined && href === null) continue;

    if (trouve.index > curseur) {
      pousserTexte(noeuds, texte.slice(curseur, trouve.index));
    }

    if (code !== undefined) noeuds.push({ type: "code", texte: code });
    else if (href !== null)
      noeuds.push({ type: "lien", href, enfants: inlineDe(libelle) });
    else if (fort !== undefined)
      noeuds.push({ type: "fort", enfants: inlineDe(fort) });
    else noeuds.push({ type: "accent", enfants: inlineDe(accent) });

    curseur = trouve.index + entier.length;
  }

  if (curseur < texte.length) pousserTexte(noeuds, texte.slice(curseur));
  return noeuds;
}

/**
 * Ajoute du texte brut en **coupant sur les retours à la ligne** : le saut
 * devient un nœud à lui, ce qui laisse le rendu poser un `<br />` plutôt que de
 * s'en remettre à un `whitespace-pre-wrap` — lequel figerait aussi l'indentation
 * des listes imbriquées et les alignements du modèle.
 */
function pousserTexte(noeuds: Inline[], brut: string): void {
  const morceaux = brut.split("\n");
  morceaux.forEach((morceau, rang) => {
    if (rang > 0) noeuds.push({ type: "saut" });
    if (morceau !== "") noeuds.push({ type: "texte", texte: morceau });
  });
}

"use client";

/**
 * Suivre le bas d'un fil qui n'a **plus son propre ascenseur** (#691, lot 1 de #690).
 *
 * Jusqu'ici la conversation défilait dans une boîte à elle (`max-h-[60vh]
 * overflow-y-auto`), et « aller en bas » se disait en une ligne :
 * `conteneur.scrollTop = conteneur.scrollHeight`. La revue du 2026-08-28 a
 * retiré cette boîte — le fil est l'écran, et c'est la **page** qui le parcourt.
 * Le geste change donc de destinataire : il ne vise plus un élément qu'on tient
 * par une `ref`, mais l'ascenseur du **cadre** (`Shell`), que le fil ne connaît
 * pas et n'a pas à connaître.
 *
 * D'où ces trois fonctions, et pas une de plus :
 *
 * - `ascenseurDe` **trouve** cet ascenseur en remontant les ancêtres. Le
 *   composant de fil est partagé (`components/Conversation`, #620) et monté à
 *   deux endroits — l'écran `/chat` et l'onglet Chat d'une fiche agent : coder
 *   en dur « le div du Shell » marcherait à un endroit et pas à l'autre, et
 *   casserait en silence le jour où la page changerait d'emboîtement ;
 * - `positionEnBas` **dit où est le bas**, c'est-à-dire de combien il faut
 *   défiler pour voir la fin du fil ;
 * - `estEnBas` **décide** si le lecteur suit encore la conversation. Sans elle,
 *   suivre le fil revient à arracher l'écran des mains de qui remonte lire —
 *   le défaut que la note de #265 nomme et que le streaming (#695) rendra
 *   permanent, une réponse qui s'écrit produisant une rafale de rendus.
 *
 * ## Le bas du FIL n'est pas le bas de la PAGE (#941)
 *
 * De #691 à ce lot, les deux dernières disaient « en bas » pour *le bas de
 * l'ascenseur* : `scrollTop = scrollHeight` d'un côté, `scrollHeight -
 * scrollTop - clientHeight` de l'autre. C'était juste tant que le fil finissait
 * la page — ce qu'il fait au **grand format** de `/chat`, où la colonne de
 * propriétés est posée à côté de lui. Sous `@4xl` elle passe **dessous**, et
 * « le bas de la page » devient le bas de cette colonne : à 420 × 860,
 * `/chat` s'ouvrait donc sur le bas de la pile, composeur 93 px au-dessus du
 * bord supérieur de l'écran, qu'aucun défilement de page ne ramenait (retex du
 * 2026-09-11, constat G8 ; mesuré au banc le 2026-09-19). À 420 × 1400, où la
 * page ne déborde pas, le même code ne défilait rien et l'ordre paraissait
 * juste : ce n'est pas l'empilement qui était faux, c'est ce que « en bas »
 * désignait.
 *
 * La cible est donc le bas **du fil**, et les deux fonctions la lisent au même
 * endroit — deux formulations de « en bas » finiraient par ne plus désigner le
 * même point, et le fil montrerait « Dernier message » sur un fil qu'il vient
 * lui-même de coller en bas.
 *
 * ⚠ Ce qui suit le fil quand il **finit** la page n'est pas du contenu : c'est
 * la réserve du bouton flottant (`after:h-24` sur `main`, #888), dont le
 * composeur à quai a besoin pour retrouver sa place naturelle au bas du fil.
 * `positionEnBas` va donc jusqu'au bout de l'ascenseur quand ce qui reste
 * dessous tient dans le seuil — sans quoi ce lot aurait remonté de 32 px le
 * repos mesuré par #888 sur toutes les fenêtres où le fil déborde.
 *
 * ⚠ **Aucune ne mesure quoi que ce soit sous jsdom**, et c'est voulu :
 * jsdom ne calcule ni hauteur ni défilement (#308, frontière du skill
 * `/banc-mise-en-page`). Écrire un `scrollTop` n'y fait rien, et `estEnBas` y
 * lit trois zéros. Le fil se rend donc normalement en test, sans qu'un faux
 * verdict de géométrie puisse s'y glisser. `positionEnBas` y rend donc le bas
 * de l'ascenseur — le geste d'avant #941 : là où il n'y a **rien à mesurer**,
 * une cible calculée sur des zéros dirait « le haut de la page » avec les mots
 * de « le bas du fil ».
 *
 * ⚠ Mais `ascenseurDe` n'y rend **pas** l'élément racine, contrairement à ce que
 * cette note a dit de #691 à #877 : jsdom **n'implémente pas**
 * `document.scrollingElement` (mesuré : `undefined`, donc `null` rendu), et
 * aucun ancêtre n'y a d'`overflow` calculé tant qu'on n'en pose pas un. Le fil
 * ne s'abonne donc à **rien** sous jsdom, et un test qui dispatcherait un
 * `scroll` sur `document.documentElement` n'exercerait rien en rendant un vert.
 * Ce qui remet les choses dans l'ordre du produit — où l'ascenseur est le
 * conteneur du `Shell`, jamais la fenêtre — est un `overflowY` posé sur un
 * ancêtre **avant** le montage : voir `tests/dernier-message.test.tsx`.
 */

/**
 * Distance au bas (px) sous laquelle on tient le lecteur pour « en bas ».
 *
 * Généreuse à dessein : à l'exact pixel près, une hauteur de ligne
 * fractionnaire ou une image qui finit de charger suffit à faire décrocher le
 * suivi pour toujours. Trop généreuse, on ramènerait en bas quelqu'un qui vient
 * de remonter d'un cran — d'où l'ordre de grandeur d'un message court, et pas
 * d'un écran.
 */
export const SEUIL_BAS_PX = 96;

/** Les valeurs d'`overflow-y` qui font d'un élément un ascenseur. */
const DEFILANT = /^(auto|scroll|overlay)$/;

/**
 * Le premier ancêtre de `element` qui défile — l'ascenseur qui le porte.
 *
 * Retombe sur l'élément racine du document (`document.scrollingElement`) quand
 * aucun ancêtre ne défile : c'est le cas d'une page ordinaire, où c'est la
 * fenêtre qui fait l'ascenseur. Rend `null` hors document (élément détaché,
 * rendu serveur) — l'appelant n'a alors rien à faire.
 */
export function ascenseurDe(element: Element | null): HTMLElement | null {
  if (element === null) return null;
  const vue = element.ownerDocument?.defaultView;
  if (vue == null) return null;
  for (
    let noeud = element.parentElement;
    noeud !== null;
    noeud = noeud.parentElement
  ) {
    if (DEFILANT.test(vue.getComputedStyle(noeud).overflowY)) return noeud;
  }
  return (element.ownerDocument.scrollingElement as HTMLElement | null) ?? null;
}

/**
 * Le bas de la zone **visible** de `ascenseur`, en coordonnées de fenêtre.
 *
 * Pour un conteneur défilant ordinaire — le cas du produit, où c'est le cadre
 * du `Shell` —, c'est le bas de sa boîte de contenu. Pour l'élément racine, sur
 * lequel `ascenseurDe` retombe hors du `Shell`, la zone visible est la fenêtre
 * elle-même : sa boîte, qui fait toute la hauteur du document, dirait tout
 * autre chose.
 */
function basVisibleDe(ascenseur: HTMLElement): number {
  if (ascenseur === ascenseur.ownerDocument.scrollingElement) {
    return ascenseur.clientHeight;
  }
  const boite = ascenseur.getBoundingClientRect();
  return boite.top + ascenseur.clientTop + ascenseur.clientHeight;
}

/**
 * De combien le bas de `fil` est **sous** le bas de la zone visible (négatif
 * s'il est déjà au-dessus) — la mesure que les deux verbes publics partagent.
 */
function resteSousLeBas(ascenseur: HTMLElement, fil: Element): number {
  return fil.getBoundingClientRect().bottom - basVisibleDe(ascenseur);
}

/**
 * La bande qu'un élément **à quai** se réserve sous lui — lue sur lui, jamais
 * recopiée.
 *
 * C'est le `bottom-16` du composeur (#726) : les 64 px du bouton flottant de
 * l'assistant, dans lesquels il ne descend pas. Mesurer la valeur déclarée
 * plutôt que l'inscrire ici garde la règle d'un seul côté — celui qui la
 * dessine ; un chiffre recopié aurait cessé de suivre le jour où le quai
 * remonte, et personne n'aurait vu la cible dériver.
 *
 * Rend `0` pour ce qui n'est pas à quai, et sous jsdom, où aucun style calculé
 * ne dit de pixel.
 */
function reserveDuQuai(quai: HTMLElement | null): number {
  if (quai === null) return 0;
  const vue = quai.ownerDocument.defaultView;
  if (vue == null) return 0;
  const style = vue.getComputedStyle(quai);
  if (style.position !== "sticky") return 0;
  const bas = Number.parseFloat(style.bottom);
  return Number.isFinite(bas) ? Math.max(0, bas) : 0;
}

/**
 * Le `scrollTop` qui met la fin de `fil` sous les yeux — la cible de
 * « coller en bas » (#941).
 *
 * `quai` est l'élément collant que le fil porte en dernier (le composeur) : sa
 * réserve est **ajoutée** à la cible, pour qu'il s'y pose à sa place naturelle
 * plutôt que décalé de 64 px sur le fil — mesuré au banc du 2026-09-19 à
 * 420 × 860, c'est la différence entre un dernier message entier et un dernier
 * message amputé de 48 px, exactement le défaut que #888 nomme.
 *
 * Bornée aux deux bouts par ce que l'ascenseur peut défiler, et poussée jusqu'à
 * son bout quand ce qui reste sous le fil tient dans `seuil` : là, ce qui suit
 * le fil n'est plus du contenu mais la réserve de fin de page (#888), qui vaut
 * déjà plus que celle du quai — s'arrêter avant elle remonterait le repos que
 * #888 a mesuré.
 *
 * `fil` absent ou rien à mesurer (jsdom, élément détaché) : le bas de
 * l'ascenseur, comme avant ce lot.
 */
export function positionEnBas(
  ascenseur: HTMLElement,
  fil: Element | null,
  quai: HTMLElement | null = null,
  seuil: number = SEUIL_BAS_PX,
): number {
  const bout = Math.max(0, ascenseur.scrollHeight - ascenseur.clientHeight);
  if (fil === null || ascenseur.clientHeight === 0) return bout;
  const cible = Math.min(
    bout,
    Math.max(
      0,
      ascenseur.scrollTop +
        resteSousLeBas(ascenseur, fil) +
        reserveDuQuai(quai),
    ),
  );
  return bout - cible <= seuil ? bout : cible;
}

/**
 * Le lecteur est-il assez près du bas **du fil** pour qu'on le suive encore ?
 *
 * Mesuré contre la cible de `positionEnBas` et non contre le bas de
 * l'ascenseur : c'est ce qui rend la question symétrique — remonté lire **ou**
 * descendu dans ce qui suit le fil, on a décroché, et le geste de retour (#877)
 * ramène au même endroit que le recollement. Les deux verbes lisent donc la
 * même cible, ce qui est la seule façon qu'ils ne se contredisent pas.
 */
export function estEnBas(
  ascenseur: HTMLElement,
  fil: Element | null,
  quai: HTMLElement | null = null,
  seuil: number = SEUIL_BAS_PX,
): boolean {
  const cible = positionEnBas(ascenseur, fil, quai, seuil);
  return Math.abs(ascenseur.scrollTop - cible) <= seuil;
}

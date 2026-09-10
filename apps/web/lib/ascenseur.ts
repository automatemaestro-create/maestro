"use client";

/**
 * Dire au socle qu'un élément **défile** (#725, lot 2 de #722).
 *
 * L'ascenseur discret de `app/globals.css` s'efface au repos et se montre dès
 * qu'il peut servir : sous le pointeur (`:hover`) et quand le focus est dedans
 * (`:focus-within`) — deux états que CSS lit tout seul. Il n'en existe aucun
 * pour « en train de défiler » : ni pseudo-classe, ni requête de conteneur
 * (`scroll-state()` dit si l'on *peut* défiler, jamais si l'on *est* en train
 * de le faire). Or c'est le troisième moment où la barre doit se voir — le
 * tactile, qui n'a pas de survol ; le défilement au clavier quand le focus
 * n'est pas dans la surface ; et un défilement que personne ne fait à la main,
 * comme la page qu'on fait suivre au bas de la conversation (`lib/defilement`).
 *
 * D'où cette écoute, et rien d'autre : l'événement `scroll` de **n'importe
 * quel** élément — capturé au document, puisqu'il ne remonte pas —, qui pose
 * `data-defilement` sur l'élément qui vient de défiler et le retire après un
 * court repos. Le CSS fait le reste : c'est lui qui décide de la couleur et de
 * ce qu'un attribut posé fait à la barre. Ce module ne sait rien des ascenseurs.
 *
 * ⚠ Sous jsdom, aucun `scroll` ne part de lui-même (#308) : l'écoute s'installe
 * et n'a jamais rien à faire — ce qui est exactement ce qu'on attend d'un test
 * d'écran, où la géométrie n'existe pas.
 */

/** L'attribut que le CSS lit : présent, l'élément vient de défiler. */
export const ATTRIBUT_DEFILEMENT = "data-defilement";

/**
 * Le second contrat de la frontière : l'attribut qui **désigne** un ascenseur.
 *
 * Un attribut de données et non une classe utilitaire (même raison que
 * `data-defilement`) : le CSS lit un contrat, que nul refactor de Tailwind ne
 * retire. Ce module ne le pose jamais — c'est le `Shell` qui le porte sur son
 * conteneur défilant —, il le **nomme**, pour que le JSX qui l'écrit et la
 * sonde qui lit les octets de la feuille tiennent la même valeur.
 */
export const ATTRIBUT_ASCENSEUR = "data-ascenseur";

/**
 * La valeur qui désigne **l'ascenseur de la page** — celui du `Shell`.
 *
 * C'est la seule exception à la règle discrète (#882, parti pris 1 de la veille
 * #859) : la barre de page est un **repère permanent**, peinte sans condition,
 * et seules les surfaces imbriquées s'effacent au repos. Une exception qui a
 * besoin d'un marqueur, faute d'un sélecteur qui distingue « la page » d'une
 * colonne bornée : `*:hover` s'applique à **tout ancêtre du pointeur**, donc le
 * conteneur du `Shell` montrait sa barre dès que le pointeur était sur le
 * contenu et la perdait dès qu'il passait sur la navigation ou quittait la
 * fenêtre — et elle n'existait pas au clavier tant qu'aucun focus n'était dans
 * le contenu. Le repère de page clignotait au gré du pointeur.
 */
export const ASCENSEUR_PAGE = "page";

/**
 * Le repos (ms) après le dernier `scroll` avant que la barre ne s'efface.
 *
 * Assez long pour qu'un défilement par à-coups (molette, touches) reste **une**
 * apparition et non un clignotement ; assez court pour que la barre parte avec
 * le geste, et pas une seconde après.
 *
 * 700 ms de #725 à #882 était « un ordre de grandeur, pas une mesure ». La
 * veille #859 a vérifié les deux références qui effacent : VS Code web
 * (`HIDE_TIMEOUT = 500` dans `scrollableElement.ts`, lu sur les sources) et
 * Zulip (`transition: opacity .2s linear .5s`) — **500 ms** l'une et l'autre.
 */
export const REPOS_DEFILEMENT_MS = 500;

/**
 * Marque de `data-defilement` tout élément de `document` pendant qu'il défile.
 *
 * Rend la fonction qui **détache** l'écoute : elle retire aussi les marques
 * encore posées, pour qu'un démontage ne laisse aucune barre allumée derrière
 * lui. Un même élément qui défile sans s'arrêter n'est marqué qu'une fois — la
 * marque suit le dernier `scroll`, elle ne se repose pas à chaque tic.
 */
export function ecouterDefilement(
  document: Document,
  repos: number = REPOS_DEFILEMENT_MS,
): () => void {
  const minuteurs = new Map<Element, ReturnType<typeof setTimeout>>();

  const surDefilement = (evenement: Event) => {
    // Le défilement de la fenêtre arrive avec `document` pour cible : c'est
    // l'élément racine qui porte alors l'ascenseur, et c'est lui qu'on marque.
    const cible =
      evenement.target === document
        ? document.documentElement
        : evenement.target;
    if (!(cible instanceof Element)) return;
    const enCours = minuteurs.get(cible);
    if (enCours === undefined) cible.setAttribute(ATTRIBUT_DEFILEMENT, "");
    else clearTimeout(enCours);
    minuteurs.set(
      cible,
      setTimeout(() => {
        minuteurs.delete(cible);
        cible.removeAttribute(ATTRIBUT_DEFILEMENT);
      }, repos),
    );
  };

  // `capture` : `scroll` ne remonte pas, il ne s'entend qu'à la descente.
  // `passive` : on n'empêche jamais rien — le défilement reste entier.
  document.addEventListener("scroll", surDefilement, {
    capture: true,
    passive: true,
  });
  return () => {
    document.removeEventListener("scroll", surDefilement, { capture: true });
    for (const [element, minuteur] of minuteurs) {
      clearTimeout(minuteur);
      element.removeAttribute(ATTRIBUT_DEFILEMENT);
    }
    minuteurs.clear();
  };
}

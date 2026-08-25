"use client";

/**
 * L'infobulle accessible (#536, lot 4 de #532).
 *
 * L'état mesuré par la recherche #471 (docs/30 §3.4) : `title=` natif, **42
 * occurrences sur 23 fichiers**, et **zéro** `role="tooltip"`. Le `title` du
 * navigateur a trois défauts qu'aucun réglage ne corrige — il n'apparaît **pas
 * au clavier**, **pas au tactile**, et son délai d'apparition ne se règle pas.
 * Posé, comme ici, sur des `<span>` et des `<p>` qui ne sont même pas
 * focusables, il est purement et simplement invisible à qui n'a pas de souris.
 *
 * **Ce composant s'adresse au contenu NON focusable, et c'est sa raison
 * d'être.** Le wrapper porte `tabIndex={0}` : c'est lui qui rend l'information
 * atteignable au clavier, ce qui est tout le problème. Quand la cible est
 * **déjà** focusable (un bouton), on n'enrobe rien — l'information rejoint son
 * `aria-label` ou un `aria-describedby` posé sur l'élément lui-même. Enrober un
 * bouton créerait un second arrêt de tabulation pour la même chose, et
 * `aria-describedby` posé sur un parent ne se lit pas.
 *
 * Deux choix méritent leur explication :
 *
 * - **La bulle est toujours dans le DOM**, visuellement masquée par `sr-only`
 *   tant qu'elle est au repos, et `aria-describedby` la référence en
 *   permanence. Le réflexe inverse — ne la monter qu'à l'ouverture — laisse
 *   plusieurs lecteurs d'écran manquer la description, annoncée au moment même
 *   où le nœud apparaît. Ici l'information est acquise dès que le focus arrive,
 *   que la bulle soit affichée ou non.
 * - **La bulle vit à l'intérieur du wrapper**, pas à côté : le pointeur peut
 *   donc entrer dedans sans la faire disparaître, ce qu'exige WCAG 2.1 (1.4.13,
 *   « Content on Hover or Focus »). `Échap` la referme, pour la même règle.
 */

import { useId, useState, type ReactNode } from "react";

export function Infobulle({
  texte,
  children,
  className = "",
}: {
  /** Ce que le `title` disait — l'information qui n'est portée nulle part ailleurs. */
  texte: string;
  /** Le contenu décrit. Non focusable : c'est le wrapper qui le devient. */
  children: ReactNode;
  /**
   * Classes du wrapper, pour qu'il n'altère pas la mise en page de l'appelant.
   * Elles **remplacent** le `inline` par défaut plutôt que de s'y ajouter :
   * deux utilitaires `display` concurrents dans la même liste se départagent
   * par l'ordre de la feuille Tailwind et non par celui de la chaîne,
   * c'est-à-dire de façon imprévisible.
   *
   * Le défaut est `inline`, et non `inline-flex`, parce que c'est le seul
   * affichage **neutre** : le wrapper prend la place d'un `title=`, qui n'en
   * occupait aucune. Un `inline-flex` est une boîte atomique, que le `truncate`
   * du parent ne sait plus abréger — les tuiles de chiffres et les cellules
   * tronquées déborderaient au lieu de finir en points de suspension.
   */
  className?: string;
}) {
  const id = useId();
  const [visible, setVisible] = useState(false);

  return (
    // `jsx-a11y/recommended` est passé en `error` (#537), et les deux règles
    // éteintes ici — à la ligne près, pour ces règles-là — décrivent le défaut
    // **inverse** de celui-ci : un élément inerte qu'on a rendu *actionnable* à
    // la main, sans clavier ni rôle. Ce wrapper n'active rien. Il ne fait que
    // rendre une description **atteignable au clavier**, ce qui est le motif
    // ARIA du `tooltip` quand le contenu décrit n'est pas focusable (voir
    // l'en-tête de ce fichier), et son unique `onKeyDown` referme la bulle sur
    // `Échap` — exigé par WCAG 2.1 §1.4.13, pas une activation déguisée. Lui
    // donner `role="button"` pour faire taire le lint mentirait au lecteur
    // d'écran sur ce qu'il y a à faire ; l'enrober d'un `<button>` créerait
    // l'arrêt de tabulation en double que l'en-tête écarte déjà.
    // eslint-disable-next-line jsx-a11y/no-static-element-interactions
    <span
      className={"relative " + (className.trim() || "inline")}
      onPointerEnter={() => setVisible(true)}
      onPointerLeave={() => setVisible(false)}
      onFocus={() => setVisible(true)}
      onBlur={() => setVisible(false)}
      onKeyDown={(evenement) => {
        if (evenement.key !== "Escape" || !visible) return;
        // Sans `stopPropagation`, l'`Échap` qui ferme la bulle fermerait dans la
        // foulée la modale ou le menu qui la contient — deux gestes pour une
        // seule frappe.
        evenement.stopPropagation();
        setVisible(false);
      }}
      // Même exemption, même motif : le `tabIndex` n'ajoute pas une action, il
      // ajoute le seul point d'entrée clavier vers `aria-describedby`. Sans
      // lui, l'information n'existe que pour qui a une souris — c'est-à-dire le
      // défaut exact que ce composant a été écrit pour corriger.
      // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex
      tabIndex={0}
      aria-describedby={id}
    >
      {children}
      <span
        role="tooltip"
        id={id}
        className={
          visible
            ? "pointer-events-auto absolute bottom-full left-1/2 z-50 mb-1.5 w-max max-w-64 -translate-x-1/2 rounded-md border border-neutral-200 bg-white px-2 py-1 text-annexe font-normal whitespace-pre-line text-neutral-700 shadow-lg dark:border-neutral-700 dark:bg-neutral-900 dark:text-neutral-200"
            : "sr-only"
        }
      >
        {texte}
      </span>
    </span>
  );
}

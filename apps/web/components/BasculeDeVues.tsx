"use client";

/**
 * La bascule de vues — **le second niveau d'un bloc** (#539, lot 7 de #532).
 *
 * C'est l'une des deux réponses que la règle des trois places (docs/30 §4) donne
 * à un écran dont le corps déborde — l'autre étant la colonne de propriétés —,
 * et la seule qui ne retire aucune information : ce qui cesse d'être un bloc
 * reste à un clic. Plusieurs lectures d'un même sujet, une à la fois.
 *
 * Elle était écrite dans `components/runs/VueRun.tsx` (#491, troisième position
 * #516) et vit ici depuis que `/couts` l'a demandée à son tour pour son bloc
 * « Détail de la période » : deux barres d'onglets identiques écrites deux fois
 * sont exactement la recopie que ce chantier retire. Le DOM n'a pas bougé d'un
 * attribut.
 *
 * Des **boutons** et non des liens, contrairement aux onglets d'une fiche agent
 * (`components/OngletsAgent`) : ceux-là changent de page, celui-ci change de
 * regard sur la page qu'on a déjà. `aria-current="page"` marque l'actif.
 *
 * ⚠ Deux choix à ne pas défaire :
 *
 * - **ce n'est pas le motif `tablist` d'ARIA**, et c'est délibéré : le produit
 *   n'en déclare aucun (docs/30 §3.4, où les barres d'onglets sont assumées par
 *   écrit comme des `<nav>`), et en introduire un ici obligerait à en tenir tout
 *   le contrat — flèches, `aria-controls`, `tabpanel`, `aria-selected` — sur une
 *   barre pendant que les quatre autres s'en passent ;
 * - **le fichier est à part de `Primitives.tsx`**, comme `Infobulle`, et pour la
 *   même raison : il appelle un hook (`useId`), et `Primitives.tsx` est partagé
 *   avec des composants serveur, où aucun hook ne peut tourner.
 */

import { Fragment, useId } from "react";

import type { Icone } from "@/components/Primitives";

/** Une vue de la bascule : sa clé, son mot, ce à quoi elle répond. */
export type VueBascule<C extends string> = {
  cle: C;
  libelle: string;
  /**
   * La question à laquelle la vue répond. Rendue en **description** accessible
   * et non dans le nom de l'onglet : l'ajouter au nom ferait annoncer « Journal,
   * ce qui s'est passé… » à chaque tabulation, alors qu'un nom d'onglet doit
   * rester le mot qu'on cherche. Et un `title=` ne l'aurait donnée qu'à la
   * souris (#536).
   */
  question?: string;
  icone?: Icone;
};

export function BasculeDeVues<C extends string>({
  etiquette,
  vues,
  courante,
  choisir,
  className = "",
}: {
  /** Ce que la bascule commande — le nom accessible de la navigation. */
  etiquette: string;
  vues: VueBascule<C>[];
  courante: C;
  choisir: (cle: C) => void;
  className?: string;
}) {
  const base = useId();
  return (
    <nav
      aria-label={etiquette}
      className={
        "flex flex-wrap gap-1 border-b border-neutral-200 dark:border-neutral-800 " +
        className
      }
    >
      {vues.map(({ cle, libelle, question, icone: Icone }) => {
        const courant = cle === courante;
        return (
          // La question vit **hors** du bouton : dedans, elle serait lue comme
          // une partie de son nom, ce que `aria-describedby` est justement là
          // pour éviter.
          <Fragment key={cle}>
            <button
              type="button"
              onClick={() => choisir(cle)}
              aria-current={courant ? "page" : undefined}
              aria-describedby={question ? `${base}-${cle}` : undefined}
              className={
                "-mb-px inline-flex items-center gap-1.5 rounded-t-md border-b-2 px-3 py-2 text-corps transition-colors motion-reduce:transition-none " +
                (courant
                  ? "border-emerald-600 font-medium text-neutral-900 dark:border-emerald-500 dark:text-neutral-100"
                  : "border-transparent text-neutral-500 hover:bg-neutral-100 hover:text-neutral-900 dark:text-neutral-400 dark:hover:bg-neutral-900 dark:hover:text-neutral-100")
              }
            >
              {Icone && <Icone className="size-4 shrink-0" />}
              {libelle}
            </button>
            {question && (
              <span id={`${base}-${cle}`} className="sr-only">
                {question}
              </span>
            )}
          </Fragment>
        );
      })}
    </nav>
  );
}

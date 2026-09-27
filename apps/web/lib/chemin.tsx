/**
 * Un chemin de dossier **affiché** dans une surface étroite (#1294, #1180).
 *
 * Deux surfaces du fil nomment un projet par son dossier — la bulle d'un projet
 * créé (`components/Conversation`, « Projet créé : ») et la carte d'une
 * proposition de run (`components/chat/DemandeDeCadrage`, « Dans le projet ») —,
 * et les deux vivent aussi dans la colonne de conversation du shell, à 320 px. La
 * coupure s'écrit donc une fois, ici : deux copies finiraient par ne plus couper
 * le même chemin au même endroit.
 */

import { Fragment, type ReactNode } from "react";

/**
 * Un chemin qui ne se coupe qu'à ses séparateurs : une occasion de coupure
 * (`<wbr>`) après chaque `/`, et `break-words` en dernier recours pour un nom de
 * dossier plus large que la colonne entière — à poser par l'appelant. Vu à la
 * relecture de clôture de #1294 : `break-all` coupait en plein nom de dossier.
 */
export function cheminASesSeparateurs(chemin: string): ReactNode {
  const segments = chemin.split("/");
  return segments.map((segment, rang) => (
    <Fragment key={rang}>
      {segment}
      {rang < segments.length - 1 && (
        <>
          /<wbr />
        </>
      )}
    </Fragment>
  ));
}

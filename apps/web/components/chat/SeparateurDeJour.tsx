"use client";

/**
 * Le trait daté qui ouvre une journée dans le fil (#697, lot 7 de #690).
 *
 * C'est un **`<li>` du fil**, et pas un `role="separator"` posé dessus : le
 * `<ol>` du fil ne doit contenir que des `<li>` (règle `list` d'axe), et la date
 * n'est pas une décoration — c'est la seule chose qui distingue deux bulles
 * séparées de trois jours. Elle se lit donc à voix haute comme le reste, à sa
 * place dans l'ordre. Seuls les deux filets sont `aria-hidden` : eux ne portent
 * rien.
 *
 * Le libellé (« Aujourd'hui », « Hier », la date) vient de `lib/journees`, qui
 * tient la règle d'hydratation : sans horloge, la date absolue ; « Aujourd'hui »
 * au premier battement.
 */

export function SeparateurDeJour({ libelle }: { libelle: string }) {
  return (
    <li className="flex items-center gap-3 pt-2 pb-1">
      <span aria-hidden="true" className="h-px flex-1 bg-bord" />
      <span className="text-micro text-texte-secondaire">{libelle}</span>
      <span aria-hidden="true" className="h-px flex-1 bg-bord" />
    </li>
  );
}

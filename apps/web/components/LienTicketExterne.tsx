/**
 * La référence du ticket externe d'une tâche (#192) : de la carte du Kanban
 * jusqu'au ticket GitLab / Jira qui l'a motivée, en un clic.
 *
 * Trois règles tiennent le composant :
 *
 * - **Rien à montrer ⇒ rien de rendu.** Une tâche sans référence laisse la vue
 *   strictement inchangée : pas de puce vide, pas d'espace réservé.
 * - **Jamais de lien mort.** Une référence dont l'URL n'est pas suivable
 *   (`lienExterneSur`) s'affiche en texte simple : l'identifiant reste lisible,
 *   il n'y a juste rien à cliquer.
 * - **L'URL vient du flux, pas de l'UI.** Elle est filtrée par `lienExterneSur`
 *   et le lien part en `rel="noopener noreferrer"` — la page ouverte n'obtient
 *   aucune poignée sur la Control Tower.
 *
 * L'UI ne connaît aucun outil de ticketing : elle affiche l'identifiant rendu
 * par le backend (« #192 », « MAE-42 ») et ouvre l'URL qui l'accompagne.
 */

import { IconeLienExterne, IconeTicket } from "@/components/Icones";
import { Infobulle } from "@/components/Infobulle";
import { lienExterneSur } from "@/lib/liens";
import type { ReferenceTicket } from "@/lib/types";

type Props = {
  /**
   * La référence portée par la tâche. `null`/`undefined` — le cas courant tant
   * que le lot backend (#187) ne renseigne pas le champ — ne rend rien.
   */
  reference: ReferenceTicket | null | undefined;
  /** Le nom de la tâche, pour que le libellé accessible dise de quoi on parle. */
  tache?: string;
  /** Classes de placement laissées à l'appelant (marges, alignement). */
  className?: string;
};

/** Le libellé de repli quand la référence n'a qu'une URL, sans identifiant. */
const LIBELLE_PAR_DEFAUT = "Ticket externe";

// Les teintes tiennent les deux thèmes : `sky-700` sur fond clair, `sky-300`
// sur fond sombre — le même accent que la colonne « Assignées » du Kanban.
const STYLE_COMMUN = "inline-flex max-w-full items-center gap-1 text-annexe";
const STYLE_LIEN =
  "rounded text-sky-700 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-600 dark:text-sky-300 dark:focus-visible:outline-sky-400";
const STYLE_TEXTE = "text-neutral-500 dark:text-neutral-400";

export function LienTicketExterne({ reference, tache, className }: Props) {
  if (!reference) return null;

  const identifiant = (reference.id ?? "").trim();
  const url = lienExterneSur(reference.url);
  // Ni identifiant lisible ni URL suivable : la référence n'apprend rien.
  if (!identifiant && url === null) return null;

  const libelle = identifiant || LIBELLE_PAR_DEFAUT;
  const surTache = tache ? ` de la tâche ${tache}` : "";
  const classes = `${STYLE_COMMUN} ${className ?? ""}`.trimEnd();

  if (url === null) {
    return (
      <Infobulle
        texte={`Ticket externe${surTache} — aucune URL exploitable`}
        className={`${classes} ${STYLE_TEXTE}`}
      >
        <IconeTicket className="size-3.5 shrink-0" />
        <span className="truncate">{libelle}</span>
      </Infobulle>
    );
  }

  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      // Pas de `title={url}` (#536) : sur un lien, la destination est déjà
      // portée par `href` — le navigateur l'affiche dans sa barre d'état et le
      // lecteur d'écran sait l'annoncer. Le `title` ne faisait que la répéter.
      aria-label={`Ouvrir le ticket externe ${libelle}${surTache} dans un nouvel onglet`}
      className={`${classes} ${STYLE_LIEN}`}
    >
      <IconeTicket className="size-3.5 shrink-0" />
      <span className="truncate">{libelle}</span>
      <IconeLienExterne className="size-3 shrink-0" />
    </a>
  );
}

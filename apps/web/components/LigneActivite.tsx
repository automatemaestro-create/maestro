"use client";

/**
 * Une ligne d'activité (#250) — la brique commune du fil du tableau de bord, du
 * centre de notifications et, demain, de l'onglet Logs d'un agent.
 *
 * Elle existe pour que ces trois endroits ne se mettent pas à formater dans leur
 * coin : la phrase vient de `resumeEvenement`, le détail brut de
 * `detailEvenement`, l'âge de `formatHeureRelative`. Ce qui reste ici est ce qui
 * ne peut pas vivre dans `lib/` : le pliage et l'accessibilité.
 *
 * Le pliage est offert sur **toutes** les lignes, pas seulement sur les rafales.
 * C'est ce qui rend tenable la promesse du lot — une phrase à la place d'un
 * identifiant — sans rien perdre : l'identifiant, le statut du bus et le texte
 * libre du moteur sont à un clic, toujours au même endroit.
 */

import { useId, useState } from "react";

import type { Icone } from "@/components/Primitives";
import {
  detailEvenement,
  iconeEvenement,
  resumeEvenement,
  type GroupeEvenements,
} from "@/lib/evenements";
import { formatDateHeure, formatHeure, formatHeureRelative } from "@/lib/format";
import { useHorloge } from "@/lib/horloge";

/**
 * La colonne de gauche d'une ligne : l'icône du type d'événement, prise dans le
 * jeu SVG du lot 1 (#245). Elle arrive **en prop** — l'idiome du dépôt
 * (`EnTeteSection`, `Kanban`) — plutôt que par un `const Icone = …` calculé dans
 * le corps du composant, qui serait un composant recréé à chaque rendu.
 */
function PuceEvenement({
  icone: Composant,
  className,
}: {
  icone: Icone;
  className?: string;
}) {
  // Décorative : la phrase juste à côté porte déjà le sens.
  return <Composant aria-hidden="true" className={className} />;
}

export function LigneActivite({
  groupe,
  compact = false,
}: {
  groupe: GroupeEvenements;
  /** Version resserrée, taillée pour la largeur du centre de notifications. */
  compact?: boolean;
}) {
  const [deplie, setDeplie] = useState(false);
  const maintenant = useHorloge();
  const panneau = useId();

  const { tete, evenements } = groupe;
  const nombre = evenements.length;
  // Le dépli d'une rafale raconte la tâche dans l'ordre où elle s'est jouée,
  // alors que le fil, lui, va du plus récent au plus ancien.
  const chronologie = nombre > 1 ? [...evenements].reverse() : [];

  return (
    <li className={compact ? "text-annexe" : "text-corps"}>
      <button
        type="button"
        onClick={() => setDeplie((avant) => !avant)}
        aria-expanded={deplie}
        aria-controls={panneau}
        className={`flex w-full items-center gap-2 rounded text-left hover:bg-neutral-100 dark:hover:bg-neutral-900 ${
          compact ? "px-1 py-1" : "px-1 py-0.5"
        }`}
      >
        {/* `chiffre` : l'âge se réévalue sous les yeux (#245, un compteur qui
            change ne doit pas faire sauter la ligne autour de lui). */}
        <time
          dateTime={tete.horodatage}
          title={formatDateHeure(tete.horodatage)}
          className={`chiffre shrink-0 font-mono text-neutral-400 dark:text-neutral-500 ${
            compact ? "text-micro" : "text-annexe"
          }`}
        >
          {formatHeureRelative(tete.horodatage, maintenant)}
        </time>
        <PuceEvenement
          icone={iconeEvenement(tete)}
          className={`shrink-0 text-neutral-400 dark:text-neutral-500 ${
            compact ? "size-3.5" : "size-4"
          }`}
        />
        <span
          className={`min-w-0 flex-1 truncate ${
            compact ? "text-neutral-600 dark:text-neutral-300" : ""
          }`}
        >
          {resumeEvenement(tete)}
        </span>
        {nombre > 1 && (
          <span className="chiffre shrink-0 rounded-full bg-neutral-100 px-1.5 text-micro font-medium text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300">
            {nombre} étapes
          </span>
        )}
      </button>

      {/* Toujours dans le DOM (`aria-controls` doit désigner quelque chose),
          masqué tant qu'on ne l'a pas ouvert. */}
      <div
        id={panneau}
        hidden={!deplie}
        className="mt-1 mb-1 ml-6 border-l border-neutral-200 pl-3 dark:border-neutral-800"
      >
        {chronologie.length > 0 && (
          <ol className="mb-2 space-y-0.5">
            {chronologie.map((evenement, index) => (
              <li
                key={`${evenement.horodatage}-${index}`}
                className="flex items-baseline gap-2 text-annexe text-neutral-600 dark:text-neutral-300"
              >
                <span className="chiffre shrink-0 font-mono text-micro text-neutral-400 dark:text-neutral-500">
                  {formatHeure(evenement.horodatage)}
                </span>
                <span className="min-w-0" title={evenement.detail || undefined}>
                  {resumeEvenement(evenement)}
                </span>
              </li>
            ))}
          </ol>
        )}
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-micro">
          {detailEvenement(tete).map((champ) => (
            <div key={champ.libelle} className="contents">
              <dt className="text-neutral-500 dark:text-neutral-400">
                {champ.libelle}
              </dt>
              <dd className="min-w-0 font-mono break-words text-neutral-700 dark:text-neutral-300">
                {champ.valeur}
              </dd>
            </div>
          ))}
        </dl>
      </div>
    </li>
  );
}

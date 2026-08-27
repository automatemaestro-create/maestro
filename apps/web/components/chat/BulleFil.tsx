"use client";

/**
 * La bulle du fil, **sans ce qu'elle porte** (#483, lot 2 de #481).
 *
 * Elle vivait en privé dans `FilChat` tant qu'un fil ne portait que des
 * messages. Le cadrage y arrive — le brief, les questions de clarification et
 * leurs réponses se lisent désormais dans la conversation —, et deux formes de
 * bulle auraient donné deux fils : celui des messages et celui du cadrage,
 * distincts à l'œil sur le même écran, alors que c'est la même conversation qui
 * les porte.
 *
 * Deux propriétés à ne pas défaire :
 *
 * - **le côté dit qui parle**, et rien d'autre ne le dit : l'utilisateur à
 *   droite en plein, l'agent à gauche en surface neutre. Le pied redit le nom
 *   parce qu'une bulle relue au lecteur d'écran n'a ni gauche ni droite ;
 * - **la largeur est un choix de l'appelant**, pas une propriété de l'auteur.
 *   Un message tient dans 70 % de la colonne ; un brief de sept sections
 *   éditables, non — et le rétrécir ferait de la correction un travail de
 *   contorsion, c'est-à-dire la friction qui fait approuver sans lire (docs/05
 *   §2.7.4). D'où `pleineLargeur`, demandé au cas par cas.
 */

import type { ReactNode } from "react";

import { Infobulle } from "@/components/Infobulle";
import { formatDateHeure, formatHeure } from "@/lib/format";

export function BulleFil({
  auteur,
  utilisateur = false,
  horodatage,
  pleineLargeur = false,
  children,
}: {
  /** Le nom affiché au pied — remplacé par « vous » côté utilisateur. */
  auteur: string;
  utilisateur?: boolean;
  /** Horodatage ISO, quand le fil en connaît un (une saisie en cours, non). */
  horodatage?: string;
  /** Le contenu déborde la largeur d'un message : brief, formulaire, rapport. */
  pleineLargeur?: boolean;
  children: ReactNode;
}) {
  return (
    <li className={"flex " + (utilisateur ? "justify-end" : "justify-start")}>
      <div
        className={
          "rounded-lg px-3 py-2 text-sm shadow-sm " +
          (pleineLargeur ? "w-full " : "max-w-[85%] sm:max-w-[70%] ") +
          (utilisateur
            ? "bg-emerald-600 text-white dark:bg-emerald-700"
            : "border border-neutral-200 bg-white text-neutral-900 dark:border-neutral-800 dark:bg-neutral-900 dark:text-neutral-100")
        }
      >
        {children}
        <p
          className={
            "mt-1 text-right text-[10px] " +
            (utilisateur
              ? "text-emerald-100"
              : "text-neutral-400 dark:text-neutral-500")
          }
        >
          {utilisateur ? "vous" : auteur}
          {horodatage !== undefined && (
            <>
              {" · "}
              <Infobulle texte={formatDateHeure(horodatage)}>
                <time dateTime={horodatage}>{formatHeure(horodatage)}</time>
              </Infobulle>
            </>
          )}
        </p>
      </div>
    </li>
  );
}

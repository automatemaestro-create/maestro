"use client";

/**
 * Le menu d'aide de la barre supérieure (#122, lot 6 de #116) : le point
 * d'entrée de l'accompagnement, présent sur toutes les pages via le shell. Il
 * porte la **relance de la visite guidée** — qui ne se déclenche d'elle-même
 * qu'à la première visite — et l'ouverture de l'**assistant** (#123), dont le
 * bouton flottant reste le point d'entrée principal : on le retrouve ici parce
 * que c'est là qu'on cherche de l'aide.
 *
 * Un menu plutôt qu'un bouton direct, pour cette raison même : la géométrie de
 * la barre n'a pas bougé quand le lot suivant y a ajouté son entrée.
 *
 * Le comportement du menu (clic à l'extérieur, `Échap`, focus rendu au bouton,
 * et depuis #536 la navigation aux flèches) ne « reprend » plus celui de la
 * bascule de thème : les deux **sourcent le même hook**, `useSurfaceDeroulee`.
 * C'est le sens du lot — quatre menus portaient quatre copies du même bloc, et
 * c'est pour ça qu'aucun des quatre n'avait de navigation au clavier.
 */

import { useCallback, useRef, useState } from "react";

import { IconeAide } from "@/components/Icones";
import { ouvrirAssistant } from "@/lib/assistance";
import { ETAPES_GUIDE, lancerGuide } from "@/lib/guide";
import { useSurfaceDeroulee } from "@/lib/useSurfaceDeroulee";

export function MenuAide() {
  const [ouvert, setOuvert] = useState(false);
  const conteneur = useRef<HTMLDivElement>(null);
  const declencheur = useRef<HTMLButtonElement>(null);
  const surface = useRef<HTMLDivElement>(null);

  // Clic à l'extérieur, `Échap`, flèches, `Home`/`End` et focus d'entrée : tout
  // vient du hook partagé (#536), qui a remplacé quatre copies du même bloc.
  const fermer = useCallback(() => setOuvert(false), []);
  useSurfaceDeroulee({ ouvert, fermer, conteneur, declencheur, surface });

  return (
    // `data-guide` : la dernière étape de la visite pointe ce bouton — c'est
    // par lui qu'on la relance.
    <div ref={conteneur} data-guide="aide" className="relative">
      <button
        ref={declencheur}
        type="button"
        onClick={() => setOuvert((avant) => !avant)}
        aria-haspopup="menu"
        aria-expanded={ouvert}
        aria-label="Aide"
        className="block rounded-md p-2 text-neutral-500 hover:bg-neutral-100 hover:text-neutral-900 dark:text-neutral-400 dark:hover:bg-neutral-900 dark:hover:text-neutral-100"
      >
        <IconeAide className="size-5" />
      </button>

      {ouvert && (
        <div
          ref={surface}
          role="menu"
          aria-label="Aide"
          tabIndex={-1}
          className="absolute top-full right-0 z-20 mt-2 w-64 max-w-[calc(100vw-1.5rem)] rounded-lg border border-neutral-200 bg-white py-1 shadow-lg dark:border-neutral-800 dark:bg-neutral-900"
        >
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              // Fermé d'abord : le menu recouvrirait la première surbrillance.
              setOuvert(false);
              // Le focus rendu au bouton **avant** le lancement : c'est lui que
              // la visite retiendra comme point de retour, là où l'entrée de
              // menu, elle, va être démontée.
              declencheur.current?.focus();
              lancerGuide();
            }}
            className="flex w-full flex-col items-start gap-0.5 px-3 py-2 text-left hover:bg-neutral-100 dark:hover:bg-neutral-800"
          >
            <span className="text-sm font-medium">Visite guidée</span>
            <span className="text-xs text-neutral-500 dark:text-neutral-400">
              Redécouvrir la Control Tower en {ETAPES_GUIDE.length} étapes
            </span>
          </button>
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              // Le menu se referme, mais le focus va au panneau (qui prend la
              // main sur sa zone de saisie), pas au bouton d'aide : c'est là que
              // l'utilisateur écrit sa question.
              setOuvert(false);
              ouvrirAssistant();
            }}
            className="flex w-full flex-col items-start gap-0.5 px-3 py-2 text-left hover:bg-neutral-100 dark:hover:bg-neutral-800"
          >
            <span className="text-sm font-medium">Poser une question</span>
            <span className="text-xs text-neutral-500 dark:text-neutral-400">
              Ouvrir l&apos;assistant, sans quitter la page
            </span>
          </button>
        </div>
      )}
    </div>
  );
}

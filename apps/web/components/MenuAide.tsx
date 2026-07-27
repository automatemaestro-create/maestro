"use client";

/**
 * Le menu d'aide de la barre supérieure (#122, lot 6 de #116) : le point
 * d'entrée de l'accompagnement, présent sur toutes les pages via le shell. Il
 * porte pour l'instant la **relance de la visite guidée** — qui ne se déclenche
 * d'elle-même qu'à la première visite — et reste l'emplacement de l'assistant
 * à venir (#123).
 *
 * Un menu plutôt qu'un bouton direct, pour cette raison même : la géométrie de
 * la barre ne bougera pas quand le lot suivant y ajoutera son entrée.
 *
 * Le comportement du menu (clic à l'extérieur, Échap, focus rendu au bouton)
 * reprend celui de la bascule de thème (#118, `BasculeTheme`).
 */

import { useEffect, useRef, useState } from "react";

import { IconeAide } from "@/components/Icones";
import { ETAPES_GUIDE, lancerGuide } from "@/lib/guide";

export function MenuAide() {
  const [ouvert, setOuvert] = useState(false);
  const conteneur = useRef<HTMLDivElement>(null);
  const declencheur = useRef<HTMLButtonElement>(null);

  // Fermeture du menu : clic à l'extérieur ou Échap (le focus revient alors au
  // bouton, sans quoi il retomberait sur le document).
  useEffect(() => {
    if (!ouvert) return;
    const surPointeur = (evenement: PointerEvent) => {
      if (!conteneur.current?.contains(evenement.target as Node)) {
        setOuvert(false);
      }
    };
    const surTouche = (evenement: KeyboardEvent) => {
      if (evenement.key !== "Escape") return;
      setOuvert(false);
      declencheur.current?.focus();
    };
    document.addEventListener("pointerdown", surPointeur);
    document.addEventListener("keydown", surTouche);
    return () => {
      document.removeEventListener("pointerdown", surPointeur);
      document.removeEventListener("keydown", surTouche);
    };
  }, [ouvert]);

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
        title="Aide"
        className="block rounded-md p-2 text-neutral-500 hover:bg-neutral-100 hover:text-neutral-900 dark:text-neutral-400 dark:hover:bg-neutral-900 dark:hover:text-neutral-100"
      >
        <IconeAide className="size-5" />
      </button>

      {ouvert && (
        <div
          role="menu"
          aria-label="Aide"
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
        </div>
      )}
    </div>
  );
}

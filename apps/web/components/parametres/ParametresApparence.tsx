"use client";

/**
 * Section « Apparence » des Paramètres (#121) : les préférences d'affichage du
 * poste — thème (#118) et repli de la barre latérale (#117).
 *
 * Les deux réglages existent déjà dans la barre supérieure ; ils sont ici la
 * **même** commande, pas une copie : les modules `lib/theme` et
 * `lib/preferences` portent la valeur et notifient les autres contrôles, si
 * bien qu'un changement fait ici bascule l'interface immédiatement et met à jour
 * la barre supérieure (et les autres onglets).
 */

import { useEffect, useState } from "react";

import { IconeEcran, IconeLune, IconeSoleil } from "@/components/Icones";
import {
  ecouterRepliSidebar,
  ecrireRepliSidebar,
  lireRepliSidebar,
} from "@/lib/preferences";
import {
  appliquer,
  ecouterChoix,
  ecrireChoix,
  lireChoix,
  type ChoixTheme,
} from "@/lib/theme";

import { Interrupteur, LigneReglage } from "./SectionParametres";

const THEMES: {
  valeur: ChoixTheme;
  libelle: string;
  Icone: (props: { className?: string }) => React.ReactNode;
}[] = [
  { valeur: "clair", libelle: "Clair", Icone: IconeSoleil },
  { valeur: "sombre", libelle: "Sombre", Icone: IconeLune },
  { valeur: "systeme", libelle: "Système", Icone: IconeEcran },
];

export function ParametresApparence() {
  return (
    <div className="flex flex-col">
      <ReglageTheme />
      <ReglageSidebar />
    </div>
  );
}

function ReglageTheme() {
  // Rendu serveur : « Système », le défaut — le serveur ne connaît pas le
  // localStorage. Contrairement à la bascule de la barre supérieure (dont
  // l'icône est pilotée par le CSS et le menu rendu à l'ouverture seulement),
  // l'état coché des trois choix est **dans** le HTML : le lire pendant le
  // rendu ferait diverger les deux arbres. Il est donc restitué après
  // l'hydratation, différé d'un tick comme partout ailleurs.
  const [choix, setChoix] = useState<ChoixTheme>("systeme");

  useEffect(() => {
    const tick = setTimeout(() => setChoix(lireChoix()), 0);
    const detacher = ecouterChoix(setChoix);
    return () => {
      clearTimeout(tick);
      detacher();
    };
  }, []);

  const choisir = (valeur: ChoixTheme) => {
    ecrireChoix(valeur);
    appliquer(valeur);
  };

  return (
    <LigneReglage
      libelle="Thème de l'interface"
      aide="« Système » suit la préférence de l'appareil, y compris quand elle change en cours de session."
    >
      <div
        role="radiogroup"
        aria-label="Thème de l'interface"
        className="flex flex-wrap gap-1"
      >
        {THEMES.map(({ valeur, libelle, Icone }) => {
          const actif = valeur === choix;
          return (
            <button
              key={valeur}
              type="button"
              role="radio"
              aria-checked={actif}
              onClick={() => choisir(valeur)}
              className={
                "flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm " +
                (actif
                  ? "border-neutral-400 bg-neutral-100 font-medium dark:border-neutral-600 dark:bg-neutral-800"
                  : "border-neutral-200 text-neutral-600 hover:bg-neutral-50 dark:border-neutral-800 dark:text-neutral-400 dark:hover:bg-neutral-800")
              }
            >
              <Icone className="size-4 shrink-0" />
              {libelle}
            </button>
          );
        })}
      </div>
    </LigneReglage>
  );
}

function ReglageSidebar() {
  // Rendu serveur : déplié — comme le shell, qui lit le stockage après
  // l'hydratation. L'abonnement ci-dessous restitue le choix aussitôt.
  const [repliee, setRepliee] = useState(false);

  useEffect(() => {
    const tick = setTimeout(() => setRepliee(lireRepliSidebar()), 0);
    const detacher = ecouterRepliSidebar(setRepliee);
    return () => {
      clearTimeout(tick);
      detacher();
    };
  }, []);

  return (
    <LigneReglage
      libelle="Barre latérale repliée"
      aide="Réduit la navigation à ses icônes. Sous les grands écrans, le rail d'icônes est imposé de toute façon."
    >
      <Interrupteur
        libelle="Barre latérale repliée"
        actif={repliee}
        basculer={() => ecrireRepliSidebar(!repliee)}
      />
    </LigneReglage>
  );
}

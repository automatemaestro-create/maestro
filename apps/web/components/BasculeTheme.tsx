"use client";

/**
 * La bascule de thème de la barre supérieure (#118, lot 2 de #116) : clair,
 * sombre ou « système » (le défaut — on suit la préférence de l'appareil).
 *
 * Trois choix plutôt qu'un simple va-et-vient clair/sombre : « système » est un
 * état à part entière, qui continue de suivre l'OS quand celui-ci bascule au
 * coucher du soleil. Le choix vit dans le localStorage (`lib/theme.ts`), lu à
 * l'identique par le script d'init du layout — d'où l'absence de flash au
 * chargement, et d'écart entre l'état React et le thème déjà appliqué au DOM.
 */

import { useEffect, useRef, useState } from "react";

import {
  IconeCoche,
  IconeEcran,
  IconeLune,
  IconeSoleil,
} from "@/components/Icones";
import {
  appliquer,
  CLE_THEME,
  ecouterSysteme,
  ecrireChoix,
  lireChoix,
  type ChoixTheme,
} from "@/lib/theme";

const OPTIONS: {
  valeur: ChoixTheme;
  libelle: string;
  Icone: (props: { className?: string }) => React.ReactNode;
}[] = [
  { valeur: "clair", libelle: "Clair", Icone: IconeSoleil },
  { valeur: "sombre", libelle: "Sombre", Icone: IconeLune },
  { valeur: "systeme", libelle: "Système", Icone: IconeEcran },
];

export function BasculeTheme() {
  // Initialisation paresseuse sur la même source que le script d'init : les
  // deux lisent le localStorage, ils ne peuvent donc pas diverger. Rien du HTML
  // rendu par le serveur n'en dépend — le menu n'existe qu'une fois ouvert et
  // l'icône du bouton bascule en CSS —, donc aucun écart d'hydratation.
  const [choix, setChoix] = useState<ChoixTheme>(() =>
    typeof window === "undefined" ? "systeme" : lireChoix(),
  );
  const [ouvert, setOuvert] = useState(false);
  const conteneur = useRef<HTMLDivElement>(null);
  const declencheur = useRef<HTMLButtonElement>(null);

  // En mode « système », l'OS peut basculer pendant que la page est ouverte.
  useEffect(() => {
    if (choix !== "systeme") return;
    return ecouterSysteme(() => appliquer("systeme"));
  }, [choix]);

  // Un autre onglet a changé le thème : le suivre plutôt que diverger.
  useEffect(() => {
    const surStockage = (evenement: StorageEvent) => {
      if (evenement.key !== CLE_THEME) return;
      const autre = lireChoix();
      setChoix(autre);
      appliquer(autre);
    };
    window.addEventListener("storage", surStockage);
    return () => window.removeEventListener("storage", surStockage);
  }, []);

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

  const choisir = (valeur: ChoixTheme) => {
    setChoix(valeur);
    ecrireChoix(valeur);
    appliquer(valeur);
    setOuvert(false);
    declencheur.current?.focus();
  };

  return (
    <div ref={conteneur} className="relative">
      <button
        ref={declencheur}
        type="button"
        onClick={() => setOuvert((avant) => !avant)}
        aria-haspopup="menu"
        aria-expanded={ouvert}
        aria-label="Thème de l'interface"
        title="Thème de l'interface"
        className="block rounded-md p-2 text-neutral-500 hover:bg-neutral-100 hover:text-neutral-900 dark:text-neutral-400 dark:hover:bg-neutral-900 dark:hover:text-neutral-100"
      >
        {/* Icône pilotée par le CSS : elle suit le thème **appliqué** sans
            dépendre d'un état React, donc sans écart entre le HTML du serveur
            et le DOM corrigé par le script d'init. */}
        <IconeSoleil className="size-5 dark:hidden" />
        <IconeLune className="hidden size-5 dark:block" />
      </button>

      {ouvert && (
        <div
          role="menu"
          aria-label="Thème de l'interface"
          className="absolute top-full right-0 z-20 mt-2 w-44 rounded-lg border border-neutral-200 bg-white py-1 shadow-lg dark:border-neutral-800 dark:bg-neutral-900"
        >
          {OPTIONS.map(({ valeur, libelle, Icone }) => {
            const actif = valeur === choix;
            return (
              <button
                key={valeur}
                type="button"
                role="menuitemradio"
                aria-checked={actif}
                onClick={() => choisir(valeur)}
                className={
                  "flex w-full items-center gap-2.5 px-3 py-2 text-left text-sm hover:bg-neutral-100 dark:hover:bg-neutral-800 " +
                  (actif
                    ? "font-medium text-neutral-900 dark:text-neutral-100"
                    : "text-neutral-600 dark:text-neutral-400")
                }
              >
                <Icone className="size-4 shrink-0" />
                <span className="flex-1">{libelle}</span>
                {actif && <IconeCoche className="size-4 shrink-0" />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

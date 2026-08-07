"use client";

/**
 * Le shell applicatif de la Control Tower (#117, lot 1 de #116) : le cadre
 * commun à toutes les pages — sidebar de navigation, barre supérieure et zone
 * de contenu. Chaque page ne rend plus que son contenu ; l'en-tête, le retour
 * au tableau de bord et les indicateurs globaux vivent ici, une seule fois.
 *
 * Le repli de la sidebar est tenu ici : la sidebar en dépend pour sa largeur,
 * la barre supérieure porte le bouton qui le bascule.
 */

import { useEffect, useState } from "react";

import { AssistantFlottant } from "@/components/AssistantFlottant";
import { BarreLaterale } from "@/components/BarreLaterale";
import { BarreSuperieure } from "@/components/BarreSuperieure";
import { BasculeTheme } from "@/components/BasculeTheme";
import { CentreNotifications } from "@/components/CentreNotifications";
import { GuidePriseEnMain } from "@/components/GuidePriseEnMain";
import { MenuAide } from "@/components/MenuAide";
import { FournisseurEtatGlobal } from "@/lib/etatGlobal";
import {
  ecouterRepliSidebar,
  ecrireRepliSidebar,
  lireRepliSidebar,
} from "@/lib/preferences";

export function Shell({ children }: { children: React.ReactNode }) {
  const [repliee, setRepliee] = useState(false);

  // Lu après l'hydratation : le rendu serveur ne connaît pas le localStorage,
  // le lire pendant le rendu ferait diverger les deux arbres. Restitution
  // différée d'un tick (même mécanique que useControlTower) : l'effet lui-même
  // ne déclenche aucun setState synchrone. L'abonnement, lui, suit ensuite les
  // changements venus d'ailleurs — section Apparence des Paramètres (#121) ou
  // autre onglet.
  useEffect(() => {
    const tick = setTimeout(() => setRepliee(lireRepliSidebar()), 0);
    const detacher = ecouterRepliSidebar(setRepliee);
    return () => {
      clearTimeout(tick);
      detacher();
    };
  }, []);

  // Le stockage tranche : on écrit, l'abonnement ci-dessus met l'état à jour —
  // ici comme depuis les Paramètres, un seul chemin de bascule.
  const basculerRepli = () => ecrireRepliSidebar(!repliee);

  return (
    <FournisseurEtatGlobal>
      {/* `min-h-0` tout le long (#248) : la hauteur définie posée par le
          `<body>` ne descend jusqu'aux pages que si chaque élément flex
          accepte de rétrécir sous son contenu — le `min-height:auto` par
          défaut le lui interdit, et un seul maillon manquant suffit à rendre
          la chaîne indéfinie. */}
      <div className="flex min-h-0 flex-1">
        <BarreLaterale repliee={repliee} />
        <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-y-auto">
          <BarreSuperieure
            repliee={repliee}
            basculerRepli={basculerRepli}
            notifications={<CentreNotifications />}
            theme={<BasculeTheme />}
            aide={<MenuAide />}
          />
          {/* `@container` : la sidebar prend de la largeur au contenu, donc les
              grilles des pages se calent sur la largeur **réelle** de cette
              zone (`@md:`, `@3xl:`…) et non sur celle de la fenêtre.
              `data-guide` : ancre de repli de la visite guidée (#122) quand la
              page ne rend pas encore le panneau qu'une étape vise.
              `pb-24` : la bande que le bouton de l'assistant (#123) occupe en bas
              à droite est réservée — aucun contenu ne se termine sous lui, donc
              aucune action de la page (décider une validation…) ne finit
              masquée.
              `min-h-0 flex-1` : la zone occupe la hauteur du cadre, ce qui
              donne enfin une hauteur à prendre à une page qui le demande (le
              Kanban, #248). Ce qui déborde fait défiler la colonne parente —
              la barre supérieure y reste collée (`sticky`) comme quand c'était
              la fenêtre qui défilait, et l'ascenseur reste au bord de l'écran
              plutôt qu'au bord de la colonne centrée. */}
          <main
            data-guide="contenu"
            className="@container mx-auto flex min-h-0 w-full max-w-screen-2xl flex-1 flex-col gap-6 p-4 pb-24 sm:p-6 sm:pb-24"
          >
            {children}
          </main>
        </div>
      </div>
      {/* Hors flux (position fixe) : la visite se superpose au shell entier, et
          l'assistant (#123) flotte sur toutes les pages — la visite passant
          par-dessus lui (`z-40`/`z-50` contre `z-30`). */}
      <AssistantFlottant />
      <GuidePriseEnMain />
    </FournisseurEtatGlobal>
  );
}

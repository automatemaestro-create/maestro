"use client";

/**
 * La barre supérieure du backoffice (#117) : titre de la page courante à
 * gauche, indicateurs globaux à droite — statut du flux temps réel et coût
 * cumulé (docs/05 §2.1 — « lisibilité du coût »), qui suivent l'utilisateur de
 * page en page au lieu d'être refaits dans chaque en-tête.
 *
 * Les emplacements de droite sont des `slots` que leur lot de #116 remplit : le
 * thème (#118) et l'aide (#122) sont livrés, les notifications sont encore
 * tenues par un bouton inerte quand la barre est rendue sans elles, pour que sa
 * géométrie ne bouge pas.
 */

import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import {
  IconeDeplier,
  IconeNotifications,
  IconeReplier,
} from "@/components/Icones";
import { useEtatGlobal } from "@/lib/etatGlobal";
import { formatCout } from "@/lib/format";
import { entreeCourante } from "@/lib/navigation";

type Props = {
  /** Sidebar repliée (état tenu par le shell). */
  repliee: boolean;
  basculerRepli: () => void;
  /** Cloche de notifications — lot #119. */
  notifications?: ReactNode;
  /** Bascule clair / sombre / système (#118). */
  theme: ReactNode;
  /** Menu d'aide — visite guidée (#122), assistant à venir (#123). */
  aide: ReactNode;
};

export function BarreSuperieure({
  repliee,
  basculerRepli,
  notifications,
  theme,
  aide,
}: Props) {
  const chemin = usePathname();
  const { connecte, coutTotal, projet } = useEtatGlobal();
  const titre = entreeCourante(chemin)?.libelle ?? "Control Tower";

  return (
    <header className="sticky top-0 z-10 flex h-14 shrink-0 items-center gap-x-3 gap-y-2 border-b border-neutral-200 bg-white/90 px-4 backdrop-blur sm:px-6 dark:border-neutral-800 dark:bg-neutral-950/90">
      <button
        type="button"
        onClick={basculerRepli}
        aria-expanded={!repliee}
        aria-controls="navigation-principale"
        aria-label={repliee ? "Déplier la navigation" : "Replier la navigation"}
        title={repliee ? "Déplier la navigation" : "Replier la navigation"}
        // Le rail est imposé sous `lg` : le bouton n'y aurait rien à basculer.
        className="-ml-1 hidden rounded-md p-1.5 text-neutral-500 hover:bg-neutral-100 hover:text-neutral-900 lg:block dark:text-neutral-400 dark:hover:bg-neutral-900 dark:hover:text-neutral-100"
      >
        {repliee ? (
          <IconeDeplier className="size-5" />
        ) : (
          <IconeReplier className="size-5" />
        )}
      </button>
      <h1
        title={titre}
        className="min-w-0 truncate text-base font-semibold tracking-tight"
      >
        {titre}
      </h1>

      <div className="ml-auto flex items-center gap-2 sm:gap-3">
        <span
          className={
            "inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium " +
            (connecte
              ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300"
              : "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300")
          }
        >
          <span
            className={
              "size-1.5 rounded-full " +
              (connecte ? "bg-emerald-500" : "animate-pulse bg-amber-500")
            }
          />
          <span className="hidden sm:inline">
            {connecte ? "Temps réel connecté" : "Reconnexion…"}
          </span>
        </span>

        {/* Masqué sur les écrans les plus étroits : le titre de page y a la
            priorité, et le coût reste lisible dans la page elle-même.
            Depuis #281 c'est la dépense **du projet actif** — le titre le dit,
            un montant qui suit l'utilisateur de page en page ne pouvant pas
            rester le seul chiffre de l'écran à parler de tous les projets. */}
        <span
          data-guide="cout-cumule"
          className="hidden whitespace-nowrap text-sm text-neutral-600 sm:inline dark:text-neutral-400"
          title={`Coût cumulé sur ${projet.nom} — somme des grands livres, planification comprise`}
        >
          Coût cumulé :{" "}
          <span className="font-medium tabular-nums">{formatCout(coutTotal)}</span>
        </span>

        <div className="flex items-center gap-1 border-l border-neutral-200 pl-2 sm:pl-3 dark:border-neutral-800">
          {notifications ?? (
            <EmplacementReserve
              libelle="Notifications"
              lot={119}
              Icone={IconeNotifications}
            />
          )}
          {theme}
          {aide}
        </div>
      </div>
    </header>
  );
}

/**
 * La place tenue par un lot à venir : visible et désactivée plutôt qu'absente —
 * la barre garde sa géométrie définitive dès maintenant.
 */
function EmplacementReserve({
  libelle,
  lot,
  Icone,
}: {
  libelle: string;
  lot: number;
  Icone: (props: { className?: string }) => ReactNode;
}) {
  return (
    <button
      type="button"
      disabled
      aria-label={`${libelle} — bientôt disponible`}
      title={`${libelle} — bientôt (ticket #${lot})`}
      className="rounded-md p-2 text-neutral-400 opacity-50 dark:text-neutral-600"
    >
      <Icone className="size-5" />
    </button>
  );
}

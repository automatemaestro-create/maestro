"use client";

/**
 * L'en-tête d'une fiche agent (#190, lot 1 de #189) : le retour à la liste, le
 * nom de l'agent et la barre d'onglets — le cadre commun à ses quatre facettes.
 *
 * Rendu par le *layout* de `/agents/[nom]` : il survit au passage d'un onglet à
 * l'autre, donc la barre ne clignote pas et seul le contenu de l'onglet est
 * remonté. L'onglet actif se déduit du chemin (`lib/agents`), sans que la page
 * ait à le redire — un onglet ajouté à `ONGLETS_AGENT` apparaît ici sans
 * toucher à ce composant.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { IconeAgent, IconeFlecheGauche } from "@/components/Icones";
import { EnTeteSection } from "@/components/Primitives";
import { cheminOnglet, ONGLETS_AGENT, ongletDuChemin } from "@/lib/agents";

export function OngletsAgent({
  nom,
  children,
}: {
  nom: string;
  children: ReactNode;
}) {
  const actif = ongletDuChemin(usePathname());

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-4">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <Link
          href="/agents"
          className="inline-flex items-center gap-1 text-annexe font-medium text-neutral-500 hover:text-neutral-900 dark:text-neutral-400 dark:hover:text-neutral-100"
        >
          <IconeFlecheGauche className="size-3.5 shrink-0" />
          Tous les agents
        </Link>
        <EnTeteSection titre={nom} icone={IconeAgent} />
      </div>

      <nav
        aria-label={`Facettes de ${nom}`}
        className="flex flex-wrap gap-1 border-b border-neutral-200 dark:border-neutral-800"
      >
        {ONGLETS_AGENT.map(({ cle, libelle, icone: Icone }) => {
          const courant = cle === actif;
          return (
            <Link
              key={cle}
              href={cheminOnglet(nom, cle)}
              aria-current={courant ? "page" : undefined}
              className={
                "-mb-px inline-flex items-center gap-1.5 rounded-t-md border-b-2 px-3 py-2 text-corps transition-colors " +
                (courant
                  ? "border-emerald-600 font-medium text-neutral-900 dark:border-emerald-500 dark:text-neutral-100"
                  : "border-transparent text-neutral-500 hover:bg-neutral-100 hover:text-neutral-900 dark:text-neutral-400 dark:hover:bg-neutral-900 dark:hover:text-neutral-100")
              }
            >
              <Icone className="size-4 shrink-0" />
              {libelle}
            </Link>
          );
        })}
      </nav>

      {children}
    </div>
  );
}

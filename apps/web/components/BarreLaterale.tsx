"use client";

/**
 * La sidebar du backoffice (#117) : la navigation principale, commune à toutes
 * les pages. Deux largeurs — étendue (icône + libellé) et repliée (icônes
 * seules). Sous `lg`, elle est **toujours** repliée : le rail d'icônes reste
 * utilisable sur petit écran sans manger la largeur du contenu. Au-delà, le
 * bouton de la barre supérieure tranche (l'état vit dans le shell).
 */

import Link from "next/link";
import { usePathname } from "next/navigation";

import { LogoMaestro } from "@/components/Logo";
import { entreeCourante, MENU } from "@/lib/navigation";

export function BarreLaterale({ repliee }: { repliee: boolean }) {
  const chemin = usePathname();
  const courante = entreeCourante(chemin);

  // Sous `lg`, la largeur reste celle du rail quel que soit le choix.
  const largeur = repliee ? "w-16" : "w-16 lg:w-60";
  const libelleVisible = repliee ? "hidden" : "hidden lg:inline";

  return (
    <aside
      className={
        "sticky top-0 flex h-dvh shrink-0 flex-col border-r border-neutral-200 bg-neutral-50 transition-[width] duration-200 dark:border-neutral-800 dark:bg-neutral-950 " +
        largeur
      }
    >
      <Link
        href="/"
        data-guide="marque"
        className="flex h-14 shrink-0 items-center gap-2 px-4 text-neutral-900 dark:text-neutral-100"
        // `aria-label` et non `title` (#536) : repliée, la sidebar masque le
        // libellé et le glyphe est `aria-hidden` — le `title` était alors le
        // **seul** nom accessible du lien, c'est-à-dire un nom que ni le
        // clavier ni le tactile n'atteignaient.
        aria-label="Maestro — Control Tower"
      >
        {/* Repliée, la sidebar masque le libellé : le glyphe seul (variante
            compacte) subsiste. Le mark hérite de `currentColor` — lisible en
            clair comme en sombre. */}
        <LogoMaestro className="size-7 shrink-0" />
        <span
          className={
            "truncate text-sm font-semibold tracking-tight " + libelleVisible
          }
        >
          Maestro
        </span>
      </Link>

      <nav
        id="navigation-principale"
        data-guide="navigation"
        aria-label="Navigation principale"
        className="flex flex-1 flex-col gap-0.5 overflow-y-auto px-2 py-2"
      >
        {MENU.map(({ href, libelle, Icone }) => {
          const actif = courante?.href === href;
          return (
            <Link
              key={href}
              href={href}
              aria-current={actif ? "page" : undefined}
              // Même raison qu'au lien de marque ci-dessus (#536) : repliée, la
              // sidebar n'affiche plus que l'icône, et le nom du lien ne tenait
              // qu'au `title`.
              aria-label={libelle}
              className={
                "flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors " +
                (actif
                  ? "bg-neutral-200 font-medium text-neutral-900 dark:bg-neutral-800 dark:text-neutral-100"
                  : "text-neutral-600 hover:bg-neutral-100 hover:text-neutral-900 dark:text-neutral-400 dark:hover:bg-neutral-900 dark:hover:text-neutral-100")
              }
            >
              <Icone className="size-5 shrink-0" />
              <span className={"truncate " + libelleVisible}>{libelle}</span>
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}

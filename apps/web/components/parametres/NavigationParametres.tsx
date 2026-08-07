"use client";

/**
 * Le sous-menu de la page Paramètres (#121) : les sections de `lib/parametres`
 * en ancres, avec mise en évidence de celle qu'on est en train de lire.
 *
 * Ancres plutôt qu'onglets : toute la page reste imprimable, cherchable
 * (Ctrl+F) et partageable au lien près (`/parametres#agents`). La section
 * courante se déduit du défilement — la dernière dont le haut est passé sous la
 * barre supérieure — plutôt que d'un état cliqué, pour rester juste quand on
 * fait défiler à la molette.
 *
 * Cette règle suppose que **chaque** section peut atteindre le repère, y compris
 * la dernière : c'est le rôle de l'`EspaceDefilement` posé sous elles par la
 * page. Sans lui, cliquer une des dernières entrées défilerait jusqu'en butée
 * sans que la section vise le repère, et le sous-menu désignerait la précédente.
 */

import { useEffect, useState } from "react";

import {
  DECALAGE_ANCRE_PX,
  SECTIONS_PARAMETRES,
  type IdSection,
} from "@/lib/parametres";

export function NavigationParametres() {
  const [courante, setCourante] = useState<IdSection>(
    SECTIONS_PARAMETRES[0].id,
  );

  useEffect(() => {
    const sections = SECTIONS_PARAMETRES.map(({ id }) => ({
      id,
      noeud: document.getElementById(id),
    })).filter(
      (section): section is { id: IdSection; noeud: HTMLElement } =>
        section.noeud !== null,
    );
    if (sections.length === 0) return;

    const surDefilement = () => {
      let lue = sections[0];
      for (const section of sections) {
        if (section.noeud.getBoundingClientRect().top - DECALAGE_ANCRE_PX <= 1) {
          lue = section;
        }
      }
      setCourante(lue.id);
    };

    // Différé d'un tick (même mécanique que le shell) : l'effet lui-même ne
    // déclenche aucun setState synchrone. Rattrape au passage l'ancre d'entrée
    // (`/parametres#agents`), où le navigateur a déjà défilé.
    const tick = setTimeout(surDefilement, 0);
    // En **capture** (#248) : depuis que le cadre du shell a une hauteur
    // définie, ce n'est plus la fenêtre qui défile mais la zone de contenu, et
    // l'événement `scroll` d'un élément ne remonte pas — écouté en phase
    // montante, celui-ci n'aurait plus jamais été reçu et la mise en évidence
    // serait restée figée sur la première section. La capture, elle, voit les
    // deux : la mesure ci-dessus est relative à la fenêtre, elle reste juste
    // quel que soit l'élément qui a défilé.
    window.addEventListener("scroll", surDefilement, {
      capture: true,
      passive: true,
    });
    window.addEventListener("resize", surDefilement);
    return () => {
      clearTimeout(tick);
      window.removeEventListener("scroll", surDefilement, { capture: true });
      window.removeEventListener("resize", surDefilement);
    };
  }, []);

  return (
    <nav
      aria-label="Sections des paramètres"
      className="@3xl:sticky @3xl:top-20 @3xl:w-56 @3xl:shrink-0"
    >
      <ul className="flex flex-row flex-wrap gap-1 @3xl:flex-col">
        {SECTIONS_PARAMETRES.map(({ id, libelle }) => {
          const active = id === courante;
          return (
            <li key={id}>
              <a
                href={`#${id}`}
                aria-current={active ? "true" : undefined}
                className={
                  "block rounded-md px-3 py-2 text-sm transition-colors " +
                  (active
                    ? "bg-neutral-200 font-medium text-neutral-900 dark:bg-neutral-800 dark:text-neutral-100"
                    : "text-neutral-600 hover:bg-neutral-100 hover:text-neutral-900 dark:text-neutral-400 dark:hover:bg-neutral-900 dark:hover:text-neutral-100")
                }
              >
                {libelle}
              </a>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

"use client";

/**
 * Le sélecteur de projet de la barre supérieure (#280, lot 4 de #276) : le
 * projet actif, visible sur **toutes** les pages, et la bascule vers un autre.
 *
 * Il remplace l'entrée « Projets » de la barre latérale, et c'est tout le lot :
 * une entrée de menu range le projet **parmi** les destinations alors qu'il est
 * le cadre de toutes. Sorti de la navigation et posé contre le titre de page, il
 * se lit dans l'autre sens — « ce projet-ci, cette page-là ».
 *
 * Trois partis pris, qui expliquent sa forme :
 *
 * 1. **basculer ne navigue pas** — changer de projet écrit le choix (#279) et
 *    laisse l'URL où elle est. Les écrans se relisent sur le nouveau projet à
 *    l'endroit où l'on était : pas de page intermédiaire, rien à retrouver, et
 *    aucun aller-retour ajouté à l'historique du navigateur ;
 * 2. **la gestion reste l'écran de #225**, atteint d'ici plutôt que par le menu.
 *    Déclarer, modifier et supprimer n'ont pas changé — seul leur point d'entrée
 *    a bougé —, et le chemin est résolu par `entreeParLibelle` plutôt qu'écrit
 *    en dur, pour que l'écran puisse déménager sans que ce menu le sache ;
 * 3. **rien de neuf côté comportement de menu** — ouverture, clic à l'extérieur,
 *    Échap et focus rendu au bouton sont ceux de la bascule de thème (#118) et
 *    du menu d'aide (#122). Trois menus dans la même barre s'ouvrent et se
 *    ferment de la même façon, ou l'un des trois surprend.
 */

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import {
  IconeChevronBas,
  IconeCoche,
  IconeProjets,
} from "@/components/Icones";
import { useProjetActif } from "@/lib/etatProjetActif";
import { entreeParLibelle } from "@/lib/navigation";
import type { Projet } from "@/lib/types";

export function SelecteurProjet() {
  const { projet, projets, choisir } = useProjetActif();
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

  // La garde du shell (#279) ne monte le cadre qu'avec un projet actif : hors de
  // là il n'y a ni nom à afficher ni bascule à proposer. Rendre `null` plutôt
  // qu'un état « aucun projet » évite d'inventer une deuxième porte d'entrée à
  // côté de celle qui existe.
  if (projet === null) return null;

  const gestion = entreeParLibelle("Projets");

  const basculerVers = (autre: Projet) => {
    setOuvert(false);
    declencheur.current?.focus();
    // Réécrire le choix courant serait sans effet visible mais notifierait tous
    // les abonnés du projet actif — et donc, au lot 5, ferait relire chaque
    // écran pour rien.
    if (autre.id !== projet.id) choisir(autre);
  };

  return (
    <div ref={conteneur} className="relative min-w-0">
      <button
        ref={declencheur}
        type="button"
        onClick={() => setOuvert((avant) => !avant)}
        aria-haspopup="menu"
        aria-expanded={ouvert}
        // Le nom accessible dit à la fois **où l'on est** et **ce que le bouton
        // fait** : lu seul, « Dépensio » ne se distinguerait pas d'un titre.
        aria-label={`Projet actif : ${projet.nom} — changer de projet`}
        title={projet.racine}
        className="flex min-w-0 items-center gap-1.5 rounded-md border border-neutral-200 px-2 py-1 text-sm text-neutral-700 hover:bg-neutral-100 hover:text-neutral-900 dark:border-neutral-800 dark:text-neutral-300 dark:hover:bg-neutral-900 dark:hover:text-neutral-100"
      >
        <IconeProjets className="size-4 shrink-0 text-neutral-500 dark:text-neutral-400" />
        <span className="max-w-32 truncate font-medium sm:max-w-48">
          {projet.nom}
        </span>
        <IconeChevronBas className="size-4 shrink-0 text-neutral-400 dark:text-neutral-500" />
      </button>

      {ouvert && (
        <div
          role="menu"
          aria-label="Projet actif"
          className="absolute top-full left-0 z-20 mt-2 w-72 max-w-[calc(100vw-1.5rem)] rounded-lg border border-neutral-200 bg-white py-1 shadow-lg dark:border-neutral-800 dark:bg-neutral-900"
        >
          {/* La liste défile plutôt que de pousser la gestion hors de l'écran :
              c'est elle qui grandit avec le nombre de projets, pas le menu. */}
          <div
            role="group"
            aria-label="Projets déclarés"
            className="max-h-64 overflow-y-auto"
          >
            {projets.map((autre) => {
              const actif = autre.id === projet.id;
              return (
                <button
                  key={autre.id}
                  type="button"
                  role="menuitemradio"
                  aria-checked={actif}
                  onClick={() => basculerVers(autre)}
                  className={
                    "flex w-full items-center gap-2.5 px-3 py-2 text-left hover:bg-neutral-100 dark:hover:bg-neutral-800 " +
                    (actif
                      ? "text-neutral-900 dark:text-neutral-100"
                      : "text-neutral-600 dark:text-neutral-400")
                  }
                >
                  <span className="min-w-0 flex-1">
                    <span
                      className={
                        "block truncate text-sm " + (actif ? "font-medium" : "")
                      }
                    >
                      {autre.nom}
                    </span>
                    {/* La racine, et pas seulement le nom : deux clones d'un
                        même dépôt portent volontiers le même nom, et c'est le
                        chemin qui dit alors sur lequel on va travailler. */}
                    <span className="block truncate font-mono text-xs text-neutral-500 dark:text-neutral-400">
                      {autre.racine}
                    </span>
                  </span>
                  {actif && <IconeCoche className="size-4 shrink-0" />}
                </button>
              );
            })}
          </div>

          {gestion && (
            <>
              <div
                role="separator"
                className="my-1 border-t border-neutral-200 dark:border-neutral-800"
              />
              <Link
                href={gestion.href}
                role="menuitem"
                onClick={() => setOuvert(false)}
                className="flex w-full flex-col items-start gap-0.5 px-3 py-2 text-left hover:bg-neutral-100 dark:hover:bg-neutral-800"
              >
                <span className="text-sm font-medium">Gérer les projets</span>
                <span className="text-xs text-neutral-500 dark:text-neutral-400">
                  Déclarer, modifier ou supprimer une racine
                </span>
              </Link>
            </>
          )}
        </div>
      )}
    </div>
  );
}

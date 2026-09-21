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
 *    ferment de la même façon, ou l'un des trois surprend. Depuis #536 cette
 *    identité n'est plus une intention tenue à la relecture : les quatre
 *    surfaces **appellent le même hook**, `useSurfaceDeroulee`, qui leur a
 *    apporté d'un coup la navigation aux flèches qu'aucune n'avait.
 *
 * ── Ce que #1108 y change : le bouton ne peint plus hors de sa boîte ────────
 *
 * C'est ici qu'était la **cause** du chevauchement de la barre, et elle ne se
 * voyait pas à la lecture : un `<button>` est un contrôle de formulaire, donc
 * sa largeur `auto` est un *shrink-to-fit* sur son contenu — pas un « remplis
 * ton parent », comme pour un `<div>`. Le conteneur ci-dessous rétrécissait
 * donc bien (`min-w-0`), et le bouton, lui, gardait ses 254 px : il les
 * peignait **par-dessus** le titre de page, qui se croyait seul à cet endroit.
 * `max-w-full` referme le piège en une classe — le bouton ne peut plus dépasser
 * la boîte qu'on lui donne, et c'est son propre libellé qui tronque.
 *
 * Le reste applique l'ordre de cession de la variante retenue (voir l'en-tête
 * de `BarreSuperieure`) : le nom du projet est **le deuxième** à céder, après le
 * coût cumulé et avant le titre de page. Il se raccourcit par paliers lus sur la
 * largeur de la **barre** (`@2xl`, `@3xl`), et ne descend jamais sous `min-w-8` :
 * un préfixe reste lisible, comme une cellule tronquée de GitHub Actions. C'est
 * le point qui a fait écarter la variante B, où le nom disparaissait pour ne
 * laisser qu'une icône — un nom accessible ne répare pas un libellé absent.
 */

import Link from "next/link";
import { useCallback, useRef, useState } from "react";

import {
  IconeChevronBas,
  IconeCoche,
  IconeProjets,
} from "@/components/Icones";
import { useProjetActif } from "@/lib/etatProjetActif";
import { entreeParLibelle } from "@/lib/navigation";
import type { Projet } from "@/lib/types";
import { useSurfaceDeroulee } from "@/lib/useSurfaceDeroulee";

export function SelecteurProjet() {
  const { projet, projets, choisir } = useProjetActif();
  const [ouvert, setOuvert] = useState(false);
  const conteneur = useRef<HTMLDivElement>(null);
  const declencheur = useRef<HTMLButtonElement>(null);
  const surface = useRef<HTMLDivElement>(null);

  // Clic à l'extérieur, `Échap`, flèches, `Home`/`End` et focus d'entrée : tout
  // vient du hook partagé (#536), qui a remplacé quatre copies du même bloc.
  const fermer = useCallback(() => setOuvert(false), []);
  useSurfaceDeroulee({ ouvert, fermer, conteneur, declencheur, surface });

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
    // Pas de `min-w-0` (#1108) : sans lui, la taille minimale automatique de cet
    // élément flex est son contenu minimal — icône, préfixe de nom, chevron —,
    // et c'est ce **plancher** qui garde le sélecteur lisible quand la barre se
    // resserre. Avec `min-w-0` il pouvait tomber à zéro, et le nom du projet
    // avec lui : la barre aurait dit sur quelle page on est, plus sur quel
    // projet.
    <div ref={conteneur} className="relative">
      <button
        ref={declencheur}
        type="button"
        onClick={() => setOuvert((avant) => !avant)}
        aria-haspopup="menu"
        aria-expanded={ouvert}
        // Le nom accessible dit à la fois **où l'on est** et **ce que le bouton
        // fait** : lu seul, « Dépensio » ne se distinguerait pas d'un titre. La
        // racine y a rejoint le nom (#536) : elle vivait dans un `title=`,
        // c'est-à-dire nulle part pour qui n'a pas de souris, alors que c'est
        // elle qui départage deux clones d'un même dépôt.
        aria-label={`Projet actif : ${projet.nom} (${projet.racine}) — changer de projet`}
        // `max-w-full` (#1108) : la classe qui referme le chevauchement — un
        // `<button>` se dimensionne sur son contenu, jamais sur son parent.
        className="flex max-w-full min-w-0 items-center gap-1.5 rounded-md border border-neutral-200 px-2 py-1 text-sm text-neutral-700 hover:bg-neutral-100 hover:text-neutral-900 dark:border-neutral-800 dark:text-neutral-300 dark:hover:bg-neutral-900 dark:hover:text-neutral-100"
      >
        <IconeProjets className="size-4 shrink-0 text-neutral-500 dark:text-neutral-400" />
        {/* Quatre paliers, lus sur la largeur de la BARRE (#1108) : 4 rem quand
            elle est au plus serré, puis 6, 8 et 12 à mesure qu'elle respire.
            `min-w-8` plutôt que rien : c'est lui qui interdit au nom de se
            réduire à une lettre — ce que faisait la variante C, écartée pour
            cela.
            ⚠ Le palier de 4 rem a été ajouté **après** le balayage de 960 à
            1440 px, pas au brouillon : sans lui, le nom restait à son plafond
            de 6 rem pendant que le titre de page tombait à « Tabl… » (mesuré à
            1024 px, colonne ouverte, nom de projet long) — c'est-à-dire l'ordre
            de cession à l'envers. Un plafond qui ne baisse jamais est un
            plancher déguisé. */}
        <span className="min-w-8 max-w-16 truncate font-medium @lg:max-w-24 @2xl:max-w-32 @3xl:max-w-48">
          {projet.nom}
        </span>
        <IconeChevronBas className="size-4 shrink-0 text-neutral-400 dark:text-neutral-500" />
      </button>

      {ouvert && (
        <div
          ref={surface}
          role="menu"
          aria-label="Projet actif"
          tabIndex={-1}
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

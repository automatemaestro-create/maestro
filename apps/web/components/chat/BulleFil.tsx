"use client";

/**
 * La bulle du fil, **sans ce qu'elle porte** (#483, lot 2 de #481).
 *
 * Elle vivait en privé dans `FilChat` tant qu'un fil ne portait que des
 * messages. Le cadrage y arrive — le brief, les questions de clarification et
 * leurs réponses se lisent désormais dans la conversation —, et deux formes de
 * bulle auraient donné deux fils : celui des messages et celui du cadrage,
 * distincts à l'œil sur le même écran, alors que c'est la même conversation qui
 * les porte.
 *
 * Deux propriétés à ne pas défaire :
 *
 * - **le côté dit qui parle**, et rien d'autre ne le dit : l'utilisateur à
 *   droite en plein, l'agent à gauche en surface neutre. Le pied redit le nom
 *   parce qu'une bulle relue au lecteur d'écran n'a ni gauche ni droite ;
 * - **la largeur est un choix de l'appelant**, pas une propriété de l'auteur.
 *   Un message tient dans 70 % de la colonne ; un brief de sept sections
 *   éditables, non — et le rétrécir ferait de la correction un travail de
 *   contorsion, c'est-à-dire la friction qui fait approuver sans lire (docs/05
 *   §2.7.4). D'où `pleineLargeur`, demandé au cas par cas.
 *
 * ## Ce que #697 y change : la largeur de lecture, et les jetons du socle
 *
 * **Une borne en `ch` s'ajoute au pourcentage**, et c'est le critère « largeur
 * de lecture bornée ». Depuis #691 le fil occupe l'écran : 70 % d'une colonne
 * large font des lignes de 110 caractères, où l'œil perd son retour à la ligne —
 * la mesure confortable tient entre 60 et 75. Le pourcentage **reste** pour les
 * écrans étroits, où il est la contrainte qui mord ; les deux se composent par
 * `min()`, donc c'est toujours la plus serrée des deux qui décide. La borne ne
 * s'applique pas à `pleineLargeur` : un brief est un formulaire, pas de la
 * prose.
 *
 * **Les couleurs viennent des jetons** (`globals.css`, #533) et non plus de la
 * palette brute. Ce n'était pas cosmétique : la bulle de l'utilisateur portait le
 * `bg-emerald-600` + blanc à **3,65:1** que #535 a retiré des boutons, et son
 * pied `text-neutral-400` à 2,58:1. `bg-accent` / `text-sur-ton` valent 5,36:1
 * en clair et 8,00:1 en sombre, mesurés et gardés par `tests/contraste.test.ts`.
 *
 * ⚠ Le pied de la bulle de l'utilisateur s'écrit donc en `text-sur-ton` **plein**
 * et non dans une teinte affaiblie : sa discrétion vient de sa **taille**
 * (`text-micro`, le pas que le socle réserve à l'horodatage — « lisible, pas
 * lu »), jamais d'un contraste rabaissé. C'est la règle du socle appliquée ici :
 * un `text-sur-ton/70` aurait l'air plus sobre et sortirait du barème sans que
 * rien ne le dise.
 */

import type { ReactNode } from "react";

import { Infobulle } from "@/components/Infobulle";
import { formatDateHeure, formatHeureCourte } from "@/lib/format";

export function BulleFil({
  auteur,
  utilisateur = false,
  horodatage,
  pleineLargeur = false,
  children,
}: {
  /** Le nom affiché au pied — remplacé par « vous » côté utilisateur. */
  auteur: string;
  utilisateur?: boolean;
  /** Horodatage ISO, quand le fil en connaît un (une saisie en cours, non). */
  horodatage?: string;
  /** Le contenu déborde la largeur d'un message : brief, formulaire, rapport. */
  pleineLargeur?: boolean;
  children: ReactNode;
}) {
  return (
    <li className={"flex " + (utilisateur ? "justify-end" : "justify-start")}>
      <div
        className={
          // `shadow-sm` est celle de `Carte` (`Primitives`) et pas une ombre
          // inventée ici : c'est la seule élévation du socle, et une bulle est
          // une surface posée sur la page au même titre qu'une carte.
          "min-w-0 rounded-lg px-3 py-2 text-corps shadow-sm " +
          (pleineLargeur
            ? "w-full "
            : "max-w-[85%] sm:max-w-[min(70%,72ch)] ") +
          (utilisateur
            ? "bg-accent text-sur-ton"
            : "border border-bord bg-surface text-texte")
        }
      >
        {children}
        <p
          className={
            "mt-1 text-right text-micro " +
            (utilisateur ? "text-sur-ton" : "text-texte-secondaire")
          }
        >
          {utilisateur ? "vous" : auteur}
          {horodatage !== undefined && (
            <>
              {" · "}
              <Infobulle texte={formatDateHeure(horodatage)}>
                <time dateTime={horodatage}>
                  {formatHeureCourte(horodatage)}
                </time>
              </Infobulle>
            </>
          )}
        </p>
      </div>
    </li>
  );
}

"use client";

/**
 * Un bloc de code dans le fil, **avec sa copie** (#697, lot 7 de #690).
 *
 * C'est la moitié du critère qui ne se voit pas : un agent qui rend une commande
 * ou un correctif écrit du code pour qu'il soit *joué*, et le sélectionner à la
 * souris dans un fil qui défile pendant qu'on le fait est le geste que ce bouton
 * supprime.
 *
 * Quatre décisions, et chacune se défait mal :
 *
 * - **rien n'est copiable tant que rien n'est complet.** Un bloc encore en train
 *   d'arriver (`ferme: false`, voir `lib/markdown`) n'offre pas la copie :
 *   emporter la moitié d'une commande est pire que devoir attendre deux
 *   secondes, et c'est la seule faute que ce bouton peut commettre ;
 * - **le pied du bloc ne change pas de taille** quand la copie aboutit. Le
 *   libellé bascule de « Copier » à « Copié », le bouton garde sa place, et
 *   c'est le second critère du ticket appliqué à l'endroit le plus petit : un
 *   état qui se lit ne doit pas faire bouger ce qu'on est en train de lire ;
 * - **le code déborde en défilant, jamais en s'élargissant.** `overflow-x-auto`
 *   sur le `<pre>` : sans lui, une ligne longue élargirait la bulle, donc la
 *   colonne, donc la page — c'est la classe de bug que `/banc-mise-en-page`
 *   attrape (#308), et elle se prévient ici ;
 * - **aucune coloration syntaxique.** Elle demanderait un second moteur, plus
 *   lourd que l'analyseur Markdown lui-même, pour un fil où le code se lit plus
 *   qu'il ne s'édite. Le langage annoncé par la clôture est rendu **en toutes
 *   lettres** dans le pied : il dit ce qu'on regarde, ce que des couleurs
 *   disent moins bien à qui ne les distingue pas.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { IconeCoche, IconeCopier } from "@/components/Icones";
import { Bouton } from "@/components/Primitives";

/** Le temps que « Copié » reste à l'écran avant de rendre la main au libellé. */
const TEMOIN_MS = 2000;

export function BlocDeCode({
  texte,
  langage,
  ferme,
}: {
  texte: string;
  /** Ce que la clôture annonçait (« bash », « python »), ou la chaîne vide. */
  langage: string;
  /** Le bloc est-il complet ? Un bloc en cours d'arrivée ne se copie pas. */
  ferme: boolean;
}) {
  const [copie, setCopie] = useState(false);
  const minuterie = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Le témoin est une minuterie, donc quelque chose qui survit au démontage si
  // on ne le retire pas : un bloc copié puis chassé du fil poserait son état sur
  // un composant qui n'est plus là.
  useEffect(
    () => () => {
      if (minuterie.current !== null) clearTimeout(minuterie.current);
    },
    [],
  );

  const copier = useCallback(async () => {
    // `navigator.clipboard` n'existe pas partout : hors contexte sécurisé, et
    // pas davantage sous jsdom. On ne prétend pas avoir copié dans ce cas —
    // afficher « Copié » sans rien avoir copié est le seul mensonge que ce
    // bouton puisse dire.
    const presse = navigator.clipboard;
    if (presse === undefined) return;
    try {
      await presse.writeText(texte);
    } catch {
      return;
    }
    setCopie(true);
    if (minuterie.current !== null) clearTimeout(minuterie.current);
    minuterie.current = setTimeout(() => setCopie(false), TEMOIN_MS);
  }, [texte]);

  return (
    <div className="my-2 overflow-hidden rounded-md border border-bord bg-surface-creuse">
      <pre className="overflow-x-auto px-3 py-2">
        <code className="font-mono text-annexe text-texte">{texte}</code>
      </pre>
      {/* Le pied ne se pose que s'il a quelque chose à dire : sur un bloc encore
          en cours d'arrivée sans langage annoncé, une barre vide ajouterait du
          cadre à un fil qu'on épure. */}
      {(langage !== "" || ferme) && (
        <p className="flex items-center justify-between gap-2 border-t border-bord px-2 py-1">
          <span className="text-micro text-texte-secondaire">{langage}</span>
          {ferme && (
            <Bouton
              variante="discret"
              ton="neutre"
              taille="petite"
              icone={copie ? IconeCoche : IconeCopier}
              onClick={() => void copier()}
              aria-label={
                copie ? "Bloc de code copié" : "Copier le bloc de code"
              }
            >
              {copie ? "Copié" : "Copier"}
            </Bouton>
          )}
        </p>
      )}
    </div>
  );
}

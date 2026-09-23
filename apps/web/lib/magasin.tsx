"use client";

/**
 * Le magasin d'événements de l'API, tel que le shell le surveille (#1206).
 *
 * Quand l'API perd son magasin (Redis), elle le dit sur `/api/sante` et refuse
 * ses lectures en `503` nommé. Mais un écran **déjà chargé** ne relit rien de
 * lui-même, et le flux temps réel reste ouvert — l'API tourne : sans ce module,
 * l'écran figé se tait, et c'est précisément le « ok » menteur du ticket, dans
 * l'état « en cours de route » que #1165 nommait non couvert.
 *
 * Le shell sonde donc la santé **une fois pour toute l'application**
 * (`FournisseurMagasin`, monté par `Shell`) et rend le bandeau système sous la
 * barre supérieure (`BandeauMagasin`, variante C retenue par le regard neuf,
 * d'après Carbon — « directly below the main header » — et Primer, *Degraded
 * experiences*).
 *
 * **Un seul message à la fois** (Carbon, « only one banner at a time ») : quand
 * le shell dit la panne, le bandeau d'écran de la **même** panne s'efface
 * (`useMagasinSignale`, lu par `BanniereErreurApi`). À la porte d'entrée, qui
 * n'a pas de shell, c'est le bandeau d'écran qui la dit, seul.
 */

import { createContext, useContext, useEffect, useRef, useState } from "react";

import { chargerSante } from "./api";
import type { EtatMagasin } from "./types";

/**
 * Entre deux sondes, en millisecondes. L'API garde son verdict une seconde et sa
 * pompe réessaie jusqu'à toutes les dix : cinq secondes disent la panne vite
 * sans interroger l'API plus souvent qu'elle ne change d'avis.
 */
export const PAS_SONDE_MAGASIN_MS = 5_000;

/** Le magasin **perdu**, tel que la santé le rend — `null` tant qu'il répond. */
const ContexteMagasin = createContext<EtatMagasin | null>(null);

/**
 * La panne du magasin que le shell signale, ou `null`.
 *
 * `null` aussi hors du shell (porte d'entrée, tests d'un composant seul) : rien
 * n'y est signalé, donc c'est au bandeau d'écran de dire la panne.
 */
export function useMagasinSignale(): EtatMagasin | null {
  return useContext(ContexteMagasin);
}

/**
 * Sonde `/api/sante` à intervalle régulier et rend le magasin perdu, ou `null`.
 *
 * Une API **injoignable** rend `null` ici : ce n'est pas la panne du magasin,
 * et elle a déjà ses signes — la pastille « Reconnexion… » de la barre et
 * l'« API injoignable » des écrans (#996). La dire deux fois, sous deux noms,
 * serait la confusion que #996 a défaite.
 */
function useSondeMagasin(): EtatMagasin | null {
  const [perdu, setPerdu] = useState<EtatMagasin | null>(null);
  useEffect(() => {
    let vivant = true;
    const sonder = () => {
      chargerSante()
        .then((sante) => {
          if (!vivant) return;
          const magasin = sante.magasin;
          setPerdu(
            magasin !== undefined && !magasin.disponible ? magasin : null,
          );
        })
        .catch(() => {
          if (vivant) setPerdu(null);
        });
    };
    sonder();
    const minuterie = setInterval(sonder, PAS_SONDE_MAGASIN_MS);
    return () => {
      vivant = false;
      clearInterval(minuterie);
    };
  }, []);
  return perdu;
}

export function FournisseurMagasin({
  auRetour,
  children,
}: {
  /**
   * Appelé quand un magasin **perdu** répond de nouveau (#1217). Le shell y
   * branche la relecture de son état : l'API a répondu tout du long, le flux
   * ne s'est pas coupé, et rien d'autre ne ferait relire un écran resté sur la
   * panne de sa dernière lecture — il la dirait encore, magasin revenu.
   */
  auRetour?: () => void;
  children: React.ReactNode;
}) {
  const perdu = useSondeMagasin();
  // Le verdict précédent, pour ne réagir qu'à la **transition** perdu → rendu,
  // jamais à chaque sonde d'un magasin sain.
  const precedent = useRef<EtatMagasin | null>(null);
  useEffect(() => {
    if (precedent.current !== null && perdu === null) auRetour?.();
    precedent.current = perdu;
  }, [perdu, auRetour]);
  return (
    <ContexteMagasin.Provider value={perdu}>
      {children}
    </ContexteMagasin.Provider>
  );
}

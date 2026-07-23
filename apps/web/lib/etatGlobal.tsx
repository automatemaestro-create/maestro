"use client";

/**
 * L'état temps réel partagé par tout le shell (#117).
 *
 * `useControlTower` ouvre un WebSocket et recharge l'état à chaque événement :
 * l'appeler dans plusieurs composants d'une même page doublerait connexions et
 * requêtes. Le shell l'appelle **une fois**, au-dessus des pages, et le diffuse
 * par contexte — la barre supérieure y lit le statut de connexion et le coût
 * cumulé sur toutes les pages, le tableau de bord y lit tout le reste.
 */

import { createContext, useContext } from "react";

import { useControlTower, type ControlTower } from "./useControlTower";

export type EtatGlobal = ControlTower & {
  /** Somme des coûts rapportés par les agents (null si aucun ne l'a fait). */
  coutTotal: number | null;
};

const ContexteEtatGlobal = createContext<EtatGlobal | null>(null);

export function FournisseurEtatGlobal({
  children,
}: {
  children: React.ReactNode;
}) {
  const etat = useControlTower();

  const couts = etat.agents
    .map((a) => a.cout_usd)
    .filter((c): c is number => c !== null);
  // Aucun coût rapporté ≠ coût nul : on laisse « — » plutôt que 0 $.
  const coutTotal =
    couts.length > 0 ? couts.reduce((somme, c) => somme + c, 0) : null;

  return (
    <ContexteEtatGlobal value={{ ...etat, coutTotal }}>
      {children}
    </ContexteEtatGlobal>
  );
}

/** L'état temps réel du shell. Erreur explicite hors du fournisseur. */
export function useEtatGlobal(): EtatGlobal {
  const etat = useContext(ContexteEtatGlobal);
  if (etat === null) {
    throw new Error(
      "useEtatGlobal doit être appelé sous <FournisseurEtatGlobal> (components/Shell).",
    );
  }
  return etat;
}

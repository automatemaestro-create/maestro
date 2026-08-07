"use client";

/**
 * L'état temps réel partagé par tout le shell (#117) — **celui du projet actif**
 * depuis #281.
 *
 * `useControlTower` ouvre un WebSocket et recharge l'état à chaque événement :
 * l'appeler dans plusieurs composants d'une même page doublerait connexions et
 * requêtes. Le shell l'appelle **une fois**, au-dessus des pages, et le diffuse
 * par contexte — la barre supérieure y lit le statut de connexion et le coût
 * cumulé sur toutes les pages, le tableau de bord y lit tout le reste.
 *
 * Le **projet** voyage dans ce même contexte, et pas seulement son identifiant.
 * Deux raisons : les écrans lisent ainsi leur périmètre là où ils lisent leurs
 * données (une seule source, un seul fournisseur à monter en test), et un écran
 * **vide** doit pouvoir **nommer** le projet qui l'est — « rien encore sur
 * Dépensio » ne se confond ni avec une panne ni avec l'absence de projet
 * (critère #281). C'est le shell qui le donne : lui seul a passé la garde de
 * #279, donc lui seul sait qu'il y en a un.
 */

import { createContext, useContext } from "react";

import type { PorteeProjet } from "./api";
import type { CoutExecution, Projet } from "./types";
import { useControlTower, type ControlTower } from "./useControlTower";

export type EtatGlobal = ControlTower & {
  /** Le projet dans lequel la Control Tower est ouverte — le cadre de tout ceci. */
  projet: Projet;
  /** La portée passée aux lectures : l'identifiant du projet actif (#277). */
  portee: PorteeProjet;
  /** Dépense cumulée **du projet actif** (null si rien n'a été rapporté). */
  coutTotal: number | null;
};

/**
 * La dépense cumulée que portent des grands livres (#57) : somme des totaux,
 * **planification comprise**.
 *
 * Exportée parce que deux endroits l'affichent — la barre supérieure sur toutes
 * les pages et la tuile « Dépense » du tableau de bord —, et que deux formules
 * finiraient par diverger. C'est la source qui a changé avec #281 : le coût
 * cumulé se lisait sur `agents[].cout_usd`, un total **de tous les projets**
 * puisque le parc d'agents est celui du poste (`lib/api`). Les grands livres,
 * eux, sont dérivés des tâches du projet : le chiffre est cadré par
 * construction, et les deux affichages s'accordent enfin — là où l'écart de la
 * part d'orchestration demandait d'être expliqué pour ne pas passer pour un bug.
 *
 * Aucun coût rapporté ≠ coût nul : `null` plutôt que 0, `formatCout` rend « — ».
 */
export function coutCumule(couts: CoutExecution[]): number | null {
  const montants = couts
    .map((c) => c.total.cout_usd)
    .filter((c): c is number => c !== null);
  return montants.length > 0 ? montants.reduce((somme, c) => somme + c, 0) : null;
}

const ContexteEtatGlobal = createContext<EtatGlobal | null>(null);

export function FournisseurEtatGlobal({
  projet,
  children,
}: {
  projet: Projet;
  children: React.ReactNode;
}) {
  const etat = useControlTower(projet.id);

  return (
    <ContexteEtatGlobal
      value={{
        ...etat,
        projet,
        portee: projet.id,
        coutTotal: coutCumule(etat.couts),
      }}
    >
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

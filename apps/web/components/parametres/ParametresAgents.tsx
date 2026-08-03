"use client";

/**
 * Section « Agents & capacité » des Paramètres (#121) : le réglage que l'API
 * porte déjà (`POST /api/agents/{nom}/capacite`, #86 / EF-21) — un agent
 * désactivé ne reçoit plus de tâches, son plafond d'instances borne ses
 * exécutions simultanées.
 *
 * Le tableau de bord portait les mêmes contrôles sur une fiche par agent ; son
 * épuration (#191) les laisse ici, seuls et réunis en tableau, tous agents en
 * regard — la vue « configuration », le tableau de bord ne gardant que le
 * compte des agents actifs et occupés. `reglerCapacite` vient du contexte
 * global : le réglage est persisté côté backend et pris en compte dès la tâche
 * suivante.
 */

import { useState } from "react";

import { useEtatGlobal } from "@/lib/etatGlobal";
import type { EtatAgent } from "@/lib/types";

import { EtatVide, Interrupteur } from "./SectionParametres";

export function ParametresAgents() {
  const { agents, chargement, reglerCapacite } = useEtatGlobal();

  if (chargement) {
    return <p className="text-sm text-neutral-500">Chargement des agents…</p>;
  }
  if (agents.length === 0) {
    return (
      <EtatVide
        message="Aucun agent connu pour l'instant."
        releve="Le catalogue est servi par l'API : vérifiez la connexion dans la section Général."
        lien={{ href: "/agents", libelle: "Ouvrir la liste des agents" }}
      />
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-neutral-200 text-left text-xs text-neutral-500 dark:border-neutral-800 dark:text-neutral-400">
            <th className="py-1 pr-3 font-medium">Agent</th>
            <th className="py-1 pr-3 font-medium">Rôle</th>
            <th className="py-1 pr-3 text-center font-medium">Instances</th>
            <th className="py-1 text-right font-medium">Actif</th>
          </tr>
        </thead>
        <tbody>
          {agents.map((agent) => (
            <LigneAgent
              key={agent.nom}
              agent={agent}
              reglerCapacite={reglerCapacite}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function LigneAgent({
  agent,
  reglerCapacite,
}: {
  agent: EtatAgent;
  reglerCapacite: (
    nom: string,
    reglage: { actif?: boolean; instances?: number },
  ) => Promise<void>;
}) {
  const [enCours, setEnCours] = useState(false);
  const [erreur, setErreur] = useState<string | null>(null);

  const surReglage = async (reglage: { actif?: boolean; instances?: number }) => {
    setEnCours(true);
    setErreur(null);
    try {
      await reglerCapacite(agent.nom, reglage);
    } catch (e) {
      setErreur(e instanceof Error ? e.message : String(e));
    } finally {
      setEnCours(false);
    }
  };

  return (
    <tr className="border-b border-neutral-100 last:border-b-0 dark:border-neutral-800/60">
      <td className="py-2 pr-3 align-top font-medium">
        {agent.nom}
        {erreur && (
          <p className="text-xs font-normal text-rose-600 dark:text-rose-400">
            {erreur}
          </p>
        )}
      </td>
      <td className="py-2 pr-3 align-top text-neutral-500 dark:text-neutral-400">
        {agent.role || "—"}
      </td>
      <td className="py-2 pr-3 align-top">
        <div className="flex items-center justify-center gap-1.5">
          <button
            type="button"
            aria-label={`Retirer une instance à ${agent.nom}`}
            className="size-6 rounded border border-neutral-300 leading-none text-neutral-600 hover:bg-neutral-100 disabled:opacity-40 dark:border-neutral-700 dark:text-neutral-300 dark:hover:bg-neutral-800"
            disabled={enCours || agent.instances <= 1}
            onClick={() => void surReglage({ instances: agent.instances - 1 })}
          >
            −
          </button>
          <span className="min-w-5 text-center font-medium tabular-nums">
            {agent.instances}
          </span>
          <button
            type="button"
            aria-label={`Ajouter une instance à ${agent.nom}`}
            className="size-6 rounded border border-neutral-300 leading-none text-neutral-600 hover:bg-neutral-100 disabled:opacity-40 dark:border-neutral-700 dark:text-neutral-300 dark:hover:bg-neutral-800"
            disabled={enCours}
            onClick={() => void surReglage({ instances: agent.instances + 1 })}
          >
            +
          </button>
        </div>
      </td>
      <td className="py-2 align-top text-right">
        <Interrupteur
          libelle={`Agent ${agent.nom} actif`}
          actif={agent.actif}
          desactive={enCours}
          basculer={() => void surReglage({ actif: !agent.actif })}
        />
      </td>
    </tr>
  );
}

"use client";

/**
 * Le panneau Agents du tableau de bord (docs/05 §2.1) : une fiche par agent —
 * libre/occupé/désactivé, tâche courante, compteurs, coût cumulé — mise à jour
 * en temps réel via le hook `useControlTower`, et le **contrôle de capacité**
 * (#86, EF-21) : activer/désactiver l'agent et ajuster son nombre d'instances
 * (`POST /api/agents/{nom}/capacite`), à effet réel sur la file et les workers.
 */

import { useState } from "react";

import { formatCout, formatHeure } from "@/lib/format";
import { AGENT_OCCUPE, type EtatAgent } from "@/lib/types";

type ReglerCapacite = (
  nom: string,
  reglage: { actif?: boolean; instances?: number },
) => Promise<void>;

export function PanneauAgents({
  agents,
  reglerCapacite,
}: {
  agents: EtatAgent[];
  reglerCapacite: ReglerCapacite;
}) {
  return (
    <section aria-label="Agents">
      <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-neutral-500 dark:text-neutral-400">
        Agents
      </h2>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        {agents.map((agent) => (
          <FicheAgent
            key={agent.nom}
            agent={agent}
            reglerCapacite={reglerCapacite}
          />
        ))}
        {agents.length === 0 && (
          <p className="text-sm text-neutral-500">Aucun agent connu.</p>
        )}
      </div>
    </section>
  );
}

function FicheAgent({
  agent,
  reglerCapacite,
}: {
  agent: EtatAgent;
  reglerCapacite: ReglerCapacite;
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

  const occupe = agent.statut === AGENT_OCCUPE;
  const pastille = !agent.actif
    ? {
        libelle: "Désactivé",
        badge: "bg-neutral-200 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-400",
        point: "bg-neutral-400",
      }
    : occupe
      ? {
          libelle: "Occupé",
          badge: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
          point: "bg-amber-500",
        }
      : {
          libelle: "Libre",
          badge:
            "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
          point: "bg-emerald-500",
        };

  return (
    <article
      className={
        "rounded-lg border border-neutral-200 bg-white p-3 text-sm shadow-sm dark:border-neutral-800 dark:bg-neutral-900" +
        (!agent.actif ? " opacity-75" : "")
      }
    >
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium">{agent.nom}</span>
        <span
          className={
            "inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium " +
            pastille.badge
          }
        >
          <span className={"size-1.5 rounded-full " + pastille.point} />
          {pastille.libelle}
        </span>
      </div>
      <p className="mt-0.5 text-xs text-neutral-500 dark:text-neutral-400">
        {agent.role || "—"}
      </p>
      <dl className="mt-2 space-y-1 text-xs text-neutral-600 dark:text-neutral-400">
        {occupe && agent.taches_en_cours.length > 1 ? (
          <div className="flex justify-between gap-2">
            <dt>Tâches en cours</dt>
            <dd
              className="truncate font-medium"
              title={agent.taches_en_cours.join(", ")}
            >
              {agent.taches_en_cours.length} en parallèle
            </dd>
          </div>
        ) : (
          occupe &&
          agent.tache_courante && (
            <div className="flex justify-between gap-2">
              <dt>Tâche courante</dt>
              <dd className="truncate font-medium" title={agent.tache_courante}>
                {agent.tache_courante}
              </dd>
            </div>
          )
        )}
        <div className="flex justify-between gap-2">
          <dt>Terminées / échouées</dt>
          <dd>
            <span className="text-emerald-600 dark:text-emerald-400">
              {agent.taches_terminees}
            </span>
            {" / "}
            <span className="text-rose-600 dark:text-rose-400">
              {agent.taches_echouees}
            </span>
          </dd>
        </div>
        <div className="flex justify-between gap-2">
          <dt>Coût cumulé</dt>
          <dd>{formatCout(agent.cout_usd)}</dd>
        </div>
        {agent.derniere_activite && (
          <div className="flex justify-between gap-2">
            <dt>Dernière activité</dt>
            <dd>{formatHeure(agent.derniere_activite)}</dd>
          </div>
        )}
      </dl>
      <div className="mt-2 flex items-center justify-between gap-2 border-t border-neutral-100 pt-2 text-xs dark:border-neutral-800">
        <div className="flex items-center gap-1.5 text-neutral-600 dark:text-neutral-400">
          <span>Instances</span>
          <button
            type="button"
            aria-label={`Retirer une instance à ${agent.nom}`}
            className="size-5 rounded border border-neutral-300 leading-none text-neutral-600 hover:bg-neutral-100 disabled:opacity-40 dark:border-neutral-700 dark:text-neutral-300 dark:hover:bg-neutral-800"
            disabled={enCours || agent.instances <= 1}
            onClick={() => void surReglage({ instances: agent.instances - 1 })}
          >
            −
          </button>
          <span className="min-w-4 text-center font-medium text-neutral-800 dark:text-neutral-200">
            {agent.instances}
          </span>
          <button
            type="button"
            aria-label={`Ajouter une instance à ${agent.nom}`}
            className="size-5 rounded border border-neutral-300 leading-none text-neutral-600 hover:bg-neutral-100 disabled:opacity-40 dark:border-neutral-700 dark:text-neutral-300 dark:hover:bg-neutral-800"
            disabled={enCours}
            onClick={() => void surReglage({ instances: agent.instances + 1 })}
          >
            +
          </button>
        </div>
        <button
          type="button"
          className={
            "rounded border px-2 py-0.5 font-medium disabled:opacity-40 " +
            (agent.actif
              ? "border-rose-200 text-rose-700 hover:bg-rose-50 dark:border-rose-900 dark:text-rose-400 dark:hover:bg-rose-950"
              : "border-emerald-200 text-emerald-700 hover:bg-emerald-50 dark:border-emerald-900 dark:text-emerald-400 dark:hover:bg-emerald-950")
          }
          disabled={enCours}
          onClick={() => void surReglage({ actif: !agent.actif })}
        >
          {agent.actif ? "Désactiver" : "Réactiver"}
        </button>
      </div>
      {erreur && (
        <p className="mt-1 text-xs text-rose-600 dark:text-rose-400">{erreur}</p>
      )}
    </article>
  );
}

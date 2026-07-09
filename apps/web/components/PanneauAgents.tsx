/**
 * Le panneau Agents du tableau de bord (docs/05 §2.1) : une fiche par agent —
 * libre/occupé, tâche courante, compteurs et coût cumulé — mise à jour en
 * temps réel via le hook `useControlTower`.
 */

import { formatCout, formatHeure } from "@/lib/format";
import { AGENT_OCCUPE, type EtatAgent } from "@/lib/types";

export function PanneauAgents({ agents }: { agents: EtatAgent[] }) {
  return (
    <section aria-label="Agents">
      <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-neutral-500 dark:text-neutral-400">
        Agents
      </h2>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        {agents.map((agent) => (
          <FicheAgent key={agent.nom} agent={agent} />
        ))}
        {agents.length === 0 && (
          <p className="text-sm text-neutral-500">Aucun agent connu.</p>
        )}
      </div>
    </section>
  );
}

function FicheAgent({ agent }: { agent: EtatAgent }) {
  const occupe = agent.statut === AGENT_OCCUPE;
  return (
    <article className="rounded-lg border border-neutral-200 bg-white p-3 text-sm shadow-sm dark:border-neutral-800 dark:bg-neutral-900">
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium">{agent.nom}</span>
        <span
          className={
            "inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium " +
            (occupe
              ? "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300"
              : "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300")
          }
        >
          <span
            className={
              "size-1.5 rounded-full " + (occupe ? "bg-amber-500" : "bg-emerald-500")
            }
          />
          {occupe ? "Occupé" : "Libre"}
        </span>
      </div>
      <p className="mt-0.5 text-xs text-neutral-500 dark:text-neutral-400">
        {agent.role || "—"}
      </p>
      <dl className="mt-2 space-y-1 text-xs text-neutral-600 dark:text-neutral-400">
        {occupe && agent.tache_courante && (
          <div className="flex justify-between gap-2">
            <dt>Tâche courante</dt>
            <dd className="truncate font-medium" title={agent.tache_courante}>
              {agent.tache_courante}
            </dd>
          </div>
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
    </article>
  );
}

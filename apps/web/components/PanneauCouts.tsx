/**
 * Le grand livre de chaque exécution (docs/05 §2.5, ticket #58), servi par
 * `GET /api/executions/{run_id}/cout` (#57) — la part de planification
 * (l'orchestrateur), le coût par tâche (tokens, coût estimé, durée) et
 * l'agrégat du run (critère MVP n°6).
 *
 * Rangé sur la page Coûts & analytics par #191 : c'est le **détail ligne à
 * ligne** que les agrégats de cette page (par tâche, par agent, par exécution)
 * résument. Le tableau de bord n'en garde que la dépense totale, en tuile, avec
 * le renvoi ici.
 */

import { IconeGrandLivre, IconeMonnaie } from "@/components/Icones";
import { LienTicketExterne } from "@/components/LienTicketExterne";
import { Carte, EnTeteSection } from "@/components/Primitives";
import { formatCout, formatDuree, formatTokens } from "@/lib/format";
import type { CoutExecution, Usage } from "@/lib/types";

export function PanneauCouts({ couts }: { couts: CoutExecution[] }) {
  if (couts.length === 0) return null;

  return (
    <section aria-label="Grand livre par exécution">
      <EnTeteSection
        titre="Grand livre par exécution"
        icone={IconeGrandLivre}
        className="mb-2"
      />
      <div className="space-y-3">
        {couts.map((cout) => (
          <GrandLivre key={cout.run_id} cout={cout} />
        ))}
      </div>
    </section>
  );
}

function GrandLivre({ cout }: { cout: CoutExecution }) {
  // La ligne de planification n'apparaît que si l'orchestrateur a rapporté
  // quelque chose — une exécution sans télémétrie de planification reste lisible.
  const planificationRapportee =
    cout.planification.appels > 0 || cout.planification.cout_usd !== null;

  return (
    <Carte className="text-corps">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h3 className="flex items-center gap-1.5 font-medium">
          <IconeGrandLivre className="size-4 shrink-0 text-neutral-400 dark:text-neutral-500" />
          Exécution{" "}
          <span className="font-mono text-annexe text-neutral-600 dark:text-neutral-400">
            {cout.run_id}
          </span>
        </h3>
        <p className="chiffre flex items-center gap-1.5 text-annexe text-neutral-600 dark:text-neutral-400">
          <IconeMonnaie className="size-3.5 shrink-0" />
          <span className="font-medium">{formatCout(cout.total.cout_usd)}</span>
          {" · "}
          {formatTokens(cout.total.tokens_total)} tokens
          {" · "}
          {formatDuree(cout.total.duree_ms)}
        </p>
      </div>
      <div className="mt-2 overflow-x-auto">
        <table className="w-full text-annexe">
          <thead>
            <tr className="border-b border-neutral-200 text-left text-neutral-500 dark:border-neutral-800 dark:text-neutral-400">
              <th className="py-1 pr-3 font-medium">Tâche</th>
              <th className="py-1 pr-3 font-medium">Agent</th>
              <th className="py-1 pr-3 text-right font-medium">Tokens</th>
              <th className="py-1 pr-3 text-right font-medium">Coût</th>
              <th className="py-1 text-right font-medium">Durée</th>
            </tr>
          </thead>
          <tbody>
            {planificationRapportee && (
              <tr className="border-b border-neutral-100 dark:border-neutral-800/60">
                <td className="py-1 pr-3 italic">Planification</td>
                <td className="py-1 pr-3 text-neutral-500 dark:text-neutral-400">
                  orchestrateur
                </td>
                <CellulesUsage usage={cout.planification} />
              </tr>
            )}
            {cout.taches.map((tache) => (
              <tr
                key={tache.tache_id}
                className="border-b border-neutral-100 dark:border-neutral-800/60"
              >
                <td className="max-w-64 py-1 pr-3">
                  <span className="block truncate" title={tache.tache_id}>
                    {tache.nom || tache.tache_id}
                  </span>
                  <LienTicketExterne
                    reference={tache.ticket}
                    tache={tache.nom || tache.tache_id}
                  />
                </td>
                <td className="py-1 pr-3 text-neutral-500 dark:text-neutral-400">
                  {tache.agent
                    ? `${tache.agent}${tache.role ? ` · ${tache.role}` : ""}`
                    : "—"}
                </td>
                <CellulesUsage usage={tache.usage} />
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr className="font-medium">
              <td className="py-1 pr-3">Total</td>
              <td className="py-1 pr-3" />
              <CellulesUsage usage={cout.total} />
            </tr>
          </tfoot>
        </table>
      </div>
    </Carte>
  );
}

/** Les trois cellules chiffrées d'une ligne du grand livre : tokens, coût, durée. */
function CellulesUsage({ usage }: { usage: Usage }) {
  return (
    <>
      <td
        className="chiffre py-1 pr-3 text-right"
        title={`${formatTokens(usage.tokens_entree)} tokens en entrée / ${formatTokens(usage.tokens_sortie)} en sortie`}
      >
        {formatTokens(usage.tokens_total)}
      </td>
      <td className="chiffre py-1 pr-3 text-right">{formatCout(usage.cout_usd)}</td>
      <td className="chiffre py-1 text-right">{formatDuree(usage.duree_ms)}</td>
    </>
  );
}

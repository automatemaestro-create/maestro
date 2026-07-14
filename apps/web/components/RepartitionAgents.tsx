/**
 * La répartition du coût par agent (ticket #87) : une barre horizontale par
 * acteur (orchestrateur compris — sa planification est une dépense comme une
 * autre), triée par l'API du plus dépensier au moindre. Catégories nominales,
 * série unique : toutes les barres portent la même teinte, la longueur (et
 * l'étiquette directe coût + part) fait le travail. Quand aucun coût n'a été
 * rapporté, la part retombe sur les tokens — le libellé l'annonce.
 */

import { formatCout, formatDuree, formatTokens } from "@/lib/format";
import type { CoutAgentAgrege } from "@/lib/types";

export function RepartitionAgents({ agents }: { agents: CoutAgentAgrege[] }) {
  if (agents.length === 0) {
    return (
      <p className="text-sm text-neutral-500 dark:text-neutral-400">
        Aucun usage attribué à un agent sur la période.
      </p>
    );
  }

  const coutTotal = agents.reduce((s, a) => s + (a.usage.cout_usd ?? 0), 0);
  const tokensTotal = agents.reduce((s, a) => s + a.usage.tokens_total, 0);
  // Part au coût quand il est rapporté, aux tokens sinon (coût inconnu ≠ nul).
  const surTokens = coutTotal <= 0;
  const reference = surTokens ? tokensTotal : coutTotal;

  return (
    <div className="space-y-2">
      {surTokens && reference > 0 && (
        <p className="text-xs text-neutral-500 dark:text-neutral-400">
          Aucun coût rapporté sur la période : répartition en tokens.
        </p>
      )}
      {agents.map((agent) => {
        const valeur = surTokens
          ? agent.usage.tokens_total
          : (agent.usage.cout_usd ?? 0);
        const part = reference > 0 ? valeur / reference : 0;
        return (
          <div
            key={agent.agent}
            title={`${formatTokens(agent.usage.tokens_total)} tokens · ${agent.usage.appels} appel(s) · ${formatDuree(agent.usage.duree_ms)}`}
          >
            <div className="flex items-baseline justify-between gap-x-4 text-sm">
              <p className="truncate">
                <span className="font-medium">{agent.agent}</span>
                {agent.role && (
                  <span className="text-neutral-500 dark:text-neutral-400">
                    {" "}
                    · {agent.role}
                  </span>
                )}
              </p>
              <p className="shrink-0 text-xs tabular-nums text-neutral-600 dark:text-neutral-400">
                {surTokens
                  ? `${formatTokens(agent.usage.tokens_total)} tokens`
                  : formatCout(agent.usage.cout_usd)}
                {reference > 0 && (
                  <span className="text-neutral-400 dark:text-neutral-500">
                    {" "}
                    · {Math.round(part * 100)}&nbsp;%
                  </span>
                )}
              </p>
            </div>
            <div className="mt-1 h-2.5 overflow-hidden rounded-full bg-neutral-100 dark:bg-neutral-800">
              <div
                className="h-full rounded-full bg-[#2a78d6] dark:bg-[#3987e5]"
                style={{ width: `${Math.max(part * 100, valeur > 0 ? 1 : 0)}%` }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}

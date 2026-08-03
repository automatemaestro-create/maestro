"use client";

/**
 * La page Coûts & analytics de la Control Tower (ticket #87) : piloter la
 * dépense au quotidien (critère de sortie V1 « coûts maîtrisés »). Branchée
 * sur `GET /api/analytics/couts` : coût total de la période, évolution dans
 * le temps, répartition par agent, détail par tâche et par exécution — le
 * tout sur une période sélectionnable et rafraîchi en temps réel par le
 * WebSocket. Pendant un rechargement, la vue précédente reste affichée,
 * estompée (pas de squelette, pas de saut de mise en page).
 *
 * Depuis #191, la page héberge aussi le **grand livre par exécution** (#58)
 * que le tableau de bord empilait : le détail ligne à ligne, sous les agrégats
 * qui le résument. Il vient du contexte partagé du shell et non de
 * `useAnalyticsCouts` — il n'est donc pas borné par le filtre de période, d'où
 * sa place à part, hors de la zone estompée pendant un rafraîchissement.
 */

import { BanniereErreurApi } from "@/components/BanniereErreurApi";
import { GraphiqueEvolutionCout } from "@/components/GraphiqueEvolutionCout";
import { LienTicketExterne } from "@/components/LienTicketExterne";
import { PanneauCouts } from "@/components/PanneauCouts";
import { RepartitionAgents } from "@/components/RepartitionAgents";
import { useEtatGlobal } from "@/lib/etatGlobal";
import {
  formatCout,
  formatDateHeure,
  formatDuree,
  formatTokens,
  libelleStatut,
} from "@/lib/format";
import type {
  AnalyticsCouts,
  CoutExecutionResume,
  CoutTacheAgregee,
  PasSerie,
} from "@/lib/types";
import { PERIODES, useAnalyticsCouts, type Periode } from "@/lib/useAnalyticsCouts";
import { useState } from "react";

export default function PageCouts() {
  const [periode, setPeriode] = useState<Periode>(
    PERIODES.find((p) => p.id === "tout") ?? PERIODES[0],
  );
  // Le statut du flux temps réel est porté par la barre supérieure du shell
  // (#117) : la page n'a plus qu'à consommer les agrégats.
  const { vue, chargement, rafraichissement, erreur } =
    useAnalyticsCouts(periode);
  // Les grands livres, eux, sont déjà chargés par le contexte partagé (#117) :
  // les relire ici ne coûte ni requête ni connexion supplémentaire.
  const { couts } = useEtatGlobal();

  return (
    <>
      <BanniereErreurApi erreur={erreur} />
      {/* La rangée de filtres : une seule, au-dessus de tout ce qu'elle borne —
          chaque vue en dessous (compteurs, graphiques, tables) suit la même
          fenêtre, les chiffres concordent toujours. */}
      <nav aria-label="Période" className="flex flex-wrap items-center gap-2">
        <span className="text-sm text-neutral-500 dark:text-neutral-400">
          Période :
        </span>
        {PERIODES.map((p) => (
          <button
            key={p.id}
            type="button"
            onClick={() => setPeriode(p)}
            aria-pressed={p.id === periode.id}
            className={
              "rounded-full border px-3 py-1 text-sm " +
              (p.id === periode.id
                ? "border-neutral-400 bg-neutral-100 font-medium dark:border-neutral-600 dark:bg-neutral-800"
                : "border-neutral-200 bg-white text-neutral-600 hover:bg-neutral-50 dark:border-neutral-800 dark:bg-neutral-900 dark:text-neutral-400 dark:hover:bg-neutral-800")
            }
          >
            {p.libelle}
          </button>
        ))}
      </nav>
      {chargement || vue === null ? (
        <p className="text-sm text-neutral-500">Chargement des agrégats…</p>
      ) : (
        <div
          className={
            "flex flex-col gap-6 transition-opacity " +
            (rafraichissement ? "opacity-60" : "opacity-100")
          }
        >
          <Compteurs vue={vue} />
          <div className="grid gap-6 lg:grid-cols-5">
            <section
              aria-label="Évolution du coût"
              className="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm lg:col-span-3 dark:border-neutral-800 dark:bg-neutral-900"
            >
              <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-neutral-500 dark:text-neutral-400">
                Évolution du coût
              </h2>
              <GraphiqueEvolutionCout serie={vue.serie} pas={vue.pas as PasSerie} />
            </section>
            <section
              aria-label="Répartition par agent"
              className="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm lg:col-span-2 dark:border-neutral-800 dark:bg-neutral-900"
            >
              <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-neutral-500 dark:text-neutral-400">
                Répartition par agent
              </h2>
              <RepartitionAgents agents={vue.agents} />
            </section>
          </div>
          <TableTaches taches={vue.taches} />
          <TableExecutions executions={vue.executions} />
        </div>
      )}
      <PanneauCouts couts={couts} />
    </>
  );
}

/** La rangée de compteurs : les totaux de la fenêtre, d'un coup d'œil. */
function Compteurs({ vue }: { vue: AnalyticsCouts }) {
  return (
    <section
      data-guide="couts"
      aria-label="Totaux de la période"
      className="grid grid-cols-2 gap-3 sm:grid-cols-4"
    >
      <Compteur libelle="Coût total" valeur={formatCout(vue.total.cout_usd)} />
      <Compteur
        libelle="Tokens"
        valeur={formatTokens(vue.total.tokens_total)}
        detail={`${formatTokens(vue.total.tokens_entree)} entrée · ${formatTokens(vue.total.tokens_sortie)} sortie`}
      />
      <Compteur
        libelle="Appels modèle"
        valeur={formatTokens(vue.total.appels)}
      />
      <Compteur
        libelle="Exécutions"
        valeur={formatTokens(vue.executions.length)}
        detail={`${formatTokens(vue.taches.length)} tâche(s) comptabilisée(s)`}
      />
    </section>
  );
}

function Compteur({
  libelle,
  valeur,
  detail,
}: {
  libelle: string;
  valeur: string;
  detail?: string;
}) {
  return (
    <article className="rounded-lg border border-neutral-200 bg-white p-3 shadow-sm dark:border-neutral-800 dark:bg-neutral-900">
      <p className="text-xs text-neutral-500 dark:text-neutral-400">{libelle}</p>
      <p className="mt-1 text-2xl font-semibold">{valeur}</p>
      {detail && (
        <p className="mt-0.5 text-xs text-neutral-500 dark:text-neutral-400">
          {detail}
        </p>
      )}
    </article>
  );
}

/** Le détail par tâche : cumul toutes exécutions confondues, tri par coût (API). */
function TableTaches({ taches }: { taches: CoutTacheAgregee[] }) {
  if (taches.length === 0) return null;
  return (
    <section
      aria-label="Coûts par tâche"
      className="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm dark:border-neutral-800 dark:bg-neutral-900"
    >
      <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-neutral-500 dark:text-neutral-400">
        Par tâche
      </h2>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-neutral-200 text-left text-neutral-500 dark:border-neutral-800 dark:text-neutral-400">
              <th className="py-1 pr-3 font-medium">Tâche</th>
              <th className="py-1 pr-3 font-medium">Agent</th>
              <th className="py-1 pr-3 font-medium">Statut</th>
              <th className="py-1 pr-3 text-right font-medium">Exécutions</th>
              <th className="py-1 pr-3 text-right font-medium">Tokens</th>
              <th className="py-1 pr-3 text-right font-medium">Coût</th>
              <th className="py-1 text-right font-medium">Durée</th>
            </tr>
          </thead>
          <tbody>
            {taches.map((tache) => (
              <tr
                key={tache.tache_id}
                className="border-b border-neutral-100 dark:border-neutral-800/60"
              >
                <td className="max-w-64 py-1 pr-3">
                  <span className="block truncate" title={tache.tache_id}>
                    {tache.nom || tache.tache_id}
                  </span>
                  {/* Le ticket externe (#192) sous le nom : la colonne garde sa
                      largeur, et la ligne d'une tâche sans référence ne bouge pas. */}
                  <LienTicketExterne
                    reference={tache.reference_externe}
                    tache={tache.nom || tache.tache_id}
                  />
                </td>
                <td className="py-1 pr-3 text-neutral-500 dark:text-neutral-400">
                  {tache.agent
                    ? `${tache.agent}${tache.role ? ` · ${tache.role}` : ""}`
                    : "—"}
                </td>
                <td className="py-1 pr-3">{libelleStatut(tache.statut) || "—"}</td>
                <td className="py-1 pr-3 text-right tabular-nums">
                  {tache.executions}
                </td>
                <td className="py-1 pr-3 text-right tabular-nums">
                  {formatTokens(tache.usage.tokens_total)}
                </td>
                <td className="py-1 pr-3 text-right tabular-nums">
                  {formatCout(tache.usage.cout_usd)}
                </td>
                <td className="py-1 text-right tabular-nums">
                  {formatDuree(tache.usage.duree_ms)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

/** Le détail par exécution : bornes, tâches et usage cumulé de chaque run. */
function TableExecutions({ executions }: { executions: CoutExecutionResume[] }) {
  if (executions.length === 0) return null;
  return (
    <section
      aria-label="Coûts par exécution"
      className="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm dark:border-neutral-800 dark:bg-neutral-900"
    >
      <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-neutral-500 dark:text-neutral-400">
        Par exécution
      </h2>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-neutral-200 text-left text-neutral-500 dark:border-neutral-800 dark:text-neutral-400">
              <th className="py-1 pr-3 font-medium">Exécution</th>
              <th className="py-1 pr-3 text-right font-medium">Tâches</th>
              <th className="py-1 pr-3 font-medium">Début</th>
              <th className="py-1 pr-3 font-medium">Fin</th>
              <th className="py-1 pr-3 text-right font-medium">Tokens</th>
              <th className="py-1 text-right font-medium">Coût</th>
            </tr>
          </thead>
          <tbody>
            {executions.map((execution) => (
              <tr
                key={execution.run_id}
                className="border-b border-neutral-100 dark:border-neutral-800/60"
              >
                <td className="py-1 pr-3 font-mono">{execution.run_id}</td>
                <td className="py-1 pr-3 text-right tabular-nums">
                  {execution.nb_taches}
                </td>
                <td className="py-1 pr-3 text-neutral-500 dark:text-neutral-400">
                  {formatDateHeure(execution.debut) || "—"}
                </td>
                <td className="py-1 pr-3 text-neutral-500 dark:text-neutral-400">
                  {formatDateHeure(execution.fin) || "—"}
                </td>
                <td className="py-1 pr-3 text-right tabular-nums">
                  {formatTokens(execution.usage.tokens_total)}
                </td>
                <td className="py-1 text-right tabular-nums">
                  {formatCout(execution.usage.cout_usd)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

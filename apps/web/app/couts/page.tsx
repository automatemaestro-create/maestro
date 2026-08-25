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
 *
 * Les deux sources sont cadrées sur le **projet actif** (#281) : la vue agrégée
 * par sa portée (`?projet=`, #277), les grands livres parce qu'ils sont dérivés
 * des tâches du projet. Elles ne peuvent donc pas se contredire — un total de
 * période plus petit que la somme des grands livres se lirait comme un bug, là
 * où ce ne serait qu'un mélange de périmètres.
 */

import { BanniereErreurApi } from "@/components/BanniereErreurApi";
import { GraphiqueEvolutionCout } from "@/components/GraphiqueEvolutionCout";
import { LienTicketExterne } from "@/components/LienTicketExterne";
import { PanneauCouts } from "@/components/PanneauCouts";
import { Carte } from "@/components/Primitives";
import { RegionLive } from "@/components/RegionLive";
import { RepartitionAgents } from "@/components/RepartitionAgents";
import { mesureDeLaDepense } from "@/lib/annonces";
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
  // Les grands livres sont déjà chargés par le contexte partagé (#117) : les
  // relire ici ne coûte ni requête ni connexion supplémentaire. C'est aussi de
  // là que vient la portée projet (#281), pour que les deux sources de cette
  // page portent le même périmètre sans qu'on ait à le rappeler deux fois.
  const {
    couts,
    coutTotal,
    portee,
    projet,
    // Le chargement **du shell**, à ne pas confondre avec celui des agrégats
    // ci-dessous : c'est lui qui porte la dépense cumulée, donc lui qui dit
    // quand la région live peut prendre son premier relevé.
    chargement: chargementProjet,
  } = useEtatGlobal();
  // Le statut du flux temps réel est porté par la barre supérieure du shell
  // (#117) : la page n'a plus qu'à consommer les agrégats.
  const { vue, chargement, rafraichissement, erreur } = useAnalyticsCouts(
    periode,
    portee,
  );

  return (
    <>
      <BanniereErreurApi erreur={erreur} />
      {/* La région live de l'écran (#538). Elle ne parle **que** de la dépense,
          et pas à chaque rafraîchissement : cette page se relit à tout événement
          du bus (`useAnalyticsCouts` ne filtre rien), donc annoncer le
          rafraîchissement serait annoncer le flux. Ce qui vaut d'être dit est le
          franchissement d'un dollar — un seuil, pas un rechargement.
          Le total vient du contexte du shell et non de `vue.total` : celui-ci
          suit la période choisie, et changer de période n'est pas une dépense. */}
      {!chargementProjet && (
        <RegionLive
          libelle="Dépense du projet"
          mesures={[mesureDeLaDepense(coutTotal)]}
        />
      )}
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
            "flex flex-col gap-6 transition-opacity motion-reduce:transition-none " +
            (rafraichissement ? "opacity-60" : "opacity-100")
          }
        >
          <Compteurs vue={vue} />
          {vue.executions.length === 0 && vue.taches.length === 0 && (
            // Les compteurs restent : « 0 $ sur la période » est une réponse.
            // Ce qu'ils ne disent pas, c'est de quel périmètre ce zéro est le
            // zéro — et sans les tables, qui s'effacent quand elles sont vides,
            // l'écran se lirait comme une page à moitié chargée (#281).
            <p className="text-sm text-neutral-500 dark:text-neutral-400">
              Rien encore sur {projet.nom} : aucune exécution de ce projet
              n&apos;a de dépense à comptabiliser sur cette période.
            </p>
          )}
          <div className="grid gap-6 lg:grid-cols-5">
            <Carte
              balise="section"
              densite="aeree"
              aria-label="Évolution du coût"
              className="lg:col-span-3"
            >
              <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-neutral-500 dark:text-neutral-400">
                Évolution du coût
              </h2>
              <GraphiqueEvolutionCout serie={vue.serie} pas={vue.pas as PasSerie} />
            </Carte>
            <Carte
              balise="section"
              densite="aeree"
              aria-label="Répartition par agent"
              className="lg:col-span-2"
            >
              <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-neutral-500 dark:text-neutral-400">
                Répartition par agent
              </h2>
              <RepartitionAgents agents={vue.agents} />
            </Carte>
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
    <Carte>
      <p className="text-xs text-neutral-500 dark:text-neutral-400">{libelle}</p>
      <p className="mt-1 text-2xl font-semibold">{valeur}</p>
      {detail && (
        <p className="mt-0.5 text-xs text-neutral-500 dark:text-neutral-400">
          {detail}
        </p>
      )}
    </Carte>
  );
}

/** Le détail par tâche : cumul toutes exécutions confondues, tri par coût (API). */
function TableTaches({ taches }: { taches: CoutTacheAgregee[] }) {
  if (taches.length === 0) return null;
  return (
    <Carte balise="section" densite="aeree" aria-label="Coûts par tâche">
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
                    reference={tache.ticket}
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
    </Carte>
  );
}

/** Le détail par exécution : bornes, tâches et usage cumulé de chaque run. */
function TableExecutions({ executions }: { executions: CoutExecutionResume[] }) {
  if (executions.length === 0) return null;
  return (
    <Carte balise="section" densite="aeree" aria-label="Coûts par exécution">
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
    </Carte>
  );
}

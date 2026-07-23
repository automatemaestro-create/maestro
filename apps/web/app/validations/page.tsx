"use client";

/**
 * La page Validations (#117, lot 1 de #116) : les demandes de validation
 * humaine (#48) sorties du tableau de bord — celles en attente pour trancher,
 * et l'historique de celles déjà tranchées. Page provisoire : la refonte du
 * centre de notifications (#119) rebrassera la façon dont ces demandes
 * remontent à l'utilisateur ; l'API et les décisions, elles, sont définitives.
 */

import { BanniereErreurApi } from "@/components/BanniereErreurApi";
import { PanneauValidations } from "@/components/PanneauValidations";
import { useEtatGlobal } from "@/lib/etatGlobal";
import { formatDateHeure } from "@/lib/format";
import {
  VALIDATION_APPROUVEE,
  VALIDATION_EN_ATTENTE,
  type Validation,
} from "@/lib/types";

export default function PageValidations() {
  const { validations, chargement, erreur, decider } = useEtatGlobal();

  const enAttente = validations.filter(
    (v) => v.statut === VALIDATION_EN_ATTENTE,
  );
  const tranchees = validations.filter(
    (v) => v.statut !== VALIDATION_EN_ATTENTE,
  );

  return (
    <>
      <BanniereErreurApi erreur={erreur} />
      {chargement ? (
        <p className="text-sm text-neutral-500">Chargement des demandes…</p>
      ) : (
        <>
          {enAttente.length === 0 ? (
            <p className="rounded-lg border border-neutral-200 bg-white p-4 text-sm text-neutral-500 shadow-sm dark:border-neutral-800 dark:bg-neutral-900 dark:text-neutral-400">
              Aucune validation en attente — les moteurs tournent sans demander
              d&apos;arbitrage.
            </p>
          ) : (
            <PanneauValidations validations={validations} decider={decider} />
          )}
          <HistoriqueValidations validations={tranchees} />
        </>
      )}
    </>
  );
}

/** Ce qui a déjà été tranché : la trace des arbitrages, la plus récente en tête. */
function HistoriqueValidations({ validations }: { validations: Validation[] }) {
  if (validations.length === 0) return null;
  return (
    <section
      aria-label="Validations tranchées"
      className="rounded-lg border border-neutral-200 bg-white p-4 shadow-sm dark:border-neutral-800 dark:bg-neutral-900"
    >
      <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-neutral-500 dark:text-neutral-400">
        Déjà tranchées
      </h2>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-neutral-200 text-left text-neutral-500 dark:border-neutral-800 dark:text-neutral-400">
              <th className="py-1 pr-3 font-medium">Tâche</th>
              <th className="py-1 pr-3 font-medium">Agent</th>
              <th className="py-1 pr-3 font-medium">Issue</th>
              <th className="py-1 font-medium">Quand</th>
            </tr>
          </thead>
          <tbody>
            {validations.map((validation) => (
              <tr
                key={validation.tache_id}
                className="border-b border-neutral-100 dark:border-neutral-800/60"
              >
                <td
                  className="max-w-64 truncate py-1 pr-3"
                  title={validation.tache_id}
                >
                  {validation.titre || validation.tache_id}
                </td>
                <td className="py-1 pr-3 text-neutral-500 dark:text-neutral-400">
                  {validation.agent}
                  {validation.role ? ` · ${validation.role}` : ""}
                </td>
                <td className="py-1 pr-3">
                  {validation.statut === VALIDATION_APPROUVEE ? (
                    <span className="text-emerald-700 dark:text-emerald-400">
                      Approuvée
                    </span>
                  ) : (
                    <span className="text-rose-700 dark:text-rose-400">
                      Refusée
                    </span>
                  )}
                </td>
                <td className="py-1 text-neutral-500 dark:text-neutral-400">
                  {formatDateHeure(validation.horodatage) || "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

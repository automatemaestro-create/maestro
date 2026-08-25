"use client";

/**
 * La page Validations (#117, lot 1 de #116) : les demandes de validation
 * humaine (#48) sorties du tableau de bord — celles en attente pour trancher,
 * et l'historique de celles déjà tranchées. Page provisoire : la refonte du
 * centre de notifications (#119) rebrassera la façon dont ces demandes
 * remontent à l'utilisateur ; l'API et les décisions, elles, sont définitives.
 *
 * Les demandes viennent du contexte du shell, cadrées sur le projet actif
 * (`?projet=`, #277/#281) : ce qui vaut pour la file en attente vaut pour
 * l'historique, sans quoi on trancherait ici les arbitrages d'un autre projet.
 * L'écran vide le dit **en nommant le projet** — « aucune validation en
 * attente » tout court se lit comme un état de l'orchestration entière.
 */

import { BanniereErreurApi } from "@/components/BanniereErreurApi";
import { PanneauValidations } from "@/components/PanneauValidations";
import { Carte } from "@/components/Primitives";
import { RegionLive } from "@/components/RegionLive";
import { mesureDesValidationsTranchees } from "@/lib/annonces";
import { useEtatGlobal } from "@/lib/etatGlobal";
import { formatDateHeure } from "@/lib/format";
import {
  VALIDATION_APPROUVEE,
  VALIDATION_EN_ATTENTE,
  type Validation,
} from "@/lib/types";

export default function PageValidations() {
  const { projet, validations, chargement, erreur, decider } = useEtatGlobal();

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
          {/* La région live de l'écran (#538) : elle annonce les décisions
              **prises** — depuis un autre onglet, ou par quelqu'un d'autre. Les
              demandes qui arrivent, elles, coupent la parole depuis la région
              assertive du shell : les dire ici aussi les dirait deux fois sur le
              seul écran où elles sont déjà sous les yeux. */}
          <RegionLive
            libelle="Arbitrages tranchés"
            mesures={[mesureDesValidationsTranchees(validations)]}
          />
          {enAttente.length === 0 ? (
            <Carte
              balise="p"
              densite="aeree"
              className="text-sm text-neutral-500 dark:text-neutral-400"
            >
              {validations.length === 0
                ? `Rien encore sur ${projet.nom} : aucune demande d'arbitrage n'y a été faite.`
                : `Aucune validation en attente sur ${projet.nom} — les moteurs y tournent sans demander d'arbitrage.`}
            </Carte>
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
    <Carte balise="section" densite="aeree" aria-label="Validations tranchées">
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
    </Carte>
  );
}

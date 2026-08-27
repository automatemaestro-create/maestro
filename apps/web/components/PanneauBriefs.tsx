"use client";

/**
 * Les briefs en attente, signalés **là où on regarde** (#322, critère 2) : sur le
 * tableau de bord, au-dessus du reste, sur le patron du panneau des validations
 * humaines (#48).
 *
 * Un run arrêté sur son brief n'a créé **aucune tâche** : il n'apparaît ni au
 * Kanban, ni dans les grands livres, ni dans le fil d'activité si l'événement qui
 * l'a suspendu est déjà sorti des cinquante derniers. Sans ce panneau, un run
 * suspendu est un run qu'on ne voit pas — donc un run perdu, et une dépense de
 * cadrage jetée avec lui.
 *
 * **Il signale et il achemine, il ne décide pas** — c'est ce qui le distingue de
 * `PanneauValidations`, dont les cartes portent Approuver / Refuser. Une demande
 * de validation tient dans sa carte (une action, son motif, son diff) ; un brief,
 * non : sept sections, des questions et un coût. Proposer d'approuver ici
 * inviterait à trancher sans lire, c'est-à-dire à défaire le point de contrôle
 * qu'on vient de poser.
 *
 * ⚠ **Il achemine vers le fil depuis #483**, où le brief se décide. Le
 * changement n'est pas cosmétique et c'est le critère 3 du lot : la destination
 * est résolue par le **menu** (`entreeParLibelle`), donc le jour où #484 retire
 * l'entrée « Valider le brief », ce panneau serait rendu `null` et un run bloqué
 * deviendrait invisible — exactement ce que « un run bloqué ne doit pas dépendre
 * d'un écran qu'on n'a pas ouvert » interdit. Le renvoi suit le geste, et il le
 * suit **avant** que l'écran parte, pas après.
 *
 * Ce jour est le 2026-08-28 (#484) : l'entrée est partie, ce panneau n'a pas
 * bougé d'une ligne, et c'est exactement ce que la précaution ci-dessus
 * achetait.
 */

import Link from "next/link";

import { IconeBrief, IconeFlecheDroite } from "@/components/Icones";
import { BadgeEtat, Carte, EnTeteSection } from "@/components/Primitives";
import { PAGE_DU_CADRAGE, runsEnAttente } from "@/lib/brief";
import { formatHeureRelative } from "@/lib/format";
import { useHorloge } from "@/lib/horloge";
import { entreeParLibelle } from "@/lib/navigation";
import {
  EXECUTION_EN_ATTENTE_REPONSES,
  type ResumeExecution,
} from "@/lib/types";

export function PanneauBriefs({
  executions,
}: {
  executions: ResumeExecution[];
}) {
  const maintenant = useHorloge();
  const runs = runsEnAttente(executions);
  const page = entreeParLibelle(PAGE_DU_CADRAGE);
  if (runs.length === 0 || page === undefined) return null;

  return (
    <Carte balise="section" ton="attention" aria-label="Briefs en attente">
      <EnTeteSection
        titre={
          <>
            Briefs en attente
            <BadgeEtat ton="attention" className="chiffre">
              {runs.length}
            </BadgeEtat>
          </>
        }
        icone={IconeBrief}
        ton="attention"
        className="mb-2"
      />
      <ul className="space-y-2">
        {runs.map((run) => {
          const reponses = run.statut === EXECUTION_EN_ATTENTE_REPONSES;
          return (
            <li key={run.run_id}>
              <Link
                href={page.href}
                className="flex items-center gap-3 rounded-md border border-amber-200 bg-amber-50/60 px-3 py-2 hover:bg-amber-50 dark:border-amber-900 dark:bg-amber-950/30 dark:hover:bg-amber-950/50"
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-corps font-medium">
                    {run.objectif || run.run_id}
                  </span>
                  <span className="mt-0.5 block text-annexe text-neutral-600 dark:text-neutral-300">
                    {reponses
                      ? "Le Chef de projet attend vos réponses"
                      : "Le brief attend votre décision"}
                    {run.attente_depuis
                      ? ` · ${formatHeureRelative(run.attente_depuis, maintenant)}`
                      : ""}
                  </span>
                </span>
                <span className="flex shrink-0 items-center gap-1 text-annexe font-medium text-amber-800 dark:text-amber-300">
                  {reponses ? "Répondre" : "Relire"}
                  <IconeFlecheDroite className="size-3.5" />
                </span>
              </Link>
            </li>
          );
        })}
      </ul>
    </Carte>
  );
}

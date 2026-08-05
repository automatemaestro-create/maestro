"use client";

/**
 * Le panneau des validations humaines (docs/05 §2.6, ticket #48) : chaque
 * demande en attente est mise en avant — action requise — avec le contexte
 * pour trancher (agent, tâche, action demandée, justification) et les boutons
 * **Approuver** / **Refuser** qui appellent
 * `POST /api/validations/{tache_id}/decision`. Le moteur, en pause sur la
 * demande, reprend la tâche ou l'annule proprement selon la décision.
 *
 * Depuis #227 une demande peut porter un **diff** (EF-37) : « appliquer ce
 * travail dans mon projet ? » est une action sensible de plus sur le même canal,
 * et c'est le diff — fichiers touchés, lignes ajoutées/supprimées, branche à
 * fusionner — qui remplace alors la description libre. Sans lui la question
 * serait une signature à l'aveugle ; le refus, lui, n'écrit rien et laisse le
 * travail consultable.
 */

import { useState } from "react";

import { formatHeure } from "@/lib/format";
import {
  NATURE_AJOUT,
  NATURE_MODIFICATION,
  NATURE_SUPPRESSION,
  VALIDATION_EN_ATTENTE,
  type DiffProjet,
  type Validation,
} from "@/lib/types";

type Decider = (tacheId: string, approuve: boolean) => Promise<void>;

export function PanneauValidations({
  validations,
  decider,
}: {
  validations: Validation[];
  decider: Decider;
}) {
  const enAttente = validations.filter((v) => v.statut === VALIDATION_EN_ATTENTE);
  if (enAttente.length === 0) return null;

  return (
    <section
      data-guide="validations"
      aria-label="Validations en attente"
      className="rounded-lg border border-amber-300 bg-amber-50 p-3 dark:border-amber-800 dark:bg-amber-950/40"
    >
      <h2 className="mb-2 flex items-center gap-2 text-sm font-semibold uppercase tracking-wide text-amber-800 dark:text-amber-300">
        ⚠️ Validations en attente
        <span className="rounded-full bg-amber-200 px-2 text-xs text-amber-900 dark:bg-amber-900 dark:text-amber-200">
          {enAttente.length}
        </span>
      </h2>
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        {enAttente.map((validation) => (
          <CarteValidation
            key={validation.tache_id}
            validation={validation}
            decider={decider}
          />
        ))}
      </div>
    </section>
  );
}

function CarteValidation({
  validation,
  decider,
}: {
  validation: Validation;
  decider: Decider;
}) {
  const [enCours, setEnCours] = useState(false);
  const [erreur, setErreur] = useState<string | null>(null);

  const surDecision = async (approuve: boolean) => {
    setEnCours(true);
    setErreur(null);
    try {
      await decider(validation.tache_id, approuve);
    } catch (e) {
      setErreur(e instanceof Error ? e.message : String(e));
    } finally {
      setEnCours(false);
    }
  };

  return (
    <article className="rounded-md border border-amber-200 bg-white p-3 text-sm shadow-sm dark:border-amber-900 dark:bg-neutral-900">
      <p className="font-medium" title={validation.tache_id}>
        {validation.titre || validation.tache_id}
      </p>
      <p className="mt-1 text-xs text-neutral-500 dark:text-neutral-400">
        🤖 {validation.agent}
        {validation.role ? ` · ${validation.role}` : ""}
        {validation.horodatage ? ` · ${formatHeure(validation.horodatage)}` : ""}
      </p>
      {validation.diff ? (
        <DiffApplication diff={validation.diff} />
      ) : (
        validation.description && (
          <p className="mt-2 whitespace-pre-wrap text-xs text-neutral-600 dark:text-neutral-300">
            {validation.description}
          </p>
        )
      )}
      {validation.raison && (
        <p className="mt-2 text-xs italic text-amber-700 dark:text-amber-400">
          Motif : {validation.raison}
        </p>
      )}
      <div className="mt-3 flex gap-2">
        <button
          type="button"
          disabled={enCours}
          onClick={() => void surDecision(true)}
          className="rounded-md bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
        >
          {enCours ? "Envoi…" : "Approuver"}
        </button>
        <button
          type="button"
          disabled={enCours}
          onClick={() => void surDecision(false)}
          className="rounded-md border border-rose-300 px-3 py-1.5 text-xs font-medium text-rose-700 hover:bg-rose-50 disabled:opacity-50 dark:border-rose-800 dark:text-rose-400 dark:hover:bg-rose-950"
        >
          Refuser
        </button>
      </div>
      {erreur && (
        <p className="mt-2 text-xs text-rose-600 dark:text-rose-400">{erreur}</p>
      )}
    </article>
  );
}

/**
 * Le diff d'une demande d'application dans le projet (#227, EF-37) : l'en-tête
 * dit ce qui se produirait à l'accord — fusion d'une branche pour un projet
 * versionné, écriture des fichiers sinon — puis chaque fichier avec ses lignes.
 *
 * Le tableau **défile** au-delà d'une quinzaine de fichiers plutôt que de
 * pousser les boutons Approuver/Refuser hors de l'écran : une demande de
 * validation dont on ne voit plus la réponse n'en est plus une.
 */
function DiffApplication({ diff }: { diff: DiffProjet }) {
  return (
    <div className="mt-2 rounded-md border border-neutral-200 bg-neutral-50 text-xs dark:border-neutral-700 dark:bg-neutral-950/60">
      <p className="border-b border-neutral-200 px-2 py-1.5 text-neutral-700 dark:border-neutral-700 dark:text-neutral-300">
        <span className="font-medium">
          {diff.fichiers} fichier{diff.fichiers > 1 ? "s" : ""}
        </span>{" "}
        <span className="text-emerald-600 dark:text-emerald-400">+{diff.ajouts}</span>{" "}
        <span className="text-rose-600 dark:text-rose-400">−{diff.suppressions}</span>
        <br />
        <span className="text-neutral-500 dark:text-neutral-400">
          {diff.branche
            ? `Fusion de ${diff.branche} vers ${diff.base}`
            : "Écriture des fichiers dans le projet (non versionné)"}
        </span>
      </p>
      <ul className="max-h-48 overflow-y-auto px-2 py-1.5 font-mono">
        {diff.modifications.map((modification) => (
          <li key={modification.chemin} className="flex items-baseline gap-2 py-0.5">
            <span
              aria-hidden
              className={`w-3 shrink-0 text-center ${couleurNature(modification.nature)}`}
            >
              {SIGNE_NATURE[modification.nature] ?? "~"}
            </span>
            <span className="min-w-0 flex-1 break-all text-neutral-700 dark:text-neutral-300">
              {modification.chemin}
            </span>
            <span className="shrink-0 text-neutral-500 dark:text-neutral-400">
              {modification.binaire
                ? "binaire"
                : `+${modification.ajouts} −${modification.suppressions}`}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

const SIGNE_NATURE: Record<string, string> = {
  [NATURE_AJOUT]: "+",
  [NATURE_MODIFICATION]: "~",
  [NATURE_SUPPRESSION]: "−",
};

function couleurNature(nature: string): string {
  if (nature === NATURE_AJOUT) return "text-emerald-600 dark:text-emerald-400";
  if (nature === NATURE_SUPPRESSION) return "text-rose-600 dark:text-rose-400";
  return "text-amber-600 dark:text-amber-400";
}

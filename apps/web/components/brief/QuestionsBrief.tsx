"use client";

/**
 * Les questions de clarification d'un brief (#321) vues depuis l'écran qui les
 * tranche (#322) : **y répondre** quand le run les attend, et **les relire** avec
 * leurs réponses une fois le tour joué.
 *
 * Ce sont deux moments d'une même conversation, d'où un seul fichier. Ce qui les
 * sépare est ce que l'écran propose : on ne propose pas « approuver / refuser » à
 * quelqu'un à qui on pose une question — c'est la raison d'être du statut
 * `en_attente_reponses`, distinct de `en_attente_brief`.
 *
 * Deux propriétés du contrat portées jusque dans l'UI :
 *
 * - **l'appariement est positionnel**. Le formulaire envoie toujours autant de
 *   réponses qu'il y a de questions, dans l'ordre, une chaîne vide comprise —
 *   l'API refuse en 422 une liste qui ne fait pas le compte, et elle a raison :
 *   une liste décalée affecterait des réponses aux mauvaises questions sans que
 *   rien ne le signale ;
 * - **ne pas savoir est une réponse**. Une réponse vide vaut « je ne sais pas » :
 *   la question partira en hypothèse explicite plutôt que d'être reposée. Le
 *   formulaire le dit, et n'exige donc rien pour être envoyé — un tour de
 *   clarification qu'on ne peut pas clore faute d'une réponse est un run bloqué.
 */

import { useState } from "react";

import { IconeArbitrage, IconeHistorique } from "@/components/Icones";
import { BadgeEtat, Carte, EnTeteSection } from "@/components/Primitives";
import type { TourClarification } from "@/lib/brief";
import { formatDateHeure } from "@/lib/format";

const CLASSE_CHAMP =
  "w-full rounded-md border border-neutral-200 bg-white px-3 py-1.5 text-corps text-neutral-900 placeholder:text-neutral-400 focus:border-emerald-500 focus:outline-none disabled:opacity-50 dark:border-neutral-800 dark:bg-neutral-900 dark:text-neutral-100 dark:placeholder:text-neutral-600";

const CLASSE_BOUTON_PRIMAIRE =
  "rounded-md bg-emerald-600 px-3 py-1.5 text-annexe font-medium text-white hover:bg-emerald-700 disabled:opacity-50";

/** Le formulaire de réponse : une question, un champ, dans l'ordre du brief. */
export function FormulaireReponses({
  questions,
  reponses,
  changer,
  envoyer,
  enCours,
  refus,
  tour,
  toursMax,
}: {
  questions: string[];
  reponses: string[];
  changer: (rang: number, valeur: string) => void;
  envoyer: () => void;
  enCours: boolean;
  refus: string | null;
  tour: number;
  toursMax: number;
}) {
  const repondues = reponses.filter((r) => r.trim().length > 0).length;

  return (
    <Carte
      balise="section"
      ton="attention"
      densite="aeree"
      aria-label="Questions de clarification"
    >
      <EnTeteSection
        titre={
          <>
            Questions de clarification
            <BadgeEtat ton="attention" className="chiffre">
              {questions.length}
            </BadgeEtat>
          </>
        }
        icone={IconeArbitrage}
        ton="attention"
        className="mb-2"
        aside={
          // Le plafond annoncé (#321) : savoir s'il reste un tour change la façon
          // de répondre — on développe au dernier, on va à l'essentiel avant.
          toursMax > 0 ? (
            <BadgeEtat ton="neutre" contour className="chiffre">
              tour {tour} sur {toursMax}
            </BadgeEtat>
          ) : undefined
        }
      />
      <p className="mb-3 text-annexe text-neutral-600 dark:text-neutral-300">
        Le Chef de projet n&apos;a pas pu trancher seul. Vos réponses repartent
        dans un brief régénéré en entier — laisser un champ vide est une réponse
        aussi : la question deviendra une <strong>hypothèse explicite</strong> au
        lieu d&apos;être reposée.
      </p>
      <ol className="space-y-3">
        {questions.map((question, rang) => (
          // La position **est** l'identité d'une question (#318) : le brief est
          // régénéré à chaque tour, aucun identifiant ne survivrait.
          <li key={`question-${rang}`}>
            <label className="flex flex-col gap-1">
              <span className="text-corps text-neutral-800 dark:text-neutral-200">
                {question}
              </span>
              <textarea
                value={reponses[rang] ?? ""}
                onChange={(e) => changer(rang, e.target.value)}
                disabled={enCours}
                rows={2}
                placeholder="Sans réponse : la question partira en hypothèse"
                className={CLASSE_CHAMP}
              />
            </label>
          </li>
        ))}
      </ol>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <button
          type="button"
          disabled={enCours}
          onClick={envoyer}
          className={CLASSE_BOUTON_PRIMAIRE}
        >
          {enCours ? "Envoi…" : "Envoyer les réponses"}
        </button>
        <span className="text-annexe text-neutral-500 dark:text-neutral-400">
          {repondues} sur {questions.length} renseignée
          {repondues > 1 ? "s" : ""}
        </span>
      </div>
      {refus && (
        <p className="mt-2 text-annexe text-rose-600 dark:text-rose-400">
          {refus}
        </p>
      )}
    </Carte>
  );
}

/**
 * Les allers-retours déjà joués, du plus ancien au plus récent : ce qui a été
 * demandé et ce qui a été répondu.
 *
 * C'est le contexte qui manque le plus au moment d'approuver — un brief corrigé
 * par deux tours de questions ne se relit pas comme un premier jet, et une
 * hypothèse qui sort d'un « je ne sais pas » assumé ne se conteste pas comme une
 * hypothèse que personne n'a vue passer. Une question restée sans réponse est
 * **dite** telle, jamais escamotée.
 */
export function HistoriqueClarifications({
  tours,
}: {
  tours: TourClarification[];
}) {
  const [deplie, setDeplie] = useState(false);
  if (tours.length === 0) return null;

  return (
    <Carte balise="section" aria-label="Clarifications déjà jouées">
      <EnTeteSection
        titre={
          <>
            Clarifications
            <BadgeEtat ton="info" className="chiffre">
              {tours.length} tour{tours.length > 1 ? "s" : ""}
            </BadgeEtat>
          </>
        }
        icone={IconeHistorique}
        aside={
          <button
            type="button"
            onClick={() => setDeplie((avant) => !avant)}
            aria-expanded={deplie}
            className="text-annexe font-medium text-sky-700 hover:underline dark:text-sky-400"
          >
            {deplie ? "Replier" : "Voir les échanges"}
          </button>
        }
      />
      {deplie && (
        <ol className="mt-3 space-y-4">
          {tours.map((tour) => (
            <li key={`tour-${tour.tour}-${tour.horodatage}`}>
              <p className="text-annexe font-semibold text-neutral-500 dark:text-neutral-400">
                Tour {tour.tour}
                {tour.horodatage ? ` · ${formatDateHeure(tour.horodatage)}` : ""}
              </p>
              <dl className="mt-1 space-y-2">
                {tour.questions.map((question, rang) => {
                  const reponse = (tour.reponses[rang] ?? "").trim();
                  return (
                    <div key={`echange-${rang}`}>
                      <dt className="text-corps text-neutral-800 dark:text-neutral-200">
                        {question}
                      </dt>
                      <dd
                        className={
                          "mt-0.5 border-l-2 pl-2 text-corps " +
                          (reponse
                            ? "border-emerald-300 whitespace-pre-wrap text-neutral-700 dark:border-emerald-800 dark:text-neutral-300"
                            : "border-neutral-200 text-neutral-500 italic dark:border-neutral-700 dark:text-neutral-400")
                        }
                      >
                        {reponse || "Sans réponse — partie en hypothèse"}
                      </dd>
                    </div>
                  );
                })}
              </dl>
            </li>
          ))}
        </ol>
      )}
    </Carte>
  );
}

"use client";

/**
 * Le centre de notifications déroulant de la barre supérieure (#119, lot 3 de
 * #116). Une cloche, présente sur toutes les pages via le shell, signale d'un
 * badge le nombre de validations humaines (#48) en attente et ouvre un panneau
 * qui les liste — chacune approuvable / refusable sur place, sans quitter la
 * page courante — puis rappelle l'activité récente notable.
 *
 * Le badge suit les `validations` du contexte global (rechargées à chaque
 * événement WebSocket) : il se met à jour en temps réel. Une demande **traitée**
 * (approuvée ou refusée) quitte l'état « en attente » et disparaît donc du badge
 * de lui-même ; le panneau, lui, reste consultable pour les événements récents
 * même quand plus rien n'attend d'arbitrage.
 *
 * Le comportement du menu (clic à l'extérieur, Échap, focus rendu au bouton)
 * reprend celui de la bascule de thème (#118, `BasculeTheme`).
 */

import { useEffect, useRef, useState } from "react";

import { IconeNotifications } from "@/components/Icones";
import {
  estNotableNotification,
  iconeEvenement,
  resumeEvenement,
} from "@/lib/evenements";
import { useEtatGlobal } from "@/lib/etatGlobal";
import { formatHeure } from "@/lib/format";
import { VALIDATION_EN_ATTENTE, type Validation } from "@/lib/types";

/** Décideur d'une validation, tel que fourni par le contexte global (#48). */
type Decider = (tacheId: string, approuve: boolean) => Promise<void>;

/** Nombre d'événements récents notables rappelés dans le panneau. */
const MAX_EVENEMENTS_NOTABLES = 8;

export function CentreNotifications() {
  const { validations, evenements, decider } = useEtatGlobal();
  const [ouvert, setOuvert] = useState(false);
  const conteneur = useRef<HTMLDivElement>(null);
  const declencheur = useRef<HTMLButtonElement>(null);

  const enAttente = validations.filter(
    (v) => v.statut === VALIDATION_EN_ATTENTE,
  );
  const nb = enAttente.length;
  const notables = evenements
    .filter(estNotableNotification)
    .slice(0, MAX_EVENEMENTS_NOTABLES);

  // Fermeture du menu : clic à l'extérieur ou Échap (le focus revient alors au
  // bouton, sans quoi il retomberait sur le document).
  useEffect(() => {
    if (!ouvert) return;
    const surPointeur = (evenement: PointerEvent) => {
      if (!conteneur.current?.contains(evenement.target as Node)) {
        setOuvert(false);
      }
    };
    const surTouche = (evenement: KeyboardEvent) => {
      if (evenement.key !== "Escape") return;
      setOuvert(false);
      declencheur.current?.focus();
    };
    document.addEventListener("pointerdown", surPointeur);
    document.addEventListener("keydown", surTouche);
    return () => {
      document.removeEventListener("pointerdown", surPointeur);
      document.removeEventListener("keydown", surTouche);
    };
  }, [ouvert]);

  const etiquette =
    nb > 0
      ? `Notifications — ${nb} validation${nb > 1 ? "s" : ""} en attente`
      : "Notifications";

  return (
    <div ref={conteneur} className="relative">
      <button
        ref={declencheur}
        type="button"
        onClick={() => setOuvert((avant) => !avant)}
        aria-haspopup="menu"
        aria-expanded={ouvert}
        aria-label={etiquette}
        title={etiquette}
        className="relative block rounded-md p-2 text-neutral-500 hover:bg-neutral-100 hover:text-neutral-900 dark:text-neutral-400 dark:hover:bg-neutral-900 dark:hover:text-neutral-100"
      >
        <IconeNotifications className="size-5" />
        {nb > 0 && (
          // Décoratif : le compte est déjà dans l'`aria-label` du bouton.
          <span
            aria-hidden="true"
            className="absolute -top-0.5 -right-0.5 inline-flex min-w-4 items-center justify-center rounded-full bg-rose-600 px-1 text-[0.625rem] leading-4 font-semibold text-white"
          >
            {nb > 9 ? "9+" : nb}
          </span>
        )}
      </button>

      {ouvert && (
        <div
          role="menu"
          aria-label="Notifications"
          className="absolute top-full right-0 z-20 mt-2 flex max-h-[min(70vh,32rem)] w-80 max-w-[calc(100vw-1.5rem)] flex-col overflow-hidden rounded-lg border border-neutral-200 bg-white shadow-lg dark:border-neutral-800 dark:bg-neutral-900"
        >
          <div className="flex items-center justify-between border-b border-neutral-200 px-3 py-2 dark:border-neutral-800">
            <span className="text-sm font-semibold">Notifications</span>
            {nb > 0 && (
              <span className="rounded-full bg-rose-100 px-2 py-0.5 text-xs font-medium text-rose-700 dark:bg-rose-950 dark:text-rose-300">
                {nb} à valider
              </span>
            )}
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto">
            <section aria-label="Validations en attente" className="p-2">
              <h3 className="px-1 pb-1 text-xs font-semibold tracking-wide text-neutral-500 uppercase dark:text-neutral-400">
                À valider
              </h3>
              {enAttente.length === 0 ? (
                <p className="px-1 py-1 text-xs text-neutral-500 dark:text-neutral-400">
                  Aucune validation en attente.
                </p>
              ) : (
                <ul className="space-y-2">
                  {enAttente.map((validation) => (
                    <li key={validation.tache_id}>
                      <CarteValidationCompacte
                        validation={validation}
                        decider={decider}
                      />
                    </li>
                  ))}
                </ul>
              )}
            </section>

            {/* L'activité récente notable : le panneau reste consultable même
                quand plus aucune validation n'est en attente (critère #119). */}
            <section
              aria-label="Activité récente"
              className="border-t border-neutral-200 p-2 dark:border-neutral-800"
            >
              <h3 className="px-1 pb-1 text-xs font-semibold tracking-wide text-neutral-500 uppercase dark:text-neutral-400">
                Activité récente
              </h3>
              {notables.length === 0 ? (
                <p className="px-1 py-1 text-xs text-neutral-500 dark:text-neutral-400">
                  Rien de notable pour l&apos;instant.
                </p>
              ) : (
                <ol className="space-y-0.5">
                  {notables.map((evenement, index) => (
                    <li
                      key={`${evenement.horodatage}-${index}`}
                      className="flex items-baseline gap-2 rounded px-1 py-1 text-xs"
                    >
                      <span className="shrink-0 font-mono text-[0.625rem] text-neutral-400 dark:text-neutral-500">
                        {formatHeure(evenement.horodatage)}
                      </span>
                      <span className="shrink-0" aria-hidden="true">
                        {iconeEvenement(evenement)}
                      </span>
                      <span
                        className="min-w-0 flex-1 truncate text-neutral-600 dark:text-neutral-300"
                        title={evenement.detail || undefined}
                      >
                        {resumeEvenement(evenement)}
                      </span>
                    </li>
                  ))}
                </ol>
              )}
            </section>
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * Une demande de validation en version compacte, taillée pour la largeur du
 * panneau : mêmes informations et même flux de décision que la carte pleine du
 * tableau de bord (`PanneauValidations`), mais resserrés. La décision passe par
 * le `decider` du contexte — le moteur reprend ou annule la tâche —, la demande
 * quitte alors l'état « en attente » et disparaît de la liste (donc du badge).
 */
function CarteValidationCompacte({
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
      // Succès : la demande sort de « en attente » au rechargement et la carte
      // se démonte — inutile de rétablir `enCours`. En cas d'échec seulement,
      // on rend la main pour réessayer.
    } catch (e) {
      setErreur(e instanceof Error ? e.message : String(e));
      setEnCours(false);
    }
  };

  return (
    <article className="rounded-md border border-amber-200 bg-amber-50 p-2 dark:border-amber-900 dark:bg-amber-950/40">
      <p className="text-xs font-medium" title={validation.tache_id}>
        {validation.titre || validation.tache_id}
      </p>
      <p className="mt-0.5 text-[0.6875rem] text-neutral-500 dark:text-neutral-400">
        🤖 {validation.agent}
        {validation.role ? ` · ${validation.role}` : ""}
      </p>
      {validation.description && (
        <p className="mt-1 line-clamp-2 text-[0.6875rem] whitespace-pre-wrap text-neutral-600 dark:text-neutral-300">
          {validation.description}
        </p>
      )}
      {validation.raison && (
        <p className="mt-1 text-[0.6875rem] text-amber-700 italic dark:text-amber-400">
          Motif : {validation.raison}
        </p>
      )}
      <div className="mt-2 flex gap-1.5">
        <button
          type="button"
          disabled={enCours}
          onClick={() => void surDecision(true)}
          className="rounded bg-emerald-600 px-2 py-1 text-[0.6875rem] font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
        >
          {enCours ? "Envoi…" : "Approuver"}
        </button>
        <button
          type="button"
          disabled={enCours}
          onClick={() => void surDecision(false)}
          className="rounded border border-rose-300 px-2 py-1 text-[0.6875rem] font-medium text-rose-700 hover:bg-rose-50 disabled:opacity-50 dark:border-rose-800 dark:text-rose-400 dark:hover:bg-rose-950"
        >
          Refuser
        </button>
      </div>
      {erreur && (
        <p className="mt-1 text-[0.6875rem] text-rose-600 dark:text-rose-400">
          {erreur}
        </p>
      )}
    </article>
  );
}

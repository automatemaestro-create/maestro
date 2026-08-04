"use client";

/**
 * Le poste de pilotage **vide**, expliqué (ticket #186, lot 2 de #184).
 *
 * Depuis que le lancement local démarre en **mode réel**
 * (`scripts/controltower/start.sh`, sur Redis), un premier démarrage n'a plus
 * rien à montrer : aucune tâche, aucun événement, aucune validation. Quatre
 * panneaux vides feraient croire à une panne — ou pire, laisseraient chercher
 * dans les logs ce qui n'y est pas. On remplace donc l'écran par ce qu'il faut
 * faire pour le remplir.
 *
 * Ce n'est pas un état d'erreur : l'API injoignable, elle, a sa bannière
 * (`BanniereErreurApi`), et le tableau de bord garde ses panneaux dans ce
 * cas-là — un écran vide *et* silencieux ne se diagnostique pas de la même
 * façon qu'un écran vide *et* connecté.
 */

import { lancerGuide } from "@/lib/guide";

/** Ce que le poste montre dès qu'un run publie — l'inventaire de ce qui manque. */
const CE_QUI_ARRIVE = [
  "les demandes d'arbitrage humain, en tête d'écran",
  "les indicateurs de tête : run en cours, tâches, agents, dépense",
  "les tâches réparties par statut dans le Kanban",
  "l'activité en direct, événement par événement",
];

export function PosteVide({ connecte }: { connecte: boolean }) {
  return (
    <section
      aria-label="Poste de pilotage vide"
      className="rounded-lg border border-neutral-200 bg-white p-5 dark:border-neutral-800 dark:bg-neutral-900"
    >
      <h2 className="text-base font-semibold">Aucun run n&apos;a encore publié d&apos;événement</h2>
      <p className="mt-1 max-w-2xl text-sm text-neutral-600 dark:text-neutral-300">
        La Control Tower est branchée et vous écoute — elle n&apos;a simplement
        rien à afficher pour l&apos;instant. Lancez une exécution : cet écran se
        remplit tout seul, sans le recharger.
      </p>

      <div className="mt-4 grid gap-3 md:grid-cols-2">
        <Action
          titre="Lancer une orchestration"
          detail="Le moteur découpe l'objectif en tâches, les assigne aux agents et publie chaque étape ici."
          commande={'maestro-run --publier "<votre objectif>"'}
          note="--publier est ce qui alimente ce poste de pilotage."
        />
        <Action
          titre="Juste explorer l'interface"
          detail="Un scénario de démonstration, sans Redis ni appel modèle — les données sont factices et le disent."
          commande="bash scripts/controltower/start.sh --demo"
          note="À relancer depuis le dépôt : le mode remplace la session courante."
        />
      </div>

      <div className="mt-5 border-t border-neutral-200 pt-4 dark:border-neutral-800">
        <p className="text-xs font-medium uppercase tracking-wide text-neutral-500 dark:text-neutral-400">
          Ce qui apparaîtra ici
        </p>
        <ul className="mt-2 space-y-1 text-sm text-neutral-600 dark:text-neutral-300">
          {CE_QUI_ARRIVE.map((ligne) => (
            <li key={ligne} className="flex gap-2">
              <span aria-hidden="true" className="text-neutral-400">
                ·
              </span>
              {ligne}
            </li>
          ))}
        </ul>
        <p className="mt-3 text-xs text-neutral-500 dark:text-neutral-400">
          {connecte
            ? "Temps réel connecté : rien à rafraîchir, les événements arriveront d'eux-mêmes."
            : "Temps réel en cours de connexion — la reprise est automatique."}{" "}
          L&apos;historique, lui, est conservé : un redémarrage de l&apos;API
          rejoue les événements déjà publiés.
        </p>
        <button
          type="button"
          onClick={lancerGuide}
          className="mt-3 rounded-md border border-neutral-300 px-3 py-1.5 text-xs font-medium hover:bg-neutral-50 dark:border-neutral-700 dark:hover:bg-neutral-800"
        >
          Faire la visite guidée
        </button>
      </div>
    </section>
  );
}

function Action({
  titre,
  detail,
  commande,
  note,
}: {
  titre: string;
  detail: string;
  commande: string;
  note: string;
}) {
  return (
    <article className="rounded-md border border-neutral-200 bg-neutral-50 p-3 dark:border-neutral-800 dark:bg-neutral-950">
      <h3 className="text-sm font-medium">{titre}</h3>
      <p className="mt-1 text-xs text-neutral-600 dark:text-neutral-300">{detail}</p>
      <pre className="mt-2 overflow-x-auto rounded bg-neutral-900 px-2 py-1.5 text-xs text-neutral-100 dark:bg-black">
        <code>{commande}</code>
      </pre>
      <p className="mt-1 text-xs text-neutral-500 dark:text-neutral-400">{note}</p>
    </article>
  );
}

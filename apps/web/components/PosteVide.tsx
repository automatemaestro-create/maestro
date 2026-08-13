"use client";

/**
 * Le poste de pilotage **vide**, expliqué (ticket #186, lot 2 de #184) — et,
 * depuis #281, vide **pour un projet donné**.
 *
 * Depuis que le lancement local démarre en **mode réel**
 * (`scripts/controltower/start.sh`, sur Redis), un premier démarrage n'a plus
 * rien à montrer : aucune tâche, aucun événement, aucune validation. Quatre
 * panneaux vides feraient croire à une panne — ou pire, laisseraient chercher
 * dans les logs ce qui n'y est pas. On remplace donc l'écran par ce qu'il faut
 * faire pour le remplir.
 *
 * Trois vides se ressemblent et ne se diagnostiquent pas pareil, d'où le soin
 * mis à les séparer (critère #281) :
 *
 * - **une panne** — l'API injoignable a sa bannière (`BanniereErreurApi`), et le
 *   tableau de bord garde ses panneaux dans ce cas-là : un écran vide *et*
 *   silencieux ne se lit pas comme un écran vide *et* connecté ;
 * - **l'absence de projet** — elle n'atteint jamais cet écran : la garde du
 *   shell (#279) montre la porte d'entrée avant que la Control Tower ne soit
 *   montée. Ici, il y a un projet, et il est nommé ;
 * - **rien encore sur ce projet** — le cas de ce composant. Il le dit avec le
 *   nom du projet, parce qu'un « aucun événement » anonyme, sur une Control
 *   Tower qui n'en montre plus qu'un, laisse croire que rien ne tourne nulle
 *   part.
 *
 * Il dit aussi **pourquoi il peut rester vide alors qu'un run tourne** : un run
 * publié sans `projet_id` ne relève d'aucun projet et n'entre donc dans la vue
 * d'aucun (#277). C'est le cas de `maestro-run --publier`, qui n'a pas d'option
 * de rattachement — le taire ferait chercher une panne là où il n'y a qu'un
 * périmètre.
 */

import Link from "next/link";

import { lancerGuide } from "@/lib/guide";
import type { Projet } from "@/lib/types";

/** Ce que le poste montre dès qu'un run publie — l'inventaire de ce qui manque. */
const CE_QUI_ARRIVE = [
  "les demandes d'arbitrage humain, en tête d'écran",
  "les indicateurs de tête : run en cours, tâches, agents, dépense",
  "les tâches réparties par statut dans le Kanban",
  "l'activité en direct, événement par événement",
];

export function PosteVide({
  projet,
  connecte,
}: {
  projet: Projet;
  connecte: boolean;
}) {
  return (
    <section
      aria-label="Poste de pilotage vide"
      className="rounded-lg border border-neutral-200 bg-white p-5 dark:border-neutral-800 dark:bg-neutral-900"
    >
      <h2 className="text-base font-semibold">
        Rien encore sur {projet.nom}
      </h2>
      <p className="mt-1 max-w-2xl text-sm text-neutral-600 dark:text-neutral-300">
        La Control Tower est branchée et vous écoute — elle est simplement
        cadrée sur <strong className="font-medium">{projet.nom}</strong>, et
        aucun run n&apos;y a encore publié d&apos;événement. Ce n&apos;est ni une
        panne (une API injoignable, elle, affiche sa bannière) ni l&apos;absence
        de projet : lancez une exécution dans ce projet, cet écran se remplit
        tout seul, sans le recharger.
      </p>

      <div className="mt-4 grid gap-3 md:grid-cols-2">
        <Action
          titre="Lancer une orchestration dans ce projet"
          detail="Le moteur découpe l'objectif en tâches, les assigne aux agents et publie chaque étape ici."
          lien={{ href: "/composer", libelle: "Composer un objectif" }}
          note="L'écran compose l'objectif — texte, fichiers déposés, dossier de références, adresses — et montre ce qui sera lu avant de lancer (#319). L'API reste servie pour les scripts."
        />
        <Action
          titre="Juste explorer l'interface"
          detail="Un scénario de démonstration, sans Redis ni appel modèle — les données sont factices et le disent."
          commande="bash scripts/controltower/start.sh --demo"
          note="À relancer depuis le dépôt : le mode remplace la session courante."
        />
      </div>

      {/* Le piège que ce lot rend possible : un run tourne, et cet écran reste
          vide parce que ce run n'appartient à aucun projet. Le dire ici est ce
          qui distingue « rien encore sur ce projet » d'une panne. */}
      <p className="mt-3 max-w-2xl rounded-md border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900 dark:border-amber-900/60 dark:bg-amber-950/40 dark:text-amber-200">
        Un run lancé <strong className="font-medium">sans projet</strong> —
        c&apos;est le cas de <code>maestro-run --publier</code>, qui n&apos;a pas
        d&apos;option de rattachement — ne relève d&apos;aucun projet et
        n&apos;apparaît donc sur l&apos;écran d&apos;aucun. Ses tâches existent,
        elles ne sont simplement pas ici.
      </p>

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

/**
 * Une porte de sortie du poste vide : une commande à copier, ou — depuis #319 —
 * un **écran** de l'interface quand il en existe un. Les deux et pas l'un ou
 * l'autre : le formulaire de lancement existe désormais, la ligne de commande
 * reste ce dont un script a besoin.
 */
function Action({
  titre,
  detail,
  commande,
  lien,
  note,
}: {
  titre: string;
  detail: string;
  commande?: string;
  lien?: { href: string; libelle: string };
  note: string;
}) {
  return (
    <article className="rounded-md border border-neutral-200 bg-neutral-50 p-3 dark:border-neutral-800 dark:bg-neutral-950">
      <h3 className="text-sm font-medium">{titre}</h3>
      <p className="mt-1 text-xs text-neutral-600 dark:text-neutral-300">{detail}</p>
      {commande && (
        <pre className="mt-2 overflow-x-auto rounded bg-neutral-900 px-2 py-1.5 text-xs text-neutral-100 dark:bg-black">
          <code>{commande}</code>
        </pre>
      )}
      {lien && (
        <Link
          href={lien.href}
          className="mt-2 inline-block rounded-md bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-700"
        >
          {lien.libelle}
        </Link>
      )}
      <p className="mt-1 text-xs text-neutral-500 dark:text-neutral-400">{note}</p>
    </article>
  );
}

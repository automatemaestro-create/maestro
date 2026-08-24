"use client";

/**
 * La vue d'un run (#475, lot 3 de #472, docs/05 §2.4.2) : sa progression en tête,
 * son Kanban dessous, et **son journal** au pied (#478).
 *
 * Ouvrir un run donne enfin son backlog. Jusqu'ici le Kanban était celui du
 * **projet** (#248) et un run n'avait pas de vue à lui : impossible de voir ce que
 * *ce* run avait fait, dans un projet où plusieurs runs se succèdent (revue #470,
 * docs/29 §3).
 *
 * Trois décisions la tiennent :
 *
 * - **Le Kanban est réutilisé, pas réimplémenté.** `components/Kanban` rend les
 *   mêmes colonnes, les mêmes cartes et le même détail sur place (#251) — ce qui
 *   change est ce qu'on lui donne : les tâches de ce run. Seul son **vide** est
 *   nommé ici, la phrase par défaut désignant le projet (`messageVide`).
 * - **L'appartenance au run vient de l'API.** Les tâches arrivent par
 *   `?run=<run_id>` (#473) et non d'un filtre sur `etatGlobal.taches` : un
 *   identifiant de tâche est partagé entre un run et sa relance, si bien qu'un
 *   filtre local ferait disparaître de la vue les tâches qu'un successeur a
 *   reprises (`lib/useTachesRun`).
 * - **Le temps réel est celui du shell.** Aucune seconde WebSocket : la vue se
 *   rafraîchit au **pouls** du shell (`revision`), c'est-à-dire aux instants où
 *   celui-ci vient de relire — chargement initial, reconnexion, rafale
 *   d'événements coalescée.
 * - **Le journal du run part du persisté** (#478, `components/runs/JournalRun`) :
 *   ouvrir un run terminé hier montre ce qu'il a dit, là où le fil du shell —
 *   borné aux derniers événements **reçus** — n'aurait rien eu à montrer.
 *
 * Le run lui-même est lu dans `executions`, la liste que le shell tient déjà pour
 * le projet actif : elle porte tout ce que la tête affiche (statut, vitalité,
 * progression, coût, attente) et se met à jour d'elle-même. Un run **absent** de
 * cette liste n'est donc pas une panne — c'est un run d'un autre projet, ou un
 * identifiant qui n'existe pas —, et la vue le dit au lieu d'afficher un Kanban
 * vide qui se lirait « ce run n'a rien fait ».
 */

import Link from "next/link";

import { BanniereErreurApi } from "@/components/BanniereErreurApi";
import { IconeFlecheGauche, IconeRuns } from "@/components/Icones";
import { Kanban } from "@/components/Kanban";
import { Carte, EtatVide } from "@/components/Primitives";
import {
  Avancement,
  BadgeRun,
  fondDe,
  LigneAttente,
  LigneInterruption,
} from "@/components/runs/EtatRun";
import { JournalRun } from "@/components/runs/JournalRun";
import { useEtatGlobal } from "@/lib/etatGlobal";
import {
  ATTENTE_BRIEF,
  ATTENTE_REPONSES,
  causeDAttente,
  regimeDuRun,
  runsEnAttenteDeValidation,
  REGIME_SUSPENDU,
  type CauseAttente,
} from "@/lib/execution";
import { formatCout, formatHeureRelative } from "@/lib/format";
import { useHorloge } from "@/lib/horloge";
import { entreeParLibelle, hrefRun } from "@/lib/navigation";
import type { ResumeExecution } from "@/lib/types";
import { useTachesRun } from "@/lib/useTachesRun";

export function VueRun({ runId }: { runId: string }) {
  const {
    projet,
    portee,
    executions,
    validations,
    taches: tachesDuProjet,
    agents,
    evenements,
    reassigner,
    revision,
    chargement,
    erreur,
  } = useEtatGlobal();

  const run = executions.find((execution) => execution.run_id === runId);
  // `null` tant que le run n'est pas reconnu comme un run de ce projet : inutile
  // de redemander ses tâches à chaque battement pour s'entendre répondre une
  // liste vide (ou un 404 `run-inconnu` sur une faute de frappe).
  const {
    taches,
    chargement: chargementTaches,
    erreur: erreurTaches,
  } = useTachesRun(portee, run === undefined ? null : runId, revision);

  // L'appariement validation → run passe par les tâches **du projet** : une
  // demande de validation porte sa tâche, jamais son run (`lib/execution`). Il se
  // fait donc sur la liste du shell et non sur celle du run ci-dessus, qui n'est
  // pas encore là au premier rendu.
  const attendUneValidation = runsEnAttenteDeValidation(
    validations,
    tachesDuProjet,
  ).has(runId);
  const attente =
    run === undefined || regimeDuRun(run, attendUneValidation) !== REGIME_SUSPENDU
      ? null
      : causeDAttente(run, attendUneValidation);

  const liste = entreeParLibelle("Runs");

  return (
    <>
      {/* L'erreur du shell d'abord : quand l'API est éteinte les deux lectures
          échouent, et deux bannières identiques ne diraient pas deux choses. */}
      <BanniereErreurApi erreur={erreur ?? erreurTaches} />

      <section aria-label="Run">
        {liste && (
          <p className="mb-2">
            <Link
              href={liste.href}
              className="inline-flex items-center gap-1 text-annexe font-medium text-sky-700 hover:underline dark:text-sky-400"
            >
              <IconeFlecheGauche className="size-3.5 shrink-0" />
              Tous les runs
            </Link>
          </p>
        )}

        {chargement ? (
          <p className="text-sm text-neutral-500">Chargement du run…</p>
        ) : run === undefined ? (
          // Ni panne ni Kanban vide : le run n'est pas de ce projet, ou n'existe
          // pas. Le dire évite la lecture « ce run n'a rien fait », qui est le
          // contresens que le 404 `run-inconnu` du backend refuse déjà côté API
          // (#473, §6.0bis).
          <EtatVide
            icone={IconeRuns}
            message={`Aucun run ${runId} sur ${projet.nom}. Un run appartient à un projet : celui-ci relève peut-être d'un autre, ou son identifiant n'existe pas.`}
            lien={liste && { href: liste.href, libelle: "Voir les runs du projet" }}
          />
        ) : (
          <EnTeteRun
            run={run}
            attendUneValidation={attendUneValidation}
            attente={attente}
          />
        )}
      </section>

      {run !== undefined && (
        <Kanban
          taches={taches}
          agents={agents}
          reassigner={reassigner}
          projet={projet}
          messageVide={
            chargementTaches
              ? "Chargement des tâches de ce run…"
              : messageVideDuRun(attente)
          }
        />
      )}

      {/* Sous le Kanban, et non à côté : le Kanban répond à « où en est-il ? »,
          le journal à « qu'a-t-il fait ? » — on ne consulte le second qu'après
          avoir lu le premier. */}
      {run !== undefined && (
        <JournalRun
          portee={portee}
          runId={runId}
          direct={evenements}
          revision={revision}
        />
      )}
    </>
  );
}

/**
 * La tête de la vue : ce qu'est ce run, où il en est, et ce qui le retient.
 *
 * La **barre de progression** y est `ample` : dans la liste elle s'empile par
 * dizaines, ici elle est **la** réponse à « où en est-il ? ». Ses compteurs sont
 * ceux du lot 1 (#473) — comptés par le backend sur la machine à états du moteur,
 * jamais recomptés sur les tâches chargées, ce qui ferait mesurer à la barre sa
 * propre pagination.
 *
 * Le régime, le badge et l'attente sont **exactement** ceux de la liste des runs
 * (`lib/execution`, `components/runs/EtatRun`) : un run lu « Brief à valider »
 * dans la liste et « En cours » dans sa vue serait un run dont on doute.
 */
function EnTeteRun({
  run,
  attendUneValidation,
  attente,
}: {
  run: ResumeExecution;
  attendUneValidation: boolean;
  attente: CauseAttente | null;
}) {
  const maintenant = useHorloge();
  const regime = regimeDuRun(run, attendUneValidation);
  const repris = run.reprise_de ? hrefRun(run.reprise_de) : undefined;

  return (
    <Carte balise="div" ton={fondDe(regime)} densite="aeree">
      <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-1">
        {/* `line-clamp-3` et non `truncate` : un objectif tient rarement sur une
            ligne, et sur un run **relancé** (#349) c'est le brief approuvé qui
            en tient lieu — plusieurs paragraphes. Trois lignes suffisent à
            reconnaître le run ; le texte entier reste en infobulle, comme la
            cloche le fait déjà du même champ (`CentreNotifications`). */}
        <h2
          className="line-clamp-3 min-w-0 flex-1 text-corps font-semibold"
          title={run.objectif || run.run_id}
        >
          {run.objectif || run.run_id}
        </h2>
        <BadgeRun run={run} regime={regime} attente={attente} />
      </div>

      <p className="chiffre mt-1 truncate text-annexe text-neutral-500 dark:text-neutral-400">
        {run.run_id}
        {run.debut
          ? ` · démarré ${formatHeureRelative(run.debut, maintenant)}`
          : ""}
        {run.fin ? ` · terminé ${formatHeureRelative(run.fin, maintenant)}` : ""}
        {` · ${formatCout(run.cout_usd)}`}
      </p>

      {/* Un run qui en reprend un autre (#349) le dit, et y mène : sans ce
          renvoi, le cadrage déjà payé et les tâches du run repris seraient hors
          de portée depuis celui qui les continue. */}
      {run.reprise_de && (
        <p className="chiffre mt-0.5 truncate text-annexe text-neutral-500 dark:text-neutral-400">
          Reprise de{" "}
          {repris ? (
            <Link href={repris} className="font-medium hover:underline">
              {run.reprise_de}
            </Link>
          ) : (
            run.reprise_de
          )}
        </p>
      )}

      <Avancement run={run} taille="ample" />

      <LigneAttente run={run} attente={attente} className="mt-3" />
      <LigneInterruption run={run} regime={regime} className="mt-3" />
    </Carte>
  );
}

/**
 * Ce que dit le Kanban **vide** de ce run — et il y a deux vides, qui ne
 * s'expliquent pas de la même façon.
 *
 * Un run arrêté sur son brief n'a créé **aucune** tâche : la décomposition n'a pas
 * eu lieu, c'est son état normal et non le symptôme d'une lecture ratée. Les
 * autres cas n'ont rien à expliquer, seulement à dire que ça viendra.
 */
function messageVideDuRun(attente: CauseAttente | null): string {
  return attente === ATTENTE_BRIEF || attente === ATTENTE_REPONSES
    ? "Aucune tâche : ce run attend une décision sur son brief, la décomposition n'a pas encore eu lieu."
    : "Aucune tâche pour ce run — le tableau se remplira dès qu'il publiera ses événements.";
}

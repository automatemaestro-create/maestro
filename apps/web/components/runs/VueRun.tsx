"use client";

/**
 * La vue d'un run (#475, lot 3 de #472, docs/05 §2.4.2) : sa progression en tête,
 * et **la lecture qu'on a choisie** dessous.
 *
 * Cette lecture est **triple** — #491 l'a rendue double (le **pipeline**, le flux
 * — quoi après quoi ; le **Kanban**, les états — combien dans quelle colonne),
 * #516 y a ajouté le **journal** (qu'a-t-il fait), qui se lisait jusque-là au
 * pied de la vue, sous les deux autres. Le raisonnement complet, ce que l'ordre
 * des onglets conserve de #478 et les options écartées vivent dans `lib/vuesRun`
 * — pas ici : cette page les monte, elle ne les tranche pas.
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
 *   borné aux derniers événements **reçus** — n'aurait rien eu à montrer. C'est
 *   aussi ce qui rend son démontage sans conséquence quand on regarde un autre
 *   onglet : il se relit à l'ouverture, il ne se perd pas.
 *
 * Le run lui-même est lu dans `executions`, la liste que le shell tient déjà pour
 * le projet actif : elle porte tout ce que la tête affiche (statut, vitalité,
 * progression, coût, attente) et se met à jour d'elle-même. Un run **absent** de
 * cette liste n'est donc pas une panne — c'est un run d'un autre projet, ou un
 * identifiant qui n'existe pas —, et la vue le dit au lieu d'afficher un Kanban
 * vide qui se lirait « ce run n'a rien fait ».
 */

import Link from "next/link";
import { useState } from "react";

import { BanniereErreurApi } from "@/components/BanniereErreurApi";
import { IconeFlecheGauche, IconeRuns } from "@/components/Icones";
import { Kanban } from "@/components/Kanban";
import { Carte, EtatVide } from "@/components/Primitives";
import {
  Avancement,
  BadgeRun,
  fondDe,
  GestesRun,
  LigneAttente,
  LigneCause,
  LigneInterruption,
  LignePause,
} from "@/components/runs/EtatRun";
import { JournalRun } from "@/components/runs/JournalRun";
import { VuePipeline } from "@/components/runs/VuePipeline";
import { useEtatGlobal } from "@/lib/etatGlobal";
import {
  ATTENTE_BRIEF,
  ATTENTE_REPONSES,
  causeDAttente,
  regimeDuRun,
  runsEnAttenteDeValidation,
  tachesEnAttenteDeValidation,
  REGIME_SUSPENDU,
  type CauseAttente,
} from "@/lib/execution";
import { formatCout, formatHeureRelative } from "@/lib/format";
import { useHorloge } from "@/lib/horloge";
import { entreeParLibelle, hrefRun } from "@/lib/navigation";
import type { ResumeExecution } from "@/lib/types";
import { useTachesRun } from "@/lib/useTachesRun";
import {
  VUES_RUN,
  VUE_JOURNAL,
  VUE_KANBAN,
  VUE_PIPELINE,
  VUE_RUN_DEFAUT,
  type VueRunCle,
} from "@/lib/vuesRun";

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

  // La lecture affichée. `useState` et non une route : les trois vues portent le
  // *même* run, déjà chargé — une frontière de route ferait repartir la tête et
  // les autres lectures pour un changement de regard (`lib/vuesRun`).
  const [vue, setVue] = useState<VueRunCle>(VUE_RUN_DEFAUT);

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
        <>
          <OngletsVueRun vue={vue} choisir={setVue} />

          {vue === VUE_PIPELINE && (
            <VuePipeline
              runId={runId}
              taches={taches}
              agents={agents}
              reassigner={reassigner}
              // L'attente humaine se lit dans la file des validations et non sur
              // la tâche : le moteur n'émet pas encore `en_attente_validation`
              // (`lib/execution`). C'est le troisième critère de #491, et le
              // défaut d'origine du chantier.
              enAttenteHumaine={tachesEnAttenteDeValidation(validations)}
              revision={revision}
              messageVide={messageVideDuRun(attente)}
            />
          )}

          {vue === VUE_KANBAN && (
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

          {/* Dans la bascule, et non sous elle (#516). L'ordre de lecture que
              #478 défendait tient toujours — le Kanban répond à « où en est-il ? »,
              le journal à « qu'a-t-il fait ? », et on ne consulte le second
              qu'après avoir lu le premier —, mais il se dit désormais par la
              **position** de l'onglet, qui ferme la rangée. Rendu en dehors du
              `vue === …`, ce fil s'affichait sous les deux autres lectures, donc
              sous le pipeline : collé à un graphe, il s'en lisait comme le
              détail. */}
          {vue === VUE_JOURNAL && (
            <JournalRun
              portee={portee}
              runId={runId}
              direct={evenements}
              revision={revision}
            />
          )}
        </>
      )}
    </>
  );
}

/**
 * La bascule entre les trois lectures d'un run (#491, troisième position #516).
 *
 * Des **boutons** et non des liens, contrairement aux onglets d'une fiche agent
 * (`components/OngletsAgent`) : ceux-là changent de page, celui-ci change de
 * regard sur la page qu'on a déjà. La forme reste la même — un onglet souligné,
 * `aria-current` sur l'actif — pour que le geste se reconnaisse d'un écran à
 * l'autre.
 *
 * L'infobulle porte **la question** à laquelle chaque vue répond, et non son
 * contenu : « Pipeline », « Kanban » et « Journal » ne disent pas d'eux-mêmes
 * lequel montre quoi, et c'est précisément la confusion que l'arbitrage devait
 * lever.
 */
function OngletsVueRun({
  vue,
  choisir,
}: {
  vue: VueRunCle;
  choisir: (vue: VueRunCle) => void;
}) {
  return (
    <nav
      aria-label="Lectures de ce run"
      className="flex flex-wrap gap-1 border-b border-neutral-200 dark:border-neutral-800"
    >
      {VUES_RUN.map(({ cle, libelle, question, icone: Icone }) => {
        const courant = cle === vue;
        return (
          <button
            key={cle}
            type="button"
            onClick={() => choisir(cle)}
            aria-current={courant ? "page" : undefined}
            title={question}
            className={
              "-mb-px inline-flex items-center gap-1.5 rounded-t-md border-b-2 px-3 py-2 text-corps transition-colors " +
              (courant
                ? "border-emerald-600 font-medium text-neutral-900 dark:border-emerald-500 dark:text-neutral-100"
                : "border-transparent text-neutral-500 hover:bg-neutral-100 hover:text-neutral-900 dark:text-neutral-400 dark:hover:bg-neutral-900 dark:hover:text-neutral-100")
            }
          >
            <Icone className="size-4 shrink-0" />
            {libelle}
          </button>
        );
      })}
    </nav>
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
      {/* Même ordre que dans la liste (`CarteRun`), et c'est le point : un run
          lu « Plafond de dépense atteint » dans la liste doit se lire pareil
          ici. */}
      <LigneCause run={run} className="mt-3" />
      <LigneInterruption run={run} regime={regime} className="mt-3" />
      <LignePause regime={regime} className="mt-3" />
      <GestesRun run={run} className="mt-3" />
    </Carte>
  );
}

/**
 * Ce que dit la vue **vide** de ce run — et il y a deux vides, qui ne
 * s'expliquent pas de la même façon.
 *
 * Un run arrêté sur son brief n'a créé **aucune** tâche : la décomposition n'a pas
 * eu lieu, c'est son état normal et non le symptôme d'une lecture ratée. Les
 * autres cas n'ont rien à expliquer, seulement à dire que ça viendra.
 *
 * La phrase ne nomme plus « le tableau » depuis #491 : les deux lectures la
 * partagent, et un pipeline vide qui promettrait de remplir un tableau désignerait
 * l'écran d'à côté.
 */
function messageVideDuRun(attente: CauseAttente | null): string {
  return attente === ATTENTE_BRIEF || attente === ATTENTE_REPONSES
    ? "Aucune tâche : ce run attend une décision sur son brief, la décomposition n'a pas encore eu lieu."
    : "Aucune tâche pour ce run — cette vue se remplira dès qu'il publiera ses événements.";
}

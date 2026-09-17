"use client";

/**
 * Le run qui tourne, **au centre de l'écran d'accueil** (#927, lot 6 de #921,
 * docs/35 §3.2).
 *
 * C'est le verdict du [retex du 2026-09-11](../../../../docs/retex/2026-09-11-premiere-session-utilisateur.md)
 * appliqué : *« le pipeline du run — graphe par niveaux, dernier geste horodaté,
 * checklist, chrono, coût par tâche — **la meilleure vue du produit** »*. Cette
 * vue était à deux clics, derrière une liste, sur un écran qu'on ne visite pas
 * spontanément. Elle est désormais ce qu'on voit en arrivant.
 *
 * **Rien n'est réécrit ici, tout est remonté.** `CarteRun` est la ligne de run
 * qu'on lit déjà dans la liste et dans l'état des runs (#474, #476) ;
 * `VuePipeline` est la vue par défaut d'un run depuis #491 et les quatre lectures
 * gardent leur arbitrage (`lib/vuesRun`) — ce bloc n'en monte qu'une, la bascule
 * restant dans la vue du run, où l'on va par le titre de la carte.
 *
 * Quatre décisions le tiennent, et les deux premières viennent de la veille de
 * conception du ticket (consignée sur #927) :
 *
 * - **Le résumé avant le dessin** — d'après la page d'un run GitHub Actions,
 *   capturée le 2026-09-17 : une ligne de faits en tête (*Triggered via … ·
 *   Status · Total duration · Artifacts*), **puis** le graphe, dans un cadre qui
 *   lui appartient. C'est aussi le reproche que docs/30 §1.2 fait déjà à notre
 *   onglet Pipeline, qui ouvre d'emblée sur le graphe.
 * - **Le run promu quitte la liste** — d'après la page d'aperçu d'un projet
 *   Vercel, qui **sort** le déploiement courant de la liste des déploiements au
 *   lieu de l'y répéter. `EtatDesRuns` reçoit donc les identifiants promus et ne
 *   les rend plus (`exclure`) : un objet, une place. C'est ce qui tient la
 *   promesse de docs/35 §4 — *ce qui est mis au centre remplace, il ne s'ajoute
 *   pas* — et ce qui garde le corps de l'écran sous les trois blocs de la règle
 *   des trois places (docs/30 §4).
 * - **Un seul pipeline déployé, jamais N.** Deux graphes empilés ne répondent pas
 *   à « où ça en est », ils demandent au lecteur de choisir — et le cadre du
 *   pipeline fait à lui seul 34 rem de haut. Le run de **tête** est celui du
 *   backend, qui rend ses résumés récents d'abord (`lib/execution` ne retrie
 *   jamais) ; les autres gardent leur carte, dans le même bloc, avec le renvoi
 *   vers leur vue. Un run en vol n'est donc jamais perdu de vue, et c'est
 *   l'invariant que `EtatDesRuns` protège de son côté.
 * - **« Aucun run en cours » reste un état normal** (docs/35 §3.2) : sans run qui
 *   travaille, ce bloc ne rend **rien** et l'écran est exactement celui d'avant —
 *   `PosteVide` et son renvoi au fil compris. Un écran qui ne saurait dire que
 *   « ça tourne » mentirait la moitié du temps.
 *
 * ⚠ Ce bloc **ne porte aucun geste à lui**, comme `EtatDesRuns` (#476) : les
 * ordres du run (pause, interruption) viennent avec `CarteRun`, et l'arbitrage
 * d'une tâche se tranche sur l'écran qui montre de quoi trancher — le nœud du
 * pipeline y mène.
 */

import { IconeStatutEnCours } from "@/components/Icones";
import { EnTeteSection, LienRenvoi } from "@/components/Primitives";
import { CarteRun } from "@/components/runs/EtatRun";
import { VuePipeline } from "@/components/runs/VuePipeline";
import type { Reassigner } from "@/components/SelecteurReassignation";
import type { PorteeProjet } from "@/lib/api";
import {
  causeDAttente,
  messageVideDuRun,
  tachesEnAttenteDeValidation,
} from "@/lib/execution";
import { entreeParLibelle } from "@/lib/navigation";
import type { EtatAgent, ResumeExecution, Validation } from "@/lib/types";
import { useTachesRun } from "@/lib/useTachesRun";

export function RunAuCentre({
  runs,
  validations,
  enValidation,
  portee,
  agents,
  reassigner,
  revision,
}: {
  /**
   * Les runs **qui travaillent**, dans l'ordre du backend — la liste que l'écran
   * a tirée de `runsParRegime`, et celle-là même qu'il passe à la tuile de tête.
   * Vide : ce bloc ne rend rien.
   */
  runs: ResumeExecution[];
  /** La file d'arbitrage du projet — les nœuds arrêtés sur un humain s'y lisent. */
  validations: Validation[];
  /** Les runs dont une tâche attend une décision (`lib/execution`) — filet de #571. */
  enValidation: ReadonlySet<string>;
  portee: PorteeProjet;
  agents: EtatAgent[];
  reassigner: Reassigner;
  /** Le pouls du shell : une lecture des tâches et du graphe par battement. */
  revision: number;
}) {
  if (runs.length === 0) return null;

  const [tete, ...autres] = runs;
  const vue = entreeParLibelle("Runs");

  return (
    // `data-guide` : la visite guidée (#122) peut éclairer ce qui occupe
    // désormais le haut de l'écran (`lib/guide`).
    <section data-guide="run-au-centre" aria-label="Run en cours">
      <EnTeteSection
        titre="Run en cours"
        icone={IconeStatutEnCours}
        className="mb-2"
        aside={
          vue && (
            <LienRenvoi renvoi={{ href: vue.href, libelle: "Tous les runs" }} />
          )
        }
      />

      {/* Le résumé, dans la forme qu'il a partout ailleurs. `<ul>` parce que
          `CarteRun` rend un `<li>` : la même carte, la même sémantique. */}
      <ul aria-label="Run en cours" className="space-y-2">
        <CarteRun run={tete} attendUneValidation={enValidation.has(tete.run_id)} />
      </ul>

      <PipelineDuRunAuCentre
        run={tete}
        validations={validations}
        enValidation={enValidation}
        portee={portee}
        agents={agents}
        reassigner={reassigner}
        revision={revision}
      />

      {/* Les autres runs en vol — nommés et non masqués : ils tiennent un hôte,
          un coût et un plan, et c'est `EtatDesRuns` qui ne les rendra plus. */}
      {autres.length > 0 && (
        <div className="mt-4">
          <p className="chiffre mb-2 text-annexe text-texte-secondaire">
            {`${autres.length} autre${autres.length > 1 ? "s" : ""} run${
              autres.length > 1 ? "s" : ""
            } en cours — ouvrez-le${autres.length > 1 ? "s" : ""} pour voir son pipeline`}
          </p>
          <ul aria-label="Autres runs en cours" className="space-y-2">
            {autres.map((run) => (
              <CarteRun
                key={run.run_id}
                run={run}
                attendUneValidation={enValidation.has(run.run_id)}
              />
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

/**
 * Le pipeline du run de tête — le même composant que dans la vue d'un run.
 *
 * Il est extrait dans son propre composant pour une raison de mécanique et une
 * seule : `useTachesRun` est un **hook**, et le bloc ci-dessus s'efface quand
 * aucun run ne travaille. Un hook derrière un retour anticipé serait un hook
 * conditionnel.
 *
 * Les tâches (`?run=<run_id>`, #473) ne servent qu'à **ouvrir un nœud** : un
 * `NoeudGraphe` porte de quoi se dessiner, pas de quoi se détailler. Leur lecture
 * ratée dégrade donc sans mentir — les nœuds restent dessinés, ils ne s'ouvrent
 * pas — et n'ajoute pas une seconde bannière d'erreur à un écran qui en a déjà
 * une (`BanniereErreurApi`, `app/page`). Le graphe, lui, dit son propre échec.
 */
function PipelineDuRunAuCentre({
  run,
  validations,
  enValidation,
  portee,
  agents,
  reassigner,
  revision,
}: {
  run: ResumeExecution;
  validations: Validation[];
  enValidation: ReadonlySet<string>;
  portee: PorteeProjet;
  agents: EtatAgent[];
  reassigner: Reassigner;
  revision: number;
}) {
  const { taches } = useTachesRun(portee, run.run_id, revision);

  return (
    <div className="mt-4">
      <VuePipeline
        runId={run.run_id}
        taches={taches}
        agents={agents}
        reassigner={reassigner}
        // L'attente humaine se lit dans la file des validations et non sur la
        // tâche : le moteur n'émet pas `en_attente_validation` (`lib/execution`).
        // Même lecture que dans la vue d'un run — un nœud teinté ici et pas
        // là-bas serait un nœud dont on doute.
        enAttenteHumaine={tachesEnAttenteDeValidation(validations)}
        revision={revision}
        // `causeDAttente` rend `null` sur un run du régime `travaille` — par
        // construction, puisque c'est ce qui le définit. On l'appelle quand même
        // plutôt que d'écrire `null` : la phrase du vide sort de la même règle
        // ici et dans la vue d'un run, et ce bloc n'a pas à savoir laquelle des
        // trois elle rendra.
        messageVide={messageVideDuRun(
          run,
          causeDAttente(run, enValidation.has(run.run_id)),
        )}
      />
    </div>
  );
}

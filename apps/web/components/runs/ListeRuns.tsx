"use client";

/**
 * La liste des runs du projet actif (#474, lot 2 de #472, docs/05 §2.4.1).
 *
 * Un run n'était l'objet d'**aucun** écran : on y entrait par « Composer un
 * objectif » et on n'y revenait jamais. Les runs passés n'étaient listés nulle
 * part, et un run suspendu sur son brief n'apparaissait ni au Kanban, ni dans les
 * grands livres, ni dans le fil d'activité — tous dérivés des tâches (revue #470,
 * docs/29 §3). Cet écran est la porte qui manquait.
 *
 * Trois décisions le tiennent :
 *
 * - **L'ordre vient du backend.** `GET /api/executions` rend ses résumés récents
 *   d'abord (`ExecutionService.resumes`), ce qui *est* l'ordre du critère. Retrier
 *   ici poserait une seconde règle à tenir d'accord avec la première pour un
 *   résultat identique — même parti pris que `runsRelancables`.
 * - **La progression n'est pas recomptée.** Elle arrive comptée par le backend sur
 *   la machine à états du moteur (#473) ; la recalculer depuis les tâches chargées
 *   ferait d'une barre d'avancement la mesure de sa propre pagination.
 * - **« En cours » ne veut pas dire « travaille ».** Un run arrêté sur son brief,
 *   sur des questions de clarification ou sur une validation de tâche affiche le
 *   même statut qu'un run qui avance. C'est le défaut d'origine — 53 minutes
 *   perdues le 2026-08-14 (#355) — et le régime de `lib/execution` est ce qui les
 *   sépare, à l'œil autant qu'en toutes lettres.
 *
 * L'écran **vide n'est pas une panne** (§2.1.1) : il nomme le projet, dit ce qui
 * apparaîtra ici et propose le geste qui le remplit. Une API injoignable, elle,
 * garde sa bannière et **rien d'autre** — conseiller « lancez un run » à qui n'a
 * pas de backend serait un contresens.
 *
 * Depuis #475 **une carte s'ouvre** : elle mène à la vue du run, qui porte son
 * Kanban et sa progression. Ce qui reste inchangé est l'autre renvoi — une attente
 * mène toujours à l'écran qui porte **le geste** qui la lève, pas à la vue du run,
 * qui montre sans débloquer (`components/runs/EtatRun`).
 */

import Link from "next/link";

import { BanniereErreurApi } from "@/components/BanniereErreurApi";
import { IconeRuns } from "@/components/Icones";
import {
  Avancement,
  BadgeRun,
  fondDe,
  LigneAttente,
  LigneInterruption,
} from "@/components/runs/EtatRun";
import { BadgeEtat, Carte, EnTeteSection, EtatVide } from "@/components/Primitives";
import { useEtatGlobal } from "@/lib/etatGlobal";
import {
  causeDAttente,
  regimeDuRun,
  runsEnAttenteDeValidation,
  REGIME_SUSPENDU,
} from "@/lib/execution";
import { formatCout, formatHeureRelative } from "@/lib/format";
import { useHorloge } from "@/lib/horloge";
import { entreeParLibelle, hrefRun } from "@/lib/navigation";
import type { ResumeExecution } from "@/lib/types";

export function ListeRuns() {
  const { projet, executions, validations, taches, chargement, erreur } =
    useEtatGlobal();

  // L'appariement validation → run passe par les tâches (`lib/execution`) : une
  // demande de validation porte sa tâche, jamais son run. Calculé une fois pour
  // toute la liste plutôt qu'une fois par carte.
  const enValidation = runsEnAttenteDeValidation(validations, taches);
  const composer = entreeParLibelle("Composer un objectif");

  return (
    <>
      <BanniereErreurApi erreur={erreur} />
      {chargement ? (
        <p className="text-sm text-neutral-500">Chargement des runs…</p>
      ) : executions.length > 0 ? (
        <section aria-label="Runs du projet">
          <EnTeteSection
            titre={
              <>
                Runs de {projet.nom}
                <BadgeEtat className="chiffre">{executions.length}</BadgeEtat>
              </>
            }
            icone={IconeRuns}
            className="mb-2"
          />
          <p className="mb-3 text-annexe text-neutral-500 dark:text-neutral-400">
            Du plus récent au plus ancien. Un run lancé sans projet n&apos;en
            relève d&apos;aucun et n&apos;apparaît donc dans la liste
            d&apos;aucun.
          </p>
          <ul className="space-y-2">
            {executions.map((run) => (
              <CarteRun
                key={run.run_id}
                run={run}
                attendUneValidation={enValidation.has(run.run_id)}
              />
            ))}
          </ul>
        </section>
      ) : erreur !== null ? null : (
        // Rien sur ce projet, et l'API répond : ce n'est pas une panne, il n'y a
        // simplement pas encore eu de run ici (§2.1.1, convention #281 — l'écran
        // vide **nomme** le projet).
        <EtatVide
          message={`Aucun run sur ${projet.nom}. Chaque exécution lancée dans ce projet s'inscrira ici — son état, son objectif, sa progression et son coût — et y restera une fois terminée.`}
          icone={IconeRuns}
          lien={
            composer && {
              href: composer.href,
              libelle: "Composer un objectif",
            }
          }
          releve="La Control Tower est branchée : la liste se remplira d'elle-même, sans recharger la page."
        />
      )}
    </>
  );
}

/**
 * Une ligne de la liste : l'objectif, l'état, la progression, le coût — et, quand
 * le run attend quelqu'un, **quoi** et **depuis quand**.
 *
 * Le titre est un **lien vers la vue du run** (#475) : c'est la carte entière qui
 * mène quelque part, mais seul le titre porte le geste, pour que le clavier et les
 * lecteurs d'écran aient une cible nommée plutôt qu'un bloc cliquable — même parti
 * pris que la carte du Kanban (#251).
 */
function CarteRun({
  run,
  attendUneValidation,
}: {
  run: ResumeExecution;
  attendUneValidation: boolean;
}) {
  const maintenant = useHorloge();
  const regime = regimeDuRun(run, attendUneValidation);
  const attente =
    regime === REGIME_SUSPENDU ? causeDAttente(run, attendUneValidation) : null;
  const nom = run.objectif || run.run_id;
  const vue = hrefRun(run.run_id);

  return (
    <Carte balise="li" ton={fondDe(regime)}>
      <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-1">
        <h3 className="min-w-0 flex-1 truncate text-corps font-medium" title={nom}>
          {vue ? (
            <Link
              href={vue}
              className="rounded hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-600 dark:focus-visible:outline-sky-400"
            >
              {nom}
            </Link>
          ) : (
            nom
          )}
        </h3>
        <BadgeRun run={run} regime={regime} attente={attente} />
      </div>

      <p className="chiffre mt-0.5 truncate text-annexe text-neutral-500 dark:text-neutral-400">
        {run.run_id}
        {run.debut ? ` · ${formatHeureRelative(run.debut, maintenant)}` : ""}
        {` · ${formatCout(run.cout_usd)}`}
      </p>

      <Avancement run={run} />

      <LigneAttente run={run} attente={attente} className="mt-2" />
      <LigneInterruption run={run} regime={regime} className="mt-2" />
    </Carte>
  );
}

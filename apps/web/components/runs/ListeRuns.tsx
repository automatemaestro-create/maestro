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
 *
 * La **carte** elle-même a rejoint ce même fichier partagé avec #476, où le tableau
 * de bord est devenu le troisième écran à rendre une ligne de run : cet écran-ci les
 * empile toutes, celui-là ne montre que ce qui tourne, mais une ligne s'y lit à
 * l'identique.
 */

import { BanniereErreurApi } from "@/components/BanniereErreurApi";
import { IconeRuns } from "@/components/Icones";
import { CarteRun } from "@/components/runs/EtatRun";
import { BadgeEtat, EnTeteSection, EtatVide } from "@/components/Primitives";
import { RegionLive } from "@/components/RegionLive";
import { mesuresDesRuns } from "@/lib/annonces";
import { useEtatGlobal } from "@/lib/etatGlobal";
import { runsEnAttenteDeValidation } from "@/lib/execution";
import { entreeParLibelle } from "@/lib/navigation";

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
      {/* La région live de l'écran (#538) : un run qui se solde, un run qui
          démarre. Les deux attentes humaines n'y sont pas — elles partent dans
          la région assertive du shell (`lib/annonces`).
          Montée **après** le chargement et **hors** du partage plein/vide :
          après, pour que son premier relevé soit celui des runs déjà lus ;
          hors, parce qu'un projet vide est justement l'écran où un premier run
          qui démarre mérite d'être annoncé. */}
      {!chargement && (
        <RegionLive
          libelle="Activité des runs"
          mesures={mesuresDesRuns(executions)}
        />
      )}
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

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
 */

import Link from "next/link";

import { BanniereErreurApi } from "@/components/BanniereErreurApi";
import { IconeFlecheDroite, IconeRuns } from "@/components/Icones";
import {
  BadgeEtat,
  Carte,
  EnTeteSection,
  EtatVide,
  type TonBadge,
  type TonCarte,
} from "@/components/Primitives";
import { useEtatGlobal } from "@/lib/etatGlobal";
import {
  ATTENTE_BRIEF,
  ATTENTE_REPONSES,
  ATTENTE_VALIDATION,
  causeDAttente,
  estRelancable,
  regimeDuRun,
  runsEnAttenteDeValidation,
  REGIME_INTERROMPU,
  REGIME_SUSPENDU,
  REGIME_TRAVAILLE,
  type CauseAttente,
  type RegimeRun,
} from "@/lib/execution";
import {
  formatCout,
  formatHeureRelative,
  libelleStatutExecution,
} from "@/lib/format";
import { useHorloge } from "@/lib/horloge";
import { entreeParLibelle } from "@/lib/navigation";
import {
  EXECUTION_ECHEC,
  EXECUTION_TERMINEE,
  type Progression,
  type ResumeExecution,
} from "@/lib/types";

/**
 * Ce que chaque cause d'attente **dit** et **où elle mène**.
 *
 * Le libellé de page est résolu par le menu (`entreeParLibelle`) et non par un
 * chemin en dur : c'est la règle du dépôt depuis #191 — un renvoi suit sa page si
 * elle déménage, et ne s'allume pas vers une page qui n'existe pas encore. La vue
 * d'un run (#475) n'est pas encore là ; en attendant, chaque attente mène à l'écran
 * qui porte **le geste** qui la lève, ce qui vaut mieux qu'un lien mort.
 */
const ATTENTES: Record<
  CauseAttente,
  { libelle: string; phrase: string; page: string; action: string }
> = {
  [ATTENTE_BRIEF]: {
    libelle: "Brief à valider",
    phrase: "Le brief attend votre décision",
    page: "Valider le brief",
    action: "Relire",
  },
  [ATTENTE_REPONSES]: {
    libelle: "Questions en attente",
    phrase: "Le Chef de projet attend vos réponses",
    page: "Valider le brief",
    action: "Répondre",
  },
  [ATTENTE_VALIDATION]: {
    libelle: "Validation en attente",
    phrase: "Une tâche attend un arbitrage humain",
    page: "Validations",
    action: "Trancher",
  },
};

/**
 * Les compartiments de la progression (#473), **dans l'ordre où la barre les
 * empile** : ce qui est acquis à gauche, ce qui reste à droite — de sorte que la
 * barre se remplit dans le sens de la lecture.
 *
 * Chaque segment porte son libellé au singulier et au pluriel : la couleur appuie
 * le sens, elle ne le porte jamais seule (règle des primitives — deux teintes se
 * ressemblent pour qui ne les distingue pas, et disparaissent à l'impression).
 */
const SEGMENTS = [
  {
    cle: "terminees",
    couleur: "bg-emerald-500",
    singulier: "terminée",
    pluriel: "terminées",
  },
  { cle: "echecs", couleur: "bg-rose-500", singulier: "échec", pluriel: "échecs" },
  {
    cle: "bloquees",
    couleur: "bg-amber-500",
    singulier: "bloquée",
    pluriel: "bloquées",
  },
  {
    cle: "en_cours",
    couleur: "bg-sky-500",
    singulier: "en cours",
    pluriel: "en cours",
  },
  {
    cle: "a_faire",
    couleur: "bg-neutral-300 dark:bg-neutral-700",
    singulier: "à faire",
    pluriel: "à faire",
  },
  {
    cle: "autres",
    couleur: "bg-neutral-400 dark:bg-neutral-500",
    singulier: "autre",
    pluriel: "autres",
  },
] as const satisfies readonly {
  cle: keyof Progression;
  couleur: string;
  singulier: string;
  pluriel: string;
}[];

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
  const { ton, libelle, pulse } = apparence(run, regime, attente);
  const page = attente === null ? undefined : entreeParLibelle(ATTENTES[attente].page);

  return (
    <Carte balise="li" ton={fondDe(regime)}>
      <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-1">
        <h3
          className="min-w-0 flex-1 truncate text-corps font-medium"
          title={run.objectif || run.run_id}
        >
          {run.objectif || run.run_id}
        </h3>
        <BadgeEtat ton={ton} pastille pulse={pulse}>
          {libelle}
        </BadgeEtat>
      </div>

      <p className="chiffre mt-0.5 truncate text-annexe text-neutral-500 dark:text-neutral-400">
        {run.run_id}
        {run.debut ? ` · ${formatHeureRelative(run.debut, maintenant)}` : ""}
        {` · ${formatCout(run.cout_usd)}`}
      </p>

      <Avancement run={run} />

      {attente !== null && (
        <p className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-annexe text-amber-800 dark:text-amber-300">
          <span>
            {ATTENTES[attente].phrase}
            {run.attente_depuis
              ? ` · ${formatHeureRelative(run.attente_depuis, maintenant)}`
              : ""}
          </span>
          {page && (
            <Link
              href={page.href}
              className="inline-flex items-center gap-1 font-medium hover:underline"
            >
              {ATTENTES[attente].action}
              <IconeFlecheDroite className="size-3.5 shrink-0" />
            </Link>
          )}
        </p>
      )}

      {regime === REGIME_INTERROMPU && (
        <p className="mt-2 text-annexe text-rose-700 dark:text-rose-400">
          Son hôte ne répond plus (#348)
          {estRelancable(run)
            ? " — son brief a été validé, il peut repartir depuis le tableau de bord."
            : " et rien ne s'y joue plus."}
        </p>
      )}
    </Carte>
  );
}

/**
 * Le fond de la carte. Seul le régime **suspendu** en change, et c'est mesuré :
 * teinter les quatre reviendrait à n'en signaler aucun. Ce qui attend quelqu'un
 * est le seul état qui appelle un geste — les trois autres se lisent au badge.
 */
function fondDe(regime: RegimeRun): TonCarte {
  return regime === REGIME_SUSPENDU ? "attentionClaire" : "pleine";
}

/**
 * Le badge d'un run — son ton, son libellé, et s'il bat.
 *
 * **La pastille ne bat que pour ce qui travaille**, et c'est là que se joue le
 * critère « un run en cours se distingue d'un run soldé » : un run qui avance est
 * bleu et bat, un run terminé est vert et immobile. Deux verts, dont un pulsant,
 * auraient demandé de lire le libellé pour trancher — ce qui est précisément ce
 * qu'un coup d'œil doit éviter.
 */
function apparence(
  run: ResumeExecution,
  regime: RegimeRun,
  attente: CauseAttente | null,
): { ton: TonBadge; libelle: string; pulse: boolean } {
  if (regime === REGIME_TRAVAILLE) {
    return { ton: "info", libelle: "En cours", pulse: true };
  }
  if (regime === REGIME_SUSPENDU && attente !== null) {
    return { ton: "attention", libelle: ATTENTES[attente].libelle, pulse: false };
  }
  if (regime === REGIME_INTERROMPU) {
    return { ton: "alerte", libelle: "Interrompu", pulse: false };
  }
  // Soldé : le statut porte l'issue, et les trois ne se valent pas — un run
  // terminé n'est pas un run annulé, un run en échec appelle une lecture.
  const ton: TonBadge =
    run.statut === EXECUTION_TERMINEE
      ? "positif"
      : run.statut === EXECUTION_ECHEC
        ? "alerte"
        : "neutre";
  return { ton, libelle: libelleStatutExecution(run.statut), pulse: false };
}

/**
 * L'avancement d'un run : la barre, puis le compte en toutes lettres.
 *
 * `progression` est **optionnelle** dans le contrat (#473) — une trace relue d'un
 * backend antérieur n'en porte pas —, d'où le repli sur `nb_taches` : dire « 8
 * tâches » sans savoir où elles en sont vaut mieux qu'une barre inventée. Et un run
 * **sans aucune tâche** le dit aussi : c'est l'état normal d'un run arrêté sur son
 * brief, qui n'en a créé aucune, pas le symptôme d'une lecture ratée.
 */
function Avancement({ run }: { run: ResumeExecution }) {
  const progression = run.progression;

  if (progression === undefined || progression.total === 0) {
    const nb = progression?.total ?? run.nb_taches;
    return (
      <p className="chiffre mt-1.5 text-annexe text-neutral-500 dark:text-neutral-400">
        {nb === 0 ? "Aucune tâche" : `${nb} tâche${nb > 1 ? "s" : ""}`}
      </p>
    );
  }

  const parts = SEGMENTS.map((segment) => ({
    ...segment,
    valeur: progression[segment.cle],
  })).filter((segment) => segment.valeur > 0);

  return (
    <>
      <div
        role="progressbar"
        aria-label="Progression du run"
        aria-valuemin={0}
        aria-valuemax={progression.total}
        aria-valuenow={progression.soldees}
        aria-valuetext={`${progression.soldees} tâche${progression.soldees > 1 ? "s" : ""} soldée${progression.soldees > 1 ? "s" : ""} sur ${progression.total}`}
        className="mt-2 flex h-1.5 w-full overflow-hidden rounded-full bg-neutral-200 dark:bg-neutral-800"
      >
        {parts.map((segment) => (
          <div
            key={segment.cle}
            className={`h-full ${segment.couleur}`}
            style={{ width: `${(segment.valeur / progression.total) * 100}%` }}
          />
        ))}
      </div>
      <p className="chiffre mt-1 text-annexe text-neutral-500 dark:text-neutral-400">
        {parts
          .map(
            (segment) =>
              `${segment.valeur} ${segment.valeur > 1 ? segment.pluriel : segment.singulier}`,
          )
          .join(" · ")}
        {` — ${progression.soldees}/${progression.total} soldée${progression.soldees > 1 ? "s" : ""}`}
      </p>
    </>
  );
}

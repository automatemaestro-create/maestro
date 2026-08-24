"use client";

/**
 * Ce qu'un run montre de lui-même : son **badge**, ce qui le **retient**, son
 * **avancement** — et la **carte** qui assemble les trois.
 *
 * Ces briques sont nées dans la liste des runs (#474) ; #475 les en a sorties le
 * jour où un second écran a eu à dire la même chose — la **vue d'un run**, qui
 * rend son Kanban sous une barre de progression et doit annoncer son état
 * exactement comme la ligne dont on vient de l'ouvrir. Deux formulations du même
 * état finiraient par diverger, et c'est la raison habituelle du dépôt : un run
 * qu'on lit « En cours » dans la liste et « en_cours » dans sa vue est un run
 * dont on doute.
 *
 * `CarteRun` a suivi le même chemin, un lot plus tard et pour la même raison :
 * #476 met **l'état des runs** au tableau de bord, ce qui fait un troisième écran
 * à rendre une ligne de run. Elle était restée dans `ListeRuns` tant qu'un seul
 * écran l'affichait ; l'y laisser aurait fait dépendre le tableau de bord du
 * composant d'une autre page, alors que ce fichier-ci est précisément l'endroit
 * où vit « ce qu'un run montre de lui-même ».
 *
 * Rien n'a changé de comportement à aucun de ces deux passages : ce fichier est
 * l'extraction de ce que `ListeRuns` portait, à la seule addition de `taille` sur
 * la barre — une liste l'empile par dizaines, une vue de run en a une seule et de
 * tête.
 *
 * ⚠ #477 y a mis le premier **geste** (`BoutonsPause`), et c'est un revirement
 * assumé : jusque-là ces briques montraient sans débloquer, et la table `ATTENTES`
 * ci-dessous renvoyait vers l'écran qui porte l'action. La règle tenait pour ce
 * qui demande de *lire* avant de trancher — un brief ne se valide pas depuis une
 * ligne de liste. Suspendre un run n'est pas un arbitrage sur un contenu : rien
 * n'est détruit, rien n'est perdu, et le geste se défait par son jumeau. Les
 * attentes, elles, continuent de renvoyer ailleurs.
 */

import Link from "next/link";
import { useState } from "react";

import {
  IconeFlecheDroite,
  IconePause,
  IconeReprise,
} from "@/components/Icones";
import {
  BadgeEtat,
  Carte,
  type TonBadge,
  type TonCarte,
} from "@/components/Primitives";
import { useEtatGlobal } from "@/lib/etatGlobal";
import {
  ATTENTE_BRIEF,
  ATTENTE_REPONSES,
  ATTENTE_VALIDATION,
  causeDAttente,
  estEnPause,
  estRelancable,
  peutEtreSuspendu,
  regimeDuRun,
  REGIME_EN_PAUSE,
  REGIME_INTERROMPU,
  REGIME_SUSPENDU,
  REGIME_TRAVAILLE,
  type CauseAttente,
  type RegimeRun,
} from "@/lib/execution";
import {
  formatCout,
  formatHeureRelative,
  libelleCause,
  libelleStatutExecution,
} from "@/lib/format";
import { useHorloge } from "@/lib/horloge";
import { entreeParLibelle, hrefRun } from "@/lib/navigation";
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
 * elle déménage, et ne s'allume pas vers une page qui n'existe pas encore.
 *
 * Chaque attente mène à l'écran qui porte **le geste** qui la lève, et non à la
 * vue du run : celle-ci existe depuis #475, mais elle *montre* le run, elle ne le
 * débloque pas — un brief se tranche à « Valider le brief », un arbitrage de tâche
 * à « Validations ». Le jour où la vue d'un run portera ces gestes, c'est cette
 * table qu'il faudra changer, et elle seule.
 */
export const ATTENTES: Record<
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
 * Le fond d'une surface qui porte un run. Seul le régime **suspendu** en change,
 * et c'est mesuré : teinter les cinq reviendrait à n'en signaler aucun. Ce qui
 * attend quelqu'un est le seul état qui appelle un geste — les autres se lisent
 * au badge.
 *
 * Un run **en pause** (#477) garde donc le fond ordinaire, alors qu'il est lui
 * aussi arrêté : la teinte dit « quelqu'un doit intervenir », et un run qu'on
 * vient de mettre de côté n'attend justement rien de personne. Le teinter
 * mettrait sur le même plan une décision déjà prise et une décision qui manque.
 */
export function fondDe(regime: RegimeRun): TonCarte {
  return regime === REGIME_SUSPENDU ? "attentionClaire" : "pleine";
}

/**
 * Le badge d'un run — son ton, son libellé, et s'il bat.
 *
 * **La pastille ne bat que pour ce qui travaille**, et c'est là que se joue le
 * critère « un run en cours se distingue d'un run soldé » (#474) : un run qui
 * avance est bleu et bat, un run terminé est vert et immobile. Deux verts, dont un
 * pulsant, auraient demandé de lire le libellé pour trancher — ce qui est
 * précisément ce qu'un coup d'œil doit éviter.
 */
export function BadgeRun({
  run,
  regime,
  attente,
  className,
}: {
  run: ResumeExecution;
  regime: RegimeRun;
  attente: CauseAttente | null;
  className?: string;
}) {
  const { ton, libelle, pulse } = apparence(run, regime, attente);
  return (
    <BadgeEtat ton={ton} pastille pulse={pulse} className={className}>
      {libelle}
    </BadgeEtat>
  );
}

function apparence(
  run: ResumeExecution,
  regime: RegimeRun,
  attente: CauseAttente | null,
): { ton: TonBadge; libelle: string; pulse: boolean } {
  if (regime === REGIME_TRAVAILLE) {
    return { ton: "info", libelle: "En cours", pulse: true };
  }
  if (regime === REGIME_EN_PAUSE) {
    // Neutre et immobile, à dessein : le run est vivant et personne ne l'attend
    // — c'est le seul état arrêté qui ne demande rien. Le faire pulser dirait
    // qu'il avance, le mettre en « attention » qu'il manque une décision.
    return { ton: "neutre", libelle: "En pause", pulse: false };
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
 * Ce qui retient le run, **depuis quand**, et le geste qui le lève. Ne rend rien
 * quand il n'attend personne : c'est le cas courant.
 */
export function LigneAttente({
  run,
  attente,
  className = "",
}: {
  run: ResumeExecution;
  attente: CauseAttente | null;
  className?: string;
}) {
  const maintenant = useHorloge();
  if (attente === null) return null;
  const page = entreeParLibelle(ATTENTES[attente].page);
  return (
    <p
      className={`flex flex-wrap items-center gap-x-2 gap-y-1 text-annexe text-amber-800 dark:text-amber-300 ${className}`}
    >
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
  );
}

/**
 * Ce qu'une pause veut dire, en toutes lettres (#477). Ne rend rien ailleurs.
 *
 * C'est une exigence du ticket et non une politesse : « une tâche tuée en cours
 * perd son travail — d'où *on ne lance plus* plutôt que *on interrompt* ». La
 * distinction entre pause et annulation ne se devine pas d'un badge gris, et
 * quelqu'un qui croirait avoir tout arrêté serait surpris de voir une tâche
 * rendre son livrable trois minutes plus tard.
 */
export function LignePause({
  regime,
  className = "",
}: {
  regime: RegimeRun;
  className?: string;
}) {
  if (regime !== REGIME_EN_PAUSE) return null;
  return (
    <p
      className={`text-annexe text-neutral-600 dark:text-neutral-400 ${className}`}
    >
      {"Aucune tâche nouvelle n'est lancée ; celles qui étaient en vol vont à leur terme. Le run reprendra son plan là où il en est."}
    </p>
  );
}

const CLASSE_BOUTON_ORDRE =
  "inline-flex shrink-0 items-center gap-1.5 rounded-md border border-neutral-300 px-3 py-1.5 text-annexe font-medium text-neutral-700 hover:bg-neutral-100 disabled:opacity-50 dark:border-neutral-700 dark:text-neutral-200 dark:hover:bg-neutral-800";

/**
 * Les **deux boutons** d'un run : le suspendre, le reprendre (#477).
 *
 * Un seul est jamais visible — ce sont deux faces du même geste, et en montrer
 * deux dont un inerte ferait chercher lequel s'applique. Aucun n'apparaît sur un
 * run soldé ou orphelin (`peutEtreSuspendu` : rien à suspendre là où plus rien ne
 * tourne, et un orphelin ne recevrait pas l'ordre).
 *
 * Les ordres partent par le contexte plutôt que par des props : le composant est
 * monté à la fois dans la liste et dans la vue d'un run, et faire descendre deux
 * fonctions à travers deux arbres pour un bouton donnerait deux endroits où les
 * oublier. C'est déjà d'où `ListeRuns` et `VueRun` tirent leurs runs.
 *
 * Le bouton se **désarme pendant l'appel** et rend le refus de l'API sous lui :
 * un 409 « déjà suspendue » se lit, il ne se devine pas. C'est le patron de
 * `PanneauRunsPerdus`, à une différence près — ici la carte ne disparaît pas au
 * succès, c'est le badge qui bascule.
 */
export function BoutonsPause({
  run,
  className = "",
}: {
  run: ResumeExecution;
  className?: string;
}) {
  const { suspendreRun, reprendreRun } = useEtatGlobal();
  const [enCours, setEnCours] = useState(false);
  const [erreur, setErreur] = useState<string | null>(null);
  const enPause = estEnPause(run);

  if (!enPause && !peutEtreSuspendu(run)) return null;

  const ordonner = async () => {
    setEnCours(true);
    setErreur(null);
    try {
      await (enPause ? reprendreRun(run.run_id) : suspendreRun(run.run_id));
    } catch (e) {
      setErreur(e instanceof Error ? e.message : String(e));
    } finally {
      // Réarmé dans tous les cas, succès compris : le bouton qui reste est
      // l'**autre** — un run repris se resuspend, et laisser le geste inverse
      // désarmé obligerait à recharger la page pour le reprendre.
      setEnCours(false);
    }
  };

  return (
    <span className={className}>
      <button
        type="button"
        disabled={enCours}
        onClick={() => void ordonner()}
        className={CLASSE_BOUTON_ORDRE}
      >
        {enPause ? (
          <IconeReprise className="size-3.5 shrink-0" />
        ) : (
          <IconePause className="size-3.5 shrink-0" />
        )}
        {enCours
          ? enPause
            ? "Reprise…"
            : "Mise en pause…"
          : enPause
            ? "Reprendre"
            : "Mettre en pause"}
      </button>
      {erreur && (
        <span className="mt-1 block text-annexe text-rose-600 dark:text-rose-400">
          {erreur}
        </span>
      )}
    </span>
  );
}

/**
 * **Pourquoi** ce run s'est arrêté (#479). Ne rend rien quand il n'y a rien à
 * dire — un run en cours, un run qui a fini normalement, un échec que le backend
 * n'a pas su classer.
 *
 * Elle est ici, avec `LigneAttente` et `LigneInterruption`, parce que le critère
 * est « dans la liste **comme** dans sa vue » : c'est exactement ce que ce
 * fichier existe pour garantir depuis #475 — deux formulations du même état
 * finiraient par diverger. La cause est donc écrite une fois et montée aux deux
 * endroits, comme tout ce qui l'entoure.
 *
 * Le **statut** reste le badge, la cause vient dessous : « Échec » dit ce qui est
 * arrivé au run, « Plafond de dépense atteint » ce qu'il faut aller changer. Les
 * fondre en un seul badge ferait perdre l'un des deux, et c'est le badge qui a
 * la place la plus contrainte.
 */
export function LigneCause({
  run,
  className = "",
}: {
  run: ResumeExecution;
  className?: string;
}) {
  const libelle = libelleCause(run.cause);
  if (libelle === null) return null;
  return (
    <p
      className={`text-annexe text-rose-700 dark:text-rose-400 ${className}`}
      // Le run est soldé : `role="status"` annoncerait un changement en cours.
      // C'est un fait acquis qu'on lit, pas une alerte qui survient.
    >
      {libelle}
    </p>
  );
}

/**
 * L'hôte de ce run ne bat plus (#348), et ce qu'on peut encore en faire (#349).
 * Ne rend rien pour les autres régimes.
 */
export function LigneInterruption({
  run,
  regime,
  className = "",
}: {
  run: ResumeExecution;
  regime: RegimeRun;
  className?: string;
}) {
  if (regime !== REGIME_INTERROMPU) return null;
  return (
    <p className={`text-annexe text-rose-700 dark:text-rose-400 ${className}`}>
      Son hôte ne répond plus (#348)
      {estRelancable(run)
        ? " — son brief a été validé, il peut repartir depuis le tableau de bord."
        : " et rien ne s'y joue plus."}
    </p>
  );
}

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

/**
 * Les deux formats de la barre. `compacte` est celui de la liste, où elle
 * s'empile par dizaines ; `ample` celui de la vue d'un run, où elle est **la**
 * réponse à « où en est-il ? » et se lit de loin.
 */
const TAILLES = {
  compacte: { barre: "h-1.5", compte: "text-annexe" },
  ample: { barre: "h-2.5", compte: "text-corps" },
} as const;

export type TailleAvancement = keyof typeof TAILLES;

/**
 * L'avancement d'un run : la barre, puis le compte en toutes lettres.
 *
 * `progression` est **optionnelle** dans le contrat (#473) — une trace relue d'un
 * backend antérieur n'en porte pas —, d'où le repli sur `nb_taches` : dire « 8
 * tâches » sans savoir où elles en sont vaut mieux qu'une barre inventée. Et un run
 * **sans aucune tâche** le dit aussi : c'est l'état normal d'un run arrêté sur son
 * brief, qui n'en a créé aucune, pas le symptôme d'une lecture ratée.
 */
export function Avancement({
  run,
  taille = "compacte",
}: {
  run: ResumeExecution;
  taille?: TailleAvancement;
}) {
  const progression = run.progression;
  const format = TAILLES[taille];

  if (progression === undefined || progression.total === 0) {
    const nb = progression?.total ?? run.nb_taches;
    return (
      <p
        className={`chiffre mt-1.5 ${format.compte} text-neutral-500 dark:text-neutral-400`}
      >
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
        className={`mt-2 flex ${format.barre} w-full overflow-hidden rounded-full bg-neutral-200 dark:bg-neutral-800`}
      >
        {parts.map((segment) => (
          <div
            key={segment.cle}
            className={`h-full ${segment.couleur}`}
            style={{ width: `${(segment.valeur / progression.total) * 100}%` }}
          />
        ))}
      </div>
      <p
        className={`chiffre mt-1 ${format.compte} text-neutral-500 dark:text-neutral-400`}
      >
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

/**
 * Un run en une ligne : l'objectif, l'état, la progression, le coût — et, quand
 * le run attend quelqu'un, **quoi** et **depuis quand**.
 *
 * Le titre est un **lien vers la vue du run** (#475) : c'est la carte entière qui
 * mène quelque part, mais seul le titre porte le geste, pour que le clavier et les
 * lecteurs d'écran aient une cible nommée plutôt qu'un bloc cliquable — même parti
 * pris que la carte du Kanban (#251).
 *
 * Elle sert la liste des runs (#474) et **l'état des runs** du tableau de bord
 * (#476), sans une prise pour les distinguer : les deux écrans répondent à la même
 * question sur le même objet, et une ligne qui se lirait autrement selon la page
 * serait le défaut que l'extraction de ce fichier existe pour empêcher. Ce qui les
 * sépare est en amont — *quels* runs, et regroupés comment.
 */
export function CarteRun({
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
      {/* La cause **avant** l'interruption : les deux peuvent coexister sur un
          run mort dont l'hôte ne bat plus, et « pourquoi il s'est arrêté »
          précède « et son hôte ne répond plus » dans l'ordre où on les lit. */}
      <LigneCause run={run} className="mt-2" />
      <LigneInterruption run={run} regime={regime} className="mt-2" />
      <LignePause regime={regime} className="mt-2" />
      {/* Le geste **sur la ligne** et pas seulement dans la vue du run (#477) :
          on met un run de côté en le voyant passer, sans avoir à l'ouvrir. Rien
          ne s'affiche pour un run soldé ou orphelin, donc ni la liste ni le
          tableau de bord ne s'alourdissent d'une rangée de boutons inertes. */}
      <BoutonsPause run={run} className="mt-2 block" />
    </Carte>
  );
}

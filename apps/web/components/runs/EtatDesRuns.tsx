"use client";

/**
 * L'état des runs, au tableau de bord (#476, lot 4 de #472, docs/05 §2.1).
 *
 * **Ce composant renverse #248**, et c'est l'objet du lot : le Kanban *était* le
 * tableau de bord, il en prenait toute la hauteur. Le motif du renversement est une
 * question de **portée**, pas de place (revue #470, docs/29 §3) — le Kanban rend les
 * tâches du **projet** (#277/#281), c'est-à-dire ce qui court mêlé à ce qui est fini
 * depuis trois jours, alors que « où en est-on ? » porte sur ce qui **tourne**,
 * c'est-à-dire un run. Il ne disparaît pas : il reparaît entier dans la vue d'un run
 * (#475, §2.4.2), et c'est ce lot-là qui lui a donné cet autre endroit où vivre.
 *
 * Trois décisions le tiennent :
 *
 * - **Les groupes sont les régimes, pas les statuts.** « En cours » au sens de l'API
 *   recouvre un run qui avance et un run arrêté depuis trois heures sur une question
 *   — c'est le défaut d'origine du chantier (53 minutes perdues le 2026-08-14, #355).
 *   Le découpage vient donc de `regimeDuRun` (`lib/execution`), la même règle que la
 *   liste des runs et la vue d'un run, jamais un second tri écrit ici.
 * - **Une ligne de run se lit à l'identique partout.** C'est `CarteRun`
 *   (`components/runs/EtatRun`), celle de la liste — badge, avancement, attente,
 *   renvoi vers la vue compris. Ce qui distingue cet écran est en amont : *quels*
 *   runs, et regroupés comment.
 * - **Seuls les soldés sont bornés dans le temps et en nombre.** Un run terminé
 *   avant-hier n'apprend rien sur « où en est-on » et la liste des runs le garde ;
 *   ce qui tourne, attend ou est tombé s'affiche **en entier**, puisque c'est
 *   précisément ce que l'écran existe pour montrer.
 *
 * Il ne **décide** de rien, et cela le distingue des trois panneaux qui le précèdent
 * (§2.1) : ceux-là portent le geste qui lève une attente — trancher un brief,
 * arbitrer une tâche, reprendre un run perdu —, celui-ci montre. Un run interrompu
 * peut donc paraître deux fois sur l'écran, dans « Runs interrompus » avec son bouton
 * et ici avec son état : c'est la même superposition que le Kanban avait avec les
 * validations, et elle est voulue — ce qui appelle un geste passe devant, ce qui
 * décrit l'état reste lisible d'un bloc.
 */

import { IconeRuns } from "@/components/Icones";
import {
  BadgeEtat,
  EnTeteSection,
  EtatVide,
  LienRenvoi,
  type TonBadge,
} from "@/components/Primitives";
import { CarteRun } from "@/components/runs/EtatRun";
import {
  regimeDuRun,
  runsEnAttenteDeValidation,
  REGIME_INTERROMPU,
  REGIME_SOLDE,
  REGIME_SUSPENDU,
  REGIME_TRAVAILLE,
  type RegimeRun,
} from "@/lib/execution";
import { useHorloge } from "@/lib/horloge";
import { entreeParLibelle } from "@/lib/navigation";
import type {
  Projet,
  ResumeExecution,
  Tache,
  Validation,
} from "@/lib/types";

/**
 * Les groupes, **dans l'ordre de lecture de l'écran** : ce qui avance, ce qui
 * attend quelqu'un, ce qui est tombé, ce qui est fini.
 *
 * Le critère du ticket en nomme trois — en cours, suspendus, soldés du jour. Le
 * quatrième, **interrompu**, est ajouté parce que `regimeDuRun` en rend quatre et
 * qu'en omettre un ferait **disparaître** ces runs-là du tableau de bord : le panneau
 * « Runs interrompus » qui les précède ne montre que les **récupérables** (orphelin
 * *et* brief approuvé, #349), si bien qu'un run mort avant validation de son cadrage
 * ne serait nulle part. Il ne s'affiche que s'il y en a, comme les trois autres.
 *
 * Sa place — après « suspendus », avant « soldés » — suit l'arbitrage de #349, déjà
 * rendu un cran plus haut sur le même écran : ce qui retient du travail **vivant**
 * passe devant ce qui ne retient plus rien.
 */
const GROUPES: readonly {
  regime: RegimeRun;
  titre: string;
  ton: TonBadge;
  /** Au-delà, le groupe dit ce qu'il masque au lieu de dérouler. */
  plafond?: number;
}[] = [
  { regime: REGIME_TRAVAILLE, titre: "En cours", ton: "info" },
  { regime: REGIME_SUSPENDU, titre: "Suspendus", ton: "attention" },
  { regime: REGIME_INTERROMPU, titre: "Interrompus", ton: "alerte" },
  {
    regime: REGIME_SOLDE,
    titre: "Soldés du jour",
    ton: "neutre",
    // Le seul groupe qui grossit sans borne — une journée chargée en solde des
    // dizaines, et le tableau de bord deviendrait la liste des runs. Même parti
    // pris que l'aperçu d'activité (#191) : on en montre quelques-uns et on dit
    // combien restent, le renvoi de l'en-tête menant à l'écran qui les porte tous.
    plafond: 5,
  },
];

export function EtatDesRuns({
  executions,
  validations,
  taches,
  projet,
}: {
  executions: ResumeExecution[];
  validations: Validation[];
  /** Les tâches du projet — l'appariement validation → run passe par elles. */
  taches: Tache[];
  projet: Projet;
}) {
  const maintenant = useHorloge();

  // L'appariement validation → run passe par les tâches (`lib/execution`) : une
  // demande de validation porte sa tâche, jamais son run. Calculé une fois pour
  // tout l'écran plutôt qu'une fois par carte.
  const enValidation = runsEnAttenteDeValidation(validations, taches);
  const liste = entreeParLibelle("Runs");

  // Une seule passe : le régime est demandé une fois par run, et l'ordre du
  // backend (récents d'abord) se conserve de lui-même dans chaque groupe — le
  // même parti pris que `ListeRuns`, qui ne retrie pas non plus.
  const parRegime = new Map<RegimeRun, ResumeExecution[]>();
  for (const run of executions) {
    const regime = regimeDuRun(run, enValidation.has(run.run_id));
    if (regime === REGIME_SOLDE && !soldeAujourdHui(run, maintenant)) continue;
    const deja = parRegime.get(regime);
    if (deja) deja.push(run);
    else parRegime.set(regime, [run]);
  }

  const groupes = GROUPES.map((groupe) => ({
    ...groupe,
    runs: parRegime.get(groupe.regime) ?? [],
  })).filter((groupe) => groupe.runs.length > 0);

  return (
    // `data-guide` : la visite guidée (#122) éclairait ici le Kanban, qui était
    // l'objet de l'écran ; elle éclaire ce qui a pris sa place (`lib/guide`).
    <section data-guide="etat-runs" aria-label="État des runs">
      <EnTeteSection
        titre="État des runs"
        icone={IconeRuns}
        className="mb-2"
        aside={
          liste && (
            <LienRenvoi renvoi={{ href: liste.href, libelle: "Tous les runs" }} />
          )
        }
      />
      {groupes.length === 0 ? (
        // Ni une panne ni un projet vide : la bannière d'erreur et `PosteVide` ont
        // déjà traité ces deux cas-là en amont (app/page). Ce qu'il reste est un
        // projet qui a vécu et ne tourne pas aujourd'hui — le dire, et dire où les
        // runs d'hier sont restés.
        <EtatVide
          icone={IconeRuns}
          message={`Aucun run en cours, suspendu ni soldé aujourd'hui sur ${projet.nom}.`}
          releve="Les runs des jours précédents restent dans la liste des runs, avec leur progression et leur coût."
          lien={
            liste && { href: liste.href, libelle: "Voir tous les runs" }
          }
        />
      ) : (
        <div className="space-y-4">
          {groupes.map((groupe) => (
            <GroupeRuns
              key={groupe.regime}
              titre={groupe.titre}
              ton={groupe.ton}
              runs={groupe.runs}
              plafond={groupe.plafond}
              enValidation={enValidation}
            />
          ))}
        </div>
      )}
    </section>
  );
}

/**
 * Un groupe : son titre, son compte, ses runs — et ce qu'il masque s'il est borné.
 *
 * La liste porte le titre en nom accessible (`aria-label`) plutôt qu'un second
 * repère de page : quatre régions imbriquées dans la région de la section
 * encombreraient les points de repère pour un gain nul, là où une liste **nommée**
 * se retrouve aussi bien.
 */
function GroupeRuns({
  titre,
  ton,
  runs,
  plafond,
  enValidation,
}: {
  titre: string;
  ton: TonBadge;
  runs: ResumeExecution[];
  plafond?: number;
  enValidation: Set<string>;
}) {
  const montres = plafond === undefined ? runs : runs.slice(0, plafond);
  const masques = runs.length - montres.length;

  return (
    <div>
      <EnTeteSection
        niveau={3}
        titre={
          <>
            {titre}
            <BadgeEtat ton={ton} className="chiffre">
              {runs.length}
            </BadgeEtat>
          </>
        }
        className="mb-2"
      />
      <ul aria-label={titre} className="space-y-2">
        {montres.map((run) => (
          <CarteRun
            key={run.run_id}
            run={run}
            attendUneValidation={enValidation.has(run.run_id)}
          />
        ))}
      </ul>
      {masques > 0 && (
        <p className="chiffre mt-2 text-annexe text-neutral-500 dark:text-neutral-400">
          + {masques} autre{masques > 1 ? "s" : ""} soldé{masques > 1 ? "s" : ""}{" "}
          aujourd&apos;hui
        </p>
      )}
    </div>
  );
}

/**
 * Ce run a-t-il rendu son verdict **aujourd'hui**, au sens du calendrier local ?
 *
 * `fin` d'abord, `debut` en repli : le contrat garde `fin` nullable
 * (`lib/types`), et une trace relue d'un backend antérieur peut être soldée sans
 * porter sa date de fin — la dater de son début vaut mieux que la faire disparaître.
 *
 * **Sans horloge, personne n'est du jour**, et c'est la règle du dépôt et non une
 * prudence de plus : `useHorloge` rend `null` au rendu serveur et à l'hydratation
 * parce que `Date.now()` n'y vaut pas la même chose que dans le navigateur (#250).
 * Trancher « aujourd'hui » sur un instant qui diverge ferait diverger l'HTML hydraté ;
 * le groupe apparaît donc au premier battement, exactement comme un « il y a 3 min »
 * remplace une heure absolue. Un horodatage illisible ne compte pas non plus : on ne
 * sait pas quel jour c'était, et la liste des runs, elle, le garde.
 */
function soldeAujourdHui(
  run: ResumeExecution,
  maintenant: number | null,
): boolean {
  if (maintenant === null) return false;
  const horodatage = run.fin ?? run.debut;
  if (!horodatage) return false;
  const instant = new Date(horodatage);
  if (Number.isNaN(instant.getTime())) return false;
  const jour = new Date(maintenant);
  return (
    instant.getFullYear() === jour.getFullYear() &&
    instant.getMonth() === jour.getMonth() &&
    instant.getDate() === jour.getDate()
  );
}

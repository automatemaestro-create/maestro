"use client";

/**
 * Les runs qui **n'avancent plus**, signalés là où on regarde — et triés par ce
 * qui les arrête (#349, #486, #738).
 *
 * Ce panneau est né avec un seul verdict de surveillance (#349, sous le nom
 * *Runs interrompus*) : un run dont l'hôte est tombé reste affiché `en_cours`
 * pour toujours — le journal durable (#97) conserve le dernier état publié, et
 * personne ne publie « je suis mort ». Le battement (#348) donne enfin le
 * verdict ; ce panneau en fait un geste. Ce qu'il propose de récupérer n'est pas
 * du temps machine mais un **cadrage validé par un humain** : sur le run du
 * 2026-08-14, deux tours de clarification, trois réponses et une approbation,
 * soit 2,52 $ et une vingtaine de minutes d'attention.
 *
 * **#738 lui en confie un second**, et c'est ce qui l'a fait changer de nom.
 * `en_souffrance` (#737, [docs/33 §7.3](../../../docs/33-decision-surveillance-run.md))
 * répond de l'**autre** question — non pas « son hôte est-il là ? » mais « ce run
 * avance-t-il ? » —, et il n'y avait rien à inventer pour le rendre : le seul
 * verdict de surveillance existant ne passe **pas** par la file de validations,
 * il sort son run de la liste et lui attache son geste.
 *
 * > Une alerte est un **état de run rendu visible**, jamais une carte à trancher.
 *
 * Trois décisions le tiennent, et aucune n'est cosmétique :
 *
 * - **deux familles, deux gestes, et jamais un chapeau commun qui mentirait.**
 *   Un run dont l'hôte est mort ne retient plus rien et se **reprend** ; un run
 *   qu'on a laissé attendre est **vivant**, il tient son hôte et son cadrage, et
 *   il n'y a rien à décider *ici* — le geste est d'**aller le voir**. Les fondre
 *   sous un même bouton proposerait de relancer un run qui n'attend qu'une
 *   réponse, c'est-à-dire de jeter ce qu'il a déjà fait ;
 * - **ce qui retient du travail vivant passe devant.** C'est l'arbitrage de #349,
 *   déjà rendu un cran plus haut par l'ordre des panneaux du tableau de bord
 *   (`app/page.tsx`) et par celui des groupes d'`EtatDesRuns` : les runs en
 *   souffrance viennent donc **avant** les runs perdus ;
 * - **aucun bloc de plus.** La règle des trois places (#539,
 *   [docs/30 §4](../../../docs/30-cible-visuelle-control-tower.md)) plafonne le
 *   corps du tableau de bord à trois blocs de plein format, et il en porte déjà
 *   trois d'arbitrage. Un quatrième panneau aurait été la réponse évidente et la
 *   mauvaise : ce qui déborde s'étend dans un bloc existant, il ne s'ajoute pas.
 *
 * **Il ne montre que ce qui est actionnable**, et les deux familles s'y
 * restreignent pour des raisons différentes. Côté perdus : orphelin **ou éteint**
 * (#486) *et* brief approuvé (`runsRelancables`) — un run `indetermine` n'est pas
 * un run mort mais un run dont on ne sait rien, et un run **sans** brief approuvé
 * n'a rien à rejouer (422). Côté souffrance : le régime **suspendu**
 * (`runsEnSouffrance`), qui écarte l'orphelin — personne ne recevrait la réponse —
 * et le run en pause, où quelqu'un a déjà décidé. Les deux règles vivent dans
 * `lib/execution`, jamais ici : le panneau rend, il ne juge pas.
 *
 * Le second état de la première famille vient de #486 et le panneau ne s'en
 * distingue pas : un run que l'extinction de Maestro a soldé (`start.sh --stop`)
 * se reprend par le **même** bouton, ce qui est le critère du ticket. Seule la
 * phrase change — l'un a perdu son hôte, l'autre a été arrêté avec l'application,
 * et présenter le second comme une panne ferait chercher un incident là où il n'y
 * a qu'un redémarrage.
 *
 * Il **décide** — pour les perdus seulement —, contrairement au panneau des
 * briefs, et c'est la même règle qui l'autorise : un brief ne tient pas dans une
 * carte (sept sections, des questions, un coût), donc y proposer « approuver »
 * inviterait à trancher sans lire. Reprendre un run n'est pas un arbitrage sur un
 * contenu : c'est un geste sur un run mort, qui ne détruit rien et dont le pire
 * cas est un run en trop, qu'on annule.
 */

import { useState, type ReactNode } from "react";

import { IconeChrono, IconeHistorique } from "@/components/Icones";
import {
  BadgeEtat,
  Bouton,
  Carte,
  EnTeteSection,
  LienRenvoi,
} from "@/components/Primitives";
import { ATTENTES } from "@/components/runs/EtatRun";
import {
  causeDAttente,
  estEteint,
  runsEnSouffrance,
  runsRelancables,
} from "@/lib/execution";
import { formatHeureRelative } from "@/lib/format";
import { useHorloge } from "@/lib/horloge";
import { hrefRun } from "@/lib/navigation";
import type { ResumeExecution } from "@/lib/types";

type Relancer = (runId: string) => Promise<ResumeExecution>;

/** Le nom du bloc — celui que la règle des trois places recense (#539). */
export const TITRE_RUNS_IMMOBILES = "Runs qui n'avancent plus";

export function PanneauRunsImmobiles({
  executions,
  relancer,
}: {
  executions: ResumeExecution[];
  relancer: Relancer;
}) {
  const enSouffrance = runsEnSouffrance(executions);
  const perdus = runsRelancables(executions);
  const total = enSouffrance.length + perdus.length;
  if (total === 0) return null;
  // Le compte par famille n'a de sens **qu'en face de l'autre** : seul, il répète
  // au mot près celui de l'en-tête, et deux fois le même nombre à deux lignes
  // d'écart se lit comme deux chiffres.
  const deuxFamilles = enSouffrance.length > 0 && perdus.length > 0;

  return (
    <Carte balise="section" ton="attention" aria-label={TITRE_RUNS_IMMOBILES}>
      <EnTeteSection
        titre={
          <>
            {TITRE_RUNS_IMMOBILES}
            <BadgeEtat ton="attention" className="chiffre">
              {total}
            </BadgeEtat>
          </>
        }
        icone={IconeHistorique}
        ton="attention"
        className="mb-2"
      />
      {/* Les sous-parties portent leur `h3` **même seules** : elles nomment ce
          qui arrête le run, et c'est précisément la distinction que le panneau
          existe pour rendre (#738, critère 3). Sans elles, un run vivant qu'on a
          laissé attendre et un run dont l'hôte est mort se liraient sous le même
          chapeau, avec deux gestes différents et rien pour dire lequel va avec
          lequel. */}
      <div className="space-y-4">
        {enSouffrance.length > 0 && (
          <Famille
            titre="Personne n'a répondu"
            phrase="Ils attendent un geste humain depuis trop longtemps. Rien n'est annulé et rien n'est perdu : allez voir ce qu'ils demandent."
            compte={deuxFamilles ? enSouffrance.length : null}
          >
            {enSouffrance.map((run) => (
              <CarteRunEnSouffrance key={run.run_id} run={run} />
            ))}
          </Famille>
        )}
        {perdus.length > 0 && (
          <Famille
            titre="Leur hôte s'est tu"
            phrase="Leur brief a été validé : il peut repartir sans repasser par la clarification."
            compte={deuxFamilles ? perdus.length : null}
          >
            {perdus.map((run) => (
              <CarteRunPerdu key={run.run_id} run={run} relancer={relancer} />
            ))}
          </Famille>
        )}
      </div>
    </Carte>
  );
}

/**
 * Une famille de runs : ce qui les arrête, ce que ça veut dire, et leurs cartes.
 *
 * La liste porte le titre en nom accessible (`aria-label`) plutôt qu'une région
 * de plus — même parti pris que les groupes d'`EtatDesRuns` : deux régions
 * imbriquées dans celle du panneau encombreraient les points de repère pour un
 * gain nul, là où une liste **nommée** se retrouve aussi bien.
 */
function Famille({
  titre,
  phrase,
  compte,
  children,
}: {
  titre: string;
  phrase: string;
  /** `null` quand la famille est seule : le compte est alors celui de l'en-tête. */
  compte: number | null;
  children: ReactNode;
}) {
  return (
    <div>
      <EnTeteSection
        niveau={3}
        titre={
          <>
            {titre}
            {compte !== null && (
              <BadgeEtat ton="attention" className="chiffre">
                {compte}
              </BadgeEtat>
            )}
          </>
        }
        ton="attention"
        className="mb-1"
      />
      <p className="mb-2 text-annexe text-neutral-600 dark:text-neutral-300">
        {phrase}
      </p>
      <ul aria-label={titre} className="space-y-2">
        {children}
      </ul>
    </div>
  );
}

/**
 * Un run qu'on a laissé attendre (#738) — **ce qu'il attend**, depuis quand, et
 * le renvoi vers sa vue.
 *
 * Pas de bouton, et c'est le critère du ticket : la réponse à une attente n'est
 * ni oui ni non (« répondre », « relever le budget », « annuler », « rien »), donc
 * il n'y a pas de geste à mettre sous une carte. Ce que le panneau propose est
 * d'**aller voir le run**, là où tout ce qu'il faut pour décider se trouve — et
 * le renvoi passe par `hrefRun`, donc il ne s'allume que si la page existe.
 *
 * L'ancienneté est là **en second**, jamais comme le signal : le tri fait le
 * travail (« il sort de la liste »), l'horodatage ne fait que dire de combien.
 * C'est exactement ce que le ticket reproche à l'état d'avant — huit endroits
 * affichaient déjà `attente_depuis`, aucun ne le comparait à quoi que ce soit.
 *
 * `causeDAttente` est demandé **sans** l'appariement par les tâches : un run en
 * souffrance porte forcément un `statut` d'attente, donc la cause se lit sur lui
 * (#571). Elle peut malgré tout manquer sur une trace d'un backend inconnu, d'où
 * le repli — dire « il attend » sans savoir quoi vaut mieux que taire le run.
 *
 * Le libellé, lui, vient de la table `ATTENTES` d'`EtatRun` et n'est pas réécrit
 * ici : c'est le mot sous lequel le badge, la ligne d'attente et la liste des runs
 * nomment déjà la même chose, et deux formulations du même état finiraient par
 * diverger. Ce que le panneau **n'emprunte pas** à cette table est son renvoi :
 * là-bas chaque attente mène à l'écran qui porte le geste qui la lève, ici on
 * envoie vers le **run**, parce que le sujet est ce run-là et non la file dont il
 * fait partie.
 */
function CarteRunEnSouffrance({ run }: { run: ResumeExecution }) {
  const maintenant = useHorloge();
  const cause = causeDAttente(run, false);
  const vue = hrefRun(run.run_id);

  return (
    <Carte balise="li" ton="attentionClaire" className="text-corps">
      <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-1">
        <span className="min-w-0 flex-1">
          {/* Pas de `title` (#536) : il répéterait le texte, que le lecteur
              d'écran lit en entier même tronqué — et l'identifiant, lui, est sur
              la ligne juste en dessous. */}
          <span className="block truncate font-medium">
            {run.objectif || run.run_id}
          </span>
          <span className="chiffre mt-0.5 flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-annexe text-neutral-500 dark:text-neutral-400">
            <span className="min-w-0 truncate">{run.run_id}</span>
            <span aria-hidden="true">·</span>
            <span>{cause === null ? "en attente" : ATTENTES[cause].libelle}</span>
            {run.attente_depuis && (
              <>
                <span aria-hidden="true">·</span>
                <span className="inline-flex items-center gap-1 whitespace-nowrap">
                  <IconeChrono aria-hidden="true" className="size-3.5 shrink-0" />
                  {/* Le glyphe dit « depuis » à l'œil et rien à qui écoute — même
                      partage que la ligne de faits de `CarteRun`. */}
                  <span className="sr-only">attend depuis </span>
                  {formatHeureRelative(run.attente_depuis, maintenant)}
                </span>
              </>
            )}
          </span>
        </span>
        {vue && (
          <LienRenvoi
            renvoi={{ href: vue, libelle: "Aller voir" }}
            className="shrink-0"
          />
        )}
      </div>
    </Carte>
  );
}

function CarteRunPerdu({
  run,
  relancer,
}: {
  run: ResumeExecution;
  relancer: Relancer;
}) {
  const maintenant = useHorloge();
  const [enCours, setEnCours] = useState(false);
  const [erreur, setErreur] = useState<string | null>(null);

  const surRelance = async () => {
    setEnCours(true);
    setErreur(null);
    try {
      await relancer(run.run_id);
      // Pas de message de succès : la relance solde ce run, donc le rechargement
      // le fait **sortir de la liste**. Une carte qui disparaît dit déjà ce qui
      // s'est passé, et un état « repris ✓ » sur un composant qu'on démonte
      // aussitôt ne serait jamais lu.
    } catch (e) {
      setErreur(e instanceof Error ? e.message : String(e));
      setEnCours(false);
    }
  };

  return (
    <Carte balise="li" ton="attentionClaire" className="text-corps">
      <div className="flex items-start gap-3">
        <span className="min-w-0 flex-1">
          <span className="block truncate font-medium" title={run.run_id}>
            {run.objectif || run.run_id}
          </span>
          <span className="chiffre mt-0.5 block text-annexe text-neutral-500 dark:text-neutral-400">
            {run.run_id}
            {/* Pourquoi ce run est là (#486) — sur la ligne déjà présente et non
                sur une de plus : les deux états mènent au même geste, seule leur
                origine diffère, et présenter une extinction volontaire comme une
                panne ferait chercher un incident après un simple redémarrage. */}
            {` · ${estEteint(run) ? "arrêté avec Maestro" : "hôte muet"}`}
            {run.debut ? ` · ${formatHeureRelative(run.debut, maintenant)}` : ""}
            {run.nb_taches > 0
              ? ` · ${run.nb_taches} tâche${run.nb_taches > 1 ? "s" : ""} planifiée${run.nb_taches > 1 ? "s" : ""}`
              : ""}
          </span>
        </span>
        <Bouton ton="attention" occupe={enCours} onClick={() => void surRelance()}>
          {enCours ? "Reprise…" : "Reprendre"}
        </Bouton>
      </div>
      {erreur && (
        <p className="mt-2 text-annexe text-rose-600 dark:text-rose-400">{erreur}</p>
      )}
    </Carte>
  );
}

"use client";

/**
 * Les runs perdus, signalés **là où on regarde** (#349, critère 3) : sur le tableau
 * de bord, sur le patron du panneau des briefs (#322) et de celui des validations
 * (#48).
 *
 * Un run dont l'hôte est tombé reste affiché `en_cours` pour toujours — le journal
 * durable (#97) conserve le dernier état publié, et personne ne publie « je suis
 * mort ». Le battement (#348) donne enfin le verdict ; ce panneau en fait un geste.
 * Ce qu'il propose de récupérer n'est pas du temps machine mais un **cadrage validé
 * par un humain** : sur le run du 2026-08-14, deux tours de clarification, trois
 * réponses et une approbation, soit 2,52 $ et une vingtaine de minutes d'attention.
 *
 * **Il ne montre que ce qui est récupérable** — orphelin **ou éteint** (#486), *et*
 * brief approuvé (`runsRelancables`). Deux exclusions, chacune pour sa raison. Un
 * run `indetermine` n'est pas un run mort mais un run dont on ne sait rien : l'API
 * accepte quand même de le relancer, l'UI ne le **propose** pas — proposer sur une
 * absence d'information serait deviner, ce que le troisième verdict existe pour
 * refuser. Et un run **sans** brief approuvé n'a rien à rejouer : le bouton
 * n'aboutirait pas (422), et l'offrir ferait passer pour une panne ce qui est un run
 * mort avant d'avoir rien coûté.
 *
 * Le **second** état vient de #486 et le panneau ne s'en distingue pas : un run que
 * l'extinction de Maestro a soldé (`start.sh --stop`) se reprend par le **même**
 * bouton, ce qui est le critère du ticket. Seule la phrase change — l'un a perdu son
 * hôte, l'autre a été arrêté avec l'application, et présenter le second comme une
 * panne ferait chercher un incident là où il n'y a qu'un redémarrage.
 *
 * Il **décide**, contrairement au panneau des briefs — et c'est la même règle qui
 * l'autorise : un brief ne tient pas dans une carte (sept sections, des questions,
 * un coût), donc y proposer « approuver » inviterait à trancher sans lire. Reprendre
 * un run n'est pas un arbitrage sur un contenu : c'est un geste sur un run mort,
 * qui ne détruit rien et dont le pire cas est un run en trop, qu'on annule.
 */

import { useState } from "react";

import { IconeHistorique } from "@/components/Icones";
import { BadgeEtat, Bouton, Carte, EnTeteSection } from "@/components/Primitives";
import { estEteint, runsRelancables } from "@/lib/execution";
import { formatHeureRelative } from "@/lib/format";
import { useHorloge } from "@/lib/horloge";
import type { ResumeExecution } from "@/lib/types";

type Relancer = (runId: string) => Promise<ResumeExecution>;

export function PanneauRunsPerdus({
  executions,
  relancer,
}: {
  executions: ResumeExecution[];
  relancer: Relancer;
}) {
  const runs = runsRelancables(executions);
  if (runs.length === 0) return null;

  return (
    <Carte balise="section" ton="attention" aria-label="Runs interrompus">
      <EnTeteSection
        titre={
          <>
            Runs interrompus
            <BadgeEtat ton="attention" className="chiffre">
              {runs.length}
            </BadgeEtat>
          </>
        }
        icone={IconeHistorique}
        ton="attention"
        className="mb-2"
      />
      <p className="mb-2 text-annexe text-neutral-600 dark:text-neutral-300">
        Leur brief a été validé : il peut repartir sans repasser par la
        clarification.
      </p>
      <ul className="space-y-2">
        {runs.map((run) => (
          <CarteRunPerdu key={run.run_id} run={run} relancer={relancer} />
        ))}
      </ul>
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

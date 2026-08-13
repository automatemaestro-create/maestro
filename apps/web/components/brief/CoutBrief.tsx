"use client";

/**
 * Les deux montants qu'on compare quand on tranche un brief (#322, critère 3) :
 * ce qui est **déjà dépensé** et ce que l'accord **engage**.
 *
 * Sans eux, un refus est timide plutôt que rationnel — on n'ose pas jeter ce
 * qu'on a payé sans savoir ce qu'on économise, et c'est exactement le réflexe
 * (le coût irrécupérable) qui rend un point de contrôle inutile. Les mettre côte
 * à côte inverse la question : ce qui est engagé ne dépend plus de la décision,
 * seul le second montant en dépend.
 *
 * Deux natures différentes, et l'écran ne les confond pas :
 *
 * - **l'engagé est mesuré** — le grand livre du run (#57), tel que le backend
 *   l'agrège. À cet instant du run il ne couvre que l'entrée en matière : lire
 *   les sources et rédiger le brief. Aucune tâche n'existe encore ;
 * - **la suite est estimée** — une fourchette, sourcée de docs/09 (lib/estimation)
 *   et d'aucune mesure de ce run-ci. D'où le « ≈ », la fourchette plutôt qu'un
 *   montant, et la phrase qui dit d'où elle vient : un chiffre dont on ignore la
 *   provenance ne se conteste pas, donc ne se décide pas.
 */

import { IconeGrandLivre, IconeMonnaie } from "@/components/Icones";
import { Carte, TuileChiffre } from "@/components/Primitives";
import { estimerSuite } from "@/lib/estimation";
import { formatCout } from "@/lib/format";
import type { Brief, CoutExecution } from "@/lib/types";

export function CoutBrief({
  cout,
  brief,
}: {
  cout: CoutExecution;
  /** Le brief **tel qu'il serait approuvé** — corrections comprises. */
  brief: Brief;
}) {
  const estimation = estimerSuite(brief);
  const engage = cout.total.cout_usd;

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      <TuileChiffre
        libelle="Déjà engagé"
        icone={IconeGrandLivre}
        valeur={formatCout(engage)}
        detail="Lecture des sources et rédaction du brief — dépensé quoi qu'il advienne"
      />
      <TuileChiffre
        libelle="Si vous approuvez"
        icone={IconeMonnaie}
        valeur={`≈ ${formatCout(estimation.bas)} à ${formatCout(estimation.haut)}`}
        detail={`Découpage puis ≈ ${estimation.nbTaches} tâche${
          estimation.nbTaches > 1 ? "s" : ""
        } — ordre de grandeur, docs/09`}
      />
      {/* Ce que le refus **ne** coûte pas : c'est la moitié de l'information, et
          celle qu'on oublie. Aucune tâche n'a été créée à ce stade — refuser
          n'annule aucun travail, il évite celui qui n'a pas commencé. */}
      <Carte densite="compacte" ton="creuse" className="sm:col-span-2">
        <p className="text-annexe text-neutral-600 dark:text-neutral-300">
          Refuser n&apos;engage <strong>rien de plus</strong> : aucune tâche
          n&apos;a encore été créée, le run se solde sur ce brief. Les montants
          ci-dessus sont ceux de l&apos;API au token ; sur abonnement, ils se
          lisent comme une part du budget d&apos;usage plutôt que comme une
          facture.
        </p>
      </Carte>
    </div>
  );
}

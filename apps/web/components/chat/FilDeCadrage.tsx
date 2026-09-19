"use client";

/**
 * Ce qui attend un geste de cadrage sur le projet actif, et le fil de celui
 * qu'on traite (#483, lot 2 de #481).
 *
 * C'est la file de `/brief` (#322) rendue dans la conversation : mêmes règles,
 * même ordre — **le plus ancien d'abord** (`runsEnAttente`, `lib/brief`), parce
 * que deux briefs en attente ne se valent pas et que celui qui dort depuis trois
 * heures est celui qu'on a oublié. Le sélecteur ne s'affiche qu'à partir de deux
 * runs : un choix entre une seule option n'est pas un choix, c'est un clic de
 * plus avant de lire.
 *
 * Le repli sur le plus ancien n'est pas un détail : trancher un brief le fait
 * sortir de la file, et sans lui le fil resterait planté sur un run qui n'attend
 * plus rien.
 *
 * ⚠ Aucune **région polie** n'est montée ici, à la différence de `/brief` (#538).
 * Ce qui entre dans cette file est déjà annoncé par la région **assertive** du
 * shell (`RegionArbitrage`, « Arbitrage requis : … »), qui compte les briefs
 * suspendus depuis #538 et coupe la parole exprès ; une seconde annonce polie sur
 * le même événement le dirait deux fois. Ce que `/brief` annonce, lui, est ce qui
 * **sort** de la file — c'est son écran, pas celui-ci.
 *
 * ## La file avait une seconde entrée, qu'elle ne voyait pas (#943)
 *
 * Une proposition de l'orchestration — « Je lance ? » — est un cadrage en
 * attente **sans run** : `runsEnAttente` ne pouvait pas la voir, et ce composant
 * affirmait « Aucun cadrage en attente » au moment même où la question était
 * posée (retex du 2026-09-11, constat G10). Il la reçoit désormais de la page,
 * qui la lit par `propositionEnAttente` — la règle vit dans `lib/brief` avec
 * l'autre, précisément pour que les deux ne puissent plus désigner des files
 * différentes.
 *
 * Il la **signale**, il ne la tranche pas — le partage que `PanneauBriefs` tient
 * déjà : la carte dit qu'une décision attend et où elle se prend, le geste vit
 * là où on lit la demande (`chat/DemandeDeCadrage`, au pied du fil). Deux
 * endroits pour un même bouton, ce serait la même question posée deux fois sur
 * le même écran.
 */

import { useState } from "react";

import { BanniereErreurApi } from "@/components/BanniereErreurApi";
import { CadrageDansLeFil } from "@/components/chat/CadrageDansLeFil";
import { IconeObjectif } from "@/components/Icones";
import { BadgeEtat, EtatVide } from "@/components/Primitives";
import { runsEnAttente } from "@/lib/brief";
import { useEtatGlobal } from "@/lib/etatGlobal";
import { formatHeureRelative } from "@/lib/format";
import { useHorloge } from "@/lib/horloge";
import { entreeParLibelle } from "@/lib/navigation";
import {
  EXECUTION_EN_ATTENTE_REPONSES,
  type MessageChat,
} from "@/lib/types";

export function FilDeCadrage({
  proposition = null,
}: {
  /**
   * La demande de cadrage que le fil porte encore (#943) — celle que la page a
   * lue par `propositionEnAttente`. `null` : aucune, et c'est la seule façon de
   * dire « aucun » sans se tromper.
   */
  proposition?: MessageChat | null;
}) {
  const { projet, executions, chargement, erreur } = useEtatGlobal();
  const maintenant = useHorloge();
  const [choisi, setChoisi] = useState<string | null>(null);

  const runs = runsEnAttente(executions);
  const courant = runs.find((r) => r.run_id === choisi) ?? runs[0];
  const composer = entreeParLibelle("Composer un objectif");

  if (chargement) {
    return <p className="text-sm text-neutral-500">Chargement du cadrage…</p>;
  }

  return (
    <>
      <BanniereErreurApi erreur={erreur} />
      {/* La demande **sans run**, en tête : c'est la plus récente des deux
          sortes, et la seule à laquelle on répond sans quitter l'écran. */}
      {proposition !== null && (
        <PropositionEnAttente demande={proposition} maintenant={maintenant} />
      )}
      {courant === undefined ? (
        proposition === null && (
          <EtatVide
            message={`Aucun cadrage en attente sur ${projet.nom}. Un run lancé depuis la Control Tower s'arrête ici avant de décomposer : c'est le moment où corriger coûte un message.`}
            icone={IconeObjectif}
            lien={composer && { href: composer.href, libelle: "Composer un objectif" }}
          />
        )
      ) : (
        <>
          {runs.length > 1 && (
            <nav aria-label="Cadrages en attente" className="flex flex-wrap gap-2">
              {runs.map((run) => (
                <button
                  key={run.run_id}
                  type="button"
                  onClick={() => setChoisi(run.run_id)}
                  aria-current={run.run_id === courant.run_id}
                  className={
                    "max-w-full rounded-md border px-3 py-1.5 text-left text-annexe " +
                    (run.run_id === courant.run_id
                      ? "border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200"
                      : "border-neutral-200 hover:bg-neutral-50 dark:border-neutral-800 dark:hover:bg-neutral-900")
                  }
                >
                  <span className="block truncate font-medium">
                    {run.objectif || run.run_id}
                  </span>
                  <span className="mt-0.5 flex items-center gap-1.5 text-micro text-neutral-500 dark:text-neutral-400">
                    <BadgeEtat
                      ton={
                        run.statut === EXECUTION_EN_ATTENTE_REPONSES
                          ? "info"
                          : "attention"
                      }
                    >
                      {run.statut === EXECUTION_EN_ATTENTE_REPONSES
                        ? "réponses"
                        : "décision"}
                    </BadgeEtat>
                    {run.attente_depuis
                      ? formatHeureRelative(run.attente_depuis, maintenant)
                      : ""}
                  </span>
                </button>
              ))}
            </nav>
          )}
          {/* La `key` remet le fil à zéro — brief chargé, corrections en cours,
              réponses saisies. D'un run à l'autre, et **d'un tour de
              clarification au suivant** sur le même run : répondre fait
              régénérer le brief en entier, et rien de ce qu'on avait sous les
              yeux ne s'applique encore. D'où l'attente dans la clé, et pas
              seulement l'identifiant. */}
          <CadrageDansLeFil
            key={`${courant.run_id}|${courant.statut}|${courant.attente_depuis ?? ""}`}
            execution={courant}
          />
        </>
      )}
    </>
  );
}

/**
 * La proposition que l'orchestration attend de voir tranchée, **signalée**
 * (#943) : ce qu'elle lancerait, et depuis quand elle le demande.
 *
 * Elle ne porte pas de bouton, et c'est le partage de `PanneauBriefs` — « il
 * signale et il achemine, il ne décide pas ». Ici le geste est à quelques
 * lignes de là, au pied du fil ; le redonner en tête poserait deux fois la même
 * question sur le même écran. Ce que cette carte apporte est ce qui manquait :
 * que la surface faite pour montrer les cadrages en attente en montre un quand
 * il y en a un.
 */
function PropositionEnAttente({
  demande,
  maintenant,
}: {
  demande: MessageChat;
  maintenant: number | null;
}) {
  const quand = demande.horodatage
    ? formatHeureRelative(demande.horodatage, maintenant)
    : "";
  return (
    <div className="flex min-w-0 flex-col gap-1.5 rounded-md border border-attention bg-attention-creux px-3 py-2">
      <span className="flex flex-wrap items-center gap-2">
        <BadgeEtat ton="attention">décision</BadgeEtat>
        <span className="text-annexe text-attention-texte">
          L&apos;orchestration attend votre accord{quand && ` — ${quand}`}
        </span>
      </span>
      <p className="min-w-0 break-words text-corps text-texte">
        {demande.proposition}
      </p>
      <p className="text-annexe text-texte-secondaire">
        Répondez sur la demande, au bas du fil : lancer, corriger l&apos;objectif
        ou refuser.
      </p>
    </div>
  );
}

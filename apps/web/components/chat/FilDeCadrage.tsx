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
import { EXECUTION_EN_ATTENTE_REPONSES } from "@/lib/types";

export function FilDeCadrage() {
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
      {courant === undefined ? (
        <EtatVide
          message={`Aucun cadrage en attente sur ${projet.nom}. Un run lancé depuis la Control Tower s'arrête ici avant de décomposer : c'est le moment où corriger coûte un message.`}
          icone={IconeObjectif}
          lien={composer && { href: composer.href, libelle: "Composer un objectif" }}
        />
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

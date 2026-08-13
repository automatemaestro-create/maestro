"use client";

/**
 * Les sept sections du brief structuré (#318), en lecture et en correction — le
 * cœur de l'écran de validation (#322, docs/05 §2.7.4).
 *
 * Deux rendus du même objet, et l'écart entre eux est le sujet du lot :
 *
 * - **en lecture**, chaque section est une liste à puces et une section vide se
 *   dit « — » plutôt que de disparaître. Une section absente et une section vide
 *   ne sont pas la même information : « aucune contrainte » est une affirmation
 *   du Chef de projet, un blanc serait un oubli de l'écran. C'est ce que le
 *   schéma partagé garantit en n'omettant jamais une clé (`Brief`, lib/types) ;
 * - **en correction**, chaque section est un champ libre, **une entrée par
 *   ligne**. Corriger un brief, c'est réécrire des phrases — pas gérer une liste.
 *   Un champ par puce transformerait « retire ces deux critères » en quatre
 *   gestes, et c'est cette friction-là qui fait approuver sans lire.
 *
 * `questions` ferme la marche dans les deux cas : elle ne décrit pas le travail,
 * elle dit ce que le Chef de projet n'a pas pu trancher seul.
 */

import { SECTIONS_LISTE, type BriefEdite, type CleSectionListe } from "@/lib/brief";
import type { Brief } from "@/lib/types";

const CLASSE_CHAMP =
  "w-full rounded-md border border-neutral-200 bg-white px-3 py-1.5 text-corps text-neutral-900 placeholder:text-neutral-400 focus:border-emerald-500 focus:outline-none disabled:opacity-50 dark:border-neutral-800 dark:bg-neutral-900 dark:text-neutral-100 dark:placeholder:text-neutral-600";

const CLASSE_LIBELLE =
  "flex flex-col gap-1 text-annexe font-medium text-neutral-600 dark:text-neutral-400";

/**
 * La hauteur d'un champ de section : celle de son contenu, entre deux et dix
 * lignes. En deçà le champ paraît cassé, au-delà il pousserait les boutons de
 * décision hors de l'écran — un champ qui défile chez lui vaut mieux qu'une page
 * qui défile pour lui (même parti pris que le diff des validations, #227).
 */
const LIGNES_MIN = 2;
const LIGNES_MAX = 10;

function lignes(texte: string): number {
  return Math.min(LIGNES_MAX, Math.max(LIGNES_MIN, texte.split("\n").length));
}

/** Le brief tel qu'on le relit — sept sections, aucune escamotée. */
export function SectionsBrief({ brief }: { brief: Brief }) {
  return (
    <div className="space-y-3">
      <section aria-label="Objectif">
        <h3 className="text-annexe font-semibold text-neutral-600 dark:text-neutral-400">
          Objectif
        </h3>
        <p className="mt-1 whitespace-pre-wrap text-corps text-neutral-800 dark:text-neutral-200">
          {brief.objectif || "—"}
        </p>
      </section>
      {SECTIONS_LISTE.map(({ cle, libelle }) => (
        <section key={cle} aria-label={libelle}>
          <h3 className="text-annexe font-semibold text-neutral-600 dark:text-neutral-400">
            {libelle}
          </h3>
          {brief[cle].length === 0 ? (
            <p className="mt-1 text-corps text-neutral-500 dark:text-neutral-400">—</p>
          ) : (
            <ul className="mt-1 list-disc space-y-0.5 pl-5 text-corps text-neutral-800 dark:text-neutral-200">
              {brief[cle].map((entree, rang) => (
                // La position est la seule identité d'une entrée de brief : le
                // schéma partagé n'en donne aucune, le brief étant régénéré en
                // entier à chaque tour (#318). Deux entrées identiques seraient
                // de toute façon refusées (`uniqueItems`).
                <li key={`${cle}-${rang}`} className="whitespace-pre-wrap">
                  {entree}
                </li>
              ))}
            </ul>
          )}
        </section>
      ))}
    </div>
  );
}

/**
 * Le brief tel qu'on le corrige — **avant** de l'approuver, jamais après : ce
 * qui repart en décomposition est ce qu'on a sous les yeux, et c'est toute la
 * valeur du point de contrôle (corriger un plan coûte un message, corriger douze
 * tâches coûte douze exécutions).
 */
export function ChampsBrief({
  edite,
  changer,
  desactive = false,
}: {
  edite: BriefEdite;
  changer: (cle: "objectif" | CleSectionListe, valeur: string) => void;
  desactive?: boolean;
}) {
  return (
    <div className="space-y-3">
      <label className={CLASSE_LIBELLE}>
        Objectif
        <textarea
          value={edite.objectif}
          onChange={(e) => changer("objectif", e.target.value)}
          disabled={desactive}
          rows={lignes(edite.objectif)}
          className={CLASSE_CHAMP}
        />
      </label>
      {SECTIONS_LISTE.map(({ cle, libelle }) => (
        <label key={cle} className={CLASSE_LIBELLE}>
          <span>
            {libelle}
            <span className="ml-1.5 font-normal text-neutral-400 dark:text-neutral-500">
              une entrée par ligne
            </span>
          </span>
          <textarea
            value={edite[cle]}
            onChange={(e) => changer(cle, e.target.value)}
            disabled={desactive}
            rows={lignes(edite[cle])}
            className={CLASSE_CHAMP}
          />
        </label>
      ))}
    </div>
  );
}

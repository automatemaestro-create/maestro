"use client";

/**
 * Le rapport de lecture des sources, montré **avant** de lancer (#319, critère 2).
 *
 * C'est la moitié visible de l'aperçu : ce qui a été lu, ce qui a été tronqué et
 * à quelle limite, ce qui a été ignoré et pour quel motif, et le **coût estimé**
 * de l'ensemble. Il rend le geste de composer un objectif réversible tant qu'il
 * est gratuit — corriger une saisie coûte un clic, corriger douze tâches coûte
 * douze exécutions ([docs/24 §3.4](../../../../docs/24-projets-locaux-et-poste-de-travail.md)).
 *
 * Trois partis pris, tous les trois pris contre l'habitude :
 *
 * - **une ligne par source déclarée**, y compris celles qu'on n'a pas lues. Une
 *   extraction silencieuse produirait un brief qui parle d'un document que
 *   personne n'a lu, et rien à l'écran ne le dirait ;
 * - **« ignoré » n'est pas une erreur.** Un `.png` au milieu d'un dossier de
 *   maquettes est un constat, pas une faute — il se range en ton neutre, là où
 *   un **refus** (racine interdite, plafond dépassé) remonte au geste qui l'a
 *   provoqué, en ambre. C'est le critère 3 : « rien à lire ici » et « je refuse
 *   de lire ça » ne s'affichent jamais pareil ;
 * - **le contenu ne s'affiche pas.** Le rapport dit ce qu'une source coûte, pas
 *   ce qu'elle raconte : le Markdown extrait a son propre chemin, encadré comme
 *   donnée et jamais comme consigne (`contexte_markdown`, ENF-13).
 */

import { BadgeEtat, type TonBadge } from "@/components/Primitives";
import { formaterTokens, libelleEtat, libelleMotifLecture, libelleType } from "@/lib/sources";
import {
  LECTURE_IGNOREE,
  LECTURE_LUE,
  LECTURE_TRONQUEE,
  type LectureSource,
  type RapportLecture,
} from "@/lib/types";

/** Le ton d'un état : le lu rassure, le tronqué avertit, l'ignoré constate. */
function tonEtat(etat: string): TonBadge {
  if (etat === LECTURE_LUE) return "positif";
  if (etat === LECTURE_TRONQUEE) return "attention";
  return "neutre";
}

export function RapportExtraction({ rapport }: { rapport: RapportLecture }) {
  const tronquees = rapport.lectures.filter(
    (lecture) => lecture.etat === LECTURE_TRONQUEE,
  ).length;
  const ignorees = rapport.lectures.filter(
    (lecture) => lecture.etat === LECTURE_IGNOREE,
  ).length;

  return (
    <section
      aria-label="Aperçu de l'extraction"
      className="rounded-md border border-neutral-200 bg-neutral-50 p-3 dark:border-neutral-800 dark:bg-neutral-950"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-corps font-medium">Ce qui sera lu</h3>
        <p className="text-annexe text-neutral-600 dark:text-neutral-300">
          Coût estimé{" "}
          <strong className="chiffre font-medium">
            {formaterTokens(rapport.tokens)} tokens
          </strong>
        </p>
      </div>

      {rapport.lectures.length === 0 ? (
        <p className="mt-2 text-annexe text-neutral-500 dark:text-neutral-400">
          Aucune source déclarée : le run partira sur l&apos;objectif seul.
        </p>
      ) : (
        <>
          <ul className="mt-2 space-y-1.5">
            {rapport.lectures.map((lecture, rang) => (
              <LigneLecture key={`${lecture.type}-${lecture.nom}-${rang}`} lecture={lecture} />
            ))}
          </ul>
          {/* Le résumé **après** le détail : il ne remplace pas les lignes, il
              dit ce qu'on risque de ne pas y avoir cherché. */}
          {(tronquees > 0 || ignorees > 0) && (
            <p className="mt-2 text-annexe text-neutral-600 dark:text-neutral-300">
              {tronquees > 0 && (
                <>
                  {tronquees} source{tronquees > 1 ? "s" : ""} tronquée
                  {tronquees > 1 ? "s" : ""} — le contexte n&apos;en portera
                  qu&apos;une partie.{" "}
                </>
              )}
              {ignorees > 0 && (
                <>
                  {ignorees} source{ignorees > 1 ? "s" : ""} ignorée
                  {ignorees > 1 ? "s" : ""} — elle
                  {ignorees > 1 ? "s n'entreront" : " n'entrera"} pas dans le
                  contexte.
                </>
              )}
            </p>
          )}
        </>
      )}
    </section>
  );
}

/** Une source du rapport : son état, son coût, et ce qui explique l'un ou l'autre. */
function LigneLecture({ lecture }: { lecture: LectureSource }) {
  const explication =
    lecture.etat === LECTURE_IGNOREE
      ? (libelleMotifLecture(lecture.motif) ?? lecture.message)
      : lecture.limite;

  return (
    <li className="rounded border border-neutral-200 bg-white px-2.5 py-1.5 dark:border-neutral-800 dark:bg-neutral-900">
      <div className="flex flex-wrap items-center gap-2">
        <BadgeEtat ton={tonEtat(lecture.etat)}>{libelleEtat(lecture.etat)}</BadgeEtat>
        <span className="min-w-0 truncate text-corps font-medium">{lecture.nom}</span>
        <span className="text-annexe text-neutral-500 dark:text-neutral-400">
          {libelleType(lecture.type)}
        </span>
        <span className="chiffre ml-auto text-annexe text-neutral-600 dark:text-neutral-300">
          {formaterTokens(lecture.tokens)} tokens
        </span>
      </div>
      {explication !== "" && (
        <p className="mt-1 text-annexe text-neutral-600 dark:text-neutral-300">
          {lecture.etat === LECTURE_TRONQUEE ? `Limite atteinte : ${explication}` : explication}
          {lecture.etat === LECTURE_IGNOREE && (
            <>
              {" "}
              <code className="font-mono text-neutral-500 dark:text-neutral-400">
                {lecture.motif}
              </code>
            </>
          )}
        </p>
      )}
      {lecture.entrees.length > 0 && (
        // Les fichiers d'un dossier **sous** leur dossier : les renvoyer à la
        // fin ferait perdre à quelle source ils appartiennent, ce qui est
        // exactement l'information qu'on vient chercher.
        <ul className="mt-1.5 space-y-0.5 border-l border-neutral-200 pl-2.5 dark:border-neutral-800">
          {lecture.entrees.map((entree, rang) => (
            <li
              key={`${entree.nom}-${rang}`}
              className="flex flex-wrap items-center gap-2 text-annexe"
            >
              <BadgeEtat ton={tonEtat(entree.etat)} contour>
                {libelleEtat(entree.etat)}
              </BadgeEtat>
              <span className="min-w-0 truncate">{entree.nom}</span>
              {entree.etat === LECTURE_IGNOREE && entree.motif !== "" && (
                <code className="font-mono text-neutral-500 dark:text-neutral-400">
                  {entree.motif}
                </code>
              )}
              <span className="chiffre ml-auto text-neutral-500 dark:text-neutral-400">
                {formaterTokens(entree.tokens)} tokens
              </span>
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

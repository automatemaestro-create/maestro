"use client";

/**
 * Ce qu'un message **a** porté, une fois parti (#482, critères 1 et 3).
 *
 * La bulle dit ce que l'utilisateur a écrit ; ce composant dit ce qu'il a joint,
 * et — c'est le critère 3 — ce que Maestro en a **réellement lu**. Les deux
 * moitiés ne sont pas la même information, et c'est tout le sujet : « j'ai joint
 * trois documents » est un fait sur le geste, « deux ont été lus, le troisième
 * est une image » est un fait sur le résultat. Le second ne se devine pas depuis
 * le premier.
 *
 * Le rapport est **replié par défaut et dépliable sur place**. Trois raisons, et
 * la première est la règle de sobriété (#539) : un fil est une suite de bulles,
 * et déplier d'office un tableau de lectures sous chacune ferait de la
 * conversation un journal. Ensuite, l'information est rarement demandée mais
 * toujours attendue au même endroit — le message qui a porté les sources, pas un
 * écran voisin (« sans quitter l'écran », dit le critère). Enfin, le résumé
 * visible sans déplier — « 2 lues, 1 ignorée » — suffit à savoir s'il *faut*
 * déplier, ce qui est exactement le rôle d'un résumé.
 *
 * Le rendu du rapport lui-même est celui du composer (`RapportExtraction`,
 * #319), sans une ligne de variante : ce que « lu / tronqué / ignoré » veut dire
 * ne dépend pas de l'écran qui l'affiche, et deux rendus auraient fini par
 * traduire différemment le même motif.
 */

import { useId, useState } from "react";

import { RapportExtraction } from "@/components/composer/RapportExtraction";
import { IconeDossier, IconeLienExterne } from "@/components/Icones";
import { formaterOctets, formaterTokens, libelleType } from "@/lib/sources";
import {
  LECTURE_IGNOREE,
  LECTURE_LUE,
  LECTURE_TRONQUEE,
  SOURCE_DOSSIER,
  SOURCE_URL,
  type MessageChat,
} from "@/lib/types";

export function SourcesDuFil({ message }: { message: MessageChat }) {
  const idRapport = useId();
  const [deplie, setDeplie] = useState(false);
  const sources = message.sources ?? [];
  const rapport = message.rapport ?? null;

  if (sources.length === 0) return null;

  return (
    <div className="mt-1.5 space-y-1 border-t border-white/25 pt-1.5 dark:border-white/15">
      <ul
        aria-label={`Sources jointes (${sources.length})`}
        className="space-y-0.5"
      >
        {sources.map((source, rang) => (
          <li
            key={`${source.type}-${source.nom}-${rang}`}
            className="flex flex-wrap items-center gap-1.5 text-[11px]"
          >
            {source.type === SOURCE_DOSSIER && (
              <IconeDossier className="size-3 shrink-0 opacity-70" />
            )}
            {source.type === SOURCE_URL && (
              <IconeLienExterne className="size-3 shrink-0 opacity-70" />
            )}
            <span className="opacity-70">{libelleType(source.type)}</span>
            <span className="min-w-0 truncate font-medium">{source.nom}</span>
            {source.taille !== null && (
              <span className="chiffre opacity-70">
                {formaterOctets(source.taille)}
              </span>
            )}
          </li>
        ))}
      </ul>

      {/* Le rapport n'accompagne pas toujours les sources : un fil écrit avant
          #482, ou relu d'une ligne qui n'en portait pas, montre les pièces
          jointes sans mentir sur une lecture qui n'a pas été consignée. */}
      {rapport !== null && (
        <>
          <button
            type="button"
            onClick={() => setDeplie(!deplie)}
            aria-expanded={deplie}
            aria-controls={idRapport}
            className="inline-flex min-h-6 items-center gap-1 rounded text-[11px] underline underline-offset-2 opacity-90 hover:opacity-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-current"
          >
            {deplie ? "Masquer" : "Voir"} ce qui a été lu — {resumeLecture(rapport)}
          </button>
          {deplie && (
            // Le rapport reprend le fond de l'écran plutôt que celui de la bulle :
            // `RapportExtraction` est écrit pour une carte claire, et le poser tel
            // quel sur l'aplat vert de l'utilisateur en rendrait le texte illisible.
            <div id={idRapport} className="mt-1.5 text-neutral-900 dark:text-neutral-100">
              <RapportExtraction rapport={rapport} />
            </div>
          )}
        </>
      )}
    </div>
  );
}

/**
 * Le rapport en une ligne : ce qu'on lit avant de décider s'il faut déplier.
 *
 * Compté sur les **états** et non sur les seules sources lues : « 3 sources » ne
 * dit pas si l'une d'elles est passée à la trappe, et c'est précisément ce que le
 * critère 3 demande de rendre vérifiable.
 */
function resumeLecture(rapport: { tokens: number; lectures: { etat: string }[] }): string {
  const compte = (etat: string) =>
    rapport.lectures.filter((lecture) => lecture.etat === etat).length;
  const morceaux: string[] = [];
  const lues = compte(LECTURE_LUE);
  const tronquees = compte(LECTURE_TRONQUEE);
  const ignorees = compte(LECTURE_IGNOREE);
  if (lues > 0) morceaux.push(`${lues} lue${lues > 1 ? "s" : ""}`);
  if (tronquees > 0) morceaux.push(`${tronquees} tronquée${tronquees > 1 ? "s" : ""}`);
  if (ignorees > 0) morceaux.push(`${ignorees} ignorée${ignorees > 1 ? "s" : ""}`);
  const etats = morceaux.length > 0 ? morceaux.join(", ") : "rien à lire";
  return `${etats}, ${formaterTokens(rapport.tokens)} tokens`;
}

"use client";

/**
 * Ce que l'interlocuteur a **lu** pour répondre (#1223) — au-dessus de sa réponse.
 *
 * L'orchestrateur ne répond plus sur un contexte figé : il ouvre les fichiers du
 * projet, cherche dedans, relit le détail complet d'un run
 * (`maestro.controltower.consultation`). Sans ce composant, rien à l'écran ne le
 * dirait, et une réponse juste se lirait comme une réponse sûre d'elle — la
 * différence entre « il a regardé » et « il a l'air sûr de lui ».
 *
 * ## La forme, et qui l'a choisie
 *
 * Trois variantes ont été rendues sur la vraie stack et confrontées à deux
 * références capturées en direct le 2026-09-23 — Perplexity et le détail d'un job
 * GitHub Actions ; le sous-agent `regard-neuf` a retenu celle-ci (#1009, #980,
 * choix consigné sur le ticket sous « ## Variante retenue »). Ce qui a été
 * écarté : les lectures **à plat et toujours visibles** (elles occupaient plus de
 * hauteur que les réponses), et la mention **en pied de réponse** (la preuve
 * arrivait après ce qu'elle appuie, contre les deux références).
 *
 * Quatre propriétés portent la décision, et aucune n'est cosmétique :
 *
 * - **une seule ligne, repliée** — « A consulté 3 éléments », d'après la ligne
 *   « Recherche terminée » de Perplexity. Un fil est une suite de bulles ; trois
 *   lignes de plus avant chaque réponse en feraient un journal (c'est la règle de
 *   sobriété de docs/30 §4, et c'est ce qui a écarté la variante à plat) ;
 * - **au-dessus de la réponse** — l'ordre réel des choses : il lit, puis il
 *   rédige. Les deux références le font, et la bulle en cours l'exige : pendant
 *   qu'il lit, il n'y a encore aucun texte sous quoi se mettre ;
 * - **le dépli montre les lectures, pas leur contenu** — une ligne par lecture,
 *   chacune repliable à son tour. Perplexity dépliée montre la recherche faite,
 *   pas ce qu'elle a rapporté ; ouvrir les trois extraits d'un coup enfouirait la
 *   réponse sous son propre appareil de preuve ;
 * - **second plan** — `text-annexe text-texte-secondaire`, aucune `Carte`, aucune
 *   couleur d'accent, aucun bloc de plein format : la règle des trois places
 *   (docs/30 §4) interdit d'ajouter un bloc au corps de `/chat`, dont le fil est
 *   le seul bloc permanent.
 *
 * ## Deux choses à ne pas défaire
 *
 * **Le repli est un `<details>/<summary>` natif**, comme celui des rôles écartés
 * d'`EquipeDansLeFil` : il est ouvrable au clavier, annoncé par les lecteurs
 * d'écran et replié par le navigateur sans un état React. Le `min-h-6` du sommaire
 * est `CIBLE_MINIMALE` (#537) — un repli reste une cible de pointage.
 *
 * **Rien à montrer ⇒ rien à rendre.** Un message ordinaire n'a lu nulle part, et
 * ne doit pas laisser une ligne vide au-dessus de lui : même règle que `Suite` et
 * que `SourcesDuFil`.
 */

import { IconeBrief } from "@/components/Icones";
import { CIBLE_MINIMALE } from "@/components/Primitives";
import type { EtapeFil } from "@/lib/types";

/** Ce que le sommaire dit — le **compte**, qui est ce qu'on lit sans déplier. */
function resume(nombre: number, enCours: boolean): string {
  if (enCours) {
    return nombre === 1 ? "Consulte 1 élément…" : `Consulte ${nombre} éléments…`;
  }
  return nombre === 1 ? "A consulté 1 élément" : `A consulté ${nombre} éléments`;
}

export function EtapesDuFil({
  etapes,
  enCours = false,
}: {
  etapes: EtapeFil[];
  /**
   * La réponse s'écrit encore (#1223) : le sommaire est alors au présent et le
   * repli s'ouvre **d'office** — pendant qu'il lit, ce qu'il lit *est* ce qu'il y
   * a à voir, et rien d'autre n'est encore écrit. Une fois la réponse posée,
   * c'est elle qu'on lit et le repli se referme.
   */
  enCours?: boolean;
}) {
  if (etapes.length === 0) return null;
  return (
    <details
      open={enCours}
      className="mb-1.5 text-annexe text-texte-secondaire"
      data-etapes-du-fil=""
    >
      <summary className={`${CIBLE_MINIMALE} cursor-pointer select-none`}>
        {resume(etapes.length, enCours)}
      </summary>
      <ul className="mt-1 flex flex-col gap-0.5 border-s border-bord ps-2.5">
        {etapes.map((etape, rang) => (
          // Le libellé n'est pas une clé : deux lectures identiques dans un même
          // tour sont possibles (relire le même fichier après une recherche), et
          // React les fondrait en une.
          <li key={`${rang}-${etape.libelle}`} className="flex items-start gap-1.5">
            <IconeBrief className="mt-1 size-3.5 shrink-0" aria-hidden="true" />
            {etape.detail === "" ? (
              // Une lecture qui n'a rien rendu n'a rien à déplier : un chevron
              // qui n'ouvre rien promet ce qu'il n'a pas.
              <p className={`${CIBLE_MINIMALE} min-w-0 break-words`}>
                {etape.libelle}
              </p>
            ) : (
              <details className="min-w-0">
                <summary className={`${CIBLE_MINIMALE} cursor-pointer select-none`}>
                  {etape.libelle}
                </summary>
                {/* Ce que la lecture a rendu, tel quel : une matière brute
                    (arborescence, extrait de fichier, trace), qu'une mise en
                    forme ferait dire autre chose. Le défilement est **dans** le
                    bloc — une ligne longue ne pousse jamais la page.

                    ⚠ **Pas de `BlocDeCode`, et c'est un choix corrigé sur pièces.**
                    Le premier jet l'employait, pour son bouton « copier » : le
                    regard neuf a relevé que son encadré bordé était alors *le
                    seul élément encadré du fil*, que sa barre de pied occupait
                    autant de hauteur que l'extrait qu'elle servait, et qu'il
                    pliait le parti pris n°4 de la veille — « aucune `Carte`,
                    aucun cadre ». Une surface creuse sans bord tient le second
                    plan et aligne l'extrait sur le libellé qu'il éclaire. Le
                    bouton « copier » est le prix, et il se paierait en cadre. */}
                <pre className="mt-1 overflow-x-auto rounded-carte bg-surface-creuse p-2.5 text-micro">
                  <code className="font-mono">{etape.detail}</code>
                </pre>
              </details>
            )}
          </li>
        ))}
      </ul>
    </details>
  );
}

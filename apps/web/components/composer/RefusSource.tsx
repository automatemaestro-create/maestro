"use client";

/**
 * Le bandeau d'un refus de source (#319) — le bandeau de `RefusMotive` (#225),
 * avec le vocabulaire des routes de composition d'un objectif.
 *
 * La forme est celle de l'écran Projets, écrite une fois : le titre, la
 * **phrase** du backend (ce qui s'est passé), le **conseil** quand l'écran en
 * connaît un (ce qu'on peut faire), puis le **code** du motif au second plan
 * (veille de #1036, #1613). Les laisser diverger ferait lire un code d'un côté
 * et une phrase de l'autre pour le même fait.
 *
 * Un composant à part plutôt qu'un appel direct à `RefusMotive` : le conseil
 * n'est pas le même (`conseilSource` connaît les motifs d'ingestion, et délègue
 * à celui des projets pour les motifs de racine qu'ils partagent), et un refus
 * de source porte en plus un **index** — la position de la source fautive —, ce
 * qui change l'endroit où il s'affiche et non seulement son texte.
 */

import { RefusMotive } from "@/components/projets/ExplorateurDossiers";
import { conseilSource } from "@/lib/sources";

export function RefusSource({
  refus,
  titre,
}: {
  refus: { motif: string; message: string };
  titre: string;
}) {
  return (
    <RefusMotive
      refus={refus}
      titre={titre}
      conseil={conseilSource(refus.motif) ?? undefined}
    />
  );
}

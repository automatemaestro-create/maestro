"use client";

/**
 * **Ce qu'on peut faire d'un message** (#1225) — parti pris 4 de la veille du
 * ticket, d'après ChatGPT (copier / partager, en icônes nues sous la réponse)
 * et Duck.ai (un rail d'actions sous **chaque** message, des deux côtés).
 *
 * Le défaut qu'il corrige tient en une phrase : aucun message du fil n'avait
 * d'action, et copier une réponse demandait de la sélectionner à la souris —
 * c'est-à-dire de viser, dans un fil qui défile pendant qu'on écrit.
 *
 * Trois propriétés, et la première est celle que la veille a refusée aux
 * références :
 *
 * - **toujours présentes, jamais au survol seul.** Zulip ne montre son rail
 *   d'actions qu'au survol : inatteignable au doigt et au clavier, donc écarté
 *   au §5 de la veille. Ici les gestes sont rendus en permanence, en second
 *   plan (`Bouton variante="discret" taille="petite"`, `CIBLE_MINIMALE` — les
 *   24 px de WCAG 2.2 §2.5.8 que `a11y.test.tsx` balaie) ;
 * - **hors de la bulle.** `chat/BulleFil` les pose sous elle, du côté du
 *   message : sur le fond plein de la personne (`bg-accent`), une icône de
 *   second plan n'aurait pas de contraste à elle ;
 * - **le geste dit ce qu'il a fait.** Un bouton qui ne répond rien ne se
 *   distingue pas d'une page figée — c'est la règle que `runs/AnnonceIssueRun`
 *   applique déjà à « Copier le chemin », et le retour passe par un
 *   `role="status"` pour être dit aussi à qui ne regarde pas le bouton.
 *
 * ⚠ **Rien à copier ⇒ rien à rendre.** Un message vide — celui qui ne porte
 * qu'une pièce jointe ou qu'un rattachement de run — ne doit pas laisser un
 * bouton qui copierait la chaîne vide : même règle que `Suite`, `SourcesDuFil`
 * et `EtapesDuFil`.
 */

import { useState } from "react";

import { IconeCopier } from "@/components/Icones";
import { Bouton, CIBLE_MINIMALE } from "@/components/Primitives";
import { copierTexte } from "@/lib/poste";

export function ActionsDuMessage({ texte }: { texte: string }) {
  const [dit, setDit] = useState<string | null>(null);
  if (texte.trim() === "") return null;

  const surCopie = async () => {
    setDit((await copierTexte(texte)) ? "Message copié" : "Copie refusée");
  };

  return (
    <p className="flex flex-wrap items-center gap-2">
      <Bouton
        taille="petite"
        variante="discret"
        ton="neutre"
        className={CIBLE_MINIMALE}
        onClick={() => void surCopie()}
      >
        <IconeCopier className="size-3.5 shrink-0" aria-hidden="true" />
        Copier
      </Bouton>
      {/* Il remplace le précédent au lieu de s'empiler : deux messages de copie
          côte à côte ne diraient rien de plus. */}
      {dit !== null && (
        <span role="status" className="text-micro text-texte-secondaire">
          {dit}
        </span>
      )}
    </p>
  );
}

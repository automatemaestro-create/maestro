"use client";

/**
 * **L'enveloppe d'un geste du fil** (#1225) — parti pris 5 de la veille du
 * ticket, d'après Perplexity (la carte « Sources » est la **seule** enveloppe de
 * la page, distincte de la prose) et Duck.ai (deux formes de second plan, pas
 * une de plus).
 *
 * Six objets se posent aujourd'hui dans une conversation sans être de la prose :
 * la **proposition de run** (`chat/DemandeDeCadrage`), l'**équipe**
 * (`chat/EquipeDansLeFil`), la **question d'un agent**
 * (`chat/QuestionDansLeFil`), la **question d'outillage**
 * (`chat/QuestionDOutillage`) et sa **conclusion**
 * (`chat/ConclusionOutillage`), et la **fin d'un run**
 * (`runs/AnnonceIssueRun`). Les cinq premiers écrivaient chacun la même chaîne à
 * la main — `Carte balise="section" ton="attention" densite="aeree"`, un
 * `EnTeteSection` de niveau 3, un `aria-label` ; le sixième était un `div` nu.
 * Six objets du même fil, deux enveloppes, et cinq occasions de diverger : c'est
 * la recopie que docs/30 §2.2 a mesurée (18 cartes, 26 boutons), sur la surface
 * même que #1225 refait.
 *
 * ## Ce que cette enveloppe décide, et ce qu'elle laisse à ses contenus
 *
 * Elle décide de **la surface, du titre et du libellé** — rien d'autre. Ce qui
 * se passe **dans** la carte reste l'affaire de chaque geste, tranché sur
 * pièces par son propre ticket : la veille de #1225 n'a pas rouvert leurs
 * contenus, et n'avait pas à le faire.
 *
 * Trois propriétés à ne pas défaire :
 *
 * - **`balise="section"` et un `aria-label` obligatoire.** C'est ce qui range le
 *   geste comme une zone nommée, et c'est ce que `sobriete.test.tsx` compte : un
 *   bloc sans nom accessible y rougit (docs/30 §4). Elles sont **exigées par le
 *   type**, pas seulement conseillées ;
 * - **le ton par défaut est `attention`.** Un geste du fil attend un arbitrage —
 *   c'est le sens de ce ton dans le socle, et c'est le rendu qu'avaient déjà les
 *   cinq cartes du pied. La fin d'un run, elle, n'attend rien : elle **raconte**,
 *   d'où `ton="creuse"` à son appel ;
 * - **le titre est un `h3`.** Ces cartes vivent dans le fil, qui est lui-même
 *   une section de l'écran (`Conversation`) : un `h2` y casserait la hiérarchie
 *   que le balayage d'accessibilité lit.
 *
 * ⚠ **Ce n'est pas un bloc de plus dans le corps de l'écran.** Ces cartes sont
 * rendues *dans* le fil — au pied de son `<ol>` ou comme un de ses `<li>` —,
 * donc jamais au premier niveau de `#contenu-principal` : elles ne comptent pas
 * dans la règle des trois places, et c'est exactement ce que la veille a refusé
 * d'ajouter au corps de `/chat` (`tests/places.ts`, `estDePremierNiveau`).
 */

import type { ReactNode } from "react";

import {
  Carte,
  EnTeteSection,
  type Icone,
  type TonCarte,
} from "@/components/Primitives";

export function CarteDuFil({
  libelle,
  titre,
  icone,
  aside,
  ton = "attention",
  className = "",
  children,
}: {
  /**
   * Le nom accessible de la carte — ce sous quoi la sonde des trois places la
   * recense, et ce qu'un lecteur d'écran annonce en entrant dedans. Obligatoire.
   */
  libelle: string;
  titre: ReactNode;
  icone?: Icone;
  /** Ce qui se pose à droite du titre : badge d'état, heure, compte. */
  aside?: ReactNode;
  /**
   * `attention` (défaut) pour un geste qui attend un arbitrage ; `creuse` pour
   * un geste qui n'attend rien et ne fait que raconter (la fin d'un run).
   */
  ton?: TonCarte;
  className?: string;
  children: ReactNode;
}) {
  return (
    <Carte
      balise="section"
      ton={ton}
      densite="aeree"
      aria-label={libelle}
      className={className}
    >
      <EnTeteSection
        niveau={3}
        icone={icone}
        titre={titre}
        ton={ton === "attention" ? "attention" : "neutre"}
        className="mb-3"
        aside={aside}
      />
      {children}
    </Carte>
  );
}

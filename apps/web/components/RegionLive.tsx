"use client";

/**
 * Les deux régions live de la Control Tower (#538, lot 6 de #532) : ce par quoi
 * un écran qui bouge tout seul le **dit**.
 *
 * Elles sont deux, et le partage est le contenu du ticket :
 *
 * - **`RegionLive`** (`aria-live="polite"`) est montée **une fois par écran temps
 *   réel**, à côté du contenu qu'elle décrit. Elle attend une pause dans le
 *   discours du lecteur d'écran, ce qui est exactement le régime de « voilà ce qui
 *   a bougé pendant que vous lisiez ».
 * - **`RegionArbitrage`** (`aria-live="assertive"`) est montée **une seule fois
 *   pour toute l'application**, dans le shell. Elle coupe la parole, et c'est
 *   réservé à ce qui attend une action : les validations et les briefs. Une
 *   demande d'arbitrage doit s'entendre quel que soit l'écran ouvert — la monter
 *   par écran l'aurait rendue muette sur les autres, ou dite N fois par un écran
 *   qui en porterait plusieurs.
 *
 * Ce que ces deux composants **ne font pas** : choisir quoi dire. Le vocabulaire
 * et l'agrégation vivent dans `lib/annonces` et `lib/useAnnonce`, testables sans
 * DOM ; il ne reste ici que le nœud accessible et sa clé.
 *
 * Trois détails du balisage, aucun facultatif :
 *
 * ① **`role` et `aria-live` sont écrits tous les deux**, bien que `status`
 *    implique `polite` et `alert` implique `assertive`. Le rôle est ce qui rend la
 *    région adressable en test (`getByRole`) ; l'attribut est ce que la sonde du
 *    ticket compte sur écran — c'est lui qui valait **zéro** sur les dix écrans
 *    mesurés le 2026-08-25, et une implication n'est pas une mesure.
 * ② **`aria-atomic`** : la phrase se lit entière ou pas du tout. Sans lui, un
 *    lecteur d'écran peut ne dire que le fragment de texte qui a changé — soit,
 *    d'une annonce à l'autre, un mot sorti de sa phrase.
 * ③ **`sr-only` et non `hidden`** : une région masquée par `display:none` n'est
 *    pas annoncée du tout. Ce qui est recherché est l'inverse — présente à l'arbre
 *    d'accessibilité, absente de l'écran, parce que ce qu'elle dit est **déjà**
 *    visible pour qui regarde.
 */

import {
  mesuresDesArbitrages,
  type Mesure,
} from "@/lib/annonces";
import { useEtatGlobal } from "@/lib/etatGlobal";
import {
  DELAI_ANNONCE_MS,
  DELAI_ARBITRAGE_MS,
  useAnnonce,
  type Annonce,
} from "@/lib/useAnnonce";

/**
 * La région polie d'un écran : ce qu'il annonce de ses propres changements.
 *
 * `libelle` nomme la région — il n'est pas lu avec l'annonce, il sert à la
 * désigner (dans un inventaire de régions, dans un test) ; il doit donc dire de
 * quel écran il s'agit, pas ce qui vient d'arriver.
 */
export function RegionLive({
  libelle,
  mesures,
  delaiMs = DELAI_ANNONCE_MS,
}: {
  libelle: string;
  mesures: Mesure[];
  delaiMs?: number;
}) {
  const annonce = useAnnonce(mesures, delaiMs);
  return (
    <Region
      role="status"
      urgence="polite"
      libelle={libelle}
      annonce={annonce}
    />
  );
}

/**
 * La région assertive du shell : les demandes d'arbitrage humain, et rien
 * d'autre.
 *
 * Elle lit l'état global elle-même plutôt que de le recevoir : elle n'a qu'un
 * point de montage (`components/Shell`), et lui faire traverser deux niveaux de
 * props n'apprendrait rien à personne.
 */
export function RegionArbitrage() {
  const { validations, executions } = useEtatGlobal();
  const annonce = useAnnonce(
    mesuresDesArbitrages(validations, executions),
    DELAI_ARBITRAGE_MS,
  );
  return (
    <Region
      role="alert"
      urgence="assertive"
      libelle="Demandes d'arbitrage"
      annonce={annonce}
    />
  );
}

/** Le nœud commun aux deux — seuls le rôle et l'urgence les séparent. */
function Region({
  role,
  urgence,
  libelle,
  annonce,
}: {
  role: "status" | "alert";
  urgence: "polite" | "assertive";
  libelle: string;
  annonce: Annonce;
}) {
  return (
    <p
      role={role}
      aria-live={urgence}
      aria-atomic="true"
      aria-label={libelle}
      className="sr-only"
    >
      {/* La clé, et non le texte, est ce qui garantit qu'une phrase répétée est
          réentendue : React ne toucherait pas au DOM pour réécrire la même
          chaîne, et une région live parle sur mutation (`lib/useAnnonce` ②). */}
      <span key={annonce.cle}>{annonce.texte}</span>
    </p>
  );
}

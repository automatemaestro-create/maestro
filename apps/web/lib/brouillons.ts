"use client";

/**
 * Le message en cours de saisie, hissé hors du composant qui le porte (#926,
 * lot 5 de #921).
 *
 * ## Pourquoi il ne peut pas rester un `useState`
 *
 * Il l'a été de #269 à #926, et c'était juste tant qu'il n'y avait qu'une
 * surface : `/chat` et l'onglet Chat d'une fiche sont des **écrans**, on n'en
 * sort pas sans quitter le fil. La colonne de droite change cela — elle vit
 * dans le shell, on écrit dedans **pendant** qu'on navigue, et le critère du
 * ticket est explicite : *changer d'écran ne perd ni le fil, ni un message en
 * cours de saisie*.
 *
 * Or deux gestes démontent le composeur, et aucun des deux n'est un abandon :
 *
 * - **aller sur `/chat`** — la colonne s'y replie (parti pris 3 de la veille :
 *   une seule conversation à l'écran, et `useChat` ouvre une WebSocket **par
 *   instance**, donc deux montages = deux sockets) ;
 * - **replier la colonne** pour voir l'écran en entier, puis la rouvrir.
 *
 * Un brouillon gardé dans le composant disparaîtrait aux deux. Gardé ici, il
 * **suit** : commencé dans la colonne, il est là en grand sur `/chat`, et
 * réciproquement — ce qui est le sens fort du critère plutôt que sa lettre.
 *
 * ## Ce que ce module n'est pas
 *
 * Ce n'est **pas une préférence** (`lib/preferences`), et il ne va donc pas dans
 * le `localStorage` : une préférence est un réglage du poste qu'on veut
 * retrouver demain, un brouillon est un geste en cours. Il survit à une
 * navigation — c'est ce qu'on lui demande — et meurt avec l'onglet, comme le
 * texte à moitié tapé de n'importe quel formulaire. Le persister ferait
 * ressurgir dans six jours une phrase dont personne ne se souvient, dans un fil
 * qui a continué sans elle.
 *
 * La mémoire est donc **celle du module**, indexée par interlocuteur : chaque
 * fil garde son propre brouillon, et passer de l'orchestration à un aparté avec
 * un agent (`/chat`, #671) ne mélange pas les deux.
 */

import { useCallback, useEffect, useState } from "react";

/** Le texte en cours, par interlocuteur. Vidé par l'envoi, jamais persisté. */
const brouillons = new Map<string, string>();

/**
 * L'événement interne qui tient les instances d'accord.
 *
 * Même contrat que `lib/preferences` — le stockage tranche, l'événement
 * notifie — mais **sans** `storage` : la mémoire est celle de l'onglet, un
 * onglet voisin n'a rien à en apprendre. Deux composeurs simultanés sur le même
 * fil ne devraient pas exister (parti pris 3) ; s'il en apparaissait un, il
 * suivrait plutôt que de diverger en silence.
 */
const EVENEMENT = "maestro:brouillon";

/** Ce qui est en train d'être écrit pour cet interlocuteur, ou `""`. */
export function lireBrouillon(agent: string): string {
  return brouillons.get(agent) ?? "";
}

/**
 * Vide la mémoire — **pour les tests**, et pour eux seuls.
 *
 * Une mémoire de module survit à un démontage : c'est toute sa raison d'être
 * ici, et c'est aussi ce qui la ferait survivre d'un test au suivant, rendant
 * l'ordre d'exécution signifiant. Six tests de `composeur` et `chat-global` l'ont
 * montré au premier passage (un « Bonjour » retrouvé collé devant le suivant).
 * `tests/setup.ts` l'appelle donc au même titre qu'il vide le `localStorage` —
 * son point 3, « un état propre entre deux tests ».
 *
 * Rien dans l'application n'a de raison de l'appeler : un brouillon se vide en
 * étant envoyé, ou meurt avec l'onglet.
 */
export function oublierLesBrouillons(): void {
  brouillons.clear();
}

/** Mémorise le brouillon et prévient les abonnés. */
export function ecrireBrouillon(agent: string, texte: string): void {
  if (texte === "") brouillons.delete(agent);
  else brouillons.set(agent, texte);
  window.dispatchEvent(
    new CustomEvent(EVENEMENT, { detail: { agent, texte } }),
  );
}

/**
 * Le brouillon d'un fil, comme un `useState` — c'est ce qui rend le
 * remplacement invisible pour l'appelant.
 *
 * ⚠ La valeur initiale est `""` et non `lireBrouillon(agent)`, puis l'effet la
 * corrige : le rendu serveur ne connaît pas cette mémoire, et la lire pendant
 * le rendu ferait diverger les deux arbres. Même mécanique, même raison, que la
 * restitution des préférences dans le `Shell` (#925).
 */
export function useBrouillon(
  agent: string,
): [string, (texte: string | ((courant: string) => string)) => void] {
  const [texte, setTexte] = useState("");

  useEffect(() => {
    // Restitution **différée d'un tick**, comme le `Shell` restitue ses
    // préférences (#925) et pour les deux mêmes raisons : le rendu serveur ne
    // connaît pas cette mémoire (la lire pendant le rendu ferait diverger les
    // deux arbres), et l'effet ne doit déclencher aucun `setState` synchrone —
    // ce que `react-hooks/set-state-in-effect` refuse, lint à l'appui.
    const tick = setTimeout(() => setTexte(lireBrouillon(agent)), 0);
    const surInterne = (recu: Event) => {
      const detail = (recu as CustomEvent<{ agent: string; texte: string }>)
        .detail;
      if (detail.agent !== agent) return;
      setTexte(detail.texte);
    };
    window.addEventListener(EVENEMENT, surInterne);
    return () => {
      clearTimeout(tick);
      window.removeEventListener(EVENEMENT, surInterne);
    };
  }, [agent]);

  /**
   * ⚠ La **forme fonctionnelle est tenue**, et ce n'est pas du zèle d'API : le
   * fil s'en sert pour ne rendre un brouillon refusé *que si l'on n'a pas déjà
   * retapé par-dessus* (`Conversation`, #695). Elle se résout sur la mémoire du
   * module — la seule source de vérité — et non sur l'état local, qui peut être
   * d'un rendu en retard.
   */
  const poser = useCallback(
    (valeur: string | ((courant: string) => string)) => {
      const suivant =
        typeof valeur === "function" ? valeur(lireBrouillon(agent)) : valeur;
      // L'état local est posé **avant** la diffusion : la frappe doit se voir à
      // la vitesse du clavier, sans attendre un aller-retour d'événement.
      setTexte(suivant);
      ecrireBrouillon(agent, suivant);
    },
    [agent],
  );

  return [texte, poser];
}

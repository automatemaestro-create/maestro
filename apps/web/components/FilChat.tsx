"use client";

/**
 * Le fil de chat utilisateur ↔ agent (ticket #85) : l'onglet Chat d'une fiche
 * agent, branché sur l'API du lot 1 (#84, `/api/chat`) via `useChat`
 * (historique REST + temps réel WebSocket).
 *
 * Depuis #269 il ne porte **plus** sa mise en page : bulles, saisie, région live,
 * dépôt de sources (#482) et rattachements (#268) vivent dans
 * `components/Conversation`, le composant de fil du produit, que le chat
 * **global** (`app/chat/page.tsx`) monte de la même façon. C'est ce que le lot 2
 * de #244 demandait en toutes lettres — « le fil par agent reste la vue
 * détaillée, et les deux ne divergent pas ». Ils ne peuvent plus diverger de mise
 * en page : il n'y en a qu'une, et ce fichier n'est plus que le branchement d'un
 * fil sur un agent.
 *
 * C'est aussi ce que `lib/useSourcesComposees` annonçait en se posant hors des
 * composants : « les deux surfaces de fil de l'application n'auront pas à
 * s'accorder sur une copie chacune ». Elles n'en ont plus qu'une, et elle est
 * ailleurs.
 *
 * Ce qui lui reste en propre est ce qui est propre à cet onglet : le nom de
 * l'agent est porté par l'en-tête de la fiche (#190), donc le titre du fil ne
 * répète pas le nom mais donne le rôle quand l'appelant le connaît.
 */

import { Conversation } from "@/components/Conversation";
import { IconeChat } from "@/components/Icones";
import { useChat } from "@/lib/useChat";

export function FilChat({
  agent,
  role,
}: {
  agent: string;
  /**
   * Le rôle de l'agent, quand l'appelant le connaît déjà. Facultatif depuis
   * #190 : l'onglet Chat d'une fiche agent n'a que le nom en main, et le rôle
   * s'y lit sur l'onglet Profil — le charger ici ne vaudrait pas la requête.
   */
  role?: string;
}) {
  const fil = useChat(agent);
  return (
    <Conversation
      fil={fil}
      interlocuteur={agent}
      libelle={`Chat avec ${agent}`}
      titre={`Conversation${role ? ` · ${role}` : ""}`}
      icone={IconeChat}
      niveauTitre={3}
    />
  );
}
